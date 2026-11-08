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
