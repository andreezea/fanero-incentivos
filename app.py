"""
app.py -- App Streamlit para que los BO gestionen incentivos de Fanero.

Flujo:
1. Cargar el archivo de ventas (el template con las 12 columnas).
2. Crear un incentivo: fecha/hora inicio-fin, condicion de victoria,
   nivel de participante (LOGIN o LEADER), productos que cuentan.
3. Cargar la lista de participantes (DNI o nombres de lider, segun nivel).
4. Calcular resultado y ver el ganador.
5. Consultar el historial de incentivos pasados.
"""

import streamlit as st
import pandas as pd
import numpy as np
import json
import io
import os
from datetime import datetime, date, time
import db


def obtener_password_admin():
    """Lee la contraseña de administrador desde Secrets (Streamlit Cloud)
    o desde el .env local. Si no esta configurada en ningun lado, usa un
    valor por defecto (avisa que hay que cambiarlo)."""
    valor = os.environ.get("ADMIN_PASSWORD")
    if not valor:
        try:
            valor = st.secrets["ADMIN_PASSWORD"]
        except Exception:
            valor = None
    return valor or "fanero2026"

st.set_page_config(page_title="Fanero - Incentivos", layout="wide", page_icon="logo_fanero.jpg")
@st.cache_resource
def _inicializar_base_de_datos():
    db.init_db()
    return True


_inicializar_base_de_datos()

PRODUCTOS_DISPONIBLES = ["PREPAGO", "POSTPAGO", "PORTA_PREPAGO", "OSS"]

PLANES_POSTPAGO_OSS = [
    "TODOS", "POWER 29.90", "POWER 39.90", "POWER 49.90", "POWER 59.90", "POWER ILIM 69.90",
]
PLANES_PREPAGO = ["TODOS", "PLAN FLEXIBLE"]
PARTNER_VALIDO = "DISTRIB. Y COMERCIALIZADORA FANERO S.A.C"

DEPARTAMENTOS = [
    "AMAZONAS", "CAJAMARCA", "HUANCAVELICA", "HUANUCO", "JUNIN",
    "LORETO", "PASCO", "SAN MARTIN", "UCAYALI",
]


def parsear_departamentos(valor):
    """Lee el campo departamento sea que venga en JSON (formato nuevo, lista)
    o como texto simple (incentivos creados antes de este cambio)."""
    if not valor:
        return []
    try:
        parsed = json.loads(valor)
        if isinstance(parsed, list):
            return parsed
        return [str(parsed)]
    except (ValueError, TypeError):
        return [valor]


# ---------------------------------------------------------------------
# Normalizacion del archivo de ventas
# ---------------------------------------------------------------------


def normalizar_ventas(df_raw: pd.DataFrame) -> pd.DataFrame:
    """Convierte el excel crudo al formato interno que usa la base de datos."""
    df = df_raw.copy()
    df.columns = [c.strip().upper() for c in df.columns]

    columnas_esperadas = [
        "ACTIVATIONTYPE", "STATUS", "LASTSTATUSMODDATE", "REQUESTSTARTHOUR",
        "LOGIN", "PARTNER", "LEADER", "USERTYPE", "DEPARTMENT",
        "TIPO ACTIVACION", "PORTABILIDAD", "TARIFFPLANNAME",
    ]
    faltantes = [c for c in columnas_esperadas if c not in df.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas obligatorias en el archivo: {', '.join(faltantes)}")

    # Quitar filas completamente vacias
    df = df.dropna(how="all")

    out = pd.DataFrame()
    out["activationtype"] = df["ACTIVATIONTYPE"].astype(str).str.strip()
    out["status"] = df["STATUS"].astype(str).str.strip().str.upper()

    # Fecha: formato DD/MM/AAAA (peruano) -- dayfirst=True es OBLIGATORIO,
    # sin esto pandas asume MM/DD/AAAA (formato EEUU) y arruina las fechas:
    # "01/08/2026" se leeria como 8 de enero en vez de 1 de agosto, y
    # cualquier dia >12 (ej. "15/08/2026") se perderia directamente.
    out["fecha"] = pd.to_datetime(
        df["LASTSTATUSMODDATE"], errors="coerce", dayfirst=True
    ).dt.strftime("%Y-%m-%d")

    # Hora: quedarnos solo con el numero de hora (sin minutos/segundos).
    # Vectorizado: la mayoria de los valores ya vienen como numero (int),
    # asi que se intenta la conversion directa primero (rapida), y solo
    # las filas que fallan (texto tipo "07:00") se procesan aparte.
    hora_numerica = pd.to_numeric(df["REQUESTSTARTHOUR"], errors="coerce")
    faltantes_hora = hora_numerica.isna() & df["REQUESTSTARTHOUR"].notna()
    if faltantes_hora.any():
        texto_hora = df.loc[faltantes_hora, "REQUESTSTARTHOUR"].astype(str).str.split(":").str[0]
        hora_numerica.loc[faltantes_hora] = pd.to_numeric(texto_hora, errors="coerce")
    out["hora"] = hora_numerica

    out["login"] = df["LOGIN"].astype(str).str.strip()
    out["partner"] = df["PARTNER"].astype(str).str.strip()
    out["leader"] = df["LEADER"].astype(str).str.strip()
    out["usertype"] = df["USERTYPE"].astype(str).str.strip()

    # Department: quitar tildes de forma vectorizada (mucho mas rapido que
    # llamar unicodedata fila por fila en un archivo de 45,000+ filas)
    depto_upper = df["DEPARTMENT"].astype(str).str.strip().str.upper()
    tabla_tildes = str.maketrans("ÁÉÍÓÚÑ", "AEIOUN")
    out["department"] = depto_upper.str.translate(tabla_tildes)

    out["district"] = df["DISTRICT"].astype(str).str.strip() if "DISTRICT" in df.columns else ""
    out["tipo_activacion"] = df["TIPO ACTIVACION"].astype(str).str.strip().str.upper()
    out["portabilidad"] = df["PORTABILIDAD"].astype(str).str.strip().str.upper()
    out["tariffplanname"] = df["TARIFFPLANNAME"].astype(str).str.strip().str.upper()

    # Producto derivado: TIPO ACTIVACION siempre trae PREPAGO o POSTPAGO,
    # y PORTABILIDAD siempre trae PORTA PREPAGO, OSS, o NINGUNA (nunca vacio).
    # Cuando PORTABILIDAD es distinto de NINGUNA, manda esa columna (es una
    # venta de portabilidad). Si PORTABILIDAD es NINGUNA, se usa TIPO ACTIVACION.
    # Vectorizado con np.select en vez de un .apply() fila por fila, que es
    # muchisimo mas lento en archivos grandes.
    condiciones = [
        out["portabilidad"] == "PORTA PREPAGO",
        out["portabilidad"] == "OSS",
        out["tipo_activacion"] == "PREPAGO",
        out["tipo_activacion"] == "POSTPAGO",
    ]
    resultados_producto = ["PORTA_PREPAGO", "OSS", "PREPAGO", "POSTPAGO"]
    out["producto"] = np.select(condiciones, resultados_producto, default="SIN_CLASIFICAR")

    # Venta valida: STATUS = COMPLETADA y PARTNER correcto
    out["es_valida"] = (
        (out["status"] == "COMPLETADA") &
        (out["partner"].str.upper().str.strip() == PARTNER_VALIDO.upper())
    ).astype(int)

    # Descartar filas sin fecha u hora (no se pueden ubicar en el tiempo)
    out = out.dropna(subset=["fecha", "hora"])

    return out


# ---------------------------------------------------------------------
# Calculo del ganador
# ---------------------------------------------------------------------

def _hora_a_entero(valor_hora):
    """Convierte 'HH:MM:SS' (como se guarda el incentivo) a un entero de hora.
    Necesario porque la columna 'hora' en ventas es INTEGER, y comparar un
    INTEGER contra un TEXT en SQLite nunca matchea (siempre gana el TEXT)."""
    if isinstance(valor_hora, int):
        return valor_hora
    return int(str(valor_hora).split(":")[0])


def _calcular_ranking_departamento(incentivo, participantes, productos_para_meta, departamento):
    """Corre el calculo de meta+cupos para UN solo departamento."""
    # El filtro de plan tarifario aplica si el incentivo lo pidio, sin
    # importar el producto (Prepago, Postpago u OSS) -- la lista de planes
    # disponibles ya se filtro por producto al momento de crear el incentivo.
    plan_a_filtrar = None
    if incentivo.get("requiere_plan_flexible"):
        plan_a_filtrar = incentivo.get("plan_tarifario")

    ventas = db.obtener_ventas_en_rango(
        incentivo["fecha_inicio"], _hora_a_entero(incentivo["hora_inicio"]),
        incentivo["fecha_fin"], _hora_a_entero(incentivo["hora_fin"]),
        productos_para_meta, incentivo["nivel_participante"],
        [departamento], plan_a_filtrar,
    )

    nivel = incentivo["nivel_participante"]
    campo = "login" if nivel == "LOGIN" else "leader"

    participantes_set = set(participantes)
    ventas_participantes = [v for v in ventas if v[campo] in participantes_set]
    df = pd.DataFrame(ventas_participantes)

    resultados = []
    if df.empty:
        for p in participantes:
            resultados.append({
                "identificador": p, "departamento": departamento,
                "ventas_contadas": 0, "gano": False, "momento_meta": None,
            })
        return resultados

    meta = incentivo["meta_fija"]
    cupos = incentivo.get("cupos", 1) or 1

    df["momento"] = df["fecha"] + " " + df["hora"].astype(str).str.zfill(2) + ":00"
    df = df.sort_values("momento")

    # Agrupar UNA sola vez (en vez de filtrar el dataframe completo por cada
    # participante individualmente, que es muy lento con muchos participantes).
    momento_meta_por_participante = {}
    conteo_final = {}
    for p, sub in df.groupby(campo):
        if p not in participantes_set:
            continue
        conteo_final[p] = len(sub)
        if len(sub) >= meta:
            momento_meta_por_participante[p] = sub.iloc[meta - 1]["momento"]

    # Ordenar a quienes SI alcanzaron la meta por el momento en que la alcanzaron
    # (el mas temprano primero). Los primeros "cupos" son los ganadores DE ESTE DEPARTAMENTO.
    alcanzaron = sorted(momento_meta_por_participante.items(), key=lambda x: x[1])

    if len(alcanzaron) > cupos:
        momento_limite = alcanzaron[cupos - 1][1]
        ganadores_ids = {p for p, m in alcanzaron if m <= momento_limite}
    else:
        ganadores_ids = {p for p, m in alcanzaron}

    for p in participantes:
        alcanzo = momento_meta_por_participante.get(p)
        resultados.append({
            "identificador": p,
            "departamento": departamento,
            "ventas_contadas": conteo_final.get(p, 0),
            "gano": p in ganadores_ids,
            "momento_meta": alcanzo,
        })

    return resultados


def calcular_incentivo(incentivo: dict):
    # producto_meta siempre esta definido (es un campo obligatorio al crear
    # el incentivo), asi que la meta se mide siempre sobre ESE unico producto.
    productos_para_meta = [incentivo["producto_meta"]]

    departamentos = parsear_departamentos(incentivo.get("departamento"))
    if not departamentos:
        departamentos = [None]  # fallback si por alguna razon no hay departamento guardado

    # Cada departamento compite por separado, con sus propios cupos y meta,
    # y usando SOLO los participantes que fueron asignados a ese departamento
    # especifico (no toda la lista general de participantes del incentivo).
    resultados_totales = []
    for depto in departamentos:
        participantes_depto = db.obtener_participantes(incentivo["id"], depto)
        resultados_totales.extend(
            _calcular_ranking_departamento(incentivo, participantes_depto, productos_para_meta, depto)
        )

    resultados_totales.sort(key=lambda r: r["ventas_contadas"], reverse=True)
    return resultados_totales





# ---------------------------------------------------------------------
# Interfaz
# ---------------------------------------------------------------------

try:
    st.image("logo_fanero.jpg", width=220)
except Exception:
    pass

st.title("Fanero - Gestion de Incentivos")

if "admin_autenticado" not in st.session_state:
    st.session_state["admin_autenticado"] = False

with st.sidebar:
    st.subheader("Acceso administrador")
    if st.session_state["admin_autenticado"]:
        st.success("Sesion iniciada")
        if st.button("Cerrar sesion"):
            st.session_state["admin_autenticado"] = False
            st.rerun()
    else:
        password_ingresada = st.text_input("Contraseña", type="password", key="password_admin")
        if st.button("Iniciar sesion"):
            if password_ingresada == obtener_password_admin():
                st.session_state["admin_autenticado"] = True
                st.rerun()
            else:
                st.error("Contraseña incorrecta.")
        st.caption("Si eres BO, no necesitas iniciar sesion -- usa las pestañas de la derecha.")

if st.session_state["admin_autenticado"]:
    tab1, tab2, tab3, tab4 = st.tabs([
        "Cargar ventas", "Crear incentivo", "Calcular resultado", "Historial"
    ])
else:
    tab1 = None
    tab2, tab3, tab4 = st.tabs([
        "Crear incentivo", "Calcular resultado", "Historial"
    ])

@st.cache_data(show_spinner="Procesando archivo de ventas (puede tardar con archivos grandes)...")
def _leer_y_normalizar_ventas(archivo_bytes):
    df_raw = pd.read_excel(io.BytesIO(archivo_bytes))
    df_normalizado = normalizar_ventas(df_raw)
    return df_raw, df_normalizado


# --- TAB 1: Cargar ventas (solo visible con sesion iniciada) ---
if tab1:
    with tab1:
        st.subheader("Cargar archivo de ventas")
        st.caption("Sube el excel con las columnas: ACTIVATIONTYPE, STATUS, LASTSTATUSMODDATE, "
               "REQUESTSTARTHOUR, LOGIN, PARTNER, LEADER, USERTYPE, DEPARTMENT, "
               "TIPO ACTIVACION, PORTABILIDAD, TARIFFPLANNAME")

        fecha_declarada = st.date_input(
        "Fecha de las ventas que estas cargando (para verificacion)",
        value=date.today(),
        )
        archivo_ventas = st.file_uploader("Archivo de ventas (.xlsx)", type=["xlsx"], key="ventas")

        if archivo_ventas is not None:
            try:
                archivo_bytes = archivo_ventas.getvalue()
                df_raw, df_normalizado = _leer_y_normalizar_ventas(archivo_bytes)

                st.write(f"Filas leidas: {len(df_raw)} | Filas procesables: {len(df_normalizado)}")
                st.write(f"Ventas validas (COMPLETADA + partner correcto): {df_normalizado['es_valida'].sum()}")

                fechas_reales = sorted(df_normalizado["fecha"].dropna().unique().tolist())
                fecha_declarada_str = str(fecha_declarada)
                if fecha_declarada_str not in fechas_reales:
                    st.error(
                        f"Elegiste la fecha {fecha_declarada_str}, pero el archivo NO tiene "
                        f"ventas de ese dia. El archivo contiene fechas: "
                        f"{', '.join(fechas_reales) if len(fechas_reales) <= 10 else f'{fechas_reales[0]} a {fechas_reales[-1]} ({len(fechas_reales)} dias)'}. "
                        f"Verifica que sea el archivo correcto antes de continuar."
                    )
                else:
                    st.success(f"El archivo si contiene ventas de la fecha {fecha_declarada_str}. Ok para continuar.")

                st.dataframe(df_normalizado.head(20))

                file_hash = db.calcular_hash_archivo(archivo_bytes)
                ya_cargado = db.archivo_ya_cargado(file_hash)

                if ya_cargado:
                    st.warning(
                        f"Este archivo (byte por byte identico) ya fue cargado antes: "
                        f"'{ya_cargado['file_name']}' el {ya_cargado['cargado_en']}. "
                        f"Si de verdad quieres volver a cargarlo, cambia algo minimo en el "
                        f"archivo o avisa para forzar la carga."
                    )
                else:
                    hashes_nuevos = db.calcular_hashes_de_dataframe(df_normalizado)
                    cantidad_repetida = db.contar_hashes_existentes(hashes_nuevos)

                    excluir_repetidas = False
                    if cantidad_repetida > 0:
                        st.warning(
                            f"{cantidad_repetida} de las {len(df_normalizado)} filas de este archivo "
                            f"son IDENTICAS (mismas 12 columnas) a ventas que ya estan cargadas. Esto "
                            f"puede ser normal (dos ventas reales identicas por casualidad) o senal de "
                            f"que este archivo se solapa con uno ya subido antes."
                        )
                        excluir_repetidas = st.checkbox(
                            f"Excluir esas {cantidad_repetida} filas repetidas de esta carga (recomendado si "
                            f"sabes que este archivo se solapa en fechas con uno ya cargado)"
                        )

                    if st.button("Confirmar carga a la base de datos"):
                        df_a_insertar = df_normalizado
                        if excluir_repetidas:
                            df_normalizado_con_hash = df_normalizado.copy()
                            df_normalizado_con_hash["_hash_temp"] = hashes_nuevos
                            ya_existentes_set = set(db.obtener_hashes_existentes(hashes_nuevos))
                            df_a_insertar = df_normalizado_con_hash[
                                ~df_normalizado_con_hash["_hash_temp"].isin(ya_existentes_set)
                            ].drop(columns=["_hash_temp"])

                        insertadas, _ = db.insertar_ventas(df_a_insertar, file_hash, archivo_ventas.name)
                        st.success(f"Se cargaron {insertadas} filas.")
            except ValueError as e:
                st.error(str(e))

        st.divider()
        st.subheader("Maestro de participantes (opcional)")
        st.caption(
        "Complemento para enriquecer los resultados con nombre, lider, gestor, etc. "
        "Sube el archivo tal cual lo exportas (con columnas como NUMERODEDOCUMENTO, PATERNO, "
        "MATERNO, NOMBRES, TIPO, DEPARTAMENTO, DNILIDER, GESTOR, CLASE, CLASIFICACION, etc.). "
        "Al subir un archivo nuevo, se actualiza (no se duplica) la informacion de "
        "cada LOGIN que ya exista."
        )
        fecha_maestro = st.date_input(
        "Fecha del corte / incentivo al que corresponde este maestro",
        value=date.today(),
        )
        archivo_maestro = st.file_uploader("Archivo del maestro (.xlsx)", type=["xlsx"], key="maestro")

        if archivo_maestro is not None:
            df_maestro_raw = pd.read_excel(archivo_maestro)

            columnas_necesarias = [
                "NUMERODEDOCUMENTO", "PATERNO", "MATERNO", "NOMBRES", "TIPO",
                "DEPARTAMENTO", "DNILIDER", "GESTOR", "CLASE", "CLASIFICACION",
            ]
            faltantes_maestro = [c for c in columnas_necesarias if c not in df_maestro_raw.columns]

            if faltantes_maestro:
                st.error(f"Faltan columnas en el archivo del maestro: {', '.join(faltantes_maestro)}")
            else:
                df_m = pd.DataFrame()
                df_m["LOGIN"] = df_maestro_raw["NUMERODEDOCUMENTO"].astype(str).str.strip()
                df_m["USERNAME"] = (
                    df_maestro_raw["PATERNO"].fillna("").astype(str).str.strip() + " " +
                    df_maestro_raw["MATERNO"].fillna("").astype(str).str.strip() + " " +
                    df_maestro_raw["NOMBRES"].fillna("").astype(str).str.strip()
                ).str.replace(r"\s+", " ", regex=True).str.strip()
                df_m["USERTYPE"] = df_maestro_raw["TIPO"].astype(str).str.strip()
                df_m["DEPARTMENT"] = df_maestro_raw["DEPARTAMENTO"].astype(str).str.strip().str.upper()
                df_m["LEADER"] = df_maestro_raw["DNILIDER"].astype(str).str.strip()
                df_m["GESTOR"] = df_maestro_raw["GESTOR"].astype(str).str.strip()
                df_m["FECHA"] = str(fecha_maestro)
                df_m["NIVEL"] = df_maestro_raw["CLASIFICACION"].astype(str).str.strip()
                df_m["_CLASE"] = df_maestro_raw["CLASE"].astype(str).str.strip().str.upper()

                # LEADER NAME: solo se busca entre las filas donde CLASE = LIDER
                # (no cualquier fila que por casualidad tenga ese DNI en User).
                solo_lideres = df_m[df_m["_CLASE"] == "LIDER"]
                nombre_por_login_lider = dict(zip(solo_lideres["LOGIN"], solo_lideres["USERNAME"]))
                df_m["LEADER NAME"] = df_m["LEADER"].map(nombre_por_login_lider).fillna("")
                df_m = df_m.drop(columns=["_CLASE"])

                st.write(f"Filas procesadas: {len(df_m)} | Lideres identificados: {len(solo_lideres)}")
                st.dataframe(df_m.head(10))

                if st.button("Confirmar carga del maestro"):
                    actualizados = db.cargar_maestro_participantes(df_m)
                    st.success(f"Maestro actualizado: {actualizados} participantes.")

        st.divider()
        with st.expander("Zona de peligro"):
            total_ventas_actual = db.contar_ventas()
            st.write(f"Ventas cargadas actualmente en la base de datos: **{total_ventas_actual}**")

            st.markdown("**Ver y borrar por fecha especifica (mas seguro)**")
            fechas_cargadas = db.listar_fechas_cargadas()
            if fechas_cargadas:
                df_fechas = pd.DataFrame(fechas_cargadas)
                st.dataframe(df_fechas)

                fecha_a_borrar = st.date_input("Elige una fecha para ver/borrar solo esas ventas", value=date.today())
                fecha_a_borrar_str = str(fecha_a_borrar)
                cantidad_en_fecha = db.contar_ventas_por_fecha(fecha_a_borrar_str)
                st.write(f"Ventas cargadas para {fecha_a_borrar_str}: **{cantidad_en_fecha}**")

                if cantidad_en_fecha > 0 and st.button(f"Borrar solo las ventas de {fecha_a_borrar_str}"):
                    borradas = db.borrar_ventas_por_fecha(fecha_a_borrar_str)
                    st.success(f"Se borraron {borradas} ventas de la fecha {fecha_a_borrar_str}.")
            else:
                st.caption("Todavia no hay ventas cargadas.")

            st.divider()
            st.markdown("**Borrar TODO (todas las fechas)**")
            st.caption(
                "Esto borra TODAS las ventas cargadas (no los incentivos ni sus resultados). "
                "Usalo solo si de verdad quieres empezar de cero."
            )
            confirmacion = st.text_input("Escribe BORRAR para confirmar")
            if st.button("Borrar todas las ventas cargadas", type="primary"):
                if confirmacion.strip().upper() == "BORRAR":
                    db.borrar_todas_las_ventas()
                    st.success("Todas las ventas fueron borradas. Ya puedes cargar tu archivo limpio.")
                else:
                    st.error("Escribe exactamente BORRAR (en mayusculas) en el cuadro de arriba para confirmar.")

# --- TAB 2: Crear incentivo ---
with tab2:
    st.subheader("Crear nuevo incentivo")

    nombre = st.text_input("Nombre del incentivo")
    departamentos_incentivo = st.multiselect("Departamento(s)", options=DEPARTAMENTOS)

    col1, col2 = st.columns(2)
    with col1:
        fecha_inicio = st.date_input("Fecha inicio", value=date.today())
        hora_inicio = st.time_input("Hora inicio", value=time(0, 0))
    with col2:
        fecha_fin = st.date_input("Fecha fin", value=date.today())
        hora_fin = st.time_input("Hora fin", value=time(23, 0))

    st.markdown("**Meta especifica del incentivo**")
    producto_meta = st.selectbox(
        "Producto meta",
        options=["PREPAGO", "POSTPAGO", "PORTA_PREPAGO", "OSS"],
    )

    col_meta, col_cupos = st.columns(2)
    with col_meta:
        unidades_objetivo = st.number_input("Unidades a realizar (meta)", min_value=1, step=1, value=10)
    with col_cupos:
        cupos = st.number_input("Cantidad de ganadores (cupos)", min_value=1, step=1, value=1)

    meta_fija = unidades_objetivo
    tipo_condicion = "META_FIJA"

    st.caption(
        f"Ganan los primeros {cupos} participante(s) en llegar a {unidades_objetivo} "
        f"unidades de {producto_meta}."
    )

    premio = st.text_input(
        "Premio (que gana cada ganador)",
        placeholder="Ej: 6 recargas prepago de S/25",
    )

    nivel_participante = st.selectbox(
        "Los participantes se identifican por",
        options=["LOGIN", "LEADER"],
        format_func=lambda x: "LOGIN (DNI del vendedor)" if x == "LOGIN" else "LEADER (nombre del lider)",
    )

    requiere_ur = False
    cuota_ur = None
    if producto_meta == "PREPAGO":
        requiere_ur = st.checkbox("¿Este incentivo tambien requiere una cuota de UR?")
        if requiere_ur:
            cuota_ur = st.number_input("Cuota UR por participante", min_value=1, step=1, value=1)
            st.caption(
                "Las unidades UR no vienen en el archivo de ventas -- se cargan aparte "
                "en la pestaña 'Calcular resultado', con una plantilla de DNI + UR."
            )

    requiere_plan_flexible = False
    plan_tarifario = None
    if producto_meta in ("PREPAGO", "POSTPAGO", "OSS"):
        requiere_plan_flexible = st.checkbox("¿Este incentivo requiere un Plan Flexible especifico?")
        if requiere_plan_flexible:
            opciones_plan = PLANES_PREPAGO if producto_meta == "PREPAGO" else PLANES_POSTPAGO_OSS
            plan_tarifario = st.selectbox("Plan tarifario", options=opciones_plan)
            st.caption(
                "El plan SI viene en el archivo de ventas (columna TARIFFPLANNAME), "
                "asi que este filtro se calcula automaticamente, igual que el resto."
            )

    st.markdown("**Lista de participantes**")

    multi_depto = len(departamentos_incentivo) > 1

    if multi_depto:
        st.caption(
            "Como elegiste mas de un departamento, el archivo debe tener DOS columnas: "
            "DNI (o nombre del lider) y DEPARTAMENTO. Cada participante compite solo en "
            "el departamento que le asignes."
        )
        plantilla_part = pd.DataFrame({
            "DNI": [],
            "DEPARTAMENTO": [],
        })
        buffer_part = io.BytesIO()
        plantilla_part.to_excel(buffer_part, index=False, engine="openpyxl")
        buffer_part.seek(0)
        st.download_button(
            "Descargar plantilla de participantes (DNI + DEPARTAMENTO)",
            data=buffer_part,
            file_name="plantilla_participantes.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        archivo_participantes = st.file_uploader("Archivo de participantes (.xlsx)", type=["xlsx"], key="participantes")
        participantes_manual = ""  # el texto manual no soporta multi-departamento
    else:
        st.caption("Sube un excel con una sola columna de identificadores (DNI si elegiste LOGIN, "
                   "o el nombre exacto del lider si elegiste LEADER), o escribelos manualmente abajo.")
        archivo_participantes = st.file_uploader("Archivo de participantes (.xlsx)", type=["xlsx"], key="participantes")
        participantes_manual = st.text_area("O escribe los identificadores separados por coma")

    if st.button("Crear incentivo"):
        if not nombre:
            st.error("Ponle un nombre al incentivo.")
        elif not departamentos_incentivo:
            st.error("Selecciona al menos un departamento.")
        else:
            participantes_con_depto = []  # lista de (identificador, departamento)
            error_carga = None

            if archivo_participantes is not None:
                df_part = pd.read_excel(archivo_participantes)
                df_part.columns = [c.strip().upper() for c in df_part.columns]

                if multi_depto:
                    if "DNI" not in df_part.columns or "DEPARTAMENTO" not in df_part.columns:
                        error_carga = "El archivo debe tener las columnas DNI y DEPARTAMENTO."
                    else:
                        invalidos = set(df_part["DEPARTAMENTO"].dropna().astype(str).str.strip()) - set(departamentos_incentivo)
                        if invalidos:
                            error_carga = f"Estos departamentos del archivo no fueron elegidos para el incentivo: {', '.join(invalidos)}"
                        else:
                            for _, fila in df_part.dropna(subset=["DNI", "DEPARTAMENTO"]).iterrows():
                                participantes_con_depto.append((str(fila["DNI"]).strip(), str(fila["DEPARTAMENTO"]).strip()))
                else:
                    primera_columna = df_part.columns[0]
                    unico_depto = departamentos_incentivo[0]
                    for ident in df_part[primera_columna].dropna().astype(str).str.strip().tolist():
                        participantes_con_depto.append((ident, unico_depto))

            if participantes_manual and not multi_depto:
                unico_depto = departamentos_incentivo[0]
                for ident in [x.strip() for x in participantes_manual.split(",") if x.strip()]:
                    participantes_con_depto.append((ident, unico_depto))

            if error_carga:
                st.error(error_carga)
            elif not participantes_con_depto:
                st.error("Agrega al menos un participante (archivo o texto manual).")
            elif len(participantes_con_depto) > 2000:
                st.error(
                    f"El archivo trae {len(participantes_con_depto)} identificadores -- eso es "
                    f"mucho para una lista de participantes de un incentivo. Probablemente subiste "
                    f"el archivo de VENTAS por error, en vez de la plantilla de participantes. "
                    f"Revisa el archivo antes de continuar."
                )
            else:
                incentivo_id = db.crear_incentivo(
                    nombre, fecha_inicio, hora_inicio, fecha_fin, hora_fin,
                    tipo_condicion, meta_fija, nivel_participante, [producto_meta],
                    producto_meta, cupos, premio,
                    json.dumps(departamentos_incentivo),
                    requiere_ur, cuota_ur,
                    requiere_plan_flexible, plan_tarifario,
                )
                db.agregar_participantes(incentivo_id, participantes_con_depto)
                st.success(f"Incentivo '{nombre}' creado con {len(participantes_con_depto)} participantes (ID {incentivo_id}).")

# --- TAB 3: Calcular resultado ---
with tab3:
    st.subheader("Calcular resultado de un incentivo")

    incentivos = db.listar_incentivos()
    if not incentivos:
        st.info("Todavia no has creado ningun incentivo.")
    else:
        opciones = {
            f"[{i['id']}] {i['nombre']} - {', '.join(parsear_departamentos(i.get('departamento'))) or '-'} ({i['estado']})": i["id"]
            for i in incentivos
        }
        seleccion = st.selectbox("Elige un incentivo", options=list(opciones.keys()))
        incentivo_id = opciones[seleccion]
        incentivo = db.obtener_incentivo(incentivo_id)

        st.write(f"**Departamento(s):** {', '.join(parsear_departamentos(incentivo.get('departamento'))) or '-'}")
        st.write(f"**Rango:** {incentivo['fecha_inicio']} {incentivo['hora_inicio']} -> {incentivo['fecha_fin']} {incentivo['hora_fin']}")
        st.write(f"**Condicion:** {incentivo['tipo_condicion']}" + (f" (meta: {incentivo['meta_fija']})" if incentivo['meta_fija'] else ""))
        st.write(f"**Nivel:** {incentivo['nivel_participante']}")

        st.write(f"**Producto meta:** {incentivo.get('producto_meta', '-')}")
        if incentivo.get("meta_fija"):
            st.write(f"**Unidades objetivo:** {incentivo['meta_fija']}")
        st.write(f"**Cupos (cantidad de ganadores):** {incentivo.get('cupos', 1)}")
        if incentivo.get("requiere_plan_flexible") and incentivo.get("plan_tarifario"):
            st.write(f"**Plan tarifario (Plan Flexible):** {incentivo['plan_tarifario']}")
        if incentivo.get("premio"):
            st.write(f"**Premio:** {incentivo['premio']}")

        with st.expander("Editar este incentivo"):
            st.caption(
                "Puedes corregir estos datos sin borrar el incentivo. El departamento, "
                "el nivel de participante y la lista de participantes NO se pueden "
                "editar aqui -- si necesitas cambiar eso, crea un incentivo nuevo."
            )

            e_nombre = st.text_input("Nombre", value=incentivo["nombre"], key=f"e_nombre_{incentivo_id}")

            col_ei, col_ef = st.columns(2)
            with col_ei:
                e_fecha_inicio = st.date_input(
                    "Fecha inicio", value=pd.to_datetime(incentivo["fecha_inicio"]).date(), key=f"e_fi_{incentivo_id}"
                )
                e_hora_inicio = st.time_input(
                    "Hora inicio", value=pd.to_datetime(incentivo["hora_inicio"]).time(), key=f"e_hi_{incentivo_id}"
                )
            with col_ef:
                e_fecha_fin = st.date_input(
                    "Fecha fin", value=pd.to_datetime(incentivo["fecha_fin"]).date(), key=f"e_ff_{incentivo_id}"
                )
                e_hora_fin = st.time_input(
                    "Hora fin", value=pd.to_datetime(incentivo["hora_fin"]).time(), key=f"e_hf_{incentivo_id}"
                )

            e_producto_meta = st.selectbox(
                "Producto meta",
                options=["PREPAGO", "POSTPAGO", "PORTA_PREPAGO", "OSS"],
                index=["PREPAGO", "POSTPAGO", "PORTA_PREPAGO", "OSS"].index(incentivo["producto_meta"]),
                key=f"e_pm_{incentivo_id}",
            )

            col_em, col_ec = st.columns(2)
            with col_em:
                e_meta_fija = st.number_input(
                    "Unidades a realizar (meta)", min_value=1, step=1,
                    value=int(incentivo["meta_fija"] or 1), key=f"e_meta_{incentivo_id}",
                )
            with col_ec:
                e_cupos = st.number_input(
                    "Cantidad de ganadores (cupos)", min_value=1, step=1,
                    value=int(incentivo.get("cupos") or 1), key=f"e_cupos_{incentivo_id}",
                )

            e_premio = st.text_input("Premio", value=incentivo.get("premio") or "", key=f"e_premio_{incentivo_id}")

            e_requiere_ur = False
            e_cuota_ur = incentivo.get("cuota_ur")
            if e_producto_meta == "PREPAGO":
                e_requiere_ur = st.checkbox(
                    "Requiere cuota de UR", value=bool(incentivo.get("requiere_ur")), key=f"e_ur_check_{incentivo_id}"
                )
                if e_requiere_ur:
                    e_cuota_ur = st.number_input(
                        "Cuota UR", min_value=1, step=1,
                        value=int(incentivo.get("cuota_ur") or 1), key=f"e_ur_cuota_{incentivo_id}",
                    )

            e_requiere_plan = False
            e_plan_tarifario = incentivo.get("plan_tarifario")
            if e_producto_meta in ("PREPAGO", "POSTPAGO", "OSS"):
                e_requiere_plan = st.checkbox(
                    "Requiere Plan Flexible especifico", value=bool(incentivo.get("requiere_plan_flexible")),
                    key=f"e_plan_check_{incentivo_id}",
                )
                if e_requiere_plan:
                    opciones_plan_e = PLANES_PREPAGO if e_producto_meta == "PREPAGO" else PLANES_POSTPAGO_OSS
                    valor_actual_plan = incentivo.get("plan_tarifario") or opciones_plan_e[0]
                    if valor_actual_plan not in opciones_plan_e:
                        valor_actual_plan = opciones_plan_e[0]
                    e_plan_tarifario = st.selectbox(
                        "Plan tarifario", options=opciones_plan_e,
                        index=opciones_plan_e.index(valor_actual_plan), key=f"e_plan_sel_{incentivo_id}",
                    )

            if st.button("Guardar cambios", key=f"e_guardar_{incentivo_id}"):
                db.actualizar_incentivo(
                    incentivo_id, e_nombre, e_fecha_inicio, e_hora_inicio, e_fecha_fin, e_hora_fin,
                    e_producto_meta, e_meta_fija, e_cupos, e_premio,
                    e_requiere_ur, e_cuota_ur if e_requiere_ur else None,
                    e_requiere_plan, e_plan_tarifario if e_requiere_plan else None,
                )
                st.success("Incentivo actualizado. Vuelve a darle 'Calcular ganador' para aplicar los cambios.")
                st.rerun()

        participantes_con_depto = db.obtener_participantes(incentivo_id)
        lista_dnis = [p[0] for p in participantes_con_depto]

        if st.button("Calcular ganador"):
            resultados = calcular_incentivo(incentivo)

            if not resultados:
                st.warning(
                    "No se encontraron participantes para este incentivo. Verifica que "
                    "la lista de participantes se haya cargado bien (pestaña 2) y que "
                    "esten asignados a los departamentos correctos."
                )
            else:
                db.guardar_resultados(incentivo_id, resultados)

                df_resultados = pd.DataFrame(resultados)

                # Enriquecer con el maestro de participantes (nombre, lider, gestor, etc.)
                identificadores_resultado = df_resultados["identificador"].astype(str).tolist()
                datos_maestro = db.obtener_datos_maestro(identificadores_resultado)

                columnas_maestro = ["username", "usertype", "department", "leader", "leader_name", "gestor", "nivel"]
                for col in columnas_maestro:
                    df_resultados[col] = df_resultados["identificador"].astype(str).apply(
                        lambda ident: datos_maestro.get(ident, {}).get(col, "")
                    )

                st.dataframe(df_resultados)

                df_ganadores = df_resultados[df_resultados["gano"] == True].copy()

                buffer_resultados = io.BytesIO()
                df_ganadores.to_excel(buffer_resultados, index=False, engine="openpyxl")
                buffer_resultados.seek(0)
                st.download_button(
                    "Descargar ganadores en Excel",
                    data=buffer_resultados,
                    file_name=f"ganadores_incentivo_{incentivo_id}_{incentivo['nombre']}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    disabled=df_ganadores.empty,
                )

                ganadores = [r for r in resultados if r["gano"]]
                if ganadores:
                    deptos_con_ganadores = sorted(set(g["departamento"] for g in ganadores))
                    for depto in deptos_con_ganadores:
                        nombres = ", ".join(g["identificador"] for g in ganadores if g["departamento"] == depto)
                        st.success(f"**{depto}** -- Ganador(es): {nombres}")
                else:
                    st.warning("Nadie cumplio la condicion todavia, en ningun departamento.")

        # --- Seccion de UR (solo si este incentivo la requiere) ---
        if incentivo.get("requiere_ur"):
            st.divider()
            st.markdown(f"**Cuota UR** -- meta: {incentivo.get('cuota_ur')} unidades por participante")

            # Plantilla descargable con los DNI ya precargados
            plantilla = pd.DataFrame({"DNI": lista_dnis, "UR": [0] * len(lista_dnis)})
            buffer = io.BytesIO()
            plantilla.to_excel(buffer, index=False, engine="openpyxl")
            buffer.seek(0)

            st.download_button(
                "Descargar plantilla UR (DNI precargados)",
                data=buffer,
                file_name=f"plantilla_ur_incentivo_{incentivo_id}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

            archivo_ur = st.file_uploader(
                "Subir plantilla UR completada (columnas DNI y UR)",
                type=["xlsx"], key=f"ur_{incentivo_id}",
            )

            if archivo_ur is not None:
                df_ur_subido = pd.read_excel(archivo_ur)
                df_ur_subido.columns = [c.strip().upper() for c in df_ur_subido.columns]

                if "DNI" not in df_ur_subido.columns or "UR" not in df_ur_subido.columns:
                    st.error("El archivo debe tener las columnas DNI y UR.")
                else:
                    st.dataframe(df_ur_subido)
                    if st.button("Guardar UR"):
                        db.guardar_ur(incentivo_id, df_ur_subido, incentivo["cuota_ur"])
                        st.success("UR guardada.")

            resultados_ur = db.obtener_ur(incentivo_id)
            if resultados_ur:
                st.write("**Resultado UR:**")
                df_res_ur = pd.DataFrame(resultados_ur)[["dni", "unidades_ur", "cumplio_cuota"]]
                df_res_ur["cumplio_cuota"] = df_res_ur["cumplio_cuota"].map({1: "Si", 0: "No"})
                st.dataframe(df_res_ur)

# --- TAB 4: Historial ---
with tab4:
    st.subheader("Historial de incentivos")
    incentivos = db.listar_incentivos()
    if not incentivos:
        st.info("Todavia no hay incentivos registrados.")
    else:
        filtro_depto = st.selectbox("Filtrar por departamento", options=["Todos"] + DEPARTAMENTOS)
        if filtro_depto != "Todos":
            incentivos = [i for i in incentivos if filtro_depto in parsear_departamentos(i.get("departamento"))]

        if not incentivos:
            st.info(f"No hay incentivos registrados para {filtro_depto}.")

        for inc in incentivos:
            deptos_texto = ", ".join(parsear_departamentos(inc.get("departamento"))) or "-"
            with st.expander(f"[{inc['id']}] {inc['nombre']} - {deptos_texto} - {inc['estado']}"):
                st.write(f"Rango: {inc['fecha_inicio']} {inc['hora_inicio']} -> {inc['fecha_fin']} {inc['hora_fin']}")
                st.write(f"Producto meta: {inc.get('producto_meta', '-')} | Meta: {inc.get('meta_fija')} | Cupos: {inc.get('cupos', 1)}")
                if inc.get("requiere_plan_flexible") and inc.get("plan_tarifario"):
                    st.write(f"Plan tarifario: {inc['plan_tarifario']}")
                if inc.get("premio"):
                    st.write(f"Premio: {inc['premio']}")
                resultados = db.obtener_resultados(inc["id"])
                if resultados:
                    st.dataframe(pd.DataFrame(resultados))
                else:
                    st.caption("Sin resultados calculados todavia.")
