#!/usr/bin/env python3
import logging
import sys
from contextlib import contextmanager
from collections import deque
import threading

import Ice

from gst_player import GstPlayer

Ice.loadSlice('-I{} spotifice_v2.ice'.format(Ice.getSliceDir()))
import Spotifice  # type: ignore # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MediaRender")


class MediaRenderI(Spotifice.MediaRender):
    def __init__(self, player: GstPlayer):
        self.player = player
        self.server: Spotifice.MediaServerPrx | None = None
        self.secure_stream: Spotifice.SecureStreamManagerPrx | None = None

        self.current_track: Spotifice.TrackInfo | None = None
        self.current_playlist: Spotifice.Playlist | None = None
        self.current_track_index: int = 0

        self.state = Spotifice.PlaybackState.STOPPED
        self.repeat = False

        # history: deque where last element is current track id
        self.history: deque[str] = deque(maxlen=500)

        # Locks
        self._state_lock = threading.RLock()
        # guard for exhaustion handling to avoid concurrent handlers
        self._handling_exhaustion = False

    # --- Helpers ---
    def ensure_player_stopped(self):
        # Don't rely solely on player.is_playing (async backends).
        # Use state flag as authoritative.
        with self._state_lock:
            if self.state == Spotifice.PlaybackState.PLAYING:
                raise Spotifice.PlayerError(item="", reason="Already playing")

    def ensure_server_bound(self):
        if not self.server:
            raise Spotifice.BadReference(item="", reason="No MediaServer bound")
        if not self.secure_stream:
            raise Spotifice.BadReference(item="", reason="No authenticated session")

    def _push_history(self, track_id: str):
        if not track_id:
            return
        if not self.history or self.history[-1] != track_id:
            self.history.append(track_id)

    def _pop_history(self) -> str | None:
        # Require at least two elements: previous and current
        if len(self.history) < 2:
            return None
        try:
            self.history.pop()  # remove current
            return self.history.pop()  # return previous (also removed)
        except Exception:
            return None

    def _reset_history(self):
        self.history.clear()

    # --- RenderConnectivity ---
    def bind_media_server(self, media_server, secure_stream, current=None):
        with self._state_lock:
            self.server = media_server
            self.secure_stream = secure_stream
            self._reset_history()
            logger.info("Bound to MediaServer with SecureStreamManager session")

    def unbind_media_server(self, current=None):
        with self._state_lock:
            # best-effort stop
            try:
                self.stop(current)
            except Exception:
                pass
            # close remote session (best-effort)
            if self.secure_stream:
                try:
                    self.secure_stream.close()
                except Exception:
                    logger.debug("Ignoring exception closing secure session")
            # clear local state
            self.server = None
            self.secure_stream = None
            self.current_playlist = None
            self.current_track = None
            self.current_track_index = 0
            self._reset_history()
            logger.info("Unbound MediaServer and closed session")

    # --- ContentManager ---
    def load_track(self, track_id, current=None):
        # SPEC: If player was playing, it should continue playing the new track.
        # keep_playing_state will stop and re-play if necessary.
        with self._state_lock:
            self.ensure_server_bound()
            prev_id = self.current_track.id if self.current_track else None
            if prev_id and prev_id != track_id:
                self._push_history(prev_id)
            # loading a single track should not change playlist
            self.current_playlist = None
            self.current_track_index = 0
            with self.keep_playing_state(current):
                # bubble up TrackError/IOError
                self.current_track = self.server.get_track_info(track_id)
            logger.info(f"Loaded track: {self.current_track.id}")

    def load_playlist(self, playlist_id, current=None):
        # SPEC: load_playlist MUST load first track but NOT start playback.
        # Important: do NOT use keep_playing_state here (spec says do not start playback).
        with self._state_lock:
            self.ensure_server_bound()
            playlist = self.server.get_playlist(playlist_id)
            if not playlist.track_ids:
                raise Spotifice.PlaylistError(item=playlist_id, reason="Empty playlist")
            self.current_playlist = playlist
            self.current_track_index = 0
            # Reset history per spec when loading playlist
            self._reset_history()
            first_track_id = playlist.track_ids[0]
            # load first track info but do NOT start playback
            self.current_track = self.server.get_track_info(first_track_id)
            # Add the loaded track to history (current)
            self._push_history(self.current_track.id)
            logger.info(f"Loaded playlist '{playlist.name}' with first track '{first_track_id}'")

    def get_current_track(self, current=None):
        # read-only, safe
        return self.current_track

    # --- PlaybackController ---
    @contextmanager
    def keep_playing_state(self, current):
        """Temporarily stop playback while switching tracks, then resume if was playing."""
        with self._state_lock:
            was_playing = self.state == Spotifice.PlaybackState.PLAYING
            if was_playing:
                try:
                    self.player.stop()
                except Exception:
                    logger.debug("player.stop() failed inside keep_playing_state")
        try:
            yield
        finally:
            if was_playing:
                try:
                    # call play which will re-open stream if needed
                    self.play(current)
                except Exception:
                    # if resume fails, ensure consistent stopped state
                    with self._state_lock:
                        self.state = Spotifice.PlaybackState.STOPPED

    def _on_track_exhausted(self, current):
        # Avoid concurrent exhaustion handling
        with self._state_lock:
            if self._handling_exhaustion:
                return
            self._handling_exhaustion = True
        try:
            if not self.current_playlist:
                self.stop(current)
                return
            next_index = self.current_track_index + 1
            if next_index >= len(self.current_playlist.track_ids):
                if self.repeat:
                    next_index = 0
                else:
                    self.stop(current)
                    return
            next_track_id = self.current_playlist.track_ids[next_index]
            with self.keep_playing_state(current):
                try:
                    track_info = self.server.get_track_info(next_track_id)
                except (Spotifice.TrackError, Spotifice.IOError) as e:
                    logger.warning(f"Failed to load next track '{next_track_id}': {e}")
                    self.stop(current)
                    return
                with self._state_lock:
                    self.current_track = track_info
                    self.current_track_index = next_index
                    self._push_history(self.current_track.id)
        finally:
            with self._state_lock:
                self._handling_exhaustion = False

    def play(self, current=None):
        # Prevent concurrent play calls and double playback
        with self._state_lock:
            self.ensure_server_bound()
            # If already playing, ignore repeated play requests (idempotent)
            if self.state == Spotifice.PlaybackState.PLAYING:
                if self.state == Spotifice.PlaybackState.PLAYING:
                    raise Spotifice.PlayerError("","Already playing")


            # resume from paused
            if self.state == Spotifice.PlaybackState.PAUSED:
                try:
                    if hasattr(self.player, "resume"):
                        self.player.resume()
                        self.state = Spotifice.PlaybackState.PLAYING
                        return
                    else:
                        # If no resume, we will re-open the stream below
                        pass
                except Exception as e:
                    raise Spotifice.PlayerError(item="", reason=f"Failed to resume playback: {e}")

            # fresh play
            # Ensure we aren't "already playing" at backend (use state as authoritative)
            # ensure_player_stopped checks state flag
            self.ensure_player_stopped()
            if not self.current_track:
                raise Spotifice.TrackError(item="", reason="No track loaded")

            # open remote stream for this session (server will close previous stream if any)
            try:
                self.secure_stream.open_stream(self.current_track.id)
            except Spotifice.StreamError as e:
                # couldn't open remote stream
                raise

            # chunk provider: non-blocking exhaustion handling
            def get_chunk_hook(chunk_size):
                try:
                    data = self.secure_stream.get_audio_chunk(chunk_size)
                except Spotifice.StreamError:
                    data = b""
                if not data:
                    # call exhaustion logic in background to avoid blocking player's thread
                    try:
                        t = threading.Thread(target=self._on_track_exhausted, args=(current,), daemon=True)
                        t.start()
                    except Exception:
                        logger.debug("Failed to spawn exhaustion handler thread")
                    return b""
                return data

            # configure player and start
            self.player.configure(get_chunk_hook)
            if not self.player.confirm_play_starts():
                raise Spotifice.PlayerError(item="", reason="Failed to confirm playback")
            self.state = Spotifice.PlaybackState.PLAYING
            self._push_history(self.current_track.id)
            logger.info(f"Started playback of {self.current_track.id}")

    def pause(self, current=None):
        with self._state_lock:
            if self.state != Spotifice.PlaybackState.PLAYING:
                raise Spotifice.PlayerError(item="", reason="Not playing")
            try:
                if hasattr(self.player, "pause"):
                    self.player.pause()
                else:
                    self.player.stop()
                self.state = Spotifice.PlaybackState.PAUSED
            except Exception as e:
                raise Spotifice.PlayerError(item="", reason=f"Failed to pause: {e}")

    def stop(self, current=None):
        with self._state_lock:
            # Close remote stream but ignore errors
            if self.secure_stream:
                try:
                    self.secure_stream.close_stream()
                except Exception:
                    logger.debug("Ignoring exception closing remote stream")
            try:
                self.player.stop()
            except Exception as e:
                # Lanzar PlayerError si no se puede detener el reproductor
                raise Spotifice.PlayerError(item="", reason=f"Failed to stop player: {e}")
            self.state = Spotifice.PlaybackState.STOPPED
            logger.info("Stopped playback")


    def get_status(self, current=None):
        with self._state_lock:
            current_track_id = self.current_track.id if self.current_track else ""
            return Spotifice.PlaybackStatus(
                state=self.state,
                current_track_id=current_track_id,
                repeat=self.repeat,
            )

    def next(self, current=None):
        with self._state_lock:
            if not self.current_playlist:
                return
            next_index = self.current_track_index + 1
            if next_index >= len(self.current_playlist.track_ids):
                if self.repeat:
                    next_index = 0
                else:
                    return
            next_track_id = self.current_playlist.track_ids[next_index]

        was_playing = self.state == Spotifice.PlaybackState.PLAYING

        # load new track
        with self.keep_playing_state(current):
            try:
                track_info = self.server.get_track_info(next_track_id)
            except (Spotifice.TrackError, Spotifice.IOError):
                logger.warning(f"Failed to load next track '{next_track_id}'")
                return
            with self._state_lock:
                self.current_track = track_info
                self.current_track_index = next_index
                self._push_history(self.current_track.id)

        # Force play of new track if player was previously playing
        if was_playing:
            # reset state to STOPPED para que play() funcione
            with self._state_lock:
                self.state = Spotifice.PlaybackState.STOPPED
            self.play(current)


    def previous(self, current=None):
        prev_id = self._pop_history()
        if not prev_id:
            return

        keep_playlist = False
        new_index = 0
        with self._state_lock:
            if self.current_playlist and prev_id in self.current_playlist.track_ids:
                keep_playlist = True
                new_index = self.current_playlist.track_ids.index(prev_id)

        was_playing = self.state == Spotifice.PlaybackState.PLAYING

        with self.keep_playing_state(current):
            try:
                track_info = self.server.get_track_info(prev_id)
            except (Spotifice.TrackError, Spotifice.IOError):
                logger.warning(f"Failed to load previous track '{prev_id}'")
                return
            with self._state_lock:
                self.current_track = track_info
                if keep_playlist:
                    self.current_track_index = new_index
                else:
                    self.current_playlist = None
                    self.current_track_index = 0
                self._push_history(self.current_track.id)

        if was_playing:
            with self._state_lock:
                self.state = Spotifice.PlaybackState.STOPPED
            self.play(current)

    def set_repeat(self, value, current=None):
        with self._state_lock:
            self.repeat = bool(value)


def main(ic, player):
    servant = MediaRenderI(player)
    adapter = ic.createObjectAdapter("MediaRenderAdapter")
    proxy = adapter.add(servant, ic.stringToIdentity("mediaRender1"))
    logger.info(f"MediaRender: {proxy}")
    adapter.activate()
    ic.waitForShutdown()
    logger.info("Shutdown")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: media_render_v2.py <config-file>")
    player = GstPlayer()
    player.start()
    try:
        with Ice.initialize(sys.argv[1]) as communicator:
            main(communicator, player)
    except KeyboardInterrupt:
        logger.info("Server interrupted by user.")
    finally:
        player.shutdown()
