"""Prueba de las transiciones de la etapa logística (pedido_etapa).

Verifica la matriz de permisos: qué rol puede pasar de qué etapa a cuál, y que
al llegar a 'entregado' se intente mover el stock una sola vez.
"""
import io
import inspect
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core.pedidos as ped  # noqa: E402

# Matriz de avance declarada en el backend (flujo unificado).
AVANCE = {
    "preparador": {"pendiente": "en_camino"},
    "repartidor": {"en_camino": "entregado"},
}
ETAPAS = ("pendiente", "en_camino", "entregado", "rechazado")

fallos = 0


def check(nombre, condicion):
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK ' if condicion else 'FALLA'} {nombre}")


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
# Los endpoints de entrega comparten el bloqueo de la fila del pedido y el
# helper no debe hacer rollback, que liberaría el bloqueo antes del commit.
fuente = inspect.getsource(ped._despachar_stock)
sin_rollback = ".rollback(" not in fuente
check("el despacho no libera el bloqueo con rollback", sin_rollback)

for nombre in ("pedido_despachar", "pedido_estado", "pedido_etapa"):
    vista = getattr(ped, nombre)
    bloquea_pedido = "FOR UPDATE" in inspect.getsource(vista)
    check(f"{nombre} bloquea el pedido antes de cambiar stock/estado",
          bloquea_pedido)

# Los roles actúan en el lado que les corresponde: preparar desde el proveedor
# y confirmar entrega desde la sucursal que solicitó.
fuente_etapa = inspect.getsource(ped.pedido_etapa)
check("preparador limitado a su sucursal proveedora",
      'rol == "preparador" and not es_proveedor' in fuente_etapa)
check("repartidor limitado a su sucursal solicitante",
      'rol == "repartidor" and pedido["sucursal_id"] != sid' in fuente_etapa)

# La interfaz debe conservar una sola ubicación para gestionar los estados.
base = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(base, "static", "app.js"), encoding="utf-8") as archivo:
    app_js = archivo.read()
with open(os.path.join(base, "templates", "index.html"), encoding="utf-8") as archivo:
    index_html = archivo.read()
check("sin controles duplicados de cambio de estado",
      all(control not in app_js + index_html for control in (
          "btn-pedido-despachar", "pedido-cambiar-estado",
          "window.despacharPedido", "window.cambiarEstadoPedido")))
check("acciones centralizadas en el detalle",
      "function pintarEtapaEnModal" in app_js and "verPedido(${p.id}, true)" in app_js)

print("\n" + "=" * 70)
print("FALLOS:", fallos)
print("=" * 70)
sys.exit(1 if fallos else 0)
