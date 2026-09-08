from datetime import date

from flask import Blueprint, session

from database import get_conn
from .util import ok, login_requerido, sucursal_actual, sucursal_operativa, es_gestion, es_encargado_almacen

dashboard_bp = Blueprint("dashboard", __name__)


def _cls(col):
    """Devuelve (condición SQL, params) para filtrar por la sucursal del usuario
    (o filtro vacío para superadmin que ve todo)."""
    sid = sucursal_actual()
    if sid is None:
        return "", []
    return f" AND {col} = %s", [sid]


@dashboard_bp.route("/api/dashboard")
@login_requerido
def dashboard():
    conn = get_conn()
    hoy = date.today().isoformat()
    sid = sucursal_actual()
    # Los encargados de almacén principal coordinan el inventario: ven las alertas
    # de stock de TODAS las sucursales, igual que el admin (su "mano derecha").
    alerta_global = sid is None or es_encargado_almacen(conn)

    # Helper que a partir del where base construye query + params con filtro de sucursal
    def scoped(sql_where, sql_extra="", params_extra=None):
        cond, cls_params = _cls(sql_where)
        q = " WHERE 1=1" + cond + sql_extra
        params = cls_params + (params_extra or [])
        return q, params

    # Scalar helpers
    def esc(fn):
        return conn.execute(fn[0], fn[1]).fetchone()["t"]

    if sid is None:
        total_productos = conn.execute("SELECT COUNT(*) c FROM productos WHERE activo = 1").fetchone()["c"]
    else:
        total_productos = conn.execute("""
            SELECT COUNT(DISTINCT s.producto_id) c
            FROM stock s JOIN productos p ON p.id = s.producto_id
            WHERE p.activo = 1 AND s.sucursal_id = %s
        """, (sid,)).fetchone()["c"]

    if sid is None:
        stock_total = conn.execute("""
            SELECT COUNT(*) c FROM stock s JOIN productos p ON p.id = s.producto_id
            WHERE s.cantidad > 0 AND p.activo = 1
        """).fetchone()["c"]
        valor = conn.execute("""
            SELECT COALESCE(SUM(s.cantidad * p.costo_promedio), 0) AS valor
            FROM stock s JOIN productos p ON p.id = s.producto_id
            WHERE s.cantidad > 0 AND p.activo = 1
        """).fetchone()["valor"]
    else:
        stock_total = conn.execute("""
            SELECT COUNT(*) c FROM stock s JOIN productos p ON p.id = s.producto_id
            WHERE s.cantidad > 0 AND p.activo = 1 AND s.sucursal_id = %s
        """, (sid,)).fetchone()["c"]
        valor = conn.execute("""
            SELECT COALESCE(SUM(s.cantidad * p.costo_promedio), 0) AS valor
            FROM stock s JOIN productos p ON p.id = s.producto_id
            WHERE s.cantidad > 0 AND p.activo = 1 AND s.sucursal_id = %s
        """, (sid,)).fetchone()["valor"]

    # Joins para las alertas de stock (globales si el usuario las ve de todas las sucursales)
    if alerta_global:
        stock_join = "LEFT JOIN (SELECT producto_id, SUM(cantidad) AS cantidad FROM stock GROUP BY producto_id) s ON s.producto_id = p.id"
        stock_where = ""
        stock_params = []
    else:
        stock_join = "LEFT JOIN stock s ON s.producto_id = p.id AND s.sucursal_id = %s"
        stock_where = " AND s.sucursal_id = %s AND s.producto_id IS NOT NULL"
        stock_params = [sid, sid]

    # Top entradas/salidas
    top_entrada = conn.execute("""
        SELECT p.nombre, p.unidad, SUM(m.cantidad) AS total
        FROM movimientos m JOIN productos p ON p.id = m.producto_id
        WHERE m.tipo = 'entrada'""" + (_cls("m.sucursal_id")[0] or "") + """
        GROUP BY m.producto_id ORDER BY total DESC LIMIT 5
    """, _cls("m.sucursal_id")[1]).fetchall()

    top_salida = conn.execute("""
        SELECT p.nombre, p.unidad, SUM(m.cantidad) AS total
        FROM movimientos m JOIN productos p ON p.id = m.producto_id
        WHERE m.tipo = 'salida'""" + (_cls("m.sucursal_id")[0] or "") + """
        GROUP BY m.producto_id ORDER BY total DESC LIMIT 5
    """, _cls("m.sucursal_id")[1]).fetchall()

    stock_bajo = conn.execute("""
        SELECT p.id, p.nombre, p.codigo, p.stock_minimo, p.unidad, p.costo_promedio, p.precio_venta,
               COALESCE(s.cantidad, 0) AS stock
        FROM productos p
        {join}
        WHERE p.activo = 1 AND COALESCE(s.cantidad, 0) <= COALESCE(p.stock_minimo, 10)
              {where}
        ORDER BY stock ASC
    """.format(join=stock_join, where=stock_where), stock_params).fetchall()

    if alerta_global:
        corr_stock = "0"
        por_extra = ""
        por_params = [hoy, hoy, hoy]
    else:
        corr_stock = "COALESCE((SELECT s2.cantidad FROM stock s2 WHERE s2.producto_id = p.id AND s2.sucursal_id = %s), 0)"
        por_extra = " AND p.id IN (SELECT DISTINCT producto_id FROM stock WHERE sucursal_id = %s)"
        por_params = [sid, hoy, hoy, hoy, sid]

    por_vencer = conn.execute("""
        SELECT p.id, p.nombre, p.vencimiento, p.unidad, {corr} AS stock,
               CASE
                   WHEN STR_TO_DATE(p.vencimiento, '%Y-%m-%d') < DATE(%s) THEN 'vencido'
                   WHEN STR_TO_DATE(p.vencimiento, '%Y-%m-%d') <= DATE_ADD(DATE(%s), INTERVAL 7 DAY) THEN 'urgente'
                   ELSE 'proximo'
               END AS estado
        FROM productos p
        WHERE p.activo = 1 AND p.vencimiento IS NOT NULL AND p.vencimiento != ''
          AND STR_TO_DATE(p.vencimiento, '%Y-%m-%d') <= DATE_ADD(DATE(%s), INTERVAL 30 DAY)
              {extra}
        ORDER BY p.vencimiento
    """.format(corr=corr_stock, extra=por_extra), por_params).fetchall()

    q, p = scoped("v.sucursal_id", " AND substr(v.fecha, 1, 7) = substr(%s, 1, 7)", [hoy])
    ventas_mes = esc(("SELECT COALESCE(SUM(v.total), 0) AS t FROM ventas v" + q, p))
    q, p = scoped("v.sucursal_id", " AND substr(v.fecha, 1, 4) = substr(%s, 1, 4)", [hoy])
    ventas_año = esc(("SELECT COALESCE(SUM(v.total), 0) AS t FROM ventas v" + q, p))
    q, p = scoped("v.sucursal_id", " AND substr(v.fecha, 1, 10) = %s", [hoy])
    ventas_hoy = esc(("SELECT COALESCE(SUM(v.total), 0) AS t FROM ventas v" + q, p))

    q, p = scoped("g.sucursal_id", " AND substr(g.fecha, 1, 7) = substr(%s, 1, 7)", [hoy])
    gastos_mes = esc(("SELECT COALESCE(SUM(g.monto), 0) AS t FROM gastos g" + q, p))
    q, p = scoped("g.sucursal_id", " AND substr(g.fecha, 1, 4) = substr(%s, 1, 4)", [hoy])
    gastos_año = esc(("SELECT COALESCE(SUM(g.monto), 0) AS t FROM gastos g" + q, p))
    q, p = scoped("g.sucursal_id", " AND substr(g.fecha, 1, 10) = %s", [hoy])
    gastos_hoy = esc(("SELECT COALESCE(SUM(g.monto), 0) AS t FROM gastos g" + q, p))

    q, p = scoped("m.sucursal_id", " AND m.tipo = 'entrada' AND substr(m.fecha, 1, 7) = substr(%s, 1, 7)", [hoy])
    entradas_mes = esc(("SELECT COALESCE(SUM(m.cantidad), 0) AS t FROM movimientos m" + q, p))
    q, p = scoped("m.sucursal_id", " AND m.tipo = 'salida' AND substr(m.fecha, 1, 7) = substr(%s, 1, 7)", [hoy])
    salidas_mes = esc(("SELECT COALESCE(SUM(m.cantidad), 0) AS t FROM movimientos m" + q, p))

    por_vencer_hoy = conn.execute("""
        SELECT COUNT(*) c FROM productos p
        WHERE p.activo = 1 AND p.vencimiento IS NOT NULL AND p.vencimiento != ''
          AND STR_TO_DATE(p.vencimiento, '%Y-%m-%d') <= DATE(%s)
    """ + (" AND " + ("p.id IN (SELECT DISTINCT producto_id FROM stock WHERE sucursal_id = %s)" if not alerta_global else "1=1")), (hoy,) + (tuple([sid]) if not alerta_global else ())).fetchone()["c"]

    def count_scoped(fn):
        c, cp = fn
        return conn.execute(c, cp).fetchone()["c"]

    num_ventas_hoy = count_scoped((
        "SELECT COUNT(*) c FROM ventas v" + scoped("v.sucursal_id", " AND substr(v.fecha, 1, 10) = %s", [hoy])[0],
        scoped("v.sucursal_id", " AND substr(v.fecha, 1, 10) = %s", [hoy])[1]))
    num_ventas_mes = count_scoped((
        "SELECT COUNT(*) c FROM ventas v" + scoped("v.sucursal_id", " AND substr(v.fecha, 1, 7) = substr(%s, 1, 7)", [hoy])[0],
        scoped("v.sucursal_id", " AND substr(v.fecha, 1, 7) = substr(%s, 1, 7)", [hoy])[1]))
    if sid is None:
        repartos_mes = count_scoped((
            "SELECT COUNT(*) c FROM repartos r" + scoped("r.sucursal_id", " AND substr(r.fecha, 1, 7) = substr(%s, 1, 7)", [hoy])[0],
            scoped("r.sucursal_id", " AND substr(r.fecha, 1, 7) = substr(%s, 1, 7)", [hoy])[1]))
    else:
        repartos_mes = count_scoped((
            "SELECT COUNT(*) c FROM repartos r WHERE (r.sucursal_id = %s OR r.origen_sucursal_id = %s)"
            " AND substr(r.fecha, 1, 7) = substr(%s, 1, 7)",
            [sid, sid, hoy]))

    q, p = scoped("v.sucursal_id", " AND substr(v.fecha, 1, 4) = substr(%s, 1, 4)", [hoy])
    num_ventas_año = count_scoped(("SELECT COUNT(*) c FROM ventas v" + q, p))
    if sid is None:
        repartos_hoy = count_scoped((
            "SELECT COUNT(*) c FROM repartos r" + scoped("r.sucursal_id", " AND substr(r.fecha, 1, 10) = %s", [hoy])[0],
            scoped("r.sucursal_id", " AND substr(r.fecha, 1, 10) = %s", [hoy])[1]))
        repartos_año = count_scoped((
            "SELECT COUNT(*) c FROM repartos r" + scoped("r.sucursal_id", " AND substr(r.fecha, 1, 4) = substr(%s, 1, 4)", [hoy])[0],
            scoped("r.sucursal_id", " AND substr(r.fecha, 1, 4) = substr(%s, 1, 4)", [hoy])[1]))
    else:
        repartos_hoy = count_scoped((
            "SELECT COUNT(*) c FROM repartos r WHERE (r.sucursal_id = %s OR r.origen_sucursal_id = %s)"
            " AND substr(r.fecha, 1, 10) = %s",
            [sid, sid, hoy]))
        repartos_año = count_scoped((
            "SELECT COUNT(*) c FROM repartos r WHERE (r.sucursal_id = %s OR r.origen_sucursal_id = %s)"
            " AND substr(r.fecha, 1, 4) = substr(%s, 1, 4)",
            [sid, sid, hoy]))

    def util_scoped(base_where_seg):
        where_cls = _cls("v.sucursal_id")[0]
        params = _cls("v.sucursal_id")[1] + base_where_seg[1]
        q = (" WHERE 1=1" + where_cls + " AND " + base_where_seg[0])
        return esc(("SELECT COALESCE(SUM((d.precio_unitario - d.costo_unitario) * d.cantidad), 0) AS t "
                    "FROM venta_detalle d JOIN ventas v ON v.id = d.venta_id" + q, params))

    utilidad_hoy = util_scoped(("substr(v.fecha, 1, 10) = %s", [hoy]))
    utilidad_mes = util_scoped(("substr(v.fecha, 1, 7) = substr(%s, 1, 7)", [hoy]))
    utilidad_año = util_scoped(("substr(v.fecha, 1, 4) = substr(%s, 1, 4)", [hoy]))

    cls_mov, params_mov = _cls("m.sucursal_id")
    mov_recientes = conn.execute("""
        SELECT m.fecha, m.tipo, m.cantidad, m.precio_unitario, m.nota, m.usuario,
               p.nombre AS producto_nombre, p.unidad
        FROM movimientos m
        JOIN productos p ON p.id = m.producto_id
        WHERE 1=1""" + cls_mov + """
        ORDER BY m.fecha DESC, m.id DESC
        LIMIT 8
    """, params_mov).fetchall()

    sesiones_recientes = conn.execute("""
        SELECT fecha, usuario, accion, detalle
        FROM auditoria
        WHERE accion IN ('Inicio de sesion', 'Cierre de sesion')"""
        + (" AND usuario IN (SELECT usuario FROM usuarios WHERE sucursal_id = %s)" if sid is not None else "") + """
        ORDER BY fecha DESC, id DESC
        LIMIT 10
    """, (tuple([sid]) if sid is not None else ())).fetchall()

    total_alertas = len(stock_bajo) + len(por_vencer)

    conn.close()
    return ok({
        "rol": session.get("rol"),
        "total_productos": total_productos,
        "stock_total": stock_total,
        "top_entrada": [dict(r) for r in top_entrada],
        "top_salida": [dict(r) for r in top_salida],
        "valor_inventario": round(valor, 2),
        "ventas_hoy": round(ventas_hoy, 2),
        "ventas_mes": round(ventas_mes, 2),
        "ventas_anio": round(ventas_año, 2),
        "num_ventas_hoy": num_ventas_hoy,
        "num_ventas_mes": num_ventas_mes,
        "num_ventas_anio": num_ventas_año,
        "repartos_hoy": repartos_hoy,
        "repartos_mes": repartos_mes,
        "repartos_anio": repartos_año,
        "gastos_hoy": round(gastos_hoy, 2),
        "gastos_mes": round(gastos_mes, 2),
        "gastos_anio": round(gastos_año, 2),
        "utilidad_hoy": round(utilidad_hoy, 2),
        "utilidad_mes": round(utilidad_mes, 2),
        "utilidad_anio": round(utilidad_año, 2),
        "entradas_mes": round(entradas_mes, 2),
        "salidas_mes": round(salidas_mes, 2),
        "por_vencer_hoy": por_vencer_hoy,
        "total_alertas": total_alertas,
        "stock_bajo": [dict(r) for r in stock_bajo],
        "por_vencer": [dict(r) for r in por_vencer],
        "mov_recientes": [dict(r) for r in mov_recientes],
        "sesiones_recientes": [dict(r) for r in sesiones_recientes],
    })


@dashboard_bp.route("/api/dashboard/graficos")
@login_requerido
def dashboard_graficos():
    conn = get_conn()
    hoy = date.today()
    sid = sucursal_actual()
    cond, cls_params = _cls("v.sucursal_id")
    cond_m, cls_params_m = _cls("m.sucursal_id")

    partes = []
    params = []
    for tabla in ("ventas", "gastos", "movimientos"):
        cond_b, cls_params_b = _cls("sucursal_id")
        partes.append("SELECT MIN(SUBSTRING(fecha, 1, 7)) AS m FROM " + tabla + " WHERE 1=1" + cond_b)
        params.extend(cls_params_b)
    primer_mes = conn.execute("SELECT MIN(m) AS m FROM (" + " UNION ALL ".join(partes) + ") t", params).fetchone()["m"]

    meses = []
    if primer_mes:
        aa_ini, mm_ini = int(primer_mes[:4]), int(primer_mes[5:7])
    else:
        aa_ini, mm_ini = hoy.year, hoy.month
    fin = hoy.year * 12 + (hoy.month - 1)
    for k in range(aa_ini * 12 + (mm_ini - 1), fin + 1):
        yy, mm = divmod(k, 12)
        clave = f"{yy:04d}-{mm + 1:02d}"
        etiqueta = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"][mm]
        ventas = conn.execute("""
            SELECT COALESCE(SUM(v.total), 0) AS t FROM ventas v
            WHERE 1=1""" + cond + " AND substr(v.fecha, 1, 7) = %s",
            cls_params + [clave]).fetchone()["t"]
        gastos = conn.execute("""
            SELECT COALESCE(SUM(g.monto), 0) AS t FROM gastos g
            WHERE 1=1""" + _cls("g.sucursal_id")[0] + " AND substr(g.fecha, 1, 7) = %s",
            _cls("g.sucursal_id")[1] + [clave]).fetchone()["t"]
        utilidad = conn.execute("""
            SELECT COALESCE(SUM((d.precio_unitario - d.costo_unitario) * d.cantidad), 0) AS t
            FROM venta_detalle d JOIN ventas v ON v.id = d.venta_id
            WHERE 1=1""" + cond + " AND substr(v.fecha, 1, 7) = %s",
            cls_params + [clave]).fetchone()["t"]
        meses.append({"mes": f"{etiqueta} {yy % 100:02d}", "ventas": round(ventas, 2),
                      "gastos": round(gastos, 2), "utilidad": round(utilidad, 2)})

    top_productos = conn.execute("""
        SELECT d.producto_nombre AS nombre, SUM(d.cantidad) AS cantidad,
               SUM(d.subtotal) AS total
        FROM venta_detalle d JOIN ventas v ON v.id = d.venta_id
        WHERE 1=1""" + cond + """
        GROUP BY d.producto_id, d.producto_nombre
        ORDER BY cantidad DESC LIMIT 5
    """, cls_params).fetchall()

    if sid is None:
        top_repartos = conn.execute("""
            SELECT s.nombre, COALESCE(SUM(r.total), 0) AS total
            FROM sucursales s LEFT JOIN repartos r ON r.sucursal_id = s.id
            WHERE 1=1""" + _cls("r.sucursal_id")[0] + """
            GROUP BY s.id ORDER BY total DESC LIMIT 5
        """, _cls("r.sucursal_id")[1]).fetchall()
    else:
        top_repartos = conn.execute("""
            SELECT s.nombre, COALESCE(SUM(r.total), 0) AS total
            FROM sucursales s
            LEFT JOIN repartos r ON (r.sucursal_id = s.id AND r.origen_sucursal_id = %s)
                                 OR (r.origen_sucursal_id = s.id AND r.sucursal_id = %s)
            WHERE s.id != %s
            GROUP BY s.id ORDER BY total DESC LIMIT 5
        """, (sid, sid, sid)).fetchall()

    conn.close()
    return ok({
        "meses": meses,
        "top_productos": [dict(r) for r in top_productos],
        "top_repartos": [dict(r) for r in top_repartos],
    })


@dashboard_bp.route("/api/catalogos")
@login_requerido
def catalogos():
    conn = get_conn()
    sid = sucursal_operativa()
    if es_gestion():
        almacenes = [dict(r) for r in conn.execute("SELECT * FROM almacenes ORDER BY nombre").fetchall()]
    else:
        # Encargado: solo ve el almacén de SU sucursal
        sid_alm = sucursal_actual() or sid
        almacenes = [dict(r) for r in conn.execute(
            "SELECT * FROM almacenes WHERE sucursal_id = %s ORDER BY nombre", (sid_alm,)).fetchall()]
    data = {
        "almacenes": almacenes,
        "categorias": [dict(r) for r in conn.execute("SELECT * FROM categorias ORDER BY nombre").fetchall()],
        "proveedores": [dict(r) for r in conn.execute(
            "SELECT * FROM proveedores WHERE sucursal_id = %s OR sucursal_id IS NULL ORDER BY nombre", (sid,)).fetchall()],
        "sucursales": [dict(r) for r in conn.execute("SELECT * FROM sucursales ORDER BY principal DESC, nombre").fetchall()],
    }
    conn.close()
    return ok(data)
