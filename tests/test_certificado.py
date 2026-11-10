"""Certificado de rentas y retenciones de quinta categoría del cesado."""
from decimal import Decimal as D

from tests.test_liquidacion import API, H, _cerrar, _escenario, _vista

CE = lambda B: f"{B}/liquidaciones/certificado-5ta"


def _cert(cli, clave, B, pid, emp, **k):
    q = "".join(f"&{a}={b}" for a, b in k.items())
    return cli.get(f"{CE(B)}?periodo_id={pid}&empleado_id={emp}{q}", headers=H(clave))


def test_certificado_cuadra_con_la_renta_de_5ta_de_la_liquidacion(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    r = _cert(cli, clave, B, pids[10], ana)
    assert r.status_code == 200
    c = r.json()
    q = _vista(cli, clave, B, pids[10], ana, True)["snapshot"]["quinta"]
    rubros = {x["clave"]: D(str(x["monto"])) for x in c["rubros"]}
    assert D(str(c["remuneracion_bruta"])) == D(str(q["renta_bruta"])) and sum(rubros.values()) == D(str(c["remuneracion_bruta"]))   # los rubros suman la renta bruta
    assert c["conciliado"] is True and rubros["sueldo"] > 0 and rubros["gratificaciones"] > 0 and rubros["otras_empresas"] == 0
    assert D(str(c["deduccion"])) == D("38500.00") and D(str(c["deduccion_uit"])) == 7                                              # 7 UIT de 5,500
    assert D(str(c["renta_neta"])) == D(str(c["remuneracion_bruta"])) - D("38500.00") if D(str(c["remuneracion_bruta"])) > D("38500") else True
    assert c["definitivo"] is False and c["trabajador"]["dni"] == "45678912" and c["ejercicio"] == 2026 and c["fecha"] == "2026-10-20"
    # impuesto, retenciones y devolución cuadran entre sí
    assert D(str(c["retenido_total"])) - D(str(c["devolucion"])) == D(str(c["impuesto"]))
    assert D(str(c["creditos"])) == 0
    assert "CERTIFICA QUE A NUESTRO(A) TRABAJADOR(A) SR(A): QUISPE ROJAS ANA CON DNI 45678912" in c["texto"] and "CON 0" not in c["texto"][:0]
    assert c["faltan"] == []                                                                         # la empresa de la prueba tiene domicilio fiscal y representante
    assert "PARTIDA" not in c["texto"] and "DOMICILIADO" not in c["texto"] and "IDENTIFICADO" not in c["texto"]      # sin DNI del representante, partida ni domicilio del colaborador


def test_certificado_con_rentas_previas_y_datos_de_la_empresa(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    assert cli.post(f"{B}/empleados/{ana}/rentas-previas-5ta", headers=H(clave), json={"anio": 2026, "mes": 3, "ruc": "20512345678", "razon_social": "Otra SAC", "ingreso_afecto": 4000, "retencion": 100}).status_code == 201
    c = _cert(cli, clave, B, pids[10], ana).json()
    rubros = {x["clave"]: D(str(x["monto"])) for x in c["rubros"]}
    assert rubros["otras_empresas"] == D("4000.00") and c["conciliado"] is True and D(str(c["retenido_otros"])) == D("100.00")
    assert c["empresa"]["representante"] == "GERENTE DE PRUEBA" and c["faltan"] == [] and len(c["rentas_previas"]) == 1
    assert "REPRESENTADA POR EL SR.(A) GERENTE DE PRUEBA, CERTIFICA QUE A NUESTRO(A)" in c["texto"]
    pdf = cli.get(f"{CE(B)}/pdf?periodo_id={pids[10]}&empleado_id={ana}", headers=H(clave))
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content[:4] == b"%PDF" and "BORRADOR" in pdf.headers["content-disposition"]
    # al cerrar la liquidación el certificado deja de ser borrador
    _cerrar(cli, clave, B, pids[10], ana)
    cd = _cert(cli, clave, B, pids[10], ana).json()
    assert cd["definitivo"] is True and D(str(cd["impuesto"])) == D(str(c["impuesto"]))
    pdf2 = cli.get(f"{CE(B)}/pdf?periodo_id={pids[10]}&empleado_id={ana}", headers=H(clave))
    assert pdf2.status_code == 200 and "BORRADOR" not in pdf2.headers["content-disposition"]


def test_reabrir_la_liquidacion_desbloquea_la_boleta_del_mes(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    cerrada = _cerrar(cli, clave, B, pids[10], ana).json()
    lid = cerrada["liquidacion_id"]
    assert _vista(cli, clave, B, pids[10], ana, True)["estado"] == "CERRADA"
    r = cli.post(f"{B}/liquidaciones/{lid}/reabrir", headers=H(clave), json={"motivo": "Corrección de vacaciones"})
    assert r.status_code == 200 and r.json()["estado"] == "SIN_CERRAR"
    assert _vista(cli, clave, B, pids[10], ana, True)["estado"] != "CERRADA"
    assert cli.post(f"{B}/planillas/{pids[10]}/recalcular", headers=H(clave)).status_code == 200                       # la boleta vuelve a procesarse
