"""Aislamiento por sucursal del INVENTARIO DIARIO.

Cada sucursal es una caja cerrada: el encargado de America no puede abrir, ver,
contar ni cerrar la planilla de Siglo XX. Con dos encargados por sucursal esto
importa doble, porque los dos comparten la misma planilla y un descuido del uno
contamina el conteo del otro.

Se prueba de dos formas complementarias:

1. FUNCIONAL: se levanta el blueprint con una conexion falsa y se intenta, de
   verdad, abrir la planilla de otra sucursal pasandole el id en la URL. La
   sucursal sale de la SESION, nunca del request, asi que el intento tiene que
   ignorarse. Esto es lo que evita que un encargado de America abra la de Siglo XX.
2. ESTATICA: se comprueba que cada endpoint que ESCRIBE planillas tenga el
   candado `inv["sucursal_id"] != sucursal_operativa()`, y que el listado filtre
   por sucursal para quien no es gestion. Si alguien saca un candado, el test falla.
"""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

from flask import Flask

import core.inventario as invmod
import core.util as utilmod

FALLOS = []
CAPTURADO = []
EXISTENTE = {"fila": None}


class Fila(dict):
    """Dict que devuelve None para claves desconocidas en vez de Romper."""

    def __missing__(self, k):
        return None


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    @property
    def lastrowid(self):
        return self.conn.next_id

    @property
    def rowcount(self):
        return 1

    def execute(self, sql, params=None):
        self.conn.ultimo = " ".join(str(sql).split())
        self.conn.ultimos_params = list(params or [])
        CAPTURADO.append((self.conn.ultimo, self.conn.ultimos_params))
        return self

    def fetchone(self):
        sql, params = self.conn.ultimo, self.conn.ultimos_params
        if "FROM categorias" in sql:
            return Fila(nombre="Pollo")
        if "SELECT principal, nombre FROM sucursales" in sql:
            # America no es almacen principal: es una filial.
            return Fila(principal=0, nombre="America")
        if "categoria_id > 0" in sql or "categoria_id = 0" in sql:
            # La planilla "opuesta": no existe.
            return None
        if sql.startswith("SELECT * FROM inventario_diario WHERE sucursal_id"):
            return EXISTENTE["fila"]
        if "SELECT estado FROM inventario_diario" in sql:
            return Fila(estado="abierto")
        return Fila()

    def fetchall(self):
        return []

    def __getattr__(self, _):
        return lambda *a, **k: None


class FakeConn:
    def __init__(self):
        self.ultimo = ""
        self.ultimos_params = []
        self.commits = 0
        self.next_id = 0

    def cursor(self):
        return FakeCursor(self)

    def execute(self, sql, params=None):
        cur = FakeCursor(self)
        cur.execute(sql, params)
        if str(sql).strip().upper().startswith("INSERT"):
            self.next_id += 1
        return cur

    def fetchone(self):
        return FakeCursor(self).fetchone()

    def fetchall(self):
        return []

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    def close(self):
        pass


def _servidor():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test"
    app.register_blueprint(invmod.inventario_bp)
    invmod.get_conn = lambda: FakeConn()
    # La auditoria abre su PROPIA conexion. Sin esto el test revienta con un
    # error de credenciales de MySQL y el fallo real queda escondido.
    utilmod.get_conn = lambda: FakeConn()
    utilmod.registrar_auditoria = lambda *a, **k: None
    return app


def _cliente(rol, sucursal_id, usuario):
    app = _servidor()
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = f"u-{usuario}"
        s["usuario"] = usuario
        s["rol"] = rol
        s["sucursal_id"] = sucursal_id
    return c


def _inserts_de_inventario_diario():
    return [(sql, p) for sql, p in CAPTURADO if sql.startswith("INSERT INTO inventario_diario")]


def _check(nombre, ok, detalle=""):
    print(f"{'OK  ' if ok else 'FALLO'}  {nombre}")
    if detalle:
        print(f"        {detalle}")
    if not ok:
        FALLOS.append(nombre)


def prueba_no_puede_abrir_otra_sucursal():
    """El caso concreto: encargado de America intentando abrir la de Siglo XX."""
    CAPTURADO.clear()
    EXISTENTE["fila"] = None
    SIGLO_XX = 41  # id que el cliente intenta colar por la URL
    c = _cliente("encargado", 35, "encargado_america")

    r = c.post(f"/api/inventario-diario?fecha={date.today().isoformat()}&sucursal_id={SIGLO_XX}")
    body = r.get_json() or {}

    inserts = _inserts_de_inventario_diario()
    _check("encargado de America NO abre la planilla de Siglo XX",
           len(inserts) == 1 and inserts[0][1][0] == 35,
           f"la sucursal guardada fue {inserts[0][1][0] if inserts else '?'} "
           f"(America=35, el que pidio por URL era {SIGLO_XX})")
    _check("la respuesta dice que es de America",
           body.get("ok") and body.get("data", {}).get("sucursal_id") == 35,
           f"sucursal_id devuelto: {body.get('data', {}).get('sucursal_id')}")

    ninguna = [p for sql, p in CAPTURADO
               if "sucursal_id = %s AND fecha = %s AND categoria_id" in sql and SIGLO_XX in p]
    _check("el id de Siglo XX nunca se usa para filtrar", not ninguna,
           f"consultas que usaron {SIGLO_XX}: {len(ninguna)}")


def prueba_mensaje_dice_quien_abrio():
    """El segundo encargado tiene que saber que la planilla ya existe y quien la abrio."""
    CAPTURADO.clear()
    EXISTENTE["fila"] = Fila(id=7, estado="abierto", fecha=date.today().isoformat(),
                             usuario="encargado_america", hora_corte="07:12",
                             sucursal_id=35, categoria_id=0)
    c = _cliente("encargado", 35, "encargado_siglo")

    r = c.post(f"/api/inventario-diario?fecha={date.today().isoformat()}")
    body = r.get_json() or {}
    msg = body.get("message", "")

    _check("avisa que la planilla ya esta abierta", "ya está abierta" in msg, msg)
    _check("dice QUIEN la abrio (encargado_america)", "encargado_america" in msg, msg)
    _check("dice a que hora la abrio", "07:12" in msg, msg)
    _check("devuelve la MISMA planilla, no crea otra",
           body.get("data", {}).get("id") == 7 and not _inserts_de_inventario_diario(),
           f"id devuelto: {body.get('data', {}).get('id')}")
    _check("le dice que lo que ya cargaste queda como esta", "queda como está" in msg, msg)

    # Y el que la abrio se la reconoce como suya.
    EXISTENTE["fila"] = Fila(id=7, estado="abierto", fecha=date.today().isoformat(),
                             usuario="encargado_america", hora_corte="07:12",
                             sucursal_id=35, categoria_id=0)
    c2 = _cliente("encargado", 35, "encargado_america")
    msg2 = (c2.post(f"/api/inventario-diario?fecha={date.today().isoformat()}").get_json() or {}).get("message", "")
    _check("el que la abrio se la reconoce como propia", "la abriste vos" in msg2, msg2)


def prueba_se_sabe_quien_conto():
    """Los dos encargados cuentan sobre la misma planilla: tiene que quedar
    registrado quien conto cada linea, y avisar SIN bloquear al otro.

    Requisito del usuario: que nadie salga perjudicado. Por eso el caso de
    'el otro ya conto esto' se AVISA, no se rechaza: el segundo encargado a
    veces esta corrigiendo un numero que el primero cargo mal, y si se le
    bloquea el guardado pierde el trabajo de toda la planilla.
    """
    raiz = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(raiz, "database.py"), encoding="utf-8") as fh:
        db = fh.read()
    with open(os.path.join(raiz, "core", "inventario.py"), encoding="utf-8") as fh:
        inv = fh.read()

    _check("la base tiene la columna contado_por", "contado_por" in db)
    # Nullable: las planillas ya contadas quedan en NULL y no dan avisos falsos.
    _check("contado_por es nullable (no rompe planillas ya contadas)",
           "contado_por VARCHAR(255)" in db and "NOT NULL" not in
           db.split("contado_por VARCHAR(255)")[0].rsplit("_add_columna", 1)[-1])
    _check("solo se crea la columna si no existe todavia",
           'if not _col_existe(cur, "inventario_detalle", "contado_por"):' in db)

    m = re.search(r"def inventario_guardar\(.*?\n(?=@|\Z)", inv, re.S)
    guardar = m.group(0) if m else ""
    _check("inventario_guardar guarda QUIEN conto", "contado_por = %s" in guardar)
    _check("inventario_guardar compara contra el usuario de la sesion",
           'session.get("usuario"' in guardar)
    _check("detecta cuando la linea ya la conto OTRO encargado",
           "ya_contado_por_otro" in guardar)
    aviso = guardar.split("if ya_contado_por_otro:", 1)[-1].split("observaciones =", 1)[0]
    _check("AVISA y permite corregir una línea ya contada por el otro",
           "avisos.append" in aviso and "return err" not in aviso)
    _check("el aviso menciona al otro encargado y los dos numeros",
           'ya lo contó' in guardar and "fila['conteo_fisico']" in guardar)
    # No se pisa la firma previa cuando la linea se manda vacia para recounts.
    _check("borrar el numero para recountar NO borra el contado_por anterior",
           "contado_por = previo or None" in guardar)

    m = re.search(r"def inventario_detalle\(.*?\n(?=@|\Z)", inv, re.S)
    detalle = m.group(0) if m else ""
    _check("el detalle expone counted_por para la pantalla",
           '"contado_por"' in detalle)
    _check("el detalle marca si la linea es de otro encargado",
           '"es_de_otro"' in detalle)
    _check("el detalle devuelve el último aporte manual guardado",
           "ultimo_ingreso_manual" in detalle
           and "FROM inventario_ingresos_manuales" in detalle
           and "ORDER BY id" in detalle)

    # Respaldo: si la migracion no corrio y la columna no existe, el conteo se
    # tiene que guardar igual. Perder el conteo de toda la planilla porque falte
    # una columna de autoria seria JUSTO lo que hay que evitar.
    _check("si la columna no esta, guarda igual sin la firma",
           "if con_firma:" in guardar and guardar.count("UPDATE inventario_detalle") >= 2)
    _check("detecta la columna desde las propias filas, sin consulta extra",
           'any("contado_por" in f for f in validas.values())' in guardar)
    # La rama sin firma no puede mencionar la columna: si la pusiéramos, el
    # UPDATE fallaría contra una base sin migrar.
    cola = guardar.split("if con_firma:", 1)[-1].split("else:", 1)[-1]
    cola = cola.split("except _DatoInvalido")[0]
    _check("el UPDATE sin firma NO menciona contado_por",
           "contado_por = %s" not in cola)


def prueba_guardado_parcial_compartido():
    """Un encargado no debe pisar campos que otro cargó desde una pantalla vieja."""
    raiz = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(raiz, "core", "inventario.py"), encoding="utf-8") as fh:
        inv = fh.read()
    with open(os.path.join(raiz, "static", "app.js"), encoding="utf-8") as fh:
        js = fh.read()

    m = re.search(r"def inventario_guardar\(.*?\n(?=@|\Z)", inv, re.S)
    guardar = m.group(0) if m else ""
    m = re.search(r"function collectedInventario\(\).*?\n\}", js, re.S)
    recopilar = m.group(0) if m else ""
    m = re.search(r"async function refrescarStockInicial\(.*?\n\}", js, re.S)
    refrescar = m.group(0) if m else ""
    m = re.search(r"async function abrirInventario\(.*?\n\}", js, re.S)
    abrir = m.group(0) if m else ""

    _check("el navegador manda solo las filas con cambios locales",
           "INV_CAMPOS_EDITADOS.entries()" in recopilar)
    _check("el navegador identifica cuáles celdas fueron editadas",
           "campos_modificados: Array.from(campos)" in recopilar
           and "version: 3" in js)
    _check("el servidor serializa guardados y cierre",
           "_sucursal_de_planilla(conn, inv_id, bloquear=True)" in guardar)
    _check("el servidor rechaza pantallas antiguas antes de escribir",
           "La pantalla del inventario está desactualizada" in guardar
           and "campos_modificados" in guardar)
    _check("el ingreso manual se registra como aporte con identificador idempotente",
           "inventario_ingresos_manuales" in guardar
           and "ingreso_manual_id" in guardar
           and "aporte_manual = 0.0" in guardar)
    _check("el servidor ignora las celdas que esta sesión no modificó",
           'if not campos:' in guardar and 'else fila["conteo_fisico"]' in guardar
           and 'if "ingreso_manual" in campos:' in guardar)
    _check("la sincronización respeta los campos editados y actualiza los demás",
           "INV_CAMPOS_EDITADOS.get(f.id) || new Set()" in refrescar
           and "editados.has('conteo_fisico')" in refrescar
           and "editados.has('ingreso_manual')" in refrescar
           and "editados.has('observaciones')" in refrescar
           and "if (INV_CAMPOS_EDITADOS.has(f.id)) return" not in refrescar)
    _check("la sincronización no añade mensajes de autor al conteo",
           "f.contado_por" not in refrescar and "inv-count-author" not in refrescar)
    _check("el inicial es solo lectura y no se ofrece como campo editable",
           'class="inv-inicial"' in abrir
           and 'value="${esc(fmtInvQ(f.inicial))}" readonly ${bloq}' in abrir
           and ".inv-conteo, .inv-ingreso-man" in abrir
           and ".inv-inicial" not in recopilar)
    _check("el ingreso manual muestra el último aporte en el mismo campo",
           'value="${f.ultimo_ingreso_manual == null ? \'\' : esc(fmtInvQ(f.ultimo_ingreso_manual))}" placeholder="Cantidad a agregar"' in abrir
           and "ingresoIn.value = f.ultimo_ingreso_manual == null" in refrescar
           and "camposEditados.has('ingreso_manual')" in js
           and "Acumulado compartido:" not in abrir
           and "Conteo físico:" not in abrir
           and "contado_por" not in abrir
           and "data-inv-ingresado" not in abrir
           and "ingreso_manual_id: INV_INGRESO_IDS.get(id)" in recopilar)
    _check("la planilla comunica que muestra los datos compartidos al abrir",
           "al abrirla aparecen los datos guardados" in abrir)
    _check("la otra sesión sincroniza cambios en un máximo de 10 segundos",
           "}, 10000);" in js)
    _check("Actualizar stock llama al refresco inmediato de la planilla",
           "$('#btn-inv-refrescar')?.addEventListener('click', invClick(() => refrescarStockInicial(false)))"
           in js)
    with open(os.path.join(raiz, "templates", "index.html"), encoding="utf-8") as fh:
        html = fh.read()
    _check("la planilla explica editar el stock del producto y actualizar",
           "edita el producto de esta sucursal en <strong>Productos</strong>" in html
           and "pulsa <strong>Actualizar stock</strong>" in html)
    with open(os.path.join(raiz, "core", "productos.py"), encoding="utf-8") as fh:
        productos = fh.read()
    producto_put = re.search(
        r"def producto\(.*?\n(?=@|\Z)", productos, re.S)
    producto_put = producto_put.group(0) if producto_put else ""
    _check("editar stock en Productos queda limitado al ámbito del encargado",
           "ids_sucursal_consolidada(conn, sid_user)" in producto_put
           and 'int(fila["sucursal_id"] or 0) not in cons' in producto_put
           and 'if (id) body.stock' in js
           and '"Ajuste de inventario"' in producto_put)

    original_get_conn = invmod.get_conn
    original_registrar_auditoria = utilmod.registrar_auditoria
    estado_inv = Fila(id=7, sucursal_id=35, estado="abierto",
                      fecha=date.today().isoformat(), hora_corte="08:00",
                      observaciones=None)
    estado_linea = Fila(
        id=11, producto_id=3, producto_nombre="Pollo", categoria_nombre="Aves",
        codigo="P1", unidad="kg", stock_sistema=10, inicial=10, ingreso_dia=0,
        ingreso_manual=0, disponible=10, conteo_fisico=None, final=10,
        utilizada=0, diferencia=0, costo_promedio=5, precio_venta=8,
        observaciones=None, contado_por=None,
    )
    otra_linea = Fila(
        id=12, producto_id=4, producto_nombre="Papas", categoria_nombre="Verduras",
        codigo="P2", unidad="kg", stock_sistema=6, inicial=6, ingreso_dia=0,
        ingreso_manual=0, disponible=6, conteo_fisico=None, final=6,
        utilizada=0, diferencia=0, costo_promedio=2, precio_venta=4,
        observaciones=None, contado_por=None,
    )
    estado_lineas = [estado_linea, otra_linea]
    sqls = []
    entradas_manuales = {}

    class Cursor:
        def __init__(self, one=None, rows=None):
            self.one = one
            self.rows = rows or []
            self.rowcount = 1

        def fetchone(self):
            return self.one

        def fetchall(self):
            return self.rows

    class SharedConn:
        def execute(self, sql, params=None):
            s = " ".join(str(sql).split())
            p = list(params or [])
            sqls.append(s)
            if s.startswith("SELECT * FROM inventario_diario WHERE id"):
                return Cursor(one=estado_inv)
            if "SELECT principal, nombre FROM sucursales" in s:
                return Cursor(one=Fila(principal=0, nombre="America"))
            if "FROM inventario_detalle d" in s:
                return Cursor(rows=[Fila(**fila) for fila in estado_lineas])
            if "FROM lotes WHERE" in s:
                return Cursor(rows=[Fila(producto_id=3, c=10),
                                    Fila(producto_id=4, c=6)])
            if "FROM inventario_ingresos_manuales WHERE request_id" in s:
                entrada = entradas_manuales.get(p[0])
                return Cursor(one=Fila(**entrada) if entrada else None)
            if "AS i FROM movimientos" in s or "AS s FROM movimientos" in s:
                return Cursor(rows=[])
            if s.startswith("INSERT INTO inventario_ingresos_manuales"):
                detalle_id, request_id, usuario, cantidad, _ = p
                entradas_manuales[request_id] = {
                    "detalle_id": detalle_id, "usuario": usuario, "cantidad": cantidad,
                }
                return Cursor()
            if s.startswith("UPDATE inventario_detalle SET"):
                nombres = (
                    "inicial", "ingreso_dia", "ingreso_manual", "disponible",
                    "conteo_fisico", "final", "utilizada", "diferencia",
                    "stock_sistema", "observaciones", "contado_por",
                )
                fila = next(f for f in estado_lineas if f["id"] == p[-1])
                for nombre, valor in zip(nombres, p[:-1]):
                    fila[nombre] = valor
                return Cursor()
            if s.startswith("UPDATE inventario_diario SET"):
                estado_inv["observaciones"], estado_inv["hora_corte"], _ = p
                return Cursor()
            raise AssertionError(f"Consulta inesperada: {s}")

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    app = _servidor()
    invmod.get_conn = lambda: SharedConn()
    utilmod.registrar_auditoria = lambda *a, **k: None

    def guardar_como(usuario, lineas, version=3):
        cliente = app.test_client()
        with cliente.session_transaction() as sesion:
            sesion["user_id"] = f"u-{usuario}"
            sesion["usuario"] = usuario
            sesion["rol"] = "encargado"
            sesion["sucursal_id"] = 35
        return cliente.put("/api/inventario-diario/7",
                           json={"version": version, "lineas": lineas})

    try:
        primero = guardar_como("encargado_america", [{
            "id": 11, "campos_modificados": ["conteo_fisico", "inicial"],
            "conteo_fisico": "8", "ingreso_manual": "0",
            "inicial": "12", "observaciones": "",
        }])
        segundo_producto = guardar_como("encargado_america2", [{
            "id": 12, "campos_modificados": ["conteo_fisico"],
            "conteo_fisico": "5", "ingreso_manual": None,
            "inicial": "8", "observaciones": "",
        }])
        segundo = guardar_como("encargado_america2", [{
            "id": 11, "campos_modificados": ["observaciones"],
            "conteo_fisico": None, "ingreso_manual": "0",
            "inicial": "10", "observaciones": "Revisado por el segundo encargado",
        }])
        conteo_despues_segundo = estado_linea["conteo_fisico"]
        autor_despues_segundo = estado_linea["contado_por"]
        tercero = guardar_como("encargado_america2", [{
            "id": 11, "campos_modificados": ["conteo_fisico"],
            "conteo_fisico": "7", "ingreso_manual": "0",
            "inicial": "10", "observaciones": "",
        }])
        entrada_uno = guardar_como("encargado_america", [{
            "id": 12, "campos_modificados": ["ingreso_manual"],
            "ingreso_manual": "3", "ingreso_manual_id": "entry-uno",
        }])
        entrada_dos = guardar_como("encargado_america2", [{
            "id": 12, "campos_modificados": ["ingreso_manual"],
            "ingreso_manual": "2", "ingreso_manual_id": "entry-dos",
        }])
        reintento_entrada_dos = guardar_como("encargado_america2", [{
            "id": 12, "campos_modificados": ["ingreso_manual"],
            "ingreso_manual": "2", "ingreso_manual_id": "entry-dos",
        }])
        writes_antes = sum(sql.startswith("UPDATE inventario_detalle SET") for sql in sqls)
        desactualizado = guardar_como("encargado_america", [{
            "id": 11, "conteo_fisico": None, "ingreso_manual": "0",
            "inicial": "10", "observaciones": "",
        }], version=2)
        linea_incompleta = guardar_como("encargado_america", [{
            "id": 11, "conteo_fisico": None,
        }])
        writes_despues = sum(sql.startswith("UPDATE inventario_detalle SET") for sql in sqls)

        _check("el guardado del segundo conserva el conteo previo del primero",
               primero.get_json().get("ok") and segundo_producto.get_json().get("ok")
               and estado_linea["inicial"] == 10
               and otra_linea["inicial"] == 6
               and otra_linea["ingreso_manual"] == 5
               and otra_linea["disponible"] == 11
               and otra_linea["conteo_fisico"] == 5
               and segundo.get_json().get("ok")
               and conteo_despues_segundo == 8
               and autor_despues_segundo == "encargado_america"
               and estado_linea["observaciones"] == "Revisado por el segundo encargado",
               f"conteo tras segundo={conteo_despues_segundo}, "
               f"autor tras segundo={autor_despues_segundo}, "
               f"observaciones={estado_linea['observaciones']}, "
               f"iniciales=({estado_linea['inicial']}, {otra_linea['inicial']}), "
               f"otra_linea=({otra_linea['ingreso_manual']}, {otra_linea['disponible']}, "
               f"{otra_linea['conteo_fisico']}), "
               f"respuestas=({primero.status_code}, {segundo_producto.status_code}, "
               f"{segundo.status_code})")
        _check("si ambos corrigen la misma celda, se avisa quién había contado",
               bool(tercero.get_json().get("data", {}).get("avisos"))
               and "encargado_america" in tercero.get_json()["data"]["avisos"][0])
        _check("el ingreso manual suma las cantidades nuevas de ambos encargados",
               entrada_uno.get_json().get("ok") and entrada_dos.get_json().get("ok")
               and otra_linea["ingreso_manual"] == 5
               and otra_linea["disponible"] == 11,
               f"acumulado={otra_linea['ingreso_manual']}, "
               f"disponible={otra_linea['disponible']}")
        _check("reintentar el mismo guardado no duplica el ingreso manual",
               reintento_entrada_dos.get_json().get("ok")
               and otra_linea["ingreso_manual"] == 5
               and len(entradas_manuales) == 2,
               f"acumulado={otra_linea['ingreso_manual']}, "
               f"entradas={len(entradas_manuales)}")
        _check("el guardado bloquea la planilla mientras actualiza",
               any("FOR UPDATE" in sql for sql in sqls))
        _check("una pantalla vieja no puede borrar cambios ya guardados",
               desactualizado.status_code == 409
               and writes_despues == writes_antes
               and estado_linea["conteo_fisico"] == 7)
        _check("una línea malformada devuelve error claro sin 500",
               linea_incompleta.status_code == 400
               and "incompleta" in linea_incompleta.get_json().get("message", ""))
    finally:
        invmod.get_conn = original_get_conn
        utilmod.registrar_auditoria = original_registrar_auditoria


def prueba_sincronizacion_actualiza_inicial_sistema():
    """El inicial de solo lectura sigue el stock calculado por el sistema."""
    filas_productos = [
        Fila(id=3, categoria_id=1, categoria_nombre="Aves", nombre="Pollo",
             codigo="P1", unidad="kg", costo_promedio=5, precio_venta=8,
             stock=20, posterior=0),
        Fila(id=4, categoria_id=1, categoria_nombre="Aves", nombre="Pavo",
             codigo="P2", unidad="kg", costo_promedio=5, precio_venta=8,
             stock=20, posterior=0),
    ]
    detalles = [
        Fila(id=11, producto_id=3, inicial=12, stock_sistema=10,
             disponible=12, conteo_fisico=None, observaciones="Revisar entrega",
             categoria_nombre="Aves", producto_nombre="Pollo", codigo="P1",
             unidad="kg"),
        Fila(id=12, producto_id=4, inicial=10, stock_sistema=10,
             disponible=10, conteo_fisico=None, observaciones=None,
             categoria_nombre="Aves", producto_nombre="Pavo", codigo="P2",
             unidad="kg"),
    ]
    updates = []

    class SyncConn:
        def execute(self, sql, params=None):
            s = " ".join(str(sql).split())
            if s.startswith("SELECT estado FROM inventario_diario"):
                return CierreCursor([Fila(estado="abierto")])
            if "FROM productos p" in s:
                return CierreCursor(filas_productos)
            if "FROM inventario_detalle d" in s:
                return CierreCursor(detalles)
            if s.startswith("UPDATE inventario_detalle SET"):
                updates.append((s, list(params or [])))
                return CierreCursor([])
            raise AssertionError(f"Consulta inesperada: {s}")

        def commit(self):
            pass

    cambios = invmod._sincronizar_detalle(SyncConn(), 7, 35, "2026-10-09", 0)
    update_ids = [params[-1] for _, params in updates]
    _check("la sincronización refresca el inicial aunque haya observaciones",
           cambios == 2 and 11 in update_ids and 12 in update_ids
           and next(p for _, p in updates if p[-1] == 11)[6] == 20,
           f"cambios={cambios}, updates={update_ids}")


def prueba_iniciar_planilla_no_falla_en_silencio():
    """'Iniciar inventario' no puede volver a no hacer NADA cuando hay error.

    Esto paso de verdad: el boton de confirmar no estaba envuelto en el mismo
    try/catch que los otros, asi que el mensaje del servidor se perdia y en
    pantalla no pasaba absolutely nada. Solo se veia en la consola.
    """
    raiz = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(raiz, "static", "app.js"), encoding="utf-8") as fh:
        js = fh.read()

    m = re.search(r"async function confirmarCrearInventario\(.*?\n(?=\}\S|\n\S)", js, re.S)
    cuerpo = m.group(0) if m else ""
    _check("confirmarCrearInventario atrapa el error del servidor",
           "catch (e)" in cuerpo and "toast(" in cuerpo)
    _check("el error se muestra, no se traga", "No se pudo iniciar el inventario" in cuerpo)
    _check("si falla, refresca la lista para poder abrir la que ya existe",
           "listarInventario()" in cuerpo)

    # Y el toast tiene que aguantar un mensaje largo: el aviso de planillas que
    # coexisten no entra en 3 segundos.
    mt = re.search(r"function toast\([^)]*\)", js)
    _check("toast acepta duracion", mt and "ms" in mt.group(0), mt.group(0) if mt else "")
    _check("el aviso de coexistencia dura mas de 3s", "15000" in cuerpo)


def prueba_se_pueden_abrir_las_dos_tipos_de_planilla():
    """El usuario decidiu que 'todas las categorias' y 'por categoria' pueden
    coexistir el mismo dia. No se bloquea, pero tampoco se avisa en silencio."""
    raiz = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(raiz, "core", "inventario.py"), encoding="utf-8") as fh:
        inv = fh.read()

    m = re.search(r"def inventario_crear\(.*?\n(?=@|\Z)", inv, re.S)
    crear = m.group(0) if m else ""
    _check("ya NO bloquea la mezcla de planillas",
           "No se puede mezclar" not in crear
           and "Podés" not in crear.split("avisos_planilla")[0])
    _check("pero avisa cuales planillas van a coexistir",
           "avisos_planilla" in crear and "contados dos veces" in crear)
    _check("el aviso viaja en la respuesta de la planilla creada",
           '"avisos": avisos_planilla' in crear)
    _check("no devuelve 409 al mezclar", "409" not in crear)


def prueba_columnas_inventario_diario():
    """`inventario_diario` NO tiene columna `categoria_nombre` (esa vive en
    inventario_detalle, junto al producto).

    Esto produjo un 500 en produccion: un SELECT pedia `categoria_nombre` a
    `inventario_diario`, MySQL respondio 1054 y 'Iniciar inventario' fallo.
    El arreglo fue traer el nombre con un JOIN a categorias.

    Chequeo acotado y a proposito: si un SELECT sin alias pide esa columna, el
    endpoint va a fallar en MySQL aunque compile y pase todo lo otro.
    """
    raiz = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(raiz, "core", "inventario.py"), encoding="utf-8") as fh:
        inv = fh.read()

    malos = []
    for m in re.finditer(r"SELECT\s+(.{0,200}?)\s+FROM\s+inventario_diario(?![\s\S]{0,120}JOIN)",
                         inv, re.S | re.I):
        frag = m.group(1)
        if re.search(r"\b[a-zA-Z]\s*\.", frag):
            continue                      # hay alias: no se puede verificar
        if "categoria_nombre" in frag:
            malos.append(" ".join(frag.split())[:70])
    _check("ningun SELECT sin alias pide categoria_nombre a inventario_diario",
           not malos, "; ".join(malos[:3]))

    # Y el nombre tiene que llegar por el JOIN, no por una columna inventada.
    m = re.search(r"def inventario_crear\(.*?\n(?=@|\Z)", inv, re.S)
    crear = m.group(0) if m else ""
    _check("el nombre de la categoría se trae con JOIN a categorias",
           "LEFT JOIN categorias" in crear and "AS categoria_nombre" in crear)


def prueba_candados_en_el_codigo():
    """Ningun endpoint que escribe planillas puede quedarse sin candado."""
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "core", "inventario.py"), encoding="utf-8").read()

    for nombre in ("inventario_guardar", "inventario_cerrar", "inventario_borrar"):
        m = re.search(rf"def {nombre}\(.*?\n(?=@|\Z)", src, re.S)
        cuerpo = m.group(0) if m else ""
        _check(f"{nombre} exige que la planilla sea de su sucursal",
               'inv["sucursal_id"] != sucursal_operativa()' in cuerpo)

    m = re.search(r"def inventario_crear\(.*?\n(?=@|\Z)", src, re.S)
    crear = m.group(0) if m else ""
    _check("inventario_crear saca la sucursal de la sesion",
           "sid = sucursal_operativa()" in crear)
    _check("inventario_crear NO acepta la sucursal por parametro",
           'request.args.get("sucursal_id")' not in crear)

    m = re.search(r"def inventario_lista\(.*?\n(?=@|\Z)", src, re.S)
    lista = m.group(0) if m else ""
    _check("el listado filtra por sucursal para quien no es gestion",
           "i.sucursal_id = %s" in lista and "es_gestion() or es_encargado_almacen" in lista)


class CierreCursor:
    def __init__(self, filas):
        self._filas = filas

    def fetchall(self):
        return self._filas

    def fetchone(self):
        return self._filas[0] if self._filas else None


class CierreConn:
    """Fake que responde solo a las tres consultas de _avisos_conteo_cruzado."""

    def __init__(self, otras=(), mios=(), ajenos=()):
        self.otras = list(otras)
        self.mios = list(mios)
        self.ajenos = list(ajenos)
        self.sqls = []

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split())
        self.sqls.append((s, list(params or [])))
        if "SELECT id FROM inventario_diario" in s:
            return CierreCursor([Fila(id=i) for i in self.otras])
        if "GROUP BY producto_id" in s:
            return CierreCursor(self.ajenos)
        if "conteo_fisico IS NOT NULL" in s:
            return CierreCursor(self.mios)
        return CierreCursor([])

    def rollback(self):
        pass

    def close(self):
        pass


def _linea(pid, nombre, conteo):
    return Fila(producto_id=pid, producto_nombre=nombre, conteo_fisico=conteo)


def prueba_dos_encargados_no_disparan_el_aviso():
    """Lo que preguntaste: dos encargados de la misma sucursal comparten planilla.

    Con una sola planilla abierta no hay nada que cruzar, asi que el aviso de
    doble conteo jamas aparece. El caso 'los dos contaron lo mismo' lo cubre el
    aviso de atribucion al guardar, que es otro codigo.
    """
    inv = Fila(id=7, sucursal_id=35, fecha="2026-10-03", categoria_id=0)
    conn = CierreConn(otras=[],                      # <- no hay otra planilla
                      mios=[_linea(1, "Pollo", 10)],
                      ajenos=[])
    # Antes era `_avisos_conteo_cruzado.__wrapped__(...)`: el `.__wrapped__`
    # estaba porque el decorador de la ruta habia quedado pegado a este auxiliar
    # por error. Con eso estos tests PASABAN mientras la ruta de cierre estaba
    # rota. Se llama directo: si un dia vuelve a estar decorated, revienta acá.
    avisos = invmod._avisos_conteo_cruzado(conn, inv)
    _check("dos encargados sobre la MISMA planilla no generan aviso",
           avisos == [], f"avisos={avisos}")

    # Y la consulta tiene que excluir la planilla que se esta cerrando, para que
    # contar de a dos sobre la misma nunca se confunda con planillas distintas.
    sql = conn.sqls[0][0]
    _check("la consulta excluye la planilla que se cierra (id <> %s)",
           "id <> %s" in sql)
    _check("la consulta filtra por la misma sucursal y el mismo dia",
           "sucursal_id = %s" in sql and "fecha = %s" in sql)


def prueba_avisa_si_ya_esta_contado_en_otra_planilla():
    inv = Fila(id=7, sucursal_id=35, fecha="2026-10-03", categoria_id=0)
    conn = CierreConn(otras=[9],
                      mios=[_linea(1, "Pollo entero", 10)],
                      ajenos=[Fila(producto_id=1, producto_nombre="Pollo entero",
                                   conteo_fisico=8)])
    avisos = invmod._avisos_conteo_cruzado(conn, inv)
    _check("avisa cuando el producto ya esta contado en otra planilla",
           len(avisos) == 1, f"avisos={avisos}")
    if avisos:
        a = avisos[0]
        _check("el aviso dice cuantos productos se adjusts dos veces",
               "1 producto(s)" in a, a[:80])
        _check("el aviso nombra el producto", "Pollo entero" in a)
        _check("el aviso muestra los DOS conteos", "10" in a and "8" in a)


def prueba_no_avisa_si_no_se_pisan_productos():
    inv = Fila(id=7, sucursal_id=35, fecha="2026-10-03", categoria_id=0)
    conn = CierreConn(otras=[9],
                      mios=[_linea(1, "Pollo", 10)],
                      ajenos=[Fila(producto_id=2, producto_nombre="Papas",
                                   conteo_fisico=5)])
    avisos = invmod._avisos_conteo_cruzado(conn, inv)
    _check("no avisa si las planillas no comparten productos",
           avisos == [], f"avisos={avisos}")


def prueba_el_cierre_avisa_pero_no_bloquea():
    """El aviso es informativo: el cierre se aplica igual."""
    raiz = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(raiz, "core", "inventario.py"), encoding="utf-8") as fh:
        inv = fh.read()
    m = re.search(r"def inventario_cerrar\(.*?\n(?=@|\Z)", inv, re.S)
    cerrar = m.group(0) if m else ""
    _check("el cierre calcula el aviso de conteo cruzado",
           "_avisos_conteo_cruzado" in cerrar)
    _check("el cierre NO devuelve 409 por conteo cruzado",
           "409" not in cerrar.split("_avisos_conteo_cruzado")[1][:1200]
           or "Planilla se cerró mientras" in cerrar)
    _check("el aviso viaja en la respuesta del cierre",
           '"avisos": avisos_cierre' in cerrar)


def prueba_el_aviso_del_cierre_se_ve_en_pantalla():
    raiz = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(raiz, "static", "app.js"), encoding="utf-8") as fh:
        js = fh.read()
    m = re.search(r"async function cerrarInventario\(\).*?\n\}", js, re.S)
    fn = m.group(0) if m else ""
    _check("cerrarInventario lee los avisos del cierre",
           "avisosCierre" in fn and "r.data.avisos" in fn)
    _check("el aviso del cierre dura mas de 3s",
           re.search(r"toast\(a,\s*'err',\s*20000\)", fn) is not None)


def prueba_cada_ruta_apunta_a_su_vista():
    """Que CADA ruta ejecute la vista que le corresponde, no un auxiliar.

    Cuando se agrego `_avisos_conteo_cruzado` quedo escrita ENTRE los
    decoradores (`@inventario_bp.route(...) /cerrar` y `@login_requerido`) y
    `def inventario_cerrar`. Flask atado `POST .../cerrar` a esa funcion
    auxiliar, que recibe `(conn, inv)`: la llamaba con `inv_id` y reventaba con
    TypeError, o sea 500 "Error interno del servidor" en TODOS los cierres.
    Y `inventario_cerrar` se quedaba sin ruta y sin candado de sesion: nadie
    podia cerrar ninguna planilla.

    Se comprueba sobre el blueprint ya registrado, que es donde se ve a quien
    quedo atada cada ruta de verdad.
    """
    app = _servidor()
    rutas = {}
    for regla in app.url_map.iter_rules():
        if not regla.rule.startswith("/api/inventario-diario"):
            continue
        vista = app.view_functions.get(regla.endpoint)
        for metodo in regla.methods - {"HEAD", "OPTIONS"}:
            rutas[f"{metodo} {regla.rule}"] = getattr(vista, "__name__", "?")
    cierre = rutas.get("POST /api/inventario-diario/<int:inv_id>/cerrar")
    _check("la ruta de cerrar ejecuta `inventario_cerrar`",
           cierre == "inventario_cerrar",
           f"esa ruta ejecuta `{cierre}`: por eso el cierre daba 500 y la "
           f"planilla nunca quedaba cerrada")
    _check("ninguna ruta del inventario queda atada a un auxiliar (nombre con _)",
           not [r for r, v in rutas.items() if v.startswith("_")],
           f"auxiliares expuestos: {[r for r, v in rutas.items() if v.startswith('_')]}")
    # Y que la vista de cerrar este donde el decorador de sesion la espera.
    fuente = _leer_inventario()
    m = re.search(r"@login_requerido\s*\ndef inventario_cerrar\(", fuente)
    _check("inventario_cerrar tiene @login_requerido pegado a su def",
           m is not None,
           "si el decorador se separa del def, la vista se queda sin candado")


def _leer_inventario():
    raiz = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(raiz, "core", "inventario.py"), encoding="utf-8") as fh:
        return fh.read()


def main():
    print("=" * 70)
    print("AISLAMIENTO POR SUCURSAL DEL INVENTARIO DIARIO")
    print("=" * 70)
    prueba_no_puede_abrir_otra_sucursal()
    prueba_mensaje_dice_quien_abrio()
    prueba_se_sabe_quien_conto()
    prueba_guardado_parcial_compartido()
    prueba_sincronizacion_actualiza_inicial_sistema()
    prueba_iniciar_planilla_no_falla_en_silencio()
    prueba_se_pueden_abrir_las_dos_tipos_de_planilla()
    prueba_columnas_inventario_diario()
    prueba_dos_encargados_no_disparan_el_aviso()
    prueba_avisa_si_ya_esta_contado_en_otra_planilla()
    prueba_no_avisa_si_no_se_pisan_productos()
    prueba_el_cierre_avisa_pero_no_bloquea()
    prueba_el_aviso_del_cierre_se_ve_en_pantalla()
    prueba_candados_en_el_codigo()
    prueba_cada_ruta_apunta_a_su_vista()
    print("=" * 70)
    print(f"FALLOS: {len(FALLOS)}")
    if FALLOS:
        for f in FALLOS:
            print(f"  - {f}")
    print("=" * 70)
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())
