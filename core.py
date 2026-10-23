"""Lectura del export de Fanero y cálculo de incentivos por cuota.

Reglas acordadas:
- Vendedor = LOGIN. Líder = LEADER; su venta es la suma de todo su equipo
  (todas las filas donde figura como LEADER, incluidas las suyas).
- Fecha = LASTSTATUSMODDATE. Rango horario = REQUESTSTARTHOUR (hora de solicitud).
- "Primeros por departamento" = los primeros en LLEGAR a la cuota. El momento en
  que llegan se toma del timestamp más preciso disponible:
  LASTSTATUSMODDATE + LASTSTATUSMODHOUR.
"""
from __future__ import annotations

import io
import unicodedata

import pandas as pd

COLUMNAS_REQUERIDAS = [
    "STATUS", "REQUESTSTARTHOUR", "LASTSTATUSMODDATE", "LASTSTATUSMODHOUR",
    "LOGIN", "LEADER", "DEPARTMENT", "PRODUCTO",
]
COLUMNAS_OPCIONALES = [
    "ACTIVATIONTYPE", "TARIFFPLANNAME", "PARTNER", "USERTYPE", "LATITUDE",
    "LONGITUDE", "TIPO ACTIVACION", "PORTABILIDAD", "CANAL", "PROVINCIA",
    "DISTRITO", "FUENTE UBICACION",
]

# Nombres de columna en la base (sin espacios)
MAPA_BD = {c: c.lower().replace(" ", "_") for c in COLUMNAS_REQUERIDAS + COLUMNAS_OPCIONALES}


def sin_tildes(texto) -> str:
    if pd.isna(texto):
        return ""
    t = unicodedata.normalize("NFKD", str(texto))
    return "".join(ch for ch in t if not unicodedata.combining(ch)).strip().upper()


def _limpiar_id(serie: pd.Series) -> pd.Series:
    s = serie.astype(str).str.strip()
    s = s.str.replace(r"\.0$", "", regex=True).str.lstrip("'")
    return s.replace({"nan": "", "None": ""})


def leer_archivo(nombre: str, contenido: bytes) -> pd.DataFrame:
    """Lee el export de Fanero (xlsx, xls, csv o txt separado por tabs/;/,)."""
    nombre = nombre.lower()
    if nombre.endswith((".xlsx", ".xlsm", ".xls")):
        df = pd.read_excel(io.BytesIO(contenido), dtype=str)
    else:
        texto = None
        for enc in ("utf-8-sig", "latin-1"):
            try:
                texto = contenido.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        primera = texto.splitlines()[0] if texto else ""
        sep = "\t" if "\t" in primera else (";" if primera.count(";") > primera.count(",") else ",")
        df = pd.read_csv(io.StringIO(texto), sep=sep, dtype=str)
    return df


def normalizar(df: pd.DataFrame) -> pd.DataFrame:
    """Valida columnas y deja el DataFrame listo para guardar/calcular."""
    df = df.copy()
    df.columns = [str(c).strip().upper() for c in df.columns]
    faltan = [c for c in COLUMNAS_REQUERIDAS if c not in df.columns]
    if faltan:
        raise ValueError("Al archivo le faltan estas columnas: " + ", ".join(faltan))

    for c in COLUMNAS_OPCIONALES:
        if c not in df.columns:
            df[c] = None
    df = df[COLUMNAS_REQUERIDAS + COLUMNAS_OPCIONALES]
    df = df.dropna(how="all")

    df["LOGIN"] = _limpiar_id(df["LOGIN"])
    df["LEADER"] = _limpiar_id(df["LEADER"])
    for c in ("STATUS", "PRODUCTO", "PROVINCIA", "DISTRITO", "CANAL", "USERTYPE",
              "TIPO ACTIVACION", "PORTABILIDAD", "ACTIVATIONTYPE"):
        df[c] = df[c].astype(str).str.strip().replace({"nan": None, "None": None})
    df["DEPARTMENT"] = df["DEPARTMENT"].map(sin_tildes)
    for c in ("LATITUDE", "LONGITUDE"):
        df[c] = pd.to_numeric(df[c].astype(str).str.replace("'", "", regex=False), errors="coerce")

    df["REQUESTSTARTHOUR"] = pd.to_numeric(df["REQUESTSTARTHOUR"], errors="coerce").astype("Int64")
    fecha = pd.to_datetime(df["LASTSTATUSMODDATE"].astype(str).str.strip().str[:10],
                           dayfirst=True, errors="coerce")
    hora = df["LASTSTATUSMODHOUR"].astype(str).str.strip()
    df["LASTSTATUSMODDATE"] = fecha.dt.date
    df["FECHA_HORA"] = pd.to_datetime(fecha.dt.strftime("%Y-%m-%d") + " " + hora, errors="coerce")
    # si la hora vino rara, al menos conserva la fecha
    df["FECHA_HORA"] = df["FECHA_HORA"].fillna(fecha)

    malas = df["LASTSTATUSMODDATE"].isna() | (df["LOGIN"] == "")
    if malas.any():
        df = df[~malas]
    return df.reset_index(drop=True)


def a_formato_bd(df: pd.DataFrame) -> pd.DataFrame:
    out = df.rename(columns=MAPA_BD).rename(columns={"FECHA_HORA": "fecha_hora"})
    out = out.rename(columns={"laststatusmoddate": "fecha"})
    return out


def desde_bd(df: pd.DataFrame) -> pd.DataFrame:
    inv = {v: k for k, v in MAPA_BD.items()}
    inv.update({"fecha": "LASTSTATUSMODDATE", "fecha_hora": "FECHA_HORA"})
    out = df.rename(columns=inv)
    out["FECHA_HORA"] = pd.to_datetime(out["FECHA_HORA"])
    out["LASTSTATUSMODDATE"] = pd.to_datetime(out["LASTSTATUSMODDATE"]).dt.date
    out["REQUESTSTARTHOUR"] = pd.to_numeric(out["REQUESTSTARTHOUR"], errors="coerce").astype("Int64")
    return out


# --------------------------------------------------------------------------- #
# Cálculo
# --------------------------------------------------------------------------- #
CONFIG_DEFECTO = {
    "nombre": "",
    "fecha_inicio": None,          # "YYYY-MM-DD"
    "fecha_fin": None,
    "hora_desde": 0,               # REQUESTSTARTHOUR inclusive
    "hora_hasta": 23,              # inclusive (23 = hasta las 23:59)
    "evaluacion": "periodo",       # "periodo" o "diaria"
    "estados": ["Completada"],
    "productos": [],               # vacío = todos
    "departamentos": [],           # vacío = todos
    "participa_vendedor": True,
    "participa_lider": True,
    "cuota_vendedor": 1,
    "cuota_lider": 1,
    "gana_todos_cumplen": True,
    "gana_primeros_depto": False,
    "primeros_n": 1,
    "premio_vendedor": "",
    "premio_lider": "",
}


def filtrar(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    d = df
    if cfg.get("fecha_inicio"):
        d = d[d["LASTSTATUSMODDATE"] >= pd.to_datetime(cfg["fecha_inicio"]).date()]
    if cfg.get("fecha_fin"):
        d = d[d["LASTSTATUSMODDATE"] <= pd.to_datetime(cfg["fecha_fin"]).date()]
    h1, h2 = int(cfg.get("hora_desde", 0)), int(cfg.get("hora_hasta", 23))
    d = d[d["REQUESTSTARTHOUR"].between(h1, h2)]
    if cfg.get("estados"):
        d = d[d["STATUS"].isin(cfg["estados"])]
    if cfg.get("productos"):
        d = d[d["PRODUCTO"].isin(cfg["productos"])]
    if cfg.get("departamentos"):
        d = d[d["DEPARTMENT"].isin(cfg["departamentos"])]
    return d


def _depto_principal(serie: pd.Series) -> str:
    m = serie.mode()
    return m.iloc[0] if not m.empty else ""


def _matriz_rol(d: pd.DataFrame, base: pd.DataFrame, rol: str, cuota: int, cfg: dict) -> pd.DataFrame:
    """Matriz de un rol. `base` = universo de participantes (sin filtros de fecha/hora),
    para que aparezcan también los que vendieron 0 en el rango."""
    col = "LOGIN" if rol == "Vendedor" else "LEADER"
    diaria = cfg.get("evaluacion") == "diaria"
    claves = [col] + (["DIA"] if diaria else [])

    if d.empty:
        ventas = pd.DataFrame(columns=claves + ["VENTAS", "HORA_CUOTA"])
    else:
        d = d.sort_values("FECHA_HORA").copy()
        d["DIA"] = d["LASTSTATUSMODDATE"]
        g = d.groupby(claves, sort=False)
        ventas = g.size().rename("VENTAS").reset_index()
        if cuota > 0:
            d["_N"] = g.cumcount() + 1
            llega = d[d["_N"] == cuota][claves + ["FECHA_HORA"]].rename(columns={"FECHA_HORA": "HORA_CUOTA"})
            ventas = ventas.merge(llega, on=claves, how="left")
        else:
            ventas["HORA_CUOTA"] = pd.NaT

    # universo: todos los que aparecen con ese rol en el archivo (filtrado solo por
    # producto/estado/departamento), para que la matriz muestre también a los de 0 ventas
    info = (base[base[col] != ""].groupby(col)
            .agg(DEPARTAMENTO=("DEPARTMENT", _depto_principal),
                 LIDER=("LEADER", _depto_principal))
            .reset_index())
    if diaria:
        dias = pd.date_range(cfg["fecha_inicio"], cfg["fecha_fin"]).date if cfg.get("fecha_inicio") and cfg.get("fecha_fin") \
            else sorted(d["LASTSTATUSMODDATE"].unique()) if not d.empty else []
        info = info.merge(pd.DataFrame({"DIA": list(dias)}), how="cross")
    m = info.merge(ventas, on=claves, how="left")
    m["VENTAS"] = m["VENTAS"].fillna(0).astype(int)

    m.insert(0, "ROL", rol)
    m = m.rename(columns={col: "DNI"})
    if rol == "Líder":
        m = m.drop(columns=["LIDER"])
        m["LIDER"] = ""
    m["CUOTA"] = cuota
    m["AVANCE_%"] = (m["VENTAS"] / cuota * 100).round(1) if cuota else 0.0
    m["FALTAN"] = (cuota - m["VENTAS"]).clip(lower=0)
    m["CUMPLE"] = m["VENTAS"] >= cuota

    grupo = ["DEPARTAMENTO"] + (["DIA"] if diaria else [])
    m["PUESTO_DEPTO"] = (m[m["CUMPLE"]].groupby(grupo)["HORA_CUOTA"]
                         .rank(method="first").reindex(m.index))

    gana_todos = m["CUMPLE"] & bool(cfg.get("gana_todos_cumplen"))
    gana_primero = m["PUESTO_DEPTO"].le(int(cfg.get("primeros_n", 1))) & bool(cfg.get("gana_primeros_depto"))
    m["GANA"] = gana_todos | gana_primero
    motivo = []
    for t, p in zip(gana_todos, gana_primero):
        r = []
        if t:
            r.append("Cumplió cuota")
        if p:
            r.append("Primero(s) del departamento")
        motivo.append(" + ".join(r))
    m["MOTIVO"] = motivo
    premio = cfg.get("premio_vendedor" if rol == "Vendedor" else "premio_lider", "")
    m["PREMIO"] = m["GANA"].map(lambda g: premio if g else "")
    return m


def calcular(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    cfg = {**CONFIG_DEFECTO, **cfg}
    universo = df
    if cfg.get("estados"):
        universo = universo[universo["STATUS"].isin(cfg["estados"])]
    if cfg.get("productos"):
        universo = universo[universo["PRODUCTO"].isin(cfg["productos"])]
    if cfg.get("departamentos"):
        universo = universo[universo["DEPARTMENT"].isin(cfg["departamentos"])]
    d = filtrar(df, cfg)

    partes = []
    if cfg["participa_vendedor"]:
        partes.append(_matriz_rol(d, universo, "Vendedor", int(cfg["cuota_vendedor"]), cfg))
    if cfg["participa_lider"]:
        partes.append(_matriz_rol(d, universo, "Líder", int(cfg["cuota_lider"]), cfg))
    if not partes:
        return pd.DataFrame()
    m = pd.concat(partes, ignore_index=True)

    cols = ["ROL", "DNI", "DEPARTAMENTO", "LIDER"]
    if cfg["evaluacion"] == "diaria":
        cols.append("DIA")
    cols += ["VENTAS", "CUOTA", "AVANCE_%", "FALTAN", "CUMPLE", "HORA_CUOTA",
             "PUESTO_DEPTO", "GANA", "MOTIVO", "PREMIO"]
    m = m[cols]
    m["PUESTO_DEPTO"] = m["PUESTO_DEPTO"].astype("Int64")
    m["CUMPLE"] = m["CUMPLE"].map({True: "Sí", False: "No"})
    m["GANA"] = m["GANA"].map({True: "Sí", False: "No"})
    orden = ["ROL", "DEPARTAMENTO"] + (["DIA"] if cfg["evaluacion"] == "diaria" else [])
    m = m.sort_values(orden + ["GANA", "VENTAS"], ascending=[True] * len(orden) + [False, False])
    return m.reset_index(drop=True)


def resumen(m: pd.DataFrame) -> pd.DataFrame:
    if m.empty:
        return m
    return (m.groupby(["ROL", "DEPARTAMENTO"])
            .agg(PARTICIPANTES=("DNI", "nunique"),
                 VENTAS=("VENTAS", "sum"),
                 CUMPLEN=("CUMPLE", lambda s: (s == "Sí").sum()),
                 GANADORES=("GANA", lambda s: (s == "Sí").sum()))
            .reset_index())


def a_excel(matriz: pd.DataFrame, cfg: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        reglas = pd.DataFrame(
            [(k, ", ".join(map(str, v)) if isinstance(v, list) else v) for k, v in cfg.items()],
            columns=["Parámetro", "Valor"])
        m = matriz.copy()
        if "HORA_CUOTA" in m:
            m["HORA_CUOTA"] = pd.to_datetime(m["HORA_CUOTA"]).dt.strftime("%d/%m/%Y %H:%M:%S").fillna("")
        m.to_excel(xw, sheet_name="Matriz", index=False)
        m[m["GANA"] == "Sí"].to_excel(xw, sheet_name="Ganadores", index=False)
        resumen(matriz).to_excel(xw, sheet_name="Resumen depto", index=False)
        reglas.to_excel(xw, sheet_name="Reglas", index=False)
        from openpyxl.styles import Font, PatternFill
        azul = PatternFill("solid", fgColor="00679E")
        for ws in xw.book.worksheets:
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = azul
            for col in ws.columns:
                ancho = max(len(str(c.value or "")) for c in col[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(10, ancho + 2), 40)
            ws.freeze_panes = "A2"
    return buf.getvalue()
