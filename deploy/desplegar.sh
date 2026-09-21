#!/usr/bin/env bash
# Despliegue automatico de Pollos Lucho en el droplet.
# Se ejecuta via SSH (GitHub Actions o manualmente). Nunca borra:
#   .env, .rclone.conf, .secret_key, venv, respaldos respaldo_*.sql ni logs.
set -euo pipefail

APP_DIR="/opt/pollos-lucho"
STAGE="/root/pollos-deploy"
VENV="$APP_DIR/venv"
REPO="https://github.com/colaboradorponco-1/inventario_pollos_lucho.git"

echo "==> Clonando ultima version ($(date '+%F %T'))"
rm -rf "$STAGE"
git clone --depth 1 --branch main "$REPO" "$STAGE"

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
    exit 0
  fi
  sleep 2
done
echo "ERROR: la app no respondio correctamente tras el despliegue"
systemctl status pollos-lucho --no-pager || true
journalctl -u pollos-lucho -n 30 --no-pager || true
exit 1