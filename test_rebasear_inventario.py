import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask  # noqa: E402

import core.inventario as inventario  # noqa: E402


class _Conn:
    def __init__(self, fila=None):
        self.fila = fila or {
            "id": 9794,
            "inventario_id": 77,
            "producto_id": 413,
            "producto_nombre": "KETCHUP GALON",
            "codigo_actual": "PRD-0409",
            "producto_sucursal_id": 35,
            "conteo_fisico": 4.2,
            "inicial": 17.8,
            "stock_sistema": 17.8,
            "ingreso_manual": 0,
        }
        self.updates = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, sql, params=None):
        sql = " ".join(str(sql).split())
        if sql.startswith("SELECT inventario_id FROM inventario_detalle"):
            self.result = {"inventario_id": 77}
        elif sql.startswith("SELECT d.*, p.codigo AS codigo_actual"):
            self.result = self.fila
        elif sql.startswith("UPDATE inventario_detalle"):
            self.updates.append((sql, tuple(params or ())))
            self.rowcount = 1
        else:
            raise AssertionError(f"Consulta inesperada: {sql}")
        return self

    def fetchone(self):
        return self.result

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class RebasearInventarioTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(inventario.inventario_bp)
        self.conn = _Conn()
        self.patches = [
            patch.object(inventario, "get_conn", side_effect=lambda: self.conn),
            patch.object(
                inventario, "_sucursal_de_planilla",
                return_value=({"id": 77, "sucursal_id": 35,
                               "estado": "abierto"}, True)),
            patch.object(inventario, "ids_sucursal_consolidada",
                        return_value=[35]),
            patch.object(inventario, "_stocks_actuales", return_value={413: 7.6}),
            patch.object(inventario, "registrar_auditoria"),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(self._stop_patches)

    def _stop_patches(self):
        for p in reversed(self.patches):
            p.stop()

    def _rebasar(self, **overrides):
        data = {
            "codigo_esperado": "PRD-0409",
            "inicial_esperado": 17.8,
            "conteo_esperado": 4.2,
            "stock_esperado": 7.6,
        }
        data.update(overrides)
        client = self.app.test_client()
        with client.session_transaction() as s:
            s["user_id"] = "encargado-america"
            s["usuario"] = "encargado_america"
            s["rol"] = "encargado"
            s["sucursal_id"] = 35
        return client.post(
            "/api/inventario-diario/rebasar-referencia/9794", json=data)

    def test_rebasa_la_referencia_conservando_el_conteo_y_sin_mover_stock(self):
        respuesta = self._rebasar()
        body = respuesta.get_json()
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(body["data"]["inicial"], 7.6)
        self.assertEqual(body["data"]["conteo_fisico"], 4.2)
        self.assertEqual(body["data"]["diferencia"], -3.4)
        self.assertEqual(len(self.conn.updates), 1)
        self.assertEqual(self.conn.updates[0][1][:6],
                         (7.6, 7.6, 7.6, 4.2, 3.4, -3.4))
        self.assertEqual(self.conn.commits, 1)
        self.assertEqual(self.conn.rollbacks, 0)

    def test_no_rebasa_si_el_conteo_guardado_ya_cambio(self):
        respuesta = self._rebasar(conteo_esperado=5)
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(self.conn.updates, [])
        self.assertEqual(self.conn.commits, 0)
        self.assertEqual(self.conn.rollbacks, 1)

    def test_no_rebasa_si_el_stock_actual_no_coincide_con_confirmacion(self):
        with patch.object(inventario, "_stocks_actuales", return_value={413: 8}):
            respuesta = self._rebasar()
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(self.conn.updates, [])
        self.assertEqual(self.conn.commits, 0)

    def test_rechaza_codigo_o_sucursal_que_no_coincide(self):
        respuesta_codigo = self._rebasar(codigo_esperado="PRD-9999")
        self.assertEqual(respuesta_codigo.status_code, 409)
        self.assertEqual(self.conn.updates, [])

        self.conn = _Conn()
        self.conn.fila["producto_sucursal_id"] = 44
        respuesta_sucursal = self._rebasar()
        self.assertEqual(respuesta_sucursal.status_code, 409)
        self.assertEqual(self.conn.updates, [])


if __name__ == "__main__":
    unittest.main()
