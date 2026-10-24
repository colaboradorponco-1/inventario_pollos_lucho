import unittest
from unittest.mock import patch

from flask import Flask, session

import core.usuarios as usuarios


class ConexionUsuarios:
    def __init__(self, receptor=0, sucursal_id=12):
        self.receptor = receptor
        self.sucursal_id = sucursal_id
        self.actualizacion = None
        self.closed = False

    def execute(self, sql, params=()):
        if sql.startswith("SELECT rol, receptor FROM usuarios"):
            return Resultado({"rol": "encargado", "receptor": self.receptor})
        if sql.startswith("SELECT sucursal_id FROM usuarios"):
            return Resultado({"sucursal_id": self.sucursal_id})
        if sql.startswith("UPDATE usuarios SET nombre"):
            self.actualizacion = params
        return Resultado(None)

    def commit(self):
        pass

    def close(self):
        self.closed = True


class Resultado:
    def __init__(self, fila):
        self.fila = fila

    def fetchone(self):
        return self.fila


class UsuariosReceptorTest(unittest.TestCase):
    def ejecutar_actualizacion(self, rol, receptor_actual=0):
        conn = ConexionUsuarios(receptor=receptor_actual)
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/usuarios/8", method="PUT",
                json={"nombre": "Cuenta AS", "rol": rol, "activo": 1}):
            session["user_id"] = 3
            session["rol"] = "admin"
            session["sucursal_id"] = 12
            with patch.object(usuarios, "get_conn", return_value=conn), \
                    patch.object(usuarios, "registrar_auditoria"):
                respuesta = usuarios.usuario(8)
        return respuesta, conn

    def test_admin_puede_asignar_o_quitar_receptor_en_su_sucursal(self):
        respuesta, conn = self.ejecutar_actualizacion("receptor")
        self.assertTrue(respuesta.get_json()["ok"])
        self.assertEqual(conn.actualizacion[3:5], (12, 1))
        self.assertTrue(conn.closed)

        respuesta, conn = self.ejecutar_actualizacion(
            "encargado", receptor_actual=1)
        self.assertTrue(respuesta.get_json()["ok"])
        self.assertEqual(conn.actualizacion[3:5], (12, 0))

    def test_actualizacion_sin_rol_conserva_la_marca_receptor(self):
        conn = ConexionUsuarios(receptor=1)
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/usuarios/8", method="PUT",
                json={"nombre": "Cuenta AS", "activo": 1}):
            session["user_id"] = 3
            session["rol"] = "admin"
            session["sucursal_id"] = 12
            with patch.object(usuarios, "get_conn", return_value=conn), \
                    patch.object(usuarios, "registrar_auditoria"):
                respuesta = usuarios.usuario(8)
        self.assertTrue(respuesta.get_json()["ok"])
        self.assertEqual(conn.actualizacion[3:5], (12, 1))


if __name__ == "__main__":
    unittest.main()
