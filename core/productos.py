import logging
import re
import unicodedata
from datetime import datetime

from flask import Blueprint, request, session

from database import get_conn
from .lotes import (destinos_validos, elegir_destino, stock_por_destino,
                    _ids_proveedores_misma_ciudad)
from .util import (ok, err, login_requerido, registrar_auditoria, registrar_movimiento,
                   ok_paginado, paginar_params, sucursal_actual, sucursal_operativa,
                   clausula_sucursal, stock_actual, es_gestion, es_encargado_almacen,
                   ids_sucursal_consolidada)

productos_bp = Blueprint("productos", __name__)


def norm_nombre(texto):
    """Normaliza un texto (sin acentos, sin símbolos) para comparar nombres."""
    s = unicodedata.normalize("NFKD", str(texto or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]", "", s)


def _tacho_unidad(data):
    """Cuánto entra en UN tacho del producto, en su unidad base (kg, unidades...).
    0 = no definido (el valor se escribe en el pedido)."""
    try:
        v = float(data.get("unidad_tacho") or 0)
    except (TypeError, ValueError):
        return 0
    return v if v > 0 else 0


def _pide_tacho(data):
    """¿Este producto se pide por tachos? (marca solo papa, plátano y los que elijan)."""
    v = data.get("pide_tacho")
    if v is None:
        return 1 if data.get("unidad_tacho") else 0
    return 1 if str(v).lower() in ("1", "true", "on", "yes") else 0


def _para_proveer(data):
    """¿Este producto se ofrece a otras sucursales en pedidos? (1 = sí).
    Los de «solo inventario» se ven en el inventario general pero no aparecen
    cuando otra sucursal arma un pedido."""
    v = data.get("para_proveer")
    if v is None:
        return 1
    return 1 if str(v).lower() in ("1", "true", "on", "yes") else 0


def _stock_sucursal_permitido(conn, ver_todo, sid):
    """Valida ?stock_sucursal= antes de usarlo.

    Sin esta comprobación cualquier encargado podía pedir el stock de OTRA
    sucursal y conocer sus existencias. Devuelve (sucursal_id, respuesta_de_error).
    """
    pedido = request.args.get("stock_sucursal", "").strip()
    try:
        pedido = int(pedido)
    except (TypeError, ValueError):
        return None, err("El identificador de sucursal no es válido", 400)
    existe = conn.execute("SELECT id FROM sucursales WHERE id = %s", (pedido,)).fetchone()
    if not existe:
        return None, err("La sucursal no existe", 400)
    if not (ver_todo or pedido == sid):
        return None, err("No tienes permisos para ver el stock de esa sucursal", 403)
    return pedido, None


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
            # Central: puede asignar el producto a cualquier sucursal
            sid = data.get("sucursal_id")
        else:
            # El encargado de filial crea productos de SU sucursal y con proveedor de su sucursal
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
        if data.get("almacen_id"):
            # Mismo criterio ampliado que al editar: valen los almacenes de la
            # sucursal elegida y de sus AS hijas (el producto "para proveer"
            # terminará viviendo en la AS y usa los almacenes de la tienda).
            sc = list(ids_sucursal_consolidada(conn, sid))
            if not conn.execute(
                    "SELECT 1 FROM almacenes WHERE id = ? AND sucursal_id IN ("
                    + ",".join(["?"] * len(sc)) + ")",
                    (int(data["almacen_id"]), *sc)).fetchone():
                conn.close()
                return err("El almacén no pertenece a esa sucursal", 400)
        # Un producto «para proveer» de una tienda con almacén de producción
        # separado vive en su sucursal AS ("AS <tienda>"): ahí se ofrece, ahí se
        # despacha. El stock de producción no se mezcla con el de venta.
        if _para_proveer(data):
            from .util import sucursal_almacen_as
            sid = sucursal_almacen_as(conn, sid)
        cur = conn.execute("""
            INSERT INTO productos (codigo, nombre, marca, categoria_id, unidad, stock_minimo, costo_promedio,
                                   precio_venta, vencimiento, almacen_id, proveedor_id, sucursal_id, unidad_tacho, pide_tacho, para_proveer, activo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 1)
        """, (codigo, data["nombre"].strip(), (data.get("marca") or "").strip() or None,
              data.get("categoria_id"),
              data.get("unidad", "unidad"), data.get("stock_minimo", 0) or 0,
              data.get("costo_promedio", 0) or 0, data.get("precio_venta", 0) or 0,
              data.get("almacen_id"), proveedor_id, sid, _tacho_unidad(data), _pide_tacho(data),
              _para_proveer(data)))
        conn.commit()
        new_id = cur.lastrowid
        stock_inicial = data.get("stock_inicial", 0) or 0
        if stock_inicial > 0:
            registrar_movimiento(conn, new_id, "entrada", stock_inicial,
                                 data.get("costo_promedio", 0) or 0,
                                 datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Stock inicial",
                                 session.get("usuario", ""), sucursal_id=sid,
                                 proveedor_id=proveedor_id, vencimiento=data.get("vencimiento"))
            conn.commit()
        aviso = ""
        nv = norm_nombre(data["nombre"])
        if nv:
            unidad_actual = (data.get("unidad") or "unidad").strip().lower()
            duplicado = conn.execute(
                "SELECT nombre, codigo, marca, unidad FROM productos WHERE activo = 1 AND id != ? ORDER BY nombre LIMIT 5",
                (new_id,)).fetchall()
            for f in duplicado:
                if norm_nombre(f["nombre"]) == nv:
                    detalle = f["marca"] and (" - " + f["marca"]) or ""
                    u_existente = (f["unidad"] or "unidad").strip().lower()
                    if u_existente != unidad_actual:
                        aviso = (f"Ya existe '{f['nombre']}'{detalle} (cód. {f['codigo']}) con unidad '{f['unidad']}'. "
                                 "Usa la MISMA unidad en todas las sucursales para no confundir el stock.")
                    else:
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
    para_pedido = request.args.get("para_pedido", "").strip() in ("1", "true", "si")
    sid = sucursal_actual()
    # Sucursal que pide (para acotar el catálogo de pedidos a su misma ciudad).
    desde_arg = request.args.get("desde", "").strip()
    desde_cat = int(desde_arg) if desde_arg.isdigit() else (sid if para_pedido else None)
    # Cualquier valor no numérico en un filtro devolvía 500 en vez de 400.
    def _id_o_none(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    categoria = _id_o_none(categoria) if categoria else None
    proveedor = _id_o_none(proveedor) if proveedor else None
    sucursal = _id_o_none(sucursal) if sucursal else None
    # Admin, superadmin y encargados de almacén principal ven TODO el catálogo
    # (sus productos globales + los de todas las sucursales).
    ver_todo = es_gestion() or es_encargado_almacen(conn)
    # stock_sucursal: fuerza el stock de UNA sucursal concreta (p. ej. el desplegable
    # de ventas/repartos usa el stock de la sucursal del usuario, no el total).
    stock_sid = None
    if request.args.get("stock_sucursal", "").strip():
        stock_sid, e = _stock_sucursal_permitido(conn, ver_todo, sid)
        if e:
            conn.close()
            return e
    elif sucursal is not None:
        # La pestaña de sucursal del listado muestra el stock DE ESA sucursal
        # (ella + su AS), no el de la sucursal del usuario. Si no, al filtrar por
        # otra sucursal los estados "con stock"/"agotado"/"stock bajo"/"por
        # vencer" se calculaban contra los lotes del usuario y la lista salía en 0
        # (pasa en los paneles AS y en los de cualquier filial).
        stock_sid = sucursal
    elif not ver_todo and sid is not None:
        stock_sid = sid
    if stock_sid is not None:
        ids_stock = [int(x) for x in ids_sucursal_consolidada(conn, int(stock_sid))]
        ph_in = ",".join(str(x) for x in ids_stock)
        join_stock = ("LEFT JOIN (SELECT producto_id, SUM(cantidad) AS cantidad "
                      "FROM lotes WHERE sucursal_id IN (" + ph_in + ") GROUP BY producto_id) s ON s.producto_id = p.id")
        lote_cond = " AND l2.sucursal_id IN (" + ph_in + ")"
    else:
        join_stock = "LEFT JOIN (SELECT producto_id, SUM(cantidad) AS cantidad FROM lotes GROUP BY producto_id) s ON s.producto_id = p.id"
        lote_cond = ""
    q = """
        SELECT p.id, p.codigo, p.nombre, p.marca, p.categoria_id, p.unidad, p.stock_minimo,
               p.costo_promedio, p.precio_venta, p.almacen_id, p.proveedor_id, p.sucursal_id, p.activo,
               IFNULL(p.unidad_tacho, 0) AS unidad_tacho,
               IFNULL(p.pide_tacho, 0) AS pide_tacho,
               IFNULL(p.para_proveer, 1) AS para_proveer,
               c.nombre AS categoria_nombre, a.nombre AS almacen_nombre,
               su.nombre AS sucursal_nombre, pr.nombre AS proveedor_nombre,
               COALESCE(s.cantidad, 0) AS stock,
               (SELECT MIN(l2.fecha_vencimiento) FROM lotes l2
                WHERE l2.producto_id = p.id AND l2.cantidad > 0
                  AND l2.fecha_vencimiento IS NOT NULL{lote_cond}) AS vencimiento
        FROM productos p
        LEFT JOIN categorias c ON c.id = p.categoria_id
        LEFT JOIN almacenes a ON a.id = p.almacen_id
        LEFT JOIN sucursales su ON su.id = p.sucursal_id
        LEFT JOIN proveedores pr ON pr.id = p.proveedor_id
        {join_stock}
        WHERE p.activo = ?
    """.format(join_stock=join_stock, lote_cond=lote_cond)
    params = []
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
        if sucursal == 0:
            q += " AND p.sucursal_id IS NULL"
        else:
            # La sucursal incluye sus AS hijas (América/Simón López/6 de Agosto):
            # sus productos viven en la AS y se cuentan/listan en la tienda.
            ids_suc = ids_sucursal_consolidada(conn, sucursal)
            ph = ",".join(["?"] * len(ids_suc))
            q += " AND p.sucursal_id IN (" + ph + ")"
            params.extend(ids_suc)
    # El formulario de pedidos SOLO ofrece lo marcado «para proveer» (más lo
    # global y lo del almacén principal, de donde cualquier sucursal puede
    # pedir). Lo de «solo inventario» se ve en el inventario general pero no se
    # puede pedir.
    if para_pedido:
        q += (" AND (p.para_proveer = 1 OR p.sucursal_id IS NULL OR EXISTS "
              "(SELECT 1 FROM sucursales s2 WHERE s2.id = p.sucursal_id AND s2.principal = 1))")
        # «La versión para proveer del AS manda»: si el artículo ya tiene una
        # fila con casilla «para proveer» en un almacén de sucursal (AS) de la
        # misma ciudad, SOLO esa fila se ofrece en los pedidos. La misma
        # mercadería cargada como global/venta o en el almacén principal queda
        # oculta mientras exista esa versión: si no, los pedidos se repartían
        # entre el principal (44 kg) y el AS (toneladas) sin poder controlarlo.
        permitidos = _ids_proveedores_misma_ciudad(conn, desde_cat)
        ciudad_ok = "1=1" if permitidos is None else (
            "p2.sucursal_id IN (" + ",".join(str(i) for i in permitidos) + ")")
        q += (" AND ("
              "    (p.para_proveer = 1 AND EXISTS ("
              "        SELECT 1 FROM sucursales as_ "
              "        WHERE as_.id = p.sucursal_id AND as_.es_as = 1))"
              "    OR NOT EXISTS ("
              "        SELECT 1 FROM productos p2"
              "        JOIN sucursales as_ ON as_.id = p2.sucursal_id"
              "        WHERE p2.activo = 1 AND IFNULL(p2.para_proveer, 1) = 1"
              "          AND as_.es_as = 1 AND p2.id <> p.id AND " + ciudad_ok + " AND ("
              "            (IFNULL(p2.codigo, '') <> '' "
              "             AND IFNULL(p2.codigo, '') = IFNULL(p.codigo, '')"
              "            ) OR ("
              "              IFNULL(p2.codigo, '') = '' OR IFNULL(p.codigo, '') = ''"
              "            ) AND p2.nombre = p.nombre"
              "        )))")
    if estado == "con-stock":
        q += " AND COALESCE(s.cantidad, 0) > 0"
    elif estado == "agotado":
        q += " AND COALESCE(s.cantidad, 0) <= 0"
    elif estado == "stock-bajo":
        q += " AND p.stock_minimo > 0 AND COALESCE(s.cantidad, 0) <= p.stock_minimo"
    elif estado == "por-vencer":
        q += (" AND EXISTS (SELECT 1 FROM lotes l2 WHERE l2.producto_id = p.id AND l2.cantidad > 0"
              " AND l2.fecha_vencimiento IS NOT NULL"
              " AND l2.fecha_vencimiento <= DATE_ADD(CURDATE(), INTERVAL 14 DAY){lote_cond})"
              ).format(lote_cond=lote_cond)
    q += " ORDER BY p.nombre"
    _, sep, tail = q.partition("FROM productos p")
    count_q = "SELECT COUNT(*) AS c FROM (SELECT 1 " + sep + tail + ") AS sub"
    total = conn.execute(count_q, params).fetchone()["c"]
    offset, limit, pagina, por_pagina = paginar_params()
    q += " LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    dest = request.args.get("destino_id", "").strip()
    destino = int(dest) if dest.isdigit() else None
    prov = _stock_disponible(conn, destino_id=destino)
    # El catálogo del formulario de pedidos se acota a la sucursal que pide
    # (`?desde=`, la que eligió el encargado/gestion en el paso 1). Así lo que
    # la pantalla ofrece es exactamente lo que el servidor acepta al guardar:
    # Principales + sucursales AS de la misma ciudad. (`desde_cat` ya quedó
    # resuelto arriba, antes del filtro de la versión que provee.)
    # `para_pedido=1` lo manda solo el catálogo del formulario de pedidos. En ese
    # caso cada producto trae el destino por el que SE PEDIRÍA (el que tiene
    # stock) y el disponible de ahí, para que la pantalla y el servidor elijan lo
    # mismo. Sin el flag, las demás pantallas siguen midiendo contra el almacén
    # donde el producto está registrado, que es lo de siempre.
    destinos = {}
    nombres = {}
    reservas = {}
    if para_pedido:
        validos = destinos_validos(conn)
        destinos = stock_por_destino(conn)
        # Acotado a la sucursal que pide (la elegida en el paso 1, o la propia):
        # Principales + sucursales AS de su misma ciudad, la misma lista que
        # revalida el alta del pedido. Las tiendas sin AS no despachan pedidos.
        if desde_cat:
            permitidos = _ids_proveedores_misma_ciudad(conn, desde_cat)
            if permitidos is not None:
                validos = [v for v in validos if v in permitidos]
                destinos = {pid: [t for t in lista if t[0] in permitidos]
                            for pid, lista in destinos.items()}
                destinos = {pid: lista for pid, lista in destinos.items() if lista}
        # Un solo SELECT para los nombres: si se hiciera por producto serían
        # cientos de consultas en cada carga del catálogo.
        nombres = {r["id"]: r["nombre"] or "" for r in conn.execute(
            "SELECT id, nombre FROM sucursales").fetchall()}
        # Lo ya apartado en pedidos pendientes o en camino, POR DESTINO. Sin
        # esto se mostraría el stock del destino resuelto sin descontar lo que
        # otros pedidos ya mineraron de ahí, y dos personas podrían pedir lo
        # mismo. El stock se mueve recién al 'entregado', así que 'en_camino'
        # sigue reservando hasta que se entrega.
        for r in conn.execute(
            "SELECT d.producto_id AS pid, "
            "       COALESCE(d.destino_id, pd.destino_id) AS dest, "
            "       SUM(d.cantidad) AS c "
            "FROM pedido_detalle d JOIN pedidos pd ON pd.id = d.pedido_id "
            "WHERE pd.estado IN ('pendiente', 'en_camino') "
            "GROUP BY d.producto_id, COALESCE(d.destino_id, pd.destino_id)"
        ).fetchall():
            reservas[(int(r["pid"]), r["dest"])] = float(r["c"] or 0)
    for r in rows:
        r["stock_prov"] = prov.get(r["id"], 0)
        r["destino_stock"] = None
        r["destino_nombre"] = ""
        # El proveedor (quien distribuye) no tiene stock de este producto. El
        # pedido se puede hacer IGUAL y queda avisado; no es "agotado".
        r["sin_stock"] = False
        if para_pedido and destino is None:
            # Automático: va a quien REPARTE el producto, que es la sucursal que
            # lo tiene en el catálogo (o el principal si esa no despacha).
            # NO se elige "la que tiene stock": tener mercadería no da permiso
            # para repartirla, y el pedido se la llevaría de su propia venta.
            sid_dest, stock_dest = elegir_destino(
                destinos.get(r["id"]), validos, r["sucursal_id"])
            r["destino_id"] = sid_dest
            r["destino_nombre"] = nombres.get(sid_dest, "")
            if sid_dest:
                disp = stock_dest - reservas.get((int(r["id"]), sid_dest), 0.0)
                r["stock_prov"] = max(0.0, disp)
                r["destino_stock"] = stock_dest
                r["sin_stock"] = stock_dest <= 0
            else:
                r["stock_prov"] = 0.0
        else:
            r["destino_id"] = destino
            # Con "Todo a X" el destino es el elegido, así que también se
            # muestra ese nombre y no el del catálogo.
            r["destino_nombre"] = nombres.get(destino, "") if para_pedido else ""
    conn.close()
    return ok_paginado(rows, total, pagina, por_pagina)


@productos_bp.route("/api/productos/codigo")
@login_requerido
def producto_por_codigo():
    codigo = request.args.get("codigo", "").strip()
    if not codigo:
        return err("Ingrese un código de barras")
    conn = get_conn()
    sid = sucursal_actual()
    ver_todo = es_gestion() or es_encargado_almacen(conn)
    stock_sid = None
    if request.args.get("stock_sucursal", "").strip():
        stock_sid, e = _stock_sucursal_permitido(conn, ver_todo, sid)
        if e:
            conn.close()
            return e
    elif not ver_todo and sid is not None:
        stock_sid = sid
    if stock_sid is not None:
        ids_stock = [int(x) for x in ids_sucursal_consolidada(conn, int(stock_sid))]
        ph_in = ",".join(str(x) for x in ids_stock)
        join_stock = ("LEFT JOIN (SELECT producto_id, SUM(cantidad) AS cantidad "
                      "FROM lotes WHERE sucursal_id IN (" + ph_in + ") GROUP BY producto_id) s ON s.producto_id = p.id")
        lote_cond = " AND l2.sucursal_id IN (" + ph_in + ")"
    else:
        join_stock = "LEFT JOIN (SELECT producto_id, SUM(cantidad) AS cantidad FROM lotes GROUP BY producto_id) s ON s.producto_id = p.id"
        lote_cond = ""
    scope_sql, scope_params = scope_productos(ver_todo, sucursal_operativa(), "")
    row = conn.execute("""
        SELECT p.id, p.codigo, p.nombre, p.marca, p.categoria_id, p.unidad, p.stock_minimo,
               p.costo_promedio, p.precio_venta, p.almacen_id, p.proveedor_id, p.sucursal_id, p.activo,
               IFNULL(p.unidad_tacho, 0) AS unidad_tacho,
               IFNULL(p.pide_tacho, 0) AS pide_tacho,
               IFNULL(p.para_proveer, 1) AS para_proveer,
               COALESCE(s.cantidad, 0) AS stock,
               (SELECT MIN(l2.fecha_vencimiento) FROM lotes l2
                WHERE l2.producto_id = p.id AND l2.cantidad > 0
                  AND l2.fecha_vencimiento IS NOT NULL{lote_cond}) AS vencimiento
        FROM productos p
        {join_stock}
        WHERE LOWER(p.codigo) = LOWER(?) AND p.activo = 1
        {scope}
    """.format(join_stock=join_stock, lote_cond=lote_cond, scope=scope_sql),
        ([codigo] + scope_params)).fetchone()
    conn.close()
    if not row:
        return err("Producto no encontrado con ese código", 404)
    return ok(dict(row))


def _stock_disponible(conn, destino_id=None):
    """Dict {producto_id: disponible} PARA UN ALMACÉN DETERMINADO.

    "Disponible" = el stock que ese almacén puede despacho del producto, menos
    lo ya apartado en pedidos pendientes (nadie pierde stock por pedir lo mismo).

    Hay más de un almacén principal, así que el disponible ya no puede ser un
    único número por producto: la misma mercadería puede estar en Almacén 1 y
    no en Almacén 2. Por eso `destino_id` decide contra qué almacén se mide, y
    es el mismo destino que se guarda en `pedido_detalle.destino_id`.

    Sin `destino_id` se mantiene el comportamiento anterior (el almacén donde
    vive el producto), que es lo que usan el resto de pantallas."""
    ppal = conn.execute("SELECT id FROM sucursales WHERE principal = 1 ORDER BY id LIMIT 1").fetchone()
    if not ppal:
        return {}
    # Sin `destino_id` cada producto se mide contra SU almacén (y los productos
    # globales contra el primer almacén principal), que es lo que usan el resto
    # de pantallas y no se toca.
    # Con `destino_id` se mide contra ese almacén: cuenta lo que hay en sus
    # lotes. No hace falta ningún caso especial para los productos globales,
    # porque su stock también vive en lotes de un almacén concreto — si está en
    # D suma, si está en otro no, que es exactamente lo que se quiere ver.
    if destino_id:
        cond_lote = "l.sucursal_id = %s"
        params = (destino_id,)
        # La reserva se descuenta SOLO de los pedidos dirigidos a este almacén.
        # Antes no se filtraba por destino, así que un pedido pendiente para
        # Almacén 1 restaba mercadería del disponible de América y Simón López
        # aunque allí no tuviera nada que ver: un almacén podía mostrarse sin
        # stock por culpa de un pedido ajeno.
        cond_apartado = "AND COALESCE(d.destino_id, pd.destino_id) = %s"
        params_reserva = (destino_id,)
    else:
        cond_lote = "l.sucursal_id = COALESCE(p.sucursal_id, %s)"
        params = (ppal["id"],)
        cond_apartado = ""
        params_reserva = ()
    sql = """
        SELECT l.producto_id AS id,
               (COALESCE(SUM(CASE WHEN {cond_lote}
                                  THEN l.cantidad ELSE 0 END), 0)
                - COALESCE((SELECT SUM(d.cantidad) FROM pedido_detalle d
                            JOIN pedidos pd ON pd.id = d.pedido_id
                            WHERE d.producto_id = l.producto_id
                              AND pd.estado IN ('pendiente', 'en_camino') {cond_apartado}), 0)) AS disp
        FROM lotes l JOIN productos p ON p.id = l.producto_id
        WHERE l.cantidad > 0 AND p.activo = 1
        GROUP BY l.producto_id""".format(cond_lote=cond_lote, cond_apartado=cond_apartado)
    try:
        rows = conn.execute(sql, params + params_reserva).fetchall()
        return {int(r["id"]): float(max(0, r["disp"] or 0)) for r in rows}
    except Exception as exc:
        # Sin esto el fallo era invisible: devolvía {} y TODOS los productos
        # aparecían como "sin stock disponible", que parece un dato real y en
        # realidad es un error de SQL.
        app_logger = logging.getLogger("app")
        app_logger.error("fallo en _stock_disponible (destino_id=%s): %s", destino_id, exc)
        return {}


@productos_bp.route("/api/productos/disponible", methods=["GET"])
@login_requerido
def productos_disponible():
    """Disponible de cada producto para armar pedidos (stock del proveedor menos
    lo apartado en pedidos pendientes).

    `?destino_id=N` mide contra ese almacén: hay más de un almacén principal y
    la misma mercadería puede estar en uno y no en el otro."""
    conn = get_conn()
    dest = request.args.get("destino_id", "").strip()
    destino = int(dest) if dest.isdigit() else None
    disp = _stock_disponible(conn, destino_id=destino)
    conn.close()
    return ok(disp)


@productos_bp.route("/api/productos/<int:prod_id>", methods=["PUT", "DELETE"])
@login_requerido
def producto(prod_id):
    conn = get_conn()
    fila = conn.execute("SELECT * FROM productos WHERE id = ?", (prod_id,)).fetchone()
    if not fila:
        conn.close()
        return err("Producto no encontrado", 404)
    # Admin/superadmin y encargados de almacén principal gestionan cualquier producto;
    # las sucursales filiales solo gestionan los de SU ámbito: su sucursal + sus AS
    # hijas (los productos de América/Simón López/6 de Agosto viven en la AS y el
    # encargado de la tienda debe poder gestionarlos igual que los propios).
    if not es_gestion() and not es_encargado_almacen(conn):
        sid_user = sucursal_actual()
        cons = ids_sucursal_consolidada(conn, sid_user) if sid_user is not None else []
        if sid_user is None or int(fila["sucursal_id"] or 0) not in cons:
            app_logger = logging.getLogger("app")
            app_logger.error("producto %s de sucursal %s editado por %s con ámbito %s -> 403",
                             prod_id, fila["sucursal_id"], sid_user, cons)
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
    sid_p = fila["sucursal_id"] if not es_gestion() else (data.get("sucursal_id") or fila["sucursal_id"])
    if not sid_p:
        conn.close()
        return err("Debes asignar una sucursal al producto", 400)
    sid_p = int(sid_p)
    if data.get("almacen_id"):
        # El almacén vale si es de la propia sucursal, de sus AS hijas o, si el
        # producto vive en una AS (América/Simón López/6 de Agosto), de la tienda
        # madre: el encargado de la tienda elige sus almacenes y el producto se
        # despacha desde la AS.
        sc = list(ids_sucursal_consolidada(conn, sid_p))
        if len(sc) == 1:
            pr = conn.execute("SELECT padre_id FROM sucursales WHERE id = ? AND es_as = 1",
                              (sid_p,)).fetchone()
            if pr and pr["padre_id"]:
                sc.append(int(pr["padre_id"]))
        if not conn.execute(
                "SELECT 1 FROM almacenes WHERE id = ? AND sucursal_id IN ("
                + ",".join(["?"] * len(sc)) + ")",
                (int(data["almacen_id"]), *sc)).fetchone():
            conn.close()
            return err("El almacén no pertenece a esa sucursal", 400)
    conn.execute("""
        UPDATE productos SET codigo=?, nombre=?, marca=?, categoria_id=?, unidad=?, stock_minimo=?,
               costo_promedio=?, precio_venta=?, almacen_id=?, proveedor_id=?, sucursal_id=?,
               unidad_tacho=?, pide_tacho=?, para_proveer=?
        WHERE id=?
    """, (codigo, data["nombre"].strip(), (data.get("marca") or "").strip() or None,
          data.get("categoria_id"),
          data.get("unidad", "unidad"), data.get("stock_minimo", 0) or 0,
          data.get("costo_promedio", 0) or 0, data.get("precio_venta", 0) or 0,
          data.get("almacen_id"), data.get("proveedor_id"), sid_p,
          _tacho_unidad(data) if data.get("unidad_tacho") is not None
          else (fila.get("unidad_tacho") or 0),
          _pide_tacho(data) if data.get("pide_tacho") is not None
          else (fila.get("pide_tacho") or 0),
          _para_proveer(data) if data.get("para_proveer") is not None
          else int(fila.get("para_proveer") or 1), prod_id))
    # Ajuste directo de stock: registra la diferencia como movimiento para mantener la sincronía
    stock_nuevo = data.get("stock")
    if stock_nuevo is not None:
        try:
            stock_nuevo = float(stock_nuevo or 0)
        except (TypeError, ValueError):
            conn.rollback()
            conn.close()
            return err("El stock debe ser un número", 400)
        if stock_nuevo < 0:
            conn.rollback()
            conn.close()
            return err("El stock no puede ser negativo", 400)
        # El ajuste va en la sucursal NUEVA (la que se acaba de guardar, sid_p).
        # Antes se usaba `fila["sucursal_id"]`, que es el valor VIEJO de antes del
        # UPDATE: si un admin movía el producto de América a La Paz y mandaba stock,
        # el movimiento quedaba en América y el stock de La Paz no cambiaba.
        sid_stock = sid_p or sucursal_operativa()
        if not sid_stock:
            conn.rollback()
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
        SELECT m.fecha, m.tipo, m.cantidad, m.precio_unitario, m.nota, m.usuario,
               m.proveedor_id
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
    # `wb` y `conn` se abren antes del try y se cierran en el finally. Antes, cualquier
    # error a mitad de la importación (una columna rara, un tipo de dato, un corte de
    # conexión) salía por el `except` de abajo y dejaba el archivo .xlsx abierto —que
    # load_workbook mantiene abierto como zip— y una conexión MySQL viva. Con unos
    # pocos intentos fallidos el servidor se queda sin conexiones y deja de responder.
    wb = None
    conn = None
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
            return err("Debes elegir una sucursal para importar los productos", 400)
        s = int(raw_suc)
        if not (es_gestion() or es_encargado_almacen(conn)) and s != sucursal_actual():
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
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1)
                """, (codigo, nombre, marca, cat_id, unidad, stock_min, costo, precio, prov_id, sid_imp))
                prod_id = cur.lastrowid
                stock_cant = parse_num(rd.get(h_stock)) if h_stock else 0.0
                if stock_cant > 0:
                    registrar_movimiento(conn, prod_id, "entrada", stock_cant, costo,
                                         datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                         "Importación Excel", session.get("usuario", ""),
                                         sucursal_id=sid_imp, vencimiento=vencimiento)
                importados += 1
            except Exception as e:
                errores.append(f"Fila {row_idx}: {str(e)}")
        conn.commit()
        resumen = f"{importados} producto(s) importado(s)"
        if errores:
            resumen += f" · {len(errores)} error(es): " + "; ".join(errores[:8])
            if len(errores) > 8:
                resumen += "..."
        return ok({"importados": importados, "errores": errores}, message=resumen)
    except Exception as e:
        # La fila que falló se anota en `errores` y el resto sigue; solo si la
        # transacción entera no aguanta se llega acá. Se revierte para no dejar
        # productos a medio insertar.
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return err(f"Error al leer el archivo: {str(e)}")
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if wb is not None:
            try:
                wb.close()
            except Exception:
                pass
