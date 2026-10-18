import sqlite3
import unittest

import core.inventario as inventario


class SqliteConn:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        return self.conn.execute(sql.replace("%s", "?"), params)


class ProductosPlanillaScopeTest(unittest.TestCase):
    def test_stock_historico_de_otra_sucursal_no_agrega_producto_a_planilla(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE productos (
                id INTEGER, codigo TEXT, nombre TEXT, unidad TEXT,
                costo_promedio REAL, precio_venta REAL, categoria_id INTEGER,
                sucursal_id INTEGER, activo INTEGER
            );
            CREATE TABLE categorias (id INTEGER, nombre TEXT);
            CREATE TABLE lotes (producto_id INTEGER, sucursal_id INTEGER, cantidad REAL);
            CREATE TABLE movimientos (
                producto_id INTEGER, sucursal_id INTEGER, tipo TEXT,
                fecha TEXT, cantidad REAL
            );
            INSERT INTO categorias VALUES (13, 'Alimentos');
            INSERT INTO productos VALUES
                (399, 'PRD-0395', 'PAPA HOLANDESA', 'kg', 0, 0, 13, 35, 1),
                (660, 'PRD-0656', 'PAPA HOLANDESA', 'kg', 0, 0, 13, 50, 1),
                (900, 'GEN-1', 'PRODUCTO GLOBAL', 'unidad', 0, 0, 13, NULL, 1);
            INSERT INTO lotes VALUES (399, 35, 30), (660, 35, 72.4), (900, 35, 1);
        """)

        filas = inventario._productos_planilla(
            SqliteConn(conn), [35], "2026-10-17", 13)

        self.assertEqual({fila["id"] for fila in filas}, {399, 900})
        conn.close()


if __name__ == "__main__":
    unittest.main()
