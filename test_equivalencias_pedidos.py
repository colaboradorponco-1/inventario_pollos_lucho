import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, session  # noqa: E402

import core.pedidos as pedidos  # noqa: E402
import core.productos as productos  # noqa: E402


class _FilaConn:
    def __init__(self, fila, filas=None):
        self.fila = fila
        self.filas = filas or []

    def execute(self, _sql, _params=None):
        return self

    def fetchone(self):
        return self.fila

    def fetchall(self):
        return self.filas


class _DespachoConn:
    def __init__(self):
        self.sql = ""
        self.params = ()
        self.queries = []
        self.lastrowid = 57

    def execute(self, sql, params=None):
        self.sql = " ".join(str(sql).split())
        self.params = params or ()
        self.queries.append((self.sql, self.params))
        return self

    def fetchone(self):
        if "SELECT nombre FROM sucursales" in self.sql:
            return {"nombre": "Sucursal"}
        if "SELECT costo_promedio FROM productos" in self.sql:
            return {"costo_promedio": 3.5}
        return None


class _EquivalenciaApiConn:
    def __init__(self, unidad_destino="unidad", sucursal_destino=35):
        self.unidad_destino = unidad_destino
        self.sucursal_destino = sucursal_destino
        self.inserts = []
        self.commits = 0
        self.closed = False

    def execute(self, sql, params=None):
        if "SELECT id, unidad FROM productos" in sql:
            self.fila = {"id": params[0], "unidad": "unidad"}
        elif "SELECT id, sucursal_id, unidad FROM productos" in sql:
            self.fila = {
                "id": params[0],
                "sucursal_id": self.sucursal_destino,
                "unidad": self.unidad_destino,
            }
        elif "SELECT id FROM sucursales" in sql:
            self.fila = {"id": params[0]}
        else:
            self.inserts.append((sql, params))
            self.fila = None
        return self

    def fetchone(self):
        return self.fila

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class _ReparacionConn:
    def __init__(self):
        self.sql = ""
        self.params = ()
        self.reparada = False
        self.commits = 0

    def execute(self, sql, params=None):
        self.sql = " ".join(str(sql).split())
        self.params = params or ()
        if "INSERT INTO pedido_stock_reparaciones" in self.sql:
            self.reparada = True
        return self

    def fetchone(self):
        if "FROM pedidos WHERE nro_ticket" in self.sql:
            return {
                "id": 46, "nro_ticket": "TKT-00046", "estado": "entregado",
                "etapa": "entregado", "stock_movido": 1, "sucursal_id": 35,
                "destino_id": 49,
            }
        if "FROM pedido_stock_reparaciones" in self.sql:
            return {"pedido_id": 46} if self.reparada else None
        if "SUM(cantidad)" in self.sql:
            if "tipo = 'salida'" in self.sql:
                return {"cantidad": 4}
            if "nota IN" in self.sql:
                return {"cantidad": 0}
            return {"cantidad": 4}
        if "SELECT codigo, nombre FROM productos" in self.sql:
            return {"codigo": "LLA-662", "nombre": "llajua"}
        return None

    def fetchall(self):
        if "FROM pedido_detalle WHERE pedido_id" in self.sql:
            return [{
                "producto_id": 661, "producto_nombre": "llajua",
                "cantidad": 4, "origen_id": 49,
            }]
        return []

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


class EquivalenciasPedidosTest(unittest.TestCase):
    def test_receptor_resuelve_equivalencia_661_a_662(self):
        conn = _FilaConn({
            "sucursal_origen_id": 49,
            "unidad_origen": "unidad",
            "equivalencia_destino_id": 662,
            "producto_destino_id": 662,
            "sucursal_producto_destino": 35,
            "unidad_destino": "unidad",
            "destino_activo": 1,
        })
        self.assertEqual(pedidos._producto_receptor(conn, 661, 35), (662, None))

    def test_receptor_rechaza_falta_de_equivalencia_entre_sucursales(self):
        conn = _FilaConn({
            "sucursal_origen_id": 49,
            "nombre_origen": "Producto sin ficha",
            "unidad_origen": "unidad",
            "equivalencia_destino_id": None,
            "producto_destino_id": None,
            "sucursal_producto_destino": None,
            "unidad_destino": None,
            "destino_activo": None,
        })
        destino, error = pedidos._producto_receptor(conn, 661, 35)
        self.assertIsNone(destino)
        self.assertIn("No hay equivalencia configurada", error)

    def test_receptor_usa_ficha_local_unica_con_mismo_nombre_y_unidad(self):
        conn = _FilaConn({
            "sucursal_origen_id": 49,
            "nombre_origen": "Plátano de freír",
            "unidad_origen": "kg",
            "equivalencia_destino_id": None,
            "producto_destino_id": None,
            "sucursal_producto_destino": None,
            "unidad_destino": None,
            "destino_activo": None,
        }, filas=[{"id": 662, "nombre": "Platano de freir", "unidad": "kg"}])
        self.assertEqual(pedidos._producto_receptor(conn, 661, 35), (662, None))

    def test_receptor_rechaza_coincidencias_ambiguas_o_unidades_distintas(self):
        origen = {
            "sucursal_origen_id": 49,
            "nombre_origen": "Papa holandesa",
            "unidad_origen": "kg",
            "equivalencia_destino_id": None,
            "producto_destino_id": None,
            "sucursal_producto_destino": None,
            "unidad_destino": None,
            "destino_activo": None,
        }
        duplicados = _FilaConn(origen, filas=[
            {"id": 662, "nombre": "Papa holandesa", "unidad": "kg"},
            {"id": 663, "nombre": "Papa holandesa", "unidad": "kg"},
        ])
        destino, error = pedidos._producto_receptor(duplicados, 661, 35)
        self.assertIsNone(destino)
        self.assertIn("varias fichas", error)

        unidad_distinta = _FilaConn(origen, filas=[
            {"id": 662, "nombre": "Papa holandesa", "unidad": "unidad"},
        ])
        destino, error = pedidos._producto_receptor(unidad_distinta, 661, 35)
        self.assertIsNone(destino)
        self.assertIn("unidades", error)

    def test_receptor_no_requiere_equivalencia_si_es_la_misma_sucursal(self):
        conn = _FilaConn({
            "sucursal_origen_id": 35,
            "unidad_origen": "unidad",
            "equivalencia_destino_id": None,
            "producto_destino_id": None,
            "sucursal_producto_destino": None,
            "unidad_destino": None,
            "destino_activo": None,
        })
        self.assertEqual(pedidos._producto_receptor(conn, 662, 35), (662, None))

    def test_receptor_rechaza_unidad_distinta_y_ficha_inactiva(self):
        fila = {
            "sucursal_origen_id": 49,
            "unidad_origen": "unidad",
            "equivalencia_destino_id": 662,
            "producto_destino_id": 662,
            "sucursal_producto_destino": 35,
            "unidad_destino": "kg",
            "destino_activo": 1,
        }
        destino, error = pedidos._producto_receptor(_FilaConn(fila), 661, 35)
        self.assertIsNone(destino)
        self.assertIn("unidades", error)
        fila["unidad_destino"] = "unidad"
        fila["destino_activo"] = 0
        destino, error = pedidos._producto_receptor(_FilaConn(fila), 661, 35)
        self.assertIsNone(destino)
        self.assertIn("inactivo", error)

    def test_despacho_registra_salida_origen_y_entrada_receptor_sin_duplicar(self):
        conn = _DespachoConn()
        movimientos = []
        detalle = [{
            "producto_id": 661,
            "producto_nombre": "llajua",
            "cantidad": 4,
            "destino_id": 49,
        }]
        pedido = {
            "id": 46,
            "nro_ticket": "TKT-00046",
            "sucursal_id": 35,
            "destino_id": 49,
        }
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context("/"):
            session["usuario"] = "test"
            with patch.object(pedidos, "sucursal_operativa", return_value=None), \
                    patch.object(pedidos, "stock_actual", return_value=10), \
                    patch.object(pedidos, "_producto_receptor", return_value=(662, None)), \
                    patch.object(pedidos, "registrar_movimiento",
                                 side_effect=lambda *args: movimientos.append(args)):
                resultado = pedidos._despachar_stock(conn, pedido, detalle)

        self.assertEqual(resultado, (14.0, [57]))
        self.assertEqual([(m[1], m[2], m[3], m[-1]) for m in movimientos], [
            (661, "salida", 4, 49),
            (662, "entrada", 4, 35),
        ])
        detalle_reparto = next(q for q in conn.queries if "INSERT INTO reparto_detalle" in q[0])
        self.assertEqual(detalle_reparto[1][1], 661)
        self.assertEqual(sum(m[2] == "entrada" for m in movimientos), 1)

    def test_despacho_sin_equivalencia_no_mueve_stock(self):
        conn = _DespachoConn()
        movimientos = []
        detalle = [{
            "producto_id": 661,
            "producto_nombre": "llajua",
            "cantidad": 4,
            "destino_id": 49,
        }]
        pedido = {
            "id": 46,
            "nro_ticket": "TKT-00046",
            "sucursal_id": 35,
            "destino_id": 49,
        }
        with patch.object(pedidos, "sucursal_operativa", return_value=None), \
                patch.object(pedidos, "stock_actual", return_value=10), \
                patch.object(pedidos, "_producto_receptor",
                             return_value=(None, "Falta equivalencia")), \
                patch.object(pedidos, "registrar_movimiento",
                             side_effect=lambda *args: movimientos.append(args)):
            resultado = pedidos._despachar_stock(conn, pedido, detalle)
        self.assertEqual(resultado, ("error", "Falta equivalencia"))
        self.assertEqual(movimientos, [])
        self.assertFalse(any("INSERT INTO repartos" in q[0] for q in conn.queries))

    def test_api_no_guarda_equivalencia_con_unidades_distintas(self):
        conn = _EquivalenciaApiConn(unidad_destino="kg")
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/equivalencias-productos", method="POST",
                json={"producto_origen_id": 661, "sucursal_destino_id": 35,
                      "producto_destino_id": 662}):
            session["user_id"] = 1
            session["rol"] = "superadmin"
            with patch.object(productos, "get_conn", return_value=conn), \
                    patch.object(productos, "registrar_auditoria"):
                respuesta, status = productos.equivalencias_productos()
        self.assertEqual(status, 400)
        self.assertIn("unidades", respuesta.get_json()["message"])
        self.assertEqual(conn.inserts, [])
        self.assertTrue(conn.closed)

    def test_reparacion_simula_y_aplica_una_sola_vez(self):
        conn = _ReparacionConn()
        movimientos = []
        app = Flask(__name__)
        app.secret_key = "test"
        with patch.object(pedidos, "get_conn", return_value=conn), \
                patch.object(pedidos, "_producto_receptor", return_value=(662, None)), \
                patch.object(pedidos, "stock_actual", return_value=4), \
                patch.object(pedidos, "registrar_movimiento",
                             side_effect=lambda *args: movimientos.append(args)), \
                patch.object(pedidos, "registrar_auditoria"):
            with app.test_request_context(
                    "/api/pedidos/reparar-stock-equivalencias", method="POST",
                    json={"ticket": "TKT-00046"}):
                session["user_id"] = 1
                session["rol"] = "superadmin"
                simulacion = pedidos.pedido_reparar_stock_equivalencias()
            self.assertTrue(simulacion.get_json()["data"]["simulacion"])
            self.assertEqual(simulacion.get_json()["data"]["cambios"][0]["cantidad"], 4)
            self.assertEqual(movimientos, [])

            with app.test_request_context(
                    "/api/pedidos/reparar-stock-equivalencias", method="POST",
                    json={"ticket": "TKT-00046", "confirmar": True}):
                session["user_id"] = 1
                session["rol"] = "superadmin"
                aplicada = pedidos.pedido_reparar_stock_equivalencias()
            self.assertFalse(aplicada.get_json()["data"]["simulacion"])
            self.assertEqual([(m[1], m[2], m[3], m[-1]) for m in movimientos], [
                (661, "salida", 4, 35),
                (662, "entrada", 4, 35),
            ])

            with app.test_request_context(
                    "/api/pedidos/reparar-stock-equivalencias", method="POST",
                    json={"ticket": "TKT-00046", "confirmar": True}):
                session["user_id"] = 1
                session["rol"] = "superadmin"
                repetida, status = pedidos.pedido_reparar_stock_equivalencias()
            self.assertEqual(status, 409)
            self.assertEqual(len(movimientos), 2)


if __name__ == "__main__":
    unittest.main()
