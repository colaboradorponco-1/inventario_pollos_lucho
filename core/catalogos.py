import pymysql

from flask import Blueprint, request

from database import get_conn
from .util import (ok, err, login_requerido, es_gestion, es_superadmin,
                   sucursal_actual, sucursal_operativa)

catalogos_bp = Blueprint("catalogos", __name__)


@catalogos_bp.route("/api/almacenes", methods=["GET", "POST"])
@login_requerido
def almacenes():
    conn = get_conn()
    if request.method == "POST":
        if not es_superadmin():
            conn.close()
            return err("Solo el superadministrador puede crear almacenes", 403)
        data = request.get_json()
        try:
            conn.execute("INSERT INTO almacenes (nombre, ubicacion) VALUES (?, ?)",
                         (data["nombre"].strip(), data.get("ubicacion", "").strip()))
            conn.commit()
            return ok(message="Almacén creado")
        except pymysql.err.IntegrityError:
            return err("Ya existe un almacén con ese nombre")
        finally:
            conn.close()
    rows = conn.execute("SELECT * FROM almacenes ORDER BY nombre").fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@catalogos_bp.route("/api/almacenes/<int:alm_id>", methods=["PUT", "DELETE"])
@login_requerido
def almacen(alm_id):
    if not es_superadmin():
        return err("Solo el superadministrador puede gestionar almacenes", 403)
    conn = get_conn()
    if request.method == "DELETE":
        try:
            conn.execute("DELETE FROM almacenes WHERE id = ?", (alm_id,))
            conn.commit()
            return ok(message="Almacén eliminado")
        except pymysql.err.IntegrityError:
            return err("No se puede eliminar: tiene productos asociados")
        finally:
            conn.close()
    data = request.get_json()
    conn.execute("UPDATE almacenes SET nombre = ?, ubicacion = ? WHERE id = ?",
                 (data["nombre"].strip(), data.get("ubicacion", "").strip(), alm_id))
    conn.commit()
    conn.close()
    return ok(message="Almacén actualizado")


@catalogos_bp.route("/api/categorias", methods=["GET", "POST"])
@login_requerido
def categorias():
    conn = get_conn()
    if request.method == "POST":
        if not es_superadmin():
            conn.close()
            return err("Solo el superadministrador puede crear categorías", 403)
        data = request.get_json()
        try:
            conn.execute("INSERT INTO categorias (nombre) VALUES (?)", (data["nombre"].strip(),))
            conn.commit()
            return ok(message="Categoría creada")
        except pymysql.err.IntegrityError:
            return err("Ya existe esa categoría")
        finally:
            conn.close()
    rows = conn.execute("SELECT * FROM categorias ORDER BY nombre").fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@catalogos_bp.route("/api/categorias/<int:cat_id>", methods=["PUT", "DELETE"])
@login_requerido
def categoria(cat_id):
    if not es_superadmin():
        return err("Solo el superadministrador puede gestionar categorías", 403)
    conn = get_conn()
    if request.method == "DELETE":
        try:
            conn.execute("DELETE FROM categorias WHERE id = ?", (cat_id,))
            conn.commit()
            return ok(message="Categoría eliminada")
        except pymysql.err.IntegrityError:
            return err("No se puede eliminar: tiene productos asociados")
        finally:
            conn.close()
    data = request.get_json()
    try:
        conn.execute("UPDATE categorias SET nombre = ? WHERE id = ?",
                     (data["nombre"].strip(), cat_id))
        conn.commit()
        return ok(message="Categoría actualizada")
    except pymysql.err.IntegrityError:
        return err("Ya existe esa categoría")
    finally:
        conn.close()


@catalogos_bp.route("/api/proveedores", methods=["GET", "POST"])
@login_requerido
def proveedores():
    conn = get_conn()
    if request.method == "POST":
        # El encargado crea proveedores de SU sucursal; admin/superadmin de cualquier sucursal.
        data = request.get_json()
        sid = data.get("sucursal_id")
        if sid:
            if not es_gestion():
                conn.close()
                return err("No tienes permisos para crear proveedores para otra sucursal", 403)
            existe = conn.execute("SELECT id FROM sucursales WHERE id = ?", (int(sid),)).fetchone()
            if not existe:
                conn.close()
                return err("Sucursal no válida")
            sid = int(sid)
        else:
            sid = sucursal_operativa()
            if sid is None:
                conn.close()
                return err("No se puede crear el proveedor sin una sucursal definida")
        cur = conn.execute("""
            INSERT INTO proveedores (nombre, telefono, email, direccion, sucursal_id)
            VALUES (?, ?, ?, ?, ?)""",
            (data["nombre"].strip(), data.get("telefono", ""), data.get("email", ""),
             data.get("direccion", ""), sid))
        conn.commit()
        return ok({"id": cur.lastrowid}, message="Proveedor creado")

    filtro = request.args.get("filtro", "").strip()
    sid_filtro = request.args.get("sucursal_id", "")
    q = """SELECT pr.*, s.nombre AS sucursal_nombre
           FROM proveedores pr LEFT JOIN sucursales s ON s.id = pr.sucursal_id WHERE 1=1"""
    params = []
    if es_gestion():
        if sid_filtro:
            q += " AND pr.sucursal_id = ?"
            params.append(int(sid_filtro))
    else:
        q += " AND (pr.sucursal_id = ? OR pr.sucursal_id IS NULL)"
        params.append(sucursal_actual())
    if filtro:
        q += " AND (pr.nombre LIKE ? OR pr.telefono LIKE ? OR pr.email LIKE ?)"
        params += [f"%{filtro}%"] * 3
    q += " ORDER BY pr.sucursal_id, pr.nombre"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@catalogos_bp.route("/api/proveedores/<int:prov_id>", methods=["PUT", "DELETE"])
@login_requerido
def proveedor(prov_id):
    conn = get_conn()
    fila = conn.execute("SELECT * FROM proveedores WHERE id = ?", (prov_id,)).fetchone()
    if not fila:
        conn.close()
        return err("Proveedor no encontrado", 404)
    # Encargado solo puede gestionar proveedores de su sucursal.
    if not es_gestion():
        sid_usuario = sucursal_actual()
        if fila["sucursal_id"] != sid_usuario:
            conn.close()
            return err("Solo puedes gestionar proveedores de tu sucursal", 403)
    if request.method == "DELETE":
        conn.execute("DELETE FROM proveedores WHERE id = ?", (prov_id,))
        conn.commit()
        conn.close()
        return ok(message="Proveedor eliminado")
    data = request.get_json()
    sid = int(data["sucursal_id"]) if data.get("sucursal_id") else fila["sucursal_id"]
    if sid:
        existe = conn.execute("SELECT id FROM sucursales WHERE id = ?", (sid,)).fetchone()
        if not existe:
            conn.close()
            return err("Sucursal no válida")
    conn.execute("UPDATE proveedores SET nombre=?, telefono=?, email=?, direccion=?, sucursal_id=? WHERE id = ?",
                 (data["nombre"].strip(), data.get("telefono", ""), data.get("email", ""),
                  data.get("direccion", ""), sid, prov_id))
    conn.commit()
    conn.close()
    return ok(message="Proveedor actualizado")
