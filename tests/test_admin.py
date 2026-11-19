"""Modo administrador: acceso a todas las cuentas y RUC con el código de instalación (ADMIN_TOKEN o SETUP_TOKEN)."""
KEY = lambda k: {"X-API-Key": k}
ADM = {"X-Admin-Token": "codigo-admin-de-prueba"}
API = "/api/v1"


def _dos_cuentas(cli, nueva):
    a, b = nueva("Estudio A", max_empresas=2), nueva("Estudio B", max_empresas=1, max_trabajadores=1)
    ea = cli.post(f"{API}/empresas", json={"ruc": "20111111111", "razon_social": "Empresa de A SAC"}, headers=KEY(a)).json()["id"]
    eb = cli.post(f"{API}/empresas", json={"ruc": "20222222222", "razon_social": "Empresa de B SAC"}, headers=KEY(b)).json()["id"]
    return a, b, ea, eb


def _emp(dni="45678912"):
    return {"nombres": "Ana", "apellidos": "Quispe", "documento_identidad": dni, "fecha_ingreso": "2026-10-01", "sueldo_basico": 2000}


def test_el_administrador_ve_todas_las_empresas_de_todas_las_cuentas(entorno, monkeypatch):
    cli, nueva = entorno
    monkeypatch.setenv("SETUP_TOKEN", "codigo-admin-de-prueba")
    a, b, ea, eb = _dos_cuentas(cli, nueva)
    solo_a = cli.get(f"{API}/empresas", headers=KEY(a)).json()
    assert [e["razon_social"] for e in solo_a] == ["EMPRESA DE A SAC"] and solo_a[0]["cuenta"] is None      # una cuenta no ve el nombre de otras
    todas = cli.get(f"{API}/empresas", headers=ADM).json()
    assert {(e["razon_social"], e["cuenta"]) for e in todas} == {("EMPRESA DE A SAC", "Estudio A"), ("EMPRESA DE B SAC", "Estudio B")}
    # puede trabajar en la empresa de cualquier cuenta
    assert cli.post(f"{API}/empresas/{eb}/empleados", headers=ADM, json=_emp()).status_code == 201
    assert len(cli.get(f"{API}/empresas/{eb}/empleados", headers=ADM).json()) == 1
    # ... pero respeta el plan de la cuenta dueña (B permite 1 trabajador)
    assert cli.post(f"{API}/empresas/{eb}/empleados", headers=ADM, json=_emp("40123456")).status_code == 403
    # y una cuenta común sigue sin ver la empresa ajena
    assert cli.get(f"{API}/empresas/{eb}/empleados", headers=KEY(a)).status_code == 404


def test_el_administrador_crea_empresas_y_cuentas(entorno, monkeypatch):
    cli, nueva = entorno
    monkeypatch.setenv("SETUP_TOKEN", "codigo-admin-de-prueba")
    a, b, ea, eb = _dos_cuentas(cli, nueva)
    datos = {"ruc": "20333333333", "razon_social": "Tercera SAC"}
    assert cli.post(f"{API}/empresas", json=datos, headers=ADM).status_code == 422                          # debe elegir cuenta
    cuentas = {c["nombre"]: c for c in cli.get(f"{API}/admin/cuentas", headers=ADM).json()}
    assert cuentas["Estudio A"]["empresas"] == 1 and cuentas["Estudio B"]["max_empresas"] == 1
    assert cli.post(f"{API}/empresas", json={**datos, "cuenta_id": cuentas["Estudio B"]["id"]}, headers=ADM).status_code == 403   # B ya tiene su 1 empresa
    ok = cli.post(f"{API}/empresas", json={**datos, "cuenta_id": cuentas["Estudio A"]["id"]}, headers=ADM)
    assert ok.status_code == 201 and ok.json()["cuenta_id"] == cuentas["Estudio A"]["id"]
    assert len(cli.get(f"{API}/empresas", headers=KEY(a)).json()) == 2                                       # A ya ve la nueva
    # crear una cuenta nueva devuelve la clave una sola vez y esa clave funciona
    nueva_c = cli.post(f"{API}/admin/cuentas", json={"nombre": "Cliente Libre", "plan": "FREELANCER"}, headers=ADM).json()
    assert nueva_c["api_key"].startswith("pa_") and cli.get(f"{API}/empresas", headers=KEY(nueva_c["api_key"])).json() == []
    # el administrador puede bloquear una cuenta
    assert cli.patch(f"{API}/admin/cuentas/{nueva_c['id']}", json={"activa": False}, headers=ADM).status_code == 200
    assert cli.get(f"{API}/empresas", headers=KEY(nueva_c["api_key"])).status_code == 401


def test_acceso_de_administrador_protegido(entorno, monkeypatch):
    cli, nueva = entorno
    a, b, ea, eb = _dos_cuentas(cli, nueva)
    monkeypatch.delenv("SETUP_TOKEN", raising=False)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert cli.get(f"{API}/empresas", headers=ADM).status_code == 401                                         # sin código configurado no hay administrador
    monkeypatch.setenv("SETUP_TOKEN", "codigo-admin-de-prueba")
    assert cli.get(f"{API}/empresas", headers={"X-Admin-Token": "otro"}).status_code == 401                    # código incorrecto
    assert cli.get(f"{API}/admin/cuentas", headers=KEY(a)).status_code == 403                                  # una cuenta no es administrador
    assert cli.get(f"{API}/admin/cuentas").status_code == 401
    # ADMIN_TOKEN tiene prioridad sobre SETUP_TOKEN
    monkeypatch.setenv("ADMIN_TOKEN", "solo-el-admin")
    assert cli.get(f"{API}/empresas", headers=ADM).status_code == 401
    assert cli.get(f"{API}/empresas", headers={"X-Admin-Token": "solo-el-admin"}).status_code == 200
    # el administrador no puede usar rutas que requieren elegir una empresa sin indicarla (no existe la ruta): 404 de empresa
    assert cli.get(f"{API}/empresas/999999/empleados", headers={"X-Admin-Token": "solo-el-admin"}).status_code == 404


def test_f19_clientes_vigencia_renovacion_y_solo_lectura(entorno, monkeypatch):
    from datetime import date, timedelta
    cli, nueva = entorno
    monkeypatch.setenv("SETUP_TOKEN", "codigo-admin-de-prueba")
    a, b, ea, eb = _dos_cuentas(cli, nueva)
    t = cli.get(f"{API}/admin/clientes", headers=ADM).json()
    assert t["total"] == 2 and t["resumen"]["SIN_VENCIMIENTO"] == 2
    ida = next(f["id"] for f in t["items"] if f["nombre"] == "Estudio A")
    assert cli.get(f"{API}/admin/clientes?q=20222222222", headers=ADM).json()["total"] == 1          # búsqueda por RUC
    # vence hace 3 días → en gracia (todo normal); hace 10 → vencida (solo lectura)
    cli.patch(f"{API}/admin/cuentas/{ida}", headers=ADM, json={"vence_el": str(date.today() - timedelta(days=3))})
    assert cli.get(f"{API}/admin/clientes?estado=EN_GRACIA", headers=ADM).json()["total"] == 1
    assert cli.post(f"{API}/empresas/{ea}/empleados", headers=KEY(a), json=_emp()).status_code == 201
    cli.patch(f"{API}/admin/cuentas/{ida}", headers=ADM, json={"vence_el": str(date.today() - timedelta(days=10))})
    assert cli.post(f"{API}/empresas/{ea}/empleados", headers=KEY(a), json=_emp("40123456")).status_code == 402
    assert cli.get(f"{API}/empresas/{ea}/empleados", headers=KEY(a)).status_code == 200              # consultar sí
    # renovar 1 mes desde hoy (ya había vencido) → vuelve a vigente y se guarda el pago
    r = cli.post(f"{API}/admin/cuentas/{ida}/renovar", headers=ADM, json={"meses": 1, "monto": 49.9, "referencia": "OP-123"}).json()
    assert r["estado"] in ("VIGENTE", "POR_VENCER") and r["vence_el"] > str(date.today())
    assert cli.post(f"{API}/empresas/{ea}/empleados", headers=KEY(a), json=_emp("40123456")).status_code == 201
    d = cli.get(f"{API}/admin/cuentas/{ida}/detalle", headers=ADM).json()
    assert d["pagos"][0]["referencia"] == "OP-123" and d["empresas"][0]["ruc"] == "20111111111"
    # masivo: suspender y reactivar
    assert cli.post(f"{API}/admin/cuentas/masivo", headers=ADM, json={"ids": [ida], "accion": "SUSPENDER"}).json()["cuentas"] == 1
    assert cli.get(f"{API}/admin/clientes?estado=SUSPENDIDA", headers=ADM).json()["total"] == 1
    assert cli.get(f"{API}/admin/clientes/exportar", headers=ADM).status_code == 200
    assert cli.get(f"{API}/admin/clientes", headers=KEY(b)).status_code == 403                       # una cuenta no ve la tabla del administrador


def test_f20_catalogo_de_planes_oferta_y_precio_con_adicionales(entorno, monkeypatch):
    cli, nueva = entorno
    monkeypatch.setenv("SETUP_TOKEN", "codigo-admin-de-prueba")
    a = nueva("Estudio A", max_empresas=1)
    of = cli.get(f"{API}/planes/oferta", headers=KEY(a)).json()
    cods = [p["codigo"] for p in of["planes"]]
    assert cods == ["GRATIS", "INICIO", "ESTUDIO", "PROFESIONAL", "EMPRESARIAL"]           # FREELANCER (antiguo) no se ofrece
    pro = next(p for p in of["planes"] if p["codigo"] == "PROFESIONAL")
    assert (pro["precio_mensual"], pro["max_empresas"], pro["max_trabajadores"]) == (59, 3, 100) and pro["precio_anual"] == 601.8   # 59×12×0.85
    assert of["igv_incluido"] is False and of["cuenta"]["estado"] == "SIN_VENCIMIENTO"
    # el administrador edita precio y WhatsApp
    assert cli.put(f"{API}/admin/planes/INICIO", headers=ADM, json={"precio": 31}).status_code == 200
    assert cli.put(f"{API}/admin/config-comercial", headers=ADM, json={"whatsapp": "+51 999-888-777"}).status_code == 200
    of = cli.get(f"{API}/planes/oferta", headers=KEY(a)).json()
    assert next(p for p in of["planes"] if p["codigo"] == "INICIO")["precio_mensual"] == 31 and of["config"]["whatsapp"] == "+51999888777"
    assert cli.put(f"{API}/admin/planes/INICIO", headers=KEY(a), json={"precio": 1}).status_code == 403
    # precio con adicionales: plan ESTUDIO (3 RUC × 50) con 4 RUC y 60 trab. por RUC → +10 por RUC y +0.60 × 10 × 4
    cid = cli.get(f"{API}/admin/clientes", headers=ADM).json()["items"][0]["id"]
    cli.post(f"{API}/admin/cuentas/masivo", headers=ADM, json={"ids": [cid], "accion": "CAMBIAR_PLAN", "plan": "ESTUDIO"})
    cli.patch(f"{API}/admin/cuentas/{cid}", headers=ADM, json={"max_empresas": 4, "max_trabajadores": 60})
    assert cli.get(f"{API}/admin/clientes", headers=ADM).json()["items"][0]["precio_mensual"] == 39 + 10 + 24


def test_f22_gratis_20_activos_y_post_planilla_solo_con_plan(entorno, monkeypatch):
    cli, _ = entorno
    monkeypatch.setenv("SETUP_TOKEN", "codigo-admin-de-prueba")
    gr = cli.post(f"{API}/admin/cuentas", headers=ADM, json={"nombre": "Libre", "plan": "GRATIS"}).json()
    pa = cli.post(f"{API}/admin/cuentas", headers=ADM, json={"nombre": "Pago", "plan": "INICIO"}).json()
    lim = {c["nombre"]: c["max_trabajadores"] for c in cli.get(f"{API}/admin/cuentas", headers=ADM).json()}
    assert lim["Libre"] == 20 and lim["Pago"] == 50
    for k, esperado in ((gr["api_key"], 403), (pa["api_key"], 200)):
        h = {"X-API-Key": k}
        eid = cli.post(f"{API}/empresas", headers=h, json={"ruc": "20111111111", "razon_social": "X SAC"}).json()["id"]
        assert cli.get(f"{API}/empresas/{eid}/config/plame", headers=h).status_code == esperado
        assert cli.get(f"{API}/empresas/{eid}/config/cuentas", headers=h).status_code == esperado
        assert cli.get(f"{API}/empresas/{eid}/planillas/1/asiento", headers=h).status_code in ((403,) if esperado == 403 else (200, 404, 409, 422))
        assert cli.get(f"{API}/empresas/{eid}/empleados", headers=h).status_code == 200                # lo demás sigue igual
