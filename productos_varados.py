# -*- coding: utf-8 -*-
"""Reporte de productos que NO puede pedir nadie. Solo lectura.

Un producto se puede pedir únicamente si está registrado bajo una sucursal que
de verdad provee (la principal, una marcada como proveedora o un almacén AS). Por eso
los productos registrados bajo una sucursal que NO provee quedan varados:

  - otra sucursal no puede pedirlos (el destino no es válido)
  - su propia sucursal tampoco (el backend rechaza pedirte a ti mismo)

O sea: ni se venden ni se piden desde el formulario. Este script los lista para
decidir qué hacer con ellos. NO MODIFICA NADA: solo SELECT.

Uso (en el servidor, desde la carpeta del proyecto):

    python productos_varados.py

Si el proyecto lee las credenciales del .env, no hace falta pasar nada.
"""
import io
import os
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from database import get_conn, requerir_credenciales  # noqa: E402


def _sucursales(conn):
    return {r["id"]: r for r in conn.execute(
        "SELECT s.id, s.nombre, s.principal, IFNULL(s.provee, 0) AS provee, "
        "IFNULL(s.es_as, 0) AS es_as, "
        "EXISTS (SELECT 1 FROM usuarios u "
        "WHERE u.sucursal_id = s.id AND IFNULL(u.receptor, 0) = 1) AS receptor, "
        "EXISTS (SELECT 1 FROM almacenes a "
        "WHERE a.sucursal_id = s.id AND a.nombre LIKE 'AS %') AS almacen_as "
        "FROM sucursales s ORDER BY principal DESC, nombre").fetchall()}


def _puede_ser_destino(s):
    return (bool(s["principal"]) or bool(s["provee"]) or bool(s["es_as"])
            or bool(s["receptor"]) or bool(s["almacen_as"]))


def main():
    if not requerir_credenciales():
        print("No hay MYSQL_USER/MYSQL_PASS configurados: no se puede leer la base.")
        print("Revisa el .env del servidor.")
        return 1

    conn = get_conn()
    sucursales = _sucursales(conn)

    print("=" * 78)
    print("SUCURSALES")
    print("=" * 78)
    print("%-26s %-10s %s" % ("SUCURSAL", "PRINCIPAL", "¿PROVEE A OTRAS?"))
    for s in sucursales.values():
        print("%-26s %-10s %s" % (
            s["nombre"][:25],
            "si" if s["principal"] else "no",
            "si" if _puede_ser_destino(s) else "NO"))
    print()

    # Productos por sucursal, con el stock real (lotes) al lado: sirve para
    # decidir si el stock está de verdad en ese almacén o si el producto se
    # registró en la sucursal equivocada.
    filas = conn.execute("""
        SELECT p.sucursal_id,
               COUNT(*) AS num,
               COALESCE(SUM(l.stock), 0) AS stock
        FROM productos p
        LEFT JOIN (SELECT producto_id, SUM(cantidad) AS stock FROM lotes GROUP BY producto_id) l
               ON l.producto_id = p.id
        WHERE p.activo = 1
        GROUP BY p.sucursal_id
        ORDER BY num DESC
    """).fetchall()

    print("=" * 78)
    print("DÓNDE ESTÁN LOS PRODUCTOS")
    print("=" * 78)
    print("%-26s %-9s %-12s %s" % ("SUCURSAL", "PRODUCTOS", "STOCK", "¿SE PUEDEN PEDIR?"))
    varados = []
    for f in filas:
        sid = f["sucursal_id"]
        s = sucursales.get(sid)
        nombre = s["nombre"] if s else "(producto global, sin sucursal)"
        ok = bool(s) and _puede_ser_destino(s)
        print("%-26s %-9s %-12s %s" % (
            nombre[:25], f["num"], f["stock"], "si" if ok else "NO -> NADIE PUEDE PEDIRLOS"))
        if not ok:
            varados.append((sid, nombre, f["num"], f["stock"]))
    print()

    if not varados:
        print("Todo bien: no hay productos varados.")
        conn.close()
        return 0

    print("=" * 78)
    print("PRODUCTOS QUE HAY QUE REVISAR (NO SE TOCA NADA, SOLO INFORMA)")
    print("=" * 78)
    total = 0
    for sid, nombre, num, stock in varados:
        print()
        print("--- %s  (%s productos, stock %s) ---" % (nombre, num, stock))
        print("%-8s %-38s %s" % ("ID", "PRODUCTO", "STOCK"))
        prods = conn.execute(
            "SELECT p.id, p.nombre, p.codigo, "
            "  COALESCE((SELECT SUM(l.cantidad) FROM lotes l WHERE l.producto_id = p.id), 0) AS stock "
            "FROM productos p WHERE p.activo = 1 AND p.sucursal_id = %s "
            "ORDER BY p.nombre", (sid,)).fetchall()
        for p in prods:
            total += 1
            marca = "  (sin stock)" if not p["stock"] else ""
            print("%-8s %-38s %s%s" % (p["id"], (p["nombre"] or "")[:37], p["stock"], marca))
        print("   ¿Son productos que en realidad están en el Almacén 1/2?")
        print()
        print("   Si SÍ, hay que moverlos al Almacén principal. Este script NO lo")
        print("   hace. Cuando lo decidas, el UPDATE es (reemplazá <ID_PRINCIPAL>):")
        print()
        print("       UPDATE productos SET sucursal_id = <ID_PRINCIPAL>")
        print("       WHERE id IN (%s);" % ", ".join(str(p["id"]) for p in prods))
        print()
        print("   OJO: mover el producto NO mueve el stock. El stock vive en")
        print("   `lotes`, y hay que revisar que los lotes de estos productos")
        print("   estén en los almacenes del Almacén 1/2 y no en los de esta")
        print("   sucursal. Mover solo `productos` sin tocar `lotes` deja el")
        print("   stock donde estaba.")
        print()
        print("   Si son stock local de esa sucursal -> dejalos, pero sepa que")
        print("   no se van a poder pedir desde el formulario.")

    print()
    print("=" * 78)
    print("TOTAL A REVISAR:", total)
    print("=" * 78)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
