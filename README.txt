README - Spotifice con IceGrid

Descripción
---------------
Este proyecto despliega la aplicación Spotifice (streaming de audio) usando ZeroC Ice y IceGrid en Linux.
Incluye un MediaServer, un MediaRender y un MediaControl GUI. Todo se puede iniciar fácilmente usando tmux y el Makefile proporcionado.

Requisitos
---------------
- Linux (Ubuntu recomendado)
- Python ≥ 3.10
- ZeroC Ice 3.7 o superior
- tmux instalado


Inicio rápido
-----------------
Crear nodos con tmux
Ejecuta:
    make nodes
Esto hace lo siguiente:
- Inicia sesión tmux "spotifice"
- Abre dos terminales:
    - node1 → MediaServer
    - node2 → MediaRender
- Ajusta las terminales en layout tiled
- Conéctate a la sesión tmux automáticamente

Abrir MediaControl GUI
En otra terminal (dentro de la sesión tmux):
    make gui
Esto abre la ventana del MediaControl GUI para interactuar con los renders.

Detener todo
Para cerrar nodos y GUI:
    make stop

IceGrid GUI (opcional)
--------------------------
- Se puede iniciar manualmente con:
    icegridgui
- Permite ver nodos, servidores y objetos distribuidos.
- Solo necesario para depuración o administración avanzada.

Notas
---------
- El Makefile usa tmux, por lo que todo corre en una sesión organizada, evitando múltiples terminales dispersas.
- Los nodos están configurados para ejecutarse en modo always. Esto asegura que los servidores se inicien automáticamente cuando IceGrid 
  detecte la aplicación.
- El make stop puede tardar alrededor de 1 minuto en cerrar completamente los nodos, de todas formas se podra seguir usando en la misma 
  sesion hasta que los nodos se cierren.
-Las primeras veces que se use make nodes, puede no funcionar a la perfección porque los nodos tardan un segundo en abrirse
(recomendamos volver a cerrarlos con make stop y abrirlos con make nodes de nuevo).
