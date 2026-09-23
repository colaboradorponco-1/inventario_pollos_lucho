"""Pedidos / tickets por sucursal (Fase 2).

Cada sucursal registra un pedido (ticket) con todo lo que necesita; cada
producto se pide al almacén/sucursal que lo provee (puede haber varios
proveedores en un mismo pedido). Se genera UN ticket imprimible agrupado
por proveedor y por categoría.

Estados: pendiente -> despachado -> cumplido.
Cambiar el estado o despachar puede hacerlo un admin/superadmin o un
encargado de almacén principal (los que coordinan el inventario).
"""
from collections import OrderedDict
from datetime import datetime

from flask import Blueprint, redirect, render_template, request, session

from database import get_conn
from .util import (ok, err, login_requerido, registrar_auditoria, ok_paginado,
                   paginar_params, sucursal_actual, sucursal_operativa, stock_actual,
                   es_gestion, es_superadmin, es_encargado_almacen, registrar_movimiento)

pedidos_bp = Blueprint("pedidos", __name__)

_ESTADOS = ("pendiente", "despachado", "cumplido")


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


def _puede_ver_pedido(conn, pedido, detalle):
    """Visibilidad para encargados filiales: solo lo que piden o lo que proveen."""
    sid = sucursal_actual()
    if sid is None or es_superadmin():
        return True
    if session.get("rol") != "encargado":
        return True
    f = conn.execute("SELECT principal FROM sucursales WHERE id = ?", (sid,)).fetchone()
    if f and f["principal"]:
        return True
    if pedido["sucursal_id"] == sid or pedido.get("destino_id") == sid:
        return True
    return any((d.get("destino_id") or pedido.get("destino_id")) == sid for d in detalle)


@pedidos_bp.route("/api/pedidos", methods=["GET", "POST"])
@login_requerido
def pedidos():
    conn = get_conn()
    if request.method == "POST":
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
        items = []
        for item in detalle:
            prod_id = item.get("producto_id")
            cantidad = float(item.get("cantidad", 0) or 0)
            if cantidad <= 0 or not prod_id:
                continue
            fila = conn.execute("SELECT id, nombre, sucursal_id, unidad FROM productos WHERE id = ? AND activo = 1",
                                (prod_id,)).fetchone()
            if not fila:
                conn.close()
                return err("Producto no encontrado")
            proveedor = item.get("destino_id") or destino_defecto or fila["sucursal_id"]
            if not proveedor:
                conn.close()
                return err(f"Indica el proveedor para '{fila['nombre']}'")
            if proveedor == sucursal_id:
                conn.close()
                return err(f"'{fila['nombre']}' es de tu propia sucursal; no puede pedirse a ti mismo")
            items.append((prod_id, fila["nombre"], cantidad,
                          proveedor, fila["unidad"] or "unidad"))
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
        for prod_id, nombre, cantidad, proveedor, unidad in items:
            conn.execute("""
                INSERT INTO pedido_detalle (pedido_id, producto_id, producto_nombre, cantidad, destino_id, unidad)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (pedido_id, prod_id, nombre, cantidad, proveedor, unidad))
        conn.commit()
        conn.close()
        registrar_auditoria("Pedido registrado", f"{nro} de sucursal ID {sucursal_id}")
        return ok({"id": pedido_id, "nro_ticket": nro}, message=f"Pedido {nro} registrado")

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
    es_central = False
    if sid:
        f = conn.execute("SELECT principal FROM sucursales WHERE id = ?", (sid,)).fetchone()
        es_central = bool(f and f["principal"])
    if not es_superadmin() and sid:
        if session.get("rol") == "encargado" and not es_central:
            # Sucursal filial: ve sus pedidos (los que pide y los que le piden)
            q += (" AND (p.sucursal_id = ? OR p.destino_id = ? OR "
                  "EXISTS (SELECT 1 FROM pedido_detalle d4 WHERE d4.pedido_id = p.id AND d4.destino_id = ?))")
            params += [sid, sid, sid]
        # Administradores y encargados de almacén principal ven todos los pedidos
    if estado in _ESTADOS:
        q += " AND p.estado = ?"
        params.append(estado)
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
    count_q = "SELECT COUNT(*) AS c FROM (" + q + ") AS sub"
    total = conn.execute(count_q, params).fetchone()["c"]
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
    detalle = conn.execute("SELECT * FROM pedido_detalle WHERE pedido_id = ?", (pedido_id,)).fetchall()
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
            "costo": d["costo_unitario"] or 0,
            "subtotal": round((d["costo_unitario"] or 0) * d["cantidad"], 2),
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
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if not pedido:
        conn.close()
        return err("Pedido no encontrado", 404)
    # Cambian el estado: admins/superadmin y encargados de almacén principal SOLO
    # de los pedidos destinados a SU sucursal; encargados filiales de las peticiones
    # que les llegan (destino = su sucursal) o que ellos mismos pidieron.
    sid = sucursal_actual()
    sid_op = sucursal_operativa()
    puede = False
    if es_gestion() or es_encargado_almacen(conn):
        det = conn.execute("SELECT destino_id FROM pedido_detalle WHERE pedido_id = ?",
                           (pedido_id,)).fetchall()
        puede = bool(det) and any((d["destino_id"] or pedido["destino_id"]) == sid_op
                                  for d in det)
    elif session.get("rol") == "encargado" and sid:
        det = conn.execute("SELECT destino_id FROM pedido_detalle WHERE pedido_id = ?",
                           (pedido_id,)).fetchall()
        es_proveedor = any((d["destino_id"] or pedido["destino_id"]) == sid for d in det)
        puede = pedido["sucursal_id"] == sid or es_proveedor
    if not puede:
        conn.close()
        return err("No tienes permisos para esta acción", 403)
    data = request.get_json() or {}
    estado = data.get("estado", "")
    if estado not in _ESTADOS:
        conn.close()
        return err("Estado inválido")
    conn.execute("UPDATE pedidos SET estado = ? WHERE id = ?", (estado, pedido_id))
    conn.commit()
    conn.close()
    registrar_auditoria("Pedido actualizado", f"{pedido['nro_ticket']} -> {estado}")
    return ok(message=f"Pedido {pedido['nro_ticket']} marcado como {estado}")


@pedidos_bp.route("/api/pedidos/<int:pedido_id>/despachar", methods=["POST"])
@login_requerido
def pedido_despachar(pedido_id):
    """Convierte un pedido pendiente en repartos reales: uno por cada proveedor.
    Puede despachar un admin/superadmin o un encargado de almacén principal."""
    conn = get_conn()
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if not pedido:
        conn.close()
        return err("Pedido no encontrado", 404)
    if not (es_gestion() or es_encargado_almacen(conn)):
        conn.close()
        return err("No tienes permisos para despachar pedidos", 403)
    # Admins/almacén despachan SOLO pedidos destinados a su propia sucursal.
    sid_op = sucursal_operativa()
    det_prov = conn.execute("SELECT destino_id FROM pedido_detalle WHERE pedido_id = ?",
                            (pedido_id,)).fetchall()
    if not any((d["destino_id"] or pedido["destino_id"]) == sid_op for d in det_prov):
        conn.close()
        return err("Solo puedes despachar pedidos destinados a tu almacén", 403)
    if pedido["estado"] != "pendiente":
        conn.close()
        return err("Solo se pueden despachar pedidos en estado 'pendiente'")
    detalle = conn.execute("SELECT * FROM pedido_detalle WHERE pedido_id = ?", (pedido_id,)).fetchall()
    if not detalle:
        conn.close()
        return err("El pedido no tiene productos")

    # Agrupar líneas por proveedor (cada proveedor genera su propio reparto)
    grupos = {}
    for d in detalle:
        origen_id = d["destino_id"] or pedido["destino_id"]
        if not origen_id:
            conn.close()
            return err("Una línea del pedido no tiene proveedor asignado")
        if origen_id == pedido["sucursal_id"]:
            conn.close()
            return err("El origen no puede ser igual al destino del pedido")
        grupos.setdefault(origen_id, []).append(d)

    suc = conn.execute("SELECT nombre FROM sucursales WHERE id = ?", (pedido["sucursal_id"],)).fetchone()
    if not suc:
        conn.close()
        return err("Sucursal solicitante no encontrada")

    # Validar stock en cada proveedor antes de despachar (evita stock negativo)
    for origen_id, items in grupos.items():
        org = conn.execute("SELECT nombre FROM sucursales WHERE id = ?", (origen_id,)).fetchone()
        if not org:
            conn.close()
            return err(f"Proveedor {origen_id} no encontrado")
        for d in items:
            stock = stock_actual(conn, d["producto_id"], origen_id)
            if stock < d["cantidad"]:
                conn.close()
                return err(f"Stock insuficiente de {d['producto_nombre']} en {org['nombre']}. "
                           f"Disponible: {stock}")

    conn.rollback()  # descartar transacción de lectura implícita
    total = 0.0
    reparto_ids = []
    for origen_id, items in grupos.items():
        cur = conn.execute(
            "INSERT INTO repartos (fecha, sucursal_id, origen_sucursal_id, total, usuario, nota, pedido_id) "
            "VALUES (?, ?, ?, 0, ?, ?, ?)",
            (pedido["fecha"], pedido["sucursal_id"], origen_id,
             session.get("usuario", ""), f"Despacho del pedido {pedido['nro_ticket']}", pedido_id))
        reparto_id = cur.lastrowid
        reparto_ids.append(reparto_id)
        subtotal_reparto = 0.0
        for d in items:
            costo = 0.0
            prod = conn.execute("SELECT costo_promedio FROM productos WHERE id = ?", (d["producto_id"],)).fetchone()
            if prod and prod["costo_promedio"]:
                costo = float(prod["costo_promedio"])
            subtotal = d["cantidad"] * costo
            subtotal_reparto += subtotal
            total += subtotal
            conn.execute("""
                INSERT INTO reparto_detalle (reparto_id, producto_id, producto_nombre, cantidad, costo_unitario, subtotal)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (reparto_id, d["producto_id"], d["producto_nombre"], d["cantidad"], costo, round(subtotal, 2)))
            registrar_movimiento(conn, d["producto_id"], "salida", d["cantidad"], costo, pedido["fecha"],
                                 f"Despacho pedido {pedido['nro_ticket']}", session.get("usuario", ""), origen_id)
            registrar_movimiento(conn, d["producto_id"], "entrada", d["cantidad"], costo, pedido["fecha"],
                                 f"Recepción pedido {pedido['nro_ticket']}", session.get("usuario", ""),
                                 pedido["sucursal_id"])
        conn.execute("UPDATE repartos SET total = ? WHERE id = ?", (round(subtotal_reparto, 2), reparto_id))
    conn.execute("UPDATE pedidos SET estado = 'despachado', total = ? WHERE id = ?", (round(total, 2), pedido_id))
    conn.commit()
    conn.close()
    registrar_auditoria("Pedido despachado",
                        f"{pedido['nro_ticket']} -> repartos #{','.join(map(str, reparto_ids))} (Bs {round(total, 2)})")
    return ok({"reparto_ids": reparto_ids, "reparto_id": reparto_ids[0] if reparto_ids else None,
               "total": round(total, 2)},
              message=f"Pedido {pedido['nro_ticket']} despachado como repartos #{','.join(map(str, reparto_ids))}")


@pedidos_bp.route("/api/pedidos/bandeja", methods=["GET"])
@login_requerido
def pedidos_bandeja():
    """Pedidos pendientes agrupados por sucursal para la bandeja de llegada.

    Todo usuario con sucursal asignada ve únicamente los pedidos en los que su
    sucursal es el destino (destino_id = su sucursal), ya sea un almacén
    principal, América/Simón López o cualquier otra sucursal que abastezca.
    Solo un admin/superadmin sin sucursal asignada consolida todos los pendientes."""
    conn = get_conn()
    sid = sucursal_actual()
    where = "WHERE 1=1"
    params = []
    # Bandeja de «pedidos que me realizaron»: cada usuario ve únicamente los
    # pedidos en los que SU sucursal es el destino (las líneas que le piden a él),
    # ya sea un almacén principal, América/Simón López o cualquier sucursal que
    # abastezca. Solo un admin/superadmin sin sucursal asignada consolida todo.
    if sid:
        where += (" AND EXISTS (SELECT 1 FROM pedido_detalle dd "
                  "WHERE dd.pedido_id = p.id AND dd.destino_id = ?)")
        params.append(sid)
    elif not es_gestion():
        conn.close()
        return ok([])
    # Reseteo diario: por defecto solo los pedidos del día actual. El filtro
    # desde/hasta permite ver días anteriores (mismo patrón que el resto del sistema).
    hoy = datetime.now().strftime("%Y-%m-%d")
    desde = request.args.get("desde", "") or hoy
    hasta = request.args.get("hasta", "") or hoy
    where += " AND date(p.fecha) >= date(?)"
    params.append(desde)
    where += " AND date(p.fecha) <= date(?)"
    params.append(hasta)
    rows = conn.execute("""
        SELECT p.id, p.nro_ticket, p.fecha, p.estado, p.nota, p.usuario, p.sucursal_id,
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
                   d.destino_id, s.nombre AS destino_nombre
            FROM pedido_detalle d LEFT JOIN sucursales s ON s.id = d.destino_id
            WHERE d.pedido_id IN ({marks})
            ORDER BY d.pedido_id, d.producto_nombre
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