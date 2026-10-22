import unittest
from unittest.mock import patch

from flask import Flask, session

import core.pedidos as pedidos_module
from core.pedidos import _puede_ver_pedido
from core.util import ruta_bloqueada_sasonador


class FakeResult:
    def fetchone(self):
        return {"c": 0}

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self):
        self.queries = []

    def execute(self, query, params=()):
        self.queries.append((query, tuple(params)))
        return FakeResult()

    def close(self):
        pass


class SasonadorPermisosTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.ctx = self.app.test_request_context("/")
        self.ctx.push()
        session["rol"] = "sasonador"
        session["sucursal_id"] = 42

    def tearDown(self):
        self.ctx.pop()

    def test_puede_crear_y_consultar_pedidos_de_su_sucursal(self):
        for path in ("/api/sesion", "/api/catalogos", "/api/productos",
                     "/api/perfil/avatar/imagen", "/api/pedidos",
                     "/api/pedidos/15", "/api/pedidos/15/ticket",
                     "/pedidos/ticket/15"):
            with self.subTest(path=path):
                self.assertFalse(ruta_bloqueada_sasonador(path, "GET"))
        self.assertFalse(ruta_bloqueada_sasonador("/api/pedidos", "POST"))
        self.assertFalse(ruta_bloqueada_sasonador("/api/logout", "POST"))
        self.assertTrue(ruta_bloqueada_sasonador("/api/pedidos/15/etapa", "PUT"))
        self.assertTrue(ruta_bloqueada_sasonador("/api/pedidos/bandeja", "GET"))
        self.assertTrue(ruta_bloqueada_sasonador("/api/productos", "POST"))
        self.assertTrue(ruta_bloqueada_sasonador("/api/usuarios", "GET"))
        self.assertTrue(ruta_bloqueada_sasonador("/api/sucursales", "GET"))

    def test_solo_ve_pedidos_solicitados_por_su_sucursal(self):
        propio = {"sucursal_id": 42, "destino_id": 30}
        recibido = {"sucursal_id": 30, "destino_id": 42}
        self.assertTrue(_puede_ver_pedido(None, propio, []))
        self.assertFalse(_puede_ver_pedido(None, recibido, []))

    def test_listado_filtra_pedidos_por_sucursal_solicitante(self):
        session["user_id"] = 7
        conn = FakeConn()
        with patch.object(pedidos_module, "get_conn", return_value=conn):
            pedidos_module.pedidos()

        count_query, count_params = conn.queries[0]
        list_query, list_params = conn.queries[1]
        self.assertIn("AND p.sucursal_id = ?", count_query)
        self.assertIn("AND p.sucursal_id = ?", list_query)
        self.assertEqual(count_params[0], 42)
        self.assertEqual(list_params[0], 42)
        self.assertNotIn("p.destino_id = ?", count_query)
        self.assertNotIn("p.destino_id = ?", list_query)


if __name__ == "__main__":
    unittest.main()
