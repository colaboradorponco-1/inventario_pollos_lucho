# Configuración de Gunicorn para Pollos Lucho (Ubuntu / DigitalOcean)
# 1 worker × 16 threads (gthread). El trabajo es de I/O hacia MySQL con poca CPU,
# y tener UN SOLO worker mantiene compartida la protección contra fuerza bruta
# (_fallos, en memoria). Si algún día necesitas >1 worker, mueve la cuenta de
# intentos a Redis (session fallida por IP centralizada).
bind = "127.0.0.1:5000"
workers = 1
worker_class = "gthread"
threads = 16
timeout = 120         # tiempo máximo por request (registro de movimientos grandes)
keepalive = 5
preload_app = True    # init_db corre 1 sola vez al inicio, luego fork
errorlog = "/opt/pollos-lucho/logs/gunicorn.err"
accesslog = "/opt/pollos-lucho/logs/access.log"
loglevel = "info"