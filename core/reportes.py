from flask import Blueprint, request

from database import get_conn
from .util import (ok, err, login_requerido, rol_requerido, responder_excel, sucursal_actual,
                   sucursal_operativa, es_gestion, es_encargado_almacen, clausula_sucursal)
from .productos import scope_productos

reportes_bp = Blueprint("reportes", __name__)


def _cls(col):
    """(condición, params) para filtrar por la sucursal del usuario (o vacío para superadmin)."""
    sid = sucursal_actual()
    if sid is None:
        return "", []
    return f" AND {col} = %s", [sid]


@reportes_bp.route("/api/exportar/consumo")
@login_requerido
@rol_requerido("admin", "superadmin")
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
    cls, cls_params = _cls("m.sucursal_id")
    q += cls
    params += cls_params
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
    return responder_excel("consumo.xlsx",
                         ["Producto", "Unidad", "Tipo", "Cantidad", "Total (Bs)"], filas,
                         [30, 12, 10, 12, 15])


@reportes_bp.route("/api/exportar/productos")
@login_requerido
def exportar_productos():
    conn = get_conn()
    filtro = request.args.get("filtro", "").strip()
    categoria = request.args.get("categoria", "").strip()
    proveedor = request.args.get("proveedor", "").strip()
    estado = request.args.get("estado", "").strip()
    sucursal = request.args.get("sucursal", "").strip()
    sid = sucursal_actual()
    ver_todo = es_gestion() or es_encargado_almacen(conn)
    if ver_todo or sid is None:
        join_stock = "LEFT JOIN (SELECT producto_id, SUM(cantidad) AS cantidad FROM lotes GROUP BY producto_id) s ON s.producto_id = p.id"
        lote_cond = ""
        stock_params = []
    else:
        join_stock = "LEFT JOIN (SELECT producto_id, SUM(cantidad) AS cantidad FROM lotes WHERE sucursal_id = %s GROUP BY producto_id) s ON s.producto_id = p.id"
        lote_cond = " AND l2.sucursal_id = " + str(int(sid))
        stock_params = [sid]
    q = """
        SELECT p.codigo, p.nombre, p.marca, c.nombre AS categoria, a.nombre AS almacen,
               p.unidad, p.stock_minimo, p.costo_promedio, p.precio_venta,
               COALESCE(s.cantidad, 0) AS stock, p.vencimiento, pr.nombre AS proveedor,
               (SELECT MIN(l2.fecha_vencimiento) FROM lotes l2
                WHERE l2.producto_id = p.id AND l2.cantidad > 0
                  AND l2.fecha_vencimiento IS NOT NULL{lote_cond}) AS venc
        FROM productos p
        LEFT JOIN categorias c ON c.id = p.categoria_id
        LEFT JOIN almacenes a ON a.id = p.almacen_id
        LEFT JOIN proveedores pr ON pr.id = p.proveedor_id
        {join_stock}
        WHERE p.activo = ?
    """.format(join_stock=join_stock, lote_cond=lote_cond)
    params = stock_params
    if estado == "inactivos":
        params.append(0)
    else:
        params.append(1)
    scope_sql, scope_params = scope_productos(ver_todo, sucursal_operativa(), request.args.get("scope", "").strip())
    q += scope_sql
    params += scope_params
    if filtro:
        q += " AND (p.nombre LIKE ? OR p.codigo LIKE ? OR pr.nombre LIKE ? OR c.nombre LIKE ?)"
        f = f"%{filtro}%"
        params += [f, f, f, f]
    if categoria:
        q += " AND p.categoria_id = ?"
        params.append(int(categoria))
    if proveedor:
        q += " AND p.proveedor_id = ?"
        params.append(int(proveedor))
    if sucursal:
        suc = int(sucursal)
        if suc == 0:
            q += " AND p.sucursal_id IS NULL"
        else:
            # El scope de visibilidad limita a filiales: solo lo que pueden ver
            q += " AND p.sucursal_id = ?"
            params.append(suc)
    if estado == "con-stock":
        q += " AND COALESCE(s.cantidad, 0) > 0"
    elif estado == "agotado":
        q += " AND COALESCE(s.cantidad, 0) = 0"
    elif estado == "stock-bajo":
        q += " AND p.stock_minimo > 0 AND COALESCE(s.cantidad, 0) <= p.stock_minimo"
    elif estado == "por-vencer":
        q += (" AND EXISTS (SELECT 1 FROM lotes l2 WHERE l2.producto_id = p.id AND l2.cantidad > 0"
              " AND l2.fecha_vencimiento IS NOT NULL"
              " AND l2.fecha_vencimiento <= DATE_ADD(CURDATE(), INTERVAL 14 DAY){lote_cond})"
              ).format(lote_cond=lote_cond)
    q += " ORDER BY p.nombre"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    filas = [(r["codigo"] or "", r["nombre"], r["marca"] or "", r["categoria"] or "", r["almacen"] or "",
              r["unidad"], r["stock"], r["stock_minimo"], r["costo_promedio"],
              r["precio_venta"], r["proveedor"] or "", r["venc"] or "") for r in rows]
    return responder_excel("productos.xlsx",
                         ["Código", "Producto", "Marca", "Categoría", "Almacén", "Unidad",
                          "Stock", "Stock mínimo", "Costo (Bs)", "Precio venta (Bs)",
                          "Proveedor", "Vencimiento"],
                         filas, [14, 22, 14, 16, 16, 10, 10, 12, 14, 14, 18, 14],
                         titulo="PRODUCTOS - POLLOS LUCHO")


@reportes_bp.route("/api/exportar/ventas")
@login_requerido
def exportar_ventas():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    filtro = request.args.get("filtro", "").strip()
    sid_filtro = request.args.get("sucursal_id", "")
    conn = get_conn()
    q = """SELECT v.id, v.fecha, v.total, v.usuario, v.nota, s.nombre AS sucursal
           FROM ventas v LEFT JOIN sucursales s ON s.id = v.sucursal_id WHERE 1=1"""
    params = []
    if es_gestion() and sid_filtro:
        q += " AND v.sucursal_id = %s"
        params.append(int(sid_filtro))
    elif not es_gestion():
        cls, cls_params = _cls("v.sucursal_id")
        q += cls
        params += cls_params
    if desde:
        q += " AND date(v.fecha) >= date(%s)"
        params.append(desde)
    if hasta:
        q += " AND date(v.fecha) <= date(%s)"
        params.append(hasta)
    if filtro:
        q += " AND (CAST(v.id AS CHAR) LIKE %s OR v.usuario LIKE %s OR v.nota LIKE %s)"
        f = f"%{filtro}%"
        params += [f, f, f]
    q += " ORDER BY v.fecha DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    filas = [(r["id"], r["fecha"], r["total"], r["sucursal"] or "", r["usuario"] or "", r["nota"] or "") for r in rows]
    return responder_excel("ventas.xlsx",
                         ["Nº", "Fecha y hora", "Total (Bs)", "Sucursal", "Usuario", "Nota"], filas,
                         [8, 22, 15, 15, 15, 25])


@reportes_bp.route("/api/exportar/repartos")
@login_requerido
def exportar_repartos():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    sid_filtro = request.args.get("sucursal_id", "")
    conn = get_conn()
    q = """
        SELECT r.id, r.fecha, s.nombre AS sucursal, o.nombre AS origen, r.total, r.usuario, r.nota,
               (SELECT COUNT(*) FROM reparto_detalle d WHERE d.reparto_id = r.id) AS num_items
        FROM repartos r
        JOIN sucursales s ON s.id = r.sucursal_id
        LEFT JOIN sucursales o ON o.id = r.origen_sucursal_id
        WHERE 1=1
    """
    params = []
    # Admin/superadmin ven todos los repartos (filtrables por sucursal_id)
    if es_gestion() and sid_filtro:
        q += " AND r.sucursal_id = %s"
        params.append(int(sid_filtro))
    elif not es_gestion():
        cls, cls_params = clausula_sucursal("r.sucursal_id")
        if cls:
            q += " AND (" + cls[5:] + " OR r.origen_sucursal_id = %s)"
            params += cls_params + cls_params
    if desde:
        q += " AND date(r.fecha) >= date(%s)"
        params.append(desde)
    if hasta:
        q += " AND date(r.fecha) <= date(%s)"
        params.append(hasta)
    q += " ORDER BY r.fecha DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    filas = [(r["id"], r["fecha"], r["origen"], r["sucursal"], r["num_items"], r["total"],
              r["usuario"] or "", r["nota"] or "") for r in rows]
    return responder_excel("repartos.xlsx",
                         ["Nº", "Fecha y hora", "Origen", "Sucursal", "Items", "Total (Bs)", "Usuario", "Nota"], filas,
                         [8, 22, 20, 20, 8, 15, 15, 25])


@reportes_bp.route("/api/reportes/consumo")
@login_requerido
@rol_requerido("admin", "superadmin")
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
    cls, cls_params = _cls("m.sucursal_id")
    q += cls
    params += cls_params
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
@rol_requerido("admin", "superadmin")
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
    cls, cls_params = _cls("r.sucursal_id")
    q += cls
    params += cls_params
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
@rol_requerido("admin", "superadmin")
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
    cls, cls_params = _cls("v.sucursal_id")
    q += cls
    params += cls_params
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
@rol_requerido("admin", "superadmin")
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
    cls, cls_params = _cls("v.sucursal_id")
    q += cls
    params += cls_params
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
    return responder_excel("ganancias.xlsx",
                         ["Producto", "Cantidad vendida", "Ventas (Bs)", "Costo (Bs)", "Ganancia (Bs)"], filas,
                         [25, 15, 15, 15, 15])


@reportes_bp.route("/api/reportes/valorizacion")
@login_requerido
@rol_requerido("admin", "superadmin")
def reporte_valorizacion():
    conn = get_conn()
    sid = sucursal_actual()
    if sid is None:
        join_stock = "LEFT JOIN (SELECT producto_id, SUM(cantidad) AS cantidad FROM lotes GROUP BY producto_id) s ON s.producto_id = p.id"
        params = []
    else:
        join_stock = "LEFT JOIN (SELECT producto_id, SUM(cantidad) AS cantidad FROM lotes WHERE sucursal_id = %s GROUP BY producto_id) s ON s.producto_id = p.id"
        params = [sid]
    rows = conn.execute("""
        SELECT p.nombre, p.unidad, p.costo_promedio, p.precio_venta,
               COALESCE(s.cantidad, 0) AS stock,
               ROUND(p.costo_promedio * COALESCE(s.cantidad, 0), 2) AS valor
        FROM productos p
        {join_stock}
        WHERE p.activo = 1 AND COALESCE(s.cantidad, 0) > 0
        ORDER BY valor DESC
    """.format(join_stock=join_stock), params).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@reportes_bp.route("/api/reportes/vencimientos")
@login_requerido
@rol_requerido("admin", "superadmin")
def reporte_vencimientos():
    conn = get_conn()
    sid = sucursal_actual()
    if sid is None:
        params = []
        lote_cond = ""
    else:
        params = [sid]
        lote_cond = " AND l.sucursal_id = %s"
    rows = conn.execute("""
        SELECT p.nombre, l.fecha_vencimiento AS vencimiento, p.unidad,
               l.cantidad AS stock, s.nombre AS sucursal
        FROM lotes l
        JOIN productos p ON p.id = l.producto_id
        JOIN sucursales s ON s.id = l.sucursal_id
        WHERE p.activo = 1 AND l.cantidad > 0 AND l.fecha_vencimiento IS NOT NULL{lote_cond}
        ORDER BY l.fecha_vencimiento
    """.format(lote_cond=lote_cond), params).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


def _auditar(conn):
    """Resumen de consistencia numérica del sistema (solo lectura)."""
    rows = conn.execute("""
        SELECT p.nombre, p.id AS producto_id, l.sucursal_id, s.nombre AS sucursal,
               l.cantidad AS stock_tab, l.fecha_vencimiento AS venc,
               SUM(IF(ml.tipo='entrada', ml.cantidad, -ml.cantidad)) AS mov,
               l.cantidad - SUM(IF(ml.tipo='entrada', ml.cantidad, -ml.cantidad)) AS dif
        FROM lotes l
        JOIN productos p ON p.id = l.producto_id
        JOIN sucursales s ON s.id = l.sucursal_id
        LEFT JOIN movimientos ml ON ml.lote_id = l.id
        GROUP BY l.id, p.nombre, p.id, l.sucursal_id, s.nombre, l.cantidad, l.fecha_vencimiento
        HAVING ABS(l.cantidad - SUM(IF(ml.tipo='entrada', ml.cantidad, -ml.cantidad))) > 0.001
        ORDER BY p.nombre, s.nombre
    """).fetchall()
    ventas = conn.execute("""
        SELECT COUNT(*) AS n,
               COALESCE(SUM(v.total <> COALESCE(d.t, 0)), 0) AS m_total,
               COALESCE(SUM(d.t IS NULL), 0) AS m_vacio
        FROM ventas v
        LEFT JOIN (SELECT venta_id, SUM(subtotal) t FROM venta_detalle GROUP BY venta_id) d ON d.venta_id = v.id
    """).fetchone()
    repartos = conn.execute("""
        SELECT COUNT(*) AS n,
               COALESCE(SUM(x.total <> COALESCE(d.t, 0)), 0) AS m_total,
               COALESCE(SUM(d.t IS NULL), 0) AS m_vacio
        FROM repartos x
        LEFT JOIN (SELECT reparto_id, SUM(subtotal) t FROM reparto_detalle GROUP BY reparto_id) d ON d.reparto_id = x.id
    """).fetchone()
    val = conn.execute("""
        SELECT s.nombre AS sucursal,
               ROUND(SUM(p.costo_promedio * l.cantidad), 2) AS valor,
               SUM(l.cantidad) AS unid
        FROM lotes l
        JOIN productos p ON p.id = l.producto_id
        JOIN sucursales s ON s.id = l.sucursal_id
        WHERE l.cantidad <> 0
        GROUP BY l.sucursal_id
        ORDER BY valor DESC
    """).fetchall()
    con_stock = conn.execute("SELECT COUNT(*) AS n FROM lotes WHERE cantidad > 0").fetchone()["n"]
    con_venc = conn.execute("SELECT COUNT(*) AS n FROM lotes WHERE cantidad > 0 AND fecha_vencimiento IS NOT NULL").fetchone()["n"]
    proveedores = conn.execute("SELECT COUNT(*) AS n FROM proveedores").fetchone()["n"]
    return {
        "descuadres": [dict(r) for r in rows],
        "ventas": dict(ventas),
        "repartos": dict(repartos),
        "valorizacion": [dict(r) for r in val],
        "total_valorizacion": round(float(sum(r["valor"] or 0 for r in val)), 2),
        "productos_con_stock": con_stock,
        "productos_con_vencimiento": con_venc,
        "proveedores": proveedores,
    }


@reportes_bp.route("/api/reportes/auditoria")
@login_requerido
@rol_requerido("admin", "superadmin")
def reporte_auditoria():
    conn = get_conn()
    resultado = _auditar(conn)
    conn.close()
    return ok(resultado)


@reportes_bp.route("/api/reportes/reconciliar", methods=["POST"])
@login_requerido
@rol_requerido("admin", "superadmin")
def reporte_reconciliar():
    import re as _re
    conn = get_conn()
    antes = _auditar(conn)
    borrados = 0
    cur = conn.execute("""SELECT id, nota FROM movimientos
                          WHERE nota LIKE 'Venta #%' OR nota LIKE 'Reparto #%'
                             OR nota LIKE 'Anulación%' OR nota LIKE 'Reposición%'
                             OR nota LIKE 'Devolución%' OR nota LIKE 'Descuento%'""").fetchall()
    for m in cur:
        mm = _re.search(r"(venta|reparto)\s*#\s*(\d+)", m["nota"] or "", _re.I)
        if not mm:
            continue
        tipo, nid = mm.group(1).lower(), int(mm.group(2))
        tabla = "ventas" if tipo == "venta" else "repartos"
        existe = conn.execute("SELECT id FROM {t} WHERE id = %s".format(t=tabla), (nid,)).fetchone()
        if not existe:
            conn.execute("DELETE FROM movimientos WHERE id = %s", (m["id"],))
            borrados += 1
    conn.execute("UPDATE lotes l JOIN "
                 "(SELECT lote_id, SUM(IF(tipo='entrada', cantidad, -cantidad)) c "
                 " FROM movimientos WHERE lote_id IS NOT NULL GROUP BY lote_id) m ON m.lote_id = l.id "
                 "SET l.cantidad = m.c")
    conn.execute("UPDATE lotes SET cantidad = 0 WHERE cantidad < 0.0001")
    conn.execute("UPDATE ventas v JOIN (SELECT venta_id, SUM(subtotal) t FROM venta_detalle GROUP BY venta_id) d "
                 "ON d.venta_id = v.id SET v.total = d.t")
    conn.execute("UPDATE repartos r JOIN (SELECT reparto_id, SUM(subtotal) t FROM reparto_detalle GROUP BY reparto_id) d "
                 "ON d.reparto_id = r.id SET r.total = d.t")
    conn.commit()
    despues = _auditar(conn)
    conn.close()
    return ok({
        "movimientos_huerfanos_borrados": borrados,
        "antes": antes,
        "despues": despues,
    })
