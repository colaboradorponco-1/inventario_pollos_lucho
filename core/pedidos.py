"""Pedidos / tickets por sucursal (Fase 2).

Cada sucursal registra un pedido (ticket) que llega al almacén principal.
Estados: pendiente -> despachado -> cumplido.
Solo admin/superadmin pueden cambiar el estado o despachar un pedido.
"""
from datetime import datetime

from flask import Blueprint, request, session

from database import get_conn
from .util import (ok, err, login_requerido, registrar_auditoria, ok_paginado,
                   paginar_params, sucursal_actual, stock_actual, es_gestion, es_superadmin)

pedidos_bp = Blueprint("pedidos", __name__)

_ESTADOS = ("pendiente", "despachado", "cumplido")


def _nro_ticket(conn):
    fila = conn.execute("SELECT MAX(id) m FROM pedidos").fetchone()["m"] or 0
    return f"TKT-{int(fila) + 1:05d}"


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
        # Sucursal a la que va dirigido el pedido (quién provee)
        destino_id = data.get("destino_id")
        if session.get("rol") == "encargado" and not destino_id:
            conn.close()
            return err("Debes seleccionar la sucursal a la que va el pedido")
        if destino_id:
            dest_fila = conn.execute("SELECT id FROM sucursales WHERE id = ?", (destino_id,)).fetchone()
            if not dest_fila:
                conn.close()
                return err("La sucursal destino no existe")
        else:
            dest_fila = conn.execute("SELECT id FROM sucursales ORDER BY id LIMIT 1").fetchone()
            destino_id = dest_fila["id"] if dest_fila else None
        if not destino_id:
            conn.close()
            return err("Debes indicar la sucursal a la que va el pedido")
        if destino_id == sucursal_id:
            conn.close()
            return err("El pedido no puede dirigirse a la misma sucursal que lo solicita")
        nota = data.get("nota", "")
        fecha = data.get("fecha") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        total = 0.0
        items = []
        for item in detalle:
            prod_id = item.get("producto_id")
            cantidad = float(item.get("cantidad", 0) or 0)
            if cantidad <= 0 or not prod_id:
                continue
            fila = conn.execute("SELECT id, nombre FROM productos WHERE id = ? AND activo = 1",
                                (prod_id,)).fetchone()
            if not fila:
                conn.close()
                return err("Producto no encontrado")
            items.append((prod_id, fila["nombre"], cantidad))
        if not items:
            conn.close()
            return err("El pedido no tiene productos válidos")

        nro = _nro_ticket(conn)
        try:
            cur = conn.execute("""
                INSERT INTO pedidos (nro_ticket, fecha, sucursal_id, destino_id, estado, total, usuario, nota)
                VALUES (?, ?, ?, ?, 'pendiente', 0, ?, ?)
            """, (nro, fecha, sucursal_id, destino_id, session.get("usuario", ""), nota))
        except Exception:
            conn.rollback()
            conn.close()
            return err("Error al registrar el pedido")
        pedido_id = cur.lastrowid
        for prod_id, nombre, cantidad in items:
            conn.execute("""
                INSERT INTO pedido_detalle (pedido_id, producto_id, producto_nombre, cantidad)
                VALUES (?, ?, ?, ?)
            """, (pedido_id, prod_id, nombre, cantidad))
        conn.commit()
        conn.close()
        registrar_auditoria("Pedido registrado", f"{nro} de sucursal ID {sucursal_id}")
        return ok({"id": pedido_id, "nro_ticket": nro}, message=f"Pedido {nro} registrado")

    estado = request.args.get("estado", "").strip()
    filtro = request.args.get("filtro", "").strip()
    q = """
        SELECT p.*, s.nombre AS sucursal_nombre,
               d.nombre AS destino_nombre,
               (SELECT COUNT(*) FROM pedido_detalle d WHERE d.pedido_id = p.id) AS num_items
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
            q += " AND (p.sucursal_id = ? OR p.destino_id = ?)"
            params += [sid, sid]
        # Administradores y encargados de almacén principal ven todos los pedidos
    if estado in _ESTADOS:
        q += " AND p.estado = ?"
        params.append(estado)
    if filtro:
        q += " AND (p.nro_ticket LIKE ? OR s.nombre LIKE ? OR p.nota LIKE ? OR d.nombre LIKE ?)"
        params += [f"%{filtro}%"] * 4
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
    conn.close()
    return ok({"pedido": dict(pedido), "detalle": [dict(r) for r in detalle]})


@pedidos_bp.route("/api/pedidos/<int:pedido_id>/estado", methods=["PUT"])
@login_requerido
def pedido_estado(pedido_id):
    if not es_gestion():
        return err("No tienes permisos para esta acción", 403)
    conn = get_conn()
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if not pedido:
        conn.close()
        return err("Pedido no encontrado", 404)
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
    """Convierte un pedido pendiente en un reparto real del almacén a la sucursal."""
    rol = session.get("rol")
    conn = get_conn()
    pedido = conn.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
    if not pedido:
        conn.close()
        return err("Pedido no encontrado", 404)
    puede = rol in ("admin", "superadmin")
    if not puede:
        puede = rol == "encargado" and sucursal_actual() == pedido.get("destino_id")
    if not puede:
        conn.close()
        return err("Solo puede despachar el pedido la sucursal a la que va dirigido, "
                   "un administrador o un superadministrador", 403)
    if pedido["estado"] != "pendiente":
        conn.close()
        return err("Solo se pueden despachar pedidos en estado 'pendiente'")
    detalle = conn.execute("SELECT * FROM pedido_detalle WHERE pedido_id = ?", (pedido_id,)).fetchall()
    if not detalle:
        conn.close()
        return err("El pedido no tiene productos")

    # Origen: el almacén principal destino del pedido
    from .util import sucursal_operativa
    origen_id = pedido["destino_id"] or sucursal_operativa()
    if not origen_id or origen_id == pedido["sucursal_id"]:
        conn.close()
        return err("El origen no puede ser igual al destino del pedido")
    org = conn.execute("SELECT nombre FROM sucursales WHERE id = ?", (origen_id,)).fetchone()
    suc = conn.execute("SELECT nombre FROM sucursales WHERE id = ?", (pedido["sucursal_id"],)).fetchone()

    # Validar stock del almacén antes de despachar (evita stock negativo)
    for d in detalle:
        stock = stock_actual(conn, d["producto_id"], origen_id)
        if stock < d["cantidad"]:
            conn.close()
            return err(f"Stock insuficiente de {d['producto_nombre']} en {org['nombre']}. "
                       f"Disponible: {stock}")

    # Crear reparto real
    conn.rollback()  # descartar transacción de lectura implícita
    cur = conn.execute(
        "INSERT INTO repartos (fecha, sucursal_id, origen_sucursal_id, total, usuario, nota) "
        "VALUES (?, ?, ?, 0, ?, ?)",
        (pedido["fecha"], pedido["sucursal_id"], origen_id,
         session.get("usuario", ""), f"Despacho del pedido {pedido['nro_ticket']}"))
    reparto_id = cur.lastrowid

    from .util import registrar_movimiento
    total = 0.0
    for d in detalle:
        costo = 0.0
        prod = conn.execute("SELECT costo_promedio FROM productos WHERE id = ?", (d["producto_id"],)).fetchone()
        if prod and prod["costo_promedio"]:
            costo = float(prod["costo_promedio"])
        subtotal = d["cantidad"] * costo
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
    conn.execute("UPDATE pedidos SET estado = 'despachado' WHERE id = ?", (pedido_id,))
    conn.commit()
    conn.close()
    registrar_auditoria("Pedido despachado",
                        f"{pedido['nro_ticket']} -> reparto #{reparto_id} (Bs {round(total, 2)})")
    return ok({"reparto_id": reparto_id, "total": round(total, 2)},
              message=f"Pedido {pedido['nro_ticket']} despachado como reparto #{reparto_id}")
