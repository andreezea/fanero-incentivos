"""Incentivos Fanero — incentivos por cuota para vendedores y líderes.

Secrets (Streamlit Cloud → Settings → Secrets):
    DATABASE_URL   = "postgresql://usuario:clave@host.neon.tech/db?sslmode=require"
    ADMIN_PASSWORD = "tu-clave"
Sin DATABASE_URL usa un SQLite local (solo para pruebas).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date

import pandas as pd
import streamlit as st

import core
import db

AZUL = "#00679E"
st.set_page_config(page_title="Incentivos Fanero", page_icon="logo.jpg", layout="wide")
st.markdown(f"""
<style>
  h1, h2, h3 {{ color: {AZUL}; }}
  div[data-testid="stMetricValue"] {{ color: {AZUL}; }}
  .stTabs [aria-selected="true"] {{ color: {AZUL} !important; border-bottom-color: {AZUL} !important; }}
</style>""", unsafe_allow_html=True)


def _secret(nombre, defecto=None):
    try:
        return st.secrets[nombre]
    except Exception:
        return defecto


@st.cache_resource
def motor():
    return db.get_engine(_secret("DATABASE_URL", "sqlite:///incentivos_local.db"))


eng = motor()


# --- Caché: nada pesado se repite en cada clic -------------------------------
# `version` = id de la última carga; cambia solo cuando se suben o borran ventas,
# y así todas las sesiones ven datos frescos sin recalcular en cada interacción.
def version_datos() -> int:
    return db.ultima_version(eng)


@st.cache_data(ttl=3600, show_spinner="Leyendo ventas...", max_entries=6)
def ventas_cacheadas(desde, hasta, version):
    return db.leer_ventas(eng, desde, hasta)


@st.cache_data(ttl=3600, show_spinner="Calculando incentivo...", max_entries=20)
def matriz_cacheada(cfg_json: str, version: int):
    cfg = core.completar_config(json.loads(cfg_json))
    ventas = ventas_cacheadas(cfg["fecha_inicio"], cfg["fecha_fin"], version)
    if ventas.empty:
        return None, 0
    en_rango = core.filtrar(ventas, cfg)
    mask = pd.Series(False, index=en_rango.index)
    for p in cfg["productos"]:
        mask |= core.mascara_producto(en_rango, p, cfg["planes"].get(p))
    rec = recargas_cacheadas(cfg["fecha_inicio"], cfg["fecha_fin"], version) if core.usa_recargas(cfg) else None
    return core.calcular(ventas, cfg, rec), int(mask.sum())


@st.cache_data(ttl=3600, show_spinner="Leyendo recargas...", max_entries=6)
def recargas_cacheadas(desde, hasta, version):
    return db.leer_recargas(eng, desde, hasta)


@st.cache_data(ttl=3600, show_spinner="Preparando Excel...", max_entries=20)
def excel_cacheado(cfg_json: str, version: int) -> bytes:
    matriz, _ = matriz_cacheada(cfg_json, version)
    return core.a_excel(matriz, json.loads(cfg_json))


@st.cache_data(ttl=3600, show_spinner=False)
def catalogos(version: int):
    return {
        "estados": db.valores_distintos(eng, "status") or ["Completada"],
        "deptos": db.valores_distintos(eng, "department"),
        "planes": core.planes_disponibles(db.planes_vistos(eng)),
        "resumen": db.resumen_ventas(eng),
        "historial": db.historial_cargas(eng),
        "recargas": db.resumen_recargas(eng),
    }


@st.cache_data(show_spinner="Leyendo archivo...", max_entries=3)
def leer_fanero(nombre: str, contenido: bytes) -> pd.DataFrame:
    return core.normalizar(core.leer_archivo(nombre, contenido))


def datos_cambiaron():
    st.cache_data.clear()


CLAVE_ADMIN = _secret("ADMIN_PASSWORD")

with st.sidebar:
    st.subheader("Acceso administrador")
    if CLAVE_ADMIN is None:
        st.session_state.admin_ok = True
        st.info("Modo local: sin clave configurada")
    elif st.session_state.get("admin_ok"):
        st.success("Sesión iniciada")
        if st.button("Cerrar sesión"):
            st.session_state.admin_ok = False
            st.rerun()
    else:
        with st.form("login"):
            pw = st.text_input("Clave", type="password")
            if st.form_submit_button("Entrar"):
                if hashlib.sha256(pw.encode()).digest() == hashlib.sha256(str(CLAVE_ADMIN).encode()).digest():
                    st.session_state.admin_ok = True
                    st.rerun()
                st.error("Clave incorrecta")


def es_admin() -> bool:
    if st.session_state.get("admin_ok"):
        return True
    st.info("🔒 Inicia sesión en la barra lateral para usar esta sección.")
    return False


try:
    st.image("logo.jpg", width=280)
except Exception:
    pass
st.title("Fanero - Gestión de Incentivos")
tab_res, tab_inc, tab_carga = st.tabs(["📊 Resultados", "⚙️ Incentivos", "📤 Cargar ventas"])

# --------------------------------------------------------------------------- #
# CARGAR VENTAS
# --------------------------------------------------------------------------- #
with tab_carga:
    if es_admin():
        st.subheader("Subir el reporte de Fanero")
        st.caption("Sube el export tal como sale del sistema (Excel o CSV). Las fechas que trae el "
                   "archivo reemplazan lo ya guardado para esas fechas, así que puedes subir el "
                   "acumulado del mes cada día sin duplicar ventas.")
        archivo = st.file_uploader("Archivo Fanero", type=["xlsx", "xls", "csv", "txt"])
        if archivo:
            try:
                df = leer_fanero(archivo.name, archivo.getvalue())
            except Exception as e:
                st.error(f"No pude leer el archivo: {e}")
                df = None
            if df is not None and not df.empty:
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Filas", f"{len(df):,}")
                c2.metric("Desde", df["LASTSTATUSMODDATE"].min().strftime("%d/%m/%Y"))
                c3.metric("Hasta", df["LASTSTATUSMODDATE"].max().strftime("%d/%m/%Y"))
                c4.metric("Vendedores", df["LOGIN"].nunique())
                with st.expander("Vista previa"):
                    st.dataframe(df.head(50), width="stretch")
                    st.write("Ventas por estado:", df["STATUS"].value_counts().to_dict())
                if st.button("Guardar en la base", type="primary"):
                    with st.spinner("Guardando..."):
                        r = db.guardar_ventas(eng, df, archivo.name)
                    datos_cambiaron()
                    st.success(f"Listo: {r['filas']:,} ventas guardadas del "
                               f"{r['desde']:%d/%m/%Y} al {r['hasta']:%d/%m/%Y} "
                               f"({r['reemplazadas']:,} ventas anteriores de esas fechas reemplazadas).")
            elif df is not None:
                st.warning("El archivo no tiene filas válidas.")

        st.divider()
        st.subheader("Subir el Consolidado (recargas)")
        st.caption("Solo si algún incentivo exige prepagos recargados. Se usa la columna T (UR's). "
                   "Las fechas que trae el archivo reemplazan las recargas ya guardadas de esas fechas.")
        arch_c = st.file_uploader("Archivo Consolidado", type=["xlsx", "xls", "csv"], key="consolidado")
        if arch_c:
            try:
                crudo, (mes_d, anio_d), filtros = core.leer_consolidado(arch_c.name, arch_c.getvalue())
            except Exception as e:
                st.error(f"No pude leer el Consolidado: {e}")
                crudo = None
            if crudo is not None:
                hoy = date.today()
                c1, c2 = st.columns(2)
                meses = list(range(1, 13))
                mes = c1.selectbox("Mes de las ventas", meses, index=(mes_d or hoy.month) - 1,
                                   format_func=lambda m: ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
                                                          "Agosto", "Setiembre", "Octubre", "Noviembre",
                                                          "Diciembre"][m - 1])
                anio = c2.number_input("Año", 2024, 2100, anio_d or hoy.year)
                if mes_d:
                    st.caption(f"Mes detectado del filtro del archivo: {mes_d:02d}/{anio_d}. "
                               "El Consolidado solo trae el número de día; cámbialo si no corresponde.")
                else:
                    st.warning("No pude detectar el mes en el archivo. Elige el mes de las ventas.")
                try:
                    rec = core.normalizar_consolidado(crudo, int(mes), int(anio))
                except Exception as e:
                    st.error(str(e))
                    rec = None
                if rec is not None and not rec.empty:
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Filas", f"{len(rec):,}")
                    c2.metric("Desde", rec["FECHA"].min().strftime("%d/%m/%Y"))
                    c3.metric("Hasta", rec["FECHA"].max().strftime("%d/%m/%Y"))
                    c4.metric("Prepagos recargados", f"{int(rec['UR_PREPAGO'].sum()):,}")
                    if st.button("Guardar recargas", type="primary"):
                        with st.spinner("Guardando..."):
                            r = db.guardar_recargas(eng, rec, arch_c.name)
                        datos_cambiaron()
                        st.success(f"Listo: recargas del {r['desde']:%d/%m/%Y} al {r['hasta']:%d/%m/%Y} guardadas "
                                   f"({r['ur']:,} prepagos recargados).")
                elif rec is not None:
                    st.warning("El Consolidado no tiene filas válidas.")

        st.divider()
        st.subheader("Ventas guardadas por día")
        cat = catalogos(version_datos())
        rv = cat["resumen"].copy()
        if rv.empty:
            st.info("Todavía no hay ventas cargadas.")
        else:
            rv["fecha"] = pd.to_datetime(rv["fecha"])
            st.bar_chart(rv.set_index("fecha")["ventas"], color=AZUL)
            with st.expander("Historial de cargas"):
                st.dataframe(cat["historial"], width="stretch", hide_index=True)
            with st.expander("Borrar ventas de un rango de fechas"):
                rango = st.date_input("Rango a borrar", value=(), format="DD/MM/YYYY", key="borrar")
                if len(rango) == 2 and st.button("Borrar definitivamente"):
                    n = db.borrar_rango(eng, rango[0], rango[1])
                    datos_cambiaron()
                    st.success(f"Se borraron {n:,} ventas.")

# --------------------------------------------------------------------------- #
# INCENTIVOS
# --------------------------------------------------------------------------- #
with tab_inc:
    if es_admin():
        existentes = db.listar_incentivos(eng)
        opciones = ["➕ Nuevo incentivo"] + [f"{i['id']} · {i['nombre']}" for i in existentes]
        elegido = st.selectbox("Crear o editar", opciones)
        actual = None if elegido.startswith("➕") else existentes[opciones.index(elegido) - 1]
        cfg0 = core.completar_config(actual["config"] if actual else {})
        clave_form = f"form_inc_{actual['id'] if actual else 'nuevo'}"

        cat = catalogos(version_datos())
        estados_bd, deptos_bd, planes_disp = cat["estados"], cat["deptos"], cat["planes"]

        with st.form(clave_form):
            nombre = st.text_input("Nombre del incentivo", cfg0["nombre"],
                                   placeholder="Ej. Madrugadores 23/10 · 7 a 10 am")
            c1, c2 = st.columns(2)
            hoy = date.today()
            fi = pd.to_datetime(cfg0["fecha_inicio"]).date() if cfg0["fecha_inicio"] else hoy
            ff = pd.to_datetime(cfg0["fecha_fin"]).date() if cfg0["fecha_fin"] else hoy
            fechas = c1.date_input("Fecha (o rango de fechas)", value=(fi, ff), format="DD/MM/YYYY")
            evaluacion = c2.radio("¿Cómo se evalúa la cuota?",
                                  ["periodo", "diaria"], index=0 if cfg0["evaluacion"] == "periodo" else 1,
                                  format_func=lambda x: "Sumando todo el rango de fechas"
                                  if x == "periodo" else "Cada día por separado (cuota diaria)")
            c1, c2 = st.columns(2)
            horas = list(range(24))
            h1 = c1.selectbox("Hora desde (hora de solicitud)", horas, index=int(cfg0["hora_desde"]),
                              format_func=lambda h: f"{h:02d}:00")
            h2 = c2.selectbox("Hora hasta (incluida)", horas, index=int(cfg0["hora_hasta"]),
                              format_func=lambda h: f"{h:02d}:59")

            st.markdown("**Qué ventas cuentan**")
            c1, c2 = st.columns(2)
            estados = c1.multiselect("Estado", estados_bd,
                                     default=[e for e in cfg0["estados"] if e in estados_bd])
            deptos = c2.multiselect("Departamento (vacío = todos)", deptos_bd,
                                    default=[d for d in cfg0["departamentos"] if d in deptos_bd])

            st.markdown("**Productos y cuotas generales** — debe cumplir la cuota de cada producto marcado")
            st.caption("Prepago y Postpago se cuentan con TIPO ACTIVACION (columna N). "
                       "Porta prepago y OSS se cuentan con PRODUCTO (columna P). "
                       "Los planes salen de TARIFFPLANNAME (columna G).")
            enc = st.columns([1.3, 1, 1, 3])
            enc[0].markdown("Producto")
            enc[1].markdown("Cuota vendedor")
            enc[2].markdown("Cuota líder (equipo)")
            enc[3].markdown("Planes que cuentan (vacío = todos)")
            elegidos, cuotas_v, cuotas_l, planes_sel = [], {}, {}, {}
            for p in core.PRODUCTOS:
                f = st.columns([1.3, 1, 1, 3])
                marcado = f[0].checkbox(p.title(), p in cfg0["productos"], key=f"{clave_form}_{p}")
                qv = f[1].number_input("Cuota vendedor", 0, 10000, int(cfg0["cuotas_vendedor"].get(p, 1)),
                                       key=f"{clave_form}_qv_{p}", label_visibility="collapsed")
                ql = f[2].number_input("Cuota líder", 0, 100000, int(cfg0["cuotas_lider"].get(p, 1)),
                                       key=f"{clave_form}_ql_{p}", label_visibility="collapsed")
                opciones_plan = planes_disp[p]
                previos = [x for x in cfg0["planes"].get(p, []) if x in opciones_plan]
                pl = f[3].multiselect("Planes", opciones_plan, default=previos,
                                      key=f"{clave_form}_pl_{p}", label_visibility="collapsed",
                                      placeholder="Todos los planes")
                if marcado:
                    elegidos.append(p)
                    cuotas_v[p], cuotas_l[p] = int(qv), int(ql)
                    if pl:
                        planes_sel[p] = pl

            st.markdown("**Recargas mínimas** (Consolidado, columna T) — 0 = no se exige")
            st.caption("Cuántas de esas ventas deben estar recargadas. Respeta fechas, planes y departamentos "
                       "del incentivo; el Consolidado no tiene hora, así que cuenta el día completo. "
                       "Sube el Consolidado en Cargar ventas.")
            rec_v, rec_l = {}, {}
            for p in core.RECARGABLES:
                f = st.columns([1.3, 1, 1, 3])
                f[0].markdown(f"{p.title()} recargados")
                rv_ = f[1].number_input("Mín. recargas vendedor", 0, 10000,
                                        int(cfg0["recargas_vendedor"].get(p, 0)),
                                        key=f"{clave_form}_rv_{p}", label_visibility="collapsed")
                rl_ = f[2].number_input("Mín. recargas líder", 0, 100000, int(cfg0["recargas_lider"].get(p, 0)),
                                        key=f"{clave_form}_rl_{p}", label_visibility="collapsed")
                if rv_:
                    rec_v[p] = int(rv_)
                if rl_:
                    rec_l[p] = int(rl_)

            st.markdown("**Participantes**")
            c1, c2 = st.columns(2)
            p_vend = c1.checkbox("Participan vendedores (LOGIN)", cfg0["participa_vendedor"])
            premio_v = c1.text_input("Premio vendedor", cfg0["premio_vendedor"])
            p_lid = c2.checkbox("Participan líderes (LEADER, suma de su equipo)", cfg0["participa_lider"])
            premio_l = c2.text_input("Premio líder", cfg0["premio_lider"])

            n_lista = len(cfg0["participantes"])
            st.caption(f"Lista actual: {n_lista} participantes. Solo ellos compiten."
                       if n_lista else
                       "Sin lista: compiten todos los que aparecen en el Fanero con la cuota general.")
            archivo_part = st.file_uploader("Lista de participantes (opcional; reemplaza la actual)",
                                            type=["xlsx", "xls", "csv"])
            quitar_lista = st.checkbox("Quitar la lista y que compitan todos", False) if n_lista else False

            st.markdown("**Quiénes ganan** (puedes marcar ambas)")
            c1, c2 = st.columns(2)
            g_todos = c1.checkbox("Todos los que lleguen a la cuota", cfg0["gana_todos_cumplen"])
            g_prim = c2.checkbox("Los primeros en llegar a la cuota en cada departamento",
                                 cfg0["gana_primeros_depto"])
            n_prim = c2.number_input("¿Cuántos primeros por departamento?", 1, 50, int(cfg0["primeros_n"]))
            cupos = st.number_input("Cupos: máximo de ganadores por rol, los primeros en llegar a la cuota "
                                    "(0 = sin límite; si es cuota diaria, cupos por día)",
                                    0, 10000, int(cfg0.get("cupos") or 0))

            guardar = st.form_submit_button("Guardar incentivo", type="primary")

        st.download_button("⬇️ Descargar plantilla de participantes", core.plantilla_participantes(),
                           file_name="plantilla_participantes.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        if guardar:
            errores, avisos = [], []
            participantes = [] if quitar_lista else cfg0["participantes"]
            if archivo_part is not None:
                try:
                    participantes, avisos = core.leer_participantes(archivo_part.name, archivo_part.getvalue())
                    if not participantes:
                        errores.append("La lista de participantes no tiene filas válidas.")
                except Exception as e:
                    errores.append(f"Lista de participantes: {e}")
            if not nombre.strip():
                errores.append("Ponle un nombre.")
            if len(fechas) != 2:
                errores.append("Elige fecha de inicio y fin (para un solo día, elige el mismo día dos veces).")
            if h1 > h2:
                errores.append("La hora desde no puede ser mayor que la hora hasta.")
            if not elegidos:
                errores.append("Marca al menos un producto.")
            for p in set(rec_v) | set(rec_l):
                if p not in elegidos:
                    errores.append(f"Pusiste recargas mínimas de {p.title()} pero ese producto no está marcado.")
            if not (p_vend or p_lid):
                errores.append("Marca al menos un tipo de participante.")
            if not (g_todos or g_prim):
                errores.append("Marca al menos una forma de ganar.")
            if errores:
                for e in errores:
                    st.error(e)
            else:
                cfg = dict(nombre=nombre.strip(), fecha_inicio=str(fechas[0]), fecha_fin=str(fechas[1]),
                           hora_desde=h1, hora_hasta=h2, evaluacion=evaluacion, estados=estados,
                           departamentos=deptos, productos=elegidos, planes=planes_sel,
                           cuotas_vendedor=cuotas_v, cuotas_lider=cuotas_l,
                           recargas_vendedor={k: v for k, v in rec_v.items() if k in elegidos},
                           recargas_lider={k: v for k, v in rec_l.items() if k in elegidos},
                           participa_vendedor=p_vend, participa_lider=p_lid,
                           gana_todos_cumplen=g_todos, gana_primeros_depto=g_prim, primeros_n=int(n_prim), cupos=int(cupos),
                           premio_vendedor=premio_v, premio_lider=premio_l,
                           participantes=participantes)
                db.guardar_incentivo(eng, cfg, actual["id"] if actual else None)
                for a in avisos:
                    st.warning(a)
                if p_lid and any(v <= 1 for v in cuotas_l.values()):
                    st.warning("Ojo: participan líderes con cuota de 1. Revisa que sea a propósito; "
                               "si el incentivo es solo para vendedores, desmarca Participan líderes.")
                st.success(f"Incentivo guardado con {len(participantes) or 'todos los'} participantes. "
                           "Míralo en la pestaña Resultados.")

        if actual and st.button("Archivar este incentivo"):
            db.archivar_incentivo(eng, actual["id"])
            st.rerun()

# --------------------------------------------------------------------------- #
# RESULTADOS
# --------------------------------------------------------------------------- #
def describir(cfg: dict) -> str:
    fi = pd.to_datetime(cfg["fecha_inicio"]).strftime("%d/%m/%Y")
    ff = pd.to_datetime(cfg["fecha_fin"]).strftime("%d/%m/%Y")
    fechas = fi if fi == ff else f"{fi} al {ff}"
    partes = [f"📅 {fechas}", f"🕒 {cfg['hora_desde']:02d}:00 a {cfg['hora_hasta']:02d}:59 (hora de solicitud)"]
    if cfg["evaluacion"] == "diaria":
        partes.append("cuota diaria")
    if cfg["planes"]:
        partes.append("📋 " + "; ".join(f"{p}: {', '.join(v)}" for p, v in cfg["planes"].items()))
    if cfg["recargas_vendedor"] or cfg["recargas_lider"]:
        r = [f"vendedor {p} {n}" for p, n in cfg["recargas_vendedor"].items()]
        r += [f"líder {p} {n}" for p, n in cfg["recargas_lider"].items()]
        partes.append("🔋 recargadas mín. " + ", ".join(r))
    if cfg["participa_vendedor"]:
        partes.append("🎯 vendedor " + ", ".join(f"{p} {n}" for p, n in cfg["cuotas_vendedor"].items()))
    if cfg["participa_lider"]:
        partes.append("🎯 líder " + ", ".join(f"{p} {n}" for p, n in cfg["cuotas_lider"].items()))
    gana = []
    if cfg["gana_todos_cumplen"]:
        gana.append("todos los que cumplen")
    if cfg["gana_primeros_depto"]:
        gana.append(f"primeros {cfg['primeros_n']} por depto")
    if cfg.get("cupos"):
        gana.append(f"máximo {cfg['cupos']} cupos")
    partes.append("🏆 " + " + ".join(gana))
    partes.append(f"👥 {len(cfg['participantes'])} en lista" if cfg["participantes"] else "👥 todos los del Fanero")
    return "  |  ".join(partes)


with tab_res:
    incs = db.listar_incentivos(eng)
    if not incs:
        st.info("Aún no hay incentivos. Créalos en la pestaña ⚙️ Incentivos.")
    else:
        sel = st.selectbox("Incentivo", incs, format_func=lambda i: f"{i['id']} · {i['nombre']}")
        cfg = core.completar_config(sel["config"])
        cfg_json = json.dumps(cfg, sort_keys=True, default=str, ensure_ascii=False)
        st.caption(describir(cfg))

        version = version_datos()
        if core.usa_recargas(cfg):
            rr = catalogos(version)["recargas"]
            if rr.empty:
                st.warning("Este incentivo exige recargas, pero no hay ningún Consolidado cargado. "
                           "Súbelo en Cargar ventas; mientras tanto, nadie cumple la recarga.")
            else:
                fechas_r = set(pd.to_datetime(rr["fecha"]).dt.date)
                pedidas = set(pd.date_range(cfg["fecha_inicio"], cfg["fecha_fin"]).date)
                faltan = sorted(pedidas - fechas_r)
                if faltan:
                    st.warning("Faltan recargas del Consolidado para: "
                               + ", ".join(d.strftime("%d/%m") for d in faltan))
        matriz, n_rango = matriz_cacheada(cfg_json, version)
        if matriz is None:
            st.warning("No hay ventas cargadas para esas fechas.")
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Participantes", f"{len(matriz):,}")
            c2.metric("Cumplen cuota", f"{(matriz['CUMPLE'] == 'Sí').sum():,}")
            c3.metric("Ganadores", f"{(matriz['GANA'] == 'Sí').sum():,}")
            c4.metric("Ventas en el rango", f"{n_rango:,}",
                      help="Ventas de los productos del incentivo dentro de las fechas y horas")

            with st.form("filtros_resultados", border=False):
                c1, c2, c3, c4 = st.columns(4)
                f_rol = c1.multiselect("Rol", sorted(matriz["ROL"].unique()))
                f_dep = c2.multiselect("Departamento", sorted(matriz["DEPARTAMENTO"].unique()))
                f_gana = c3.selectbox("Mostrar", ["Todos", "Solo ganadores", "Solo no ganadores"])
                f_dni = c4.text_input("Buscar DNI o nombre")
                st.form_submit_button("Aplicar filtros")
            vista = matriz
            if f_rol:
                vista = vista[vista["ROL"].isin(f_rol)]
            if f_dep:
                vista = vista[vista["DEPARTAMENTO"].isin(f_dep)]
            if f_gana == "Solo ganadores":
                vista = vista[vista["GANA"] == "Sí"]
            elif f_gana == "Solo no ganadores":
                vista = vista[vista["GANA"] == "No"]
            if f_dni:
                b = f_dni.strip().lstrip("0")
                vista = vista[vista["DNI"].fillna("").str.contains(b, regex=False)
                              | vista["LIDER"].fillna("").str.contains(b, regex=False)
                              | vista["NOMBRE"].fillna("").str.contains(f_dni.strip(), case=False, regex=False)]

            st.caption(f"Mostrando {len(vista):,} de {len(matriz):,} filas. Los ganadores aparecen primero.")
            st.dataframe(
                vista, width="stretch", hide_index=True, height=520,
                column_config={
                    "GANA": st.column_config.TextColumn("Gana", help="Sí = ganador"),
                    "HORA_CUOTA": st.column_config.DatetimeColumn("Llegó a la cuota",
                                                                  format="DD/MM/YYYY HH:mm:ss"),
                    "PUESTO_DEPTO": st.column_config.NumberColumn("Puesto depto"),
                    "PUESTO_GENERAL": st.column_config.NumberColumn("Puesto general"),
                    "AVANCE_%": st.column_config.ProgressColumn("Avance (lo más atrasado)", format="%.0f%%",
                                                                min_value=0, max_value=100),
                    "LIDER": "Líder",
                    "DIA": st.column_config.DateColumn("Día", format="DD/MM/YYYY"),
                })

            with st.expander("Resumen por departamento"):
                st.dataframe(core.resumen(matriz), width="stretch", hide_index=True)

            # El Excel se arma solo cuando lo pides, y queda guardado para la siguiente descarga
            clave_xl = f"xl_{sel['id']}_{version}_{hash(cfg_json)}"
            if st.session_state.get("xl_listo") != clave_xl:
                if st.button("📄 Preparar Excel de la matriz", type="primary"):
                    st.session_state["xl_listo"] = clave_xl
                    st.rerun()
            else:
                st.download_button(
                    "⬇️ Descargar matriz en Excel",
                    excel_cacheado(cfg_json, version),
                    file_name=f"incentivo_{sel['id']}_{cfg['nombre'][:30].replace(' ', '_')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    type="primary")
