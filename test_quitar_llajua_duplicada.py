import unittest
from datetime import date
from unittest.mock import patch

from flask import Flask

import core.backup as backup
import core.inventario as inventario


def _client():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(inventario.inventario_bp)
    client = app.test_client()
    with client.session_transaction() as session:
        session["user_id"] = "admin-test"
        session["usuario"] = "admin-test"
        session["rol"] = "superadmin"
    return client


class Cursor:
    def __init__(self, row=None, rows=None, rowcount=0):
        self.row = row
        self.rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class Conn:
    def __init__(self, conteo_fisico=None):
        self.writes = []
        self.commits = 0
        self.rollbacks = 0
        self.conteo_fisico = conteo_fisico

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT id, sucursal_id"):
            return Cursor({
                "sucursal_id": 35,
                "categoria_id": 13,
                "fecha": date(2026, 10, 16),
                "estado": "abierto",
            })
        if normalized.startswith("SELECT id, producto_id"):
            return Cursor(rows=[
                {"id": 8931, "producto_id": 662, "producto_nombre": "llajua",
                 "unidad": "unidad", "inicial": 4, "ingreso_manual": 0,
                 "conteo_fisico": None},
                {"id": 8965, "producto_id": 661, "producto_nombre": "llajua",
                 "unidad": "unidad", "inicial": 4, "ingreso_manual": 0,
                 "conteo_fisico": self.conteo_fisico},
            ])
        if "inventario_ingresos_manuales" in normalized:
            return Cursor({"total": 0})
        if normalized.startswith("SELECT COUNT(*)"):
            return Cursor({"total": 20})
        self.writes.append((normalized, params))
        return Cursor(rowcount=1)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class QuitarLlajuaDuplicadaTests(unittest.TestCase):
    def _post(self):
        return _client().post(
            "/api/inventario-diario/163/excluir-llajua-duplicada",
            json={"confirmacion": "EXCLUIR_FILA_8965_DE_PLANILLA_163"})

    def test_quita_solo_la_fila_661_y_registra_exclusion(self):
        conn = Conn()
        with patch.object(inventario, "get_conn", return_value=conn), \
                patch.object(inventario, "es_superadmin", return_value=True), \
                patch.object(inventario, "registrar_auditoria"), \
                patch.object(backup, "carpeta_respaldos", return_value="backups"), \
                patch.object(backup, "nombre_respaldo", return_value="respaldo.sql"), \
                patch.object(backup, "volcar_a_archivo", return_value=(100, 1)):
            response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ok"])
        self.assertEqual(conn.commits, 1)
        self.assertTrue(any(sql.startswith("INSERT INTO inventario_detalle_exclusiones")
                            for sql, _ in conn.writes))
        self.assertTrue(any(sql.startswith("DELETE FROM inventario_detalle")
                            for sql, _ in conn.writes))
        self.assertTrue(any(sql.startswith("UPDATE inventario_diario SET total_items")
                            for sql, _ in conn.writes))
        self.assertTrue(all("movimientos" not in sql and "lotes" not in sql
                            for sql, _ in conn.writes))

    def test_no_modifica_la_planilla_si_la_fila_ya_tiene_conteo(self):
        conn = Conn(conteo_fisico=3)
        with patch.object(inventario, "get_conn", return_value=conn), \
                patch.object(inventario, "es_superadmin", return_value=True):
            response = self._post()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(conn.writes, [])
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 0)

    def test_no_permite_la_operacion_a_no_superadmin(self):
        llamada_db = []
        with patch.object(inventario, "get_conn",
                          side_effect=lambda: llamada_db.append(True)), \
                patch.object(inventario, "es_superadmin", return_value=False):
            response = self._post()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(llamada_db, [])


if __name__ == "__main__":
    unittest.main()
