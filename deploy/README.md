# Guía de despliegue en DigitalOcean (Ubuntu 24.04)

```
Esquema:
  INTERNET → Caddy (:443 TLS) → Gunicorn (127.0.0.1:5000) → Flask app
                                      ↕
                                MySQL 8 (local)
```

## 0. Requisitos previos
- Droplet Ubuntu 24.04 (≥2 GB RAM / 1 vCPU) — recomendado plan Basic 4 GB (≈24 USD/mes) para user testing.
- Dominio apuntando al IP del droplet (A record). Espera ~10 min a que resuelva: `dig +short inventariopolloslucho.com`.
- SSH al droplet como `root`.

> **¿Y la base de datos?** El droplet instala **MySQL 8.0 LTS local** (utf8mb4, zona
> `America/La_Paz`, usuario dedicado `admin_inventario`, solo accesible desde
> `127.0.0.1`). Para 5 sucursales y este volumen de movimientos es más que
> suficiente y queda bajo tu control: no hace falta el MySQL Managed de DO (~+15 USD/mes).
> Los respaldos diarios siguen yéndose a Spaces para que la BD sea substituible si el
> droplet muere.

## 1. Crear usuario en el droplet y subir archivos
```bash
# En el droplet (como root)
adduser --no-create-home --disabled-password pollos
```

En tu PC local:
```bash
scp -r "C:\Users\escal\OneDrive\Desktop\Inventario-Pollos-Lucho" pollos@<DROPLET_IP>:/tmp/inventario
# (sube TODO el proyecto incluyendo deploy/ y el .sql más reciente)
```

## 2. Ejecutar el instalador (una sola vez)
```bash
# En el droplet (como root)
cd /tmp/inventario/deploy
chmod +x install.sh
# Opción A: sin importar BD (después la importas manualmente)
./install.sh
# Opción B: importar tu respaldo actual ahora
./install.sh /tmp/inventario/respaldo_pollos_lucho_20260912_123138.sql
# Opción C: además declarar el dominio → escribe /etc/caddy/Caddyfile y
# activa COOKIE_SECURE=1 automáticamente (HTTPS con Let's Encrypt)
sudo SITE_DOMAIN=inventariopolloslucho.com bash install.sh /tmp/inventario/respaldo_pollos_lucho_20260912_123138.sql
```
> El script imprime la contraseña MySQL generada **una sola vez** — cópiala a un gestor de contraseñas (Bitwarden, 1Password, etc.) o anótala en un lugar seguro.
> El instalador es idempotente: si lo re-ejecutas con `SITE_DOMAIN` de nuevo, **conserva** la misma contraseña MySQL (lee `.env` ya existente) y solo reconfigura Caddy/HTTPS.

## 3. Configurar HTTPS (Caddy + Let's Encrypt)
> Si usaste la **Opción C** (`SITE_DOMAIN=...`), este paso ya está hecho: el instalador
> escribió `/etc/caddy/Caddyfile`, recargó Caddy y puso `COOKIE_SECURE=1` en `.env`.
> Solo verifica en el paso 5.
1. Edita `/etc/caddy/Caddyfile` con tu dominio:
   ```
   inventariopolloslucho.com {
       reverse_proxy 127.0.0.1:5000
   }
   ```
2. Habilita `COOKIE_SECURE=1` en `/opt/pollos-lucho/.env`:
   ```bash
   sudo sed -i 's/COOKIE_SECURE=0/COOKIE_SECURE=1/' /opt/pollos-lucho/.env
   ```
3. Recarga Caddy:
   ```bash
   systemctl reload caddy
   systemctl status caddy   # debe decir "active (running)"
   ```
4. Prueba en `https://inventariopolloslucho.com` — Caddy obtiene el certificado automáticamente.

> ✅ **Modo LAN (sin dominio)**: si ejecutaste `install.sh` **sin** `SITE_DOMAIN`, esto ya quedó
> automático: Caddy con `:80`, headers de seguridad y `COOKIE_SECURE=0`. Accede por `http://<IP_VPS>:80`.

## 5. Verificaciones post-instalación
```bash
# ¿El servicio arrancó?
systemctl status pollos-lucho

# Login rápido (ver headers de seguridad + login)
curl -s -o /dev/null -w "status=%{http_code}\n" http://127.0.0.1:5000/
curl -s -X POST http://127.0.0.1:5000/api/login \
  -H "Content-Type: application/json" \
  -d '{"usuario":"admin","password":"123456"}'
# Debe devolver {"ok":true,...}

# ¿Gunicorn escuchando?
ss -tlnp | grep 5000

# ¿Caddy activo?
systemctl status caddy
```

## 6. Cambios iniciales (¡Hazlo!)
1. Cambia la contraseña de `admin` → Perfil → Contraseña.
2. Crea los usuarios de cada sucursal con contraseñas únicas.
3. Prueba la tarea programada de respaldo (manual):
   ```bash
   sudo -u pollos /opt/pollos-lucho/deploy/respaldar_respaldo.sh
   ```

## 7. Backups
- **Diario 03:00** se ejecuta automáticamente via cron (`/etc/cron.d/pollos-lucho-respaldo`).
- Guarda copias en DigitalOcean Spaces + en disco (retención 30 días).
- Configura rclone para Spaces: `sudo -u pollos rclone config` (ver `deploy/respaldar_respaldo.sh` para el nombre del remote: `spaces`).
- Crea el bucket Spaces vía la consola DigitalOcean (Settings → Spaces → Create).

## 8. Resumen de archivos
| Archivo | Función |
|---|---|
| `deploy/install.sh` | Instalador idempotente (MySQL, venv, gunicorn, timezone, firewall) |
| `deploy/pollos-lucho.service` | Unidad systemd para Gunicorn |
| `deploy/gunicorn.conf.py` | Configuración de Gunicorn (1 worker × 16 threads; fuerza bruta centralizada) |
| `deploy/Caddyfile.production` | Caddy con TLS (para tu dominio) |
| `deploy/Caddyfile.lan` | Caddy solo HTTP (red interna) |
| `deploy/respaldar_respaldo.sh` | Respaldo + upload a Spaces + retención 30 días |
| `deploy/.env.production.example` | Template del `.env` para el VPS |

## 9. Mantenimiento
- Logs:
  - App: `server.err.log` (errores Flask) + `logs/access.log` + `logs/gunicorn.err`
  - Caddy: `journalctl -u caddy -f`
- Ver respaldos: `ls /opt/pollos-lucho/respaldo_*.sql` o `rclone ls spaces:mi-bucket/backups/`
- Actualizar código: sube los archivos, `systemctl restart pollos-lucho`
- Restaurar respaldo: `mysql -uroot pollos_lucho < respaldo_pollos_lucho_XXXX.sql`