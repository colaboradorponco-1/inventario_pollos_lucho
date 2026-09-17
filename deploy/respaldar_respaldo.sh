#!/usr/bin/env bash
# =====================================================================
#  Respaldo diario de pollos_lucho + subida a DigitalOcean Spaces
#  Ejecuta: cd /opt/pollos-lucho && ./deploy/respaldar_respaldo.sh
#  (se ejecuta automáticamente a las 03:00 via /etc/cron.d/pollos-lucho-respaldo)
# =====================================================================
set -euo pipefail

APP_DIR="/opt/pollos-lucho"
VENV="$APP_DIR/venv"
FECHA=$(date +%Y%m%d_%H%M%S)
RCLONE_REMOTE="spaces"          # nombre del remote en rclone (ver abajo)
RCLOME_BUCKET="backups-pollos-lucho"
RETENCION_DIAS=30

cd "$APP_DIR"

echo "[$(date)] === Inicio respaldo $FECHA ==="

# 1) Exportar BD
"$VENV/bin/python" respaldar.py
DUMP="respaldo_pollos_lucho_${FECHA}.sql"

if [ ! -f "$DUMP" ]; then
  echo "ERROR: no se generó $DUMP"
  exit 1
fi
echo "  → Exportado: $DUMP ($(stat -c%s "$DUMP") bytes)"

# 2) Verificar integridad
echo "  → Verificando integridad (probar_respaldo.py)..."
"$VENV/bin/python" probar_respaldo.py && echo "  → Integrity OK" || echo "  → AVISO: verificación falló (revisar logs)"

# 3) Subir a Spaces (si rclone está configurado)
if command -v rclone &>/dev/null; then
  echo "  → Subiendo a Spaces: ${RCLONE_REMOTE}:${RCLOME_BUCKET}/pollos-lucho/${DUMP}"
  rclone copy "$DUMP" "${RCLONE_REMOTE}:${RCLOME_BUCKET}/pollos-lucho/${DUMP}" --stats-one-line 2>/dev/null || \
    echo "  → AVISO: falló la subida a Spaces (¿rclone configurado?). Ver: sudo -u pollos rclone config"
else
  echo "  → rclone no instalado, subida a Spaces omitida"
fi

# 4) Retención local: borrar respaldos mayores a RETENCION_DIAS días
echo "  → Limpiando respaldos locales > ${RETENCION_DIAS} días..."
find "$APP_DIR" -name 'respaldo_pollos_lucho_*.sql' -mtime +${RETENCION_DIAS} -delete -print | \
  xargs -r -I{} echo "    eliminado: {}"

# 5) Retención en Spaces: borrar respaldos > RETENCION_DIAS días
if command -v rclone &>/dev/null; then
  echo "  → Limpiando respaldos antiguos en Spaces (> ${RETENCION_DIAS} días)..."
  rclone delete "${RCLONE_REMOTE}:${RCLOME_BUCKET}/pollos-lucho/" \
    --min-age "${RETENCION_DIAS}d" --stats-one-line 2>/dev/null || true
fi

echo "[$(date)] === Respaldo completado ==="