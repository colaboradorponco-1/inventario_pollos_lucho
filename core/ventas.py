from datetime import datetime

from flask import Blueprint, request, session

from database import get_conn
from .util import ok, err, login_requerido, registrar_auditoria, registrar_movimiento, stock_actual, ok_paginado, paginar_params, sucursal_actual, sucursal_operativa, clausula_sucursal, es_gestion

ventas_bp = Blueprint("ventas", __name__)


@ventas_bp.route("/api/ventas", methods=["GET", "POST"])
@login_requerido
def ventas():
    conn = get_conn()
    if request.method == "POST":
        data = request.get_json() or {}
        fecha = data.get("fecha") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        nota = data.get("nota", "")
        detalle = data.get("detalle", [])
        if not detalle:
            conn.close()
            return err("La venta no tiene productos")

        total = 0.0
        items_validados = []
        sid = sucursal_operativa()
        for item in detalle:
            prod_id = item.get("producto_id")
            cantidad = float(item.get("cantidad", 0) or 0)
            precio = float(item.get("precio_unitario", 0) or 0)
            if cantidad <= 0:
                conn.close()
                return err("La cantidad debe ser mayor a cero")
            fila = conn.execute("SELECT id, nombre, costo_promedio FROM productos WHERE id = ? AND activo = 1",
                                (prod_id,)).fetchone()
            if not fila:
                conn.close()
                return err("Producto no encontrado")
            stock = stock_actual(conn, prod_id, sid)
            if stock < cantidad:
                conn.close()
                return err(f"Stock insuficiente de {fila['nombre']}. Disponible: {stock}")
            items_validados.append((prod_id, fila["nombre"], cantidad, precio,
                                    fila["costo_promedio"] or 0, cantidad * precio))
            total += cantidad * precio

        cur = conn.execute(
            "INSERT INTO ventas (fecha, total, usuario, nota, sucursal_id) VALUES (?, ?, ?, ?, ?)",
            (fecha, round(total, 2), session.get("usuario", ""), nota, sid))
        venta_id = cur.lastrowid

        for prod_id, nombre, cantidad, precio, costo, subtotal in items_validados:
            conn.execute("""
                INSERT INTO venta_detalle (venta_id, producto_id, producto_nombre, cantidad,
                                           precio_unitario, costo_unitario, subtotal)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (venta_id, prod_id, nombre, cantidad, precio, costo, round(subtotal, 2)))
            registrar_movimiento(conn, prod_id, "salida", cantidad, precio, fecha,
                                 f"Venta #{venta_id}", session.get("usuario", ""))
        conn.commit()

        conn.close()
        registrar_auditoria("Venta registrada", f"Venta #{venta_id} por Bs {round(total, 2)}")
        return ok({"id": venta_id, "total": round(total, 2)}, message="Venta registrada")

    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    filtro = request.args.get("filtro", "").strip()
    sid_filtro = request.args.get("sucursal_id", "")
    where = " WHERE 1=1"
    params = []
    # Admin/superadmin ven todas las sucursales (filtrables por sucursal_id).
    # Encargado SIEMPRE solo su sucursal.
    if es_gestion() and sid_filtro:
        where += " AND v.sucursal_id = ?"
        params.append(int(sid_filtro))
    elif not es_gestion():
        cls, cls_params = clausula_sucursal("v.sucursal_id")
        if cls:
            where += cls
            params += cls_params
    if desde:
        where += " AND date(v.fecha) >= date(?)"
        params.append(desde)
    if hasta:
        where += " AND date(v.fecha) <= date(?)"
        params.append(hasta)
    if filtro:
        where += " AND (CAST(v.id AS CHAR) LIKE ? OR v.usuario LIKE ? OR v.nota LIKE ?)"
        params += [f"%{filtro}%"] * 3
    q = ("""
        SELECT v.*,
               (SELECT COUNT(*) FROM venta_detalle d WHERE d.venta_id = v.id) AS num_items,
               (SELECT GROUP_CONCAT(CONCAT(d.producto_nombre, ' (', d.cantidad, ')') SEPARATOR ', ')
                FROM venta_detalle d WHERE d.venta_id = v.id) AS items_detalle,
               s.nombre AS sucursal_nombre
        FROM ventas v
        LEFT JOIN sucursales s ON s.id = v.sucursal_id
    """ + where)
    count_q = "SELECT COUNT(*) AS c FROM ventas v" + where
    total = conn.execute(count_q, params).fetchone()["c"]
    offset, limit, pagina, por_pagina = paginar_params()
    q += " ORDER BY v.fecha DESC, v.id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok_paginado([dict(r) for r in rows], total, pagina, por_pagina)


@ventas_bp.route("/api/ventas/<int:venta_id>", methods=["GET"])
@login_requerido
def venta_detalle(venta_id):
    conn = get_conn()
    venta = conn.execute("SELECT * FROM ventas WHERE id = ?", (venta_id,)).fetchone()
    if not venta:
        conn.close()
        return err("Venta no encontrada", 404)
    detalle = conn.execute(
        "SELECT * FROM venta_detalle WHERE venta_id = ?", (venta_id,)).fetchall()
    conn.close()
    return ok({"venta": dict(venta), "detalle": [dict(r) for r in detalle]})


@ventas_bp.route("/api/ventas/<int:venta_id>", methods=["DELETE"])
@login_requerido
def venta_eliminar(venta_id):
    if not es_gestion():
        return err("No tienes permisos para esta acción", 403)
    conn = get_conn()
    venta = conn.execute("SELECT * FROM ventas WHERE id = ?", (venta_id,)).fetchone()
    if not venta:
        conn.close()
        return err("Venta no encontrada", 404)
    detalle = conn.execute("SELECT * FROM venta_detalle WHERE venta_id = ?", (venta_id,)).fetchall()
    for d in detalle:
        registrar_movimiento(conn, d["producto_id"], "entrada", d["cantidad"], d["precio_unitario"],
                             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                             f"Devolución por anulación de venta #{venta_id}",
                             session.get("usuario", ""), venta["sucursal_id"])
    conn.execute("DELETE FROM venta_detalle WHERE venta_id = ?", (venta_id,))
    conn.execute("DELETE FROM ventas WHERE id = ?", (venta_id,))
    conn.commit()
    conn.close()
    registrar_auditoria("Venta anulada", f"Venta #{venta_id}")
    return ok(message="Venta anulada y stock repuesto")
