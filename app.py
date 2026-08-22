"""Punto de entrada del sistema de inventario Pollos Lucho.

Crea la aplicación Flask a partir de la fábrica create_app() y la arranca.
Para producción usa `run.py` (servidor WSGI Waitress).
"""
from flask import Flask, request as flask_request

from database import init_db
from core.auth import auth_bp
from core.catalogos import catalogos_bp
from core.dashboard import dashboard_bp
from core.movimientos import movimientos_bp
from core.pages import pages_bp
from core.productos import productos_bp
from core.reportes import reportes_bp
from core.sucursales import sucursales_bp
from core.usuarios import usuarios_bp
from core.ventas import ventas_bp


def create_app():
    init_db()
    app = Flask(__name__)
    app.secret_key = "pollos-lucho-inventario-2026"
    for bp in (pages_bp, auth_bp, dashboard_bp, catalogos_bp, productos_bp,
               movimientos_bp, ventas_bp, sucursales_bp, usuarios_bp, reportes_bp):
        app.register_blueprint(bp)

    @app.after_request
    def add_headers(resp):
        if flask_request.path.startswith('/static/'):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        else:
            resp.headers["Cache-Control"] = "no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        return resp

    return app


app = create_app()


if __name__ == "__main__":
    from core.util import ip_local
    ip = ip_local()
    print("=" * 60)
    print("  SISTEMA DE INVENTARIO - POLLOS LUCHO")
    print("=" * 60)
    print(f"  En esta computadora:  http://localhost:5000")
    print(f"  Desde el celular:     http://{ip}:5000")
    print("  (El celular debe estar conectado al mismo WiFi)")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=False)
