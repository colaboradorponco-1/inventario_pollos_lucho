import math
from datetime import date, datetime

from flask import Blueprint, request, session

from database import get_conn
from .util import err, login_requerido, ok, responder_excel, sucursal_actual

sazonador_bp = Blueprint("sazonador", __name__)


def _sesion_sazonador():
    return session.get("rol") == "sasonador" and sucursal_actual() is not None


def _almacen_sazonador(conn, sid):
    return conn.execute("""
        SELECT a.id, a.nombre
        FROM almacenes a
        JOIN sucursales s ON s.id = a.sucursal_id
        WHERE a.sucursal_id = ?
          AND LOWER(TRIM(a.nombre)) = LOWER(TRIM(s.nombre))
        ORDER BY a.id
        LIMIT 1
    """, (sid,)).fetchone()


def _numero(valor, nombre, defecto=None):
    if valor in (None, ""):
        return defecto
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        raise ValueError(f"{nombre} debe ser un número")
    if not math.isfinite(numero) or numero < 0:
        raise ValueError(f"{nombre} debe ser un número no negativo")
    return numero


@sazonador_bp.route("/api/sazonador/productos", methods=["GET", "POST"])
@login_requerido
def productos_sazonador():
    if not _sesion_sazonador():
        return err("Esta operación es exclusiva del rol Sazonador con sucursal asignada", 403)
    sid = sucursal_actual()
    conn = get_conn()
    almacen = _almacen_sazonador(conn, sid)
    if not almacen:
        conn.close()
        return err("Crea en la sucursal Sazonador el almacén con el mismo nombre de la sucursal", 409)
    if request.method == "POST":
        data = request.get_json() or {}
        nombre = (data.get("nombre") or "").strip()
        if not nombre or len(nombre) > 255:
            conn.close()
            return err("El nombre del producto es obligatorio y no puede superar 255 caracteres")
        codigo = (data.get("codigo") or "").strip() or None
        unidad = (data.get("unidad") or "unidad").strip() or "unidad"
        try:
            stock_minimo = _numero(data.get("stock_minimo"), "El stock mínimo", 0)
            stock_inicial = _numero(data.get("stock_inicial"), "El stock inicial", 0)
            categoria_id = int(data["categoria_id"]) if data.get("categoria_id") else None
        except (TypeError, ValueError) as exc:
            conn.close()
            return err(str(exc))
        if categoria_id and not conn.execute(
                "SELECT 1 FROM categorias WHERE id = ?", (categoria_id,)).fetchone():
            conn.close()
            return err("La categoría seleccionada no existe")
        if codigo and conn.execute(
                "SELECT 1 FROM sazonador_productos WHERE sucursal_id = ? AND codigo = ?",
                (sid, codigo)).fetchone():
            conn.close()
            return err("Ya tienes un producto con ese código en tu inventario", 409)
        cursor = conn.execute("""
            INSERT INTO sazonador_productos
                (sucursal_id, almacen_id, codigo, nombre, marca, categoria_id, unidad,
                 stock_minimo, stock_actual, vencimiento, creado_por)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (sid, almacen["id"], codigo, nombre, (data.get("marca") or "").strip() or None,
              categoria_id, unidad, stock_minimo, stock_inicial,
              data.get("vencimiento") or None,
              session.get("usuario", "")))
        product_id = cursor.lastrowid
        if not codigo:
            codigo = f"SAZ-{sid}-{product_id:06d}"
            conn.execute("UPDATE sazonador_productos SET codigo = ? WHERE id = ?",
                         (codigo, product_id))
        conn.commit()
        conn.close()
        return ok({"id": product_id, "codigo": codigo},
                  message="Producto agregado al inventario independiente del Sazonador")

    filtro = request.args.get("filtro", "").strip()
    categoria = request.args.get("categoria", "").strip()
    estado = request.args.get("estado", "").strip()
    consulta = """
        SELECT p.id, p.codigo, p.nombre, p.marca, p.categoria_id, p.unidad,
               p.stock_minimo, p.stock_actual AS stock, p.sucursal_id, p.almacen_id,
               p.vencimiento, c.nombre AS categoria_nombre,
               s.nombre AS sucursal_nombre, a.nombre AS almacen_nombre,
               NULL AS proveedor_id, NULL AS proveedor_nombre
        FROM sazonador_productos p
        LEFT JOIN categorias c ON c.id = p.categoria_id
        JOIN almacenes a ON a.id = p.almacen_id
        JOIN sucursales s ON s.id = p.sucursal_id
        WHERE p.sucursal_id = ? AND p.almacen_id = ? AND p.activo = 1
    """
    params = [sid, almacen["id"]]
    if filtro:
        consulta += " AND (p.nombre LIKE ? OR p.codigo LIKE ?)"
        params.extend([f"%{filtro}%", f"%{filtro}%"])
    if categoria:
        consulta += " AND p.categoria_id = ?"
        params.append(int(categoria))
    if estado == "con-stock":
        consulta += " AND p.stock_actual > 0"
    elif estado == "agotado":
        consulta += " AND p.stock_actual <= 0"
    elif estado == "stock-bajo":
        consulta += " AND p.stock_minimo > 0 AND p.stock_actual <= p.stock_minimo"
    elif estado == "por-vencer":
        consulta += " AND p.vencimiento IS NOT NULL AND p.vencimiento <= DATE_ADD(CURDATE(), INTERVAL 14 DAY)"
    consulta += " ORDER BY p.nombre"
    filas = [dict(fila) for fila in conn.execute(consulta, params).fetchall()]
    for fila in filas:
        fila["es_inventario_sazonador"] = True
    conn.close()
    return ok(filas)


@sazonador_bp.route("/api/sazonador/productos/<int:producto_id>", methods=["PUT"])
@login_requerido
def editar_producto_sazonador(producto_id):
    if not _sesion_sazonador():
        return err("Esta operación es exclusiva del rol Sazonador con sucursal asignada", 403)
    sid = sucursal_actual()
    conn = get_conn()
    almacen = _almacen_sazonador(conn, sid)
    if not almacen:
        conn.close()
        return err("Crea en la sucursal Sazonador el almacén con el mismo nombre de la sucursal", 409)
    producto = conn.execute("""
        SELECT id FROM sazonador_productos
        WHERE id = ? AND sucursal_id = ? AND almacen_id = ? AND activo = 1
    """, (producto_id, sid, almacen["id"])).fetchone()
    if not producto:
        conn.close()
        return err("Producto Sazonador no encontrado", 404)

    data = request.get_json() or {}
    nombre = (data.get("nombre") or "").strip()
    codigo = (data.get("codigo") or "").strip() or None
    unidad = (data.get("unidad") or "unidad").strip() or "unidad"
    try:
        stock_minimo = _numero(data.get("stock_minimo"), "El stock mínimo", 0)
        categoria_id = int(data["categoria_id"]) if data.get("categoria_id") else None
    except (TypeError, ValueError) as exc:
        conn.close()
        return err(str(exc))
    if not nombre or len(nombre) > 255:
        conn.close()
        return err("El nombre del producto es obligatorio y no puede superar 255 caracteres")
    if len(unidad) > 50:
        conn.close()
        return err("La unidad no puede superar 50 caracteres")
    if codigo and conn.execute("""
            SELECT 1 FROM sazonador_productos
            WHERE sucursal_id = ? AND codigo = ? AND id <> ?
        """, (sid, codigo, producto_id)).fetchone():
        conn.close()
        return err("Ya tienes un producto con ese código en tu inventario", 409)
    if categoria_id and not conn.execute(
            "SELECT 1 FROM categorias WHERE id = ?", (categoria_id,)).fetchone():
        conn.close()
        return err("La categoría seleccionada no existe")
    vencimiento = data.get("vencimiento") or None
    if vencimiento:
        try:
            vencimiento = datetime.strptime(str(vencimiento)[:10], "%Y-%m-%d").date()
        except ValueError:
            conn.close()
            return err("La fecha de vencimiento no es válida")

    conn.execute("""
        UPDATE sazonador_productos
        SET codigo = ?, nombre = ?, marca = ?, categoria_id = ?, unidad = ?,
            stock_minimo = ?, vencimiento = ?
        WHERE id = ? AND sucursal_id = ? AND almacen_id = ? AND activo = 1
    """, (codigo, nombre, (data.get("marca") or "").strip() or None,
          categoria_id, unidad, stock_minimo, vencimiento,
          producto_id, sid, almacen["id"]))
    conn.commit()
    conn.close()
    return ok({"id": producto_id}, message="Producto Sazonador actualizado")


def _inventario_por_id(conn, inv_id, sid, bloquear=False):
    query = "SELECT * FROM sazonador_inventarios WHERE id = ? AND sucursal_id = ?"
    if bloquear:
        query += " FOR UPDATE"
    return conn.execute(query, (inv_id, sid)).fetchone()


def _inventario_detalle(conn, inv_id):
    return conn.execute("""
        SELECT d.*, c.nombre AS categoria_nombre
        FROM sazonador_inventario_detalle d
        LEFT JOIN categorias c ON c.id = d.categoria_id
        WHERE d.inventario_id = ?
        ORDER BY COALESCE(c.nombre, 'Sin categoría'), d.producto_nombre
    """, (inv_id,)).fetchall()


@sazonador_bp.route("/api/sazonador/inventario-diario", methods=["GET", "POST"])
@login_requerido
def inventario_sazonador():
    if not _sesion_sazonador():
        return err("Esta operación es exclusiva del rol Sazonador con sucursal asignada", 403)
    sid = sucursal_actual()
    conn = get_conn()
    if request.method == "GET":
        almacen = _almacen_sazonador(conn, sid)
        if not almacen:
            conn.close()
            return err("Crea en la sucursal Sazonador el almacén con el mismo nombre de la sucursal", 409)
        filtros = ["i.sucursal_id = ?"]
        params = [sid]
        desde = request.args.get("desde", "").strip()
        hasta = request.args.get("hasta", "").strip()
        busqueda = request.args.get("busqueda", "").strip()
        categoria = request.args.get("categoria_id", "").strip()
        estado = request.args.get("estado", "").strip()
        if desde:
            filtros.append("i.fecha >= ?")
            params.append(desde)
        if hasta:
            filtros.append("i.fecha <= ?")
            params.append(hasta)
        if busqueda:
            filtros.append("(c.nombre LIKE ? OR s.nombre LIKE ?)")
            params.extend([f"%{busqueda}%"] * 2)
        if categoria:
            try:
                filtros.append("i.categoria_id = ?")
                params.append(int(categoria))
            except ValueError:
                conn.close()
                return err("La categoría no es válida")
        if estado:
            filtros.append("i.estado = ?")
            params.append(estado)
        filas = conn.execute("""
            SELECT i.*, c.nombre AS categoria_nombre,
                   s.nombre AS sucursal_nombre,
                   a.nombre AS almacen_nombre,
                   (SELECT COUNT(*) FROM sazonador_inventario_detalle d
                    WHERE d.inventario_id = i.id) AS total_items,
                   (SELECT COUNT(*) FROM sazonador_inventario_detalle d
                    WHERE d.inventario_id = i.id AND d.conteo_fisico IS NULL) AS sin_contar
            FROM sazonador_inventarios i
            LEFT JOIN categorias c ON c.id = i.categoria_id
            JOIN almacenes a ON a.id = i.almacen_id
            JOIN sucursales s ON s.id = i.sucursal_id
            WHERE i.almacen_id = ? AND """ + " AND ".join(filtros) + """
            ORDER BY (i.estado = 'abierto') DESC, i.fecha DESC, c.nombre
        """, [almacen["id"]] + params).fetchall()
        resultado = []
        for row in filas:
            item = dict(row)
            item["fecha"] = str(item["fecha"])[:10]
            if item["estado"] != "cerrado":
                item["contados"] = item["total_items"] - item["sin_contar"]
                item["total_faltantes"] = item["sin_contar"]
                item["total_sobrantes"] = 0
            else:
                item["contados"] = item["total_items"]
            resultado.append(item)
        conn.close()
        return ok(resultado)

    almacen = _almacen_sazonador(conn, sid)
    if not almacen:
        conn.close()
        return err("Crea en la sucursal Sazonador el almacén con el mismo nombre de la sucursal", 409)
    fecha = request.args.get("fecha") or date.today().isoformat()
    try:
        fecha = datetime.strptime(fecha[:10], "%Y-%m-%d").date().isoformat()
        categoria_id = max(0, int(request.args.get("categoria_id") or 0))
    except (TypeError, ValueError):
        conn.close()
        return err("La fecha o categoría de inventario no es válida")
    if fecha > date.today().isoformat():
        conn.close()
        return err("La fecha del inventario no puede estar en el futuro")
    hora = request.args.get("hora") or datetime.now().strftime("%H:%M")
    existente = conn.execute("""
        SELECT id, estado FROM sazonador_inventarios
        WHERE sucursal_id = ? AND almacen_id = ? AND categoria_id = ? AND fecha = ?
    """, (sid, almacen["id"], categoria_id, fecha)).fetchone()
    if existente:
        conn.close()
        if existente["estado"] == "cerrado":
            return err("El inventario de esa categoría y fecha ya está cerrado", 409)
        return ok({"id": existente["id"]}, message="Ya existe un inventario abierto")
    solapada = conn.execute("""
        SELECT id FROM sazonador_inventarios
        WHERE sucursal_id = ? AND almacen_id = ? AND fecha = ?
          AND (categoria_id = 0 OR ? = 0)
        LIMIT 1
    """, (sid, almacen["id"], fecha, categoria_id)).fetchone()
    if solapada:
        conn.close()
        return err("Ya existe una planilla de esa fecha que se superpone con esos productos", 409)
    categoria_nombre = "Todas las categorías"
    if categoria_id:
        cat = conn.execute("SELECT nombre FROM categorias WHERE id = ?",
                           (categoria_id,)).fetchone()
        if not cat:
            conn.close()
            return err("La categoría seleccionada no existe")
        categoria_nombre = cat["nombre"]
    cursor = conn.execute("""
        INSERT INTO sazonador_inventarios
            (sucursal_id, almacen_id, categoria_id, fecha, hora_corte, usuario, estado)
        VALUES (?, ?, ?, ?, ?, ?, 'abierto')
    """, (sid, almacen["id"], categoria_id, fecha, hora, session.get("usuario", "")))
    inv_id = cursor.lastrowid
    productos = conn.execute("""
        SELECT p.*, c.nombre AS categoria_nombre
        FROM sazonador_productos p
        LEFT JOIN categorias c ON c.id = p.categoria_id
        WHERE p.sucursal_id = ? AND p.almacen_id = ? AND p.activo = 1
          AND (? = 0 OR p.categoria_id = ?)
        ORDER BY c.nombre, p.nombre
    """, (sid, almacen["id"], categoria_id, categoria_id)).fetchall()
    for producto in productos:
        stock = float(producto["stock_actual"] or 0)
        conn.execute("""
            INSERT INTO sazonador_inventario_detalle
                (inventario_id, producto_id, categoria_id, producto_nombre,
                 codigo, unidad, stock_sistema, inicial, disponible)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (inv_id, producto["id"], producto["categoria_id"],
              producto["nombre"], producto["codigo"], producto["unidad"],
              stock, stock, stock))
    conn.commit()
    conn.close()
    return ok({"id": inv_id, "estado": "abierto", "fecha": fecha,
               "categoria_nombre": categoria_nombre},
              message="Inventario diario del Sazonador creado")


@sazonador_bp.route("/api/sazonador/inventario-diario/<int:inv_id>",
                    methods=["GET", "PUT", "DELETE"])
@login_requerido
def detalle_inventario_sazonador(inv_id):
    if not _sesion_sazonador():
        return err("Esta operación es exclusiva del rol Sazonador con sucursal asignada", 403)
    sid = sucursal_actual()
    conn = get_conn()
    inv = _inventario_por_id(
        conn, inv_id, sid, bloquear=request.method in ("PUT", "DELETE"))
    if not inv:
        conn.close()
        return err("Inventario no encontrado", 404)
    if request.method == "DELETE":
        if inv["estado"] == "cerrado":
            conn.rollback()
            conn.close()
            return err(
                "La planilla está cerrada. No se puede borrar porque ya actualizó "
                "el inventario del Sazonador.", 400)
        lineas = conn.execute(
            "SELECT COUNT(*) AS n FROM sazonador_inventario_detalle "
            "WHERE inventario_id = ?", (inv_id,)).fetchone()["n"]
        conn.execute(
            "DELETE FROM sazonador_inventario_detalle WHERE inventario_id = ?",
            (inv_id,))
        conn.execute(
            "DELETE FROM sazonador_inventarios WHERE id = ? AND sucursal_id = ?",
            (inv_id, sid))
        conn.commit()
        conn.close()
        return ok({"id": inv_id, "lineas": lineas},
                  message=f"Planilla abierta borrada ({lineas} productos)")
    if request.method == "PUT":
        if inv["estado"] == "cerrado":
            conn.close()
            return err("El inventario está cerrado", 409)
        data = request.get_json() or {}
        for linea in data.get("lineas", []):
            if not isinstance(linea, dict) or not isinstance(linea.get("campos_modificados", []), list):
                continue
            actual = conn.execute("""
                SELECT id FROM sazonador_inventario_detalle
                WHERE id = ? AND inventario_id = ?
            """, (linea.get("id"), inv_id)).fetchone()
            if not actual:
                conn.rollback()
                conn.close()
                return err("Una línea no pertenece a este inventario", 400)
            campos = set(linea.get("campos_modificados", []))
            if "conteo_fisico" in campos:
                try:
                    conteo = _numero(linea.get("conteo_fisico"), "El conteo")
                except ValueError as exc:
                    conn.rollback()
                    conn.close()
                    return err(str(exc))
                conn.execute("""
                    UPDATE sazonador_inventario_detalle
                    SET conteo_fisico = ?, final = ?,
                        diferencia = CASE WHEN ? IS NULL THEN 0 ELSE ? - stock_sistema END,
                        utilizada = CASE WHEN ? IS NULL THEN 0 ELSE GREATEST(inicial - ?, 0) END
                    WHERE id = ? AND inventario_id = ?
                """, (conteo, conteo if conteo is not None else 0,
                      conteo, conteo if conteo is not None else 0,
                      conteo, conteo if conteo is not None else 0,
                      actual["id"], inv_id))
            if "observaciones" in campos:
                conn.execute("""
                    UPDATE sazonador_inventario_detalle SET observaciones = ?
                    WHERE id = ? AND inventario_id = ?
                """, ((linea.get("observaciones") or "").strip(), actual["id"], inv_id))
        if "observaciones" in data:
            conn.execute("UPDATE sazonador_inventarios SET observaciones = ? WHERE id = ?",
                         ((data.get("observaciones") or "").strip(), inv_id))
        if data.get("hora_corte"):
            conn.execute("UPDATE sazonador_inventarios SET hora_corte = ? WHERE id = ?",
                         (data["hora_corte"], inv_id))
        conn.commit()
        conn.close()
        return ok({"id": inv_id}, message="Conteo del Sazonador guardado")

    filas = _inventario_detalle(conn, inv_id)
    lineas = []
    faltantes = []
    sobrantes = []
    for fila in filas:
        diferencia = float(fila["diferencia"] or 0)
        if diferencia < 0:
            faltantes.append(diferencia)
        elif diferencia > 0:
            sobrantes.append(diferencia)
        lineas.append({
            "id": fila["id"], "producto_id": fila["producto_id"],
            "categoria": fila["categoria_nombre"] or "Sin categoría",
            "producto": fila["producto_nombre"], "codigo": fila["codigo"],
            "unidad": fila["unidad"], "stock_sistema": fila["stock_sistema"] or 0,
            "inicial": fila["inicial"] or 0, "ingreso_sistema": 0,
            "ingreso_manual": 0, "ingreso_dia": 0,
            "disponible": fila["disponible"] or 0,
            "conteo_fisico": fila["conteo_fisico"],
            "contado_por": "", "es_de_otro": False,
            "final": fila["final"] or 0, "utilizada": fila["utilizada"] or 0,
            "diferencia": diferencia, "observaciones": fila["observaciones"] or "",
        })
    categoria = conn.execute("SELECT nombre FROM categorias WHERE id = ?",
                             (inv["categoria_id"],)).fetchone() if inv["categoria_id"] else None
    sucursal = conn.execute("SELECT nombre FROM sucursales WHERE id = ?",
                            (sid,)).fetchone()
    almacen = conn.execute("SELECT nombre FROM almacenes WHERE id = ?",
                           (inv["almacen_id"],)).fetchone()
    conn.close()
    return ok({
        "id": inv["id"], "fecha": str(inv["fecha"])[:10],
        "hora_corte": inv["hora_corte"] or "", "sucursal_id": sid,
        "sucursal_nombre": sucursal["nombre"] if sucursal else "",
        "almacen_id": inv["almacen_id"],
        "almacen_nombre": almacen["nombre"] if almacen else "",
        "categoria_id": inv["categoria_id"],
        "categoria_nombre": categoria["nombre"] if categoria else "Todas las categorías",
        "usuario": inv["usuario"] or "", "cerrado_por": inv["cerrado_por"] or "",
        "fecha_hora_cierre": inv["fecha_hora_cierre"] or "",
        "estado": inv["estado"], "puede_editar": True,
        "observaciones": inv["observaciones"] or "", "avisos": [],
        "lineas": lineas,
        "resumen": {
            "total_items": len(lineas), "total_faltantes": len(faltantes),
            "total_sobrantes": len(sobrantes),
            "cantidad_faltante": round(sum(abs(v) for v in faltantes), 3),
            "cantidad_sobrante": round(sum(sobrantes), 3),
            "total_utilizada": round(sum(l["utilizada"] for l in lineas), 3),
        },
    })


@sazonador_bp.route("/api/sazonador/inventario-diario/<int:inv_id>/excel")
@login_requerido
def inventario_sazonador_excel(inv_id):
    if not _sesion_sazonador():
        return err("Esta operación es exclusiva del rol Sazonador con sucursal asignada", 403)
    sid = sucursal_actual()
    conn = get_conn()
    inv = _inventario_por_id(conn, inv_id, sid)
    if not inv:
        conn.close()
        return err("Inventario no encontrado", 404)
    filas = _inventario_detalle(conn, inv_id)
    sucursal = conn.execute(
        "SELECT nombre FROM sucursales WHERE id = ?", (sid,)).fetchone()
    almacen = conn.execute(
        "SELECT nombre FROM almacenes WHERE id = ?", (inv["almacen_id"],)).fetchone()
    categoria = conn.execute(
        "SELECT nombre FROM categorias WHERE id = ?",
        (inv["categoria_id"],)).fetchone() if inv["categoria_id"] else None
    conn.close()

    encabezados = [
        "Categoría", "Producto", "Código", "Unidad", "Inventario inicial",
        "Disponible del día", "Inventario final", "Cantidad utilizada",
        "Diferencia", "Observaciones",
    ]
    filas_excel = [
        (
            fila["categoria_nombre"] or "Sin categoría",
            fila["producto_nombre"] or "",
            fila["codigo"] or "",
            fila["unidad"] or "",
            fila["inicial"] or 0,
            fila["disponible"] or 0,
            fila["final"] or 0,
            fila["utilizada"] or 0,
            fila["diferencia"] or 0,
            fila["observaciones"] or "",
        )
        for fila in filas
    ]
    fecha = str(inv["fecha"])[:10]
    nombre_categoria = categoria["nombre"] if categoria else "Todas las categorías"
    return responder_excel(
        f"inventario_sazonador_{fecha}.xlsx", encabezados, filas_excel,
        [22, 32, 16, 12, 18, 18, 18, 18, 12, 40],
        titulo="PLANILLA DE INVENTARIO DIARIO DEL SAZONADOR",
        subtitulos=[
            sucursal["nombre"] if sucursal else "",
            almacen["nombre"] if almacen else "",
            f"Categoría: {nombre_categoria}",
            f"Fecha: {fecha}",
            f"Hora de corte: {inv['hora_corte'] or ''}",
            f"Estado: {'CERRADA' if inv['estado'] == 'cerrado' else 'ABIERTA'}",
        ],
    )


@sazonador_bp.route("/api/sazonador/inventario-diario/<int:inv_id>/cerrar",
                    methods=["POST"])
@login_requerido
def cerrar_inventario_sazonador(inv_id):
    if not _sesion_sazonador():
        return err("Esta operación es exclusiva del rol Sazonador con sucursal asignada", 403)
    conn = get_conn()
    sid = sucursal_actual()
    inv = _inventario_por_id(conn, inv_id, sid, bloquear=True)
    if not inv:
        conn.rollback()
        conn.close()
        return err("Inventario no encontrado", 404)
    if inv["estado"] == "cerrado":
        conn.rollback()
        conn.close()
        return err("El inventario ya está cerrado", 409)
    filas = _inventario_detalle(conn, inv_id)
    sin_contar = [fila for fila in filas if fila["conteo_fisico"] is None]
    if sin_contar:
        conn.rollback()
        conn.close()
        return err(f"Faltan {len(sin_contar)} productos por contar", 400)
    for fila in filas:
        conn.execute("""
            UPDATE sazonador_productos
            SET stock_actual = ?
                WHERE id = ? AND sucursal_id = ? AND almacen_id = ?
            """, (fila["conteo_fisico"], fila["producto_id"], sid, inv["almacen_id"]))
    faltantes = sum(1 for fila in filas if (fila["diferencia"] or 0) < 0)
    sobrantes = sum(1 for fila in filas if (fila["diferencia"] or 0) > 0)
    conn.execute("""
        UPDATE sazonador_inventarios
        SET estado = 'cerrado', cerrado_por = ?, fecha_hora_cierre = ?,
            total_items = ?, total_faltantes = ?, total_sobrantes = ?
        WHERE id = ? AND sucursal_id = ? AND estado = 'abierto'
    """, (session.get("usuario", ""), datetime.now().isoformat(timespec="seconds"),
          len(filas), faltantes, sobrantes, inv_id, sid))
    conn.commit()
    conn.close()
    return ok({"ajustes": 0, "total_items": len(filas),
               "total_faltantes": faltantes, "total_sobrantes": sobrantes},
              message="Inventario independiente del Sazonador cerrado")
