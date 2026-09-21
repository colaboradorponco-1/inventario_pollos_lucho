#!/usr/bin/env bash
# Configuracion UNICA en el droplet para habilitar el despliegue automatico.
# 1) Crea una clave SSH dedicada para GitHub Actions.
# 2) Descarga e instala el script de despliegue.
# 3) Ejecuta un primer despliegue (deja la app actualizada).
# 4) Muestra la llave privada para copiarla a GitHub Secrets.
set -euo pipefail

echo "==> Generando clave SSH para CI/CD (si no existe)"
mkdir -p /root/.ssh
chmod 700 /root/.ssh
if [ ! -f /root/.ssh/pollos_deploy ]; then
  ssh-keygen -t ed25519 -N "" -f /root/.ssh/pollos_deploy -C "github-actions-pollos-lucho"
fi
PUB="$(cat /root/.ssh/pollos_deploy.pub)"
touch /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
grep -qF "$PUB" /root/.ssh/authorized_keys || echo "$PUB" >> /root/.ssh/authorized_keys

echo "==> Instalando script de despliegue"
curl -fsSL -o /root/desplegar.sh \
  https://raw.githubusercontent.com/colaboradorponco-1/inventario_pollos_lucho/main/deploy/desplegar.sh
chmod +x /root/desplegar.sh

echo "==> Primer despliegue (actualiza el droplet a la ultima version)"
bash /root/desplegar.sh

echo ""
echo "======================================================================="
echo "  COPIA LA LLAVE PRIVADA COMPLETA EN GITHUB (repositorio) como secret:"
echo "    DROPLET_SSH_KEY  (todo el contenido del archivo /root/.ssh/pollos_deploy)"
echo ""
echo "  Y crea ademas los secrets:"
echo "    DROPLET_HOST  = 68.183.18.95"
echo "    DROPLET_USER  = root"
echo "======================================================================="
cat /root/.ssh/pollos_deploy