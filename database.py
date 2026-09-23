"""Capa de base de datos MySQL para el sistema de inventario Pollos Lucho.

Adapta la interfaz de sqlite3 que usaba el sistema a PyMySQL/MariaDB:
- get_conn() devuelve un objeto con la misma API (cursor + row_factory)
- los placeholders '?' se traducen a '%s' automáticamente
- las filas se acceden por nombre de columna (fila['nombre'])
- lastrowid, commit, close, executemany compatibles
"""
import os
import re
import unicodedata
import pymysql
import pymysql.cursors

# ---------------------------------------------------------------------------
# Configuración de conexión MySQL (cargada desde variables de entorno o .env)
# ---------------------------------------------------------------------------
def _cargar_env():
    """Carga KEY=VALUE desde un archivo .env junto al proyecto (sin sobrescribir
    variables ya presentes en el entorno, que tienen prioridad)."""
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(ruta):
        return
    with open(ruta, "r", encoding="utf-8") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            clave = clave.strip()
            valor = valor.strip().strip('"').strip("'")
            if clave and clave not in os.environ:
                os.environ[clave] = valor


_cargar_env()


def _cfg(nombre, defecto=""):
    """Variable de entorno con valor por defecto. Sin valor por defecto
    hardcodeado sensible: si no hay entorno ni .env, se impide arrancar."""
    return os.environ.get(nombre, "").strip() or defecto


MYSQL_HOST = _cfg("MYSQL_HOST", "localhost")
MYSQL_USER = _cfg("MYSQL_USER")
MYSQL_PASS = _cfg("MYSQL_PASS")
MYSQL_DB = _cfg("MYSQL_DB", "pollos_lucho")


def requerir_credenciales():
    """Devuelve True si MYSQL_USER/MYSQL_PASS están definidos (desde .env o env).
    Sin esto no se puede conectar; el error al arrancar será claro."""
    return bool(MYSQL_USER and MYSQL_PASS)


class MysqlRow(dict):
    """Fila con acceso por nombre (dict) y también por índice compatible."""

    def __getitem__(self, key):
        try:
            return dict.__getitem__(self, key)
        except KeyError:
            raise KeyError(key)

    def keys(self):
        return list(dict.keys(self))


class MysqlCursor:
    """Cursor que traduce SQL con '?' a '%s' de PyMySQL."""

    def __init__(self, pymysql_cursor):
        self._c = pymysql_cursor
        self.description = None

    @staticmethod
    def _convierte(sql):
        # Convierte cada '?' fuera de strings/comentarios a '%s',
        # y escapa los '%' literales a '%%' (salvo los '%s' ya insertados)
        # para que PyMySQL (que usa formato %) no los interprete.
        out = []
        in_s = False   # string simple '
        in_d = False   # string doble "
        prev = None
        i = 0
        n = len(sql)
        while i < n:
            ch = sql[i]
            if ch == "'" and not in_d and prev != "\\":
                in_s = not in_s
            elif ch == '"' and not in_s and prev != "\\":
                in_d = not in_d
            if ch == "?" and not in_s and not in_d:
                out.append("%s")
            elif ch == "%":
                if i + 1 < n and sql[i + 1] == "s":
                    out.append("%s")
                    i += 1
                else:
                    out.append("%%")
            else:
                out.append(ch)
            prev = ch
            i += 1
        return "".join(out)

    def execute(self, sql, params=None):
        sql2 = self._convierte(sql)
        if params is None:
            return self._c.execute(sql2)
        if isinstance(params, (list, tuple)):
            return self._c.execute(sql2, tuple(params))
        return self._c.execute(sql2, (params,))

    def executemany(self, sql, seq_of_params):
        sql2 = self._convierte(sql)
        return self._c.executemany(sql2, [tuple(p) if isinstance(p, (list, tuple)) else (p,) for p in seq_of_params])

    def fetchone(self):
        row = self._c.fetchone()
        return row

    def fetchall(self):
        return self._c.fetchall()

    def fetchall_rows(self):
        return self._c.fetchall()

    @property
    def lastrowid(self):
        return self._c.lastrowid

    @property
    def rowcount(self):
        return self._c.rowcount


class MysqlConnection:
    """Conexión con API compatible con sqlite3.Connection."""

    def __init__(self, pymysql_conn, dict_cursor):
        self._conn = pymysql_conn
        self._dict_cursor = dict_cursor

    def cursor(self):
        cur = self._conn.cursor(cursor=self._dict_cursor)
        return MysqlCursor(cur)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def execute(self, sql, params=None):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executescript(self, sql):
        # Ejecuta múltiples sentencias separadas por ';'
        cur = self._conn.cursor()
        cur.execute(sql)
        return cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def get_conn():
    if not requerir_credenciales():
        raise RuntimeError(
            "Faltan credenciales de MySQL (MYSQL_USER/MYSQL_PASS). "
            "Defínelas en el archivo .env o como variables de entorno.")
    conn = pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASS,
        database=MYSQL_DB,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=10,
    )
    return MysqlConnection(conn, pymysql.cursors.DictCursor)


def init_db():
    """Crea la base de datos y las tablas si no existen, y datos iniciales."""
    import hashlib

    # 1) Crear la base de datos si no existe
    conn = pymysql.connect(host=MYSQL_HOST, user=MYSQL_USER, password=MYSQL_PASS,
                           autocommit=True, connect_timeout=10)
    cur = conn.cursor()
    cur.execute(
        f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DB}` "
        f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    conn.close()

    # 2) Crear tablas
    db = get_conn()
    cur = db.cursor()

    # Esquema objetivo (idéntico al generado por mysqldump en producción).
    # Orden: las tablas referenciadas se crean antes que las que las usan.
    cur.execute("SET FOREIGN_KEY_CHECKS = 0")
    try:
        cur.execute("CREATE TABLE IF NOT EXISTS sucursales ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "nombre VARCHAR(255) NOT NULL UNIQUE,"
                    "direccion VARCHAR(255) DEFAULT '',"
                    "principal TINYINT DEFAULT 0)")
        cur.execute("CREATE TABLE IF NOT EXISTS almacenes ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "nombre VARCHAR(255) NOT NULL UNIQUE,"
                    "sucursal_id INT,"
                    "ubicacion VARCHAR(255) DEFAULT '',"
                    "FOREIGN KEY (sucursal_id) REFERENCES sucursales(id))")
        cur.execute("CREATE TABLE IF NOT EXISTS categorias ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "nombre VARCHAR(255) NOT NULL UNIQUE)")
        cur.execute("CREATE TABLE IF NOT EXISTS proveedores ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "nombre VARCHAR(255) NOT NULL,"
                    "telefono VARCHAR(100) DEFAULT '',"
                    "email VARCHAR(255) DEFAULT '',"
                    "direccion VARCHAR(255) DEFAULT '',"
                    "sucursal_id INT)")
        cur.execute("CREATE TABLE IF NOT EXISTS productos ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "codigo VARCHAR(255) UNIQUE,"
                    "nombre VARCHAR(255) NOT NULL,"
                    "marca VARCHAR(100),"
                    "categoria_id INT,"
                    "unidad VARCHAR(50) DEFAULT 'unidad',"
                    "stock_minimo DOUBLE DEFAULT 0,"
                    "costo_promedio DOUBLE DEFAULT 0,"
                    "precio_venta DOUBLE DEFAULT 0,"
                    "vencimiento VARCHAR(50),"
                    "almacen_id INT,"
                    "proveedor_id INT,"
                    "sucursal_id INT,"
                    "activo TINYINT DEFAULT 1,"
                    "FOREIGN KEY (categoria_id) REFERENCES categorias(id),"
                    "FOREIGN KEY (almacen_id) REFERENCES almacenes(id),"
                    "FOREIGN KEY (proveedor_id) REFERENCES proveedores(id))")
        cur.execute("CREATE TABLE IF NOT EXISTS lotes ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "producto_id INT NOT NULL,"
                    "sucursal_id INT NOT NULL,"
                    "lote VARCHAR(100),"
                    "cantidad DOUBLE NOT NULL DEFAULT 0,"
                    "fecha_ingreso VARCHAR(50) NOT NULL,"
                    "fecha_vencimiento DATE,"
                    "FOREIGN KEY (producto_id) REFERENCES productos(id),"
                    "FOREIGN KEY (sucursal_id) REFERENCES sucursales(id))")
        cur.execute("CREATE TABLE IF NOT EXISTS movimientos ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "producto_id INT NOT NULL,"
                    "tipo VARCHAR(10) NOT NULL,"
                    "cantidad DOUBLE NOT NULL,"
                    "precio_unitario DOUBLE DEFAULT 0,"
                    "fecha VARCHAR(50) NOT NULL,"
                    "almacen_id INT,"
                    "nota TEXT,"
                    "usuario VARCHAR(255) DEFAULT '',"
                    "sucursal_id INT,"
                    "proveedor_id INT,"
                    "lote_id INT,"
                    "vencimiento DATE,"
                    "FOREIGN KEY (producto_id) REFERENCES productos(id),"
                    "FOREIGN KEY (almacen_id) REFERENCES almacenes(id))")
        cur.execute("CREATE TABLE IF NOT EXISTS gastos ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "categoria VARCHAR(255) NOT NULL,"
                    "descripcion TEXT,"
                    "monto DOUBLE NOT NULL,"
                    "fecha VARCHAR(50) NOT NULL,"
                    "proveedor_id INT,"
                    "sucursal_id INT,"
                    "FOREIGN KEY (proveedor_id) REFERENCES proveedores(id))")
        cur.execute("CREATE TABLE IF NOT EXISTS usuarios ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "usuario VARCHAR(255) NOT NULL UNIQUE,"
                    "password_hash VARCHAR(255) NOT NULL,"
                    "nombre VARCHAR(255) DEFAULT '',"
                    "rol VARCHAR(50) DEFAULT 'encargado',"
                    "sucursal_id INT,"
                    "activo TINYINT DEFAULT 1)")
        cur.execute("CREATE TABLE IF NOT EXISTS ventas ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "fecha VARCHAR(50) NOT NULL,"
                    "total DOUBLE NOT NULL,"
                    "sucursal_id INT,"
                    "usuario VARCHAR(255) DEFAULT '',"
                    "nota TEXT)")
        cur.execute("CREATE TABLE IF NOT EXISTS venta_detalle ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "venta_id INT NOT NULL,"
                    "producto_id INT NOT NULL,"
                    "producto_nombre VARCHAR(255) DEFAULT '',"
                    "cantidad DOUBLE NOT NULL,"
                    "precio_unitario DOUBLE DEFAULT 0,"
                    "costo_unitario DOUBLE DEFAULT 0,"
                    "subtotal DOUBLE DEFAULT 0,"
                    "FOREIGN KEY (venta_id) REFERENCES ventas(id) ON DELETE CASCADE,"
                    "FOREIGN KEY (producto_id) REFERENCES productos(id))")
        cur.execute("CREATE TABLE IF NOT EXISTS auditoria ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "fecha VARCHAR(50) NOT NULL,"
                    "usuario VARCHAR(255) DEFAULT '',"
                    "accion VARCHAR(255) NOT NULL,"
                    "detalle TEXT)")
        cur.execute("CREATE TABLE IF NOT EXISTS pedidos ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "nro_ticket VARCHAR(30) NOT NULL UNIQUE,"
                    "fecha VARCHAR(50) NOT NULL,"
                    "sucursal_id INT NOT NULL,"
                    "estado VARCHAR(20) DEFAULT 'pendiente',"
                    "total DOUBLE DEFAULT 0,"
                    "usuario VARCHAR(255) DEFAULT '',"
                    "nota TEXT,"
                    "destino_id INT,"
                    "FOREIGN KEY (sucursal_id) REFERENCES sucursales(id))")
        cur.execute("CREATE TABLE IF NOT EXISTS pedido_detalle ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "pedido_id INT NOT NULL,"
                    "producto_id INT NOT NULL,"
                    "producto_nombre VARCHAR(255) DEFAULT '',"
                    "cantidad DOUBLE NOT NULL,"
                    "destino_id INT,"
                    "unidad VARCHAR(50) DEFAULT 'unidad',"
                    "FOREIGN KEY (pedido_id) REFERENCES pedidos(id) ON DELETE CASCADE,"
                    "FOREIGN KEY (producto_id) REFERENCES productos(id))")
        cur.execute("CREATE TABLE IF NOT EXISTS repartos ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "fecha VARCHAR(50) NOT NULL,"
                    "origen_sucursal_id INT,"
                    "sucursal_id INT NOT NULL,"
                    "total DOUBLE NOT NULL,"
                    "usuario VARCHAR(255) DEFAULT '',"
                    "nota TEXT,"
                    "pedido_id INT,"
                    "FOREIGN KEY (sucursal_id) REFERENCES sucursales(id),"
                    "CONSTRAINT fk_repartos_pedido FOREIGN KEY (pedido_id) REFERENCES pedidos(id))")
        cur.execute("CREATE TABLE IF NOT EXISTS reparto_detalle ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "reparto_id INT NOT NULL,"
                    "producto_id INT NOT NULL,"
                    "producto_nombre VARCHAR(255) DEFAULT '',"
                    "cantidad DOUBLE NOT NULL,"
                    "costo_unitario DOUBLE DEFAULT 0,"
                    "subtotal DOUBLE DEFAULT 0,"
                    "FOREIGN KEY (reparto_id) REFERENCES repartos(id) ON DELETE CASCADE,"
                    "FOREIGN KEY (producto_id) REFERENCES productos(id))")
        cur.execute("CREATE TABLE IF NOT EXISTS stock ("
                    "producto_id INT,"
                    "sucursal_id INT,"
                    "cantidad DOUBLE NOT NULL DEFAULT 0,"
                    "PRIMARY KEY (producto_id, sucursal_id),"
                    "FOREIGN KEY (producto_id) REFERENCES productos(id),"
                    "FOREIGN KEY (sucursal_id) REFERENCES sucursales(id))")
    finally:
        cur.execute("SET FOREIGN_KEY_CHECKS = 1")

    # Índices (MySQL no soporta CREATE INDEX IF NOT EXISTS, se verifica antes)
    _INDICES = {
        "idx_mov_producto": "CREATE INDEX idx_mov_producto ON movimientos(producto_id)",
        "idx_mov_fecha": "CREATE INDEX idx_mov_fecha ON movimientos(fecha)",
        "idx_mov_sucursal": "CREATE INDEX idx_mov_sucursal ON movimientos(sucursal_id)",
        "idx_ventas_fecha": "CREATE INDEX idx_ventas_fecha ON ventas(fecha)",
        "idx_ventas_sucursal": "CREATE INDEX idx_ventas_sucursal ON ventas(sucursal_id)",
        "idx_venta_detalle_venta": "CREATE INDEX idx_venta_detalle_venta ON venta_detalle(venta_id)",
        "idx_venta_detalle_producto": "CREATE INDEX idx_venta_detalle_producto ON venta_detalle(producto_id)",
        "idx_repartos_fecha": "CREATE INDEX idx_repartos_fecha ON repartos(fecha)",
        "idx_repartos_sucursal": "CREATE INDEX idx_repartos_sucursal ON repartos(sucursal_id)",
        "idx_reparto_detalle_reparto": "CREATE INDEX idx_reparto_detalle_reparto ON reparto_detalle(reparto_id)",
        "idx_auditoria_fecha": "CREATE INDEX idx_auditoria_fecha ON auditoria(fecha)",
        "idx_gastos_fecha": "CREATE INDEX idx_gastos_fecha ON gastos(fecha)",
        "idx_prod_nombre": "CREATE INDEX idx_prod_nombre ON productos(nombre)",
        "idx_prod_categoria": "CREATE INDEX idx_prod_categoria ON productos(categoria_id)",
        "idx_pedidos_fecha": "CREATE INDEX idx_pedidos_fecha ON pedidos(fecha)",
        "idx_pedidos_sucursal": "CREATE INDEX idx_pedidos_sucursal ON pedidos(sucursal_id)",
        "idx_lotes_prod_suc": "CREATE INDEX idx_lotes_prod_suc ON lotes(producto_id, sucursal_id)",
        "idx_mov_lote": "CREATE INDEX idx_mov_lote ON movimientos(lote_id)",
        "idx_pedido_detalle_pedido": "CREATE INDEX idx_pedido_detalle_pedido ON pedido_detalle(pedido_id)",
    }
    cur.execute(
        "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME IN ('movimientos','ventas','venta_detalle','repartos','reparto_detalle','auditoria','gastos', 'productos','pedidos','pedido_detalle','lotes')",
        (MYSQL_DB,))
    existentes = {r["INDEX_NAME"] for r in cur.fetchall()}
    for nombre, ddl in _INDICES.items():
        if nombre not in existentes:
            try:
                cur.execute(ddl)
            except Exception:
                pass

    db.commit()

    # Datos iniciales (solo para BD nueva/vacía; la migración se hace en migrar_esquema)
    cur.execute("SELECT COUNT(*) AS c FROM almacenes")
    if cur.fetchone()["c"] == 0:
        cur.executemany("INSERT INTO almacenes (nombre, ubicacion) VALUES (%s, %s)",
                        [("Almacén Principal", ""), ("Cocina", ""), ("Limpieza", "")])
    cur.execute("SELECT COUNT(*) AS c FROM categorias")
    if cur.fetchone()["c"] == 0:
        cur.executemany("INSERT INTO categorias (nombre) VALUES (%s)",
                        [("Cocina",), ("Limpieza",), ("Administración",), ("Alimentos",), ("Insumos",), ("Bebidas",), ("Gastos",)])
    cur.execute("SELECT COUNT(*) AS c FROM usuarios")
    if cur.fetchone()["c"] == 0:
        cur.execute("INSERT INTO usuarios (usuario, password_hash, nombre, rol, activo) VALUES (%s, %s, %s, %s, 1)",
                    ("admin", _hash("123456"), "Administrador", "superadmin"))
        cur.execute("INSERT INTO usuarios (usuario, password_hash, nombre, rol, activo) VALUES (%s, %s, %s, %s, 1)",
                    ("encargado_almacen", _hash("678910"), "Encargado de Almacén", "encargado"))
    cur.execute("UPDATE usuarios SET rol = 'superadmin', activo = 1 WHERE usuario = 'admin'")
    cur.execute("SELECT COUNT(*) AS c FROM sucursales")
    if cur.fetchone()["c"] == 0:
        cur.executemany("INSERT INTO sucursales (nombre, direccion, principal) VALUES (%s, %s, %s)",
                        [("Almacén Principal 1", "", 1), ("Almacén Principal 2", "", 1)])

    db.commit()
    db.close()

    # Migrar BD existente (agrega columnas, sucursales y datos que falten)
    migrar_esquema()


def _nombre_norm(nombre):
    """Normaliza un nombre de sucursal para comparar sin acentos ni
    palabras genéricas (sucursal/almacén/principal)."""
    n = unicodedata.normalize("NFD", nombre).encode("ascii", "ignore").decode().lower()
    for w in ("sucursal", "almacen", "principal"):
        n = n.replace(w, " ")
    return " ".join(n.split())


def _col_existe(cur, tabla, columna):
    cur.execute(
        "SELECT COUNT(*) c FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (MYSQL_DB, tabla, columna))
    return cur.fetchone()["c"] > 0


def _add_columna(cur, tabla, ddl):
    try:
        cur.execute(f"ALTER TABLE `{tabla}` ADD COLUMN {ddl}")
        db = None
        return True
    except Exception:
        return False


def migrar_esquema():
    """Adapta una BD preexistente (esquema SQLite antiguo) al modelo jerárquico
    por sucursal y añade la sección de pedidos/tickets. Es idempotente."""
    db = get_conn()
    cur = db.cursor()
    try:
        # 1) Columnas faltantes
        if not _col_existe(cur, "usuarios", "sucursal_id"):
            _add_columna(cur, "usuarios", "sucursal_id INT")
        if not _col_existe(cur, "movimientos", "sucursal_id"):
            _add_columna(cur, "movimientos", "sucursal_id INT")
        if not _col_existe(cur, "gastos", "sucursal_id"):
            _add_columna(cur, "gastos", "sucursal_id INT")
        if not _col_existe(cur, "ventas", "sucursal_id"):
            _add_columna(cur, "ventas", "sucursal_id INT")
        if not _col_existe(cur, "repartos", "origen_sucursal_id"):
            _add_columna(cur, "repartos", "origen_sucursal_id INT")
        if not _col_existe(cur, "pedidos", "destino_id"):
            _add_columna(cur, "pedidos", "destino_id INT")
        if not _col_existe(cur, "pedido_detalle", "destino_id"):
            _add_columna(cur, "pedido_detalle", "destino_id INT")
        if not _col_existe(cur, "pedido_detalle", "unidad"):
            _add_columna(cur, "pedido_detalle", "unidad VARCHAR(50) DEFAULT 'unidad'")
        if not _col_existe(cur, "movimientos", "proveedor_id"):
            _add_columna(cur, "movimientos", "proveedor_id INT")
        if not _col_existe(cur, "proveedores", "sucursal_id"):
            _add_columna(cur, "proveedores", "sucursal_id INT")
        if not _col_existe(cur, "productos", "sucursal_id"):
            _add_columna(cur, "productos", "sucursal_id INT")
        if not _col_existe(cur, "repartos", "pedido_id"):
            _add_columna(cur, "repartos", "pedido_id INT")
        db.commit()
        # Backfill: proveedores existentes van al primer almacén principal
        if _col_existe(cur, "proveedores", "sucursal_id"):
            cur.execute("SELECT id FROM sucursales WHERE principal = 1 ORDER BY id LIMIT 1")
            _prim = cur.fetchone()
            if _prim:
                cur.execute("UPDATE proveedores SET sucursal_id = %s WHERE sucursal_id IS NULL", (_prim["id"],))
                db.commit()
        # Backfill: pedidos antiguos se dirigen al primer almacén principal
        if _col_existe(cur, "pedidos", "destino_id"):
            cur.execute("SELECT id FROM sucursales WHERE principal = 1 ORDER BY id LIMIT 1")
            _prim = cur.fetchone()
            if _prim:
                cur.execute("UPDATE pedidos SET destino_id = %s WHERE destino_id IS NULL", (_prim["id"],))
                db.commit()
        # Backfill: cada línea de pedido hereda el proveedor del pedido y su unidad
        if _col_existe(cur, "pedido_detalle", "destino_id"):
            cur.execute("""UPDATE pedido_detalle d JOIN pedidos p ON p.id = d.pedido_id
                           SET d.destino_id = COALESCE(p.destino_id,
                               (SELECT id FROM sucursales WHERE principal = 1 ORDER BY id LIMIT 1))
                           WHERE d.destino_id IS NULL""")
            db.commit()
        # Backfill: vincular repartos de despacho con su pedido (via nota) y FK si no existe
        if _col_existe(cur, "repartos", "pedido_id"):
            cur.execute("""UPDATE repartos r JOIN pedidos p
                           ON CONCAT('Despacho del pedido ', p.nro_ticket)
                               COLLATE utf8mb4_unicode_ci = r.nota COLLATE utf8mb4_unicode_ci
                           SET r.pedido_id = p.id WHERE r.pedido_id IS NULL""")
            db.commit()
            cur.execute(
                "SELECT COUNT(*) c FROM information_schema.TABLE_CONSTRAINTS "
                "WHERE CONSTRAINT_SCHEMA = %s AND TABLE_NAME = 'repartos' "
                "AND CONSTRAINT_NAME = 'fk_repartos_pedido'",
                (MYSQL_DB,))
            if cur.fetchone()["c"] == 0:
                try:
                    cur.execute("ALTER TABLE repartos ADD CONSTRAINT fk_repartos_pedido "
                                "FOREIGN KEY (pedido_id) REFERENCES pedidos(id)")
                    db.commit()
                except Exception:
                    pass
            # Backfill: totales de repartos de despacho y de su pedido, a partir del detalle.
            # Solo corrige los que quedaron en 0 (no altera totales ya válidos).
            cur.execute("""UPDATE repartos r
                           JOIN (SELECT reparto_id, ROUND(SUM(subtotal), 2) s
                                 FROM reparto_detalle GROUP BY reparto_id) t ON t.reparto_id = r.id
                           SET r.total = t.s
                           WHERE r.pedido_id IS NOT NULL AND (r.total IS NULL OR r.total = 0)""")
            cur.execute("""UPDATE pedidos p
                           JOIN (SELECT r.pedido_id pid, ROUND(SUM(rd.subtotal), 2) s
                                 FROM repartos r JOIN reparto_detalle rd ON rd.reparto_id = r.id
                                 GROUP BY r.pedido_id) t ON t.pid = p.id
                           SET p.total = t.s
                           WHERE p.total IS NULL OR p.total = 0""")
            db.commit()
        if _col_existe(cur, "pedido_detalle", "unidad"):
            cur.execute("""UPDATE pedido_detalle d JOIN productos p ON p.id = d.producto_id
                           SET d.unidad = COALESCE(NULLIF(TRIM(p.unidad), ''), 'unidad')
                           WHERE d.unidad IS NULL OR d.unidad = ''""")
            db.commit()

        # 2) Migrar tabla stock a PK compuesta (producto_id, sucursal_id)
        if _col_existe(cur, "stock", "sucursal_id") is False:
            cur.execute("SELECT id FROM sucursales WHERE principal = 1 ORDER BY id LIMIT 1")
            fila = cur.fetchone()
            if not fila:
                cur.execute("INSERT INTO sucursales (nombre, direccion, principal) VALUES (%s, %s, %s)",
                            ("Almacén Principal 1", "", 1))
                db.commit()
                cur.execute("SELECT id FROM sucursales WHERE principal = 1 ORDER BY id LIMIT 1")
                fila = cur.fetchone()
            suc_default = fila["id"]
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            try:
                cur.execute("""CREATE TABLE stock_v2 (
                    producto_id INT, sucursal_id INT, cantidad DOUBLE NOT NULL DEFAULT 0,
                    PRIMARY KEY (producto_id, sucursal_id),
                    FOREIGN KEY (producto_id) REFERENCES productos(id),
                    FOREIGN KEY (sucursal_id) REFERENCES sucursales(id)) ENGINE=InnoDB""")
                cur.execute("INSERT INTO stock_v2 (producto_id, sucursal_id, cantidad) "
                            "SELECT producto_id, %s, cantidad FROM stock", (suc_default,))
                cur.execute("DROP TABLE stock")
                cur.execute("RENAME TABLE stock_v2 TO stock")
            finally:
                cur.execute("SET FOREIGN_KEY_CHECKS = 1")
            db.commit()

        # 3) Sucursales: garantizar Principales y las solicitantes de destino
        SUCURSALES_BASE = [
            ("Almacén Principal 1", 1), ("Almacén Principal 2", 1),
            ("Sucursal América", 0), ("Siglo XX", 0), ("Simón López", 0),
        ]
        for nombre, principal in SUCURSALES_BASE:
            # Reutiliza una sucursal con nombre parecido (sin acentos/sin palabras
            # genéricas) para no duplicar (ej. "America" ya existe).
            cur.execute("SELECT id, nombre FROM sucursales")
            todas = cur.fetchall()
            encontrada = next(
                (f["id"] for f in todas if _nombre_norm(f["nombre"]) == _nombre_norm(nombre)), None)
            if not encontrada:
                cur.execute("INSERT INTO sucursales (nombre, direccion, principal) VALUES (%s, %s, %s)",
                            (nombre, "", principal))
        db.commit()

        # 3b) La Paz es una sucursal filial (nunca almacén principal): corrige
        # el dato si quedó marcada por error en el panel de sucursales.
        cur.execute("SELECT id, nombre FROM sucursales")
        _todas = cur.fetchall()
        _lp = next((f for f in _todas if "la paz" in _nombre_norm(f["nombre"])), None)
        if _lp:
            cur.execute("UPDATE sucursales SET principal = 0 WHERE id = %s", (_lp["id"],))
        else:
            cur.execute("INSERT INTO sucursales (nombre, direccion, principal) VALUES (%s, %s, %s)",
                        ("La Paz - 6 de Agosto", "", 0))
        db.commit()

        # 4) Asociar el superadmin a Principal 1
        cur.execute("SELECT id FROM sucursales WHERE nombre = 'Almacén Principal 1'")
        p1 = cur.fetchone()
        cur.execute("SELECT id FROM sucursales WHERE nombre = 'Almacén Principal 2'")
        p2 = cur.fetchone()
        # Si ya existe un usuario 'admin', actualizarlo a superadmin; si no, crearlo.
        cur.execute("SELECT id, rol FROM usuarios WHERE usuario = 'admin'")
        admin_existente = cur.fetchone()
        if admin_existente:
            if admin_existente["rol"] != "superadmin":
                cur.execute("UPDATE usuarios SET rol = 'superadmin' WHERE usuario = 'admin'")
            if p1:
                cur.execute("UPDATE usuarios SET sucursal_id = %s WHERE usuario = 'admin'", (p1["id"],))
        else:
            if p1:
                cur.execute("INSERT INTO usuarios (usuario, password_hash, nombre, rol, activo, sucursal_id) "
                            "VALUES (%s, %s, %s, %s, 1, %s)",
                            ("admin", _hash("123456"), "Administrador", "superadmin", p1["id"]))
            else:
                cur.execute("INSERT INTO usuarios (usuario, password_hash, nombre, rol, activo) "
                            "VALUES (%s, %s, %s, %s, 1)",
                            ("admin", _hash("123456"), "Administrador", "superadmin"))
        if p2:
            cur.execute("SELECT id FROM usuarios WHERE usuario = 'admin2'")
            if not cur.fetchone():
                cur.execute("INSERT INTO usuarios (usuario, password_hash, nombre, rol, activo, sucursal_id) "
                            "VALUES (%s, %s, %s, %s, 1, %s)",
                            ("admin2", _hash("admin22"), "Administrador Principal 2", "admin", p2["id"]))
        db.commit()

        # 5) Asignar a cada encargado/admin la sucursal que realmente le toca.
        # Antes todos quedaban en el Almacén Principal 1 por defecto (por eso
        # un encargado de La Paz veía los datos del principal). Si su usuario o
        # nombre menciona una sucursal filial, se le asigna esa sucursal.
        cur.execute("SELECT id, nombre, principal FROM sucursales")
        _sucs = cur.fetchall()
        cur.execute("SELECT id, usuario, nombre, sucursal_id FROM usuarios WHERE rol IN ('encargado','admin')")
        _users = cur.fetchall()
        _princs = {f["id"] for f in _sucs if f["principal"]}
        _STOP = {"la", "de", "el", "los", "las", "del", "un", "una", "6"}
        for u in _users:
            if u["sucursal_id"] is not None and u["sucursal_id"] not in _princs:
                continue  # ya pertenece a una filial concreta
            u_toks = set(_nombre_norm((u["usuario"] or "") + " " + (u["nombre"] or "")).split())
            mejor_id, mejor_score = None, 0
            for s in _sucs:
                if s["principal"]:
                    continue
                s_toks = {t for t in _nombre_norm(s["nombre"]).split()
                          if t not in _STOP and len(t) > 2}
                score = len(s_toks & u_toks)
                if score > mejor_score:
                    mejor_id, mejor_score = s["id"], score
            if mejor_id:
                cur.execute("UPDATE usuarios SET sucursal_id = %s WHERE id = %s",
                            (mejor_id, u["id"]))
        # El resto de encargados/admin sin sucursal queda en el Almacén Principal 1.
        if p1:
            cur.execute("UPDATE usuarios SET sucursal_id = %s WHERE sucursal_id IS NULL AND rol IN ('encargado','admin')",
                        (p1["id"],))
        db.commit()

        # 6) Cada sucursal tendrá sus propios almacenes base (modelo jerárquico:
        # los almacenes pertenecen a una sucursal). Las filiales nuevas (La Paz)
        # reciben así "sus almacenes", no los del almacén principal.
        cur.execute("""SELECT INDEX_NAME FROM information_schema.STATISTICS
                       WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'almacenes'
                       AND COLUMN_NAME = 'nombre'""", (MYSQL_DB,))
        for _idx in cur.fetchall():
            try:
                cur.execute("ALTER TABLE almacenes DROP INDEX `%s`" % _idx["INDEX_NAME"])
            except Exception:
                pass
        db.commit()
        cur.execute("SELECT id FROM sucursales ORDER BY id")
        _ids_suc = [f["id"] for f in cur.fetchall()]
        cur.execute("SELECT DISTINCT sucursal_id FROM almacenes WHERE sucursal_id IS NOT NULL")
        _con_alm = {r["sucursal_id"] for r in cur.fetchall()}
        _BASE_ALMACENES = ["Almacén Principal", "Cocina", "Limpieza"]
        for sid in _ids_suc:
            if sid in _con_alm:
                continue
            for nm in _BASE_ALMACENES:
                cur.execute("INSERT INTO almacenes (nombre, ubicacion, sucursal_id) VALUES (%s, %s, %s)",
                            (nm, "", sid))
        db.commit()
    finally:
        db.close()


def _hash(password):
    from werkzeug.security import generate_password_hash
    return generate_password_hash(password)
