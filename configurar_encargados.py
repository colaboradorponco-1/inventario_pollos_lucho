"""Script definitivo para configurar todos los usuarios de sucursal,
incluyendo los segundos encargados para America, Siglo XX, Simon Lopez y La Paz,
mas el personal logistico.
"""
from database import get_conn
from werkzeug.security import generate_password_hash

P_ENCARGADO = "encargado2026"
P_PERSONAL = "personal2026"

CONFIG_USUARIOS = {
    # America (2 encargados + logistica)
    'encargado_america': ('encargado', 'America', P_ENCARGADO),
    'encargado_america2': ('encargado', 'America', P_ENCARGADO),
    'preparador_america': ('preparador', 'America', P_PERSONAL),
    'repartidor_america': ('repartidor', 'America', P_PERSONAL),
    
    # Siglo XX (2 encargados)
    'encargado_sigloxx': ('encargado', 'Siglo XX', P_ENCARGADO),
    'encargado_sigloxx2': ('encargado', 'Siglo XX', P_ENCARGADO),
    
    # Simon Lopez (2 encargados + logistica)
    'encargado_simonlopez': ('encargado', 'Simón López', P_ENCARGADO),
    'encargado_simonlopez2': ('encargado', 'Simón López', P_ENCARGADO),
    'preparador_simonlopez': ('preparador', 'Simón López', P_PERSONAL),
    'repartidor_simonlopez': ('repartidor', 'Simón López', P_PERSONAL),
    
    # La Paz 6 de Agosto (2 encargados)
    'encargado_lapaz': ('encargado', 'La Paz 6 de Agosto', P_ENCARGADO),
    'encargado_lapaz2': ('encargado', 'La Paz 6 de Agosto', P_ENCARGADO),
}

def configurar_todos():
    conn = get_conn()
    sucs = {s['nombre']: s['id'] for s in conn.execute("SELECT id, nombre FROM sucursales").fetchall()}
    print("Sucursales disponibles en BD:", sucs)
    
    for usuario, (rol, nombre_suc, password_plana) in CONFIG_USUARIOS.items():
        suc_id = sucs.get(nombre_suc)
        if not suc_id:
            print(f"[AVISO] Sucursal '{nombre_suc}' no encontrada para {usuario}")
            continue
            
        usr = conn.execute("SELECT id FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
        pwd_hash = generate_password_hash(password_plana)
        
        if usr:
            conn.execute("UPDATE usuarios SET rol = ?, sucursal_id = ? WHERE usuario = ?", (rol, suc_id, usuario))
            print(f"[OK] {usuario} actualizado (rol: {rol}, sucursal: {nombre_suc})")
        else:
            conn.execute("INSERT INTO usuarios (usuario, password, rol, sucursal_id) VALUES (?, ?, ?, ?)", (usuario, pwd_hash, rol, suc_id))
            print(f"[CREADO] {usuario} (rol: {rol}, sucursal: {nombre_suc}, pass: {password_plana})")
            
    conn.commit()
    conn.close()
    print("¡Configuracion de usuarios completada con exito!")

if __name__ == '__main__':
    configurar_todos()
