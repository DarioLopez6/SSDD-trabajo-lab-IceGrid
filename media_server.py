#!/usr/bin/env python3

import hashlib
import secrets
import json
import logging
import sys
import time
import threading
from pathlib import Path
from typing import Dict, Optional


import Ice

Ice.loadSlice('-I{} spotifice_v2.ice'.format(Ice.getSliceDir()))
import Spotifice  # type: ignore # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MediaServer")


class StreamedFile:
    def __init__(self, track_info: Spotifice.TrackInfo, media_dir: Path):
        self.track = track_info
        filepath = media_dir / track_info.filename
        try:
            self.file = open(filepath, 'rb')
        except Exception as e:
            raise Spotifice.IOError(track_info.filename, f"Error opening media file: {e}")

    def read(self, size: int) -> bytes:
        return self.file.read(size)

    def close(self) -> None:
        try:
            if getattr(self, "file", None):
                self.file.close()
        except Exception as e:
            logger.error(f"Error closing file for track '{self.track.id}': {e}")


class SecureStreamManagerI(Spotifice.SecureStreamManager):
    """
    Servant per-session. Thread-safe design:
      - metadata guarded by _lock
      - file reads happen outside lock on stable reference
      - EOF handling closes under lock after read completes
    """
    def __init__(self, server: "MediaServerI", media_dir: Path):
        self.server = server
        self.media_dir = media_dir
        self.current_track_id: Optional[str] = None
        self.stream_file: Optional[StreamedFile] = None
        self.closed = False
        self.user_info: Optional[Spotifice.UserInfo] = None
        self._session_identity: Optional[Ice.Identity] = None
        self._lock = threading.Lock()
        self._media_render: Optional[Spotifice.MediaRenderPrx] = None 

    # Session
    def get_user_info(self, current=None):
        return self.user_info

    def close(self, current=None):
        with self._lock:
            if self.closed:
                return
            self.closed = True
            if self.stream_file:
                try:
                    self.stream_file.close()
                except Exception:
                    pass
                self.stream_file = None
                self.current_track_id = None
            identity = self._session_identity
        if identity:
            try:
                self.server.unregister_session(identity)
            except Exception as e:
                logger.warning(f"Error unregistering session: {e}")

    # SecureStreamManager
    def open_stream(self, track_id, current=None):
        with self._lock:
            self.ensure_open()
            self.server.ensure_track_exists(track_id)
            # close previous stream if any
            if self.stream_file:
                try:
                    self.stream_file.close()
                except Exception:
                    pass
                self.stream_file = None
                self.current_track_id = None
            # open new file (may raise IOError)
            self.stream_file = StreamedFile(self.server.tracks[track_id], self.media_dir)
            self.current_track_id = track_id
            logger.info(f"Opened stream for track '{track_id}' for session {self._session_identity}")

    def close_stream(self, current=None):
        with self._lock:
            if self.stream_file:
                try:
                    self.stream_file.close()
                except Exception:
                    pass
                self.stream_file = None
                self.current_track_id = None
                logger.info(f"Closed stream for session {self._session_identity}")

    def get_audio_chunk(self, chunk_size, current=None):
        self.ensure_open()
        with self._lock:
            if self.stream_file is None:
                raise Spotifice.StreamError(item=self._media_render.ice_getIdentity().name, reason="No open stream for render")
            sf = self.stream_file
            if not sf:
                raise Spotifice.StreamError("", "No open stream")
        try:
            data = sf.read(chunk_size)
        except Exception as e:
            raise Spotifice.IOError(sf.track.filename if sf and sf.track else "", f"Read error: {e}")
        if not data:
            with self._lock:
                if self.stream_file is sf:
                    try:
                        sf.close()
                    except Exception:
                        pass
                    self.stream_file = None
                    self.current_track_id = None
                    logger.info(f"Reached EOF and closed stream for session {self._session_identity}")
        return data

    def ensure_open(self):
        if self.closed:
            raise Spotifice.StreamError("", "Session closed")


class MediaServerI(Spotifice.MediaServer):
    def __init__(self, media_dir: Path, playlists_dir: Path, users_file: Path):
        self.media_dir = Path(media_dir).resolve()
        self.playlists_dir = Path(playlists_dir).resolve()
        self.users_file = Path(users_file).resolve()

        # in-memory stores
        self.tracks: Dict[str, Spotifice.TrackInfo] = {}
        self.playlists: Dict[str, Spotifice.Playlist] = {}
        self.users: Dict[str, dict] = {}

        # adapter and sessions
        self.adapter: Optional[Ice.ObjectAdapter] = None
        self._sessions_lock = threading.Lock()
        self._sessions: Dict[str, SecureStreamManagerI] = {}

        # load resources
        self.load_media()
        self.load_playlists()
        self.load_users()

    # Utilities and loaders (unchanged core logic)
    def ensure_track_exists(self, track_id: str):
        if track_id not in self.tracks:
            raise Spotifice.TrackError(track_id, "Track not found")

    def _normalize_username(self, username: str) -> str:
        return username.strip()

    def load_media(self):
        self.tracks = {}
        if not self.media_dir.exists():
            logger.warning(f"Media directory not found: {self.media_dir}")
            return
        for filepath in sorted(self.media_dir.iterdir()):
            if not filepath.is_file():
                continue
            if filepath.suffix.lower() != ".mp3":
                continue
            key = filepath.name
            self.tracks[key] = Spotifice.TrackInfo(
                id=key,
                title=filepath.stem,
                filename=key,
            )
        logger.info(f"Loaded {len(self.tracks)} tracks from {self.media_dir}")

    def load_playlists(self):
        self.playlists = {}
        if not self.playlists_dir.exists():
            logger.warning(f"Playlists directory not found: {self.playlists_dir}")
            return
        for pat in ("*.json", "*.playlist"):
            for json_file in sorted(self.playlists_dir.glob(pat)):
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    pl_id_raw = data.get("id", "")
                    pl_id = pl_id_raw.strip().lower()
                    if not pl_id:
                        continue
                    raw_tracks = data.get("track_ids", [])
                    valid_tracks = [t for t in raw_tracks if t in self.tracks]
                    created_at = int(time.time())
                    ca = data.get("created_at")
                    if isinstance(ca, (int, float)):
                        created_at = int(ca)
                    playlist = Spotifice.Playlist(
                        id=pl_id,
                        name=data.get("name", ""),
                        description=data.get("description", ""),
                        owner=data.get("owner", ""),
                        created_at=created_at,
                        track_ids=valid_tracks,
                    )
                    self.playlists[pl_id] = playlist
                    logger.info(f"Loaded playlist '{pl_id}' with {len(valid_tracks)} tracks (from {json_file.name})")
                except Exception as e:
                    logger.error(f"Error loading playlist file '{json_file}': {e}")

    # MusicLibrary
    def get_all_tracks(self, current=None):
        return list(self.tracks.values())

    def get_track_info(self, track_id, current=None):
        self.ensure_track_exists(track_id)
        return self.tracks[track_id]

    # PlaylistManager
    def get_all_playlists(self, current=None):
        return list(self.playlists.values())

    def get_playlist(self, playlist_id, current=None):
        pl_id = playlist_id.strip().lower()
        if pl_id not in self.playlists:
            raise Spotifice.PlaylistError(pl_id, "Playlist not found")
        return self.playlists[pl_id]

    # users
    def load_users(self):
        self.users = {}
        if not self.users_file.exists():
            logger.warning(f"Users file not found: {self.users_file}")
            return
        try:
            with open(self.users_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for k, v in raw.items():
                if not isinstance(k, str):
                    continue
                self.users[self._normalize_username(k)] = v
            logger.info(f"Loaded {len(self.users)} users")
        except Exception as e:
            logger.error(f"Error loading users file: {e}")
            self.users = {}

    def verify_password(self, password: str, salt: str, digest: str) -> bool:
        calc = hashlib.md5((password + salt).encode('utf-8')).hexdigest()
        return secrets.compare_digest(calc, digest)

    # Session management
    def register_session(self, ssm: SecureStreamManagerI) -> Spotifice.SecureStreamManagerPrx:
        if not self.adapter:
            raise RuntimeError("Adapter not initialized for sessions")
        proxy = self.adapter.addWithUUID(ssm)
        identity = proxy.ice_getIdentity()
        ssm._session_identity = identity
        key = identity.name
        with self._sessions_lock:
            self._sessions[key] = ssm
        logger.info(f"Registered SecureStreamManager session: {identity}")
        return Spotifice.SecureStreamManagerPrx.uncheckedCast(proxy)

    def unregister_session(self, identity: Ice.Identity) -> None:
        if not self.adapter:
            return
        key = identity.name
        with self._sessions_lock:
            servant = self._sessions.pop(key, None)
        try:
            self.adapter.remove(identity)
            logger.info(f"Unregistered session {identity}")
        except Exception as e:
            logger.warning(f"Failed to remove session {identity}: {e}")

    # AuthManager
    def authenticate(self, media_render, username, password, current=None):
        uname = self._normalize_username(username)
        user = self.users.get(uname)
        if not user:
            raise Spotifice.AuthError(uname, "User not found")
        try:
            salt = user["salt"]
            digest = user["digest"]
        except KeyError:
            raise Spotifice.AuthError(uname, "Malformed user entry")
        if not self.verify_password(password, salt, digest):
            raise Spotifice.AuthError(uname, "Invalid password")
        logger.info(f"Authenticating user '{username}'")
        ssm_servant = SecureStreamManagerI(self, self.media_dir)
        if media_render is None:
            raise Spotifice.BadReference("bad-render", "Invalid render identity")

        ssm_servant._media_render = media_render

        created_at = user.get("created_at")
        try:
            if isinstance(created_at, (int, float)):
                created_at_val = int(created_at)
            else:
                created_at_val = int(time.time())
        except Exception:
            created_at_val = int(time.time())
        ssm_servant.user_info = Spotifice.UserInfo(
            username=uname,
            fullname=user.get("fullname", uname),
            email=user.get("email", f"{uname}@example.com"),
            is_premium=user.get("is_premium", False),
            created_at=created_at_val,
        )
        ssm_proxy = self.register_session(ssm_servant)
        return ssm_proxy

    def set_adapter(self, adapter: Ice.ObjectAdapter):
        self.adapter = adapter


def main(ic):
    properties = ic.getProperties()
    media_dir = properties.getPropertyWithDefault("MediaServer.Content", "media")
    playlists_dir = properties.getPropertyWithDefault("MediaServer.Playlists", "playlists")
    users_file = properties.getPropertyWithDefault("MediaServer.UsersFile", "users.json")

    servant = MediaServerI(Path(media_dir), Path(playlists_dir), Path(users_file))
    adapter = ic.createObjectAdapter("MediaServerAdapter")
    servant.set_adapter(adapter)
    proxy = adapter.add(servant, ic.stringToIdentity("mediaServer1"))
    logger.info(f"MediaServer ready: {proxy}")

    adapter.activate()
    ic.waitForShutdown()
    logger.info("Shutdown")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: media_server_v2.py <config-file>")
    try:
        with Ice.initialize(sys.argv[1]) as communicator:
            main(communicator)
    except KeyboardInterrupt:
        logger.info("Server interrupted by user.")
