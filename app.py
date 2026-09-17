"""Punto de entrada del sistema de inventario Pollos Lucho.

Crea la aplicación Flask a partir de la fábrica create_app() y la arranca.
Para producción usa `run.py` (servidor WSGI Waitress).
"""
import logging
import os
import time
from datetime import timedelta

from flask import (Flask, jsonify, redirect, render_template,
                   request as flask_request, send_from_directory, session)

from database import init_db
from core.auth import auth_bp
from core.catalogos import catalogos_bp
from core.dashboard import dashboard_bp
from core.movimientos import movimientos_bp
from core.pages import pages_bp
from core.productos import productos_bp
from core.pedidos import pedidos_bp
from core.reportes import reportes_bp
from core.sucursales import sucursales_bp
from core.usuarios import usuarios_bp
from core.ventas import ventas_bp

# Duración de la sesión: expire tras 8 horas de inactividad (se refresca en
# cada petición mediante before_request).
SESION_INACTIVIDAD = 8 * 60 * 60


def _clave_secreta_dir(app_dir):
    """Clave secreta: 1) variable de entorno SECRET_KEY, 2) archivo .secret_key."""
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key.encode("utf-8")
    key_file = os.path.join(app_dir, ".secret_key")
    if os.path.exists(key_file):
        with open(key_file, "rb") as f:
            return f.read()
    clave = os.urandom(32)
    with open(key_file, "wb") as f:
        f.write(clave)
    return clave


def _configurar_logging():
    """Log de errores a archivo la primera vez que se arranca la app."""
    logger = logging.getLogger()
    if logger.handlers:
        return
    logger.setLevel(logging.INFO)
    formateador = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    manejador = logging.FileHandler(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "server.err.log"),
        encoding="utf-8")
    manejador.setFormatter(formateador)
    logger.addHandler(manejador)


def create_app():
    _configurar_logging()
    init_db()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    app = Flask(__name__)
    app.secret_key = _clave_secreta_dir(base_dir)
    # Sesiones: cookies firmadas, solo HTTP, mismasite Lax y expiración por inactividad.
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(seconds=SESION_INACTIVIDAD)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get(
        "COOKIE_SECURE", "0") == "1"  # activar tras desplegar con HTTPS
    app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32 MB para importaciones

    for bp in (pages_bp, auth_bp, dashboard_bp, catalogos_bp, productos_bp,
               movimientos_bp, ventas_bp, sucursales_bp, usuarios_bp, reportes_bp,
               pedidos_bp):
        app.register_blueprint(bp)

    @app.before_request
    def sesion_por_inactividad():
        """Expira la sesión tras SESION_INACTIVIDAD de inactividad (deslizante)."""
        if "user_id" not in session:
            return None
        ahora = int(time.time())
        ultima = session.get("_ultima_actividad")
        if ultima is None or ahora - ultima > SESION_INACTIVIDAD:
            session.clear()
            if flask_request.path.startswith("/api/"):
                return jsonify({"ok": False,
                                "message": "Sesión expirada por inactividad"}), 401
            return redirect("/login")
        session["_ultima_actividad"] = ahora
        return None

    @app.after_request
    def add_headers(resp):
        if flask_request.path.startswith('/static/'):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        else:
            resp.headers["Cache-Control"] = "no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "SAMEORIGIN"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp

    @app.route("/sw.js")
    def service_worker():
        """Sirve el service worker desde la raíz para que controle toda la app (PWA)."""
        resp = send_from_directory(app.static_folder, "sw.js",
                                   mimetype="application/javascript")
        resp.headers["Service-Worker-Allowed"] = "/"
        return resp

    @app.errorhandler(404)
    def no_encontrado(e):
        if flask_request.path.startswith("/api/"):
            return jsonify({"ok": False, "message": "Recurso no encontrado"}), 404
        return render_template("error.html",
                               codigo=404,
                               titulo="Página no encontrada",
                               mensaje="La página que buscas no existe o fue movida."), 404

    @app.errorhandler(500)
    def error_interno(e):
        app.logger.exception("Error interno en %s",
                             flask_request.path or "ruta desconocida")
        if flask_request.path.startswith("/api/"):
            return jsonify({"ok": False,
                            "message": "Error interno del servidor"}), 500
        return render_template("error.html",
                               codigo=500,
                               titulo="Error interno",
                               mensaje="Ocurrió un error inesperado. Revisa el log y vuelve a intentarlo."), 500

    return app


app = create_app()


if __name__ == "__main__":
    from core.util import ip_local
    from waitress import serve
    ip = ip_local()
    print("=" * 60)
    print("  SISTEMA DE INVENTARIO - POLLOS LUCHO")
    print("=" * 60)
    print(f"  En esta computadora:  http://localhost:5000")
    print(f"  Desde el celular:     http://{ip}:5000")
    print("  (El celular debe estar conectado al mismo WiFi)")
    print("=" * 60)
    serve(app, host="0.0.0.0", port=5000, threads=16)
