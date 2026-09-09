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