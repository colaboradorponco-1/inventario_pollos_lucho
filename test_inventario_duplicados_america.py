import sqlite3
import unittest
from datetime import date
from unittest.mock import patch

from flask import Flask

import core.backup as backup
import core.inventario as inventario


class Cursor:
    def __init__(self, row=None, rows=None, rowcount=0):
        self.row = row
        self.rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class SqliteConn:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=()):
        return self.conn.execute(sql.replace("%s", "?"), params)


class Conn:
    def __init__(self, conteo_duplicado=None, conteo_platano=626):
        self.writes = []
        self.commits = 0
        self.rollbacks = 0
        self.conteo_duplicado = conteo_duplicado
        self.conteo_platano = conteo_platano

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT id, sucursal_id"):
            return Cursor({
                "id": 170,
                "sucursal_id": 35,
                "categoria_id": 13,
                "fecha": date(2026, 10, 17),
                "estado": "abierto",
            })
        if normalized.startswith("SELECT id, producto_id"):
            return Cursor(rows=[
                {"id": 9109, "producto_id": 399, "producto_nombre": "PAPA HOLANDESA",
                 "codigo": "PRD-0395", "unidad": "kg", "inicial": 30,
                 "ingreso_manual": 0, "conteo_fisico": None},
                {"id": 9110, "producto_id": 660, "producto_nombre": "PAPA HOLANDESA",
                 "codigo": "PRD-0656", "unidad": "kg", "inicial": 72.4,
                 "ingreso_manual": 0, "conteo_fisico": self.conteo_duplicado},
                {"id": 9113, "producto_id": 401, "producto_nombre": "PLÁTANO DE FREÍR",
                 "codigo": "PRD-0397", "unidad": "unidad", "inicial": 73,
                 "ingreso_manual": 0, "conteo_fisico": self.conteo_platano},
                {"id": 9114, "producto_id": 659, "producto_nombre": "PLATANO DE FREIR",
                 "codigo": "PRD-0655", "unidad": "unidad", "inicial": 200,
                 "ingreso_manual": 0, "conteo_fisico": None},
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


def client():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(inventario.inventario_bp)
    c = app.test_client()
    with c.session_transaction() as session:
        session["user_id"] = "encargado-america"
        session["usuario"] = "encargado_america"
        session["rol"] = "encargado"
        session["sucursal_id"] = 35
    return c


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


class QuitarDuplicadosPlanillaTest(unittest.TestCase):
    confirmacion = {"confirmacion": "QUITAR_FILAS_9110_9114_DE_PLANILLA_170"}

    def test_elimina_solo_las_dos_filas_y_preserva_la_ficha_conteada(self):
        conn = Conn()
        with patch.object(inventario, "get_conn", return_value=conn), \
                patch.object(inventario, "registrar_auditoria"), \
                patch.object(backup, "carpeta_respaldos", return_value="backups"), \
                patch.object(backup, "nombre_respaldo", return_value="copia.sql"), \
                patch.object(backup, "volcar_a_archivo", return_value=(1024, 1)):
            response = client().post(
                "/api/inventario-diario/170/quitar-duplicados-confirmados",
                json=self.confirmacion)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["data"]["filas_eliminadas"], [9110, 9114])
        self.assertEqual(response.json["data"]["filas_conservadas"], [9109, 9113])
        self.assertEqual(conn.commits, 1)
        self.assertEqual(sum(sql.startswith("DELETE FROM inventario_detalle")
                             for sql, _ in conn.writes), 2)
        self.assertTrue(all("movimientos" not in sql and "lotes" not in sql
                            for sql, _ in conn.writes))

    def test_conteo_fisico_de_fila_duplicada_cancela_sin_escrituras(self):
        conn = Conn(conteo_duplicado=1)
        with patch.object(inventario, "get_conn", return_value=conn), \
                patch.object(backup, "volcar_a_archivo") as backup_mock:
            response = client().post(
                "/api/inventario-diario/170/quitar-duplicados-confirmados",
                json=self.confirmacion)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(conn.writes, [])
        self.assertEqual(conn.commits, 0)
        backup_mock.assert_not_called()

    def test_cambios_en_conteo_conservado_cancela_la_operacion(self):
        conn = Conn(conteo_platano=625)
        with patch.object(inventario, "get_conn", return_value=conn), \
                patch.object(backup, "volcar_a_archivo") as backup_mock:
            response = client().post(
                "/api/inventario-diario/170/quitar-duplicados-confirmados",
                json=self.confirmacion)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(conn.writes, [])
        backup_mock.assert_not_called()

    def test_sucursal_distinta_no_puede_usar_la_operacion(self):
        conn = Conn()
        c = client()
        with c.session_transaction() as session:
            session["sucursal_id"] = 30
        with patch.object(inventario, "get_conn", return_value=conn):
            response = c.post(
                "/api/inventario-diario/170/quitar-duplicados-confirmados",
                json=self.confirmacion)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(conn.writes, [])


if __name__ == "__main__":
    unittest.main()
