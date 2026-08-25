"""
diagnostico_postgres.py -- Revisa el estado real de la base de datos en Neon,
sin modificar nada. Correr con: python diagnostico_postgres.py
"""

import os
import psycopg2
import psycopg2.extras

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DNI_A_REVISAR = "76368953"
FECHA_A_REVISAR = "2026-08-14"

conn_string = os.environ.get("DATABASE_URL")
conn = psycopg2.connect(conn_string, cursor_factory=psycopg2.extras.RealDictCursor)
cur = conn.cursor()

print("=" * 70)
print(f"1. TODAS LAS FILAS DE VENTAS PARA EL LOGIN {DNI_A_REVISAR} EL {FECHA_A_REVISAR}")
print("=" * 70)
cur.execute("""
    SELECT id, login, fecha, hora, producto, tipo_activacion, portabilidad,
           tariffplanname, department, es_valida, cargado_en, row_hash
    FROM ventas
    WHERE login = %s AND fecha = %s
    ORDER BY hora
""", (DNI_A_REVISAR, FECHA_A_REVISAR))
filas = cur.fetchall()
print(f"Total filas encontradas: {len(filas)}")
for f in filas:
    print(dict(f))

print()
print("=" * 70)
print("2. SOLO LAS VALIDAS Y DE PRODUCTO PORTA_PREPAGO (lo que cuenta el incentivo)")
print("=" * 70)
cur.execute("""
    SELECT id, login, fecha, hora, producto, es_valida, cargado_en
    FROM ventas
    WHERE login = %s AND fecha = %s AND producto = 'PORTA_PREPAGO' AND es_valida = 1
    ORDER BY hora
""", (DNI_A_REVISAR, FECHA_A_REVISAR))
filas_validas = cur.fetchall()
print(f"Total filas validas PORTA_PREPAGO: {len(filas_validas)}")
for f in filas_validas:
    print(dict(f))

print()
print("=" * 70)
print("3. CUANTAS VECES SE HA CARGADO UN ARCHIVO DE VENTAS (revisar si hay cargas repetidas)")
print("=" * 70)
cur.execute("SELECT file_name, record_count, cargado_en FROM archivos_cargados ORDER BY cargado_en")
for f in cur.fetchall():
    print(dict(f))

print()
print("=" * 70)
print("4. TOTAL DE FILAS EN row_hash duplicado para este DNI (mismo contenido exacto mas de una vez)")
print("=" * 70)
cur.execute("""
    SELECT row_hash, count(*) as veces
    FROM ventas
    WHERE login = %s AND fecha = %s
    GROUP BY row_hash
    HAVING count(*) > 1
""", (DNI_A_REVISAR, FECHA_A_REVISAR))
duplicados = cur.fetchall()
if duplicados:
    print(f"Se encontraron {len(duplicados)} row_hash con mas de una fila identica:")
    for d in duplicados:
        print(dict(d))
else:
    print("No hay filas con contenido exactamente identico repetido para este DNI/fecha.")

cur.close()
conn.close()
