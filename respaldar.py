"""Respaldo de la base de datos pollos_lucho a un archivo .sql (UTF-8).

El archivo incluye el esquema completo (CREATE TABLE) y los datos, es decir,
puede restaurarse de cero en cualquier MySQL sin depender del esquema previo.
Las credenciales se leen desde .env / variables de entorno (ver database.py).
Uso:
    python respaldar.py          # guarda respaldo_pollos_lucho_<fecha>.sql
"""
import datetime
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from database import get_conn  # noqa: E402


def _sql_val(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


c = get_conn()
ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
outfile = os.path.join(BASE, f"respaldo_pollos_lucho_{ts}.sql")
f = open(outfile, "w", encoding="utf-8")

f.write(f"-- Respaldo pollos_lucho {ts}\n-- Base de datos: pollos_lucho\n\n")
f.write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS=0;\n\n")

tables = [r[next(iter(r))] for r in c.execute("SHOW TABLES").fetchall()]
print(f"Tablas encontradas: {len(tables)}")

for t in tables:
    rows = c.execute(f"SELECT * FROM `{t}`").fetchall()
    f.write(f"-- Tabla: {t} ({len(rows)} filas)\n")
    f.write(f"-- Estructura\n")
    f.write(f"DROP TABLE IF EXISTS `{t}`;\n")
    f.write(c.execute(f"SHOW CREATE TABLE `{t}`").fetchone()["Create Table"] + ";\n")
    f.write("-- Datos\n")
    if rows:
        cols = list(rows[0].keys())
        col_str = ", ".join(f"`{col}`" for col in cols)
        f.write(f"INSERT INTO `{t}` ({col_str}) VALUES\n")
        lines = []
        for row in rows:
            vals = tuple(row[c_] for c_ in cols)
            line = "(" + ", ".join(_sql_val(v) for v in vals) + ")"
            lines.append("  " + line)
        f.write(",\n".join(lines) + ";\n\n")
    else:
        f.write("\n")
    print(f"  {t}: {len(rows)} filas")

f.write("SET FOREIGN_KEY_CHECKS=1;\n")
f.close()
c.close()
print(f"\nRespaldo guardado: {outfile}")