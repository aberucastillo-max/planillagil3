"""Configuración de la empresa.

- Centros de costo, puestos y áreas: tablas de consulta para los demás módulos, con registro, edición y borrado. Sin periodo de aplicación ni historial.
- Afectaciones y suspensiones: DATO ÚNICO editable (sin periodo de aplicación ni historial). Lo que se guarda rige para la planilla que se procese a continuación.
- Parámetros y tasas AFP: ya no se configuran aquí; se editan por periodo en Cálculo de Planilla y se heredan al abrir el siguiente periodo (parametros_periodo.py).
  Los registros antiguos de estas dos familias se conservan solo como respaldo para periodos que aún no tienen valores propios."""
from datetime import date
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .diccionario import EDITABLES_PERIODO, TASAS_PERIODO, TasaAFP, cargar_parametros
from .models import ConfigVigencia, Concepto, DiccionarioAFP, Empleado, Empresa, Periodo, TipoSuspension
from .postplanilla import ultimo_cerrado

CATALOGOS = ("CENTRO_COSTO", "PUESTO", "AREA")
FAMILIAS = ("PARAMETRO", "TASA_AFP", "AFECTACION", "SUSPENSION") + CATALOGOS
FAM_UNICAS = ("AFECTACION", "SUSPENSION")                                     # dato único editable
FAM_POR_PERIODO = ("PARAMETRO", "TASA_AFP")                                   # se editan por periodo en Cálculo de Planilla
FECHA_UNICA = date(2000, 1, 1)
CAMPOS_AFP = ("aporte", "comision", "comision_mixta_flujo", "comision_saldo", "prima")
TIPOS_SUSP = ("PERFECTA", "IMPERFECTA", "SUBSIDIADA")
CAMPO_EMPLEADO = {"CENTRO_COSTO": "centro_costo", "PUESTO": "puesto", "AREA": "area"}


def _dec(x, nombre: str) -> Decimal:
    try:
        d = Decimal(str(x))
    except (InvalidOperation, ValueError):
        raise HTTPException(422, f"{nombre}: valor no numérico.")
    return d


def _mes(texto: str) -> date:
    a, m = texto.split("-")
    return date(int(a), int(m), 1)


def _ref(db: Session, empresa: Empresa, periodo_id: Optional[int]) -> date:
    if periodo_id is None:
        h = date.today()
        return date(h.year, h.month, 1)
    p = db.get(Periodo, periodo_id)
    if not p or p.empresa_id != empresa.id:
        raise HTTPException(404, "Periodo no encontrado.")
    return date(p.anio, p.mes, 1)


def vigentes(db: Session, empresa_id: int, familia: str, mes_ref: date) -> dict:
    salida: dict = {}
    q = (db.query(ConfigVigencia).filter(ConfigVigencia.empresa_id == empresa_id, ConfigVigencia.familia == familia,
                                         ConfigVigencia.aplica_desde <= mes_ref).order_by(ConfigVigencia.aplica_desde))
    for r in q:
        salida[r.clave] = r
    return salida


def ultimos(db: Session, empresa_id: int, familia: str) -> dict:
    """Dato único: el registro más reciente de cada clave (los antiguos con periodo de aplicación se leen como un solo valor)."""
    salida: dict = {}
    for r in db.query(ConfigVigencia).filter(ConfigVigencia.empresa_id == empresa_id, ConfigVigencia.familia == familia).order_by(ConfigVigencia.aplica_desde, ConfigVigencia.id):
        salida[r.clave] = r
    return salida


# ---------------------------------------------------------------- aplicación al cálculo
def aplicar_a_parametros(db: Session, empresa_id: int, fecha: date, valores: dict, afps: dict, conceptos: dict, susp: dict) -> None:
    """Superpone la configuración de la empresa vigente en el mes de `fecha`. Modifica los diccionarios recibidos (copias en memoria)."""
    mes = date(fecha.year, fecha.month, 1)
    for k, r in vigentes(db, empresa_id, "PARAMETRO", mes).items():
        valores[k] = Decimal(str(r.valor["valor"]))
    for k, r in vigentes(db, empresa_id, "TASA_AFP", mes).items():
        base = afps.get(k.upper())
        v = r.valor
        afps[k.upper()] = TasaAFP(base.nombre if base else k, Decimal(str(v["aporte"])), Decimal(str(v["comision"])), Decimal(str(v["comision_mixta_flujo"])),
                                  Decimal(str(v["comision_saldo"])), Decimal(str(v["prima"])), base.tope_prima if base else None)
    for k, r in ultimos(db, empresa_id, "AFECTACION").items():
        orig = conceptos.get(k)
        if orig is None:
            continue
        copia = SimpleNamespace(**{c.name: getattr(orig, c.name) for c in orig.__table__.columns})
        copia.afecta_afp_onp, copia.afecta_essalud, copia.afecta_5ta_regular = bool(r.valor["afp"]), bool(r.valor["essalud"]), bool(r.valor["quinta"])
        conceptos[k] = copia
    por_codigo = {s.codigo_plame: sid for sid, s in susp.items()}
    for k, r in ultimos(db, empresa_id, "SUSPENSION").items():
        sid = por_codigo.get(k)
        if sid is None:
            continue
        orig = susp[sid]
        copia = SimpleNamespace(**{c.name: getattr(orig, c.name) for c in orig.__table__.columns})
        copia.tipo = r.valor["tipo"]
        susp[sid] = copia


# ---------------------------------------------------------------- validación por familia
def _normalizar(db: Session, empresa: Empresa, familia: str, clave: str, valor: dict, desde: date) -> dict:
    if familia in CATALOGOS:
        nombre = str((valor or {}).get("nombre", "")).strip()
        if not nombre or len(nombre) > 120:
            raise HTTPException(422, "Indique el nombre (hasta 120 caracteres).")
        if not (1 <= len(clave) <= 20) or clave != clave.strip():
            raise HTTPException(422, "El código debe tener entre 1 y 20 caracteres.")
        return {"nombre": nombre, "activo": bool((valor or {}).get("activo", True))}
    pr = cargar_parametros(db, empresa.diccionario_id, desde)
    if familia == "PARAMETRO":
        if clave not in EDITABLES_PERIODO:
            raise HTTPException(422, f"El parámetro {clave} no es configurable. Configurables: {', '.join(EDITABLES_PERIODO)}.")
        d = _dec((valor or {}).get("valor"), clave)
        if clave in TASAS_PERIODO and not (0 <= d <= 1):
            raise HTTPException(422, f"{clave} es una tasa: use un valor entre 0 y 1 (por ejemplo 0.09).")
        if clave not in TASAS_PERIODO and d <= 0:
            raise HTTPException(422, f"{clave} debe ser mayor que cero.")
        return {"valor": str(d)}
    if familia == "TASA_AFP":
        if clave.upper() not in pr.afps:
            raise HTTPException(422, f"La AFP {clave} no existe en el Diccionario.")
        out = {}
        for c in CAMPOS_AFP:
            d = _dec((valor or {}).get(c), c)
            if not (0 <= d <= 1):
                raise HTTPException(422, f"{c}: use una fracción entre 0 y 1 (por ejemplo 0.0147).")
            out[c] = str(d)
        return out
    if familia == "AFECTACION":
        c = pr.conceptos.get(clave)
        if not c or c.categoria != "INGRESO":
            raise HTTPException(422, f"{clave} no es un ingreso del Diccionario: solo los ingresos tienen afectaciones.")
        v = valor or {}
        return {"afp": bool(v.get("afp")), "essalud": bool(v.get("essalud")), "quinta": bool(v.get("quinta"))}
    if familia == "SUSPENSION":
        if clave not in {s.codigo_plame for s in pr.suspensiones.values()}:
            raise HTTPException(422, f"El tipo de suspensión {clave} no existe en el Diccionario.")
        tipo = str((valor or {}).get("tipo", "")).upper()
        if tipo not in TIPOS_SUSP:
            raise HTTPException(422, f"Tipo inválido. Use: {', '.join(TIPOS_SUSP)}.")
        return {"tipo": tipo}
    raise HTTPException(404, "Familia de configuración desconocida.")


def _fila_hist(r: ConfigVigencia, cerrado: Optional[date]) -> dict:
    return {"id": r.id, "clave": r.clave, "valor": r.valor, "aplica_desde": r.aplica_desde.strftime("%Y-%m"), "nota": r.nota,
            "creado": r.creado.isoformat() if r.creado else None, "editable": not (cerrado and r.aplica_desde <= cerrado)}


def _bloquear_unicas(familia: str) -> None:
    if familia in CATALOGOS:
        raise HTTPException(409, "Los centros de costo, puestos y áreas ya no llevan periodo de aplicación: se registran, editan y borran en Configuración General.")
    if familia in FAM_POR_PERIODO:
        raise HTTPException(409, "Los parámetros y las tasas AFP ya no se registran con periodo de aplicación: se editan en Cálculo de Planilla, por periodo.")
    if familia in FAM_UNICAS:
        raise HTTPException(409, "Las afectaciones y las suspensiones son dato único: se guardan con «Guardar» en Cálculo de Planilla, sin periodo de aplicación.")


def registrar(db: Session, empresa: Empresa, familia: str, datos) -> dict:
    if familia not in FAMILIAS:
        raise HTTPException(404, "Familia de configuración desconocida.")
    _bloquear_unicas(familia)
    desde = _mes(datos.aplica_desde)
    valor = _normalizar(db, empresa, familia, datos.clave, datos.valor, desde)
    cerrado = ultimo_cerrado(db, empresa.id)
    if cerrado and desde <= cerrado:
        raise HTTPException(409, f"El periodo de aplicación debe ser posterior a {cerrado.strftime('%m/%Y')}, último periodo cerrado: no se alteran meses cerrados.")
    if (db.query(ConfigVigencia).filter(ConfigVigencia.empresa_id == empresa.id, ConfigVigencia.familia == familia,
                                        ConfigVigencia.clave == datos.clave, ConfigVigencia.aplica_desde == desde).first()):
        raise HTTPException(409, f"Ya hay un registro de {datos.clave} que aplica desde {datos.aplica_desde}. Elimínelo o use otro mes.")
    r = ConfigVigencia(empresa_id=empresa.id, familia=familia, clave=datos.clave, valor=valor, aplica_desde=desde, nota=datos.nota)
    db.add(r)
    db.commit()
    return _fila_hist(r, cerrado)


def eliminar(db: Session, empresa: Empresa, familia: str, registro_id: int) -> dict:
    _bloquear_unicas(familia)
    r = db.query(ConfigVigencia).filter(ConfigVigencia.id == registro_id, ConfigVigencia.empresa_id == empresa.id, ConfigVigencia.familia == familia).first()
    if not r:
        raise HTTPException(404, "Registro no encontrado.")
    cerrado = ultimo_cerrado(db, empresa.id)
    if cerrado and r.aplica_desde <= cerrado:
        raise HTTPException(409, "Este registro ya aplicó a un periodo cerrado: no se puede eliminar.")
    db.delete(r)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- consulta
def listar(db: Session, empresa: Empresa, familia: str, periodo_id: Optional[int] = None) -> dict:
    if familia not in FAMILIAS:
        raise HTTPException(404, "Familia de configuración desconocida.")
    ref = _ref(db, empresa, periodo_id)
    vig = vigentes(db, empresa.id, familia, ref)
    cerrado = ultimo_cerrado(db, empresa.id)
    items = []
    if familia in CATALOGOS:
        for k, r in sorted(vig.items()):
            items.append({"clave": k, "etiqueta": r.valor["nombre"], "base": None, "vigente": r.valor, "origen": "EMPRESA", "desde": r.aplica_desde.strftime("%Y-%m")})
    else:
        pr = cargar_parametros(db, empresa.diccionario_id, ref)
        def item(clave, etiqueta, base):
            r = vig.get(clave)
            items.append({"clave": clave, "etiqueta": etiqueta, "base": base, "vigente": r.valor if r else base,
                          "origen": "EMPRESA" if r else "DICCIONARIO", "desde": r.aplica_desde.strftime("%Y-%m") if r else None})
        if familia == "PARAMETRO":
            for k in EDITABLES_PERIODO:
                item(k, k, {"valor": str(pr.p[k])} if k in pr.p else None)
        elif familia == "TASA_AFP":
            for a in sorted(pr.afps.values(), key=lambda x: x.nombre):
                item(a.nombre, a.nombre, {"aporte": str(a.aporte), "comision": str(a.comision), "comision_mixta_flujo": str(a.comision_mixta_flujo),
                                           "comision_saldo": str(a.comision_saldo), "prima": str(a.prima)})
        elif familia == "AFECTACION":
            for c in sorted((c for c in pr.conceptos.values() if c.categoria == "INGRESO"), key=lambda x: x.codigo):
                item(c.codigo, c.descripcion, {"afp": bool(c.afecta_afp_onp), "essalud": bool(c.afecta_essalud), "quinta": bool(c.afecta_5ta_regular or c.afecta_5ta_directa)})
        else:
            for s in sorted(pr.suspensiones.values(), key=lambda x: x.codigo_plame):
                item(s.codigo_plame, s.descripcion, {"tipo": s.tipo})
    hist = (db.query(ConfigVigencia).filter(ConfigVigencia.empresa_id == empresa.id, ConfigVigencia.familia == familia)
            .order_by(ConfigVigencia.aplica_desde.desc(), ConfigVigencia.clave).all())
    return {"familia": familia, "referencia": ref.strftime("%Y-%m"), "ultimo_periodo_cerrado": cerrado.strftime("%Y-%m") if cerrado else None,
            "items": items, "historial": [_fila_hist(r, cerrado) for r in hist]}


# ---------------------------------------------------------------- incidencia de los catálogos en la planilla
def _coincide(valor: str, codigo: str, nombre: str) -> bool:
    v = (valor or "").strip().upper()
    return bool(v) and (v == codigo.upper() or v == nombre.upper() or v.startswith(codigo.upper() + " ") or v == f"{codigo} {nombre}".upper())


def verificar_catalogos(db: Session, empresa: Empresa, periodo_id: int) -> dict:
    """Colaboradores cuyo centro de costo, puesto o área no existe en el catálogo o está inactivo. Solo se revisan los catálogos que la empresa ya empezó a llenar."""
    ref = _ref(db, empresa, periodo_id)
    p = db.get(Periodo, periodo_id)
    problemas = []
    empleados = (db.query(Empleado).filter(Empleado.empresa_id == empresa.id, Empleado.fecha_ingreso <= p.fecha_fin)
                 .filter((Empleado.fecha_cese.is_(None)) | (Empleado.fecha_cese >= p.fecha_inicio)).order_by(Empleado.apellidos).all())
    for fam in CATALOGOS:
        todos = ultimos(db, empresa.id, fam)
        if not todos:
            continue
        for e in empleados:
            valor = getattr(e, CAMPO_EMPLEADO[fam]) or ""
            if not valor.strip():
                problemas.append({"empleado_id": e.id, "colaborador": f"{e.apellidos}, {e.nombres}", "familia": fam, "valor": "", "motivo": "sin asignar"})
                continue
            coincide = [r for k, r in todos.items() if _coincide(valor, k, r.valor["nombre"])]
            if any(r.valor["activo"] for r in coincide):
                continue
            problemas.append({"empleado_id": e.id, "colaborador": f"{e.apellidos}, {e.nombres}", "familia": fam, "valor": valor,
                              "motivo": "inactivo en el catálogo" if coincide else "no existe en el catálogo"})
    return {"periodo_id": periodo_id, "referencia": ref.strftime("%Y-%m"), "problemas": problemas}


# ---------------------------------------------------------------- catálogos: tablas de consulta con registro, edición y borrado
def _chk_cat(familia: str) -> None:
    if familia not in CATALOGOS:
        raise HTTPException(404, "Catálogo desconocido (use CENTRO_COSTO, PUESTO o AREA).")


def _fila_cat(r: ConfigVigencia, en_uso: int) -> dict:
    return {"id": r.id, "codigo": r.clave, "nombre": r.valor["nombre"], "activo": bool(r.valor.get("activo", True)), "en_uso": en_uso}


def cat_listar(db: Session, empresa: Empresa, familia: str) -> dict:
    _chk_cat(familia)
    ult = ultimos(db, empresa.id, familia)
    todos = db.query(Empleado).filter(Empleado.empresa_id == empresa.id).all()
    campo = CAMPO_EMPLEADO[familia]
    items = [_fila_cat(r, sum(1 for e in todos if _coincide(getattr(e, campo) or "", k, r.valor["nombre"]))) for k, r in sorted(ult.items())]
    return {"familia": familia, "items": items}


def _limpiar_cat(codigo: str, nombre: str) -> tuple:
    codigo = (codigo or "").strip(); nombre = (nombre or "").strip()
    if not (1 <= len(codigo) <= 20):
        raise HTTPException(422, "El código debe tener entre 1 y 20 caracteres.")
    if not nombre or len(nombre) > 120:
        raise HTTPException(422, "Indique el nombre (hasta 120 caracteres).")
    return codigo, nombre


def cat_crear(db: Session, empresa: Empresa, familia: str, codigo: str, nombre: str, activo: bool = True) -> dict:
    _chk_cat(familia)
    codigo, nombre = _limpiar_cat(codigo, nombre)
    if codigo in ultimos(db, empresa.id, familia):
        raise HTTPException(409, f"Ya existe el código {codigo}. Edítelo en la tabla.")
    r = ConfigVigencia(empresa_id=empresa.id, familia=familia, clave=codigo, valor={"nombre": nombre, "activo": bool(activo)}, aplica_desde=FECHA_UNICA, nota="Catálogo")
    db.add(r); db.commit()
    return _fila_cat(r, 0)


def _reg_cat(db: Session, empresa: Empresa, familia: str, registro_id: int) -> ConfigVigencia:
    _chk_cat(familia)
    r = db.query(ConfigVigencia).filter(ConfigVigencia.id == registro_id, ConfigVigencia.empresa_id == empresa.id, ConfigVigencia.familia == familia).first()
    if not r:
        raise HTTPException(404, "Registro no encontrado.")
    return r


def cat_editar(db: Session, empresa: Empresa, familia: str, registro_id: int, nombre: str, activo: bool) -> dict:
    r = _reg_cat(db, empresa, familia, registro_id)
    _, nombre = _limpiar_cat(r.clave, nombre)
    db.query(ConfigVigencia).filter(ConfigVigencia.empresa_id == empresa.id, ConfigVigencia.familia == familia, ConfigVigencia.clave == r.clave, ConfigVigencia.id != r.id).delete()
    r.valor = {"nombre": nombre, "activo": bool(activo)}; r.aplica_desde = FECHA_UNICA
    db.commit()
    return _fila_cat(r, 0)


def cat_borrar(db: Session, empresa: Empresa, familia: str, registro_id: int) -> dict:
    r = _reg_cat(db, empresa, familia, registro_id)
    db.query(ConfigVigencia).filter(ConfigVigencia.empresa_id == empresa.id, ConfigVigencia.familia == familia, ConfigVigencia.clave == r.clave).delete()
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- afectaciones y suspensiones: dato único
def unico_listar(db: Session, empresa: Empresa, familia: str) -> dict:
    if familia not in FAM_UNICAS:
        raise HTTPException(404, "Solo las afectaciones y las suspensiones son dato único.")
    pr = cargar_parametros(db, empresa.diccionario_id, date.today())              # Diccionario puro = valores de origen
    ult = ultimos(db, empresa.id, familia)
    items = []

    def item(clave, etiqueta, base):
        r = ult.get(clave)
        items.append({"clave": clave, "etiqueta": etiqueta, "base": base, "vigente": r.valor if r else base, "modificado": r is not None})
    if familia == "AFECTACION":
        for c in sorted((c for c in pr.conceptos.values() if c.categoria == "INGRESO"), key=lambda x: x.codigo):
            item(c.codigo, c.descripcion, {"afp": bool(c.afecta_afp_onp), "essalud": bool(c.afecta_essalud), "quinta": bool(c.afecta_5ta_regular or c.afecta_5ta_directa)})
    else:
        for s in sorted(pr.suspensiones.values(), key=lambda x: x.codigo_plame):
            item(s.codigo_plame, s.descripcion, {"tipo": s.tipo})
    return {"familia": familia, "items": items}


def guardar_unico(db: Session, empresa: Empresa, familia: str, valores: dict) -> dict:
    """Guarda los valores como dato único. Un valor igual al del Diccionario no genera registro propio."""
    base = {i["clave"]: i["base"] for i in unico_listar(db, empresa, familia)["items"]}
    mes = date.today().replace(day=1)
    nuevos = {}
    for clave, valor in (valores or {}).items():
        if clave not in base:
            raise HTTPException(422, f"{clave} no existe en el Diccionario.")
        nuevos[clave] = _normalizar(db, empresa, familia, clave, valor, mes)
    for clave, v in nuevos.items():
        db.query(ConfigVigencia).filter(ConfigVigencia.empresa_id == empresa.id, ConfigVigencia.familia == familia, ConfigVigencia.clave == clave).delete()
        if v != base[clave]:
            db.add(ConfigVigencia(empresa_id=empresa.id, familia=familia, clave=clave, valor=v, aplica_desde=FECHA_UNICA, nota="Dato único"))
    db.commit()
    return unico_listar(db, empresa, familia)
