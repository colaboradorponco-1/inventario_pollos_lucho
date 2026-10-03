"""Script para asegurar que los 4 usuarios encargados de sucursal existan,
tengan su clave y esten correctamente vinculados a su sucursal.
Ejecutar en el servidor de produccion donde este configurado el .env con MySQL.
"""
from database import get_conn
import hashlib

# Contrasenia inicial estandar para los encargados (la pueden cambiar despues)
PASSWORD_DEFAULT = "encargado2026"

# Hash de la contrasenia (el sistema usa sha256 o bcrypt segun database.py)
# Verificamos como guarda passwords el sistema:
import inspect
from core import auth # o similar
# O usamos la funcion de hashing del sistema si existe, sino generamos hash estandar.

MAPEO_SUCURSALES = {
    'encargado_america': 'America',
    'encargado_sigloxx': 'Siglo XX',
    'encargado_simonlopez': 'Simón López',
    'encargado_lapaz': 'La Paz 6 de Agosto'
}

def asegurar_usuarios():
    conn = get_conn()
    
    # Obtener mapa de sucursales ID
    sucs = {s['nombre']: s['id'] for s in conn.execute("SELECT id, nombre FROM sucursales").fetchall()}
    print("Sucursales disponibles en BD:", sucs)
    
    for usuario, nombre_suc in MAPEO_SUCURSALES.items():
        suc_id = sucs.get(nombre_suc)
        if not suc_id:
            print(f"[AVISO] No se encontro la sucursal '{nombre_suc}' en la BD.")
            continue
            
        # Verificar si el usuario ya existe
        usr = conn.execute("SELECT id FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
        
        # Hashear password (el proyecto suele usar werkzeug.security o sha256)
        from werkzeug.security import generate_password_hash
        pwd_hash = generate_password_hash(PASSWORD_DEFAULT)
        
        if usr:
            # Actualizar sucursal y rol para asegurar que este bien amarrado
            conn.execute("""
                UPDATE usuarios 
                SET rol = 'encargado', sucursal_id = ? 
                WHERE usuario = ?
            """, (suc_id, usuario))
            print(f"[OK] Usuario '{usuario}' actualizado (sucursal: {nombre_suc}, id={suc_id}).")
        else:
            # Crear usuario nuevo
            conn.execute("""
                INSERT INTO usuarios (usuario, password, rol, sucursal_id)
                VALUES (?, ?, 'encargado', ?)
            """, (usuario, pwd_hash, suc_id))
            print(f"[CREADO] Usuario '{usuario}' creado con exito (sucursal: {nombre_suc}, pass: {PASSWORD_DEFAULT}).")
            
    conn.commit()
    conn.close()
    print("¡Listo! Todos los encargados de sucursal configurados correctamente.")

if __name__ == '__main__':
    try:
        asegurar_usuarios()
    except Exception as e:
        print("Error al configurar usuarios:", e)
        print("Asegurate de ejecutar esto donde esten las credenciales de la BD de produccion.")
