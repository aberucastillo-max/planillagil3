"""Reglas matemáticas PURAS (sin base de datos): fáciles de probar y de versionar.
Todo el dinero usa Decimal y se redondea a 2 decimales (mitad hacia arriba)."""
import calendar
from collections import Counter
from datetime import date
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
