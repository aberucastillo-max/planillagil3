"""Parámetros legales y tasas AFP POR PERIODO (pestaña «Parámetros y tasas» de Cálculo de Planilla).

El Diccionario solo sirve para cargar el sistema la primera vez: al abrir un periodo se copian los valores del periodo anterior (o, si es el primero, los del
Diccionario) y desde ahí los modifica el usuario (Editar, Guardar, Cancelar) mientras el periodo esté ABIERTO. Al abrir el siguiente periodo la información viaja.
Un periodo CERRADO conserva sus valores y solo se consulta."""
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import List

from sqlalchemy.orm import Session

from .diccionario import EDITABLES_PERIODO, TASAS_PERIODO, ErrorDeNegocio, cargar_parametros
from .models import Empresa, Periodo, PeriodoAFP, PeriodoParametro


def _tiene_ediciones(db: Session, periodo_id: int) -> bool:
    return bool(db.query(PeriodoParametro).filter(PeriodoParametro.periodo_id == periodo_id).first()
                or db.query(PeriodoAFP).filter(PeriodoAFP.periodo_id == periodo_id).first())


def _guardar_filas(db: Session, periodo: Periodo, pr) -> None:
    for k in EDITABLES_PERIODO:
        if k in pr.p:
            db.add(PeriodoParametro(periodo_id=periodo.id, clave=k, valor=pr.p[k]))
    for a in pr.afps.values():
        db.add(PeriodoAFP(periodo_id=periodo.id, nombre=a.nombre.upper(), aporte=a.aporte, comision=a.comision,
                          comision_mixta_flujo=a.comision_mixta_flujo, comision_saldo=a.comision_saldo, prima=a.prima))


def asegurar_filas(db: Session, empresa: Empresa, periodo: Periodo) -> None:
    """Un periodo ABIERTO anterior a esta versión (sin valores propios) guarda los que venía usando para que ya se puedan editar."""
    if periodo.estado == "ABIERTO" and not _tiene_ediciones(db, periodo.id):
        _guardar_filas(db, periodo, cargar_parametros(db, empresa.diccionario_id, periodo.fecha_inicio, empresa_id=empresa.id, periodo_id=periodo.id))
        db.commit()


def heredar(db: Session, empresa: Empresa, periodo: Periodo) -> bool:
    """Al abrir un periodo: copia los valores del periodo anterior; si es el primero, los del Diccionario. Devuelve True si copió de un periodo anterior."""
    if _tiene_ediciones(db, periodo.id):
        return False
    previo = (db.query(Periodo).filter(Periodo.empresa_id == periodo.empresa_id, Periodo.fecha_inicio <= periodo.fecha_inicio, Periodo.id != periodo.id,
                                       Periodo.id < periodo.id).order_by(Periodo.fecha_inicio.desc(), Periodo.id.desc()).first())
    if previo is not None:
        pr = cargar_parametros(db, empresa.diccionario_id, previo.fecha_inicio, empresa_id=empresa.id, periodo_id=previo.id)
    else:
        pr = cargar_parametros(db, empresa.diccionario_id, periodo.fecha_inicio, empresa_id=empresa.id)
    _guardar_filas(db, periodo, pr)
    db.commit()
    return previo is not None


def efectivos(db: Session, empresa: Empresa, periodo: Periodo) -> dict:
    asegurar_filas(db, empresa, periodo)
    pr = cargar_parametros(db, empresa.diccionario_id, periodo.fecha_inicio, empresa_id=empresa.id, periodo_id=periodo.id)
    return {
        "periodo": {"id": periodo.id, "anio": periodo.anio, "mes": periodo.mes, "fase": periodo.fase, "estado": periodo.estado},
        "editable": periodo.estado == "ABIERTO",
        "parametros": {k: str(pr.p[k]) for k in EDITABLES_PERIODO if k in pr.p},
        "afps": [{"nombre": a.nombre, "aporte": str(a.aporte), "comision": str(a.comision),
                  "comision_mixta_flujo": str(a.comision_mixta_flujo), "comision_saldo": str(a.comision_saldo),
                  "prima": str(a.prima)} for a in sorted(pr.afps.values(), key=lambda x: x.nombre)],
    }


def _num(valor, nombre: str) -> Decimal:
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        raise ErrorDeNegocio(f"{nombre}: valor no numérico.", 422)


def guardar(db: Session, empresa: Empresa, periodo: Periodo, parametros: dict, afps: List[dict]) -> dict:
    if periodo.estado == "CERRADO":
        raise ErrorDeNegocio("El periodo está CERRADO: sus parámetros son históricos y no se editan. Reábralo con motivo para cambiarlos.", 403)
    nuevos = {}
    for clave, valor in (parametros or {}).items():
        if clave not in EDITABLES_PERIODO:
            raise ErrorDeNegocio(f"El parámetro {clave} no es editable por periodo.", 422)
        v = _num(valor, clave)
        if clave in TASAS_PERIODO and not (Decimal("0") <= v <= Decimal("1")):
            raise ErrorDeNegocio(f"{clave}: la tasa debe estar entre 0 y 1 (ej. 0.09 = 9 %).", 422)
        if clave not in TASAS_PERIODO and v <= 0:
            raise ErrorDeNegocio(f"{clave}: debe ser mayor que 0.", 422)
        nuevos[clave] = v
    filas_afp = []
    for a in afps or []:
        nombre = str(a.get("nombre", "")).upper()
        if not nombre:
            raise ErrorDeNegocio("Falta el nombre de una AFP.", 422)
        vals = {}
        for campo in ("aporte", "comision", "comision_mixta_flujo", "comision_saldo", "prima"):
            v = _num(a.get(campo, 0), f"{nombre}.{campo}")
            if not (Decimal("0") <= v <= Decimal("1")):
                raise ErrorDeNegocio(f"{nombre}.{campo}: la tasa debe estar entre 0 y 1.", 422)
            vals[campo] = v
        filas_afp.append((nombre, vals))
    for clave, v in nuevos.items():
        fila = db.query(PeriodoParametro).filter(PeriodoParametro.periodo_id == periodo.id, PeriodoParametro.clave == clave).first()
        if fila:
            fila.valor = v
        else:
            db.add(PeriodoParametro(periodo_id=periodo.id, clave=clave, valor=v))
    for nombre, vals in filas_afp:
        fila = db.query(PeriodoAFP).filter(PeriodoAFP.periodo_id == periodo.id, PeriodoAFP.nombre == nombre).first()
        if fila:
            for k, v in vals.items():
                setattr(fila, k, v)
        else:
            db.add(PeriodoAFP(periodo_id=periodo.id, nombre=nombre, **vals))
    db.commit()
    return efectivos(db, empresa, periodo)
