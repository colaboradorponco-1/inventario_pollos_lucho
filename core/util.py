"""Utilidades compartidas del sistema."""
from datetime import datetime
from functools import wraps
from io import BytesIO, StringIO
import socket

from flask import jsonify, redirect, request, session, send_file

from database import get_conn


def ok(data=None, message="OK"):
    return jsonify({"ok": True, "message": message, "data": data})


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
            if session.get("rol") not in roles:
                return err("No tienes permisos para esta acción", 403)
            return f(*args, **kwargs)
        return wrapper
    return decorador


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


def stock_actual(conn, prod_id):
    fila = conn.execute("SELECT cantidad FROM stock WHERE producto_id = ?", (prod_id,)).fetchone()
    return fila["cantidad"] if fila else 0.0


def registrar_movimiento(conn, producto_id, tipo, cantidad, precio, fecha, nota, usuario,
                         almacen_id=None):
    """Inserta un movimiento y actualiza la tabla stock en la misma transacción."""
    conn.execute("""
        INSERT INTO movimientos (producto_id, tipo, cantidad, precio_unitario, fecha, almacen_id, nota, usuario)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (producto_id, tipo, cantidad, precio, fecha, almacen_id, nota, usuario))
    signo = cantidad if tipo == "entrada" else -cantidad
    conn.execute("""
        INSERT INTO stock (producto_id, cantidad) VALUES (?, ?)
        ON CONFLICT(producto_id) DO UPDATE SET cantidad = stock.cantidad + excluded.cantidad
    """, (producto_id, signo))


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
