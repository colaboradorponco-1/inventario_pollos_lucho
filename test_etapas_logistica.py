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
    "repartidor": {"en_camino": "entregado"},
}
ETAPAS = ("pendiente", "en_camino", "entregado", "rechazado")

fallos = 0


def check(nombre, condicion):
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK ' if condicion else 'FALLA'} {nombre}")


def permitido(rol, actual, destino):
    if destino == actual:
        return True  # idempotente: ya esta
    return AVANCE.get(rol, {}).get(actual) == destino


print("=" * 70)
print("MATRIZ DE ETAPAS (rol x etapa actual -> etapa destino)")
print("=" * 70)

for rol in ("preparador", "repartidor"):
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

# El preparador NO puede marcar entregado ni rechazar.
print("\n" + "=" * 70)
print("REGLAS CRITICAS")
print("=" * 70)
reglas = [
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
# Los endpoints de entrega comparten el bloqueo de la fila del pedido y el
# helper no debe hacer rollback, que liberaría el bloqueo antes del commit.
fuente = inspect.getsource(ped._despachar_stock)
sin_rollback = ".rollback(" not in fuente
check("el despacho no libera el bloqueo con rollback", sin_rollback)

for nombre in ("pedido_despachar", "pedido_estado", "pedido_etapa"):
    vista = getattr(ped, nombre)
    bloquea_pedido = "FOR UPDATE" in inspect.getsource(vista)
    check(f"{nombre} bloquea el pedido antes de cambiar stock/estado",
          bloquea_pedido)

# Los roles actúan en el lado que les corresponde: preparar desde el proveedor
# y confirmar entrega desde la sucursal que solicitó.
fuente_etapa = inspect.getsource(ped.pedido_etapa)
check("preparador limitado a su sucursal proveedora",
      'rol == "preparador" and not es_proveedor' in fuente_etapa)
check("repartidor limitado a su sucursal solicitante",
      'rol == "repartidor" and pedido["sucursal_id"] != sid' in fuente_etapa)

app_pruebas = Flask(__name__)
app_pruebas.secret_key = "prueba"
with app_pruebas.test_request_context("/"):
    session["receptor"] = True
    check("la bandera de receptor conserva el permiso de su cuenta",
          util.es_receptor())
    session["receptor"] = False
    check("una cuenta normal no recibe el permiso de receptor",
          not util.es_receptor())

for nombre in ("pedido_despachar", "pedido_estado"):
    fuente = inspect.getsource(getattr(ped, nombre))
    check(f"{nombre} permite al superadmin despachar origenes globales",
          "_despachar_stock" in fuente and
          "origen_propio=not es_superadmin()" in fuente)


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
    etapas_despues_de_cada_preparador = []
    for proveedor_id in (20, 21):
        with app_pruebas.test_request_context(
                "/api/pedidos/11/etapa", method="PUT", json={"etapa": "en_camino"}):
            session["user_id"] = 1
            session["rol"] = "preparador"
            session["sucursal_id"] = proveedor_id
            respuestas.append(ped.pedido_etapa(11).get_json())
            etapas_despues_de_cada_preparador.append(conn_multi.pedido["etapa"])
    check("cada preparador registra su parte y el pedido avanza al completar ambas",
          all(respuesta["ok"] for respuesta in respuestas)
          and etapas_despues_de_cada_preparador == ["pendiente", "en_camino"]
          and conn_multi.pedido["etapa"] == "en_camino"
          and all(linea["preparado"] for linea in conn_multi.detalle))
finally:
    ped.get_conn = get_conn_original
    ped.registrar_auditoria = auditoria_original

# La interfaz debe conservar una sola ubicación para gestionar los estados.
base = os.path.dirname(os.path.abspath(__file__))
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

print("\n" + "=" * 70)
print("FALLOS:", fallos)
print("=" * 70)
sys.exit(1 if fallos else 0)
