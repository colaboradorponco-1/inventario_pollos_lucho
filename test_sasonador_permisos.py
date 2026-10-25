import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask, session

from core.inventario import _ocultar_valores_inventario_sasonador
import core.dashboard as dashboard_module
import core.lotes as lotes_module
import core.pedidos as pedidos_module
import core.sazonador as sazonador_module
from core.dashboard import _datos_dashboard_sasonador
from core.productos import (
    _ocultar_datos_comerciales_productos,
    _productos_sazonador_visibles,
    scope_productos,
)
from core.pedidos import _puede_ver_pedido
from core.productos import _sucursal_puede_proveer
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


class SqliteCompatConnection:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.create_function("GREATEST", -1, max)

    def execute(self, query, params=()):
        return self.conn.execute(query.replace(" FOR UPDATE", ""), params)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


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
        self.assertFalse(ruta_bloqueada_sasonador("/api/pedidos/15/archivar", "PUT"))
        self.assertTrue(ruta_bloqueada_sasonador("/api/pedidos/15", "DELETE"))
        self.assertTrue(ruta_bloqueada_sasonador(
            "/api/productos", "POST"))
        self.assertTrue(ruta_bloqueada_sasonador("/api/productos/15", "PUT"))
        self.assertTrue(ruta_bloqueada_sasonador("/api/usuarios", "GET"))
        self.assertTrue(ruta_bloqueada_sasonador("/api/sucursales", "GET"))
        self.assertFalse(ruta_bloqueada_sasonador(
            "/api/sazonador/productos", "POST"))
        self.assertFalse(ruta_bloqueada_sasonador(
            "/api/sazonador/productos/15", "PUT"))
        self.assertFalse(ruta_bloqueada_sasonador(
            "/api/sazonador/productos/15", "DELETE"))
        self.assertFalse(ruta_bloqueada_sasonador(
            "/api/sazonador/productos/15/restaurar", "POST"))
        self.assertFalse(ruta_bloqueada_sasonador(
            "/api/sazonador/inventario-diario", "POST"))
        self.assertFalse(ruta_bloqueada_sasonador(
            "/api/sazonador/inventario-diario/15", "PUT"))
        self.assertFalse(ruta_bloqueada_sasonador(
            "/api/sazonador/inventario-diario/15", "DELETE"))
        self.assertFalse(ruta_bloqueada_sasonador(
            "/api/sazonador/inventario-diario/15/cerrar", "POST"))

    def test_dashboard_productos_e_inventario_diario(self):
        for path in ("/api/dashboard", "/api/productos", "/api/inventario-diario",
                     "/api/inventario-diario/15", "/api/inventario-diario/15/excel",
                     "/inventario-diario/15/imprimir"):
            with self.subTest(path=path):
                self.assertFalse(ruta_bloqueada_sasonador(path, "GET"))
        self.assertTrue(ruta_bloqueada_sasonador("/api/dashboard/graficos", "GET"))
        self.assertFalse(ruta_bloqueada_sasonador("/api/inventario-diario", "POST"))
        self.assertFalse(ruta_bloqueada_sasonador("/api/inventario-diario/15", "PUT"))
        self.assertFalse(ruta_bloqueada_sasonador(
            "/api/inventario-diario/15/cerrar", "POST"))
        self.assertTrue(ruta_bloqueada_sasonador(
            "/api/inventario-diario/15", "DELETE"))
        self.assertTrue(ruta_bloqueada_sasonador(
            "/api/inventario-diario/15/cerrar", "DELETE"))

    def test_dashboard_sasonador_no_expone_finanzas(self):
        datos = {
            "rol": "sasonador", "total_productos": 4, "stock_total": 3,
            "valor_inventario": 100, "ventas_hoy": 20, "gastos_hoy": 10,
            "utilidad_hoy": 10, "sesiones_recientes": [{"usuario": "admin"}],
            "stock_bajo": [{
                "id": 1, "nombre": "Pollo", "codigo": "P1",
                "stock_minimo": 2, "unidad": "kg", "stock": 1,
                "costo_promedio": 5, "precio_venta": 8,
            }],
            "mov_recientes": [{
                "fecha": "2025-01-01", "tipo": "entrada", "cantidad": 2,
                "nota": "Compra", "producto_nombre": "Pollo", "unidad": "kg",
                "precio_unitario": 5, "usuario": "admin",
            }],
            "top_entrada": [], "top_salida": [], "por_vencer_hoy": 0,
            "total_alertas": 1, "por_vencer": [],
        }
        resultado = _datos_dashboard_sasonador(datos)
        self.assertEqual(resultado["total_productos"], 4)
        self.assertNotIn("ventas_hoy", resultado)
        self.assertNotIn("sesiones_recientes", resultado)
        self.assertNotIn("costo_promedio", resultado["stock_bajo"][0])
        self.assertNotIn("precio_unitario", resultado["mov_recientes"][0])

    def test_dashboard_sasonador_sin_sucursal_no_muestra_datos_globales(self):
        session["user_id"] = 7
        session.pop("sucursal_id")
        with patch.object(dashboard_module, "get_conn", return_value=FakeConn()):
            _, status = dashboard_module.dashboard()
        self.assertEqual(status, 403)

    def test_catalogo_sasonador_solo_sucursal_asignada(self):
        condicion, parametros = scope_productos(
            False, 42, "", solo_sucursal_ids=[42, 43])
        self.assertEqual(condicion, " AND p.sucursal_id IN (%s,%s)")
        self.assertEqual(parametros, [42, 43])
        condicion, parametros = scope_productos(False, None, "")
        self.assertEqual(condicion, " AND 1=0")
        self.assertEqual(parametros, [])
        condicion, parametros = scope_productos(False, None, "", para_pedido=True)
        self.assertEqual(condicion, " AND 1=0")
        self.assertEqual(parametros, [])

    def test_catalogo_productos_oculta_costos_y_proveedores(self):
        productos = [{
            "id": 1, "nombre": "Pollo", "costo_promedio": 5,
            "precio_venta": 8, "proveedor_id": 3, "proveedor_nombre": "Proveedor",
        }]
        resultado = _ocultar_datos_comerciales_productos(productos)
        self.assertEqual(resultado, [{"id": 1, "nombre": "Pollo"}])

    def test_inventario_sasonador_oculta_valores_comerciales(self):
        datos = {
            "valor_diferencia": 12.5,
            "resumen": {"valor_diferencia": 12.5, "total_items": 1},
            "lineas": [{
                "producto": "Pollo", "costo_promedio": 5,
                "precio_venta": 8, "diferencia": -1,
            }],
        }
        resultado = _ocultar_valores_inventario_sasonador(datos)
        self.assertNotIn("valor_diferencia", resultado)
        self.assertNotIn("valor_diferencia", resultado["resumen"])
        self.assertEqual(resultado["lineas"], [{"producto": "Pollo", "diferencia": -1}])

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


class InventarioSazonadorSeparadoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp.name, "inventario.db")
        conn = sqlite3.connect(self.db_path)
        conn.executescript("""
            CREATE TABLE sucursales (
                id INTEGER PRIMARY KEY, nombre TEXT, principal INTEGER DEFAULT 0,
                provee INTEGER DEFAULT 0, es_as INTEGER DEFAULT 0
            );
            INSERT INTO sucursales VALUES (30, 'Almacén Principal', 1, 0, 0);
            INSERT INTO sucursales VALUES (42, 'Sazonador Simon Lopez', 0, 0, 0);
            CREATE TABLE almacenes (
                id INTEGER PRIMARY KEY, nombre TEXT, sucursal_id INTEGER
            );
            INSERT INTO almacenes VALUES (90, 'Almacén Principal', 30);
            INSERT INTO almacenes VALUES (91, 'Sazonador Simon Lopez', 42);
            CREATE TABLE usuarios (
                id INTEGER PRIMARY KEY, sucursal_id INTEGER, receptor INTEGER DEFAULT 0,
                rol TEXT, activo INTEGER DEFAULT 1
            );
            CREATE TABLE categorias (id INTEGER PRIMARY KEY, nombre TEXT);
            INSERT INTO categorias VALUES (1, 'Insumos');
            CREATE TABLE productos (
                id INTEGER PRIMARY KEY, sucursal_id INTEGER, nombre TEXT,
                stock_actual REAL, activo INTEGER DEFAULT 1,
                para_proveer INTEGER DEFAULT 1, unidad TEXT DEFAULT 'unidad',
                costo_promedio REAL DEFAULT 0, unidad_tacho REAL DEFAULT 0,
                pide_tacho INTEGER DEFAULT 0
            );
            INSERT INTO productos
                (id, sucursal_id, nombre, stock_actual, activo, para_proveer, unidad)
            VALUES (1, 42, 'Producto del encargado', 18, 1, 0, 'kg');
            INSERT INTO productos
                (id, sucursal_id, nombre, stock_actual, activo, para_proveer, unidad)
            VALUES (2, 30, 'Producto proveedor', 12, 1, 1, 'kg');
            CREATE TABLE sazonador_productos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, sucursal_id INTEGER NOT NULL,
                almacen_id INTEGER,
                codigo TEXT, nombre TEXT NOT NULL, marca TEXT, categoria_id INTEGER,
                unidad TEXT NOT NULL, stock_minimo REAL NOT NULL, stock_actual REAL NOT NULL,
                producto_pedido_id INTEGER,
                vencimiento TEXT, creado_por TEXT, activo INTEGER NOT NULL DEFAULT 1,
                UNIQUE (sucursal_id, codigo)
            );
            CREATE TABLE sazonador_inventarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, sucursal_id INTEGER NOT NULL,
                almacen_id INTEGER,
                categoria_id INTEGER NOT NULL DEFAULT 0, fecha TEXT NOT NULL,
                hora_corte TEXT, usuario TEXT, cerrado_por TEXT, estado TEXT NOT NULL,
                fecha_hora_cierre TEXT, observaciones TEXT, total_items INTEGER DEFAULT 0,
                total_faltantes INTEGER DEFAULT 0, total_sobrantes INTEGER DEFAULT 0,
                UNIQUE (sucursal_id, categoria_id, fecha)
            );
            CREATE TABLE sazonador_inventario_detalle (
                id INTEGER PRIMARY KEY AUTOINCREMENT, inventario_id INTEGER NOT NULL,
                producto_id INTEGER NOT NULL, categoria_id INTEGER, producto_nombre TEXT,
                codigo TEXT, unidad TEXT, stock_sistema REAL, inicial REAL,
                disponible REAL, conteo_fisico REAL, final REAL DEFAULT 0,
                utilizada REAL DEFAULT 0, diferencia REAL DEFAULT 0,
                observaciones TEXT, UNIQUE (inventario_id, producto_id)
            );
            CREATE TABLE pedidos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, nro_ticket TEXT, fecha TEXT,
                sucursal_id INTEGER, destino_id INTEGER, estado TEXT, total REAL,
                usuario TEXT, nota TEXT
            );
            CREATE TABLE pedido_detalle (
                id INTEGER PRIMARY KEY AUTOINCREMENT, pedido_id INTEGER,
                producto_id INTEGER, producto_nombre TEXT, cantidad REAL,
                destino_id INTEGER, unidad TEXT DEFAULT 'kg',
                tacho_fraccion REAL DEFAULT 0, tacho_texto TEXT DEFAULT '',
                tacho_unidad REAL DEFAULT 0, sin_stock INTEGER DEFAULT 0,
                producto_sazonador_id INTEGER
            );
            CREATE TABLE repartos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT, sucursal_id INTEGER,
                origen_sucursal_id INTEGER, total REAL, usuario TEXT, nota TEXT,
                pedido_id INTEGER
            );
            CREATE TABLE reparto_detalle (
                id INTEGER PRIMARY KEY AUTOINCREMENT, reparto_id INTEGER,
                producto_id INTEGER, producto_nombre TEXT, cantidad REAL,
                costo_unitario REAL, subtotal REAL
            );
        """)
        conn.commit()
        conn.close()
        self.app = Flask(__name__)
        self.app.secret_key = "test"
        self.app.register_blueprint(sazonador_module.sazonador_bp)
        self.app.register_blueprint(pedidos_module.pedidos_bp)
        self.client = self.app.test_client()
        self.patch_conn = patch.object(
            sazonador_module, "get_conn",
            side_effect=lambda: SqliteCompatConnection(self.db_path))
        self.patch_conn.start()
        self.addCleanup(self.patch_conn.stop)
        self.addCleanup(self.temp.cleanup)

    def test_producto_y_conteo_usan_stock_separado_del_encargado(self):
        with self.client.session_transaction() as state:
            state.update({
                "user_id": 7, "rol": "sasonador", "sucursal_id": 42,
                "usuario": "sazonador_prueba",
            })
        crear = self.client.post("/api/sazonador/productos", json={
            "nombre": "Adobo del Sazonador", "categoria_id": 1,
            "unidad": "kg", "stock_minimo": 2, "stock_inicial": 5,
        })
        self.assertEqual(crear.status_code, 200)
        product_id = crear.json["data"]["id"]

        listado = self.client.get("/api/sazonador/productos")
        self.assertEqual([p["nombre"] for p in listado.json["data"]],
                         ["Adobo del Sazonador"])
        self.assertEqual(listado.json["data"][0]["almacen_id"], 91)
        self.assertEqual(
            listado.json["data"][0]["almacen_nombre"], "Sazonador Simon Lopez")
        self.assertTrue(listado.json["data"][0]["es_inventario_sazonador"])
        inicio = self.client.post("/api/sazonador/inventario-diario")
        self.assertEqual(inicio.status_code, 200)
        inventario_id = inicio.json["data"]["id"]
        conn = sqlite3.connect(self.db_path)
        registro_inventario = conn.execute(
            "SELECT almacen_id FROM sazonador_inventarios WHERE id = ?",
            (inventario_id,)).fetchone()
        conn.close()
        self.assertEqual(registro_inventario[0], 91)
        self.assertEqual(len(self.client.get(
            "/api/sazonador/inventario-diario").json["data"]), 1)
        self.assertEqual(self.client.get(
            "/api/sazonador/inventario-diario?desde=9999-01-01").json["data"], [])
        detalle = self.client.get(
            f"/api/sazonador/inventario-diario/{inventario_id}")
        self.assertEqual(
            detalle.json["data"]["almacen_nombre"], "Sazonador Simon Lopez")
        linea = detalle.json["data"]["lineas"][0]
        self.assertEqual(linea["inicial"], 5)
        guardado = self.client.put(
            f"/api/sazonador/inventario-diario/{inventario_id}",
            json={"lineas": [{
                "id": linea["id"], "campos_modificados": ["conteo_fisico"],
                "conteo_fisico": 3,
            }]})
        self.assertEqual(guardado.status_code, 200)
        guardado_vacio = self.client.put(
            f"/api/sazonador/inventario-diario/{inventario_id}",
            json={"lineas": [{
                "id": linea["id"], "campos_modificados": ["conteo_fisico"],
                "conteo_fisico": None,
            }]})
        self.assertEqual(guardado_vacio.status_code, 200)
        detalle_vacio = self.client.get(
            f"/api/sazonador/inventario-diario/{inventario_id}")
        self.assertEqual(
            detalle_vacio.json["data"]["lineas"][0]["diferencia"], 0)
        cierre_incompleto = self.client.post(
            f"/api/sazonador/inventario-diario/{inventario_id}/cerrar")
        self.assertEqual(cierre_incompleto.status_code, 400)
        self.assertEqual(self.client.put(
            f"/api/sazonador/inventario-diario/{inventario_id}",
            json={"lineas": [{
                "id": linea["id"], "campos_modificados": ["conteo_fisico"],
                "conteo_fisico": 3,
            }]}).status_code, 200)
        cierre = self.client.post(
            f"/api/sazonador/inventario-diario/{inventario_id}/cerrar")
        self.assertEqual(cierre.status_code, 200)
        borrado_cerrado = self.client.delete(
            f"/api/sazonador/inventario-diario/{inventario_id}")
        self.assertEqual(borrado_cerrado.status_code, 400)

        conn = sqlite3.connect(self.db_path)
        stock_sazonador = conn.execute(
            "SELECT stock_actual FROM sazonador_productos WHERE id = ?",
            (product_id,)).fetchone()[0]
        stock_encargado = conn.execute(
            "SELECT stock_actual FROM productos WHERE sucursal_id = 42").fetchone()[0]
        conn.close()
        self.assertEqual(stock_sazonador, 3)
        self.assertEqual(stock_encargado, 18)
        inventario_duplicado = self.client.post(
            "/api/sazonador/inventario-diario?categoria_id=1")
        self.assertEqual(inventario_duplicado.status_code, 409)

    def test_sazonador_puede_editar_sus_productos_sin_cambiar_stock(self):
        with self.client.session_transaction() as state:
            state.update({
                "user_id": 7, "rol": "sasonador", "sucursal_id": 42,
                "usuario": "sazonador_prueba",
            })
        crear = self.client.post("/api/sazonador/productos", json={
            "nombre": "Producto original", "codigo": "SAZ-EDIT-1",
            "unidad": "kg", "stock_minimo": 2, "stock_inicial": 7,
        })
        self.assertEqual(crear.status_code, 200)
        producto_id = crear.json["data"]["id"]

        respuesta = self.client.put(f"/api/sazonador/productos/{producto_id}", json={
            "nombre": "Producto actualizado", "codigo": "SAZ-EDIT-2",
            "marca": "Marca nueva", "categoria_id": 1, "unidad": "kg",
            "stock_minimo": 4, "vencimiento": "2027-01-15",
            "producto_pedido_id": 2,
        })
        self.assertEqual(respuesta.status_code, 200)

        listado = self.client.get("/api/sazonador/productos")
        producto = listado.json["data"][0]
        self.assertEqual(producto["nombre"], "Producto actualizado")
        self.assertEqual(producto["codigo"], "SAZ-EDIT-2")
        self.assertEqual(producto["marca"], "Marca nueva")
        self.assertEqual(producto["stock_minimo"], 4)
        self.assertEqual(producto["producto_pedido_id"], 2)
        self.assertEqual(producto["producto_pedido_nombre"], "Producto proveedor")
        self.assertEqual(producto["stock"], 7)

        with self.client.session_transaction() as state:
            state["rol"] = "admin"
        prohibido = self.client.put(f"/api/sazonador/productos/{producto_id}", json={
            "nombre": "Cambio no autorizado",
        })
        self.assertEqual(prohibido.status_code, 403)

    def test_producto_sazonador_se_desactiva_y_restaura_sin_borrar_historial(self):
        with self.client.session_transaction() as state:
            state.update({
                "user_id": 7, "rol": "sasonador", "sucursal_id": 42,
                "usuario": "sazonador_prueba",
            })
        creado = self.client.post("/api/sazonador/productos", json={
            "nombre": "Producto reversible", "codigo": "SAZ-REST-1",
            "unidad": "kg", "stock_inicial": 0,
        })
        self.assertEqual(creado.status_code, 200)
        producto_id = creado.json["data"]["id"]

        desactivar = self.client.delete(f"/api/sazonador/productos/{producto_id}")
        self.assertEqual(desactivar.status_code, 200)
        inactivos = self.client.get("/api/sazonador/productos?estado=inactivos")
        self.assertEqual([p["id"] for p in inactivos.json["data"]], [producto_id])

        restaurar = self.client.post(
            f"/api/sazonador/productos/{producto_id}/restaurar")
        self.assertEqual(restaurar.status_code, 200)
        activos = self.client.get("/api/sazonador/productos")
        self.assertEqual([p["id"] for p in activos.json["data"]], [producto_id])

    def test_producto_sazonador_no_se_desactiva_con_stock_o_inventario_abierto(self):
        with self.client.session_transaction() as state:
            state.update({
                "user_id": 7, "rol": "sasonador", "sucursal_id": 42,
                "usuario": "sazonador_prueba",
            })
        creado = self.client.post("/api/sazonador/productos", json={
            "nombre": "Producto protegido", "codigo": "SAZ-PROTEGIDO",
            "unidad": "kg", "stock_inicial": 2,
        })
        producto_id = creado.json["data"]["id"]
        con_stock = self.client.delete(f"/api/sazonador/productos/{producto_id}")
        self.assertEqual(con_stock.status_code, 409)

        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE sazonador_productos SET stock_actual = 0 WHERE id = ?",
            (producto_id,))
        conn.execute("""
            INSERT INTO sazonador_inventarios
                (sucursal_id, almacen_id, categoria_id, fecha, estado)
            VALUES (42, 91, 0, '2025-03-01', 'abierto')
        """)
        inventario_id = conn.execute(
            "SELECT id FROM sazonador_inventarios").fetchone()[0]
        conn.execute("""
            INSERT INTO sazonador_inventario_detalle
                (inventario_id, producto_id, producto_nombre)
            VALUES (?, ?, 'Producto protegido')
        """, (inventario_id, producto_id))
        conn.commit()
        conn.close()
        en_inventario = self.client.delete(
            f"/api/sazonador/productos/{producto_id}")
        self.assertEqual(en_inventario.status_code, 409)

    def test_actualizar_stock_inicial_refresca_sin_perder_conteo_u_observaciones(self):
        with self.client.session_transaction() as state:
            state.update({
                "user_id": 7, "rol": "sasonador", "sucursal_id": 42,
                "usuario": "sazonador_prueba",
            })
        creado = self.client.post("/api/sazonador/productos", json={
            "nombre": "Producto para contar", "unidad": "kg",
            "stock_inicial": 5,
        })
        producto_id = creado.json["data"]["id"]
        inventario = self.client.post("/api/sazonador/inventario-diario")
        inventario_id = inventario.json["data"]["id"]
        detalle = self.client.get(
            f"/api/sazonador/inventario-diario/{inventario_id}").json["data"]
        linea = detalle["lineas"][0]
        self.assertEqual(linea["stock_sistema"], 5)

        guardado = self.client.put(
            f"/api/sazonador/inventario-diario/{inventario_id}",
            json={"lineas": [{
                "id": linea["id"],
                "campos_modificados": ["conteo_fisico", "observaciones"],
                "conteo_fisico": 3,
                "observaciones": "Conteo confirmado",
            }]})
        self.assertEqual(guardado.status_code, 200)
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE sazonador_productos SET stock_actual = 9 WHERE id = ?",
            (producto_id,))
        conn.commit()
        conn.close()

        refresco_normal = self.client.get(
            f"/api/sazonador/inventario-diario/{inventario_id}")
        self.assertEqual(
            refresco_normal.json["data"]["lineas"][0]["stock_sistema"], 5)
        refresco = self.client.get(
            f"/api/sazonador/inventario-diario/{inventario_id}?actualizar_stock=1")
        linea_actualizada = refresco.json["data"]["lineas"][0]
        self.assertEqual(linea_actualizada["stock_sistema"], 9)
        self.assertEqual(linea_actualizada["conteo_fisico"], 3)
        self.assertEqual(linea_actualizada["diferencia"], -6)
        self.assertEqual(linea_actualizada["utilizada"], 6)
        self.assertEqual(linea_actualizada["observaciones"], "Conteo confirmado")

    def test_planilla_sazonador_abierta_se_borra_sin_mover_stock(self):
        with self.client.session_transaction() as state:
            state.update({
                "user_id": 7, "rol": "sasonador", "sucursal_id": 42,
                "usuario": "sazonador_prueba",
            })
        creado = self.client.post("/api/sazonador/productos", json={
            "nombre": "Producto planilla", "unidad": "kg", "stock_inicial": 6,
        })
        product_id = creado.json["data"]["id"]
        inventario = self.client.post("/api/sazonador/inventario-diario")
        inventario_id = inventario.json["data"]["id"]

        borrar = self.client.delete(
            f"/api/sazonador/inventario-diario/{inventario_id}")
        self.assertEqual(borrar.status_code, 200)
        conn = sqlite3.connect(self.db_path)
        stock = conn.execute(
            "SELECT stock_actual FROM sazonador_productos WHERE id = ?",
            (product_id,)).fetchone()[0]
        existe = conn.execute(
            "SELECT 1 FROM sazonador_inventarios WHERE id = ?",
            (inventario_id,)).fetchone()
        conn.close()
        self.assertEqual(stock, 6)
        self.assertIsNone(existe)

    def test_sazonador_no_es_proveedor_de_ninguna_sucursal(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO usuarios (sucursal_id, rol, activo) "
            "VALUES (42, 'sasonador', 1)")
        conn.execute(
            "UPDATE sucursales SET provee = 1, es_as = 1 WHERE id = 42")
        conn.commit()
        destinos = lotes_module.destinos_validos(conn)
        self.assertNotIn(42, destinos)
        self.assertFalse(_sucursal_puede_proveer(conn, 42))
        conn.close()

    def test_sazonador_puede_crear_pedido_sin_vincular_fichas_antes(self):
        with self.client.session_transaction() as state:
            state.update({
                "user_id": 7, "rol": "sasonador", "sucursal_id": 42,
                "usuario": "sazonador_prueba",
            })
        conn_factory = lambda: SqliteCompatConnection(self.db_path)
        with patch.object(pedidos_module, "get_conn", side_effect=conn_factory), \
                patch.object(pedidos_module, "destinos_validos", return_value=[30]), \
                patch.object(pedidos_module, "stock_por_destino",
                             return_value={2: [(30, 12, "Almacén Principal")]}), \
                patch.object(pedidos_module, "_ids_proveedores_misma_ciudad",
                             return_value=None), \
                patch.object(pedidos_module, "elegir_destino",
                             return_value=(30, 12)), \
                patch.object(pedidos_module, "stock_en_destino", return_value=12), \
                patch.object(pedidos_module, "_nro_ticket",
                             return_value="TKT-00003"), \
                patch.object(pedidos_module, "registrar_auditoria"):
            respuesta = self.client.post("/api/pedidos", json={
                "detalle": [{"producto_id": 2, "cantidad": 3}],
            })

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(respuesta.json["data"]["nro_ticket"], "TKT-00003")
        conn = sqlite3.connect(self.db_path)
        linea = conn.execute("""
            SELECT producto_id, producto_sazonador_id
            FROM pedido_detalle WHERE pedido_id = ?
        """, (respuesta.json["data"]["id"],)).fetchone()
        conn.close()
        self.assertEqual(linea, (2, None))

    def test_sazonador_solo_puede_pedir_a_almacenes_principales(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO sucursales VALUES (43, 'America', 0, 1, 0)")
        conn.execute("""
            INSERT INTO productos
                (id, sucursal_id, nombre, stock_actual, activo, para_proveer, unidad)
            VALUES (3, 43, 'Producto de America', 8, 1, 1, 'kg')
        """)
        conn.commit()
        conn.close()

        with self.client.session_transaction() as state:
            state.update({
                "user_id": 7, "rol": "sasonador", "sucursal_id": 42,
                "usuario": "sazonador_prueba",
            })
        conn_factory = lambda: SqliteCompatConnection(self.db_path)
        with patch.object(pedidos_module, "get_conn", side_effect=conn_factory), \
                patch.object(pedidos_module, "destinos_validos",
                             return_value=[30, 43]), \
                patch.object(pedidos_module, "stock_por_destino",
                             return_value={2: [
                                 (30, 12, "Almacén Principal"),
                                 (43, 8, "America"),
                             ]}), \
                patch.object(pedidos_module, "_ids_proveedores_misma_ciudad",
                             return_value=None), \
                patch.object(pedidos_module, "_nombre_sucursal",
                             side_effect=lambda conn, sid:
                             {30: "Almacén Principal", 43: "America"}.get(sid, sid)):
            respuesta = self.client.post("/api/pedidos", json={
                "destino_id": 43,
                "detalle": [{"producto_id": 2, "cantidad": 3}],
            })
            producto_no_principal = self.client.post("/api/pedidos", json={
                "detalle": [{"producto_id": 3, "cantidad": 1}],
            })

        self.assertEqual(respuesta.status_code, 400)
        self.assertIn("no pueden ser destino", respuesta.json["message"])
        self.assertEqual(producto_no_principal.status_code, 400)
        self.assertIn("Almacenes Principales",
                      producto_no_principal.json["message"])
        conn = sqlite3.connect(self.db_path)
        cantidad_pedidos = conn.execute(
            "SELECT COUNT(*) FROM pedidos").fetchone()[0]
        conn.close()
        self.assertEqual(cantidad_pedidos, 0)

    def test_entrega_pedido_suma_stock_al_producto_sazonador_vinculado(self):
        with self.client.session_transaction() as state:
            state.update({
                "user_id": 7, "rol": "sasonador", "sucursal_id": 42,
                "usuario": "sazonador_prueba",
            })
        creado = self.client.post("/api/sazonador/productos", json={
            "nombre": "Adobo Sazonador", "unidad": "kg",
            "stock_inicial": 4, "producto_pedido_id": 2,
        })
        self.assertEqual(creado.status_code, 200)
        producto_sazonador_id = creado.json["data"]["id"]

        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO usuarios (sucursal_id, rol, activo) VALUES (42, 'sasonador', 1)")
        conn.execute(
            "INSERT INTO pedidos (id, nro_ticket, sucursal_id, destino_id) "
            "VALUES (1, 'TKT-00001', 42, 30)")
        conn.execute("""
            INSERT INTO pedido_detalle
                (id, pedido_id, producto_id, producto_nombre, cantidad,
                 destino_id, producto_sazonador_id)
            VALUES (1, 1, 2, 'Producto proveedor', 3, 30, ?)
        """, (producto_sazonador_id,))
        conn.commit()
        conn.close()

        movimientos = []
        with self.app.test_request_context("/"):
            session.update({
                "user_id": 7, "rol": "repartidor", "sucursal_id": 42,
                "usuario": "repartidor_prueba",
            })
            with patch.object(pedidos_module, "stock_actual", return_value=12), \
                    patch.object(pedidos_module, "registrar_movimiento",
                                 side_effect=lambda *args: movimientos.append(args)):
                conn = SqliteCompatConnection(self.db_path)
                try:
                    total, repartos = pedidos_module._despachar_stock(
                        conn,
                        {"id": 1, "nro_ticket": "TKT-00001", "sucursal_id": 42,
                         "destino_id": 30},
                        origen_propio=False)
                    conn.commit()
                finally:
                    conn.close()

        self.assertEqual(total, 0)
        self.assertEqual(len(repartos), 1)
        self.assertEqual(len(movimientos), 1)
        self.assertEqual(movimientos[0][1:3], (2, "salida"))
        conn = sqlite3.connect(self.db_path)
        stock_sazonador = conn.execute(
            "SELECT stock_actual FROM sazonador_productos WHERE id = ?",
            (producto_sazonador_id,)).fetchone()[0]
        stock_producto_normal = conn.execute(
            "SELECT stock_actual FROM productos WHERE id = 2").fetchone()[0]
        conn.close()
        self.assertEqual(stock_sazonador, 7)
        self.assertEqual(stock_producto_normal, 12)

    def test_entrega_pedido_crea_ficha_sazonador_si_no_hay_vinculo(self):
        with self.client.session_transaction() as state:
            state.update({
                "user_id": 7, "rol": "sasonador", "sucursal_id": 42,
                "usuario": "sazonador_prueba",
            })
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO usuarios (sucursal_id, rol, activo) VALUES (42, 'sasonador', 1)")
        conn.execute(
            "INSERT INTO pedidos (id, nro_ticket, sucursal_id, destino_id) "
            "VALUES (2, 'TKT-00002', 42, 30)")
        conn.execute("""
            INSERT INTO pedido_detalle
                (id, pedido_id, producto_id, producto_nombre, cantidad,
                 destino_id, producto_sazonador_id)
            VALUES (2, 2, 2, 'Producto proveedor', 3, 30, NULL)
        """)
        conn.commit()
        conn.close()

        movimientos = []
        with self.app.test_request_context("/"):
            session.update({
                "user_id": 7, "rol": "repartidor", "sucursal_id": 42,
                "usuario": "repartidor_prueba",
            })
            with patch.object(pedidos_module, "stock_actual", return_value=12), \
                    patch.object(pedidos_module, "registrar_movimiento",
                                 side_effect=lambda *args: movimientos.append(args)):
                conn = SqliteCompatConnection(self.db_path)
                try:
                    total, repartos = pedidos_module._despachar_stock(
                        conn,
                        {"id": 2, "nro_ticket": "TKT-00002", "sucursal_id": 42,
                         "destino_id": 30},
                        origen_propio=False)
                    conn.commit()
                finally:
                    conn.close()

        self.assertEqual(total, 0)
        self.assertEqual(len(repartos), 1)
        self.assertEqual(len(movimientos), 1)
        conn = sqlite3.connect(self.db_path)
        ficha = conn.execute("""
            SELECT nombre, unidad, stock_actual
            FROM sazonador_productos
            WHERE sucursal_id = 42 AND almacen_id = 91
        """).fetchone()
        stock_producto_normal = conn.execute(
            "SELECT stock_actual FROM productos WHERE id = 2").fetchone()[0]
        conn.close()
        self.assertEqual(ficha, ("Producto proveedor", "kg", 3))
        self.assertEqual(stock_producto_normal, 12)

    def test_exige_el_almacen_especifico_de_la_sucursal_sazonador(self):
        with self.client.session_transaction() as state:
            state.update({
                "user_id": 7, "rol": "sasonador", "sucursal_id": 42,
                "usuario": "sazonador_prueba",
            })
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "DELETE FROM almacenes WHERE id = 91")
        conn.commit()
        conn.close()
        response = self.client.post("/api/sazonador/productos", json={
            "nombre": "No debe guardarse", "stock_inicial": 1,
        })
        self.assertEqual(response.status_code, 409)

    def test_producto_sazonador_aparece_en_filtro_de_sucursal(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            INSERT INTO sazonador_productos
                (sucursal_id, almacen_id, codigo, nombre, categoria_id, unidad,
                 stock_minimo, stock_actual, activo)
            VALUES (42, 91, 'SAZ-42-000001', 'Adobo', 1, 'kg', 2, 5, 1)
        """)
        conn.commit()
        with self.app.test_request_context("/api/productos"):
            session["rol"] = "admin"
            productos, total = _productos_sazonador_visibles(
                conn, 42, "", None, None, "", True, False, 0, 10)
            self.assertEqual(total, 1)
            self.assertEqual(len(productos), 1)
            self.assertEqual(productos[0]["stock"], 5)
            self.assertEqual(productos[0]["sucursal_id"], 42)
            self.assertEqual(productos[0]["almacen_nombre"], "Sazonador Simon Lopez")
            self.assertTrue(productos[0]["es_inventario_sazonador"])
            no_pedibles, total_no_pedibles = _productos_sazonador_visibles(
                conn, 42, "", None, None, "", True, True, 0, 10)
            self.assertEqual(no_pedibles, [])
            self.assertEqual(total_no_pedibles, 0)
            otra_sucursal, total_otra_sucursal = _productos_sazonador_visibles(
                conn, 43, "", None, None, "", True, False, 0, 10)
            self.assertEqual(otra_sucursal, [])
            self.assertEqual(total_otra_sucursal, 0)
        conn.close()


if __name__ == "__main__":
    unittest.main()
