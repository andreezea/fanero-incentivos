"""Incentivos Fanero — incentivos por cuota para vendedores y líderes.

Secrets (Streamlit Cloud → Settings → Secrets):
    DATABASE_URL   = "postgresql://usuario:clave@host.neon.tech/db?sslmode=require"
    ADMIN_PASSWORD = "tu-clave"
Sin DATABASE_URL usa un SQLite local (solo para pruebas).
"""
from __future__ import annotations

import hashlib
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


@st.cache_data(ttl=300, show_spinner=False)
def ventas_cacheadas(desde, hasta, _version):
    return db.leer_ventas(eng, desde, hasta)


if "version_datos" not in st.session_state:
    st.session_state.version_datos = 0


def datos_cambiaron():
    st.session_state.version_datos += 1
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
                df = core.normalizar(core.leer_archivo(archivo.name, archivo.getvalue()))
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
        st.subheader("Ventas guardadas por día")
        rv = db.resumen_ventas(eng)
        if rv.empty:
            st.info("Todavía no hay ventas cargadas.")
        else:
            rv["fecha"] = pd.to_datetime(rv["fecha"])
            st.bar_chart(rv.set_index("fecha")["ventas"], color=AZUL)
            with st.expander("Historial de cargas"):
                st.dataframe(db.historial_cargas(eng), width="stretch", hide_index=True)
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
        cfg0 = {**core.CONFIG_DEFECTO, **(actual["config"] if actual else {})}

        estados_bd = db.valores_distintos(eng, "status") or ["Completada"]
        productos_bd = db.valores_distintos(eng, "producto")
        deptos_bd = db.valores_distintos(eng, "department")

        with st.form("form_inc"):
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
                                  if x == "periodo" else "Cada día por separado (cuota diaria)",
                                  horizontal=False)
            c1, c2 = st.columns(2)
            horas = list(range(24))
            h1 = c1.selectbox("Hora desde (hora de solicitud)", horas, index=int(cfg0["hora_desde"]),
                              format_func=lambda h: f"{h:02d}:00")
            h2 = c2.selectbox("Hora hasta (incluida)", horas, index=int(cfg0["hora_hasta"]),
                              format_func=lambda h: f"{h:02d}:59")

            st.markdown("**Qué ventas cuentan**")
            c1, c2, c3 = st.columns(3)
            estados = c1.multiselect("Estado", estados_bd,
                                     default=[e for e in cfg0["estados"] if e in estados_bd])
            productos = c2.multiselect("Producto (vacío = todos)", productos_bd,
                                       default=[p for p in cfg0["productos"] if p in productos_bd])
            deptos = c3.multiselect("Departamento (vacío = todos)", deptos_bd,
                                    default=[d for d in cfg0["departamentos"] if d in deptos_bd])

            st.markdown("**Participantes y cuotas**")
            c1, c2 = st.columns(2)
            p_vend = c1.checkbox("Participan vendedores (LOGIN)", cfg0["participa_vendedor"])
            cuota_v = c1.number_input("Cuota vendedor (ventas)", 1, 10000, int(cfg0["cuota_vendedor"]))
            premio_v = c1.text_input("Premio vendedor", cfg0["premio_vendedor"])
            p_lid = c2.checkbox("Participan líderes (LEADER, suma de su equipo)", cfg0["participa_lider"])
            cuota_l = c2.number_input("Cuota líder (ventas del equipo)", 1, 100000, int(cfg0["cuota_lider"]))
            premio_l = c2.text_input("Premio líder", cfg0["premio_lider"])

            st.markdown("**Quiénes ganan** (puedes marcar ambas)")
            c1, c2 = st.columns(2)
            g_todos = c1.checkbox("Todos los que lleguen a la cuota", cfg0["gana_todos_cumplen"])
            g_prim = c2.checkbox("Los primeros en llegar a la cuota en cada departamento",
                                 cfg0["gana_primeros_depto"])
            n_prim = c2.number_input("¿Cuántos primeros por departamento?", 1, 50, int(cfg0["primeros_n"]))

            guardar = st.form_submit_button("Guardar incentivo", type="primary")

        if guardar:
            errores = []
            if not nombre.strip():
                errores.append("Ponle un nombre.")
            if len(fechas) != 2:
                errores.append("Elige fecha de inicio y fin (para un solo día, elige el mismo día dos veces).")
            if h1 > h2:
                errores.append("La hora desde no puede ser mayor que la hora hasta.")
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
                           productos=productos, departamentos=deptos,
                           participa_vendedor=p_vend, participa_lider=p_lid,
                           cuota_vendedor=int(cuota_v), cuota_lider=int(cuota_l),
                           gana_todos_cumplen=g_todos, gana_primeros_depto=g_prim, primeros_n=int(n_prim),
                           premio_vendedor=premio_v, premio_lider=premio_l)
                db.guardar_incentivo(eng, cfg, actual["id"] if actual else None)
                st.success("Incentivo guardado. Míralo en la pestaña Resultados.")
                st.rerun()

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
    cuotas = []
    if cfg["participa_vendedor"]:
        cuotas.append(f"vendedor {cfg['cuota_vendedor']}")
    if cfg["participa_lider"]:
        cuotas.append(f"líder {cfg['cuota_lider']}")
    partes.append("🎯 cuota " + " · ".join(cuotas))
    gana = []
    if cfg["gana_todos_cumplen"]:
        gana.append("todos los que cumplen")
    if cfg["gana_primeros_depto"]:
        gana.append(f"primeros {cfg['primeros_n']} por depto")
    partes.append("🏆 " + " + ".join(gana))
    if cfg["productos"]:
        partes.append("📦 " + ", ".join(cfg["productos"]))
    return "  |  ".join(partes)


with tab_res:
    incs = db.listar_incentivos(eng)
    if not incs:
        st.info("Aún no hay incentivos. Créalos en la pestaña ⚙️ Incentivos.")
    else:
        sel = st.selectbox("Incentivo", incs, format_func=lambda i: f"{i['id']} · {i['nombre']}")
        cfg = {**core.CONFIG_DEFECTO, **sel["config"]}
        st.caption(describir(cfg))

        ventas_df = ventas_cacheadas(cfg["fecha_inicio"], cfg["fecha_fin"], st.session_state.version_datos)
        if ventas_df.empty:
            st.warning("No hay ventas cargadas para esas fechas.")
        else:
            matriz = core.calcular(ventas_df, cfg)
            ganadores = matriz[matriz["GANA"] == "Sí"]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Participantes", f"{len(matriz):,}")
            c2.metric("Cumplen cuota", f"{(matriz['CUMPLE'] == 'Sí').sum():,}")
            c3.metric("Ganadores", f"{len(ganadores):,}")
            c4.metric("Ventas en el rango", f"{len(core.filtrar(ventas_df, cfg)):,}")

            c1, c2, c3, c4 = st.columns(4)
            f_rol = c1.multiselect("Rol", sorted(matriz["ROL"].unique()))
            f_dep = c2.multiselect("Departamento", sorted(matriz["DEPARTAMENTO"].unique()))
            f_gana = c3.selectbox("Mostrar", ["Todos", "Solo ganadores", "Solo no ganadores"])
            f_dni = c4.text_input("Buscar DNI")
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
                vista = vista[vista["DNI"].str.contains(f_dni.strip()) | vista["LIDER"].str.contains(f_dni.strip())]

            def pintar(fila):
                color = "background-color: #DCEFF8" if fila["GANA"] == "Sí" else ""
                return [color] * len(fila)

            st.dataframe(
                vista.style.apply(pintar, axis=1),
                width="stretch", hide_index=True, height=520,
                column_config={
                    "AVANCE_%": st.column_config.ProgressColumn("Avance", format="%.0f%%",
                                                                min_value=0, max_value=100),
                    "HORA_CUOTA": st.column_config.DatetimeColumn("Llegó a la cuota",
                                                                  format="DD/MM/YYYY HH:mm:ss"),
                    "PUESTO_DEPTO": st.column_config.NumberColumn("Puesto depto"),
                    "LIDER": "Líder",
                    "DIA": st.column_config.DateColumn("Día", format="DD/MM/YYYY"),
                })

            with st.expander("Resumen por departamento"):
                st.dataframe(core.resumen(matriz), width="stretch", hide_index=True)

            st.download_button(
                "⬇️ Descargar matriz en Excel",
                core.a_excel(matriz, cfg),
                file_name=f"incentivo_{sel['id']}_{cfg['nombre'][:30].replace(' ', '_')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary")
