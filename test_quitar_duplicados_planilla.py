# -*- coding: utf-8 -*-
import sqlite3
import unittest
from unittest.mock import patch

from flask import Flask

import core.inventario as inventario


class ConexionSQLite:
    def __init__(self, conexion):
        self.conexion = conexion

    def execute(self, sql, params=()):
        return self.conexion.execute(
            sql.replace(" FOR UPDATE", "").replace("%s", "?"), params)

    def commit(self):
        self.conexion.commit()

    def rollback(self):
        self.conexion.rollback()

    def close(self):
        pass


class QuitarDuplicadosPlanillaTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE productos (
                id INTEGER PRIMARY KEY, nombre TEXT, unidad TEXT, activo INTEGER
            );
            CREATE TABLE inventario_diario (
                id INTEGER PRIMARY KEY, sucursal_id INTEGER, fecha TEXT,
                estado TEXT, categoria_id INTEGER, hora_corte TEXT,
                usuario TEXT, total_items INTEGER
            );
            CREATE TABLE inventario_detalle (
                id INTEGER PRIMARY KEY, inventario_id INTEGER, producto_id INTEGER,
                producto_nombre TEXT, unidad TEXT, inicial REAL,
                ingreso_manual REAL, conteo_fisico REAL,
                observaciones TEXT
            );
            CREATE TABLE inventario_ingresos_manuales (
                detalle_id INTEGER, request_id TEXT
            );
            CREATE TABLE stock (
                producto_id INTEGER, sucursal_id INTEGER, cantidad REAL
            );
            INSERT INTO productos VALUES
                (1, 'PAPA HOLANDESA', 'kg', 1),
                (2, 'Ficha antigua inactiva', 'UNIDAD', 0),
                (3, 'PLATANO DE FREIR', 'unidad', 1),
                (4, 'Ficha antigua inactiva', 'UNIDAD', 0);
            INSERT INTO inventario_diario VALUES
                (5, 20, '2026-10-16', 'abierto', 0, '23:59', 'encargado', 4);
            INSERT INTO inventario_detalle VALUES
                (51, 5, 1, 'PAPA HOLANDESA', 'kg', 35, 0, NULL, 'nota vigente'),
                (52, 5, 2, 'Papa Holandesa', 'KG', 72.4, 0, NULL, 'nota antigua'),
                (53, 5, 3, 'PLATANO DE FREIR', 'unidad', 144, 0, NULL, NULL),
                (54, 5, 4, 'Plátano de freír', 'UNIDAD', 200, 0, NULL, NULL);
            INSERT INTO stock VALUES (1, 20, 35), (3, 20, 144);
        """)
        self.conn = ConexionSQLite(self.db)
        self.filas = [
            {"id": 1, "nombre": "PAPA HOLANDESA", "unidad": "kg", "stock": 35},
            {"id": 3, "nombre": "PLATANO DE FREIR", "unidad": "unidad", "stock": 144},
        ]
        self.detalles = self.db.execute(
            "SELECT * FROM inventario_detalle WHERE inventario_id = 5"
        ).fetchall()
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(inventario.inventario_bp)
        self.client = self.app.test_client()
        self.db_patch = patch.object(inventario, "get_conn", return_value=self.conn)
        self.db_patch.start()
        self.products_patch = patch.object(
            inventario, "_productos_planilla", return_value=self.filas)
        self.products_patch.start()
        self.detail_patch = patch.object(
            inventario, "_detalle", return_value=self.detalles)
        self.detail_patch.start()
        self.scope_patch = patch.object(
            inventario, "ids_sucursal_consolidada", return_value=[20])
        self.scope_patch.start()
        self.backup_patch = patch.object(
            inventario, "volcar_a_archivo", return_value=(1, 1))
        self.backup_patch.start()
        self.audit_patch = patch.object(inventario, "registrar_auditoria")
        self.audit_patch.start()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["rol"] = "admin"
            sess["sucursal_id"] = 20

    def tearDown(self):
        for mocked in (self.audit_patch, self.backup_patch, self.scope_patch,
                       self.detail_patch, self.products_patch, self.db_patch):
            mocked.stop()
        self.db.close()

    def test_detecta_solo_fichas_inactivas_equivalentes_y_sin_conteo(self):
        candidatas, bloqueadas = inventario._lineas_duplicadas_seguidas(
            self.conn, self.filas, self.detalles)
        self.assertEqual(
            [(c["detalle_id"], c["nombre"], c["cantidad_antigua"],
              c["cantidad_correcta"]) for c in candidatas],
            [(52, "PAPA HOLANDESA", 72.4, 35), (54, "PLATANO DE FREIR", 200, 144)])
        self.assertEqual(bloqueadas, [])

    def test_quita_solo_las_dos_lineas_antiguas_y_no_toca_stock(self):
        respuesta = self.client.post(
            "/api/inventario-diario/5/quitar-lineas-duplicadas",
            json={"confirmar": True, "detalle_ids": [52, 54]})
        self.assertEqual(respuesta.status_code, 200, respuesta.get_json())
        lineas = self.db.execute(
            "SELECT producto_id, inicial FROM inventario_detalle "
            "WHERE inventario_id = 5 ORDER BY id").fetchall()
        self.assertEqual([(f["producto_id"], f["inicial"]) for f in lineas],
                         [(1, 35), (3, 144)])
        self.assertEqual(
            [tuple(f) for f in self.db.execute(
                "SELECT producto_id, cantidad FROM stock ORDER BY producto_id"
            ).fetchall()], [(1, 35), (3, 144)])
        self.assertEqual(self.db.execute(
            "SELECT observaciones FROM inventario_detalle WHERE id = 51"
        ).fetchone()[0], "nota vigente · nota antigua")
        self.assertIn("respaldo", respuesta.get_json()["data"])

    def test_no_quita_una_fila_que_ya_tiene_conteo(self):
        self.db.execute(
            "UPDATE inventario_detalle SET conteo_fisico = 70 WHERE id = 52")
        self.detalles = self.db.execute(
            "SELECT * FROM inventario_detalle WHERE inventario_id = 5"
        ).fetchall()
        self.detail_patch.stop()
        self.detail_patch = patch.object(
            inventario, "_detalle", return_value=self.detalles)
        self.detail_patch.start()
        respuesta = self.client.post(
            "/api/inventario-diario/5/quitar-lineas-duplicadas",
            json={"confirmar": True, "detalle_ids": [52]})
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM inventario_detalle WHERE id = 52").fetchone()[0], 1)

    def test_no_quita_si_el_respaldo_falla(self):
        with patch.object(inventario, "volcar_a_archivo",
                          side_effect=OSError("disco lleno")):
            respuesta = self.client.post(
                "/api/inventario-diario/5/quitar-lineas-duplicadas",
                json={"confirmar": True, "detalle_ids": [52, 54]})
        self.assertEqual(respuesta.status_code, 500)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM inventario_detalle WHERE inventario_id = 5"
        ).fetchone()[0], 4)

    def test_solo_administracion_puede_quitar_lineas(self):
        with self.client.session_transaction() as sess:
            sess["rol"] = "encargado"
        respuesta = self.client.post(
            "/api/inventario-diario/5/quitar-lineas-duplicadas",
            json={"confirmar": True, "detalle_ids": [52]})
        self.assertEqual(respuesta.status_code, 403)

    def test_planilla_cerrada_no_se_modifica(self):
        self.db.execute(
            "UPDATE inventario_diario SET estado = 'cerrado' WHERE id = 5")
        respuesta = self.client.post(
            "/api/inventario-diario/5/quitar-lineas-duplicadas",
            json={"confirmar": True, "detalle_ids": [52]})
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM inventario_detalle WHERE inventario_id = 5"
        ).fetchone()[0], 4)


if __name__ == "__main__":
    unittest.main()
