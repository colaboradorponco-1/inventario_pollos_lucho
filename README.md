# Inventario Pollos Lucho

Sistema de inventario multi-sucursal (productos, movimientos, ventas, repartos,
pedidos, gastos, reportes y auditoría) para Pollos Lucho.

## Tecnologías

- Python 3 + Flask
- MySQL 8 (PyMySQL) con charset y collation `utf8mb4_unicode_ci`
- Waitress (servidor WSGI) en el puerto 5000
- HTML/CSS/JavaScript (frontend SPA)

## Requisitos e instalación

```bash
pip install -r requirements.txt
```

Configura el acceso a MySQL en un archivo `.env` (NO se sube al repositorio):

```ini
MYSQL_HOST=localhost
MYSQL_USER=admin_inventario      # usuario dedicado de la app
MYSQL_PASS=una-contrasena-fuerte
MYSQL_DB=pollos_lucho
```

En producción crea en MySQL un usuario **dedicado** (no `root`) limitado a la BD:

```sql
CREATE USER 'admin_inventario'@'localhost' IDENTIFIED BY 'una-contrasena-fuerte';
GRANT SELECT, INSERT, UPDATE, DELETE ON pollos_lucho.* TO 'admin_inventario'@'localhost';
```

## Ejecutar

```bash
python run.py          # producción (Waitress + log en servidor.log)
```

Disponible en `http://localhost:5000` (en LAN: `http://<IP-de-esta-PC>:5000`).

La tabla `usuarios` se crea con `admin/123456` como inicio de sesión inicial:
**cámbiala** (Perfil → cambiar contraseña) y crea usuarios para el personal.

## Seguridad incluida

- Credenciales de BD desde `.env` / variables de entorno (nunca hardcodeadas).
- `SECRET_KEY` desde la variable de entorno `SECRET_KEY`, o el archivo `.secret_key` (aleatorio, gitignored).
- Modo debug desactivado (siempre `waitress`, nunca `app.run(debug=True)`).
- Sesiones: cookies firmadas, `HttpOnly`, `SameSite=Lax`, **expiración por inactividad** (8 h) deslizante.
- Límite de **5 intentos fallidos de login** por IP con bloqueo de 15 minutos.
- Todas las consultas usan parámetros preparados (`?` → `%s`), sin concatenar SQL.
- Validación de tipos numéricos en el backend (cantidades/precios) y control de **stock negativo**
  con bloqueos `SELECT ... FOR UPDATE` (entradas y salidas por FEFO en `core/lotes.py`).
- Páginas de error 404/500 amigables (HTML) y JSON para las rutas `/api/*`.
- Confirmación `confirm()` antes de acciones destructivas (eliminar, anular venta/reparto, restaurar BD).
- Botones con protección de doble envío ("Procesando...") en venta, movimiento, reparto, pedido, producto y usuario.
- Headers de seguridad: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`.
- Índices sobre columnas buscadas (nombre de producto, sucursal, fechas, venta/reparto detalle).

## Respaldos

- Exportar (por consola):

  ```bash
  python respaldar.py
  ```

  Genera `respaldo_pollos_lucho_<fecha>.sql` en el directorio del proyecto (UTF-8).
- Verificación de integridad (importa el respaldo en una BD temporal y compara filas):

  ```bash
  python probar_respaldo.py            # usa el último respaldo
  python probar_respaldo.py <archivo.sql>
  ```

- Respaldo automático diario (03:00): ejecuta una vez

  ```powershell
  powershell -ExecutionPolicy Bypass -File programar_respaldo.ps1
  ```

  Instala la tarea `PollosLucho_Respaldo` en el Programador de tareas de Windows.
- **Copia externa**: una vez por semana descarga un `.sql` a otra máquina/nube.
  No guardes SOLO el respaldo en el mismo disco del servidor.

## Operación recomendada (checklist)

- **Zona horaria**: configura el servidor en `America/La_Paz` (o `date.timezone` de MySQL).
  Las fechas se guardan con la hora del servidor; un desfase arruina reportes diarios.
- **HTTPS**: antes de exposición a internet, pon NGINX/Caddy como proxy TLS con certificado
  (Let's Encrypt) y activa en `.env` `COOKIE_SECURE=1`. En LAN cerrada no es imprescindible.
- **Contingencia ante cortes de luz en sucursal**: el sistema NO guarda borradores offline.
  Define un cuaderno de respaldo en la sucursal para anotar ventas durante caídas del servidor.
- **Cierre de caja diario**: compara el inventario teórico del sistema con el físico al cierre.
- **Monitoreo**: revisa periódicamente memoria y disco del VPS (Windows: Administrador de tareas).
  Si `servidor.log` / `server.err.log` crecen demasiado, archívalos con la fecha.
- **Limpieza de datos**: la tabla `auditoria` y los `movimientos` crecen; define una política
  (p. ej. archivar a Excel los meses anteriores y purgar > 24 meses) si el volumen es alto.
- **Impresión**: prueba tickets y reportes en las impresoras térmicas reales de las sucursales.

## Estructura principal

- `app.py` - fábrica de la app (sesiones, headers, errores, logging)
- `run.py` - arranque de producción con Waitress
- `database.py` - conexión MySQL, esquema, migraciones e índices
- `core/` - módulos (auth, productos, movimientos, ventas, pedidos, reportes, ...)
- `templates/`, `static/` - vistas y recursos
- `respaldar.py` / `probar_respaldo.py` / `programar_respaldo.ps1` - respaldos