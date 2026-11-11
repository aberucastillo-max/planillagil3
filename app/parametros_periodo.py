"""Parámetros legales y tasas AFP con EDICIONES PROPIAS por periodo (antes, pestaña «Soporte de Fórmula»; la pantalla ya no las edita: los valores se
configuran en Configuración General con su periodo de aplicación y estas ediciones solo se pueden consultar o quitar).
Al abrir un periodo se arrastran los valores editados del mes anterior; luego el usuario puede editarlos y guardarlos
mientras el periodo esté ABIERTO. Sin ediciones, rigen los valores del Diccionario."""
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import List

from sqlalchemy.orm import Session

from .diccionario import EDITABLES_PERIODO, TASAS_PERIODO, ErrorDeNegocio, cargar_parametros
from .models import Empresa, Periodo, PeriodoAFP, PeriodoParametro


def _tiene_ediciones(db: Session, periodo_id: int) -> bool:
    return bool(db.query(PeriodoParametro).filter(PeriodoParametro.periodo_id == periodo_id).first()
                or db.query(PeriodoAFP).filter(PeriodoAFP.periodo_id == periodo_id).first())


def _origen(db: Session, empresa: Empresa, periodo: Periodo) -> str:
    """De dónde salen los valores del periodo: ediciones propias del periodo (prevalecen), configuración de la empresa o Diccionario."""
    from .configuracion import vigentes
    if _tiene_ediciones(db, periodo.id):
        return "PERIODO"
    mes = date(periodo.anio, periodo.mes, 1)
    return "CONFIGURACION" if (vigentes(db, empresa.id, "PARAMETRO", mes) or vigentes(db, empresa.id, "TASA_AFP", mes)) else "DICCIONARIO"


def efectivos(db: Session, empresa: Empresa, periodo: Periodo) -> dict:
    pr = cargar_parametros(db, empresa.diccionario_id, periodo.fecha_inicio, empresa_id=empresa.id, periodo_id=periodo.id)
    return {
        "periodo": {"id": periodo.id, "anio": periodo.anio, "mes": periodo.mes, "estado": periodo.estado},
        "origen": _origen(db, empresa, periodo),
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


def copiar_del_anterior(db: Session, periodo: Periodo) -> bool:
    """Al abrir un periodo: copia lo editado en el periodo anterior de la empresa (si hubo ediciones)."""
    previos = (db.query(Periodo).filter(Periodo.empresa_id == periodo.empresa_id, Periodo.fecha_inicio < periodo.fecha_inicio,
                                        Periodo.id != periodo.id).order_by(Periodo.fecha_inicio.desc(), Periodo.id.desc()).all())
    for prev in previos:
        if _tiene_ediciones(db, prev.id):
            for r in db.query(PeriodoParametro).filter(PeriodoParametro.periodo_id == prev.id):
                db.add(PeriodoParametro(periodo_id=periodo.id, clave=r.clave, valor=r.valor))
            for r in db.query(PeriodoAFP).filter(PeriodoAFP.periodo_id == prev.id):
                db.add(PeriodoAFP(periodo_id=periodo.id, nombre=r.nombre, aporte=r.aporte, comision=r.comision,
                                  comision_mixta_flujo=r.comision_mixta_flujo, comision_saldo=r.comision_saldo, prima=r.prima))
            db.commit()
            return True
    return False


def restablecer(db: Session, empresa: Empresa, periodo: Periodo) -> dict:
    """Quita las ediciones propias del periodo ABIERTO (editadas o heredadas): vuelven a regir la Configuración General y el Diccionario."""
    if periodo.estado == "CERRADO":
        raise ErrorDeNegocio("El periodo está CERRADO: sus parámetros son históricos y no se modifican. Reábralo con motivo para cambiarlos.", 403)
    db.query(PeriodoParametro).filter(PeriodoParametro.periodo_id == periodo.id).delete()
    db.query(PeriodoAFP).filter(PeriodoAFP.periodo_id == periodo.id).delete()
    db.commit()
    return efectivos(db, empresa, periodo)
