"""Pruebas de avatares predefinidos y fotos de perfil."""
import io
import os
import sys

from PIL import Image
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core.auth as auth  # noqa: E402


class FakeConn:
    def __init__(self):
        self.usuario = {
            "id": 7,
            "avatar": "pollito",
            "avatar_imagen": None,
        }
        self._resultado = None
        self.queries = []

    def execute(self, sql, params=None):
        sql = " ".join(str(sql).split())
        params = list(params or [])
        self.queries.append((sql, params))
        if sql.startswith("SELECT avatar, avatar_imagen"):
            self._resultado = dict(self.usuario)
        elif sql.startswith("SELECT avatar FROM usuarios"):
            self._resultado = {"avatar": self.usuario["avatar"]}
        elif "SET avatar = 'foto'" in sql:
            self.usuario["avatar"] = "foto"
            self.usuario["avatar_imagen"] = params[0]
            self._resultado = None
        elif sql.startswith("UPDATE usuarios SET avatar ="):
            self.usuario["avatar"] = params[0]
            self.usuario["avatar_imagen"] = None
            self._resultado = None
        return self

    def fetchone(self):
        return self._resultado

    def commit(self):
        pass

    def close(self):
        pass


CONN = FakeConn()
auth.get_conn = lambda: CONN
auth.registrar_auditoria = lambda *args, **kwargs: None

app = Flask(__name__)
app.secret_key = "avatar-test"
app.register_blueprint(auth.auth_bp)
app.config["TESTING"] = True


def cliente_autenticado():
    cliente = app.test_client()
    with cliente.session_transaction() as datos:
        datos["user_id"] = 7
        datos["usuario"] = "prueba"
    return cliente


def crear_png():
    imagen = Image.new("RGB", (400, 240), "#e6b422")
    archivo = io.BytesIO()
    imagen.save(archivo, format="PNG")
    archivo.seek(0)
    return archivo


def ejecutar():
    fallos = []
    with open(os.path.join(os.path.dirname(__file__), "database.py"),
              encoding="utf-8") as archivo:
        esquema = archivo.read()
    with open(os.path.join(os.path.dirname(__file__), "templates", "index.html"),
              encoding="utf-8") as archivo:
        interfaz = archivo.read()
    with open(os.path.join(os.path.dirname(__file__), "static", "app.js"),
              encoding="utf-8") as archivo:
        javascript = archivo.read()

    def check(nombre, condicion):
        print(f"  {'OK' if condicion else 'FALLO'} {nombre}")
        if not condicion:
            fallos.append(nombre)

    print("=" * 64)
    print("AVATARES DE PERFIL")
    print("=" * 64)
    check("la base agrega columnas persistentes y compatibles con los respaldos",
          '"avatar_imagen MEDIUMBLOB"' in esquema
          and '_add_columna(cur, "usuarios", "avatar_imagen MEDIUMBLOB")' in esquema)
    check("la interfaz ofrece avatares de pollo y carga de foto",
          'data-avatar-preset="pollito"' in interfaz
          and 'data-avatar-preset="alita"' in interfaz
          and 'id="perfil-foto-archivo"' in interfaz
          and "new FormData()" in javascript)

    CONN.usuario.update(avatar="pollito", avatar_imagen=None)
    cliente = cliente_autenticado()
    respuesta = cliente.put("/api/perfil/avatar", json={"avatar": "gallina"})
    check("permite elegir un avatar de pollo disponible",
          respuesta.status_code == 200 and CONN.usuario["avatar"] == "gallina")

    respuesta_invalida = cliente.put("/api/perfil/avatar", json={"avatar": "no-permitido"})
    check("rechaza valores de avatar arbitrarios",
          respuesta_invalida.status_code == 400 and CONN.usuario["avatar"] == "gallina")

    respuesta_foto = cliente.put(
        "/api/perfil/avatar",
        data={"imagen": (crear_png(), "foto.png")},
        content_type="multipart/form-data")
    check("acepta y normaliza una foto subida",
          respuesta_foto.status_code == 200
          and CONN.usuario["avatar"] == "foto"
          and isinstance(CONN.usuario["avatar_imagen"], bytes))
    imagen = Image.open(io.BytesIO(CONN.usuario["avatar_imagen"]))
    check("redimensiona la foto a WebP cuadrado",
          imagen.format == "WEBP" and imagen.size == (256, 256))

    respuesta_imagen = cliente.get("/api/perfil/avatar/imagen")
    check("sirve la foto privada al usuario autenticado",
          respuesta_imagen.status_code == 200
          and respuesta_imagen.mimetype == "image/webp"
          and respuesta_imagen.headers.get("X-Content-Type-Options") == "nosniff")
    respuesta_invalida = cliente.put(
        "/api/perfil/avatar",
        data={"imagen": (io.BytesIO(b"<svg onload=alert(1)>"), "avatar.svg")},
        content_type="multipart/form-data")
    check("rechaza SVG y contenido que no sea una imagen segura",
          respuesta_invalida.status_code == 400 and CONN.usuario["avatar"] == "foto")
    cliente.put("/api/perfil/avatar", json={"avatar": "alita"})
    check("cambiar a un avatar predefinido limpia la foto guardada",
          CONN.usuario["avatar"] == "alita" and CONN.usuario["avatar_imagen"] is None)
    check("la imagen deja de estar disponible tras cambiar a un avatar",
          cliente.get("/api/perfil/avatar/imagen").status_code == 404)

    respuesta_sin_sesion = app.test_client().get("/api/perfil/avatar/imagen")
    check("protege el endpoint de imagen sin sesión",
          respuesta_sin_sesion.status_code == 401)
    print("=" * 64)
    print("FALLOS:", len(fallos))
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(ejecutar())
