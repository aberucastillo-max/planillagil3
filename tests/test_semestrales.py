"""Gratificación automática (julio y diciembre), CTS semestral y reingreso."""
import io
import zipfile
from decimal import Decimal as D

from tests.test_liquidacion import API, H, _conceptos, _escenario

PLAME = "/exportar-plame-zip"


def _base(cli, nueva, meses):
    clave = nueva("Estudio", max_empresas=2)
    e = cli.post(API, json={"ruc": "20444444444", "razon_social": "Cliente Semestral SAC"}, headers=H(clave)).json()["id"]
    B = f"{API}/{e}"
    ids = {}
    for nombre, dni, ing, sueldo, af in [("Ana", "45678912", "2025-03-01", 3000, True), ("Luis", "40123456", "2026-02-01", 2000, False),
                                         ("Rosa", "70112233", "2026-08-15", 1800, False)]:
        ids[nombre] = cli.post(f"{B}/empleados", headers=H(clave), json={
            "nombres": nombre, "apellidos": nombre + " Prueba", "documento_identidad": dni, "fecha_ingreso": ing,
            "sueldo_basico": sueldo, "sistema_pensionario": "ONP", "asignacion_familiar": af, "numero_cuenta": "1"}).json()["id"]
    pids = {m: cli.post(f"{B}/periodos", json={"anio": 2026, "mes": m}, headers=H(clave)).json()["id"] for m in meses}
    return clave, B, ids, pids


def _detalle(cli, clave, B, pid, emp):
    return {c["codigo"]: c for c in cli.get(f"{B}/planillas/{pid}/resultados/{emp}/detalle", headers=H(clave)).json()["conceptos"]}


def test_gratificacion_automatica_de_julio_y_diciembre(entorno):
    cli, nueva = entorno
    clave, B, ids, pids = _base(cli, nueva, [7, 12])
    assert cli.post(f"{B}/planillas/{pids[7]}/recalcular", headers=H(clave)).status_code == 200
    ana, luis = _detalle(cli, clave, B, pids[7], ids["Ana"]), _detalle(cli, clave, B, pids[7], ids["Luis"])
    assert D(str(ana["@GRATIFICACION"]["monto"])) == D("3113.00") and D(str(ana["@BONIF_EXTRA_29351"]["monto"])) == D("280.17")
    assert D(str(luis["@GRATIFICACION"]["monto"])) == D("1666.67")                  # febrero a junio = 5 meses
    assert D(str(luis["@BONIF_EXTRA_29351"]["monto"])) == D("150.00")
    # la gratificación va en los mismos archivos que la boleta: línea 0406 en el PLAME
    z = zipfile.ZipFile(io.BytesIO(cli.get(f"{B}/planillas/{pids[7]}{PLAME}", headers=H(clave)).content))
    rem = z.read([n for n in z.namelist() if n.endswith("rem")][0]).decode("latin-1")
    assert "01|45678912|0406|3113.00|3113.00|" in rem and "01|40123456|0406|1666.67|1666.67|" in rem
    # diciembre: Rosa ingresó el 15/08 => setiembre a diciembre = 4 meses completos
    assert cli.post(f"{B}/planillas/{pids[12]}/recalcular", headers=H(clave)).status_code == 200
    rosa = _detalle(cli, clave, B, pids[12], ids["Rosa"])
    assert D(str(rosa["@GRATIFICACION"]["monto"])) == D("1200.00") and D(str(rosa["@BONIF_EXTRA_29351"]["monto"])) == D("108.00")
    ana_d = _detalle(cli, clave, B, pids[12], ids["Ana"])
    assert D(str(ana_d["@GRATIFICACION"]["monto"])) == D("3113.00")                  # julio a diciembre = 6 meses
    # gratificación y bonificación no pagan AFP/ONP ni EsSalud: la base previsional es solo sueldo + asignación
    fila = [f for f in cli.get(f"{B}/planillas/{pids[12]}/resultados", headers=H(clave)).json()["filas"] if f["documento"] == "45678912"][0]
    assert D(str(fila["descuentos"])) < D("1000")


def test_la_novedad_manual_de_gratificacion_prevalece(entorno):
    cli, nueva = entorno
    clave, B, ids, pids = _base(cli, nueva, [7])
    cli.put(f"{B}/periodos/{pids[7]}/novedades", headers=H(clave), json=[{"documento": "45678912", "codigo": "@GRATIFICACION", "valor": "2500"}])
    cli.post(f"{B}/planillas/{pids[7]}/recalcular", headers=H(clave))
    ana = _detalle(cli, clave, B, pids[7], ids["Ana"])
    assert D(str(ana["@GRATIFICACION"]["monto"])) == D("2500.00") and ana["@GRATIFICACION"]["origen"] == "NOVEDAD"
    assert "@BONIF_EXTRA_29351" not in ana                                            # no se mezcla con la automática


def test_cts_semestral_calculo_deposito_y_excel(entorno):
    cli, nueva = entorno
    clave, B, ids, pids = _base(cli, nueva, [7, 10])
    cli.post(f"{B}/planillas/{pids[7]}/recalcular", headers=H(clave))                  # gratificación de julio (sexto para la CTS)
    r = cli.post(f"{B}/cts-semestral/calcular", headers=H(clave), json={"anio": 2026, "semestre": "NOV"}).json()
    por_doc = {f["documento"]: f for f in r["filas"]}
    assert D(str(por_doc["45678912"]["monto"])) == D("1815.92") and por_doc["45678912"]["meses"] == 6     # (3,113 + 518.83) / 12 × 6
    assert D(str(por_doc["70112233"]["monto"])) == D("385.00")                          # 2 meses + 17 días, sin sexto
    assert (por_doc["70112233"]["meses"], por_doc["70112233"]["dias"]) == (2, 17)
    assert r["semestre"]["inicio"] == "2026-05-01" and r["semestre"]["fin"] == "2026-10-31"
    assert any("ROSA" in a for a in r["avisos"])                                        # sin gratificación registrada
    assert cli.get(f"{B}/cts-semestral?anio=2026&semestre=NOV", headers=H(clave)).json()["total"] == r["total"]
    x = cli.get(f"{B}/cts-semestral/exportar?anio=2026&semestre=NOV", headers=H(clave))
    assert x.status_code == 200 and x.content[:2] == b"PK"                              # .xlsx
    dep = cli.post(f"{B}/cts-semestral/depositar", headers=H(clave), json={"anio": 2026, "semestre": "NOV", "fecha_deposito": "2026-11-12"}).json()
    assert dep["resumen"] == {"calculadas": 0, "depositadas": len(r["filas"])}
    assert cli.post(f"{B}/cts-semestral/depositar", headers=H(clave), json={"anio": 2026, "semestre": "NOV", "fecha_deposito": "2026-11-12"}).status_code == 409
    # recalcular no toca lo ya depositado
    cli.post(f"{B}/empleados/{ids['Ana']}/sueldos", headers=H(clave), json={"monto": 9000, "fecha_vigencia": "2026-10-01"})
    r2 = cli.post(f"{B}/cts-semestral/calcular", headers=H(clave), json={"anio": 2026, "semestre": "NOV"}).json()
    assert {f["documento"]: f["monto"] for f in r2["filas"]} == {f["documento"]: f["monto"] for f in r["filas"]}
    rev = cli.post(f"{B}/cts-semestral/revertir", headers=H(clave), json={"anio": 2026, "semestre": "NOV", "empleado_ids": [ids["Ana"]]}).json()
    assert rev["resumen"]["calculadas"] == 1
    assert cli.post(f"{B}/cts-semestral/calcular", headers=H(clave), json={"anio": 2026, "semestre": "XXX"}).status_code == 422


def test_cts_depositada_marca_como_pagada_la_cts_anterior_de_la_liquidacion(entorno):
    cli, nueva = entorno
    clave, B, ids, pids = _base(cli, nueva, [7, 11])
    beto = cli.post(f"{B}/empleados", headers=H(clave), json={
        "nombres": "Beto", "apellidos": "Cese Pronto", "documento_identidad": "11223344", "fecha_ingreso": "2025-06-01",
        "sueldo_basico": 2400, "sistema_pensionario": "ONP", "numero_cuenta": "1"}).json()["id"]
    cli.post(f"{B}/planillas/{pids[7]}/recalcular", headers=H(clave))
    cli.post(f"{B}/cts-semestral/calcular", headers=H(clave), json={"anio": 2026, "semestre": "NOV"})
    cli.post(f"{B}/cts-semestral/depositar", headers=H(clave), json={"anio": 2026, "semestre": "NOV", "fecha_deposito": "2026-11-12"})
    cli.post(f"{B}/empleados/{beto}/baja", headers=H(clave), json={"fecha_cese": "2026-11-10", "motivo": "01"})
    cli.post(f"{B}/planillas/{pids[11]}/recalcular", headers=H(clave))
    r = cli.post(f"{B}/liquidaciones", headers=H(clave), json={"periodo_id": pids[11], "empleado_ids": [beto]}).json()
    assert r["errores"] == [], r
    v = cli.get(f"{B}/liquidaciones/{r['procesadas'][0]['liquidacion_id']}", headers=H(clave)).json()
    assert v["cts_anterior_pagada"] is True and v["puede_cerrar"]                       # no hubo que responder la casilla
    assert any("DEPOSITADA" in a for a in v["avisos"])
    assert [c["codigo"] for c in v["conceptos"]].count("@CTS_TRUNCA") == 1               # solo la del semestre en curso


def test_reingreso_abre_un_nuevo_ciclo_y_conserva_el_historial(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    lid = cli.post(f"{B}/liquidaciones", headers=H(clave), json={"periodo_id": pids[10], "empleado_ids": [ana], "cts_anterior_pagada": True}).json()["procesadas"][0]["liquidacion_id"]
    # con la liquidación ABIERTA no se puede reingresar; el ingreso debe ser posterior al cese
    assert cli.post(f"{B}/empleados/{ana}/reingreso", headers=H(clave), json={"fecha_ingreso": "2026-11-02"}).status_code == 409
    cli.post(f"{B}/liquidaciones/{lid}/cerrar", headers=H(clave))
    assert cli.post(f"{B}/empleados/{ana}/reingreso", headers=H(clave), json={"fecha_ingreso": "2026-10-20"}).status_code == 422
    assert cli.post(f"{B}/empleados/{ana}/sueldos", headers=H(clave), json={"monto": 3500, "fecha_vigencia": "2026-11-02"}).status_code == 403
    r = cli.post(f"{B}/empleados/{ana}/reingreso", headers=H(clave), json={"fecha_ingreso": "2026-11-02", "sueldo_basico": 3500})
    assert r.status_code == 200 and r.json()["estado"] == "ACTIVO" and r.json()["aviso"] is None
    emp = {e["documento"]: e for e in cli.get(f"{B}/empleados", headers=H(clave)).json()}["45678912"]
    assert emp["estado"] == "ACTIVO" and emp["fecha_ingreso"] == "2026-11-02" and emp["fecha_cese"] is None and float(emp["sueldo_vigente"]) == 3500.0
    hist = cli.get(f"{B}/empleados/{ana}/vinculos", headers=H(clave)).json()
    assert hist == [{"fecha_ingreso": "2025-03-01", "fecha_cese": "2026-10-20", "motivo_cese": "01", "clase_despido": None}]
    # el nuevo ciclo ya no está bloqueado por la liquidación del ciclo anterior
    assert cli.post(f"{B}/empleados/{ana}/vacaciones", headers=H(clave), json={"fecha_inicio": "2027-02-01", "fecha_fin": "2027-02-05"}).status_code == 201
    nov = cli.post(f"{B}/periodos", json={"anio": 2026, "mes": 11}, headers=H(clave)).json()["id"]
    assert [f["documento"] for f in cli.get(f"{B}/liquidaciones?periodo_id={nov}", headers=H(clave)).json()["filas"] if f["documento"] == "45678912"] == []
    assert cli.post(f"{B}/empleados/{ana}/reingreso", headers=H(clave), json={"fecha_ingreso": "2026-12-01"}).status_code == 409   # ya está activo
