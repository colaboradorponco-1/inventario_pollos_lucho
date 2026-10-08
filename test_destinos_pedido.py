# -*- coding: utf-8 -*-
"""Que la lista de destinos de un pedido la mande la base, y no el nombre.

Regla de negocio: una sucursal puede ser destino de un pedido si tiene marcada
la casilla "¿Provee a otras?" (`sucursales.provee`), si es almacen principal o
si es un almacen AS (`es_as`). El resto de sucursales requiere la casilla.

El bug que este test caza: `sucursalProvee()` tenia un atajo escrito a mano,
por nombre:

    if (m.provee) return true;
    return nombreNorm.includes('america') || nombreNorm.includes('simon lopez');

Con eso, desmarcar la casilla en la pantalla NO servia de nada: America y Simon
 Lopez seguian viendo la bandeja "Pedidos que nos hicieron" y el servidor
seguia aceptandoles pedidos como destino. El checkbox era decorativo.

Y el problema de fondo: una sucursal se puede crear, renombrar o duplicar
cuando quiera. Cualquier atajo por nombre ("si el nombre tiene X, entonces
provee") se rompe en silencio el dia que se cree "America Sur" y no avisa.

Los tests revisan el FUENTE de app.js, no la pantalla: son reglas de negocio
que hoy viven en JS y no hay navegador en la suite. Lo que se comprueba es que
la funcion NO decida por texto de nombre, y que las tres decisiones de destino
(la lista, la agrupacion del catalogo y la bandera de la sucursal propia) lean
la misma columna.
"""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)

APP_JS = os.path.join(RAIZ, "static", "app.js")

FALLOS = []


def _check(nombre, ok, detalle=""):
    print(("OK    " if ok else "FALLO ") + nombre)
    if not ok:
        if detalle:
            print("        " + str(detalle))
        FALLOS.append(nombre)


def _fuente():
    with io.open(APP_JS, encoding="utf-8") as fh:
        return fh.read()


def _cuerpo(nombre_funcion):
    """El cuerpo de una funcion del fuente, hasta su llave de cierre inicial."""
    fuente = _fuente()
    m = re.search(r"function\s+" + nombre_funcion + r"\s*\([^)]*\)\s*\{", fuente)
    if not m:
        return None
    i = m.end() - 1
    nivel = 0
    while i < len(fuente):
        if fuente[i] == "{":
            nivel += 1
        elif fuente[i] == "}":
            nivel -= 1
            if nivel == 0:
                return fuente[m.end():i]
        i += 1
    return None


# Nombres de sucursal que el codigo NO debe usar para decidir si provee.
# Si alguno aparece dentro de una de estas funciones, se volvio a colar un atajo.
NOMBRES_SUCURSAL = [
    "america",
    "simon lopez",
    "siglo xx",
    "la paz",
]


def prueba_sucursal_provee_no_usa_el_nombre():
    """El atajo que hacia inutil el checkbox "¿Provee a otras?"."""
    cuerpo = _cuerpo("sucursalProvee")
    _check("`sucursalProvee` existe", cuerpo is not None,
           "no se encontro la funcion en static/app.js")
    if cuerpo is None:
        return
    colados = [n for n in NOMBRES_SUCURSAL if n in cuerpo.lower()]
    _check("`sucursalProvee` decide por la bandera, no por el nombre",
           not colados,
           f"usa el nombre de la sucursal para decidir que provee: {colados}. "
           f"Con eso desmarcar la casilla 'Provee a otras?' no hacia nada.")
    _check("`sucursalProvee` lee la columna `provee`",
           "provee" in cuerpo,
           "no lee `m.provee`: sin leer la base no puede respetar el checkbox")


def prueba_provee_activo_no_usa_el_nombre():
    """La agrupacion del catalogo por proveedor usa la misma columna."""
    cuerpo = _cuerpo("proveeActivo")
    _check("`proveeActivo` existe", cuerpo is not None)
    if cuerpo is None:
        return
    colados = [n for n in NOMBRES_SUCURSAL if n in cuerpo.lower()]
    _check("`proveeActivo` decide por la bandera, no por el nombre",
           not colados,
           f"usa el nombre de la sucursal: {colados}")


def prueba_la_lista_de_destinos_usa_la_misma_columna():
    """El <select> de destino debe ofrecer solo principales y proveedoras."""
    fuente = _fuente()
    m = re.search(r"const\s+pueden\s*=\s*sucursales\.filter\([^;]*;", fuente)
    _check("el filtro de destinos existe", m is not None,
           "no se encontro `sucursales.filter(...)` armando la lista de destinos")
    if m is None:
        return
    expr = m.group(0)
    _check("la lista de destinos contempla principal, provee y AS",
           "principal" in expr and "provee" in expr and "es_as" in expr,
           f"el filtro es: {expr}")
    colados = [n for n in NOMBRES_SUCURSAL if n in expr.lower()]
    _check("la lista de destinos no filtra por nombre",
           not colados,
           f"filtra por nombre: {colados}")


def prueba_el_servidor_tambien_exige_la_bandera():
    """Si el JS se rompe, el servidor tiene que seguir rechazando el destino.

    Un destino valido se calcula en `core/lotes.py:destinos_validos`, y el
    alta del pedido lo revalida. Si alguno de los dos se queda sin mirar
    `provee`, un pedido podria llegar a una sucursal que nadie marco.
    """
    with io.open(os.path.join(RAIZ, "core", "lotes.py"), encoding="utf-8") as fh:
        lotes = fh.read()
    cuerpo = _cuerpo_en(f"Lotes", lotes, "destinos_validos")
    _check("`destinos_validos` mira `provee`",
           cuerpo is not None and "provee" in cuerpo,
           "la lista de destinos validos no mira `provee`: cualquier filial "
           "volveria a poder recibir pedidos")
    _check("`destinos_validos` reconoce los almacenes AS",
           cuerpo is not None and "es_as" in cuerpo,
           "un almacén AS no podría ofrecer productos para pedidos")

    with io.open(os.path.join(RAIZ, "core", "pedidos.py"), encoding="utf-8") as fh:
        pedidos = fh.read()
    valida = "proveedores_validos" in pedidos and "destinos_malos" in pedidos
    _check("el alta del pedido revalida el destino contra esa lista",
           valida,
           "core/pedidos.py perdio la revalidacion: un destino invalido "
           "pasaria aunque la lista lo excluya")


def _cuerpo_en(etiqueta, fuente, nombre_funcion):
    """Cuerpo de una funcion dentro de un texto ya leido."""
    m = re.search(r"def\s+" + nombre_funcion + r"\s*\([^)]*\)\s*(?:->[^:]*)?:", fuente)
    if not m:
        return None
    i = m.end()
    while i < len(fuente) and fuente[i] in " \t\r\n":
        i += 1
    nivel = 0
    while i < len(fuente):
        c = fuente[i]
        if c == "{":
            nivel += 1
        elif c == "}":
            nivel -= 1
            if nivel == 0:
                return fuente[m.end():i]
        i += 1
    return None


def prueba_el_destino_es_cualquier_almacen_de_la_misma_ciudad():
    """Cada sucursal es un almacén 100% independiente (America, as_america,
    Simon Lopez, as_simon_lopez, el AS de La Paz, ...): el panel de pedidos de
    una sucursal ofrece los Principales (siempre) y TODOS los almacenes de su
    misma ciudad, sin filtrar por "es AS". El pedido va DIRECTO al almacén que
    lo despacha.
    """
    fuente = _fuente()
    _check("la ciudad de La Paz se detecta aunque el nombre no lo diga",
           "esNombreLaPaz" in fuente and "sopocachi" in fuente
           and "miraflores" in fuente,
           "sin esto 'Sopocachi'/'Miraflores' (La Paz) se mostraban como "
           "Cochabamba y aparecian en los pedidos de la otra ciudad")
    _check("los destinos 'Todo a X' ya NO se acotan a Principales + AS",
           "destinosP = pueden;" in fuente,
           "los almacenes de la misma ciudad tienen que ofrecerse directo")
    with io.open(os.path.join(RAIZ, "core", "lotes.py"), encoding="utf-8") as fh:
        lotes = fh.read()
    cuerpo_ids = _cuerpo_en("Lotes", lotes, "_ids_proveedores_misma_ciudad")
    _check("el backend acepta CUALQUIER almacen de la misma ciudad",
           cuerpo_ids is not None and 'if not r.get("es_as"):' not in cuerpo_ids,
           "se colo de vuelta el filtro que solo aceptaba AS como destino")
    _check("el backend sigue limitando por ciudad",
           cuerpo_ids is not None and "ciudad_normalizada" in cuerpo_ids,
           "sin acotar por ciudad los pedidos irian a la otra ciudad")
    with io.open(os.path.join(RAIZ, "core", "productos.py"), encoding="utf-8") as fh:
        prod = fh.read()
    _check("el catalogo de pedidos acota a la sucursal que pide",
           "_ids_proveedores_misma_ciudad(conn," in prod and "desde_cat" in prod,
           "la pantalla ofreceria destinos que luego el pedido rechaza")


def main():
    print("=" * 70)
    print("QUE LOS DESTINOS DE PEDIDO LOS MANDE LA BASE, NO EL NOMBRE")
    print("=" * 70)
    prueba_sucursal_provee_no_usa_el_nombre()
    prueba_provee_activo_no_usa_el_nombre()
    prueba_la_lista_de_destinos_usa_la_misma_columna()
    prueba_el_servidor_tambien_exige_la_bandera()
    prueba_el_destino_es_cualquier_almacen_de_la_misma_ciudad()
    print("=" * 70)
    print(f"FALLOS: {len(FALLOS)}")
    for f in FALLOS:
        print("  - " + f)
    print("=" * 70)
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())
