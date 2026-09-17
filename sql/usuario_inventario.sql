-- =====================================================================
--  USUARIO DEDICADO PARA LA APLICACIÓN (inventario)
--  Ejecutar UNA VEZ en producción como root/administrador de MySQL:
--      mysql -u root -p < sql/usuario_inventario.sql
--
--  IMPORTANTE: reemplaza CAMBIAR_ESTA_CONTRASENA por una contraseña
--  fuerte ANTES de ejecutar. Luego pon esas credenciales en el .env
--  del servidor (MYSQL_USER / MYSQL_PASS), nunca en el código.
-- =====================================================================

-- Base de datos principal siempre con utf8mb4 (tildes, ñ, emojis)
CREATE DATABASE IF NOT EXISTS pollos_lucho
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Base temporal para que "probar_respaldo.py" verifique los respaldos
-- (importa el dump en un clon y compara filas).
CREATE DATABASE IF NOT EXISTS pollos_lucho_test
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Usuario de la aplicación: acceso SOLO a las BD de inventario.
-- Se le dan permisos completos (dueño de su BD) porque la app ejecuta
-- migraciones DDL en el arranque (init_db). NADA fuera de pollos_lucho*.
CREATE USER IF NOT EXISTS 'admin_inventario'@'localhost' IDENTIFIED BY 'CAMBIAR_ESTA_CONTRASENA';
ALTER USER 'admin_inventario'@'localhost' IDENTIFIED BY 'CAMBIAR_ESTA_CONTRASENA';

GRANT ALL PRIVILEGES ON pollos_lucho.* TO 'admin_inventario'@'localhost';

FLUSH PRIVILEGES;

-- Verificación rápida:
--   SHOW GRANTS FOR 'admin_inventario'@'localhost';