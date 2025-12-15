SESSION_NAME=spotifice
RUN_SCRIPT=./run.sh

.PHONY: nodes-cliente gui stop

# Inicia los nodos y MediaControl GUI en tmux usando IcePatch2 Client
nodes-cliente:
	@echo "Sincronizando con IcePatch2 Server..."
	@icepatch2client --IcePatch2Client.Proxy="Spotifice.IcePatch2/server:tcp -h 192.168.1.137 -p 10002" ./IcePatch2
	@echo "Iniciando nodos y MediaControl GUI en tmux..."
	@bash $(RUN_SCRIPT)

# Abre solo el panel del control si ya hay sesión
gui:
	@echo "Iniciando solo MediaControl GUI en tmux..."
	tmux send-keys -t $(SESSION_NAME):0.2 "python3 IcePatch2/media_control_v2.py IcePatch2/control.config" C-m

# Para nodos y sesión tmux enviando Ctrl-C
stop:
	@echo "Deteniendo nodos..."
	-@tmux send-keys -t $(SESSION_NAME):0.0 C-c
	-@tmux send-keys -t $(SESSION_NAME):0.1 C-c
	@echo "Cerrando sesión tmux..."
	-@tmux kill-session -t $(SESSION_NAME) 2>/dev/null || true