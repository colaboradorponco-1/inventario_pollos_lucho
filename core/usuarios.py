import os
from datetime import date

import pymysql
from flask import Blueprint, request, session
from werkzeug.security import generate_password_hash

from database import get_conn
from .backup import (DumpInvalido, aplicar, carpeta_respaldos, nombre_respaldo,
                     revisar, volcar, volcar_a_archivo)
from .util import ok, err, login_requerido, rol_requerido, registrar_auditoria, sucursal_actual, es_superadmin

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
        sid = sucursal_actual()
        if not es_superadmin() and sid is not None:
            t2 = conn.execute("SELECT sucursal_id FROM usuarios WHERE id = ?", (user_id,)).fetchone()
            if t2 and t2["sucursal_id"] != sid:
                conn.close()
                return err("No tienes permisos para esta acción", 403)
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
        sid = sucursal_actual()
        t2 = conn.execute("SELECT sucursal_id FROM usuarios WHERE id = ?", (user_id,)).fetchone()
        if t2 and sid is not None and t2["sucursal_id"] != sid:
            conn.close()
            return err("No tienes permisos para esta acción", 403)
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
    desde = request.args.get("desde")
    hasta = request.args.get("hasta")
    q = "SELECT * FROM auditoria WHERE 1 = 1"
    params = []
    if desde:
        q += " AND DATE(fecha) >= %s"
        params.append(desde)
    if hasta:
        q += " AND DATE(fecha) <= %s"
        params.append(hasta)
    q += " ORDER BY id DESC LIMIT %s"
    params.append(limite)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


# --------------------------------------------------------------------------
# Respaldos (dump SQL para MySQL)
# --------------------------------------------------------------------------
@usuarios_bp.route("/api/backup")
@login_requerido
@rol_requerido("admin")
def backup():
    """Descarga un dump SQL COMPLETO y restaurable.

    Va por `core.backup.volcar`, que saca las tablas de SHOW TABLES. La version
    anterior usaba una lista fija y se salia `pedidos`, `pedido_detalle`, `stock`
    y `stock_v2` sin avisar; ademas solo escribia INSERT, sin CREATE TABLE, asi
    que el archivo no se podia restaurar sobre la base que ya existia.
    """
    from flask import Response

    def _dumpar():
        conn = get_conn()
        try:
            yield from volcar(conn)
        finally:
            conn.close()

    hoy = date.today().isoformat()
    registrar_auditoria("Respaldo creado", "Descarga de copia de seguridad SQL")
    return Response(_dumpar(), mimetype="application/sql",
                    headers={"Content-Disposition":
                             "attachment; "
                             f'filename="inventario_pollos_lucho_{hoy}.sql"'})


@usuarios_bp.route("/api/restaurar", methods=["POST"])
@login_requerido
@rol_requerido("superadmin")
def restaurar():
    """Restaura un respaldo SQL. Solo superadmin.

    Va en dos pasos. Sin `confirmar` NO se toca la base: solo se valida el
    archivo y se devuelve un resumen de lo que vendria. Con `confirmar=1` se
    hace primero una copia real de la base actual y, solo si esa copia sale
    bien, se aplica el archivo.

    Lo que hacia la version anterior, y por que era peligroso: ejecutaba el
    archivo sentencia por sentencia sin mirar que tenia dentro (un `DELETE` o
    un `DROP DATABASE` pegado a mano se ejecutaba sin pestear), y la UI prometia
    una copia automatica que no existia en ningun sitio.
    """
    archivo = request.files.get("archivo")
    if not archivo:
        return err("Debes seleccionar un archivo")
    crudo = archivo.read()
    if not crudo:
        return err("El archivo esta vacio")
    sql = crudo.decode("utf-8", errors="replace")

    conn = get_conn()
    try:
        try:
            resumen = revisar(sql, conn)
        except DumpInvalido as e:
            registrar_auditoria(
                "Respaldo rechazado",
                f"Se subio un archivo que no es un respaldo restaurable: "
                f"{str(e)[:180]}")
            return err(str(e), 400)

        confirmar = str(request.form.get("confirmar", "")).strip().lower() \
            in ("1", "true", "si", "s", "sí")
        if not confirmar:
            return ok({**resumen, "por_confirmar": True},
                      message="Archivo valido. Confirma para restaurar.")

        # Copia REAL de la base actual, antes de tocar nada. Si esto falla no se
        # restaura: quedarse sin la base actual y sin red es peor que no
        # poder restaurar el respaldo.
        try:
            ruta = os.path.join(carpeta_respaldos(), nombre_respaldo("antes_de_restaurar"))
            volcar_a_archivo(conn, ruta)
        except Exception as e:
            return err("No se pudo hacer la copia de seguridad previa, se "
                       f"cancela la restauracion: {str(e)[:200]}", 500)

        try:
            aplicadas = aplicar(conn, sql)
        except Exception as e:
            registrar_auditoria(
                "Restauracion fallida",
                f"Se intento restaurar y fallo ({str(e)[:150]}). Copia previa: {ruta}")
            return err(f"La restauracion fallo: {str(e)[:200]}. La base puede "
                       f"quedar incompleta; la copia previa esta en {ruta}", 500)
    finally:
        conn.close()

    registrar_auditoria(
        "Base de datos restaurada",
        f"Se restauraron {aplicadas} sentencias desde un respaldo. "
        f"Copia previa: {ruta}")
    return ok({"sentencias": aplicadas, "tablas": resumen["tablas"],
               "copia_previa": ruta},
              message="Base de datos restaurada correctamente")
