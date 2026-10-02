"""Planilla de Inventario Diario de Almacén.

Cada sucursal (incluidos los almacenes principales) cuenta su inventario una vez
por día, con fecha y hora de corte. Para cada categoría y producto se registra:

    Inventario inicial + Ingreso del día = Disponible del día
    Disponible del día - Inventario final  = Cantidad utilizada

- El inventario inicial se reconstruye a partir de los movimientos del día: es el
  stock que tenía la sucursal a las 00:00 de esa fecha.
- El ingreso del día son los movimientos tipo 'entrada' de esa fecha.
- El inventario inicial y el ingreso del día también se pueden escribir a mano cuando
  el encargado cuenta otra cosa (merma previa, apertura manual, etc.).
- El inventario final es el CONTEO FÍSICO que digita el encargado (no el del sistema).
- Al cerrar la planilla, cualquier diferencia entre el conteo y el stock del sistema
  se corrige con un movimiento tipo 'ajuste', para que `lotes` nunca se desincronice.

Cada día se pueden hacer VARIAS planillas por sucursal: una por categoría
(UNIQUE sucursal_id + categoria_id + fecha). El encargado llena y cierra las de su
propia sucursal; un admin/superadmin puede ver las de todas.
"""
from datetime import date, datetime, timedelta

from flask import (Blueprint, current_app, redirect, render_template, request,
                   session)

from database import get_conn
from .lotes import stock_lotes
from .util import (ok, err, login_requerido, responder_excel, sucursal_actual,
                   sucursal_operativa, es_gestion, es_encargado_almacen,
                   registrar_auditoria, registrar_movimiento, flotante)

inventario_bp = Blueprint("inventario", __name__)

# Un día no puede tener más de 2 años hacia atrás ni 1 día hacia delante.
# Ojo: replace(year=...) falla el 29 de febrero en año bisiesto; por eso se
# construye desde timedelta en vez de replace.
_MIN_FECHA = (date.today() - timedelta(days=731)).isoformat()


class _DatoInvalido(Exception):
    """Valor escrito a mano por el encargado que no se puede guardar."""


def _validar_hora(valor):
    """Normaliza la hora de corte a 'HH:MM' (o '' si no viene).

    Antes se guardaba tal cual, sin validar. MySQL compara `TIME(fecha) > 'lo-que-sea'`
    como texto, así que un valor raro (por ejemplo '99:99' o una fecha) hacía que la
    comparación fuera siempre falsa: `_movimientos_posteriores` ignoraba los
    movimientos del mismo día y el ajuste del cierre salía mal sin dar ningún error.
    """
    if valor is None:
        return ""
    s = str(valor).strip()
    if not s:
        return ""
    for formato in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(s, formato).strftime("%H:%M")
        except ValueError:
            continue
    return None  # formato inválido: el llamador responde 400


def _fecha_iso(valor):
    """MySQL devuelve las columnas DATE como texto RFC 1123 en UTC
    ('Tue, 29 Sep 2026 00:00:00 GMT'). Si eso llega al navegador se convierte a la
    hora local y en Bolivia la fecha se ve un día antes. Se devuelve como
    'AAAA-MM-DD', que es una fecha y no un instante, para que no haya desfase."""
    if not valor:
        return valor
    s = str(valor)
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        return s


def _validar_fecha(valor):
    """Normaliza 'YYYY-MM-DD'; devuelve la fecha ISO o None si es inválida."""
    if not valor:
        return None
    f = str(valor).strip()[:10]
    try:
        return datetime.strptime(f, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _sucursal_de_planilla(conn, inv_id, escribir=False, bloquear=False):
    """Devuelve (planilla, ok). Verifica que el usuario tenga acceso a esa planilla:
    el encargado solo a las de su sucursal; gestión a todas.

    `bloquear=True` añade FOR UPDATE para que nadie pueda tocar la fila mientras
    se opera sobre ella. Sin esto, dos cierres simultáneos de la misma planilla
    pasaban los dos la comprobación de 'estado' y generaban los ajustes dos veces.
    """
    sql = "SELECT * FROM inventario_diario WHERE id = ?"
    if bloquear:
        sql += " FOR UPDATE"
    inv = conn.execute(sql, (inv_id,)).fetchone()
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


def _stocks_actuales(conn, sucursal_id):
    """{producto_id: stock en lotes} de la sucursal, en una sola consulta."""
    return {r["producto_id"]: r["c"] or 0.0 for r in conn.execute(
        "SELECT producto_id, COALESCE(SUM(cantidad), 0) AS c FROM lotes "
        "WHERE sucursal_id = %s GROUP BY producto_id", (sucursal_id,)).fetchall()}


def _ingresos_del_dia(conn, sucursal_id, fecha):
    """{producto_id: total que ENTRÓ} a la sucursal en ese día, según el SISTEMA.

    Es la parte automática del "ingreso del día": todo lo que se registró como
    movimiento 'entrada', o sea las compras y los pedidos que otro almacén le
    entregó a esta sucursal. No se escribe a mano, y por eso no se puede
    duplicar: si el encargado lo anotara otra vez, una sucursal que pidió 30
    papas y las recibió tendría 60 de ingreso con 30 en el depósito.

    Lo que sí llega por vías que no pasan por el sistema (compra directa en el
    mercado, devolución, mercadería traída de la casa) se anota aparte, en el
    campo de ingreso manual, y se SUMA a este."""
    return {r["producto_id"]: r["i"] or 0.0 for r in conn.execute(
        "SELECT producto_id, SUM(cantidad) AS i FROM movimientos "
        "WHERE sucursal_id = %s AND tipo = 'entrada' AND DATE(fecha) = %s "
        "GROUP BY producto_id", (sucursal_id, fecha)).fetchall()}


def _ingresos_de_linea(ingresos, fila, abierto):
    """(ingreso_del_sistema, ingreso_manual, ingreso_total) de una línea.

    Mientras la planilla está ABIERTA la parte del sistema se recalcula, para que
    una mercadería que llegó después de abrir la planilla igual sume (antes se
    congelaba al crearla y el ingreso se perdía en silencio). La parte manual es
    siempre la que escribió el encargado. Si la planilla ya está CERRADA se
    respeta lo que se usó al cerrarla: es un registro histórico y no debe cambiar
    solo por registrar un movimiento belatedado."""
    if not abierto:
        sistema = fila["ingreso_dia"] or 0.0
        manual = fila["ingreso_manual"] or 0.0
    else:
        sistema = ingresos.get(fila["producto_id"], 0.0)
        manual = fila["ingreso_manual"] or 0.0
    return sistema, manual, sistema + manual


def _movimientos_posteriores(conn, sucursal_id, fecha, hora_corte=None):
    """Efecto neto en el stock de los movimientos registrados DESPUÉS del corte de la
    planilla: ya están en el stock actual pero no en el conteo físico.

    Son los de fechas posteriores a `fecha` y, si `hora_corte` viene informado, los
    del mismo día posteriores a esa hora. Sin esto, una planilla abierta a las 07:00 y
    cerrada a las 10:00, con ventas de las 08:00, volvería a subir ese stock al
    ajustar contra el conteo de las 07:00.

    OJO con el signo: en `movimientos` las ENTRADAS y las SALIDAS se guardan siempre
    como cantidad positiva (una salida de 10 es `cantidad = 10`, no -10) y quien las
    resta es el lote. Solo el tipo 'ajuste' viene con signo (negativo = faltó).
    Por eso el neto NO es un SUM(cantidad) pelado: hay que restar las salidas, o el
    stock de referencia saldría corrido en el doble de lo vendido después del corte."""
    cond = "DATE(fecha) > %s"
    params = [sucursal_id, fecha]
    if hora_corte:
        cond = "(DATE(fecha) > %s OR (DATE(fecha) = %s AND TIME(fecha) > %s))"
        params = [sucursal_id, fecha, fecha, hora_corte]
    return {r["producto_id"]: r["s"] or 0.0 for r in conn.execute(
        "SELECT producto_id, SUM(CASE WHEN tipo = 'salida' THEN -cantidad "
        "ELSE cantidad END) AS s FROM movimientos "
        "WHERE sucursal_id = %s AND tipo IN ('entrada', 'salida', 'ajuste') "
        f"AND {cond} GROUP BY producto_id", params).fetchall()}


# --------------------------------------------------------------------------
# Crear la planilla del día (o recuperarla si ya existe)
# --------------------------------------------------------------------------
@inventario_bp.route("/api/inventario-diario", methods=["POST"])
@login_requerido
def inventario_crear():
    """Crea (o reabre) la planilla del día para la sucursal operativa del usuario.
    Se puede hacer UNA PLANILLA POR CATEGORÍA (o una de todas), así el almacén
    cuenta por partes: la categoría se elige al abrirla y solo salen sus productos.
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
    hora = _validar_hora(request.args.get("hora")) or datetime.now().strftime("%H:%M")

    try:
        cat_id = int(request.args.get("categoria_id") or 0)
    except (TypeError, ValueError):
        cat_id = 0
    if cat_id < 0:
        cat_id = 0
    cat_nombre = "Todas las categorías"
    if cat_id > 0:
        c = conn.execute("SELECT nombre FROM categorias WHERE id = %s", (cat_id,)).fetchone()
        if not c:
            conn.close()
            return err("La categoría no existe", 400)
        cat_nombre = c["nombre"] or f"Categoría {cat_id}"

    # Una planilla "de todas las categorías" y una "por categoría" del mismo día
    #meterían dos veces al mismo producto. Al cerrar, cada una compara contra el
    # stock que dejó la anterior, así que la diferencia queda repartida entre las
    # dos planillas y no se sabe dónde se contabilizó. Se impide mezclar.
    criterio_opuesto = ("> 0" if cat_id == 0 else "= 0")
    opuesta = conn.execute(
        f"SELECT id, categoria_id, estado FROM inventario_diario "
        f"WHERE sucursal_id = %s AND fecha = %s AND categoria_id {criterio_opuesto}",
        (sid, fecha)).fetchone()
    if opuesta:
        conn.close()
        if cat_id == 0:
            return err(
                "Ese día ya hay una planilla por categoría. Cierra o borra esas "
                "planillas antes de crear una de todas las categorías, o el mismo "
                "producto se contaría dos veces.", 400)
        # Ya existe la planilla de "Todas las categorías" ese día: abrirla en
        # vez de bloquear (así no parece que la categoría no se puede elegir).
        return ok({"id": opuesta["id"], "estado": opuesta["estado"],
                   "fecha": fecha, "sucursal_id": sid},
                  message="Ya existe la planilla de 'Todas las categorías'; se abrió esa")

    existente = conn.execute(
        "SELECT * FROM inventario_diario WHERE sucursal_id = %s AND categoria_id = %s AND fecha = %s",
        (sid, cat_id, fecha)).fetchone()
    if existente and existente["estado"] == "cerrado":
        conn.close()
        return err(f"La planilla de '{cat_nombre}' de esa fecha ya está cerrada", 400)
    if existente:
        conn.close()
        return ok({"id": existente["id"], "estado": existente["estado"],
                   "fecha": existente["fecha"], "sucursal_id": sid},
                  message=f"La planilla de '{cat_nombre}' ya existe")

    cur = conn.execute("""
        INSERT INTO inventario_diario (sucursal_id, categoria_id, fecha, hora_corte, usuario, estado)
        VALUES (?, ?, ?, ?, ?, 'abierto')
    """, (sid, cat_id, fecha, hora, session.get("usuario", "")))
    inv_id = cur.lastrowid

    # Inventario inicial = stock a las 00:00 = stock actual - ingresos del día
    # + salidas del día. Los ajustes también mueven stock, así que se restan.
    filas = conn.execute("""
        SELECT p.id, p.codigo, p.nombre, p.unidad, p.costo_promedio, p.precio_venta,
               p.categoria_id, c.nombre AS categoria_nombre,
               COALESCE(l.stock, 0) AS stock,
               COALESCE(ent.ingreso, 0) AS ingreso,
               COALESCE(sa.salida, 0) AS salida,
               COALESCE(aj.ajuste, 0) AS ajuste,
               COALESCE(po.post, 0) AS posterior
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
        LEFT JOIN (SELECT producto_id, SUM(cantidad) AS ajuste FROM movimientos
                   WHERE sucursal_id = %s AND tipo = 'ajuste' AND DATE(fecha) = %s
                   GROUP BY producto_id) aj ON aj.producto_id = p.id
        LEFT JOIN (SELECT producto_id, SUM(CASE WHEN tipo = 'salida' THEN -cantidad
                   ELSE cantidad END) AS post FROM movimientos
                   WHERE sucursal_id = %s AND tipo IN ('entrada', 'salida', 'ajuste')
                     AND DATE(fecha) > %s
                   GROUP BY producto_id) po ON po.producto_id = p.id
        WHERE p.activo = 1 AND (l.stock > 0 OR ent.ingreso > 0 OR sa.salida > 0
                                OR p.sucursal_id = %s OR p.sucursal_id IS NULL)
          AND (%s = 0 OR p.categoria_id = %s)
        ORDER BY c.nombre, p.nombre
    """, (sid, sid, fecha, sid, fecha, sid, fecha, sid, fecha, sid, cat_id, cat_id)).fetchall()

    for f in filas:
        # 'stock' es el stock de HOY. Si la planilla es de un día anterior, primero
        # se deshacen los movimientos posteriores a esa fecha para obtener el stock
        # al cierre de ese día; luego se deshacen los del propio día para llegar al
        # inicial, cada uno con su signo:
        #   stock_dia  = stock - posteriores
        #   inicial    = stock_dia - ingreso + salida - ajuste
        # 'ajuste' viene con signo (negativo = faltante, positivo = sobrante).
        stock_dia = (f["stock"] or 0) - (f["posterior"] or 0)
        inicial = (stock_dia - (f["ingreso"] or 0)
                   + (f["salida"] or 0) - (f["ajuste"] or 0))
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
              f["nombre"], f["codigo"], f["unidad"] or "unidad", stock_dia,
              inicial, f["ingreso"] or 0, disponible, disponible,
              f["costo_promedio"] or 0, f["precio_venta"] or 0))

    conn.commit()
    conn.close()
    registrar_auditoria("Planilla de inventario creada",
                        f"Sucursal {sid} - {fecha} - {cat_nombre}")
    return ok({"id": inv_id, "estado": "abierto", "fecha": fecha, "sucursal_id": sid,
               "categoria_id": cat_id, "categoria_nombre": cat_nombre,
               "lineas": len(filas)}, message=f"Planilla creada: {cat_nombre}")


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
    stocks = _stocks_actuales(conn, inv["sucursal_id"])
    posteriores = _movimientos_posteriores(conn, inv["sucursal_id"], inv["fecha"], inv["hora_corte"])
    ingresos = _ingresos_del_dia(conn, inv["sucursal_id"], inv["fecha"])
    abierto = inv["estado"] != "cerrado"
    sucursal = conn.execute("SELECT nombre FROM sucursales WHERE id = ?",
                            (inv["sucursal_id"],)).fetchone()
    cat = None
    if (inv["categoria_id"] or 0) > 0:
        cat = conn.execute("SELECT nombre FROM categorias WHERE id = ?",
                           (inv["categoria_id"],)).fetchone()
    conn.close()

    lineas = []
    for f in filas:
        # El ingreso se muestra partido: lo del sistema (automático, sombreado)
        # y lo manual (lo que anota el encargado). Se suman para el disponible.
        ing_sis, ing_man, ingreso = _ingresos_de_linea(ingresos, f, abierto)
        disponible = (f["inicial"] or 0) + ingreso
        conteo = f["conteo_fisico"]
        # Stock del sistema tal como estaba al momento del conteo: el actual menos
        # lo que se movió después de la fecha de la planilla.
        stock_ref = stocks.get(f["producto_id"], 0.0) - posteriores.get(f["producto_id"], 0.0)
        if conteo is None:
            # Sin conteo no hay diferencia que informar (se calcula al cerrar).
            diferencia = 0.0
        else:
            diferencia = round((f["final"] or 0) - stock_ref, 3)
        lineas.append({
            "id": f["id"],
            "producto_id": f["producto_id"],
            "categoria": f["categoria_nombre"] or "Sin categoría",
            "producto": f["producto_nombre"],
            "codigo": f["codigo"],
            "unidad": f["unidad"],
            "stock_sistema": round(stock_ref, 3),
            "inicial": round(f["inicial"] or 0, 3),
            "ingreso_sistema": round(ing_sis, 3),
            "ingreso_manual": round(ing_man, 3),
            "ingreso_dia": round(ingreso, 3),
            "disponible": round(disponible, 3),
            "conteo_fisico": conteo,
            "final": round(f["final"] or 0, 3),
            "utilizada": round(f["utilizada"] or 0, 3),
            "diferencia": diferencia,
            "costo_promedio": round(f["costo_promedio"] or 0, 2),
            "precio_venta": round(f["precio_venta"] or 0, 2),
            "observaciones": f["observaciones"] or "",
        })

    faltantes = [l for l in lineas if l["diferencia"] < 0]
    sobrantes = [l for l in lineas if l["diferencia"] > 0]
    return ok({
        "id": inv["id"],
        "fecha": _fecha_iso(inv["fecha"]),
        "hora_corte": inv["hora_corte"] or "",
        "sucursal_id": inv["sucursal_id"],
        "sucursal_nombre": sucursal["nombre"] if sucursal else "",
        "categoria_id": inv["categoria_id"] or 0,
        "categoria_nombre": (cat["nombre"] if cat else "Todas las categorías"),
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
    """Guarda la planilla línea por línea. El encargado llena a mano el inventario
    inicial, el ingreso del día y el inventario final; el sistema calcula
    disponible (= inicial + ingreso), utilizada (= disponible - final) y la
    diferencia, sin tocar el stock: el ajuste real se aplica al cerrar."""
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

    def _campo(bruto, etiqueta):
        """Número >= 0 escrito por el encargado. None si lo dejó vacío."""
        if bruto in (None, ""):
            return None
        v = flotante(bruto, None)
        if v is None:
            raise _DatoInvalido(f"{etiqueta} debe ser un número (déjalo vacío si no aplica)")
        if v < 0:
            raise _DatoInvalido(f"{etiqueta} no puede ser negativo")
        return v

    validas = {f["id"]: f for f in _detalle(conn, inv_id)}
    stocks = _stocks_actuales(conn, inv["sucursal_id"])
    posteriores = _movimientos_posteriores(conn, inv["sucursal_id"], inv["fecha"], inv["hora_corte"])
    ingresos = _ingresos_del_dia(conn, inv["sucursal_id"], inv["fecha"])
    try:
        for item in lineas:
            if not isinstance(item, dict):
                continue
            lid = item.get("id")
            fila = validas.get(lid)
            if not fila:
                continue
            conteo = _campo(item.get("conteo_fisico"), "El inventario final")
            # El encargado puede corregir el INICIAL a mano (el stock con el que
            # arrancó el día no siempre cuadra con el que tiene el sistema).
            inicial = _campo(item.get("inicial"), "El inventario inicial")
            if inicial is None:
                inicial = fila["inicial"] or 0
            # El INGRESO va partido en dos y cada parte se guarda de una vez:
            #  - del sistema: se deduce de los movimientos, NO se acepta escrito.
            #    Si el navegador manda algo distinto se avisa, porque casi siempre
            #    es que el encargado está anotando de nuevo una entrega que el
            #    sistema ya registró, y eso duplicaba la mercadería.
            #  - manual: sí se acepta, es para la mercadería que llega por vías que
            #    no pasan por el sistema (compra directa, devolución, de la casa).
            ing_sis, ing_man, _ = _ingresos_de_linea(ingresos, fila, True)
            enviado_sis = item.get("ingreso_sistema")
            if enviado_sis not in (None, ""):
                v_env = flotante(enviado_sis, None)
                if v_env is not None and abs(v_env - ing_sis) > 1e-9:
                    raise _DatoInvalido(
                        f"{fila['producto_nombre']}: el ingreso del sistema no se escribe a mano, ya "
                        f"tiene {round(ing_sis, 3)} de entrada (compras y pedidos entregados). "
                        f"Anótalo en la columna 'Ingreso manual' de al lado, que esa sí es para lo "
                        f"que llega sin pasar por el sistema."
                    )
            ing_man = _campo(item.get("ingreso_manual"), "El ingreso manual")
            if ing_man is None:
                ing_man = fila["ingreso_manual"] or 0
            ingreso = ing_sis + ing_man
            disponible = inicial + ingreso
            final = conteo if conteo is not None else disponible
            utilizada = max(disponible - final, 0)
            if conteo is None:
                # Sin conteo no hay diferencia: se calcula recién al cerrar.
                diferencia = 0.0
            else:
                # Diferencia = lo que hay de menos (-) o de más (+) respecto al sistema.
                # Se compara contra el stock real de `lotes` (al momento del conteo),
                # no contra `disponible` ni contra el stock de cuando se abrió la planilla.
                stock_ref = stocks.get(fila["producto_id"], 0.0) - posteriores.get(fila["producto_id"], 0.0)
                diferencia = round(final - stock_ref, 3)
            conn.execute("""
                UPDATE inventario_detalle
                SET inicial = %s, ingreso_dia = %s, ingreso_manual = %s, disponible = %s,
                    conteo_fisico = %s, final = %s, utilizada = %s, diferencia = %s, observaciones = %s
                WHERE id = %s
            """, (inicial, ingreso, ing_man, disponible,
                  conteo, final, utilizada, diferencia,
                  (item.get("observaciones") or "")[:500] or None, lid))
    except _DatoInvalido as e:
        conn.close()
        return err(str(e), 400)

    hora_nueva = _validar_hora(data.get("hora_corte"))
    if hora_nueva is None:
        conn.close()
        return err("La hora de corte no tiene un formato válido (debe ser HH:MM)", 400)
    conn.execute("UPDATE inventario_diario SET observaciones = %s, hora_corte = %s WHERE id = %s",
                 ((data.get("observaciones") or inv["observaciones"] or "")[:2000] or None,
                  hora_nueva or inv["hora_corte"] or None, inv_id))
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
    # FOR UPDATE: bloquea la fila para que un doble clic o dos usuarios a la vez
    # no puedan pasar los dos la comprobación de estado y aplicar los ajustes dos
    # veces (eso inflaría el stock con los sobrantes).
    inv, permitido = _sucursal_de_planilla(conn, inv_id, bloquear=True)
    if not permitido:
        conn.rollback()
        conn.close()
        return err("Planilla no encontrada", 404)
    if inv["estado"] == "cerrado":
        conn.rollback()
        conn.close()
        return err("La planilla ya está cerrada", 400)
    if inv["sucursal_id"] != sucursal_operativa():
        conn.rollback()
        conn.close()
        return err("Solo puedes cerrar la planilla de tu sucursal", 403)

    filas = _detalle(conn, inv_id)
    sin_contar = [f for f in filas if f["conteo_fisico"] is None]
    if sin_contar:
        conn.rollback()
        conn.close()
        return err(f"Faltan {len(sin_contar)} productos por contar antes de cerrar", 400)

    fecha_cierre = datetime.now().isoformat(timespec="seconds")
    total_items = len(filas)
    ajustes = 0
    faltantes = 0
    sobrantes = 0
    valor_dif = 0.0
    # El ajuste se calcula contra el stock REAL al momento del conteo (el de
    # lotes menos lo que se movió después de la fecha de la planilla), no contra
    # el stock de cuando se abrió: si hubo entradas/salidas mientras la planilla
    # estuvo abierta, usar el valor viejo dejaba el stock descuadrado.
    posteriores = _movimientos_posteriores(conn, inv["sucursal_id"], inv["fecha"], inv["hora_corte"])
    dif_por_linea = {}
    for f in filas:
        final = f["final"]
        if final is None:
            continue
        stock_actual = stock_lotes(conn, f["producto_id"], inv["sucursal_id"])
        stock_ref = (stock_actual or 0.0) - posteriores.get(f["producto_id"], 0.0)
        dif = round((final or 0) - stock_ref, 3)
        dif_por_linea[f["id"]] = dif
        if abs(dif) < 1e-9:
            continue
        if dif < 0:
            faltantes += 1
        else:
            sobrantes += 1
        valor_dif += dif * (f["costo_promedio"] or 0)
        # Ajuste: cantidad negativa = faltó (baja stock), positiva = sobró (sube stock).
        try:
            registrar_movimiento(
                conn, f["producto_id"], "ajuste", dif,
                f["costo_promedio"] or 0, fecha_cierre,
                f"Inventario diario {inv['fecha']} (ajuste "
                f"{'faltante' if dif < 0 else 'sobrante'})",
                session.get("usuario", ""), inv["sucursal_id"])
        except Exception as e:
            # Un ajuste a la baja no puede dejar el stock en negativo o hay otro error.
            # Se revierte TODO, incluyendo los ajustes ya aplicados: la planilla queda
            # abierta y el stock como estaba.
            current_app.logger.exception("Error ajustando %s en la planilla %s",
                                         f["producto_nombre"], inv_id)
            conn.rollback()
            conn.close()
            return err(f"{f['producto_nombre']}: {str(e)}", 400)
        ajustes += 1

    # La diferencia real que se ajustó queda registrada en la planilla (es la que
    # se ve en pantalla y en el Excel).
    if dif_por_linea:
        conn.executemany(
            "UPDATE inventario_detalle SET diferencia = %s WHERE id = %s",
            [(d, lid) for lid, d in dif_por_linea.items()])

    # El estado solo se cambia si sigue 'abierto'. La fila está bloqueada con
    # FOR UPDATE desde el inicio del cierre, así que la condición es una red de
    # seguridad extra: si algo la cerró en medio, no se pisa.
    cur = conn.execute("""
        UPDATE inventario_diario
        SET estado = 'cerrado', cerrado_por = %s, fecha_hora_cierre = %s,
            total_items = %s, total_faltantes = %s, total_sobrantes = %s, valor_diferencia = %s
        WHERE id = %s AND estado = 'abierto'
    """, (session.get("usuario", ""), fecha_cierre, total_items, faltantes, sobrantes,
          round(valor_dif, 2), inv_id))
    if cur.rowcount != 1:
        conn.rollback()
        conn.close()
        return err("La planilla se cerró mientras se ajustaba. No se aplicó ningún cambio.", 409)
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
# Borrado de una planilla abierta
# --------------------------------------------------------------------------
@inventario_bp.route("/api/inventario-diario/<int:inv_id>", methods=["DELETE"])
@login_requerido
def inventario_borrar(inv_id):
    """Elimina una planilla que sigue ABIERTA.

    Guardar el conteo nunca mueve stock, así que borrar una planilla abierta es
    seguro: no toca `lotes` ni `movimientos`. Una planilla CERRADA no se puede
    borrar porque ya generó ajustes de stock; esos movimientos se quedan como
    registro y quien los produjo tiene que corregirlos a mano."""
    conn = get_conn()
    try:
        inv = conn.execute("SELECT * FROM inventario_diario WHERE id = %s", (inv_id,)).fetchone()
        if not inv:
            conn.close()
            return err("La planilla no existe", 404)
        if inv["sucursal_id"] != sucursal_operativa():
            conn.close()
            return err("Solo puedes borrar planillas de tu sucursal", 403)
        if inv["estado"] == "cerrado":
            conn.close()
            return err(
                "La planilla está cerrada y ya ajustó el stock. No se puede borrar: "
                "los movimientos de ajuste quedan como registro.", 400)

        lineas = conn.execute(
            "SELECT COUNT(*) AS n FROM inventario_detalle WHERE inventario_id = %s",
            (inv_id,)).fetchone()["n"]
        conn.execute("DELETE FROM inventario_detalle WHERE inventario_id = %s", (inv_id,))
        conn.execute("DELETE FROM inventario_diario WHERE id = %s", (inv_id,))
        conn.commit()
        conn.close()
        return ok({"id": inv_id, "lineas": lineas},
                  message=f"Planilla abierta borrada ({lineas} productos, sin cambios en el stock)")
    except Exception:
        conn.rollback()
        conn.close()
        app_logger = current_app.logger
        app_logger.exception("Error al borrar la planilla %s", inv_id)
        return err("No se pudo borrar la planilla", 500)


# --------------------------------------------------------------------------
# Listado de planillas
# --------------------------------------------------------------------------
@inventario_bp.route("/api/inventario-diario")
@login_requerido
def inventario_lista():
    """Lista las planillas de inventario diario.

    Acepta desde/hasta (fechas), busqueda (categoría o sucursal), categoria_id,
    estado y sucursal_id. Sin paginación: son pocas por día (una por categoría) y
    se piden todas para poder filtrar en el navegador."""
    conn = get_conn()
    sid = sucursal_actual()
    cond = []
    params = []
    if not (es_gestion() or es_encargado_almacen(conn)):
        cond.append("i.sucursal_id = %s")
        params.append(sid)
    desde = request.args.get("desde", "").strip()
    hasta = request.args.get("hasta", "").strip()
    if desde:
        cond.append("i.fecha >= %s")
        params.append(desde)
    if hasta:
        cond.append("i.fecha <= %s")
        params.append(hasta)
    busqueda = request.args.get("busqueda", "").strip()
    if busqueda:
        cond.append("(c.nombre LIKE %s OR s.nombre LIKE %s)")
        params += [f"%{busqueda}%"] * 2
    cat_arg = request.args.get("categoria_id")
    if cat_arg:
        cond.append("i.categoria_id = %s")
        params.append(int(cat_arg))
    if request.args.get("estado"):
        cond.append("i.estado = %s")
        params.append(request.args["estado"])
    if request.args.get("sucursal_id") and (es_gestion() or es_encargado_almacen(conn)):
        cond.append("i.sucursal_id = %s")
        params.append(int(request.args["sucursal_id"]))
    q = """SELECT i.*, s.nombre AS sucursal_nombre, c.nombre AS categoria_nombre,
                  (SELECT COUNT(*) FROM inventario_detalle d
                   WHERE d.inventario_id = i.id) AS items_det,
                  (SELECT COUNT(*) FROM inventario_detalle d
                   WHERE d.inventario_id = i.id AND d.conteo_fisico IS NULL) AS items_sin_contar
           FROM inventario_diario i
           JOIN sucursales s ON s.id = i.sucursal_id
           LEFT JOIN categorias c ON c.id = i.categoria_id"""
    if cond:
        q += " WHERE " + " AND ".join(cond)
    # Abiertas primero (son las que hay que terminar), luego por fecha y categoría.
    q += " ORDER BY (i.estado = 'abierto') DESC, i.fecha DESC, c.nombre ASC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    lista = []
    for r in rows:
        d = dict(r)
        d["fecha"] = _fecha_iso(d.get("fecha"))
        # En una planilla ABIERTA los totales de la tabla solo se rellenan al cerrar,
        # así que se calculan al vuelo para que la lista no muestre ceros falsos.
        if d["estado"] != "cerrado":
            contados = (d["items_det"] or 0) - (d["items_sin_contar"] or 0)
            d["total_items"] = d["items_det"] or 0
            d["total_faltantes"] = d["items_sin_contar"] or 0
            d["total_sobrantes"] = 0
            d["contados"] = contados
        else:
            d["contados"] = d["total_items"] or 0
        lista.append(d)
    return ok(lista)


# --------------------------------------------------------------------------
# Planilla imprimible (hoja del almacén) para llenar a mano y archivar
# --------------------------------------------------------------------------
@inventario_bp.route("/inventario-diario/<int:inv_id>/imprimir")
@login_requerido
def inventario_imprimir(inv_id):
    """Hoja A4 con la planilla del día. Si el inventario final ya está contado
    se imprime el valor; si no, la casilla va en blanco para llenarla a mano."""
    conn = get_conn()
    inv, permitido = _sucursal_de_planilla(conn, inv_id)
    if not permitido:
        conn.close()
        return redirect("/")
    filas = _detalle(conn, inv_id)
    sucursal = conn.execute("SELECT nombre FROM sucursales WHERE id = ?",
                            (inv["sucursal_id"],)).fetchone()
    cat = None
    if (inv["categoria_id"] or 0) > 0:
        cat = conn.execute("SELECT nombre FROM categorias WHERE id = ?",
                           (inv["categoria_id"],)).fetchone()
    stocks = _stocks_actuales(conn, inv["sucursal_id"])
    posteriores = _movimientos_posteriores(conn, inv["sucursal_id"], inv["fecha"], inv["hora_corte"])
    ingresos = _ingresos_del_dia(conn, inv["sucursal_id"], inv["fecha"])
    abierto = inv["estado"] != "cerrado"
    conn.close()

    lineas = []
    for f in filas:
        conteo = f["conteo_fisico"]
        # Mismo criterio que en pantalla: el ingreso se deduce de los movimientos
        # y la planilla abierta se recalcula, para que el Excel y la pantalla
        # digan exactamente lo mismo.
        ing_sis, ing_man, ingreso = _ingresos_de_linea(ingresos, f, abierto)
        disponible = (f["inicial"] or 0) + ingreso
        if conteo is None:
            final = ""
            utilizada = ""
        else:
            final = f["final"] or 0
            utilizada = round(max(disponible - (f["final"] or 0), 0), 3)
        lineas.append({
            "categoria": f["categoria_nombre"] or "Sin categoría",
            "producto": f["producto_nombre"],
            "unidad": f["unidad"] or "unidad",
            "inicial": round(f["inicial"] or 0, 3),
            "ingreso_dia": round(ingreso, 3),
            "disponible": round(disponible, 3),
            "final": final,
            "utilizada": utilizada,
            "observaciones": f["observaciones"] or "",
            "contado": conteo is not None,
        })

    return render_template("planilla_inventario.html", inv=inv,
                           sucursal_nombre=sucursal["nombre"] if sucursal else "",
                           categoria_nombre=cat["nombre"] if cat else "Todas las categorías",
                           lineas=lineas,
                           suma_inicial=round(sum(l["inicial"] for l in lineas), 3),
                           suma_ingreso=round(sum(l["ingreso_dia"] for l in lineas), 3),
                           suma_disponible=round(sum(l["disponible"] for l in lineas), 3),
                           suma_final=round(sum(l["final"] for l in lineas
                                                if l["contado"]), 3),
                           total_utilizada=round(sum(l["utilizada"] for l in lineas
                                                      if l["contado"]), 3))


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
    # El Excel se arma con el MISMO ingreso que se ve en pantalla. Si se usara el
    # valor guardado al crear la planilla, el reporte podria decir una cosa y la
    # pantalla otra, que es justo como se cuelan los errores de inventario.
    ingresos = _ingresos_del_dia(conn, inv["sucursal_id"], inv["fecha"])
    abierto = inv["estado"] != "cerrado"
    sucursal = conn.execute("SELECT nombre FROM sucursales WHERE id = ?",
                            (inv["sucursal_id"],)).fetchone()
    nombre_cat = "Todas las categorías"
    if (inv["categoria_id"] or 0) > 0:
        c = conn.execute("SELECT nombre FROM categorias WHERE id = ?",
                         (inv["categoria_id"],)).fetchone()
        if c:
            nombre_cat = c["nombre"]
    conn.close()

    enc = ["Categoría", "Producto", "Unidad", "Inventario inicial",
           "Ingreso del sistema", "Ingreso manual", "Ingreso del día",
           "Disponible del día", "Inventario final", "Cantidad utilizada",
           "Diferencia", "Observaciones"]
    filas_xl = []
    for f in filas:
        ing_sis, ing_man, ingreso = _ingresos_de_linea(ingresos, f, abierto)
        filas_xl.append((f["categoria_nombre"] or "Sin categoría", f["producto_nombre"],
                         f["unidad"] or "", f["inicial"] or 0, ing_sis, ing_man, ingreso,
                         (f["inicial"] or 0) + ingreso, f["final"] or 0,
                         f["utilizada"] or 0, f["diferencia"] or 0,
                         f["observaciones"] or ""))

    titulo = "PLANILLA DE INVENTARIO DIARIO DE ALMACÉN"
    return responder_excel(
        f"inventario_{inv['fecha']}.xlsx", enc, filas_xl,
        [22, 32, 9, 17, 16, 14, 14, 16, 16, 16, 11, 40],
        titulo=titulo,
        subtitulos=[sucursal["nombre"] if sucursal else "",
                    f"Categoría: {nombre_cat}",
                    f"Fecha: {inv['fecha']}",
                    f"Hora de corte: {inv['hora_corte'] or ''}",
                    f"Estado: {'CERRADA' if inv['estado'] == 'cerrado' else 'ABIERTA'}"])
