from flask import Blueprint, request

from database import get_conn
from .util import ok, login_requerido, responder_csv

reportes_bp = Blueprint("reportes", __name__)


@reportes_bp.route("/api/exportar/consumo")
@login_requerido
def exportar_consumo():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    conn = get_conn()
    q = """
        SELECT p.nombre, p.unidad, m.tipo,
               SUM(m.cantidad) AS cantidad, SUM(m.cantidad * m.precio_unitario) AS total
        FROM movimientos m JOIN productos p ON p.id = m.producto_id
        WHERE 1=1
    """
    params = []
    if desde:
        q += " AND date(m.fecha) >= date(?)"
        params.append(desde)
    if hasta:
        q += " AND date(m.fecha) <= date(?)"
        params.append(hasta)
    q += " GROUP BY p.id, m.tipo ORDER BY cantidad DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    filas = [(r["nombre"], r["unidad"], "Entrada" if r["tipo"] == "entrada" else "Salida",
              r["cantidad"], round(r["total"] or 0, 2)) for r in rows]
    return responder_csv("reporte_consumo.csv",
                         ["Producto", "Unidad", "Tipo", "Cantidad", "Total (S/)"], filas)


@reportes_bp.route("/api/exportar/productos")
@login_requerido
def exportar_productos():
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.codigo, p.nombre, c.nombre AS categoria, a.nombre AS almacen,
               p.unidad, p.stock_minimo, p.costo_promedio, p.precio_venta,
               COALESCE(s.cantidad, 0) AS stock, p.vencimiento
        FROM productos p
        LEFT JOIN categorias c ON c.id = p.categoria_id
        LEFT JOIN almacenes a ON a.id = p.almacen_id
        LEFT JOIN stock s ON s.producto_id = p.id
        WHERE p.activo = 1 ORDER BY p.nombre
    """).fetchall()
    conn.close()
    filas = [(r["codigo"] or "", r["nombre"], r["categoria"] or "", r["almacen"] or "",
              r["unidad"], r["stock"], r["stock_minimo"], r["costo_promedio"],
              r["precio_venta"], r["vencimiento"] or "") for r in rows]
    return responder_csv("productos.csv",
                         ["Código", "Producto", "Categoría", "Almacén", "Unidad",
                          "Stock", "Stock mínimo", "Costo prom. (S/)", "Precio venta (S/)", "Vencimiento"],
                         filas)


@reportes_bp.route("/api/exportar/ventas")
@login_requerido
def exportar_ventas():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    conn = get_conn()
    q = "SELECT * FROM ventas WHERE 1=1"
    params = []
    if desde:
        q += " AND date(fecha) >= date(?)"
        params.append(desde)
    if hasta:
        q += " AND date(fecha) <= date(?)"
        params.append(hasta)
    q += " ORDER BY fecha DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    filas = [(r["id"], r["fecha"], r["total"], r["usuario"] or "", r["nota"] or "") for r in rows]
    return responder_csv("ventas.csv",
                         ["Nº", "Fecha", "Total (S/)", "Usuario", "Nota"], filas)


@reportes_bp.route("/api/exportar/repartos")
@login_requerido
def exportar_repartos():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    conn = get_conn()
    q = """
        SELECT r.id, r.fecha, s.nombre AS sucursal, r.total, r.usuario, r.nota,
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
    q += " ORDER BY r.fecha DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    filas = [(r["id"], r["fecha"], r["sucursal"], r["num_items"], r["total"],
              r["usuario"] or "", r["nota"] or "") for r in rows]
    return responder_csv("repartos.csv",
                         ["Nº", "Fecha", "Sucursal", "Items", "Total (S/)", "Usuario", "Nota"], filas)


@reportes_bp.route("/api/reportes/consumo")
@login_requerido
def reporte_consumo():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    conn = get_conn()
    q = """
        SELECT p.nombre, p.unidad, m.tipo,
               SUM(m.cantidad) AS cantidad, SUM(m.cantidad * m.precio_unitario) AS total
        FROM movimientos m JOIN productos p ON p.id = m.producto_id
        WHERE 1=1
    """
    params = []
    if desde:
        q += " AND date(m.fecha) >= date(?)"
        params.append(desde)
    if hasta:
        q += " AND date(m.fecha) <= date(?)"
        params.append(hasta)
    q += " GROUP BY p.id, m.tipo ORDER BY cantidad DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@reportes_bp.route("/api/reportes/repartos")
@login_requerido
def reporte_repartos():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    conn = get_conn()
    q = """
        SELECT s.id, s.nombre, s.principal,
               COUNT(r.id) AS num_repartos,
               COALESCE(SUM(r.total), 0) AS total_repartido
        FROM sucursales s
        LEFT JOIN repartos r ON r.sucursal_id = s.id
        WHERE 1=1
    """
    params = []
    if desde:
        q += " AND (r.id IS NULL OR date(r.fecha) >= date(?))"
        params.append(desde)
    if hasta:
        q += " AND (r.id IS NULL OR date(r.fecha) <= date(?))"
        params.append(hasta)
    q += " GROUP BY s.id ORDER BY total_repartido DESC, s.nombre"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@reportes_bp.route("/api/reportes/ganancias")
@login_requerido
def reporte_ganancias():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    conn = get_conn()
    q = """
        SELECT d.producto_nombre AS nombre,
               SUM(d.cantidad) AS cantidad,
               SUM(d.subtotal) AS venta,
               SUM(d.costo_unitario * d.cantidad) AS costo,
               SUM((d.precio_unitario - d.costo_unitario) * d.cantidad) AS utilidad
        FROM venta_detalle d JOIN ventas v ON v.id = d.venta_id
        WHERE 1=1
    """
    params = []
    if desde:
        q += " AND date(v.fecha) >= date(?)"
        params.append(desde)
    if hasta:
        q += " AND date(v.fecha) <= date(?)"
        params.append(hasta)
    q += " GROUP BY d.producto_id, d.producto_nombre ORDER BY utilidad DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@reportes_bp.route("/api/exportar/ganancias")
@login_requerido
def exportar_ganancias():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    conn = get_conn()
    q = """
        SELECT d.producto_nombre AS nombre,
               SUM(d.cantidad) AS cantidad,
               SUM(d.subtotal) AS venta,
               SUM(d.costo_unitario * d.cantidad) AS costo,
               SUM((d.precio_unitario - d.costo_unitario) * d.cantidad) AS utilidad
        FROM venta_detalle d JOIN ventas v ON v.id = d.venta_id WHERE 1=1
    """
    params = []
    if desde:
        q += " AND date(v.fecha) >= date(?)"
        params.append(desde)
    if hasta:
        q += " AND date(v.fecha) <= date(?)"
        params.append(hasta)
    q += " GROUP BY d.producto_id, d.producto_nombre ORDER BY utilidad DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    filas = [(r["nombre"], r["cantidad"], round(r["venta"], 2), round(r["costo"], 2),
              round(r["utilidad"], 2)) for r in rows]
    return responder_csv("ganancias.csv",
                         ["Producto", "Cantidad vendida", "Ventas (S/)", "Costo (S/)", "Ganancia (S/)"], filas)


@reportes_bp.route("/api/reportes/valorizacion")
@login_requerido
def reporte_valorizacion():
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.nombre, p.unidad, p.costo_promedio, p.precio_venta,
               COALESCE(s.cantidad, 0) AS stock,
               ROUND(p.costo_promedio * COALESCE(s.cantidad, 0), 2) AS valor
        FROM productos p
        LEFT JOIN stock s ON s.producto_id = p.id
        WHERE p.activo = 1 AND COALESCE(s.cantidad, 0) > 0
        ORDER BY valor DESC
    """).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@reportes_bp.route("/api/reportes/vencimientos")
@login_requerido
def reporte_vencimientos():
    conn = get_conn()
    rows = conn.execute("""
        SELECT p.nombre, p.vencimiento, p.unidad, COALESCE(s.cantidad, 0) AS stock
        FROM productos p
        LEFT JOIN stock s ON s.producto_id = p.id
        WHERE p.activo = 1 AND p.vencimiento IS NOT NULL AND p.vencimiento != ''
        ORDER BY p.vencimiento
    """).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])
