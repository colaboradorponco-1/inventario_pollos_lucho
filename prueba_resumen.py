"""Resumen de control DESPUES de la prueba E2E.

Dice dos cosas, para que haya que mirar una sola pantalla:

1. Que productos tienen 1 sola unidad en todo el sistema. Son los que la prueba
   usa, asi que si el rollback fallara el dano seria de una sola unidad y esto
   dice exactamente cual reponer a mano.
2. Si quedo alguna fila de la prueba: pedidos, movimientos, repartos y auditoria.
   Todo tiene que dar 0. Cualquier numero distinto de 0 significa que la
   transaccion se filtro por algun lado y hay que limpiar.

Solo lee. No escribe nada.
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_conn  # noqa: E402

RESIDUOS = [
    ("pedidos PRUEBA-E2E", "SELECT COUNT(*) n FROM pedidos WHERE nro_ticket LIKE 'PRUEBA-E2E%%'"),
    ("movimientos prueba_e2e", "SELECT COUNT(*) n FROM movimientos WHERE usuario = 'prueba_e2e'"),
    ("repartos prueba_e2e", "SELECT COUNT(*) n FROM repartos WHERE usuario = 'prueba_e2e'"),
    ("auditoria prueba_e2e", "SELECT COUNT(*) n FROM auditoria WHERE usuario = 'prueba_e2e'"),
    ("detalles de pedido huerfanos",
     "SELECT COUNT(*) n FROM pedido_detalle d "
     "LEFT JOIN pedidos p ON p.id = d.pedido_id WHERE p.id IS NULL"),
]


def main():
    conn = get_conn()
    print("PRODUCTOS CON 1 SOLA UNIDAD (los que usa la prueba):")
    filas = conn.execute(
        "SELECT p.id, p.nombre, SUM(l.cantidad) s FROM productos p "
        "JOIN lotes l ON l.producto_id = p.id WHERE p.activo = 1 "
        "GROUP BY p.id, p.nombre HAVING SUM(l.cantidad) = 1 ORDER BY p.id LIMIT 5"
    ).fetchall()
    if filas:
        for r in filas:
            print(f"  id {r['id']} | {r['nombre']} | {r['s']} unidad(es)")
    else:
        print("  (ninguno: la prueba no va a poder elegir producto)")

    print()
    print("RESIDUOS (todo tiene que estar en 0):")
    sucio = 0
    for nombre, sql in RESIDUOS:
        n = conn.execute(sql).fetchone()["n"]
        sucio += n
        marca = "limpio" if n == 0 else f"!!! {n} filas"
        print(f"  {nombre}: {marca}")
    conn.close()
    print()
    print("RESULTADO:", "SIN RASTROS" if sucio == 0 else f"HAY {sucio} FILAS QUE HAY QUE BORRAR")
    return 1 if sucio else 0


if __name__ == "__main__":
    sys.exit(main())
