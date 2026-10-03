"""Prueba de aislamiento por sucursal en GET /api/pedidos.

Reproduce el bug que hacia que preparador/repartidor vieran los pedidos de
TODAS las sucursales (el filtro de `core/pedidos.py` solo se aplicaba cuando el
rol era 'encargado'). Se intercepta el SQL y se comprueba que cada rol lleve su
clausula de sucursal... o que no lleve ninguna si legitimately ve todo.
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CAPTURADO = []


class FakeCursor:
    def execute(self, sql, params=None):
        CAPTURADO.append((" ".join(str(sql).split()), list(params or [])))
        return self

    def fetchone(self):
        return {"principal": 0, "c": 0, "n": 0, "nombre": ""}

    def fetchall(self):
        return []

    def __getattr__(self, _):
        return lambda *a, **k: None


class FakeConn:
    def cursor(self):
        return FakeCursor()

    def execute(self, sql, params=None):
        CAPTURADO.append((" ".join(str(sql).split()), list(params or [])))
        return self

    def fetchone(self):
        # El endpoint hace varios SELECT; se devuelve un dict con todas las
        # claves que cualquiera de ellos pueda pedir.
        fila = {"c": 0, "n": 0, "nombre": "", "principal": 0}
        for sql, params in reversed(CAPTURADO):
            if "SELECT principal FROM sucursales" in sql:
                fila["principal"] = 1 if (params or [None])[0] in (30, 36) else 0
                break
        return fila

    def fetchall(self):
        return []

    def close(self):
        pass


import core.pedidos as ped  # noqa: E402

ped.get_conn = lambda *a, **k: FakeConn()

from flask import Flask, session  # noqa: E402

srv = Flask(__name__)
srv.secret_key = "prueba"


def sql_de_listado(rol, sucursal_id):
    CAPTURADO.clear()
    with srv.test_request_context("/api/pedidos"):
        session["user_id"] = 1
        session["rol"] = rol
        session["sucursal_id"] = sucursal_id
        session["usuario"] = "prueba"
        ped.pedidos()
    for sql, params in CAPTURADO:
        if "FROM pedidos p" in sql:
            return sql, params
    return "", []


# (etiqueta, rol, sucursal_id, nombre, debe_llevar_filtro)
CASOS = [
    ("preparador America", "preparador", 35, "America", True),
    ("repartidor Simon", "repartidor", 33, "Simon Lopez", True),
    ("encargado filial", "encargado", 42, "La Paz", True),
    ("encargado principal", "encargado", 30, "Almacen Principal 1", False),
    ("admin", "admin", 30, "Almacen Principal 1", False),
    ("superadmin", "superadmin", None, "todas", False),
]

fallos = 0
print("=" * 72)
print("AISLAMIENTO POR SUCURSAL EN GET /api/pedidos")
print("=" * 72)

for etiqueta, rol, sid, nombre, debe_filtrar in CASOS:
    sql, params = sql_de_listado(rol, sid)
    lleva = "destino_id = ?" in sql or "p.sucursal_id = ?" in sql
    sids = [p for p in params if isinstance(p, int)]
    veredicto = "OK" if lleva == debe_filtrar else "FALLA"
    if lleva != debe_filtrar:
        fallos += 1
    detalle = (f"FILTRADO por sucursal {sids}" if lleva
               else "sin filtro (ve todos, por diseño)")
    print(f"\n{veredicto:6s} {etiqueta:24s} {nombre}")
    print(f"        {detalle}")

print("\n" + "=" * 72)
print("FALLOS:", fallos)
print("=" * 72)
sys.exit(1 if fallos else 0)
