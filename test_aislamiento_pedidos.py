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


# (etiqueta, rol, sucursal_id, nombre, filtro_obligatorio)
# El filtro importa, no es que "lleve alguno": `destino_id` es el PROVEEDOR y
# `sucursal_id` es quien PIDIO. Un admin de sucursal solo ve lo relacionado con
# su sucursal; únicamente superadmin conserva alcance global.
CASOS = [
    ("preparador America", "preparador", 35, "America", "p.sucursal_id = ?"),
    ("repartidor Simon", "repartidor", 33, "Simon Lopez", "p.sucursal_id = ?"),
    ("encargado filial", "encargado", 42, "La Paz", "p.sucursal_id = ?"),
    ("encargado AP1", "encargado", 30, "Almacen Principal 1", "p.sucursal_id = ?"),
    ("encargado AP2", "encargado", 36, "Almacen Principal 2", "p.sucursal_id = ?"),
    ("admin de sucursal", "admin", 30, "Almacen Principal 1", "p.sucursal_id = ?"),
    ("admin sin sucursal", "admin", None, "sin asignar", "AND 1 = 0"),
    ("superadmin", "superadmin", None, "todas", None),
]

fallos = 0
print("=" * 72)
print("AISLAMIENTO POR SUCURSAL EN GET /api/pedidos")
print("=" * 72)

detalle_mixto = [
    {"id": 1, "destino_id": 30},
    {"id": 2, "destino_id": 36},
    {"id": 3, "destino_id": None},
]
pedido_ajeno = {"sucursal_id": 42, "destino_id": 30}
pedido_propio = {"sucursal_id": 30, "destino_id": 30}
with srv.test_request_context("/api/pedidos/1"):
    session["rol"] = "admin"
    session["sucursal_id"] = 30
    lineas_proveedor = ped._detalle_visible_pedido(pedido_ajeno, detalle_mixto)
    session["rol"] = "encargado"
    lineas_solicitante = ped._detalle_visible_pedido(pedido_propio, detalle_mixto)
    session["rol"] = "superadmin"
    session.pop("sucursal_id", None)
    lineas_superadmin = ped._detalle_visible_pedido(pedido_ajeno, detalle_mixto)

ok_proveedor = [d["id"] for d in lineas_proveedor] == [1, 3]
ok_solicitante = [d["id"] for d in lineas_solicitante] == [1, 2, 3]
ok_superadmin = [d["id"] for d in lineas_superadmin] == [1, 2, 3]
for nombre, resultado in (
        ("el proveedor solo ve las líneas de su almacén", ok_proveedor),
        ("la sucursal solicitante ve todo su pedido", ok_solicitante),
        ("solo superadmin ve todas las líneas del pedido mixto", ok_superadmin)):
    if not resultado:
        fallos += 1
    print(f"{'OK' if resultado else 'FALLA':6s} {nombre}")


class FakeBandejaConn(FakeConn):
    def fetchall(self):
        sql = CAPTURADO[-1][0] if CAPTURADO else ""
        if "SELECT p.id, p.nro_ticket" in sql:
            return [{
                "id": 1, "nro_ticket": "TKT-00001", "fecha": "",
                "estado": "pendiente", "etapa": "pendiente", "nota": "",
                "usuario": "origen", "sucursal_id": 42, "destino_id": 30,
                "sucursal_nombre": "Sucursal solicitante",
            }]
        return []


original_get_conn = ped.get_conn
ped.get_conn = FakeBandejaConn
try:
    CAPTURADO.clear()
    with srv.test_request_context("/api/pedidos/bandeja?alcance=mi-almacen"):
        session["user_id"] = 1
        session["rol"] = "admin"
        session["sucursal_id"] = 30
        session["usuario"] = "prueba"
        ped.pedidos_bandeja()
    consulta_detalle = next(
        (sql, params) for sql, params in CAPTURADO
        if "FROM pedido_detalle d" in sql and "JOIN pedidos p" in sql)
    bandeja_filtrada = (
        "(p.sucursal_id = ? OR COALESCE(d.destino_id, p.destino_id) = ?)"
        in consulta_detalle[0]
        and consulta_detalle[1][-2:] == [30, 30])
finally:
    ped.get_conn = original_get_conn
if not bandeja_filtrada:
    fallos += 1
print(f"{'OK' if bandeja_filtrada else 'FALLA':6s} "
      "la bandeja limita las líneas a las propias o las que despacha el almacén")

for etiqueta, rol, sid, nombre, filtro_esperado in CASOS:
    sql, params = sql_de_listado(rol, sid)
    lleva_destino = "destino_id = ?" in sql
    lleva_sucursal = "p.sucursal_id = ?" in sql
    lleva = lleva_destino or lleva_sucursal

    if filtro_esperado is None:
        ok = not lleva
        detalle = "sin filtro (solo superadmin ve todos)"
    elif filtro_esperado == "AND 1 = 0":
        ok = filtro_esperado in sql and not lleva
        detalle = "sin sucursal, no debe ver pedidos"
    else:
        ok = (filtro_esperado in sql)
        detalle = f"filtro por {filtro_esperado}"

    # El rol logistico ve las dos colas: los pedidos que le hicieron a su
    # sucursal (los que despacha) y los que su sucursal le hizo a un almacen
    # (los que solo puede mirar). Lo que NO puede es desaparecerlos.
    if rol in ("preparador", "repartidor") and not lleva_sucursal:
        ok = False
        detalle = "no ve ni los pedidos que su sucursal pidio (deberia verlos)"

    sids = [p for p in params if isinstance(p, int)]
    veredicto = "OK" if ok else "FALLA"
    if not ok:
        fallos += 1
    print(f"\n{veredicto:6s} {etiqueta:24s} {nombre}")
    print(f"        {detalle}" + (f"  (params {sids})" if lleva else ""))

    conteos = [(q, p) for q, p in CAPTURADO
               if q.startswith("SELECT COUNT(*) AS c FROM pedidos p")]
    conteo_ok = len(conteos) == 1
    if conteo_ok:
        sql_conteo, params_conteo = conteos[0]
        conteo_ok = sql_conteo.count("?") == len(params_conteo)
        if rol in ("encargado", "preparador", "repartidor", "admin") and sid:
            conteo_ok = conteo_ok and "p.sucursal_id = ?" in sql_conteo \
                and "d4.destino_id = ?" in sql_conteo
        if rol == "admin" and sid is None:
            conteo_ok = conteo_ok and "AND 1 = 0" in sql_conteo
    if not conteo_ok:
        fallos += 1
        print("FALLA  conteo de pedidos            filtros y parámetros alineados")
        if conteos:
            print(f"        {conteos[0][0]}  params={conteos[0][1]}")
    else:
        print("OK     conteo de pedidos            filtros y parámetros alineados")

print("\n" + "=" * 72)
print("FALLOS:", fallos)
print("=" * 72)
sys.exit(1 if fallos else 0)
