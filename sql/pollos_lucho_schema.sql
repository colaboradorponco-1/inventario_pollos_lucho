-- =============================================================
--  POLLOS LUCHO - Sistema de Inventario (MODELO JERÁRQUICO)
--  Script para MySQL Workbench (MySQL 8.0)
--  Crea la base de datos, las tablas y los datos iniciales.
--  Selecciona todo y ejecuta (Ctrl+Shift+Enter).
--
--  Modelo multi-sucursal:
--  - Varias PC (sucursales) se conectan por red/WiFi a una BD central.
--  - Roles jerárquicos: superadmin / admin / encargado.
--  - Inventario y stock separados por sucursal.
-- =============================================================

-- Base de datos (compartida por todas las sucursales vía red/WiFi)
CREATE DATABASE IF NOT EXISTS `pollos_lucho`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `pollos_lucho`;

-- =============================================================
--  TABLAS
-- =============================================================

CREATE TABLE IF NOT EXISTS `almacenes` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `nombre` VARCHAR(255) NOT NULL UNIQUE,
  `ubicacion` VARCHAR(255) DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `categorias` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `nombre` VARCHAR(255) NOT NULL UNIQUE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `proveedores` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `nombre` VARCHAR(255) NOT NULL,
  `telefono` VARCHAR(100) DEFAULT '',
  `email` VARCHAR(255) DEFAULT '',
  `direccion` VARCHAR(255) DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `productos` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `codigo` VARCHAR(255) UNIQUE,
  `nombre` VARCHAR(255) NOT NULL,
  `categoria_id` INT,
  `unidad` VARCHAR(50) DEFAULT 'unidad',
  `stock_minimo` DOUBLE DEFAULT 0,
  `costo_promedio` DOUBLE DEFAULT 0,
  `precio_venta` DOUBLE DEFAULT 0,
  `vencimiento` VARCHAR(50),
  `almacen_id` INT,
  `proveedor_id` INT,
  `activo` TINYINT DEFAULT 1,
  CONSTRAINT `fk_prod_categoria` FOREIGN KEY (`categoria_id`) REFERENCES `categorias`(`id`),
  CONSTRAINT `fk_prod_almacen` FOREIGN KEY (`almacen_id`) REFERENCES `almacenes`(`id`),
  CONSTRAINT `fk_prod_proveedor` FOREIGN KEY (`proveedor_id`) REFERENCES `proveedores`(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `sucursales` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `nombre` VARCHAR(255) NOT NULL UNIQUE,
  `direccion` VARCHAR(255) DEFAULT '',
  `principal` TINYINT DEFAULT 0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Stock por producto Y por sucursal (PK compuesta)
CREATE TABLE IF NOT EXISTS `stock` (
  `producto_id` INT NOT NULL,
  `sucursal_id` INT NOT NULL,
  `cantidad` DOUBLE NOT NULL DEFAULT 0,
  PRIMARY KEY (`producto_id`, `sucursal_id`),
  CONSTRAINT `fk_stock_producto` FOREIGN KEY (`producto_id`) REFERENCES `productos`(`id`),
  CONSTRAINT `fk_stock_sucursal` FOREIGN KEY (`sucursal_id`) REFERENCES `sucursales`(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `movimientos` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `producto_id` INT NOT NULL,
  `tipo` VARCHAR(10) NOT NULL,
  `cantidad` DOUBLE NOT NULL,
  `precio_unitario` DOUBLE DEFAULT 0,
  `fecha` VARCHAR(50) NOT NULL,
  `almacen_id` INT,
  `nota` TEXT,
  `usuario` VARCHAR(255) DEFAULT '',
  `sucursal_id` INT,
  CONSTRAINT `fk_mov_producto` FOREIGN KEY (`producto_id`) REFERENCES `productos`(`id`),
  CONSTRAINT `fk_mov_almacen` FOREIGN KEY (`almacen_id`) REFERENCES `almacenes`(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `gastos` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `categoria` VARCHAR(255) NOT NULL,
  `descripcion` TEXT,
  `monto` DOUBLE NOT NULL,
  `fecha` VARCHAR(50) NOT NULL,
  `proveedor_id` INT,
  `sucursal_id` INT,
  CONSTRAINT `fk_gasto_proveedor` FOREIGN KEY (`proveedor_id`) REFERENCES `proveedores`(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Usuarios con rol jerárquico (superadmin/admin/encargado) y sucursal asignada
CREATE TABLE IF NOT EXISTS `usuarios` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `usuario` VARCHAR(255) NOT NULL UNIQUE,
  `password_hash` VARCHAR(255) NOT NULL,
  `nombre` VARCHAR(255) DEFAULT '',
  `rol` VARCHAR(50) DEFAULT 'encargado',
  `activo` TINYINT DEFAULT 1,
  `sucursal_id` INT,
  CONSTRAINT `fk_user_sucursal` FOREIGN KEY (`sucursal_id`) REFERENCES `sucursales`(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `ventas` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `fecha` VARCHAR(50) NOT NULL,
  `total` DOUBLE NOT NULL,
  `usuario` VARCHAR(255) DEFAULT '',
  `nota` TEXT,
  `sucursal_id` INT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `venta_detalle` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `venta_id` INT NOT NULL,
  `producto_id` INT NOT NULL,
  `producto_nombre` VARCHAR(255) DEFAULT '',
  `cantidad` DOUBLE NOT NULL,
  `precio_unitario` DOUBLE DEFAULT 0,
  `costo_unitario` DOUBLE DEFAULT 0,
  `subtotal` DOUBLE DEFAULT 0,
  CONSTRAINT `fk_vd_venta` FOREIGN KEY (`venta_id`) REFERENCES `ventas`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_vd_producto` FOREIGN KEY (`producto_id`) REFERENCES `productos`(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `auditoria` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `fecha` VARCHAR(50) NOT NULL,
  `usuario` VARCHAR(255) DEFAULT '',
  `accion` VARCHAR(255) NOT NULL,
  `detalle` TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Repartos con sucursal origen (de dónde sale el stock)
CREATE TABLE IF NOT EXISTS `repartos` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `fecha` VARCHAR(50) NOT NULL,
  `sucursal_id` INT NOT NULL,
  `total` DOUBLE NOT NULL,
  `usuario` VARCHAR(255) DEFAULT '',
  `nota` TEXT,
  `origen_sucursal_id` INT,
  CONSTRAINT `fk_rep_sucursal` FOREIGN KEY (`sucursal_id`) REFERENCES `sucursales`(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `reparto_detalle` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `reparto_id` INT NOT NULL,
  `producto_id` INT NOT NULL,
  `producto_nombre` VARCHAR(255) DEFAULT '',
  `cantidad` DOUBLE NOT NULL,
  `costo_unitario` DOUBLE DEFAULT 0,
  `subtotal` DOUBLE DEFAULT 0,
  CONSTRAINT `fk_rd_reparto` FOREIGN KEY (`reparto_id`) REFERENCES `repartos`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_rd_producto` FOREIGN KEY (`producto_id`) REFERENCES `productos`(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================
--  FASE 2: PEDIDOS / TICKETS POR SUCURSAL
-- =============================================================

CREATE TABLE IF NOT EXISTS `pedidos` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `nro_ticket` VARCHAR(30) NOT NULL UNIQUE,
  `fecha` VARCHAR(50) NOT NULL,
  `sucursal_id` INT NOT NULL,
  `estado` VARCHAR(20) DEFAULT 'pendiente',
  `total` DOUBLE DEFAULT 0,
  `usuario` VARCHAR(255) DEFAULT '',
  `nota` TEXT,
  CONSTRAINT `fk_ped_sucursal` FOREIGN KEY (`sucursal_id`) REFERENCES `sucursales`(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `pedido_detalle` (
  `id` INT AUTO_INCREMENT PRIMARY KEY,
  `pedido_id` INT NOT NULL,
  `producto_id` INT NOT NULL,
  `producto_nombre` VARCHAR(255) DEFAULT '',
  `cantidad` DOUBLE NOT NULL,
  CONSTRAINT `fk_pd_pedido` FOREIGN KEY (`pedido_id`) REFERENCES `pedidos`(`id`) ON DELETE CASCADE,
  CONSTRAINT `fk_pd_producto` FOREIGN KEY (`producto_id`) REFERENCES `productos`(`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- =============================================================
--  ÍNDICES (se crean solo si no existen aún)
-- =============================================================
DELIMITER $$
DROP PROCEDURE IF EXISTS `CrearIndice`$$
CREATE PROCEDURE `CrearIndice`(IN p_tabla VARCHAR(64), IN p_indice VARCHAR(64),
                               IN p_columnas VARCHAR(255))
BEGIN
    DECLARE n INT;
    SELECT COUNT(*) INTO n FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = p_tabla AND INDEX_NAME = p_indice;
    IF n = 0 THEN
        SET @ddl = CONCAT('CREATE INDEX `', p_indice, '` ON `', p_tabla, '` (', p_columnas, ')');
        PREPARE stmt FROM @ddl;
        EXECUTE stmt;
        DEALLOCATE PREPARE stmt;
    END IF;
END$$
DELIMITER ;

CALL `CrearIndice`('movimientos', 'idx_mov_producto', '`producto_id`');
CALL `CrearIndice`('movimientos', 'idx_mov_fecha', '`fecha`');
CALL `CrearIndice`('ventas', 'idx_ventas_fecha', '`fecha`');
CALL `CrearIndice`('venta_detalle', 'idx_venta_detalle_venta', '`venta_id`');
CALL `CrearIndice`('repartos', 'idx_repartos_fecha', '`fecha`');
CALL `CrearIndice`('reparto_detalle', 'idx_reparto_detalle_reparto', '`reparto_id`');
CALL `CrearIndice`('auditoria', 'idx_auditoria_fecha', '`fecha`');
CALL `CrearIndice`('gastos', 'idx_gastos_fecha', '`fecha`');
CALL `CrearIndice`('pedidos', 'idx_pedidos_fecha', '`fecha`');
CALL `CrearIndice`('pedidos', 'idx_pedidos_sucursal', '`sucursal_id`');
CALL `CrearIndice`('pedido_detalle', 'idx_pedido_detalle_pedido', '`pedido_id`');

DROP PROCEDURE IF EXISTS `CrearIndice`;

-- =============================================================
--  DATOS INICIALES (solo si están vacías)
-- =============================================================

INSERT INTO `almacenes` (nombre, ubicacion)
SELECT * FROM (SELECT 'Almacén Principal', '' UNION ALL
               SELECT 'Cocina', '' UNION ALL
               SELECT 'Limpieza', '') t
WHERE NOT EXISTS (SELECT 1 FROM `almacenes`);

INSERT INTO `categorias` (nombre)
SELECT * FROM (SELECT 'Cocina' UNION ALL SELECT 'Limpieza' UNION ALL
               SELECT 'Administración' UNION ALL SELECT 'Alimentos' UNION ALL
               SELECT 'Insumos' UNION ALL SELECT 'Bebidas' UNION ALL SELECT 'Gastos') t
WHERE NOT EXISTS (SELECT 1 FROM `categorias`);

-- Sucursales: las dos primeras son principales (Almacenes Principales 1 y 2)
INSERT INTO `sucursales` (nombre, direccion, principal)
SELECT CONCAT('Almacén Principal ', n), '', IF(n <= 2, 1, 0)
FROM (SELECT 1 n UNION ALL SELECT 2) nums
WHERE NOT EXISTS (SELECT 1 FROM `sucursales`);

-- Usuario administrador (password: 123456) y encargado (password: 678910)
-- Los hashes son scrypt válidos para werkzeug (check_password_hash)
-- admin = superadmin (sin sucursal fija / sucursal 1)
INSERT INTO `usuarios` (usuario, password_hash, nombre, rol, activo, sucursal_id)
SELECT 'admin', 'scrypt:32768:8:1$ityawKwQIpvy0Mbf$5a4836db21caa4c7a2635106e7de07183c4148fe9241cc6fc67777f264fcd1c2965e9e5be2a876a82f6d6e42123192d0c6a7febe907e22ddf3f80de44a2e0d5f', 'Administrador', 'superadmin', 1, 1
WHERE NOT EXISTS (SELECT 1 FROM `usuarios` WHERE usuario = 'admin');

INSERT INTO `usuarios` (usuario, password_hash, nombre, rol, activo, sucursal_id)
SELECT 'encargado_almacen', 'scrypt:32768:8:1$uG4KEXjH0eFTGiFB$3288834d380b2db5d8a2ad9064a75ee21f0384724d3c835a2420f660cd5117ddfc4b6d6347f9cf979f95e263d5827c1a8fce1dc76716d2c2e2d9461214f930be', 'Encargado de Almacén', 'encargado', 1, 1
WHERE NOT EXISTS (SELECT 1 FROM `usuarios` WHERE usuario = 'encargado_almacen');
