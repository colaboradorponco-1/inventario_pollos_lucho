"""Utilidades compartidas del sistema."""
from datetime import datetime
from functools import wraps
from io import BytesIO, StringIO
import socket
import sys
import unicodedata

from flask import (jsonify, redirect, request, session, send_file,
                   current_app, has_app_context)

from database import get_conn


def ok(data=None, message="OK"):
    return jsonify({"ok": True, "message": message, "data": data})


def ok_paginado(data, total, pagina, por_pagina):
    return jsonify({"ok": True, "message": "OK", "data": data,
                    "total": total, "pagina": pagina, "por_pagina": por_pagina,
                    "paginas": max(1, -(-total // por_pagina))})


def _entero_query(nombre, defecto):
    """Lee un query param que debe ser entero. Antes `?pagina=abc` reventaba con
    500 en una docena de endpoints; ahora cae al valor por defecto."""
    bruto = request.args.get(nombre, "").strip()
    if not bruto:
        return defecto
    try:
        return int(bruto)
    except (TypeError, ValueError):
        return defecto


def paginar_params():
    """Extrae pagina/por_pagina de los query params. Retorna (offset, limit, pagina, por_pagina)."""
    pagina = max(1, _entero_query("pagina", 1))
    por_pagina = min(2000, max(10, _entero_query("por_pagina", 50)))
    offset = (pagina - 1) * por_pagina
    return offset, por_pagina, pagina, por_pagina


def err(message, status=400):
    return jsonify({"ok": False, "message": message}), status


def flotante(valor, defecto=0.0):
    """Convierte a float de forma segura. Devuelve None si el valor no es
    numérico (para que el llamador responda 400 en vez de un 500)."""
    if valor is None or valor == "":
        return defecto
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def login_requerido(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return err("Debes iniciar sesión", 401)
            return redirect("/login")
        return f(*args, **kwargs)
    return wrapper


def rol_requerido(*roles):
    def decorador(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            rol = session.get("rol")
            # superadmin tiene acceso a cualquier rol administrativo
            if rol == "superadmin" and ("admin" in roles or "superadmin" in roles):
                return f(*args, **kwargs)
            if rol not in roles:
                return err("No tienes permisos para esta acción", 403)
            return f(*args, **kwargs)
        return wrapper
    return decorador


def es_superadmin():
    return session.get("rol") == "superadmin"


def es_gestion():
    """True si el usuario puede gestionar (admin/superadmin)."""
    return session.get("rol") in ("admin", "superadmin")


def es_logistica():
    """True si el rol es operativo de despacho (preparador/repartidor).

    Estos roles SOLO existen para marcar el avance de los pedidos de su
    sucursal. No participan de la gestión: ni dashboard, ni finanzas,
    ni inventario, ni productos, ni catalogos."""
    return session.get("rol") in ("preparador", "repartidor")


def es_receptor():
    """True si la cuenta está configurada como receptora de pedidos."""
    return bool(session.get("receptor"))


def _nombre_norm(nombre):
    """Normaliza el nombre de sucursal igual que `database._nombre_norm`
    (sin acentos ni palabras genéricas), para que la regla AS coincida."""
    n = unicodedata.normalize("NFD", (nombre or "")) \
        .encode("ascii", "ignore").decode().lower()
    for w in ("sucursal", "almacen", "principal"):
        n = n.replace(w, " ")
    return " ".join(n.split())


def _tienda_almacen_as(norm):
    """True si la sucursal corresponde a una tienda con almacén de producción
    separado ("AS <tienda>"). Por ahora solo: América, Simón López y
    6 de Agosto (La Paz). Se evalúa sobre el nombre normalizado.

    MANTENER EN SYNC con `database._es_tienda_almacen_as`: la regla define
    qué sucursales reciben su sucursal AS hija al arrancar.
    """
    return ("america" in norm or "simon" in norm
            or ("la paz" in norm and "6" in norm))


def sucursal_almacen_as(conn, sucursal_id):
    """Modelo actual: cada sucursal es un almacén 100% independiente (America,
    as_america, Simon Lopez, as_simon_lopez, ...) con sus propios productos y
    su propio stock. No hay sucursales AS de producción ligadas a la tienda:
    un producto vive donde fue creado. Identidad.
    """
    return sucursal_id


def ids_sucursal_consolidada(conn, sucursal_id):
    """Ids que pertenecen a una sucursal para contar/ver su inventario.

    Modelo actual: cada sucursal es un almacén 100% independiente (America,
    as_america, Simon Lopez, as_simon_lopez, ...) con sus propios productos y
    su propio stock, su inventario diario, sus entradas y salidas. No hay
    tiendas con sucursal AS de producción ligadas: el alcance de "la sucursal"
    es ELLA MISMA. Identidad: solo amplía nada.
    """
    try:
        sucursal_id = int(sucursal_id or 0)
    except (TypeError, ValueError):
        return [sucursal_id]
    return [sucursal_id]


# Endpoints que un preparador/repartidor JAMAŚ debe tocar: todo lo que no sea
# su cola de pedidos. Los pedidos (bandeja + etapa) quedan fuera.
RUTAS_BLOQUEADAS_LOGISTICA = (
    "/api/dashboard", "/api/categorias", "/api/movimientos",
    "/api/ventas", "/api/gastos", "/api/reportes", "/api/inventario",
    "/api/usuarios", "/api/auditoria", "/api/almacenes",
    "/api/proveedores", "/api/repartos", "/api/backup",
    "/api/config",
)

# Endpoints que el rol logistico puede LEER (los necesita para que los pedidos
# se rendericen con nombre, unidad y stock) pero nunca escribir.
RUTAS_SOLO_LECTURA_LOGISTICA = ("/api/productos",)


def ruta_bloqueada_logistica(path, method="GET"):
    """True si `path`+`method` es un endpoint prohibido para preparador/repartidor."""
    if not es_logistica():
        return False
    metodo = (method or "GET").upper()
    for bloqueada in RUTAS_BLOQUEADAS_LOGISTICA:
        if path == bloqueada or path.startswith(bloqueada + "/"):
            return True
    if metodo in ("GET", "HEAD", "OPTIONS"):
        return False
    # Única escritura permitida: advancing la etapa logística del pedido.
    import re as _re
    if _re.fullmatch(r"/api/pedidos/\d+/etapa", path) and metodo == "PUT":
        return False
    for solo_lectura in RUTAS_SOLO_LECTURA_LOGISTICA:
        if path == solo_lectura or path.startswith(solo_lectura + "/"):
            return True
    # No crean pedidos: su panel es solo la cola que les llega.
    if path == "/api/pedidos" or path.startswith("/api/pedidos/"):
        return True
    return False



def es_encargado_almacen(conn):
    """True si el rol es encargado y su sucursal es un almacén principal.
    Estos encargados coordinan el inventario: ven todo, como el admin."""
    if session.get("rol") != "encargado":
        return False
    sid = sucursal_actual()
    if not sid:
        return False
    fila = conn.execute("SELECT principal, nombre FROM sucursales WHERE id = ?", (sid,)).fetchone()
    if not fila or not fila["principal"]:
        return False
    # La Paz es una sucursal filial: aunque por error quede marcada como
    # principal en la base, su encargado NO actúa como almacén principal.
    norm = unicodedata.normalize("NFD", fila["nombre"] or "").encode("ascii", "ignore").decode().lower()
    return "la paz" not in norm


def sucursal_actual():
    """Devuelve el id de la sucursal del usuario logueado.
    superadmin ve todo (devuelve None). admin/encargado devuelven su sucursal."""
    if session.get("rol") == "superadmin":
        return None
    return session.get("sucursal_id") or None


def sucursal_operativa():
    """Devuelve la sucursal sobre la que se aplica una operación de escritura (stock).
    Todo usuario (incluido el superadmin) opera SIEMPRE sobre su sucursal asignada:
    - superadmin: su Almacén Principal 1.
    - admin: su almacén principal asignado.
    - encargado: la sucursal que tiene asignada.
    Esta es distinta de sucursal_actual(), que para superadmin devuelve None (vista global)."""
    return session.get("sucursal_id") or None


def clausula_sucursal(col="sucursal_id"):
    """Devuelve (sql_where, params) para filtrar por la sucursal del usuario.
    superadmin (sin sucursal) ve todo."""
    sid = sucursal_actual()
    if sid is None:
        return "", []
    return f" AND {col} = ?", [sid]


def registrar_auditoria(accion, detalle=""):
    """Deja rastro de una acción en la tabla `auditoria`.

    Antes tenía `except Exception: pass`, o sea que si la BD estaba caída, la tabla
    no existía o el detalle era demasiado largo, el rastro desaparecía sin dejar
    ni una línea en el log. Eso es peligroso justo en las operaciones delicadas
    (reconciliación, borrados, cambios de stock) donde el rastro es lo único que
    explica después qué pasó.

    Ahora se registra la traza completa en el log del servidor y, fuera de una
    petición (scripts, respaldos), avise por stderr. La operación que se auditaba
    NO se interrumpe: perder el rastro es malo, pero tumbar la venta porque no se
    pudo anotar es peor.
    """
    conn = None
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO auditoria (fecha, usuario, accion, detalle) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"),
             session.get("usuario", ""), accion, detalle))
        conn.commit()
    except Exception:
        try:
            if has_app_context():
                current_app.logger.exception(
                    "No se pudo registrar la auditoría de %r: %r", accion, detalle)
        except Exception:
            print(f"[auditoria] NO se pudo registrar {accion!r}: {detalle!r}",
                  file=sys.stderr)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def stock_actual(conn, prod_id, sucursal_id=None):
    """Stock actual del producto (suma de sus lotes).
    Si no se indica sucursal, suma los lotes de todas las sucursales."""
    from .lotes import stock_lotes
    if sucursal_id is None:
        sucursal_id = sucursal_actual()
    if sucursal_id is not None:
        return stock_lotes(conn, prod_id, sucursal_id)
    return stock_lotes(conn, prod_id)


# Tipos de movimiento que el sistema sabe interpretar.
#   entrada / salida: se guardan SIEMPRE positivas (la salida resta).
#   ajuste: ya viene con signo (negativo = faltó, positivo = sobró).
# Cualquier tipo fuera de esta lista es basura histórica (p. ej. el 'merma' que dejó
# la feature revertida) y hay que detectarlo: si se ignora en silencio, auditoría y
# reconciliación dan un stock equivocado sin avisar, que es justo lo que pasó con
# el ACE (2.1 -> 3.1).
TIPOS_MOVIMIENTO = ("entrada", "salida", "ajuste")


def signo_movimiento(tipo, cantidad):
    """Cantidad con su signo para sumar al stock, o None si el tipo no se conoce.

    Fuente única de verdad: auditoría y reconciliación tienen que usar EXACTAMENTE
    la misma regla, o una corrige lo que la otra marca como descuadre.
    """
    if tipo == "entrada":
        return float(cantidad or 0)
    if tipo == "salida":
        return -float(cantidad or 0)
    if tipo == "ajuste":
        return float(cantidad or 0)
    return None


def tipos_movimiento_desconocidos(conn):
    """[(tipo, n)] de los tipos que hay en `movimientos` pero el código no maneja."""
    filas = conn.execute(
        "SELECT tipo, COUNT(*) AS n FROM movimientos GROUP BY tipo").fetchall()
    return [{"tipo": r["tipo"], "n": int(r["n"] or 0)}
            for r in filas if (r["tipo"] or "") not in TIPOS_MOVIMIENTO]


def registrar_movimiento(conn, producto_id, tipo, cantidad, precio, fecha, nota, usuario,
                         sucursal_id=None, proveedor_id=None, vencimiento=None, lote=None):
    """Inserta un movimiento y actualiza los lotes de la sucursal en la misma transacción.

    - entrada: acumula en el lote con esa fecha de vencimiento (None = lote general).
    - salida:   consume por FEFO (primero el lote que vence antes); puede generar
                varios movimientos si la salida abarca más de un lote.
    - ajuste:   corrige el stock a una cantidad contada en el inventario físico.
                cantidad negativa = faltó (baja stock), positiva = sobró (sube stock).
                No recalcula el costo promedio, porque no es una compra real.
    Si no se indica sucursal y el usuario (p. ej. superadmin) no tiene una asignada,
    se usa la primera sucursal principal como destino del stock."""
    from .lotes import entrada_lote, salida_fefo, stock_lotes
    if tipo not in TIPOS_MOVIMIENTO:
        raise ValueError(f"Tipo de movimiento desconocido: {tipo!r}")
    if sucursal_id is None:
        sucursal_id = sucursal_operativa()
    if sucursal_id is None:
        raise ValueError("No se puede registrar el movimiento sin una sucursal definida")
    if tipo == "ajuste":
        cantidad = float(cantidad)
        if abs(cantidad) < 1e-9:
            return None
        if cantidad > 0:
            lote_id = entrada_lote(conn, producto_id, sucursal_id, cantidad, fecha)
        else:
            consumidos = salida_fefo(conn, producto_id, sucursal_id, -cantidad)
            if not consumidos:
                return None
            lote_id = consumidos[0][0]
        conn.execute("""
            INSERT INTO movimientos (producto_id, tipo, cantidad, precio_unitario, fecha,
                                     sucursal_id, lote_id, nota, usuario)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (producto_id, "ajuste", cantidad, precio or 0, fecha, sucursal_id, lote_id,
              nota, usuario))
        return lote_id
    if tipo == "entrada":
        stock_previo = stock_lotes(conn, producto_id, sucursal_id)
        lote_id = entrada_lote(conn, producto_id, sucursal_id, cantidad, fecha,
                               vencimiento=vencimiento, lote_label=lote)
        conn.execute("""
            INSERT INTO movimientos (producto_id, tipo, cantidad, precio_unitario, fecha,
                                     sucursal_id, lote_id, vencimiento, nota, usuario, proveedor_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (producto_id, tipo, cantidad, precio, fecha, sucursal_id, lote_id,
              vencimiento or None, nota, usuario, proveedor_id))
        if precio and precio > 0:
            _recalcular_costo_promedio(conn, producto_id, stock_previo, cantidad, precio)
        return lote_id
    consumidos = salida_fefo(conn, producto_id, sucursal_id, cantidad)
    for lote_id, take in consumidos:
        conn.execute("""
            INSERT INTO movimientos (producto_id, tipo, cantidad, precio_unitario, fecha,
                                     sucursal_id, lote_id, nota, usuario)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (producto_id, tipo, take, precio, fecha, sucursal_id, lote_id, nota, usuario))
    return None


def _recalcular_costo_promedio(conn, producto_id, stock_previo, cantidad, precio):
    """Actualiza productos.costo_promedio con el método de promedio móvil tras una
    entrada con precio: nuevo = (stock*costo + cantidad*precio) / (stock+cantidad).
    Si no hay stock previo, el costo pasa a ser el precio de esta entrada.

    `productos.costo_promedio` es UNA sola columna, pero el mismo nombre de producto
    vive en varias sucursales. Antes el promedio se mezclaba con el stock de una sola
    sucursal: comprar 100 kg a Bs 12 en La Paz con stock_previo=0 dejaba el costo en
    12 para TODAS las sucursales, pisando el promedio de América. Ahora el stock que
    entra en la fórmula es el total del producto en todos los almacenes, que es lo
    que corresponde a una columna global.

    El precio de compra es por kg/unidad, no depende de la sucursal: por eso el costo
    promedio puede ser único. Lo que sí es por sucursal es el stock.
    """
    if cantidad <= 0:
        return
    fila = conn.execute("SELECT costo_promedio FROM productos WHERE id = ?", (producto_id,)).fetchone()
    if not fila:
        return
    costo_previo = float(fila["costo_promedio"] or 0)
    total = conn.execute(
        "SELECT COALESCE(SUM(cantidad), 0) AS s FROM lotes WHERE producto_id = %s",
        (producto_id,)).fetchone()
    stock_global = float(total["s"] or 0.0)
    # Si ninguna sucursal tiene stock, se usa el precio de esta entrada.
    base = stock_global if stock_global > 0 else 0.0
    nuevo_stock = base + cantidad
    if base <= 0:
        costo_nuevo = float(precio)
    else:
        costo_nuevo = ((base * costo_previo) + (cantidad * float(precio))) / nuevo_stock
    conn.execute("UPDATE productos SET costo_promedio = ? WHERE id = ?",
                 (round(costo_nuevo, 2), producto_id))


def responder_excel(nombre_archivo, encabezados, filas, ancho_col=None, titulo=None,
                    subtitulos=None):
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.page import PageMargins

    wb = Workbook()
    ws = wb.active
    ws.title = "Reporte"

    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_LETTER
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_margins = PageMargins(left=0.4, right=0.4, top=0.6, bottom=0.6, header=0.3, footer=0.3)
    ws.oddHeader.center.text = titulo or "POLLOS LUCHO"
    ws.oddHeader.center.size = 14
    if subtitulos:
        lineas = [s for s in subtitulos if s]
        ws.oddHeader.right.text = "\n".join(lineas)
    ws.oddFooter.left.text = "Impreso el &D"
    ws.oddFooter.right.text = "Página &P de &N"

    header_font = Font(name="Calibri", bold=True, size=11, color="FFFFFF")
    header_fill = PatternFill(start_color="0C0908", end_color="0C0908", fill_type="solid")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"))

    for col_idx, enc in enumerate(encabezados, 1):
        cell = ws.cell(row=1, column=col_idx, value=enc)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin_border

    ws.row_dimensions[1].height = 28

    body_font = Font(name="Calibri", size=10)
    alt_fill = PatternFill(start_color="F5F5F5", end_color="F5F5F5", fill_type="solid")
    money_fmt = '#,##0.00'

    for row_idx, fila in enumerate(filas, 2):
        for col_idx, val in enumerate(fila, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = body_font
            cell.alignment = Alignment(vertical="center")
            cell.border = thin_border
            if row_idx % 2 == 0:
                cell.fill = alt_fill
            if isinstance(val, (int, float)):
                cell.number_format = money_fmt
                cell.alignment = Alignment(horizontal="right", vertical="center")

    if ancho_col:
        for i, w in enumerate(ancho_col, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
    else:
        for i in range(1, len(encabezados) + 1):
            ws.column_dimensions[get_column_letter(i)].width = 18

    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(buf, as_attachment=True, download_name=nombre_archivo,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# Identificadores de sucursales de La Paz que no llevan "la paz" en el nombre
# ("Sucursal Sopocachi", "Sucursal Miraflores", ...). Sin esto se clasificaban
# como Cochabamba y se colaban en pedidos/paneles de la otra ciudad.
# MANTENER EN SYNC con `static/app.js:esNombreLaPaz`.
LA_PAZ_NOMBRES = ("la paz", "sopocachi", "miraflores", "6 de agosto")


def ciudad_normalizada(nombre):
    """Normaliza el nombre de una sucursal/cliente a identificador de ciudad."""
    try:
        n = unicodedata.normalize("NFD", (nombre or "")).lower()
        n = "".join(c for c in n if unicodedata.category(c) != "Mn")
        return "la-paz" if any(marca in n for marca in LA_PAZ_NOMBRES) else "cochabamba"
    except Exception:
        return "cochabamba"


def ids_ciudad(conn, ciudad):
    """IDs de las sucursales de una ciudad ('cochabamba' | 'la-paz'); None si no aplica."""
    ciudad = (ciudad or "").strip().lower()
    if ciudad not in ("cochabamba", "la-paz"):
        return None
    out = []
    for r in conn.execute(
            "SELECT id, nombre FROM sucursales ORDER BY principal DESC, nombre").fetchall():
        if ciudad_normalizada(r["nombre"]) == ciudad:
            out.append(r["id"])
    return out or None


def cond_ciudad(col, cids):
    """(condición SQL, params) para restringir una consulta a un conjunto de sucursales de una ciudad."""
    if not cids:
        return "", []
    ph = ",".join(["%s"] * len(cids))
    return f" AND {col} IN ({ph})", list(cids)


def puede_ver_varias_sucursales(conn):
    """True si el usuario tiene legitimidad para consolidar más de una sucursal:
    gestión (admin/superadmin) o encargado de almacén principal. Un encargado de
    filial NUNCA puede ver datos de otras sucursales, aunque pase ?ciudad=..."""
    return es_gestion() or es_encargado_almacen(conn)


def ids_ciudad_permitidos(conn, ciudad):
    """ids_ciudad() pero devuelve None si el usuario no tiene autorización para
    filtrar por ciudad. Así el filtro ?ciudad=.. es inocuo para un encargado
    filial: la consulta se limita a su propia sucursal."""
    if not puede_ver_varias_sucursales(conn):
        return None
    return ids_ciudad(conn, ciudad)


def sucursal_filtro_permitida(conn, sucursal):
    """Valida el parámetro ?sucursal=<id> que se usa para acotar el alcance a una
    sucursal puntual. Devuelve el id normalizado, o None si el usuario no puede
    ver esa sucursal (en cuyo caso el llamador debe ignorar el parámetro)."""
    if sucursal in (None, ""):
        return None
    try:
        sid = int(sucursal)
    except (TypeError, ValueError):
        return None
    if not puede_ver_varias_sucursales(conn):
        return None
    existe = conn.execute("SELECT 1 FROM sucursales WHERE id = ?", (sid,)).fetchone()
    return sid if existe else None
