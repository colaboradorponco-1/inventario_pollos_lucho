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
import ast
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


def _py_def(src, nombre):
    """La función Python COMPLETA (con su `def`), para poder ejecutarla."""
    src = src.lstrip("\ufeff")
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.FunctionDef) and n.name == nombre:
            return ast.get_source_segment(src, n) or ""
    return ""


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
    lotes = _leer(os.path.join("core", "lotes.py"))
    suc = _leer(os.path.join("core", "sucursales.py"))
    _check("el catalogo ya NO usa el proxy de 'tiene productos activos'",
           "ids_proveedoras" not in dash)
    # El catálogo usa la regla de destino del backend; /api/sucursales expone
    # la configuración explícita que permite administrar la misma regla.
    _check("el catalogo deriva provee con la misma regla de destinos_validos",
           "destinos_validos(conn)" in dash and 'provee=r["id"] in _validos' in dash)
    _check("el listado /api/sucursales expone la configuración provee guardada",
           "return ok([dict(r, provee=bool(r.get(\"provee\", 0))) for r in rows])" in suc)
    # La lista de destinos válidos la arma core/lotes.py (destinos_validos) para
    # que la usen el pedido y el catálogo por igual.
    m = re.search(r"destinos_validos\(conn\)", py)
    _check("el backend sigue validando con la misma lista de destinos",
           m is not None and "destinos_validos" in py)
    # Para poder arreglar los datos de un vistazo: el listado de sucursales dice
    # cuantas tiene sin marcar como proveedora (esos productos no se pueden
    # pedir y antes el error no decia cuales eran).
    js = _leer(os.path.join("static", "app.js"))
    _check("el listado de sucursales cuenta sus productos",
           "num_productos" in suc and "AS num_productos" in suc)
    _check("avisa cuando tiene productos pero no provee",
           "num_productos" in js and "¿Provee a otras?" in js
           and "!s.es_as" in js and "s.provee || s.es_as" in js)


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


def _lotes_ns():
    """Namespace con las funciones puras de lotes.py, para probarlas de verdad."""
    src = _leer(os.path.join("core", "lotes.py"))
    ns = {}
    for nombre in ("stock_en_destino", "elegir_destino"):
        exec(compile(_py_def(src, nombre), "lotes.py", "exec"), ns)
    return ns


def prueba_el_destino_no_se_elige_por_stock():
    """TENER STOCK NO DA PERMISO PARA REPARTIR.

    Una sucursal puede tener mercaderia para su propia venta y que otro
    encargado se la lleve en un pedido, dejandola sin nada. Por eso el destino
    NO puede ser "la sucursal que tiene stock": es la sucursal que tiene el
    producto en el catalogo, que es quien lo reparte.

    Caso real: las tapas las reparte el Almacen 1, pero estan escritas en el
    catalogo de America y America tiene stock de tapas porque las compro para
    venderlas. El pedido tiene que ir al Almacen 1, no a America.
    """
    ns = _lotes_ns()
    elegir = ns["elegir_destino"]
    # principal=1, America=5 (provee), Simon=7 (provee)
    validos = [1, 5, 7]
    # America tiene 10 tapas, el Almacen 1 y Simon no tienen nada.
    cand_america = [(5, 10.0, "America")]
    _check("el destino es la sucursal que reparte, NO la que tiene stock",
           elegir(cand_america, validos, 1) == (1, 0.0),
           "las tapas las reparte el Almacen 1; que America tenga 10 de venta "
           "propia no significa que pueda despachar")

    # Y al reves: el producto es de Simon (que reparte) y el stock esta en
    # America. Va a Simon, no a America.
    cand_simon = [(5, 50.0, "America")]
    _check("tambien al reves: producto de Simon no se manda a America",
           elegir(cand_simon, validos, 7) == (7, 0.0),
           "el stock de America es de su propia venta")

    # Si la sucursal que reparte SI tiene stock, se usa (no se toca lo que ya
    # funcionaba).
    cand_ok = [(7, 8.0, "Simon Lopez")]
    _check("si quien reparte tiene stock, va ahi y se ve cuanto",
           elegir(cand_ok, validos, 7) == (7, 8.0))

    # Si el que reparte y el que tiene stock son el mismo, da igual el orden en
    # que vengan los candidatos.
    _check("si coinciden el responsable y el que tiene stock, no cambia",
           elegir([(5, 10.0, "America"), (7, 99.0, "Simon Lopez")], validos, 5)
           == (5, 10.0))

    # Producto global (sucursal_id NULL) o en una sucursal que no despacha:
    # va al principal.
    _check("producto global o en sucursal que no reparte: al principal",
           elegir([], validos, None) == (1, 0.0)
           and elegir([], validos, 9) == (1, 0.0),
           "si no hay quien lo reparta de verdad, responde el principal")

    # Si no hay ningun destino valido, no inventa uno.
    _check("sin ninguna sucursal que pueda repartir, devuelve None",
           elegir([(5, 10.0, "America")], [], 5) == (None, 0.0))


def prueba_el_destino_no_se_elige_por_stock_en_el_codigo():
    """Las mismas reglas, pero fijadas en el fuente, para que no se rompan."""
    lotes = _leer(os.path.join("core", "lotes.py"))
    py = _leer(os.path.join("core", "pedidos.py"))
    prod = _leer(os.path.join("core", "productos.py"))
    js = _leer(os.path.join("static", "app.js"))

    _check("existe stock_por_destino (una consulta para todos los productos)",
           "def stock_por_destino" in lotes)
    _check("stock de destino reconoce principales, proveedores, AS y receptores",
           "s.principal = 1 OR IFNULL(s.provee, 0) = 1 OR IFNULL(s.es_as, 0) = 1" in lotes
           and "u.sucursal_id = s.id AND IFNULL(u.receptor, 0) = 1" in lotes
           and "a.sucursal_id = s.id AND a.nombre LIKE 'AS %'" in lotes)
    _check("existe destinos_validos con el principal primero",
           "def destinos_validos" in lotes and "ORDER BY principal DESC, nombre" in lotes)
    _check("elegir_destino devuelve la sucursal del catalogo si puede atender",
           "sid in validos" in lotes)
    _check("elegir_destino NO devuelve 'el primero con stock'",
           "candidatos[0][0]" not in lotes,
           "esa regla vaciaba el inventario de una sucursal que no reparte")
    _check("stock_por_destino dice que es solo para avisar, no para elegir",
           "NO para" in lotes and "elegir destino" in lotes)

    # El backend tiene que usar la regla, y las ramas inválidas se siguen
    # acumulando como antes.
    _check("el pedido resuelve el destino con elegir_destino",
           "elegir_destino(" in py and "candidatos.get(prod_id)" in py)
    _check("calcula los candidatos una sola vez, no por linea",
           py.count("stock_por_destino(conn)") == 1)
    _check("si el producto no tiene quien lo reparta, el error lo dice",
           "no tiene una sucursal que pueda" in py)

    # Pantalla y servidor eligen lo mismo.
    _check("el catalogo de pedidos usa la misma regla",
           "para_pedido" in prod and "stock_por_destino(conn)" in prod
           and "destinos_validos(conn)" in prod)
    _check("el catalogo manda el destino resuelto y el disponible de ahi",
           "destino_id" in prod and "destino_nombre" in prod and "destino_stock" in prod)
    _check("el disponible se mide contra el destino resuelto",
           "reservas.get((int(r[\"id\"]), sid_dest)" in prod
           and "stock_dest - reservas.get" in prod)
    _check("lo apartado en pedidos pendientes se descuenta del destino real",
           "COALESCE(d.destino_id, pd.destino_id)" in prod
           and "pd.estado IN ('pendiente', 'en_camino')" in prod)
    _check("las demas pantallas NO cambian de comportamiento",
           re.search(r"para_pedido = request\.args\.get", prod) is not None)

    # El frontend sigue mostrando por el destino que manda el servidor.
    _check("el frontend agrupa por el destino resuelto",
           "function provDeProducto" in js and js.count("provDeProducto(p)") >= 3)
    _check("el frontend pide para_pedido=1",
           "para_pedido: '1'" in js)

    # Y el aviso de "no hay stock" es aviso, NO bloqueo.
    _check("avisa cuando el destino responsable no tiene stock",
           "sin_stock" in py and "stock_en_destino(candidatos.get(prod_id), proveedor) <= 0" in py)
    _check("el aviso NO bloquea el pedido (se guarda igual)",
           py.count("sin_stock = []") == 1
           and py.index("if sin_stock:") > py.index("conn.commit()"))
    _check("el aviso dice DONDE hay, para que la persona decida",
           "está en" in py)

    # La sucursal se habilita como proveedor explícitamente; la opción del
    # producto solo decide si ese artículo se ofrece.
    _check("destinos_validos incluye principales, proveedoras, AS y receptores",
           "WHERE principal = 1 OR IFNULL(provee, 0) = 1 OR IFNULL(es_as, 0) = 1" in lotes
           and "u.sucursal_id = sucursales.id AND IFNULL(u.receptor, 0) = 1" in lotes
           and "a.sucursal_id = sucursales.id AND a.nombre LIKE 'AS %'" in lotes
           and "EXISTS (SELECT 1 FROM productos p" not in lotes)
    _check("stock_por_destino usa la misma autorización",
           "s.principal = 1 OR IFNULL(s.provee, 0) = 1 OR IFNULL(s.es_as, 0) = 1" in lotes
           and "u.sucursal_id = s.id AND IFNULL(u.receptor, 0) = 1" in lotes
           and "a.sucursal_id = s.id AND a.nombre LIKE 'AS %'" in lotes
           and "EXISTS (SELECT 1 FROM productos p" not in lotes)

    # Auto-pedido: una sucursal puede pedirse a si misma (su propio almacén
    # principal, p. ej. America pide su llajua). Ni el backend ni el front la
    # ocultan.
    _check("el backend ya NO rechaza pedirte a ti mismo",
           "no puede pedirse a ti mismo" not in py)
    _check("el frontend ya NO esconde los productos del propio AS",
           "provId === sid" not in js)
    _check("el selector de destino incluye principal, provee y AS",
           re.search(r"const pueden = sucursales\.filter\(\(x\) => \(x\.principal \|\| x\.provee \|\| x\.es_as\)\);", js) is not None)


def prueba_sin_stock_no_bloquea():
    """El faltante del proveedor se AVISA, no se convierte en "agotado".

    Antes, si al proveedor (quien reparte el producto) no le quedaba stock, la
    pantalla deshabilitaba el producto y el pedido no se podia hacer: el faltante
    de stock se tapaba con un bloqueo. Ahora el pedido se manda igual a quien le
    corresponde, y queda marcado para que el almacen consiga la mercaderia.
    """
    prod = _leer(os.path.join("core", "productos.py"))
    py = _leer(os.path.join("core", "pedidos.py"))
    db = _leer(os.path.join("database.py"))
    js = _leer(os.path.join("static", "app.js"))
    tpl = _leer(os.path.join("templates", "ticket_pedido.html"))

    # Backend: el catalogo marca la linea y el pedido la guarda.
    _check("el catalogo marca sin_stock cuando el destino no tiene",
           'r["sin_stock"] = False' in prod
           and 'r["sin_stock"] = stock_dest <= 0' in prod)
    _check("el pedido guarda la marca en la linea (no solo en el mensaje)",
           "tacho_unidad, sin_stock in items" in py
           and "sin_stock)" in py,
           "si no se persiste, el almacen descubre el faltante al separar")
    _check("la columna sin_stock existe al crear la tabla",
           "sin_stock TINYINT NOT NULL DEFAULT 0" in db)
    _check("se agrega la columna a las bases ya creadas (migracion aditiva)",
           '_add_columna(cur, "pedido_detalle", "sin_stock TINYINT NOT NULL DEFAULT 0")' in db)
    _check("el ticket tambien avisa del faltante",
           '"sin_stock": d.get("sin_stock")' in py and "item.sin_stock" in tpl)

    # Frontend: NO hay bloqueo; el faltante del proveedor tiene su propio aviso.
    _check("el frontend distingue 'proveedor sin stock' de 'agotado'",
           "function sinStockProveedor" in js
           and "const agotado = max <= 0 && !falta;" in js)
    _check("pedir de mas no se bloquea si el proveedor no tiene stock",
           "function excedeDisponible" in js
           and "if (sinStockProveedor(p)) return false;" in js)
    _check("la revision y el detalle muestran el faltante",
           "sinStockProveedor(it.p)" in js and "d.sin_stock" in js)


def prueba_control_oferta_productos():
    prod = _leer(os.path.join("core", "productos.py"))
    js = _leer(os.path.join("static", "app.js"))
    tpl = _leer(os.path.join("templates", "index.html"))
    ns = {}
    exec(_py_def(prod, "_para_proveer"), ns)
    exec(_py_def(prod, "_sucursal_puede_proveer"), ns)
    _check("la opción de ofrecer producto respeta Sí/No",
           ns["_para_proveer"]({"para_proveer": True}) == 1
           and ns["_para_proveer"]({"para_proveer": False}) == 0
           and ns["_para_proveer"]({}) == 0)
    _check("una sucursal principal, proveedora o AS puede ofrecer un producto",
           "_sucursal_puede_proveer" in prod
           and "SELECT s.principal, s.provee, s.es_as" in prod
           and "sucursal.get(\"es_as\")" in prod
           and "sucursal.get(\"receptor\")" in prod
           and "sucursal.get(\"almacen_as\")" in prod
           and "if para_proveer and not _sucursal_puede_proveer(conn, sid)" in prod)
    _check("el catálogo de pedidos incluye productos de almacenes AS",
           "IFNULL(p.para_proveer, 1) = 1" in prod
           and "p.sucursal_id IS NOT NULL" in prod
           and "sp.principal = 1 OR sp.provee = 1 OR sp.es_as = 1" in prod
           and "u.sucursal_id = sp.id AND IFNULL(u.receptor, 0) = 1" in prod
           and "a.sucursal_id = sp.id AND a.nombre LIKE 'AS %'" in prod)
    _check("el formulario guarda la opción al crear y editar productos",
           'id="prod-para-proveer"' in tpl
           and "para_proveer: $('#prod-para-proveer').checked" in js
           and "actualizarControlProductoPedido" in js
           and "function sucursalPermiteOfrecerProductos" in js
           and "String(a.sucursal_id) === String(sucursalId)" in js
           and "/^AS\\s/i.test" in js
           and "$('#prod-almacen').addEventListener('change'" in js
           and "!!sucursal.es_as" in js
           and "async function sucursalPermiteOfrecerProductos" not in js)
    _check("desactivar la opción conserva el producto en inventario",
           "Desactivarlo no cambia el inventario" in tpl
           and "UPDATE productos SET codigo" in prod)
    _check("los productos importados no se ofrecen por defecto",
           "sucursal_id, para_proveer, activo)" in prod
           and "NULL, ?, ?, 0, 1)" in prod)

    class Resultado:
        def __init__(self, fila):
            self.fila = fila

        def fetchone(self):
            return self.fila

    class Conexion:
        def __init__(self, fila):
            self.fila = fila

        def execute(self, _query, _params):
            return Resultado(self.fila)

    puede = ns.get("_sucursal_puede_proveer")
    _check("un almacén AS puede habilitar productos para pedidos",
           callable(puede) and puede(Conexion({"principal": 0, "provee": 0, "es_as": 1}), 5))
    _check("una sucursal con usuario receptor puede ofrecer productos",
           callable(puede) and puede(Conexion({
               "principal": 0, "provee": 0, "es_as": 0, "receptor": 1}), 5))
    _check("una sucursal con almacén AS puede ofrecer productos",
           callable(puede) and puede(Conexion({
               "principal": 0, "provee": 0, "es_as": 0, "receptor": 0, "almacen_as": 1}), 5))
    if callable(puede):
        ns["session"] = {"receptor": True}
        ns["sucursal_actual"] = lambda: 5
        receptor_por_sesion = puede(
            Conexion({"principal": 0, "provee": 0, "es_as": 0, "receptor": 0}), 5)
        ns["session"] = {"receptor": False}
    else:
        receptor_por_sesion = False
    _check("la sesión receptora propia permite ofrecer aunque falte la marca del catálogo",
           receptor_por_sesion
           and "window.RECEPTOR && String(sucursalId) === String(window.SUCURSAL_ID)" in js)
    _check("una sucursal normal aún requiere activar provee",
           callable(puede)
           and not puede(Conexion({
               "principal": 0, "provee": 0, "es_as": 0, "receptor": 0}), 5)
           and puede(Conexion({
               "principal": 0, "provee": 1, "es_as": 0, "receptor": 0}), 5))

    db = _leer("database.py")
    _check("el arranque ya no fusiona ni elimina sucursales AS",
           "_destruir_sucursales_as" not in db)


def main():
    print("=" * 72)
    print("ORDEN DE LOS PRODUCTOS EN LOS PEDIDOS")
    print("=" * 72)
    prueba_detalle_ordenado()
    prueba_ticket_conserva_orden()
    prueba_bandeja_ordenada()
    prueba_catalogo_provee_coincide_con_backend()
    prueba_el_destino_no_se_elige_por_stock()
    prueba_el_destino_no_se_elige_por_stock_en_el_codigo()
    prueba_sin_stock_no_bloquea()
    prueba_control_oferta_productos()
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
