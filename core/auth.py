import time
import unicodedata
from io import BytesIO

from flask import Blueprint, request, send_file, session
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.security import check_password_hash, generate_password_hash

from database import get_conn
from .util import ok, err, login_requerido, registrar_auditoria

auth_bp = Blueprint("auth", __name__)

AVATARES_PERMITIDOS = {"pollito", "gallina", "gallo", "pechuga", "alita"}
AVATAR_IMAGEN_MAX_BYTES = 3 * 1024 * 1024
AVATAR_IMAGEN_MAX_PIXELES = 20_000_000
AVATAR_IMAGEN_TAMANO = (256, 256)


def _normalizar_imagen_perfil(contenido):
    if not contenido or len(contenido) > AVATAR_IMAGEN_MAX_BYTES:
        raise ValueError("La imagen debe pesar menos de 3 MB")
    try:
        origen = Image.open(BytesIO(contenido))
        if origen.format not in ("JPEG", "PNG", "WEBP"):
            raise ValueError("Usa una imagen JPG, PNG o WebP")
        ancho, alto = origen.size
        if (ancho <= 0 or alto <= 0 or ancho * alto > AVATAR_IMAGEN_MAX_PIXELES
                or max(ancho, alto) > 8000):
            raise ValueError("La imagen tiene dimensiones demasiado grandes")
        origen.verify()
        imagen = Image.open(BytesIO(contenido))
        imagen = ImageOps.exif_transpose(imagen)
        if imagen.mode in ("RGBA", "LA") or "transparency" in imagen.info:
            rgba = imagen.convert("RGBA")
            fondo = Image.new("RGB", rgba.size, "white")
            fondo.paste(rgba, mask=rgba.getchannel("A"))
            imagen = fondo
        else:
            imagen = imagen.convert("RGB")
        imagen = ImageOps.fit(imagen, AVATAR_IMAGEN_TAMANO, method=Image.Resampling.LANCZOS)
        salida = BytesIO()
        imagen.save(salida, format="WEBP", quality=82, method=6)
        return salida.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("No se pudo leer la imagen. Selecciona un JPG, PNG o WebP válido") from exc


def _es_sucursal_paz(nombre):
    """True si el nombre de la sucursal corresponde a La Paz (filial).

    Permite que una sucursal de La Paz nunca sea tratada como almacén
    principal, aunque por error quede marcada así en la base."""
    if not nombre:
        return False
    norm = unicodedata.normalize("NFD", str(nombre)).encode("ascii", "ignore").decode().lower()
    return "la paz" in norm

# ---- Protección contra fuerza bruta (por IP): 5 intentos fallidos -> 15 min ----
MAX_INTENTOS = 5
BLOQUEO_MIN = 15
_fallos = {}  # ip -> {"n": contador, "hasta": timestamp_unix}


def _bloqueado():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    fila = _fallos.get(ip)
    if not fila:
        return False, None
    if fila["hasta"] and time.time() < fila["hasta"]:
        return True, int((fila["hasta"] - time.time()) // 60)
    if fila["hasta"] and time.time() >= fila["hasta"]:
        _fallos.pop(ip, None)
        return False, None
    return False, None


def _anotar_fallo():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    fila = _fallos.get(ip, {"n": 0, "hasta": None})
    fila["n"] += 1
    if fila["n"] >= MAX_INTENTOS:
        fila["hasta"] = time.time() + BLOQUEO_MIN * 60
        fila["n"] = 0
    _fallos[ip] = fila


def _limpiar_fallos():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?").split(",")[0].strip()
    _fallos.pop(ip, None)


@auth_bp.route("/api/login", methods=["POST"])
def login():
    bloqueado, minutos = _bloqueado()
    if bloqueado:
        return err(f"Demasiados intentos fallidos. Espera {minutos} min. (tu IP fue bloqueada)", 429)
    data = request.get_json() or {}
    usuario = (data.get("usuario") or "").strip()
    password = data.get("password") or ""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, usuario, password_hash, nombre, rol, sucursal_id, activo, receptor "
        "FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
    sucursal_nombre = ""
    if row and row.get("sucursal_id"):
        s = conn.execute("SELECT nombre FROM sucursales WHERE id = ?", (row["sucursal_id"],)).fetchone()
        sucursal_nombre = s["nombre"] if s else ""
    conn.close()
    if not row or not check_password_hash(row["password_hash"], password):
        _anotar_fallo()
        return err("Usuario o contraseña incorrectos")
    if not row["activo"]:
        _anotar_fallo()
        return err("Usuario desactivado. Contacta al administrador")
    _limpiar_fallos()
    session.permanent = True  # respeta PERMANENT_SESSION_LIFETIME
    session["_ultima_actividad"] = int(time.time())
    session["user_id"] = row["id"]
    session["usuario"] = row["usuario"]
    session["nombre"] = row["nombre"]
    session["rol"] = row["rol"]
    session["sucursal_id"] = row["sucursal_id"]
    session["sucursal_nombre"] = sucursal_nombre
    session["receptor"] = bool(row.get("receptor"))
    registrar_auditoria("Inicio de sesión", f"Usuario {row['usuario']} ingresó al sistema")
    return ok({"usuario": row["usuario"], "nombre": row["nombre"], "rol": row["rol"],
               "sucursal_id": row["sucursal_id"],
               "sucursal_nombre": sucursal_nombre, "receptor": bool(row.get("receptor"))},
              message="Bienvenido")


@auth_bp.route("/api/logout", methods=["POST"])
def logout():
    registrar_auditoria("Cierre de sesión", f"Usuario {session.get('usuario', '')} salió del sistema")
    session.clear()
    return ok(message="Sesión cerrada")


@auth_bp.route("/api/sesion")
def sesion():
    if "user_id" not in session:
        return err("Sin sesión", 401)
    principal = False
    sid = session.get("sucursal_id")
    nombre_suc = session.get("sucursal_nombre", "")
    if sid:
        conn = get_conn()
        fila = conn.execute("SELECT principal FROM sucursales WHERE id = ?", (sid,)).fetchone()
        conn.close()
        principal = bool(fila and fila["principal"])
    es_la_paz = _es_sucursal_paz(nombre_suc)
    if es_la_paz:
        principal = False  # La Paz es filial: nunca es almacén principal
    conn = get_conn()
    fila_avatar = conn.execute(
        "SELECT avatar FROM usuarios WHERE id = ?", (session["user_id"],)).fetchone()
    conn.close()
    return ok({"usuario": session.get("usuario"), "nombre": session.get("nombre"),
               "rol": session.get("rol"), "sucursal_id": sid,
               "sucursal_nombre": session.get("sucursal_nombre", ""),
               "avatar": (fila_avatar or {}).get("avatar") or "pollito",
               "sucursal_principal": principal,
               "es_la_paz": es_la_paz,
               "receptor": bool(session.get("receptor")),
               "superadmin": session.get("rol") == "superadmin"})


@auth_bp.route("/api/cambiar_password", methods=["POST"])
@login_requerido
def cambiar_password():
    data = request.get_json() or {}
    actual = data.get("actual") or ""
    nueva = data.get("nueva") or ""
    if len(nueva) < 4:
        return err("La nueva contraseña debe tener al menos 4 caracteres")
    conn = get_conn()
    row = conn.execute(
        "SELECT password_hash FROM usuarios WHERE id = ?", (session["user_id"],)).fetchone()
    if not row or not check_password_hash(row["password_hash"], actual):
        conn.close()
        return err("La contraseña actual es incorrecta")
    conn.execute("UPDATE usuarios SET password_hash = ? WHERE id = ?",
                 (generate_password_hash(nueva), session["user_id"]))
    conn.commit()
    conn.close()
    registrar_auditoria("Cambio de contraseña", f"Usuario {session['usuario']} cambió su contraseña")
    return ok(message="Contraseña actualizada")


@auth_bp.route("/api/perfil", methods=["PUT"])
@login_requerido
def editar_perfil():
    data = request.get_json() or {}
    nombre = (data.get("nombre") or "").strip()
    actual = data.get("actual") or ""
    nueva = data.get("nueva") or ""
    if not nombre:
        return err("El nombre es obligatorio")
    conn = get_conn()
    row = conn.execute(
        "SELECT id, password_hash FROM usuarios WHERE id = ?", (session["user_id"],)).fetchone()
    if not row:
        conn.close()
        return err("Usuario no encontrado", 404)
    if nueva:
        if len(nueva) < 4:
            conn.close()
            return err("La nueva contraseña debe tener al menos 4 caracteres")
        if not check_password_hash(row["password_hash"], actual):
            conn.close()
            return err("La contraseña actual es incorrecta")
        conn.execute("UPDATE usuarios SET nombre = ?, password_hash = ? WHERE id = ?",
                     (nombre, generate_password_hash(nueva), session["user_id"]))
        session["nombre"] = nombre
        registrar_auditoria("Edición de perfil", f"Usuario {session['usuario']} actualizó nombre y contraseña")
    else:
        conn.execute("UPDATE usuarios SET nombre = ? WHERE id = ?", (nombre, session["user_id"]))
        session["nombre"] = nombre
        registrar_auditoria("Edición de perfil", f"Usuario {session['usuario']} actualizó su nombre")
    conn.commit()
    conn.close()
    return ok(message="Perfil actualizado")


@auth_bp.route("/api/perfil/avatar", methods=["PUT"])
@login_requerido
def editar_avatar_perfil():
    conn = get_conn()
    user_id = session["user_id"]
    if request.mimetype == "multipart/form-data":
        if request.content_length and request.content_length > AVATAR_IMAGEN_MAX_BYTES + 65536:
            conn.close()
            return err("La imagen debe pesar menos de 3 MB", 413)
        archivo = request.files.get("imagen")
        if not archivo or not archivo.filename:
            conn.close()
            return err("Selecciona una imagen")
        contenido = archivo.stream.read(AVATAR_IMAGEN_MAX_BYTES + 1)
        try:
            imagen = _normalizar_imagen_perfil(contenido)
        except ValueError as exc:
            conn.close()
            return err(str(exc), 400)
        conn.execute(
            "UPDATE usuarios SET avatar = 'foto', avatar_imagen = ? WHERE id = ?",
            (imagen, user_id))
        avatar = "foto"
    else:
        data = request.get_json(silent=True) or {}
        avatar = data.get("avatar")
        if avatar not in AVATARES_PERMITIDOS:
            conn.close()
            return err("Elige uno de los avatares disponibles", 400)
        conn.execute(
            "UPDATE usuarios SET avatar = ?, avatar_imagen = NULL WHERE id = ?",
            (avatar, user_id))
    conn.commit()
    conn.close()
    registrar_auditoria("Avatar actualizado", f"Usuario {session.get('usuario', '')}")
    return ok({"avatar": avatar}, message="Foto de perfil actualizada")


@auth_bp.route("/api/perfil/avatar/imagen")
@login_requerido
def imagen_avatar_perfil():
    conn = get_conn()
    fila = conn.execute(
        "SELECT avatar, avatar_imagen FROM usuarios WHERE id = ?",
        (session["user_id"],)).fetchone()
    conn.close()
    if not fila or fila.get("avatar") != "foto" or not fila.get("avatar_imagen"):
        return err("No hay una foto de perfil", 404)
    respuesta = send_file(BytesIO(fila["avatar_imagen"]), mimetype="image/webp",
                          max_age=0, download_name="avatar.webp")
    respuesta.headers["Cache-Control"] = "private, no-store"
    respuesta.headers["X-Content-Type-Options"] = "nosniff"
    return respuesta
