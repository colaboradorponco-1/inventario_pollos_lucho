-- Respaldo pollos_lucho 20260903_094521
-- Base de datos: pollos_lucho

SET FOREIGN_KEY_CHECKS=0;

-- Tabla: almacenes (1 filas)
TRUNCATE TABLE `almacenes`;
INSERT INTO `almacenes` (`id`, `nombre`, `ubicacion`) VALUES
  (1, 'Almacén Principal', '');

-- Tabla: auditoria (45 filas)
TRUNCATE TABLE `auditoria`;
INSERT INTO `auditoria` (`id`, `fecha`, `usuario`, `accion`, `detalle`) VALUES
  (68, '2026-08-05T15:49:27', 'admin', 'Cierre de sesión', 'Usuario admin salió del sistema'),
  (70, '2026-08-05T16:09:13', 'encargado_almacen', 'Producto creado', 'platos desechables (PRD-0001)'),
  (71, '2026-08-05T16:13:12', 'encargado_almacen', 'Producto eliminado', 'Producto ID 13'),
  (72, '2026-08-05T16:18:09', 'encargado_almacen', 'Cierre de sesión', 'Usuario encargado_almacen salió del sistema'),
  (74, '2026-08-05T16:21:39', 'admin', 'Cierre de sesión', 'Usuario admin salió del sistema'),
  (373, '2026-09-01T17:36:03', 'admin2', 'Inicio de sesión', 'Usuario admin2 ingresó al sistema'),
  (384, '2026-09-01T17:40:07', 'admin2', 'Inicio de sesión', 'Usuario admin2 ingresó al sistema'),
  (385, '2026-09-01T17:40:22', 'admin', 'Inicio de sesión', 'Usuario admin ingresó al sistema'),
  (386, '2026-09-01T17:40:22', 'encargado_almacen', 'Inicio de sesión', 'Usuario encargado_almacen ingresó al sistema'),
  (387, '2026-09-01T17:40:22', 'encargado_sigloxx', 'Inicio de sesión', 'Usuario encargado_sigloxx ingresó al sistema'),
  (388, '2026-09-01T17:40:23', 'admin2', 'Inicio de sesión', 'Usuario admin2 ingresó al sistema'),
  (389, '2026-09-01T17:42:37', 'admin', 'Inicio de sesión', 'Usuario admin ingresó al sistema'),
  (390, '2026-09-01T17:42:37', 'encargado_almacen', 'Inicio de sesión', 'Usuario encargado_almacen ingresó al sistema'),
  (391, '2026-09-01T17:42:37', 'encargado_sigloxx', 'Inicio de sesión', 'Usuario encargado_sigloxx ingresó al sistema'),
  (392, '2026-09-01T17:42:37', 'admin2', 'Inicio de sesión', 'Usuario admin2 ingresó al sistema'),
  (393, '2026-09-01T17:44:29', 'admin', 'Inicio de sesión', 'Usuario admin ingresó al sistema'),
  (394, '2026-09-01T17:44:53', 'admin', 'Inicio de sesión', 'Usuario admin ingresó al sistema'),
  (395, '2026-09-01T17:44:53', 'admin2', 'Inicio de sesión', 'Usuario admin2 ingresó al sistema'),
  (396, '2026-09-01T17:44:54', 'encargado_almacen', 'Inicio de sesión', 'Usuario encargado_almacen ingresó al sistema'),
  (397, '2026-09-01T17:44:54', 'encargado_sigloxx', 'Inicio de sesión', 'Usuario encargado_sigloxx ingresó al sistema'),
  (398, '2026-09-01T17:45:28', 'admin', 'Inicio de sesión', 'Usuario admin ingresó al sistema'),
  (399, '2026-09-01T17:45:28', 'admin2', 'Inicio de sesión', 'Usuario admin2 ingresó al sistema'),
  (400, '2026-09-01T17:45:29', 'encargado_almacen', 'Inicio de sesión', 'Usuario encargado_almacen ingresó al sistema'),
  (401, '2026-09-01T17:45:29', 'encargado_sigloxx', 'Inicio de sesión', 'Usuario encargado_sigloxx ingresó al sistema'),
  (402, '2026-09-01T17:47:19', 'admin', 'Producto eliminado', 'Producto ID 19'),
  (403, '2026-09-01T17:47:21', 'admin', 'Producto eliminado', 'Producto ID 18'),
  (404, '2026-09-01T17:47:24', 'admin', 'Producto eliminado', 'Producto ID 21'),
  (405, '2026-09-01T17:47:26', 'admin', 'Producto eliminado', 'Producto ID 13'),
  (406, '2026-09-01T17:47:28', 'admin', 'Producto eliminado', 'Producto ID 23'),
  (407, '2026-09-01T17:47:30', 'admin', 'Producto eliminado', 'Producto ID 22'),
  (408, '2026-09-01T17:47:32', 'admin', 'Producto eliminado', 'Producto ID 14'),
  (409, '2026-09-01T17:47:34', 'admin', 'Producto eliminado', 'Producto ID 15'),
  (410, '2026-09-01T17:47:36', 'admin', 'Producto eliminado', 'Producto ID 16'),
  (411, '2026-09-01T17:47:37', 'admin', 'Producto eliminado', 'Producto ID 17'),
  (412, '2026-09-01T17:47:39', 'admin', 'Producto eliminado', 'Producto ID 20'),
  (413, '2026-09-01T17:47:41', 'admin', 'Producto eliminado', 'Producto ID 26'),
  (414, '2026-09-01T17:47:43', 'admin', 'Producto eliminado', 'Producto ID 25'),
  (415, '2026-09-01T17:47:59', 'admin', 'Cierre de sesión', 'Usuario admin salió del sistema'),
  (416, '2026-09-01T17:48:06', 'encargado_almacen', 'Inicio de sesión', 'Usuario encargado_almacen ingresó al sistema'),
  (417, '2026-09-01T17:48:20', 'encargado_almacen', 'Cierre de sesión', 'Usuario encargado_almacen salió del sistema'),
  (418, '2026-09-01T17:48:29', 'admin', 'Inicio de sesión', 'Usuario admin ingresó al sistema'),
  (419, '2026-09-01T17:50:56', 'admin', 'Inicio de sesión', 'Usuario admin ingresó al sistema'),
  (420, '2026-09-01T18:00:43', 'admin', 'Cierre de sesión', 'Usuario admin salió del sistema'),
  (421, '2026-09-01T18:00:57', 'admin', 'Inicio de sesión', 'Usuario admin ingresó al sistema'),
  (422, '2026-09-03T09:41:29', 'admin', 'Inicio de sesión', 'Usuario admin ingresó al sistema');

-- Tabla: categorias (7 filas)
TRUNCATE TABLE `categorias`;
INSERT INTO `categorias` (`id`, `nombre`) VALUES
  (12, 'Administración'),
  (13, 'Alimentos'),
  (15, 'Bebidas'),
  (10, 'Cocina'),
  (16, 'Gastos'),
  (14, 'Insumos'),
  (11, 'Limpieza');

-- Tabla: gastos (1 filas)
TRUNCATE TABLE `gastos`;
INSERT INTO `gastos` (`id`, `categoria`, `descripcion`, `monto`, `fecha`, `proveedor_id`, `sucursal_id`) VALUES
  (1, 'Otros', '', 34.0, '2026-08-18', NULL, 30);

-- Tabla: movimientos (31 filas)
TRUNCATE TABLE `movimientos`;
INSERT INTO `movimientos` (`id`, `producto_id`, `tipo`, `cantidad`, `precio_unitario`, `fecha`, `almacen_id`, `nota`, `usuario`, `sucursal_id`) VALUES
  (31, 13, 'entrada', 45.0, 45.0, '2026-08-05', 1, 'Stock inicial', 'encargado_almacen', 30),
  (32, 15, 'entrada', 2.0, 0.0, '2026-08-17', NULL, 'Stock inicial', 'admin', 30),
  (33, 16, 'entrada', 2.0, 0.0, '2026-08-17', NULL, 'Stock inicial', 'admin', 30),
  (35, 18, 'entrada', 34.0, 56.0, '2026-08-17', 1, 'Stock inicial', 'admin', 30),
  (36, 19, 'entrada', 34.0, 0.0, '2026-08-17', NULL, 'Stock inicial', 'admin', 30),
  (37, 20, 'entrada', 56.0, 23.0, '2026-08-17', 1, 'Stock inicial', 'admin', 30),
  (38, 18, 'salida', 1.0, 0.0, '2026-08-18', NULL, '', 'admin', 30),
  (39, 16, 'salida', 1.0, 0.0, '2026-08-18', NULL, '', 'admin', 30),
  (40, 16, 'entrada', 5.0, 0.0, '2026-08-18', NULL, '', 'admin', 30),
  (41, 18, 'salida', 11.0, 56.0, '2026-08-18', NULL, 'Reparto #5 a Sucursal 01', 'admin', 30),
  (42, 18, 'entrada', 11.0, 56.0, '2026-08-18', NULL, 'Anulación reparto #5 de Sucursal 01', 'admin', 30),
  (43, 18, 'salida', 2.0, 56.0, '2026-08-18', NULL, 'Reparto #6 a Sucursal 01', 'encargado_almacen', 30),
  (44, 18, 'salida', 1.0, 34.0, '2026-08-18', NULL, 'Venta #7', 'admin', 30),
  (45, 13, 'salida', 2.0, 45.0, '2026-08-19:00', NULL, 'Reparto #7 a Sucursal 01', 'admin', 30),
  (46, 18, 'entrada', 2.0, 56.0, '2026-08-18', NULL, 'Anulación reparto #6 de Sucursal 01', 'admin', 30),
  (47, 13, 'salida', 2.0, 45.0, '2026-08-18', NULL, 'Reparto #8 a Sucursal 01', 'admin', 30),
  (48, 13, 'entrada', 2.0, 45.0, '2026-08-18 18:49:11', NULL, 'Anulación reparto #8 de Sucursal 01', 'admin', 30),
  (49, 13, 'entrada', 2.0, 45.0, '2026-08-18 18:51:10', NULL, 'Anulación reparto #7 de Sucursal 01', 'admin', 30),
  (50, 18, 'salida', 2.0, 34.0, '2026-08-18 18:49:00', NULL, 'Reparto #9 a Sucursal America', 'admin', 30),
  (51, 18, 'salida', 2.0, 0.0, '2026-08-19 15:05:00', NULL, '', 'admin', 30),
  (52, 21, 'entrada', 1.0, 56.0, '2026-08-19 15:47:57', 1, 'Stock inicial', 'admin', 30),
  (53, 22, 'entrada', 45.0, 56.0, '2026-08-19 16:47:30', 1, 'Stock inicial', 'admin', 30),
  (54, 13, 'entrada', 5.0, 45.0, '2026-08-31 15:38:41', NULL, 'Prueba MySQL', 'admin', 30),
  (55, 13, 'salida', 5.0, 45.0, '2026-08-31 15:38:47', NULL, 'Revertir prueba MySQL', 'admin', 30),
  (64, 17, 'salida', 1.0, 0.0, '2026-08-31 18:42:00', NULL, 'Despacho pedido TKT-00001', 'admin', 30),
  (65, 17, 'entrada', 1.0, 0.0, '2026-08-31 18:42:00', NULL, 'Recepción pedido TKT-00001', 'admin', 32),
  (67, 17, 'entrada', 1.0, 0.0, '2026-08-31 18:47:17', NULL, 'Anulación reparto #12 de Siglo XX', 'admin', 30),
  (68, 17, 'salida', 1.0, 0.0, '2026-08-31 18:47:17', NULL, 'Reposición reparto #12 a Siglo XX', 'admin', 32),
  (72, 25, 'entrada', 5.0, 10.0, '2026-08-31 18:59:01', NULL, 'Stock inicial', 'admin', 30),
  (73, 26, 'entrada', 7.0, 10.0, '2026-08-31', NULL, 'test mv entrada', 'admin', 30),
  (74, 26, 'salida', 3.0, 10.0, '2026-08-31', NULL, 'test mv salida', 'admin', 30);

-- Tabla: pedido_detalle (1 filas)
TRUNCATE TABLE `pedido_detalle`;
INSERT INTO `pedido_detalle` (`id`, `pedido_id`, `producto_id`, `producto_nombre`, `cantidad`) VALUES
  (7, 6, 17, 'LAPIZ', 1.0);

-- Tabla: pedidos (1 filas)
TRUNCATE TABLE `pedidos`;
INSERT INTO `pedidos` (`id`, `nro_ticket`, `fecha`, `sucursal_id`, `estado`, `total`, `usuario`, `nota`) VALUES
  (6, 'TKT-00001', '2026-08-31 18:42:00', 32, 'cumplido', 0.0, 'admin', '');

-- Tabla: productos (13 filas)
TRUNCATE TABLE `productos`;
INSERT INTO `productos` (`id`, `codigo`, `nombre`, `categoria_id`, `unidad`, `stock_minimo`, `costo_promedio`, `precio_venta`, `vencimiento`, `almacen_id`, `proveedor_id`, `activo`) VALUES
  (13, 'PRD-0001', 'platos desechables', NULL, 'paquete', 100.0, 45.0, 80.0, '2026-08-31', 1, NULL, 0),
  (14, 'PRD-0002', 'caja', NULL, 'unidad', 0.0, 0.0, 0.0, NULL, NULL, NULL, 0),
  (15, 'PRD-0003', 'caja', NULL, 'unidad', 0.0, 0.0, 0.0, '2026-08-17', NULL, NULL, 0),
  (16, 'X004EGX077', 'caja', NULL, 'unidad', 0.0, 0.0, 0.0, NULL, NULL, NULL, 0),
  (17, '0404175136574', 'LAPIZ', NULL, 'unidad', 0.0, 0.0, 0.0, NULL, NULL, NULL, 0),
  (18, '7750082077812', 'CON BANDA PLASTICA', NULL, 'caja', 23.0, 56.0, 34.0, '2026-08-17', 1, NULL, 0),
  (19, 'PRD-0004', 'caja', NULL, 'unidad', 0.0, 0.0, 0.0, NULL, NULL, NULL, 0),
  (20, 'PRD-0005', 'pollos', NULL, 'kg', 1.0, 23.0, 45.0, '2026-08-19', 1, NULL, 0),
  (21, 'PRD-0006', 'arroz', NULL, 'g', 100.0, 56.0, 34.0, '2026-08-19', 1, NULL, 0),
  (22, '7776507001835', 'papel', NULL, 'caja', 34.0, 56.0, 58.0, '2026-08-20', 1, NULL, 0),
  (23, 'PRD-TEST001', 'PRUEBA-TEMP', NULL, 'unidad', 0.0, 10.0, 15.0, NULL, NULL, NULL, 0),
  (25, 'PTXTEST2026', 'ProdStockOK2', NULL, 'unidad', 0.0, 0.0, 0.0, NULL, NULL, NULL, 0),
  (26, 'PTXMOV2026', 'ProdMovTest', NULL, 'unidad', 0.0, 10.0, 20.0, NULL, NULL, NULL, 0);

-- Tabla: proveedores (0 filas)
TRUNCATE TABLE `proveedores`;
-- Tabla: reparto_detalle (1 filas)
TRUNCATE TABLE `reparto_detalle`;
INSERT INTO `reparto_detalle` (`id`, `reparto_id`, `producto_id`, `producto_nombre`, `cantidad`, `costo_unitario`, `subtotal`) VALUES
  (9, 9, 18, 'CON BANDA PLASTICA', 2.0, 34.0, 68.0);

-- Tabla: repartos (1 filas)
TRUNCATE TABLE `repartos`;
INSERT INTO `repartos` (`id`, `fecha`, `sucursal_id`, `total`, `usuario`, `nota`, `origen_sucursal_id`) VALUES
  (9, '2026-08-18 18:49:00', 35, 68.0, 'admin', '', 30);

-- Tabla: stock (13 filas)
TRUNCATE TABLE `stock`;
INSERT INTO `stock` (`producto_id`, `sucursal_id`, `cantidad`) VALUES
  (13, 30, 45.0),
  (15, 30, 2.0),
  (16, 30, 6.0),
  (17, 30, 0.0),
  (17, 32, 0.0),
  (18, 30, 28.0),
  (19, 30, 34.0),
  (20, 30, 56.0),
  (21, 30, 1.0),
  (21, 32, 1.0),
  (22, 30, 45.0),
  (25, 30, 5.0),
  (26, 30, 4.0);

-- Tabla: sucursales (6 filas)
TRUNCATE TABLE `sucursales`;
INSERT INTO `sucursales` (`id`, `nombre`, `direccion`, `principal`) VALUES
  (30, 'Almacén Principal 1', '', 1),
  (32, 'Siglo XX', '', 0),
  (33, 'Simón López', '', 0),
  (35, 'America', '', 0),
  (36, 'Almacén Principal 2', '', 1),
  (38, 'Sucursal América', '', 0);

-- Tabla: usuarios (7 filas)
TRUNCATE TABLE `usuarios`;
INSERT INTO `usuarios` (`id`, `usuario`, `password_hash`, `nombre`, `rol`, `activo`, `sucursal_id`) VALUES
  (1, 'admin', 'pbkdf2:sha256:1000000$ErZGvKniS1Nn2s2i$e3e15a32c7ba2b334a13ec33cfae4223935ad860bd4fbf867c8c944f1f90a66a', 'Administrador', 'superadmin', 1, 30),
  (2, 'encargado_almacen', 'scrypt:32768:8:1$6YX87mPBCENGwimX$69f3007fa9fd51f214c033037e8996e995c4a0200f1549cc74331dfe51a37f40924a91a60ed355f3ff1c2de28328bd7e150326283043bea03aa9e1b52bb06ecf', 'Encargado de Almacén', 'encargado', 1, 30),
  (4, 'admin2', 'scrypt:32768:8:1$JvQoNPHOxqJqVtub$e56e1b7aba7856f9a74ac973e8ccf0808d0221582c6ad1ed929c806520a730bba50cdca42205c4320663d49af63f59383f8f5643ee95ebfe1af42b59fc39762e', 'Administrador Principal 2', 'admin', 1, 36),
  (5, 'encargado_almacen2', 'scrypt:32768:8:1$xbT72KsTVazgQ1N6$83978bce6fcc7e4cfab6970132655803405128f2415a22ac4912e8b53f0a562e23ee3d4e5cd279d5d2b3cd5994095af0bf6f82b5dc34b923b79804179e182273', 'Encargado Almacen Principal 2', 'encargado', 1, 36),
  (6, 'encargado_america', 'scrypt:32768:8:1$xbT72KsTVazgQ1N6$83978bce6fcc7e4cfab6970132655803405128f2415a22ac4912e8b53f0a562e23ee3d4e5cd279d5d2b3cd5994095af0bf6f82b5dc34b923b79804179e182273', 'Encargado Sucursal America', 'encargado', 1, 35),
  (7, 'encargado_sigloxx', 'scrypt:32768:8:1$xbT72KsTVazgQ1N6$83978bce6fcc7e4cfab6970132655803405128f2415a22ac4912e8b53f0a562e23ee3d4e5cd279d5d2b3cd5994095af0bf6f82b5dc34b923b79804179e182273', 'Encargado Sucursal Siglo XX', 'encargado', 1, 32),
  (8, 'encargado_simonlopez', 'scrypt:32768:8:1$xbT72KsTVazgQ1N6$83978bce6fcc7e4cfab6970132655803405128f2415a22ac4912e8b53f0a562e23ee3d4e5cd279d5d2b3cd5994095af0bf6f82b5dc34b923b79804179e182273', 'Encargado Sucursal Simon Lopez', 'encargado', 1, 33);

-- Tabla: venta_detalle (1 filas)
TRUNCATE TABLE `venta_detalle`;
INSERT INTO `venta_detalle` (`id`, `venta_id`, `producto_id`, `producto_nombre`, `cantidad`, `precio_unitario`, `costo_unitario`, `subtotal`) VALUES
  (7, 7, 18, 'CON BANDA PLASTICA', 1.0, 34.0, 56.0, 34.0);

-- Tabla: ventas (1 filas)
TRUNCATE TABLE `ventas`;
INSERT INTO `ventas` (`id`, `fecha`, `total`, `usuario`, `nota`, `sucursal_id`) VALUES
  (7, '2026-08-18', 34.0, 'admin', '', 30);

SET FOREIGN_KEY_CHECKS=1;
