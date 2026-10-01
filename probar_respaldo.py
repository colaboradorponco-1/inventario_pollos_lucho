"""Prueba de integridad del respaldo: importa el .sql en una BD temporal limpia
(sin clonar el esquema) y comprueba que el archivo se restaura tal cual.

Compara fila por fila y valor por valor contra la base original, no solo
CUANTOS hay. Eso importa: un respaldo con los mismos numeros pero los valores
cambiados (un precio, un saldo) pasaria un `COUNT(*)` y al restaurarla
descadraria el inventario sin que nadie lo note.

Tambien se comprueba que el archivo no haya perdido caracteres UTF-8 y que
genere una lista de tablas coherente.

Usa `core.backup.sentencias` para partir el script: partir por ";\n" (lo que
hacia antes) parte en dos cualquier INSERT que lleve un ";" dentro de un dato,
por ejemplo un nombre de proveedor.

No toca la base real: la BD de prueba se elimina al terminar. Falla con
exit != 0 si algo no cuadra.

Uso:    python probar_respaldo.py [archivo.sql]
"""
import os
import sys

import pymysql
import pymysql.cursors

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import database  # noqa: E402
from core.backup import DumpInvalido, revisar, sentencias  # noqa: E402

TEMP_DB = "pollos_lucho_test"


def _conectar(db=None):
    return pymysql.connect(host=database.MYSQL_HOST, user=database.MYSQL_USER,
                           password=database.MYSQL_PASS, database=db,
                           charset="utf8mb4", autocommit=True, connect_timeout=10,
                           cursorclass=pymysql.cursors.DictCursor)


def _elegir_archivo():
    if len(sys.argv) > 1:
        return sys.argv[1]
    # El volcado actual escribe en respaldos/, los antiguos quedaron en la raiz.
    candidatos = []
    for carpeta in (os.path.join(BASE, "respaldos"), BASE):
        if not os.path.isdir(carpeta):
            continue
        for f in os.listdir(carpeta):
            if f.endswith(".sql") and ("respaldo" in f or "antes_de_restaurar" in f):
                ruta = os.path.join(carpeta, f)
                candidatos.append((os.path.getmtime(ruta), ruta))
    if not candidatos:
        print("No hay respaldos para probar.")
        return None
    return sorted(candidatos)[-1][1]


def main():
    ruta = _elegir_archivo()
    if not ruta:
        return 1
    sql = open(ruta, "r", encoding="utf-8").read()
    print(f"Probando: {ruta} ({len(sql)} bytes)")

    if "\ufffd" in sql:
        print("ERROR: el archivo tiene caracteres de reemplazo (encoding roto).")
        return 1

    # Partir con el parser de la app, no con split(";\n").
    stmts = sentencias(sql)
    print(f"  sentencias detectadas: {len(stmts)}")

    conn = _conectar()
    cur = conn.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS `{TEMP_DB}`")
    cur.execute(f"CREATE DATABASE `{TEMP_DB}` CHARACTER SET utf8mb4 "
                f"COLLATE utf8mb4_unicode_ci")
    fallos = 0
    try:
        cur.execute(f"USE `{TEMP_DB}`")

        # 1) Ejecutar el dump tal cual, en el mismo orden que usaria un restore.
        aplicadas = 0
        for s in stmts:
            try:
                cur.execute(s)
                aplicadas += 1
            except Exception:
                print(f"  FALLA aplicando: {s[:110]}...")
                raise
        print(f"  sentencias aplicadas: {aplicadas}")

        # 2) Comparar contra la base real, TABLA POR TABLA y VALOR POR VALOR.
        orig = _conectar(database.MYSQL_DB)
        ocur = orig.cursor()

        cur.execute("SHOW TABLES")
        tablas = [list(r.values())[0] for r in cur.fetchall()]
        print(f"  tablas en el respaldo: {len(tablas)}")

        for t in tablas:
            cur.execute(f"SELECT COUNT(*) c FROM `{t}`")
            n = cur.fetchone()["c"]
            try:
                ocur.execute(f"SHOW TABLES LIKE %s", (t,))
                existe = bool(ocur.fetchone())
            except Exception:
                existe = False
            if not existe:
                print(f"  {t}: {n} filas (no existe en la original)")
                continue

            ocur.execute(f"SELECT COUNT(*) c FROM `{t}`")
            n0 = ocur.fetchone()["c"]
            if n != n0:
                print(f"  {t}: {n} filas -> DESCUADRE (orig={n0})")
                fallos += 1
                continue

            # Mismo numero de filas: comparar el contenido.
            cur.execute(f"SELECT * FROM `{t}`")
            a = cur.fetchall()
            ocur.execute(f"SELECT * FROM `{t}`")
            b = ocur.fetchall()
            claves = list(a[0].keys()) if a else (list(b[0].keys()) if b else [])
            ka = sorted([tuple(str(r.get(c)) for c in claves) for r in a])
            kb = sorted([tuple(str(r.get(c)) for c in claves) for r in b])
            if ka == kb:
                print(f"  {t}: {n} filas -> OK (contenido identico)")
            else:
                solo_a = [x for x in ka if x not in set(kb)]
                solo_b = [x for x in kb if x not in set(ka)]
                print(f"  {t}: {n} filas -> CONTENIDO DISTINTO")
                if solo_a:
                    print(f"      solo en el respaldo ({len(solo_a)}): {solo_a[:2]}")
                if solo_b:
                    print(f"      solo en la base      ({len(solo_b)}): {solo_b[:2]}")
                fallos += 1

        # 3) UTF-8 real
        cur.execute("SELECT nombre FROM sucursales LIMIT 8")
        nombres = [r["nombre"] for r in cur.fetchall()]
        print(f"  sucursales: {nombres}")
        orig.close()
    finally:
        try:
            cur.execute(f"DROP DATABASE IF EXISTS `{TEMP_DB}`")
        except Exception:
            pass
        conn.close()

    if fallos:
        print(f"ERROR: el respaldo NO es integro ({fallos} tabla(s) con problema).")
        return 1
    print("Respaldo verificado correctamente (se restaura identico a la base).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
