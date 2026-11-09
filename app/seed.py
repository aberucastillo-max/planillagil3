"""Siembra el DICCIONARIO Régimen General (RG_728) v1.
Los valores marcados 'validar' son REFERENCIALES: confírmelos con la norma vigente antes de producción."""
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from .models import (Concepto, Diccionario, DiccionarioAFP, DiccionarioEscala5ta, DiccionarioParametro,
                     TipoSuspension)

DESDE = date(2026, 1, 1)

PARAMETROS = [  # clave, valor, nota
    ("UIT", "5500", "DS 301-2025-EF"),
    ("RMV", "1130", "validar vigencia"),
    ("ASIG_FAM_PCT", "0.10", "10 % de la RMV"),
    ("ESSALUD_TASA", "0.09", ""),
    ("CREDITO_EPS_TASA", "0.0225", ""),
    ("ONP_TASA", "0.13", ""),
    ("BASE_DIAS", "30", "base estándar mensual"),
    ("HORAS_DIA", "8", ""),
    ("HE_RECARGO_1", "0.25", "primeras 2 horas"),
    ("HE_RECARGO_2", "0.35", "desde la 3.ª hora"),
    ("DEDUCCION_5TA_UIT", "7", ""),
    ("DEDUCCION_ADICIONAL_5TA_UIT", "0", "3 UIT con comprobantes: activar cuando exista el módulo de gastos"),
    ("TASA_5TA_DIRECTA", "0.14", "validar"),
]

AFPS = [  # nombre, aporte, comision flujo, prima, tope prima   (tasas referenciales: actualizar según SBS)
    ("HABITAT", "0.10", "0.0147", "0.0137", "12875"),
    ("INTEGRA", "0.10", "0.0155", "0.0137", "12875"),
    ("PRIMA", "0.10", "0.0160", "0.0137", "12875"),
    ("PROFUTURO", "0.10", "0.0169", "0.0137", "12875"),
]

ESCALA_5TA = [("0", "5", "0.08"), ("5", "20", "0.14"), ("20", "35", "0.17"), ("35", "45", "0.20"), ("45", None, "0.30")]

# codigo, descripcion, categoria, naturaleza, calculado, afp_onp, 5ta_reg, 5ta_dir, essalud, codigo_plame (validar)
CONCEPTOS = [
    ("@SUELDO_TEORICO", "Sueldo teórico", "PARAMETRO", "FIJO", True, 0, 0, 0, 0, None),
    ("@DIAS_TRABAJADOS", "Días trabajados", "PARAMETRO", "FORMULA", False, 0, 0, 0, 0, None),
    ("@HEXT_25", "Horas extras 25 % (cantidad)", "PARAMETRO", "VARIABLE", False, 0, 0, 0, 0, None),
    ("@HEXT_35", "Horas extras 35 % (cantidad)", "PARAMETRO", "VARIABLE", False, 0, 0, 0, 0, None),
    ("@MINUTOS_TARDANZA", "Minutos de tardanza", "PARAMETRO", "VARIABLE", False, 0, 0, 0, 0, None),
    ("@DIAS_FALTAS", "Días de faltas", "PARAMETRO", "VARIABLE", True, 0, 0, 0, 0, None),
    ("@DIAS_SUSPENSION", "Días de suspensión", "PARAMETRO", "VARIABLE", True, 0, 0, 0, 0, None),
    ("@DIAS_LIC_SIN_GOCE", "Días de licencia sin goce", "PARAMETRO", "VARIABLE", True, 0, 0, 0, 0, None),
    ("@DIAS_LIC_CON_GOCE", "Días de licencia con goce", "PARAMETRO", "VARIABLE", True, 0, 0, 0, 0, None),
    ("@DIAS_SUBSIDIO", "Días de subsidio EsSalud", "PARAMETRO", "VARIABLE", True, 0, 0, 0, 0, None),
    ("@DIAS_DMEDICO", "Días de descanso médico (20 días empleador)", "PARAMETRO", "VARIABLE", True, 0, 0, 0, 0, None),
    ("@HABER_BASICO", "Haber básico", "INGRESO", "FORMULA", True, 1, 1, 0, 1, "0121"),
    ("@ASIGNACION_FAMILIAR", "Asignación familiar", "INGRESO", "FORMULA", True, 1, 1, 0, 1, "0201"),
    ("@HORAS_EXTRAS_25", "Horas extras 25 %", "INGRESO", "VARIABLE", True, 1, 1, 0, 1, "0105"),
    ("@HORAS_EXTRAS_35", "Horas extras 35 %", "INGRESO", "VARIABLE", True, 1, 1, 0, 1, "0106"),
    ("@COMISIONES", "Comisiones", "INGRESO", "VARIABLE", False, 1, 1, 0, 1, "0107"),
    ("@SUBS_ENFERMEDAD", "Subsidio por enfermedad", "INGRESO", "FORMULA", True, 0, 0, 0, 0, "0915"),
    ("@REMUNERA_VACACIONAL", "Remuneración vacacional", "INGRESO", "FORMULA", True, 1, 1, 0, 1, "0118"),
    ("@GRATIFICACION", "Gratificación", "INGRESO", "VARIABLE", False, 0, 1, 0, 0, "0406"),
    ("@BONIF_EXTRA_29351", "Bonificación extraordinaria Ley 29351", "INGRESO", "VARIABLE", False, 0, 1, 0, 0, "0312"),
    ("@INGRESO_5TA_DIRECTA", "Ingreso extraordinario (5.ª directa)", "INGRESO", "VARIABLE", False, 0, 0, 1, 0, None),
    ("@DESCUENTO_EPS", "Descuento EPS", "DESCUENTO", "VARIABLE", True, 0, 0, 0, 0, "0706"),
    ("@QUINCENA", "Adelanto de quincena", "DESCUENTO", "VARIABLE", False, 0, 0, 0, 0, "0701"),
    ("@FONDO_PENSIONES", "Aporte obligatorio AFP", "DESCUENTO", "FORMULA", True, 0, 0, 0, 0, "0608"),
    ("@SEGURO_INVALIDEZ", "Prima de seguro AFP", "DESCUENTO", "FORMULA", True, 0, 0, 0, 0, "0601"),
    ("@COMISION_VARIABLE", "Comisión AFP", "DESCUENTO", "FORMULA", True, 0, 0, 0, 0, "0606"),
    ("@SNP_ONP", "Aporte ONP", "DESCUENTO", "FORMULA", True, 0, 0, 0, 0, "0607"),
    ("@IMP_RENTA_5TA", "Impuesto a la renta de 5.ª categoría", "DESCUENTO", "FORMULA", True, 0, 0, 0, 0, "0605"),
    ("@ESSALUD", "Aporte EsSalud (empleador)", "APORTE", "FORMULA", True, 0, 0, 0, 0, None),
    ("@APORTE_EPS", "Aporte EPS (empleador)", "APORTE", "FORMULA", True, 0, 0, 0, 0, None),
]

SUSPENSIONES = [  # códigos tal como figuran en el Registro Maestro; validar contra la Tabla 21 oficial de SUNAT
    ("01", "Sanción disciplinaria", "PERFECTA", "@DIAS_SUSPENSION"),
    ("04", "Permiso o licencia concedida por el empleador sin goce de haber", "PERFECTA", "@DIAS_LIC_SIN_GOCE"),
    ("05", "Caso fortuito o fuerza mayor", "PERFECTA", "@DIAS_SUSPENSION"),
    ("06", "Falta injustificada", "PERFECTA", "@DIAS_FALTAS"),
    ("07", "Inasistencia injustificada", "PERFECTA", "@DIAS_FALTAS"),
    ("08", "Licencia para cargos cívicos y servicio militar", "IMPERFECTA", "@DIAS_LIC_CON_GOCE"),
    ("09", "Licencia con goce de haber concedida por el empleador", "IMPERFECTA", "@DIAS_LIC_CON_GOCE"),
    ("20", "Enfermedad o accidente común - subsidiado EsSalud", "SUBSIDIADA", "@DIAS_SUBSIDIO"),
    ("21", "Maternidad pre y post natal - subsidiado EsSalud", "SUBSIDIADA", "@DIAS_SUBSIDIO"),
    ("22", "Descanso médico, primeros 20 días a cargo del empleador", "IMPERFECTA", "@DIAS_DMEDICO"),
    ("23", "Incapacidad temporal prolongada", "SUBSIDIADA", "@DIAS_SUBSIDIO"),
    ("24", "Licencia por paternidad", "IMPERFECTA", "@DIAS_LIC_CON_GOCE"),
    ("25", "Licencia por adopción", "IMPERFECTA", "@DIAS_LIC_CON_GOCE"),
    ("26", "Terapia de hijos con discapacidad", "IMPERFECTA", "@DIAS_LIC_CON_GOCE"),
    ("27", "Enfermedad grave de familiar directo (Ley 30012)", "IMPERFECTA", "@DIAS_LIC_CON_GOCE"),
    ("28", "Exámenes oncológicos preventivos", "IMPERFECTA", "@DIAS_LIC_CON_GOCE"),
    ("30", "Suspensión perfecta de labores", "PERFECTA", "@DIAS_SUSPENSION"),
]


def sembrar_rg728(db: Session) -> Diccionario:
    dic = db.query(Diccionario).filter_by(codigo="RG_728", version=1).first()
    if dic:
        completar_rg728(db)
        return dic
    dic = Diccionario(codigo="RG_728", nombre="Régimen General (D.Leg. 728)", version=1, vigente_desde=DESDE)
    db.add(dic)
    db.flush()
    for clave, valor, nota in PARAMETROS:
        db.add(DiccionarioParametro(diccionario_id=dic.id, clave=clave, valor=Decimal(valor), vigente_desde=DESDE, nota=nota))
    for n, ap, co, pr, tp in AFPS:
        db.add(DiccionarioAFP(diccionario_id=dic.id, nombre=n, aporte=Decimal(ap), comision=Decimal(co),
                              prima=Decimal(pr), tope_prima=Decimal(tp), vigente_desde=DESDE))
    for d, h, t in ESCALA_5TA:
        db.add(DiccionarioEscala5ta(diccionario_id=dic.id, desde_uit=Decimal(d),
                                    hasta_uit=Decimal(h) if h else None, tasa=Decimal(t)))
    for cod, desc, cat, nat, calc, afp, r5, d5, ess, plame in CONCEPTOS:
        db.add(Concepto(diccionario_id=dic.id, codigo=cod, descripcion=desc, categoria=cat, naturaleza=nat,
                        calculado=calc, afecta_afp_onp=bool(afp), afecta_5ta_regular=bool(r5),
                        afecta_5ta_directa=bool(d5), afecta_essalud=bool(ess), codigo_plame=plame))
    for cod, desc, tipo, conc in SUSPENSIONES:
        db.add(TipoSuspension(diccionario_id=dic.id, codigo_plame=cod, descripcion=desc, tipo=tipo, concepto_codigo=conc))
    db.commit()
    completar_rg728(db)
    return dic


# ------------------------------------------------------------------ liquidación por cese (etapa 2)
# Los valores marcados 'validar' son los de la propuesta aprobada y siguen POR VALIDAR con contador / norma.
PARAMETROS_LIQUIDACION = [  # clave, valor, desde, hasta, nota
    ("BONIF_EXTRA_PCT", "0.09", DESDE, None, "bonificación extraordinaria sobre la gratificación (validar)"),
    ("BONIF_EXTRA_EPS_PCT", "0.0675", DESDE, None, "con EPS (validar)"),
    ("GRATI_DIVISOR", "6", DESDE, None, "gratificación trunca = RC / 6 por mes completo (validar)"),
    ("CTS_DIVISOR_MES", "12", DESDE, None, "CTS = RC / 12 por mes completo (validar)"),
    ("CTS_DIVISOR_DIA", "360", DESDE, None, "CTS = RC / 360 por día (validar)"),
    ("VAC_DIAS_ANIO", "30", DESDE, None, "30 días por año; 2.5 por mes (validar)"),
    ("INDEM_FACTOR", "1.5", DESDE, None, "1.5 remuneraciones por año (validar)"),
    ("INDEM_TOPE", "12", DESDE, None, "tope de 12 remuneraciones (validar)"),
    ("REGULARIDAD_MESES", "3", DESDE, None, "un variable es regular si aparece en 3 meses o más (validar)"),
    ("PROMEDIO_MESES", "6", DESDE, None, "ventana de meses para promediar variables (validar)"),
    ("GRATIFICACION_AUTOMATICA", "1", DESDE, None, "1 = julio y diciembre calculan la gratificación sola; 0 = solo por novedad"),
    ("RMA_PRIMA", "12209.11", date(2026, 1, 1), date(2026, 3, 31), "SBS, 1.er trimestre 2026 (validar)"),
    ("RMA_PRIMA", "12598.91", date(2026, 4, 1), date(2026, 6, 30), "SBS, 2.º trimestre 2026 (validar)"),
    ("RMA_PRIMA", "12672.65", date(2026, 7, 1), date(2026, 9, 30), "SBS, 3.er trimestre 2026 (validar)"),
    ("RMA_PRIMA", "12732.70", date(2026, 10, 1), date(2026, 12, 31), "SBS, 4.º trimestre 2026; fuente secundaria (validar)"),
]

# codigo, descripcion, categoria, naturaleza, calculado, afp_onp, 5ta_reg, 5ta_dir, essalud, codigo_plame
CONCEPTOS_LIQUIDACION = [
    ("@CTS_TRUNCA", "CTS trunca", "INGRESO", "FORMULA", True, 0, 0, 0, 0, None),
    ("@VACACIONES_VENCIDAS", "Vacaciones vencidas no gozadas", "INGRESO", "FORMULA", True, 1, 1, 0, 1, "0118"),
    ("@VACACIONES_TRUNCAS", "Vacaciones truncas", "INGRESO", "FORMULA", True, 1, 1, 0, 1, "0118"),
    ("@INDEMNIZACION_DESPIDO", "Indemnización por despido arbitrario", "INGRESO", "FORMULA", True, 0, 0, 0, 0, None),
]


def completar_rg728(db: Session) -> None:
    """Agrega, sin duplicar, los parámetros y conceptos de la liquidación al Diccionario RG_728 v1 (idempotente)."""
    dic = db.query(Diccionario).filter_by(codigo="RG_728", version=1).first()
    if not dic:
        return
    existentes = {(p.clave, p.vigente_desde) for p in
                  db.query(DiccionarioParametro).filter(DiccionarioParametro.diccionario_id == dic.id)}
    for clave, valor, desde, hasta, nota in PARAMETROS_LIQUIDACION:
        if (clave, desde) not in existentes:
            db.add(DiccionarioParametro(diccionario_id=dic.id, clave=clave, valor=Decimal(valor),
                                        vigente_desde=desde, vigente_hasta=hasta, nota=nota))
    codigos = {c.codigo for c in db.query(Concepto).filter(Concepto.diccionario_id == dic.id)}
    for cod, desc, cat, nat, calc, afp, r5, d5, ess, plame in CONCEPTOS_LIQUIDACION:
        if cod not in codigos:
            db.add(Concepto(diccionario_id=dic.id, codigo=cod, descripcion=desc, categoria=cat, naturaleza=nat,
                            calculado=calc, afecta_afp_onp=bool(afp), afecta_5ta_regular=bool(r5),
                            afecta_5ta_directa=bool(d5), afecta_essalud=bool(ess), codigo_plame=plame))
    db.commit()
