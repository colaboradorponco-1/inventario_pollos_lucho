# Inventario Pollos Lucho

Sistema de inventario para gestión de productos, movimientos, ventas, sucursales y reportes.

## Tecnologías

- Python
- Flask
- SQLite
- HTML/CSS/JavaScript

## Requisitos

```bash
pip install -r requirements.txt
```

## Ejecutar la aplicación

```bash
python app.py
```

La aplicación quedará disponible en:

- http://localhost:5000

## Estructura principal

- `app.py` - punto de entrada
- `database.py` - base de datos y migraciones
- `core/` - módulos del sistema
- `templates/` - plantillas HTML
- `static/` - archivos estáticos

## Nota

La base de datos local se genera automáticamente al iniciar la aplicación.
