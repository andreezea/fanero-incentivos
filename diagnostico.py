"""
diagnostico.py -- Revisa el estado real de la base de datos, sin modificar nada.
Correr con: python diagnostico.py  (o: py diagnostico.py)
"""

import sqlite3

conn = sqlite3.connect("fanero_incentivos.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=" * 60)
print("1. ESQUEMA ACTUAL DE LA TABLA 'ventas'")
print("=" * 60)
cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='ventas'")
fila = cur.fetchone()
if fila:
    print(fila["sql"])
    if "UNIQUE" in fila["sql"]:
        print("\n*** ALERTA: la tabla 'ventas' TODAVIA tiene la restriccion vieja (UNIQUE). ***")
        print("*** La migracion no se aplico. Avisa esto en el chat. ***")
    else:
        print("\nOK: la tabla ya NO tiene la restriccion UNIQUE (migracion aplicada).")
else:
    print("No existe la tabla 'ventas' todavia.")

print()
print("=" * 60)
print("2. TOTAL DE FILAS EN 'ventas'")
print("=" * 60)
cur.execute("SELECT count(*) as n FROM ventas")
print("Total filas:", cur.fetchone()["n"])

print()
print("=" * 60)
print("3. TABLA DE ARCHIVOS CARGADOS (archivos_cargados)")
print("=" * 60)
try:
    cur.execute("SELECT * FROM archivos_cargados")
    for fila in cur.fetchall():
        print(dict(fila))
except sqlite3.OperationalError as e:
    print("No existe esa tabla todavia:", e)

print()
print("=" * 60)
print("4. VENTAS DEL DNI 48791865 EL 18/08 ENTRE LAS 13 Y 16 HORAS")
print("=" * 60)
cur.execute("""
    SELECT login, fecha, hora, producto, es_valida, department
    FROM ventas
    WHERE login = '48791865' AND fecha = '2026-08-18' AND hora >= 13 AND hora <= 16
""")
filas = cur.fetchall()
print(f"Total filas encontradas: {len(filas)}")
for f in filas:
    print(dict(f))

print()
print("=" * 60)
print("5. TABLA VIEJA DE RESPALDO (si existe)")
print("=" * 60)
try:
    cur.execute("SELECT count(*) as n FROM ventas_backup_con_bug_dedup")
    print("Filas en el respaldo viejo (con el bug):", cur.fetchone()["n"])
except sqlite3.OperationalError:
    print("No existe tabla de respaldo (normal si nunca tuviste el bug corriendo).")

print()
print("=" * 60)
print("6. INCENTIVOS CREADOS")
print("=" * 60)
cur.execute("SELECT id, nombre, fecha_inicio, hora_inicio, fecha_fin, hora_fin, producto_meta, departamento, meta_fija, cupos FROM incentivos ORDER BY id DESC")
for fila in cur.fetchall():
    print(dict(fila))

conn.close()
