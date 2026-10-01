"""Respaldo y restauracion de la base de datos.

Un solo lugar que sabe (a) generar un dump que SI se puede restaurar sobre una
base existente y (b) validar ese dump antes de tocar la base.

Por que este modulo existe (los tres bugs que tenia el respaldo anterior):

1. `/api/backup` usaba una lista fija de tablas, `_TABLAS_BACKUP`. Se salia en
   silencio 4 tablas que existen en la base: `pedidos`, `pedido_detalle`,
   `stock` y `stock_v2`. Un respaldo sin esas tablas pierde pedidos enteros y
   no hay forma de que el usuario se entere al descargar el archivo.
   Ahora la lista sale de `SHOW TABLES`: si mañana se crea una tabla, queda
   respaldada sola, sin tocar este archivo.

2. `/api/backup` solo escribia `INSERT INTO`. Sin `DROP TABLE` ni
   `CREATE TABLE`, restaurar el archivo sobre la base que ya existe era
   imposible: los INSERT revientaban por clave duplicada (producto 1 ya
   existe) y las columnas nuevas quedaban vacias. Ahora el dump lleva
   estructura + datos, igual que `respaldar.py`.

3. `/api/restaurar` ejecutaba el archivo tal cual, sin mirar que tuviera
   dentro, y la UI decia "antes sehara una copia automatica" sin que existiera
   ninguna copia. Ahora:
     - se valida el contenido y se RECHAZA si trae algo que no sea
       DROP/CREATE TABLE, INSERT o SET (nada de DELETE, DROP DATABASE,
       GRANT, ALTER USER...);
     - antes de aplicar se hace una copia real de la base, y si esa copia
       falla NO se restaura.
"""
import datetime
import decimal
import os
import re

# Statements que un dump generado por este modulo puede contener.
_PERMITIDAS = ("drop_table", "create_table", "insert", "set", "alter_table")

_RE_DROP = re.compile(r"^DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?\s*$", re.I)
_RE_CREATE = re.compile(r"^CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?", re.I)
_RE_ALTER = re.compile(r"^ALTER\s+TABLE\s+[`\"]?(\w+)[`\"]?", re.I)
_RE_INSERT = re.compile(r"^INSERT\s+INTO\s+[`\"]?(\w+)[`\"]?", re.I)
_RE_SET = re.compile(
    r"^SET\s+(NAMES|CHARACTER\s+SET|FOREIGN_KEY_CHECKS|UNIQUE_CHECKS|"
    r"SQL_MODE|SQL_NO_CACHE|AUTO_INCREMENT|CHARSET|COLLATION|"
    r"GLOBAL|SESSION|DEFAULT)\b", re.I)

# Techo de defensa: un .sql de mas de esto no es un respaldo, es un ataque.
TAMANO_MAX = 300 * 1024 * 1024
SENTENCIAS_MAX = 2_000_000
LOTE = 500


class DumpInvalido(Exception):
    """El archivo no es un respaldo restaurable de esta base."""


# ---------------------------------------------------------------------------
# Volcado
# ---------------------------------------------------------------------------
def tablas_de_la_base(conn):
    """TODAS las tablas. Nada de lista fija: asi no se puede volver a colar
    una tabla que quede fuera del respaldo."""
    filas = conn.execute("SHOW TABLES").fetchall()
    tablas = []
    for f in filas:
        tablas.append(f[next(iter(f.keys()))])
    return tablas


def literal(val):
    """Valor de Python -> literal SQL.

    Los numeros NO van entre comillas y los DECIMAL tampoco: `repr(Decimal)`
    devuelve "Decimal('1.5')", que no es SQL valido, y un DECIMAL entre
    comillas MySQL lo acepta pero es mejor no dejarlo al azar.
    """
    if val is None:
        return "NULL"
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, (int, float, decimal.Decimal)):
        return str(val)
    if isinstance(val, (bytes, bytearray)):
        return "X'" + bytes(val).hex() + "'"
    if isinstance(val, (datetime.datetime, datetime.date, datetime.time)):
        if isinstance(val, datetime.datetime):
            txt = val.isoformat(sep=" ")
        else:
            txt = val.isoformat()
        return "'" + txt + "'"
    # MySQL usa backslash como escape dentro de las comillas. Se duplica la barra
    # ANTES de cerrar la cadena: con un solo valor de una barra, "a\b", el '\'
    # escaparia la comilla de cierre y el dump quedaria partido (o inyectaria SQL).
    txt = str(val).replace("\\", "\\\\").replace("'", "''")
    return "'" + txt + "'"


def volcar(conn, tablas=None):
    """Genera el dump completo: esquema + datos de cada tabla.

    El orden va de dependencies a dependientes y cada tabla se crea de cero
    (`DROP TABLE IF EXISTS` + `CREATE TABLE`), asi que restaurar es aplicable
    sobre cualquier base: vacia o con datos.
    """
    tablas = tablas if tablas is not None else tablas_de_la_base(conn)
    yield ("-- Respaldo Pollos Lucho "
           + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
           + "\n-- Generado por core/backup.py (restaurable: lleva DROP + CREATE)\n")
    yield "SET NAMES utf8mb4;\n"
    yield "SET FOREIGN_KEY_CHECKS = 0;\n\n"

    for t in tablas:
        create = conn.execute(f"SHOW CREATE TABLE `{t}`").fetchone()
        if not create:
            continue
        ddl = next((v for k, v in create.items() if "Create Table" in str(k)), None)
        if not ddl:
            continue
        filas = conn.execute(f"SELECT * FROM `{t}`").fetchall()
        cols = [c for c in (filas[0].keys() if filas else [])]
        yield f"-- ---- {t}: {len(filas)} fila(s)\n"
        yield f"DROP TABLE IF EXISTS `{t}`;\n"
        yield ddl + ";\n"
        if filas:
            cols_sql = ", ".join(f"`{c}`" for c in cols)
            for i in range(0, len(filas), LOTE):
                trozo = filas[i:i + LOTE]
                vals = ",\n  ".join(
                    "(" + ", ".join(literal(f[c]) for c in cols) + ")"
                    for f in trozo)
                yield f"INSERT INTO `{t}` ({cols_sql}) VALUES\n  {vals};\n"
        yield "\n"

    yield "SET FOREIGN_KEY_CHECKS = 1;\n"


def volcar_a_archivo(conn, ruta, tablas=None):
    """Escribe el dump a disco. Devuelve (bytes, filas) para poder registrarlo."""
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    filas = 0
    with open(ruta, "w", encoding="utf-8", newline="\n") as fh:
        for trozo in volcar(conn, tablas):
            fh.write(trozo)
            filas += trozo.count("\n")
    return os.path.getsize(ruta), filas


def carpeta_respaldos():
    """<proyecto>/respaldos: las copias de seguridad del servidor."""
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(raiz, "respaldos")


def nombre_respaldo(prefijo="auto"):
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefijo}_pollos_lucho_{ts}.sql"


# ---------------------------------------------------------------------------
# Lectura del script
# ---------------------------------------------------------------------------
def sentencias(sql):
    """Divide el script en sentencias ignorando comentarios y respetando comillas.

    Antes `_divide_sentencias` contaba un `'` como apertura/cierre y se comia los
    comentarios: un texto con apostrofe (un nombre de proveedor, por ejemplo)
    dejaba el parser a media cadena y las sentencias siguientes se partian mal.
    """
    out = []
    buf = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        if ch == "-" and sql.startswith("--", i):
            fin = sql.find("\n", i)
            i = n if fin == -1 else fin + 1
            continue
        if ch == "/" and sql.startswith("/*", i):
            fin = sql.find("*/", i + 2)
            i = n if fin == -1 else fin + 2
            continue
        if ch in ("'", '"', "`"):
            buf.append(ch)
            cierre = ch
            i += 1
            while i < n:
                c = sql[i]
                if c == "\\" and i + 1 < n:      # escape con barra invertida
                    buf.append(c)
                    buf.append(sql[i + 1])
                    i += 2
                    continue
                if c == cierre:
                    if i + 1 < n and sql[i + 1] == cierre:   # '' duplicado
                        buf.append(c)
                        buf.append(c)
                        i += 2
                        continue
                    buf.append(c)
                    i += 1
                    break
                buf.append(c)
                i += 1
            continue
        if ch == ";":
            s = "".join(buf).strip()
            if s:
                out.append(s)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    s = "".join(buf).strip()
    if s:
        out.append(s)
    return out


def _filas_de_insert(stmt):
    """Cuantas filas tiene un INSERT, contando los parentesis de verdad.

    Contar `'),\\n'` a pelo miente: un valor puede contener esa secuencia y
    hacer que la cuenta salga mal, que es justo el dato que se le muestra al
    usuario para que decida si restaura. Se recorre la sentencia respetando
    comillas y nivel de parentesis.
    """
    i = stmt.upper().find("VALUES")
    if i == -1:
        return 0
    filas = 0
    nivel = 0
    dentro = False
    en_cadena = False
    n = len(stmt)
    while i < n:
        ch = stmt[i]
        if en_cadena:
            if ch == "\\":
                i += 2
                continue
            if ch == "'":
                if i + 1 < n and stmt[i + 1] == "'":
                    i += 2
                    continue
                en_cadena = False
            i += 1
            continue
        if ch == "'":
            en_cadena = True
        elif ch == "(":
            if nivel == 0:
                dentro = True
            nivel += 1
        elif ch == ")":
            nivel -= 1
            if nivel == 0 and dentro:
                filas += 1
                dentro = False
        i += 1
    return filas


def _clasificar(stmt):
    for regex, tipo in ((_RE_DROP, "drop_table"), (_RE_CREATE, "create_table"),
                        (_RE_INSERT, "insert"), (_RE_ALTER, "alter_table"),
                        (_RE_SET, "set")):
        m = regex.match(stmt)
        if m:
            # En `SET` el grupo capturado es el nombre del ajuste
            # (`NAMES`, `FOREIGN_KEY_CHECKS`...), no una tabla.
            return tipo, (None if tipo == "set" else m.group(1))
    return None, None


def revisar(sql, conn):
    """Valida el dump ANTES de tocar la base.

    Reglas, en orden:
      1. Solo se admiten DROP/CREATE TABLE, INSERT INTO, ALTER TABLE y un SET de
         lista blanca. Rechaza DELETE, TRUNCATE, DROP DATABASE, GRANT, CREATE
         USER...: el archivo no ejecuta SQL arbitrario.
      2. Toda tabla que el archivo mencione tiene que existir YA en esta base.
         Asi un dump de otra aplicacion no puede crear tablas aqui.
      3. Toda tabla a la que se le inserta tiene que traer su CREATE TABLE en el
         mismo archivo. Sin esquema el restore no es viable sobre una base con
         datos (los INSERT chocan por clave duplicada).

    `conn` es obligatorio: sin ver la base no se puede validar nada, y validar a
    ciegas es justo lo que hacia el restore viejo.
    """
    if conn is None:
        raise DumpInvalido("No se puede validar el archivo sin acceso a la base")
    if len(sql) > TAMANO_MAX:
        raise DumpInvalido(f"El archivo es demasiado grande "
                           f"({len(sql) // (1024 * 1024)} MB)")
    stmts = sentencias(sql)
    if len(stmts) > SENTENCIAS_MAX:
        raise DumpInvalido(f"El archivo tiene demasiadas sentencias ({len(stmts)})")
    if not stmts:
        raise DumpInvalido("El archivo esta vacio")

    try:
        existentes = set(tablas_de_la_base(conn))
    except Exception as e:
        raise DumpInvalido(f"No se pudo leer el esquema de la base: {e}")

    resumen = {"tablas": [], "sentencias": len(stmts),
               "inserts": 0, "filas": 0, "creadas": 0}
    vistas = set()
    creadas_set = set()
    insertadas = set()

    for s in stmts:
        tipo, tabla = _clasificar(s)
        if tipo is None:
            raise DumpInvalido(
                f"El archivo trae una sentencia no permitida: {s[:80]!r}. "
                "Solo se aceptan respaldos generados por la aplicacion.")
        if tabla is not None and tabla not in existentes:
            raise DumpInvalido(
                f"El archivo toca la tabla {tabla!r}, que no existe en esta base. "
                "No parece un respaldo de Pollos Lucho,asi que no se aplica.")
        if tipo == "insert":
            resumen["inserts"] += 1
            resumen["filas"] += _filas_de_insert(s)
            insertadas.add(tabla)
        elif tipo == "create_table":
            resumen["creadas"] += 1
            creadas_set.add(tabla)
        if tabla and tabla not in vistas:
            vistas.add(tabla)
            resumen["tablas"].append(tabla)

    if not resumen["creadas"] and not resumen["inserts"]:
        raise DumpInvalido("El archivo no contiene ni CREATE TABLE ni INSERT INTO")

    sin_esquema = sorted(insertadas - creadas_set)
    if sin_esquema:
        raise DumpInvalido(
            f"El archivo no trae el esquema (CREATE TABLE) de: {sin_esquema}. "
            "Ese formato no se puede restaurar sobre una base con datos; "
            "descarga un respaldo nuevo con la aplicacion actualizada.")
    return resumen


def aplicar(conn, sql):
    """Ejecuta un dump YA validado por `revisar`.

    Va en dos fases a proposito. En MySQL el DDL hace commit implicito, asi que
    una restauracion NO puede ser atomica: si una sentencia falla a mitad, lo
    que queda son tablas DROPeadas para siempre. Con este orden se crea primero
    TODO el esquema y despues se cargan los datos, de modo que un fallo deja
    tablas vacias pero existentes, que es recuperable; el orden contrario
    (DROP+CREATE+INSERT de cada tabla seguido) puede dejar tablas que ya no
    existen en la base.
    """
    stmts = sentencias(sql)
    esquema = [s for s in stmts if _clasificar(s)[0] in ("drop_table", "create_table")]
    datos = [s for s in stmts if _clasificar(s)[0] not in ("drop_table", "create_table")]
    try:
        conn.execute("SET FOREIGN_KEY_CHECKS = 0")
        cur = conn.cursor()
        for s in esquema:
            cur.execute(s)
        conn.commit()
        for s in datos:
            cur.execute(s)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.execute("SET FOREIGN_KEY_CHECKS = 1")
        except Exception:
            pass
    return len(esquema) + len(datos)
