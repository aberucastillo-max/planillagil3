"""Carga masiva de trabajadores desde Excel: todo o nada, actualización de existentes, sistema pensionario obligatorio."""
import base64
import io
from datetime import date

from openpyxl import Workbook, load_workbook

from app import carga_masiva as CM
from tests.test_liquidacion import API, H, _escenario

CM_URL = lambda B: f"{B}/empleados/carga-masiva"


def _xlsx(filas: list, omitir=(), extra_hoja=None) -> str:
    """Arma un Excel con los encabezados de la plantilla; cada fila es un dict clave -> valor."""
    wb = Workbook(); ws = wb.active; ws.title = "Trabajadores"
    cols = [c for c in CM.COLS if c[0] not in omitir]
    for j, c in enumerate(cols, 1):
        ws.cell(1, j, c[1] + (" *" if c[3] == CM.O else ""))
    for i, f in enumerate(filas, 2):
        for j, c in enumerate(cols, 1):
            ws.cell(i, j, f.get(c[0]))
    buf = io.BytesIO(); wb.save(buf)
    return base64.b64encode(buf.getvalue()).decode()


def _lista(cli, clave, B):
    return {e["documento"]: e for e in cli.get(f"{B}/empleados", headers=H(clave)).json()}


NUEVO1 = dict(apellidos="ramos torres", nombres="luis alberto", documento="45123789", fecha_ingreso=date(2025, 2, 3), sueldo=3500, sistema_pensionario="AFP", afp="prima",
              cuspp="589741ARTRM3", tipo_comision="FLUJO", asignacion_familiar="SI", banco="BCP", numero_cuenta="19412345678012", puesto="analista contable")
NUEVO2 = dict(apellidos="Soto Gil", nombres="Marta", documento="46001122", fecha_ingreso="01/03/2025", sueldo="2,100.50", sistema_pensionario="ONP")      # solo lo obligatorio
NUEVO3 = dict(apellidos="Paz Luna", nombres="Rosa", documento=4512378, fecha_ingreso=date(2026, 1, 5), sueldo=1800, sistema_pensionario="ONP", asignacion_familiar="NO")


def test_plantilla_con_las_afp_del_diccionario(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    r = cli.get(f"{CM_URL(B)}/plantilla", headers=H(clave))
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"] and "PLANTILLA_CARGA" in r.headers["content-disposition"]
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Léeme", "Trabajadores", "Parámetros", "Listas"]
    assert "PRIMA" in [c.value for c in wb["Listas"]["C"]] and wb["Trabajadores"]["H1"].value.startswith("Sistema pensionario")
    # la plantilla se puede volver a subir tal cual (la fila de ejemplo se ignora) pero no trae trabajadores
    assert cli.post(f"{CM_URL(B)}/validar", headers=H(clave), json={"archivo_base64": base64.b64encode(r.content).decode()}).status_code == 422


def test_carga_de_trabajadores_nuevos_con_datos_opcionales_en_blanco(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    archivo = _xlsx([NUEVO1, NUEVO2, NUEVO3])
    v = cli.post(f"{CM_URL(B)}/validar", headers=H(clave), json={"archivo_base64": archivo}).json()
    assert v["puede_cargar"] is True and v["resumen"]["crear"] == 3 and v["resumen"]["con_error"] == 0
    avisos = {f["documento"]: f["avisos"] for f in v["filas"]}
    assert avisos["45123789"] == [] and any("N° de cuenta" in a for a in avisos["46001122"]) and any("ceros" in a for a in avisos["04512378"])
    antes = len(_lista(cli, clave, B))
    assert len(_lista(cli, clave, B)) == antes                                                     # revisar no guarda nada
    r = cli.post(f"{CM_URL(B)}/cargar", headers=H(clave), json={"archivo_base64": archivo}).json()
    assert r["cargado"] is True and r["creados"] == 3 and r["actualizados"] == 0
    lst = _lista(cli, clave, B)
    a, b, c = lst["45123789"], lst["46001122"], lst["04512378"]
    assert a["nombre"] == "RAMOS TORRES, LUIS ALBERTO" and a["afp"] == "PRIMA" and a["puesto"] == "ANALISTA CONTABLE" and a["asignacion_familiar"] is True
    assert b["sistema_pensionario"] == "ONP" and b["asignacion_familiar"] is False and b["tipo_contrato"] == "INDEFINIDO" and b["categoria"] == "EMPLEADOS" and not b["cuenta"]
    assert c["documento"] == "04512378"
    sueldos = cli.get(f"{B}/empleados/{b['id']}/sueldos", headers=H(clave)).json()
    assert len(sueldos) == 1 and float(sueldos[0]["monto"]) == 2100.50


def test_todo_o_nada_con_un_solo_error_no_se_carga_ninguna_fila(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    malas = [dict(NUEVO2, documento="ABC"), dict(NUEVO2, documento="46001133", sueldo=0), dict(NUEVO2, documento="46001144", sistema_pensionario=None),
             dict(NUEVO2, documento="46001155", sistema_pensionario="AFP", afp="NOEXISTE"), dict(NUEVO2, documento="46001166", tipo_contrato="PLAZO_FIJO"),
             dict(NUEVO2, documento="46001177", fecha_ingreso="31/31/2025"), dict(NUEVO2, documento="46001188", asignacion_familiar="TAL VEZ")]
    archivo = _xlsx([NUEVO1] + malas + [NUEVO1])                                                  # la 1.ª es válida; la última repite su documento
    antes = len(_lista(cli, clave, B))
    r = cli.post(f"{CM_URL(B)}/cargar", headers=H(clave), json={"archivo_base64": archivo}).json()
    assert r["cargado"] is False and r["informe"]["puede_cargar"] is False and r["informe"]["resumen"]["con_error"] == 8
    assert len(_lista(cli, clave, B)) == antes                                                    # ni siquiera la fila válida se guardó
    err = {f["fila"]: " ".join(f["errores"]) for f in r["informe"]["filas"]}
    assert "8 dígitos" in err[3] and "mayor que cero" in err[4] and "sistema pensionario" in err[5] and "no reconocida" in err[6]
    assert "plazo fijo" in err[7] and "Fecha de ingreso" in err[8] and "SI ni NO" in err[9] and "repetido" in err[10]
    assert r["informe"]["filas"][0]["estado"] == "OK"                                             # la válida se muestra como correcta


def test_los_existentes_se_actualizan_sin_borrar_lo_que_no_se_llena(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    nuevo = cli.post(f"{B}/empleados", headers=H(clave), json={"nombres": "Mario", "apellidos": "Tarde", "documento_identidad": "47001122", "fecha_ingreso": "2025-01-02",
                     "sueldo_basico": 3000, "sistema_pensionario": "AFP", "afp_nombre": "PRIMA", "cuspp": "123456ABCDE1", "numero_cuenta": "999", "area": "VENTAS", "banco": "BBVA"})
    mid = nuevo.json()["id"]
    fila = dict(apellidos="OTRO NOMBRE", nombres="OTRO", documento="47001122", fecha_ingreso=date(2020, 1, 1), sueldo=3000, sistema_pensionario="AFP", puesto="Jefe de Ventas",
                asignacion_familiar="SI", centro_costo="000002 VENTAS")                          # sin AFP, sin cuenta, sin banco, sin área: no deben borrarse
    v = cli.post(f"{CM_URL(B)}/validar", headers=H(clave), json={"archivo_base64": _xlsx([fila])}).json()
    f0 = v["filas"][0]
    assert v["puede_cargar"] is True and f0["accion"] == "ACTUALIZAR" and f0["estado"] == "AVISO"
    assert {c["campo"] for c in f0["cambios"]} == {"Puesto", "Asignación familiar", "Centro de costo"}
    assert any("nombre" in a for a in f0["avisos"]) and any("ingreso" in a for a in f0["avisos"])        # nombre e ingreso no se modifican
    r = cli.post(f"{CM_URL(B)}/cargar", headers=H(clave), json={"archivo_base64": _xlsx([fila])}).json()
    assert r["cargado"] and r["actualizados"] == 1 and r["creados"] == 0
    e = _lista(cli, clave, B)["47001122"]
    assert e["puesto"] == "JEFE DE VENTAS" and e["asignacion_familiar"] is True and e["centro_costo"] == "000002 VENTAS"
    assert e["nombre"] == "TARDE, MARIO" and e["afp"] == "PRIMA" and e["cuenta"] == "999" and e["area"] == "VENTAS" and e["banco"] == "BBVA"
    # sin cambios: se avisa y no se cuenta como actualizado
    r2 = cli.post(f"{CM_URL(B)}/cargar", headers=H(clave), json={"archivo_base64": _xlsx([fila])}).json()
    assert r2["cargado"] and r2["actualizados"] == 0 and any("Sin cambios" in a for a in r2["informe"]["filas"][0]["avisos"])


def test_un_cambio_de_sueldo_exige_la_vigencia_y_queda_en_el_historial(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    mid = cli.post(f"{B}/empleados", headers=H(clave), json={"nombres": "Mario", "apellidos": "Tarde", "documento_identidad": "47001122", "fecha_ingreso": "2025-01-02",
                   "sueldo_basico": 3000, "sistema_pensionario": "ONP"}).json()["id"]
    fila = dict(apellidos="Tarde", nombres="Mario", documento="47001122", fecha_ingreso=date(2025, 1, 2), sueldo=3300, sistema_pensionario="ONP")
    r = cli.post(f"{CM_URL(B)}/cargar", headers=H(clave), json={"archivo_base64": _xlsx([fila])}).json()
    assert r["cargado"] is False and "Vigencia del nuevo sueldo" in r["informe"]["filas"][0]["errores"][0]
    ok = cli.post(f"{CM_URL(B)}/cargar", headers=H(clave), json={"archivo_base64": _xlsx([dict(fila, vigencia_sueldo=date(2026, 1, 1))])}).json()
    assert ok["cargado"] and ok["actualizados"] == 1 and "Sueldo (desde 01/01/2026)" in [c["campo"] for c in ok["informe"]["filas"][0]["cambios"]]
    sueldos = cli.get(f"{B}/empleados/{mid}/sueldos", headers=H(clave)).json()
    assert sorted(float(s["monto"]) for s in sueldos) == [3000.0, 3300.0]
    # cambiar de AFP a ONP limpia la AFP; pasar a AFP sin indicar cuál es error
    a = cli.post(f"{CM_URL(B)}/validar", headers=H(clave), json={"archivo_base64": _xlsx([dict(fila, sueldo=3300, sistema_pensionario="AFP")])}).json()
    assert a["puede_cargar"] is False and "Indique la AFP" in a["filas"][0]["errores"][0]


def test_limite_del_plan_y_archivos_invalidos(entorno):
    cli, nueva = entorno
    clave, B, ana, luis, pids = _escenario(cli, nueva)
    lim = [e for e in cli.get(API, headers=H(clave)).json() if f"{API}/{e['id']}" == B][0]["limite_trabajadores"]
    activos = sum(1 for e in cli.get(f"{B}/empleados", headers=H(clave)).json() if e["estado"] == "ACTIVO")
    filas = [dict(NUEVO2, documento=f"{50000000 + i}") for i in range(lim - activos + 1)]       # uno más de lo que admite el plan
    r = cli.post(f"{CM_URL(B)}/cargar", headers=H(clave), json={"archivo_base64": _xlsx(filas)}).json()
    assert r["cargado"] is False and any("límite de su plan" in " ".join(f["errores"]) for f in r["informe"]["filas"])
    assert cli.post(f"{CM_URL(B)}/validar", headers=H(clave), json={"archivo_base64": base64.b64encode(b"no es un excel").decode()}).status_code == 422
    falta = cli.post(f"{CM_URL(B)}/validar", headers=H(clave), json={"archivo_base64": _xlsx([NUEVO2], omitir=("sistema_pensionario",))})
    assert falta.status_code == 422 and "Sistema pensionario" in falta.json()["detail"]
