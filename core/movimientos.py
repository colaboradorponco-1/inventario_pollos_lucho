from datetime import datetime

from flask import Blueprint, request, session

from database import get_conn
from .util import (ok, err, login_requerido, registrar_auditoria, registrar_movimiento,
                   stock_actual, responder_excel, ok_paginado, paginar_params,
                   sucursal_actual, sucursal_operativa, clausula_sucursal, es_gestion)

movimientos_bp = Blueprint("movimientos", __name__)


@movimientos_bp.route("/api/movimientos", methods=["GET", "POST"])
@login_requerido
def movimientos():
    conn = get_conn()
    if request.method == "POST":
        data = request.get_json()
        prod_id = data["producto_id"]
        tipo = data["tipo"]
        cantidad = float(data.get("cantidad", 0) or 0)
        if cantidad <= 0:
            conn.close()
            return err("La cantidad debe ser mayor a cero")
        precio = float(data.get("precio_unitario", 0) or 0)
        fecha = data.get("fecha") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        nota = data.get("nota", "")
        proveedor_id = data.get("proveedor_id")
        # Admin/superadmin pueden registrar movimientos en CUALQUIER sucursal;
        # encargado SIEMPRE en la suya.
        sid = sucursal_operativa()
        if es_gestion():
            sid_payload = data.get("sucursal_id") or data.get("almacen_id")
            if sid_payload:
                sid = int(sid_payload)
                existe_suc = conn.execute("SELECT id FROM sucursales WHERE id = ?", (sid,)).fetchone()
                if not existe_suc:
                    conn.close()
                    return err("Sucursal no válida")
        if not sid:
            conn.close()
            return err("No se puede registrar el movimiento sin una sucursal definida")
        if proveedor_id:
            prov = conn.execute("SELECT id, sucursal_id FROM proveedores WHERE id = ?", (int(proveedor_id),)).fetchone()
            if not prov:
                conn.close()
                return err("Proveedor no válido")
            if prov["sucursal_id"] and prov["sucursal_id"] != sid:
                conn.close()
                return err("El proveedor no pertenece a esta sucursal")
            proveedor_id = prov["id"]

        prod = conn.execute("SELECT sucursal_id FROM productos WHERE id = ?", (prod_id,)).fetchone()
        if not prod:
            conn.close()
            return err("Producto no encontrado")
        if prod["sucursal_id"] != sid:
            conn.close()
            return err("El producto no pertenece a esta sucursal")

        stock_actual_val = stock_actual(conn, prod_id, sid)

        if tipo == "salida" and stock_actual_val < cantidad:
            conn.close()
            return err(f"Stock insuficiente. Disponible: {stock_actual_val}")

        registrar_movimiento(conn, prod_id, tipo, cantidad, precio, fecha,
                             nota, session.get("usuario", ""), proveedor_id=proveedor_id,
                             sucursal_id=sid, vencimiento=data.get("vencimiento"))
        conn.commit()
        conn.close()
        registrar_auditoria("Movimiento registrado",
                            f"{tipo.capitalize()} {cantidad} de producto ID {prod_id}")
        return ok(message="Movimiento registrado")

    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    tipo = request.args.get("tipo", "")
    filtro = request.args.get("filtro", "").strip()
    sid_filtro = request.args.get("sucursal_id", "")
    q = """
        SELECT m.*, p.nombre AS producto_nombre, p.unidad, s.nombre AS sucursal_nombre,
               pr.nombre AS proveedor_nombre
        FROM movimientos m
        JOIN productos p ON p.id = m.producto_id
        LEFT JOIN sucursales s ON s.id = m.sucursal_id
        LEFT JOIN proveedores pr ON pr.id = m.proveedor_id
        WHERE 1=1
    """
    params = []
    # Admin/superadmin ven TODAS las sucursales (filtrables por sucursal_id).
    # Encargado SIEMPRE solo su almacén.
    if es_gestion():
        if sid_filtro:
            q += " AND m.sucursal_id = ?"
            params.append(int(sid_filtro))
    else:
        cls, cls_params = clausula_sucursal("m.sucursal_id")
        if cls:
            q += cls
            params += cls_params
    if desde:
        q += " AND date(m.fecha) >= date(?)"
        params.append(desde)
    if hasta:
        q += " AND date(m.fecha) <= date(?)"
        params.append(hasta)
    if tipo:
        q += " AND m.tipo = ?"
        params.append(tipo)
    if filtro:
        q += " AND (p.nombre LIKE ? OR m.nota LIKE ? OR m.usuario LIKE ? OR pr.nombre LIKE ?)"
        params += [f"%{filtro}%"] * 4
    count_q = "SELECT COUNT(*) AS c FROM (" + q.replace("m.*, p.nombre AS producto_nombre, p.unidad, s.nombre AS sucursal_nombre, pr.nombre AS proveedor_nombre", "1") + ") AS sub"
    total = conn.execute(count_q, params).fetchone()["c"]
    offset, limit, pagina, por_pagina = paginar_params()
    q += " ORDER BY m.fecha DESC, m.id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok_paginado([dict(r) for r in rows], total, pagina, por_pagina)


@movimientos_bp.route("/api/movimientos/lote", methods=["POST"])
@login_requerido
def movimientos_lote():
    data = request.get_json() or {}
    tipo = data.get("tipo", "entrada")
    items = data.get("items", [])
    nota = data.get("nota", "")
    fecha = data.get("fecha") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    proveedor_id = data.get("proveedor_id")
    if tipo not in ("entrada", "salida"):
        return err("Tipo inválido")
    if not items:
        return err("No hay productos para registrar")
    conn = get_conn()
    if proveedor_id:
        prov = conn.execute("SELECT id, sucursal_id FROM proveedores WHERE id = ?", (int(proveedor_id),)).fetchone()
        if not prov:
            conn.close()
            return err("Proveedor no válido")
        if prov["sucursal_id"] and prov["sucursal_id"] != sucursal_operativa():
            conn.close()
            return err("El proveedor no pertenece a esta sucursal")
        proveedor_id = prov["id"]
    errores = []
    registrados = 0
    sid = sucursal_operativa()
    for item in items:
        prod_id = item.get("producto_id")
        cantidad = float(item.get("cantidad", 0) or 0)
        if cantidad <= 0 or not prod_id:
            continue
        prod = conn.execute("SELECT nombre, sucursal_id FROM productos WHERE id=?", (prod_id,)).fetchone()
        if not prod:
            errores.append(f"Producto {prod_id} no encontrado")
            continue
        if prod["sucursal_id"] != sid:
            errores.append(f"{prod['nombre']}: el producto no pertenece a esta sucursal")
            continue
        stock_val = stock_actual(conn, prod_id, sid)
        if tipo == "salida" and stock_val < cantidad:
            errores.append(f"{prod['nombre']}: stock insuficiente ({stock_val})")
            continue
        registrar_movimiento(conn, prod_id, tipo, cantidad,
                             float(item.get("precio_unitario", 0) or 0),
                             fecha, nota, session.get("usuario", ""),
                             proveedor_id=proveedor_id, sucursal_id=sid,
                             vencimiento=item.get("vencimiento"))
        registrados += 1
    conn.commit()
    conn.close()
    msg = f"{registrados} movimiento(s) registrado(s)"
    if errores:
        msg += ". Errores: " + "; ".join(errores)
    return ok({"registrados": registrados, "errores": errores}, message=msg)


@movimientos_bp.route("/api/gastos", methods=["GET", "POST"])
@login_requerido
def gastos():
    conn = get_conn()
    if request.method == "POST":
        # Encargado registra gastos de SU sucursal; admin/superadmin de la suya.
        data = request.get_json()
        conn.execute("""
            INSERT INTO gastos (categoria, descripcion, monto, fecha, proveedor_id, sucursal_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (data["categoria"].strip(), data.get("descripcion", ""),
              float(data["monto"]), data.get("fecha") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              data.get("proveedor_id"), sucursal_operativa()))
        conn.commit()
        conn.close()
        return ok(message="Gasto registrado")

    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    filtro = request.args.get("filtro", "").strip()
    sid_filtro = request.args.get("sucursal_id", "")
    q = """SELECT g.*, p.nombre AS proveedor_nombre, s.nombre AS sucursal_nombre
           FROM gastos g
           LEFT JOIN proveedores p ON p.id = g.proveedor_id
           LEFT JOIN sucursales s ON s.id = g.sucursal_id WHERE 1=1"""
    params = []
    # Admin/superadmin ven todas las sucursales (filtrables por sucursal_id).
    if es_gestion() and sid_filtro:
        q += " AND g.sucursal_id = ?"
        params.append(int(sid_filtro))
    elif not es_gestion():
        cls, cls_params = clausula_sucursal("g.sucursal_id")
        if cls:
            q += cls
            params += cls_params
    if desde:
        q += " AND date(g.fecha) >= date(?)"
        params.append(desde)
    if hasta:
        q += " AND date(g.fecha) <= date(?)"
        params.append(hasta)
    if filtro:
        q += " AND (g.categoria LIKE ? OR g.descripcion LIKE ? OR p.nombre LIKE ?)"
        params += [f"%{filtro}%"] * 3
    count_q = "SELECT COUNT(*) AS c FROM (" + q.replace("g.*, p.nombre AS proveedor_nombre, s.nombre AS sucursal_nombre", "g.id") + ") AS sub"
    total = conn.execute(count_q, params).fetchone()["c"]
    offset, limit, pagina, por_pagina = paginar_params()
    q += " ORDER BY g.fecha DESC, g.id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok_paginado([dict(r) for r in rows], total, pagina, por_pagina)


@movimientos_bp.route("/api/gastos/<int:gasto_id>", methods=["PUT", "DELETE"])
@login_requerido
def gasto(gasto_id):
    conn = get_conn()
    fila = conn.execute("SELECT * FROM gastos WHERE id = ?", (gasto_id,)).fetchone()
    if not fila:
        conn.close()
        return err("Gasto no encontrado", 404)
    # Encargado solo puede gestionar gastos de su sucursal.
    if not es_gestion() and fila["sucursal_id"] != sucursal_actual():
        conn.close()
        return err("Solo puedes gestionar gastos de tu sucursal", 403)
    if request.method == "DELETE":
        conn.execute("DELETE FROM gastos WHERE id = ?", (gasto_id,))
        conn.commit()
        conn.close()
        return ok(message="Gasto eliminado")
    data = request.get_json()
    conn.execute("""
        UPDATE gastos SET categoria=?, descripcion=?, monto=?, fecha=?, proveedor_id=? WHERE id=?
    """, (data["categoria"].strip(), data.get("descripcion", ""),
          float(data["monto"]), data.get("fecha") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
          data.get("proveedor_id"), gasto_id))
    conn.commit()
    conn.close()
    registrar_auditoria("Gasto actualizado", f"Gasto ID {gasto_id}")
    return ok(message="Gasto actualizado")


@movimientos_bp.route("/api/exportar/movimientos")
@login_requerido
def exportar_movimientos():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    tipo = request.args.get("tipo", "")
    filtro = request.args.get("filtro", "").strip()
    sid_filtro = request.args.get("sucursal_id", "")
    conn = get_conn()
    q = """
        SELECT m.fecha, p.nombre AS producto_nombre, p.unidad, m.tipo,
               m.cantidad, m.precio_unitario, s.nombre AS sucursal_nombre,
               pr.nombre AS proveedor_nombre, m.nota, m.usuario
        FROM movimientos m
        JOIN productos p ON p.id = m.producto_id
        LEFT JOIN sucursales s ON s.id = m.sucursal_id
        LEFT JOIN proveedores pr ON pr.id = m.proveedor_id
        WHERE 1=1
    """
    params = []
    if es_gestion():
        if sid_filtro:
            q += " AND m.sucursal_id = ?"
            params.append(int(sid_filtro))
    else:
        cls, cls_params = clausula_sucursal("m.sucursal_id")
        if cls:
            q += cls
            params += cls_params
    if desde:
        q += " AND date(m.fecha) >= date(?)"
        params.append(desde)
    if hasta:
        q += " AND date(m.fecha) <= date(?)"
        params.append(hasta)
    if tipo:
        q += " AND m.tipo = ?"
        params.append(tipo)
    if filtro:
        q += " AND (p.nombre LIKE ? OR m.nota LIKE ? OR m.usuario LIKE ? OR pr.nombre LIKE ?)"
        params += [f"%{filtro}%"] * 4
    q += " ORDER BY m.fecha DESC, m.id DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    filas = [(r["fecha"], r["producto_nombre"], r["unidad"],
              "Entrada" if r["tipo"] == "entrada" else "Salida",
              r["cantidad"], round(r["precio_unitario"] or 0, 2),
              r["sucursal_nombre"] or "", r["proveedor_nombre"] or "", r["nota"] or "", r["usuario"] or "") for r in rows]
    return responder_excel("movimientos.xlsx",
                         ["Fecha y hora", "Producto", "Unidad", "Tipo", "Cantidad",
                          "Precio (Bs)", "Sucursal", "Proveedor", "Nota", "Usuario"], filas,
                         [20, 25, 10, 10, 10, 13, 15, 20, 25, 12])


@movimientos_bp.route("/api/exportar/gastos")
@login_requerido
def exportar_gastos():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    filtro = request.args.get("filtro", "").strip()
    sid_filtro = request.args.get("sucursal_id", "")
    conn = get_conn()
    q = """SELECT g.fecha, g.categoria, g.descripcion, g.monto, s.nombre AS sucursal_nombre,
                  p.nombre AS proveedor_nombre
           FROM gastos g
           LEFT JOIN proveedores p ON p.id = g.proveedor_id
           LEFT JOIN sucursales s ON s.id = g.sucursal_id WHERE 1=1"""
    params = []
    if es_gestion() and sid_filtro:
        q += " AND g.sucursal_id = ?"
        params.append(int(sid_filtro))
    elif not es_gestion():
        cls, cls_params = clausula_sucursal("g.sucursal_id")
        if cls:
            q += cls
            params += cls_params
    if desde:
        q += " AND date(g.fecha) >= date(?)"
        params.append(desde)
    if hasta:
        q += " AND date(g.fecha) <= date(?)"
        params.append(hasta)
    if filtro:
        q += " AND (g.categoria LIKE ? OR g.descripcion LIKE ? OR p.nombre LIKE ?)"
        params += [f"%{filtro}%"] * 3
    q += " ORDER BY g.fecha DESC, g.id DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    filas = [(r["fecha"], r["categoria"], r["descripcion"] or "",
              round(r["monto"], 2), r["sucursal_nombre"] or "", r["proveedor_nombre"] or "") for r in rows]
    return responder_excel("gastos.xlsx",
                         ["Fecha y hora", "Categoría", "Descripción", "Monto (Bs)", "Sucursal", "Proveedor"], filas,
                         [20, 18, 30, 15, 15, 20])
