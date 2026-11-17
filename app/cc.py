"""Construcción Civil (obreros): tipos de obrero, tarifas del convenio y tarifas editables por empresa con vigencia.
Valores: RM 197-2025-TR (vigente del 01.01.2026 al 31.12.2026), tablas de la Federación de Trabajadores en Construcción Civil del Perú.
El convenio se carga en el Diccionario CONSTRUCCION_CIVIL; la empresa puede editar cualquier valor desde una fecha (CcValor) sin tocar el convenio."""
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Dict, List

from sqlalchemy.orm import Session

from .diccionario import ErrorDeNegocio
from .models import CcValor

# tipo de obrero -> (nombre, categoría base de la que toma jornal y BUC, clave de BAE o None)
TIPOS_OBRERO = {
    "OPERARIO": ("Operario", "OPERARIO", None),
    "OFICIAL": ("Oficial", "OFICIAL", None),
    "PEON": ("Peón", "PEON", None),
    "OP_EQUIPO_MEDIANO": ("Operario · Operador de equipo mediano", "OPERARIO", "BAE_OP_EQUIPO_MEDIANO"),
    "OP_EQUIPO_PESADO": ("Operario · Operador de equipo pesado", "OPERARIO", "BAE_OP_EQUIPO_PESADO"),
    "OP_ELECTROMECANICO": ("Operario · Electromecánico", "OPERARIO", "BAE_OP_ELECTROMECANICO"),
    "OP_TOPOGRAFO": ("Operario · Topógrafo", "OPERARIO", "BAE_OP_TOPOGRAFO"),
}

# clave -> (valor del convenio, etiqueta, grupo)
VALORES_CONVENIO: Dict[str, tuple] = {
    "JORNAL_OPERARIO": ("89.30", "Jornal básico · Operario (S/ por día)", "Jornales"),
    "JORNAL_OFICIAL": ("69.75", "Jornal básico · Oficial (S/ por día)", "Jornales"),
    "JORNAL_PEON": ("62.80", "Jornal básico · Peón (S/ por día)", "Jornales"),
    "BUC_OPERARIO": ("0.32", "BUC · Operario (% del jornal)", "Bonificaciones"),
    "BUC_OFICIAL": ("0.30", "BUC · Oficial (% del jornal)", "Bonificaciones"),
    "BUC_PEON": ("0.30", "BUC · Peón (% del jornal)", "Bonificaciones"),
    "BAE_OP_EQUIPO_MEDIANO": ("0.08", "BAE · Operador de equipo mediano (%)", "Bonificaciones"),
    "BAE_OP_EQUIPO_PESADO": ("0.10", "BAE · Operador de equipo pesado (%)", "Bonificaciones"),
    "BAE_OP_ELECTROMECANICO": ("0.22", "BAE · Electromecánico (%)", "Bonificaciones"),
    "BAE_OP_TOPOGRAFO": ("0.09", "BAE · Topógrafo (%)", "Bonificaciones"),
    "MOVILIDAD_DIA": ("8.60", "Bonificación por movilidad (S/ por día trabajado)", "Bonificaciones"),
    "INDEM_PCT": ("0.15", "Indemnización por tiempo de servicios (% del jornal)", "Beneficios sociales"),
    "VAC_PCT": ("0.10", "Vacaciones truncas (% del jornal)", "Beneficios sociales"),
    "GRATI_JORNALES": ("40", "Gratificación: jornales por semestre", "Beneficios sociales"),
    "GRATI_DIV_ENE_JUL": ("210", "Gratificación: divisor diario de enero a julio", "Beneficios sociales"),
    "GRATI_DIV_AGO_DIC": ("150", "Gratificación: divisor diario de agosto a diciembre", "Beneficios sociales"),
    "BONIF_EXTRA_CC_PCT": ("0.09", "Bonificación extraordinaria sobre la gratificación (%)", "Beneficios sociales"),
    "DSO_DIVISOR_HORAS": ("48", "Descanso semanal: divisor de horas (jornal ÷ 48 × horas trabajadas)", "Jornada"),
    "HE_PCT_60": ("0.60", "Horas extras: recargo primeras horas (%)", "Jornada"),
    "HE_PCT_100": ("1.00", "Horas extras: recargo posterior / feriados (%)", "Jornada"),
    "CONAFOVICER_PCT": ("0.02", "CONAFOVICER (% sobre jornal + DSO + feriados)", "Descuentos"),
}
GRUPOS = ["Jornales", "Bonificaciones", "Beneficios sociales", "Jornada", "Descuentos"]

# codigo, descripcion, categoria, naturaleza, calculado, afp_onp, 5ta_reg, essalud, codigo_plame (por validar)
CONCEPTOS_CC = [
    ("@JORNAL_BASICO", "Jornal básico", "INGRESO", "FORMULA", True, 1, 1, 1, "0121"),
    ("@DSO", "Descanso semanal obligatorio (DSO)", "INGRESO", "FORMULA", True, 1, 1, 1, None),
    ("@FERIADO", "Feriados pagados", "INGRESO", "FORMULA", True, 1, 1, 1, None),
    ("@BUC", "Bonificación unificada de construcción (BUC)", "INGRESO", "FORMULA", True, 1, 1, 1, None),
    ("@BAE", "Bonificación por alta especialización (BAE)", "INGRESO", "FORMULA", True, 1, 1, 1, None),
    ("@MOVILIDAD_CC", "Bonificación por movilidad", "INGRESO", "FORMULA", True, 0, 0, 0, None),
    ("@HORAS_EXTRAS_60", "Horas extras con recargo 60 %", "INGRESO", "FORMULA", True, 1, 1, 1, None),
    ("@HORAS_EXTRAS_100", "Horas extras con recargo 100 %", "INGRESO", "FORMULA", True, 1, 1, 1, None),
    ("@INDEMNIZACION_CC", "Indemnización por tiempo de servicios (15 %)", "INGRESO", "FORMULA", True, 0, 0, 0, None),
    ("@VACACIONES_TRUNCAS_CC", "Vacaciones truncas (10 %)", "INGRESO", "FORMULA", True, 1, 1, 1, None),
    ("@GRATIF_CC", "Gratificación (prorrata semanal)", "INGRESO", "FORMULA", True, 0, 1, 0, None),
    ("@BONIF_EXTRA_CC", "Bonificación extraordinaria sobre la gratificación", "INGRESO", "FORMULA", True, 0, 1, 0, None),
    ("@CONAFOVICER", "Aporte CONAFOVICER", "DESCUENTO", "FORMULA", True, 0, 0, 0, None),
    ("@ASIGNACION_ESCOLAR", "Asignación escolar", "INGRESO", "VARIABLE", False, 0, 0, 0, None),
    ("@HEXT_60", "Horas extras 60 % (cantidad)", "PARAMETRO", "VARIABLE", False, 0, 0, 0, None),
    ("@HEXT_100", "Horas extras 100 % (cantidad)", "PARAMETRO", "VARIABLE", False, 0, 0, 0, None),
    ("@HORAS_TRABAJADAS", "Horas trabajadas en la semana", "PARAMETRO", "VARIABLE", False, 0, 0, 0, None),
    ("@DIAS_FERIADO", "Días de feriado pagado", "PARAMETRO", "VARIABLE", False, 0, 0, 0, None),
]
HANDLED_CC = {"@DIAS_TRABAJADOS", "@HEXT_60", "@HEXT_100", "@HORAS_TRABAJADAS", "@DIAS_FERIADO"}


def aplicar_overrides(db: Session, empresa_id: int, fecha: date, valores: Dict[str, Decimal]) -> None:
    """Pisa los valores del convenio con los que la empresa registró (el de mayor vigencia que ya rige a `fecha`)."""
    vig: Dict[str, CcValor] = {}
    for r in db.query(CcValor).filter(CcValor.empresa_id == empresa_id, CcValor.vigente_desde <= fecha):
        if r.clave not in vig or r.vigente_desde > vig[r.clave].vigente_desde:
            vig[r.clave] = r
    for k, r in vig.items():
        valores[k] = Decimal(r.valor)


def tarifas_vigentes(db: Session, empresa, fecha: date) -> List[dict]:
    from .diccionario import cargar_parametros
    pr = cargar_parametros(db, empresa.diccionario_id, fecha)
    base = {k: pr.p[k] for k in VALORES_CONVENIO if k in pr.p}
    vals = dict(base)
    aplicar_overrides(db, empresa.id, fecha, vals)
    desde = {}
    for r in db.query(CcValor).filter(CcValor.empresa_id == empresa.id, CcValor.vigente_desde <= fecha):
        if r.clave not in desde or r.vigente_desde > desde[r.clave]:
            desde[r.clave] = r.vigente_desde
    return [{"clave": k, "etiqueta": VALORES_CONVENIO[k][1], "grupo": VALORES_CONVENIO[k][2], "valor": str(vals.get(k)),
             "convenio": str(base.get(k)), "origen": "EMPRESA" if k in desde else "CONVENIO", "vigente_desde": desde.get(k)}
            for k in VALORES_CONVENIO]


def guardar_tarifas(db: Session, empresa_id: int, vigente_desde: date, valores: Dict[str, str]) -> int:
    n = 0
    for clave, v in valores.items():
        if clave not in VALORES_CONVENIO:
            raise ErrorDeNegocio(f"Tarifa desconocida: {clave}.", 422)
        try:
            d = Decimal(str(v))
        except (InvalidOperation, ValueError):
            raise ErrorDeNegocio(f"{VALORES_CONVENIO[clave][1]}: valor no numérico.", 422)
        if d < 0:
            raise ErrorDeNegocio(f"{VALORES_CONVENIO[clave][1]}: no puede ser negativo.", 422)
        fila = db.query(CcValor).filter_by(empresa_id=empresa_id, clave=clave, vigente_desde=vigente_desde).first()
        if fila:
            fila.valor = d
        else:
            db.add(CcValor(empresa_id=empresa_id, clave=clave, valor=d, vigente_desde=vigente_desde))
        n += 1
    db.commit()
    return n
