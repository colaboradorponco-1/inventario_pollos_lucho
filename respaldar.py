"""Respaldo de la base de datos pollos_lucho a un archivo .sql (UTF-8).

Genera el mismo dump que descarga la aplicacion (`core/backup.py`): TODAS las
tablas de SHOW TABLES, con esquema (DROP + CREATE) y datos. Se puede restaurar
sobre una base vacia o sobre una que ya tiene datos.

Antes este script llevaba su propio escapado de valores, distinto del de la API
y con dos fallos: `repr(Decimal(...))` generaba "Decimal('1.50')", que no es SQL
valido, y las comillas se escapaban con barra invertida, que depende del modo de
MySQL. Ahora usa el mismo modulo que la API, asi que no pueden divergir.

Las credenciales se leen desde .env / variables de entorno (ver database.py).
Uso:
    python respaldar.py                       # respaldo automatico con fecha
    python respaldar.py salida/mi_respaldo.sql
"""
import datetime
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from core.backup import nombre_respaldo, volcar_a_archivo  # noqa: E402
from database import get_conn  # noqa: E402

# El destino se puede pasar como argumento. Si no, se usa `respaldos/` en la raiz.
# OJO: `deploy/respaldar_respaldo.sh` (el cron de las 4am) espera encontrar el
# archivo en la RAIZ del proyecto y aborta con "ERROR: no se genero" si no esta
# ahi. Si cambias la carpeta por defecto, hay que tocar tambien ese script.
if len(sys.argv) > 1:
    destino = sys.argv[1]
else:
    destino = os.path.join(BASE, "respaldos", nombre_respaldo("manual"))

conn = get_conn()
try:
    bytes_, _ = volcar_a_archivo(conn, destino)
finally:
    conn.close()

print(f"Respaldo guardado: {destino}")
print(f"Tamano: {bytes_ / 1024:.1f} KB")
print(f"Fecha:  {datetime.datetime.now():%Y-%m-%d %H:%M:%S}")
