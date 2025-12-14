Spotifice con IceGrid
=====================

Descripción
-----------
Spotifice es una aplicación de streaming de audio que utiliza ZeroC Ice e IceGrid en Linux.  
Incluye los siguientes componentes:

- **MediaServer**: gestiona la reproducción y almacenamiento de audio.  
- **MediaRender**: se encarga de renderizar y reproducir el audio.  
- **MediaControl GUI**: interfaz gráfica para controlar los renders.  

El proyecto está diseñado para iniciarse fácilmente usando **tmux** y el **Makefile** proporcionado.

Requisitos
-----------
- Linux (Ubuntu recomendado)  
- Python ≥ 3.10  
- ZeroC Ice 3.7 o superior  
- tmux instalado  

Inicio rápido
-------------

### Iniciar nodos del servidor
Ejecuta:
    make nodes-Server

Esto hará lo siguiente:
- Crea una sesión tmux llamada `spotifice`.  
- Abre tres paneles:
    - `0.0` → MediaServer  
    - `0.1` → MediaRender  
    - `0.2` → libre (puede usarse para GUI o comandos adicionales)  
- Ajusta el layout en modo **tiled**.  
- Se conecta automáticamente a la sesión tmux.  

### Iniciar nodos del cliente
Ejecuta:
    make nodes-Client

Esto hará lo siguiente:
- Sincroniza el cliente con el servidor IcePatch2 usando la IP y puerto configurados (`SERVER_IP` y `SERVER_PORT`).  
- Inicia los nodos del cliente en tmux, igual que en el servidor.  

### Abrir MediaControl GUI
Ejecuta dentro de la sesión tmux:
    make gui

Esto abrirá la interfaz gráfica `MediaControl GUI` para interactuar con los renders.

Detener todo
------------
Ejecuta:
    make stop

- Envía Ctrl-C a los nodos en ejecución.  
- Cierra la sesión tmux `spotifice`.  
- El cierre completo puede tardar aproximadamente un minuto, durante el cual se puede seguir usando la sesión.  

IceGrid GUI (opcional)
----------------------
- Se puede iniciar manualmente con:
    icegridgui
- Permite visualizar nodos, servidores y objetos distribuidos.  
- Útil principalmente para depuración o administración avanzada.  

Notas importantes
-----------------
- El Makefile organiza todo en **una sola sesión tmux**, evitando múltiples terminales dispersas.  
- Los nodos están configurados en modo **always**, asegurando que se inicien automáticamente cuando IceGrid detecta la aplicación.  
- Cada máquina debe conocer la IP de las demás y ajustar `control.config` y Makefile si cambia la red.  
- Los puertos de IcePatch2 son dinámicos; si se desea una conexión directa fija, deben ajustarse manualmente en el Makefile.  

Configuración avanzada
---------------------
Valores por defecto en Makefile y `run.sh`:

- `SERVER_IP=0.0.0.0` → IP del servidor IcePatch2.  
- `SERVER_PORT=10000` → puerto del servidor IcePatch2. 

Arranque rápido entre ordenadores
---------------------------------

1. Servidor
   - Iniciar IcePatch2.
   - Arrancar nodos (MediaServer y MediaRender) con:
       make nodes-Server

2. Cliente
   - Sincronizar con el servidor y arrancar nodos con:
       make nodes-Client

3. Abrir MediaControl GUI (igual para servidor o cliente)
   - Inicia la interfaz para controlar los renders:
       make gui

4. Detener todo
   - Cierra nodos y sesión tmux cuando se termine de usar (igual para servidor o cliente):
       make stop
