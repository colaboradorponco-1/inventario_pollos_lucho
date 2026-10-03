"""Prueba E2E del flujo logistico POR ETAPA, sin dejar rastro.

Responde una pregunta concreta del negocio: America y Simon no solo PIDEN al
almacen, tambien DISTRIBUYEN a otras sucursales. Entonces el proveedor de un
pedido puede ser un almacen principal O una sucursal distribuidora, y el stock
tiene que salir de ahi igual.

Casos:
  A. Almacen principal -> Sucursal     (el flujo de todas las sucursales)
  B. Sucursal distribuidora -> Sucursal (America/Simon repartiendo)
  C. Una sucursal que NO distribuye no puede ser proveedor
  D. Los totales conservan la precision (sin redondeo a 2 decimales)
  E. Permisos: cada rol solo su paso, y nadie de otra sucursal

COMO ES SEGURA
--------------
get_conn() usa autocommit=False, asi que todo corre en una transaccion abierta.
El endpoint y esta prueba comparten UNA sola conexion con commit() no-op: las
escrituras se acumulan y son visibles para poder MEDIRLAS. Al final hay un unico
rollback() real y se comprueba con una conexion nueva que no quedo nada.

El stock que se usa lo crea la propia prueba con una 'entrada' ficticia dentro
de la transaccion, asi los deltas son exactos y no depende de quantos kilos
haya hoy en cada bodega.

Uso (en el droplet, dentro de /opt/pollos-lucho):
    ./venv/bin/python prueba_etapa_e2e.py
"""
import io
import os
import sys
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, session  # noqa: E402

import database  # noqa: E402
import core.pedidos as ped  # noqa: E402
from core.util import registrar_movimiento, stock_actual  # noqa: E402


class ConexionTransactional:
    """Conexion unica compartida con commit() no-op.

    Asi el endpoint escribe sobre la MISMA transaccion que lee la prueba, que es
    la unica forma de medir el efecto. El commit real es el unico rollback del
    final."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, nombre):
        return getattr(self._conn, nombre)

    def commit(self):
        pass  # no-op: sigue abierta

    def close(self):
        pass  # la transaccion es de esta prueba


ORIG = database.get_conn
CONN = None
TICKET = "PRUEBA-E2E-TMP"


def get_conn_virtual():
    return ConexionTransactional(CONN)


ped.get_conn = get_conn_virtual

srv = Flask(__name__)
srv.secret_key = "prueba-e2e"

fallos = []
AVISO = "PRUEBA: se descarta"


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


def catalogo_sucursales():
    return {r["id"]: r for r in CONN.execute(
        "SELECT id, nombre, principal, IFNULL(provee, 0) AS provee FROM sucursales").fetchall()}


def producto_prueba():
    """Un producto activo cualquiera: el stock lo crea la prueba."""
    return CONN.execute(
        "SELECT id, nombre, IFNULL(costo_promedio, 0) AS costo FROM productos "
        "WHERE activo = 1 ORDER BY id LIMIT 1").fetchone()


def crear_pedido(proveedor_id, sucursal_id, prod, cantidad):
    """Pedido minimo: sucursal_id pide, proveedor_id despacha."""
    cur = CONN.execute(
        "INSERT INTO pedidos (nro_ticket, fecha, sucursal_id, destino_id, estado, total, usuario, nota) "
        "VALUES (%s, NOW(), %s, %s, 'pendiente', 0, 'prueba_e2e', %s)",
        (TICKET, sucursal_id, proveedor_id, AVISO))
    pid = cur.lastrowid
    CONN.execute(
        "INSERT INTO pedido_detalle (pedido_id, producto_id, producto_nombre, cantidad, destino_id, unidad) "
        "VALUES (%s, %s, %s, %s, %s, 'unidad')",
        (pid, prod["id"], prod["nombre"], cantidad, proveedor_id))
    return pid


def etapa_estado(pid):
    f = CONN.execute("SELECT etapa, estado, total FROM pedidos WHERE id = ?", (pid,)).fetchone()
    return f["etapa"], f["estado"], f["total"]


def llamar(pid, rol, etapa, sucursal=None):
    with srv.test_request_context(f"/api/pedidos/{pid}/etapa",
                                  method="PUT", json={"etapa": etapa}):
        session["user_id"] = 1
        session["rol"] = rol
        session["sucursal_id"] = sucursal
        session["usuario"] = "prueba_e2e"
        return ped.pedido_etapa(pid)


def dar_stock(prod_id, sucursal_id, cantidad):
    """Entrada ficticia dentro de la transaccion: el rollback la borra."""
    registrar_movimiento(CONN, prod_id, "entrada", cantidad, 0,
                         datetime.now(), AVISO, "prueba_e2e", sucursal_id)


def correr_flujo(pid, sucursal_logistica, origen_id, destino_id, prod, cantidad):
    """El flujo completo del caso, con las aserciones comunes."""
    stock_o = stock_actual(CONN, prod["id"], origen_id)
    stock_d = stock_actual(CONN, prod["id"], destino_id)

    # 1) El repartidor no arranca solo
    r = llamar(pid, "repartidor", "en_camino", sucursal_logistica)
    check("repartidor NO arranca sin preparacion", es_error(r) and r[1] == 403, mensaje(r))

    # 2) El preparador arranca
    r = llamar(pid, "preparador", "en_preparacion", sucursal_logistica)
    check("preparador marca En preparacion", not es_error(r), mensaje(r))
    e, es, _ = etapa_estado(pid)
    check("la etapa quedo en en_preparacion", e == "en_preparacion", f"etapa={e}")
    check("el stock NO se movio todavia",
          stock_actual(CONN, prod["id"], origen_id) == stock_o,
          f"origen={stock_actual(CONN, prod['id'], origen_id)}")

    # 3) El preparador no se salta su paso
    r = llamar(pid, "preparador", "en_camino", sucursal_logistica)
    check("preparador NO puede marcar En camino", es_error(r) and r[1] == 403, mensaje(r))

    # 4) El repartidor pone En camino
    r = llamar(pid, "repartidor", "en_camino", sucursal_logistica)
    check("repartidor marca En camino", not es_error(r), mensaje(r))
    check("la etapa quedo en en_camino", etapa_estado(pid)[0] == "en_camino")
    check("el stock AUN no se movio",
          stock_actual(CONN, prod["id"], origen_id) == stock_o,
          f"origen={stock_actual(CONN, prod['id'], origen_id)}")

    # 5) Entregado: aqui se mueve el stock
    r = llamar(pid, "repartidor", "entregado", sucursal_logistica)
    check("repartidor marca Entregado", not es_error(r), mensaje(r))
    e, es, total = etapa_estado(pid)
    check("la etapa quedo en entregado", e == "entregado", f"etapa={e}")
    check("el estado quedo en cumplido (los reportes no cambian)", es == "cumplido", f"estado={es}")

    o = stock_actual(CONN, prod["id"], origen_id)
    d = stock_actual(CONN, prod["id"], destino_id)
    print(f"  Stock -> origen: {stock_o} a {o} | destino: {stock_d} a {d}")
    check("el stock del PROVEEDOR bajo la cantidad exacta",
          abs((stock_o - o) - cantidad) < 1e-9, f"delta={stock_o - o}, esperado={cantidad}")
    check("el stock de la SUCURSAL subio la cantidad exacta",
          abs((d - stock_d) - cantidad) < 1e-9, f"delta={d - stock_d}, esperado={cantidad}")

    # 6) Entregar dos veces no repite el movimiento
    r = llamar(pid, "repartidor", "entregado", sucursal_logistica)
    check("entregar de nuevo NO repite el movimiento",
          stock_actual(CONN, prod["id"], origen_id) == o, f"origen={stock_actual(CONN, prod['id'], origen_id)}")

    # 7) Una sucursal ajena no toca el pedido
    r = llamar(pid, "repartidor", "en_camino", 999999)
    check("una sucursal ajena NO puede avanzar el pedido", es_error(r) and r[1] == 403, mensaje(r))

    return total


def main():
    global CONN
    CONN = ORIG()
    suc = catalogo_sucursales()
    prod = producto_prueba()
    if not prod:
        print("No hay productos activos. Abortando.")
        return 1

    principales = [s for s in suc.values() if s["principal"]]
    distribuidoras = [s for s in suc.values() if not s["principal"] and s["provee"]]
    no_distribuidoras = [s for s in suc.values() if not s["principal"] and not s["provee"]]

    print("=" * 74)
    print("PRUEBA E2E POR ETAPA (una transaccion, rollback al final)")
    print("=" * 74)
    print("  Almacenes principales:      " + ", ".join(s["nombre"] for s in principales))
    print("  Sucursales que distribuyen: " + ", ".join(s["nombre"] for s in distribuidoras))
    print("  Sucursales que NO dist:     " + ", ".join(s["nombre"] for s in no_distribuidoras))
    print(f"  Producto de prueba:         {prod['nombre']} (id {prod['id']})")
    print()

    if not principales or not distribuidoras or not no_distribuidoras:
        print("Faltan sucursales de algun tipo. Revisar la tabla sucursales.")
        return 1

    ap = principales[0]
    dist = distribuidoras[0]
    no_dist = no_distribuidoras[0]

    # Stock real de TODAS las sucursales antes de tocar nada: al final tiene que
    # volver exactamente igual. Es la prueba de que el rollback no dejo nada.
    stock_inicial = {sid: stock_actual(CONN, prod["id"], sid) for sid in suc}

    # ---- Caso A: almacen principal -> sucursal (el flujo de todas) ----
    print("-" * 74)
    print(f"CASO A: {ap['nombre']} -> {dist['nombre']}  (almacen principal)")
    print("-" * 74)
    cantidad = 2.0
    dar_stock(prod["id"], ap["id"], 100)
    pid = crear_pedido(ap["id"], dist["id"], prod, cantidad)
    correr_flujo(pid, dist["id"], ap["id"], dist["id"], prod, cantidad)
    print()

    # ---- Caso B: sucursal distribuidora -> sucursal (el caso complicado) ----
    print("-" * 74)
    print(f"CASO B: {dist['nombre']} -> {no_dist['nombre']}  (sucursal que DISTRIBUYE)")
    print("-" * 74)
    cantidad_b = 3.0
    dar_stock(prod["id"], dist["id"], 100)
    pid_b = crear_pedido(dist["id"], no_dist["id"], prod, cantidad_b)
    total_b = correr_flujo(pid_b, no_dist["id"], dist["id"], no_dist["id"], prod, cantidad_b)
    print()

    # ---- Caso C: una sucursal que no distribuye no puede ser proveedor ----
    print("-" * 74)
    print("CASO C: una sucursal que NO distribuye no puede ser proveedor")
    print("-" * 74)
    validos = {r["id"] for r in CONN.execute(
        "SELECT id FROM sucursales WHERE principal = 1 OR IFNULL(provee, 0) = 1").fetchall()}
    check(f"'{no_dist['nombre']}' NO puede ser proveedor", no_dist["id"] not in validos)
    check(f"'{dist['nombre']}' SI puede ser proveedor", dist["id"] in validos)
    for s in no_distribuidoras:
        check(f"'{s['nombre']}' tiene provee apagado", not s["provee"])
    for s in distribuidoras:
        check(f"'{s['nombre']}' tiene provee encendido", bool(s["provee"]))
    print()

    # ---- Caso D: los totales no se redondean ----
    print("-" * 74)
    print("CASO D: los totales conservan la precision (sin redondeo)")
    print("-" * 74)
    # 3 x 3.333 = 9.999 exacto: con round(...,2) el total quedaba en 10.0
    costo = 3.333
    CONN.execute("UPDATE productos SET costo_promedio = ? WHERE id = ?", (costo, prod["id"]))
    dar_stock(prod["id"], ap["id"], 100)
    pid_d = crear_pedido(ap["id"], no_dist["id"], prod, 3.0)
    total_d = correr_flujo(pid_d, no_dist["id"], ap["id"], no_dist["id"], prod, 3.0)
    esperado = 3.0 * costo
    check("el total del pedido NO esta redondeado a 2 decimales",
          abs(total_d - esperado) < 1e-9, f"total={total_d}, exacto={esperado}")
    _, _, total_guardado = etapa_estado(pid_d)
    check("lo guardado en pedidos.total tambien es exacto",
          abs(total_guardado - esperado) < 1e-9, f"guardado={total_guardado}, exacto={esperado}")
    print()

    # ---- CLEANUP: un unico rollback real ----
    print("=" * 74)
    print("CLEANUP: rollback de la transaccion (no queda nada)")
    print("=" * 74)
    CONN.rollback()
    CONN._conn.close()

    o = ORIG()
    sigue = o.execute(
        "SELECT COUNT(*) c FROM pedidos WHERE nro_ticket LIKE 'PRUEBA-E2E%%'").fetchone()["c"]
    check("no queda ningun pedido de prueba", sigue == 0, f"encontrados={sigue}")
    mov = o.execute("SELECT COUNT(*) c FROM movimientos WHERE usuario = 'prueba_e2e'").fetchone()["c"]
    check("no queda ningun movimiento de la prueba", mov == 0, f"encontrados={mov}")
    rep = o.execute("SELECT COUNT(*) c FROM repartos WHERE usuario = 'prueba_e2e'").fetchone()["c"]
    check("no queda ningun reparto de la prueba", rep == 0, f"encontrados={rep}")
    lote = o.execute(
        "SELECT COUNT(*) c FROM movimientos WHERE usuario = 'prueba_e2e' AND lote_id IS NOT NULL"
    ).fetchone()["c"]
    check("no queda ningun lote creado por la prueba", lote == 0, f"encontrados={lote}")
    for sid, snap in stock_inicial.items():
        ahora = stock_actual(o, prod["id"], sid)
        if abs(ahora - snap) > 1e-9:
            check(f"el stock de '{suc[sid]['nombre']}' quedo intacto", False,
                  f"antes={snap}, ahora={ahora}")
            break
    else:
        check(f"el stock quedo intacto en las {len(stock_inicial)} sucursales", True)
    costo_restaurado = o.execute(
        "SELECT IFNULL(costo_promedio, 0) c FROM productos WHERE id = ?", (prod["id"],)).fetchone()["c"]
    check("el costo_promedio del producto quedo como estaba",
          abs(costo_restaurado - prod["costo"]) < 1e-9,
          f"ahora={costo_restaurado}, antes={prod['costo']}")
    o.close()

    print("\n" + "=" * 74)
    print("FALLOS:", len(fallos))
    for f in fallos:
        print("   -", f)
    print("=" * 74)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
