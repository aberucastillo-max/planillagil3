"""Reglas matemáticas PURAS (sin base de datos): fáciles de probar y de versionar.
Todo el dinero usa Decimal y se redondea a 2 decimales (mitad hacia arriba)."""
import calendar
from collections import Counter
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

CENT = Decimal("0.01")
CERO = Decimal("0")


def D(x) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def q2(x) -> Decimal:
    return D(x).quantize(CENT, rounding=ROUND_HALF_UP)


def fin_de_mes(anio: int, mes: int) -> date:
    return date(anio, mes, calendar.monthrange(anio, mes)[1])


# ------------------------------------------------------------------ días
def dias_calendario_solapados(ini_a: date, fin_a: date, ini_b: date, fin_b: date) -> int:
    ini, fin = max(ini_a, ini_b), min(fin_a, fin_b)
    return max((fin - ini).days + 1, 0)


def dias_vinculo(ingreso: date, cese: Optional[date], ini: date, fin: date, base: int = 30) -> int:
    """Base estándar de 30 días: ingreso el día 21 => 10 días. Cese el último día del mes => 30."""
    if ingreso > fin or (cese is not None and cese < ini):
        return 0
    d_ini, d_fin = 1, base
    if ingreso > ini:
        d_ini = min(ingreso.day, base)
    if cese is not None and cese < fin:
        d_fin = min(cese.day, base)
    return max(d_fin - d_ini + 1, 0)


# ------------------------------------------------------------------ horas extras
def valor_hora(sueldo, asignacion_familiar, base_dias: int = 30, horas_dia: int = 8) -> Decimal:
    """(Sueldo básico + Asignación Familiar) / 30 / 8."""
    return (D(sueldo) + D(asignacion_familiar)) / D(base_dias) / D(horas_dia)


def monto_horas_extras(horas, valor_h: Decimal, recargo) -> Decimal:
    return q2(D(horas) * valor_h * (D(1) + D(recargo)))


# ------------------------------------------------------------------ previsional y EsSalud
def calcular_afp(base, aporte, comision, prima, tope_prima=None) -> Tuple[Decimal, Decimal, Decimal]:
    base = D(base)
    base_prima = min(base, D(tope_prima)) if tope_prima else base
    return q2(base * D(aporte)), q2(base * D(comision)), q2(base_prima * D(prima))


def calcular_onp(base, tasa) -> Decimal:
    return q2(D(base) * D(tasa))


def calcular_essalud(base, rmv, tasa, tasa_credito_eps, tiene_eps: bool) -> Tuple[Decimal, Decimal]:
    """Devuelve (essalud, aporte_eps). Con EPS: EsSalud = tasa - crédito; el crédito va a @APORTE_EPS.
    La base mínima de EsSalud es la RMV."""
    base = D(base)
    if base <= 0:
        return CERO, CERO
    b = max(base, D(rmv))
    if tiene_eps:
        return q2(b * (D(tasa) - D(tasa_credito_eps))), q2(b * D(tasa_credito_eps))
    return q2(b * D(tasa)), CERO


# ------------------------------------------------------------------ subsidios (EsSalud)
def base_diaria_subsidio(meses: Sequence[dict], sueldo_actual) -> Decimal:
    """Retrocede hasta 12 meses; variables solo si aparecen en >= 3 meses (regularidad);
    incluye subsidios previos; divide entre 30 x meses (=360 con 12 meses).
    meses: [{'fijo': Decimal, 'variables': {codigo: Decimal}, 'subsidios': Decimal}] (más antiguo primero)."""
    meses = list(meses)[-12:]
    n = len(meses)
    if n == 0:
        return (D(sueldo_actual) / D(30)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    conteo = Counter(c for m in meses for c, v in m["variables"].items() if D(v) > 0)
    total = CERO
    for m in meses:
        total += D(m["fijo"]) + D(m.get("subsidios", 0))
        total += sum((D(v) for c, v in m["variables"].items() if conteo[c] >= 3), CERO)
    return (total / D(30 * n)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------ quinta categoría
def impuesto_anual_5ta(renta_neta, uit, escala: Iterable[Tuple[Decimal, Optional[Decimal], Decimal]]) -> Decimal:
    """escala: (desde_uit, hasta_uit|None, tasa)."""
    renta_neta, uit, imp = D(renta_neta), D(uit), CERO
    for desde, hasta, tasa in sorted(escala, key=lambda t: t[0]):
        d = D(desde) * uit
        h = D(hasta) * uit if hasta is not None else None
        if renta_neta > d:
            tope = renta_neta if h is None else min(renta_neta, h)
            imp += (tope - d) * D(tasa)
    return q2(imp)


def divisor_5ta(mes: int) -> int:
    if mes <= 3:
        return 12
    if mes == 4:
        return 9
    if mes <= 7:
        return 8
    if mes == 8:
        return 9
    if mes <= 11:
        return 4
    return 1


def gratificacion_proyectada(sueldo, mes: int) -> Decimal:
    """Gratificaciones de julio y diciembre que faltan percibir (sin bonificación 9 %: pendiente)."""
    n = 2 if mes < 7 else (1 if mes < 12 else 0)
    return D(sueldo) * n


def retencion_5ta_regular(*, mes, base_ordinaria_mes, extra_mes, gratificacion_proy, previos_empresa,
                          retenido_empresa, previos_otros, retenido_otros, uit, deduccion_uit,
                          deduccion_adicional_uit, escala) -> Decimal:
    restantes = 12 - mes + 1
    bruta = (D(base_ordinaria_mes) * restantes + D(extra_mes) + D(gratificacion_proy)
             + D(previos_empresa) + D(previos_otros))
    neta = max(bruta - (D(deduccion_uit) + D(deduccion_adicional_uit)) * D(uit), CERO)
    imp = impuesto_anual_5ta(neta, uit, escala)
    por_retener = imp - D(retenido_empresa) - D(retenido_otros)
    return q2(max(por_retener / D(divisor_5ta(mes)), CERO))


# =================================================================== LIQUIDACIÓN POR CESE (etapa 2)
# Todas puras y con Decimal. Fórmulas de la propuesta aprobada; los porcentajes y divisores vienen del Diccionario.
def tiempo_servicio(ingreso: date, cese: date) -> Tuple[int, int, int]:
    """Años, meses y días de servicio hasta el cese, incluido el día del cese."""
    fin = cese + timedelta(days=1)
    y, m, d = fin.year - ingreso.year, fin.month - ingreso.month, fin.day - ingreso.day
    if d < 0:
        m -= 1
        d += (fin.replace(day=1) - timedelta(days=1)).day     # días del mes anterior
    if m < 0:
        y -= 1
        m += 12
    return y, m, d


def meses_calendario_completos(desde: date, hasta: date) -> int:
    """Cuenta los meses calendario que quedan ENTEROS dentro de [desde, hasta] (los días sueltos no cuentan)."""
    n, y, m = 0, desde.year, desde.month
    while date(y, m, 1) <= hasta:
        if date(y, m, 1) >= desde and fin_de_mes(y, m) <= hasta:
            n += 1
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return n


def semestre_gratificacion(fecha: date) -> Tuple[date, date]:
    return (date(fecha.year, 1, 1), date(fecha.year, 6, 30)) if fecha.month <= 6 \
        else (date(fecha.year, 7, 1), date(fecha.year, 12, 31))


def semestre_cts(fecha: date) -> Tuple[date, date]:
    """Semestres de la CTS: mayo a octubre y noviembre a abril."""
    if 5 <= fecha.month <= 10:
        return date(fecha.year, 5, 1), date(fecha.year, 10, 31)
    ini = date(fecha.year if fecha.month >= 11 else fecha.year - 1, 11, 1)
    return ini, date(ini.year + 1, 4, 30)


def semestre_cts_anterior(fecha: date) -> Tuple[date, date]:
    ini, _ = semestre_cts(fecha)
    return semestre_cts(ini - timedelta(days=1))


def gratificacion_trunca(rc, meses: int, divisor) -> Decimal:
    return q2(D(rc) / D(divisor) * meses)


def bonificacion_extraordinaria(gratificacion, pct) -> Decimal:
    return q2(D(gratificacion) * D(pct))


def cts_monto(rc, meses: int, dias: int, div_mes, div_dia) -> Tuple[Decimal, Decimal]:
    """Devuelve (importe por meses, importe por días)."""
    return q2(D(rc) / D(div_mes) * meses), q2(D(rc) / D(div_dia) * dias)


def dias_vacaciones_ganados(anios: int, meses: int, dias: int, dias_anio) -> Decimal:
    """30 días por año, 2.5 por mes y 30/360 por día."""
    da = D(dias_anio)
    return da * anios + da / 12 * meses + da / 360 * dias


def vacaciones_pendientes(ganados: Decimal, usados: Decimal, anios: int, dias_anio) -> Tuple[Decimal, Decimal]:
    """(vencidas, truncas) en días. Vencidas = días de años completos sin usar; truncas = el resto."""
    pendientes = max(D(ganados) - D(usados), CERO)
    vencidas = min(max(D(dias_anio) * anios - D(usados), CERO), pendientes)
    return vencidas, pendientes - vencidas


def pago_vacaciones(rc, dias, dias_anio) -> Decimal:
    return q2(D(rc) / D(dias_anio) * D(dias))


def indemnizacion_indefinido(remuneracion, anios: int, meses: int, dias: int, factor, tope) -> Tuple[Decimal, List[Decimal]]:
    """1.5 remuneraciones por año; fracciones por dozavos y treintavos; tope de 12. Devuelve (total, [años, meses, días])."""
    r, f = D(remuneracion), D(factor)
    partes = [q2(f * r * anios), q2(f * r * meses / 12), q2(f * r * dias / 360)]
    return min(sum(partes, CERO), q2(D(tope) * r)), partes


def indemnizacion_plazo_fijo(remuneracion, meses_restantes: int, dias_restantes: int, factor, tope) -> Tuple[Decimal, List[Decimal]]:
    """1.5 remuneraciones por cada mes que falta hasta el vencimiento (fracción de mes por días / 30); tope de 12."""
    r, f = D(remuneracion), D(factor)
    partes = [q2(f * r * meses_restantes), q2(f * r * dias_restantes / 30)]
    return min(sum(partes, CERO), q2(D(tope) * r)), partes


def promedio_variable_regular(montos_por_mes: Sequence, regularidad: int, ventana: int) -> Decimal:
    """Variable regular (aparece en `regularidad` meses o más de la ventana): suma / ventana; si no, 0."""
    montos = [D(m) for m in montos_por_mes]
    if sum(1 for m in montos if m > 0) < int(regularidad):
        return CERO
    return q2(sum(montos, CERO) / D(ventana))


def ajuste_5ta_cese(*, base_acumulada, base_liquidacion, retenido_empresa, retenido_otros, uit, deduccion_uit,
                    deduccion_adicional_uit, escala):
    """Ajuste final SIN proyectar: impuesto anual sobre lo realmente percibido (incluida la boleta del mes en curso
    y la liquidación) menos lo ya retenido. Positivo = retener; negativo = devolver el exceso."""
    bruta = D(base_acumulada) + D(base_liquidacion)
    neta = max(bruta - (D(deduccion_uit) + D(deduccion_adicional_uit)) * D(uit), CERO)
    impuesto = impuesto_anual_5ta(neta, uit, escala)
    return bruta, neta, impuesto, q2(impuesto - D(retenido_empresa) - D(retenido_otros))


def rmv_minima_essalud(rmv, dias_subsidio, base_dias: int = 30) -> Decimal:
    """Base mínima de EsSalud: la RMV; con días de subsidio se prorratea por los días NO subsidiados."""
    pagados = max(D(base_dias) - D(dias_subsidio), CERO)
    return q2(D(rmv) * pagados / D(base_dias))


def essalud_diferencial(base_boleta, base_liquidacion, rmv_min, tasa, tasa_credito_eps, tiene_eps: bool):
    """Aporte consolidado (boleta + liquidación, mínimo aplicado una sola vez) menos lo ya calculado en la boleta."""
    total = calcular_essalud(D(base_boleta) + D(base_liquidacion), rmv_min, tasa, tasa_credito_eps, tiene_eps)
    antes = calcular_essalud(base_boleta, rmv_min, tasa, tasa_credito_eps, tiene_eps)
    return total[0] - antes[0], total[1] - antes[1]


def prima_diferencial(base_boleta, base_liquidacion, prima, tope) -> Decimal:
    """Prima del seguro sobre la base consolidada con el tope (RMA) aplicado una sola vez, menos la ya cobrada."""
    def prima_de(b):
        b = D(b)
        return q2((min(b, D(tope)) if tope else b) * D(prima))
    return max(prima_de(D(base_boleta) + D(base_liquidacion)) - prima_de(base_boleta), CERO)
