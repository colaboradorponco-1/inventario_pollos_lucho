"""Permisos para la vista y registro de repartos según el rol operativo."""
import io
import inspect
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, session  # noqa: E402

import core.sucursales as suc  # noqa: E402
import core.pedidos as ped  # noqa: E402
import core.movimientos as mov  # noqa: E402
from core.util import ruta_bloqueada_logistica  # noqa: E402


class FakeConn:
    def __init__(self, principal=0, reparto=None):
        self.principal = principal
        self.sucursales = [
            {"id": 20, "nombre": "AS America", "principal": 0},
            {"id": 21, "nombre": "AS Simon Lopez", "principal": 0},
            {"id": 30, "nombre": "Almacen Principal 1", "principal": 1},
            {"id": 42, "nombre": "La Paz - 6 de Agosto", "principal": 0},
        ]
        self.reparto = reparto or {
            "id": 8, "sucursal_id": 42, "origen_sucursal_id": 20,
            "sucursal_nombre": "America",
        }
        self.sql = []
        self.actual = ""

    def execute(self, query, params=None):
        self.actual = " ".join(str(query).split())
        self.sql.append((self.actual, list(params or [])))
        return self

    def fetchone(self):
        if "SELECT principal, nombre FROM sucursales" in self.actual:
            return {"principal": self.principal, "nombre": "Almacen Principal 1"}
        if self.actual == "SELECT nombre FROM sucursales WHERE id = ?":
            sucursal_id = self.sql[-1][1][0]
            return next(({"nombre": suc["nombre"]} for suc in self.sucursales
                         if suc["id"] == sucursal_id), None)
        if "SELECT r.*" in self.actual:
            return self.reparto
        if self.actual.startswith("SELECT COUNT(*)"):
            return {"c": 0}
        return None

    def fetchall(self):
        if self.actual == "SELECT id, nombre, principal FROM sucursales":
            return self.sucursales
        return []

    def close(self):
        pass


app = Flask(__name__)
app.secret_key = "repartos-permisos"
conn_actual = None
suc.get_conn = lambda: conn_actual
fallos = 0


def check(nombre, condicion):
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK' if condicion else 'FALLA'} {nombre}")


def ejecutar(endpoint, rol, sid, metodo="GET", receptor=False, principal=0, data=None):
    global conn_actual
    conn_actual = FakeConn(principal=principal)
    with app.test_request_context(
            "/api/repartos" if endpoint == "lista" else "/api/repartos/8",
            method=metodo, json=(data or {}) if metodo == "POST" else None):
        session["user_id"] = 1
        session["rol"] = rol
        session["sucursal_id"] = sid
        session["receptor"] = receptor
        respuesta = suc.repartos() if endpoint == "lista" else suc.reparto_detalle(8)
        codigo = respuesta[1] if isinstance(respuesta, tuple) else respuesta.status_code
        return codigo, conn_actual


print("=" * 68)
print("PERMISOS DEL APARTADO DE REPARTOS")
print("=" * 68)

codigo, _ = ejecutar("lista", "encargado", 42)
check("encargado de sucursal normal no puede listar repartos", codigo == 403)
codigo, _ = ejecutar("lista", "encargado", 42, metodo="POST")
check("encargado de sucursal normal no puede registrar repartos", codigo == 403)

codigo, conn = ejecutar("lista", "encargado", 20, receptor=True)
check("receptor puede consultar sus repartos", codigo == 200)
consulta = next((sql for sql, _ in conn.sql if "FROM repartos r" in sql), "")
check("receptor ve solo repartos vinculados a su sucursal",
      "r.origen_sucursal_id = ?" in consulta)
codigo, _ = ejecutar("lista", "encargado", 20, metodo="POST", receptor=True)
check("receptor puede registrar un reparto desde su propio almacén", codigo == 400)
codigo, conn = ejecutar(
    "lista", "encargado", 20, metodo="POST", receptor=True,
    data={"sucursal_id": 21, "origen_sucursal_id": 42, "detalle": []})
check("receptor puede enviar a otra sucursal permitida de su ciudad",
      codigo == 400 and any(
          query == "SELECT nombre FROM sucursales WHERE id = ?" and params == [20]
          for query, params in conn.sql))
respuesta_fuera_ciudad, conn_fuera_ciudad = ejecutar(
    "lista", "encargado", 20, metodo="POST", receptor=True,
    data={"sucursal_id": 42, "detalle": []})
check("receptor no puede enviar a una sucursal de otra ciudad",
      respuesta_fuera_ciudad == 400 and not any(
          query == "SELECT nombre FROM sucursales WHERE id = ?" and params == [42]
          for query, params in conn_fuera_ciudad.sql))
codigo, _ = ejecutar(
    "lista", "encargado", 20, metodo="POST", receptor=True,
    data={"sucursal_id": 30, "detalle": []})
check("receptor conserva el envío a un almacén principal de referencia",
      codigo == 400)

codigo, conn = ejecutar("lista", "repartidor", 20)
check("repartidor puede consultar su lista de repartos", codigo == 200)
consulta = next((sql for sql, _ in conn.sql if "FROM repartos r" in sql), "")
check("lista de repartidor limita resultados a origen o destino de su sucursal",
      "r.origen_sucursal_id = ?" in consulta)
codigo, _ = ejecutar("lista", "repartidor", 20, metodo="POST")
check("repartidor no puede crear transferencias de inventario", codigo == 403)
codigo, _ = ejecutar("detalle", "repartidor", 20)
check("repartidor puede abrir el detalle que sale de su sucursal", codigo == 200)

codigo, _ = ejecutar("lista", "encargado", 30, principal=1)
check("encargado de almacén principal conserva acceso a repartos", codigo == 200)
codigo, _ = ejecutar("lista", "admin", 42)
check("administrador conserva acceso a repartos", codigo == 200)

with app.test_request_context("/"):
    session["rol"] = "preparador"
    check("preparador sigue sin acceso al apartado de repartos",
          ruta_bloqueada_logistica("/api/repartos", "GET"))
    session["rol"] = "repartidor"
    check("repartidor solo obtiene lectura de la API de repartos",
          not ruta_bloqueada_logistica("/api/repartos", "GET")
          and ruta_bloqueada_logistica("/api/repartos", "POST")
          and ruta_bloqueada_logistica("/api/repartos/8", "DELETE")
          and ruta_bloqueada_logistica("/api/exportar/repartos", "GET"))

reparto_manual = inspect.getsource(suc.repartos)
reparto_pedido = inspect.getsource(ped._despachar_stock)
historial_movimientos = inspect.getsource(mov.movimientos)
check("el reparto manual registra salida en origen y entrada en destino",
      'registrar_movimiento(conn, prod_id, "salida"' in reparto_manual
      and 'registrar_movimiento(conn, prod_id, "entrada"' in reparto_manual)
check("la entrega automática del pedido registra salida y entrada de stock",
      'conn, d["producto_id"], "salida"' in reparto_pedido
      and "productos_recepcion[(origen_id, d[\"producto_id\"])]" in reparto_pedido
      and 'registrar_movimiento(conn, producto_receptor_id, "entrada"' in reparto_pedido)
check("el historial incluye entradas y salidas si no se selecciona un tipo",
      'if tipo:' in historial_movimientos
      and 'q += " AND m.tipo = ?"' in historial_movimientos)
check("el historial limita movimientos a la sucursal activa",
      'clausula_sucursal("m.sucursal_id")' in historial_movimientos)

print("=" * 68)
print("FALLOS:", fallos)
print("=" * 68)
sys.exit(1 if fallos else 0)
