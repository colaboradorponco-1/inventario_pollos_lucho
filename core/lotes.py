"""Gestión de lotes por producto/sucursal con fecha de vencimiento (FEFO).

Cada entrada (manual, compra, importación, ajuste...) acumula su cantidad en el
lote de la sucursal con esa fecha de vencimiento (NULL = lote general "sin
vencimiento"). Las salidas consumen stock por FEFO: primero los lotes con
vencimiento más próximo; los lotes sin vencimiento se consumen al final.
"""
from datetime import datetime


def normalizar_fecha_vencimiento(valor):
    """Convierte 'YYYY-MM-DD' o 'YYYY-MM-DD HH:MM:SS' a date, o None."""
    if not valor:
        return None
    f = str(valor).strip()
    if len(f) > 10:
        f = f[:10]
    try:
        return datetime.strptime(f, "%Y-%m-%d").date()
    except ValueError:
        return None


def stock_por_destino(conn):
    """{producto_id: [(sucursal_id, stock, es_principal, nombre), ...]}

    Solo destinos que de verdad pueden atender un pedido (principal = 1 o
    provee = 1) y con stock > 0. La lista de cada producto viene ordenada por
    prioridad: el almacén principal primero y después las sucursales
    proveedora por nombre.

    Existe porque el destino de un pedido NO puede ser la sucursal donde el
    producto está registrado en el catálogo. La principal distribuye un mismo
    producto a varias sucursales y el stock real vive en `lotes`, por sucursal:
    un producto registrado en América puede tener todo su stock en Simón López.
    Antes se usaba `productos.sucursal_id` a secas, así que el pedido se
    mandaba a América, que es donde estaba escrito y donde no había nada.
    """
    filas = conn.execute("""
        SELECT l.producto_id, l.sucursal_id, s.principal, s.nombre,
               SUM(l.cantidad) AS c
        FROM lotes l
        JOIN sucursales s ON s.id = l.sucursal_id
        WHERE l.cantidad > 0 AND l.sucursal_id IS NOT NULL
          AND (s.principal = 1 OR IFNULL(s.provee, 0) = 1)
        GROUP BY l.producto_id, l.sucursal_id, s.principal, s.nombre
        ORDER BY s.principal DESC, s.nombre, l.sucursal_id
    """).fetchall()
    out = {}
    for f in filas:
        out.setdefault(int(f["producto_id"]), []).append(
            (int(f["sucursal_id"]), float(f["c"] or 0), bool(f["principal"]), f["nombre"] or ""))
    return out


def elegir_destino(candidatos, sucursal_producto):
    """Elige el destino de un pedido entre los que tienen stock. -> (id, stock)

    Regla, en este orden:

    1. La sucursal donde el producto está registrado, SI es un destino válido y
       tiene stock. Esto conserva el comportamiento de siempre: lo que ya
       funcionaba no cambia.
    2. Si ahí no hay nada, el de mayor prioridad que sí tenga: el principal
       primero, después las proveedoras por nombre.
    3. Si ningún destino válido tiene stock, None. No se manda a una sucursal
       vacía: es mejor un error claro que un pedido que nadie puede despachar.

    `candidatos` tiene que venir de `stock_por_destino` (ya viene ordenada por
    prioridad).
    """
    if not candidatos:
        return None, 0.0
    if sucursal_producto:
        for sid, stock, _pr, _nom in candidatos:
            if sid == int(sucursal_producto):
                return sid, stock
    return candidatos[0][0], candidates[0][1]


def stock_lotes(conn, producto_id, sucursal_id=None):
    """Stock total del producto (suma de lotes); por sucursal si se indica."""
    if sucursal_id is None:
        fila = conn.execute(
            "SELECT COALESCE(SUM(cantidad), 0) c FROM lotes WHERE producto_id = ?",
            (producto_id,)).fetchone()
    else:
        fila = conn.execute(
            "SELECT COALESCE(SUM(cantidad), 0) c FROM lotes "
            "WHERE producto_id = ? AND sucursal_id = ?",
            (producto_id, sucursal_id)).fetchone()
    return fila["c"] or 0.0


def entrada_lote(conn, producto_id, sucursal_id, cantidad, fecha, vencimiento=None, lote_label=None):
    """Registra una entrada acumulándola en el lote con la misma fecha de
    vencimiento (NULL = lote general). Devuelve el id del lote."""
    venc = normalizar_fecha_vencimiento(vencimiento)
    fila = conn.execute(
        "SELECT id FROM lotes WHERE producto_id = ? AND sucursal_id = ? "
        "AND fecha_vencimiento <=> ? FOR UPDATE",
        (producto_id, sucursal_id, venc)).fetchone()
    if fila:
        conn.execute("UPDATE lotes SET cantidad = cantidad + ?, lote = COALESCE(?, lote) WHERE id = ?",
                     (cantidad, lote_label, fila["id"]))
        return fila["id"]
    cur = conn.execute(
        "INSERT INTO lotes (producto_id, sucursal_id, lote, cantidad, fecha_ingreso, fecha_vencimiento) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (producto_id, sucursal_id, lote_label, cantidad, fecha, venc))
    return cur.lastrowid


def salida_fefo(conn, producto_id, sucursal_id, cantidad):
    """Consume `cantidad` de los lotes de la sucursal por FEFO (primero el que
    vence antes; sin vencimiento al final). Devuelve la lista de
    (lote_id, cantidad_consumida). Lanza ValueError si no hay stock suficiente."""
    filas = conn.execute(
        "SELECT id, cantidad FROM lotes "
        "WHERE producto_id = ? AND sucursal_id = ? AND cantidad > 0 "
        "ORDER BY (fecha_vencimiento IS NULL) ASC, fecha_vencimiento ASC, id ASC "
        "FOR UPDATE",
        (producto_id, sucursal_id)).fetchall()
    total = sum(f["cantidad"] or 0 for f in filas)
    if total + 1e-9 < cantidad:
        raise ValueError(f"Stock insuficiente. Disponible: {round(total, 2)}")
    restante = cantidad
    consumidos = []
    for f in filas:
        if restante <= 1e-9:
            break
        take = min(f["cantidad"], restante)
        conn.execute("UPDATE lotes SET cantidad = cantidad - ? WHERE id = ?", (take, f["id"]))
        restante -= take
        consumidos.append((f["id"], take))
    return consumidos