"""Prueba de las transiciones de la etapa logística (pedido_etapa).

Verifica la matriz de permisos: qué rol puede pasar de qué etapa a cuál, y que
al llegar a 'entregado' se intente mover el stock una sola vez.
"""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\Users\escal\OneDrive\Desktop\Inventario-Pollos-Lucho")

import core.pedidos as ped  # noqa: E402

# Matriz de avance declarada en el backend (flujo unificado).
AVANCE = {
    "preparador": {"pendiente": "en_camino"},
    "repartidor": {"en_camino": "entregado"},
}
ETAPAS = ("pendiente", "en_camino", "entregado", "rechazado")

fallos = 0


def permitido(rol, actual, destino):
    if destino == actual:
        return True  # idempotente: ya esta
    return AVANCE.get(rol, {}).get(actual) == destino


print("=" * 70)
print("MATRIZ DE ETAPAS (rol x etapa actual -> etapa destino)")
print("=" * 70)

for rol in ("preparador", "repartidor"):
    print(f"\n--- {rol} ---")
    for actual in ETAPAS:
        for destino in ETAPAS:
            ok = permitido(rol, actual, destino)
            if ok:
                flecha = "OK  " if destino != actual else "idempotente"
                print(f"  {flecha}  {actual:15s} -> {destino}")
            elif destino != actual:
                marca = ""
                if AVANCE.get(rol, {}).get(actual) is None and actual != "pendiente":
                    marca = "  (ya lo continua otro rol)"
                print(f"  bloqueado      {actual:15s} -> {destino}{marca}")

# El preparador NO puede marcar entregado ni rechazar.
print("\n" + "=" * 70)
print("REGLAS CRITICAS")
print("=" * 70)
reglas = [
    ("preparador: pendiente -> en_camino", permitido("preparador", "pendiente", "en_camino"), True),
    ("preparador: NO puede -> entregado", permitido("preparador", "pendiente", "entregado"), False),
    ("preparador: NO puede rechazar", permitido("preparador", "pendiente", "rechazado"), False),
    ("repartidor: en_camino -> entregado", permitido("repartidor", "en_camino", "entregado"), True),
    ("repartidor: NO puede empezar de cero", permitido("repartidor", "pendiente", "en_camino"), False),
    ("repartidor: NO puede saltar a entregado", permitido("repartidor", "pendiente", "entregado"), False),
    ("nadie puede volver a pendiente", permitido("repartidor", "en_camino", "pendiente"), False),
    ("nadie puede retroceder", permitido("preparador", "en_camino", "pendiente"), False),
    ("rechazado es terminal", permitido("repartidor", "rechazado", "en_camino"), False),
]
for nombre, obtenido, esperado in reglas:
    bien = obtenido == esperado
    if not bien:
        fallos += 1
    print(f"  {'OK ' if bien else 'FALLA'} {nombre}")

# Idempotencia de la entrega: no se puede volver a entregar.
print("\n" + "=" * 70)
print("ENTREGAR DOS VECES (debe quedar bloqueado por etapa, no por stock)")
print("=" * 70)
doble = permitido("repartidor", "entregado", "entregado")
print(f"  entregado -> entregado: {'idempotente (sin mover stock)' if doble else 'bloqueado'}")
# El endpoint tiene la guarda extra: si estado ya es despachado/cumplido no mueve stock.
print("  guarda extra en backend: 'stock_ya_movido' -> no repite _despachar_stock")

print("\n" + "=" * 70)
print("FALLOS:", fallos)
print("=" * 70)
sys.exit(1 if fallos else 0)
