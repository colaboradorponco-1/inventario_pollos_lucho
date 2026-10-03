"""Verifica los usuarios leds desde .env y conexion MySQL real.
"""
import io
import os
import sys
from dotenv import load_dotenv
load_dotenv()

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from database import get_conn

conn = get_conn()
print("Sucursales en la base de datos:")
for s in conn.execute("SELECT id, nombre FROM sucursales").fetchall():
    print(f"  id={s['id']} -> {s['nombre']}")

print()
print("Usuarios encargado_* en la base de datos:")
usrs = conn.execute("SELECT id, usuario, rol, sucursal_id FROM usuarios WHERE usuario LIKE 'encargado_%'").fetchall()

if not usrs:
    print("  (No hay usuarios con prefijo 'encargado_' en la BD de produccion)")
else:
    for u in usrs:
        print(f"  id={u['id']} usuario={u['usuario']} rol={u['rol']} sucursal_id={u['sucursal_id']}")

conn.close()
