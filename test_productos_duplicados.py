# -*- coding: utf-8 -*-
import sqlite3
import unittest

from core.productos import _producto_duplicado, _producto_duplicado_edicion


class ProductosDuplicadosTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE sucursales (id INTEGER PRIMARY KEY, nombre TEXT);
            CREATE TABLE productos (
                id INTEGER PRIMARY KEY, codigo TEXT, nombre TEXT, unidad TEXT,
                sucursal_id INTEGER, activo INTEGER
            );
            INSERT INTO sucursales VALUES (1, 'AS Simon Lopez');
            INSERT INTO sucursales VALUES (2, 'America');
            INSERT INTO productos VALUES
                (1, 'PRD-1', 'PAPA HOLANDESA', 'kg', 1, 1),
                (2, 'PRD-2', 'PAPA HOLANDESA', 'kg', 1, 0),
                (3, 'PRD-3', 'PAPA HOLANDESA', 'unidad', 1, 1),
                (4, 'PRD-4', 'PAPA HOLANDESA', 'kg', 2, 1);
        """)

    def tearDown(self):
        self.conn.close()

    def test_encuentra_duplicado_sin_importar_mayusculas_ni_acentos(self):
        duplicado = _producto_duplicado(
            self.conn, "PÁPA HOLANDESA", "KG", sucursal_id=1)
        self.assertEqual(duplicado["id"], 1)
        self.assertEqual(duplicado["sucursal_nombre"], "AS Simon Lopez")

    def test_ignora_ficha_excluida_inactiva_y_unidad_distinta(self):
        self.assertIsNone(_producto_duplicado(
            self.conn, "PAPA HOLANDESA", "kg", sucursal_id=1, excluir_id=1))
        self.assertIsNone(_producto_duplicado(
            self.conn, "PAPA HOLANDESA", "caja", sucursal_id=1))

    def test_permite_la_misma_ficha_en_una_sucursal_distinta(self):
        self.assertEqual(
            _producto_duplicado(
                self.conn, "papa holandesa", "kg", sucursal_id=2)["id"],
            4)
        self.assertIsNone(_producto_duplicado(
            self.conn, "PAPA HOLANDESA", "caja", sucursal_id=2))

    def test_editar_otros_campos_no_bloquea_ficha_que_ya_tiene_duplicado(self):
        actual = {
            "id": 2, "nombre": "Papa Holandesa", "unidad": "KG",
            "sucursal_id": 1,
        }
        self.assertIsNone(_producto_duplicado_edicion(
            self.conn, "PAPA HOLANDESA", "kg", actual))

    def test_cambiar_nombre_o_unidad_sigue_validando_duplicados(self):
        actual = {
            "id": 2, "nombre": "Papa Holandesa especial", "unidad": "KG",
            "sucursal_id": 1,
        }
        self.assertEqual(_producto_duplicado_edicion(
            self.conn, "PAPA HOLANDESA", "kg", actual)["id"], 1)
        self.assertIsNone(_producto_duplicado_edicion(
            self.conn, "Papa Holandesa especial", "caja", actual))


if __name__ == "__main__":
    unittest.main()
