"""Liquidación por cese (etapa 2).  Fórmulas y reglas: documento de propuesta aprobado («OK, desarrollar»).
Lo que la propuesta dejó abierto se resolvió con la opción más conservadora y está marcado POR CONFIRMAR en el
documento de control. Aquí no hay constantes legales: porcentajes, divisores, topes y UIT salen del Diccionario."""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import reglas as R
from .calculo import D, sueldo_a_fecha
from .diccionario import CLAVES_LIQUIDACION, ErrorDeNegocio, cargar_parametros
from .models import (Auditoria, CtsDeposito, Detalle, Empleado, Liquidacion, LiquidacionDetalle, Periodo, RentaPrevia5ta,
                     Resultado, Vacacion)

CERO = Decimal("0")
MOTIVO_DESPIDO = "02"


# ------------------------------------------------------------------ bloqueos
def liquidacion_cerrada(db: Session, empleado_id: int) -> Optional[Liquidacion]:
    """Liquidación CERRADA del ciclo laboral vigente (las de ciclos anteriores no bloquean tras un reingreso)."""
    emp = db.get(Empleado, empleado_id)
    q = db.query(Liquidacion).filter(Liquidacion.empleado_id == empleado_id, Liquidacion.estado == "CERRADA")
    if emp is not None and emp.fecha_ingreso is not None:
        q = q.filter(Liquidacion.fecha_cese >= emp.fecha_ingreso)
    return q.first()


def exigir_sin_liquidacion_cerrada(db: Session, empleado_id: int, que: str = "modificar sus datos"):
    if liquidacion_cerrada(db, empleado_id):
        raise ErrorDeNegocio(f"La liquidación del colaborador está CERRADA: reábrala (con motivo) para {que}.", 403)


def bloqueados_del_periodo(db: Session, periodo_id: int) -> set:
    return {l.empleado_id for l in db.query(Liquidacion).filter(Liquidacion.periodo_id == periodo_id,
                                                                  Liquidacion.estado == "CERRADA")}


# ------------------------------------------------------------------ lista de cesados del periodo
def _estado_visible(liq: Optional[Liquidacion]) -> str:
    return liq.estado if liq else "SIN_INICIAR"


def _tiempo_texto(a: int, m: int, d: int) -> str:
    return (f"{a} {'año' if a == 1 else 'años'}, {m} {'mes' if m == 1 else 'meses'}, {d} {'día' if d == 1 else 'días'}")


def _texto_tiempo(emp: Empleado) -> str:
    y, m, d = R.tiempo_servicio(emp.fecha_ingreso, emp.fecha_cese)
    return f"{y} a, {m} m, {d} d"


def listar(db: Session, periodo: Periodo, estado: Optional[str] = None) -> List[dict]:
    """Cesados del periodo + pendientes de meses anteriores (cesaron antes y su liquidación no está cerrada)."""
    base = db.query(Empleado).filter(Empleado.empresa_id == periodo.empresa_id, Empleado.fecha_cese.isnot(None))
    filas: List[dict] = []
    for emp in base.order_by(Empleado.fecha_cese.desc(), Empleado.apellidos):
        en_mes = periodo.fecha_inicio <= emp.fecha_cese <= periodo.fecha_fin
        liq = (db.query(Liquidacion).filter(Liquidacion.empleado_id == emp.id)
               .order_by(Liquidacion.id.desc()).first())
        if not en_mes:
            if emp.fecha_cese > periodo.fecha_fin or (liq and liq.estado == "CERRADA"):
                continue
        else:
            liq = db.query(Liquidacion).filter(Liquidacion.empleado_id == emp.id, Liquidacion.periodo_id == periodo.id).first()
        est = _estado_visible(liq)
        if estado and estado != "TODAS" and est != estado:
            continue
        filas.append({
            "empleado_id": emp.id, "documento": emp.documento_identidad, "colaborador": f"{emp.apellidos}, {emp.nombres}",
            "fecha_cese": emp.fecha_cese, "motivo_cese": emp.motivo_cese, "clase_despido": emp.clase_despido,
            "tiempo_servicio": _texto_tiempo(emp), "estado": est, "liquidacion_id": liq.id if liq else None,
            "neto_a_pagar": liq.neto_a_pagar if liq else None, "origen": "MES" if en_mes else "PENDIENTE_ANTERIOR",
            "plazo_pago": emp.fecha_cese + timedelta(days=2),
        })
    return filas


# ------------------------------------------------------------------ cálculo
def _ultima_gratificacion(db: Session, emp: Empleado, empresa_id: int, hasta: date) -> Decimal:
    """Monto de la última gratificación (@GRATIFICACION) percibida hasta el mes indicado."""
    tope = hasta.year * 12 + hasta.month
    q = (db.query(Detalle, Periodo).join(Periodo, Periodo.id == Detalle.periodo_id)
         .filter(Detalle.empleado_id == emp.id, Periodo.empresa_id == empresa_id,
                 Detalle.concepto_codigo == "@GRATIFICACION").all())
    por_mes: Dict[int, Decimal] = defaultdict(lambda: CERO)
    for d, p in q:
        i = p.anio * 12 + p.mes
        if i <= tope:
            por_mes[i] += D(d.monto)
    return por_mes[max(por_mes)] if por_mes else CERO


def _promedio_variables(db: Session, emp: Empleado, empresa_id: int, idx: int, pr) -> Decimal:
    """Suma de los variables remunerativos regulares (aparecen en N meses o más de los últimos M).
    `idx` = año × 12 + mes del primer mes que NO entra en la ventana (ej.: el mes del cese)."""
    ventana, regularidad = int(pr["PROMEDIO_MESES"]), int(pr["REGULARIDAD_MESES"])
    por_concepto: Dict[str, Dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: CERO))
    q = (db.query(Detalle, Periodo).join(Periodo, Periodo.id == Detalle.periodo_id)
         .filter(Detalle.empleado_id == emp.id, Periodo.empresa_id == empresa_id, Detalle.categoria == "INGRESO"))
    for d, p in q:
        c = pr.conceptos.get(d.concepto_codigo)
        i = p.anio * 12 + p.mes
        if c and c.naturaleza == "VARIABLE" and c.afecta_essalud and idx - ventana <= i < idx:
            por_concepto[d.concepto_codigo][i] += D(d.monto)
    total = CERO
    for meses in por_concepto.values():
        total += R.promedio_variable_regular([meses.get(idx - k, CERO) for k in range(1, ventana + 1)], regularidad, ventana)
    return total


def _acumulados_5ta(db: Session, emp: Empleado, periodo: Periodo):
    base = ret = CERO
    q = (db.query(Resultado, Periodo).join(Periodo, Periodo.id == Resultado.periodo_id)
         .filter(Resultado.empleado_id == emp.id, Periodo.empresa_id == periodo.empresa_id,
                 Periodo.anio == periodo.anio, Periodo.mes <= periodo.mes))
    for r, _ in q:
        base += D(r.base_5ta_regular)
        ret += D(r.ret_5ta_regular)
    ing = rets = CERO
    for rp in db.query(RentaPrevia5ta).filter(RentaPrevia5ta.empleado_id == emp.id, RentaPrevia5ta.anio == periodo.anio,
                                              RentaPrevia5ta.mes <= periodo.mes):
        ing += D(rp.ingreso_afecto)
        rets += D(rp.retencion)
    return base + ing, ret, rets


def _dias_vacaciones_usados(db: Session, emp: Empleado) -> Decimal:
    usados = CERO
    for v in db.query(Vacacion).filter(Vacacion.empleado_id == emp.id, Vacacion.estado == "REGISTRADO",
                                       Vacacion.fecha_inicio >= emp.fecha_ingreso):
        usados += (v.fecha_fin - v.fecha_inicio).days + 1 + (v.dias_vendidos or 0)
    return usados


def _calcular(db: Session, liq: Liquidacion, emp: Empleado, periodo: Periodo) -> None:
    pr = cargar_parametros(db, periodo.diccionario_id, periodo.fecha_inicio, extra=CLAVES_LIQUIDACION)
    cese, ing = emp.fecha_cese, emp.fecha_ingreso
    avisos: List[str] = []

    res = db.query(Resultado).filter(Resultado.periodo_id == periodo.id, Resultado.empleado_id == emp.id).first()
    if res is None:
        raise ErrorDeNegocio("Calcule primero la boleta del mes del cese (Recalcular Todo): la liquidación se apoya en ella.", 409)

    sueldo = sueldo_a_fecha(db, emp.id, cese)
    if sueldo is None:
        raise ErrorDeNegocio("El colaborador no tiene sueldo vigente a la fecha de cese.", 422)
    af = R.q2(pr["RMV"] * pr["ASIG_FAM_PCT"]) if emp.asignacion_familiar else CERO
    variables = _promedio_variables(db, emp, periodo.empresa_id, periodo.anio * 12 + periodo.mes, pr)
    rc = sueldo + af + variables                                   # remuneración computable (sin 1/6 de gratificación)
    filas: List[dict] = []

    def add(codigo, monto, formula, cat=None):
        c = pr.conceptos[codigo]
        filas.append({"codigo": codigo, "categoria": cat or c.categoria, "monto": R.q2(monto), "formula": formula})

    # ---- gratificación trunca y su bonificación
    g_ini, _ = R.semestre_gratificacion(cese)
    meses_g = R.meses_calendario_completos(max(g_ini, ing), cese)
    # La gratificación del semestre julio-diciembre se paga en diciembre: solo puede estar ya pagada si el cese es en diciembre.
    ya_pagada = None
    if g_ini.month == 7 and cese.month == 12:
        ya_pagada = (db.query(Detalle).join(Periodo, Periodo.id == Detalle.periodo_id)
                     .filter(Detalle.empleado_id == emp.id, Periodo.empresa_id == periodo.empresa_id,
                             Detalle.concepto_codigo == "@GRATIFICACION", Periodo.anio == cese.year,
                             Periodo.mes == 12).first())
    if ya_pagada:
        avisos.append("La gratificación del semestre ya figura pagada en planilla: no se calcula la trunca.")
    elif meses_g > 0:
        grat = R.gratificacion_trunca(rc, meses_g, pr["GRATI_DIVISOR"])
        add("@GRATIFICACION", grat, f"{rc} / {pr['GRATI_DIVISOR']:.0f} × {meses_g} meses completos del semestre")
        pct = pr["BONIF_EXTRA_EPS_PCT"] if emp.cobertura_eps else pr["BONIF_EXTRA_PCT"]
        add("@BONIF_EXTRA_29351", R.bonificacion_extraordinaria(grat, pct), f"{grat} × {pct * 100:.2f} %")

    # ---- CTS del semestre en curso
    sexto = R.q2(_ultima_gratificacion(db, emp, periodo.empresa_id, cese) / 6)
    if sexto == 0:
        avisos.append("No hay gratificación registrada en planilla: la CTS se calculó sin el sexto de gratificación.")
    rc_cts = rc + sexto
    c_ini, _ = R.semestre_cts(cese)
    y, m, d = R.tiempo_servicio(max(c_ini, ing), cese)
    meses_c, dias_c = y * 12 + m, d
    if meses_c or dias_c:
        por_mes, por_dia = R.cts_monto(rc_cts, meses_c, dias_c, pr["CTS_DIVISOR_MES"], pr["CTS_DIVISOR_DIA"])
        add("@CTS_TRUNCA", por_mes + por_dia,
            f"({rc} + {sexto}) / {pr['CTS_DIVISOR_MES']:.0f} × {meses_c} meses + / {pr['CTS_DIVISOR_DIA']:.0f} × {dias_c} días")

    # ---- CTS del semestre anterior: solo si NO está marcada como pagada
    a_ini, a_fin = R.semestre_cts_anterior(cese)
    if ing <= a_fin:
        deposito = (db.query(CtsDeposito).filter(CtsDeposito.empleado_id == emp.id, CtsDeposito.sem_inicio == a_ini,
                                                 CtsDeposito.estado == "DEPOSITADA").first())
        if deposito and liq.cts_anterior_pagada is None:
            liq.cts_anterior_pagada = True
            avisos.append("La CTS del semestre anterior figura DEPOSITADA en la CTS semestral: se marcó como pagada.")
        if liq.cts_anterior_pagada is None:
            avisos.append("Responda si la CTS del semestre anterior ya fue pagada (obligatorio para cerrar).")
        elif liq.cts_anterior_pagada is False:
            sueldo_a = sueldo_a_fecha(db, emp.id, a_fin) or sueldo
            sexto_a = R.q2(_ultima_gratificacion(db, emp, periodo.empresa_id, a_fin) / 6)
            rc_a = sueldo_a + af + variables + sexto_a
            ya, ma, da = R.tiempo_servicio(max(a_ini, ing), a_fin)
            pm, pd = R.cts_monto(rc_a, ya * 12 + ma, da, pr["CTS_DIVISOR_MES"], pr["CTS_DIVISOR_DIA"])
            add("@CTS_TRUNCA", pm + pd, f"Semestre anterior no pagado ({a_ini:%d/%m/%Y} a {a_fin:%d/%m/%Y}): "
                                        f"{rc_a} / 12 × {ya * 12 + ma} meses + / 360 × {da} días")

    # ---- vacaciones vencidas y truncas desde el saldo del módulo de Vacaciones
    ay, am, ad = R.tiempo_servicio(ing, cese)
    ganados = R.dias_vacaciones_ganados(ay, am, ad, pr["VAC_DIAS_ANIO"])
    usados = _dias_vacaciones_usados(db, emp)
    venc, trunc = R.vacaciones_pendientes(ganados, usados, ay, pr["VAC_DIAS_ANIO"])
    if venc > 0:
        add("@VACACIONES_VENCIDAS", R.pago_vacaciones(rc, venc, pr["VAC_DIAS_ANIO"]),
            f"{rc} / {pr['VAC_DIAS_ANIO']:.0f} × {venc:.4f} días (de años completos sin gozar)")
    if trunc > 0:
        add("@VACACIONES_TRUNCAS", R.pago_vacaciones(rc, trunc, pr["VAC_DIAS_ANIO"]),
            f"{rc} / {pr['VAC_DIAS_ANIO']:.0f} × {trunc:.4f} días del año en curso")
    if venc > 0:
        avisos.append("Hay vacaciones vencidas: la posible indemnización vacacional NO se calcula (por validar).")

    # ---- indemnización por despido arbitrario
    if emp.motivo_cese == MOTIVO_DESPIDO:
        if emp.clase_despido is None:
            avisos.append("Motivo «02 Despido»: indique la clase de despido (justificado, arbitrario o nulo).")
        elif emp.clase_despido == "NULO":
            avisos.append("Despido nulo: corresponde reposición; no se calcula la indemnización.")
        elif emp.clase_despido == "ARBITRARIO":
            if emp.tipo_contrato == "PLAZO_FIJO":
                if not emp.fecha_fin_contrato or emp.fecha_fin_contrato <= cese:
                    raise ErrorDeNegocio("Contrato a plazo fijo: registre una fecha de vencimiento posterior al cese.", 422)
                yy, mm, dd = R.tiempo_servicio(cese + timedelta(days=1), emp.fecha_fin_contrato)
                total, partes = R.indemnizacion_plazo_fijo(rc, yy * 12 + mm, dd, pr["INDEM_FACTOR"], pr["INDEM_TOPE"])
                formula = f"{pr['INDEM_FACTOR']} × {rc} × ({yy * 12 + mm} meses + {dd}/30 días que faltan); tope {pr['INDEM_TOPE']:.0f} remuneraciones"
            else:
                total, partes = R.indemnizacion_indefinido(rc, ay, am, ad, pr["INDEM_FACTOR"], pr["INDEM_TOPE"])
                formula = (f"{pr['INDEM_FACTOR']} × {rc} × ({ay} años + {am}/12 meses + {ad}/360 días); "
                           f"tope {pr['INDEM_TOPE']:.0f} remuneraciones")
            add("@INDEMNIZACION_DESPIDO", total, formula)

    ingresos = sum((f["monto"] for f in filas if f["categoria"] == "INGRESO"), CERO)
    afecta = lambda f, flag: f["categoria"] == "INGRESO" and getattr(pr.conceptos[f["codigo"]], flag)
    base_afp = sum((f["monto"] for f in filas if afecta(f, "afecta_afp_onp")), CERO)
    base_ess = sum((f["monto"] for f in filas if afecta(f, "afecta_essalud")), CERO)
    base_5ta = sum((f["monto"] for f in filas if afecta(f, "afecta_5ta_regular")), CERO)

    # ---- aportes de pensión sobre lo afecto, con el tope de la prima aplicado una sola vez sobre la base del mes
    if base_afp > 0:
        if emp.sistema_pensionario == "AFP":
            afp = pr.afps.get((emp.afp_nombre or "").upper())
            if afp is None:
                raise ErrorDeNegocio(f"AFP '{emp.afp_nombre}' no existe en el Diccionario.", 422)
            add("@FONDO_PENSIONES", base_afp * afp.aporte, f"{base_afp} × {afp.aporte}")
            add("@COMISION_VARIABLE", base_afp * afp.comision, f"{base_afp} × {afp.comision}")
            prima = R.prima_diferencial(res.base_previsional, base_afp, afp.prima, pr["RMA_PRIMA"])
            add("@SEGURO_INVALIDEZ", prima, f"prima sobre base del mes {D(res.base_previsional) + base_afp} con tope {pr['RMA_PRIMA']:.2f} "
                                            f"menos la ya cobrada en la boleta")
        else:
            add("@SNP_ONP", base_afp * pr["ONP_TASA"], f"{base_afp} × {pr['ONP_TASA']}")

    # ---- quinta categoría: ajuste final sin proyectar, con la boleta del mes en curso ya incluida
    acumulado, ret_emp, ret_otros = _acumulados_5ta(db, emp, periodo)
    # rentas previas: _acumulados_5ta devolvió juntos los ingresos; las retenciones de otros empleadores van aparte
    bruta, neta, impuesto, ajuste = R.ajuste_5ta_cese(
        base_acumulada=acumulado, base_liquidacion=base_5ta, retenido_empresa=ret_emp, retenido_otros=ret_otros,
        uit=pr["UIT"], deduccion_uit=pr["DEDUCCION_5TA_UIT"], deduccion_adicional_uit=pr["DEDUCCION_ADICIONAL_5TA_UIT"],
        escala=pr.escala)
    if ajuste != 0:
        add("@IMP_RENTA_5TA", ajuste, f"Impuesto anual {impuesto} sobre renta {bruta} − retenido {ret_emp + ret_otros}"
                                      + (" (devolución del exceso)" if ajuste < 0 else ""))

    descuentos = sum((f["monto"] for f in filas if f["categoria"] == "DESCUENTO"), CERO)

    # ---- aportes del empleador (informativo): diferencial con mínimo aplicado una sola vez
    rmv_min = R.rmv_minima_essalud(pr["RMV"], res.dias_subsidio)
    dif_ess, dif_eps = R.essalud_diferencial(res.base_essalud, base_ess, rmv_min, pr["ESSALUD_TASA"],
                                             pr["CREDITO_EPS_TASA"], emp.cobertura_eps)

    # ---- guardar
    db.query(LiquidacionDetalle).filter(LiquidacionDetalle.liquidacion_id == liq.id).delete()
    for i, f in enumerate(filas):
        db.add(LiquidacionDetalle(liquidacion_id=liq.id, orden=i, concepto_codigo=f["codigo"], categoria=f["categoria"],
                                  monto=f["monto"], formula=f["formula"]))
    liq.fecha_cese, liq.motivo_cese, liq.clase_despido = cese, emp.motivo_cese, emp.clase_despido
    liq.tipo_contrato, liq.fecha_fin_contrato = emp.tipo_contrato, emp.fecha_fin_contrato
    liq.total_ingresos, liq.total_descuentos, liq.neto_a_pagar = ingresos, descuentos, ingresos - descuentos
    liq.base_previsional, liq.base_essalud, liq.base_5ta, liq.ret_5ta_ajuste = base_afp, base_ess, base_5ta, ajuste
    liq.aporte_essalud, liq.aporte_eps = dif_ess, dif_eps
    liq.avisos, liq.calculado_en = avisos, datetime.now(timezone.utc)
    liq.snapshot = {
        "colaborador": f"{emp.apellidos}, {emp.nombres}", "documento": emp.documento_identidad, "puesto": emp.puesto,
        "fecha_ingreso": str(ing), "fecha_cese": str(cese), "tiempo_servicio": _tiempo_texto(ay, am, ad),
        "sistema_pensionario": emp.sistema_pensionario, "afp": emp.afp_nombre, "sueldo": str(sueldo),
        "asignacion_familiar": str(af), "variables_promedio": str(variables), "remuneracion_computable": str(rc),
        "sexto_gratificacion": str(sexto), "vacaciones": {"ganados": f"{ganados:.4f}", "usados": str(usados),
                                                          "vencidas": f"{venc:.4f}", "truncas": f"{trunc:.4f}"},
        "quinta": {"renta_bruta": str(bruta), "renta_neta": str(neta), "impuesto_anual": str(impuesto),
                   "retenido": str(ret_emp + ret_otros), "ajuste": str(ajuste)},
        "diccionario_parametros": {k: str(v) for k, v in pr.p.items()},
    }


def _fila_cese(db: Session, emp: Empleado, periodo: Periodo):
    if emp.fecha_cese is None:
        raise ErrorDeNegocio("El colaborador no tiene fecha de cese: dé primero de baja al colaborador.", 422)
    if not (periodo.fecha_inicio <= emp.fecha_cese <= periodo.fecha_fin):
        raise ErrorDeNegocio("El cese no cae dentro del periodo elegido.", 422)


def procesar(db: Session, periodo: Periodo, emp: Empleado, cts_anterior_pagada: Optional[bool] = None) -> Liquidacion:
    """Crea (o recalcula, si está ABIERTA) la liquidación del colaborador en el periodo del mes del cese."""
    _fila_cese(db, emp, periodo)
    if periodo.estado == "CERRADO":
        raise ErrorDeNegocio(
            "El periodo del cese está CERRADO. Reábralo con motivo para incorporar la liquidación (la alternativa de "
            "pasarla al periodo siguiente está pendiente de la decisión del contador).", 409)
    liq = db.query(Liquidacion).filter(Liquidacion.periodo_id == periodo.id, Liquidacion.empleado_id == emp.id).first()
    if liq and liq.estado == "CERRADA":
        raise ErrorDeNegocio("La liquidación está CERRADA: reábrala con motivo para recalcularla.", 409)
    if liq is None:
        liq = Liquidacion(periodo_id=periodo.id, empleado_id=emp.id, estado="ABIERTA", fecha_cese=emp.fecha_cese)
        db.add(liq)
        db.flush()
    if cts_anterior_pagada is not None:
        liq.cts_anterior_pagada = cts_anterior_pagada
    _calcular(db, liq, emp, periodo)
    db.commit()
    return liq


def actualizar_marca_cts(db: Session, liq: Liquidacion, emp: Empleado, periodo: Periodo, pagada: bool) -> Liquidacion:
    if liq.estado == "CERRADA":
        raise ErrorDeNegocio("La liquidación está CERRADA: reábrala para cambiar la marca de CTS.", 409)
    liq.cts_anterior_pagada = pagada
    _calcular(db, liq, emp, periodo)
    db.commit()
    return liq


def refrescar_abiertas(db: Session, periodo: Periodo) -> List[str]:
    """Tras Recalcular Todo, las liquidaciones ABIERTAS se recalculan sobre la boleta nueva. Devuelve los problemas."""
    problemas = []
    for liq in db.query(Liquidacion).filter(Liquidacion.periodo_id == periodo.id, Liquidacion.estado == "ABIERTA").all():
        emp = db.get(Empleado, liq.empleado_id)
        try:
            _calcular(db, liq, emp, periodo)
            db.commit()
        except ErrorDeNegocio as e:
            db.rollback()
            problemas.append(f"{emp.apellidos}, {emp.nombres}: {e.mensaje}")
    return problemas


def faltantes_para_cerrar(liq: Liquidacion, emp: Empleado, pr_ing_ant_fin: bool) -> List[str]:
    f = []
    if emp.fecha_cese is None or not emp.motivo_cese:
        f.append("Falta la fecha o el motivo de cese.")
    if emp.motivo_cese == MOTIVO_DESPIDO and emp.clase_despido is None:
        f.append("Indique la clase de despido.")
    if pr_ing_ant_fin and liq.cts_anterior_pagada is None:
        f.append("Responda si la CTS del semestre anterior ya fue pagada.")
    return f


def cerrar(db: Session, liq: Liquidacion, periodo: Periodo, emp: Empleado, cuenta_id: int) -> Liquidacion:
    if liq.estado == "CERRADA":
        raise ErrorDeNegocio("La liquidación ya está CERRADA.", 409)
    if periodo.estado == "CERRADO":
        raise ErrorDeNegocio("El periodo del cese está CERRADO: reábralo con motivo antes de cerrar la liquidación.", 409)
    _, a_fin = R.semestre_cts_anterior(emp.fecha_cese)
    pendientes = faltantes_para_cerrar(liq, emp, emp.fecha_ingreso <= a_fin)
    if pendientes:
        raise ErrorDeNegocio("No se puede cerrar: " + " ".join(pendientes), 409)
    _calcular(db, liq, emp, periodo)                       # cierra siempre sobre una boleta vigente
    if liq.total_ingresos - liq.total_descuentos != liq.neto_a_pagar:
        raise ErrorDeNegocio("Los totales no cuadran.", 500)
    liq.estado, liq.cerrado_en = "CERRADA", datetime.now(timezone.utc)
    db.add(Auditoria(cuenta_id=cuenta_id, empresa_id=periodo.empresa_id, accion="CERRAR_LIQUIDACION",
                     detalle=f"empleado {emp.id} periodo {periodo.mes:02d}/{periodo.anio}"))
    db.commit()
    return liq


def reabrir(db: Session, liq: Liquidacion, periodo: Periodo, cuenta_id: int, motivo: str) -> Liquidacion:
    if liq.estado != "CERRADA":
        raise ErrorDeNegocio("Solo se puede reabrir una liquidación CERRADA.", 409)
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT set_config('app.reapertura', 'on', true)"))
    liq.estado, liq.cerrado_en = "ABIERTA", None
    db.add(Auditoria(cuenta_id=cuenta_id, empresa_id=periodo.empresa_id, accion="REABRIR_LIQUIDACION",
                     detalle=f"empleado {liq.empleado_id}: {motivo}"))
    db.commit()
    return liq


def descartar_abierta(db: Session, liq: Liquidacion):
    if liq.estado != "ABIERTA":
        raise ErrorDeNegocio("Solo se puede descartar una liquidación ABIERTA.", 409)
    db.query(LiquidacionDetalle).filter(LiquidacionDetalle.liquidacion_id == liq.id).delete()
    db.delete(liq)
    db.commit()


# ------------------------------------------------------------------ consolidado con la boleta del mes (AFPnet y PLAME)
def consolidado(db: Session, periodo: Periodo):
    """Devuelve ({empleado_id: {'base_previsional': D, 'lineas': [(codigo, categoria, monto)]}}, [abiertas])
    solo con liquidaciones CERRADAS. Las ABIERTAS se informan aparte para avisar en la exportación."""
    cerradas: Dict[int, dict] = {}
    for liq in db.query(Liquidacion).filter(Liquidacion.periodo_id == periodo.id, Liquidacion.estado == "CERRADA"):
        lineas = [(d.concepto_codigo, d.categoria, D(d.monto))
                  for d in db.query(LiquidacionDetalle).filter(LiquidacionDetalle.liquidacion_id == liq.id)]
        cerradas[liq.empleado_id] = {"base_previsional": D(liq.base_previsional), "lineas": lineas}
    abiertas = []
    for liq in db.query(Liquidacion).filter(Liquidacion.periodo_id == periodo.id, Liquidacion.estado == "ABIERTA"):
        e = db.get(Empleado, liq.empleado_id)
        abiertas.append(f"{e.apellidos}, {e.nombres}")
    return cerradas, abiertas
