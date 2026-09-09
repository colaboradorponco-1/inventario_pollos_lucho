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
        join_stock = "LEFT JOIN (SELECT producto_id, SUM(cantidad) AS cantidad FROM stock GROUP BY producto_id) s ON s.producto_id = p.id"
        stock_params = []
    else:
        join_stock = "LEFT JOIN stock s ON s.producto_id = p.id AND s.sucursal_id = %s"
        stock_params = [sid]
    q = """
        SELECT p.codigo, p.nombre, c.nombre AS categoria, a.nombre AS almacen,
               p.unidad, p.stock_minimo, p.costo_promedio, p.precio_venta,
               COALESCE(s.cantidad, 0) AS stock, p.vencimiento, pr.nombre AS proveedor
        FROM productos p
        LEFT JOIN categorias c ON c.id = p.categoria_id
        LEFT JOIN almacenes a ON a.id = p.almacen_id
        LEFT JOIN proveedores pr ON pr.id = p.proveedor_id
        {join_stock}
        WHERE p.activo = ?
    """.format(join_stock=join_stock)
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
        if ver_todo or suc == 0:
            if suc == 0:
                q += " AND p.sucursal_id IS NULL"
            else:
                q += " AND p.sucursal_id = ?"
                params.append(suc)
        elif sid is not None and suc == sid:
            q += " AND p.sucursal_id = ?"
            params.append(suc)
        else:
            q += " AND 1 = 0"
    if estado == "con-stock":
        q += " AND COALESCE(s.cantidad, 0) > 0"
    elif estado == "agotado":
        q += " AND COALESCE(s.cantidad, 0) = 0"
    elif estado == "stock-bajo":
        q += " AND p.stock_minimo > 0 AND COALESCE(s.cantidad, 0) <= p.stock_minimo"
    elif estado == "por-vencer":
        q += (" AND p.vencimiento IS NOT NULL AND p.vencimiento != ''"
              " AND STR_TO_DATE(p.vencimiento, '%Y-%m-%d') <= DATE_ADD(CURDATE(), INTERVAL 14 DAY)")
    q += " ORDER BY p.nombre"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    filas = [(r["codigo"] or "", r["nombre"], r["categoria"] or "", r["almacen"] or "",
              r["unidad"], r["stock"], r["stock_minimo"], r["costo_promedio"],
              r["precio_venta"], r["proveedor"] or "", r["vencimiento"] or "") for r in rows]
    return responder_excel("productos.xlsx",
                         ["Código", "Producto", "Categoría", "Almacén", "Unidad",
                          "Stock", "Stock mínimo", "Costo (Bs)", "Precio venta (Bs)",
                          "Proveedor", "Vencimiento"],
                         filas, [14, 25, 16, 16, 10, 10, 12, 14, 14, 18, 14],
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
        join_stock = "LEFT JOIN (SELECT producto_id, SUM(cantidad) AS cantidad FROM stock GROUP BY producto_id) s ON s.producto_id = p.id"
        params = []
    else:
        join_stock = "LEFT JOIN stock s ON s.producto_id = p.id AND s.sucursal_id = %s"
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
        join_stock = "LEFT JOIN (SELECT producto_id, SUM(cantidad) AS cantidad FROM stock GROUP BY producto_id) s ON s.producto_id = p.id"
        params = []
    else:
        join_stock = "LEFT JOIN stock s ON s.producto_id = p.id AND s.sucursal_id = %s"
        params = [sid]
    rows = conn.execute("""
        SELECT p.nombre, p.vencimiento, p.unidad, COALESCE(s.cantidad, 0) AS stock
        FROM productos p
        {join_stock}
        WHERE p.activo = 1 AND p.vencimiento IS NOT NULL AND p.vencimiento != ''
        ORDER BY p.vencimiento
    """.format(join_stock=join_stock), params).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


def _auditar(conn):
    """Resumen de consistencia numérica del sistema (solo lectura)."""
    rows = conn.execute("""
        SELECT p.nombre, p.id AS producto_id, st.sucursal_id, s.nombre AS sucursal,
               st.cantidad AS stock_tab,
               COALESCE(m.c, 0) AS mov,
               st.cantidad - COALESCE(m.c, 0) AS dif
        FROM stock st
        JOIN productos p ON p.id = st.producto_id
        JOIN sucursales s ON s.id = st.sucursal_id
        LEFT JOIN (
            SELECT producto_id, sucursal_id, SUM(IF(tipo='entrada', cantidad, -cantidad)) c
            FROM movimientos GROUP BY producto_id, sucursal_id
        ) m ON m.producto_id = st.producto_id AND m.sucursal_id = st.sucursal_id
        WHERE st.cantidad - COALESCE(m.c, 0) <> 0
        ORDER BY p.nombre, s.nombre
    """).fetchall()
    faltantes = conn.execute("""
        SELECT p.nombre, p.id AS producto_id, m.sucursal_id, s.nombre AS sucursal,
               0 AS stock_tab, m.c AS mov, 0 - m.c AS dif
        FROM (
            SELECT producto_id, sucursal_id, SUM(IF(tipo='entrada', cantidad, -cantidad)) c
            FROM movimientos GROUP BY producto_id, sucursal_id
        ) m
        JOIN productos p ON p.id = m.producto_id
        JOIN sucursales s ON s.id = m.sucursal_id
        LEFT JOIN stock st ON st.producto_id = m.producto_id AND st.sucursal_id = m.sucursal_id
        WHERE st.producto_id IS NULL AND m.c <> 0
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
               ROUND(SUM(p.costo_promedio * st.cantidad), 2) AS valor,
               SUM(st.cantidad) AS unid
        FROM stock st
        JOIN productos p ON p.id = st.producto_id
        JOIN sucursales s ON s.id = st.sucursal_id
        WHERE st.cantidad <> 0
        GROUP BY st.sucursal_id
        ORDER BY valor DESC
    """).fetchall()
    con_stock = conn.execute("SELECT COUNT(*) AS n FROM stock WHERE cantidad > 0").fetchone()["n"]
    con_venc = conn.execute("SELECT COUNT(*) AS n FROM productos WHERE activo = 1 AND vencimiento IS NOT NULL AND vencimiento != ''").fetchone()["n"]
    proveedores = conn.execute("SELECT COUNT(*) AS n FROM proveedores").fetchone()["n"]
    return {
        "descuadres": [dict(r) for r in rows] + [dict(r) for r in faltantes],
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
    conn.execute("CREATE TEMPORARY TABLE mcalc AS "
                 "SELECT producto_id, sucursal_id, SUM(IF(tipo='entrada', cantidad, -cantidad)) c "
                 "FROM movimientos GROUP BY producto_id, sucursal_id")
    conn.execute("DELETE FROM stock")
    conn.execute("INSERT INTO stock (producto_id, sucursal_id, cantidad) "
                 "SELECT producto_id, sucursal_id, c FROM mcalc WHERE c <> 0")
    conn.execute("DROP TEMPORARY TABLE mcalc")
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
