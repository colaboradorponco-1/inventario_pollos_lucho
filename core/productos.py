import logging
import os
import re
import unicodedata
from datetime import datetime

from flask import Blueprint, request, session

from database import get_conn
from core.backup import carpeta_respaldos, nombre_respaldo, volcar_a_archivo
from .lotes import (destinos_validos, elegir_destino, stock_por_destino,
                    _ids_proveedores_misma_ciudad)
from .util import (ok, err, login_requerido, registrar_auditoria, registrar_movimiento,
                   ok_paginado, paginar_params, sucursal_actual, sucursal_operativa,
                   clausula_sucursal, stock_actual, es_gestion, es_encargado_almacen,
                   ids_sucursal_consolidada, es_superadmin, es_receptor)

productos_bp = Blueprint("productos", __name__)


def norm_nombre(texto):
    """Normaliza un texto (sin acentos, sin símbolos) para comparar nombres."""
    s = unicodedata.normalize("NFKD", str(texto or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]", "", s)


def _norm_nombre_tokens(texto):
    s = unicodedata.normalize("NFKD", str(texto or ""))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return tuple(sorted(re.findall(r"[a-z0-9]+", s)))


def _combinar_detalles_inventario(destino, origen):
    """Combina dos líneas de la misma ficha sin inventar un conteo físico."""
    campos_suma = ("stock_sistema", "inicial", "ingreso_dia", "ingreso_manual",
                   "disponible", "conteo_fisico", "final", "utilizada", "diferencia")
    campos_conteo = ("conteo_fisico", "final", "utilizada", "diferencia")
    valores = {}
    for campo in campos_conteo:
        if (destino[campo] is None) != (origen[campo] is None):
            raise ValueError(
                "Las líneas tienen conteos distintos; completa o revisa ambas antes de unificar.")
    for campo in campos_suma:
        a, b = destino[campo], origen[campo]
        valores[campo] = None if a is None and b is None else (a or 0) + (b or 0)
    observaciones = " · ".join(
        texto for texto in (destino["observaciones"], origen["observaciones"])
        if texto and texto.strip())
    valores["observaciones"] = observaciones or None
    return valores


def _chunks(valores, tamano=300):
    for inicio in range(0, len(valores), tamano):
        yield valores[inicio:inicio + tamano]


def _producto_duplicado(conn, nombre, unidad, excluir_id=None):
    """Busca una ficha activa del mismo producto y unidad en cualquier sucursal."""
    unidad_norm = norm_nombre(unidad or "unidad")
    productos = conn.execute(
        "SELECT id, codigo, nombre, unidad, sucursal_id FROM productos WHERE activo = 1"
        + (" AND id <> ?" if excluir_id is not None else ""),
        (excluir_id,) if excluir_id is not None else ()).fetchall()
    for producto in productos:
        if (norm_nombre(producto["unidad"] or "unidad") == unidad_norm
                and norm_nombre(producto["nombre"]) == norm_nombre(nombre)):
            sucursal = conn.execute(
                "SELECT nombre FROM sucursales WHERE id = ?",
                (producto["sucursal_id"],)).fetchone() if producto["sucursal_id"] else None
            return {
                "id": producto["id"],
                "codigo": producto["codigo"],
                "nombre": producto["nombre"],
                "unidad": producto["unidad"],
                "sucursal_nombre": sucursal["nombre"] if sucursal else None,
            }
    return None


def _producto_duplicado_edicion(conn, nombre, unidad, producto_actual):
    """Solo valida colisiones si al editar se cambia el nombre o la unidad."""
    nombre_actual = producto_actual["nombre"] or ""
    unidad_actual = producto_actual["unidad"] or "unidad"
    if (norm_nombre(nombre) == norm_nombre(nombre_actual)
            and norm_nombre(unidad or "unidad") == norm_nombre(unidad_actual)):
        return None
    return _producto_duplicado(conn, nombre, unidad, excluir_id=producto_actual["id"])


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
    valor = data.get("para_proveer")
    return 1 if str(valor).lower() in ("1", "true", "on", "yes") else 0


def _sucursal_puede_proveer(conn, sucursal_id):
    # Solo los Almacenes Principales y los almacenes AS reparten a las demás
    # sucursales. Un almacén AS se reconoce por la marca `es_as`, por tener un
    # almacén "AS ..." o por el propio nombre ("AS America"...). Las tiendas
    # normales ya no pueden ofrecer productos aunque tengan "provee" o un
    # usuario receptor.
    sucursal = conn.execute(
        "SELECT s.principal, s.es_as, s.nombre, "
        "EXISTS (SELECT 1 FROM almacenes a "
        "WHERE a.sucursal_id = s.id AND a.nombre LIKE 'AS %') AS almacen_as "
        "FROM sucursales s WHERE s.id = ?", (sucursal_id,)).fetchone()
    if not sucursal:
        return False
    nombre = (sucursal.get("nombre") or "").strip().upper()
    return bool(sucursal.get("principal") or sucursal.get("es_as")
                or sucursal.get("almacen_as") or nombre.startswith("AS "))


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
        nombre = (data.get("nombre") or "").strip()
        if not nombre:
            conn.close()
            return err("El nombre del producto es obligatorio", 400)
        if len(nombre) > 255:
            conn.close()
            return err("El nombre del producto no puede superar 255 caracteres", 400)
        duplicado = _producto_duplicado(conn, nombre, data.get("unidad", "unidad"))
        if duplicado:
            sucursal_dup = duplicado["sucursal_nombre"] or "sin sucursal"
            conn.close()
            return err(
                f"Ya existe '{duplicado['nombre']}' ({duplicado['codigo']}) en "
                f"{sucursal_dup}. Usa la misma ficha para mantener un solo producto.",
                409)
        para_proveer = _para_proveer(data)
        if para_proveer and not _sucursal_puede_proveer(conn, sid):
            conn.close()
            return err("Solo los almacenes proveedores pueden ofrecer productos en pedidos", 400)
        if data.get("almacen_id"):
            # Cada sucursal es un almacén independiente: el almacén debe ser de
            # la sucursal elegida, sin ascender a ninguna tienda madre.
            sc = list(ids_sucursal_consolidada(conn, sid))
            if not conn.execute(
                    "SELECT 1 FROM almacenes WHERE id = ? AND sucursal_id IN ("
                    + ",".join(["?"] * len(sc)) + ")",
                    (int(data["almacen_id"]), *sc)).fetchone():
                conn.close()
                return err("El almacén no pertenece a esa sucursal", 400)
        # Modelo actual: cada sucursal es un almacén independiente. El producto
        # vive en la sucursal que lo crea (sin moverlo a ninguna "AS").
        cur = conn.execute("""
            INSERT INTO productos (codigo, nombre, marca, categoria_id, unidad, stock_minimo, costo_promedio,
                                   precio_venta, vencimiento, almacen_id, proveedor_id, sucursal_id, unidad_tacho, pide_tacho, para_proveer, activo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, 1)
        """, (codigo, data["nombre"].strip(), (data.get("marca") or "").strip() or None,
              data.get("categoria_id"),
              data.get("unidad", "unidad"), data.get("stock_minimo", 0) or 0,
              data.get("costo_promedio", 0) or 0, data.get("precio_venta", 0) or 0,
              data.get("almacen_id"), proveedor_id, sid, _tacho_unidad(data), _pide_tacho(data),
              para_proveer))
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
    join_own_sucursal = False
    if request.args.get("stock_sucursal", "").strip():
        stock_sid, e = _stock_sucursal_permitido(conn, ver_todo, sid)
        if e:
            conn.close()
            return e
        ids_stock = [int(x) for x in ids_sucursal_consolidada(conn, int(stock_sid))]
        ph_in = ",".join(str(x) for x in ids_stock)
        join_stock = ("LEFT JOIN (SELECT producto_id, SUM(cantidad) AS cantidad "
                      "FROM lotes WHERE sucursal_id IN (" + ph_in + ") GROUP BY producto_id) s ON s.producto_id = p.id")
        lote_cond = " AND l2.sucursal_id IN (" + ph_in + ")"
    elif sucursal is not None:
        # Pestaña/sucursal elegida: el stock de cada producto es el de SU PROPIA
        # sucursal registrada (la papa que pasó a proveer vive en el AS y se ve
        # con lo que hay ahí; del otro lado, la tienda muestra lo suyo).
        join_own_sucursal = True
    elif not ver_todo and sid is not None:
        # Filial sin pestaña: cada producto muestra el stock del almacén donde
        # vive (producción en la AS, venta en la tienda). Contar SOLO los lotes
        # de la propia tienda dejaba en 0 los productos que pasaron al AS (todo
        # el inventario de América vive en su AS de producción), y sumar los
        # lotes de la tienda + el AS del MISMO artículo producía el "ciclo"
        # (45 -> 45): al pasar 15 de producción a la venta, el total no cambiaba
        # cuando en realidad la AS baja y la venta sube.
        join_own_sucursal = True
    if join_own_sucursal:
        join_stock = ("LEFT JOIN (SELECT l.producto_id, SUM(l.cantidad) AS cantidad "
                      "FROM lotes l JOIN productos pr2 ON pr2.id = l.producto_id "
                      "WHERE l.cantidad > 0 AND l.sucursal_id = pr2.sucursal_id "
                      "GROUP BY l.producto_id) s ON s.producto_id = p.id")
        lote_cond = " AND l2.sucursal_id = p.sucursal_id"
    elif stock_sid is None:
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
    # El pedido solo ofrece productos que su almacén proveedor habilitó
    # explícitamente. Los productos sin sucursal asignada siguen en inventario,
    # pero no se pueden ofrecer porque no tienen un origen de despacho claro.
    if para_pedido:
        q += (" AND IFNULL(p.para_proveer, 1) = 1 "
              "AND p.sucursal_id IS NOT NULL "
              "AND EXISTS (SELECT 1 FROM sucursales sp "
              "WHERE sp.id = p.sucursal_id "
              "AND (sp.principal = 1 OR sp.provee = 1 OR sp.es_as = 1 "
              "OR EXISTS (SELECT 1 FROM usuarios u "
              "WHERE u.sucursal_id = sp.id AND IFNULL(u.receptor, 0) = 1) "
              "OR EXISTS (SELECT 1 FROM almacenes a "
              "WHERE a.sucursal_id = sp.id AND a.nombre LIKE 'AS %')))")
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
              "    (IFNULL(p.para_proveer, 1) = 1 AND EXISTS ("
              "        SELECT 1 FROM sucursales as_ "
              "        WHERE as_.id = p.sucursal_id AND as_.es_as = 1))"
              "    OR NOT EXISTS ("
              "        SELECT 1 FROM productos p2"
              "        JOIN sucursales as_ ON as_.id = p2.sucursal_id"
              "        WHERE p2.activo = 1 AND IFNULL(p2.para_proveer, 1) = 1"
              "          AND as_.es_as = 1 "
              "          AND p2.id <> p.id AND " + ciudad_ok + " AND ("
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
    nombre = (data.get("nombre") or "").strip()
    unidad = data.get("unidad", "unidad")
    if not nombre:
        conn.close()
        return err("El nombre del producto es obligatorio", 400)
    duplicado = _producto_duplicado_edicion(conn, nombre, unidad, fila)
    if duplicado:
        sucursal_dup = duplicado["sucursal_nombre"] or "sin sucursal"
        conn.close()
        return err(
            f"Ya existe '{duplicado['nombre']}' ({duplicado['codigo']}) en "
            f"{sucursal_dup}. Usa la misma ficha para mantener un solo producto.",
            409)
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
    para_final = (_para_proveer(data) if data.get("para_proveer") is not None
                  else int(fila.get("para_proveer") or 1))
    if para_final and not _sucursal_puede_proveer(conn, sid_p):
        conn.close()
        return err("Solo los almacenes proveedores pueden ofrecer productos en pedidos", 400)
    if data.get("almacen_id"):
        # Cada sucursal es un almacén independiente: el almacén debe ser de la
        # sucursal del producto, sin ascender a ninguna tienda madre.
        sc = list(ids_sucursal_consolidada(conn, sid_p))
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
    fila = conn.execute(
        "SELECT id, nombre, unidad FROM productos WHERE id = ?", (prod_id,)).fetchone()
    if not fila:
        conn.close()
        return err("Producto no encontrado", 404)
    duplicado = _producto_duplicado(conn, fila["nombre"], fila["unidad"], excluir_id=prod_id)
    if duplicado:
        conn.close()
        return err(
            f"No se puede restaurar: ya existe '{duplicado['nombre']}' "
            f"({duplicado['codigo']}) en {duplicado['sucursal_nombre'] or 'otra sucursal'}.",
            409)
    conn.execute("UPDATE productos SET activo = 1 WHERE id = ?", (prod_id,))
    conn.commit()
    conn.close()
    registrar_auditoria("Producto restaurado", f"Producto ID {prod_id}")
    return ok(message="Producto restaurado")


@productos_bp.route("/api/productos/duplicados", methods=["GET", "POST"])
@login_requerido
def productos_duplicados():
    if not es_gestion():
        return err("Solo administradores pueden revisar fichas duplicadas", 403)

    conn = get_conn()
    if request.method == "GET":
        productos = conn.execute("""
            SELECT p.id, p.codigo, p.nombre, p.marca, p.unidad, p.sucursal_id,
                   p.categoria_id, p.costo_promedio, p.precio_venta, p.para_proveer,
                   s.nombre AS sucursal_nombre, c.nombre AS categoria_nombre
            FROM productos p
            LEFT JOIN sucursales s ON s.id = p.sucursal_id
            LEFT JOIN categorias c ON c.id = p.categoria_id
            WHERE p.activo = 1
            ORDER BY p.nombre, p.id
        """).fetchall()
        grupos = {}
        for fila in productos:
            clave = (norm_nombre(fila["nombre"]), norm_nombre(fila["unidad"] or "unidad"))
            grupos.setdefault(clave, []).append(dict(fila))
        grupos = [grupo for grupo in grupos.values() if len(grupo) > 1]
        ids = sorted({int(p["id"]) for grupo in grupos for p in grupo})
        if not ids:
            conn.close()
            return ok({"grupos": []})

        por_id = {int(p["id"]): p for grupo in grupos for p in grupo}
        for pid in ids:
            por_id[pid]["stocks"] = []
            por_id[pid]["referencias"] = {
                "movimientos": 0, "pedidos": 0, "pedidos_abiertos": 0,
                "repartos": 0, "ventas": 0, "planillas_abiertas": 0,
                "planillas_cerradas": 0,
            }
        for chunk in _chunks(ids):
            ph = ",".join(["?"] * len(chunk))
            for fila in conn.execute(f"""
                SELECT l.producto_id, l.sucursal_id, s.nombre AS sucursal_nombre,
                       SUM(l.cantidad) AS cantidad
                FROM lotes l JOIN sucursales s ON s.id = l.sucursal_id
                WHERE l.producto_id IN ({ph})
                GROUP BY l.producto_id, l.sucursal_id, s.nombre
                HAVING SUM(l.cantidad) <> 0
            """, chunk).fetchall():
                por_id[int(fila["producto_id"])]["stocks"].append({
                    "sucursal_id": fila["sucursal_id"],
                    "sucursal_nombre": fila["sucursal_nombre"],
                    "cantidad": fila["cantidad"],
                })
            referencias = (
                ("movimientos", "SELECT producto_id, COUNT(*) AS cantidad "
                 "FROM movimientos WHERE producto_id IN ({ph}) GROUP BY producto_id"),
                ("repartos", "SELECT producto_id, COUNT(*) AS cantidad "
                 "FROM reparto_detalle WHERE producto_id IN ({ph}) GROUP BY producto_id"),
                ("ventas", "SELECT producto_id, COUNT(*) AS cantidad "
                 "FROM venta_detalle WHERE producto_id IN ({ph}) GROUP BY producto_id"),
            )
            for campo, sql in referencias:
                for fila in conn.execute(sql.format(ph=ph), chunk).fetchall():
                    por_id[int(fila["producto_id"])]["referencias"][campo] = fila["cantidad"]
            for fila in conn.execute(f"""
                SELECT d.producto_id, COUNT(*) AS cantidad,
                       COUNT(DISTINCT CASE
                           WHEN p.estado NOT IN ('entregado', 'rechazado', 'cancelado')
                           THEN p.id END) AS abiertos
                FROM pedido_detalle d JOIN pedidos p ON p.id = d.pedido_id
                WHERE d.producto_id IN ({ph})
                GROUP BY d.producto_id
            """, chunk).fetchall():
                refs = por_id[int(fila["producto_id"])]["referencias"]
                refs["pedidos"] = fila["cantidad"]
                refs["pedidos_abiertos"] = fila["abiertos"] or 0

        inventarios_por_producto = {pid: [] for pid in ids}
        for chunk in _chunks(ids):
            ph = ",".join(["?"] * len(chunk))
            for fila in conn.execute(f"""
                SELECT d.producto_id, d.inventario_id, i.sucursal_id,
                       s.nombre AS sucursal_nombre, i.fecha, i.estado,
                       d.conteo_fisico, d.final
                FROM inventario_detalle d
                JOIN inventario_diario i ON i.id = d.inventario_id
                JOIN sucursales s ON s.id = i.sucursal_id
                WHERE d.producto_id IN ({ph})
            """, chunk).fetchall():
                inventarios_por_producto[int(fila["producto_id"])].append(dict(fila))

        salida = []
        for grupo in grupos:
            por_inventario = {}
            for producto in grupo:
                pid = int(producto["id"])
                producto["referencias"]["planillas_abiertas"] = sum(
                    1 for i in inventarios_por_producto[pid] if i["estado"] == "abierto")
                producto["referencias"]["planillas_cerradas"] = sum(
                    1 for i in inventarios_por_producto[pid] if i["estado"] == "cerrado")
                for detalle in inventarios_por_producto[pid]:
                    por_inventario.setdefault(int(detalle["inventario_id"]), []).append({
                        "producto_id": pid,
                        "estado": detalle["estado"],
                        "fecha": str(detalle["fecha"]),
                        "sucursal_nombre": detalle["sucursal_nombre"],
                    })
            colisiones = [
                {"estado": items[0]["estado"], "fecha": items[0]["fecha"],
                 "sucursal_nombre": items[0]["sucursal_nombre"],
                 "productos": [item["producto_id"] for item in items]}
                for items in por_inventario.values() if len(items) > 1
            ]
            proveedores = sorted({
                int(p["sucursal_id"]) for p in grupo
                if p["para_proveer"] and p["sucursal_id"]
            })
            salida.append({
                "productos": grupo,
                "proveedores_distintos": len(proveedores),
                "colisiones_planilla": colisiones,
                "categorias_distintas": len({norm_nombre(p["categoria_nombre"])
                                             for p in grupo}),
                "marcas_distintas": len({norm_nombre(p["marca"]) for p in grupo}),
                "costos_distintos": len({round(float(p["costo_promedio"] or 0), 4)
                                         for p in grupo}),
            })
        conn.close()
        return ok({"grupos": salida})

    data = request.get_json() or {}
    if data.get("confirmar") is not True:
        conn.close()
        return err("Confirma explícitamente la unificación de las fichas elegidas", 400)
    if not isinstance(data.get("duplicados"), list):
        conn.close()
        return err("La lista de fichas duplicadas no es válida", 400)
    try:
        canonico_id = int(data.get("canonico_id"))
        duplicados = list(dict.fromkeys(int(i) for i in data["duplicados"]))
    except (TypeError, ValueError):
        conn.close()
        return err("Selecciona una ficha principal y las fichas que se unificarán", 400)
    if not duplicados or canonico_id in duplicados:
        conn.close()
        return err("La ficha principal debe ser distinta de las fichas duplicadas", 400)

    ids = [canonico_id, *duplicados]
    placeholders = ",".join(["?"] * len(ids))
    respaldo = nombre_respaldo("antes_unificar")
    try:
        filas = conn.execute(f"""
            SELECT p.id, p.codigo, p.nombre, p.unidad, p.sucursal_id, p.categoria_id,
                   p.para_proveer
            FROM productos p WHERE p.activo = 1 AND p.id IN ({placeholders}) FOR UPDATE
        """, ids).fetchall()
        por_id = {int(f["id"]): f for f in filas}
        if len(por_id) != len(ids):
            raise ValueError("Una o más fichas ya no existen o están inactivas.")
        canonico = por_id[canonico_id]
        clave = (norm_nombre(canonico["nombre"]),
                 norm_nombre(canonico["unidad"] or "unidad"))
        if any((norm_nombre(por_id[i]["nombre"]),
                norm_nombre(por_id[i]["unidad"] or "unidad")) != clave
               for i in duplicados):
            raise ValueError("Solo se pueden unificar productos con mismo nombre y unidad.")
        proveedores = {
            int(p["sucursal_id"]) for p in filas
            if p["para_proveer"] and p["sucursal_id"]
        }
        if len(proveedores) > 1:
            raise ValueError(
                "Las fichas abastecen desde distintos AS. No se pueden unificar hasta "
                "definir un producto con varios proveedores.")
        if proveedores and (
                not canonico["para_proveer"]
                or int(canonico["sucursal_id"] or 0) not in proveedores):
            raise ValueError(
                "Conserva como principal la ficha del AS proveedor para no cambiar "
                "el origen de los pedidos.")

        pedidos_abiertos = conn.execute(f"""
            SELECT p.id
            FROM pedido_detalle d JOIN pedidos p ON p.id = d.pedido_id
            WHERE d.producto_id IN ({placeholders})
              AND p.estado NOT IN ('entregado', 'rechazado', 'cancelado')
            FOR UPDATE
        """, ids).fetchall()
        pedidos_abiertos_ids = {int(f["id"]) for f in pedidos_abiertos}
        if pedidos_abiertos_ids:
            raise ValueError(
                f"Hay {len(pedidos_abiertos_ids)} pedido(s) todavía abiertos con estas fichas. "
                "Espera a que terminen antes de unificar.")

        colisiones_cerradas = conn.execute(f"""
            SELECT i.fecha, s.nombre AS sucursal_nombre
            FROM inventario_detalle d
            JOIN inventario_diario i ON i.id = d.inventario_id
            JOIN sucursales s ON s.id = i.sucursal_id
            WHERE d.producto_id IN ({placeholders}) AND i.estado = 'cerrado'
            GROUP BY d.inventario_id, i.fecha, s.nombre
            HAVING COUNT(DISTINCT d.producto_id) > 1
        """, ids).fetchall()
        if colisiones_cerradas:
            detalles = ", ".join(
                f"{r['sucursal_nombre']} {r['fecha']}" for r in colisiones_cerradas[:5])
            raise ValueError(
                "Hay planillas cerradas con varias de estas fichas; se conservan intactas. "
                f"Revísalas antes de unificar: {detalles}.")

        volcar_a_archivo(conn, os.path.join(carpeta_respaldos(), respaldo))

        cat = conn.execute(
            "SELECT nombre FROM categorias WHERE id = ?", (canonico["categoria_id"],)
        ).fetchone() if canonico["categoria_id"] else None
        nombre_categoria = cat["nombre"] if cat else None
        inventarios_impactados = set()
        columnas = ("stock_sistema", "inicial", "ingreso_dia", "ingreso_manual",
                    "disponible", "conteo_fisico", "final", "utilizada", "diferencia")
        for antiguo_id in duplicados:
            detalles = conn.execute(
                "SELECT d.*, i.estado FROM inventario_detalle d "
                "JOIN inventario_diario i ON i.id = d.inventario_id "
                "WHERE d.producto_id = ? FOR UPDATE", (antiguo_id,)).fetchall()
            for detalle in detalles:
                inventario_id = int(detalle["inventario_id"])
                destino = conn.execute(
                    "SELECT * FROM inventario_detalle "
                    "WHERE inventario_id = ? AND producto_id = ? FOR UPDATE",
                    (inventario_id, canonico_id)).fetchone()
                if not destino:
                    if detalle["estado"] == "abierto":
                        conn.execute("""
                            UPDATE inventario_detalle
                            SET producto_id = ?, categoria_id = ?, categoria_nombre = ?,
                                producto_nombre = ?, codigo = ?, unidad = ?
                            WHERE id = ?
                        """, (canonico_id, canonico["categoria_id"], nombre_categoria,
                              canonico["nombre"], canonico["codigo"], canonico["unidad"],
                              detalle["id"]))
                    else:
                        conn.execute(
                            "UPDATE inventario_detalle SET producto_id = ? WHERE id = ?",
                            (canonico_id, detalle["id"]))
                    continue
                if detalle["estado"] != "abierto":
                    raise ValueError(
                        "No se modifica automáticamente una planilla cerrada con fichas repetidas.")
                valores = _combinar_detalles_inventario(destino, detalle)
                conn.execute("""
                    UPDATE inventario_detalle
                    SET stock_sistema = ?, inicial = ?, ingreso_dia = ?, ingreso_manual = ?,
                        disponible = ?, conteo_fisico = ?, final = ?, utilizada = ?,
                        diferencia = ?, observaciones = ?, categoria_id = ?,
                        categoria_nombre = ?, producto_nombre = ?, codigo = ?, unidad = ?
                    WHERE id = ?
                """, tuple(valores[c] for c in columnas)
                     + (valores["observaciones"], canonico["categoria_id"], nombre_categoria,
                        canonico["nombre"], canonico["codigo"], canonico["unidad"],
                        destino["id"]))
                conn.execute(
                    "UPDATE inventario_ingresos_manuales SET detalle_id = ? WHERE detalle_id = ?",
                    (destino["id"], detalle["id"]))
                conn.execute("DELETE FROM inventario_detalle WHERE id = ?", (detalle["id"],))
                inventarios_impactados.add(inventario_id)

        for antiguo_id in duplicados:
            for stock in conn.execute(
                    "SELECT sucursal_id, cantidad FROM stock WHERE producto_id = ?",
                    (antiguo_id,)).fetchall():
                conn.execute("""
                    INSERT INTO stock (producto_id, sucursal_id, cantidad) VALUES (?, ?, ?)
                    ON DUPLICATE KEY UPDATE cantidad = cantidad + VALUES(cantidad)
                """, (canonico_id, stock["sucursal_id"], stock["cantidad"]))
            conn.execute("DELETE FROM stock WHERE producto_id = ?", (antiguo_id,))
            for tabla in ("lotes", "movimientos", "pedido_detalle",
                          "reparto_detalle", "venta_detalle"):
                conn.execute(f"UPDATE {tabla} SET producto_id = ? WHERE producto_id = ?",
                             (canonico_id, antiguo_id))
            conn.execute(
                "UPDATE productos SET activo = 0, para_proveer = 0 WHERE id = ?",
                (antiguo_id,))

        for inventario_id in inventarios_impactados:
            conn.execute("""
                UPDATE inventario_diario
                SET total_items = (SELECT COUNT(*) FROM inventario_detalle WHERE inventario_id = ?)
                WHERE id = ? AND estado = 'abierto'
            """, (inventario_id, inventario_id))
        conn.commit()
    except ValueError as e:
        conn.rollback()
        conn.close()
        return err(str(e), 409)
    except Exception:
        conn.rollback()
        conn.close()
        logging.getLogger("app").exception(
            "Error unificando fichas de productos: principal=%s duplicados=%s",
            canonico_id, duplicados)
        return err("No se pudieron unificar las fichas; no se aplicó ningún cambio", 500)

    conn.close()
    registrar_auditoria(
        "Productos unificados",
        f"Producto principal {canonico_id}; fichas {', '.join(map(str, duplicados))}; "
        f"respaldo {respaldo}")
    return ok({"canonico_id": canonico_id, "unificados": duplicados,
               "respaldo": respaldo},
              message=f"Fichas unificadas; respaldo creado: {respaldo}")


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


@productos_bp.route("/api/equivalencias-productos", methods=["GET", "POST"])
@login_requerido
def equivalencias_productos():
    conn = get_conn()
    puede_gestionar_todos = es_gestion() or es_encargado_almacen(conn)
    es_as_limitado = es_receptor() and not puede_gestionar_todos
    sucursal_as = sucursal_actual() if es_as_limitado else None
    if not (puede_gestionar_todos or (es_as_limitado and sucursal_as)):
        conn.close()
        return err(
            "Solo administración, Almacenes Principales y usuarios AS "
            "autorizados pueden confirmar equivalencias", 403)

    if request.method == "GET":
        filtro_as = " WHERE origen.sucursal_id = ?" if es_as_limitado else ""
        parametros_as = (sucursal_as,) if es_as_limitado else ()
        equivalencias = conn.execute("""
            SELECT e.id, e.producto_origen_id, e.sucursal_destino_id,
                   e.producto_destino_id, origen.nombre AS origen_nombre,
                   origen.unidad AS origen_unidad,
                   origen_categoria.nombre AS origen_categoria_nombre,
                   origen_sucursal.nombre AS origen_sucursal_nombre,
                   destino.nombre AS destino_nombre,
                   destino.unidad AS destino_unidad,
                   destino_categoria.nombre AS destino_categoria_nombre,
                   destino_sucursal.nombre AS destino_sucursal_nombre
            FROM equivalencias_productos e
            JOIN productos origen ON origen.id = e.producto_origen_id
            LEFT JOIN categorias origen_categoria ON origen_categoria.id = origen.categoria_id
            LEFT JOIN sucursales origen_sucursal ON origen_sucursal.id = origen.sucursal_id
            JOIN productos destino ON destino.id = e.producto_destino_id
            LEFT JOIN categorias destino_categoria ON destino_categoria.id = destino.categoria_id
            JOIN sucursales destino_sucursal ON destino_sucursal.id = e.sucursal_destino_id
        """ + filtro_as + """
            ORDER BY origen_sucursal.nombre, origen.nombre, destino_sucursal.nombre
        """, parametros_as).fetchall()
        filtro_productos_as = """
            AND (
                p.sucursal_id = ?
                OR EXISTS (
                    SELECT 1
                    FROM pedidos pedido
                    JOIN pedido_detalle detalle ON detalle.pedido_id = pedido.id
                    WHERE pedido.sucursal_id = p.sucursal_id
                      AND COALESCE(detalle.destino_id, pedido.destino_id) = ?
                      AND detalle.producto_id = p.id
                      AND pedido.estado IN ('pendiente', 'en_camino')
                      AND pedido.sucursal_id <> ?
                )
            )
        """ if es_as_limitado else ""
        parametros_productos_as = (
            (sucursal_as, sucursal_as, sucursal_as) if es_as_limitado else ())
        productos = conn.execute("""
            SELECT p.id, p.nombre, p.unidad, p.sucursal_id, p.categoria_id,
                   c.nombre AS categoria_nombre,
                   COALESCE(s.nombre, 'Sin sucursal') AS sucursal_nombre
            FROM productos p
            LEFT JOIN categorias c ON c.id = p.categoria_id
            LEFT JOIN sucursales s ON s.id = p.sucursal_id
            WHERE p.activo = 1
        """ + filtro_productos_as + """
            ORDER BY s.nombre, p.nombre
        """, parametros_productos_as).fetchall()
        if es_as_limitado:
            sucursales = conn.execute("""
                SELECT DISTINCT s.id, s.nombre
                FROM sucursales s
                JOIN pedidos pedido ON pedido.sucursal_id = s.id
                JOIN pedido_detalle detalle ON detalle.pedido_id = pedido.id
                WHERE COALESCE(detalle.destino_id, pedido.destino_id) = ?
                  AND detalle.producto_id IN (
                      SELECT id FROM productos WHERE sucursal_id = ? AND activo = 1
                  )
                  AND pedido.estado IN ('pendiente', 'en_camino')
                  AND pedido.sucursal_id <> ?
                ORDER BY s.nombre
            """, (sucursal_as, sucursal_as, sucursal_as)).fetchall()
        else:
            sucursales = conn.execute(
                "SELECT id, nombre FROM sucursales ORDER BY nombre").fetchall()
        conn.close()
        return ok({
            "equivalencias": [dict(fila) for fila in equivalencias],
            "productos": [dict(fila) for fila in productos],
            "sucursales": [dict(fila) for fila in sucursales],
        })

    data = request.get_json() or {}
    try:
        producto_origen_id = int(data.get("producto_origen_id"))
        sucursal_destino_id = int(data.get("sucursal_destino_id"))
        producto_destino_id = int(data.get("producto_destino_id"))
    except (TypeError, ValueError):
        conn.close()
        return err("Selecciona los productos y la sucursal receptora", 400)

    origen = conn.execute(
        "SELECT id, unidad, sucursal_id FROM productos WHERE id = ? AND activo = 1",
        (producto_origen_id,)).fetchone()
    destino = conn.execute(
        "SELECT id, sucursal_id, unidad FROM productos WHERE id = ? AND activo = 1",
        (producto_destino_id,)).fetchone()
    sucursal = conn.execute(
        "SELECT id FROM sucursales WHERE id = ?", (sucursal_destino_id,)).fetchone()
    if not origen or not destino or not sucursal:
        conn.close()
        return err("El producto o la sucursal seleccionada no existe o está inactiva", 404)
    if es_as_limitado:
        if origen["sucursal_id"] != sucursal_as:
            conn.close()
            return err("Solo puedes revisar productos de tu propio almacén AS", 403)
        pedido_abierto = conn.execute("""
            SELECT 1
            FROM pedidos p
            JOIN pedido_detalle d ON d.pedido_id = p.id
            WHERE p.sucursal_id = ?
              AND COALESCE(d.destino_id, p.destino_id) = ?
              AND d.producto_id = ?
              AND p.estado IN ('pendiente', 'en_camino')
            LIMIT 1
        """, (sucursal_destino_id, sucursal_as, producto_origen_id)).fetchone()
        if not pedido_abierto:
            conn.close()
            return err(
                "Solo puedes confirmar fichas para pedidos abiertos de tu almacén AS",
                403)
    if destino["sucursal_id"] != sucursal_destino_id:
        conn.close()
        return err("El producto receptor debe pertenecer a la sucursal seleccionada", 400)
    unidad_origen = (origen["unidad"] or "unidad").strip().casefold()
    unidad_destino = (destino["unidad"] or "unidad").strip().casefold()
    if unidad_origen != unidad_destino:
        conn.close()
        return err("Las unidades de los productos equivalentes no coinciden", 400)

    conn.execute("""
        INSERT INTO equivalencias_productos
            (producto_origen_id, sucursal_destino_id, producto_destino_id, creado_por, creado_en)
        VALUES (?, ?, ?, ?, ?)
        ON DUPLICATE KEY UPDATE producto_destino_id = VALUES(producto_destino_id),
                                creado_por = VALUES(creado_por), creado_en = VALUES(creado_en)
    """, (producto_origen_id, sucursal_destino_id, producto_destino_id,
          session.get("usuario", ""), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    registrar_auditoria(
        "Equivalencia de producto configurada",
        f"{producto_origen_id} -> {producto_destino_id} en sucursal {sucursal_destino_id}")
    return ok(message="Equivalencia guardada")


@productos_bp.route("/api/equivalencias-productos/crear-ficha", methods=["POST"])
@login_requerido
def equivalencia_producto_crear_ficha():
    """Crea una ficha receptora sin stock y la vincula en una sola transacción."""
    conn = get_conn()
    puede_gestionar_todos = es_gestion() or es_encargado_almacen(conn)
    es_as_limitado = es_receptor() and not puede_gestionar_todos
    sucursal_as = sucursal_actual() if es_as_limitado else None
    if not (puede_gestionar_todos or (es_as_limitado and sucursal_as)):
        conn.close()
        return err(
            "Solo administración, Almacenes Principales y usuarios AS "
            "autorizados pueden crear fichas receptoras", 403)

    data = request.get_json() or {}
    try:
        producto_origen_id = int(data.get("producto_origen_id"))
        sucursal_destino_id = int(data.get("sucursal_destino_id"))
        categoria = data.get("categoria_id")
        if categoria in (None, ""):
            raise ValueError
        categoria_id = None if categoria == "sin" else int(categoria)
    except (TypeError, ValueError):
        conn.close()
        return err("Selecciona un producto, una sucursal y una categoría válida", 400)

    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        conn.close()
        return err("El nombre del producto es obligatorio", 400)
    if data.get("confirmar_no_existe") is not True:
        conn.close()
        return err(
            "Confirma que revisaste las fichas sugeridas y que el producto no existe "
            "en la sucursal receptora", 400)

    # Bloquea la sucursal mientras se revisa la duplicidad para evitar que dos
    # solicitudes simultáneas creen la misma ficha receptora.
    sucursal = conn.execute(
        "SELECT id, nombre FROM sucursales WHERE id = ? FOR UPDATE",
        (sucursal_destino_id,)).fetchone()
    origen = conn.execute("""
        SELECT id, nombre, unidad, categoria_id, marca, costo_promedio,
               sucursal_id, precio_venta, unidad_tacho, pide_tacho
        FROM productos WHERE id = ? AND activo = 1
    """, (producto_origen_id,)).fetchone()
    if not sucursal or not origen:
        conn.close()
        return err("El producto o la sucursal seleccionada no existe o está inactiva", 404)
    if es_as_limitado and origen["sucursal_id"] != sucursal_as:
        conn.close()
        return err("Solo puedes resolver pedidos de productos de tu propio almacén AS", 403)

    filtro_proveedor_as = (
        " AND COALESCE(d.destino_id, p.destino_id) = ?"
        if es_as_limitado else "")
    parametros_solicitud = (sucursal_destino_id, producto_origen_id)
    if es_as_limitado:
        parametros_solicitud += (sucursal_as,)
    solicitud = conn.execute("""
        SELECT 1
        FROM pedidos p
        JOIN pedido_detalle d ON d.pedido_id = p.id
        WHERE p.sucursal_id = ?
          AND COALESCE(d.destino_id, p.destino_id) <> p.sucursal_id
          AND d.producto_id = ?
          AND p.estado IN ('pendiente', 'en_camino')
    """ + filtro_proveedor_as + """
        LIMIT 1
    """, parametros_solicitud).fetchone()
    if not solicitud:
        conn.close()
        return err("Este producto no tiene un pedido abierto para esa sucursal", 409)

    equivalencia = conn.execute("""
        SELECT producto_destino_id
        FROM equivalencias_productos
        WHERE producto_origen_id = ? AND sucursal_destino_id = ?
        FOR UPDATE
    """, (producto_origen_id, sucursal_destino_id)).fetchone()
    if equivalencia:
        conn.close()
        return err("Este producto ya tiene una equivalencia. Recarga la revisión del pedido.", 409)

    existentes = conn.execute("""
        SELECT id, nombre, unidad
        FROM productos
        WHERE sucursal_id = ? AND activo = 1
    """, (sucursal_destino_id,)).fetchall()
    nombre_normalizado = norm_nombre(nombre)
    nombre_tokens = _norm_nombre_tokens(nombre)
    unidad_normalizada = norm_nombre(origen["unidad"] or "unidad")
    existente = next((producto for producto in existentes
                      if (norm_nombre(producto["nombre"]) == nombre_normalizado
                          or _norm_nombre_tokens(producto["nombre"]) == nombre_tokens)
                      and norm_nombre(producto["unidad"] or "unidad")
                      == unidad_normalizada), None)
    if existente:
        conn.close()
        return err(
            f"Ya existe la ficha '{existente['nombre']}' en esa sucursal. "
            "Selecciona esa ficha y confirma la equivalencia en vez de crear otra.",
            409)

    if categoria_id is not None and not conn.execute(
            "SELECT id FROM categorias WHERE id = ?", (categoria_id,)).fetchone():
        conn.close()
        return err("La categoría seleccionada no existe", 400)

    almacenes = conn.execute(
        "SELECT id, nombre FROM almacenes WHERE sucursal_id = ? ORDER BY id",
        (sucursal_destino_id,)).fetchall()
    nombre_sucursal = norm_nombre(sucursal["nombre"])
    almacen = next(
        (fila for fila in almacenes
         if norm_nombre(fila["nombre"]) == nombre_sucursal), None)
    if not almacen:
        almacen = next(
            (fila for fila in almacenes
             if norm_nombre(fila["nombre"]) == "almacenprincipal"), None)
    if not almacen and almacenes:
        almacen = almacenes[0]
    try:
        codigo = siguiente_codigo(conn)
        cur = conn.execute("""
            INSERT INTO productos (
                codigo, nombre, marca, categoria_id, unidad, stock_minimo,
                costo_promedio, precio_venta, vencimiento, almacen_id,
                proveedor_id, sucursal_id, unidad_tacho, pide_tacho,
                para_proveer, activo
            )
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, NULL, ?, NULL, ?, ?, ?, 0, 1)
        """, (codigo, nombre, origen.get("marca"), categoria_id,
              origen.get("unidad") or "unidad",
              origen.get("costo_promedio") or 0,
              origen.get("precio_venta") or 0,
              (almacen or {}).get("id"), sucursal_destino_id,
              origen.get("unidad_tacho") or 0,
              origen.get("pide_tacho") or 0))
        producto_destino_id = cur.lastrowid
        conn.execute("""
            INSERT INTO equivalencias_productos
                (producto_origen_id, sucursal_destino_id, producto_destino_id,
                 creado_por, creado_en)
            VALUES (?, ?, ?, ?, ?)
        """, (producto_origen_id, sucursal_destino_id, producto_destino_id,
              session.get("usuario", ""),
              datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    except Exception:
        conn.rollback()
        logging.exception(
            "No se pudo crear y vincular la ficha receptora del producto %s "
            "en la sucursal %s", producto_origen_id, sucursal_destino_id)
        conn.close()
        return err("No se pudo crear la ficha ni guardar la equivalencia", 500)

    conn.close()
    registrar_auditoria(
        "Ficha receptora creada y vinculada",
        f"{producto_origen_id} -> {producto_destino_id} "
        f"en sucursal {sucursal_destino_id}; stock inicial 0")
    return ok({"producto_destino_id": producto_destino_id, "codigo": codigo},
              message="Ficha creada con stock 0 y equivalencia confirmada")


@productos_bp.route("/api/equivalencias-productos/<int:equivalencia_id>", methods=["DELETE"])
@login_requerido
def equivalencia_producto_eliminar(equivalencia_id):
    if not es_superadmin():
        return err("Solo el superadministrador puede gestionar equivalencias", 403)
    conn = get_conn()
    equivalencia = conn.execute(
        "SELECT producto_origen_id, sucursal_destino_id, producto_destino_id "
        "FROM equivalencias_productos WHERE id = ?", (equivalencia_id,)).fetchone()
    if not equivalencia:
        conn.close()
        return err("Equivalencia no encontrada", 404)
    conn.execute("DELETE FROM equivalencias_productos WHERE id = ?", (equivalencia_id,))
    conn.commit()
    conn.close()
    registrar_auditoria(
        "Equivalencia de producto eliminada",
        f"{equivalencia['producto_origen_id']} -> {equivalencia['producto_destino_id']} "
        f"en sucursal {equivalencia['sucursal_destino_id']}")
    return ok(message="Equivalencia eliminada")


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
                duplicado = _producto_duplicado(conn, nombre, unidad)
                if duplicado:
                    raise ValueError(
                        f"ya existe '{duplicado['nombre']}' ({duplicado['codigo']}) "
                        f"en {duplicado['sucursal_nombre'] or 'otra sucursal'}")
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
                                           sucursal_id, para_proveer, activo)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, 1)
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
