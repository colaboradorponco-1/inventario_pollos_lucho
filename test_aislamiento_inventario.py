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


def main():
    print("=" * 70)
    print("AISLAMIENTO POR SUCURSAL DEL INVENTARIO DIARIO")
    print("=" * 70)
    prueba_no_puede_abrir_otra_sucursal()
    prueba_mensaje_dice_quien_abrio()
    prueba_candados_en_el_codigo()
    print("=" * 70)
    print(f"FALLOS: {len(FALLOS)}")
    if FALLOS:
        for f in FALLOS:
            print(f"  - {f}")
    print("=" * 70)
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())

