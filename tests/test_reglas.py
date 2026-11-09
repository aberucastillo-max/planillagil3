from datetime import date
from decimal import Decimal as D

from app import reglas as R

ESCALA = [(D("0"), D("5"), D("0.08")), (D("5"), D("20"), D("0.14")), (D("20"), D("35"), D("0.17")),
          (D("35"), D("45"), D("0.20")), (D("45"), None, D("0.30"))]


def test_ingreso_dia_21_son_10_dias():
    assert R.dias_vinculo(date(2026, 10, 21), None, date(2026, 10, 1), date(2026, 10, 31)) == 10


def test_mes_completo_y_cese_fin_de_mes_son_30():
    assert R.dias_vinculo(date(2025, 1, 1), None, date(2026, 2, 1), date(2026, 2, 28)) == 30
    assert R.dias_vinculo(date(2025, 1, 1), date(2026, 2, 28), date(2026, 2, 1), date(2026, 2, 28)) == 30
    assert R.dias_vinculo(date(2025, 1, 1), date(2026, 2, 27), date(2026, 2, 1), date(2026, 2, 28)) == 27


def test_fuera_del_periodo_son_0():
    assert R.dias_vinculo(date(2026, 11, 1), None, date(2026, 10, 1), date(2026, 10, 31)) == 0
    assert R.dias_vinculo(date(2025, 1, 1), date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 31)) == 0


def test_solape_solo_cuenta_dias_del_periodo():
    assert R.dias_calendario_solapados(date(2026, 9, 25), date(2026, 10, 3), date(2026, 10, 1), date(2026, 10, 31)) == 3
    assert R.dias_calendario_solapados(date(2026, 8, 1), date(2026, 8, 7), date(2026, 10, 1), date(2026, 10, 31)) == 0


def test_horas_extras_base_sueldo_mas_af_entre_30_y_8():
    vh = R.valor_hora(D("3000"), D("113"), 30, 8)
    assert R.monto_horas_extras(2, vh, D("0.25")) == R.q2(2 * (D("3113") / 240) * D("1.25"))


def test_afp_tope_de_prima():
    ap, co, pr = R.calcular_afp(D("20000"), D("0.10"), D("0.0155"), D("0.0137"), D("12875"))
    assert (ap, co, pr) == (D("2000.00"), D("310.00"), R.q2(D("12875") * D("0.0137")))


def test_essalud_minimo_rmv_y_eps():
    assert R.calcular_essalud(D("500"), D("1130"), D("0.09"), D("0.0225"), False) == (D("101.70"), D("0"))
    e, p = R.calcular_essalud(D("2000"), D("1130"), D("0.09"), D("0.0225"), True)
    assert (e, p) == (D("135.00"), D("45.00"))


def test_impuesto_escala_5ta():
    uit = D("5500")
    assert R.impuesto_anual_5ta(5 * uit, uit, ESCALA) == R.q2(5 * uit * D("0.08"))
    assert R.impuesto_anual_5ta(6 * uit, uit, ESCALA) == R.q2(5 * uit * D("0.08") + uit * D("0.14"))
    assert R.impuesto_anual_5ta(0, uit, ESCALA) == 0


def test_divisores_5ta():
    assert [R.divisor_5ta(m) for m in range(1, 13)] == [12, 12, 12, 9, 8, 8, 8, 9, 4, 4, 4, 1]


def test_sueldo_bajo_no_retiene_5ta():
    r = R.retencion_5ta_regular(mes=10, base_ordinaria_mes=D("2500"), extra_mes=0, gratificacion_proy=D("2500"),
                                previos_empresa=D("22500"), retenido_empresa=0, previos_otros=0, retenido_otros=0,
                                uit=D("5500"), deduccion_uit=7, deduccion_adicional_uit=0, escala=ESCALA)
    assert r == 0


def test_subsidio_regularidad_y_divisor():
    mes = lambda var: {"fijo": D("3000"), "variables": {"@COMISIONES": D(var)}, "subsidios": D("0")}
    # comisiones en 3 meses => se incluyen; en 2 => no
    tres = [mes(100), mes(100), mes(100)] + [mes(0)] * 9
    assert R.base_diaria_subsidio(tres, D("3000")) == (D("36300") / 360).quantize(D("0.0001"))
    dos = [mes(100), mes(100)] + [mes(0)] * 10
    assert R.base_diaria_subsidio(dos, D("3000")) == D("100.0000")
    assert R.base_diaria_subsidio([], D("3000")) == D("100.0000")


# ------------------------------------------------------------------ liquidación por cese (ejemplos del documento de propuesta)
from app import reglas as R2  # noqa: E402


def test_tiempo_de_servicio_y_meses_calendario():
    assert R2.tiempo_servicio(date(2025, 3, 1), date(2026, 10, 20)) == (1, 7, 20)
    assert R2.tiempo_servicio(date(2026, 1, 1), date(2026, 12, 31)) == (1, 0, 0)
    assert R2.meses_calendario_completos(date(2026, 7, 1), date(2026, 10, 20)) == 3            # jul, ago, set
    assert R2.meses_calendario_completos(date(2026, 3, 15), date(2026, 6, 30)) == 3            # abr, may, jun
    assert R2.meses_calendario_completos(date(2026, 7, 1), date(2026, 7, 20)) == 0


def test_semestres():
    assert R2.semestre_gratificacion(date(2026, 10, 20)) == (date(2026, 7, 1), date(2026, 12, 31))
    assert R2.semestre_cts(date(2026, 10, 20)) == (date(2026, 5, 1), date(2026, 10, 31))
    assert R2.semestre_cts(date(2026, 12, 5)) == (date(2026, 11, 1), date(2027, 4, 30))
    assert R2.semestre_cts(date(2027, 2, 10)) == (date(2026, 11, 1), date(2027, 4, 30))
    assert R2.semestre_cts_anterior(date(2026, 10, 20)) == (date(2025, 11, 1), date(2026, 4, 30))


def test_ejemplo_gratificacion_cts_vacaciones_de_la_propuesta():
    rc = D("3113")
    g = R2.gratificacion_trunca(rc, 3, 6)
    assert g == D("1556.50") and R2.bonificacion_extraordinaria(g, D("0.09")) == D("140.09")
    rc_cts = rc + R2.q2(rc / 6)                                                   # + 1/6 de la gratificación
    assert rc_cts == D("3631.83")
    assert R2.cts_monto(rc_cts, 5, 20, 12, 360) == (D("1513.26"), D("201.77"))
    ganados = R2.dias_vacaciones_ganados(1, 7, 20, 30)
    assert round(ganados, 4) == D("49.1667")
    venc, trunc = R2.vacaciones_pendientes(ganados, D("15"), 1, 30)
    assert venc == D("15") and round(trunc, 4) == D("19.1667")
    assert R2.pago_vacaciones(rc, venc, 30) == D("1556.50")
    assert R2.pago_vacaciones(rc, trunc, 30) == D("1988.86")


def test_vacaciones_usadas_mas_que_los_anios_completos():
    venc, trunc = R2.vacaciones_pendientes(D("49.1667"), D("40"), 1, 30)
    assert venc == D("0") and round(trunc, 4) == D("9.1667")


def test_indemnizacion_indefinida_y_plazo_fijo():
    total, partes = R2.indemnizacion_indefinido(D("3113"), 1, 7, 20, D("1.5"), 12)
    assert partes == [D("4669.50"), D("2723.88"), D("259.42")] and total == D("7652.80")
    tope, _ = R2.indemnizacion_indefinido(D("3000"), 15, 0, 0, D("1.5"), 12)
    assert tope == D("36000.00")
    pf, _ = R2.indemnizacion_plazo_fijo(D("3000"), 4, 15, D("1.5"), 12)
    assert pf == D("18000.00") + D("2250.00")


def test_promedio_de_variables_con_regularidad():
    assert R2.promedio_variable_regular([100, 100, 100, 0, 0, 0], 3, 6) == D("50.00")
    assert R2.promedio_variable_regular([100, 100, 0, 0, 0, 0], 3, 6) == D("0")


def test_ajuste_5ta_cese_con_devolucion_de_exceso():
    bruta, neta, imp, ajuste = R2.ajuste_5ta_cese(
        base_acumulada=D("33485.50"), base_liquidacion=D("5241.95"), retenido_empresa=D("300"), retenido_otros=0,
        uit=D("5500"), deduccion_uit=7, deduccion_adicional_uit=0, escala=ESCALA)
    assert (bruta, neta, imp) == (D("38727.45"), D("227.45"), D("18.20")) and ajuste == D("-281.80")


def test_topes_se_aplican_una_sola_vez_sobre_la_base_del_mes():
    ess, eps = R2.essalud_diferencial(D("500"), D("800"), D("1130"), D("0.09"), D("0.0225"), False)
    assert ess == D("15.30") and eps == 0
    assert R2.prima_diferencial(D("11500"), D("2000"), D("0.0137"), D("12732.70")) == D("16.89")
    assert R2.prima_diferencial(D("13000"), D("2000"), D("0.0137"), D("12732.70")) == D("0")   # ya estaba topado


def test_base_minima_de_essalud_con_subsidio():
    assert R2.rmv_minima_essalud(D("1130"), 0) == D("1130.00")
    assert R2.rmv_minima_essalud(D("1130"), 10) == D("753.33")
    assert R2.rmv_minima_essalud(D("1130"), 30) == D("0.00")
