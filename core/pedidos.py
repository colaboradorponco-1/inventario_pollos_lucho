"""Pedidos / tickets por sucursal (Fase 2).

Cada sucursal registra un pedido (ticket) con todo lo que necesita; cada
producto se pide al almacén/sucursal que lo provee (puede haber varios
proveedores en un mismo pedido). Se genera UN ticket imprimible agrupado
por proveedor y por categoría.

Estados unificados para todos los roles: pendiente -> en_camino -> entregado,
más rechazado (solo desde pendiente). El stock se mueve una sola vez, al
marcar 'entregado'. Cambiar el estado o despachar puede hacerlo un
admin/superadmin o un encargado de almacén principal.
"""
from collections import OrderedDict
from datetime import datetime

from flask import Blueprint, redirect, render_template, request, session

from database import get_conn
from .lotes import (destinos_validos, elegir_destino, stock_en_destino,
                   stock_por_destino, _ids_proveedores_misma_ciudad)
from .util import (ok, err, login_requerido, registrar_auditoria, ok_paginado,
                   paginar_params, sucursal_actual, sucursal_operativa, stock_actual,
                   es_gestion, es_superadmin, es_encargado_almacen, registrar_movimiento,
                   es_logistica, es_receptor)

pedidos_bp = Blueprint("pedidos", __name__)

# Estados unificados para TODOS los roles: pendiente -> en_camino -> entregado,
# más rechazado (solo desde pendiente). El stock se mueve UNA sola vez, al
# marcar 'entregado' (baja del almacén que despachó y suma a la sucursal que
# recibió). 'en_camino' y 'rechazado' no tocan el inventario.
_ESTADOS = ("pendiente", "en_camino", "entregado", "rechazado")

# Transiciones válidas. No se puede volver a 'pendiente' ni revivir un pedido
# ya entregado/rechazado: el stock ya salió (entregado) o la operación se
# cerró (rechazado). 'rechazado' nace ÚNICAMENTE desde 'pendiente'.
_TRANSICIONES = {
    "pendiente": {"pendiente", "en_camino", "entregado", "rechazado"},
    "en_camino": {"en_camino", "entregado"},
    "entregado": {"entregado"},
    "rechazado": {"rechazado"},
}


def _nombre_sucursal(conn, sucursal_id):
    """Nombre de una sucursal para mensajes de error. Si no existe o no es un
    id válido devuelve el propio id, que es más útil que 'None'."""
    try:
        f = conn.execute("SELECT nombre FROM sucursales WHERE id = ?", (sucursal_id,)).fetchone()
    except Exception:
        return sucursal_id
    return (f or {}).get("nombre") or sucursal_id


def _normalizar_fecha(v):
    """Convierte cualquier fecha a 'dd/mm/aaaa hh:mm' (acepta datetime, ISO y RFC/GMT).
    Si la hora es medianoche (00:00) se muestra solo la fecha."""
    if v is None or v == "":
        return ""
    if isinstance(v, datetime):
        return _solo_fecha(v.strftime("%d/%m/%Y %H:%M"))
    s = str(v).strip()
    try:
        return _solo_fecha(
            datetime.strptime(s, "%a, %d %b %Y %H:%M:%S GMT").strftime("%d/%m/%Y %H:%M"))
    except ValueError:
        pass
    s2 = s.replace("T", " ")
    try:
        return _solo_fecha(datetime.strptime(s2[:19], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M"))
    except ValueError:
        return s


def _solo_fecha(f):
    return f[:10] if f.endswith(" 00:00") else f


def _nro_ticket(conn):
    fila = conn.execute("SELECT MAX(id) m FROM pedidos").fetchone()["m"] or 0
    return f"TKT-{int(fila) + 1:05d}"


def _texto_tacho(fraccion):
    """'1 tacho entero', '1/2 tacho', '1/4 tacho'... a partir de la fracción."""
    try:
        f = round(float(fraccion), 4)
    except (TypeError, ValueError):
        return ""
    if f <= 0:
        return ""
    partes = int(f) if f >= 1 else 0
    resto = round(f - partes, 4)
    textos = []
    if partes:
        textos.append("1 tacho entero" if partes == 1 else f"{partes} tachos enteros")
    if resto > 0:
        num, den = 0, 1
        for d in range(1, 17):
            n = round(resto * d)
            if abs(resto * d - n) < 1e-6:
                num, den = n, d
                break
        if num:
            textos.append(f"1/{den} tacho" if num == 1 else f"{num}/{den} tacho")
        else:
            textos.append(f"{resto} tacho")
    return " + ".join(textos)


def _puede_ver_pedido(conn, pedido, detalle):
    """Limita los pedidos a la sucursal propia, salvo para superadmin."""
    sid = sucursal_actual()
    if es_superadmin():
        return True
    if sid is None:
        return False
    if pedido["sucursal_id"] == sid or pedido.get("destino_id") == sid:
        return True
    return any((d.get("destino_id") or pedido.get("destino_id")) == sid for d in detalle)


@pedidos_bp.route("/api/pedidos", methods=["GET", "POST"])
@login_requerido
def pedidos():
    conn = get_conn()
    if request.method == "POST":
        if es_gestion() or es_encargado_almacen(conn):
            conn.close()
            return err("Administración y encargados de almacén solo reciben y gestionan pedidos", 403)
        data = request.get_json() or {}
        detalle = data.get("detalle", [])
        if not detalle:
            conn.close()
            return err("El pedido no tiene productos")
        sid = sucursal_actual()
        if session.get("rol") == "encargado":
            sucursal_id = sid
        else:
            sucursal_id = data.get("sucursal_id") or sid
        if not sucursal_id:
            conn.close()
            return err("Debes indicar la sucursal que realiza el pedido")
        nota = data.get("nota", "")
        fecha = data.get("fecha") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        destino_defecto = data.get("destino_id")
        # Quiénes pueden ser destino de un pedido, por prioridad: el almacén
        # principal primero y después las sucursales proveedora. Se valida abajo
        # contra esta lista. El destino NO se elige por stock (tener mercadería
        # no da permiso para repartirla), sino por quién tiene el producto en
        # el catálogo; ver `elegir_destino`.
        validos = destinos_validos(conn)
        # Dónde hay stock de cada producto. Solo para AVISAR cuando el destino
        # responsable no tiene: que la persona decida, no que el sistema le
        # vacíe el inventario a otra sucursal.
        candidatos = stock_por_destino(conn)
        # Restricción por ciudad: la sucursal que pide solo puede pedir a los
        # Almacenes Principales o a proveedores de su misma ciudad (Cochabamba
        # no pide a La Paz, ni La Paz a Cochabamba). Ver
        # `_ids_proveedores_misma_ciudad` en core/lotes.py.
        permitidos = _ids_proveedores_misma_ciudad(conn, sucursal_id)
        if permitidos is not None:
            validos = [v for v in validos if v in permitidos]
            candidatos = {pid: [t for t in lista if t[0] in permitidos]
                          for pid, lista in candidatos.items()}
            candidatos = {pid: lista for pid, lista in candidatos.items() if lista}
        proveedores_validos = set(validos)
        # Destinos invalidos acumulados (ver mas abajo): antes se cortaba en el
        # primero y se caia el pedido entero.
        destinos_malos = {}
        # Productos cuyo destino responsable no tiene stock: aviso, no bloqueo.
        sin_stock = []
        items = []
        for item in detalle:
            prod_id = item.get("producto_id")
            if not prod_id:
                continue
            # El id se usa como clave de `candidatos`, que viene con claves
            # enteras: si llega "12" en vez de 12 no encontraría el stock.
            try:
                prod_id = int(prod_id)
            except (TypeError, ValueError):
                conn.close()
                return err("El producto del pedido no es válido")
            cantidad = float(item.get("cantidad", 0) or 0)
            try:
                fraccion = float(item.get("tacho_fraccion") or 0)
            except (TypeError, ValueError):
                fraccion = 0
            try:
                tacho_unidad = float(item.get("tacho_unidad") or 0)
            except (TypeError, ValueError):
                tacho_unidad = 0
            fila = conn.execute(
                "SELECT id, nombre, sucursal_id, unidad, IFNULL(unidad_tacho, 0) AS unidad_tacho, "
                "IFNULL(pide_tacho, 0) AS pide_tacho, IFNULL(para_proveer, 1) AS para_proveer "
                "FROM productos WHERE id = ? AND activo = 1", (prod_id,)).fetchone()
            if not fila:
                conn.close()
                return err("Producto no encontrado")
            # Los productos «solo para inventario» no se ofrecen a otras sucursales:
            # se quedan en el almacén que los registró, no entran a la cola de pedidos.
            if not fila["para_proveer"]:
                conn.close()
                return err(f"'{fila['nombre']}' es solo de inventario y no se puede pedir")
            # Pedido por tachos: la medida (¼, ½, ¾, entero) viene en la línea del
            # pedido y quien pide escribe a cuánto equivale un tacho (1 tacho = N
            # kg/unidades). Si no lo escribe, se toma el valor configurado del
            # producto como «default». La cantidad se calcula sola (¼ de 20 kg = 5 kg).
            por_tacho = fraccion > 0
            if por_tacho:
                if not fila["pide_tacho"]:
                    conn.close()
                    return err(f"'{fila['nombre']}' no se pide por tachos")
                if tacho_unidad <= 0:
                    tacho_unidad = float(fila["unidad_tacho"] or 0)
                if tacho_unidad <= 0:
                    conn.close()
                    return err(f"Indica cuánto equivale un tacho de '{fila['nombre']}' (1 tacho = N {fila['unidad'] or 'unidad'})")
                cantidad = round(fraccion * tacho_unidad, 3)
            if cantidad <= 0:
                continue
            proveedor = item.get("destino_id") or destino_defecto
            if not proveedor:
                # Sin destino explicito va a quien REPARTE el producto: la
                # sucursal que lo tiene en el catalogo (si puede atender
                # pedidos), o el principal si esta en una que no despacha.
                proveedor, _stock_destino = elegir_destino(
                    candidatos.get(prod_id), validos, fila["sucursal_id"])
            if not proveedor:
                conn.close()
                return err(f"'{fila['nombre']}' no tiene una sucursal que pueda "
                           f"repartirlo. Revisá la sucursal del producto en el catálogo.")
            # El destino tiene que ser una sucursal que de verdad provee (un
            # almacén principal, o una filial marcada como proveedora). Sin esto
            # se podía pedir a cualquier sucursal pasando su id a mano, incluso
            # a una que no despacha. Una sucursal también puede pedirse a sí
            # misma (su propio almacén principal): eso queda como auto-pedido y
            # al entregarse se registra como reparto interno, sin mover stock
            # entre sucursales.
            if proveedor not in proveedores_validos:
                destinos_malos.setdefault(proveedor, []).append(fila["nombre"])
                continue
            # Si a quien le toca despachar no tiene de esto, se avisa y se sigue:
            # el pedido se guarda igual y la persona ve donde hay. Bloquear
            # acá seria peor, porque el problema real es el stock, no el pedido.
            falta = stock_en_destino(candidatos.get(prod_id), proveedor) <= 0
            if falta:
                sin_stock.append((proveedor, fila["nombre"], prod_id))
            texto_tacho = _texto_tacho(fraccion) if por_tacho else ""
            items.append((prod_id, fila["nombre"], cantidad, proveedor,
                          fila["unidad"] or "unidad", fraccion if por_tacho else 0,
                          texto_tacho, tacho_unidad if por_tacho else 0,
                          1 if falta else 0))
        if destinos_malos:
            # Se informan TODOS los destinos invalidos de una vez. Antes se
            # devolvia en la primera linea mala, asi que un pedido de 24
            # productos se caia entero por culpa de una sola sucursal mal
            # marcada, y el encargado no tenia forma de saber cuales eran las
            # que habia que arreglar: se enteraba de a una, reintentando.
            msgs = []
            for prov, noms in destinos_malos.items():
                unicos = sorted(set(noms))
                lista = ", ".join(unicos[:3])
                if len(unicos) > 3:
                    lista += " (+%d más)" % (len(unicos) - 3)
                msgs.append("%s → %s" % (_nombre_sucursal(conn, prov), lista))
            conn.close()
            return err("Estas sucursales no pueden ser destino porque no están marcadas "
                       "como proveedores: %s. Marcá «¿Provee a otras?» en esa sucursal, "
                       "o pedí a otro almacén." % "; ".join(msgs))
        if not items:
            conn.close()
            return err("El pedido no tiene productos válidos")

        destino_cabecera = items[0][3] if len({it[3] for it in items}) == 1 else None
        nro = _nro_ticket(conn)
        try:
            cur = conn.execute("""
                INSERT INTO pedidos (nro_ticket, fecha, sucursal_id, destino_id, estado, total, usuario, nota)
                VALUES (?, ?, ?, ?, 'pendiente', 0, ?, ?)
            """, (nro, fecha, sucursal_id, destino_cabecera, session.get("usuario", ""), nota))
        except Exception:
            conn.rollback()
            conn.close()
            return err("Error al registrar el pedido")
        pedido_id = cur.lastrowid
        # La linea guarda la marca sin_stock (1 = al proveedor le falta stock)
        # para que el ticket y el detalle muestren el faltante, no solo el aviso.
        _faltantes = sin_stock
        for prod_id, nombre, cantidad, proveedor, unidad, fraccion, texto_tacho, tacho_unidad, sin_stock in items:
            conn.execute("""
                INSERT INTO pedido_detalle (pedido_id, producto_id, producto_nombre, cantidad,
                                            destino_id, unidad, tacho_fraccion, tacho_texto, tacho_unidad, sin_stock)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (pedido_id, prod_id, nombre, cantidad, proveedor, unidad, fraccion, texto_tacho, tacho_unidad, sin_stock))
        sin_stock = _faltantes
        conn.commit()
        # Aviso (no bloquea) por producto: a quien le toca despachar no tiene
        # stock. Se arma antes de cerrar la conexión porque necesita los nombres.
        aviso = ""
        if sin_stock:
            por_destino = {}
            for prov, nombre, prod_id in sin_stock:
                por_destino.setdefault(prov, []).append((nombre, prod_id))
            partes = []
            for prov, pares in por_destino.items():
                # Dónde SÍ hay de estos productos, para que la persona pueda
                # cambiar el destino si hace falta. No se cambia sola: sacarle
                # el inventario a otra sucursal tiene que ser una decisión.
                donde = []
                for nombre, prod_id in pares:
                    otras = ["%s (%g)" % (nom, st) for sid, st, nom
                             in candidatos.get(prod_id, []) if sid != prov]
                    if otras:
                        donde.append("%s está en %s" % (nombre, ", ".join(otras[:2])))
                faltan = [n for n, _ in pares]
                lista = ", ".join(sorted(set(faltan))[:3])
                if len(set(faltan)) > 3:
                    lista += " (+%d más)" % (len(set(faltan)) - 3)
                msg = "%s no tiene stock de %s." % (_nombre_sucursal(conn, prov), lista)
                if donde:
                    msg += " " + " | ".join(sorted(set(donde))[:2])
                partes.append(msg)
            aviso = " ".join(partes)
        conn.close()
        registrar_auditoria("Pedido registrado", f"{nro} de sucursal ID {sucursal_id}")
        return ok({"id": pedido_id, "nro_ticket": nro},
                  message=("Pedido %s registrado. Ojo: %s" % (nro, aviso)) if aviso
                          else "Pedido %s registrado" % nro)

    estado = request.args.get("estado", "").strip()
    filtro = request.args.get("filtro", "").strip()
    q = """
        SELECT p.*, s.nombre AS sucursal_nombre,
               d.nombre AS destino_nombre,
               (SELECT COUNT(*) FROM pedido_detalle d2 WHERE d2.pedido_id = p.id) AS num_items,
               (SELECT COUNT(DISTINCT d3.destino_id) FROM pedido_detalle d3
                WHERE d3.pedido_id = p.id AND d3.destino_id IS NOT NULL) AS num_destinos,
               (SELECT COUNT(*) FROM repartos rr WHERE rr.pedido_id = p.id) AS num_repartos,
               (SELECT IFNULL(SUM(rr.total), 0) FROM repartos rr
                WHERE rr.pedido_id = p.id) AS total_repartos
        FROM pedidos p LEFT JOIN sucursales s ON s.id = p.sucursal_id
        LEFT JOIN sucursales d ON d.id = p.destino_id
        WHERE 1=1
    """
    params = []
    sid = sucursal_actual()
    sid_archivo = sucursal_operativa() if es_superadmin() else sid
    if not es_superadmin():
        if sid:
            # `sucursal_id` es quien solicita y `destino_id` quien provee.
            q += (" AND (p.sucursal_id = ? OR p.destino_id = ? OR "
                  "EXISTS (SELECT 1 FROM pedido_detalle d4 "
                  "WHERE d4.pedido_id = p.id AND d4.destino_id = ?))")
            params += [sid, sid, sid]
        else:
            q += " AND 1 = 0"
    ver_archivados = request.args.get("archivados", "") == "1"
    if sid_archivo:
        operador_archivo = "EXISTS" if ver_archivados else "NOT EXISTS"
        q += (f" AND {operador_archivo} (SELECT 1 FROM pedidos_archivados pa "
              "WHERE pa.pedido_id = p.id AND pa.sucursal_id = ?)")
        params.append(sid_archivo)
    elif ver_archivados:
        q += " AND 1 = 0"
    # `estado` admite "pendiente,en_camino" (coma separada) para filtrar varios
    # estados a la vez, como usa el badge de pendientes del menú.
    estados = [e for e in estado.split(",") if e in _ESTADOS]
    if estados:
        marcas = ",".join("?" * len(estados))
        q += f" AND p.estado IN ({marcas})"
        params += estados
    if filtro:
        q += " AND (p.nro_ticket LIKE ? OR s.nombre LIKE ? OR p.nota LIKE ? OR d.nombre LIKE ?)"
        params += [f"%{filtro}%"] * 4
    suc_f = request.args.get("sucursal_id", "").strip()
    if suc_f.isdigit():
        q += " AND p.sucursal_id = ?"
        params.append(int(suc_f))
    dest_f = request.args.get("destino_id", "").strip()
    if dest_f.isdigit():
        q += (" AND (p.destino_id = ? OR EXISTS "
              "(SELECT 1 FROM pedido_detalle d5 WHERE d5.pedido_id = p.id AND d5.destino_id = ?))")
        params += [int(dest_f), int(dest_f)]
    # Filtro por fecha (mismo patrón que la bandeja: desde/hasta opcional).
    desde = request.args.get("desde", "").strip()
    hasta = request.args.get("hasta", "").strip()
    if desde:
        q += " AND date(p.fecha) >= date(?)"
        params.append(desde[:10])
    if hasta:
        q += " AND date(p.fecha) <= date(?)"
        params.append(hasta[:10])
    count_q = """
        SELECT COUNT(*) AS c 
        FROM pedidos p 
        LEFT JOIN sucursales s ON s.id = p.sucursal_id
        LEFT JOIN sucursales d ON d.id = p.destino_id
        WHERE 1=1
    """
    if not es_superadmin():
        if sid:
            count_q += (" AND (p.sucursal_id = ? OR p.destino_id = ? OR "
                        "EXISTS (SELECT 1 FROM pedido_detalle d4 "
                        "WHERE d4.pedido_id = p.id AND d4.destino_id = ?))")
        else:
            count_q += " AND 1 = 0"
    if sid_archivo:
        operador_archivo = "EXISTS" if ver_archivados else "NOT EXISTS"
        count_q += (f" AND {operador_archivo} (SELECT 1 FROM pedidos_archivados pa "
                    "WHERE pa.pedido_id = p.id AND pa.sucursal_id = ?)")
    elif ver_archivados:
        count_q += " AND 1 = 0"
    count_params = list(params)
    # Aplicar los mismos filtros WHERE que la consulta principal
    if estados:
        count_q += f" AND p.estado IN ({marcas})"
    if filtro:
        count_q += " AND (p.nro_ticket LIKE ? OR s.nombre LIKE ? OR p.nota LIKE ? OR d.nombre LIKE ?)"
    if suc_f.isdigit():
        count_q += " AND p.sucursal_id = ?"
    if dest_f.isdigit():
        count_q += " AND (p.destino_id = ? OR EXISTS (SELECT 1 FROM pedido_detalle d5 WHERE d5.pedido_id = p.id AND d5.destino_id = ?))"
    if desde:
        count_q += " AND date(p.fecha) >= date(?)"
    if hasta:
        count_q += " AND date(p.fecha) <= date(?)"

    total = conn.execute(count_q, count_params).fetchone()["c"]
    offset, limit, pagina, por_pagina = paginar_params()
    q += " ORDER BY p.fecha DESC, p.id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok_paginado([dict(r) for r in rows], total, pagina, por_pagina)


@pedidos_bp.route("/api/pedidos/<int:pedido_id>", methods=["GET"])
@login_requerido
def pedido_detalle(pedido_id):
    conn = get_conn()
    pedido = conn.execute("""
        SELECT p.*, s.nombre AS sucursal_nombre, d.nombre AS destino_nombre
        FROM pedidos p LEFT JOIN sucursales s ON s.id = p.sucursal_id
        LEFT JOIN sucursales d ON d.id = p.destino_id
        WHERE p.id = ?
    """, (pedido_id,)).fetchone()
    if not pedido:
        conn.close()
        return err("Pedido no encontrado", 404)
    # Orden fijo: primero por destino (para que cada quien reciba lo suyo en un
    # bloque), después por categoría y luego por nombre. Sin esto el detalle
    # salía en el orden en que se guardó el pedido, que es el orden en que el
    # encargado tocó los productos, no el que se ve en pantalla.
    detalle = conn.execute(
        "SELECT d.*, COALESCE(cat.nombre, 'Sin categoría') AS categoria_nombre "
        "FROM pedido_detalle d "
        "LEFT JOIN productos pr ON pr.id = d.producto_id "
        "LEFT JOIN categorias cat ON cat.id = pr.categoria_id "
        "WHERE d.pedido_id = ? "
        "ORDER BY d.destino_id, categoria_nombre, d.producto_nombre, d.id",
        (pedido_id,)).fetchall()
    if not _puede_ver_pedido(conn, pedido, detalle):
        conn.close()
        return err("No tienes permisos para ver este pedido", 403)
    repartos = conn.execute(
        "SELECT r.id, r.fecha, r.total, r.origen_sucursal_id AS origen_id, "
        "   o.nombre AS origen_nombre "
        "FROM repartos r LEFT JOIN sucursales o ON o.id = r.origen_sucursal_id "
        "WHERE r.pedido_id = ? ORDER BY r.id", (pedido_id,)).fetchall()
    conn.close()
    return ok({"pedido": dict(pedido), "detalle": [dict(r) for r in detalle],
               "repartos": [dict(r) for r in repartos]})


@pedidos_bp.route("/api/pedidos/<int:pedido_id>/archivar", methods=["PUT"])
@login_requerido
def pedido_archivar(pedido_id):
    conn = get_conn()
    pedido = conn.execute(
        "SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if not pedido:
        conn.close()
        return err("Pedido no encontrado", 404)
    detalle = conn.execute(
        "SELECT destino_id FROM pedido_detalle WHERE pedido_id = ?", (pedido_id,)).fetchall()
    if not _puede_ver_pedido(conn, pedido, detalle):
        conn.close()
        return err("No tienes permisos para este pedido", 403)
    if pedido["estado"] not in ("entregado", "rechazado"):
        conn.close()
        return err("Solo se pueden archivar pedidos entregados o rechazados", 400)
    sid = sucursal_operativa() if es_superadmin() else sucursal_actual()
    if sid is None:
        conn.close()
        return err("Necesitas una sucursal asignada para archivar pedidos", 403)
    if not (pedido["sucursal_id"] == sid or
            pedido.get("destino_id") == sid or
            any((d.get("destino_id") or pedido.get("destino_id")) == sid for d in detalle)):
        conn.close()
        return err("Solo puedes archivar pedidos relacionados con tu sucursal", 403)

    data = request.get_json() or {}
    archivado = data.get("archivado")
    if not isinstance(archivado, bool):
        conn.close()
        return err("Indica si deseas archivar o restaurar el pedido")
    if archivado:
        conn.execute(
            "INSERT INTO pedidos_archivados (pedido_id, sucursal_id, usuario, fecha) "
            "VALUES (?, ?, ?, ?) ON DUPLICATE KEY UPDATE usuario = VALUES(usuario), "
            "fecha = VALUES(fecha)",
            (pedido_id, sid, session.get("usuario", ""),
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        mensaje = "Pedido archivado para tu sucursal"
    else:
        conn.execute(
            "DELETE FROM pedidos_archivados WHERE pedido_id = ? AND sucursal_id = ?",
            (pedido_id, sid))
        mensaje = "Pedido restaurado en tu historial"
    conn.commit()
    conn.close()
    registrar_auditoria("Pedido archivado" if archivado else "Pedido restaurado",
                        f"{pedido['nro_ticket']} para sucursal ID {sid}")
    return ok(message=mensaje)


def _ticket_data(conn, pedido_id):
    """Agrupa el pedido por proveedor y por categoría para el ticket único."""
    pedido = conn.execute("""
        SELECT p.*, s.nombre AS sucursal_nombre
        FROM pedidos p LEFT JOIN sucursales s ON s.id = p.sucursal_id
        WHERE p.id = ?
    """, (pedido_id,)).fetchone()
    detalle = conn.execute("""
        SELECT d.*, pr.costo_promedio AS costo_unitario, c.nombre AS categoria_nombre
        FROM pedido_detalle d
        LEFT JOIN productos pr ON pr.id = d.producto_id
        LEFT JOIN categorias c ON c.id = pr.categoria_id
        WHERE d.pedido_id = ?
        ORDER BY c.nombre, d.producto_nombre
    """, (pedido_id,)).fetchall()
    if not _puede_ver_pedido(conn, pedido, detalle):
        return None
    suc_nombres = {r["id"]: dict(id=r["id"], nombre=r["nombre"], principal=bool(r["principal"]))
                   for r in conn.execute("SELECT id, nombre, principal FROM sucursales").fetchall()}
    proveedores = OrderedDict()
    for d in detalle:
        proveedor_id = d["destino_id"] or pedido["destino_id"]
        prov = suc_nombres.get(proveedor_id)
        key = proveedor_id if prov else 0
        if key not in proveedores:
            proveedores[key] = {
                "id": prov["id"] if prov else None,
                "nombre": prov["nombre"] if prov else f"Proveedor ID {proveedor_id}",
                "principal": prov["principal"] if prov else False,
                "categorias": OrderedDict(),
            }
        cat = d["categoria_nombre"] or "Sin categoría"
        if cat not in proveedores[key]["categorias"]:
            proveedores[key]["categorias"][cat] = []
        proveedores[key]["categorias"][cat].append({
            "producto_id": d["producto_id"],
            "producto": d["producto_nombre"],
            "unidad": d["unidad"] or "unidad",
            "cantidad": d["cantidad"],
            "tacho_texto": d.get("tacho_texto") or "",
            "tacho_unidad": d.get("tacho_unidad") or 0,
            "sin_stock": d.get("sin_stock"),
            "costo": d["costo_unitario"] or 0,
            # Sin round(): el subtotal tiene que sumar EXACTAMENTE lo mismo que
            # el total del pedido y que el total del reparto. Con
            # costo 3,333 x 3 esta linea mostraba 10.0 y el total daba 9.999.
            "subtotal": (d["costo_unitario"] or 0) * d["cantidad"],
        })
    ped = dict(pedido)
    ped["fecha"] = _normalizar_fecha(ped.get("fecha"))
    return {
        "pedido": ped,
        "num_items": len(detalle),
        "proveedores": [
            {"id": p["id"], "nombre": p["nombre"], "principal": p["principal"],
             "categorias": [{"nombre": c, "items": it} for c, it in p["categorias"].items()]}
            for p in proveedores.values()
        ],
    }


@pedidos_bp.route("/api/pedidos/<int:pedido_id>/ticket")
@login_requerido
def pedido_ticket(pedido_id):
    conn = get_conn()
    data = _ticket_data(conn, pedido_id)
    conn.close()
    if data is None:
        return err("No tienes permisos para ver este pedido", 403)
    return ok(data)


@pedidos_bp.route("/pedidos/ticket/<int:pedido_id>")
@login_requerido
def pedido_ticket_pagina(pedido_id):
    conn = get_conn()
    data = _ticket_data(conn, pedido_id)
    conn.close()
    if data is None:
        return redirect("/")
    return render_template("ticket_pedido.html", data=data)


@pedidos_bp.route("/api/pedidos/<int:pedido_id>/estado", methods=["PUT"])
@login_requerido
def pedido_estado(pedido_id):
    conn = get_conn()
    pedido = conn.execute(
        "SELECT * FROM pedidos WHERE id = ? FOR UPDATE", (pedido_id,)).fetchone()
    if not pedido:
        conn.close()
        return err("Pedido no encontrado", 404)
    sid_op = sucursal_operativa()
    detalle = conn.execute("SELECT destino_id FROM pedido_detalle WHERE pedido_id = ?",
                           (pedido_id,)).fetchall()
    if not _puede_ver_pedido(conn, pedido, detalle):
        conn.close()
        return err("No tienes permisos para ver este pedido", 403)
    data = request.get_json() or {}
    estado = data.get("estado", "")
    actual = pedido["estado"]
    if estado not in _ESTADOS:
        conn.close()
        return err("Estado inválido")
    # Transición válida: no se puede volver atrás (el stock ya salió del almacén).
    if estado not in _TRANSICIONES.get(actual, {actual}):
        conn.close()
        return err(f"No se puede pasar de '{actual}' a '{estado}'")

    # La logística (preparador/repartidor) mueve la ETAPA, no el `estado`:
    # ver PUT /api/pedidos/<id>/etapa. Aquí el cambio de estado sigue siendo
    # exclusivo de quien coordina el inventario, como siempre.
    if not es_gestion():
        conn.close()
        return err("Solo administración puede cambiar el estado directamente", 403)
    es_destino = any((d.get("destino_id") or pedido.get("destino_id")) == sid_op
                     for d in detalle)
    if not es_superadmin() and not es_destino:
        conn.close()
        return err("Solo puedes cambiar el estado de pedidos que despacha tu sucursal", 403)

    # El stock se mueve SOLO al marcar 'entregado' (baja del almacén que
    # despacha y suma a la sucursal que lo pidió). 'en_camino' y 'rechazado'
    # no tocan el inventario.
    if estado == "entregado" and actual != "entregado":
        # El movimiento de stock solo puede hacerlo el almacén destino del
        # pedido, igual que al entregar. Una filial no puede descontar
        # mercadería del almacén desde el desplegable de estado.
        det = conn.execute("SELECT destino_id FROM pedido_detalle WHERE pedido_id = ?",
                           (pedido_id,)).fetchall()
        es_destino = any((d["destino_id"] or pedido["destino_id"]) == sid_op for d in det)
        if not (es_gestion() or es_encargado_almacen(conn)) \
                or (not es_superadmin() and not es_destino):
            conn.close()
            return err("Para entregar la mercadería tienes que usar «Entregar». "
                       "Marcar el estado no descuenta el inventario.")
        movido = _despachar_stock(conn, pedido)
        if movido[0] == "error":
            conn.close()
            return err(movido[1])
        conn.execute("UPDATE pedidos SET estado = ?, etapa = ?, total = ? WHERE id = ?",
                     (estado, estado, movido[0], pedido_id))
        conn.commit()
        conn.close()
        registrar_auditoria("Pedido entregado", f"{pedido['nro_ticket']} -> entregado")
        return ok(message=f"Pedido {pedido['nro_ticket']} entregado. "
                          f"Se descontó el stock del almacén.")

    conn.execute("UPDATE pedidos SET estado = ?, etapa = ? WHERE id = ?",
                 (estado, estado, pedido_id))
    conn.commit()
    conn.close()
    registrar_auditoria("Pedido actualizado", f"{pedido['nro_ticket']} -> {estado}")
    return ok(message=f"Pedido {pedido['nro_ticket']} marcado como {estado}")


def _despachar_stock(conn, pedido, detalle=None, origen_propio=True):
    """Mueve la mercadería de un pedido: la descuenta del almacén que lo despacha
    y la suma a la sucursal que lo pidió, dejando un reparto por proveedor.

    Devuelve `(total, reparto_ids)`, o `("error", mensaje)` si no se puede hacer.
    OJO: los dos casos son tuplas, así que el que llama tiene que comparar
    `movido[0] == "error"` — con `isinstance(movido, tuple)` el éxito se
    confundiría con un fallo y nunca se movería stock.
    NO hace commit ni cierra la conexión: eso es del llamador, para que
    /despachar y el cambio de estado compartan exactamente la misma lógica y no
    puedan divergir en el descuento de inventario.

    `origen_propio=False` se usa cuando quien entrega NO es el proveedor (el
    repartidor de América/Simón López entrega mercadería que salió del almacén
    principal). Sin ese parámetro el repartidor recibiría "Solo puedes despachar
    líneas cuyo origen sea tu propia sucursal" y el pedido nunca se cerraría.

    Un auto-pedido (origen == sucursal solicitante) deja reparto y reparto_detalle
    pero no desplaza stock ni valida stock de salida: es solo la constancia de
    que la sucursal se surtió de su propio almacén."""
    if detalle is None:
        detalle = conn.execute("SELECT * FROM pedido_detalle WHERE pedido_id = ?",
                               (pedido["id"],)).fetchall()
    if not detalle:
        return "error", "El pedido no tiene productos"

    # Agrupar líneas por proveedor (cada proveedor genera su propio reparto)
    grupos = {}
    sid_op = sucursal_operativa()
    for d in detalle:
        origen_id = d["destino_id"] or pedido["destino_id"]
        if not origen_id:
            return "error", "Una línea del pedido no tiene proveedor asignado"
        # Una sucursal puede pedirse a sí misma (auto-pedido a su propio
        # almacén principal). En ese caso el reparto se registra igual como
        # constancia y total, pero NO se mueve stock entre sucursales (abajo se
        # salta la salida/entrada cuando origen == sucursal solicitante).
        if origen_propio and sid_op is not None and origen_id != sid_op:
            return "error", "Solo puedes despachar líneas cuyo origen sea tu propia sucursal"
        grupos.setdefault(origen_id, []).append(d)

    suc = conn.execute("SELECT nombre FROM sucursales WHERE id = ?",
                       (pedido["sucursal_id"],)).fetchone()
    if not suc:
        return "error", "Sucursal solicitante no encontrada"

    # Validar stock en cada proveedor antes de despachar (evita stock negativo).
    # Se valida TODO antes de mover nada: si una línea falla, no se movió ninguna.
    for origen_id, items in grupos.items():
        org = conn.execute("SELECT nombre FROM sucursales WHERE id = ?", (origen_id,)).fetchone()
        if not org:
            return "error", f"Proveedor {origen_id} no encontrado"
        for d in items:
            # Auto-pedido: no hay traslado, así que no se valida stock de salida.
            if origen_id == pedido["sucursal_id"]:
                continue
            stock = stock_actual(conn, d["producto_id"], origen_id)
            if stock < d["cantidad"]:
                return "error", (f"Stock insuficiente de {d['producto_nombre']} en "
                                 f"{org['nombre']}. Disponible: {stock}")

    # La mercadería se mueve HOY, no el día en que se hizo el pedido.
    # Si se usara `pedido["fecha"]`, un pedido del 30-sep entregado el 01-oct
    # contabilizaría la entrada en la planilla del 30-sep (que puede estar
    # cerrada) y la planilla del día de la entrega saldría sin ese ingreso.
    # El ingreso del día de la planilla se deduce de los movimientos 'entrada'
    # con DATE(fecha) = fecha de la planilla, así que la fecha que importa es
    # la de la llegada real.
    fecha_mov = datetime.now()
    total = 0.0
    reparto_ids = []
    for origen_id, items in grupos.items():
        cur = conn.execute(
            "INSERT INTO repartos (fecha, sucursal_id, origen_sucursal_id, total, usuario, nota, pedido_id) "
            "VALUES (?, ?, ?, 0, ?, ?, ?)",
            (fecha_mov, pedido["sucursal_id"], origen_id,
             session.get("usuario", ""), f"Despacho del pedido {pedido['nro_ticket']}", pedido["id"]))
        reparto_id = cur.lastrowid
        reparto_ids.append(reparto_id)
        subtotal_reparto = 0.0
        for d in items:
            costo = 0.0
            prod = conn.execute("SELECT costo_promedio FROM productos WHERE id = ?",
                                (d["producto_id"],)).fetchone()
            if prod and prod["costo_promedio"]:
                costo = float(prod["costo_promedio"])
            subtotal = d["cantidad"] * costo
            subtotal_reparto += subtotal
            total += subtotal
            # Sin round() en ningun total del despacho: las columnas son DOUBLE.
            # Con 3 x 3.333 el subtotal real es 9.999; redondear a 2 dejaba
            # pedido.total, reparto.total y reparto_detalle.subtotal con tres
            # valores distintos del mismo delivery.
            conn.execute("""
                INSERT INTO reparto_detalle (reparto_id, producto_id, producto_nombre, cantidad, costo_unitario, subtotal)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (reparto_id, d["producto_id"], d["producto_nombre"], d["cantidad"], costo, subtotal))
            # Los auto-pedidos no mueven stock (origen == sucursal que pide):
            # el reparto queda como constancia y el total, sin salida ni entrada.
            if origen_id != pedido["sucursal_id"]:
                registrar_movimiento(conn, d["producto_id"], "salida", d["cantidad"], costo, fecha_mov,
                                      f"Despacho pedido {pedido['nro_ticket']}", session.get("usuario", ""), origen_id)
                registrar_movimiento(conn, d["producto_id"], "entrada", d["cantidad"], costo, fecha_mov,
                                      f"Recepción pedido {pedido['nro_ticket']}", session.get("usuario", ""),
                                      pedido["sucursal_id"])
        conn.execute("UPDATE repartos SET total = ? WHERE id = ?", (subtotal_reparto, reparto_id))
    return total, reparto_ids


@pedidos_bp.route("/api/pedidos/<int:pedido_id>/despachar", methods=["POST"])
@login_requerido
def pedido_despachar(pedido_id):
    """Convierte un pedido pendiente en repartos reales: uno por cada proveedor.
    Puede despachar un admin/superadmin o un encargado de almacén principal."""
    conn = get_conn()
    # FOR UPDATE: bloquea la fila del pedido. Si dos usuarios dan clic en
    # "Despachar" al mismo tiempo (o hay un doble clic rápido), el segundo
    # intento frena acá y no duplica el descuento de stock ni los repartos.
    pedido = conn.execute(
        "SELECT * FROM pedidos WHERE id = ? FOR UPDATE", (pedido_id,)).fetchone()
    if not pedido:
        conn.close()
        return err("Pedido no encontrado", 404)
    detalle = conn.execute("SELECT destino_id FROM pedido_detalle WHERE pedido_id = ?",
                           (pedido_id,)).fetchall()
    if not _puede_ver_pedido(conn, pedido, detalle):
        conn.close()
        return err("No tienes permisos para despachar este pedido", 403)
    if pedido["estado"] not in ("pendiente", "en_camino"):
        conn.close()
        return err(f"Este pedido no se puede despachar (estado actual: {pedido['estado']})", 400)
    if not (es_gestion() or es_encargado_almacen(conn)):
        conn.close()
        return err("No tienes permisos para despachar pedidos", 403)
    # Admins/almacén despachan SOLO pedidos destinados a su propia sucursal.
    # Superadmin conserva acceso global.
    sid_op = sucursal_operativa()
    if not es_superadmin() and not any(
            (d["destino_id"] or pedido["destino_id"]) == sid_op for d in detalle):
        conn.close()
        return err("Solo puedes despachar pedidos destinados a tu almacén", 403)
    if pedido["estado"] not in ("pendiente", "en_camino"):
        conn.close()
        return err("Solo se pueden despachar pedidos en estado 'pendiente' o 'en_camino'")
    detalle = conn.execute("SELECT * FROM pedido_detalle WHERE pedido_id = ?", (pedido_id,)).fetchall()
    if not detalle:
        conn.close()
        return err("El pedido no tiene productos")

    movido = _despachar_stock(conn, pedido, detalle)
    if movido[0] == "error":
        conn.close()
        return err(movido[1])
    total, reparto_ids = movido
    # SIN round(): las columnas son DOUBLE. Con round(total, 2) el mismo pedido
    # guardaba un total si lo despachaba el almacen con el desplegable de estado
    # y otro distinto si lo entregaba la logistica con las etapas. Con costo
    # 3,333 x 3 uno guardaba 10.0 y el otro 9.999, y las cuentas no cuadraban.
    conn.execute("UPDATE pedidos SET estado = 'entregado', etapa = 'entregado', total = ? WHERE id = ?",
                 (total, pedido_id))
    conn.commit()
    conn.close()
    registrar_auditoria("Pedido despachado",
                        f"{pedido['nro_ticket']} -> repartos #{','.join(map(str, reparto_ids))} (Bs {total})")
    return ok({"reparto_ids": reparto_ids, "reparto_id": reparto_ids[0] if reparto_ids else None,
               "total": total},
              message=f"Pedido {pedido['nro_ticket']} despachado como repartos #{','.join(map(str, reparto_ids))}")


@pedidos_bp.route("/api/pedidos/bandeja", methods=["GET"])
@login_requerido
def pedidos_bandeja():
    """Cola de trabajo agrupada por sucursal.

    La cola son los pedidos que tu sucursal DESPACHA (las lineas que le piden a
    ella, `dd.destino_id = su sucursal`, porque `destino_id` es el proveedor).

    El rol logistico (preparador/repartidor) ve ademas los pedidos que su
    sucursal le hizo a un almacen: los puede mirar para saber que esta
    esperando, pero NO puede avanzarlos, porque la mercaderia la prepara y
    despacha el almacen. Eso lo aplica pedido_etapa, no el listado.

    El superadmin sin sucursal asignada consolida todos los pendientes. Un admin
    de sucursal sin asignación no recibe pedidos."""
    conn = get_conn()
    sid = sucursal_operativa() if es_superadmin() else sucursal_actual()
    alcance = (request.args.get("alcance", "") or "mi-almacen").strip().lower()
    if alcance not in ("mi-almacen", "todas"):
        conn.close()
        return err("Alcance de bandeja inválido", 400)
    if alcance == "todas" and not es_superadmin():
        conn.close()
        return err("Solo el superadmin puede ver todas las sucursales", 403)
    if es_superadmin() and alcance == "todas":
        sid = None
    elif es_superadmin() and sid is None:
        conn.close()
        return err("No tienes almacén asignado; elige la vista de todas las sucursales", 400)
    where = "WHERE 1=1"
    params = []
    # La bandeja es la recepción de pedidos. La ven:
    #  - admin/superadmin (gestion),
    #  - el rol logístico (preparador/repartidor) de su sucursal,
    #  - el encargado de un ALMACÉN PRINCIPAL (despacha lo que le piden), y
    #  - los usuarios RECEPTOR (p. ej. "AS America"/"AS Simon Lopez"), que son
    #    el almacén receptor de las sucursales proveedoras.
    # El encargado NORMAL de una filial NO ve la bandeja: él pide ("Mis
    # pedidos"), no despacha. Antes bastaba con ser proveedor (provee=1) y el
    # encargado de América y el receptor AS America veían el MISMO panel; ahora
    # la recepción es solo del usuario receptor.
    if sid:
        # La cola de trabajo son los pedidos que tu sucursal DESPACHA (las lineas
        # que le piden a ella).
        #
        # El rol logistico ve ademas los pedidos que su sucursal le hizo a un
        # almacen: los necesita para saber que esta esperando, aunque no puede
        # tocarlos (eso lo decide pedido_etapa, que solo deja avanzar a quien
        # despacha). De ahi el paréntesis: es O una cosa O la otra.
        if es_logistica():
            where += (" AND (EXISTS (SELECT 1 FROM pedido_detalle dd "
                      "WHERE dd.pedido_id = p.id AND dd.destino_id = ?) "
                      "OR (p.sucursal_id = ? AND EXISTS (SELECT 1 FROM pedido_detalle dd2 "
                      "WHERE dd2.pedido_id = p.id)))")
            params += [sid, sid]
        else:
            where += (" AND EXISTS (SELECT 1 FROM pedido_detalle dd "
                      "WHERE dd.pedido_id = p.id AND dd.destino_id = ?)")
            params.append(sid)
    elif not es_superadmin():
        conn.close()
        return ok([])
    # La bandeja es una COLA DE TRABAJO, no un parte diario: por defecto muestra
    # todo lo pendiente sin limite de fechas.
    #
    # Antes hacia `desde = request.args.get("desde", "") or hoy`, o sea que sin
    # filtro solo miraba el dia de hoy. Como la fecha del pedido es cuando se
    # CREO (no cuando hay que despacharlo), un pedido del viernes 17:46 a la
    # espera del lunes desaparecia de la bandeja al pasar las 00:00, en
    # silencio: el encargado veia "No hay pedidos para mostrar" con trabajo
    # real pendiente. Los filtros desde/hasta siguen disponibles para cuando si
    # se quiere acotar a un rango.
    #
    # El estado tambien es opcional: por defecto se muestra lo que hay que
    # trabajar (pendiente + en camino). Los entregados/rechazados se ven en el
    # historial.
    vista = (request.args.get("vista", "") or "activos").strip().lower()
    if vista not in ("activos", "historial", "archivados"):
        conn.close()
        return err("Vista de pedidos inválida", 400)
    if vista == "archivados":
        where += " AND p.estado IN ('entregado', 'rechazado')"
        if sid:
            where += (" AND EXISTS (SELECT 1 FROM pedidos_archivados pa "
                      "WHERE pa.pedido_id = p.id AND pa.sucursal_id = ?)")
            params.append(sid)
        elif es_superadmin():
            where += " AND EXISTS (SELECT 1 FROM pedidos_archivados pa WHERE pa.pedido_id = p.id)"
    elif vista == "historial":
        where += " AND p.estado IN ('entregado', 'rechazado')"
        if sid:
            where += (" AND NOT EXISTS (SELECT 1 FROM pedidos_archivados pa "
                      "WHERE pa.pedido_id = p.id AND pa.sucursal_id = ?)")
            params.append(sid)
    else:
        where += " AND p.estado IN ('pendiente', 'en_camino')"
    desde = (request.args.get("desde", "") or "").strip()
    hasta = (request.args.get("hasta", "") or "").strip()
    if desde:
        where += " AND date(p.fecha) >= date(?)"
        params.append(desde[:10])
    if hasta:
        where += " AND date(p.fecha) <= date(?)"
        params.append(hasta[:10])
    rows = conn.execute("""
        SELECT p.id, p.nro_ticket, p.fecha, p.estado, p.etapa, p.nota, p.usuario,
               p.sucursal_id, p.destino_id,
               s.nombre AS sucursal_nombre
        FROM pedidos p JOIN sucursales s ON s.id = p.sucursal_id
        """ + where + """
        ORDER BY s.nombre, p.fecha DESC, p.id DESC
    """, params or None).fetchall()
    pedido_ids = [r["id"] for r in rows]
    det = []
    if pedido_ids:
        marks = ",".join("?" * len(pedido_ids))
        det = conn.execute(f"""
            SELECT d.pedido_id, d.producto_nombre, d.cantidad, d.unidad,
                   d.tacho_texto, d.tacho_unidad, d.destino_id, s.nombre AS destino_nombre,
                   COALESCE(c.nombre, 'Sin categoría') AS categoria_nombre
            FROM pedido_detalle d LEFT JOIN sucursales s ON s.id = d.destino_id
            LEFT JOIN productos pr ON pr.id = d.producto_id
            LEFT JOIN categorias c ON c.id = pr.categoria_id
            WHERE d.pedido_id IN ({marks})
            ORDER BY d.pedido_id, d.destino_id, categoria_nombre, d.producto_nombre, d.id
        """, pedido_ids).fetchall()
    conn.close()
    det_map = {}
    for d in det:
        det_map.setdefault(d["pedido_id"], []).append(dict(d))
    grupos = OrderedDict()
    for r in rows:
        key = r["sucursal_id"]
        grupos.setdefault(key, {"sucursal_id": key, "nombre": r["sucursal_nombre"], "pedidos": []})
        pedido = dict(r)
        pedido["fecha"] = _normalizar_fecha(r["fecha"])
        pedido["items"] = det_map.get(r["id"], [])
        grupos[key]["pedidos"].append(pedido)
    return ok(list(grupos.values()))


@pedidos_bp.route("/api/logistica/resumen")
@login_requerido
def logistica_resumen():
    """Panel minimo del preparador/repartidor: SOLO su cola de trabajo.

    No devuelve ventas, gastos, ganancias, valor de inventario ni alertas:
    el rol logistico no gestiona nada de eso, asi que no debe verlo.
    Cuenta los pedidos que PLACO su sucursal (`p.sucursal_id`), agrupados por
    `etapa` y no por `estado`: en este flujo el `estado` sigue en 'pendiente'
    durante preparacion y transporte, asi que contar por `estado` daba el mismo
    numero en las dos columnas y decia '0 por entregar' con work en la calle."""
    conn = get_conn()
    sid = sucursal_actual()
    if not es_logistica() or sid is None:
        conn.close()
        return err("Solo disponible para preparador/repartidor", 403)

    def _contar(etapas, abierto=True):
        marcas = ",".join(["%s"] * len(etapas))
        extra = " AND p.etapa <> 'entregado'" if abierto else ""
        # Cuenta la cola accionable: pedidos donde ESTA sucursal es el proveedor
        # (`dd.destino_id = sid`). Los que la sucursal le hizo a un almacen se
        # pueden ver pero no avanzar, asi que no cuentan como trabajo.
        f = conn.execute(
            f"SELECT COUNT(DISTINCT p.id) AS n FROM pedidos p "
            f"JOIN pedido_detalle dd ON dd.pedido_id = p.id "
            f"WHERE dd.destino_id = %s AND p.etapa IN ({marcas}){extra}",
            [sid] + list(etapas)).fetchone()
        return int((f or {}).get("n") or 0)

    nombre = conn.execute("SELECT nombre FROM sucursales WHERE id = ?",
                          (sid,)).fetchone()
    resumen = {
        "sucursal": (nombre or {}).get("nombre") or "",
        "rol": session.get("rol"),
        "por_preparar": _contar(("pendiente",)),
        "por_entregar": _contar(("en_camino",)),
        "total": _contar(("pendiente", "en_camino")),
    }
    conn.close()
    return ok(resumen)


# ---- Etapa logistica del pedido -------------------------------------------
# `etapa` es/está alineada con `estado`: el flujo es UNO solo para todos los
# roles (pendiente -> en_camino -> entregado, mas rechazado desde pendiente).
# El stock se mueve al llegar a 'entregado'.
_ETAPAS = ("pendiente", "en_camino", "entregado", "rechazado")

_AVANCE_PREPARADOR = {"pendiente": "en_camino"}
_AVANCE_REPARTIDOR = {"en_camino": "entregado"}
_AVANCE_ORDENANTE = {"pendiente": "en_camino", "en_camino": "entregado"}

_ETIQUETA_ETAPA = {
    "pendiente": "Pendiente",
    "en_camino": "En camino",
    "entregado": "Entregado",
    "rechazado": "Rechazado",
}


@pedidos_bp.route("/api/pedidos/<int:pedido_id>/etapa", methods=["PUT"])
@login_requerido
def pedido_etapa(pedido_id):
    """Avanza la etapa logística del pedido desde quien prepara o recibe."""
    conn = get_conn()
    # FOR UPDATE: dos personas marcando a la vez no pueden saltarse la
    # validacion de la etapa actual ni duplicar el movimiento de stock.
    pedido = conn.execute(
        "SELECT * FROM pedidos WHERE id = ? FOR UPDATE", (pedido_id,)).fetchone()
    if not pedido:
        conn.close()
        return err("Pedido no encontrado", 404)
    detalle = conn.execute("SELECT destino_id FROM pedido_detalle WHERE pedido_id = ?",
                           (pedido_id,)).fetchall()
    if not _puede_ver_pedido(conn, pedido, detalle):
        conn.close()
        return err("No tienes permisos para ver este pedido", 403)

    data = request.get_json() or {}
    destino_etapa = (data.get("etapa") or "").strip()
    if destino_etapa not in _ETAPAS:
        conn.close()
        return err("Etapa invalida")

    rol = session.get("rol")
    sid = sucursal_actual()
    # Los roles logísticos, administración y el encargado de almacén pueden
    # avanzar la etapa desde la sucursal que despacha. Superadmin conserva
    # acceso global.
    if rol not in ("preparador", "repartidor") and not es_receptor() \
            and not (es_gestion() or es_encargado_almacen(conn)):
        conn.close()
        return err("Tu rol no puede cambiar la etapa del pedido", 403)
    if not sid and not es_superadmin():
        conn.close()
        return err("No tenes sucursal asignada", 403)

    # El preparador actúa en la sucursal proveedora; el repartidor confirma la
    # recepción en la sucursal solicitante. Administración/encargado de almacén
    # solo gestiona los pedidos que despacha su sucursal.
    es_proveedor = any((d["destino_id"] or pedido["destino_id"]) == sid for d in detalle)
    if rol == "preparador" and not es_proveedor:
        conn.close()
        return err("Ese pedido no lo despacha tu sucursal", 403)
    if rol == "repartidor" and pedido["sucursal_id"] != sid:
        conn.close()
        return err("Ese pedido no lo recibe tu sucursal", 403)
    if rol not in ("preparador", "repartidor") and not es_superadmin() \
            and not es_proveedor:
        conn.close()
        return err("Ese pedido no lo despacha tu sucursal", 403)

    actual_etapa = pedido["etapa"] or "pendiente"
    if destino_etapa == actual_etapa:
        conn.close()
        return ok(message=f"El pedido ya estaba en {_ETIQUETA_ETAPA[actual_etapa]}")

    # Cada rol solo puede avanzar SU paso, y hacia adelante. El rechazo es un
    # caso aparte: solo el ordenante/coordinador puede rechazar, y solo desde
    # 'pendiente' (el pedido recién entra, no se movió nada). Un pedido ya
    # 'en_camino' o 'entregado' no se puede rechazar.
    if rol == "preparador":
        permitido = _AVANCE_PREPARADOR.get(actual_etapa)
    elif rol == "repartidor":
        permitido = _AVANCE_REPARTIDOR.get(actual_etapa)
    else:
        if destino_etapa == "rechazado":
            permitido = "rechazado" if actual_etapa == "pendiente" else None
        else:
            permitido = _AVANCE_ORDENANTE.get(actual_etapa)

    if permitido != destino_etapa:
        conn.close()
        if rol == "preparador":
            esperado = _AVANCE_PREPARADOR.get(actual_etapa)
            if esperado is None:
                return err(f"El pedido ya esta en {_ETIQUETA_ETAPA[actual_etapa]}; "
                           f"el repartidor lo continua", 403)
        return err(f"No se puede pasar de '{_ETIQUETA_ETAPA[actual_etapa]}' "
                   f"a '{_ETIQUETA_ETAPA[destino_etapa]}'", 403)

    # El stock se mueve UNA sola vez, al llegar a 'entregado'. Un pedido ya
    # entregado no vuelve a mover stock.
    stock_ya_movido = pedido["estado"] == "entregado"

    if destino_etapa == "entregado" and not stock_ya_movido:
        movido = _despachar_stock(conn, pedido, origen_propio=False)
        if movido[0] == "error":
            conn.rollback()
            conn.close()
            return err(movido[1])
        # Sin round(): las columnas son DOUBLE y el movimiento ya viene
        # valuado con la precision del lote. Redondear a 2 decimales alteraba el
        # total real del pedido (3 x 3.333 = 9.999 se guardaba como 10.0).
        conn.execute("UPDATE pedidos SET etapa = ?, estado = ?, total = ? WHERE id = ?",
                     (destino_etapa, "entregado", movido[0], pedido_id))
        conn.commit()
        conn.close()
        registrar_auditoria("Pedido entregado",
                            f"{pedido['nro_ticket']} etapa {actual_etapa} -> entregado")
        return ok(message=f"Pedido {pedido['nro_ticket']} entregado. "
                          f"Se movio el stock a tu sucursal.")

    # Rechazo: desde pendiente, sin tocar stock.
    if destino_etapa == "rechazado":
        conn.execute("UPDATE pedidos SET etapa = ?, estado = ? WHERE id = ?",
                     (destino_etapa, "rechazado", pedido_id))
        conn.commit()
        conn.close()
        registrar_auditoria("Pedido rechazado",
                            f"{pedido['nro_ticket']} rechazado desde pendiente")
        return ok(message=f"Pedido {pedido['nro_ticket']} rechazado.")

    conn.execute("UPDATE pedidos SET etapa = ?, estado = ? WHERE id = ?",
                 (destino_etapa, destino_etapa, pedido_id))
    conn.commit()
    conn.close()
    registrar_auditoria("Pedido actualizado",
                        f"{pedido['nro_ticket']} etapa {actual_etapa} -> {destino_etapa}")
    return ok(message=f"Pedido {pedido['nro_ticket']}: "
                      f"{_ETIQUETA_ETAPA[actual_etapa]} -> {_ETIQUETA_ETAPA[destino_etapa]}")
