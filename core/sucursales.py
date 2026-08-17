import sqlite3
from datetime import date

from flask import Blueprint, request, session

from database import get_conn
from .util import ok, err, login_requerido, rol_requerido, registrar_auditoria, registrar_movimiento, stock_actual

sucursales_bp = Blueprint("sucursales", __name__)


# --------------------------------------------------------------------------
# Sucursales (listado para todos; gestión solo admin)
# --------------------------------------------------------------------------
@sucursales_bp.route("/api/sucursales", methods=["GET", "POST"])
@login_requerido
def sucursales():
    conn = get_conn()
    if request.method == "POST":
        if session.get("rol") != "admin":
            conn.close()
            return err("No tienes permisos para esta acción", 403)
        data = request.get_json() or {}
        nombre = (data.get("nombre") or "").strip()
        if not nombre:
            conn.close()
            return err("El nombre es obligatorio")
        principal = 1 if data.get("principal") else 0
        try:
            cur = conn.execute(
                "INSERT INTO sucursales (nombre, direccion, principal) VALUES (?, ?, ?)",
                (nombre, data.get("direccion", ""), principal))
            conn.commit()
            conn.close()
            registrar_auditoria("Sucursal creada", nombre)
            return ok({"id": cur.lastrowid}, message="Sucursal creada")
        except sqlite3.IntegrityError:
            conn.close()
            return err("Ya existe una sucursal con ese nombre")
    rows = conn.execute("""
        SELECT s.*,
               (SELECT COUNT(*) FROM repartos r WHERE r.sucursal_id = s.id) AS num_repartos,
               (SELECT COALESCE(SUM(r.total), 0) FROM repartos r WHERE r.sucursal_id = s.id) AS total_repartido
        FROM sucursales s
        ORDER BY s.principal DESC, s.nombre
    """).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@sucursales_bp.route("/api/sucursales/<int:suc_id>", methods=["PUT", "DELETE"])
@login_requerido
@rol_requerido("admin")
def sucursal(suc_id):
    conn = get_conn()
    if request.method == "DELETE":
        tiene = conn.execute("SELECT COUNT(*) c FROM repartos WHERE sucursal_id = ?", (suc_id,)).fetchone()["c"]
        if tiene:
            conn.close()
            return err("No se puede eliminar: esta sucursal tiene repartos registrados")
        conn.execute("DELETE FROM sucursales WHERE id = ?", (suc_id,))
        conn.commit()
        conn.close()
        registrar_auditoria("Sucursal eliminada", f"Sucursal ID {suc_id}")
        return ok(message="Sucursal eliminada")
    data = request.get_json() or {}
    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        conn.close()
        return err("El nombre es obligatorio")
    conn.execute("UPDATE sucursales SET nombre = ?, direccion = ?, principal = ? WHERE id = ?",
                 (nombre, data.get("direccion", ""), 1 if data.get("principal") else 0, suc_id))
    conn.commit()
    conn.close()
    registrar_auditoria("Sucursal actualizada", nombre)
    return ok(message="Sucursal actualizada")


# --------------------------------------------------------------------------
# Repartos a sucursales
# --------------------------------------------------------------------------
@sucursales_bp.route("/api/repartos", methods=["GET", "POST"])
@login_requerido
def repartos():
    conn = get_conn()
    if request.method == "POST":
        data = request.get_json() or {}
        fecha = data.get("fecha") or date.today().isoformat()
        sucursal_id = data.get("sucursal_id")
        nota = data.get("nota", "")
        detalle = data.get("detalle", [])
        if not sucursal_id:
            conn.close()
            return err("Debes seleccionar una sucursal")
        if not detalle:
            conn.close()
            return err("El reparto no tiene productos")
        suc = conn.execute("SELECT nombre FROM sucursales WHERE id = ?", (sucursal_id,)).fetchone()
        if not suc:
            conn.close()
            return err("Sucursal no encontrada")

        total = 0.0
        items_validados = []
        for item in detalle:
            prod_id = item.get("producto_id")
            cantidad = float(item.get("cantidad", 0) or 0)
            costo = float(item.get("costo_unitario", 0) or 0)
            if cantidad <= 0:
                conn.close()
                return err("La cantidad debe ser mayor a cero")
            fila = conn.execute("SELECT id, nombre FROM productos WHERE id = ? AND activo = 1",
                                (prod_id,)).fetchone()
            if not fila:
                conn.close()
                return err("Producto no encontrado")
            stock = stock_actual(conn, prod_id)
            if stock < cantidad:
                conn.close()
                return err(f"Stock insuficiente de {fila['nombre']}. Disponible: {stock}")
            items_validados.append((prod_id, fila["nombre"], cantidad, costo, cantidad * costo))
            total += cantidad * costo

        cur = conn.execute(
            "INSERT INTO repartos (fecha, sucursal_id, total, usuario, nota) VALUES (?, ?, ?, ?, ?)",
            (fecha, sucursal_id, round(total, 2), session.get("usuario", ""), nota))
        reparto_id = cur.lastrowid

        for prod_id, nombre, cantidad, costo, subtotal in items_validados:
            conn.execute("""
                INSERT INTO reparto_detalle (reparto_id, producto_id, producto_nombre, cantidad, costo_unitario, subtotal)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (reparto_id, prod_id, nombre, cantidad, costo, round(subtotal, 2)))
            registrar_movimiento(conn, prod_id, "salida", cantidad, costo, fecha,
                                 f"Reparto #{reparto_id} a {suc['nombre']}",
                                 session.get("usuario", ""))
        conn.commit()
        conn.close()
        registrar_auditoria("Reparto registrado",
                            f"Reparto #{reparto_id} a {suc['nombre']} por S/ {round(total, 2)}")
        return ok({"id": reparto_id, "total": round(total, 2)}, message="Reparto registrado")

    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    filtro = request.args.get("filtro", "").strip()
    q = """
        SELECT r.*, s.nombre AS sucursal_nombre,
               (SELECT COUNT(*) FROM reparto_detalle d WHERE d.reparto_id = r.id) AS num_items
        FROM repartos r JOIN sucursales s ON s.id = r.sucursal_id WHERE 1=1
    """
    params = []
    if desde:
        q += " AND date(r.fecha) >= date(?)"
        params.append(desde)
    if hasta:
        q += " AND date(r.fecha) <= date(?)"
        params.append(hasta)
    if filtro:
        q += " AND (s.nombre LIKE ? OR r.usuario LIKE ? OR r.nota LIKE ? OR CAST(r.id AS TEXT) LIKE ?)"
        params += [f"%{filtro}%"] * 4
    q += " ORDER BY r.fecha DESC, r.id DESC LIMIT 500"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@sucursales_bp.route("/api/repartos/<int:reparto_id>", methods=["GET"])
@login_requerido
def reparto_detalle(reparto_id):
    conn = get_conn()
    reparto = conn.execute(
        "SELECT r.*, s.nombre AS sucursal_nombre FROM repartos r JOIN sucursales s ON s.id = r.sucursal_id WHERE r.id = ?",
        (reparto_id,)).fetchone()
    if not reparto:
        conn.close()
        return err("Reparto no encontrado", 404)
    detalle = conn.execute(
        "SELECT * FROM reparto_detalle WHERE reparto_id = ?", (reparto_id,)).fetchall()
    conn.close()
    return ok({"reparto": dict(reparto), "detalle": [dict(r) for r in detalle]})


@sucursales_bp.route("/api/repartos/<int:reparto_id>", methods=["DELETE"])
@login_requerido
def reparto_eliminar(reparto_id):
    conn = get_conn()
    reparto = conn.execute(
        "SELECT r.*, s.nombre AS sucursal_nombre FROM repartos r JOIN sucursales s ON s.id = r.sucursal_id WHERE r.id = ?",
        (reparto_id,)).fetchone()
    if not reparto:
        conn.close()
        return err("Reparto no encontrado", 404)
    detalle = conn.execute("SELECT * FROM reparto_detalle WHERE reparto_id = ?", (reparto_id,)).fetchall()
    for d in detalle:
        registrar_movimiento(conn, d["producto_id"], "entrada", d["cantidad"], d["costo_unitario"],
                             date.today().isoformat(),
                             f"Anulación reparto #{reparto_id} de {reparto['sucursal_nombre']}",
                             session.get("usuario", ""))
    conn.execute("DELETE FROM repartos WHERE id = ?", (reparto_id,))
    conn.commit()
    conn.close()
    registrar_auditoria("Reparto anulado", f"Reparto #{reparto_id} de {reparto['sucursal_nombre']}")
    return ok(message="Reparto anulado y stock repuesto")
