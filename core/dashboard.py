from datetime import date

from flask import Blueprint

from database import get_conn
from .util import ok, login_requerido

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/api/dashboard")
@login_requerido
def dashboard():
    conn = get_conn()
    hoy = date.today().isoformat()

    total_productos = conn.execute("SELECT COUNT(*) c FROM productos WHERE activo = 1").fetchone()["c"]

    stock_total = conn.execute("""
        SELECT COUNT(*) FROM stock s JOIN productos p ON p.id = s.producto_id
        WHERE s.cantidad > 0 AND p.activo = 1
    """).fetchone()[0]

    top_entrada = conn.execute("""
        SELECT p.nombre, p.unidad, SUM(m.cantidad) AS total
        FROM movimientos m JOIN productos p ON p.id = m.producto_id
        WHERE m.tipo = 'entrada'
        GROUP BY m.producto_id ORDER BY total DESC LIMIT 5
    """).fetchall()

    top_salida = conn.execute("""
        SELECT p.nombre, p.unidad, SUM(m.cantidad) AS total
        FROM movimientos m JOIN productos p ON p.id = m.producto_id
        WHERE m.tipo = 'salida'
        GROUP BY m.producto_id ORDER BY total DESC LIMIT 5
    """).fetchall()

    valor = conn.execute("""
        SELECT COALESCE(SUM(s.cantidad * p.costo_promedio), 0) AS valor
        FROM stock s JOIN productos p ON p.id = s.producto_id
        WHERE s.cantidad > 0 AND p.activo = 1
    """).fetchone()["valor"]

    stock_bajo = conn.execute("""
        SELECT p.id, p.nombre, p.stock_minimo, p.unidad, p.costo_promedio, p.precio_venta,
               COALESCE(s.cantidad, 0) AS stock
        FROM productos p
        LEFT JOIN stock s ON s.producto_id = p.id
        WHERE p.activo = 1 AND COALESCE(s.cantidad, 0) <= COALESCE(p.stock_minimo, 10)
        ORDER BY stock ASC
    """).fetchall()

    por_vencer = conn.execute("""
        SELECT p.id, p.nombre, p.vencimiento, p.unidad, COALESCE(s.cantidad, 0) AS stock,
               CASE
                   WHEN date(p.vencimiento) < date(?) THEN 'vencido'
                   WHEN date(p.vencimiento) <= date(?, '+7 days') THEN 'urgente'
                   ELSE 'proximo'
               END AS estado
        FROM productos p
        LEFT JOIN stock s ON s.producto_id = p.id
        WHERE p.activo = 1 AND p.vencimiento IS NOT NULL AND p.vencimiento != ''
          AND date(p.vencimiento) <= date(?, '+30 days')
        ORDER BY p.vencimiento
    """, (hoy, hoy, hoy)).fetchall()

    ventas_mes = conn.execute("""
        SELECT COALESCE(SUM(v.total), 0) AS total
        FROM ventas v
        WHERE substr(v.fecha, 1, 7) = substr(?, 1, 7)
    """, (hoy,)).fetchone()["total"]

    ventas_anio = conn.execute("""
        SELECT COALESCE(SUM(v.total), 0) AS total
        FROM ventas v
        WHERE substr(v.fecha, 1, 4) = substr(?, 1, 4)
    """, (hoy,)).fetchone()["total"]

    ventas_hoy = conn.execute("""
        SELECT COALESCE(SUM(v.total), 0) AS total
        FROM ventas v
        WHERE substr(v.fecha, 1, 10) = ?
    """, (hoy,)).fetchone()["total"]

    gastos_mes = conn.execute("""
        SELECT COALESCE(SUM(g.monto), 0) AS total
        FROM gastos g
        WHERE substr(g.fecha, 1, 7) = substr(?, 1, 7)
    """, (hoy,)).fetchone()["total"]

    gastos_anio = conn.execute("""
        SELECT COALESCE(SUM(g.monto), 0) AS total
        FROM gastos g
        WHERE substr(g.fecha, 1, 4) = substr(?, 1, 4)
    """, (hoy,)).fetchone()["total"]

    entradas_mes = conn.execute("""
        SELECT COALESCE(SUM(m.cantidad), 0) AS total
        FROM movimientos m
        WHERE m.tipo = 'entrada' AND substr(m.fecha, 1, 7) = substr(?, 1, 7)
    """, (hoy,)).fetchone()["total"]

    salidas_mes = conn.execute("""
        SELECT COALESCE(SUM(m.cantidad), 0) AS total
        FROM movimientos m
        WHERE m.tipo = 'salida' AND substr(m.fecha, 1, 7) = substr(?, 1, 7)
    """, (hoy,)).fetchone()["total"]

    por_vencer_hoy = conn.execute("""
        SELECT COUNT(*) c FROM productos p
        WHERE p.activo = 1 AND p.vencimiento IS NOT NULL AND p.vencimiento != ''
          AND date(p.vencimiento) <= date(?)
    """, (hoy,)).fetchone()["c"]

    gastos_hoy = conn.execute("""
        SELECT COALESCE(SUM(g.monto), 0) AS total
        FROM gastos g
        WHERE substr(g.fecha, 1, 10) = ?
    """, (hoy,)).fetchone()["total"]

    num_ventas_hoy = conn.execute("""
        SELECT COUNT(*) c FROM ventas v
        WHERE substr(v.fecha, 1, 10) = ?
    """, (hoy,)).fetchone()["c"]

    num_ventas_mes = conn.execute("""
        SELECT COUNT(*) c FROM ventas v
        WHERE substr(v.fecha, 1, 7) = substr(?, 1, 7)
    """, (hoy,)).fetchone()["c"]

    repartos_mes = conn.execute("""
        SELECT COUNT(*) c FROM repartos r
        WHERE substr(r.fecha, 1, 7) = substr(?, 1, 7)
    """, (hoy,)).fetchone()["c"]

    utilidad_hoy = conn.execute("""
        SELECT COALESCE(SUM((d.precio_unitario - d.costo_unitario) * d.cantidad), 0) AS utilidad
        FROM venta_detalle d JOIN ventas v ON v.id = d.venta_id
        WHERE substr(v.fecha, 1, 10) = ?
    """, (hoy,)).fetchone()["utilidad"]

    utilidad_mes = conn.execute("""
        SELECT COALESCE(SUM((d.precio_unitario - d.costo_unitario) * d.cantidad), 0) AS utilidad
        FROM venta_detalle d JOIN ventas v ON v.id = d.venta_id
        WHERE substr(v.fecha, 1, 7) = substr(?, 1, 7)
    """, (hoy,)).fetchone()["utilidad"]

    utilidad_anio = conn.execute("""
        SELECT COALESCE(SUM((d.precio_unitario - d.costo_unitario) * d.cantidad), 0) AS utilidad
        FROM venta_detalle d JOIN ventas v ON v.id = d.venta_id
        WHERE substr(v.fecha, 1, 4) = substr(?, 1, 4)
    """, (hoy,)).fetchone()["utilidad"]

    mov_recientes = conn.execute("""
        SELECT m.fecha, m.tipo, m.cantidad, m.precio_unitario, m.nota, m.usuario,
               p.nombre AS producto_nombre, p.unidad
        FROM movimientos m
        JOIN productos p ON p.id = m.producto_id
        ORDER BY m.fecha DESC, m.id DESC
        LIMIT 8
    """).fetchall()

    total_alertas = len(stock_bajo) + len(por_vencer)

    conn.close()
    return ok({
        "total_productos": total_productos,
        "stock_total": stock_total,
        "top_entrada": [dict(r) for r in top_entrada],
        "top_salida": [dict(r) for r in top_salida],
        "valor_inventario": round(valor, 2),
        "ventas_hoy": round(ventas_hoy, 2),
        "ventas_mes": round(ventas_mes, 2),
        "ventas_anio": round(ventas_anio, 2),
        "num_ventas_hoy": num_ventas_hoy,
        "num_ventas_mes": num_ventas_mes,
        "repartos_mes": repartos_mes,
        "gastos_hoy": round(gastos_hoy, 2),
        "gastos_mes": round(gastos_mes, 2),
        "gastos_anio": round(gastos_anio, 2),
        "utilidad_hoy": round(utilidad_hoy, 2),
        "utilidad_mes": round(utilidad_mes, 2),
        "utilidad_anio": round(utilidad_anio, 2),
        "entradas_mes": round(entradas_mes, 2),
        "salidas_mes": round(salidas_mes, 2),
        "por_vencer_hoy": por_vencer_hoy,
        "total_alertas": total_alertas,
        "stock_bajo": [dict(r) for r in stock_bajo],
        "por_vencer": [dict(r) for r in por_vencer],
        "mov_recientes": [dict(r) for r in mov_recientes],
    })


@dashboard_bp.route("/api/dashboard/graficos")
@login_requerido
def dashboard_graficos():
    conn = get_conn()
    hoy = date.today()

    meses = []
    anio, mes = hoy.year, hoy.month
    for i in range(5, -1, -1):
        mm = mes - i
        yy = anio
        while mm <= 0:
            mm += 12
            yy -= 1
        clave = f"{yy:04d}-{mm:02d}"
        etiqueta = ["Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"][mm - 1]
        ventas = conn.execute("""
            SELECT COALESCE(SUM(v.total), 0) AS t FROM ventas v
            WHERE substr(v.fecha, 1, 7) = ?
        """, (clave,)).fetchone()["t"]
        gastos = conn.execute("""
            SELECT COALESCE(SUM(g.monto), 0) AS t FROM gastos g
            WHERE substr(g.fecha, 1, 7) = ?
        """, (clave,)).fetchone()["t"]
        utilidad = conn.execute("""
            SELECT COALESCE(SUM((d.precio_unitario - d.costo_unitario) * d.cantidad), 0) AS t
            FROM venta_detalle d JOIN ventas v ON v.id = d.venta_id
            WHERE substr(v.fecha, 1, 7) = ?
        """, (clave,)).fetchone()["t"]
        meses.append({"mes": etiqueta, "ventas": round(ventas, 2),
                      "gastos": round(gastos, 2), "utilidad": round(utilidad, 2)})

    top_productos = conn.execute("""
        SELECT d.producto_nombre AS nombre, SUM(d.cantidad) AS cantidad,
               SUM(d.subtotal) AS total
        FROM venta_detalle d
        GROUP BY d.producto_id, d.producto_nombre
        ORDER BY cantidad DESC LIMIT 5
    """).fetchall()

    top_repartos = conn.execute("""
        SELECT s.nombre, COALESCE(SUM(r.total), 0) AS total
        FROM sucursales s LEFT JOIN repartos r ON r.sucursal_id = s.id
        GROUP BY s.id ORDER BY total DESC LIMIT 5
    """).fetchall()

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
    data = {
        "almacenes": [dict(r) for r in conn.execute("SELECT * FROM almacenes ORDER BY nombre").fetchall()],
        "categorias": [dict(r) for r in conn.execute("SELECT * FROM categorias ORDER BY nombre").fetchall()],
        "proveedores": [dict(r) for r in conn.execute("SELECT * FROM proveedores ORDER BY nombre").fetchall()],
    }
    conn.close()
    return ok(data)
