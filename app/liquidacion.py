"""Liquidación por cese.

REGLA DE LA ETAPA 4 (indicación del dueño): la liquidación NO se guarda como borrador. Se calcula al pedir «Ver», «PDF» o
«Excel» y no deja nada en la base. Solo cuando se CIERRA se guarda, y solo entonces viaja al PLAME, al AFPnet y a los
resúmenes. Reabrirla borra el cierre (con motivo en la bitácora) y vuelve a ser un cálculo sin guardar.

Fórmulas y reglas: propuesta aprobada. Aquí no hay constantes legales: porcentajes, divisores, topes y UIT salen del Diccionario."""
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from . import reglas as R
from .calculo import D, sueldo_a_fecha
from .diccionario import CLAVES_LIQUIDACION, ErrorDeNegocio, cargar_parametros
from .models import (Auditoria, CtsDeposito, Detalle, Empleado, Liquidacion, LiquidacionDetalle, Periodo,
                     RentaPrevia5ta, Resultado, Vacacion)

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
def _tiempo_texto(a: int, m: int, d: int) -> str:
    return f"{a} {'año' if a == 1 else 'años'}, {m} {'mes' if m == 1 else 'meses'}, {d} {'día' if d == 1 else 'días'}"


def listar(db: Session, periodo: Periodo, estado: Optional[str] = None) -> List[dict]:
    """Cesados del periodo + pendientes de meses anteriores. Estados visibles: SIN_CERRAR y CERRADA."""
    filas: List[dict] = []
    base = db.query(Empleado).filter(Empleado.empresa_id == periodo.empresa_id, Empleado.fecha_cese.isnot(None))
    for emp in base.order_by(Empleado.fecha_cese.desc(), Empleado.apellidos):
        en_mes = periodo.fecha_inicio <= emp.fecha_cese <= periodo.fecha_fin
        if emp.fecha_cese > periodo.fecha_fin:
            continue
        liq = (db.query(Liquidacion).filter(Liquidacion.empleado_id == emp.id, Liquidacion.estado == "CERRADA",
                                            Liquidacion.fecha_cese == emp.fecha_cese).first())
        if not en_mes and liq:
            continue
        est = "CERRADA" if liq else "SIN_CERRAR"
        if estado and estado != "TODAS" and est != estado:
            continue
        y, m, d = R.tiempo_servicio(emp.fecha_ingreso, emp.fecha_cese)
        filas.append({
            "empleado_id": emp.id, "documento": emp.documento_identidad, "colaborador": f"{emp.apellidos}, {emp.nombres}",
            "fecha_cese": emp.fecha_cese, "motivo_cese": emp.motivo_cese, "clase_despido": emp.clase_despido,
            "tiempo_servicio": _tiempo_texto(y, m, d), "estado": est, "liquidacion_id": liq.id if liq else None,
            "neto_a_pagar": liq.neto_a_pagar if liq else None, "origen": "MES" if en_mes else "PENDIENTE_ANTERIOR",
            "periodo_id_cese": liq.periodo_id if liq else None, "plazo_pago": emp.fecha_cese + timedelta(days=2)})
    return filas


def periodo_del_cese(db: Session, empresa_id: int, fecha_cese: date) -> Optional[Periodo]:
    return (db.query(Periodo).filter(Periodo.empresa_id == empresa_id, Periodo.anio == fecha_cese.year,
                                     Periodo.mes == fecha_cese.month).order_by(Periodo.id).first())


# ------------------------------------------------------------------ insumos del cálculo
def _ultima_gratificacion(db: Session, emp: Empleado, empresa_id: int, hasta: date) -> Decimal:
    """Monto de la última gratificación (@GRATIFICACION) percibida hasta el mes indicado."""
    tope = hasta.year * 12 + hasta.month
    q = (db.query(Detalle, Periodo).join(Periodo, Periodo.id == Detalle.periodo_id)
         .filter(Detalle.empleado_id == emp.id, Periodo.empresa_id == empresa_id, Detalle.concepto_codigo == "@GRATIFICACION").all())
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


# ------------------------------------------------------------------ cálculo (no guarda nada)
@dataclass
class CalculoLiq:
    filas: List[dict] = field(default_factory=list)
    total_ingresos: Decimal = CERO
    total_descuentos: Decimal = CERO
    neto: Decimal = CERO
    base_previsional: Decimal = CERO
    base_essalud: Decimal = CERO
    base_5ta: Decimal = CERO
    ret_5ta_ajuste: Decimal = CERO
    aporte_essalud: Decimal = CERO
    aporte_eps: Decimal = CERO
    avisos: List[str] = field(default_factory=list)
    snapshot: dict = field(default_factory=dict)
    cts_anterior_aplica: bool = False
    cts_anterior_pagada: Optional[bool] = None
    faltantes: List[str] = field(default_factory=list)
    simulacion: bool = False


def _f(x) -> str:
    return str(x)


MODOS_VAC = ("FORMULA", "CERO", "DIAS", "MONTO")


def calcular(db: Session, periodo: Periodo, emp: Empleado, cts_anterior_pagada: Optional[bool] = None,
             vac_modo: str = "FORMULA", vac_valor: Optional[Decimal] = None, excluir=None) -> CalculoLiq:
    """Calcula la liquidación del colaborador con los datos de HOY. No escribe en la base.
    Opciones (en tiempo real, no se guardan salvo las de vacaciones y la CTS al cerrar):
      vac_modo   FORMULA (saldo del módulo Vacaciones) | CERO (no pagar pendientes) | DIAS (días escritos) | MONTO (monto escrito en S/)
      excluir    claves de conceptos deshabilitados para SIMULAR (no se puede cerrar así)."""
    excluir = set(excluir or [])
    vac_modo = (vac_modo or "FORMULA").upper()
    if vac_modo not in MODOS_VAC:
        raise ErrorDeNegocio("Modo de vacaciones pendientes inválido (FORMULA, CERO, DIAS o MONTO).", 422)
    if vac_modo in ("DIAS", "MONTO"):
        if vac_valor is None:
            raise ErrorDeNegocio("Escriba el valor de las vacaciones pendientes (días o monto).", 422)
        if Decimal(str(vac_valor)) < 0:
            raise ErrorDeNegocio("El valor de las vacaciones pendientes no puede ser negativo.", 422)
    if emp.fecha_cese is None:
        raise ErrorDeNegocio("El colaborador no tiene fecha de cese: dé primero de baja al colaborador.", 422)
    if not (periodo.fecha_inicio <= emp.fecha_cese <= periodo.fecha_fin):
        raise ErrorDeNegocio("El cese no cae dentro del periodo elegido.", 422)
    pr = cargar_parametros(db, periodo.diccionario_id, periodo.fecha_inicio, extra=CLAVES_LIQUIDACION, periodo_id=periodo.id)
    cese, ing = emp.fecha_cese, emp.fecha_ingreso
    out = CalculoLiq()
    avisos = out.avisos

    res = db.query(Resultado).filter(Resultado.periodo_id == periodo.id, Resultado.empleado_id == emp.id).first()
    if res is None:
        raise ErrorDeNegocio("Calcule primero la boleta del mes del cese (Recalcular Todo): la liquidación se apoya en ella.", 409)
    sueldo = sueldo_a_fecha(db, emp.id, cese)
    if sueldo is None:
        raise ErrorDeNegocio("El colaborador no tiene sueldo vigente a la fecha de cese.", 422)
    af = R.q2(pr["RMV"] * pr["ASIG_FAM_PCT"]) if emp.asignacion_familiar else CERO
    variables = _promedio_variables(db, emp, periodo.empresa_id, periodo.anio * 12 + periodo.mes, pr)
    rc = sueldo + af + variables                                   # remuneración computable (sin 1/6 de gratificación)
    filas = out.filas

    contador: Dict[str, int] = defaultdict(int)

    def add(codigo, monto, formula, cat=None):
        c = pr.conceptos[codigo]
        m = R.q2(monto)
        if m != 0:                                   # no se guardan renglones en cero (p. ej. comisión mixta mensual 0 %)
            contador[codigo] += 1
            clave = codigo if contador[codigo] == 1 else f"{codigo}:{contador[codigo]}"
            filas.append({"codigo": codigo, "clave": clave, "categoria": cat or c.categoria, "monto": m, "formula": formula,
                          "habilitado": clave not in excluir})

    # ---- gratificación trunca y su bonificación
    g_ini, _ = R.semestre_gratificacion(cese)
    meses_g = R.meses_calendario_completos(max(g_ini, ing), cese)
    ya_pagada = None
    if g_ini.month == 7 and cese.month == 12:   # la de julio-diciembre se paga en diciembre
        ya_pagada = (db.query(Detalle).join(Periodo, Periodo.id == Detalle.periodo_id)
                     .filter(Detalle.empleado_id == emp.id, Periodo.empresa_id == periodo.empresa_id,
                             Detalle.concepto_codigo == "@GRATIFICACION", Periodo.anio == cese.year, Periodo.mes == 12).first())
    grat_info = None
    if ya_pagada:
        avisos.append("La gratificación del semestre ya figura pagada en planilla: no se calcula la trunca.")
    elif meses_g > 0:
        grat = R.gratificacion_trunca(rc, meses_g, pr["GRATI_DIVISOR"])
        add("@GRATIFICACION", grat, f"{rc} / {pr['GRATI_DIVISOR']:.0f} × {meses_g} meses completos del semestre")
        pct = pr["BONIF_EXTRA_EPS_PCT"] if emp.cobertura_eps else pr["BONIF_EXTRA_PCT"]
        add("@BONIF_EXTRA_29351", R.bonificacion_extraordinaria(grat, pct), f"{grat} × {pct * 100:.2f} %")
        grat_info = {"meses": meses_g, "dias": 0}

    # ---- CTS del semestre en curso
    sexto = R.q2(_ultima_gratificacion(db, emp, periodo.empresa_id, cese) / 6)
    if sexto == 0:
        avisos.append("No hay gratificación registrada en planilla: la CTS se calculó sin el sexto de gratificación.")
    rc_cts = rc + sexto
    c_ini, _ = R.semestre_cts(cese)
    c_desde = max(c_ini, ing)
    y, m, d = R.tiempo_servicio(c_desde, cese)
    meses_c, dias_c = y * 12 + m, d
    cts_info = None
    if meses_c or dias_c:
        por_mes, por_dia = R.cts_monto(rc_cts, meses_c, dias_c, pr["CTS_DIVISOR_MES"], pr["CTS_DIVISOR_DIA"])
        add("@CTS_TRUNCA", por_mes + por_dia,
            f"({rc} + {sexto}) / {pr['CTS_DIVISOR_MES']:.0f} × {meses_c} meses + / {pr['CTS_DIVISOR_DIA']:.0f} × {dias_c} días")
        cts_info = {"desde": _f(c_desde), "hasta": _f(cese), "meses": meses_c, "dias": dias_c,
                    "por_mes": _f((rc_cts / pr["CTS_DIVISOR_MES"]).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)),   # el formato de la empresa trunca a 4 decimales
                    "por_dia": _f((rc_cts / pr["CTS_DIVISOR_DIA"]).quantize(Decimal("0.0001"), rounding=ROUND_DOWN)),
                    "monto_meses": _f(por_mes), "monto_dias": _f(por_dia)}

    # ---- CTS del semestre anterior: solo si NO está pagada
    a_ini, a_fin = R.semestre_cts_anterior(cese)
    out.cts_anterior_aplica = ing <= a_fin
    out.cts_anterior_pagada = cts_anterior_pagada
    cts_ant_info = None
    if out.cts_anterior_aplica:
        deposito = (db.query(CtsDeposito).filter(CtsDeposito.empleado_id == emp.id, CtsDeposito.sem_inicio == a_ini,
                                                 CtsDeposito.estado == "DEPOSITADA").first())
        if deposito and out.cts_anterior_pagada is None:
            out.cts_anterior_pagada = True
            avisos.append("La CTS del semestre anterior figura DEPOSITADA en la CTS semestral: se da por pagada.")
        if out.cts_anterior_pagada is None:
            avisos.append("Responda si la CTS del semestre anterior ya fue pagada (obligatorio para cerrar).")
        elif out.cts_anterior_pagada is False:
            sueldo_a = sueldo_a_fecha(db, emp.id, a_fin) or sueldo
            sexto_a = R.q2(_ultima_gratificacion(db, emp, periodo.empresa_id, a_fin) / 6)
            rc_a = sueldo_a + af + variables + sexto_a
            ya, ma, da = R.tiempo_servicio(max(a_ini, ing), a_fin)
            pm, pd = R.cts_monto(rc_a, ya * 12 + ma, da, pr["CTS_DIVISOR_MES"], pr["CTS_DIVISOR_DIA"])
            add("@CTS_TRUNCA", pm + pd, f"Semestre anterior no pagado ({a_ini:%d/%m/%Y} a {a_fin:%d/%m/%Y}): "
                                        f"{rc_a} / 12 × {ya * 12 + ma} meses + / 360 × {da} días")
            cts_ant_info = {"desde": _f(max(a_ini, ing)), "hasta": _f(a_fin), "meses": ya * 12 + ma, "dias": da,
                            "rc": _f(rc_a), "monto": _f(pm + pd)}

    # ---- vacaciones vencidas y truncas desde el saldo del módulo de Vacaciones
    ay, am, ad = R.tiempo_servicio(ing, cese)
    ganados = R.dias_vacaciones_ganados(ay, am, ad, pr["VAC_DIAS_ANIO"])
    usados = _dias_vacaciones_usados(db, emp)
    venc, trunc = R.vacaciones_pendientes(ganados, usados, ay, pr["VAC_DIAS_ANIO"])
    anio_aniv = cese.year if (cese.month, cese.day) >= (ing.month, ing.day) else cese.year - 1
    per_trunca, per_venc = f"{anio_aniv}-{anio_aniv + 1}", f"{anio_aniv - 1}-{anio_aniv}"
    pago_f = R.pago_vacaciones(rc, venc, pr["VAC_DIAS_ANIO"]) if venc > 0 else CERO
    venc_f, venc_mostrado, monto_venc = venc, venc, pago_f
    formula_v = f"{rc} / {pr['VAC_DIAS_ANIO']:.0f} × {venc_f:.4f} días (de años completos sin gozar)"
    if vac_modo == "CERO":
        venc_mostrado, monto_venc = CERO, CERO
        if venc_f > 0:
            avisos.append(f"Vacaciones pendientes deshabilitadas: no se pagan (por fórmula serían {venc_f:.4f} días = S/ {pago_f}).")
    elif vac_modo == "DIAS":
        venc_mostrado = Decimal(str(vac_valor))
        monto_venc = R.pago_vacaciones(rc, venc_mostrado, pr["VAC_DIAS_ANIO"])
        formula_v = f"{rc} / {pr['VAC_DIAS_ANIO']:.0f} × {venc_mostrado:.4f} días ESCRITOS manualmente (por fórmula: {venc_f:.4f} días)"
        avisos.append(f"Vacaciones pendientes: días escritos manualmente ({venc_mostrado:.4f}); por fórmula serían {venc_f:.4f} días = S/ {pago_f}.")
    elif vac_modo == "MONTO":
        monto_venc = R.q2(Decimal(str(vac_valor)))
        venc_mostrado = (monto_venc * pr["VAC_DIAS_ANIO"] / rc) if rc > 0 else CERO
        formula_v = f"Monto ESCRITO manualmente S/ {monto_venc} (por fórmula: S/ {pago_f})"
        avisos.append(f"Vacaciones pendientes: monto escrito manualmente (S/ {monto_venc}); por fórmula serían S/ {pago_f}.")
    if venc_f > 0:
        avisos.append("Hay vacaciones vencidas: la posible indemnización vacacional NO se calcula (por validar).")
    if monto_venc > 0:
        add("@VACACIONES_VENCIDAS", monto_venc, formula_v)
    venc = venc_mostrado
    if trunc > 0:
        add("@VACACIONES_TRUNCAS", R.pago_vacaciones(rc, trunc, pr["VAC_DIAS_ANIO"]),
            f"{rc} / {pr['VAC_DIAS_ANIO']:.0f} × {trunc:.4f} días del año en curso")

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
                total, _ = R.indemnizacion_plazo_fijo(rc, yy * 12 + mm, dd, pr["INDEM_FACTOR"], pr["INDEM_TOPE"])
                formula = (f"{pr['INDEM_FACTOR']} × {rc} × ({yy * 12 + mm} meses + {dd}/30 días que faltan); "
                           f"tope {pr['INDEM_TOPE']:.0f} remuneraciones")
            else:
                total, _ = R.indemnizacion_indefinido(rc, ay, am, ad, pr["INDEM_FACTOR"], pr["INDEM_TOPE"])
                formula = (f"{pr['INDEM_FACTOR']} × {rc} × ({ay} años + {am}/12 meses + {ad}/360 días); "
                           f"tope {pr['INDEM_TOPE']:.0f} remuneraciones")
            add("@INDEMNIZACION_DESPIDO", total, formula)

    afecta = lambda f, flag: f["habilitado"] and f["categoria"] == "INGRESO" and getattr(pr.conceptos[f["codigo"]], flag)
    base_afp = sum((f["monto"] for f in filas if afecta(f, "afecta_afp_onp")), CERO)
    base_ess = sum((f["monto"] for f in filas if afecta(f, "afecta_essalud")), CERO)
    base_5ta = sum((f["monto"] for f in filas if afecta(f, "afecta_5ta_regular")), CERO)

    # ---- aportes de pensión sobre lo afecto, con el tope de la prima aplicado una sola vez sobre la base del mes
    pension = {"tipo": emp.sistema_pensionario, "base": _f(base_afp)}
    afp = pr.afps.get((emp.afp_nombre or "").upper()) if emp.sistema_pensionario == "AFP" else None
    if emp.sistema_pensionario == "AFP" and afp is None:
        raise ErrorDeNegocio(f"AFP '{emp.afp_nombre}' no existe en el Diccionario.", 422)
    if afp is not None:
        com = afp.comision_planilla(emp.tipo_comision)
        pension.update({"aporte_pct": _f(afp.aporte * 100), "comision_pct": _f(com * 100), "prima_pct": _f(afp.prima * 100),
                        "tipo_comision": emp.tipo_comision or "FLUJO", "comision_saldo_pct": _f(afp.comision_saldo * 100)})
        if not emp.cuspp:
            avisos.append("Falta el CUSPP del colaborador (AFPnet).")
    else:
        pension.update({"onp_pct": _f(pr["ONP_TASA"] * 100)})
    if base_afp > 0:
        if afp is not None:
            add("@FONDO_PENSIONES", base_afp * afp.aporte, f"{base_afp} × {afp.aporte}")
            add("@COMISION_VARIABLE", base_afp * com, f"{base_afp} × {com}" + (" (comisión mixta: componente mensual)" if emp.tipo_comision == "MIXTA" else ""))
            prima = R.prima_diferencial(res.base_previsional, base_afp, afp.prima, pr["RMA_PRIMA"])
            add("@SEGURO_INVALIDEZ", prima, f"prima sobre base del mes {D(res.base_previsional) + base_afp} con tope "
                                            f"{pr['RMA_PRIMA']:.2f} menos la ya cobrada en la boleta")
        else:
            add("@SNP_ONP", base_afp * pr["ONP_TASA"], f"{base_afp} × {pr['ONP_TASA']}")

    # ---- quinta categoría: ajuste final sin proyectar, con la boleta del mes en curso incluida
    acumulado, ret_emp, ret_otros = _acumulados_5ta(db, emp, periodo)
    bruta, neta, impuesto, ajuste = R.ajuste_5ta_cese(
        base_acumulada=acumulado, base_liquidacion=base_5ta, retenido_empresa=ret_emp, retenido_otros=ret_otros,
        uit=pr["UIT"], deduccion_uit=pr["DEDUCCION_5TA_UIT"], deduccion_adicional_uit=pr["DEDUCCION_ADICIONAL_5TA_UIT"],
        escala=pr.escala)
    formula5 = f"Impuesto anual {impuesto} sobre renta {bruta} − retenido {ret_emp + ret_otros}"
    if ajuste > 0:
        add("@IMP_RENTA_5TA", ajuste, formula5)
    elif ajuste < 0:
        add("@DEVOLUCION_5TA", -ajuste, formula5 + " (devolución del exceso)")      # como en el formato de la empresa: otro ingreso

    # ---- aportes del empleador (informativo): diferencial con el mínimo aplicado una sola vez
    rmv_min = R.rmv_minima_essalud(pr["RMV"], res.dias_subsidio)
    dif_ess, dif_eps = R.essalud_diferencial(res.base_essalud, base_ess, rmv_min, pr["ESSALUD_TASA"],
                                             pr["CREDITO_EPS_TASA"], emp.cobertura_eps)

    out.total_ingresos = sum((f["monto"] for f in filas if f["habilitado"] and f["categoria"] == "INGRESO"), CERO)
    out.total_descuentos = sum((f["monto"] for f in filas if f["habilitado"] and f["categoria"] == "DESCUENTO"), CERO)
    out.neto = out.total_ingresos - out.total_descuentos
    out.base_previsional, out.base_essalud, out.base_5ta, out.ret_5ta_ajuste = base_afp, base_ess, base_5ta, ajuste
    out.aporte_essalud, out.aporte_eps = dif_ess, dif_eps

    out.faltantes = faltantes_para_cerrar(emp, out.cts_anterior_aplica, out.cts_anterior_pagada)
    out.simulacion = bool(excluir)
    if excluir:
        out.faltantes.append("Hay conceptos deshabilitados (SIMULACIÓN para pruebas): vuelva a habilitarlos para poder cerrar la liquidación.")
        avisos.append("SIMULACIÓN: hay conceptos deshabilitados; los totales no corresponden a la liquidación real.")
        if "@CTS_TRUNCA" in excluir: cts_info = None
        if "@CTS_TRUNCA:2" in excluir: cts_ant_info = None
        if "@GRATIFICACION" in excluir: grat_info = None
    out.snapshot = {
        "colaborador": f"{emp.apellidos}, {emp.nombres}", "documento": emp.documento_identidad, "puesto": emp.puesto,
        "sede": emp.sede, "categoria": emp.categoria, "area": emp.area, "centro_costo": emp.centro_costo,
        "calificacion": emp.calificacion, "fecha_ingreso": _f(ing), "fecha_cese": _f(cese),
        "tiempo_servicio": _tiempo_texto(ay, am, ad), "sistema_pensionario": emp.sistema_pensionario, "afp": emp.afp_nombre,
        "cuspp": emp.cuspp, "cobertura_eps": bool(emp.cobertura_eps), "sueldo": _f(sueldo), "asignacion_familiar": _f(af),
        "variables_promedio": _f(variables), "remuneracion_computable": _f(rc), "sexto_gratificacion": _f(sexto),
        "rc_cts": _f(rc_cts), "cts": cts_info, "cts_anterior": cts_ant_info, "gratificacion": grat_info,
        "vacaciones": {"ganados": f"{ganados:.4f}", "usados": _f(usados), "vencidas": f"{venc:.4f}", "truncas": f"{trunc:.4f}",
                       "periodo_vencidas": per_venc, "periodo_truncas": per_trunca},
        "opciones": {"vacaciones_pendientes": {"modo": vac_modo, "valor": None if vac_valor is None else _f(vac_valor),
                                               "formula_dias": f"{venc_f:.4f}", "formula_monto": _f(pago_f)},
                     "cts_anterior_pagada": out.cts_anterior_pagada, "excluidos": sorted(excluir)},
        "simulacion": bool(excluir),
        "regla_quinta": "Cesado: la retención regular de 5ta del mes del cese no se aplica en la boleta; se aplica completa en esta liquidación.",
        "pension": pension,
        "essalud": {"base": _f(base_ess), "con_eps": bool(emp.cobertura_eps), "tasa_essalud_pct": _f(
            (pr["ESSALUD_TASA"] - pr["CREDITO_EPS_TASA"] if emp.cobertura_eps else pr["ESSALUD_TASA"]) * 100),
            "tasa_eps_pct": _f(pr["CREDITO_EPS_TASA"] * 100)},
        "quinta": {"renta_bruta": _f(bruta), "renta_neta": _f(neta), "impuesto_anual": _f(impuesto),
                   "retenido": _f(ret_emp + ret_otros), "ajuste": _f(ajuste)},
        "diccionario_parametros": {k: _f(v) for k, v in pr.p.items()},
    }
    return out


def faltantes_para_cerrar(emp: Empleado, cts_aplica: bool, cts_pagada: Optional[bool]) -> List[str]:
    f = []
    if emp.fecha_cese is None or not emp.motivo_cese:
        f.append("Falta la fecha o el motivo de cese.")
    if emp.motivo_cese == MOTIVO_DESPIDO and emp.clase_despido is None:
        f.append("Indique la clase de despido.")
    if cts_aplica and cts_pagada is None:
        f.append("Responda si la CTS del semestre anterior ya fue pagada.")
    return f


# ------------------------------------------------------------------ cierre definitivo (lo único que se guarda)
def cerrar(db: Session, periodo: Periodo, emp: Empleado, cuenta_id: int, cts_anterior_pagada: Optional[bool],
           vac_modo: str = "FORMULA", vac_valor: Optional[Decimal] = None) -> Liquidacion:
    if periodo.estado == "CERRADO":
        raise ErrorDeNegocio("El periodo del cese está CERRADO: reábralo con motivo antes de cerrar la liquidación "
                             "(la alternativa de pasarla al periodo siguiente está pendiente de la decisión del contador).", 409)
    ya = db.query(Liquidacion).filter(Liquidacion.empleado_id == emp.id, Liquidacion.estado == "CERRADA",
                                      Liquidacion.fecha_cese == emp.fecha_cese).first()
    if ya:
        raise ErrorDeNegocio("La liquidación ya está CERRADA.", 409)
    calc = calcular(db, periodo, emp, cts_anterior_pagada, vac_modo, vac_valor)
    if calc.faltantes:
        raise ErrorDeNegocio("No se puede cerrar: " + " ".join(calc.faltantes), 409)
    if calc.total_ingresos - calc.total_descuentos != calc.neto:
        raise ErrorDeNegocio("Los totales no cuadran.", 500)
    liq = Liquidacion(
        periodo_id=periodo.id, empleado_id=emp.id, estado="CERRADA", fecha_cese=emp.fecha_cese, motivo_cese=emp.motivo_cese,
        clase_despido=emp.clase_despido, tipo_contrato=emp.tipo_contrato, fecha_fin_contrato=emp.fecha_fin_contrato,
        cts_anterior_pagada=calc.cts_anterior_pagada, total_ingresos=calc.total_ingresos, total_descuentos=calc.total_descuentos,
        neto_a_pagar=calc.neto, base_previsional=calc.base_previsional, base_essalud=calc.base_essalud, base_5ta=calc.base_5ta,
        ret_5ta_ajuste=calc.ret_5ta_ajuste, aporte_essalud=calc.aporte_essalud, aporte_eps=calc.aporte_eps,
        avisos=calc.avisos, snapshot=calc.snapshot, calculado_en=datetime.now(timezone.utc), cerrado_en=datetime.now(timezone.utc))
    db.add(liq)
    db.flush()
    for i, f in enumerate([x for x in calc.filas if x["habilitado"]]):
        db.add(LiquidacionDetalle(liquidacion_id=liq.id, orden=i, concepto_codigo=f["codigo"], categoria=f["categoria"],
                                  monto=f["monto"], formula=f["formula"]))
    db.add(Auditoria(cuenta_id=cuenta_id, empresa_id=periodo.empresa_id, accion="CERRAR_LIQUIDACION",
                     detalle=f"empleado {emp.id} periodo {periodo.mes:02d}/{periodo.anio} neto {calc.neto}"))
    db.commit()
    return liq


def reabrir(db: Session, liq: Liquidacion, periodo: Periodo, cuenta_id: int, motivo: str) -> None:
    """Anula el cierre: la liquidación deja de existir como definitiva y vuelve a ser un cálculo sin guardar."""
    if liq.estado != "CERRADA":
        raise ErrorDeNegocio("Solo se puede reabrir una liquidación CERRADA.", 409)
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT set_config('app.reapertura', 'on', true)"))
    db.add(Auditoria(cuenta_id=cuenta_id, empresa_id=periodo.empresa_id, accion="REABRIR_LIQUIDACION",
                     detalle=f"empleado {liq.empleado_id} (neto cerrado {liq.neto_a_pagar}): {motivo}"))
    db.query(LiquidacionDetalle).filter(LiquidacionDetalle.liquidacion_id == liq.id).delete()
    db.delete(liq)
    db.commit()


# ------------------------------------------------------------------ lo que viaja al PLAME, al AFPnet y a los resúmenes: SOLO lo CERRADO
def consolidado(db: Session, periodo: Periodo):
    """({empleado_id: {'base_previsional': D, 'lineas': [(codigo, categoria, monto)]}}, [cesados del periodo sin liquidación cerrada])."""
    cerradas: Dict[int, dict] = {}
    for liq in db.query(Liquidacion).filter(Liquidacion.periodo_id == periodo.id, Liquidacion.estado == "CERRADA"):
        lineas = [(d.concepto_codigo, d.categoria, D(d.monto))
                  for d in db.query(LiquidacionDetalle).filter(LiquidacionDetalle.liquidacion_id == liq.id)]
        cerradas[liq.empleado_id] = {"base_previsional": D(liq.base_previsional), "lineas": lineas}
    sin_cerrar = []
    for emp in db.query(Empleado).filter(Empleado.empresa_id == periodo.empresa_id, Empleado.fecha_cese.isnot(None),
                                         Empleado.fecha_cese >= periodo.fecha_inicio, Empleado.fecha_cese <= periodo.fecha_fin):
        if emp.id not in cerradas:
            sin_cerrar.append(f"{emp.apellidos}, {emp.nombres}")
    return cerradas, sin_cerrar
