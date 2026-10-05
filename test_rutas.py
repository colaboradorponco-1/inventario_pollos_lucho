# -*- coding: utf-8 -*-
"""Que cada RUTA ejecute la vista que le corresponde, con la firma correcta.

Bug real que este test caza: al agregar `_avisos_conteo_cruzado` en core/
inventario.py, la función quedó escrita ENTRE los decoradores
(`@inventario_bp.route(".../cerrar")` y `@login_requerido`) y
`def inventario_cerrar`. Flask ató la ruta del cierre a ese auxiliar, que recibe
`(conn, inv)`: la llamaba con `inv_id` y reventaba con TypeError, o sea un 500
"Error interno del servidor" en TODOS los cierres de planilla. Y
`inventario_cerrar` se quedaba sin ruta y sin candado de sesión: nadie podía
cerrar ninguna planilla.

Peor: los tests de ese momento PASABAN, porque llamaban al auxiliar con
`.__wrapped__` (el decorador equivocado), así que la suite-ba de verde no
significaba que el cierre funcionara.

Por eso se revisa TODO el app, no solo el archivo que se acaba de tocar:
- ninguna ruta puede apuntar a un auxiliar (una función cuyo nombre empieza con _);
- toda ruta tiene que pasarle argumentos que la vista pueda recibir, o el 500
  vuelve a aparecer sin que nadie lo note hasta que un encargado lo reporta.
"""
import inspect
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)

from flask import Flask  # noqa: E402

FALLOS = []

MODULOS = [
    ("core.auth", "auth_bp"),
    ("core.catalogos", "catalogos_bp"),
    ("core.dashboard", "dashboard_bp"),
    ("core.inventario", "inventario_bp"),
    ("core.movimientos", "movimientos_bp"),
    ("core.pages", "pages_bp"),
    ("core.pedidos", "pedidos_bp"),
    ("core.productos", "productos_bp"),
    ("core.reportes", "reportes_bp"),
    ("core.sucursales", "sucursales_bp"),
    ("core.usuarios", "usuarios_bp"),
    ("core.ventas", "ventas_bp"),
]

# Tipo con el que Flask convierte cada conversor de la URL. Si aparece algo que
# no esta aca, se omite: el chequeo de firma no sabria que comparar.
CONVERSORES = {"int": "int", "string": "str", "path": "str"}


def _check(nombre, ok, detalle=""):
    print(("OK    " if ok else "FALLO ") + nombre)
    if detalle:
        print("        " + str(detalle))
    if not ok:
        FALLOS.append(nombre)


def _rutas_de(nombre_mod, nombre_bp):
    """{(MÉTODO ruta): (nombre_mod, nombre_vista, firma)} del blueprint."""
    mod = __import__(nombre_mod, fromlist=[nombre_bp])
    bp = getattr(mod, nombre_bp)
    app = Flask(__name__)
    app.register_blueprint(bp)
    salida = {}
    for regla in app.url_map.iter_rules():
        if not str(regla.rule).startswith("/"):
            continue
        vista = app.view_functions.get(regla.endpoint)
        for metodo in (regla.methods or set()) - {"HEAD", "OPTIONS"}:
            salida[f"{metodo} {regla.rule}"] = (nombre_mod, vista)
    return salida


def prueba_ninguna_ruta_apunta_a_un_auxiliar():
    """Lo que rompia el cierre: la ruta atada a una función que no era la vista."""
    filas = []
    total = 0
    for nombre_mod, nombre_bp in MODULOS:
        for ruta, (mod_nombre, vista) in _rutas_de(nombre_mod, nombre_bp).items():
            total += 1
            fname = getattr(vista, "__name__", "?")
            if fname.startswith("_"):
                filas.append(f"{mod_nombre}  {ruta} -> {fname}")
    _check(f"las {total} rutas del app apuntan a vistas, no a auxiliares "
           f"({len(filas)} mal atadas)",
           not filas,
           "las rutas atadas a auxiliares: " + "; ".join(filas))


def prueba_toda_ruta_pasa_argumentos_que_la_vista_acepta():
    """Si la vista no recibe el argumento de la URL, Flask levanta TypeError -> 500."""
    problemas = []
    total = 0
    for nombre_mod, nombre_bp in MODULOS:
        for ruta, (mod_nombre, vista) in _rutas_de(nombre_mod, nombre_bp).items():
            partes = ruta.split(" ", 1)
            url = partes[1]
            total += 1
            try:
                params = inspect.signature(vista).parameters
            except (TypeError, ValueError):
                continue
            acepta_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD
                            for p in params.values())
            acepta_var = any(p.kind == inspect.Parameter.VAR_POSITIONAL
                             for p in params.values())
            if acepta_kw or acepta_var:
                continue
            firma = ", ".join(params)
            for trozo in url.split("/"):
                if not (":" in trozo and "<" in trozo):
                    continue
                arg = trozo.split(":", 1)[1].split(">")[0]
                conversor = arg.split(":")[-1]
                if conversor not in CONVERSORES or arg in params:
                    continue
                problemas.append(f"{mod_nombre}  {ruta} -> {getattr(vista, '__name__', '?')}"
                                 f"({firma}) no acepta '{arg}'")
    _check(f"las {total} rutas pueden invocar a su vista "
           f"({len(problemas)} firmas incompatibles)",
           not problemas,
           "; ".join(problemas))


def prueba_el_cierre_de_inventario_esta_atado_a_su_vista():
    """El caso concreto que se rompio, nombrado: la ruta del cierre."""
    filas = _rutas_de("core.inventario", "inventario_bp")
    vista = filas.get("POST /api/inventario-diario/<int:inv_id>/cerrar")
    _check("POST .../cerrar ejecuta `inventario_cerrar`",
           vista is not None and getattr(vista[1], "__name__", "") == "inventario_cerrar",
           f"esa ruta ejecuta `{getattr(vista[1], '__name__', '?') if vista else 'NO EXISTE'}`"
           f": con un auxiliar atado, el cierre daba 500 y la planilla nunca "
           f"quedaba marcada como cerrada")


def prueba_las_vistas_de_inventario_no_perdieron_el_candado():
    """Que el decorador de sesión siga pegado a su def en el fuente.

    El bug fue de ORDEN en el archivo (un def nuevo quedó entre el decorador y
    su función), no de contenido. Además de la comprobación de arriba, se mira
    que en el fuente cada `@login_requerido` vaya justo antes de su `def`.
    """
    with io.open(os.path.join(RAIZ, "core", "inventario.py"), encoding="utf-8") as fh:
        fuente = fh.read()
    import re
    sueltos = re.findall(r"@login_requerido\s*\n\s*def\s+", fuente)
    pegados = len(re.findall(r"@login_requerido\s*\n(def\s+\w+)", fuente))
    _check("cada @login_requerido del inventario va pegado a su def",
           pegados == len(sueltos) and pegados > 0,
           f"{pegados} de {len(sueltos)} decoradores bien pegados")


def main():
    print("=" * 70)
    print("QUE CADA RUTA EJECUTE SU VISTA")
    print("=" * 70)
    prueba_ninguna_ruta_apunta_a_un_auxiliar()
    prueba_toda_ruta_pasa_argumentos_que_la_vista_acepta()
    prueba_el_cierre_de_inventario_esta_atado_a_su_vista()
    prueba_las_vistas_de_inventario_no_perdieron_el_candado()
    print("=" * 70)
    print(f"FALLOS: {len(FALLOS)}")
    for f in FALLOS:
        print("  - " + f)
    print("=" * 70)
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())
