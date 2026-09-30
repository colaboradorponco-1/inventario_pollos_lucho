"""Planilla de Inventario Físico Diario de Almacén.

Cada sucursal (incluidos los almacenes principales) cuenta su inventario una vez
por día, con fecha y hora de corte. Para cada categoría y producto se registra:

    Inventario inicial + Ingreso del día = Disponible del día
    Disponible del día - Inventario final  = Cantidad utilizada

- El inventario inicial se reconstruye a partir de los movimientos del día: es el
  stock que tenía la sucursal a las 00:00 de esa fecha.
- El ingreso del día son los movimientos tipo 'entrada' de esa fecha.
- El inventario final es el CONTEO FÍSICO que digita el encargado (no el del sistema).
- Al cerrar la planilla, cualquier diferencia entre el conteo y el stock del sistema
  se corrige con un movimiento tipo 'ajuste', para que `lotes` nunca se desincronice.

Una planilla por día y sucursal (UNIQUE sucursal_id + fecha). El encargado llena y
cierra las de su propia sucursal; un admin/superadmin puede ver las de todas.
"""
from datetime import date, datetime

from flask import Blueprint, request, session

from database import get_conn
from .util import (ok, err, login_requerido, responder_excel, sucursal_actual,
                   sucursal_operativa, es_gestion, es_encargado_almacen,
                   registrar_auditoria, registrar_movimiento, flotante)

inventario_bp = Blueprint("inventario", __name__)

# Un día no puede tener más de 2 años hacia atrás ni 1 día hacia delante.
_MIN_FECHA = (date.today().replace(year=date.today().year - 2)).isoformat()


def _validar_fecha(valor):
    """Normaliza 'YYYY-MM-DD'; devuelve la fecha ISO o None si es inválida."""
    if not valor:
        return None
    f = str(valor).strip()[:10]
    try:
        return datetime.strptime(f, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _sucursal_de_planilla(conn, inv_id, escribir=False):
    """Devuelve (planilla, ok). Verifica que el usuario tenga acceso a esa planilla:
    el encargado solo a las de su sucursal; gestión a todas."""
    inv = conn.execute("SELECT * FROM inventario_diario WHERE id = ?", (inv_id,)).fetchone()
    if not inv:
        return None, False
    sid = sucursal_actual()
    if not es_gestion() and not es_encargado_almacen(conn) and inv["sucursal_id"] != sid:
        return None, False
    return inv, True


def _detalle(conn, inv_id):
    """Líneas de la planilla agrupadas por categoría, en el orden de la plantilla."""
    return conn.execute("""
        SELECT d.*, s.nombre AS sucursal_nombre
        FROM inventario_detalle d
        JOIN inventario_diario i ON i.id = d.inventario_id
        JOIN sucursales s ON s.id = i.sucursal_id
        WHERE d.inventario_id = ?
        ORDER BY d.categoria_nombre, d.producto_nombre
    """, (inv_id,)).fetchall()


# --------------------------------------------------------------------------
# Crear la planilla del día (o recuperarla si ya existe)
# --------------------------------------------------------------------------
@inventario_bp.route("/api/inventario-diario", methods=["POST"])
@login_requerido
def inventario_crear():
    """Crea (o reabre) la planilla del día para la sucursal operativa del usuario.
    Cada línea se precarga con el inventario inicial y el ingreso del día, deducidos
    de los movimientos; el 'final' queda vacío hasta que el encargado haga el conteo."""
    conn = get_conn()
    sid = sucursal_operativa()
    if sid is None:
        conn.close()
        return err("Tu usuario no tiene sucursal asignada", 400)

    fecha = _validar_fecha(request.args.get("fecha")) or date.today().isoformat()
    if fecha < _MIN_FECHA or fecha > date.today().isoformat():
        conn.close()
        return err("Fecha fuera de rango", 400)
    hora = (request.args.get("hora") or datetime.now().strftime("%H:%M"))[:20]

    existente = conn.execute(
        "SELECT * FROM inventario_diario WHERE sucursal_id = ? AND fecha = ?", (sid, fecha)).fetchone()
    if existente and existente["estado"] == "cerrado":
        conn.close()
        return err("La planilla de esa fecha ya está cerrada", 400)
    if existente:
        conn.close()
        return ok({"id": existente["id"], "estado": existente["estado"],
                   "fecha": existente["fecha"], "sucursal_id": sid},
                  message="La planilla ya existe")

    cur = conn.execute("""
        INSERT INTO inventario_diario (sucursal_id, fecha, hora_corte, usuario, estado)
        VALUES (?, ?, ?, ?, 'abierto')
    """, (sid, fecha, hora, session.get("usuario", "")))
    inv_id = cur.lastrowid

    # Inventario inicial = stock a las 00:00 = stock actual - ingresos del día
    # + salidas del día. Los ajustes también mueven stock, así que se restan.
    filas = conn.execute("""
        SELECT p.id, p.codigo, p.nombre, p.unidad, p.costo_promedio, p.precio_venta,
               p.categoria_id, c.nombre AS categoria_nombre,
               COALESCE(l.stock, 0) AS stock,
               COALESCE(ent.ingreso, 0) AS ingreso,
               COALESCE(sa.salida, 0) AS salida,
               COALESCE(aj.ajuste, 0) AS ajuste
        FROM productos p
        LEFT JOIN categorias c ON c.id = p.categoria_id
        LEFT JOIN (SELECT producto_id, SUM(cantidad) AS stock FROM lotes
                   WHERE sucursal_id = %s GROUP BY producto_id) l ON l.producto_id = p.id
        LEFT JOIN (SELECT producto_id, SUM(cantidad) AS ingreso FROM movimientos
                   WHERE sucursal_id = %s AND tipo = 'entrada' AND DATE(fecha) = %s
                   GROUP BY producto_id) ent ON ent.producto_id = p.id
        LEFT JOIN (SELECT producto_id, SUM(cantidad) AS salida FROM movimientos
                   WHERE sucursal_id = %s AND tipo = 'salida' AND DATE(fecha) = %s
                   GROUP BY producto_id) sa ON sa.producto_id = p.id
        LEFT JOIN (SELECT producto_id, SUM(CASE WHEN cantidad < 0 THEN -cantidad ELSE cantidad END)
                          AS ajuste FROM movimientos
                   WHERE sucursal_id = %s AND tipo = 'ajuste' AND DATE(fecha) = %s
                   GROUP BY producto_id) aj ON aj.producto_id = p.id
        WHERE p.activo = 1 AND (l.stock > 0 OR ent.ingreso > 0 OR sa.salida > 0
                                OR p.sucursal_id = %s OR p.sucursal_id IS NULL)
        ORDER BY c.nombre, p.nombre
    """, (sid, sid, fecha, sid, fecha, sid, fecha, sid)).fetchall()

    for f in filas:
        # El 'stock' ya incluye los movimientos del día; para reconstruir el inicial
        # hay que restar el ingreso y las salidas, y devolver los ajustes.
        inicial = (f["stock"] or 0) - (f["ingreso"] or 0) + (f["salida"] or 0) - (f["ajuste"] or 0)
        if inicial < 0:
            inicial = 0.0
        disponible = inicial + (f["ingreso"] or 0)
        conn.execute("""
            INSERT INTO inventario_detalle
                (inventario_id, producto_id, categoria_id, categoria_nombre, producto_nombre,
                 codigo, unidad, stock_sistema, inicial, ingreso_dia, disponible,
                 final, utilizada, diferencia, costo_promedio, precio_venta)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 0, 0, %s, %s)
        """, (inv_id, f["id"], f["categoria_id"], f["categoria_nombre"] or "Sin categoría",
              f["nombre"], f["codigo"], f["unidad"] or "unidad", f["stock"] or 0,
              inicial, f["ingreso"] or 0, disponible, disponible,
              f["costo_promedio"] or 0, f["precio_venta"] or 0))

    conn.commit()
    conn.close()
    registrar_auditoria("Planilla de inventario creada", f"Sucursal {sid} - {fecha}")
    return ok({"id": inv_id, "estado": "abierto", "fecha": fecha, "sucursal_id": sid,
               "lineas": len(filas)}, message="Planilla creada")


# --------------------------------------------------------------------------
# Ver la planilla (con los cálculos de la plantilla)
# --------------------------------------------------------------------------
@inventario_bp.route("/api/inventario-diario/<int:inv_id>")
@login_requerido
def inventario_detalle(inv_id):
    conn = get_conn()
    inv, permitido = _sucursal_de_planilla(conn, inv_id)
    if not permitido:
        conn.close()
        return err("Planilla no encontrada", 404)
    filas = _detalle(conn, inv_id)
    sucursal = conn.execute("SELECT nombre FROM sucursales WHERE id = ?",
                            (inv["sucursal_id"],)).fetchone()
    conn.close()

    lineas = []
    for f in filas:
        disponible = (f["inicial"] or 0) + (f["ingreso_dia"] or 0)
        conteo = f["conteo_fisico"]
        lineas.append({
            "id": f["id"],
            "producto_id": f["producto_id"],
            "categoria": f["categoria_nombre"] or "Sin categoría",
            "producto": f["producto_nombre"],
            "codigo": f["codigo"],
            "unidad": f["unidad"],
            "stock_sistema": round(f["stock_sistema"] or 0, 3),
            "inicial": round(f["inicial"] or 0, 3),
            "ingreso_dia": round(f["ingreso_dia"] or 0, 3),
            "disponible": round(disponible, 3),
            "conteo_fisico": conteo,
            "final": round(f["final"] or 0, 3),
            "utilizada": round(f["utilizada"] or 0, 3),
            "diferencia": round(f["diferencia"] or 0, 3),
            "costo_promedio": round(f["costo_promedio"] or 0, 2),
            "precio_venta": round(f["precio_venta"] or 0, 2),
            "observaciones": f["observaciones"] or "",
        })

    faltantes = [l for l in lineas if l["diferencia"] < 0]
    sobrantes = [l for l in lineas if l["diferencia"] > 0]
    return ok({
        "id": inv["id"],
        "fecha": inv["fecha"],
        "hora_corte": inv["hora_corte"] or "",
        "sucursal_id": inv["sucursal_id"],
        "sucursal_nombre": sucursal["nombre"] if sucursal else "",
        "usuario": inv["usuario"] or "",
        "cerrado_por": inv["cerrado_por"] or "",
        "fecha_hora_cierre": inv["fecha_hora_cierre"] or "",
        "estado": inv["estado"],
        "observaciones": inv["observaciones"] or "",
        "lineas": lineas,
        "resumen": {
            "total_items": len(lineas),
            "total_faltantes": len(faltantes),
            "total_sobrantes": len(sobrantes),
            "cantidad_faltante": round(sum(abs(l["diferencia"]) for l in faltantes), 3),
            "cantidad_sobrante": round(sum(l["diferencia"] for l in sobrantes), 3),
            "valor_diferencia": round(sum(l["diferencia"] * l["costo_promedio"] for l in lineas), 2),
            "total_utilizada": round(sum(l["utilizada"] for l in lineas), 3),
        },
    })


# --------------------------------------------------------------------------
# Guardar el conteo físico del encargado
# --------------------------------------------------------------------------
@inventario_bp.route("/api/inventario-diario/<int:inv_id>", methods=["PUT"])
@login_requerido
def inventario_guardar(inv_id):
    """Guarda el conteo físico línea por línea. Recalcula disponible, utilizada y
    diferencia sin tocar el stock: el ajuste real se aplica al cerrar."""
    conn = get_conn()
    inv, permitido = _sucursal_de_planilla(conn, inv_id)
    if not permitido:
        conn.close()
        return err("Planilla no encontrada", 404)
    if inv["estado"] == "cerrado":
        conn.close()
        return err("La planilla está cerrada y no se puede modificar", 400)
    if inv["sucursal_id"] != sucursal_operativa():
        conn.close()
        return err("Solo puedes modificar la planilla de tu sucursal", 403)

    data = request.get_json() or {}
    lineas = data.get("lineas")
    if not isinstance(lineas, list):
        conn.close()
        return err("Envía las líneas a actualizar", 400)

    validas = {f["id"]: f for f in _detalle(conn, inv_id)}
    for item in lineas:
        if not isinstance(item, dict):
            continue
        lid = item.get("id")
        fila = validas.get(lid)
        if not fila:
            continue
        conteo = None
        conteo_raw = item.get("conteo_fisico", None)
        if conteo_raw not in (None, ""):
            conteo = flotante(conteo_raw, None)
            if conteo is None or conteo < 0:
                conn.close()
                return err("El conteo físico debe ser un número mayor o igual a cero", 400)
        inicial = fila["inicial"] or 0
        ingreso = fila["ingreso_dia"] or 0
        disponible = inicial + ingreso
        final = conteo if conteo is not None else disponible
        utilizada = max(disponible - final, 0.0)
        # Diferencia = lo que hay de menos (-) o de más (+) respecto al sistema.
        # Se compara contra el stock real de `lotes`, no contra `disponible`.
        diferencia = final - (fila["stock_sistema"] or 0)
        conn.execute("""
            UPDATE inventario_detalle
            SET conteo_fisico = %s, final = %s, utilizada = %s, diferencia = %s, observaciones = %s
            WHERE id = %s
        """, (conteo, final, utilizada, diferencia,
              (item.get("observaciones") or "")[:500] or None, lid))

    conn.execute("UPDATE inventario_diario SET observaciones = %s, hora_corte = %s WHERE id = %s",
                 ((data.get("observaciones") or inv["observaciones"] or "")[:2000] or None,
                  (data.get("hora_corte") or inv["hora_corte"] or "")[:20] or None, inv_id))
    conn.commit()
    conn.close()
    return ok(message="Conteo guardado")


# --------------------------------------------------------------------------
# Cerrar la planilla y ajustar el stock al conteo físico
# --------------------------------------------------------------------------
@inventario_bp.route("/api/inventario-diario/<int:inv_id>/cerrar", methods=["POST"])
@login_requerido
def inventario_cerrar(inv_id):
    """Cierra la planilla. Por cada línea con diferencia genera un movimiento tipo
    'ajuste' para que el stock del sistema coincida con el conteo físico. La operación
    es idempotente: una planilla cerrada no se vuelve a cerrar."""
    conn = get_conn()
    inv, permitido = _sucursal_de_planilla(conn, inv_id)
    if not permitido:
        conn.close()
        return err("Planilla no encontrada", 404)
    if inv["estado"] == "cerrado":
        conn.close()
        return err("La planilla ya está cerrada", 400)
    if inv["sucursal_id"] != sucursal_operativa():
        conn.close()
        return err("Solo puedes cerrar la planilla de tu sucursal", 403)

    filas = _detalle(conn, inv_id)
    sin_contar = [f for f in filas if f["conteo_fisico"] is None]
    if sin_contar:
        conn.close()
        return err(f"Faltan {len(sin_contar)} productos por contar antes de cerrar", 400)

    fecha_cierre = datetime.now().isoformat(timespec="seconds")
    total_items = len(filas)
    faltantes = sum(1 for f in filas if (f["diferencia"] or 0) < 0)
    sobrantes = sum(1 for f in filas if (f["diferencia"] or 0) > 0)
    valor_dif = sum((f["diferencia"] or 0) * (f["costo_promedio"] or 0) for f in filas)
    ajustes = 0

    for f in filas:
        dif = f["diferencia"] or 0
        if abs(dif) < 1e-9:
            continue
        # Ajuste: cantidad negativa = faltó (baja stock), positiva = sobró (sube stock).
        try:
            registrar_movimiento(
                conn, f["producto_id"], "ajuste", dif,
                f["costo_promedio"] or 0, fecha_cierre,
                f"Inventario físico {inv['fecha']} (ajuste "
                f"{'faltante' if dif < 0 else 'sobrante'})",
                session.get("usuario", ""), inv["sucursal_id"])
        except ValueError as e:
            # Un ajuste a la baja no puede dejar el stock en negativo.
            conn.rollback()
            conn.close()
            return err(f"{f['producto_nombre']}: {str(e)}", 400)
        ajustes += 1

    conn.execute("""
        UPDATE inventario_diario
        SET estado = 'cerrado', cerrado_por = %s, fecha_hora_cierre = %s,
            total_items = %s, total_faltantes = %s, total_sobrantes = %s, valor_diferencia = %s
        WHERE id = %s
    """, (session.get("usuario", ""), fecha_cierre, total_items, faltantes, sobrantes,
          round(valor_dif, 2), inv_id))
    conn.commit()
    conn.close()
    registrar_auditoria("Planilla de inventario cerrada",
                        f"ID {inv_id} - {total_items} ítems - {ajustes} ajustes - "
                        f"dif. Bs {round(valor_dif, 2)}")
    return ok({"ajustes": ajustes, "total_items": total_items,
               "total_faltantes": faltantes, "total_sobrantes": sobrantes,
               "valor_diferencia": round(valor_dif, 2)},
              message=f"Planilla cerrada con {ajustes} ajuste(s) de stock")


# --------------------------------------------------------------------------
# Listado de planillas
# --------------------------------------------------------------------------
@inventario_bp.route("/api/inventario-diario")
@login_requerido
def inventario_lista():
    conn = get_conn()
    sid = sucursal_actual()
    if es_gestion() or es_encargado_almacen(conn):
        q = """SELECT i.*, s.nombre AS sucursal_nombre FROM inventario_diario i
              JOIN sucursales s ON s.id = i.sucursal_id"""
        params = []
    else:
        q = """SELECT i.*, s.nombre AS sucursal_nombre FROM inventario_diario i
              JOIN sucursales s ON s.id = i.sucursal_id WHERE i.sucursal_id = %s"""
        params = [sid]
    if request.args.get("estado"):
        q += " AND i.estado = %s"
        params.append(request.args["estado"])
    q += " ORDER BY i.fecha DESC, i.id DESC LIMIT 200"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return ok([dict(r) for r in rows])


# --------------------------------------------------------------------------
# Exportar la planilla a Excel (misma plantilla que usan en el almacén)
# --------------------------------------------------------------------------
@inventario_bp.route("/api/inventario-diario/<int:inv_id>/excel")
@login_requerido
def inventario_excel(inv_id):
    conn = get_conn()
    inv, permitido = _sucursal_de_planilla(conn, inv_id)
    if not permitido:
        conn.close()
        return err("Planilla no encontrada", 404)
    filas = _detalle(conn, inv_id)
    sucursal = conn.execute("SELECT nombre FROM sucursales WHERE id = ?",
                            (inv["sucursal_id"],)).fetchone()
    conn.close()

    enc = ["Categoría", "Producto", "Unidad", "Inventario inicial", "Ingreso del día",
           "Disponible del día", "Inventario final", "Cantidad utilizada",
           "Diferencia", "Observaciones"]
    filas_xl = [(f["categoria_nombre"] or "Sin categoría", f["producto_nombre"],
                 f["unidad"] or "", f["inicial"] or 0, f["ingreso_dia"] or 0,
                 (f["inicial"] or 0) + (f["ingreso_dia"] or 0), f["final"] or 0,
                 f["utilizada"] or 0, f["diferencia"] or 0, f["observaciones"] or "")
                for f in filas]

    titulo = "PLANILLA DE INVENTARIO FÍSICO DIARIO DE ALMACÉN"
    return responder_excel(
        f"inventario_{inv['fecha']}.xlsx", enc, filas_xl,
        [22, 32, 9, 17, 15, 18, 16, 18, 11, 40],
        titulo=titulo,
        subtitulos=[sucursal["nombre"] if sucursal else "",
                    f"Fecha: {inv['fecha']}",
                    f"Hora de corte: {inv['hora_corte'] or ''}",
                    f"Estado: {'CERRADA' if inv['estado'] == 'cerrado' else 'ABIERTA'}"])
