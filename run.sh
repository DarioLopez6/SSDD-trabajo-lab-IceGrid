#!/bin/bash

SESSION_NAME=spotifice
MODE=$1  # Puede ser: nodes

# Crear sesión solo si no existe
tmux has-session -t $SESSION_NAME 2>/dev/null
if [ $? != 0 ]; then
    tmux new-session -d -s $SESSION_NAME

    # Dividir ventanas en paneles
    tmux split-window -h -t $SESSION_NAME
    tmux split-window -v -t $SESSION_NAME

    if [ "$MODE" = "nodes" ]; then
        tmux send-keys -t $SESSION_NAME:0.0 "icegridnode --Ice.Config=node1.config" C-m
        tmux send-keys -t $SESSION_NAME:0.1 "icegridnode --Ice.Config=node2.config" C-m
        # El panel 0.2 queda vacío
    fi
fi

# Adjuntar sesión
tmux attach -t $SESSION_NAME
