import re
import unicodedata
from datetime import datetime

from flask import Blueprint, request, session

from database import get_conn
from .util import (ok, err, login_requerido, registrar_auditoria, registrar_movimiento,
                   ok_paginado, paginar_params, sucursal_actual, sucursal_operativa,
                   clausula_sucursal, stock_actual, es_gestion, es_encargado_almacen)

productos_bp = Blueprint("productos", __name__)


def norm_nombre(texto):
    """Normaliza un texto (sin acentos, sin símbolos) para comparar nombres."""
    s = unicodedata.normalize("NFKD", str(texto or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]", "", s)


def scope_productos(ver_todo, sid, scope):
    """(condición_sql, params) para filtrar productos por ámbito de visibilidad.

    - ver_todo (admin/superadmin/encargado de almacén principal):
        ''           -> todos los productos (todas las sucursales)
        mio          -> solo los de mi sucursal
        sucursales   -> los de las demás sucursales
    - sucursal filial:
        ve TODO el catálogo (visibilidad universal). Lo que agrega el admin
        o cualquier encargado se ve en todas las sucursales; cada una maneja
        su propio stock según la sucursal del usuario que consulta.
    """
    if ver_todo:
        if scope == "mio":
            if sid:
                return " AND p.sucursal_id = %s", [sid]
            return " AND p.sucursal_id IS NULL", []
        if scope == "sucursales":
            if sid:
                return " AND p.sucursal_id IS NOT NULL AND p.sucursal_id != %s", [sid]
            return " AND p.sucursal_id IS NOT NULL", []
        return "", []
    return "", []


def siguiente_codigo(conn):
    filas = conn.execute("SELECT codigo FROM productos WHERE codigo LIKE 'PRD-%'").fetchall()
    max_n = 0
    for f in filas:
        try:
            n = int((f["codigo"] or "").split("-")[1])
        except (ValueError, IndexError):
            continue
        if n > max_n:
            max_n = n
    return f"PRD-{max_n + 1:04d}"


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
        proveedor_id = data.get("proveedor_id")
        if es_gestion() or es_encargado_almacen(conn):
            sid = data.get("sucursal_id")
        else:
            # El encargado crea productos de SU sucursal y con proveedor de su sucursal
            sid = sucursal_actual()
            if proveedor_id:
                prov = conn.execute("SELECT sucursal_id FROM proveedores WHERE id = ?", (int(proveedor_id),)).fetchone()
                if not prov or (prov["sucursal_id"] and prov["sucursal_id"] != sid):
                    conn.close()
                    return err("El proveedor debe pertenecer a tu sucursal", 400)
        if not sid:
            conn.close()
            return err("Debes asignar una sucursal al producto", 400)
        sid = int(sid)
        cur = conn.execute("""
            INSERT INTO productos (codigo, nombre, marca, categoria_id, unidad, stock_minimo, costo_promedio,
                                   precio_venta, vencimiento, almacen_id, proveedor_id, sucursal_id, activo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (codigo, data["nombre"].strip(), (data.get("marca") or "").strip() or None,
              data.get("categoria_id"),
              data.get("unidad", "unidad"), data.get("stock_minimo", 0) or 0,
              data.get("costo_promedio", 0) or 0, data.get("precio_venta", 0) or 0,
              data.get("vencimiento"), data.get("almacen_id"), proveedor_id, sid))
        conn.commit()
        new_id = cur.lastrowid
        stock_inicial = data.get("stock_inicial", 0) or 0
        if stock_inicial > 0:
            registrar_movimiento(conn, new_id, "entrada", stock_inicial,
                                 data.get("costo_promedio", 0) or 0,
                                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Stock inicial",
                                 session.get("usuario", ""), sucursal_id=sid,
                                 proveedor_id=proveedor_id)
            conn.commit()
        aviso = ""
        nv = norm_nombre(data["nombre"])
        if nv:
            duplicado = conn.execute(
                "SELECT nombre, codigo, marca FROM productos WHERE activo = 1 AND id != ? ORDER BY nombre LIMIT 5",
                (new_id,)).fetchall()
            for f in duplicado:
                if norm_nombre(f["nombre"]) == nv:
                    detalle = f["marca"] and (" - " + f["marca"]) or ""
                    aviso = f"Ya existe '{f['nombre']}'{detalle} (cód. {f['codigo']}). Verifica que no sea el mismo producto."
                    break
        conn.close()
        registrar_auditoria("Producto creado", f"{data['nombre'].strip()} ({codigo})")
        return ok({"codigo": codigo, "aviso": aviso or None}, message="Producto creado")

    filtro = request.args.get("filtro", "").strip()
    categoria = request.args.get("categoria", "").strip()
    proveedor = request.args.get("proveedor", "").strip()
    estado = request.args.get("estado", "").strip()
    sucursal = request.args.get("sucursal", "").strip()
    sid = sucursal_actual()
    # Admin, superadmin y encargados de almacén principal ven TODO el catálogo
    # (sus productos globales + los de todas las sucursales).
    ver_todo = es_gestion() or es_encargado_almacen(conn)
    if ver_todo or sid is None:
        join_stock = "LEFT JOIN (SELECT producto_id, SUM(cantidad) AS cantidad FROM stock GROUP BY producto_id) s ON s.producto_id = p.id"
    else:
        join_stock = "LEFT JOIN stock s ON s.producto_id = p.id AND s.sucursal_id = ?"
    q = """
        SELECT p.*, c.nombre AS categoria_nombre, a.nombre AS almacen_nombre,
               su.nombre AS sucursal_nombre, pr.nombre AS proveedor_nombre,
               COALESCE(s.cantidad, 0) AS stock
        FROM productos p
        LEFT JOIN categorias c ON c.id = p.categoria_id
        LEFT JOIN almacenes a ON a.id = p.almacen_id
        LEFT JOIN sucursales su ON su.id = p.sucursal_id
        LEFT JOIN proveedores pr ON pr.id = p.proveedor_id
        {join_stock}
        WHERE p.activo = ?
    """.format(join_stock=join_stock)
    params = []
    if sid is not None and not ver_todo:
        params.append(sid)
    if estado == "inactivos":
        params.append(0)
    else:
        params.append(1)
    # Filtro por ámbito: admin/almacén principal alternan entre mi almacén y las sucursales;
    # las filiales ven globales (almacenes principales) + sus productos.
    scope_sql, scope_params = scope_productos(ver_todo, sucursal_operativa(), request.args.get("scope", "").strip())
    q += scope_sql
    params += scope_params
    if filtro:
        q += " AND (p.nombre LIKE ? OR p.codigo LIKE ? OR pr.nombre LIKE ? OR c.nombre LIKE ?)"
        params += [f"%{filtro}%"] * 4
    if categoria:
        q += " AND p.categoria_id = ?"
        params.append(categoria)
    if proveedor:
        q += " AND p.proveedor_id = ?"
        params.append(proveedor)
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
        q += " AND COALESCE(s.cantidad, 0) <= 0"
    elif estado == "stock-bajo":
        q += " AND p.stock_minimo > 0 AND COALESCE(s.cantidad, 0) <= p.stock_minimo"
    elif estado == "por-vencer":
        q += (" AND p.vencimiento IS NOT NULL AND p.vencimiento != ''"
              " AND STR_TO_DATE(p.vencimiento, '%Y-%m-%d') <= DATE_ADD(CURDATE(), INTERVAL 14 DAY)")
    q += " ORDER BY p.nombre"
    _, sep, tail = q.partition("FROM productos p")
    count_q = "SELECT COUNT(*) AS c FROM (SELECT 1 " + sep + tail + ") AS sub"
    total = conn.execute(count_q, params).fetchone()["c"]
    offset, limit, pagina, por_pagina = paginar_params()
    q += " LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok_paginado([dict(r) for r in rows], total, pagina, por_pagina)


@productos_bp.route("/api/productos/codigo")
@login_requerido
def producto_por_codigo():
    codigo = request.args.get("codigo", "").strip()
    if not codigo:
        return err("Ingrese un código de barras")
    conn = get_conn()
    sid = sucursal_actual()
    ver_todo = es_gestion() or es_encargado_almacen(conn)
    if ver_todo or sid is None:
        join_stock = "LEFT JOIN (SELECT producto_id, SUM(cantidad) AS cantidad FROM stock GROUP BY producto_id) s ON s.producto_id = p.id"
        params = [codigo]
    else:
        join_stock = "LEFT JOIN stock s ON s.producto_id = p.id AND s.sucursal_id = ?"
        params = [sid, codigo]
    scope_sql, scope_params = scope_productos(ver_todo, sucursal_operativa(), "")
    row = conn.execute("""
        SELECT p.*, COALESCE(s.cantidad, 0) AS stock
        FROM productos p
        {join_stock}
        WHERE LOWER(p.codigo) = LOWER(?) AND p.activo = 1
        {scope}
    """.format(join_stock=join_stock, scope=scope_sql),
        params + scope_params).fetchone()
    conn.close()
    if not row:
        return err("Producto no encontrado con ese código", 404)
    return ok(dict(row))


@productos_bp.route("/api/productos/<int:prod_id>", methods=["PUT", "DELETE"])
@login_requerido
def producto(prod_id):
    conn = get_conn()
    fila = conn.execute("SELECT * FROM productos WHERE id = ?", (prod_id,)).fetchone()
    if not fila:
        conn.close()
        return err("Producto no encontrado", 404)
    # Admin/superadmin y encargados de almacén principal gestionan cualquier producto;
    # las sucursales filiales solo gestionan los de SU sucursal (los que crearon).
    if not es_gestion() and not es_encargado_almacen(conn) and fila["sucursal_id"] != sucursal_actual():
        conn.close()
        return err("Solo puedes gestionar productos de tu sucursal", 403)
    if request.method == "DELETE":
        conn.execute("UPDATE productos SET activo = 0 WHERE id = ?", (prod_id,))
        conn.commit()
        conn.close()
        registrar_auditoria("Producto eliminado", f"Producto ID {prod_id}")
        return ok(message="Producto eliminado")

    data = request.get_json()
    codigo = (data.get("codigo") or "").strip() or None
    if codigo:
        dup = conn.execute(
            "SELECT id FROM productos WHERE LOWER(codigo) = LOWER(?) AND id != ?",
            (codigo, prod_id)).fetchone()
        if dup:
            conn.close()
            return err("Ya existe otro producto con ese código de barras")
    sid_p = fila["sucursal_id"] if not (es_gestion() or es_encargado_almacen(conn)) else (data.get("sucursal_id") or fila["sucursal_id"])
    if not sid_p:
        conn.close()
        return err("Debes asignar una sucursal al producto", 400)
    sid_p = int(sid_p)
    conn.execute("""
        UPDATE productos SET codigo=?, nombre=?, marca=?, categoria_id=?, unidad=?, stock_minimo=?,
               costo_promedio=?, precio_venta=?, vencimiento=?, almacen_id=?, proveedor_id=?, sucursal_id=?
        WHERE id=?
    """, (codigo, data["nombre"].strip(), (data.get("marca") or "").strip() or None,
          data.get("categoria_id"),
          data.get("unidad", "unidad"), data.get("stock_minimo", 0) or 0,
          data.get("costo_promedio", 0) or 0, data.get("precio_venta", 0) or 0,
          data.get("vencimiento"), data.get("almacen_id"), data.get("proveedor_id"), sid_p, prod_id))
    # Ajuste directo de stock: registra la diferencia como movimiento para mantener la sincronía
    stock_nuevo = data.get("stock")
    if stock_nuevo is not None:
        stock_nuevo = float(stock_nuevo or 0)
        if stock_nuevo < 0:
            conn.close()
            return err("El stock no puede ser negativo", 400)
        sid_stock = fila["sucursal_id"] or sucursal_operativa()
        if not sid_stock:
            conn.close()
            return err("No tienes una sucursal asignada para ajustar el stock", 400)
        actual = stock_actual(conn, prod_id, sid_stock)
        dif = round(stock_nuevo - actual, 2)
        if dif != 0:
            costo = float(data.get("costo_promedio", 0) or 0)
            registrar_movimiento(conn, prod_id, "entrada" if dif > 0 else "salida",
                                 abs(dif), costo,
                                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                 "Ajuste de inventario", session.get("usuario", ""),
                                 sucursal_id=sid_stock)
    conn.commit()
    conn.close()
    registrar_auditoria("Producto actualizado", f"Producto ID {prod_id}")
    return ok(message="Producto actualizado")


@productos_bp.route("/api/productos/<int:prod_id>/restaurar", methods=["POST"])
@login_requerido
def producto_restaurar(prod_id):
    if not es_gestion():
        return err("Solo administradores pueden restaurar productos", 403)
    conn = get_conn()
    conn.execute("UPDATE productos SET activo = 1 WHERE id = ?", (prod_id,))
    conn.commit()
    conn.close()
    registrar_auditoria("Producto restaurado", f"Producto ID {prod_id}")
    return ok(message="Producto restaurado")


@productos_bp.route("/api/productos/<int:prod_id>/historial")
@login_requerido
def producto_historial(prod_id):
    conn = get_conn()
    prod = conn.execute("SELECT * FROM productos WHERE id = ?", (prod_id,)).fetchone()
    if not prod:
        conn.close()
        return err("Producto no encontrado", 404)
    cls, cls_params = clausula_sucursal("m.sucursal_id")
    movimientos = conn.execute("""
        SELECT m.fecha, m.tipo, m.cantidad, m.precio_unitario, m.nota, m.usuario
        FROM movimientos m WHERE m.producto_id = ? {cls}
        ORDER BY m.fecha DESC, m.id DESC LIMIT 200
    """.format(cls=cls), [prod_id] + cls_params).fetchall()
    stock_val = stock_actual(conn, prod_id)
    conn.close()
    return ok({
        "producto": dict(prod),
        "stock": stock_val,
        "movimientos": [dict(r) for r in movimientos],
    })


@productos_bp.route("/api/productos/importar", methods=["POST"])
@login_requerido
def importar_productos():
    from database import get_conn as gc
    archivo = request.files.get("archivo")
    if not archivo:
        return err("No se envió ningún archivo")
    try:
        from openpyxl import load_workbook
        wb = load_workbook(archivo, read_only=True)
        ws = wb.active
        conn = gc()

        def norm(t):
            s = unicodedata.normalize("NFKD", str(t or ""))
            s = "".join(c for c in s if not unicodedata.combining(c)).lower()
            return re.sub(r"[^a-z0-9]", "", s)

        def parse_num(v):
            if v is None or v == "":
                return 0.0
            if isinstance(v, (int, float)):
                return float(v)
            s = re.sub(r"[^\d.\-,]", "", str(v))
            if "." in s and "," in s:
                if s.rfind(",") > s.rfind("."):
                    s = s.replace(".", "").replace(",", ".")
                else:
                    s = s.replace(",", "").replace(".", ".")
            elif "," in s:
                s = s.replace(",", ".")
            try:
                return float(s)
            except ValueError:
                return 0.0

        def norm_fecha(v):
            if isinstance(v, datetime):
                return v.strftime("%Y-%m-%d")
            s = str(v or "").strip()
            m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
            if m:
                return "%s-%s-%s" % (m.group(1), m.group(2).zfill(2), m.group(3).zfill(2))
            m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
            if m:
                return "%s-%s-%s" % (m.group(3), m.group(2).zfill(2), m.group(1).zfill(2))
            return s or None

        headers = [str(c.value or "").strip() for c in ws[1]]

        def buscar(*alias):
            for al in alias:
                aln = norm(al)
                for h in headers:
                    if norm(h) == aln:
                        return h
            return None

        sid_imp = None
        raw_suc = request.form.get("sucursal_id", "").strip()
        if raw_suc in ("", "0"):
            conn.close()
            wb.close()
            return err("Debes elegir una sucursal para importar los productos", 400)
        s = int(raw_suc)
        if not (es_gestion() or es_encargado_almacen(conn)) and s != sucursal_actual():
            conn.close()
            wb.close()
            return err("No tienes permisos para importar a esa sucursal", 403)
        sid_imp = s
        cat_fallback = request.form.get("categoria_id", "").strip()
        cat_fallback = int(cat_fallback) if cat_fallback.isdigit() else None

        h_nombre = buscar("nombre", "producto", "articulo")
        h_codigo = buscar("codigo", "codigo de barras", "ean", "cod")
        h_marca = buscar("marca", "brand", "marca del producto")
        h_unidad = buscar("unidad", "unidades")
        h_costo = buscar("costo", "costo promedio", "costo (bs)", "costo unitario")
        h_precio = buscar("precio", "precio venta", "precio (bs)", "precio venta (bs)")
        h_stock = buscar("stock", "existencia", "cantidad")
        h_stock_min = buscar("stock minimo", "stockminimo", "minimo")
        h_categoria = buscar("categoria", "cat")
        h_proveedor = buscar("proveedor")
        h_vencimiento = buscar("vencimiento", "fecha vencimiento", "vence")

        importados = 0
        errores = []
        for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), 2):
            try:
                rd = {}
                for i, h in enumerate(headers):
                    rd[h] = row[i] if i < len(row) else None
                nombre = str(rd.get(h_nombre) or "").strip() if h_nombre else ""
                if not nombre:
                    continue
                codigo = str(rd.get(h_codigo) or "").strip() if h_codigo else ""
                if not codigo:
                    codigo = siguiente_codigo(conn)
                marca = (str(rd.get(h_marca) or "").strip() if h_marca else "") or None
                unidad = (str(rd.get(h_unidad) or "unidad").strip() if h_unidad else "unidad") or "unidad"
                costo = parse_num(rd.get(h_costo)) if h_costo else 0.0
                precio = parse_num(rd.get(h_precio)) if h_precio else 0.0
                stock_min = parse_num(rd.get(h_stock_min)) if h_stock_min else 0.0
                categoria = str(rd.get(h_categoria) or "").strip() if h_categoria else ""
                proveedor = str(rd.get(h_proveedor) or "").strip() if h_proveedor else ""
                vencimiento = norm_fecha(rd.get(h_vencimiento)) if h_vencimiento else None

                cat_id = None
                if categoria:
                    f = conn.execute("SELECT id FROM categorias WHERE nombre = ?", (categoria,)).fetchone()
                    if not f:
                        cur = conn.execute("INSERT INTO categorias (nombre) VALUES (?)", (categoria,))
                        cat_id = cur.lastrowid
                    else:
                        cat_id = f["id"]
                if cat_id is None and cat_fallback:
                    cat_id = cat_fallback

                prov_id = None
                if proveedor:
                    f = conn.execute("SELECT id FROM proveedores WHERE nombre = ?", (proveedor,)).fetchone()
                    if not f:
                        cur = conn.execute("INSERT INTO proveedores (nombre, sucursal_id) VALUES (?, ?)",
                                           (proveedor, sid_imp))
                        prov_id = cur.lastrowid
                    else:
                        prov_id = f["id"]

                cur = conn.execute("""
                    INSERT INTO productos (codigo, nombre, marca, categoria_id, unidad, stock_minimo,
                                           costo_promedio, precio_venta, vencimiento, proveedor_id,
                                           sucursal_id, activo)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """, (codigo, nombre, marca, cat_id, unidad, stock_min, costo, precio, vencimiento, prov_id, sid_imp))
                prod_id = cur.lastrowid
                stock_cant = parse_num(rd.get(h_stock)) if h_stock else 0.0
                if stock_cant > 0:
                    registrar_movimiento(conn, prod_id, "entrada", stock_cant, costo,
                                         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                         "Importación Excel", session.get("usuario", ""),
                                         sucursal_id=sid_imp)
                importados += 1
            except Exception as e:
                errores.append(f"Fila {row_idx}: {str(e)}")
        conn.commit()
        conn.close()
        wb.close()
        resumen = f"{importados} producto(s) importado(s)"
        if errores:
            resumen += f" · {len(errores)} error(es): " + "; ".join(errores[:8])
            if len(errores) > 8:
                resumen += "..."
        return ok({"importados": importados, "errores": errores}, message=resumen)
    except Exception as e:
        return err(f"Error al leer el archivo: {str(e)}")
