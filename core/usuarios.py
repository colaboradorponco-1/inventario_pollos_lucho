import sqlite3
from datetime import date

from flask import Blueprint, request, session
from werkzeug.security import generate_password_hash

from database import get_conn, DB_PATH
from .util import ok, err, login_requerido, rol_requerido, registrar_auditoria

usuarios_bp = Blueprint("usuarios", __name__)


# --------------------------------------------------------------------------
# Usuarios (solo admin)
# --------------------------------------------------------------------------
@usuarios_bp.route("/api/usuarios", methods=["GET", "POST"])
@login_requerido
@rol_requerido("admin")
def usuarios():
    conn = get_conn()
    if request.method == "POST":
        data = request.get_json() or {}
        usuario = (data.get("usuario") or "").strip()
        nombre = (data.get("nombre") or "").strip()
        rol = data.get("rol", "encargado")
        password = data.get("password") or ""
        if not usuario or len(password) < 4:
            conn.close()
            return err("Usuario inválido o contraseña muy corta (mínimo 4)")
        try:
            conn.execute("""
                INSERT INTO usuarios (usuario, password_hash, nombre, rol, activo)
                VALUES (?, ?, ?, ?, 1)
            """, (usuario, generate_password_hash(password), nombre, rol))
            conn.commit()
        except sqlite3.IntegrityError:
            conn.close()
            return err("Ya existe ese usuario")
        conn.close()
        registrar_auditoria("Usuario creado", f"Usuario {usuario} ({rol})")
        return ok(message="Usuario creado")

    rows = conn.execute("SELECT id, usuario, nombre, rol, activo FROM usuarios ORDER BY usuario").fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@usuarios_bp.route("/api/usuarios/<int:user_id>", methods=["PUT", "DELETE"])
@login_requerido
@rol_requerido("admin")
def usuario(user_id):
    conn = get_conn()
    if request.method == "DELETE":
        if user_id == session["user_id"]:
            conn.close()
            return err("No puedes eliminarte a ti mismo")
        conn.execute("DELETE FROM usuarios WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
        registrar_auditoria("Usuario eliminado", f"Usuario ID {user_id}")
        return ok(message="Usuario eliminado")

    data = request.get_json() or {}
    nombre = (data.get("nombre") or "").strip()
    rol = data.get("rol", "encargado")
    activo = 1 if data.get("activo", 1) else 0
    conn.execute("UPDATE usuarios SET nombre = ?, rol = ?, activo = ? WHERE id = ?",
                 (nombre, rol, activo, user_id))
    password = data.get("password") or ""
    if password:
        if len(password) < 4:
            conn.close()
            return err("La contraseña debe tener al menos 4 caracteres")
        conn.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?",
                     (generate_password_hash(password), user_id))
    conn.commit()
    conn.close()
    registrar_auditoria("Usuario actualizado", f"Usuario ID {user_id}")
    return ok(message="Usuario actualizado")


# --------------------------------------------------------------------------
# Auditoría (solo admin)
# --------------------------------------------------------------------------
@usuarios_bp.route("/api/auditoria")
@login_requerido
@rol_requerido("admin")
def auditoria():
    conn = get_conn()
    limite = min(int(request.args.get("limite", 200)), 1000)
    rows = conn.execute("""
        SELECT * FROM auditoria ORDER BY id DESC LIMIT ?
    """, (limite,)).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


# --------------------------------------------------------------------------
# Copias de seguridad
# --------------------------------------------------------------------------
@usuarios_bp.route("/api/backup")
@login_requerido
@rol_requerido("admin")
def backup():
    if not DB_PATH.exists():
        return err("No existe la base de datos", 404)
    registrar_auditoria("Respaldo creado", "Descarga de copia de seguridad")
    from flask import send_file
    return send_file(
        DB_PATH, as_attachment=True,
        download_name=f"inventario_pollos_lucho_{date.today().isoformat()}.db")


@usuarios_bp.route("/api/restaurar", methods=["POST"])
@login_requerido
@rol_requerido("admin")
def restaurar():
    archivo = request.files.get("archivo")
    if not archivo:
        return err("Debes seleccionar un archivo")
    respaldo_automatico = DB_PATH.with_suffix(".db.bak_antiguo")
    conn = get_conn()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    DB_PATH.replace(respaldo_automatico)
    archivo.save(DB_PATH)
    registrar_auditoria("Base de datos restaurada", "Se restauró la base de datos desde un respaldo")
    return ok(message="Base de datos restaurada correctamente")
