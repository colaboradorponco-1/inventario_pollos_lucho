from datetime import date

from flask import Blueprint, request, session

from database import get_conn
from .util import ok, err, login_requerido, registrar_auditoria, registrar_movimiento

productos_bp = Blueprint("productos", __name__)


def siguiente_codigo(conn):
    fila = conn.execute("SELECT MAX(codigo) m FROM productos WHERE codigo LIKE 'PRD-%'").fetchone()["m"]
    if not fila:
        return "PRD-0001"
    try:
        n = int(fila.split("-")[1]) + 1
    except (ValueError, IndexError):
        n = 1
    return f"PRD-{n:04d}"


@productos_bp.route("/api/productos", methods=["GET", "POST"])
@login_requerido
def productos():
    conn = get_conn()
    if request.method == "POST":
        data = request.get_json()
        codigo = (data.get("codigo") or "").strip() or siguiente_codigo(conn)
        if conn.execute("SELECT id FROM productos WHERE codigo = ?", (codigo,)).fetchone():
            conn.close()
            return err("Ya existe un producto con ese código")
        cur = conn.execute("""
            INSERT INTO productos (codigo, nombre, categoria_id, unidad, stock_minimo, costo_promedio,
                                   precio_venta, vencimiento, almacen_id, proveedor_id, activo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (codigo, data["nombre"].strip(), data.get("categoria_id"),
              data.get("unidad", "unidad"), data.get("stock_minimo", 0) or 0,
              data.get("costo_promedio", 0) or 0, data.get("precio_venta", 0) or 0,
              data.get("vencimiento"), data.get("almacen_id"), data.get("proveedor_id")))
        conn.commit()
        new_id = cur.lastrowid
        stock_inicial = data.get("stock_inicial", 0) or 0
        if stock_inicial > 0:
            registrar_movimiento(conn, new_id, "entrada", stock_inicial,
                                 data.get("costo_promedio", 0) or 0,
                                 date.today().isoformat(), "Stock inicial",
                                 session.get("usuario", ""), data.get("almacen_id"))
            conn.commit()
        conn.close()
        registrar_auditoria("Producto creado", f"{data['nombre'].strip()} ({codigo})")
        return ok({"codigo": codigo}, message="Producto creado")

    filtro = request.args.get("filtro", "").strip()
    categoria = request.args.get("categoria", "").strip()
    proveedor = request.args.get("proveedor", "").strip()
    estado = request.args.get("estado", "").strip()
    q = """
        SELECT p.*, c.nombre AS categoria_nombre, a.nombre AS almacen_nombre, pr.nombre AS proveedor_nombre,
               COALESCE(s.cantidad, 0) AS stock
        FROM productos p
        LEFT JOIN categorias c ON c.id = p.categoria_id
        LEFT JOIN almacenes a ON a.id = p.almacen_id
        LEFT JOIN proveedores pr ON pr.id = p.proveedor_id
        LEFT JOIN stock s ON s.producto_id = p.id
        WHERE p.activo = 1
    """
    params = []
    if filtro:
        q += " AND (p.nombre LIKE ? OR p.codigo LIKE ? OR pr.nombre LIKE ? OR c.nombre LIKE ?)"
        params += [f"%{filtro}%"] * 4
    if categoria:
        q += " AND p.categoria_id = ?"
        params.append(categoria)
    if proveedor:
        q += " AND p.proveedor_id = ?"
        params.append(proveedor)
    if estado == "con-stock":
        q += " AND COALESCE(s.cantidad, 0) > 0"
    elif estado == "agotado":
        q += " AND COALESCE(s.cantidad, 0) <= 0"
    elif estado == "stock-bajo":
        q += " AND p.stock_minimo > 0 AND COALESCE(s.cantidad, 0) <= p.stock_minimo"
    elif estado == "por-vencer":
        q += (" AND p.vencimiento IS NOT NULL AND p.vencimiento != ''"
              " AND date(p.vencimiento) <= date('now', '+14 days')")
    q += " ORDER BY p.nombre"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@productos_bp.route("/api/productos/codigo")
@login_requerido
def producto_por_codigo():
    codigo = request.args.get("codigo", "").strip()
    if not codigo:
        return err("Ingrese un código de barras")
    conn = get_conn()
    row = conn.execute("""
        SELECT p.*, COALESCE(s.cantidad, 0) AS stock
        FROM productos p
        LEFT JOIN stock s ON s.producto_id = p.id
        WHERE p.codigo = ? COLLATE NOCASE AND p.activo = 1
    """, (codigo,)).fetchone()
    conn.close()
    if not row:
        return err("Producto no encontrado con ese código", 404)
    return ok(dict(row))


@productos_bp.route("/api/productos/<int:prod_id>", methods=["PUT", "DELETE"])
@login_requerido
def producto(prod_id):
    conn = get_conn()
    if request.method == "DELETE":
        conn.execute("UPDATE productos SET activo = 0 WHERE id = ?", (prod_id,))
        conn.commit()
        conn.close()
        registrar_auditoria("Producto eliminado", f"Producto ID {prod_id}")
        return ok(message="Producto eliminado")
    data = request.get_json()
    codigo = data.get("codigo", "").strip() or None
    if codigo:
        dup = conn.execute(
            "SELECT id FROM productos WHERE codigo = ? COLLATE NOCASE AND id != ?",
            (codigo, prod_id)).fetchone()
        if dup:
            conn.close()
            return err("Ya existe otro producto con ese código de barras")
    conn.execute("""
        UPDATE productos SET codigo=?, nombre=?, categoria_id=?, unidad=?, stock_minimo=?,
               costo_promedio=?, precio_venta=?, vencimiento=?, almacen_id=?, proveedor_id=?
        WHERE id=?
    """, (codigo, data["nombre"].strip(), data.get("categoria_id"),
          data.get("unidad", "unidad"), data.get("stock_minimo", 0) or 0,
          data.get("costo_promedio", 0) or 0, data.get("precio_venta", 0) or 0,
          data.get("vencimiento"), data.get("almacen_id"), data.get("proveedor_id"), prod_id))
    conn.commit()
    conn.close()
    registrar_auditoria("Producto actualizado", f"Producto ID {prod_id}")
    return ok(message="Producto actualizado")
