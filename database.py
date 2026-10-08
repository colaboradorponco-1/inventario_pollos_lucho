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

    def executemany(self, sql, seq_of_params):
        cur = self.cursor()
        cur.executemany(sql, seq_of_params)
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
                    "principal TINYINT DEFAULT 0,"
                    "es_as TINYINT NOT NULL DEFAULT 0,"
                    "padre_id INT)")
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
                    "unidad_tacho DOUBLE DEFAULT 0,"
                    "pide_tacho TINYINT NOT NULL DEFAULT 0,"
                    "para_proveer TINYINT DEFAULT 1,"
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
        cur.execute("CREATE TABLE IF NOT EXISTS inventario_diario ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "sucursal_id INT NOT NULL,"
                    "categoria_id INT NOT NULL DEFAULT 0,"
                    "fecha DATE NOT NULL,"
                    "hora_corte VARCHAR(20),"
                    "usuario VARCHAR(255),"
                    "cerrado_por VARCHAR(255),"
                    "estado VARCHAR(20) NOT NULL DEFAULT 'abierto',"
                    "fecha_hora_cierre VARCHAR(50),"
                    "observaciones TEXT,"
                    "total_items INT DEFAULT 0,"
                    "total_faltantes INT DEFAULT 0,"
                    "total_sobrantes INT DEFAULT 0,"
                    "valor_diferencia DOUBLE DEFAULT 0,"
                    "FOREIGN KEY (sucursal_id) REFERENCES sucursales(id),"
                    "UNIQUE KEY uq_inv_suc_cat_fecha (sucursal_id, categoria_id, fecha))")
        cur.execute("CREATE TABLE IF NOT EXISTS inventario_detalle ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "inventario_id INT NOT NULL,"
                    "producto_id INT NOT NULL,"
                    "categoria_id INT,"
                    "categoria_nombre VARCHAR(255),"
                    "producto_nombre VARCHAR(255),"
                    "codigo VARCHAR(100),"
                    "unidad VARCHAR(50),"
                    "stock_sistema DOUBLE DEFAULT 0,"
                    "inicial DOUBLE DEFAULT 0,"
                    "ingreso_dia DOUBLE DEFAULT 0,"
                    "ingreso_manual DOUBLE DEFAULT 0,"
                    "disponible DOUBLE DEFAULT 0,"
                    "conteo_fisico DOUBLE,"
                    "final DOUBLE DEFAULT 0,"
                    "utilizada DOUBLE DEFAULT 0,"
                    "diferencia DOUBLE DEFAULT 0,"
                    "costo_promedio DOUBLE DEFAULT 0,"
                    "precio_venta DOUBLE DEFAULT 0,"
                    "observaciones TEXT,"
                    "FOREIGN KEY (inventario_id) REFERENCES inventario_diario(id) ON DELETE CASCADE,"
                    "FOREIGN KEY (producto_id) REFERENCES productos(id),"
                    "UNIQUE KEY uq_inv_prod (inventario_id, producto_id))")
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
                    "avatar VARCHAR(30) NOT NULL DEFAULT 'pollito',"
                    "avatar_imagen MEDIUMBLOB,"
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
        cur.execute("CREATE TABLE IF NOT EXISTS pedido_ticket_secuencia ("
                    "id TINYINT NOT NULL PRIMARY KEY,"
                    "ultimo_id BIGINT NOT NULL)")
        cur.execute("CREATE TABLE IF NOT EXISTS pedidos_archivados ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "pedido_id INT NOT NULL,"
                    "sucursal_id INT NOT NULL,"
                    "usuario VARCHAR(255) DEFAULT '',"
                    "fecha VARCHAR(50) NOT NULL,"
                    "UNIQUE KEY uq_pedido_sucursal_archivado (pedido_id, sucursal_id),"
                    "FOREIGN KEY (pedido_id) REFERENCES pedidos(id) ON DELETE CASCADE,"
                    "FOREIGN KEY (sucursal_id) REFERENCES sucursales(id))")
        cur.execute("CREATE TABLE IF NOT EXISTS pedido_detalle ("
                    "id INT AUTO_INCREMENT PRIMARY KEY,"
                    "pedido_id INT NOT NULL,"
                    "producto_id INT NOT NULL,"
                    "producto_nombre VARCHAR(255) DEFAULT '',"
                    "cantidad DOUBLE NOT NULL,"
                    "destino_id INT,"
                    "unidad VARCHAR(50) DEFAULT 'unidad',"
                    "tacho_fraccion DOUBLE DEFAULT 0,"
                    "tacho_texto VARCHAR(50) DEFAULT '',"
                    "tacho_unidad DOUBLE DEFAULT 0,"
                    "sin_stock TINYINT NOT NULL DEFAULT 0,"
                    "preparado TINYINT NOT NULL DEFAULT 0,"
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


def _limpiar_pedidos_huerfanos(cur):
    """Limpia destinos huérfanos en pedidos y pedido_detalle tras la eliminación de los AS,
    asegurando que lleguen al Almacén Principal o a la sucursal activa correspondiente."""
    try:
        cur.execute("SELECT id FROM sucursales WHERE principal = 1 ORDER BY id LIMIT 1")
        ppal = cur.fetchone()
        ppal_id = ppal["id"] if ppal else 1

        cur.execute("""
            UPDATE pedidos p 
            SET p.destino_id = %s 
            WHERE p.destino_id IS NULL 
               OR p.destino_id NOT IN (SELECT id FROM sucursales)
        """, (ppal_id,))

        cur.execute("""
            UPDATE pedido_detalle pd
            SET pd.destino_id = %s
            WHERE pd.destino_id IS NULL
               OR pd.destino_id NOT IN (SELECT id FROM sucursales)
        """, (ppal_id,))
    except Exception:
        pass


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


def _indice_existe(cur, tabla, indice):
    cur.execute(
        "SELECT COUNT(*) c FROM information_schema.STATISTICS "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s AND INDEX_NAME = %s",
        (MYSQL_DB, tabla, indice))
    return cur.fetchone()["c"] > 0


# Firmas de los movimientos que dejaron las pruebas de la feature de merma, que se
# revirtió. Son filas basura: el tipo 'merma' ya no existe en el código, así que ni
# el FEFO ni el cálculo de stock las cuentan bien.
# OJO con el acento: la nota real dice "REVERSIÓN", por eso se compara con un
# prefijo que no lo incluye (LIKE 'REVERSI%') en vez de la nota completa.
_NOTA_MERMA_PRUEBA = "REVERSI%de prueba de merma%"


def _limpiar_movimientos_merma_prueba(cur):
    """Borra los movimientos de la merma de prueba y recalcula SOLO los lotes que
    esos movimientos tocaban.

    Es deliberadamente estrecho: exige producto 39 (el ACE del inventario diario),
    usuario 'admin', la nota de la reversión (o el tipo 'merma' huérfano) y la fecha
    exacta del día en que se hicieron esas pruebas. Si no encuentra nada no hace
    nada (idempotente).

    Antes esta limpieza la hizo 'reconciliar stock', que reescribió el stock del
    producto a 3.1 en lugar de 2.1. Acá se parte del estado real: se borra la
    basura y el stock vuelve a ser el que dicen los movimientos legítimos.
    """
    cur.execute("""
        SELECT id, lote_id FROM movimientos
        WHERE producto_id = 39 AND usuario = 'admin'
          AND (tipo = 'merma' OR nota LIKE %s)
          AND fecha >= '2026-09-30 00:00:00' AND fecha < '2026-10-01 00:00:00'
    """, (_NOTA_MERMA_PRUEBA,))
    filas = cur.fetchall()
    if not filas:
        return 0
    lote_ids = {f["lote_id"] for f in filas if f["lote_id"] is not None}
    for f in filas:
        cur.execute("DELETE FROM movimientos WHERE id = %s", (f["id"],))
    # Solo se tocan los lotes que esas filas tocaban. Un lote sin movimientos
    # queda en 0: ya no hay nada que lo respalde.
    for lid in lote_ids:
        cur.execute("""
            UPDATE lotes SET cantidad = COALESCE((
                SELECT SUM(IF(tipo = 'entrada', cantidad,
                       IF(tipo = 'salida', -cantidad, cantidad)))
                FROM movimientos WHERE lote_id = %s), 0)
            WHERE id = %s
        """, (lid, lid))
    return len(filas)


def migrar_esquema():
    """Adapta una BD preexistente (esquema SQLite antiguo) al modelo jerárquico
    por sucursal y añade la sección de pedidos/tickets. Es idempotente."""
    db = get_conn()
    cur = db.cursor()
    try:
        # 1) Columnas faltantes
        if not _col_existe(cur, "usuarios", "sucursal_id"):
            _add_columna(cur, "usuarios", "sucursal_id INT")
        # Usuarios RECEPTURA (p. ej. "AS America" / "AS Simon Lopez"): son el
        # ALMACEN de una sucursal proveedora. Su panel es la BANDEJA de pedidos
        # que le hacen a esa sucursal y solo sus propios datos; no crean "Mis
        # pedidos". Sin esta columna no se distinguen del encargado normal de la
        # misma sucursal (mismo rol, misma sucursal) y ven el mismo panel.
        if not _col_existe(cur, "usuarios", "receptor"):
            _add_columna(cur, "usuarios", "receptor TINYINT NOT NULL DEFAULT 0")
        if not _col_existe(cur, "usuarios", "avatar"):
            _add_columna(cur, "usuarios", "avatar VARCHAR(30) NOT NULL DEFAULT 'pollito'")
        if not _col_existe(cur, "usuarios", "avatar_imagen"):
            _add_columna(cur, "usuarios", "avatar_imagen MEDIUMBLOB")
        # Backfill auto de receptores: los almacenes receptor se llaman
        # "AS <Sucursal>" (p. ej. "AS America", "AS Simon Lopez"). Se marcan
        # solos en cada arranque, para que el usuario no tenga que correr
        # UPDATE a mano cada vez que se crea uno. Idempotente: solo sube de
        # 0 a 1, nunca pisa un 1 manual.
        cur.execute("UPDATE usuarios SET receptor = 1 "
                    "WHERE usuario LIKE 'AS %' AND receptor = 0")
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
        # Pedidos por tachos: 1 tacho = N unidades del producto (0 = no se pide por tacho)
        # y la fracción elegida en cada línea del pedido (¼, ½, ¾, entero).
        if not _col_existe(cur, "productos", "unidad_tacho"):
            _add_columna(cur, "productos", "unidad_tacho DOUBLE DEFAULT 0")
        # Solo los productos marcados «se pide por tachos» muestran la opción al pedir.
        if not _col_existe(cur, "productos", "pide_tacho"):
            _add_columna(cur, "productos", "pide_tacho TINYINT NOT NULL DEFAULT 0")
            # Los que ya tenían «1 tacho = N» configurado siguen pudiendo pedirse por tachos.
            cur.execute("UPDATE productos SET pide_tacho = 1 WHERE unidad_tacho > 0")
        # El producto puede ser «para proveer» (lo piden otras sucursales) o solo
        # «para inventario» (se ve en el inventario general pero no se ofrece en
        # pedidos). Default 1 para no cambiar el comportamiento de lo existente.
        if not _col_existe(cur, "productos", "para_proveer"):
            _add_columna(cur, "productos", "para_proveer TINYINT NOT NULL DEFAULT 1")
        if not _col_existe(cur, "pedido_detalle", "tacho_fraccion"):
            _add_columna(cur, "pedido_detalle", "tacho_fraccion DOUBLE DEFAULT 0")
        if not _col_existe(cur, "pedido_detalle", "tacho_texto"):
            _add_columna(cur, "pedido_detalle", "tacho_texto VARCHAR(50) DEFAULT ''")
        # Varias planillas por día: una por categoría (categoria_id 0 = todas).
        if not _col_existe(cur, "inventario_diario", "categoria_id"):
            _add_columna(cur, "inventario_diario", "categoria_id INT NOT NULL DEFAULT 0")
        if _col_existe(cur, "inventario_diario", "categoria_id"):
            # Primero se agrega el índice nuevo; solo si quedó puesto se quita el
            # viejo, para no dejar la tabla sin restricción única en ningún momento.
            if not _indice_existe(cur, "inventario_diario", "uq_inv_suc_cat_fecha"):
                try:
                    cur.execute("ALTER TABLE inventario_diario "
                                "ADD UNIQUE KEY uq_inv_suc_cat_fecha (sucursal_id, categoria_id, fecha)")
                except Exception:
                    pass
            if _indice_existe(cur, "inventario_diario", "uq_inv_suc_cat_fecha") and \
                    _indice_existe(cur, "inventario_diario", "uq_inv_suc_fecha"):
                try:
                    cur.execute("ALTER TABLE inventario_diario DROP INDEX uq_inv_suc_fecha")
                except Exception:
                    pass
        # Cuanto equivale un tacho escrito EN el pedido (1 tacho = N kg), asi no
        # hace falta configurar el producto. 0 = no aplica.
        if not _col_existe(cur, "pedido_detalle", "tacho_unidad"):
            _add_columna(cur, "pedido_detalle", "tacho_unidad DOUBLE DEFAULT 0")
        # El ingreso del día se separa en dos: lo que entra por el sistema (los
        # movimientos 'entrada': compras y pedidos entregados) y lo que el
        # encargado anota a mano (mercadería que llegó por una vía que no pasa por
        # el sistema). Se guardan por separado para que volver a anotar una
        # entrega ya registrada no duplique la mercadería.
        if not _col_existe(cur, "inventario_detalle", "ingreso_manual"):
            _add_columna(cur, "inventario_detalle", "ingreso_manual DOUBLE DEFAULT 0")
        # Que sucursales además de los almacenes principales pueden ser destino
        # de un pedido. Antes la interfaz leía `sucursales.provee` pero la
        # columna nunca se creó, así que daba `undefined` y America y Simón López
        # —que sí distribuyen— quedaban filtradas: sus productos no se podian
        # pedir. La columna se crea ahora; 0 = no provee, que es lo seguro.
        if not _col_existe(cur, "sucursales", "provee"):
            _add_columna(cur, "sucursales", "provee TINYINT NOT NULL DEFAULT 0")
        # Inventario de producción vs inventario de venta: el almacén de
        # producción (el "AS" de cada sucursal proveedora) es UNA SUCURSAL hija
        # con su propio stock. `es_as` la identifica, `padre_id` apunta a la
        # sucursal tienda a la que sigue perteneciendo. Así "AS América" tiene
        # sus toneladas de producción y "América" sus kg de venta, cada uno en
        # su propio inventario.
        if not _col_existe(cur, "sucursales", "es_as"):
            _add_columna(cur, "sucursales", "es_as TINYINT NOT NULL DEFAULT 0")
        if not _col_existe(cur, "sucursales", "padre_id"):
            _add_columna(cur, "sucursales", "padre_id INT")
        # Etapa logística del pedido, SEPARADA de `estado`.
        # `estado` sigue siendo pendiente/despachado/cumplido (lo que usan todos
        # los reportes y filtros, así que no se rompen). `etapa` es el avance
        # operativo de quien cocina y entrega: pendiente -> en_preparacion ->
        # en_camino -> entregado. Al llegar a 'entregado' se mueve el stock.
        if not _col_existe(cur, "pedidos", "etapa"):
            _add_columna(cur, "pedidos", "etapa VARCHAR(20) NOT NULL DEFAULT 'pendiente'")
        # Unificación de estados (2026): estado y etapa comparten el MISMO flujo
        # pendiente -> en_camino -> entregado (+ rechazado desde pendiente).
        # Solo se mapean los valores VIEJOS (despachado/cumplido/en_preparacion):
        # los que ya están en el vocabulario nuevo pasan intactos. Idempotente:
        # en cada arranque no queda trabajo pendiente y nunca pisa un 'rechazado'.
        _orden = {"pendiente": 0, "en_camino": 1, "entregado": 2, "rechazado": 3}
        _nuevos = set(_orden)
        _map_estado = {"despachado": "en_camino", "cumplido": "entregado"}
        _map_etapa = {"en_preparacion": "en_camino", "despachado": "en_camino",
                      "cumplido": "entregado"}
        cur.execute("SELECT id, estado, etapa FROM pedidos")
        for fila in cur.fetchall():
            _e_old = (fila["estado"] or "pendiente").strip().lower() or "pendiente"
            _t_old = (fila["etapa"] or "pendiente").strip().lower() or "pendiente"
            _e = _map_estado.get(_e_old, _e_old if _e_old in _nuevos else "pendiente")
            _t = _map_etapa.get(_t_old, _t_old if _t_old in _nuevos else "pendiente")
            # El que esté más avanzado de los dos manda (pedidos en tránsito
            # tenían etapa 'en_camino' con estado 'pendiente').
            _final = _e if _orden[_e] >= _orden[_t] else _t
            if _final != fila["estado"] or _final != fila["etapa"]:
                cur.execute("UPDATE pedidos SET estado = %s, etapa = %s WHERE id = %s",
                            (_final, _final, fila["id"]))
        # Quien conto cada linea del inventario diario. Hay DOS encargados por
        # sucursal y los dos cuentan sobre la MISMA planilla: sin esto, el ultimo
        # que guardaba una linea pisaba en silencio el conteo del otro y no habia
        # forma de saber despues quien habia contado cada cosa.
        # Nullable a proposito: las planillas ya contadas quedan con NULL (se
        # cuentan antes de esta columna) y no generan avisos falsos ni se rompen.
        if not _col_existe(cur, "inventario_detalle", "contado_por"):
            _add_columna(cur, "inventario_detalle", "contado_por VARCHAR(255)")
        # Marca de "el proveedor no tenía stock de este producto cuando se pidió".
        # El destino de un pedido es quien DISTRIBUYE el producto (la sucursal
        # que lo tiene en su catálogo), no una cualquiera que tenga mercadería:
        # si se eligiera por stock, el pedido le vaciaría el inventario a otra
        # sucursal. Cuando al proveedor no le alcanza, el pedido se manda igual
        # (a él le corresponde) pero queda avisado para que consiga la
        # mercadería, en vez de descubrir el faltante al ir a separar. Default 0:
        # los pedidos ya cargados no generan avisos falsos.
        if not _col_existe(cur, "pedido_detalle", "sin_stock"):
            _add_columna(cur, "pedido_detalle", "sin_stock TINYINT NOT NULL DEFAULT 0")
        if not _col_existe(cur, "pedido_detalle", "preparado"):
            _add_columna(cur, "pedido_detalle", "preparado TINYINT NOT NULL DEFAULT 0")
        cur.execute("CREATE TABLE IF NOT EXISTS pedido_ticket_secuencia ("
                    "id TINYINT NOT NULL PRIMARY KEY,"
                    "ultimo_id BIGINT NOT NULL)")
        cur.execute("""
            INSERT IGNORE INTO pedido_ticket_secuencia (id, ultimo_id)
            SELECT 1, GREATEST(
                COALESCE((SELECT MAX(id) FROM pedidos), 0),
                COALESCE((SELECT MAX(CAST(SUBSTRING(nro_ticket, 5) AS UNSIGNED))
                          FROM pedidos WHERE nro_ticket LIKE 'TKT-%'), 0))
        """)
        if _col_existe(cur, "pedidos", "etapa") and \
                not _indice_existe(cur, "pedidos", "idx_pedidos_etapa"):
            try:
                cur.execute("ALTER TABLE pedidos ADD INDEX idx_pedidos_etapa (etapa)")
            except Exception:
                pass
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

        # Basura de las pruebas de la feature de merma (ya revertida en el código).
        _borrados = _limpiar_movimientos_merma_prueba(cur)
        if _borrados:
            db.commit()
            print(f"[migrar] { _borrados} movimiento(s) de prueba de merma borrados "
                  f"y sus lotes recalculados")

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

        # 6) UNIQUE de almacenes: el nombre único POR SUCURSAL.
        #
        # El modelo jerárquico hace que "Cocina" en AP1 y "Cocina" en Siglo XX sean
        # almacenes distintos, así que el UNIQUE solo sobre `nombre` estorba. Antes la
        # migración lo borraba y ahí quedaba: la restricción desaparecía para siempre
        # y se podían crear tres "Cocina" en la misma sucursal sin avisar.
        #
        # Va ANTES del paso 7 porque los almacenes base se llaman "Cocina"/"Limpieza"
        # en todas las sucursales: con el UNIQUE global puesto, el segundo INSERT
        # revienta.
        #
        # OJO con la API de pymysql: `cur.execute()` devuelve el rowcount (un int),
        # NO el cursor — hay que pedir las filas en una llamada aparte. Encadenar
        # `cur.execute(...).fetchall()` tira AttributeError y tumba la app al
        # arrancar (pasó el 2026-10-01). Solo `conn.execute()` devuelve el cursor.
        try:
            cur.execute("""SELECT INDEX_NAME FROM information_schema.STATISTICS
                           WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'almacenes'
                           AND COLUMN_NAME = 'nombre'
                           AND INDEX_NAME <> 'uq_alm_suc_nombre'
                           GROUP BY INDEX_NAME""", (MYSQL_DB,))
            for _idx in cur.fetchall():
                try:
                    cur.execute("ALTER TABLE almacenes DROP INDEX `%s`"
                                % _idx["INDEX_NAME"])
                except Exception:
                    pass
            db.commit()
            if not _indice_existe(cur, "almacenes", "uq_alm_suc_nombre"):
                cur.execute("""
                    SELECT sucursal_id, LOWER(TRIM(nombre)) AS n, COUNT(*) AS c
                    FROM almacenes WHERE nombre IS NOT NULL
                    GROUP BY sucursal_id, LOWER(TRIM(nombre)) HAVING COUNT(*) > 1
                """)
                _dups = cur.fetchall()
                if _dups:
                    # Con repetidos no se puede crear el índice. No es motivo para
                    # tumbar la app: se avisa y la API valida el duplicado igual.
                    print("[migrar] AVISO: almacenes con nombre repetido en la misma "
                          f"sucursal {[(d['sucursal_id'], d['n'], d['c']) for d in _dups]}: "
                          "no se crea uq_alm_suc_nombre hasta que se renombreen")
                else:
                    cur.execute("ALTER TABLE almacenes ADD UNIQUE KEY "
                                "uq_alm_suc_nombre (sucursal_id, nombre)")
                    print("[migrar] UNIQUE (sucursal_id, nombre) agregado a almacenes")
            db.commit()
        except Exception as _e:
            # Este bloque es una mejora, nunca un requisito para arrancar. Cualquier
            # fallo acá (permisos, MySQL viejo, un índice en uso) se avisa y se sigue.
            try:
                db.rollback()
            except Exception:
                pass
            print(f"[migrar] AVISO: no se pudo ajustar el UNIQUE de almacenes: {_e}")

        # 7) Cada sucursal tendrá sus propios almacenes base (modelo jerárquico:
        # los almacenes pertenecen a una sucursal). Las filiales nuevas (La Paz)
        # reciben así "sus almacenes", no los del almacén principal.
        # El INSERT va envuelto porque un UNIQUE por sucursal choca si la sucursal
        # ya tiene uno con ese nombre, y eso tampoco puede impedir el arranque.
        cur.execute("SELECT id FROM sucursales ORDER BY id")
        _ids_suc = [f["id"] for f in cur.fetchall()]
        cur.execute("SELECT DISTINCT sucursal_id FROM almacenes WHERE sucursal_id IS NOT NULL")
        _con_alm = {r["sucursal_id"] for r in cur.fetchall()}
        _BASE_ALMACENES = ["Almacén Principal", "Cocina", "Limpieza"]
        for sid in _ids_suc:
            if sid in _con_alm:
                continue
            for nm in _BASE_ALMACENES:
                try:
                    cur.execute("INSERT INTO almacenes (nombre, ubicacion, sucursal_id) "
                                "VALUES (%s, %s, %s)", (nm, "", sid))
                except Exception:
                    pass
        db.commit()

        # 8) Normalizar marcas de tiempo: 'AAAA-MM-DDTHH:MM:SS' -> 'AAAA-MM-DD HH:MM:SS'.
        #
        # El cierre del inventario diario y la auditoría se guardaban con el ISO
        # completo (con 'T'), pero todo el resto de la BD usa el espacio. Con la 'T'
        # la tabla de movimientos mostraba la fecha rota ('05T18:20:07/10/2026') y
        # el Excel la exportaba igual. Idempotente: después de la primera pasada no
        # queda nada que tocar. NO se toca inventario_diario.fecha_hora_cierre, que
        # sí se lee en formato ISO.
        try:
            for _t, _c in (("movimientos", "fecha"), ("lotes", "fecha_ingreso"),
                           ("auditoria", "fecha")):
                cur.execute(f"UPDATE {_t} SET {_c} = REPLACE({_c}, 'T', ' ') "
                            f"WHERE {_c} LIKE '____-__-__T%'")
                if cur.rowcount:
                    print(f"[migrar] {cur.rowcount} marca(s) de tiempo con 'T' "
                          f"normalizadas en {_t}.{_c}")
            db.commit()
        except Exception as _e:
            try:
                db.rollback()
            except Exception:
                pass
            print(f"[migrar] AVISO: no se pudieron normalizar las fechas con 'T': {_e}")

        # Limpieza de pedidos huérfanos para asegurar que aparezcan en las bandejas
        _limpiar_pedidos_huerfanos(cur)
        db.commit()
    finally:
        db.close()


def _hash(password):
    from werkzeug.security import generate_password_hash
    return generate_password_hash(password)
