from datetime import datetime

from flask import Blueprint, request, session

from database import get_conn
from .util import ok, err, login_requerido, registrar_auditoria, registrar_movimiento, stock_actual, responder_excel

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
        almacen_id = data.get("almacen_id")
        nota = data.get("nota", "")

        stock_actual_val = stock_actual(conn, prod_id)

        if tipo == "salida" and stock_actual_val < cantidad:
            conn.close()
            return err(f"Stock insuficiente. Disponible: {stock_actual_val}")

        registrar_movimiento(conn, prod_id, tipo, cantidad, precio, fecha,
                             nota, session.get("usuario", ""), almacen_id)
        conn.commit()
        conn.close()
        registrar_auditoria("Movimiento registrado",
                            f"{tipo.capitalize()} {cantidad} de producto ID {prod_id}")
        return ok(message="Movimiento registrado")

    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    tipo = request.args.get("tipo", "")
    filtro = request.args.get("filtro", "").strip()
    q = """
        SELECT m.*, p.nombre AS producto_nombre, p.unidad, a.nombre AS almacen_nombre
        FROM movimientos m
        JOIN productos p ON p.id = m.producto_id
        LEFT JOIN almacenes a ON a.id = m.almacen_id
        WHERE 1=1
    """
    params = []
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
        q += " AND (p.nombre LIKE ? OR m.nota LIKE ? OR m.usuario LIKE ?)"
        params += [f"%{filtro}%"] * 3
    q += " ORDER BY m.fecha DESC, m.id DESC LIMIT 500"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


@movimientos_bp.route("/api/movimientos/lote", methods=["POST"])
@login_requerido
def movimientos_lote():
    data = request.get_json() or {}
    tipo = data.get("tipo", "entrada")
    items = data.get("items", [])
    nota = data.get("nota", "")
    fecha = data.get("fecha") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if tipo not in ("entrada", "salida"):
        return err("Tipo inválido")
    if not items:
        return err("No hay productos para registrar")
    conn = get_conn()
    errores = []
    registrados = 0
    for item in items:
        prod_id = item.get("producto_id")
        cantidad = float(item.get("cantidad", 0) or 0)
        if cantidad <= 0 or not prod_id:
            continue
        stock_val = stock_actual(conn, prod_id)
        if tipo == "salida" and stock_val < cantidad:
            prod = conn.execute("SELECT nombre FROM productos WHERE id=?", (prod_id,)).fetchone()
            nombre = prod["nombre"] if prod else str(prod_id)
            errores.append(f"{nombre}: stock insuficiente ({stock_val})")
            continue
        registrar_movimiento(conn, prod_id, tipo, cantidad,
                             float(item.get("precio_unitario", 0) or 0),
                             fecha, nota, session.get("usuario", ""))
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
        data = request.get_json()
        conn.execute("""
            INSERT INTO gastos (categoria, descripcion, monto, fecha, proveedor_id)
            VALUES (?, ?, ?, ?, ?)
        """, (data["categoria"].strip(), data.get("descripcion", ""),
              float(data["monto"]), data.get("fecha") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              data.get("proveedor_id")))
        conn.commit()
        conn.close()
        return ok(message="Gasto registrado")

    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    filtro = request.args.get("filtro", "").strip()
    q = """SELECT g.*, p.nombre AS proveedor_nombre FROM gastos g
           LEFT JOIN proveedores p ON p.id = g.proveedor_id WHERE 1=1"""
    params = []
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
    return ok([dict(r) for r in rows])


@movimientos_bp.route("/api/gastos/<int:gasto_id>", methods=["DELETE"])
@login_requerido
def gasto(gasto_id):
    conn = get_conn()
    conn.execute("DELETE FROM gastos WHERE id = ?", (gasto_id,))
    conn.commit()
    conn.close()
    return ok(message="Gasto eliminado")


@movimientos_bp.route("/api/exportar/movimientos")
@login_requerido
def exportar_movimientos():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    tipo = request.args.get("tipo", "")
    filtro = request.args.get("filtro", "").strip()
    conn = get_conn()
    q = """
        SELECT m.fecha, p.nombre AS producto_nombre, p.unidad, m.tipo,
               m.cantidad, m.precio_unitario, a.nombre AS almacen_nombre, m.nota, m.usuario
        FROM movimientos m
        JOIN productos p ON p.id = m.producto_id
        LEFT JOIN almacenes a ON a.id = m.almacen_id
        WHERE 1=1
    """
    params = []
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
        q += " AND (p.nombre LIKE ? OR m.nota LIKE ? OR m.usuario LIKE ?)"
        params += [f"%{filtro}%"] * 3
    q += " ORDER BY m.fecha DESC, m.id DESC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    filas = [(r["fecha"], r["producto_nombre"], r["unidad"],
              "Entrada" if r["tipo"] == "entrada" else "Salida",
              r["cantidad"], round(r["precio_unitario"] or 0, 2),
              r["almacen_nombre"] or "", r["nota"] or "", r["usuario"] or "") for r in rows]
    return responder_excel("movimientos.xlsx",
                         ["Fecha y hora", "Producto", "Unidad", "Tipo", "Cantidad",
                          "Precio (Bs)", "Almacén", "Nota", "Usuario"], filas,
                         [20, 25, 10, 10, 10, 13, 15, 25, 12])


@movimientos_bp.route("/api/exportar/gastos")
@login_requerido
def exportar_gastos():
    desde = request.args.get("desde", "")
    hasta = request.args.get("hasta", "")
    filtro = request.args.get("filtro", "").strip()
    conn = get_conn()
    q = """SELECT g.fecha, g.categoria, g.descripcion, g.monto,
                  p.nombre AS proveedor_nombre
           FROM gastos g LEFT JOIN proveedores p ON p.id = g.proveedor_id WHERE 1=1"""
    params = []
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
              round(r["monto"], 2), r["proveedor_nombre"] or "") for r in rows]
    return responder_excel("gastos.xlsx",
                         ["Fecha y hora", "Categoría", "Descripción", "Monto (Bs)", "Proveedor"], filas,
                         [20, 18, 30, 15, 20])
