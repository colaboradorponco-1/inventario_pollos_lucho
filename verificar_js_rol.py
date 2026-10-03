"""Script de verificacion para los botones de preparador y repartidor en app.js
"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
js_path = 'static/app.js'
s = open(js_path, encoding='utf-8').read()

print("Verificando si app.js tiene soporte para renderizar segun rol...")
has_rol = 'session.rol' in s or 'rol' in s
print("  Uso de rol en JS:", has_rol)
