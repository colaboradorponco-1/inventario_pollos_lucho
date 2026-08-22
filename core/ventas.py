from datetime import datetime

from flask import Blueprint, request, session

from database import get_conn
from .util import ok, err, login_requerido, registrar_auditoria, registrar_movimiento, stock_actual

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
            stock = stock_actual(conn, prod_id)
            if stock < cantidad:
                conn.close()
                return err(f"Stock insuficiente de {fila['nombre']}. Disponible: {stock}")
            items_validados.append((prod_id, fila["nombre"], cantidad, precio,
                                    fila["costo_promedio"] or 0, cantidad * precio))
            total += cantidad * precio

        cur = conn.execute(
            "INSERT INTO ventas (fecha, total, usuario, nota) VALUES (?, ?, ?, ?)",
            (fecha, round(total, 2), session.get("usuario", ""), nota))
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
    q = """
        SELECT v.*,
               (SELECT COUNT(*) FROM venta_detalle d WHERE d.venta_id = v.id) AS num_items,
               (SELECT GROUP_CONCAT(d.producto_nombre || ' (' || d.cantidad || ')' , ', ')
                FROM venta_detalle d WHERE d.venta_id = v.id) AS items_detalle
        FROM ventas v WHERE 1=1
    """
    params = []
    if desde:
        q += " AND date(v.fecha) >= date(?)"
        params.append(desde)
    if hasta:
        q += " AND date(v.fecha) <= date(?)"
        params.append(hasta)
    if filtro:
        q += " AND (CAST(v.id AS TEXT) LIKE ? OR v.usuario LIKE ? OR v.nota LIKE ?)"
        params += [f"%{filtro}%"] * 3
    q += " ORDER BY v.fecha DESC, v.id DESC LIMIT 500"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


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
    conn = get_conn()
    detalle = conn.execute("SELECT * FROM venta_detalle WHERE venta_id = ?", (venta_id,)).fetchall()
    for d in detalle:
        registrar_movimiento(conn, d["producto_id"], "entrada", d["cantidad"], d["precio_unitario"],
                             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), f"Anulación venta #{venta_id}",
                             session.get("usuario", ""))
    conn.execute("DELETE FROM ventas WHERE id = ?", (venta_id,))
    conn.commit()
    conn.close()
    registrar_auditoria("Venta anulada", f"Venta #{venta_id}")
    return ok(message="Venta anulada y stock repuesto")
