"""Carga el DICCIONARIO vigente para una fecha. El motor NO tiene constantes legales escritas en código."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .models import (Concepto, DiccionarioAFP, DiccionarioEscala5ta, DiccionarioParametro, PeriodoAFP,
                     PeriodoParametro, TipoSuspension)

CLAVES_OBLIGATORIAS = [
    "UIT", "RMV", "ASIG_FAM_PCT", "ESSALUD_TASA", "CREDITO_EPS_TASA", "ONP_TASA", "BASE_DIAS",
    "HORAS_DIA", "HE_RECARGO_1", "HE_RECARGO_2", "DEDUCCION_5TA_UIT", "DEDUCCION_ADICIONAL_5TA_UIT",
    "TASA_5TA_DIRECTA",
]


CLAVES_LIQUIDACION = [
    "BONIF_EXTRA_PCT", "BONIF_EXTRA_EPS_PCT", "GRATI_DIVISOR", "CTS_DIVISOR_MES", "CTS_DIVISOR_DIA",
    "VAC_DIAS_ANIO", "INDEM_FACTOR", "INDEM_TOPE", "REGULARIDAD_MESES", "PROMEDIO_MESES", "RMA_PRIMA",
]


class ErrorDeNegocio(Exception):
    def __init__(self, mensaje: str, codigo: int = 422):
        super().__init__(mensaje)
        self.mensaje, self.codigo = mensaje, codigo


def _vigente(r, fecha: date) -> bool:
    return r.vigente_desde <= fecha and (r.vigente_hasta is None or r.vigente_hasta >= fecha)


# Parámetros que el usuario puede editar por periodo (Soporte de Fórmula). El resto se cambia con una nueva versión del Diccionario.
EDITABLES_PERIODO = ("UIT", "RMV", "ASIG_FAM_PCT", "ESSALUD_TASA", "CREDITO_EPS_TASA", "ONP_TASA", "RMA_PRIMA")
TASAS_PERIODO = ("ASIG_FAM_PCT", "ESSALUD_TASA", "CREDITO_EPS_TASA", "ONP_TASA")


@dataclass(frozen=True)
class TasaAFP:
    nombre: str
    aporte: Decimal
    comision: Decimal                 # comisión sobre flujo (% de la remuneración)
    comision_mixta_flujo: Decimal     # componente mensual de la comisión mixta
    comision_saldo: Decimal           # comisión anual sobre saldo (informativa: no se descuenta en planilla)
    prima: Decimal
    tope_prima: Optional[Decimal] = None

    def comision_planilla(self, tipo_comision: Optional[str]) -> Decimal:
        """La comisión que se descuenta en la boleta según el tipo al que está vinculado el trabajador."""
        return self.comision_mixta_flujo if tipo_comision == "MIXTA" else self.comision


class ParametrosPeriodo:
    def __init__(self, p, afps, escala, conceptos, suspensiones):
        self.p: Dict[str, Decimal] = p
        self.afps: Dict[str, TasaAFP] = afps
        self.escala: List[Tuple[Decimal, Optional[Decimal], Decimal]] = escala
        self.conceptos: Dict[str, Concepto] = conceptos
        self.suspensiones: Dict[int, TipoSuspension] = suspensiones

    def __getitem__(self, clave: str) -> Decimal:
        return self.p[clave]


def cargar_parametros(db: Session, diccionario_id: int, fecha: date, extra=(), periodo_id: Optional[int] = None,
                      empresa_id: Optional[int] = None) -> ParametrosPeriodo:
    vig: Dict[str, DiccionarioParametro] = {}
    for r in db.query(DiccionarioParametro).filter(DiccionarioParametro.diccionario_id == diccionario_id):
        if _vigente(r, fecha) and (r.clave not in vig or r.vigente_desde > vig[r.clave].vigente_desde):
            vig[r.clave] = r
    faltan = [k for k in list(CLAVES_OBLIGATORIAS) + list(extra) if k not in vig]
    if faltan:
        raise ErrorDeNegocio(
            f"El Diccionario no tiene parámetros vigentes al {fecha}: {', '.join(faltan)}. "
            "Actualice el Diccionario antes de calcular.", 409)
    afps_orm: Dict[str, DiccionarioAFP] = {}
    for a in db.query(DiccionarioAFP).filter(DiccionarioAFP.diccionario_id == diccionario_id):
        if _vigente(a, fecha):
            k = a.nombre.upper()
            if k not in afps_orm or a.vigente_desde > afps_orm[k].vigente_desde:
                afps_orm[k] = a
    afps: Dict[str, TasaAFP] = {k: TasaAFP(a.nombre, Decimal(a.aporte), Decimal(a.comision), Decimal(a.comision_mixta_flujo or 0),
                                           Decimal(a.comision_saldo or 0), Decimal(a.prima),
                                           Decimal(a.tope_prima) if a.tope_prima is not None else None)
                                for k, a in afps_orm.items()}
    escala = [(e.desde_uit, e.hasta_uit, e.tasa) for e in
              db.query(DiccionarioEscala5ta).filter(DiccionarioEscala5ta.diccionario_id == diccionario_id)]
    conceptos = {c.codigo: c for c in db.query(Concepto).filter(Concepto.diccionario_id == diccionario_id)}
    susp = {s.id: s for s in db.query(TipoSuspension).filter(TipoSuspension.diccionario_id == diccionario_id)}
    valores = {k: Decimal(v.valor) for k, v in vig.items()}
    if empresa_id is not None:                      # configuración de la empresa con periodo de aplicación (entre el Diccionario y lo editado en el periodo)
        from .configuracion import aplicar_a_parametros
        aplicar_a_parametros(db, empresa_id, fecha, valores, afps, conceptos, susp)
    if periodo_id is not None:                      # lo editado en el periodo (o arrastrado del mes anterior) tiene prioridad
        for r in db.query(PeriodoParametro).filter(PeriodoParametro.periodo_id == periodo_id):
            valores[r.clave] = Decimal(r.valor)
        for r in db.query(PeriodoAFP).filter(PeriodoAFP.periodo_id == periodo_id):
            base = afps.get(r.nombre.upper())
            afps[r.nombre.upper()] = TasaAFP(r.nombre, Decimal(r.aporte), Decimal(r.comision), Decimal(r.comision_mixta_flujo or 0),
                                             Decimal(r.comision_saldo or 0), Decimal(r.prima), base.tope_prima if base else None)
    return ParametrosPeriodo(valores, afps, escala, conceptos, susp)
