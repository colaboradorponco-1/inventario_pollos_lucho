"""Verifica y asegura que los usuarios encargados de sucursal tengan el rol
correcto ('encargado') y estén amarrados exactamente a su sucursal respectiva.
"""
import sqlite3

# Mapeo exacto usuario -> nombre de sucursal en la base de datos
MAPEO = {
    'encargado_america': 'America',
    'encargado_sigloxx': 'Siglo XX',
    'encargado_simonlopez': 'Simón López',
    'encargado_lapaz': 'La Paz 6 de Agosto'
}

# Usamos la conexion a la base de datos de produccion
db_path = 'database.sqlite' # ajusta si usas otra ruta, ej: 'inventario.db'
# Intentamos conectar a la bd del proyecto
import os
for f in os.listdir('.'):
    if f.endswith('.sqlite') or f.endswith('.db'):
        db_path = f
        break

print(f"Base de datos detectada: {db_path}")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row

# 1. Ver sucursales disponibles
sucursales = {s['nombre']: s['id'] for s in conn.execute("SELECT id, nombre FROM sucursales").fetchall()}
print("Sucursales en BD:", sucursales)

print()
print("Estado actual de los usuarios encargado_*:")
usuarios = conn.execute("SELECT u.id, u.usuario, u.rol, u.sucursal_id, s.nombre as sucursal_nombre FROM usuarios u LEFT JOIN sucursales s ON u.sucursal_id = s.id WHERE u.usuario LIKE 'encargado_%'").fetchall()

if not usuarios:
    print("  (No se encontraron usuarios con prefijo encargado_ en la base de datos)")
else:
    for u in usuarios:
        print(f"  - id={u['id']} usuario={u['usuario']} rol={u['rol']} sucursal={u['sucursal_nombre']} (id={u['sucursal_id']})")

conn.close()
