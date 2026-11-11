"""Lectura del export de Fanero y cálculo de incentivos por cuota.

Reglas acordadas:
- Vendedor = LOGIN. Líder = LEADER; su venta es la suma de todo su equipo según
  el Fanero (todas las filas donde figura como LEADER, incluidas las suyas).
- Productos:
    PREPAGO       -> columna N, TIPO ACTIVACION = PREPAGO
    POSTPAGO      -> columna N, TIPO ACTIVACION = POSTPAGO
    PORTA PREPAGO -> columna P, PRODUCTO = PORTA PREPAGO
    OSS           -> columna P, PRODUCTO = OSS
  (una porta prepago también cuenta como prepago y un OSS como postpago,
  porque así figuran en TIPO ACTIVACION).
- Con varios productos, la persona debe cumplir la cuota de CADA producto.
- Fecha = LASTSTATUSMODDATE. Rango horario = REQUESTSTARTHOUR (hora de solicitud).
- "Primeros por departamento" = los primeros en LLEGAR a la cuota (a todas las
  cuotas de producto). El momento se toma de LASTSTATUSMODDATE + LASTSTATUSMODHOUR.
- Lista de participantes por incentivo: si existe, solo participan los de la lista.
"""
from __future__ import annotations

import io
import unicodedata

import pandas as pd

COLUMNAS_REQUERIDAS = [
    "STATUS", "REQUESTSTARTHOUR", "LASTSTATUSMODDATE", "LASTSTATUSMODHOUR",
    "LOGIN", "TARIFFPLANNAME", "LEADER", "DEPARTMENT", "TIPO ACTIVACION", "PRODUCTO",
]
COLUMNAS_OPCIONALES = [
    "ACTIVATIONTYPE", "PARTNER", "USERTYPE", "LATITUDE",
    "LONGITUDE", "PORTABILIDAD", "CANAL", "PROVINCIA", "DISTRITO", "FUENTE UBICACION",
]

# producto -> (columna del Fanero, valor)
PRODUCTOS = {
    "PREPAGO": ("TIPO ACTIVACION", "PREPAGO"),
    "POSTPAGO": ("TIPO ACTIVACION", "POSTPAGO"),
    "PORTA PREPAGO": ("PRODUCTO", "PORTA PREPAGO"),
    "OSS": ("PRODUCTO", "OSS"),
}


# Planes (columna G, TARIFFPLANNAME) que normalmente tiene cada producto
PLANES = {
    "PREPAGO": ["Prepago 5", "Plan Flexible"],
    "PORTA PREPAGO": ["Prepago 5", "Plan Flexible"],
    "POSTPAGO": ["Power 29.90", "Power 39.90", "Power 49.90", "Power 59.90", "Power Ilim 69.90"],
    "OSS": ["Power 29.90", "Power 39.90", "Power 49.90", "Power 59.90", "Power Ilim 69.90"],
}


def clave_plan(texto) -> str:
    """Para comparar planes sin importar mayúsculas, tildes ni espacios dobles."""
    return " ".join(sin_tildes(texto).split())


def planes_disponibles(df: pd.DataFrame) -> dict:
    """Catálogo + planes que aparecen en las ventas guardadas, por producto."""
    out = {}
    for p in PRODUCTOS:
        vistos = {clave_plan(x) for x in PLANES[p]}
        lista = list(PLANES[p])
        if df is not None and not df.empty:
            for x in sorted(df.loc[mascara_producto(df, p), "TARIFFPLANNAME"].dropna().unique()):
                if x and clave_plan(x) not in vistos:
                    vistos.add(clave_plan(x))
                    lista.append(x)
        out[p] = lista
    return out


# Productos que pueden exigir recargas (Consolidado, columna T "UR's")
#   PREPAGO       -> UR's (= UR VR PP + UR PORTA PP), igual que la regla de la columna N
#   PORTA PREPAGO -> Suma de UR PORTA PP
RECARGABLES = {"PREPAGO": "UR_PREPAGO", "PORTA PREPAGO": "UR_PORTA"}


def slug(producto: str) -> str:
    return producto.replace(" ", "_")


# Nombres de columna en la base (sin espacios)
MAPA_BD = {c: c.lower().replace(" ", "_") for c in COLUMNAS_REQUERIDAS + COLUMNAS_OPCIONALES}


def sin_tildes(texto) -> str:
    if texto is None or (not isinstance(texto, str) and pd.isna(texto)):
        return ""
    t = unicodedata.normalize("NFKD", str(texto))
    return "".join(ch for ch in t if not unicodedata.combining(ch)).strip().upper()


def _txt(serie: pd.Series) -> pd.Series:
    """Texto limpio; celdas vacías -> "" (pandas 3 conserva NaN al hacer astype(str))."""
    s = serie.astype(object)
    return s.where(s.notna(), "").map(str).str.strip().replace({"nan": "", "None": "", "<NA>": "", "NaT": ""})


def _limpiar_id(serie: pd.Series) -> pd.Series:
    """DNI/LOGIN como texto, sin decimales de Excel, sin apóstrofo y sin ceros a la izquierda
    (el Fanero trae 5353035 donde una lista puede traer 05353035)."""
    s = _txt(serie).str.replace(r"\.0$", "", regex=True).str.lstrip("'").str.strip()
    return s.str.lstrip("0")


def _decodificar(contenido: bytes) -> str:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return contenido.decode(enc)
        except UnicodeDecodeError:
            continue
    return contenido.decode("utf-8", errors="ignore")


def leer_tabla(nombre: str, contenido: bytes, hoja=0) -> pd.DataFrame:
    """Lee xlsx, xls, csv o txt separado por tabs/;/, todo como texto."""
    nombre = nombre.lower()
    if nombre.endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(io.BytesIO(contenido), dtype=str, sheet_name=hoja)
    texto = _decodificar(contenido)
    primera = texto.splitlines()[0] if texto else ""
    sep = "\t" if "\t" in primera else (";" if primera.count(";") > primera.count(",") else ",")
    return pd.read_csv(io.StringIO(texto), sep=sep, dtype=str)


leer_archivo = leer_tabla


def normalizar(df: pd.DataFrame) -> pd.DataFrame:
    """Valida columnas y deja el export de Fanero listo para guardar/calcular."""
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
    for c in ("STATUS", "PROVINCIA", "DISTRITO", "CANAL", "USERTYPE", "PORTABILIDAD",
              "ACTIVATIONTYPE", "TARIFFPLANNAME", "PARTNER", "FUENTE UBICACION"):
        df[c] = _txt(df[c]).replace({"": None})
    for c in ("TIPO ACTIVACION", "PRODUCTO", "DEPARTMENT"):
        df[c] = df[c].map(sin_tildes)
    for c in ("LATITUDE", "LONGITUDE"):
        df[c] = pd.to_numeric(_txt(df[c]).str.replace("'", "", regex=False), errors="coerce")

    df["REQUESTSTARTHOUR"] = pd.to_numeric(df["REQUESTSTARTHOUR"], errors="coerce").astype("Int64")
    fecha = pd.to_datetime(_txt(df["LASTSTATUSMODDATE"]).str[:10],
                           dayfirst=True, errors="coerce")
    hora = _txt(df["LASTSTATUSMODHOUR"])
    df["LASTSTATUSMODDATE"] = fecha.dt.date
    df["FECHA_HORA"] = pd.to_datetime(fecha.dt.strftime("%Y-%m-%d") + " " + hora, errors="coerce")
    df["FECHA_HORA"] = df["FECHA_HORA"].fillna(fecha)

    malas = df["LASTSTATUSMODDATE"].isna() | (df["LOGIN"] == "")
    return df[~malas].reset_index(drop=True)


def a_formato_bd(df: pd.DataFrame) -> pd.DataFrame:
    out = df.rename(columns=MAPA_BD).rename(columns={"FECHA_HORA": "fecha_hora"})
    return out.rename(columns={"laststatusmoddate": "fecha"})


def desde_bd(df: pd.DataFrame) -> pd.DataFrame:
    inv = {v: k for k, v in MAPA_BD.items()}
    inv.update({"fecha": "LASTSTATUSMODDATE", "fecha_hora": "FECHA_HORA"})
    out = df.rename(columns=inv)
    out["FECHA_HORA"] = pd.to_datetime(out["FECHA_HORA"])
    out["LASTSTATUSMODDATE"] = pd.to_datetime(out["LASTSTATUSMODDATE"]).dt.date
    out["REQUESTSTARTHOUR"] = pd.to_numeric(out["REQUESTSTARTHOUR"], errors="coerce").astype("Int64")
    for c in ("TIPO ACTIVACION", "PRODUCTO", "DEPARTMENT"):
        out[c] = out[c].map(sin_tildes)
    out["LOGIN"] = _limpiar_id(out["LOGIN"])
    out["LEADER"] = _limpiar_id(out["LEADER"])
    return out


# --------------------------------------------------------------------------- #
# Consolidado (recargas)
# --------------------------------------------------------------------------- #
def _col(df: pd.DataFrame, *nombres):
    norm = {sin_tildes(c).replace("'", "").replace(" ", ""): c for c in df.columns}
    for n in nombres:
        k = sin_tildes(n).replace("'", "").replace(" ", "")
        if k in norm:
            return norm[k]
    return None


def detectar_mes(texto: str):
    """Mes y año del filtro del Consolidado ('REQUESTDATE el o después del 01/11/2026')."""
    import re
    t = str(texto or "")
    m = re.search(r"REQUESTDATE[^0-9]*(\d{1,2})/(\d{1,2})/(\d{4})", t, re.I) or \
        re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    return (int(m.group(2)), int(m.group(3))) if m else (None, None)


def leer_consolidado(nombre: str, contenido: bytes) -> tuple[pd.DataFrame, tuple, str]:
    """Devuelve (tabla cruda con encabezados, (mes, año) detectado, texto de filtros)."""
    if nombre.lower().endswith((".xlsx", ".xlsm", ".xls")):
        crudo = pd.read_excel(io.BytesIO(contenido), header=None, dtype=str)
    else:
        crudo = leer_tabla(nombre, contenido).T.reset_index().T.reset_index(drop=True)
    fila = None
    for i in range(min(30, len(crudo))):
        valores = {sin_tildes(x) for x in crudo.iloc[i].tolist() if isinstance(x, str)}
        if "LOGIN" in valores and "DIA" in valores:
            fila = i
            break
    if fila is None:
        raise ValueError("No encontré la fila de encabezados (LOGIN, Dia, ...) en el Consolidado.")
    filtros = " ".join(str(x) for x in crudo.iloc[:fila].fillna("").to_numpy().ravel() if str(x).strip())
    df = crudo.iloc[fila + 1:].copy()
    df.columns = [str(c).strip() for c in crudo.iloc[fila]]
    return df.dropna(how="all").reset_index(drop=True), detectar_mes(filtros), filtros


def normalizar_consolidado(df: pd.DataFrame, mes: int, anio: int) -> pd.DataFrame:
    c_login, c_lider, c_dia = _col(df, "LOGIN"), _col(df, "LIDER", "LEADER"), _col(df, "Dia")
    c_plan, c_dep = _col(df, "TARIFFPLANNAME"), _col(df, "DEPARTAMENTO", "DEPARTMENT")
    c_ur = _col(df, "UR's", "URs", "UR")
    c_ur_porta = _col(df, "Suma de UR PORTA PP")
    faltan = [n for n, c in (("LOGIN", c_login), ("LIDER", c_lider), ("Dia", c_dia),
                             ("TARIFFPLANNAME", c_plan), ("UR's (columna T)", c_ur)) if c is None]
    if faltan:
        raise ValueError("Al Consolidado le faltan estas columnas: " + ", ".join(faltan))
    num = lambda c: pd.to_numeric(_txt(df[c]).str.replace(",", "", regex=False), errors="coerce").fillna(0) \
        if c else pd.Series(0, index=df.index)
    dia = pd.to_numeric(_txt(df[c_dia]), errors="coerce")
    out = pd.DataFrame({
        "LOGIN": _limpiar_id(df[c_login]),
        "LEADER": _limpiar_id(df[c_lider]),
        "FECHA": pd.to_datetime(dict(year=anio, month=mes, day=dia), errors="coerce").dt.date,
        "TARIFFPLANNAME": _txt(df[c_plan]),
        "DEPARTMENT": df[c_dep].map(sin_tildes) if c_dep else "",
        "UR_PREPAGO": num(c_ur).astype(int),
        "UR_PORTA": num(c_ur_porta).astype(int),
    })
    out = out[(out["LOGIN"] != "") & out["FECHA"].notna()]
    # una fila por login, día y plan
    return (out.groupby(["LOGIN", "LEADER", "FECHA", "TARIFFPLANNAME", "DEPARTMENT"], as_index=False)
            [["UR_PREPAGO", "UR_PORTA"]].sum())


# --------------------------------------------------------------------------- #
# Participantes
# --------------------------------------------------------------------------- #
COLS_PARTICIPANTES = (["DNI", "NOMBRE", "ROL", "DEPARTAMENTO", "DNI_LIDER"]
                      + [f"CUOTA_{slug(p)}" for p in PRODUCTOS]
                      + [f"RECARGA_{slug(p)}" for p in RECARGABLES])


def plantilla_participantes() -> bytes:
    ejemplo = pd.DataFrame([
        ["40854375", "Juan Pérez", "Vendedor", "LORETO", "41544805", 5, "", 2, "", "", ""],
        ["62300640", "María Ríos", "Vendedor", "LORETO", "41544805", "", "", "", "", "", ""],
        ["", "", "Vendedor", "", "40687294", 15, "", "", "", 3, ""],
        ["41544805", "Carlos Soto", "Líder", "LORETO", "", 30, "", 10, "", "", ""],
    ], columns=COLS_PARTICIPANTES)
    instrucciones = pd.DataFrame([
        ["DNI", "Obligatorio*", "El número que sale en LOGIN (vendedor) o LEADER (líder) del Fanero. "
                                "*Puede ir vacío si llenas DNI_LIDER (ver abajo)"],
        ["NOMBRE", "Opcional", "Solo para identificarlo en la matriz y el Excel. Puede ir vacío"],
        ["ROL", "Obligatorio", "Vendedor o Líder. Si alguien compite en ambos, va en dos filas"],
        ["DEPARTAMENTO", "Opcional", "Departamento en el que compite. Vacío = donde más vende según el Fanero"],
        ["DNI_LIDER", "Opcional", "Vendedor CON DNI: su líder (vacío = el del Fanero). "
                                  "Vendedor SIN DNI: participan TODOS los vendedores de ese líder según el Fanero, "
                                  "cada uno con su DNI. Líder sin DNI: se toma este número como su DNI"],
        ["CUOTA_PREPAGO", "Opcional", "Cuota individual de prepago (TIPO ACTIVACION = PREPAGO). Vacío = cuota general del incentivo"],
        ["CUOTA_POSTPAGO", "Opcional", "Cuota individual de postpago (TIPO ACTIVACION = POSTPAGO). Vacío = cuota general"],
        ["CUOTA_PORTA_PREPAGO", "Opcional", "Cuota individual de porta prepago (PRODUCTO = PORTA PREPAGO). Vacío = cuota general"],
        ["CUOTA_OSS", "Opcional", "Cuota individual de OSS (PRODUCTO = OSS). Vacío = cuota general"],
        ["RECARGA_PREPAGO", "Opcional", "Mínimo de prepagos recargados (Consolidado, columna T). "
                                        "Vacío = el mínimo general del incentivo"],
        ["RECARGA_PORTA_PREPAGO", "Opcional", "Mínimo de porta prepago recargadas (Consolidado, UR PORTA PP). "
                                              "Vacío = el mínimo general"],
        ["Nota", "", "Solo se usan las cuotas de los productos que tenga el incentivo. Una cuota 0 = ese producto no se le exige"],
        ["Nota", "", "Si un vendedor está con su DNI y también entra por el equipo de su líder, vale su fila con DNI"],
    ], columns=["Columna", "¿Obligatoria?", "Qué poner"])
    return _excel({"Participantes": ejemplo, "Instrucciones": instrucciones}, texto_cols={"A", "E"})


def leer_participantes(nombre: str, contenido: bytes) -> tuple[list[dict], list[str]]:
    """Devuelve (participantes, avisos). Lanza ValueError si el archivo no sirve."""
    df = leer_tabla(nombre, contenido, hoja=0)
    df.columns = [sin_tildes(c).replace(" ", "_") for c in df.columns]
    faltan = [c for c in ("DNI", "ROL") if c not in df.columns]
    if faltan:
        raise ValueError("Al archivo de participantes le faltan: " + ", ".join(faltan))
    for c in COLS_PARTICIPANTES:
        if c not in df.columns:
            df[c] = None
    df = df[COLS_PARTICIPANTES].dropna(how="all")
    avisos = []

    df["DNI"] = _limpiar_id(df["DNI"])
    df["DNI_LIDER"] = _limpiar_id(df["DNI_LIDER"])
    df["NOMBRE"] = _txt(df["NOMBRE"])
    df["DEPARTAMENTO"] = df["DEPARTAMENTO"].map(sin_tildes)
    rol = df["ROL"].map(sin_tildes)
    df["ROL"] = rol.map(lambda r: "Vendedor" if r.startswith("VEND") else ("Líder" if r.startswith("LID") else None))

    # Líder con DNI vacío pero con su número en DNI_LIDER: se toma de ahí
    es_lider = df["ROL"] == "Líder"
    mover = es_lider & (df["DNI"] == "") & (df["DNI_LIDER"] != "")
    if mover.any():
        df.loc[mover, "DNI"] = df.loc[mover, "DNI_LIDER"]
    df.loc[es_lider, "DNI_LIDER"] = ""

    # Vendedor sin DNI pero con DNI_LIDER = "todos los vendedores de ese líder"
    df["EQUIPO"] = (df["ROL"] == "Vendedor") & (df["DNI"] == "") & (df["DNI_LIDER"] != "")
    if df["EQUIPO"].any():
        avisos.append(f"{int(df['EQUIPO'].sum())} fila(s) de vendedor sin DNI: participarán todos los "
                      "vendedores de esos líderes según el Fanero.")

    sin_dni = (df["DNI"] == "") & ~df["EQUIPO"]
    if sin_dni.any():
        avisos.append(f"{int(sin_dni.sum())} fila(s) sin DNI ni DNI_LIDER se ignoraron.")
    malos = ~sin_dni & df["ROL"].isna()
    if malos.any():
        raise ValueError("ROL debe ser Vendedor o Líder. Revisa los DNI: "
                         + ", ".join(map(str, df.loc[malos, "DNI"].head(10))))
    df = df[~sin_dni]

    for c in [f"CUOTA_{slug(p)}" for p in PRODUCTOS] + [f"RECARGA_{slug(p)}" for p in RECARGABLES]:
        txt = _txt(df[c]).str.replace(r"\.0$", "", regex=True)
        num = pd.to_numeric(txt, errors="coerce")
        raros = (txt != "") & num.isna()
        if raros.any():
            raise ValueError(f"{c} tiene valores que no son números (DNI {', '.join(df.loc[raros, 'DNI'].head(5))}).")
        df[c] = num.map(lambda x: None if pd.isna(x) else int(x))

    clave = df["DNI"].where(~df["EQUIPO"], "EQUIPO:" + df["DNI_LIDER"])
    dup = pd.DataFrame({"k": clave, "r": df["ROL"]}).duplicated(keep="first")
    if dup.any():
        avisos.append(f"{int(dup.sum())} DNI repetido(s) con el mismo rol; se tomó la primera fila: "
                      + ", ".join(map(str, clave[dup].head(10))))
        df = df[~dup]
    registros = df.astype(object).where(df.notna(), None).to_dict("records")
    for r in registros:
        r["EQUIPO"] = bool(r.get("EQUIPO"))
    return registros, avisos


# --------------------------------------------------------------------------- #
# Cálculo
# --------------------------------------------------------------------------- #
CONFIG_DEFECTO = {
    "nombre": "",
    "fecha_inicio": None,          # "YYYY-MM-DD"
    "fecha_fin": None,
    "hora_desde": 0,               # REQUESTSTARTHOUR inclusive
    "hora_hasta": 23,              # inclusive
    "evaluacion": "periodo",       # "periodo" o "diaria"
    "estados": ["Completada"],
    "departamentos": [],           # vacío = todos
    "productos": ["PREPAGO"],      # subconjunto de PRODUCTOS
    "planes": {},                  # {producto: [planes]}; vacío = todos los planes
    "participa_vendedor": True,
    "participa_lider": False,      # se marca a propósito; evita premiar líderes por error
    "cuotas_vendedor": {"PREPAGO": 1},
    "cuotas_lider": {"PREPAGO": 1},
    "recargas_vendedor": {},       # {producto: mínimo recargadas}; solo PREPAGO / PORTA PREPAGO
    "recargas_lider": {},
    "gana_todos_cumplen": True,
    "gana_primeros_depto": False,
    "primeros_n": 1,
    "cupos": 0,                    # 0 = sin límite; si no, solo los primeros N ganadores en llegar
    "premio_vendedor": "",
    "premio_lider": "",
    "participantes": [],           # lista por incentivo; vacía = todos los del Fanero
}


def completar_config(cfg: dict) -> dict:
    cfg = {**CONFIG_DEFECTO, **(cfg or {})}
    cfg["productos"] = [p for p in cfg.get("productos") or [] if p in PRODUCTOS] or ["PREPAGO"]
    for clave in ("cuotas_vendedor", "cuotas_lider"):
        cfg[clave] = {p: int(cfg.get(clave, {}).get(p, 1) or 0) for p in cfg["productos"]}
    for clave in ("recargas_vendedor", "recargas_lider"):
        r = cfg.get(clave) or {}
        cfg[clave] = {p: int(r.get(p) or 0) for p in cfg["productos"] if p in RECARGABLES and int(r.get(p) or 0) > 0}
    planes = cfg.get("planes") or {}
    cfg["planes"] = {p: list(planes.get(p) or []) for p in cfg["productos"] if planes.get(p)}
    return cfg


def mascara_producto(df: pd.DataFrame, producto: str, planes=None) -> pd.Series:
    """Filas que cuentan para el producto; si se dan planes, solo esos planes."""
    col, valor = PRODUCTOS[producto]
    m = df[col] == valor
    if planes:
        elegidos = {clave_plan(x) for x in planes}
        m &= df["TARIFFPLANNAME"].map(clave_plan).isin(elegidos)
    return m


def filtrar(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Ventas que entran al incentivo (fechas, horas, estado, departamento).
    El filtro de producto se aplica por producto en el cálculo."""
    d = df
    if cfg.get("fecha_inicio"):
        d = d[d["LASTSTATUSMODDATE"] >= pd.to_datetime(cfg["fecha_inicio"]).date()]
    if cfg.get("fecha_fin"):
        d = d[d["LASTSTATUSMODDATE"] <= pd.to_datetime(cfg["fecha_fin"]).date()]
    h1, h2 = int(cfg.get("hora_desde", 0)), int(cfg.get("hora_hasta", 23))
    d = d[d["REQUESTSTARTHOUR"].between(h1, h2)]
    if cfg.get("estados"):
        d = d[d["STATUS"].isin(cfg["estados"])]
    if cfg.get("departamentos"):
        d = d[d["DEPARTMENT"].isin(cfg["departamentos"])]
    return d


def _moda_por(df: pd.DataFrame, clave: str, valor: str) -> pd.Series:
    """Valor más frecuente de `valor` para cada `clave` (vectorizado, sin apply)."""
    if clave == valor:
        u = pd.Series(df[clave].unique())
        return pd.Series(u.values, index=pd.Index(u.values, name=clave), name=valor)
    d = df[[clave, valor]]
    d = d[d[valor].astype(object).where(d[valor].notna(), "") != ""]
    if d.empty:
        return pd.Series(dtype=object, name=valor).rename_axis(clave)
    c = d.groupby([clave, valor], sort=False).size().rename("n").reset_index()
    c = c.sort_values([clave, "n", valor], ascending=[True, False, True])
    return c.drop_duplicates(clave).set_index(clave)[valor]


def _moda(serie: pd.Series) -> str:
    s = serie[_txt(serie) != ""]
    m = s.mode()
    return m.iloc[0] if not m.empty else ""


def _es_equipo(p: dict) -> bool:
    return p.get("EQUIPO") in (True, 1, "True", "true")


def _personas(universo: pd.DataFrame, rol: str, cfg: dict) -> pd.DataFrame:
    """Tabla de participantes de un rol con DNI, NOMBRE, DEPARTAMENTO, LIDER y Q_<producto>."""
    col = "LOGIN" if rol == "Vendedor" else "LEADER"
    general = cfg["cuotas_vendedor"] if rol == "Vendedor" else cfg["cuotas_lider"]
    rec_general = cfg["recargas_vendedor"] if rol == "Vendedor" else cfg["recargas_lider"]
    u = universo[universo[col] != ""]
    info = (_moda_por(u, col, "DEPARTMENT").rename("DEPTO_FANERO").to_frame()
            .join(_moda_por(u, col, "LEADER").rename("LIDER_FANERO"), how="left")
            .reset_index().rename(columns={col: "DNI"}))
    info["LIDER_FANERO"] = info["LIDER_FANERO"].fillna("")

    lista = [p for p in cfg.get("participantes") or [] if p.get("ROL") == rol]
    if cfg.get("participantes"):
        directos = [p for p in lista if not _es_equipo(p)]
        expandidos = []
        for p in lista:
            if not _es_equipo(p):
                continue
            logins = universo.loc[universo["LEADER"] == p["DNI_LIDER"], "LOGIN"]
            for login in sorted(set(logins) - {""}):
                expandidos.append({**p, "DNI": login, "NOMBRE": p.get("NOMBRE") or ""})
        base = pd.DataFrame(directos + expandidos, columns=COLS_PARTICIPANTES)
        base = base.drop_duplicates("DNI", keep="first")  # una fila explícita gana sobre el equipo
        base = base.merge(info, on="DNI", how="left")
        base["DEPARTAMENTO"] = base["DEPARTAMENTO"].where(base["DEPARTAMENTO"].fillna("") != "",
                                                          base["DEPTO_FANERO"]).fillna("")
        base["LIDER"] = base["DNI_LIDER"].where(base["DNI_LIDER"].fillna("") != "",
                                                base["LIDER_FANERO"]).fillna("")
        for p in cfg["productos"]:
            ind = pd.to_numeric(base[f"CUOTA_{slug(p)}"], errors="coerce")
            base[f"Q_{p}"] = ind.fillna(general[p]).astype(int)
        for p in rec_general:
            ind = pd.to_numeric(base[f"RECARGA_{slug(p)}"], errors="coerce")
            base[f"RQ_{p}"] = ind.fillna(rec_general[p]).astype(int)
    else:
        base = info.rename(columns={"DEPTO_FANERO": "DEPARTAMENTO", "LIDER_FANERO": "LIDER"})
        base["NOMBRE"] = ""
        for p in cfg["productos"]:
            base[f"Q_{p}"] = int(general[p])
        for p in rec_general:
            base[f"RQ_{p}"] = int(rec_general[p])
    if rol == "Líder":
        base["LIDER"] = ""
    return base[["DNI", "NOMBRE", "DEPARTAMENTO", "LIDER"] + [f"Q_{p}" for p in cfg["productos"]]
                + [f"RQ_{p}" for p in rec_general]]


def _matriz_rol(d: pd.DataFrame, universo: pd.DataFrame, rol: str, cfg: dict) -> pd.DataFrame:
    col = "LOGIN" if rol == "Vendedor" else "LEADER"
    diaria = cfg.get("evaluacion") == "diaria"
    claves = ["DNI"] + (["DIA"] if diaria else [])
    prods = cfg["productos"]

    m = _personas(universo, rol, cfg)
    if diaria:
        if cfg.get("fecha_inicio") and cfg.get("fecha_fin"):
            dias = list(pd.date_range(cfg["fecha_inicio"], cfg["fecha_fin"]).date)
        else:
            dias = sorted(d["LASTSTATUSMODDATE"].unique())
        m = m.merge(pd.DataFrame({"DIA": dias}), how="cross")

    d = d[d[col] != ""].sort_values("FECHA_HORA").copy()
    d["DNI"] = d[col]
    d["DIA"] = d["LASTSTATUSMODDATE"]
    for p in prods:
        dp = d[mascara_producto(d, p, cfg["planes"].get(p))].copy()
        conteo = dp.groupby(claves).size().rename(f"V_{p}").reset_index()
        m = m.merge(conteo, on=claves, how="left")
        m[f"V_{p}"] = m[f"V_{p}"].fillna(0).astype(int)
        # momento en que llega a SU cuota de este producto
        dp["_N"] = dp.groupby(claves).cumcount() + 1
        dp = dp.merge(m[claves + [f"Q_{p}"]], on=claves, how="inner")
        llega = (dp[dp["_N"] == dp[f"Q_{p}"]][claves + ["FECHA_HORA"]]
                 .rename(columns={"FECHA_HORA": f"T_{p}"}))
        m = m.merge(llega, on=claves, how="left")

    # Recargadas (Consolidado, columna T). No tiene hora: cuenta todo el día.
    rec_general = cfg["recargas_vendedor"] if rol == "Vendedor" else cfg["recargas_lider"]
    rec = cfg.get("_recargas")
    for p in rec_general:
        if rec is not None and not rec.empty:
            r = rec[rec[col] != ""].copy()
            if cfg.get("fecha_inicio"):
                r = r[r["FECHA"] >= pd.to_datetime(cfg["fecha_inicio"]).date()]
            if cfg.get("fecha_fin"):
                r = r[r["FECHA"] <= pd.to_datetime(cfg["fecha_fin"]).date()]
            if cfg.get("departamentos"):
                r = r[r["DEPARTMENT"].isin(cfg["departamentos"])]
            planes = cfg["planes"].get(p)
            if planes:
                r = r[r["TARIFFPLANNAME"].map(clave_plan).isin({clave_plan(x) for x in planes})]
            r["DNI"], r["DIA"] = r[col], r["FECHA"]
            conteo = r.groupby(claves)[RECARGABLES[p]].sum().rename(f"R_{p}").reset_index()
            m = m.merge(conteo, on=claves, how="left")
        else:
            m[f"R_{p}"] = 0
        m[f"R_{p}"] = m[f"R_{p}"].fillna(0).astype(int)

    q = m[[f"Q_{p}" for p in prods]].to_numpy()
    v = m[[f"V_{p}" for p in prods]].to_numpy()
    m["TOTAL_VENTAS"] = v.sum(axis=1)
    cumple_p = v >= q
    m["CUMPLE"] = cumple_p.all(axis=1)
    pct = {p: (m[f"V_{p}"] / m[f"Q_{p}"].where(m[f"Q_{p}"] > 0) * 100) for p in prods}
    faltan = (q - v).clip(min=0).sum(axis=1)
    for p in rec_general:
        m["CUMPLE"] &= m[f"R_{p}"] >= m[f"RQ_{p}"]
        pct[f"R_{p}"] = m[f"R_{p}"] / m[f"RQ_{p}"].where(m[f"RQ_{p}"] > 0) * 100
        faltan = faltan + (m[f"RQ_{p}"] - m[f"R_{p}"]).clip(lower=0).to_numpy()
    m["AVANCE_%"] = pd.DataFrame(pct).clip(upper=100).min(axis=1, skipna=True).fillna(100).round(1)
    m["FALTAN"] = faltan
    tiempos = m[[f"T_{p}" for p in prods]].where(m[[f"Q_{p}" for p in prods]].gt(0).to_numpy())
    m["HORA_CUOTA"] = tiempos.max(axis=1).where(m["CUMPLE"])

    # --- Un solo premio por persona -------------------------------------------
    # PDV sin vendedores (todas las ventas donde figura como LEADER las hizo él mismo,
    # LOGIN = LEADER): si participan vendedores, aparece solo como vendedor y no se repite.
    # Líder que ya gana como vendedor en el mismo periodo/día: tampoco gana como líder.
    m["_EXCL"] = ""
    if rol == "Líder":
        u = universo[(universo["LEADER"] != "") & (universo["LOGIN"] != universo["LEADER"])]
        con_equipo = set(u["LEADER"])
        if cfg.get("participa_vendedor"):
            # PDV sin vendedores: ya está en la matriz como vendedor con su DNI -> no se repite como líder
            m = m[m["DNI"].isin(con_equipo)].reset_index(drop=True)
            m["_EXCL"] = ""
        ganadores_v = cfg.get("_ganadores_vendedor")
        if ganadores_v is not None and not ganadores_v.empty:
            ya = m[claves].merge(ganadores_v.assign(_YA=True), on=claves, how="left")["_YA"].fillna(False)
            m.loc[ya.to_numpy() & (m["_EXCL"] == "").to_numpy(), "_EXCL"] = "Ya gana como vendedor"
    elegible = m["CUMPLE"] & (m["_EXCL"] == "")

    grupo = ["DEPARTAMENTO"] + (["DIA"] if diaria else [])
    m["PUESTO_DEPTO"] = (m[elegible].groupby(grupo)["HORA_CUOTA"]
                         .rank(method="first").reindex(m.index))
    grupo_general = ["DIA"] if diaria else None
    llegada = m[elegible]
    m["PUESTO_GENERAL"] = ((llegada.groupby(grupo_general)["HORA_CUOTA"] if grupo_general
                            else llegada["HORA_CUOTA"]).rank(method="first").reindex(m.index))
    gana_todos = elegible & bool(cfg.get("gana_todos_cumplen"))
    gana_primero = m["PUESTO_DEPTO"].le(int(cfg.get("primeros_n", 1))) & bool(cfg.get("gana_primeros_depto"))
    m["GANA"] = gana_todos | gana_primero
    m["MOTIVO"] = [" + ".join(r for r, ok in (("Cumplió cuota", t), ("Primero(s) del departamento", p)) if ok)
                   for t, p in zip(gana_todos, gana_primero)]
    excl = m["CUMPLE"] & (m["_EXCL"] != "")
    m.loc[excl, "MOTIVO"] = "Cumplió, pero no gana: " + m.loc[excl, "_EXCL"]
    cupos = int(cfg.get("cupos") or 0)
    if cupos > 0:
        # entre los que ganarían, solo los primeros `cupos` en llegar a la cuota (por día si es diaria)
        cand = m[m["GANA"]]
        orden_cupo = ((cand.groupby(grupo_general)["HORA_CUOTA"] if grupo_general else cand["HORA_CUOTA"])
                      .rank(method="first").reindex(m.index))
        sin_cupo = m["GANA"] & orden_cupo.gt(cupos)
        m.loc[sin_cupo, "GANA"] = False
        m.loc[sin_cupo, "MOTIVO"] = "Cumplió, pero sin cupo"
    premio = cfg.get("premio_vendedor" if rol == "Vendedor" else "premio_lider", "")
    m["PREMIO"] = m["GANA"].map(lambda g: premio if g else "")
    m.insert(0, "ROL", rol)
    return m


def usa_recargas(cfg: dict) -> bool:
    cfg = completar_config(cfg)
    return bool(cfg["recargas_vendedor"] or cfg["recargas_lider"])


def calcular(df: pd.DataFrame, cfg: dict, recargas: pd.DataFrame | None = None) -> pd.DataFrame:
    cfg = completar_config(cfg)
    cfg["_recargas"] = recargas
    universo = df
    if cfg.get("estados"):
        universo = universo[universo["STATUS"].isin(cfg["estados"])]
    d = filtrar(df, cfg)

    partes = []
    cfg["_ganadores_vendedor"] = None
    if cfg["participa_vendedor"]:
        mv = _matriz_rol(d, universo, "Vendedor", cfg)
        partes.append(mv)
        claves = ["DNI"] + (["DIA"] if cfg["evaluacion"] == "diaria" else [])
        cfg["_ganadores_vendedor"] = mv.loc[mv["GANA"], claves].drop_duplicates()
    if cfg["participa_lider"]:
        partes.append(_matriz_rol(d, universo, "Líder", cfg))
    if not partes:
        return pd.DataFrame()
    m = pd.concat(partes, ignore_index=True)

    diaria = cfg["evaluacion"] == "diaria"
    renombre, cols = {}, ["ROL", "DNI", "NOMBRE", "DEPARTAMENTO", "LIDER"] + (["DIA"] if diaria else [])
    for p in cfg["productos"]:
        renombre[f"V_{p}"] = f"{p} VENTAS"
        renombre[f"Q_{p}"] = f"{p} CUOTA"
        cols += [f"V_{p}", f"Q_{p}"]
        if p in cfg["recargas_vendedor"] or p in cfg["recargas_lider"]:
            for c in (f"R_{p}", f"RQ_{p}"):
                if c not in m:
                    m[c] = pd.NA
            renombre[f"R_{p}"] = f"{p} RECARGADAS"
            renombre[f"RQ_{p}"] = f"{p} MIN RECARGADAS"
            cols += [f"R_{p}", f"RQ_{p}"]
    cols += ["TOTAL_VENTAS", "AVANCE_%", "FALTAN", "CUMPLE", "HORA_CUOTA", "PUESTO_GENERAL", "PUESTO_DEPTO",
             "GANA", "MOTIVO", "PREMIO"]
    m = m[cols].rename(columns=renombre)
    m["PUESTO_DEPTO"] = m["PUESTO_DEPTO"].astype("Int64")
    m["PUESTO_GENERAL"] = m["PUESTO_GENERAL"].astype("Int64")
    m["CUMPLE"] = m["CUMPLE"].map({True: "Sí", False: "No"})
    m["GANA"] = m["GANA"].map({True: "Sí", False: "No"})
    orden = ["ROL", "DEPARTAMENTO"] + (["DIA"] if diaria else [])
    orden = ["ROL"] + (["DIA"] if diaria else [])
    m = m.sort_values(orden + ["GANA", "PUESTO_GENERAL", "AVANCE_%"],
                      ascending=[True] * len(orden) + [False, True, False], na_position="last")
    return m.reset_index(drop=True)


def resumen(m: pd.DataFrame) -> pd.DataFrame:
    if m.empty:
        return m
    return (m.groupby(["ROL", "DEPARTAMENTO"])
            .agg(PARTICIPANTES=("DNI", "nunique"),
                 VENTAS=("TOTAL_VENTAS", "sum"),
                 CUMPLEN=("CUMPLE", lambda s: (s == "Sí").sum()),
                 GANADORES=("GANA", lambda s: (s == "Sí").sum()))
            .reset_index())


def reglas_tabla(cfg: dict) -> pd.DataFrame:
    cfg = completar_config(cfg)
    filas = []
    for k, v in cfg.items():
        if k.startswith("_"):
            continue
        if k == "participantes":
            v = f"{len(v)} en lista" if v else "Todos los del Fanero"
        elif k == "planes":
            continue
        elif isinstance(v, dict):
            v = ", ".join(f"{p}: {n}" for p, n in v.items())
        elif isinstance(v, list):
            v = ", ".join(map(str, v))
        filas.append((k, v))
    filas.append(("planes", "; ".join(f"{p}: {', '.join(v)}" for p, v in cfg["planes"].items()) or "Todos"))
    filas.append(("recargas", "Consolidado, columna T (UR's). PREPAGO = UR's; PORTA PREPAGO = UR PORTA PP. "
                  "Sin hora: cuenta el día completo"))
    filas.append(("regla_productos", "PREPAGO/POSTPAGO por TIPO ACTIVACION; PORTA PREPAGO/OSS por PRODUCTO"))
    return pd.DataFrame(filas, columns=["Parámetro", "Valor"])


def _excel(hojas: dict, texto_cols=frozenset()) -> bytes:
    from openpyxl.styles import Font, PatternFill
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for nombre, df in hojas.items():
            df.to_excel(xw, sheet_name=nombre, index=False)
        azul = PatternFill("solid", fgColor="00679E")
        for ws in xw.book.worksheets:
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = azul
            for col in ws.columns:
                ancho = max(len(str(c.value or "")) for c in col[:300])
                ws.column_dimensions[col[0].column_letter].width = min(max(10, ancho + 2), 60)
                if col[0].column_letter in texto_cols:
                    for c in col[1:]:
                        c.number_format = "@"
            ws.freeze_panes = "A2"
    return buf.getvalue()


def a_excel(matriz: pd.DataFrame, cfg: dict) -> bytes:
    m = matriz.copy()
    if "HORA_CUOTA" in m:
        m["HORA_CUOTA"] = pd.to_datetime(m["HORA_CUOTA"]).dt.strftime("%d/%m/%Y %H:%M:%S").fillna("")
    return _excel({"Matriz": m, "Ganadores": m[m["GANA"] == "Sí"],
                   "Resumen depto": resumen(matriz), "Reglas": reglas_tabla(cfg)})
