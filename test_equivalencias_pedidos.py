import os
import sqlite3
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


class _EtapaPedidoConn:
    def __init__(self, etapa):
        self.pedido = {
            "id": 46, "nro_ticket": "TKT-00046", "estado": etapa,
            "etapa": etapa, "stock_movido": 0, "sucursal_id": 35,
            "destino_id": 49,
        }
        self.detalle = [{"destino_id": 49, "preparado": 0}]
        self.queries = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, sql, params=None):
        sql = " ".join(str(sql).split())
        self.queries.append((sql, params or ()))
        if sql.startswith("UPDATE pedido_detalle SET preparado"):
            for linea in self.detalle:
                linea["preparado"] = 1
        if "UPDATE pedidos SET etapa = 'en_camino'" in sql:
            self.pedido["etapa"] = "en_camino"
            self.pedido["estado"] = "en_camino"
        return self

    def fetchone(self):
        if self.queries[-1][0].startswith("SELECT * FROM pedidos"):
            return self.pedido
        return None

    def fetchall(self):
        return self.detalle

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class _EquivalenciaApiConn:
    def __init__(self, unidad_destino="unidad", sucursal_destino=35,
                 sucursal_origen=49, pedido_abierto=True):
        self.unidad_destino = unidad_destino
        self.sucursal_destino = sucursal_destino
        self.sucursal_origen = sucursal_origen
        self.pedido_abierto = pedido_abierto
        self.inserts = []
        self.commits = 0
        self.closed = False

    def execute(self, sql, params=None):
        if "SELECT id, unidad, sucursal_id FROM productos" in sql:
            self.fila = {
                "id": params[0], "unidad": "unidad",
                "sucursal_id": self.sucursal_origen,
            }
        elif "SELECT id, sucursal_id, unidad FROM productos" in sql:
            self.fila = {
                "id": params[0],
                "sucursal_id": self.sucursal_destino,
                "unidad": self.unidad_destino,
            }
        elif "SELECT id FROM sucursales" in sql:
            self.fila = {"id": params[0]}
        elif "FROM pedidos p" in sql and "JOIN pedido_detalle d" in sql:
            self.fila = {"1": 1} if self.pedido_abierto else None
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


class _CrearFichaEquivalenciaConn:
    def __init__(self, duplicado=None, falla_equivalencia=False,
                 sucursal_origen=49):
        self.duplicado = duplicado
        self.falla_equivalencia = falla_equivalencia
        self.sucursal_origen = sucursal_origen
        self.sql = ""
        self.params = ()
        self.inserts = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.lastrowid = 801

    def execute(self, sql, params=None):
        self.sql = " ".join(str(sql).split())
        self.params = params or ()
        if self.sql.startswith("INSERT INTO productos") or self.sql.startswith(
                "INSERT INTO equivalencias_productos"):
            self.inserts.append((self.sql, self.params))
            if (self.falla_equivalencia
                    and self.sql.startswith("INSERT INTO equivalencias_productos")):
                raise RuntimeError("Falló la relación de equivalencia")
        return self

    def fetchone(self):
        if "FROM sucursales WHERE id = ? FOR UPDATE" in self.sql:
            return {"id": 35, "nombre": "America"}
        if "FROM productos WHERE id = ? AND activo = 1" in self.sql:
            return {
                "id": 661, "nombre": "Aceite Grisol", "unidad": "litro",
                "categoria_id": 8, "marca": "Grisol", "costo_promedio": 12,
                "sucursal_id": self.sucursal_origen,
                "precio_venta": 14, "unidad_tacho": 0, "pide_tacho": 0,
            }
        if "FROM pedidos p" in self.sql and "JOIN pedido_detalle d" in self.sql:
            return {"1": 1}
        if "FROM equivalencias_productos" in self.sql:
            return None
        if "FROM categorias WHERE id = ?" in self.sql:
            return {"id": 8}
        return None

    def fetchall(self):
        if "SELECT codigo FROM productos" in self.sql:
            return [{"codigo": "PRD-0800"}]
        if "SELECT id, nombre, unidad FROM productos" in self.sql:
            return [self.duplicado] if self.duplicado else []
        if "SELECT id, nombre FROM almacenes WHERE sucursal_id = ?" in self.sql:
            return [
                {"id": 10, "nombre": "Almacén Principal"},
                {"id": 9, "nombre": "America"},
            ]
        return []

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

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


class _PendientesConn:
    def __init__(self, filas):
        self.filas = filas
        self.closed = False
        self.sql = ""
        self.params = ()

    def execute(self, sql, params=None):
        self.sql = " ".join(str(sql).split())
        self.params = params or ()
        return self

    def fetchall(self):
        return self.filas

    def close(self):
        self.closed = True


class _PedidoSinEquivalenciaConn:
    def __init__(self):
        self.queries = []
        self.closed = False
        self.lastrowid = 901

    def execute(self, sql, params=None):
        self.queries.append((" ".join(str(sql).split()), params or ()))
        return self

    def commit(self):
        pass

    def rollback(self):
        pass

    def fetchone(self):
        sql = self.queries[-1][0]
        if "FROM productos WHERE id = ?" in sql:
            return {
                "id": 661, "nombre": "Producto", "sucursal_id": 49,
                "unidad": "unidad", "unidad_tacho": 0, "pide_tacho": 0,
                "para_proveer": 1,
            }
        if "SELECT nombre FROM sucursales" in sql:
            return {"nombre": "Sucursal"}
        return None

    def close(self):
        self.closed = True


class EquivalenciasPedidosTest(unittest.TestCase):
    def test_stock_disponible_no_reserva_lineas_ya_entregadas_o_rechazadas(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE sucursales (id INTEGER PRIMARY KEY, principal INTEGER);
            CREATE TABLE productos (
                id INTEGER PRIMARY KEY, activo INTEGER, sucursal_id INTEGER
            );
            CREATE TABLE lotes (
                id INTEGER PRIMARY KEY, producto_id INTEGER,
                sucursal_id INTEGER, cantidad REAL
            );
            CREATE TABLE pedidos (
                id INTEGER PRIMARY KEY, estado TEXT, destino_id INTEGER
            );
            CREATE TABLE pedido_detalle (
                id INTEGER PRIMARY KEY, pedido_id INTEGER, producto_id INTEGER,
                cantidad REAL, destino_id INTEGER, entregado INTEGER,
                rechazado INTEGER
            );
            INSERT INTO sucursales VALUES (10, 1);
            INSERT INTO productos VALUES (19, 1, 10);
            INSERT INTO lotes VALUES (1, 19, 10, 10);
            INSERT INTO pedidos VALUES (1, 'pendiente', 10);
            INSERT INTO pedido_detalle VALUES
                (1, 1, 19, 4, 10, 1, 0),
                (2, 1, 19, 2, 10, 0, 0),
                (3, 1, 19, 1, 10, 0, 1);
        """)

        class ConnSQLitePlaceholders:
            def execute(self, sql, params=()):
                return conn.execute(sql.replace("%s", "?"), params)

        disponible = productos._stock_disponible(
            ConnSQLitePlaceholders(), destino_id=10)

        self.assertEqual(disponible, {19: 8.0})
        conn.close()

    def test_as_receptor_recibe_fichas_locales_para_elegir_equivalencia(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE sucursales (id INTEGER PRIMARY KEY, nombre TEXT);
            CREATE TABLE categorias (id INTEGER PRIMARY KEY, nombre TEXT);
            CREATE TABLE productos (
                id INTEGER PRIMARY KEY, nombre TEXT, unidad TEXT,
                sucursal_id INTEGER, categoria_id INTEGER, activo INTEGER
            );
            CREATE TABLE equivalencias_productos (
                id INTEGER PRIMARY KEY, producto_origen_id INTEGER,
                sucursal_destino_id INTEGER, producto_destino_id INTEGER
            );
            CREATE TABLE pedidos (
                id INTEGER PRIMARY KEY, sucursal_id INTEGER,
                destino_id INTEGER, estado TEXT
            );
            CREATE TABLE pedido_detalle (
                id INTEGER PRIMARY KEY, pedido_id INTEGER,
                producto_id INTEGER, destino_id INTEGER
            );
            INSERT INTO sucursales VALUES
                (35, 'Simon Lopez'), (49, 'AS America'), (77, 'Otra sucursal');
            INSERT INTO categorias VALUES (1, 'Verduras');
            INSERT INTO productos VALUES
                (661, 'ARVEJA', 'kg', 49, 1, 1),
                (662, 'ARVEJA', 'kg', 35, 1, 1),
                (663, 'PAPA', 'kg', 35, 1, 1),
                (664, 'ARVEJA', 'kg', 77, 1, 1);
            INSERT INTO pedidos VALUES (10, 35, 49, 'pendiente');
            INSERT INTO pedido_detalle VALUES (20, 10, 661, NULL);
        """)
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context("/api/equivalencias-productos"):
            session["user_id"] = 1
            session["rol"] = "encargado"
            session["receptor"] = True
            with patch.object(productos, "get_conn", return_value=conn), \
                    patch.object(productos, "es_gestion", return_value=False), \
                    patch.object(productos, "es_encargado_almacen", return_value=False), \
                    patch.object(productos, "es_receptor", return_value=True), \
                    patch.object(productos, "sucursal_actual", return_value=49):
                respuesta = productos.equivalencias_productos()

        data = respuesta.get_json()["data"]
        ids_productos = {producto["id"] for producto in data["productos"]}
        self.assertEqual(ids_productos, {661, 662, 663})
        self.assertEqual(data["sucursales"], [{"id": 35, "nombre": "Simon Lopez"}])

    def test_encargado_receptor_ve_origenes_pendientes_de_su_sucursal(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE sucursales (id INTEGER PRIMARY KEY, nombre TEXT);
            CREATE TABLE categorias (id INTEGER PRIMARY KEY, nombre TEXT);
            CREATE TABLE productos (
                id INTEGER PRIMARY KEY, nombre TEXT, unidad TEXT,
                sucursal_id INTEGER, categoria_id INTEGER, activo INTEGER
            );
            CREATE TABLE equivalencias_productos (
                id INTEGER PRIMARY KEY, producto_origen_id INTEGER,
                sucursal_destino_id INTEGER, producto_destino_id INTEGER
            );
            CREATE TABLE pedidos (
                id INTEGER PRIMARY KEY, sucursal_id INTEGER,
                destino_id INTEGER, estado TEXT
            );
            CREATE TABLE pedido_detalle (
                id INTEGER PRIMARY KEY, pedido_id INTEGER,
                producto_id INTEGER, destino_id INTEGER
            );
            INSERT INTO sucursales VALUES
                (35, 'Simon Lopez'), (49, 'Almacen Principal 1');
            INSERT INTO categorias VALUES (1, 'Empaques');
            INSERT INTO productos VALUES
                (19, 'BOLSA 17X34', 'unidad', 49, 1, 1),
                (20, 'BOLSA 17X34', 'unidad', 35, 1, 1),
                (21, 'OTRA BOLSA', 'unidad', 35, 1, 1);
            INSERT INTO pedidos VALUES (10, 35, 49, 'pendiente');
            INSERT INTO pedido_detalle VALUES (20, 10, 19, NULL);
        """)
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context("/api/equivalencias-productos"):
            session["user_id"] = 1
            session["rol"] = "encargado"
            with patch.object(productos, "get_conn", return_value=conn), \
                    patch.object(productos, "es_gestion", return_value=False), \
                    patch.object(productos, "es_encargado_almacen", return_value=False), \
                    patch.object(productos, "es_receptor", return_value=False), \
                    patch.object(productos, "sucursal_actual", return_value=35):
                respuesta = productos.equivalencias_productos()

        data = respuesta.get_json()["data"]
        ids_productos = {producto["id"] for producto in data["productos"]}
        self.assertEqual(ids_productos, {19, 20, 21})
        self.assertEqual(data["sucursales"], [{"id": 35, "nombre": "Simon Lopez"}])

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
        self.assertIn("No hay equivalencia confirmada", error)

    def test_receptor_no_asigna_ficha_local_automaticamente_por_nombre(self):
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
        destino, error = pedidos._producto_receptor(conn, 661, 35)
        self.assertIsNone(destino)
        self.assertIn("No hay equivalencia confirmada", error)

    def test_receptor_no_asigna_automaticamente_nombre_con_palabras_reordenadas(self):
        conn = _FilaConn({
            "sucursal_origen_id": 49,
            "nombre_origen": "Aceite Sabrosa",
            "unidad_origen": "litro",
            "equivalencia_destino_id": None,
            "producto_destino_id": None,
            "sucursal_producto_destino": None,
            "unidad_destino": None,
            "destino_activo": None,
        }, filas=[{"id": 667, "nombre": "SABROSA aceite", "unidad": "litro"}])

        destino, error = pedidos._producto_receptor(conn, 661, 35)
        self.assertIsNone(destino)
        self.assertIn("No hay equivalencia confirmada", error)

    def test_receptor_no_infiere_por_nombre_aunque_haya_ambiguos_o_unidad_distinta(self):
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
        self.assertIn("No hay equivalencia confirmada", error)

        unidad_distinta = _FilaConn(origen, filas=[
            {"id": 662, "nombre": "Papa holandesa", "unidad": "unidad"},
        ])
        destino, error = pedidos._producto_receptor(unidad_distinta, 661, 35)
        self.assertIsNone(destino)
        self.assertIn("No hay equivalencia confirmada", error)

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

    def test_prevalidacion_identifica_pedido_que_no_se_puede_recibir(self):
        app = Flask(__name__)
        app.secret_key = "test"
        conn = _FilaConn({"nombre": "Sucursal"})
        with app.test_request_context("/"):
            with patch.object(
                    pedidos, "_producto_receptor",
                    return_value=(None, "No hay equivalencia configurada")):
                destino, error = pedidos._validar_producto_receptor(
                    conn, 661, "Producto", 49, 35)
        self.assertIsNone(destino)
        self.assertIn("Producto", error)
        self.assertIn("ficha 661", error)
        self.assertIn("Sucursal", error)

    def test_crear_pedido_sin_equivalencia_guarda_ticket_pendiente(self):
        conn = _PedidoSinEquivalenciaConn()
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/pedidos", method="POST",
                json={"detalle": [{
                    "producto_id": 661, "cantidad": 1, "destino_id": 49,
                }]}):
            session["user_id"] = 1
            session["rol"] = "encargado"
            with patch.object(pedidos, "get_conn", return_value=conn), \
                    patch.object(pedidos, "es_gestion", return_value=False), \
                    patch.object(pedidos, "es_encargado_almacen", return_value=False), \
                    patch.object(pedidos, "es_receptor", return_value=False), \
                    patch.object(pedidos, "sucursal_actual", return_value=35), \
                    patch.object(pedidos, "destinos_validos", return_value=[49]), \
                    patch.object(pedidos, "stock_por_destino", return_value={}), \
                    patch.object(pedidos, "stock_en_destino", return_value=0), \
                    patch.object(pedidos, "_ids_proveedores_misma_ciudad",
                                 return_value=None), \
                    patch.object(pedidos, "_nro_ticket", return_value="TKT-TEST"), \
                    patch.object(
                        pedidos, "_producto_receptor",
                        return_value=(None, "No hay equivalencia configurada")), \
                    patch.object(pedidos, "registrar_auditoria"):
                respuesta = pedidos.pedidos()
        self.assertEqual(respuesta.status_code, 200)
        self.assertIn("Se envió el pedido; antes de recibir",
                      respuesta.get_json()["message"])
        self.assertTrue(any("INSERT INTO pedidos " in sql
                            for sql, _ in conn.queries))
        self.assertTrue(any("INSERT INTO pedido_detalle " in sql
                            for sql, _ in conn.queries))
        self.assertTrue(conn.closed)

    def test_encargado_puede_marcar_en_camino_sin_equivalencia_y_sin_mover_stock(self):
        conn = _EtapaPedidoConn("pendiente")
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/pedidos/46/etapa", method="PUT",
                json={"etapa": "en_camino"}):
            session["user_id"] = 1
            session["rol"] = "encargado"
            session["sucursal_id"] = 49
            with patch.object(pedidos, "get_conn", return_value=conn), \
                    patch.object(pedidos, "sucursal_actual", return_value=49), \
                    patch.object(pedidos, "es_encargado_almacen", return_value=True), \
                    patch.object(pedidos, "es_receptor", return_value=False), \
                    patch.object(pedidos, "registrar_auditoria"), \
                    patch.object(pedidos, "_despachar_stock") as despachar:
                respuesta = pedidos.pedido_etapa(46)

        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(conn.pedido["etapa"], "en_camino")
        self.assertEqual(conn.pedido["stock_movido"], 0)
        despachar.assert_not_called()
        self.assertTrue(conn.closed)

    def test_entrega_sin_equivalencia_se_detiene_antes_de_mover_stock(self):
        conn = _EtapaPedidoConn("en_camino")
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/pedidos/46/etapa", method="PUT",
                json={"etapa": "entregado"}):
            session["user_id"] = 1
            session["rol"] = "encargado"
            session["sucursal_id"] = 49
            with patch.object(pedidos, "get_conn", return_value=conn), \
                    patch.object(pedidos, "sucursal_actual", return_value=49), \
                    patch.object(pedidos, "es_encargado_almacen", return_value=True), \
                    patch.object(pedidos, "es_receptor", return_value=False), \
                    patch.object(pedidos, "_despachar_stock",
                                 return_value=("error", "Falta equivalencia. No se modificó stock.")) \
                    as despachar:
                respuesta, status = pedidos.pedido_etapa(46)

        self.assertEqual(status, 400)
        self.assertIn("Falta equivalencia", respuesta.get_json()["message"])
        despachar.assert_called_once()
        self.assertEqual(conn.pedido["etapa"], "en_camino")
        self.assertEqual(conn.pedido["stock_movido"], 0)
        self.assertFalse(any("stock_movido = 1" in sql for sql, _ in conn.queries))
        self.assertEqual(conn.rollbacks, 1)
        self.assertTrue(conn.closed)

    def test_reporte_pedidos_pendientes_solo_muestra_equivalencias_faltantes(self):
        filas = [
            {
                "nro_ticket": "TKT-00051", "estado": "pendiente",
                "producto_id": 661, "producto_nombre": "Producto",
                "producto_unidad": "unidad", "producto_categoria_id": 8,
                "producto_categoria": "Aceites",
                "proveedor_id": 49,
                "proveedor_nombre": "Proveedor", "sucursal_receptora_id": 35,
                "sucursal_receptora_nombre": "Receptora",
            },
            {
                "nro_ticket": "TKT-00052", "estado": "en_camino",
                "producto_id": 661, "producto_nombre": "Producto",
                "producto_unidad": "unidad", "producto_categoria_id": 8,
                "producto_categoria": "Aceites",
                "proveedor_id": 49,
                "proveedor_nombre": "Proveedor", "sucursal_receptora_id": 35,
                "sucursal_receptora_nombre": "Receptora",
            },
            {
                "nro_ticket": "TKT-00053", "estado": "pendiente",
                "producto_id": 662, "producto_nombre": "Ya vinculado",
                "producto_unidad": "kg", "producto_categoria_id": 9,
                "producto_categoria": "Otros",
                "proveedor_id": 50,
                "proveedor_nombre": "Otro proveedor", "sucursal_receptora_id": 35,
                "sucursal_receptora_nombre": "Receptora",
            },
        ]
        conn = _PendientesConn(filas)
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context("/api/pedidos/equivalencias-pendientes"):
            session["user_id"] = 1
            session["rol"] = "admin"
            with patch.object(pedidos, "get_conn", return_value=conn), \
                    patch.object(
                        pedidos, "_producto_receptor",
                        side_effect=lambda _conn, producto_id, _sucursal: (
                            (None, "Falta configurar") if producto_id == 661
                            else (662, None))):
                respuesta = pedidos.pedidos_equivalencias_pendientes()
        data = respuesta.get_json()["data"]
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["pendientes"][0]["tickets"], [
            "TKT-00051", "TKT-00052"])
        self.assertTrue(conn.closed)

    def test_reporte_equivalencias_pendientes_restringido_a_roles_autorizados(self):
        app = Flask(__name__)
        app.secret_key = "test"
        conn = _PendientesConn([])
        with app.test_request_context("/api/pedidos/equivalencias-pendientes"):
            session["user_id"] = 1
            session["rol"] = "encargado"
            with patch.object(pedidos, "get_conn", return_value=conn), \
                    patch.object(pedidos, "es_encargado_almacen", return_value=False):
                respuesta, status = pedidos.pedidos_equivalencias_pendientes()
        self.assertEqual(status, 403)
        self.assertTrue(conn.closed)

    def test_as_solo_ve_pedidos_bloqueados_de_su_propio_almacen(self):
        app = Flask(__name__)
        app.secret_key = "test"
        conn = _PendientesConn([])
        with app.test_request_context("/api/pedidos/equivalencias-pendientes"):
            session["user_id"] = 1
            session["rol"] = "encargado"
            with patch.object(pedidos, "get_conn", return_value=conn), \
                    patch.object(pedidos, "es_gestion", return_value=False), \
                    patch.object(pedidos, "es_encargado_almacen", return_value=False), \
                    patch.object(pedidos, "es_receptor", return_value=True), \
                    patch.object(pedidos, "sucursal_actual", return_value=49):
                respuesta = pedidos.pedidos_equivalencias_pendientes()

        self.assertIn("COALESCE(d.destino_id, p.destino_id) = ?", conn.sql)
        self.assertEqual(conn.params, (49, 49))
        self.assertEqual(respuesta.get_json()["data"]["total"], 0)
        self.assertTrue(conn.closed)

    def test_encargado_ve_equivalencias_pendientes_de_su_sucursal_receptora(self):
        app = Flask(__name__)
        app.secret_key = "test"
        conn = _PendientesConn([])
        with app.test_request_context("/api/pedidos/equivalencias-pendientes"):
            session["user_id"] = 1
            session["rol"] = "encargado"
            with patch.object(pedidos, "get_conn", return_value=conn), \
                    patch.object(pedidos, "es_gestion", return_value=False), \
                    patch.object(pedidos, "es_encargado_almacen", return_value=False), \
                    patch.object(pedidos, "es_receptor", return_value=False), \
                    patch.object(pedidos, "sucursal_actual", return_value=35):
                respuesta = pedidos.pedidos_equivalencias_pendientes()

        self.assertIn("AND p.sucursal_id = ?", conn.sql)
        self.assertEqual(conn.params, (35,))
        self.assertEqual(respuesta.get_json()["data"]["total"], 0)
        self.assertTrue(conn.closed)

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
        self.assertEqual(resultado[0], "error")
        self.assertIn("Falta equivalencia", resultado[1])
        self.assertIn("llajua", resultado[1])
        self.assertIn("Sucursal", resultado[1])
        self.assertIn("No se modificó stock", resultado[1])
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

    def test_as_puede_confirmar_equivalencia_solo_para_pedido_de_su_almacen(self):
        conn = _EquivalenciaApiConn(sucursal_origen=49)
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/equivalencias-productos", method="POST",
                json={"producto_origen_id": 661, "sucursal_destino_id": 35,
                      "producto_destino_id": 662}):
            session["user_id"] = 1
            session["rol"] = "encargado"
            with patch.object(productos, "get_conn", return_value=conn), \
                    patch.object(productos, "es_gestion", return_value=False), \
                    patch.object(productos, "es_encargado_almacen", return_value=False), \
                    patch.object(productos, "es_receptor", return_value=True), \
                    patch.object(productos, "sucursal_actual", return_value=49), \
                    patch.object(productos, "registrar_auditoria"):
                respuesta = productos.equivalencias_productos()

        self.assertTrue(respuesta.get_json()["ok"])
        self.assertEqual(conn.inserts[-1][1][:3], (661, 35, 662))
        self.assertTrue(conn.closed)

    def test_encargado_puede_confirmar_equivalencia_para_pedido_recibido(self):
        conn = _EquivalenciaApiConn(sucursal_origen=49)
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/equivalencias-productos", method="POST",
                json={"producto_origen_id": 661, "sucursal_destino_id": 35,
                      "producto_destino_id": 662}):
            session["user_id"] = 1
            session["rol"] = "encargado"
            with patch.object(productos, "get_conn", return_value=conn), \
                    patch.object(productos, "es_gestion", return_value=False), \
                    patch.object(productos, "es_encargado_almacen", return_value=False), \
                    patch.object(productos, "es_receptor", return_value=False), \
                    patch.object(productos, "sucursal_actual", return_value=35), \
                    patch.object(productos, "registrar_auditoria"):
                respuesta = productos.equivalencias_productos()

        self.assertTrue(respuesta.get_json()["ok"])
        self.assertEqual(conn.inserts[-1][1][:3], (661, 35, 662))
        self.assertTrue(conn.closed)

    def test_as_no_puede_confirmar_equivalencia_de_otro_almacen(self):
        conn = _EquivalenciaApiConn(sucursal_origen=88)
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/equivalencias-productos", method="POST",
                json={"producto_origen_id": 661, "sucursal_destino_id": 35,
                      "producto_destino_id": 662}):
            session["user_id"] = 1
            session["rol"] = "encargado"
            with patch.object(productos, "get_conn", return_value=conn), \
                    patch.object(productos, "es_gestion", return_value=False), \
                    patch.object(productos, "es_encargado_almacen", return_value=False), \
                    patch.object(productos, "es_receptor", return_value=True), \
                    patch.object(productos, "sucursal_actual", return_value=49):
                respuesta, status = productos.equivalencias_productos()

        self.assertEqual(status, 403)
        self.assertEqual(conn.inserts, [])
        self.assertTrue(conn.closed)

    def test_crear_ficha_receptora_crea_stock_cero_y_equivalencia_juntos(self):
        conn = _CrearFichaEquivalenciaConn()
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/equivalencias-productos/crear-ficha", method="POST",
                json={
                    "producto_origen_id": 661, "sucursal_destino_id": 35,
                    "nombre": "Aceite Sabrosa", "categoria_id": 8,
                    "confirmar_no_existe": True,
                }):
            session["user_id"] = 1
            session["rol"] = "admin"
            with patch.object(productos, "get_conn", return_value=conn), \
                    patch.object(productos, "registrar_auditoria"), \
                    patch.object(productos, "registrar_movimiento") as movimiento:
                respuesta = productos.equivalencia_producto_crear_ficha()

        data = respuesta.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["data"]["producto_destino_id"], 801)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(len(conn.inserts), 2)
        alta, equivalencia = conn.inserts
        self.assertIn("stock_minimo, costo_promedio", alta[0])
        self.assertEqual(alta[1][1], "Aceite Sabrosa")
        self.assertEqual(alta[1][3:5], (8, "litro"))
        self.assertEqual(alta[1][6:9], (14, 9, 35))
        self.assertEqual(equivalencia[1][:3], (661, 35, 801))
        movimiento.assert_not_called()
        self.assertTrue(conn.closed)

    def test_as_puede_crear_ficha_cero_para_pedido_de_su_almacen(self):
        conn = _CrearFichaEquivalenciaConn(sucursal_origen=49)
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/equivalencias-productos/crear-ficha", method="POST",
                json={
                    "producto_origen_id": 661, "sucursal_destino_id": 35,
                    "nombre": "Aceite Grisol", "categoria_id": "sin",
                    "confirmar_no_existe": True,
                }):
            session["user_id"] = 1
            session["rol"] = "encargado"
            with patch.object(productos, "get_conn", return_value=conn), \
                    patch.object(productos, "es_gestion", return_value=False), \
                    patch.object(productos, "es_encargado_almacen", return_value=False), \
                    patch.object(productos, "es_receptor", return_value=True), \
                    patch.object(productos, "sucursal_actual", return_value=49), \
                    patch.object(productos, "registrar_auditoria"), \
                    patch.object(productos, "registrar_movimiento"):
                respuesta = productos.equivalencia_producto_crear_ficha()

        self.assertTrue(respuesta.get_json()["ok"])
        self.assertEqual(conn.commits, 1)
        self.assertEqual(len(conn.inserts), 2)
        self.assertTrue(conn.closed)

    def test_encargado_puede_crear_ficha_para_pedido_recibido(self):
        conn = _CrearFichaEquivalenciaConn(sucursal_origen=49)
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/equivalencias-productos/crear-ficha", method="POST",
                json={
                    "producto_origen_id": 661, "sucursal_destino_id": 35,
                    "nombre": "Aceite Grisol", "categoria_id": "sin",
                    "confirmar_no_existe": True,
                }):
            session["user_id"] = 1
            session["rol"] = "encargado"
            with patch.object(productos, "get_conn", return_value=conn), \
                    patch.object(productos, "es_gestion", return_value=False), \
                    patch.object(productos, "es_encargado_almacen", return_value=False), \
                    patch.object(productos, "es_receptor", return_value=False), \
                    patch.object(productos, "sucursal_actual", return_value=35), \
                    patch.object(productos, "registrar_auditoria"), \
                    patch.object(productos, "registrar_movimiento"):
                respuesta = productos.equivalencia_producto_crear_ficha()

        self.assertTrue(respuesta.get_json()["ok"])
        self.assertEqual(conn.commits, 1)
        self.assertEqual(len(conn.inserts), 2)
        self.assertTrue(conn.closed)

    def test_as_no_puede_crear_ficha_para_producto_de_otro_almacen(self):
        conn = _CrearFichaEquivalenciaConn(sucursal_origen=88)
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/equivalencias-productos/crear-ficha", method="POST",
                json={
                    "producto_origen_id": 661, "sucursal_destino_id": 35,
                    "nombre": "Aceite Grisol", "categoria_id": "sin",
                    "confirmar_no_existe": True,
                }):
            session["user_id"] = 1
            session["rol"] = "encargado"
            with patch.object(productos, "get_conn", return_value=conn), \
                    patch.object(productos, "es_gestion", return_value=False), \
                    patch.object(productos, "es_encargado_almacen", return_value=False), \
                    patch.object(productos, "es_receptor", return_value=True), \
                    patch.object(productos, "sucursal_actual", return_value=49):
                respuesta, status = productos.equivalencia_producto_crear_ficha()

        self.assertEqual(status, 403)
        self.assertEqual(conn.inserts, [])
        self.assertEqual(conn.commits, 0)
        self.assertTrue(conn.closed)

    def test_crear_ficha_receptora_exige_confirmacion_de_no_existencia(self):
        conn = _CrearFichaEquivalenciaConn()
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/equivalencias-productos/crear-ficha", method="POST",
                json={
                    "producto_origen_id": 661, "sucursal_destino_id": 35,
                    "nombre": "Aceite Sabrosa", "categoria_id": 8,
                    "confirmar_no_existe": False,
                }):
            session["user_id"] = 1
            session["rol"] = "admin"
            with patch.object(productos, "get_conn", return_value=conn):
                respuesta, status = productos.equivalencia_producto_crear_ficha()

        self.assertEqual(status, 400)
        self.assertIn("Confirma que revisaste", respuesta.get_json()["message"])
        self.assertEqual(conn.inserts, [])
        self.assertEqual(conn.commits, 0)
        self.assertTrue(conn.closed)

    def test_crear_ficha_receptora_no_duplica_nombre_ya_existente(self):
        conn = _CrearFichaEquivalenciaConn(
            duplicado={"id": 667, "nombre": "Aceite Sabrosa", "unidad": "litro"})
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/equivalencias-productos/crear-ficha", method="POST",
                json={
                    "producto_origen_id": 661, "sucursal_destino_id": 35,
                    "nombre": "Aceite Sabrosa", "categoria_id": 8,
                    "confirmar_no_existe": True,
                }):
            session["user_id"] = 1
            session["rol"] = "admin"
            with patch.object(productos, "get_conn", return_value=conn):
                respuesta, status = productos.equivalencia_producto_crear_ficha()

        self.assertEqual(status, 409)
        self.assertIn("Ya existe la ficha", respuesta.get_json()["message"])
        self.assertEqual(conn.inserts, [])
        self.assertEqual(conn.commits, 0)
        self.assertTrue(conn.closed)

    def test_crear_ficha_receptora_no_duplica_nombre_con_palabras_reordenadas(self):
        conn = _CrearFichaEquivalenciaConn(
            duplicado={"id": 667, "nombre": "SABROSA aceite", "unidad": "litro"})
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/equivalencias-productos/crear-ficha", method="POST",
                json={
                    "producto_origen_id": 661, "sucursal_destino_id": 35,
                    "nombre": "Aceite Sabrosa", "categoria_id": 8,
                    "confirmar_no_existe": True,
                }):
            session["user_id"] = 1
            session["rol"] = "admin"
            with patch.object(productos, "get_conn", return_value=conn):
                respuesta, status = productos.equivalencia_producto_crear_ficha()

        self.assertEqual(status, 409)
        self.assertIn("SABROSA aceite", respuesta.get_json()["message"])
        self.assertEqual(conn.inserts, [])
        self.assertEqual(conn.commits, 0)
        self.assertTrue(conn.closed)

    def test_crear_ficha_receptora_requiere_usuario_autorizado(self):
        conn = _CrearFichaEquivalenciaConn()
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/equivalencias-productos/crear-ficha", method="POST",
                json={
                    "producto_origen_id": 661, "sucursal_destino_id": 35,
                    "nombre": "Aceite Sabrosa", "categoria_id": 8,
                    "confirmar_no_existe": True,
                }):
            session["user_id"] = 1
            session["rol"] = "encargado"
            with patch.object(productos, "get_conn", return_value=conn), \
                    patch.object(productos, "es_gestion", return_value=False), \
                    patch.object(productos, "es_encargado_almacen", return_value=False):
                respuesta, status = productos.equivalencia_producto_crear_ficha()

        self.assertEqual(status, 403)
        self.assertEqual(conn.inserts, [])
        self.assertTrue(conn.closed)

    def test_crear_ficha_receptora_revierte_si_falla_el_vinculo(self):
        conn = _CrearFichaEquivalenciaConn(falla_equivalencia=True)
        app = Flask(__name__)
        app.secret_key = "test"
        with app.test_request_context(
                "/api/equivalencias-productos/crear-ficha", method="POST",
                json={
                    "producto_origen_id": 661, "sucursal_destino_id": 35,
                    "nombre": "Aceite Sabrosa", "categoria_id": 8,
                    "confirmar_no_existe": True,
                }):
            session["user_id"] = 1
            session["rol"] = "admin"
            with patch.object(productos, "get_conn", return_value=conn), \
                    patch.object(productos, "logging") as logger:
                respuesta, status = productos.equivalencia_producto_crear_ficha()

        self.assertEqual(status, 500)
        self.assertIn("No se pudo crear la ficha", respuesta.get_json()["message"])
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)
        self.assertTrue(conn.closed)
        logger.exception.assert_called_once()

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
