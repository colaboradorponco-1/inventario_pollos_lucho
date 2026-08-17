import sqlite3

from flask import Blueprint, request

from database import get_conn
from .util import ok, err, login_requerido

catalogos_bp = Blueprint("catalogos", __name__)


@catalogos_bp.route("/api/almacenes", methods=["GET", "POST"])
@login_requerido
def almacenes():
    conn = get_conn()
    if request.method == "POST":
        data = request.get_json()
        try:
            conn.execute("INSERT INTO almacenes (nombre, ubicacion) VALUES (?, ?)",
                         (data["nombre"].strip(), data.get("ubicacion", "").strip()))
            conn.commit()
            return ok(message="Almacén creado")
        except sqlite3.IntegrityError:
            return err("Ya existe un almacén con ese nombre")
        finally:
            conn.close()
    rows = conn.execute("SELECT * FROM almacenes ORDER BY nombre").fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@catalogos_bp.route("/api/almacenes/<int:alm_id>", methods=["PUT", "DELETE"])
@login_requerido
def almacen(alm_id):
    conn = get_conn()
    if request.method == "DELETE":
        try:
            conn.execute("DELETE FROM almacenes WHERE id = ?", (alm_id,))
            conn.commit()
            return ok(message="Almacén eliminado")
        except sqlite3.IntegrityError:
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
        data = request.get_json()
        try:
            conn.execute("INSERT INTO categorias (nombre) VALUES (?)", (data["nombre"].strip(),))
            conn.commit()
            return ok(message="Categoría creada")
        except sqlite3.IntegrityError:
            return err("Ya existe esa categoría")
        finally:
            conn.close()
    rows = conn.execute("SELECT * FROM categorias ORDER BY nombre").fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@catalogos_bp.route("/api/categorias/<int:cat_id>", methods=["DELETE"])
@login_requerido
def categoria(cat_id):
    conn = get_conn()
    try:
        conn.execute("DELETE FROM categorias WHERE id = ?", (cat_id,))
        conn.commit()
        return ok(message="Categoría eliminada")
    except sqlite3.IntegrityError:
        return err("No se puede eliminar: tiene productos asociados")
    finally:
        conn.close()


@catalogos_bp.route("/api/proveedores", methods=["GET", "POST"])
@login_requerido
def proveedores():
    conn = get_conn()
    if request.method == "POST":
        data = request.get_json()
        cur = conn.execute("INSERT INTO proveedores (nombre, telefono, email, direccion) VALUES (?, ?, ?, ?)",
                           (data["nombre"].strip(), data.get("telefono", ""), data.get("email", ""), data.get("direccion", "")))
        conn.commit()
        return ok({"id": cur.lastrowid}, message="Proveedor creado")
    filtro = request.args.get("filtro", "").strip()
    q = "SELECT * FROM proveedores WHERE 1=1"
    params = []
    if filtro:
        q += " AND (nombre LIKE ? OR telefono LIKE ? OR email LIKE ?)"
        params += [f"%{filtro}%"] * 3
    q += " ORDER BY nombre"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@catalogos_bp.route("/api/proveedores/<int:prov_id>", methods=["PUT", "DELETE"])
@login_requerido
def proveedor(prov_id):
    conn = get_conn()
    if request.method == "DELETE":
        conn.execute("DELETE FROM proveedores WHERE id = ?", (prov_id,))
        conn.commit()
        conn.close()
        return ok(message="Proveedor eliminado")
    data = request.get_json()
    conn.execute("UPDATE proveedores SET nombre=?, telefono=?, email=?, direccion=? WHERE id = ?",
                 (data["nombre"].strip(), data.get("telefono", ""), data.get("email", ""), data.get("direccion", ""), prov_id))
    conn.commit()
    conn.close()
    return ok(message="Proveedor actualizado")
