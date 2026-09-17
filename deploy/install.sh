#!/usr/bin/env bash
# =====================================================================
#  Instalador idempotente de Pollos Lucho para Ubuntu 24.04 (DigitalOcean)
#  Uso:  sudo bash install.sh [ruta_al_respaldo.sql]
# =====================================================================
set -euo pipefail

APP_DIR="/opt/pollos-lucho"
VENV="$APP_DIR/venv"
DB_NAME="pollos_lucho"
DB_USER="admin_inventario"
# Reutiliza la contraseña ya existente en .env (para que re-ejecutar el instalador
# no rompa MySQL con contraseñas cambiantes); si no existe, genera una nueva.
if [ -f "/opt/pollos-lucho/.env" ] && grep -q '^MYSQL_PASS=' "/opt/pollos-lucho/.env"; then
  DB_PASS=$(grep '^MYSQL_PASS=' "/opt/pollos-lucho/.env" | cut -d= -f2-)
else
  DB_PASS=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 28)
fi
DUMP="${1:-}"
# Óptimo: https://tudominio.com (dejar vacío instala en modo LAN). Tú lo editas:
# este valor solo se usa UNA vez para escribir /etc/caddy/Caddyfile.
SITE_DOMAIN="${SITE_DOMAIN:-}"

echo "=== Pollos Lucho – instalador ==="

# -------------------------------------------------------------------
# 1) Dependencias del sistema
# -------------------------------------------------------------------
echo "[1/9] Dependencias del sistema..."
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip \
  mysql-server git curl wget rsync sudo

# -------------------------------------------------------------------
# 2) Zona horaria America/La_Paz
# -------------------------------------------------------------------
echo "[2/9] Configurando zona horaria..."
ln -sf /usr/share/zoneinfo/America/La_Paz /etc/localtime
timedatectl set-timezone America/La_Paz
cat > /etc/mysql/conf.d/timezone.cnf <<EOF
[mysqld]
default-time-zone = '-04:00'
EOF
systemctl restart mysql || true

# -------------------------------------------------------------------
# 3) Usuario de sistema 'pollos' (sin login)
# -------------------------------------------------------------------
echo "[3/9] Usuario pollos..."
if ! id pollos &>/dev/null; then
  useradd --no-create-home --shell /usr/sbin/nologin pollos
fi

# -------------------------------------------------------------------
# 4) Crear venv e instalar requirements
# -------------------------------------------------------------------
echo "[4/9] Entorno virtual e instalación de dependencias..."
if [ ! -d "$APP_DIR" ]; then
  mkdir -p "$APP_DIR"
fi

# Copiar archivos del proyecto (se asume que el script está dentro de deploy/)
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
rsync -a --exclude='.git' --exclude='__pycache__' --exclude='venv' \
  --exclude='.env' --exclude='.secret_key' --exclude='logs' \
  --exclude='respaldo_*.sql' --exclude='servidor.log' --exclude='server.err.log' \
  "$SRC_DIR/" "$APP_DIR/"

# Propiedad para el usuario 'pollos' YA (el venv se crea como pollos)
chown -R pollos:pollos "$APP_DIR"
chmod -R u+rwX "$APP_DIR"

if [ ! -d "$VENV" ]; then
  sudo -u pollos python3 -m venv "$VENV"
fi
sudo -u pollos "$VENV/bin/pip" install --upgrade pip --quiet
sudo -u pollos "$VENV/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet

mkdir -p "$APP_DIR/logs"
chown -R pollos:pollos "$APP_DIR"

# -------------------------------------------------------------------
# 5) MySQL: BD + usuario dedicado
# -------------------------------------------------------------------
echo "[5/9] Configurando MySQL..."
mysql -uroot <<SQL
CREATE DATABASE IF NOT EXISTS ${DB_NAME} DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE DATABASE IF NOT EXISTS ${DB_NAME}_test DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
ALTER USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASS}';
GRANT ALL PRIVILEGES ON ${DB_NAME}.* TO '${DB_USER}'@'localhost';
GRANT ALL PRIVILEGES ON ${DB_NAME}_test.* TO '${DB_USER}'@'localhost';
FLUSH PRIVILEGES;
SQL

# -------------------------------------------------------------------
# 6) Archivo .env (credenciales de la app)
# -------------------------------------------------------------------
echo "[6/9] Generando .env..."
cat > "$APP_DIR/.env" <<EOF
MYSQL_HOST=localhost
MYSQL_USER=${DB_USER}
MYSQL_PASS=${DB_PASS}
MYSQL_DB=${DB_NAME}
COOKIE_SECURE=0
EOF
chown pollos:pollos "$APP_DIR/.env"
chmod 640 "$APP_DIR/.env"

# -------------------------------------------------------------------
# 7) Clave secreta (sesiones firmadas)
# -------------------------------------------------------------------
echo "[7/9] Generando .secret_key..."
if [ ! -f "$APP_DIR/.secret_key" ]; then
  openssl rand -base64 32 > "$APP_DIR/.secret_key"
fi
chown pollos:pollos "$APP_DIR/.secret_key"
chmod 640 "$APP_DIR/.secret_key"

# -------------------------------------------------------------------
# 8) Importar respaldo SQL (opcional)
# -------------------------------------------------------------------
if [ -n "$DUMP" ] && [ -f "$DUMP" ]; then
  echo "[8/9] Creando esquema (init_db) e importando respaldo $DUMP..."
  # El dump solo trae datos (TRUNCATE + INSERT); las tablas las crea init_db()
  sudo -u pollos "$VENV/bin/python" -c "from app import create_app; create_app()"
  mysql -uroot "$DB_NAME" < "$DUMP"
  echo "  → Importación completada."
else
  echo "[8/9] Sin respaldo para importar (omitido)."
  echo "  Las tablas se crean automáticamente al arrancar el servicio."
fi

# -------------------------------------------------------------------
# 9) Gunicorn + systemd + Caddy
# -------------------------------------------------------------------
echo "[9/9] Configurando servicios..."

# gunicorn.conf.py
cat > "$APP_DIR/gunicorn.conf.py" <<GINI
bind = "127.0.0.1:5000"
workers = 1
worker_class = "gthread"
threads = 16
timeout = 120
keepalive = 5
preload_app = True
errorlog = "${APP_DIR}/logs/gunicorn.err"
accesslog = "${APP_DIR}/logs/access.log"
loglevel = "info"
GINI
chown pollos:pollos "$APP_DIR/gunicorn.conf.py"

# systemd service
cat > /etc/systemd/system/pollos-lucho.service <<SVC
[Unit]
Description=Sistema Inventario Pollos Lucho
After=network.target mysql.service
Wants=mysql.service

[Service]
User=pollos
Group=pollos
WorkingDirectory=${APP_DIR}
ExecStart=${VENV}/bin/gunicorn -c gunicorn.conf.py "app:create_app()"
Restart=on-failure
RestartSec=5
Environment=HOME=${APP_DIR}
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
Environment=TZ=America/La_Paz

[Install]
WantedBy=multi-user.target
SVC

systemctl daemon-reload
systemctl enable --now pollos-lucho

# cron diario de respaldo
cat > /etc/cron.d/pollos-lucho-respaldo <<CRON
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
TZ=America/La_Paz
0 3 * * * pollos ${APP_DIR}/deploy/respaldar_respaldo.sh >> ${APP_DIR}/logs/respaldo.log 2>&1
CRON
chmod 644 /etc/cron.d/pollos-lucho-respaldo

# Caddy (instalar si no existe)
if ! command -v caddy &>/dev/null; then
  apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/setup.deb.sh' | bash -
  apt-get install -y caddy
fi

# -------------------------------------------------------------------
# 10) Caddyfile (según el modo: dominio → HTTPS; si no → LAN :80)
# -------------------------------------------------------------------
if [ -n "$SITE_DOMAIN" ]; then
  echo "Configurando Caddy para dominio: $SITE_DOMAIN ..."
  cat > /etc/caddy/Caddyfile <<CADDY
${SITE_DOMAIN} {
    reverse_proxy 127.0.0.1:5000
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options SAMEORIGIN
        Referrer-Policy same-origin
        -Server
    }
}
CADDY
  # En producción las cookies deben ser Secure (solo HTTPS)
  sed -i 's/^COOKIE_SECURE=.*/COOKIE_SECURE=1/' "$APP_DIR/.env"
  echo "  → HTTPS configurado para ${SITE_DOMAIN} y COOKIE_SECURE=1 aplicado"
else
  echo "Configurando Caddy en modo LAN (acceso por IP, HTTP)..."
  cat > /etc/caddy/Caddyfile <<CADDY
:80 {
    encode zstd gzip
    reverse_proxy 127.0.0.1:5000
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options SAMEORIGIN
        Referrer-Policy same-origin
        -Server
    }
}
CADDY
  echo "  → LAN :80 listo. Cuando tengas dominio, re-ejecuta:"
  echo "    sudo SITE_DOMAIN=tudominio.com bash $APP_DIR/deploy/install.sh"
fi
systemctl reload caddy || true

# Ejecutables para el cron y uso manual
chmod +x "$APP_DIR/deploy/respaldar_respaldo.sh" "$APP_DIR/deploy/install.sh" || true

# firewall: abrir SSH, HTTP, HTTPS
if command -v ufw &>/dev/null; then
  ufw allow OpenSSH
  ufw allow 80/tcp
  ufw allow 443/tcp
  echo "y" | ufw enable
fi

echo ""
echo "=== INSTALACION COMPLETADA ==="
echo "  App        : Gunicorn 1 worker × 16 threads (gthread)"
echo "  MySQL user : ${DB_USER}"
echo "  MySQL pass : ${DB_PASS}  ← GUARDA ESTA CONTRASEÑA EN LUGAR SEGURO"
echo "  Dominio    : ${SITE_DOMAIN:-"(no configurado, modo LAN)"}"
echo ""
echo "Siguientes pasos:"
echo "  1. Crea un bucket Spaces (privado, misma región del droplet, nombre 'backups-pollos-lucho')"
echo "  2. Configura rclone: sudo -u pollos rclone config → provider=digitalocean, access/secret keys"
echo "  3. (Opcional) Dominio → configura DNS apuntando a la IP del droplet"
echo "     Luego: sudo SITE_DOMAIN=tudominio.com bash deploy/install.sh  (re-ejecuta solo para Caddyfile)"
echo "     O edita manualmente: /etc/caddy/Caddyfile"
echo "  4. systemctl reload caddy"
echo "  5. En /opt/pollos-lucho/.env → cambia COOKIE_SECURE=0 a COOKIE_SECURE=1 (en producción)"
echo "  6. Admin: visítalo → https://<IP o dominio> → login admin / 123456 → CAMBIA LA CONTRASEÑA"
echo "  7. Respaldo manual: sudo -u pollos ${APP_DIR}/deploy/respaldar_respaldo.sh"