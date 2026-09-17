-- =====================================================================
--  LIMPIEZA DE DATOS DE PRUEBA  (dejar el sistema "nuevo")
--  ---------------------------------------------------------------------
--  BORRA: productos, lotes, stock, movimientos, ventas, pedidos,
--         repartos, gastos y auditoria (todo lo creado al probar).
--  CONSERVA: usuarios, sucursales, almacenes, proveedores y las
--            categorias reales (solo elimina las que digan "PRUEBA").
--
--  IMPORTANTE: generar un respaldo ANTES de ejecutar este script.
--  Ejecutar en el servidor:
--      sudo mysql pollos_lucho < /root/limpiar_datos_prueba.sql
-- =====================================================================

SET FOREIGN_KEY_CHECKS = 0;

TRUNCATE TABLE pedido_detalle;
TRUNCATE TABLE pedidos;
TRUNCATE TABLE reparto_detalle;
TRUNCATE TABLE repartos;
TRUNCATE TABLE venta_detalle;
TRUNCATE TABLE ventas;
TRUNCATE TABLE movimientos;
TRUNCATE TABLE lotes;
TRUNCATE TABLE stock;
TRUNCATE TABLE gastos;
TRUNCATE TABLE productos;
TRUNCATE TABLE auditoria;

DELETE FROM categorias WHERE UPPER(nombre) LIKE '%PRUEBA%' OR UPPER(nombre) LIKE '%TEST%';

SET FOREIGN_KEY_CHECKS = 1;

-- Resumen final
SELECT 'productos' AS tabla, COUNT(*) AS filas FROM productos
UNION ALL SELECT 'lotes', COUNT(*) FROM lotes
UNION ALL SELECT 'stock', COUNT(*) FROM stock
UNION ALL SELECT 'movimientos', COUNT(*) FROM movimientos
UNION ALL SELECT 'ventas', COUNT(*) FROM ventas
UNION ALL SELECT 'venta_detalle', COUNT(*) FROM venta_detalle
UNION ALL SELECT 'pedidos', COUNT(*) FROM pedidos
UNION ALL SELECT 'pedido_detalle', COUNT(*) FROM pedido_detalle
UNION ALL SELECT 'repartos', COUNT(*) FROM repartos
UNION ALL SELECT 'reparto_detalle', COUNT(*) FROM reparto_detalle
UNION ALL SELECT 'gastos', COUNT(*) FROM gastos
UNION ALL SELECT 'auditoria', COUNT(*) FROM auditoria
UNION ALL SELECT 'categorias', COUNT(*) FROM categorias
UNION ALL SELECT 'proveedores', COUNT(*) FROM proveedores
UNION ALL SELECT 'sucursales', COUNT(*) FROM sucursales
UNION ALL SELECT 'almacenes', COUNT(*) FROM almacenes
UNION ALL SELECT 'usuarios', COUNT(*) FROM usuarios;
