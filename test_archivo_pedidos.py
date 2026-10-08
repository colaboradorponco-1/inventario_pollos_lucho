"""Pruebas del archivo reversible y separado por sucursal para pedidos."""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, session  # noqa: E402

import core.pedidos as ped  # noqa: E402

CAPTURADO = []
ESTADO_PEDIDO = "entregado"


class FakeConn:
    def execute(self, sql, params=None):
        CAPTURADO.append((" ".join(str(sql).split()), list(params or [])))
        return self

    def fetchone(self):
        if any("SELECT * FROM pedidos WHERE id" in sql for sql, _ in CAPTURADO[-1:]):
            return {
                "id": 7,
                "nro_ticket": "TKT-00007",
                "estado": ESTADO_PEDIDO,
                "sucursal_id": 42,
                "destino_id": 30,
            }
        return {"principal": 0, "nombre": ""}

    def fetchall(self):
        if "SELECT destino_id FROM pedido_detalle" in CAPTURADO[-1][0]:
            return [{"destino_id": 30}]
        return []

    def commit(self):
        pass

    def close(self):
        pass


ped.get_conn = lambda *args, **kwargs: FakeConn()
ped.registrar_auditoria = lambda *args, **kwargs: None

srv = Flask(__name__)
srv.secret_key = "prueba"


def ejecutar_bandeja(vista):
    CAPTURADO.clear()
    with srv.test_request_context(f"/api/pedidos/bandeja?vista={vista}"):
        session["user_id"] = 1
        session["rol"] = "admin"
        session["sucursal_id"] = 30
        ped.pedidos_bandeja()
    return next(sql for sql, _ in CAPTURADO if "FROM pedidos p JOIN sucursales" in sql)


def ejecutar_vista_invalida():
    with srv.test_request_context("/api/pedidos/bandeja?vista=desconocida"):
        session["user_id"] = 1
        session["rol"] = "admin"
        session["sucursal_id"] = 30
        return ped.pedidos_bandeja()


def ejecutar_archivo(estado, archivado):
    global ESTADO_PEDIDO
    ESTADO_PEDIDO = estado
    CAPTURADO.clear()
    with srv.test_request_context(
            "/api/pedidos/7/archivar", method="PUT", json={"archivado": archivado}):
        session["user_id"] = 1
        session["rol"] = "admin"
        session["sucursal_id"] = 30
        return ped.pedido_archivar(7)


fallos = 0


def check(nombre, condicion):
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK' if condicion else 'FALLA'} {nombre}")


print("=" * 68)
print("ARCHIVO REVERSIBLE DE PEDIDOS")
print("=" * 68)

sql_activos = ejecutar_bandeja("activos")
sql_historial = ejecutar_bandeja("historial")
sql_archivados = ejecutar_bandeja("archivados")
check("la bandeja activa solo consulta pendiente y en camino",
      "p.estado IN ('pendiente', 'en_camino')" in sql_activos)
check("el historial omite archivos de la sucursal actual",
      "NOT EXISTS (SELECT 1 FROM pedidos_archivados" in sql_historial
      and "pa.sucursal_id = ?" in sql_historial)
check("la vista archivados se limita a la sucursal actual",
      "EXISTS (SELECT 1 FROM pedidos_archivados" in sql_archivados
      and "pa.sucursal_id = ?" in sql_archivados)
check("una vista no reconocida devuelve error",
      isinstance(ejecutar_vista_invalida(), tuple))

respuesta = ejecutar_archivo("entregado", True)
inserciones = [(sql, params) for sql, params in CAPTURADO
               if "INSERT INTO pedidos_archivados" in sql]
check("solo el pedido terminal se archiva",
      not isinstance(respuesta, tuple) and len(inserciones) == 1)
check("el archivo se guarda para la sucursal que lo solicita",
      bool(inserciones) and inserciones[0][1][:2] == [7, 30])
with open(os.path.join(os.path.dirname(__file__), "database.py"),
          encoding="utf-8") as archivo:
    schema = archivo.read()
check("el mismo pedido puede archivarse por separado en cada sucursal",
      "UNIQUE KEY uq_pedido_sucursal_archivado (pedido_id, sucursal_id)" in schema)

respuesta_pendiente = ejecutar_archivo("pendiente", True)
sqls_pendiente = [sql for sql, _ in CAPTURADO]
check("un pedido pendiente no puede archivarse",
      isinstance(respuesta_pendiente, tuple)
      and not any("INSERT INTO pedidos_archivados" in sql for sql in sqls_pendiente))
respuesta_restaurar = ejecutar_archivo("rechazado", False)
check("un pedido terminal se puede restaurar al historial",
      not isinstance(respuesta_restaurar, tuple)
      and any("DELETE FROM pedidos_archivados" in sql for sql, _ in CAPTURADO))

print("=" * 68)
print("FALLOS:", fallos)
print("=" * 68)
sys.exit(1 if fallos else 0)
