"""Prueba de las transiciones de la etapa logística (pedido_etapa).

Verifica la matriz de permisos: qué rol puede pasar de qué etapa a cuál, y que
al llegar a 'entregado' se intente mover el stock una sola vez.
"""
import io
import inspect
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, session  # noqa: E402

import core.pedidos as ped  # noqa: E402
import core.util as util  # noqa: E402

# Matriz de avance declarada en el backend (flujo unificado).
AVANCE = {
    "preparador": {"pendiente": "en_camino"},
    "AS": {"pendiente": "en_camino", "en_camino": "entregado",
           "rechazado": "rechazado"},
    "repartidor": {"en_camino": "entregado"},
}
ETAPAS = ("pendiente", "en_camino", "entregado", "rechazado")

fallos = 0


def check(nombre, condicion):
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK ' if condicion else 'FALLA'} {nombre}")


def cuerpo_respuesta(respuesta):
    return (respuesta[0] if isinstance(respuesta, tuple) else respuesta).get_json()


def permitido(rol, actual, destino):
    if destino == actual:
        return True  # idempotente: ya esta
    if rol == "AS" and destino == "rechazado":
        return actual == "pendiente"
    return AVANCE.get(rol, {}).get(actual) == destino


print("=" * 70)
print("MATRIZ DE ETAPAS (rol x etapa actual -> etapa destino)")
print("=" * 70)

for rol in ("AS", "preparador", "repartidor"):
    print(f"\n--- {rol} ---")
    for actual in ETAPAS:
        for destino in ETAPAS:
            ok = permitido(rol, actual, destino)
            if ok:
                flecha = "OK  " if destino != actual else "idempotente"
                print(f"  {flecha}  {actual:15s} -> {destino}")
            elif destino != actual:
                marca = ""
                if AVANCE.get(rol, {}).get(actual) is None and actual != "pendiente":
                    marca = "  (ya lo continua otro rol)"
                print(f"  bloqueado      {actual:15s} -> {destino}{marca}")

# El AS prepara/rechaza; el preparador prepara; solo el repartidor entrega.
print("\n" + "=" * 70)
print("REGLAS CRITICAS")
print("=" * 70)
reglas = [
    ("AS: pendiente -> en_camino", permitido("AS", "pendiente", "en_camino"), True),
    ("AS puede rechazar solo desde pendiente", permitido("AS", "pendiente", "rechazado"), True),
    ("AS: en_camino -> entregado", permitido("AS", "en_camino", "entregado"), True),
    ("preparador: pendiente -> en_camino", permitido("preparador", "pendiente", "en_camino"), True),
    ("preparador: NO puede -> entregado", permitido("preparador", "pendiente", "entregado"), False),
    ("preparador: NO puede rechazar", permitido("preparador", "pendiente", "rechazado"), False),
    ("repartidor: en_camino -> entregado", permitido("repartidor", "en_camino", "entregado"), True),
    ("repartidor: NO puede empezar de cero", permitido("repartidor", "pendiente", "en_camino"), False),
    ("repartidor: NO puede saltar a entregado", permitido("repartidor", "pendiente", "entregado"), False),
    ("nadie puede volver a pendiente", permitido("repartidor", "en_camino", "pendiente"), False),
    ("nadie puede retroceder", permitido("preparador", "en_camino", "pendiente"), False),
    ("rechazado es terminal", permitido("repartidor", "rechazado", "en_camino"), False),
]
for nombre, obtenido, esperado in reglas:
    bien = obtenido == esperado
    if not bien:
        fallos += 1
    print(f"  {'OK ' if bien else 'FALLA'} {nombre}")

# Idempotencia de la entrega: no se puede volver a entregar.
print("\n" + "=" * 70)
print("ENTREGAR DOS VECES (debe quedar bloqueado por etapa, no por stock)")
print("=" * 70)
doble = permitido("repartidor", "entregado", "entregado")
print(f"  entregado -> entregado: {'idempotente (sin mover stock)' if doble else 'bloqueado'}")
# El endpoint de etapas bloquea el pedido y el helper no debe hacer rollback,
# que liberaría el bloqueo antes del commit. Los endpoints antiguos no escriben.
fuente = inspect.getsource(ped._despachar_stock)
sin_rollback = ".rollback(" not in fuente
check("el despacho no libera el bloqueo con rollback", sin_rollback)

fuente_etapa = inspect.getsource(ped.pedido_etapa)
check("pedido_etapa bloquea el pedido antes de mover stock",
      "FOR UPDATE" in fuente_etapa)
for nombre in ("pedido_despachar", "pedido_estado"):
    fuente_legacy = inspect.getsource(getattr(ped, nombre))
    check(f"{nombre} no puede saltarse el flujo único",
          "409" in fuente_legacy and "_despachar_stock" not in fuente_legacy)

# Los roles actúan en el lado que les corresponde: preparar desde el proveedor
# y confirmar entrega desde la sucursal que solicitó.
check("preparador limitado a su sucursal proveedora",
      'rol == "preparador"' in fuente_etapa and "not es_proveedor" in fuente_etapa)
check("AS y administración del almacén se limitan a sus proveedores",
      "es_as or es_preparador or puede_gestionar_todos" in fuente_etapa
      and "UPDATE pedido_detalle SET preparado = 1" in fuente_etapa)
check("repartidor limitado a su sucursal solicitante",
      'rol == "repartidor" and pedido["sucursal_id"] != sid' in fuente_etapa)
check("AS puede confirmar estados del pedido de su sucursal",
      "elif es_as or puede_gestionar_todos:" in fuente_etapa)
check("el sistema registra por separado si el stock de un pedido ya se movió",
      'pedido["stock_movido"]' in fuente_etapa
      and "stock_movido = 1" in fuente_etapa)
check("un pedido entregado sin movimiento permite una recuperación protegida",
      "reparar_stock_entregado" in fuente_etapa
      and "if reparar_stock_entregado and es_preparador" in fuente_etapa)

base = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(base, "database.py"), encoding="utf-8") as archivo:
    database_py = archivo.read()
check("la migración respeta entregas previas con reparto y detecta las no registradas",
      'pedidos", "stock_movido"' in database_py
      and "p.stock_movido = 1" in database_py
      and "EXISTS (SELECT 1 FROM repartos r WHERE r.pedido_id = p.id)" in database_py
      and database_py.count("COLLATE utf8mb4_unicode_ci") >= 4)

app_pruebas = Flask(__name__)
app_pruebas.secret_key = "prueba"
with app_pruebas.test_request_context("/"):
    session["receptor"] = True
    check("la bandera de receptor conserva el permiso de su cuenta",
          util.es_receptor())
    session["receptor"] = False
    check("una cuenta normal no recibe el permiso de receptor",
          not util.es_receptor())

with app_pruebas.test_request_context(
        "/api/pedidos/1/estado", method="PUT", json={"estado": "entregado"}):
    session["user_id"] = 1
    session["rol"] = "admin"
    session["sucursal_id"] = 20
    estado_directo = ped.pedido_estado(1)
with app_pruebas.test_request_context(
        "/api/pedidos/1/despachar", method="POST"):
    session["user_id"] = 1
    session["rol"] = "admin"
    session["sucursal_id"] = 20
    despacho_directo = ped.pedido_despachar(1)
check("las rutas administrativas antiguas rechazan cambios directos",
      isinstance(estado_directo, tuple) and estado_directo[1] == 409
      and isinstance(despacho_directo, tuple) and despacho_directo[1] == 409)


class _ConnStockInsuficiente:
    def __init__(self):
        self.queries = []
        self.sql_actual = ""

    def execute(self, sql, params=None):
        self.sql_actual = " ".join(str(sql).split())
        self.queries.append(self.sql_actual)
        return self

    def fetchone(self):
        if "SELECT nombre FROM sucursales" in self.sql_actual:
            return {"nombre": "Almacen Principal 1"}
        return None


conn_original = ped.stock_actual
sucursal_original = ped.sucursal_operativa
try:
    ped.stock_actual = lambda _conn, _producto_id, _sucursal_id: 5
    ped.sucursal_operativa = lambda: 20
    conn_prueba = _ConnStockInsuficiente()
    with app_pruebas.test_request_context("/"):
        session["usuario"] = "prueba"
        pedido_prueba = {
            "id": 9, "nro_ticket": "TKT-00009",
            "sucursal_id": 30, "destino_id": 20,
        }
        lineas_repetidas = [
            {"destino_id": 20, "producto_id": 7, "producto_nombre": "Producto",
             "cantidad": 4},
            {"destino_id": 20, "producto_id": 7, "producto_nombre": "Producto",
             "cantidad": 3},
        ]
        resultado = ped._despachar_stock(conn_prueba, pedido_prueba, lineas_repetidas)
    check("lineas repetidas validan contra la cantidad total disponible",
          resultado[0] == "error" and "Stock insuficiente" in resultado[1]
          and not any("INSERT INTO repartos" in sql for sql in conn_prueba.queries))
finally:
    ped.stock_actual = conn_original
    ped.sucursal_operativa = sucursal_original

check("un pedido no avanza hasta que todos los proveedores confirman",
      not ped._proveedores_preparados(
          [{"destino_id": 20, "preparado": 1},
           {"destino_id": 21, "preparado": 0}],
          {"destino_id": None}))
check("un pedido avanza cuando todos los proveedores confirmaron",
      ped._proveedores_preparados(
          [{"destino_id": 20, "preparado": 1},
           {"destino_id": 21, "preparado": 1}],
          {"destino_id": None}))


class _TicketConn:
    def __init__(self, ultimo_id):
        self.ultimo_id = ultimo_id
        self.sql = []

    def execute(self, query, params=None):
        query = " ".join(str(query).split())
        self.sql.append(query)
        if query.startswith("UPDATE pedido_ticket_secuencia"):
            self.ultimo_id = params[0]
        return self

    def fetchone(self):
        if self.sql[-1].startswith("SELECT GREATEST"):
            return {"max_ticket": self.ultimo_id}
        return {"ultimo_id": self.ultimo_id}


conn_ticket = _TicketConn(205)
ticket_1 = ped._nro_ticket(conn_ticket)
ticket_2 = ped._nro_ticket(conn_ticket)
check("la numeración transaccional mantiene tickets únicos y consecutivos",
      (ticket_1, ticket_2) == ("TKT-00206", "TKT-00207")
      and any("FOR UPDATE" in query for query in conn_ticket.sql))


class _MultiProveedorConn:
    def __init__(self):
        self.pedido = {
            "id": 11, "nro_ticket": "TKT-00011", "sucursal_id": 30,
            "destino_id": None, "estado": "pendiente", "etapa": "pendiente",
            "stock_movido": 0,
        }
        self.detalle = [
            {"destino_id": 20, "preparado": 0},
            {"destino_id": 21, "preparado": 0},
        ]
        self.sql_actual = ""
        self.params = []

    def execute(self, sql, params=None):
        self.sql_actual = " ".join(str(sql).split())
        self.params = list(params or [])
        if self.sql_actual.startswith("UPDATE pedido_detalle SET preparado"):
            pedido_id, destino_defecto, sucursal_id = self.params
            if pedido_id == self.pedido["id"]:
                for linea in self.detalle:
                    proveedor_id = linea["destino_id"] or destino_defecto
                    if proveedor_id == sucursal_id:
                        linea["preparado"] = 1
        elif self.sql_actual.startswith("UPDATE pedidos SET etapa = 'en_camino'"):
            self.pedido["etapa"] = self.pedido["estado"] = "en_camino"
        elif self.sql_actual.startswith(
                "UPDATE pedidos SET etapa = ?, estado = ?, total = ?, stock_movido = 1"):
            self.pedido["etapa"], self.pedido["estado"] = self.params[:2]
            self.pedido["stock_movido"] = 1
        elif self.sql_actual.startswith("UPDATE pedidos SET etapa = ?, estado = ?"):
            self.pedido["etapa"], self.pedido["estado"] = self.params[:2]
        return self

    def fetchone(self):
        if self.sql_actual.startswith("SELECT * FROM pedidos"):
            return self.pedido
        return None

    def fetchall(self):
        if "FROM pedido_detalle WHERE pedido_id" in self.sql_actual:
            return [dict(linea) for linea in self.detalle]
        return []

    def commit(self):
        pass

    def close(self):
        pass


conn_multi = _MultiProveedorConn()
get_conn_original = ped.get_conn
auditoria_original = ped.registrar_auditoria
ped.get_conn = lambda: conn_multi
ped.registrar_auditoria = lambda *args, **kwargs: None
try:
    respuestas = []
    etapas_despues_de_cada_proveedor = []
    for rol, proveedor_id, receptor in (
            ("encargado", 20, True), ("preparador", 21, False)):
        with app_pruebas.test_request_context(
                "/api/pedidos/11/etapa", method="PUT", json={"etapa": "en_camino"}):
            session["user_id"] = 1
            session["rol"] = rol
            session["sucursal_id"] = proveedor_id
            session["receptor"] = receptor
            respuestas.append(ped.pedido_etapa(11).get_json())
            etapas_despues_de_cada_proveedor.append(conn_multi.pedido["etapa"])
    check("el AS y cada preparador marcan su parte antes de pasar a En camino",
          all(respuesta["ok"] for respuesta in respuestas)
          and etapas_despues_de_cada_proveedor == ["pendiente", "en_camino"]
          and conn_multi.pedido["etapa"] == "en_camino"
          and all(linea["preparado"] for linea in conn_multi.detalle))

    conn_multi.pedido["etapa"] = conn_multi.pedido["estado"] = "pendiente"
    conn_multi.detalle = [
        {"destino_id": 20, "preparado": 1},
        {"destino_id": 21, "preparado": 1},
    ]
    conn_multi.pedido["etapa"] = conn_multi.pedido["estado"] = "en_camino"
    despacho_original = ped._despachar_stock
    ped._despachar_stock = lambda *args, **kwargs: (10.0, [91])
    with app_pruebas.test_request_context(
            "/api/pedidos/11/etapa", method="PUT", json={"etapa": "entregado"}):
        session["user_id"] = 1
        session["rol"] = "encargado"
        session["sucursal_id"] = 20
        session["receptor"] = True
        entrega_as = cuerpo_respuesta(ped.pedido_etapa(11))
    check("el AS puede confirmar Entregado dentro de su sucursal",
          entrega_as["ok"] and conn_multi.pedido["etapa"] == "entregado")

    conn_multi.pedido["etapa"] = conn_multi.pedido["estado"] = "pendiente"
    conn_multi.detalle = [
        {"destino_id": 21, "preparado": 0},
    ]
    with app_pruebas.test_request_context(
            "/api/pedidos/11/etapa", method="PUT", json={"etapa": "rechazado"}):
        session["user_id"] = 1
        session["rol"] = "encargado"
        session["sucursal_id"] = 20
        session["receptor"] = True
        cambio_as_ajeno = cuerpo_respuesta(ped.pedido_etapa(11))
    check("un AS no puede cambiar pedidos que corresponden a otro AS",
          not cambio_as_ajeno["ok"] and conn_multi.pedido["etapa"] == "pendiente")

    conn_multi.detalle = [
        {"destino_id": 20, "preparado": 0},
        {"destino_id": 21, "preparado": 1},
    ]
    with app_pruebas.test_request_context(
            "/api/pedidos/11/etapa", method="PUT", json={"etapa": "rechazado"}):
        session["user_id"] = 1
        session["rol"] = "encargado"
        session["sucursal_id"] = 20
        session["receptor"] = True
        rechazo_as = cuerpo_respuesta(ped.pedido_etapa(11))
    check("el AS puede rechazar antes de que salga el pedido",
          rechazo_as["ok"] and conn_multi.pedido["etapa"] == "rechazado")

    conn_multi.pedido["etapa"] = conn_multi.pedido["estado"] = "en_camino"
    conn_multi.pedido["stock_movido"] = 0
    conn_multi.detalle = [
        {"destino_id": 20, "preparado": 1},
        {"destino_id": 21, "preparado": 1},
    ]
    with app_pruebas.test_request_context(
            "/api/pedidos/11/etapa", method="PUT", json={"etapa": "entregado"}):
        session["user_id"] = 1
        session["rol"] = "preparador"
        session["sucursal_id"] = 21
        session["receptor"] = False
        entrega_preparador = cuerpo_respuesta(ped.pedido_etapa(11))
    check("el preparador no puede confirmar la entrega",
          not entrega_preparador["ok"])

    with app_pruebas.test_request_context(
            "/api/pedidos/11/etapa", method="PUT", json={"etapa": "entregado"}):
        session["user_id"] = 1
        session["rol"] = "repartidor"
        session["sucursal_id"] = 30
        session["receptor"] = False
        entrega_repartidor = cuerpo_respuesta(ped.pedido_etapa(11))
    check("el repartidor solicitante puede confirmar la entrega",
          entrega_repartidor["ok"])

    conn_multi.pedido["etapa"] = conn_multi.pedido["estado"] = "pendiente"
    conn_multi.detalle = [{"destino_id": 20, "preparado": 0}]
    with app_pruebas.test_request_context(
            "/api/pedidos/11/etapa", method="PUT", json={"etapa": "rechazado"}):
        session["user_id"] = 1
        session["rol"] = "admin"
        session["sucursal_id"] = 20
        session["receptor"] = False
        rechazo_admin = cuerpo_respuesta(ped.pedido_etapa(11))
    check("el administrador del almacén puede gestionar el pedido que abastece",
          rechazo_admin["ok"] and conn_multi.pedido["etapa"] == "rechazado")

    encargado_almacen_original = ped.es_encargado_almacen
    ped.es_encargado_almacen = lambda _conn: True
    conn_multi.pedido["etapa"] = conn_multi.pedido["estado"] = "pendiente"
    conn_multi.pedido["stock_movido"] = 0
    conn_multi.detalle = [
        {"destino_id": 20, "preparado": 0},
        {"destino_id": 21, "preparado": 0},
    ]
    with app_pruebas.test_request_context(
            "/api/pedidos/11/etapa", method="PUT", json={"etapa": "en_camino"}):
        session["user_id"] = 1
        session["rol"] = "encargado"
        session["sucursal_id"] = 20
        session["receptor"] = False
        avance_encargado = cuerpo_respuesta(ped.pedido_etapa(11))
    check("el encargado principal pasa el pedido completo a En camino",
          avance_encargado["ok"]
          and conn_multi.pedido["etapa"] == "en_camino"
          and conn_multi.pedido["stock_movido"] == 0
          and not any(linea["preparado"] for linea in conn_multi.detalle))

    conn_multi.pedido["etapa"] = conn_multi.pedido["estado"] = "en_camino"
    despacho_gestor_original = ped._despachar_stock
    llamadas_despacho_gestor = []
    ped._despachar_stock = lambda *args, **kwargs: (
        llamadas_despacho_gestor.append(1) or (10.0, [92]))
    try:
        with app_pruebas.test_request_context(
                "/api/pedidos/11/etapa", method="PUT", json={"etapa": "entregado"}):
            session["user_id"] = 1
            session["rol"] = "encargado"
            session["sucursal_id"] = 20
            session["receptor"] = False
            entrega_encargado = cuerpo_respuesta(ped.pedido_etapa(11))
    finally:
        ped._despachar_stock = despacho_gestor_original
    check("el encargado principal confirma Entregado y mueve stock una vez",
          entrega_encargado["ok"]
          and conn_multi.pedido["etapa"] == "entregado"
          and conn_multi.pedido["stock_movido"] == 1
          and len(llamadas_despacho_gestor) == 1)

    conn_multi.pedido["etapa"] = conn_multi.pedido["estado"] = "pendiente"
    conn_multi.pedido["stock_movido"] = 0
    conn_multi.detalle = [{"destino_id": 20, "preparado": 0}]
    with app_pruebas.test_request_context(
            "/api/pedidos/11/etapa", method="PUT", json={"etapa": "rechazado"}):
        session["user_id"] = 1
        session["rol"] = "encargado"
        session["sucursal_id"] = 20
        session["receptor"] = False
        rechazo_encargado = cuerpo_respuesta(ped.pedido_etapa(11))
    ped.es_encargado_almacen = encargado_almacen_original
    check("el encargado de almacén principal puede gestionar su proveedor",
          rechazo_encargado["ok"] and conn_multi.pedido["etapa"] == "rechazado")
finally:
    ped.get_conn = get_conn_original
    ped.registrar_auditoria = auditoria_original
    if "despacho_original" in locals():
        ped._despachar_stock = despacho_original
    if "encargado_almacen_original" in locals():
        ped.es_encargado_almacen = encargado_almacen_original


class _RetryEntregaConn:
    def __init__(self):
        self.pedido = {
            "id": 12, "nro_ticket": "TKT-00012", "sucursal_id": 30,
            "destino_id": 20, "estado": "entregado", "etapa": "entregado",
            "stock_movido": 0,
        }
        self.detalle = [{"destino_id": 20, "preparado": 1}]

    def execute(self, sql, params=None):
        query = " ".join(str(sql).split())
        if query.startswith("UPDATE pedidos SET etapa = ?, estado = ?, total = ?, stock_movido = 1"):
            self.pedido["etapa"] = params[0]
            self.pedido["estado"] = params[1]
            self.pedido["stock_movido"] = 1
        return self

    def fetchone(self):
        return self.pedido

    def fetchall(self):
        return self.detalle

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


conn_retry = _RetryEntregaConn()
despachar_original = ped._despachar_stock
puede_ver_original = ped._puede_ver_pedido
llamadas_despacho = []
ped.get_conn = lambda: conn_retry
ped.registrar_auditoria = lambda *args, **kwargs: None
ped._puede_ver_pedido = lambda *args, **kwargs: True
ped._despachar_stock = lambda *args, **kwargs: (
    llamadas_despacho.append(1) or (0.0, [91]))
try:
    with app_pruebas.test_request_context(
            "/api/pedidos/12/etapa", method="PUT", json={"etapa": "entregado"}):
        session["user_id"] = 1
        session["rol"] = "repartidor"
        session["sucursal_id"] = 30
        respuesta_recuperacion = ped.pedido_etapa(12).get_json()
    with app_pruebas.test_request_context(
            "/api/pedidos/12/etapa", method="PUT", json={"etapa": "entregado"}):
        session["user_id"] = 1
        session["rol"] = "repartidor"
        session["sucursal_id"] = 30
        respuesta_repetida = ped.pedido_etapa(12).get_json()
    check("se recupera una entrega antigua sin duplicar movimientos",
          respuesta_recuperacion["ok"]
          and "recuperó" in respuesta_recuperacion["message"]
          and respuesta_repetida["ok"]
          and len(llamadas_despacho) == 1
          and conn_retry.pedido["stock_movido"] == 1)
finally:
    ped.get_conn = get_conn_original
    ped.registrar_auditoria = auditoria_original
    ped._despachar_stock = despachar_original
    ped._puede_ver_pedido = puede_ver_original

# La interfaz debe conservar una sola ubicación para gestionar los estados.
with open(os.path.join(base, "static", "app.js"), encoding="utf-8") as archivo:
    app_js = archivo.read()
with open(os.path.join(base, "templates", "index.html"), encoding="utf-8") as archivo:
    index_html = archivo.read()
check("sin controles duplicados de cambio de estado",
      all(control not in app_js + index_html for control in (
          "btn-pedido-despachar", "pedido-cambiar-estado",
          "window.despacharPedido", "window.cambiarEstadoPedido")))
check("acciones centralizadas en el detalle",
      "function pintarEtapaEnModal" in app_js and "verPedido(${p.id}, true)" in app_js)
check("el encargado ve la acción para pasar el pedido a En camino",
      "b('Pasar a «En camino»', 'en_camino', true)" in app_js)

print("\n" + "=" * 70)
print("FALLOS:", fallos)
print("=" * 70)
sys.exit(1 if fallos else 0)
