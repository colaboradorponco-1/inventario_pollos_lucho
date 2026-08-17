"""Arranque en producción con Waitress (servidor WSGI estable y multi-hilo).

Uso:
    python run.py

Genera el log en servidor.log y escucha en el puerto 5000.
"""
import logging
import sys

from waitress import serve

from app import create_app


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler("servidor.log", encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )

    from core.util import ip_local
    ip = ip_local()

    print("=" * 60)
    print("  SISTEMA DE INVENTARIO - POLLOS LUCHO (PRODUCCIÓN)")
    print("=" * 60)
    print(f"  En esta computadora:  http://localhost:5000")
    print(f"  Desde el celular:     http://{ip}:5000")
    print("  Servidor WSGI (Waitress) - 8 hilos")
    print("=" * 60)

    app = create_app()
    serve(app, host="0.0.0.0", port=5000, threads=8)


if __name__ == "__main__":
    main()
