from flask import Blueprint, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_conn
from .util import ok, err, login_requerido, registrar_auditoria

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/api/login", methods=["POST"])
def login():
    data = request.get_json() or {}
    usuario = (data.get("usuario") or "").strip()
    password = data.get("password") or ""
    conn = get_conn()
    row = conn.execute("SELECT * FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
    conn.close()
    if not row or not check_password_hash(row["password_hash"], password):
        return err("Usuario o contraseña incorrectos")
    if not row["activo"]:
        return err("Usuario desactivado. Contacta al administrador")
    session["user_id"] = row["id"]
    session["usuario"] = row["usuario"]
    session["nombre"] = row["nombre"]
    session["rol"] = row["rol"]
    registrar_auditoria("Inicio de sesión", f"Usuario {row['usuario']} ingresó al sistema")
    return ok({"usuario": row["usuario"], "nombre": row["nombre"], "rol": row["rol"]},
              message="Bienvenido")


@auth_bp.route("/api/logout", methods=["POST"])
def logout():
    registrar_auditoria("Cierre de sesión", f"Usuario {session.get('usuario', '')} salió del sistema")
    session.clear()
    return ok(message="Sesión cerrada")


@auth_bp.route("/api/sesion")
def sesion():
    if "user_id" not in session:
        return err("Sin sesión", 401)
    return ok({"usuario": session.get("usuario"), "nombre": session.get("nombre"),
               "rol": session.get("rol")})


@auth_bp.route("/api/cambiar_password", methods=["POST"])
@login_requerido
def cambiar_password():
    data = request.get_json() or {}
    actual = data.get("actual") or ""
    nueva = data.get("nueva") or ""
    if len(nueva) < 4:
        return err("La nueva contraseña debe tener al menos 4 caracteres")
    conn = get_conn()
    row = conn.execute("SELECT * FROM usuarios WHERE id = ?", (session["user_id"],)).fetchone()
    if not row or not check_password_hash(row["password_hash"], actual):
        conn.close()
        return err("La contraseña actual es incorrecta")
    conn.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?",
                 (generate_password_hash(nueva), session["user_id"]))
    conn.commit()
    conn.close()
    registrar_auditoria("Cambio de contraseña", f"Usuario {session['usuario']} cambió su contraseña")
    return ok(message="Contraseña actualizada")
