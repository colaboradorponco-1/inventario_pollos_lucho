#!/usr/bin/env bash
# =====================================================================
#  Respaldo diario de pollos_lucho + subida a Google Drive
#  Ejecuta: cd /opt/pollos-lucho && ./deploy/respaldar_respaldo.sh
#  (se ejecuta automáticamente a las 03:00 via /etc/cron.d/pollos-lucho-respaldo)
# =====================================================================
set -euo pipefail

APP_DIR="/opt/pollos-lucho"
VENV="$APP_DIR/venv"
export RCLONE_CONFIG="$APP_DIR/.rclone.conf"   # config de rclone (usuario pollos sin home)
FECHA=$(date +%Y%m%d_%H%M%S)
RCLONE_REMOTE="gdrive"                 # nombre del remote en rclone (Google Drive)
RCLONE_BUCKET="RespaldosPollosLucho"   # carpeta en Google Drive
RETENCION_DIAS=365

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

# 3) Subir a Google Drive (si rclone está configurado)
if command -v rclone &>/dev/null; then
  echo "  → Subiendo a Google Drive: ${RCLONE_REMOTE}:${RCLONE_BUCKET}/pollos-lucho/${DUMP}"
  rclone copyto "$DUMP" "${RCLONE_REMOTE}:${RCLONE_BUCKET}/pollos-lucho/${DUMP}" --stats-one-line 2>/dev/null || \
    echo "  → AVISO: falló la subida a Google Drive (¿rclone configurado?). Ver: sudo -u pollos rclone config"
else
  echo "  → rclone no instalado, subida a Google Drive omitida"
fi

# 4) Retención local: borrar respaldos mayores a RETENCION_DIAS días
echo "  → Limpiando respaldos locales > ${RETENCION_DIAS} días..."
find "$APP_DIR" -name 'respaldo_pollos_lucho_*.sql' -mtime +${RETENCION_DIAS} -delete -print | \
  xargs -r -I{} echo "    eliminado: {}"

# 5) Retención en Google Drive: borrar respaldos > RETENCION_DIAS días
if command -v rclone &>/dev/null; then
  echo "  → Limpiando respaldos antiguos en Google Drive (> ${RETENCION_DIAS} días)..."
  rclone delete "${RCLONE_REMOTE}:${RCLONE_BUCKET}/pollos-lucho/" \
    --min-age "${RETENCION_DIAS}d" --stats-one-line 2>/dev/null || true
fi

echo "[$(date)] === Respaldo completado ==="