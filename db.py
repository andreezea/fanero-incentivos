"""
db.py -- Capa de base de datos (Postgres/Supabase) para la app de incentivos Fanero.

Se migro de SQLite a Postgres para que los datos sobrevivan cuando la app
se publica en Streamlit Community Cloud (que borra los archivos locales
cada vez que la app se reinicia).
"""

import os
import json
import hashlib
import unicodedata
from datetime import datetime

import psycopg2
import psycopg2.extras

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def get_connection():
    """Lee la connection string desde una variable de entorno DATABASE_URL,
    o desde st.secrets si esta corriendo dentro de Streamlit Cloud."""
    conn_string = os.environ.get("DATABASE_URL")
    if not conn_string:
        try:
            import streamlit as st
            conn_string = st.secrets["DATABASE_URL"]
        except Exception:
            pass
    if not conn_string:
        raise RuntimeError(
            "No se encontro DATABASE_URL. Configuralo en .env (local) o en "
            "Secrets (Streamlit Cloud)."
        )
    conn = psycopg2.connect(conn_string, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS ventas (
            id BIGSERIAL PRIMARY KEY,
            row_hash TEXT,
            activationtype TEXT,
            status TEXT,
            fecha TEXT,
            hora INTEGER,
            login TEXT,
            partner TEXT,
            leader TEXT,
            usertype TEXT,
            department TEXT,
            district TEXT,
            tipo_activacion TEXT,
            portabilidad TEXT,
            tariffplanname TEXT,
            producto TEXT,
            es_valida INTEGER,
            cargado_en TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS archivos_cargados (
            id BIGSERIAL PRIMARY KEY,
            file_hash TEXT UNIQUE,
            file_name TEXT,
            record_count INTEGER,
            cargado_en TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS incentivos (
            id BIGSERIAL PRIMARY KEY,
            nombre TEXT NOT NULL,
            fecha_inicio TEXT NOT NULL,
            hora_inicio TEXT NOT NULL,
            fecha_fin TEXT NOT NULL,
            hora_fin TEXT NOT NULL,
            tipo_condicion TEXT NOT NULL,
            meta_fija INTEGER,
            producto_meta TEXT,
            es_manual INTEGER NOT NULL DEFAULT 0,
            cupos INTEGER NOT NULL DEFAULT 1,
            premio TEXT,
            departamento TEXT,
            nivel_participante TEXT NOT NULL,
            productos TEXT NOT NULL,
            requiere_ur INTEGER NOT NULL DEFAULT 0,
            cuota_ur INTEGER,
            requiere_plan_flexible INTEGER NOT NULL DEFAULT 0,
            plan_tarifario TEXT,
            estado TEXT NOT NULL DEFAULT 'ACTIVO',
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS incentivo_participantes (
            id BIGSERIAL PRIMARY KEY,
            incentivo_id BIGINT NOT NULL REFERENCES incentivos(id),
            identificador TEXT NOT NULL,
            departamento TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS incentivo_resultados (
            id BIGSERIAL PRIMARY KEY,
            incentivo_id BIGINT NOT NULL REFERENCES incentivos(id),
            identificador TEXT NOT NULL,
            departamento TEXT,
            ventas_contadas INTEGER NOT NULL,
            gano INTEGER NOT NULL DEFAULT 0,
            momento_meta TEXT,
            calculado_en TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS incentivo_ur (
            id BIGSERIAL PRIMARY KEY,
            incentivo_id BIGINT NOT NULL REFERENCES incentivos(id),
            dni TEXT NOT NULL,
            unidades_ur INTEGER NOT NULL,
            cumplio_cuota INTEGER NOT NULL,
            cargado_en TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS maestro_participantes (
            login TEXT PRIMARY KEY,
            fecha TEXT,
            username TEXT,
            usertype TEXT,
            department TEXT,
            leader TEXT,
            leader_name TEXT,
            gestor TEXT,
            nivel TEXT,
            actualizado_en TEXT
        )
    """)

    conn.commit()

    # Normalizacion (sin tildes, mayusculas) del campo department, por si
    # se cargaron ventas antes de aplicar esta correccion.
    cur.execute("SELECT DISTINCT department FROM ventas WHERE department IS NOT NULL")
    valores_actuales = [r["department"] for r in cur.fetchall()]

    def _normalizar(valor):
        texto = str(valor).strip().upper()
        return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("utf-8")

    for valor in valores_actuales:
        normalizado = _normalizar(valor)
        if normalizado != valor:
            cur.execute("UPDATE ventas SET department = %s WHERE department = %s", (normalizado, valor))

    conn.commit()
    cur.close()
    conn.close()


def _row_hash(row: dict) -> str:
    base = "|".join(str(row.get(k, "")) for k in sorted(row.keys()))
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def calcular_hashes_de_dataframe(df) -> list:
    """Calcula el row_hash de cada fila de un DataFrame ya normalizado,
    usando la misma logica que insertar_ventas -- para poder avisar de
    posibles duplicados ANTES de insertar."""
    return [_row_hash(row.to_dict()) for _, row in df.iterrows()]


def calcular_hash_archivo(archivo_bytes: bytes) -> str:
    return hashlib.sha256(archivo_bytes).hexdigest()


def archivo_ya_cargado(file_hash: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT file_name, cargado_en, record_count FROM archivos_cargados WHERE file_hash = %s", (file_hash,))
    fila = cur.fetchone()
    cur.close()
    conn.close()
    return dict(fila) if fila else None


def insertar_ventas(df, file_hash=None, file_name=None):
    """Inserta TODAS las filas del DataFrame, sin descartar ninguna por
    contenido repetido. La proteccion contra "subir el mismo archivo dos
    veces" se hace a nivel de ARCHIVO COMPLETO, via file_hash."""
    conn = get_connection()
    cur = conn.cursor()
    ahora = datetime.now().isoformat()

    filas = []
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        filas.append((
            _row_hash(row_dict),
            row_dict.get("activationtype"),
            row_dict.get("status"),
            row_dict.get("fecha"),
            row_dict.get("hora"),
            row_dict.get("login"),
            row_dict.get("partner"),
            row_dict.get("leader"),
            row_dict.get("usertype"),
            row_dict.get("department"),
            row_dict.get("district"),
            row_dict.get("tipo_activacion"),
            row_dict.get("portabilidad"),
            row_dict.get("tariffplanname"),
            row_dict.get("producto"),
            row_dict.get("es_valida"),
            ahora,
        ))

    psycopg2.extras.execute_batch(cur, """
        INSERT INTO ventas (
            row_hash, activationtype, status, fecha, hora, login,
            partner, leader, usertype, department, district,
            tipo_activacion, portabilidad, tariffplanname, producto, es_valida, cargado_en
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, filas, page_size=1000)

    insertadas = len(filas)

    if file_hash:
        cur.execute("""
            INSERT INTO archivos_cargados (file_hash, file_name, record_count, cargado_en)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (file_hash) DO NOTHING
        """, (file_hash, file_name, insertadas, ahora))

    conn.commit()
    cur.close()
    conn.close()
    return insertadas, 0


def crear_incentivo(nombre, fecha_inicio, hora_inicio, fecha_fin, hora_fin,
                     tipo_condicion, meta_fija, nivel_participante, productos,
                     producto_meta, cupos, premio, departamento,
                     requiere_ur, cuota_ur, requiere_plan_flexible, plan_tarifario):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO incentivos (
            nombre, fecha_inicio, hora_inicio, fecha_fin, hora_fin,
            tipo_condicion, meta_fija, producto_meta, cupos, premio,
            departamento, nivel_participante, productos, requiere_ur, cuota_ur,
            requiere_plan_flexible, plan_tarifario,
            estado, created_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVO', %s)
        RETURNING id
    """, (
        nombre, str(fecha_inicio), str(hora_inicio), str(fecha_fin), str(hora_fin),
        tipo_condicion, meta_fija, producto_meta, cupos, premio,
        departamento, nivel_participante, json.dumps(productos),
        int(requiere_ur), cuota_ur,
        int(requiere_plan_flexible), plan_tarifario,
        datetime.now().isoformat(),
    ))
    incentivo_id = cur.fetchone()["id"]
    conn.commit()
    cur.close()
    conn.close()
    return incentivo_id


def agregar_participantes(incentivo_id, participantes_con_depto):
    conn = get_connection()
    cur = conn.cursor()
    filas = [
        (incentivo_id, str(ident).strip(), depto)
        for ident, depto in participantes_con_depto
        if str(ident).strip()
    ]
    psycopg2.extras.execute_batch(cur, """
        INSERT INTO incentivo_participantes (incentivo_id, identificador, departamento)
        VALUES (%s, %s, %s)
    """, filas, page_size=1000)
    conn.commit()
    cur.close()
    conn.close()


def obtener_participantes(incentivo_id, departamento=None):
    conn = get_connection()
    cur = conn.cursor()
    if departamento:
        cur.execute(
            "SELECT identificador FROM incentivo_participantes WHERE incentivo_id = %s AND departamento = %s",
            (incentivo_id, departamento),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [r["identificador"] for r in rows]
    else:
        cur.execute(
            "SELECT identificador, departamento FROM incentivo_participantes WHERE incentivo_id = %s",
            (incentivo_id,),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return [(r["identificador"], r["departamento"]) for r in rows]


def obtener_incentivo(incentivo_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM incentivos WHERE id = %s", (incentivo_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None


def actualizar_incentivo(incentivo_id, nombre, fecha_inicio, hora_inicio, fecha_fin, hora_fin,
                          producto_meta, meta_fija, cupos, premio,
                          requiere_ur, cuota_ur, requiere_plan_flexible, plan_tarifario):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE incentivos SET
            nombre = %s, fecha_inicio = %s, hora_inicio = %s, fecha_fin = %s, hora_fin = %s,
            producto_meta = %s, meta_fija = %s, cupos = %s, premio = %s,
            requiere_ur = %s, cuota_ur = %s, requiere_plan_flexible = %s, plan_tarifario = %s
        WHERE id = %s
    """, (
        nombre, str(fecha_inicio), str(hora_inicio), str(fecha_fin), str(hora_fin),
        producto_meta, meta_fija, cupos, premio,
        int(requiere_ur), cuota_ur, int(requiere_plan_flexible), plan_tarifario,
        incentivo_id,
    ))
    conn.commit()
    cur.close()
    conn.close()


def listar_incentivos():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM incentivos ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def guardar_resultados(incentivo_id, resultados):
    conn = get_connection()
    cur = conn.cursor()
    ahora = datetime.now().isoformat()

    cur.execute("DELETE FROM incentivo_resultados WHERE incentivo_id = %s", (incentivo_id,))

    for r in resultados:
        cur.execute("""
            INSERT INTO incentivo_resultados (
                incentivo_id, identificador, departamento, ventas_contadas, gano, momento_meta, calculado_en
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            incentivo_id, r["identificador"], r.get("departamento"), r["ventas_contadas"],
            1 if r["gano"] else 0, r.get("momento_meta"), ahora,
        ))

    cur.execute("UPDATE incentivos SET estado = 'FINALIZADO' WHERE id = %s", (incentivo_id,))
    conn.commit()
    cur.close()
    conn.close()


def obtener_resultados(incentivo_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM incentivo_resultados
        WHERE incentivo_id = %s
        ORDER BY ventas_contadas DESC
    """, (incentivo_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def obtener_ventas_en_rango(fecha_inicio, hora_inicio, fecha_fin, hora_fin, productos, nivel, departamentos=None, plan_tarifario=None):
    conn = get_connection()
    cur = conn.cursor()
    placeholders = ",".join("%s" for _ in productos)
    query = f"""
        SELECT * FROM ventas
        WHERE es_valida = 1
        AND producto IN ({placeholders})
        AND (
            (fecha > %s OR (fecha = %s AND hora >= %s))
            AND
            (fecha < %s OR (fecha = %s AND hora <= %s))
        )
    """
    params = list(productos) + [
        fecha_inicio, fecha_inicio, hora_inicio,
        fecha_fin, fecha_fin, hora_fin,
    ]

    if departamentos:
        placeholders_depto = ",".join("%s" for _ in departamentos)
        query += f" AND UPPER(department) IN ({placeholders_depto})"
        params.extend([d.upper() for d in departamentos])

    if plan_tarifario and plan_tarifario.upper() != "TODOS":
        query += " AND UPPER(tariffplanname) = %s"
        params.append(plan_tarifario.upper())

    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def guardar_ur(incentivo_id, df_ur, cuota_ur):
    conn = get_connection()
    cur = conn.cursor()
    ahora = datetime.now().isoformat()

    cur.execute("DELETE FROM incentivo_ur WHERE incentivo_id = %s", (incentivo_id,))

    for _, row in df_ur.iterrows():
        dni = str(row["DNI"]).strip()
        unidades = int(row["UR"])
        cumplio = 1 if unidades >= cuota_ur else 0
        cur.execute("""
            INSERT INTO incentivo_ur (incentivo_id, dni, unidades_ur, cumplio_cuota, cargado_en)
            VALUES (%s, %s, %s, %s, %s)
        """, (incentivo_id, dni, unidades, cumplio, ahora))

    conn.commit()
    cur.close()
    conn.close()


def obtener_ur(incentivo_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM incentivo_ur WHERE incentivo_id = %s ORDER BY unidades_ur DESC", (incentivo_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def cargar_maestro_participantes(df):
    """df debe tener columnas: FECHA, LOGIN, USERNAME, USERTYPE, DEPARTMENT,
    LEADER, LEADER NAME, GESTOR, NIVEL. Actualiza (upsert) por LOGIN, asi
    que subir una version nueva actualiza los datos existentes sin duplicar."""
    conn = get_connection()
    cur = conn.cursor()
    ahora = datetime.now().isoformat()

    filas = []
    for _, row in df.iterrows():
        login = str(row.get("LOGIN", "")).strip()
        if not login or login.upper() == "NAN":
            continue
        filas.append((
            login,
            str(row.get("FECHA", "")).strip(),
            str(row.get("USERNAME", "")).strip(),
            str(row.get("USERTYPE", "")).strip(),
            str(row.get("DEPARTMENT", "")).strip(),
            str(row.get("LEADER", "")).strip(),
            str(row.get("LEADER NAME", "")).strip(),
            str(row.get("GESTOR", "")).strip(),
            str(row.get("NIVEL", "")).strip(),
            ahora,
        ))

    psycopg2.extras.execute_batch(cur, """
        INSERT INTO maestro_participantes (
            login, fecha, username, usertype, department, leader, leader_name, gestor, nivel, actualizado_en
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (login) DO UPDATE SET
            fecha = EXCLUDED.fecha,
            username = EXCLUDED.username,
            usertype = EXCLUDED.usertype,
            department = EXCLUDED.department,
            leader = EXCLUDED.leader,
            leader_name = EXCLUDED.leader_name,
            gestor = EXCLUDED.gestor,
            nivel = EXCLUDED.nivel,
            actualizado_en = EXCLUDED.actualizado_en
    """, filas, page_size=1000)

    conn.commit()
    cur.close()
    conn.close()
    return len(filas)


def obtener_datos_maestro(identificadores):
    """Trae los datos del maestro para una lista de identificadores.
    Busca primero por LOGIN; si no encuentra, intenta por LEADER (para
    incentivos donde el nivel de participante es LEADER, no LOGIN).
    Devuelve un diccionario {identificador: {datos...}}."""
    if not identificadores:
        return {}

    conn = get_connection()
    cur = conn.cursor()
    placeholders = ",".join("%s" for _ in identificadores)

    resultado = {}

    cur.execute(f"SELECT * FROM maestro_participantes WHERE login IN ({placeholders})", identificadores)
    for fila in cur.fetchall():
        resultado[fila["login"]] = dict(fila)

    faltantes = [i for i in identificadores if i not in resultado]
    if faltantes:
        placeholders2 = ",".join("%s" for _ in faltantes)
        cur.execute(f"SELECT DISTINCT ON (leader) * FROM maestro_participantes WHERE leader IN ({placeholders2})", faltantes)
        for fila in cur.fetchall():
            resultado[fila["leader"]] = dict(fila)

    cur.close()
    conn.close()
    return resultado


def contar_hashes_existentes(row_hashes: list) -> int:
    """Cuenta cuantos de estos row_hash YA existen en la tabla ventas --
    para avisar antes de insertar si el archivo trae filas identicas a
    ventas que ya se cargaron (posible carga repetida o archivo solapado)."""
    if not row_hashes:
        return 0
    conn = get_connection()
    cur = conn.cursor()
    placeholders = ",".join("%s" for _ in row_hashes)
    cur.execute(f"SELECT count(*) as n FROM ventas WHERE row_hash IN ({placeholders})", row_hashes)
    n = cur.fetchone()["n"]
    cur.close()
    conn.close()
    return n


def obtener_hashes_existentes(row_hashes: list) -> list:
    """Devuelve la lista de row_hash (de los que se le paso) que YA existen
    en la tabla ventas, para poder excluir esas filas especificas antes de insertar."""
    if not row_hashes:
        return []
    conn = get_connection()
    cur = conn.cursor()
    placeholders = ",".join("%s" for _ in row_hashes)
    cur.execute(f"SELECT DISTINCT row_hash FROM ventas WHERE row_hash IN ({placeholders})", row_hashes)
    resultado = [r["row_hash"] for r in cur.fetchall()]
    cur.close()
    conn.close()
    return resultado


def contar_ventas() -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT count(*) as n FROM ventas")
    n = cur.fetchone()["n"]
    cur.close()
    conn.close()
    return n


def contar_ventas_por_fecha(fecha: str) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT count(*) as n FROM ventas WHERE fecha = %s", (fecha,))
    n = cur.fetchone()["n"]
    cur.close()
    conn.close()
    return n


def listar_fechas_cargadas() -> list:
    """Devuelve cada fecha con ventas cargadas y cuantas filas tiene."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT fecha, count(*) as cantidad
        FROM ventas
        GROUP BY fecha
        ORDER BY fecha DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def borrar_ventas_por_fecha(fecha: str) -> int:
    """Borra solo las ventas de UNA fecha especifica. Devuelve cuantas se borraron."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM ventas WHERE fecha = %s", (fecha,))
    borradas = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    return borradas


def borrar_todas_las_ventas():
    """Borra TODAS las filas de ventas y el historial de archivos cargados.
    No toca incentivos, participantes, ni resultados ya calculados."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM ventas")
    cur.execute("DELETE FROM archivos_cargados")
    conn.commit()
    cur.close()
    conn.close()
