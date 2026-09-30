#!/usr/bin/env bash
# Despliegue automatico de Pollos Lucho en el droplet.
# Se ejecuta via SSH (GitHub Actions o manualmente). Nunca borra:
#   .env, .rclone.conf, .secret_key, venv, respaldos respaldo_*.sql ni logs.
# Antes de tocar los archivos toma un respaldo de la BD (respaldar.py) y
# aborta si no se pudo generar.
set -euo pipefail

AUTO=0
[ "${1:-}" = "--auto" ] && AUTO=1
APP_DIR="/opt/pollos-lucho"
STAGE="/root/pollos-deploy"
VENV="$APP_DIR/venv"
REPO="https://github.com/colaboradorponco-1/inventario_pollos_lucho.git"
SELF="/root/desplegar.sh"
SELF_RAW="https://raw.githubusercontent.com/colaboradorponco-1/inventario_pollos_lucho/main/deploy/desplegar.sh"
# Version del script: sirve para saber desde el log que version corrio en el droplet.
SCRIPT_VERSION="2026-09-30b"

# Auto-actualizacion: si GitHub tiene este script mas nuevo, reemplazarse y reintentar.
tmp_self="$(mktemp)"
if curl -fsSL "$SELF_RAW" -o "$tmp_self" 2>/dev/null && [ -s "$tmp_self" ] && \
   ! diff -q "$tmp_self" "$SELF" >/dev/null 2>&1; then
  mv "$tmp_self" "$SELF"
  chmod +x "$SELF"
  echo "==> desplegar.sh actualizado, reintentando"
  exec bash "$SELF" "$@"
fi
rm -f "$tmp_self"

echo "==> Clonando ultima version ($(date '+%F %T'))"
rm -rf "$STAGE"
git clone --depth 1 --branch main "$REPO" "$STAGE"

HEAD="$(git -C "$STAGE" rev-parse HEAD)"
MARKER="$APP_DIR/.ultimo_deploy"
if [ "$AUTO" = "1" ] && [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = "$HEAD" ] && \
   curl -fsS -o /dev/null --max-time 10 http://127.0.0.1:5000/login 2>/dev/null; then
  echo "==> Sin novedades (HEAD $HEAD), la app ya esta al dia"
  exit 0
fi

# El respaldo lo escribe el usuario del servicio ('pollos'). Si el directorio
# quedo como root (rsync previo, restauracion manual, cron...), el respaldo falla
# con PermissionError y set -e aborta TODO el despliegue aunque la app este bien.
# Por eso los permisos se aseguran ANTES de respaldar, no solo despues del rsync.
echo "==> Script de despliegue $SCRIPT_VERSION ($(date '+%F %T'))"
echo "==> Asegurando permisos del directorio de la app"
mkdir -p "$APP_DIR"
chown pollos:pollos "$APP_DIR"
chmod u+rwx "$APP_DIR"
ls -ld "$APP_DIR" || true

# Respaldo previo: garantiza un punto de restauracion del estado exacto de la
# base justo antes del cambio. Si falla, no se toca la app (set -e).
echo "==> Respaldo previo al despliegue"
sudo -u pollos "$VENV/bin/python" "$APP_DIR/respaldar.py" \
  || { echo "ERROR: el respaldo previo fallo; despliegue abortado"; exit 1; }

# Si falta gunicorn.conf.py (archivo generado, no esta en el repo), restaurarlo.
if [ ! -f "$APP_DIR/gunicorn.conf.py" ] && [ -f "$STAGE/deploy/gunicorn.conf.py" ]; then
  echo "==> Restaurando gunicorn.conf.py (archivo generado)"
  cp "$STAGE/deploy/gunicorn.conf.py" "$APP_DIR/gunicorn.conf.py"
fi

echo "==> Actualizando archivos (preservando secretos y datos)"
rsync -a --delete \
  --exclude='.git' \
  --exclude='.gitignore' \
  --exclude='.env' \
  --exclude='.env.*' \
  --exclude='.rclone.conf' \
  --exclude='.secret_key' \
  --exclude='venv' \
  --exclude='__pycache__' \
  --exclude='respaldo_*.sql' \
  --exclude='*.log' \
  --exclude='gunicorn.conf.py' \
  --exclude='logs' \
  --exclude='.ultimo_deploy' \
  "$STAGE/" "$APP_DIR/"

echo "==> Dependencias de Python"
sudo -u pollos "$VENV/bin/pip" install -r "$APP_DIR/requirements.txt" --quiet \
  || echo "AVISO: no se pudieron actualizar dependencias (se usan las instaladas)"

echo "==> Permisos"
chown -R pollos:pollos "$APP_DIR"

echo "==> Reiniciando servicio"
systemctl restart pollos-lucho

echo "==> Verificando salud"
for i in $(seq 1 15); do
  if curl -fsS -o /dev/null -w "%{http_code}" http://127.0.0.1:5000/login 2>/dev/null | grep -q 200; then
    echo "App OK (HTTP 200)"
    echo "$HEAD" > "$MARKER"
    chown pollos:pollos "$MARKER" 2>/dev/null || true
    exit 0
  fi
  sleep 2
done
echo "ERROR: la app no respondio correctamente tras el despliegue"
systemctl status pollos-lucho --no-pager || true
journalctl -u pollos-lucho -n 30 --no-pager || true
exit 1