# -*- coding: utf-8 -*-
"""Orden de los productos en los pedidos y reglas que lo acompanan.

Que el pedido se ordene bien importa mas de lo que parece: lo que se elige en
pantalla tiene que ser lo que queda guardado, lo que se imprime y lo que ve el
proveedor. Antes estas cuatro cosas podian dar ordenes distintas.

Incluye tambien el arreglo del error "'X' no es un almacen valido para pedir":
el catalogo pisaba la columna `sucursales.provee` con un proxy ("esta sucursal
tiene productos activos"), mientras el backend valida contra la columna real. La
pantalla ofrecia productos que el servidor iba a rechazar, y una sola linea mala
tumbaba el pedido entero.
"""
import io
import json
import os
import re
import subprocess
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
FALLOS = []


def _check(nombre, ok, detalle=""):
    print(("OK    " if ok else "FALLO ") + nombre)
    if detalle:
        print("        " + str(detalle))
    if not ok:
        FALLOS.append(nombre)


def _py_fn(src, nombre):
    """Cuerpo de una funcion Python (dedentido a partir de la sangria)."""
    m = re.search(r"^def %s\(.*?\):\s*$" % re.escape(nombre), src, re.M)
    if not m:
        return ""
    base = None
    lineas = []
    for linea in src[m.end():].split("\n"):
        if linea.strip() and not linea.startswith((" ", "\t")):
            break
        lineas.append(linea)
    if not lineas:
        return ""
    base = len(lineas[0]) - len(lineas[0].lstrip())
    return "\n".join(l[base:] if len(l) > base else l for l in lineas)


def _leer(rel):
    with io.open(os.path.join(RAIZ, rel), encoding="utf-8") as fh:
        return fh.read()


def _fn(src, nombre):
    """Devuelve el cuerpo de una funcion del fuente."""
    m = re.search(r"function %s\([^)]*\)\s*\{" % re.escape(nombre), src)
    if not m:
        return ""
    i, nivel = m.end(), 1
    while i < len(src) and nivel:
        if src[i] == "{":
            nivel += 1
        elif src[i] == "}":
            nivel -= 1
        i += 1
    return src[m.end():i - 1]


# ---------------------------------------------------------------- backend ----
def prueba_detalle_ordenado():
    py = _leer(os.path.join("core", "pedidos.py"))
    m = re.search(r"def pedido_detalle\(.*?\n(?=@|\Z)", py, re.S)
    fn = m.group(0) if m else ""
    _check("el detalle del pedido tiene ORDER BY", "ORDER BY" in fn)
    _check("ordena por destino, categoria y producto",
           "d.destino_id" in fn and "categoria_nombre" in fn and "d.producto_nombre" in fn,
           "revisar el ORDER BY de pedido_detalle")
    _check("trae el nombre de la categoria con JOIN",
           "LEFT JOIN categorias" in fn and "AS categoria_nombre" in fn)
    _check("usa COALESCE para productos sin categoria",
           "COALESCE(cat.nombre" in fn or "COALESCE(c.nombre" in fn)


def prueba_ticket_conserva_orden():
    py = _leer(os.path.join("core", "pedidos.py"))
    m = re.search(r"def _ticket_data\(.*?\n(?=@|\Z)", py, re.S)
    fn = m.group(0) if m else ""
    _check("el ticket sigue ordenado por categoria y nombre",
           "ORDER BY c.nombre, d.producto_nombre" in fn)


def prueba_bandeja_ordenada():
    py = _leer(os.path.join("core", "pedidos.py"))
    m = re.search(r"def pedidos_bandeja\(.*?\n(?=@|\Z)", py, re.S)
    fn = m.group(0) if m else ""
    _check("la bandeja ordena el detalle por categoria",
           "categoria_nombre, d.producto_nombre" in fn)


def prueba_catalogo_provee_coincide_con_backend():
    """La causa del error 'no es un almacen valido para pedir'."""
    dash = _leer(os.path.join("core", "dashboard.py"))
    py = _leer(os.path.join("core", "pedidos.py"))
    _check("el catalogo ya NO usa el proxy de 'tiene productos activos'",
           "ids_proveedoras" not in dash)
    _check("el catalogo usa la columna real (principal o provee)",
           re.search(r"dict\(r,\s*provee=bool\(r\.get\(\"principal\"\)\)"
                     r"\s*or\s*bool\(r\.get\(\"provee\"\)\)\)", dash) is not None)
    m = re.search(r"proveedores_validos = \{.*?\}\s*\n", py, re.S)
    validos = m.group(0) if m else ""
    _check("el backend sigue validando con principal = 1 OR provee = 1",
           "principal = 1" in validos and "provee" in validos)
    _check("las dos reglas son la misma", "provee=bool" in dash.replace(" ", "")
           or "provee=bool" in dash)
    # Para poder arreglar los datos de un vistazo: el listado de sucursales dice
    # cuantas tiene sin marcar como proveedora (esos productos no se pueden
    # pedir y antes el error no decia cuales eran).
    suc = _leer(os.path.join("core", "sucursales.py"))
    js = _leer(os.path.join("static", "app.js"))
    _check("el listado de sucursales cuenta sus productos",
           "num_productos" in suc and "AS num_productos" in suc)
    _check("avisa cuando tiene productos pero no provee",
           "num_productos" in js and "¿Provee a otras?" in js)


# --------------------------------------------------------------- frontend ----
def prueba_front_ordena_por_categoria():
    js = _leer(os.path.join("static", "app.js"))
    _check("existe agruparPorCategoria", "function agruparPorCategoria" in js)
    fn = _fn(js, "agruparPorCategoria")
    _check("agrupa por categoria_nombre", "categoria_nombre" in fn)
    _check("dentro de la categoria ordena por nombre",
           "localeCompare(cb)" in fn and "localeCompare(b.nombre" in fn)
    _check("la lista muestra el titulo de cada categoria",
           "cat-titulo" in js)


def _sin_comentarios(texto):
    """Saca las lineas de comentario: los comentarios mencionan el codigo viejo
    a proposito (para explicar el cambio) y ensucian las busquedas."""
    return "\n".join(l for l in texto.split("\n")
                     if not l.strip().startswith("//"))


def prueba_front_manda_en_el_orden_visible():
    js = _leer(os.path.join("static", "app.js"))
    m = re.search(r"\$\('#form-pedido'\)\.addEventListener.*?\n\}\);", js, re.S)
    submit = m.group(0) if m else ""
    _check("el envio usa productosElegidos()", "productosElegidos()" in submit)
    _check("el envio YA NO usa Object.keys(pedidoSel)",
           "Object.keys(pedidoSel)" not in _sin_comentarios(submit),
           "se armaba en el orden de los toques, no en el que se ve")
    rev = _fn(js, "renderRevisionPedido")
    _check("la revision usa el mismo orden", "productosElegidos()" in rev)


def prueba_front_avisa_productos_ocultos():
    """El bug del 24 -> 32: productos seleccionados que el filtro tapaba."""
    js = _leer(os.path.join("static", "app.js"))
    m = re.search(r"\$\('#form-pedido'\)\.addEventListener.*?\n\}\);", js, re.S)
    submit = m.group(0) if m else ""
    _check("calcula cuales seleccionados no se ven", "pedidoProductosVisibles()" in submit)
    _check("avisa antes de enviar", "ocultos" in submit and "confirm(" in submit)
    _check("si cancela, no envia", re.search(r"if \(!confirm\(msg\)\) return;", submit) is not None)
    vis = _fn(js, "pedidoProductosVisibles")
    _check("replica el filtro de almacen", "pedidoProvFiltro" in vis)
    _check("replica la busqueda", "pedido-buscar" in vis)
    _check("replica el filtro de que provee", "proveeActivo" in vis)


def prueba_scroll_de_la_lista():
    css = _leer(os.path.join("static", "style.css"))
    m = re.search(r"#pedido-listado\s*\{(.*?)\}", css, re.S)
    bloque = m.group(1) if m else ""
    _check("la lista de productos tiene alto maximo", "max-height" in bloque, bloque.strip()[:70])
    _check("la lista scrola sola", "overflow-y: auto" in bloque)
    _check("en pantalla chica el alto se relaja",
           "@media (max-width: 900px)" in css and "#pedido-listado" in css)


# ------------------------------------------------------- orden real (node) ---
def prueba_orden_real_en_node():
    """Ejecuta agruparPorCategoria de verdad con datos desordenados."""
    js = _leer(os.path.join("static", "app.js"))
    fn = _fn(js, "agruparPorCategoria")
    if not fn:
        _check("se pudo extraer agruparPorCategoria", False)
        return
    harness = """
const prods = [
  { id: 1, nombre: 'Detergente', categoria_nombre: 'Limpieza' },
  { id: 2, nombre: 'Arroz',      categoria_nombre: 'Alimentos' },
  { id: 3, nombre: 'Aceite',     categoria_nombre: 'Alimentos' },
  { id: 4, nombre: 'Vasos',      categoria_nombre: undefined },
];
"""
    # OJO: en JS la declaracion necesita la lista de parametros aunque el cuerpo
    # venga pegado. _fn devuelve el CUERPO (sin la llave de cierre).
    harness += "function agruparPorCategoria(prods) {" + fn + "}\n"
    harness += """
const g = agruparPorCategoria(prods);
console.log(JSON.stringify(g.map((x) => x.cat + ':' + x.prods.map((y) => y.nombre).join('+'))));
"""
    with io.open(os.path.join(RAIZ, "_ord_tmp.js"), "w", encoding="utf-8") as fh:
        fh.write(harness)
    # Ace ite va antes que Arroz: el orden alfabetico REAL es A-c-e < A-r-r.
    esperado = '["Alimentos:Aceite+Arroz","Limpieza:Detergente","Sin categoría:Vasos"]'
    try:
        # encoding=utf-8 explicito: con text=True se usaria la codificacion del
        # sistema (cp1252 en Windows) y "categoria" con tilde llegaria rota.
        out = subprocess.run(["node", os.path.join(RAIZ, "_ord_tmp.js")],
                             capture_output=True, text=True, timeout=60,
                             encoding="utf-8")
        line = (out.stdout or "").strip().splitlines()
        got = line[-1] if line else ""
        if got != esperado:
            got += "  [stderr: %s]" % ((out.stderr or "").strip()[:200])
    except Exception as e:                                     # noqa: BLE001
        got = "ERROR %s" % e
    finally:
        try:
            os.remove(os.path.join(RAIZ, "_ord_tmp.js"))
        except OSError:
            pass

    _check("ordena por categoria y dentro alfabético", got == esperado,
           "obtenido:  %s\nesperado: %s" % (got, esperado))


# ------------------------------------------------- columnas vs esquema real ---
def prueba_columnas_de_los_joins():
    db = _leer("database.py")
    literal = r'"((?:[^"\\\n]|\\.)*)"'

    def columnas(tabla):
        i = db.find("CREATE TABLE IF NOT EXISTS %s (" % tabla)
        if i < 0:
            return set()
        ini = db.rfind('"', 0, i)
        txt = "".join(re.findall(literal, db[ini:ini + 6000]))
        cuerpo = txt[txt.index("(") + 1:]
        nivel, corte = 1, len(cuerpo)
        for p, ch in enumerate(cuerpo):
            if ch == "(":
                nivel += 1
            elif ch == ")":
                nivel -= 1
                if nivel == 0:
                    corte = p
                    break
        out = set()
        for parte in cuerpo[:corte].split(","):
            mm = re.match(r"\s*`?([a-zA-Z_][a-zA-Z0-9_]*)`?\s+[A-Za-z]", parte)
            if mm:
                out.add(mm.group(1).lower())
        return out

    detalle = columnas("pedido_detalle")
    productos = columnas("productos")
    categorias = columnas("categorias")
    _check("pedido_detalle tiene destino_id y producto_id",
           {"destino_id", "producto_id"} <= detalle)
    _check("productos tiene categoria_id", "categoria_id" in productos)
    _check("categorias tiene nombre", "nombre" in categorias)
    # `provee` no esta en el CREATE TABLE: se agrega por migracion, asi que
    # tambien cuenta como "existe" si aparece el _add_columna.
    tiene_provee = ("provee" in columnas("sucursales")
                    or re.search(r'_add_columna\(cur,\s*"sucursales",\s*"provee', db) is not None)
    _check("sucursales tiene la columna provee (por migracion)",
           tiene_provee,
           "si no esta, el catalogo y el backend no pueden coincidir")


def prueba_error_de_destino_no_tumba_el_pedido():
    """Un pedido de 24 productos no puede caerse por una sola linea mala."""
    py = _leer(os.path.join("core", "pedidos.py"))
    m = re.search(r"def pedidos\(.*?\n(?=@|\Z)", py, re.S)
    fn = m.group(0) if m else ""
    _check("acumula los destinos invalidos", "destinos_malos.setdefault" in fn)
    _check("ya NO corta el pedido en la primera linea mala",
           "no es un almacén válido para pedir" not in fn,
           "esa era la causa de que se cayera el pedido entero")
    _check("informa todos los destinos juntos, despues del bucle",
           re.search(r"if destinos_malos:", fn) is not None
           and fn.index("if destinos_malos:") > fn.index("for item in detalle:"))
    _check("el mensaje dice que marcar y por que", "¿Provee a otras?" in fn)
    # El mensaje se arma ANTES de cerrar la conexion: se llama a
    # _nombre_sucursal(conn, ...) y usarla con la conexion cerrada revienta.
    bloque = fn[fn.index("if destinos_malos:"):]
    _check("arma el mensaje antes de cerrar la conexion",
           bloque.index("_nombre_sucursal") < bloque.index("conn.close()"))


def prueba_destino_se_elige_por_stock():
    """El caso de las tapas: la principal las distribuye y el stock queda en una
    sucursal, pero el producto esta escrito en el catalogo de otra."""
    lotes = _leer(os.path.join("core", "lotes.py"))
    py = _leer(os.path.join("core", "pedidos.py"))
    prod = _leer(os.path.join("core", "productos.py"))
    js = _leer(os.path.join("static", "app.js"))

    _check("existe stock_por_destino (una consulta para todos los productos)",
           "def stock_por_destino" in lotes)
    _check("solo mira destinos que pueden atender (principal o provee)",
           re.search(r"principal = 1 OR IFNULL\(s?\.?provee, 0\) = 1", lotes) is not None)
    _check("ordena por prioridad: principal primero, luego nombre",
           re.search(r"ORDER BY s\.principal DESC, s\.nombre", lotes) is not None)
    _check("existe elegir_destino con la regla de prioridad",
           "def elegir_destino" in lotes and "candidatos[0][0]" in lotes)

    # El backend NO puede seguir usando productos.sucursal_id como destino.
    _check("el pedido YA NO usa la sucursal del catalogo como destino",
           "or fila[\"sucursal_id\"]" not in py,
           "esa era la causa: mandaba el pedido a donde el producto estaba "
           "escrito y no a donde hay stock")
    _check("el pedido resuelve el destino con elegir_destino",
           "elegir_destino(" in py and "candidatos.get(prod_id)" in py)
    _check("calcula los candidatos una sola vez, no por linea",
           py.count("stock_por_destino(conn)") == 1)
    _check("si no hay stock en ningun destino, el error lo dice",
           "No hay stock de" in py and "ningún almacén que" in py)

    # Pantalla y servidor tienen que elegir LO MISMO.
    _check("el catalogo de pedidos pide la resolucion por stock",
           "para_pedido" in prod and "stock_por_destino(conn)" in prod)
    _check("el catalogo manda el destino resuelto y el disponible de ahi",
           "destino_id" in prod and "destino_nombre" in prod and "destino_stock" in prod)
    _check("el disponible se mide contra el destino RESUELTO, no el del catalogo",
           "reservas.get((int(r[\"id\"]), sid_dest)" in prod
           and "stock_dest - reservas.get" in prod)
    _check("lo apartado en pedidos pendientes se descuenta del destino real",
           "COALESCE(d.destino_id, pd.destino_id)" in prod
           and "WHERE pd.estado = 'pendiente'" in prod)
    _check("sin destino valido el disponible es 0 (no ofrece lo que el server rechaza)",
           re.search(r"else:\s*\n\s*# Sin destino v", prod) is not None)
    _check("las demas pantallas NO cambian de comportamiento",
           re.search(r"para_pedido = request\.args\.get", prod) is not None)
    _check("el frontend agrupa por el destino resuelto",
           "function provDeProducto" in js and js.count("provDeProducto(p)") >= 3)
    _check("el frontend pide para_pedido=1",
           "para_pedido: '1'" in js)


def prueba_no_rompe_el_caso_que_ya_funcionaba():
    """La regla 1 de elegir_destino: si el producto esta en un destino valido y
    TIENE stock, se usa ese. No se cambia lo que ya andaba."""
    lotes = _leer(os.path.join("core", "lotes.py"))
    fn = _py_fn(lotes, "elegir_destino")
    _check("primero respeta la sucursal del producto si tiene stock",
           "if sid == int(sucursal_producto)" in fn)
    _check("solo si hay stock (los candidatos ya vienen con stock > 0)",
           "if not candidatos" in fn)
    _check("si no hay ninguno, devuelve None para que el backend avise",
           "return None, 0.0" in fn)


def main():
    print("=" * 72)
    print("ORDEN DE LOS PRODUCTOS EN LOS PEDIDOS")
    print("=" * 72)
    prueba_detalle_ordenado()
    prueba_ticket_conserva_orden()
    prueba_bandeja_ordenada()
    prueba_catalogo_provee_coincide_con_backend()
    prueba_destino_se_elige_por_stock()
    prueba_no_rompe_el_caso_que_ya_funcionaba()
    prueba_error_de_destino_no_tumba_el_pedido()
    prueba_front_ordena_por_categoria()
    prueba_front_manda_en_el_orden_visible()
    prueba_front_avisa_productos_ocultos()
    prueba_scroll_de_la_lista()
    prueba_orden_real_en_node()
    prueba_columnas_de_los_joins()
    print("=" * 72)
    print("FALLOS:", len(FALLOS))
    for f in FALLOS:
        print("  -", f)
    print("=" * 72)
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())
