from datetime import date

import pymysql
from flask import Blueprint, request, session
from werkzeug.security import generate_password_hash

from database import get_conn
from .util import ok, err, login_requerido, rol_requerido, registrar_auditoria, sucursal_actual, es_superadmin

# Tablas cuyos datos se vuelcan/restauran en el backup (en orden de dependencias)
_TABLAS_BACKUP = [
    "almacenes", "categorias", "proveedores", "sucursales", "usuarios",
    "productos", "lotes", "movimientos", "gastos", "ventas", "venta_detalle",
    "repartos", "reparto_detalle", "auditoria",
]

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
        sucursal_id = data.get("sucursal_id")
        if rol not in ("encargado", "admin", "superadmin"):
            rol = "encargado"
        if not es_superadmin() and rol != "encargado":
            conn.close()
            return err("Solo el superadministrador puede asignar ese rol", 403)
        if not es_superadmin():
            sucursal_id = sucursal_actual()
        if not usuario or len(password) < 4:
            conn.close()
            return err("Usuario inválido o contraseña muy corta (mínimo 4)")
        try:
            conn.execute("""
                INSERT INTO usuarios (usuario, password_hash, nombre, rol, activo, sucursal_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (usuario, generate_password_hash(password), nombre, rol,
                  data.get("activo", 1), sucursal_id))
            conn.commit()
        except pymysql.err.IntegrityError:
            conn.close()
            return err("Ya existe ese usuario")
        conn.close()
        registrar_auditoria("Usuario creado", f"Usuario {usuario} ({rol})")
        return ok(message="Usuario creado")

    if es_superadmin():
        rows = conn.execute("""
            SELECT u.id, u.usuario, u.nombre, u.rol, u.activo, u.sucursal_id, s.nombre AS sucursal_nombre
            FROM usuarios u LEFT JOIN sucursales s ON s.id = u.sucursal_id
            ORDER BY u.usuario""").fetchall()
    else:
        sid = sucursal_actual()
        rows = conn.execute("""
            SELECT u.id, u.usuario, u.nombre, u.rol, u.activo, u.sucursal_id, s.nombre AS sucursal_nombre
            FROM usuarios u LEFT JOIN sucursales s ON s.id = u.sucursal_id
            WHERE u.sucursal_id = ?
            ORDER BY u.usuario""", (sid,)).fetchall()
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
        target = conn.execute("SELECT rol FROM usuarios WHERE id = ?", (user_id,)).fetchone()
        if not target:
            conn.close()
            return err("Usuario no encontrado", 404)
        if target["rol"] == "superadmin" and not es_superadmin():
            conn.close()
            return err("No tienes permisos para eliminar a un superadministrador", 403)
        conn.execute("DELETE FROM usuarios WHERE id = ?", (user_id,))
        conn.commit()
        conn.close()
        registrar_auditoria("Usuario eliminado", f"Usuario ID {user_id}")
        return ok(message="Usuario eliminado")

    data = request.get_json() or {}
    nombre = (data.get("nombre") or "").strip()
    rol = data.get("rol", "encargado")
    activo = 1 if data.get("activo", 1) else 0
    sucursal_id = data.get("sucursal_id")
    if rol not in ("encargado", "admin", "superadmin"):
        rol = "encargado"
    target = conn.execute("SELECT rol FROM usuarios WHERE id = ?", (user_id,)).fetchone()
    if not target:
        conn.close()
        return err("Usuario no encontrado", 404)
    if not es_superadmin():
        if target["rol"] == "superadmin":
            conn.close()
            return err("No tienes permisos para editar a un superadministrador", 403)
        if rol != "encargado":
            conn.close()
            return err("Solo el superadministrador puede asignar ese rol", 403)
        sucursal_id = sucursal_actual()
    conn.execute("UPDATE usuarios SET nombre = ?, rol = ?, activo = ?, sucursal_id = ? WHERE id = ?",
                 (nombre, rol, activo, sucursal_id, user_id))
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
# Respaldos (dump SQL para MySQL)
# --------------------------------------------------------------------------
@usuarios_bp.route("/api/backup")
@login_requerido
@rol_requerido("admin")
def backup():
    from flask import Response

    def _dumpar():
        yield f"-- Backup Pollos Lucho {date.today().isoformat()}\n"
        yield "SET FOREIGN_KEY_CHECKS = 0;\n"
        conn = get_conn()
        try:
            for tabla in _TABLAS_BACKUP:
                try:
                    cols = [r["COLUMN_NAME"] for r in conn.execute(
                        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                        "ORDER BY ORDINAL_POSITION", (tabla,)).fetchall()]
                except Exception:
                    continue
                if not cols:
                    continue
                cols_sql = ", ".join(cols)
                for fila in conn.execute(
                        f"SELECT * FROM `{tabla}`").fetchall():
                    vals = ", ".join(_lit(fila[c]) for c in cols)
                    yield f"INSERT INTO `{tabla}` ({cols_sql}) VALUES ({vals});\n"
        finally:
            conn.close()
        yield "SET FOREIGN_KEY_CHECKS = 1;\n"

    registrar_auditoria("Respaldo creado", "Descarga de copia de seguridad SQL")
    return Response(_dumpar(), mimetype="application/sql",
                    headers={"Content-Disposition": "attachment; "
                             f"filename=inventario_pollos_lucho_{date.today().isoformat()}.sql"})


@usuarios_bp.route("/api/restaurar", methods=["POST"])
@login_requerido
@rol_requerido("admin")
def restaurar():
    archivo = request.files.get("archivo")
    if not archivo:
        return err("Debes seleccionar un archivo")
    sql = archivo.read().decode("utf-8", errors="replace")
    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        for stmt in _divide_sentencias(sql):
            s = stmt.strip()
            if not s:
                continue
            cur.execute(s)
        conn.commit()
    except pymysql.err.IntegrityError:
        conn.rollback()
        raise
    finally:
        conn.close()
    registrar_auditoria("Base de datos restaurada", "Se restauró la base de datos desde un respaldo")
    return ok(message="Base de datos restaurada correctamente")


def _lit(val):
    """Escapa un valor de Python a literal SQL."""
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float)):
        return repr(val)
    return "'" + str(val).replace("'", "''") + "'"


def _divide_sentencias(sql):
    """Divide un script SQL en sentencias individuales respetando comillas."""
    sentencias = []
    buf = []
    in_s = False
    for ch in sql:
        if ch == "'" and (not buf or buf[-1] != "\\"):
            in_s = not in_s
        buf.append(ch)
        if ch == ";" and not in_s:
            sentencias.append("".join(buf))
            buf = []
    if "".join(buf).strip():
        sentencias.append("".join(buf))
    return sentencias
