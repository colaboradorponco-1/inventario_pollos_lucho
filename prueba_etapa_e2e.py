"""Prueba E2E del flujo logistico POR ETAPA, sin dejar rastro.

Ejecuta el endpoint REAL (pedido_etapa) y comprueba, sobre una sola transicion,
que el stock sale del almacen y entra a la sucursal EXACTAMENTE una vez.

COMO ES SEGURA
--------------
get_conn() usa autocommit=False, asi que todo corre en una transaccion abierta.
A esta clase se le pasa UNA sola conexion que comparten el endpoint y la propia
prueba, y su commit() se convierte en un no-op: las escrituras se acumulan en la
transaccion y son VISIBLES para las verificaciones (que es lo que hay que
medir). Al final se hace un unico rollback() real y se comprueba, con una
conexion nueva, que el inventario quedo intacto.

La version anterior interceptaba commit() con rollback() en cada paso: eso
deshacia cada escritura por separado, asi que la prueba no observaba ningun
cambio y ademas el INSERT del pedido de prueba se committed por separado y
quedo en la base. Esta version corrige las dos cosas.

Uso (en el droplet, dentro de /opt/pollos-lucho):
    ./venv/bin/python prueba_etapa_e2e.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, session  # noqa: E402

import database  # noqa: E402
import core.pedidos as ped  # noqa: E402
from core.util import stock_actual  # noqa: E402


class ConexionTransactional:
    """Comparte una unica conexion y convierte commit() en no-op.

    Asi las escrituras del endpoint se acumulan en la transaccion abierta y la
    prueba puede medirlas; el unico commit/rollback real es el de cleanup().
    """

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, nombre):
        return getattr(self._conn, nombre)

    def commit(self):
        pass  # no-op: la transaccion sigue abierta

    def close(self):
        pass  # no cerrar: la transaccion es de esta prueba


ORIG = database.get_conn
CONN = None


def get_conn_virtual():
    return ConexionTransactional(CONN)


ped.get_conn = get_conn_virtual

srv = Flask(__name__)
srv.secret_key = "prueba-e2e"

fallos = []


def check(nombre, condicion, detalle=""):
    marca = "OK   " if condicion else "FALLA"
    print(f"  {marca} {nombre}" + (f"  -> {detalle}" if detalle else ""))
    if not condicion:
        fallos.append(nombre)


def es_error(resp):
    """ok() devuelve una Response; err() devuelve la tupla (Response, status)."""
    return isinstance(resp, tuple)


def mensaje(resp):
    if es_error(resp):
        return f"HTTP {resp[1]}"
    try:
        return (resp.get_json() or {}).get("message", "")
    except Exception:
        return ""


def main():
    global CONN
    CONN = ORIG()

    destino = CONN.execute(
        "SELECT id, nombre FROM sucursales WHERE nombre LIKE '%America%' LIMIT 1").fetchone()
    if not destino:
        print("No se encontro la sucursal America. Abortando.")
        return 1
    destino_id = destino["id"]
    origen = CONN.execute(
        "SELECT id, nombre FROM sucursales WHERE principal = 1 AND id <> %s LIMIT 1",
        (destino_id,)).fetchone()
    if not origen:
        print("No se encontro un almacen principal. Abortando.")
        return 1
    origen_id = origen["id"]

    print("=" * 72)
    print("PRUEBA E2E FLUJO POR ETAPA (una transaccion, rollback al final)")
    print("=" * 72)
    print(f"  Origen (despacha): {origen['nombre']} (id {origen_id})")
    print(f"  Destino (recibe):  {destino['nombre']} (id {destino_id})")

    prod = None
    for p in CONN.execute("SELECT id, nombre FROM productos WHERE activo = 1 ORDER BY id").fetchall():
        if stock_actual(CONN, p["id"], origen_id) > 5:
            prod = p
            break
    if not prod:
        print("No hay producto con stock en el almacen origen. Abortando.")
        return 1
    prod_id = prod["id"]
    cantidad = 2.0
    print(f"  Producto:          {prod['nombre']} (id {prod_id})")
    print(f"  Cantidad de prueba: {cantidad}")

    stock_origen_antes = stock_actual(CONN, prod_id, origen_id)
    stock_destino_antes = stock_actual(CONN, prod_id, destino_id)
    print(f"  Stock ANTES  -> origen: {stock_origen_antes} | destino: {stock_destino_antes}")
    print()

    ticket = "PRUEBA-E2E-TMP"
    cur = CONN.execute(
        "INSERT INTO pedidos (nro_ticket, fecha, sucursal_id, destino_id, estado, total, usuario, nota) "
        "VALUES (%s, NOW(), %s, %s, 'pendiente', 0, 'prueba_e2e', 'PRUEBA: se descarta')",
        (ticket, destino_id, origen_id))
    pedido_id = cur.lastrowid
    CONN.execute(
        "INSERT INTO pedido_detalle (pedido_id, producto_id, producto_nombre, cantidad, destino_id, unidad) "
        "VALUES (%s, %s, %s, %s, %s, 'unidad')",
        (pedido_id, prod_id, prod["nombre"], cantidad, origen_id))

    def etapa_estado():
        f = CONN.execute("SELECT etapa, estado FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
        return f["etapa"], f["estado"]

    def llamar(rol, etapa, sucursal=None):
        with srv.test_request_context(f"/api/pedidos/{pedido_id}/etapa",
                                      method="PUT", json={"etapa": etapa}):
            session["user_id"] = 1
            session["rol"] = rol
            session["sucursal_id"] = sucursal if sucursal is not None else destino_id
            session["usuario"] = "prueba_e2e"
            return ped.pedido_etapa(pedido_id)

    # 1) El repartidor no puede arrancar el pedido
    print("1) El repartidor intenta empezar sin que nadie prepare")
    r = llamar("repartidor", "en_camino")
    check("repartidor NO puede marcar 'En camino' de entrada", es_error(r) and r[1] == 403,
          mensaje(r))
    check("la etapa sigue en 'pendiente'", etapa_estado()[0] == "pendiente")

    # 2) El preparador marca En preparacion
    print("\n2) El preparador marca 'En preparacion'")
    r = llamar("preparador", "en_preparacion")
    check("el preparador PUEDE marcar 'En preparacion'", not es_error(r), mensaje(r))
    e, es = etapa_estado()
    check("la etapa quedo en en_preparacion", e == "en_preparacion", f"etapa={e} estado={es}")
    check("el stock NO se movio todavia",
          stock_actual(CONN, prod_id, origen_id) == stock_origen_antes,
          f"origen={stock_actual(CONN, prod_id, origen_id)}")

    # 3) El preparador no puede marcar En camino
    print("\n3) El preparador intenta marcar 'En camino' (no le corresponde)")
    r = llamar("preparador", "en_camino")
    check("el preparador NO puede marcar 'En camino'", es_error(r) and r[1] == 403, mensaje(r))
    check("la etapa sigue en en_preparacion", etapa_estado()[0] == "en_preparacion")

    # 4) El repartidor marca En camino
    print("\n4) El repartidor marca 'En camino'")
    r = llamar("repartidor", "en_camino")
    check("el repartidor PUEDE marcar 'En camino'", not es_error(r), mensaje(r))
    check("la etapa quedo en en_camino", etapa_estado()[0] == "en_camino")
    check("el stock AUN no se movio",
          stock_actual(CONN, prod_id, origen_id) == stock_origen_antes,
          f"origen={stock_actual(CONN, prod_id, origen_id)}")

    # 5) Entregado: aqui se mueve el stock
    print("\n5) El repartidor marca 'Entregado' (aqui se mueve el stock)")
    r = llamar("repartidor", "entregado")
    check("el repartidor PUEDE marcar 'Entregado'", not es_error(r), mensaje(r))
    e, es = etapa_estado()
    check("la etapa quedo en entregado", e == "entregado", f"etapa={e}")
    check("el estado quedo en cumplido (los reportes no cambian)", es == "cumplido", f"estado={es}")

    origen_despues = stock_actual(CONN, prod_id, origen_id)
    destino_despues = stock_actual(CONN, prod_id, destino_id)
    print(f"  Stock DESPUES -> origen: {origen_despues} | destino: {destino_despues}")
    check("el stock del ORIGEN bajo la cantidad exacta",
          abs((stock_origen_antes - origen_despues) - cantidad) < 1e-6,
          f"delta={stock_origen_antes - origen_despues}, esperado={cantidad}")
    check("el stock del DESTINO subio la cantidad exacta",
          abs((destino_despues - stock_destino_antes) - cantidad) < 1e-6,
          f"delta={destino_despues - stock_destino_antes}, esperado={cantidad}")

    # 6) Entregar dos veces no repite el movimiento
    print("\n6) El repartidor vuelve a marcar 'Entregado'")
    r = llamar("repartidor", "entregado")
    check("no se repite el movimiento de stock",
          stock_actual(CONN, prod_id, origen_id) == origen_despues,
          f"origen={stock_actual(CONN, prod_id, origen_id)}")

    # 7) Una sucursal ajena no puede tocar el pedido
    print("\n7) Un repartidor de otra sucursal intenta tocar ESTE pedido")
    r = llamar("repartidor", "en_camino", sucursal=999999)
    check("una sucursal ajena NO puede avanzar el pedido", es_error(r) and r[1] == 403,
          mensaje(r))

    # --- CLEANUP: un unico rollback real ---
    print("\n" + "=" * 72)
    print("CLEANUP: rollback de la transaccion (no queda nada)")
    print("=" * 72)
    CONN.rollback()
    CONN._conn.close()

    o = ORIG()
    sigue = o.execute("SELECT COUNT(*) c FROM pedidos WHERE nro_ticket LIKE 'PRUEBA-E2E%%'").fetchone()["c"]
    check("no queda ningun pedido de prueba", sigue == 0, f"encontrados={sigue}")
    mov = o.execute(
        "SELECT COUNT(*) c FROM movimientos WHERE usuario = 'prueba_e2e'").fetchone()["c"]
    check("no quedan movimientos de la prueba", mov == 0, f"encontrados={mov}")
    rep = o.execute(
        "SELECT COUNT(*) c FROM repartos WHERE usuario = 'prueba_e2e'").fetchone()["c"]
    check("no quedan repartos de la prueba", rep == 0, f"encontrados={rep}")
    check("el stock real del ORIGEN quedo intacto",
          stock_actual(o, prod_id, origen_id) == stock_origen_antes,
          f"{stock_actual(o, prod_id, origen_id)} vs {stock_origen_antes}")
    check("el stock real del DESTINO quedo intacto",
          stock_actual(o, prod_id, destino_id) == stock_destino_antes,
          f"{stock_actual(o, prod_id, destino_id)} vs {stock_destino_antes}")
    o.close()

    print("\n" + "=" * 72)
    print("FALLOS:", len(fallos))
    for f in fallos:
        print("   -", f)
    print("=" * 72)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
