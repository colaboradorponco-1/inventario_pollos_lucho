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
import core.util as utilmod  # noqa: E402
import core.pedidos as ped  # noqa: E402
from core.util import registrar_movimiento, stock_actual  # noqa: E402


class ConexionTransactional:
    """Conexion unica compartida con commit() y rollback() no-op.

    Asi el endpoint escribe sobre la MISMA transaccion que lee la prueba, que es
    la unica forma de medir el efecto.

    El wrapper no-op evita que los commit() de los endpoints persistan datos
    durante la prueba. Al final hay un unico rollback real sobre CONN._conn."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, nombre):
        return getattr(self._conn, nombre)

    def commit(self):
        pass  # no-op: sigue abierta

    def rollback(self):
        pass  # no-op: ver docstring. El real esta en main()

    def close(self):
        pass  # la transaccion es de esta prueba


ORIG = database.get_conn
CONN = None
TICKET = "PRUEBA-E2E-TMP"


def get_conn_virtual():
    return ConexionTransactional(CONN)


ped.get_conn = get_conn_virtual

# PROBLEMA 2: `registrar_auditoria()` abre su PROPIA conexion y le hace commit.
# Escapa de la transaccion de la prueba, asi que sus filas se guardarian aunque
# todo lo demas se deshaga. Se anula por los dos lados donde se puede estar
# enlazada: el modulo que la define y el que la importa.
AUDITORIAS_ANULADAS = []


def _auditoria_anulada(accion, detalle=""):
    AUDITORIAS_ANULADAS.append(accion)


utilmod.registrar_auditoria = _auditoria_anulada
ped.registrar_auditoria = _auditoria_anulada

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
    """Un producto REAL que tenga 1 sola unidad en todo el sistema.

    Asi, si la prueba somehow dejara algo, el dano maximo posible es una unidad
    y el dueño puede reponerla a mano. Se avisa de cual es antes de empezar."""
    return CONN.execute(
        "SELECT p.id, p.nombre, IFNULL(p.costo_promedio, 0) AS costo, "
        "       SUM(l.cantidad) AS stock_total "
        "FROM productos p JOIN lotes l ON l.producto_id = p.id "
        "WHERE p.activo = 1 "
        "GROUP BY p.id, p.nombre, p.costo_promedio "
        "HAVING SUM(l.cantidad) = 1 "
        "ORDER BY p.id LIMIT 1").fetchone()


def stock_ficticio(cantidad):
    """Stock artificial MINIMO para que los deltas se puedan medir sin duenos.

    Antes eran 100 unidades por caso. Con un producto real de 1 unidad, un
    rollback fallido habria dejado +100 de basura. Con 10 alcanza igual: lo que
    se mide es el delta exacto (2 y 3), no el stock total."""
    return cantidad


def lotos_de_prueba(prod_id):
    """Foto de los lotes del producto, para poder reponerlos a mano si algo queda."""
    return [{
        "id": r["id"], "sucursal_id": r["sucursal_id"], "cantidad": r["cantidad"],
        "vence": str(r["vence"]), "costo": r["costo"],
    } for r in CONN.execute(
        "SELECT id, sucursal_id, cantidad, vence, IFNULL(costo, 0) AS costo "
        "FROM lotes WHERE producto_id = %s ORDER BY id", (prod_id,)).fetchall()]


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


def pedido_en_bandeja(pid, rol, sucursal):
    """El pedido tal como lo recibe el frontend, por la bandeja.

    Esto importa: el endpoint de etapa podia guardar bien la etapa y aun asi la
    pantalla no avanza, porque el SELECT de la bandeja no incluia la columna
    `etapa` y el JS caia siempre en su valor por defecto 'pendiente'. El boton
    se quedaba ahi y al volver a apretar/contestar el backend 'ya estaba en En
    preparacion'. Hay que probar la RESPUESTA, no solo el endpoint."""
    with srv.test_request_context("/api/pedidos/bandeja", method="GET"):
        session["user_id"] = 1
        session["rol"] = rol
        session["sucursal_id"] = sucursal
        session["usuario"] = "prueba_e2e"
        resp = ped.pedidos_bandeja()
    if es_error(resp):
        return None, mensaje(resp)
    # ok() envuelve la respuesta: {"ok": true, "message": ..., "data": [...]}.
    # Iterar el dict directamente devuelve sus CLAVES (strings), por eso se
    # saca 'data' primero.
    cuerpo = resp.get_json() or {}
    grupos = cuerpo.get("data") if isinstance(cuerpo, dict) else cuerpo
    if grupos is None:
        return None, f"la respuesta no traia 'data': {cuerpo!r}"
    for grupo in grupos:
        for p in grupo.get("pedidos", []):
            if p.get("id") == pid:
                return p, ""
    return None, "el pedido no aparece en la bandeja"


def correr_flujo(pid, sucursal_logistica, origen_id, destino_id, prod, cantidad,
                  sucursal_que_pidio=None):
    """El flujo completo del caso, con las aserciones comunes.

    `sucursal_logistica` es el PROVEEDOR (la sucursal whose preparador/repartidor
    despacha). `sucursal_que_pidio` es la que solo hizo el pedido: no puede
    avanzar la etapa, solo mirarlo."""
    stock_o = stock_actual(CONN, prod["id"], origen_id)
    stock_d = stock_actual(CONN, prod["id"], destino_id)

    # 0) La sucursal que PIDIO el pedido no puede tocarlo: lo despacha el almacen.
    if sucursal_que_pidio and sucursal_que_pidio != sucursal_logistica:
        r = llamar(pid, "preparador", "en_preparacion", sucursal_que_pidio)
        check("la sucursal que PIDIO el pedido NO puede marcar 'En preparacion'",
              es_error(r) and r[1] == 403, mensaje(r))
        check("el pedido sigue en pendiente para ella", etapa_estado(pid)[0] == "pendiente")

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
    # La pantalla tiene que reflejar el cambio, no solo la base de datos.
    p, err = pedido_en_bandeja(pid, "preparador", sucursal_logistica)
    check("la bandeja le DEVUELVE la etapa en_preparacion al frontend",
          p is not None and p.get("etapa") == "en_preparacion",
          f"etapa que ve la pantalla={(p or {}).get('etapa')!r} {err}".strip())

    # 3) El preparador no se salta su paso
    r = llamar(pid, "preparador", "en_camino", sucursal_logistica)
    check("preparador NO puede marcar En camino", es_error(r) and r[1] == 403, mensaje(r))

    # 4) El repartidor pone En camino
    r = llamar(pid, "repartidor", "en_camino", sucursal_logistica)
    check("repartidor marca En camino", not es_error(r), mensaje(r))
    check("la etapa quedo en en_camino", etapa_estado(pid)[0] == "en_camino")
    p, err = pedido_en_bandeja(pid, "repartidor", sucursal_logistica)
    check("la bandeja le DEVUELVE la etapa en_camino al frontend",
          p is not None and p.get("etapa") == "en_camino",
          f"etapa que ve la pantalla={(p or {}).get('etapa')!r} {err}".strip())
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


def llamar_estado(pid, rol, sucursal, estado):
    """El endpoint que cambia el `estado` (el que usa el desplegable del almacen)."""
    with srv.test_request_context(f"/api/pedidos/{pid}/estado",
                                  method="PUT", json={"estado": estado}):
        session["user_id"] = 1
        session["rol"] = rol
        session["sucursal_id"] = sucursal
        session["usuario"] = "prueba_e2e"
        return ped.pedido_estado(pid)


def correr_casos():
    """Casos A-D. Todo lo que escribe corre DENTRO de la transaccion de CONN.

    No hace commit nunca. Si esta funcion revienta, el `finally` de main()
    deshace igual, asi que una excepcion a mitad de camino no puede dejar un
    pedido de prueba puesto ni stock ficticio guardado."""
    prod = producto_prueba()
    if not prod:
        print("No hay productos activos. Abortando.")
        return None
    print("=" * 74)
    print("PRUEBA E2E POR ETAPA (una transaccion, rollback al final)")
    print("=" * 74)
    print(f"  Producto de prueba:         {prod['nombre']} (id {prod['id']})")
    print(f"  Stock REAL en todo el sistema: {prod['stock_total']}")
    print()
    print("  AVISO: se eligio un producto que tiene 1 sola unidad para que, si algo")
    print("  quedara, el dano sea de UNA unidad y se pueda reponer a mano. Todo lo")
    print("  que hace la prueba va dentro de una transaccion que se deshace al final.")
    print()

    suc = catalogo_sucursales()
    principales = [s for s in suc.values() if s["principal"]]
    distribuidoras = [s for s in suc.values() if not s["principal"] and s["provee"]]
    no_distribuidoras = [s for s in suc.values() if not s["principal"] and not s["provee"]]
    print("  Almacenes principales:      " + ", ".join(s["nombre"] for s in principales))
    print("  Sucursales que distribuyen: " + ", ".join(s["nombre"] for s in distribuidoras))
    print("  Sucursales que NO dist:     " + ", ".join(s["nombre"] for s in no_distribuidoras))
    print()
    if not principales or not distribuidoras or not no_distribuidoras:
        print("Faltan sucursales de algun tipo. Revisar la tabla sucursales.")
        return None

    ap = principales[0]
    dist = distribuidoras[0]
    no_dist = no_distribuidoras[0]

    # Stock real de TODAS las sucursales antes de tocar nada: al final tiene que
    # volver exactamente igual. Es la prueba de que el rollback no dejo nada.
    stock_inicial = {sid: stock_actual(CONN, prod["id"], sid) for sid in suc}
    lotes_inicial = lotos_de_prueba(prod["id"])
    print("  Lotes ANTES (para reponer a mano si algo queda):")
    for l in lotes_inicial:
        print(f"    lote {l['id']}: sucursal {l['sucursal_id']}, "
              f"{l['cantidad']} unidades, costo {l['costo']}")
    print()

    # ---- Caso A: almacen principal -> sucursal (el flujo de todas) ----
    print("-" * 74)
    print(f"CASO A: {ap['nombre']} -> {dist['nombre']}  (almacen principal)")
    print("-" * 74)
    cantidad = 2.0
    dar_stock(prod["id"], ap["id"], 10)
    pid = crear_pedido(ap["id"], dist["id"], prod, cantidad)
    correr_flujo(pid, ap["id"], ap["id"], dist["id"], prod, cantidad, sucursal_que_pidio=dist["id"])
    print()

    # ---- Caso B: sucursal distribuidora -> sucursal (el caso complicado) ----
    print("-" * 74)
    print(f"CASO B: {dist['nombre']} -> {no_dist['nombre']}  (sucursal que DISTRIBUYE)")
    print("-" * 74)
    cantidad_b = 3.0
    dar_stock(prod["id"], dist["id"], 10)
    pid_b = crear_pedido(dist["id"], no_dist["id"], prod, cantidad_b)
    total_b = correr_flujo(pid_b, dist["id"], dist["id"], no_dist["id"], prod, cantidad_b, sucursal_que_pidio=no_dist["id"])
    print()

    # ---- Caso C: una sucursal que no distribuye no puede ser proveedor ----
    print("-" * 74)
    print("CASO C: una sucursal que NO distribuye no puede ser proveedor")
    print("-" * 74)
    validos = {r["id"] for r in CONN.execute(
        "SELECT id FROM sucursales WHERE principal = 1 OR IFNULL(provee, 0) = 1 "
        "OR IFNULL(es_as, 0) = 1").fetchall()}
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
    dar_stock(prod["id"], ap["id"], 10)
    pid_d = crear_pedido(ap["id"], no_dist["id"], prod, 3.0)
    total_d = correr_flujo(pid_d, ap["id"], ap["id"], no_dist["id"], prod, 3.0, sucursal_que_pidio=no_dist["id"])
    esperado = 3.0 * costo
    check("el total del pedido NO esta redondeado a 2 decimales",
          abs(total_d - esperado) < 1e-9, f"total={total_d}, exacto={esperado}")
    _, _, total_guardado = etapa_estado(pid_d)
    check("lo guardado en pedidos.total tambien es exacto",
          abs(total_guardado - esperado) < 1e-9, f"guardado={total_guardado}, exacto={esperado}")
    print()

    # ---- Caso E: solo el PROVEEDOR cambia el estado ----
    print("-" * 74)
    print("CASO E: solo el almacen (proveedor) cambia el estado del pedido")
    print("-" * 74)
    # Pedido de una sucursal, despachado por el almacen principal.
    stock_antes = stock_actual(CONN, prod["id"], ap["id"])
    stock_suc_antes = stock_actual(CONN, prod["id"], no_dist["id"])
    dar_stock(prod["id"], ap["id"], 10)
    pid_e = crear_pedido(ap["id"], no_dist["id"], prod, 2.0)

    r = llamar_estado(pid_e, "encargado", no_dist["id"], "despachado")
    check(f"el encargado de {no_dist['nombre']} NO cambia el estado de un pedido "
          f"que le hizo a {ap['nombre']}", es_error(r) and r[1] == 403, mensaje(r))
    check("el pedido sigue pendiente", etapa_estado(pid_e)[1] == "pendiente")

    r = llamar_estado(pid_e, "encargado", ap["id"], "despachado")
    check(f"el encargado de {ap['nombre']} SI cambia el estado",
          not es_error(r), mensaje(r))
    check("el estado quedo en despachado", etapa_estado(pid_e)[1] == "despachado",
          f"estado={etapa_estado(pid_e)[1]}")
    # Al despachar, el stock sale del almacen y entra a quien pidio. Se mide el
    # DELTA contra el instante anterior y no contra un numero fijo: el stock de
    # este producto viene acumulado de los casos A-D, asi que un 98.0 fijo
    # comparaba contra la nada y fallaba siempre.
    stock_despues = stock_actual(CONN, prod["id"], ap["id"])
    stock_suc_despues = stock_actual(CONN, prod["id"], no_dist["id"])
    check("el despacho SACO 2 unidades del almacen",
          abs((stock_antes - stock_despues) - 2.0) < 1e-9,
          f"antes={stock_antes}, ahora={stock_despues}, delta={stock_antes - stock_despues}")
    check("el despacho ENTRO 2 unidades en la sucursal que pidio",
          abs((stock_suc_despues - stock_suc_antes) - 2.0) < 1e-9,
          f"antes={stock_suc_antes}, ahora={stock_suc_despues}, "
          f"delta={stock_suc_despues - stock_suc_antes}")
    print()

    return {"prod": prod, "suc": suc, "stock_inicial": stock_inicial,
            "lotes_inicial": lotes_inicial}


def verificar_rollback(contexto):
    """Con una conexo NUEVA: si esto ve datos de la prueba, el rollback fallo."""
    prod, suc, stock_inicial = contexto["prod"], contexto["suc"], contexto["stock_inicial"]
    o = ORIG()
    sigue = o.execute(
        "SELECT COUNT(*) c FROM pedidos WHERE nro_ticket LIKE 'PRUEBA-E2E%%'").fetchone()["c"]
    check("no queda ningun pedido de prueba", sigue == 0, f"encontrados={sigue}")
    mov = o.execute("SELECT COUNT(*) c FROM movimientos WHERE usuario = 'prueba_e2e'").fetchone()["c"]
    check("no queda ningun movimiento de la prueba", mov == 0, f"encontrados={mov}")
    rep = o.execute("SELECT COUNT(*) c FROM repartos WHERE usuario = 'prueba_e2e'").fetchone()["c"]
    check("no queda ningun reparto de la prueba", rep == 0, f"encontrados={rep}")
    aud = o.execute(
        "SELECT COUNT(*) c FROM auditoria WHERE usuario = 'prueba_e2e'").fetchone()["c"]
    check("no queda ninguna fila de AUDITORIA de la prueba", aud == 0, f"encontradas={aud}")
    for sid, snap in stock_inicial.items():
        ahora = stock_actual(o, prod["id"], sid)
        if abs(ahora - snap) > 1e-9:
            check(f"el stock de '{suc[sid]['nombre']}' quedo intacto", False,
                  f"antes={snap}, ahora={ahora}")
            break
    else:
        check(f"el stock quedo intacto en las {len(stock_inicial)} sucursales", True)

    # Los LOTES son lo que hay que reponer a mano si algo quedo: ni cantidad, ni
    # vencimiento, ni costo. Se comparan enteras, no solo el total.
    lotes_ahora = lotos_de_prueba_con(o, prod["id"])
    antes = {l["id"]: l for l in contexto["lotes_inicial"]}
    ahora = {l["id"]: l for l in lotes_ahora}
    if set(antes) != set(ahora):
        check("no se creo ningun lote nuevo", False,
              f"antes={sorted(antes)}, ahora={sorted(ahora)}")
    else:
        iguales = all(
            antes[i]["cantidad"] == ahora[i]["cantidad"]
            and antes[i]["sucursal_id"] == ahora[i]["sucursal_id"]
            and antes[i]["costo"] == ahora[i]["costo"]
            for i in antes)
        check(f"los {len(antes)} lotes del producto quedaron igual (cantidad, "
              f"sucursal y costo)", iguales)

    costo_restaurado = o.execute(
        "SELECT IFNULL(costo_promedio, 0) c FROM productos WHERE id = ?", (prod["id"],)).fetchone()["c"]
    check("el costo_promedio del producto quedo como estaba",
          abs(costo_restaurado - prod["costo"]) < 1e-9,
          f"ahora={costo_restaurado}, antes={prod['costo']}")
    o.close()


def lotos_de_prueba_con(conn, prod_id):
    return [{
        "id": r["id"], "sucursal_id": r["sucursal_id"], "cantidad": r["cantidad"],
        "vence": str(r["vence"]), "costo": r["costo"],
    } for r in conn.execute(
        "SELECT id, sucursal_id, cantidad, vence, IFNULL(costo, 0) AS costo "
        "FROM lotes WHERE producto_id = %s ORDER BY id", (prod_id,)).fetchall()]


def main():
    global CONN
    CONN = ORIG()
    contexto = None
    try:
        contexto = correr_casos()
    except Exception as e:
        print(f"\nLA PRUEBA REVENTO: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Siempre, REVENTE o no. Este es el unico punto donde se deshace todo.
        print("\n" + "=" * 74)
        print("CLEANUP: rollback de la transaccion (no queda nada)")
        print("=" * 74)
        try:
            CONN.rollback()
            CONN._conn.close()
            print("  rollback hecho")
        except Exception as e:
            print("  aviso al cerrar:", e)

    if contexto is not None:
        verificar_rollback(contexto)
    else:
        print("  la prueba no termino; se hizo rollback igual")

    print("\n" + "=" * 74)
    print("FALLOS:", len(fallos))
    for f in fallos:
        print("   -", f)
    print("=" * 74)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
