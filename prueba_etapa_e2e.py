"""Prueba E2E del flujo logistico POR ETAPA, sin dejar rastro.

Ejecuta el endpoint real (pedido_etapa) con un pedido de prueba y verifica que el
stock salga del Almacén Principal y entre a la sucursal EXACTAMENTE una vez.

Seguridad: como get_conn() usa autocommit=False, todo corre dentro de una
transaccion que se descarta con ROLLBACK al final. Este script NO modifica el
inventario real, ni deja pedidos, repartos ni movimientos: es una simulacion.

Uso (en el droplet, dentro de /opt/pollos-lucho):
    ./venv/bin/python prueba_etapa_e2e.py
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask  # noqa: E402

import database  # noqa: E402
import core.pedidos as ped  # noqa: E402
from core.util import stock_actual  # noqa: E402


class ConexionSinCommit:
    """Envuelve la conexion y convierte cada commit() en un rollback().

    Asi el endpoint real se ejecuta de punta a punta (mismo SQL, misma logica,
    mismos triggers) pero NADA queda persistido.
    """

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, nombre):
        return getattr(self._conn, nombre)

    def commit(self):
        # rollback en lugar de commit: el endpoint cree que guardo, la BD no cambia.
        self._conn.rollback()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        pass  # no cerrar: reutilizamos la misma transaccion


ORIG = database.get_conn


def get_conn_virtual():
    return ConexionSinCommit(ORIG())


ped.get_conn = get_conn_virtual

srv = Flask(__name__)
srv.secret_key = "prueba-e2e"

fallos = []


def check(nombre, condicion, detalle=""):
    marca = "OK   " if condicion else "FALLA"
    print(f"  {marca} {nombre}" + (f"  -> {detalle}" if detalle else ""))
    if not condicion:
        fallos.append(nombre)


def main():
    conn = ORIG()

    # Sucursales: un almacen principal y una filial que recibe.
    destino = conn.execute(
        "SELECT id, nombre FROM sucursales WHERE nombre LIKE '%America%' LIMIT 1").fetchone()
    if not destino:
        print("No se encontro la sucursal America. Abortando.")
        return 1
    destino_id = destino["id"]
    origen = conn.execute(
        "SELECT id, nombre FROM sucursales WHERE principal = 1 AND id <> %s LIMIT 1",
        (destino_id,)).fetchone()
    if not origen:
        print("No se encontro un almacen principal. Abortando.")
        return 1
    origen_id = origen["id"]

    print("=" * 72)
    print("PRUEBA E2E FLUJO POR ETAPA (con ROLLBACK, no deja rastro)")
    print("=" * 72)
    print(f"  Origen (despacha): {origen['nombre']} (id {origen_id})")
    print(f"  Destino (recibe):  {destino['nombre']} (id {destino_id})")

    # Producto con stock real en el origen.
    prod = None
    for p in conn.execute("SELECT id, nombre FROM productos WHERE activo = 1 ORDER BY id").fetchall():
        if stock_actual(conn, p["id"], origen_id) > 5:
            prod = p
            break
    if not prod:
        print("No hay producto con stock en el almacen origen. Abortando.")
        return 1
    prod_id = prod["id"]
    cantidad = 2.0
    print(f"  Producto:          {prod['nombre']} (id {prod_id})")
    print(f"  Cantidad de prueba: {cantidad}")
    print()

    stock_origen_antes = stock_actual(conn, prod_id, origen_id)
    stock_destino_antes = stock_actual(conn, prod_id, destino_id)
    print(f"  Stock ANTES  -> origen: {stock_origen_antes} | destino: {stock_destino_antes}")
    print()

    # Crear el pedido de prueba (tambien dentro de la transaccion).
    cur = conn.execute(
        "INSERT INTO pedidos (nro_ticket, fecha, sucursal_id, destino_id, estado, total, usuario, nota) "
        "VALUES ('PRUEBA-E2E', NOW(), %s, %s, 'pendiente', 0, 'prueba_e2e', 'PRUEBA: se descarta')",
        (destino_id, origen_id))
    pedido_id = cur.lastrowid
    conn.execute(
        "INSERT INTO pedido_detalle (pedido_id, producto_id, producto_nombre, cantidad, destino_id, unidad) "
        "VALUES (%s, %s, %s, %s, %s, 'unidad')",
        (pedido_id, prod_id, prod["nombre"], cantidad, origen_id))
    conn.commit()  # commit real solo para que el endpoint vea su pedido

    def llamar_etapa(rol, etapa):
        with srv.test_request_context(
                f"/api/pedidos/{pedido_id}/etapa", method="PUT",
                json={"etapa": etapa}):
            from flask import session
            session["user_id"] = 1
            session["rol"] = rol
            session["sucursal_id"] = destino_id
            session["usuario"] = "prueba_e2e"
            resp = ped.pedido_etapa(pedido_id)
            return resp

    def estado_actual():
        f = conn.execute("SELECT etapa, estado FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
        return f["etapa"], f["estado"]

    # --- 1) El repartidor NO puede arrancar el pedido ---
    print("1) El repartidor intenta empezar sin que nadie prepare")
    r = llamar_etapa("repartidor", "en_camino")
    check("repartidor no puede marcar 'En camino' de entrada", r[1] == 403,
          f"HTTP {r[1]}")
    check("la etapa sigue en 'pendiente'", estado_actual()[0] == "pendiente")

    # --- 2) El preparador marca En preparacion ---
    print("\n2) El preparador marca 'En preparacion'")
    r = llamar_etapa("preparador", "en_preparacion")
    check("el preparador puede marcar 'En preparacion'", r[0] is not None)
    e, es = estado_actual()
    check("etapa = en_preparacion", e == "en_preparacion", f"etapa={e} estado={es}")
    check("el stock NO se movio todavia", stock_actual(conn, prod_id, origen_id) == stock_origen_antes,
          f"origen={stock_actual(conn, prod_id, origen_id)}")

    # --- 3) El preparador no puede marcar En camino ---
    print("\n3) El preparador intenta marcar 'En camino' (no le corresponde)")
    r = llamar_etapa("preparador", "en_camino")
    check("el preparador NO puede marcar 'En camino'", r[1] == 403, f"HTTP {r[1]}")

    # --- 4) El repartidor marca En camino ---
    print("\n4) El repartidor marca 'En camino'")
    r = llamar_etapa("repartidor", "en_camino")
    check("el repartidor puede marcar 'En camino'", r[0] is not None)
    check("etapa = en_camino", estado_actual()[0] == "en_camino")
    check("el stock AUN no se movio", stock_actual(conn, prod_id, origen_id) == stock_origen_antes,
          f"origen={stock_actual(conn, prod_id, origen_id)}")

    # --- 5) El repartidor marca Entregado: aqui se mueve el stock ---
    print("\n5) El repartidor marca 'Entregado' (aqui se mueve el stock)")
    r = llamar_etapa("repartidor", "entregado")
    check("el repartidor puede marcar 'Entregado'", r[0] is not None,
          "" if r[0] is not None else str(r))
    e, es = estado_actual()
    check("etapa = entregado", e == "entregado", f"etapa={e}")
    check("estado = cumplido (para que los reportes no cambien)", es == "cumplido", f"estado={es}")

    origen_despues = stock_actual(conn, prod_id, origen_id)
    destino_despues = stock_actual(conn, prod_id, destino_id)
    print(f"  Stock DESPUES -> origen: {origen_despues} | destino: {destino_despues}")
    check("el stock del origen BAJO por la cantidad exacta",
          abs((stock_origen_antes - origen_despues) - cantidad) < 1e-6,
          f"delta={stock_origen_antes - origen_despues}, esperado={cantidad}")
    check("el stock del destino SUBIO por la cantidad exacta",
          abs((destino_despues - stock_destino_antes) - cantidad) < 1e-6,
          f"delta={destino_despues - stock_destino_antes}, esperado={cantidad}")

    # --- 6) Entregar dos veces no vuelve a mover stock ---
    print("\n6) El repartidor vuelve a marcar 'Entregado' (debe ser idempotente)")
    r = llamar_etapa("repartidor", "entregado")
    check("no se repite el movimiento de stock",
          stock_actual(conn, prod_id, origen_id) == origen_despues,
          f"origen={stock_actual(conn, prod_id, origen_id)}")

    # --- 7) Una sucursal ajena no puede tocar el pedido ---
    print("\n7) Un repartidor de otra sucursal intenta tocar ESTE pedido")
    with srv.test_request_context(f"/api/pedidos/{pedido_id}/etapa", method="PUT",
                                  json={"etapa": "entregado"}):
        from flask import session
        session["user_id"] = 2
        session["rol"] = "repartidor"
        session["sucursal_id"] = 999999  # sucursal que no es el destino
        session["usuario"] = "prueba_e2e_ajeno"
        r = ped.pedido_etapa(pedido_id)
    check("una sucursal ajena NO puede avanzar el pedido", r[1] == 403, f"HTTP {r[1]}")

    # --- LIMPIEZA ---
    print("\n" + "=" * 72)
    print("LIMPIEZA: rollback total (no queda pedido, reparto ni movimiento)")
    print("=" * 72)
    conn.rollback()
    sigue = conn.execute("SELECT COUNT(*) c FROM pedidos WHERE nro_ticket = 'PRUEBA-E2E'").fetchone()["c"]
    check("el pedido de prueba no existe", sigue == 0, f"encontrados={sigue}")
    mov = conn.execute(
        "SELECT COUNT(*) c FROM movimientos WHERE nota LIKE '%%PRUEBA%%' OR usuario = 'prueba_e2e'"
    ).fetchone()["c"]
    check("no quedan movimientos de la prueba", mov == 0, f"encontrados={mov}")

    o = ORIG()
    stock_final_origen = stock_actual(o, prod_id, origen_id)
    stock_final_destino = stock_actual(o, prod_id, destino_id)
    check("el stock real del origen quedo INTACTO",
          stock_final_origen == stock_origen_antes,
          f"{stock_final_origen} vs {stock_origen_antes}")
    check("el stock real del destino quedo INTACTO",
          stock_final_destino == stock_destino_antes,
          f"{stock_final_destino} vs {stock_destino_antes}")
    o.close()
    conn.close()

    print("\n" + "=" * 72)
    print("FALLOS:", len(fallos))
    for f in fallos:
        print("   -", f)
    print("=" * 72)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
