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
