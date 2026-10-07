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


def _ids_proveedores_misma_ciudad(conn, desde):
    """Ids de sucursales a las que la sucursal `desde` puede pedir.

    Los almacenes principales atienden a TODOS (siempre válidos); el resto de
    proveedores solo si están en la MISMA ciudad que `desde` (Cochabamba no
    pide a La Paz ni La Paz a Cochabamba). Los AS de producción cuentan como
    su ciudad padre (AS 6 de Agosto = La Paz). Sin `desde` o si no existe la
    sucursal, devuelve None (sin restricción, para llamadas que no piden).

    Los únicos destinos no-principales son las sucursales AS de producción de
    la MISMA ciudad (AS América / AS Simón López para Cochabamba; AS 6 de
    Agosto para La Paz). Las tiendas sin AS (Siglo XX, Sopocachi, Miraflores...)
    no atienden pedidos: su inventario es para su propia venta, y a nadie lo
    piden.
    """
    if not desde:
        return None
    try:
        desde = int(desde)
    except (TypeError, ValueError):
        return None
    from core.util import ciudad_normalizada
    fila = conn.execute(
        "SELECT nombre, es_as, padre_id FROM sucursales WHERE id = ?",
        (desde,)).fetchone()
    if not fila or not fila["nombre"]:
        return None
    nombre = fila["nombre"]
    if fila.get("es_as") and fila.get("padre_id"):
        p = conn.execute("SELECT nombre FROM sucursales WHERE id = ?",
                         (fila["padre_id"],)).fetchone()
        if p and p["nombre"]:
            nombre = p["nombre"]
    ciudad = ciudad_normalizada(nombre)
    ids = set()
    for r in conn.execute(
            "SELECT id, nombre, principal, es_as, padre_id FROM sucursales").fetchall():
        if r["principal"]:
            ids.add(int(r["id"]))
            continue
        if not r.get("es_as"):
            continue
        prov_nombre = r["nombre"]
        if r.get("padre_id"):
            p = conn.execute("SELECT nombre FROM sucursales WHERE id = ?",
                             (r["padre_id"],)).fetchone()
            if p and p["nombre"]:
                prov_nombre = p["nombre"]
        if ciudad_normalizada(prov_nombre) == ciudad:
            ids.add(int(r["id"]))
    return ids


def destinos_validos(conn, desde=None):
    """[sucursal_id, ...] con las que un pedido puede salir, por prioridad.

    Solo las que de verdad pueden atender: el almacén principal, las sucursales
    marcadas como proveedora, y las que tienen productos activos marcados
    «para proveer» (aunque nadie haya tocado la casilla: la opción se activa
    sola con la sola existencia de esos productos). El principal va primero.

    Con `desde` (la sucursal que pide) se restrringe además por ciudad.
    """
    filas = conn.execute(
        "SELECT id FROM sucursales "
        "WHERE principal = 1 OR IFNULL(provee, 0) = 1 "
        "   OR EXISTS (SELECT 1 FROM productos p "
        "              WHERE p.sucursal_id = sucursales.id AND p.activo = 1 "
        "                AND IFNULL(p.para_proveer, 1) = 1) "
        "ORDER BY principal DESC, nombre, id").fetchall()
    ids = [int(f["id"]) for f in filas]
    permitidos = _ids_proveedores_misma_ciudad(conn, desde)
    if permitidos is not None:
        ids = [i for i in ids if i in permitidos]
    return ids


def stock_por_destino(conn, desde=None):
    """{producto_id: [(sucursal_id, stock, nombre), ...]} -> solo los VÁLIDOS
    que tienen stock > 0, ordenados por prioridad.

    Sirve para INFORMAR ("en Simón López hay 30 de este producto"), NO para
    elegir destino a automatico. Tener stock no significa que la sucursal
    reparta ese producto: una sucursal puede tener mercadería para su propia
    venta y que otro encargado se la lleve en un pedido, dejándola sin nada.
    Quien reparte un producto es la sucursal que lo tiene en el catálogo.
    """
    filas = conn.execute("""
        SELECT l.producto_id, l.sucursal_id, s.nombre, SUM(l.cantidad) AS c
        FROM lotes l
        JOIN sucursales s ON s.id = l.sucursal_id
        WHERE l.cantidad > 0 AND l.sucursal_id IS NOT NULL
          AND (s.principal = 1 OR IFNULL(s.provee, 0) = 1
               OR EXISTS (SELECT 1 FROM productos p
                          WHERE p.sucursal_id = s.id AND p.activo = 1
                            AND IFNULL(p.para_proveer, 1) = 1))
        GROUP BY l.producto_id, l.sucursal_id, s.nombre
        ORDER BY s.principal DESC, s.nombre, l.sucursal_id
    """).fetchall()
    out = {}
    for f in filas:
        out.setdefault(int(f["producto_id"]), []).append(
            (int(f["sucursal_id"]), float(f["c"] or 0), f["nombre"] or ""))
    permitidos = _ids_proveedores_misma_ciudad(conn, desde)
    if permitidos is not None:
        for pid in list(out):
            out[pid] = [t for t in out[pid] if t[0] in permitidos]
            if not out[pid]:
                del out[pid]
    return out


def elegir_destino(candidatos, validos, sucursal_producto):
    """Destino de un pedido. -> (id, stock)  (id None si no hay a quién pedir)

    NO se elige "la sucursal que tiene stock". Se elige QUIEN REPARTE el
    producto, que es la sucursal que lo tiene en el catálogo, y solo si esa
    sucursal puede atender pedidos. Tener stock no da permiso para repartir.

    1. La sucursal del catálogo, si puede atender pedidos. Aunque no tenga
       stock: esa sucursal es la responsable de repartirlo, y si no tiene se
       para que la persona decida, en vez de que el sistema le vacíe el
       inventario de otra sucursal.
    2. Si el producto está en una sucursal que no puede atender (o es global),
       el principal, que es quien reparte.
    3. Si no hay ningún destino válido, None.
    """
    validos = list(validos or [])
    if sucursal_producto:
        try:
            sid = int(sucursal_producto)
        except (TypeError, ValueError):
            sid = None
        if sid is not None and sid in validos:
            return sid, stock_en_destino(candidatos, sid)
    if validos:
        return validos[0], stock_en_destino(candidatos, validos[0])
    return None, 0.0


def stock_en_destino(candidatos, sucursal_id):
    for sid, stock, _nom in candidatos or []:
        if sid == int(sucursal_id):
            return stock
    return 0.0


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