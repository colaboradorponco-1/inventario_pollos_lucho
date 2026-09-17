"""Prueba de integridad del respaldo: importa el .sql en una BD temporal limpia
(sin clonar el esquema), comprobando que el archivo se restaura tal cual
(esquema + datos). Compara filas por tabla y revisa caracteres UTF-8
(tildes/ñ). No toca la base de datos real; la BD de prueba se elimina al
terminar. Falla con exit != 0 si algo no cuadra.

Uso:    python probar_respaldo.py [archivo.sql]
"""
import os
import sys

import pymysql
import pymysql.cursors

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

import database  # noqa: E402

TEMP_DB = "pollos_lucho_test"
ORIG_DB = database.MYSQL_DB


def main():
    archivo = sys.argv[1] if len(sys.argv) > 1 else None
    if not archivo:
        respaldos = sorted(
            f for f in os.listdir(BASE)
            if f.startswith("respaldo_pollos_lucho_") and f.endswith(".sql"))
        if not respaldos:
            print("No hay respaldos para probar.")
            return 1
        archivo = respaldos[-1]
    ruta = os.path.join(BASE, archivo)
    sql = open(ruta, "r", encoding="utf-8").read()
    if "\ufffd" in sql or "\u00c3" in sql:
        print(f"ERROR: codificación sospechosa en {archivo}.")
        return 1

    conn = pymysql.connect(host=database.MYSQL_HOST, user=database.MYSQL_USER,
                           password=database.MYSQL_PASS,
                           charset="utf8mb4", autocommit=True, connect_timeout=10,
                           cursorclass=pymysql.cursors.DictCursor)
    cur = conn.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS `{TEMP_DB}`")
    cur.execute(f"CREATE DATABASE `{TEMP_DB}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")

    try:
        cur.execute(f"USE `{TEMP_DB}`")

        # 1) Ejecutar el dump completo (esquema + datos) en orden
        for parte in sql.split(";\n"):
            # Elimina líneas de comentario (la sentencia real puede compartir
            # segmento con un comentario que la precede)
            lineas = [l for l in parte.split("\n")
                      if not l.strip().startswith("--")]
            stmt = "\n".join(lineas).strip()
            if not stmt:
                continue
            try:
                cur.execute(stmt)
            except Exception:
                print(f"FALLA en sentencia: {stmt[:120]}...")
                raise

        # 2) Comparar filas con la BD original
        orig = pymysql.connect(host=database.MYSQL_HOST, user=database.MYSQL_USER,
                               password=database.MYSQL_PASS, database=ORIG_DB,
                               charset="utf8mb4", autocommit=True, connect_timeout=10,
                               cursorclass=pymysql.cursors.DictCursor)
        ocur = orig.cursor()
        cur.execute("SHOW TABLES")
        tablas = [list(r.values())[0] for r in cur.fetchall()]
        ok = True
        for t in tablas:
            cur.execute(f"SELECT COUNT(*) c FROM `{t}`")
            n = cur.fetchone()["c"]
            try:
                ocur.execute(f"SELECT COUNT(*) c FROM `{t}`")
                n0 = ocur.fetchone()["c"]
            except Exception:
                n0 = -1
            cuadra = n0 == -1 or n == n0
            if not cuadra:
                ok = False
            print(f"  {t}: {n} filas -> {'OK' if cuadra else f'DESCUADRE (orig={n0})'}")
        orig.close()

        # 3) Caracteres UTF-8 (tildes/ñ) en nombres
        cur.execute("SELECT nombre FROM sucursales LIMIT 8")
        nombres = [r["nombre"] for r in cur.fetchall()]
        print("Sucursales (codificación):", nombres)
        con_tilde = any("\u00f1" in n or any(c in n for c in "áéíóúü") for n in nombres)
        if con_tilde:
            print("  -> tildes/ñ presentes y válidos")
        if not ok:
            print("ERROR: el respaldo NO es íntegro (descuadre de filas).")
            return 1
        print("Respaldo verificado correctamente (restauración completa).")
        return 0
    finally:
        try:
            cur.execute(f"DROP DATABASE IF EXISTS `{TEMP_DB}`")
        except Exception:
            pass
        conn.close()


if __name__ == "__main__":
    sys.exit(main())