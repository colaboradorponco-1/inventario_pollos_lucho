"""Pruebas del archivo reversible y separado por sucursal para pedidos."""
import io
import inspect
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, session  # noqa: E402

import core.pedidos as ped  # noqa: E402

CAPTURADO = []
ESTADO_PEDIDO = "entregado"
SUCURSAL_PRINCIPAL = 0
ARCHIVADO_EN_SUCURSAL = True


class FakeConn:
    def execute(self, sql, params=None):
        CAPTURADO.append((" ".join(str(sql).split()), list(params or [])))
        return self

    def fetchone(self):
        sql = CAPTURADO[-1][0] if CAPTURADO else ""
        if "SELECT principal, nombre FROM sucursales" in sql:
            return {"principal": SUCURSAL_PRINCIPAL, "nombre": "Almacen Principal 1"}
        if "SELECT * FROM pedidos WHERE id" in sql:
            return {
                "id": 7,
                "nro_ticket": "TKT-00007",
                "estado": ESTADO_PEDIDO,
                "sucursal_id": 42,
                "destino_id": 30,
            }
        if "SELECT id FROM pedidos_archivados" in sql:
            return {"id": 1} if ARCHIVADO_EN_SUCURSAL else None
        return {"principal": 0, "nombre": "", "c": 0}

    def fetchall(self):
        if "SELECT destino_id FROM pedido_detalle" in CAPTURADO[-1][0]:
            return [{"destino_id": 30}]
        return []

    def commit(self):
        pass

    def close(self):
        pass


ped.get_conn = lambda *args, **kwargs: FakeConn()
ped.registrar_auditoria = lambda *args, **kwargs: None

srv = Flask(__name__)
srv.secret_key = "prueba"


def ejecutar_bandeja(vista, rol="admin", sucursal_id=30, alcance="mi-almacen"):
    CAPTURADO.clear()
    with srv.test_request_context(
            f"/api/pedidos/bandeja?vista={vista}&alcance={alcance}"):
        session["user_id"] = 1
        session["rol"] = rol
        session["sucursal_id"] = sucursal_id
        ped.pedidos_bandeja()
    return next(sql for sql, _ in CAPTURADO if "FROM pedidos p JOIN sucursales" in sql)


def ejecutar_vista_invalida():
    with srv.test_request_context("/api/pedidos/bandeja?vista=desconocida"):
        session["user_id"] = 1
        session["rol"] = "admin"
        session["sucursal_id"] = 30
        return ped.pedidos_bandeja()


def ejecutar_listado(archivados=False):
    CAPTURADO.clear()
    query = "/api/pedidos?archivados=1" if archivados else "/api/pedidos"
    with srv.test_request_context(query):
        session["user_id"] = 1
        session["rol"] = "admin"
        session["sucursal_id"] = 30
        ped.pedidos()
    principal = next(sql for sql, _ in CAPTURADO if "SELECT p.*" in sql)
    conteo = next(sql for sql, _ in CAPTURADO
                  if sql.startswith("SELECT COUNT(*) AS c FROM pedidos p"))
    parametros_principal = next(params for sql, params in CAPTURADO if sql == principal)
    parametros_conteo = next(params for sql, params in CAPTURADO if sql == conteo)
    return principal, conteo, parametros_principal, parametros_conteo


def ejecutar_creacion(rol, sucursal_id=30, principal=0):
    global SUCURSAL_PRINCIPAL
    SUCURSAL_PRINCIPAL = principal
    with srv.test_request_context("/api/pedidos", method="POST", json={}):
        session["user_id"] = 1
        session["rol"] = rol
        session["sucursal_id"] = sucursal_id
        return ped.pedidos()


def ejecutar_archivo(estado, archivado, rol="admin", sucursal_id=30):
    global ESTADO_PEDIDO
    ESTADO_PEDIDO = estado
    CAPTURADO.clear()
    with srv.test_request_context(
            "/api/pedidos/7/archivar", method="PUT", json={"archivado": archivado}):
        session["user_id"] = 1
        session["rol"] = rol
        session["sucursal_id"] = sucursal_id
        return ped.pedido_archivar(7)


def ejecutar_eliminar(archivado=True, estado="entregado", sucursal_id=30):
    global ARCHIVADO_EN_SUCURSAL, ESTADO_PEDIDO
    ARCHIVADO_EN_SUCURSAL = archivado
    ESTADO_PEDIDO = estado
    CAPTURADO.clear()
    with srv.test_request_context("/api/pedidos/7", method="DELETE"):
        session["user_id"] = 1
        session["rol"] = "admin"
        session["sucursal_id"] = sucursal_id
        return ped.pedido_eliminar_archivado(7)


fallos = 0


def check(nombre, condicion):
    global fallos
    if not condicion:
        fallos += 1
    print(f"  {'OK' if condicion else 'FALLA'} {nombre}")


print("=" * 68)
print("ARCHIVO REVERSIBLE DE PEDIDOS")
print("=" * 68)

sql_activos = ejecutar_bandeja("activos")
sql_historial = ejecutar_bandeja("historial")
sql_archivados = ejecutar_bandeja("archivados")
check("la bandeja activa solo consulta pendiente y en camino",
      "p.estado IN ('pendiente', 'en_camino')" in sql_activos)
check("el historial omite archivos de la sucursal actual",
      "NOT EXISTS (SELECT 1 FROM pedidos_archivados" in sql_historial
      and "pa.sucursal_id = ?" in sql_historial)
check("la vista archivados se limita a la sucursal actual",
      "EXISTS (SELECT 1 FROM pedidos_archivados" in sql_archivados
      and "pa.sucursal_id = ?" in sql_archivados)
check("una vista no reconocida devuelve error",
      isinstance(ejecutar_vista_invalida(), tuple))
sql_superadmin = ejecutar_bandeja("activos", rol="superadmin", alcance="mi-almacen")
check("superadmin por defecto queda limitado a su almacén",
      "dd.destino_id = ?" in sql_superadmin)
sql_global = ejecutar_bandeja("activos", rol="superadmin", alcance="todas")
check("superadmin puede elegir la vista global",
      "dd.destino_id = ?" not in sql_global)
with srv.test_request_context("/api/pedidos/bandeja?vista=activos&alcance=todas"):
    session["user_id"] = 1
    session["rol"] = "admin"
    session["sucursal_id"] = 30
    vista_global_no_autorizada = ped.pedidos_bandeja()
check("otros roles no pueden solicitar la vista global",
      isinstance(vista_global_no_autorizada, tuple))
check("admin no puede crear solicitudes",
      isinstance(ejecutar_creacion("admin"), tuple))
check("encargado de almacén no puede crear solicitudes",
      isinstance(ejecutar_creacion("encargado", principal=1), tuple))
check("superadmin no puede crear solicitudes",
      isinstance(ejecutar_creacion("superadmin"), tuple))
fuente_etapa = inspect.getsource(ped.pedido_etapa)
check("encargado de almacén puede gestionar etapas",
      "es_encargado_almacen(conn)" in fuente_etapa
      and "_AVANCE_ORDENANTE.get(actual_etapa)" in fuente_etapa)
with open(os.path.join(os.path.dirname(__file__), "static", "app.js"),
          encoding="utf-8") as archivo:
    interfaz = archivo.read()
check("el alcance global se elige desde el control exclusivo del superadmin",
      "pedido-bandeja-alcance" in interfaz and "Todas las sucursales" in interfaz)
check("el filtro por botones lee la propiedad data-bsuc correctamente",
      "bandejaSucF = b.dataset.bsuc" in interfaz
      and "b.dataset.bSuc" not in interfaz)
check("mis solicitudes también permiten archivar estados terminales",
      "['entregado', 'rechazado'].includes(estado)" in interfaz
      and "await listarPedidos()" in interfaz)
listado_activo, conteo_activo, params_activo, params_conteo_activo = ejecutar_listado()
check("el listado normal oculta pedidos archivados de la sucursal",
      "NOT EXISTS (SELECT 1 FROM pedidos_archivados" in listado_activo
      and "pa.sucursal_id = ?" in listado_activo)
check("el conteo normal usa el mismo filtro de archivo y parámetros",
      "NOT EXISTS (SELECT 1 FROM pedidos_archivados" in conteo_activo
      and params_activo[:-2] == params_conteo_activo)
listado_archivado, conteo_archivado, params_archivado, params_conteo_archivado = ejecutar_listado(True)
check("la vista archivada muestra solo pedidos archivados por esa sucursal",
      "AND EXISTS (SELECT 1 FROM pedidos_archivados" in listado_archivado
      and "AND EXISTS (SELECT 1 FROM pedidos_archivados" in conteo_archivado
      and params_archivado[:-2] == params_conteo_archivado)
with open(os.path.join(os.path.dirname(__file__), "templates", "index.html"),
          encoding="utf-8") as archivo:
    texto = archivo.read()
check("se explica que el superadmin y el encargado no crean solicitudes",
      "incluido el superadmin" in texto and "El encargado puede cambiar estados" in texto)
check("mis solicitudes ofrece una vista para restaurar pedidos archivados",
      "pedido-propios-vista" in texto and "Archivados" in texto)

respuesta = ejecutar_archivo("entregado", True)
inserciones = [(sql, params) for sql, params in CAPTURADO
               if "INSERT INTO pedidos_archivados" in sql]
check("solo el pedido terminal se archiva",
      not isinstance(respuesta, tuple) and len(inserciones) == 1)
check("el archivo se guarda para la sucursal que lo solicita",
      bool(inserciones) and inserciones[0][1][:2] == [7, 30])
respuesta_solicitante = ejecutar_archivo(
    "rechazado", True, rol="encargado", sucursal_id=42)
inserciones_solicitante = [(sql, params) for sql, params in CAPTURADO
                           if "INSERT INTO pedidos_archivados" in sql]
check("la sucursal solicitante también puede archivar su copia",
      not isinstance(respuesta_solicitante, tuple)
      and bool(inserciones_solicitante)
      and inserciones_solicitante[0][1][:2] == [7, 42])
with open(os.path.join(os.path.dirname(__file__), "database.py"),
          encoding="utf-8") as archivo:
    schema = archivo.read()
check("el mismo pedido puede archivarse por separado en cada sucursal",
      "UNIQUE KEY uq_pedido_sucursal_archivado (pedido_id, sucursal_id)" in schema)

respuesta_pendiente = ejecutar_archivo("pendiente", True)
sqls_pendiente = [sql for sql, _ in CAPTURADO]
check("un pedido pendiente no puede archivarse",
      isinstance(respuesta_pendiente, tuple)
      and not any("INSERT INTO pedidos_archivados" in sql for sql in sqls_pendiente))
respuesta_restaurar = ejecutar_archivo("rechazado", False)
check("un pedido terminal se puede restaurar al historial",
      not isinstance(respuesta_restaurar, tuple)
      and any("DELETE FROM pedidos_archivados" in sql for sql, _ in CAPTURADO))

respuesta_eliminar = ejecutar_eliminar()
sqls_eliminar = [sql for sql, _ in CAPTURADO]
check("solo se elimina definitivamente un pedido archivado por la sucursal actual",
      not isinstance(respuesta_eliminar, tuple)
      and any("SELECT id FROM pedidos_archivados" in sql for sql in sqls_eliminar)
      and "DELETE FROM pedidos WHERE id = ?" in sqls_eliminar)
check("se preservan los movimientos y los repartos al eliminar el pedido",
      not any("DELETE FROM movimientos" in sql for sql in sqls_eliminar)
      and "UPDATE repartos SET pedido_id = NULL WHERE pedido_id = ?" in sqls_eliminar)
respuesta_no_archivado = ejecutar_eliminar(archivado=False)
check("no se puede eliminar un pedido archivado solo por otra sucursal",
      isinstance(respuesta_no_archivado, tuple)
      and not any("DELETE FROM pedidos WHERE id = ?" in sql for sql, _ in CAPTURADO))
respuesta_activo = ejecutar_eliminar(archivado=True, estado="en_camino")
check("no se puede eliminar un pedido que no llegó a un estado terminal",
      isinstance(respuesta_activo, tuple)
      and not any("DELETE FROM pedidos WHERE id = ?" in sql for sql, _ in CAPTURADO))
check("la interfaz confirma y ofrece eliminar solo en las vistas archivadas",
      "eliminarPedidoArchivado(${p.id})" in interfaz
      and "window.eliminarPedidoArchivado = async (id)" in interfaz
      and "¿Estás seguro de que deseas continuar?" in interfaz)

print("=" * 68)
print("FALLOS:", fallos)
print("=" * 68)
sys.exit(1 if fallos else 0)
