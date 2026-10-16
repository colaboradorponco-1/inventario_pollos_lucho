# -*- coding: utf-8 -*-
import sqlite3
import unittest
from unittest.mock import patch

from flask import Flask

import core.productos as productos


class ConexionSQLite:
    def __init__(self, conexion):
        self.conexion = conexion

    def execute(self, sql, params=()):
        return self.conexion.execute(sql.replace(" FOR UPDATE", ""), params)

    def commit(self):
        self.conexion.commit()

    def rollback(self):
        self.conexion.rollback()

    def close(self):
        pass


class RevisionProductosDuplicadosTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE sucursales (id INTEGER PRIMARY KEY, nombre TEXT);
            CREATE TABLE categorias (id INTEGER PRIMARY KEY, nombre TEXT);
            CREATE TABLE productos (
                id INTEGER PRIMARY KEY, codigo TEXT, nombre TEXT, marca TEXT,
                unidad TEXT, sucursal_id INTEGER, categoria_id INTEGER,
                costo_promedio REAL, precio_venta REAL, para_proveer INTEGER,
                activo INTEGER
            );
            CREATE TABLE lotes (
                id INTEGER PRIMARY KEY, producto_id INTEGER, sucursal_id INTEGER,
                cantidad REAL
            );
            CREATE TABLE movimientos (id INTEGER PRIMARY KEY, producto_id INTEGER);
            CREATE TABLE reparto_detalle (id INTEGER PRIMARY KEY, producto_id INTEGER);
            CREATE TABLE venta_detalle (id INTEGER PRIMARY KEY, producto_id INTEGER);
            CREATE TABLE pedidos (id INTEGER PRIMARY KEY, estado TEXT);
            CREATE TABLE pedido_detalle (
                id INTEGER PRIMARY KEY, pedido_id INTEGER, producto_id INTEGER
            );
            CREATE TABLE inventario_diario (
                id INTEGER PRIMARY KEY, sucursal_id INTEGER, fecha TEXT, estado TEXT,
                total_items INTEGER
            );
            CREATE TABLE inventario_detalle (
                id INTEGER PRIMARY KEY, inventario_id INTEGER, producto_id INTEGER,
                stock_sistema REAL, inicial REAL, ingreso_dia REAL,
                ingreso_manual REAL, disponible REAL, conteo_fisico REAL,
                final REAL, utilizada REAL, diferencia REAL, observaciones TEXT,
                categoria_id INTEGER, categoria_nombre TEXT, producto_nombre TEXT,
                codigo TEXT, unidad TEXT
            );
            CREATE TABLE inventario_ingresos_manuales (
                id INTEGER PRIMARY KEY, detalle_id INTEGER
            );
            CREATE TABLE stock (
                producto_id INTEGER, sucursal_id INTEGER, cantidad REAL,
                PRIMARY KEY (producto_id, sucursal_id)
            );
            INSERT INTO sucursales VALUES (10, 'AS Simon Lopez'), (20, 'America');
            INSERT INTO categorias VALUES (1, 'Alimentos');
            INSERT INTO productos VALUES
                (1, 'PRD-0656', 'PAPA HOLANDESA', NULL, 'kg', 10, 1, 0, 0, 1, 1),
                (2, 'PRD-0395', 'Papa Holandesa', NULL, 'KG', 20, 1, 0, 0, 0, 1);
            INSERT INTO lotes VALUES (1, 1, 10, 4335.4), (2, 2, 20, 35);
            INSERT INTO movimientos VALUES (1, 2);
            INSERT INTO pedidos VALUES (1, 'pendiente');
            INSERT INTO pedido_detalle VALUES (1, 1, 2);
            INSERT INTO inventario_diario VALUES (5, 20, '2026-10-16', 'abierto', 2);
            INSERT INTO inventario_detalle VALUES
                (51, 5, 1, 35, 35, 0, 0, 35, NULL, 0, 0, 0, NULL, 1, 'Alimentos', 'PAPA HOLANDESA', 'PRD-0656', 'kg'),
                (52, 5, 2, 72.4, 72.4, 0, 0, 72.4, NULL, 0, 0, 0, NULL, 1, 'Alimentos', 'PAPA HOLANDESA', 'PRD-0395', 'kg');
        """)
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(productos.productos_bp)
        self.client = self.app.test_client()
        self.db_patch = patch.object(
            productos, "get_conn", return_value=ConexionSQLite(self.db))
        self.db_patch.start()
        self.audit_patch = patch.object(productos, "registrar_auditoria")
        self.audit_patch.start()
        self.backup_patch = patch.object(
            productos, "volcar_a_archivo", return_value=(1, 1))
        self.backup_patch.start()
        with self.client.session_transaction() as sess:
            sess["user_id"] = 1
            sess["rol"] = "admin"

    def tearDown(self):
        self.audit_patch.stop()
        self.backup_patch.stop()
        self.db_patch.stop()
        self.db.close()

    def test_reporte_identifica_stock_por_sucursal_y_colision_de_planilla(self):
        respuesta = self.client.get("/api/productos/duplicados")
        self.assertEqual(respuesta.status_code, 200)
        grupo = respuesta.get_json()["data"]["grupos"][0]
        self.assertEqual(len(grupo["productos"]), 2)
        stock = {
            p["codigo"]: [(s["sucursal_nombre"], s["cantidad"]) for s in p["stocks"]]
            for p in grupo["productos"]
        }
        self.assertEqual(stock["PRD-0656"], [("AS Simon Lopez", 4335.4)])
        self.assertEqual(stock["PRD-0395"], [("America", 35.0)])
        self.assertEqual(grupo["proveedores_distintos"], 1)
        self.assertEqual(grupo["colisiones_planilla"][0]["estado"], "abierto")
        productos_por_codigo = {p["codigo"]: p for p in grupo["productos"]}
        self.assertEqual(
            productos_por_codigo["PRD-0395"]["referencias"]["pedidos_abiertos"], 1)

    def test_unifica_fichas_y_conserva_saldos_y_linea_diaria_unica(self):
        self.db.execute("UPDATE pedidos SET estado = 'entregado' WHERE id = 1")
        respuesta = self.client.post("/api/productos/duplicados", json={
            "confirmar": True, "canonico_id": 1, "duplicados": [2],
        })
        self.assertEqual(respuesta.status_code, 200, respuesta.get_json())
        self.assertEqual(self.db.execute(
            "SELECT activo FROM productos WHERE id = 2").fetchone()["activo"], 0)
        lotes = self.db.execute(
            "SELECT producto_id, sucursal_id, cantidad FROM lotes ORDER BY sucursal_id"
        ).fetchall()
        self.assertEqual([(r["producto_id"], r["sucursal_id"], r["cantidad"]) for r in lotes],
                         [(1, 10, 4335.4), (1, 20, 35.0)])
        lineas = self.db.execute(
            "SELECT producto_id, inicial FROM inventario_detalle WHERE inventario_id = 5"
        ).fetchall()
        self.assertEqual([(r["producto_id"], r["inicial"]) for r in lineas], [(1, 107.4)])
        self.assertEqual(self.db.execute(
            "SELECT producto_id FROM pedido_detalle WHERE id = 1").fetchone()["producto_id"], 1)
        self.assertTrue(respuesta.get_json()["data"]["respaldo"].startswith(
            "antes_unificar_pollos_lucho_"))

    def test_no_unifica_si_no_puede_crear_respaldo(self):
        self.db.execute("UPDATE pedidos SET estado = 'entregado' WHERE id = 1")
        with patch.object(productos, "volcar_a_archivo", side_effect=OSError("disco lleno")):
            respuesta = self.client.post("/api/productos/duplicados", json={
                "confirmar": True, "canonico_id": 1, "duplicados": [2],
            })
        self.assertEqual(respuesta.status_code, 500)
        self.assertEqual(self.db.execute(
            "SELECT activo FROM productos WHERE id = 2").fetchone()["activo"], 1)
        self.assertEqual(self.db.execute(
            "SELECT producto_id FROM pedido_detalle WHERE id = 1").fetchone()["producto_id"], 2)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM lotes WHERE producto_id = 2").fetchone()[0], 1)

    def test_no_unifica_si_el_conteo_fisico_de_una_linea_esta_incompleto(self):
        self.db.execute("UPDATE inventario_detalle SET conteo_fisico = 6 WHERE id = 51")
        respuesta = self.client.post("/api/productos/duplicados", json={
            "confirmar": True, "canonico_id": 1, "duplicados": [2],
        })
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(self.db.execute(
            "SELECT activo FROM productos WHERE id = 2").fetchone()["activo"], 1)

    def test_no_unifica_productos_de_as_proveedores_diferentes(self):
        self.db.execute("UPDATE productos SET para_proveer = 1 WHERE id = 2")
        respuesta = self.client.post("/api/productos/duplicados", json={
            "confirmar": True, "canonico_id": 1, "duplicados": [2],
        })
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM lotes WHERE producto_id = 2").fetchone()[0], 1)

    def test_no_unifica_mientras_haya_un_pedido_abierto(self):
        respuesta = self.client.post("/api/productos/duplicados", json={
            "confirmar": True, "canonico_id": 1, "duplicados": [2],
        })
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(self.db.execute(
            "SELECT producto_id FROM pedido_detalle WHERE id = 1").fetchone()["producto_id"], 2)

    def test_no_cambia_planillas_cerradas_con_lineas_duplicadas(self):
        self.db.execute("UPDATE inventario_diario SET estado = 'cerrado' WHERE id = 5")
        respuesta = self.client.post("/api/productos/duplicados", json={
            "confirmar": True, "canonico_id": 1, "duplicados": [2],
        })
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(self.db.execute(
            "SELECT COUNT(*) FROM inventario_detalle WHERE inventario_id = 5").fetchone()[0], 2)

    def test_solo_admin_puede_abrir_revision_y_unificar(self):
        with self.client.session_transaction() as sess:
            sess["rol"] = "encargado"
        respuesta = self.client.get("/api/productos/duplicados")
        self.assertEqual(respuesta.status_code, 403)
        respuesta = self.client.post("/api/productos/duplicados", json={
            "confirmar": True, "canonico_id": 1, "duplicados": [2],
        })
        self.assertEqual(respuesta.status_code, 403)


if __name__ == "__main__":
    unittest.main()
