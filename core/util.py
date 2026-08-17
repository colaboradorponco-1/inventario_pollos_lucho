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


def responder_csv(nombre_archivo, encabezados, filas):
    salida = StringIO()
    salida.write("\ufeff")
    salida.write(";".join(encabezados) + "\n")
    for fila in filas:
        salida.write(";".join(str(c).replace(";", ",") for c in fila) + "\n")
    contenido = salida.getvalue().encode("utf-8")
    return send_file(BytesIO(contenido), as_attachment=True, download_name=nombre_archivo,
                     mimetype="text/csv")


def ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"
