#!/usr/bin/env bash
#  =====================================================================
#  Respaldo diario de pollos_lucho + subida a la nube (Spaces / Drive)
#  Ejecuta: cd /opt/pollos-lucho && ./deploy/respaldar_respaldo.sh
#  (se ejecuta automáticamente a las 03:00 America/La_Paz via
#   /etc/cron.d/pollos-lucho-respaldo)
#
#  Secuencia: exportar -> verificar integridad -> subir -> limpiar antiguos.
#  Si la verificacion falla, NO se sube nada (un dump que no restaura igual a
#  la base no sirve). Si la subida falla, el script sale con codigo 1 para que
#  quede registrado en logs/respaldo.log.
#  =====================================================================
set -euo pipefail

APP_DIR="/opt/pollos-lucho"
VENV="$APP_DIR/venv"
export RCLONE_CONFIG="$APP_DIR/.rclone.conf"   # config de rclone (usuario pollos sin home)
FECHA=$(date +%Y%m%d_%H%M%S)
# Destino en la nube. El remote es el nombre con el que se configuro rclone:
# ver `sudo -u pollos rclone listremotes`. Tipsicamente "spaces" (DigitalOcean
# Spaces) o "gdrive" (Google Drive). Se puede sobreescribir por entorno.
RCLONE_REMOTE="${RCLONE_REMOTE:-spaces}"
RCLONE_BUCKET="${RCLONE_BUCKET:-RespaldosPollosLucho}"
RETENCION_DIAS="${RETENCION_DIAS:-365}"

cd "$APP_DIR"

echo "[$(date)] === Inicio respaldo $FECHA ==="

# 1) Exportar BD. Se pasa la ruta exacta porque `respaldar.py` sin argumentos
# escribe en respaldos/, y este script (y la retencion de mas abajo) asumen que
# el .sql queda en la raiz del proyecto.
DUMP="respaldo_pollos_lucho_${FECHA}.sql"
"$VENV/bin/python" respaldar.py "$DUMP"

if [ ! -f "$DUMP" ]; then
  echo "ERROR: no se generó $DUMP"
  exit 1
fi
if [ ! -s "$DUMP" ]; then
  echo "ERROR: $DUMP está vacío (0 bytes). No se sube nada."
  exit 1
fi
echo "  → Exportado: $DUMP ($(stat -c%s "$DUMP") bytes)"

# 2) Verificar integridad (probar_respaldo.py)
echo "  → Verificando integridad (probar_respaldo.py)..."
if "$VENV/bin/python" probar_respaldo.py "$DUMP"; then
  echo "  → Integridad OK"
else
  # Un respaldo que no restaura identico no sirve de nada: mejor enterarse y
  # revisarlo que subirlo a la nube creyendose bueno.
  echo "  → ERROR: la verificación de integridad falló. NO se sube el archivo."
  echo "     Revisar el log: el dump de $DUMP no se restaura igual a la base."
  exit 1
fi

# 3) Subir a la nube (si rclone está configurado)
#
# OJO: antes esto不分成败 — si la subida fallaba solo imprimia un AVISO y el
# script terminaba con exito, asi que el log decia "Respaldo completado" y nadie
# se enteraba de que hacia dias que no se subia nada a la nube. Ahora se marca
# FALLO y se devuelve codigo 1, que es lo que un cron debe hacer.
#
# El remote sale de las variables de entorno para poder cambiar entre Spaces,
# Google Drive u otro sin tocar este archivo:
#   RCLONE_REMOTE   (por defecto: el valor de abajo)
#   RCLONE_BUCKET
DESTINO="${RCLONE_REMOTE}:${RCLONE_BUCKET}/pollos-lucho/${DUMP}"

if ! command -v rclone &>/dev/null; then
  echo "  → AVISO: rclone no está instalado. El respaldo quedó solo en disco."
  echo "     Sin copia en la nube: si se pierde el droplet, se pierde la BD."
  SIN_NUBE=1
elif [ ! -f "$RCLONE_CONFIG" ]; then
  echo "  → AVISO: no existe $RCLONE_CONFIG. No se puede subir a la nube."
  SIN_NUBE=1
else
  echo "  → Subiendo: ${DESTINO}"
  if rclone copyto "$DUMP" "$DESTINO" --stats-one-line; then
    echo "  → Subida OK"
  else
    echo "  → FALLO: no se pudo subir a ${RCLONE_REMOTE}."
    echo "     Ver: sudo -u pollos rclone config  (y que RCLONE_REMOTE exista:"
    echo "     sudo -u pollos rclone listremotes)"
    SIN_NUBE=1
  fi
fi

# 4) Retención local: borrar respaldos mayores a RETENCION_DIAS días
echo "  → Limpiando respaldos locales > ${RETENCION_DIAS} días..."
find "$APP_DIR" -name 'respaldo_pollos_lucho_*.sql' -mtime +${RETENCION_DIAS} -delete -print | \
  xargs -r -I{} echo "    eliminado: {}"

# 5) Retención en la nube: borrar respaldos > RETENCION_DIAS días
if [ "${SIN_NUBE:-0}" = "0" ]; then
  echo "  → Limpiando respaldos antiguos en ${RCLONE_REMOTE} (> ${RETENCION_DIAS} días)..."
  rclone delete "${RCLONE_REMOTE}:${RCLONE_BUCKET}/pollos-lucho/" \
    --min-age "${RETENCION_DIAS}d" --stats-one-line || true
fi

if [ "${SIN_NUBE:-0}" = "1" ]; then
  echo "[$(date)] === Respaldo terminado CON FALLO: no se pudo subir a la nube ==="
  exit 1
fi

echo "[$(date)] === Respaldo completado ==="