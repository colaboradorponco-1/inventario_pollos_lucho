# -*- coding: utf-8 -*-
"""¿Hubo ajustes de stock duplicados hoy? Solo lectura. NO MODIFICA NADA.

El cierre de una planilla ajusta el stock por la diferencia de cada línea, contra
el `stock_sistema` que quedó CONGELADO cuando se escribió el conteo. Si el mismo
producto se contó en dos planillas del mismo día (una por categoría y otra de
"todas"), cada planilla mueve el stock por su cuenta: el mismo producto queda
ajustado dos veces.

Este script busca exactamente eso: productos con MÁS de un movimiento de ajuste
de inventario en la misma fecha. Cada ajuste deja una nota
"Inventario diario <fecha> (ajuste faltante|sobrante)", así que se pueden
encontrar sin depender de las planillas.

Uso:

    python revisar_ajustes_duplicados.py              # hoy
    python revisar_ajustes_duplicados.py 2026-10-03   # una fecha

Cada ajuste de más se puede revertir con un movimiento contrario, pero ESO NO LO
HACE ESTE SCRIPT: primero hay que ver la lista y decidir.
"""
import io
import os
import sys
from datetime import date

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from database import get_conn, requerir_credenciales  # noqa: E402


def main():
    fecha = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()

    if not requerir_credenciales():
        print("No hay MYSQL_USER/MYSQL_PASS configurados: no se puede leer la base.")
        print("Revisa el .env del servidor.")
        return 1

    conn = get_conn()

    print("=" * 78)
    print("PLANILLAS CERRADAS EL %s" % fecha)
    print("=" * 78)
    planillas = conn.execute(
        "SELECT i.id, i.sucursal_id, i.categoria_id, i.estado, "
        "       i.total_items, i.total_faltantes, i.total_sobrantes, "
        "       i.valor_diferencia, i.cerrado_por, i.fecha_hora_cierre, "
        "       IFNULL(c.nombre, NULL) AS cat_nombre "
        "FROM inventario_diario i "
        "LEFT JOIN categorias c ON c.id = i.categoria_id "
        "WHERE i.fecha = %s ORDER BY i.id", (fecha,)).fetchall()
    if not planillas:
        print("No hay planillas con esa fecha.")
    sucs = {r["id"]: r["nombre"] for r in conn.execute(
        "SELECT id, nombre FROM sucursales").fetchall()}
    for p in planillas:
        # categoria_id = 0 es "todas las categorías"; no hay fila con ese id.
        cat = p["cat_nombre"] or ("TODAS LAS CATEGORÍAS" if not p["categoria_id"]
                                  else str(p["categoria_id"]))
        print("  #%-4s %-22s cat=%-20s %-8s items=%-4s fal=%-4s sob=%-4s dif=Bs %-10s por %s" % (
            p["id"], (sucs.get(p["sucursal_id"]) or "?")[:21], str(cat)[:19],
            p["estado"], p["total_items"], p["total_faltantes"],
            p["total_sobrantes"], p["valor_diferencia"], p["cerrado_por"] or "-"))
    print()
    print("Ojo: hay un índice UNIQUE (sucursal_id, categoria_id, fecha). O sea que")
    print("NO se pueden abrir dos planillas iguales el mismo día: solo se puede")
    print("una de 'todas' y una por categoría. Esa mezcla es la que se pisa.")
    print()

    print("=" * 78)
    print("AJUSTES DE INVENTARIO DEL %s" % fecha)
    print("=" * 78)
    movs = conn.execute(
        "SELECT m.id, m.producto_id, p.nombre AS producto, m.cantidad, m.nota, "
        "       m.usuario, m.sucursal_id, m.fecha "
        "FROM movimientos m JOIN productos p ON p.id = m.producto_id "
        "WHERE m.tipo = 'ajuste' AND m.nota LIKE %s "
        "ORDER BY m.producto_id, m.id", ("Inventario diario " + fecha + "%",)).fetchall()

    if not movs:
        print("No se aplicó ningún ajuste de inventario en esa fecha.")
        conn.close()
        return 0

    print("Total de ajustes aplicados: %d" % len(movs))
    print()

    por_prod = {}
    for m in movs:
        por_prod.setdefault(m["producto_id"], []).append(m)

    duplicados = {k: v for k, v in por_prod.items() if len(v) > 1}
    if not duplicados:
        print("NINGUN producto fue ajustado más de una vez. No hubo doble ajuste.")
        print()
        print("Los %d ajustes corresponden a %d productos distintos, una vez cada uno."
              % (len(movs), len(por_prod)))
        conn.close()
        return 0

    print("!!! %d PRODUCTO(S) AJUSTADOS MÁS DE UNA VEZ !!!" % len(duplicados))
    print()
    for pid, ms in sorted(duplicados.items(), key=lambda kv: kv[1][0]["producto"] or ""):
        neta = sum(m["cantidad"] for m in ms)
        print("-" * 78)
        print("%s  (producto_id %s)  --  %d ajustes" % (ms[0]["producto"], pid, len(ms)))
        for m in ms:
            print("   mov #%-6s %-10s %s" % (m["id"], ("%+.3f" % m["cantidad"]).rstrip("0").rstrip("."),
                                             m["nota"]))
            print("            por %-18s %s" % (m["usuario"] or "-", m["fecha"]))
        print("   SUMA DE AJUSTES: %+.3f  (lo que hay que revertir: %+.3f)"
              % (neta, -neta))
    print("-" * 78)
    print()
    print("=" * 78)
    print("CÓMO LEER ESTO")
    print("=" * 78)
    print("Si el mismo producto aparece en las dos planillas (una por categoría y")
    print("otra de 'todas'), el stock se movió dos veces. Lo que hay que deshacer")
    print("es el ajuste de MÁS: el que no corresponde con el conteo físico real.")
    print()
    print("Ojo: no todos los duplicados están mal. Si una planilla se reopen y se")
    print("cerró otra vez, o si un producto se movió a mano, puede haber dos")
    print("ajustes legítimos. Revisá con el encargado ANTES de revertir nada.")
    print()
    print("Este script no revierte nada. Decime qué productos aparecen y armamos")
    print("el movimiento contrario con respaldo.")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
