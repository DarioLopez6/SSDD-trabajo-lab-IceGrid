SESSION_NAME=spotifice
RUN_SCRIPT=./run.sh
SERVER_IP=10.67.125.49
SERVER_PORT=10002

.PHONY: nodes gui stop

# Inicia los nodos del servidor
nodes-Server:
	@echo "Iniciando solo nodos en tmux..."
	@bash $(RUN_SCRIPT) nodes

# Inicia los nodos del cliente
nodes-Client:
	@echo "Sincronizando con IcePatch2 Server..."
	@icepatch2client --IcePatch2Client.Proxy="Spotifice.IcePatch2/server:tcp -h $(SERVER_IP) -p $(SERVER_PORT)" .
	@echo "Iniciando nodos del cliente en tmux..."
	@bash $(RUN_SCRIPT) nodes

# Abre el panel de media control GUI
gui:
	@echo "Iniciando solo MediaControl GUI en tmux..."
	tmux send-keys -t $(SESSION_NAME):0.2 "python3 media_control_v2.py control.config" C-m

# Para nodos y sesión tmux enviando Ctrl-C
stop:
	@echo "Deteniendo nodos..."
	-@tmux send-keys -t $(SESSION_NAME):0.0 C-c
	-@tmux send-keys -t $(SESSION_NAME):0.1 C-c
	@echo "Cerrando sesión tmux..."
	-@tmux kill-session -t $(SESSION_NAME) 2>/dev/null || true
