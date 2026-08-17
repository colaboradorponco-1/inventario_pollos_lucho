import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash

DB_PATH = Path(__file__).parent / "inventario.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.executescript("""
    CREATE TABLE IF NOT EXISTS almacenes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL UNIQUE,
        ubicacion TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS categorias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS proveedores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL,
        telefono TEXT DEFAULT '',
        email TEXT DEFAULT '',
        direccion TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS productos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT UNIQUE,
        nombre TEXT NOT NULL,
        categoria_id INTEGER,
        unidad TEXT DEFAULT 'unidad',
        stock_minimo REAL DEFAULT 0,
        costo_promedio REAL DEFAULT 0,
        precio_venta REAL DEFAULT 0,
        vencimiento TEXT,
        almacen_id INTEGER,
        proveedor_id INTEGER,
        activo INTEGER DEFAULT 1,
        FOREIGN KEY (categoria_id) REFERENCES categorias(id),
        FOREIGN KEY (almacen_id) REFERENCES almacenes(id),
        FOREIGN KEY (proveedor_id) REFERENCES proveedores(id)
    );

    CREATE TABLE IF NOT EXISTS movimientos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        producto_id INTEGER NOT NULL,
        tipo TEXT NOT NULL CHECK (tipo IN ('entrada', 'salida')),
        cantidad REAL NOT NULL,
        precio_unitario REAL DEFAULT 0,
        fecha TEXT NOT NULL,
        almacen_id INTEGER,
        nota TEXT DEFAULT '',
        usuario TEXT DEFAULT '',
        FOREIGN KEY (producto_id) REFERENCES productos(id),
        FOREIGN KEY (almacen_id) REFERENCES almacenes(id)
    );

    CREATE TABLE IF NOT EXISTS gastos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        categoria TEXT NOT NULL,
        descripcion TEXT DEFAULT '',
        monto REAL NOT NULL,
        fecha TEXT NOT NULL,
        proveedor_id INTEGER,
        FOREIGN KEY (proveedor_id) REFERENCES proveedores(id)
    );

    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        nombre TEXT DEFAULT '',
        rol TEXT DEFAULT 'encargado',
        activo INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS ventas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        total REAL NOT NULL,
        usuario TEXT DEFAULT '',
        nota TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS venta_detalle (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        venta_id INTEGER NOT NULL,
        producto_id INTEGER NOT NULL,
        producto_nombre TEXT DEFAULT '',
        cantidad REAL NOT NULL,
        precio_unitario REAL DEFAULT 0,
        subtotal REAL DEFAULT 0,
        FOREIGN KEY (venta_id) REFERENCES ventas(id) ON DELETE CASCADE,
        FOREIGN KEY (producto_id) REFERENCES productos(id)
    );

    CREATE TABLE IF NOT EXISTS auditoria (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        usuario TEXT DEFAULT '',
        accion TEXT NOT NULL,
        detalle TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS sucursales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL UNIQUE,
        direccion TEXT DEFAULT '',
        principal INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS repartos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        sucursal_id INTEGER NOT NULL,
        total REAL NOT NULL,
        usuario TEXT DEFAULT '',
        nota TEXT DEFAULT '',
        FOREIGN KEY (sucursal_id) REFERENCES sucursales(id)
    );

    CREATE TABLE IF NOT EXISTS reparto_detalle (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reparto_id INTEGER NOT NULL,
        producto_id INTEGER NOT NULL,
        producto_nombre TEXT DEFAULT '',
        cantidad REAL NOT NULL,
        costo_unitario REAL DEFAULT 0,
        subtotal REAL DEFAULT 0,
        FOREIGN KEY (reparto_id) REFERENCES repartos(id) ON DELETE CASCADE,
        FOREIGN KEY (producto_id) REFERENCES productos(id)
    );

    CREATE TABLE IF NOT EXISTS creditos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        venta_id INTEGER,
        cliente TEXT NOT NULL,
        telefono TEXT DEFAULT '',
        monto REAL NOT NULL,
        saldo REAL NOT NULL,
        fecha TEXT NOT NULL,
        estado TEXT DEFAULT 'pendiente',
        usuario TEXT DEFAULT '',
        FOREIGN KEY (venta_id) REFERENCES ventas(id) ON DELETE SET NULL
    );

    CREATE TABLE IF NOT EXISTS pagos_credito (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        credito_id INTEGER NOT NULL,
        monto REAL NOT NULL,
        fecha TEXT NOT NULL,
        usuario TEXT DEFAULT '',
        FOREIGN KEY (credito_id) REFERENCES creditos(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS stock (
        producto_id INTEGER PRIMARY KEY,
        cantidad REAL NOT NULL DEFAULT 0,
        FOREIGN KEY (producto_id) REFERENCES productos(id)
    );

    CREATE INDEX IF NOT EXISTS idx_mov_producto ON movimientos(producto_id);
    CREATE INDEX IF NOT EXISTS idx_mov_fecha ON movimientos(fecha);
    CREATE INDEX IF NOT EXISTS idx_ventas_fecha ON ventas(fecha);
    CREATE INDEX IF NOT EXISTS idx_venta_detalle_venta ON venta_detalle(venta_id);
    CREATE INDEX IF NOT EXISTS idx_creditos_estado ON creditos(estado);
    CREATE INDEX IF NOT EXISTS idx_pagos_credito ON pagos_credito(credito_id);
    CREATE INDEX IF NOT EXISTS idx_repartos_fecha ON repartos(fecha);
    CREATE INDEX IF NOT EXISTS idx_reparto_detalle_reparto ON reparto_detalle(reparto_id);
    CREATE INDEX IF NOT EXISTS idx_auditoria_fecha ON auditoria(fecha);
    CREATE INDEX IF NOT EXISTS idx_gastos_fecha ON gastos(fecha);
    """)

    # Migración: agregar columnas nuevas si la tabla ya existía sin ellas
    cols = [r[1] for r in cur.execute("PRAGMA table_info(usuarios)").fetchall()]
    if "rol" not in cols:
        cur.execute("ALTER TABLE usuarios ADD COLUMN rol TEXT DEFAULT 'encargado'")
    if "activo" not in cols:
        cur.execute("ALTER TABLE usuarios ADD COLUMN activo INTEGER DEFAULT 1")

    pcols = [r[1] for r in cur.execute("PRAGMA table_info(productos)").fetchall()]
    if "precio_venta" not in pcols:
        cur.execute("ALTER TABLE productos ADD COLUMN precio_venta REAL DEFAULT 0")

    mcols = [r[1] for r in cur.execute("PRAGMA table_info(movimientos)").fetchall()]
    if "usuario" not in mcols:
        cur.execute("ALTER TABLE movimientos ADD COLUMN usuario TEXT DEFAULT ''")

    vdcols = [r[1] for r in cur.execute("PRAGMA table_info(venta_detalle)").fetchall()]
    if "costo_unitario" not in vdcols:
        cur.execute("ALTER TABLE venta_detalle ADD COLUMN costo_unitario REAL DEFAULT 0")

    # Sincronizar la tabla stock con los movimientos existentes (idempotente)
    cur.execute("""
        INSERT OR REPLACE INTO stock (producto_id, cantidad)
        SELECT m.producto_id, SUM(CASE WHEN m.tipo='entrada' THEN m.cantidad ELSE -m.cantidad END)
        FROM movimientos m
        GROUP BY m.producto_id
    """)

    # Datos iniciales (solo la primera vez)
    cur.execute("SELECT COUNT(*) AS c FROM almacenes")
    if cur.fetchone()["c"] == 0:
        cur.executemany("INSERT INTO almacenes (nombre, ubicacion) VALUES (?, ?)",
                        [("Almacén Principal", ""), ("Cocina", ""), ("Limpieza", "")])

    cur.execute("SELECT COUNT(*) AS c FROM categorias")
    if cur.fetchone()["c"] == 0:
        cur.executemany("INSERT INTO categorias (nombre) VALUES (?)",
                        [("Cocina",), ("Limpieza",), ("Administración",), ("Alimentos",), ("Insumos",), ("Bebidas",), ("Gastos",)])

    # Usuario inicial (solo la primera vez): admin / 123456
    cur.execute("SELECT COUNT(*) AS c FROM usuarios")
    if cur.fetchone()["c"] == 0:
        cur.execute("INSERT INTO usuarios (usuario, password_hash, nombre, rol, activo) VALUES (?, ?, ?, ?, 1)",
                    ("admin", generate_password_hash("123456"), "Administrador", "admin"))
        cur.execute("INSERT INTO usuarios (usuario, password_hash, nombre, rol, activo) VALUES (?, ?, ?, ?, 1)",
                    ("encargado_almacen", generate_password_hash("678910"), "Encargado de Almacén", "encargado"))

    # Asegurar rol de admin en el usuario admin (migración)
    cur.execute("UPDATE usuarios SET rol = 'admin', activo = 1 WHERE usuario = 'admin'")

    # Sucursales de Cochabamba (solo la primera vez)
    cur.execute("SELECT COUNT(*) AS c FROM sucursales")
    if cur.fetchone()["c"] == 0:
        for i in range(1, 30):
            principal = 1 if i <= 3 else 0
            cur.execute("INSERT INTO sucursales (nombre, direccion, principal) VALUES (?, ?, ?)",
                        (f"Sucursal {i:02d}", "", principal))

    conn.commit()
    conn.close()
