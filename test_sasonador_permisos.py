import unittest
from unittest.mock import patch

from flask import Flask, session

from core.inventario import _ocultar_valores_inventario_sasonador
import core.pedidos as pedidos_module
from core.dashboard import _datos_dashboard_sasonador
from core.productos import _ocultar_datos_comerciales_productos, scope_productos
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

    def test_catalogo_sasonador_solo_sucursal_asignada(self):
        condicion, parametros = scope_productos(
            False, 42, "", solo_sucursal_ids=[42, 43])
        self.assertEqual(condicion, " AND p.sucursal_id IN (%s,%s)")
        self.assertEqual(parametros, [42, 43])
        condicion, parametros = scope_productos(False, None, "")
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


if __name__ == "__main__":
    unittest.main()
