"""Script ampliado para configurar los encargados de sucursal y ahora
tambien los usuarios de Reparto y Preparacion para America y Simon Lopez.
"""
from database import get_conn
from werkzeug.security import generate_password_hash

PASSWORD_DEFAULT = "personal2026"

# Mapeo completo de usuarios, rol y sucursal correspondiente
CONFIG_USUARIOS = {
    # Encargados de almacen por sucursal
    'encargado_america': ('encargado', 'America'),
    'encargado_sigloxx': ('encargado', 'Siglo XX'),
    'encargado_simonlopez': ('encargado', 'Simón López'),
    'encargado_lapaz': ('encargado', 'La Paz 6 de Agosto'),
    
    # Personal especifico de America
    'repartidor_america': ('repartidor', 'America'),
    'preparador_america': ('preparador', 'America'),
    
    # Personal especifico de Simon Lopez
    'repartidor_simonlopez': ('repartidor', 'Simón López'),
    'preparador_simonlopez': ('preparador', 'Simón López'),
}

def asegurar_todos_los_usuarios():
    conn = get_conn()
    
    sucs = {s['nombre']: s['id'] for s in conn.execute("SELECT id, nombre FROM sucursales").fetchall()}
    print("Sucursales disponibles en BD:", sucs)
    
    for usuario, (rol, nombre_suc) in CONFIG_USUARIOS.items():
        suc_id = sucs.get(nombre_suc)
        if not suc_id:
            print(f"[AVISO] No se encontro la sucursal '{nombre_suc}' en la BD para el usuario {usuario}.")
            continue
            
        usr = conn.execute("SELECT id FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
        pwd_hash = generate_password_hash(PASSWORD_DEFAULT)
        
        if usr:
            conn.execute("""
                UPDATE usuarios 
                SET rol = ?, sucursal_id = ? 
                WHERE usuario = ?
            """, (rol, suc_id, usuario))
            print(f"[OK] Usuario '{usuario}' actualizado (rol: {rol}, sucursal: {nombre_suc}).")
        else:
            conn.execute("""
                INSERT INTO usuarios (usuario, password, rol, sucursal_id)
                VALUES (?, ?, ?, ?)
            """, (usuario, pwd_hash, rol, suc_id))
            print(f"[CREADO] Usuario '{usuario}' creado (rol: {rol}, sucursal: {nombre_suc}, pass: {PASSWORD_DEFAULT}).")
            
    conn.commit()
    conn.close()
    print("¡Listo! Todos los perfiles configurados con exito.")

if __name__ == '__main__':
    try:
        asegurar_todos_los_usuarios()
    except Exception as e:
        print("Error al configurar usuarios:", e)
