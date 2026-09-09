"""Utilidades compartidas del sistema."""
from datetime import datetime
from functools import wraps
from io import BytesIO, StringIO
import socket

from flask import jsonify, redirect, request, session, send_file

from database import get_conn


def ok(data=None, message="OK"):
    return jsonify({"ok": True, "message": message, "data": data})


def ok_paginado(data, total, pagina, por_pagina):
    return jsonify({"ok": True, "message": "OK", "data": data,
                    "total": total, "pagina": pagina, "por_pagina": por_pagina,
                    "paginas": max(1, -(-total // por_pagina))})


def paginar_params():
    """Extrae pagina/por_pagina de los query params. Retorna (offset, limit, pagina, por_pagina)."""
    pagina = max(1, int(request.args.get("pagina", 1)))
    por_pagina = min(2000, max(10, int(request.args.get("por_pagina", 50))))
    offset = (pagina - 1) * por_pagina
    return offset, por_pagina, pagina, por_pagina


def err(message, status=400):
    return jsonify({"ok": False, "message": message}), status


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


def es_encargado_almacen(conn):
    """True si el rol es encargado y su sucursal es un almacén principal.
    Estos encargados coordinan el inventario: ven todo, como el admin."""
    if session.get("rol") != "encargado":
        return False
    sid = sucursal_actual()
    if not sid:
        return False
    fila = conn.execute("SELECT principal FROM sucursales WHERE id = ?", (sid,)).fetchone()
    return bool(fila and fila["principal"])


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
    try:
        conn = get_conn()
        conn.execute(
            "INSERT INTO auditoria (fecha, usuario, accion, detalle) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"),
             session.get("usuario", ""), accion, detalle))
        conn.commit()
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


def registrar_movimiento(conn, producto_id, tipo, cantidad, precio, fecha, nota, usuario,
                         sucursal_id=None, proveedor_id=None, vencimiento=None, lote=None):
    """Inserta un movimiento y actualiza los lotes de la sucursal en la misma transacción.

    - entrada: acumula en el lote con esa fecha de vencimiento (None = lote general).
    - salida:   consume por FEFO (primero el lote que vence antes); puede generar
                varios movimientos si la salida abarca más de un lote.
    Si no se indica sucursal y el usuario (p. ej. superadmin) no tiene una asignada,
    se usa la primera sucursal principal como destino del stock."""
    from .lotes import entrada_lote, salida_fefo
    if sucursal_id is None:
        sucursal_id = sucursal_operativa()
    if sucursal_id is None:
        raise ValueError("No se puede registrar el movimiento sin una sucursal definida")
    if tipo == "entrada":
        lote_id = entrada_lote(conn, producto_id, sucursal_id, cantidad, fecha,
                               vencimiento=vencimiento, lote_label=lote)
        conn.execute("""
            INSERT INTO movimientos (producto_id, tipo, cantidad, precio_unitario, fecha,
                                     sucursal_id, lote_id, vencimiento, nota, usuario, proveedor_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (producto_id, tipo, cantidad, precio, fecha, sucursal_id, lote_id,
              vencimiento or None, nota, usuario, proveedor_id))
        return lote_id
    consumidos = salida_fefo(conn, producto_id, sucursal_id, cantidad)
    for lote_id, take in consumidos:
        conn.execute("""
            INSERT INTO movimientos (producto_id, tipo, cantidad, precio_unitario, fecha,
                                     sucursal_id, lote_id, nota, usuario)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (producto_id, tipo, take, precio, fecha, sucursal_id, lote_id, nota, usuario))
    return None


def responder_excel(nombre_archivo, encabezados, filas, ancho_col=None, titulo=None):
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
