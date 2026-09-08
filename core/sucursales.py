import pymysql
from datetime import datetime

from flask import Blueprint, request, session

from database import get_conn
from .util import ok, err, login_requerido, rol_requerido, registrar_auditoria, registrar_movimiento, stock_actual, ok_paginado, paginar_params, sucursal_actual, sucursal_operativa, clausula_sucursal, es_gestion

sucursales_bp = Blueprint("sucursales", __name__)


# --------------------------------------------------------------------------
# Sucursales (listado para todos; gestión solo admin)
# --------------------------------------------------------------------------
@sucursales_bp.route("/api/sucursales", methods=["GET", "POST"])
@login_requerido
def sucursales():
    conn = get_conn()
    if request.method == "POST":
        if session.get("rol") != "superadmin":
            conn.close()
            return err("No tienes permisos para esta acción", 403)
        data = request.get_json() or {}
        nombre = (data.get("nombre") or "").strip()
        if not nombre:
            conn.close()
            return err("El nombre es obligatorio")
        principal = 1 if data.get("principal") else 0
        try:
            cur = conn.execute(
                "INSERT INTO sucursales (nombre, direccion, principal) VALUES (?, ?, ?)",
                (nombre, data.get("direccion", ""), principal))
            new_id = cur.lastrowid
            # Cada sucursal tiene su propio almacén (mantener sincronizados)
            conn.execute("INSERT INTO almacenes (nombre, ubicacion, sucursal_id) VALUES (?, ?, ?)",
                         (nombre, data.get("direccion", ""), new_id))
            conn.commit()
            conn.close()
            registrar_auditoria("Sucursal creada", nombre)
            return ok({"id": new_id}, message="Sucursal creada")
        except pymysql.err.IntegrityError:
            conn.close()
            return err("Ya existe una sucursal con ese nombre")
    rows = conn.execute("""
        SELECT s.*,
               (SELECT COUNT(*) FROM repartos r WHERE r.sucursal_id = s.id) AS num_repartos,
               (SELECT COALESCE(SUM(r.total), 0) FROM repartos r WHERE r.sucursal_id = s.id) AS total_repartido
        FROM sucursales s
        ORDER BY s.principal DESC, s.nombre
    """).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@sucursales_bp.route("/api/sucursales/<int:suc_id>", methods=["PUT", "DELETE"])
@login_requerido
@rol_requerido("superadmin")
def sucursal(suc_id):
    conn = get_conn()
    if request.method == "DELETE":
        # No permitir eliminar la sucursal del usuario logueado
        if sucursal_actual() == suc_id:
            conn.close()
            return err("No se puede eliminar la sucursal a la que perteneces")
        # Verificar dependencias que impedirían el borrado (evita error 500 del servidor)
        checks = [
            ("stock", "SELECT COUNT(*) c FROM stock WHERE sucursal_id = ?", "tiene stock de productos"),
            ("movimientos", "SELECT COUNT(*) c FROM movimientos WHERE sucursal_id = ?", "tiene movimientos registrados"),
            ("usuarios", "SELECT COUNT(*) c FROM usuarios WHERE sucursal_id = ?", "tiene usuarios asignados"),
            ("pedidos", "SELECT COUNT(*) c FROM pedidos WHERE sucursal_id = ?", "tiene pedidos asociados"),
            ("repartos", "SELECT COUNT(*) c FROM repartos WHERE sucursal_id = ?", "tiene repartos registrados"),
            ("repartos_origen", "SELECT COUNT(*) c FROM repartos WHERE origen_sucursal_id = ?", "es origen de repartos"),
            ("ventas", "SELECT COUNT(*) c FROM ventas WHERE sucursal_id = ?", "tiene ventas registradas"),
            ("gastos", "SELECT COUNT(*) c FROM gastos WHERE sucursal_id = ?", "tiene gastos registrados"),
        ]
        for _tabla, consulta, motivo in checks:
            if conn.execute(consulta, (suc_id,)).fetchone()["c"]:
                conn.close()
                return err(f"No se puede eliminar la sucursal: {motivo}")
        # Si la sucursal tiene un almacén propio, eliminarlo (si tiene productos asociados, bloquear)
        alm = conn.execute("SELECT id FROM almacenes WHERE sucursal_id = ?", (suc_id,)).fetchone()
        if alm:
            if conn.execute("SELECT COUNT(*) c FROM productos WHERE almacen_id = ?", (alm["id"],)).fetchone()["c"]:
                conn.close()
                return err("No se puede eliminar la sucursal: su almacén tiene productos asociados")
            conn.execute("DELETE FROM almacenes WHERE id = ?", (alm["id"],))
        conn.execute("DELETE FROM sucursales WHERE id = ?", (suc_id,))
        conn.commit()
        conn.close()
        registrar_auditoria("Sucursal eliminada", f"Sucursal ID {suc_id}")
        return ok(message="Sucursal eliminada")
    data = request.get_json() or {}
    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        conn.close()
        return err("El nombre es obligatorio")
    conn.execute("UPDATE sucursales SET nombre = ?, direccion = ?, principal = ? WHERE id = ?",
                 (nombre, data.get("direccion", ""), 1 if data.get("principal") else 0, suc_id))
    conn.execute("UPDATE almacenes SET nombre = ? WHERE sucursal_id = ?", (nombre, suc_id))
    conn.commit()
    conn.close()
    registrar_auditoria("Sucursal actualizada", nombre)
    return ok(message="Sucursal actualizada")


# --------------------------------------------------------------------------
# Repartos a sucursales
# --------------------------------------------------------------------------
@sucursales_bp.route("/api/repartos", methods=["GET", "POST"])
@login_requerido
def repartos():
    conn = get_conn()
    if request.method == "POST":
        rol = session.get("rol")
        if rol not in ("superadmin", "admin", "encargado"):
            conn.close()
            return err("No tienes permisos para esta acción", 403)
        data = request.get_json() or {}
        fecha = data.get("fecha") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sucursal_id = data.get("sucursal_id")
        # El origen SIEMPRE es la sucursal del usuario; superadmin -> su Almacén Principal 1
        origen_id = sucursal_operativa() if rol != "superadmin" else (
            data.get("origen_sucursal_id") or sucursal_operativa())
        nota = data.get("nota", "")
        detalle = data.get("detalle", [])
        if not sucursal_id:
            conn.close()
            return err("Debes seleccionar una sucursal de destino")
        if not origen_id:
            conn.close()
            return err("Debes seleccionar una sucursal de origen")
        if origen_id == sucursal_id:
            conn.close()
            return err("El origen y el destino no pueden ser iguales")
        if not detalle:
            conn.close()
            return err("El reparto no tiene productos")
        suc = conn.execute("SELECT nombre FROM sucursales WHERE id = ?", (sucursal_id,)).fetchone()
        org = conn.execute("SELECT nombre FROM sucursales WHERE id = ?", (origen_id,)).fetchone()
        if not suc or not org:
            conn.close()
            return err("Sucursal no encontrada")

        total = 0.0
        items_validados = []
        for item in detalle:
            prod_id = item.get("producto_id")
            cantidad = float(item.get("cantidad", 0) or 0)
            costo = float(item.get("costo_unitario", 0) or 0)
            if cantidad <= 0:
                conn.close()
                return err("La cantidad debe ser mayor a cero")
            fila = conn.execute("SELECT id, nombre FROM productos WHERE id = ? AND activo = 1",
                                (prod_id,)).fetchone()
            if not fila:
                conn.close()
                return err("Producto no encontrado")
            stock = stock_actual(conn, prod_id, origen_id)
            if stock < cantidad:
                conn.close()
                return err(f"Stock insuficiente de {fila['nombre']} en {org['nombre']}. Disponible: {stock}")
            items_validados.append((prod_id, fila["nombre"], cantidad, costo, cantidad * costo))
            total += cantidad * costo

        cur = conn.execute(
            "INSERT INTO repartos (fecha, sucursal_id, origen_sucursal_id, total, usuario, nota) VALUES (?, ?, ?, ?, ?, ?)",
            (fecha, sucursal_id, origen_id, round(total, 2), session.get("usuario", ""), nota))
        reparto_id = cur.lastrowid

        for prod_id, nombre, cantidad, costo, subtotal in items_validados:
            conn.execute("""
                INSERT INTO reparto_detalle (reparto_id, producto_id, producto_nombre, cantidad, costo_unitario, subtotal)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (reparto_id, prod_id, nombre, cantidad, costo, round(subtotal, 2)))
            registrar_movimiento(conn, prod_id, "salida", cantidad, costo, fecha,
                                 f"Reparto #{reparto_id} de {org['nombre']} a {suc['nombre']}",
                                 session.get("usuario", ""), origen_id)
            registrar_movimiento(conn, prod_id, "entrada", cantidad, costo, fecha,
                                 f"Reparto #{reparto_id} recibido de {org['nombre']}",
                                 session.get("usuario", ""), sucursal_id)
        conn.commit()
        conn.close()
        registrar_auditoria("Reparto registrado",
                            f"Reparto #{reparto_id} de {org['nombre']} a {suc['nombre']} por Bs {round(total, 2)}")
        return ok({"id": reparto_id, "total": round(total, 2)}, message="Reparto registrado")

    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    filtro = request.args.get("filtro", "").strip()
    sid_filtro = request.args.get("sucursal_id", "")
    q = """
        SELECT r.*, s.nombre AS sucursal_nombre, o.nombre AS origen_nombre,
               (SELECT COUNT(*) FROM reparto_detalle d WHERE d.reparto_id = r.id) AS num_items,
               (SELECT GROUP_CONCAT(CONCAT(d.producto_nombre, ' (', d.cantidad, ')') SEPARATOR ', ')
                FROM reparto_detalle d WHERE d.reparto_id = r.id) AS items_detalle
        FROM repartos r
        JOIN sucursales s ON s.id = r.sucursal_id
        LEFT JOIN sucursales o ON o.id = r.origen_sucursal_id
        WHERE 1=1
    """
    params = []
    # Admin/superadmin ven los repartos de TODAS las sucursales (filtrables por sucursal_id).
    if es_gestion() and sid_filtro:
        q += " AND r.sucursal_id = ?"
        params.append(int(sid_filtro))
    elif not es_gestion():
        cls, cls_params = clausula_sucursal("r.sucursal_id")
        if cls:
            q += " AND (" + cls[5:] + " OR r.origen_sucursal_id = ?)"
            params += cls_params + cls_params
    if desde:
        q += " AND date(r.fecha) >= date(?)"
        params.append(desde)
    if hasta:
        q += " AND date(r.fecha) <= date(?)"
        params.append(hasta)
    if filtro:
        q += " AND (s.nombre LIKE ? OR r.usuario LIKE ? OR r.nota LIKE ? OR CAST(r.id AS CHAR) LIKE ?)"
        params += [f"%{filtro}%"] * 4
    count_q = "SELECT COUNT(*) AS c FROM (" + q.replace("r.*, s.nombre AS sucursal_nombre,", "1,").replace("o.nombre AS origen_nombre,", "").replace("(SELECT COUNT(*) FROM reparto_detalle d WHERE d.reparto_id = r.id) AS num_items,", "").replace("(SELECT GROUP_CONCAT(CONCAT(d.producto_nombre, ' (', d.cantidad, ')') SEPARATOR ', ') FROM reparto_detalle d WHERE d.reparto_id = r.id) AS items_detalle", "") + ") AS sub"
    total = conn.execute(count_q, params).fetchone()["c"]
    offset, limit, pagina, por_pagina = paginar_params()
    q += " ORDER BY r.fecha DESC, r.id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok_paginado([dict(r) for r in rows], total, pagina, por_pagina)


@sucursales_bp.route("/api/repartos/<int:reparto_id>", methods=["GET"])
@login_requerido
def reparto_detalle(reparto_id):
    conn = get_conn()
    reparto = conn.execute(
        "SELECT r.*, s.nombre AS sucursal_nombre FROM repartos r JOIN sucursales s ON s.id = r.sucursal_id WHERE r.id = ?",
        (reparto_id,)).fetchone()
    if not reparto:
        conn.close()
        return err("Reparto no encontrado", 404)
    detalle = conn.execute(
        "SELECT * FROM reparto_detalle WHERE reparto_id = ?", (reparto_id,)).fetchall()
    conn.close()
    return ok({"reparto": dict(reparto), "detalle": [dict(r) for r in detalle]})


@sucursales_bp.route("/api/repartos/<int:reparto_id>", methods=["DELETE"])
@login_requerido
def reparto_eliminar(reparto_id):
    if session.get("rol") != "superadmin":
        return err("No tienes permisos para esta acción", 403)
    conn = get_conn()
    reparto = conn.execute(
        "SELECT r.*, s.nombre AS sucursal_nombre, so.nombre AS origen_nombre "
        "FROM repartos r JOIN sucursales s ON s.id = r.sucursal_id "
        "LEFT JOIN sucursales so ON so.id = r.origen_sucursal_id WHERE r.id = ?",
        (reparto_id,)).fetchone()
    if not reparto:
        conn.close()
        return err("Reparto no encontrado", 404)
    detalle = conn.execute("SELECT * FROM reparto_detalle WHERE reparto_id = ?", (reparto_id,)).fetchall()
    # Un reparto sin sucursal de origen registrada (p. ej. heredado de la migración)
    # no puede revertirse: no sabemos a qué sucursal devolver el stock.
    if not reparto["origen_sucursal_id"]:
        conn.close()
        return err("No se puede anular el reparto #%s: no tiene sucursal de origen registrada. "
                   "El stock salió de una sucursal desconocida." % reparto_id, 400)
    fh = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for d in detalle:
        registrar_movimiento(conn, d["producto_id"], "entrada", d["cantidad"], d["costo_unitario"],
                             fh,
                             f"Devolución por anulación de reparto #{reparto_id} a {reparto['sucursal_nombre']}",
                             session.get("usuario", ""), reparto["origen_sucursal_id"])
        registrar_movimiento(conn, d["producto_id"], "salida", d["cantidad"], d["costo_unitario"],
                             fh,
                             f"Descuento por anulación de reparto #{reparto_id} a {reparto['sucursal_nombre']}",
                             session.get("usuario", ""), reparto["sucursal_id"])
    conn.execute("DELETE FROM reparto_detalle WHERE reparto_id = ?", (reparto_id,))
    conn.execute("DELETE FROM repartos WHERE id = ?", (reparto_id,))
    conn.commit()
    conn.close()
    registrar_auditoria("Reparto anulado", f"Reparto #{reparto_id} de {reparto['sucursal_nombre']}")
    return ok(message="Reparto anulado y stock repuesto")
