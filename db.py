"""Acceso a Neon Postgres (o SQLite para pruebas locales).

Las tablas usan el prefijo `fanero_` para no chocar con las de la app anterior.

Estrategia anti-duplicados: cada carga REEMPLAZA las fechas que trae el archivo.
Si subes el Fanero del 1 al 23 de octubre, primero se borran las ventas guardadas
de esas fechas y luego se insertan las del archivo. Así puedes subir el reporte
acumulado todos los días sin duplicar, y los cambios de estado se actualizan.
"""
from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
from sqlalchemy import (Column, Date, DateTime, Float, Integer, MetaData, String, Table,
                        Text, create_engine, delete, insert, select, update)

import core

meta = MetaData()

ventas = Table(
    "fanero_ventas", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("activationtype", String), Column("status", String),
    Column("requeststarthour", Integer), Column("fecha", Date, index=True),
    Column("laststatusmodhour", String), Column("fecha_hora", DateTime),
    Column("login", String, index=True), Column("tariffplanname", String),
    Column("partner", String), Column("leader", String, index=True),
    Column("usertype", String), Column("latitude", Float), Column("longitude", Float),
    Column("department", String), Column("tipo_activacion", String),
    Column("portabilidad", String), Column("producto", String), Column("canal", String),
    Column("provincia", String), Column("distrito", String), Column("fuente_ubicacion", String),
    Column("carga_id", Integer),
)

cargas = Table(
    "fanero_cargas", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("archivo", String), Column("filas", Integer),
    Column("fecha_desde", Date), Column("fecha_hasta", Date),
    Column("reemplazadas", Integer), Column("creado", DateTime),
)

incentivos = Table(
    "fanero_incentivos", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("nombre", String), Column("config", Text),
    Column("activo", Integer, default=1), Column("creado", DateTime),
)

COLS_VENTAS = [c.name for c in ventas.columns if c.name not in ("id", "carga_id")]


def get_engine(url: str):
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    eng = create_engine(url, pool_pre_ping=True)
    meta.create_all(eng)
    return eng


def _limpio(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    return v


def guardar_ventas(eng, df_norm: pd.DataFrame, archivo: str) -> dict:
    bd = core.a_formato_bd(df_norm)[COLS_VENTAS]
    fechas = sorted(set(bd["fecha"]))
    desde, hasta = fechas[0], fechas[-1]
    with eng.begin() as cx:
        borradas = cx.execute(delete(ventas).where(ventas.c.fecha.in_(fechas))).rowcount
        cid = cx.execute(insert(cargas).values(
            archivo=archivo, filas=len(bd), fecha_desde=desde, fecha_hasta=hasta,
            reemplazadas=borradas, creado=datetime.now())).inserted_primary_key[0]
        registros = [{k: _limpio(v) for k, v in r.items()} | {"carga_id": cid}
                     for r in bd.to_dict("records")]
        for i in range(0, len(registros), 5000):
            cx.execute(insert(ventas), registros[i:i + 5000])
    return {"filas": len(bd), "desde": desde, "hasta": hasta, "reemplazadas": borradas,
            "dias": len(fechas)}


def leer_ventas(eng, desde=None, hasta=None) -> pd.DataFrame:
    q = select(*[ventas.c[c] for c in COLS_VENTAS])
    if desde:
        q = q.where(ventas.c.fecha >= desde)
    if hasta:
        q = q.where(ventas.c.fecha <= hasta)
    with eng.connect() as cx:
        df = pd.read_sql(q, cx)
    if df.empty:
        return pd.DataFrame(columns=core.COLUMNAS_REQUERIDAS + core.COLUMNAS_OPCIONALES + ["FECHA_HORA"])
    df["login"] = df["login"].astype(str)
    df["leader"] = df["leader"].fillna("").astype(str)
    return core.desde_bd(df)


def resumen_ventas(eng) -> pd.DataFrame:
    with eng.connect() as cx:
        return pd.read_sql(
            "SELECT fecha, COUNT(*) AS ventas FROM fanero_ventas GROUP BY fecha ORDER BY fecha", cx)


def valores_distintos(eng, columna: str) -> list[str]:
    with eng.connect() as cx:
        r = cx.execute(select(ventas.c[columna]).distinct()).scalars().all()
    return sorted(x for x in r if x)


def historial_cargas(eng) -> pd.DataFrame:
    with eng.connect() as cx:
        return pd.read_sql(select(cargas).order_by(cargas.c.id.desc()), cx)


def borrar_rango(eng, desde, hasta) -> int:
    with eng.begin() as cx:
        return cx.execute(delete(ventas).where(ventas.c.fecha.between(desde, hasta))).rowcount


def guardar_incentivo(eng, cfg: dict, inc_id: int | None = None) -> int:
    datos = {"nombre": cfg["nombre"], "config": json.dumps(cfg, default=str, ensure_ascii=False)}
    with eng.begin() as cx:
        if inc_id:
            cx.execute(update(incentivos).where(incentivos.c.id == inc_id).values(**datos))
            return inc_id
        return cx.execute(insert(incentivos).values(**datos, activo=1, creado=datetime.now())
                          ).inserted_primary_key[0]


def listar_incentivos(eng, solo_activos=True) -> list[dict]:
    q = select(incentivos).order_by(incentivos.c.id.desc())
    if solo_activos:
        q = q.where(incentivos.c.activo == 1)
    with eng.connect() as cx:
        filas = cx.execute(q).mappings().all()
    return [{"id": f["id"], "nombre": f["nombre"], "creado": f["creado"],
             "activo": f["activo"], "config": json.loads(f["config"])} for f in filas]


def archivar_incentivo(eng, inc_id: int):
    with eng.begin() as cx:
        cx.execute(update(incentivos).where(incentivos.c.id == inc_id).values(activo=0))
