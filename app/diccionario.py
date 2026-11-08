"""Carga el DICCIONARIO vigente para una fecha. El motor NO tiene constantes legales escritas en código."""
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .models import (Concepto, DiccionarioAFP, DiccionarioEscala5ta, DiccionarioParametro,
                     TipoSuspension)

CLAVES_OBLIGATORIAS = [
    "UIT", "RMV", "ASIG_FAM_PCT", "ESSALUD_TASA", "CREDITO_EPS_TASA", "ONP_TASA", "BASE_DIAS",
    "HORAS_DIA", "HE_RECARGO_1", "HE_RECARGO_2", "DEDUCCION_5TA_UIT", "DEDUCCION_ADICIONAL_5TA_UIT",
    "TASA_5TA_DIRECTA",
]


class ErrorDeNegocio(Exception):
    def __init__(self, mensaje: str, codigo: int = 422):
        super().__init__(mensaje)
        self.mensaje, self.codigo = mensaje, codigo


def _vigente(r, fecha: date) -> bool:
    return r.vigente_desde <= fecha and (r.vigente_hasta is None or r.vigente_hasta >= fecha)


class ParametrosPeriodo:
    def __init__(self, p, afps, escala, conceptos, suspensiones):
        self.p: Dict[str, Decimal] = p
        self.afps: Dict[str, DiccionarioAFP] = afps
        self.escala: List[Tuple[Decimal, Optional[Decimal], Decimal]] = escala
        self.conceptos: Dict[str, Concepto] = conceptos
        self.suspensiones: Dict[int, TipoSuspension] = suspensiones

    def __getitem__(self, clave: str) -> Decimal:
        return self.p[clave]


def cargar_parametros(db: Session, diccionario_id: int, fecha: date) -> ParametrosPeriodo:
    vig: Dict[str, DiccionarioParametro] = {}
    for r in db.query(DiccionarioParametro).filter(DiccionarioParametro.diccionario_id == diccionario_id):
        if _vigente(r, fecha) and (r.clave not in vig or r.vigente_desde > vig[r.clave].vigente_desde):
            vig[r.clave] = r
    faltan = [k for k in CLAVES_OBLIGATORIAS if k not in vig]
    if faltan:
        raise ErrorDeNegocio(
            f"El Diccionario no tiene parámetros vigentes al {fecha}: {', '.join(faltan)}. "
            "Actualice el Diccionario antes de calcular.", 409)
    afps: Dict[str, DiccionarioAFP] = {}
    for a in db.query(DiccionarioAFP).filter(DiccionarioAFP.diccionario_id == diccionario_id):
        if _vigente(a, fecha):
            k = a.nombre.upper()
            if k not in afps or a.vigente_desde > afps[k].vigente_desde:
                afps[k] = a
    escala = [(e.desde_uit, e.hasta_uit, e.tasa) for e in
              db.query(DiccionarioEscala5ta).filter(DiccionarioEscala5ta.diccionario_id == diccionario_id)]
    conceptos = {c.codigo: c for c in db.query(Concepto).filter(Concepto.diccionario_id == diccionario_id)}
    susp = {s.id: s for s in db.query(TipoSuspension).filter(TipoSuspension.diccionario_id == diccionario_id)}
    return ParametrosPeriodo({k: Decimal(v.valor) for k, v in vig.items()}, afps, escala, conceptos, susp)
