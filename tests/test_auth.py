"""Login: usuarios, sesiones, bloqueo, roles y recuperación."""
import pytest

from app import usuarios as U
from app.db import SessionLocal
from app.models import ResetClave, Usuario

H = lambda k: {"X-API-Key": k}

API = "/api/v1"


def _admin(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "adm-123")
    return {"X-Admin-Token": "adm-123"}


def _cuenta_con_propietario(cli, monkeypatch, email="dueno@x.pe"):
    adm = _admin(monkeypatch)
    cid = cli.post(f"{API}/admin/cuentas", headers=adm, json={"nombre": "Estudio", "plan": "GRATIS"}).json()["id"]
    r = cli.post(f"{API}/admin/cuentas/{cid}/usuarios", headers=adm, json={"email": email, "nombre": "Dueño", "rol": "PROPIETARIO"})
    assert r.status_code == 201 and r.json()["clave_temporal"]
    return cid, r.json()["clave_temporal"]


def _login(cli, email, clave):
    return cli.post(f"{API}/auth/login", json={"email": email, "clave": clave})


def _sesion(cli, email, temp, nueva="Segura2026ok"):
    t = _login(cli, email, temp).json()["token"]
    r = cli.post(f"{API}/auth/cambiar-clave", headers={"Authorization": "Bearer " + t}, json={"actual": temp, "nueva": nueva})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_hash_y_politica_de_clave():
    h = U.hash_clave("Abc1234567")
    assert h.startswith("scrypt$") and U.verificar_clave("Abc1234567", h) and not U.verificar_clave("otra", h)
    assert U.validar_clave_nueva("corta1") and U.validar_clave_nueva("sololetrasaaaa") and U.validar_clave_nueva("1234567890123")
    assert not U.validar_clave_nueva("Segura2026ok", "a@b.pe")


def test_login_cambio_obligatorio_y_uso(entorno, monkeypatch):
    cli, _ = entorno
    cid, temp = _cuenta_con_propietario(cli, monkeypatch)
    assert _login(cli, "dueno@x.pe", "mala").status_code == 401
    ok = _login(cli, "DUENO@x.pe", temp).json()
    assert ok["debe_cambiar_clave"] and ok["usuario"]["rol"] == "PROPIETARIO"
    t = {"Authorization": "Bearer " + ok["token"]}
    # con clave temporal solo puede cambiarla
    assert cli.get(f"{API}/empresas", headers=t).status_code == 403
    assert cli.post(f"{API}/auth/cambiar-clave", headers=t, json={"actual": temp, "nueva": "corta"}).status_code == 422
    h = _sesion(cli, "dueno@x.pe", temp)
    assert cli.get(f"{API}/empresas", headers=h).status_code == 200
    assert cli.get(f"{API}/auth/yo", headers=h).json()["usuario"]["email"] == "dueno@x.pe"
    assert cli.get(f"{API}/empresas", headers=t).status_code == 401          # la sesión anterior se cerró al cambiar la clave
    assert cli.post(f"{API}/auth/logout", headers=h).status_code == 200
    assert cli.get(f"{API}/empresas", headers=h).status_code == 401


def test_bloqueo_por_intentos(entorno, monkeypatch):
    cli, _ = entorno
    _, temp = _cuenta_con_propietario(cli, monkeypatch, "b@x.pe")
    U._INTENTOS_IP.clear()
    for _i in range(U.MAX_INTENTOS):
        assert _login(cli, "b@x.pe", "incorrecta1").status_code == 401
    r = _login(cli, "b@x.pe", temp)                      # aun con la clave correcta
    assert r.status_code == 423 and "bloqueada" in r.json()["detail"].lower()


def test_roles_y_gestion_de_usuarios(entorno, monkeypatch):
    cli, _ = entorno
    cid, temp = _cuenta_con_propietario(cli, monkeypatch, "p@x.pe")
    hp = _sesion(cli, "p@x.pe", temp)
    r = cli.post(f"{API}/usuarios", headers=hp, json={"email": "op@x.pe", "nombre": "Operador", "rol": "OPERADOR"})
    r2 = cli.post(f"{API}/usuarios", headers=hp, json={"email": "con@x.pe", "nombre": "Consulta", "rol": "CONSULTA"})
    assert r.status_code == 201 and r2.status_code == 201
    assert cli.post(f"{API}/usuarios", headers=hp, json={"email": "op@x.pe", "nombre": "Dup", "rol": "OPERADOR"}).status_code == 409
    hop = _sesion(cli, "op@x.pe", r.json()["clave_temporal"])
    hcon = _sesion(cli, "con@x.pe", r2.json()["clave_temporal"])
    # el operador trabaja pero no gestiona usuarios; consulta solo lee
    assert cli.get(f"{API}/usuarios", headers=hop).status_code == 403
    assert cli.get(f"{API}/empresas", headers=hcon).status_code == 200
    assert cli.post(f"{API}/empresas", headers=hcon, json={"ruc": "20100070970", "razon_social": "X"}).status_code == 403
    # el último propietario no puede quitarse ni desactivarse
    uid = next(u["id"] for u in cli.get(f"{API}/usuarios", headers=hp).json() if u["email"] == "p@x.pe")
    assert cli.put(f"{API}/usuarios/{uid}", headers=hp, json={"activo": False}).status_code == 409
    assert cli.put(f"{API}/usuarios/{uid}", headers=hp, json={"rol": "OPERADOR"}).status_code == 409
    # desactivar al operador corta su sesión
    oid = next(u["id"] for u in cli.get(f"{API}/usuarios", headers=hp).json() if u["email"] == "op@x.pe")
    assert cli.put(f"{API}/usuarios/{oid}", headers=hp, json={"activo": False}).status_code == 200
    assert cli.get(f"{API}/empresas", headers=hop).status_code == 401
    # clave temporal nueva reactiva el acceso (si está activo)
    cli.put(f"{API}/usuarios/{oid}", headers=hp, json={"activo": True})
    t2 = cli.post(f"{API}/usuarios/{oid}/clave-temporal", headers=hp).json()["clave_temporal"]
    assert _login(cli, "op@x.pe", t2).status_code == 200


def test_aislamiento_entre_cuentas(entorno, monkeypatch):
    cli, _ = entorno
    _, t1 = _cuenta_con_propietario(cli, monkeypatch, "u1@x.pe")
    adm = _admin(monkeypatch)
    c2 = cli.post(f"{API}/admin/cuentas", headers=adm, json={"nombre": "Otra", "plan": "GRATIS"}).json()["id"]
    t2 = cli.post(f"{API}/admin/cuentas/{c2}/usuarios", headers=adm, json={"email": "u2@x.pe", "nombre": "U2", "rol": "PROPIETARIO"}).json()["clave_temporal"]
    h1, h2 = _sesion(cli, "u1@x.pe", t1), _sesion(cli, "u2@x.pe", t2)
    assert [u["email"] for u in cli.get(f"{API}/usuarios", headers=h1).json()] == ["u1@x.pe"]
    assert [u["email"] for u in cli.get(f"{API}/usuarios", headers=h2).json()] == ["u2@x.pe"]


def test_recuperacion_por_enlace(entorno, monkeypatch):
    cli, _ = entorno
    _, temp = _cuenta_con_propietario(cli, monkeypatch, "r@x.pe")
    enviados = []
    monkeypatch.setenv("RESEND_API_KEY", "k"); monkeypatch.setenv("MAIL_FROM", "a@b.pe"); monkeypatch.setenv("FRONT_URL", "https://app.pe")
    monkeypatch.setattr(U, "enviar_correo", lambda d, a, h: enviados.append((d, h)) or True)
    U._INTENTOS_IP.clear()
    r1 = cli.post(f"{API}/auth/olvide", json={"email": "r@x.pe"}).json()
    r2 = cli.post(f"{API}/auth/olvide", json={"email": "noexiste@x.pe"}).json()
    assert r1 == r2 and len(enviados) == 1                                   # misma respuesta exista o no
    enlace = enviados[0][1].split('href="')[1].split('"')[0]
    token = enlace.split("restablecer=")[1]
    assert cli.post(f"{API}/auth/restablecer", json={"token": token, "nueva": "corta"}).status_code == 422
    assert cli.post(f"{API}/auth/restablecer", json={"token": token, "nueva": "NuevaClave2026"}).status_code == 200
    assert cli.post(f"{API}/auth/restablecer", json={"token": token, "nueva": "OtraClave2026x"}).status_code == 400   # un solo uso
    assert _login(cli, "r@x.pe", "NuevaClave2026").status_code == 200 and _login(cli, "r@x.pe", temp).status_code == 401


def test_clave_api_y_admin_siguen_funcionando(entorno):
    cli, nueva = entorno
    clave = nueva("Cuenta API")
    assert cli.get(f"{API}/empresas", headers=H(clave)).status_code == 200


def test_regimenes_segun_plan(entorno, monkeypatch):
    cli, nueva = entorno
    adm = _admin(monkeypatch)
    # plan gratuito: solo Régimen General
    g = cli.post(f"{API}/admin/cuentas", headers=adm, json={"nombre": "Gratis", "plan": "GRATIS"}).json()["api_key"]
    r = cli.get(f"{API}/regimenes", headers=H(g)).json()
    por = {x["codigo"]: x for x in r["regimenes"]}
    assert r["plan"] == "GRATIS" and por["RG_728"]["disponible"] and not por["MYPE_MICRO"]["permitido_por_plan"] and "plan superior" in por["MYPE_MICRO"]["motivo"]
    eid = cli.post(f"{API}/empresas", headers=H(g), json={"ruc": "20100070970", "razon_social": "Demo SAC"}).json()["id"]
    base = {"nombres": "Ana", "apellidos": "Ruiz", "documento_identidad": "45678912", "fecha_ingreso": "2026-01-02", "sueldo_basico": 2000, "sistema_pensionario": "ONP"}
    x = cli.post(f"{API}/empresas/{eid}/empleados", headers=H(g), json={**base, "regimen": "MYPE_MICRO"})
    assert x.status_code == 403 and "plan" in x.json()["detail"]
    assert cli.post(f"{API}/empresas/{eid}/empleados", headers=H(g), json={**base, "regimen": "XX"}).status_code == 422
    assert cli.post(f"{API}/empresas/{eid}/empleados", headers=H(g), json=base).status_code == 201     # sin régimen = General
    # plan de pago: el régimen está permitido, pero sus reglas aún no están cargadas
    p = cli.post(f"{API}/admin/cuentas", headers=adm, json={"nombre": "Pago", "plan": "FREELANCER"}).json()["api_key"]
    por = {x["codigo"]: x for x in cli.get(f"{API}/regimenes", headers=H(p)).json()["regimenes"]}
    assert por["MYPE_MICRO"]["permitido_por_plan"] and not por["MYPE_MICRO"]["disponible"] and "Próximamente" in por["MYPE_MICRO"]["motivo"]
    e2 = cli.post(f"{API}/empresas", headers=H(p), json={"ruc": "20100070970", "razon_social": "Otra SAC"}).json()["id"]
    y = cli.post(f"{API}/empresas/{e2}/empleados", headers=H(p), json={**base, "regimen": "MYPE_PEQUENA"})
    assert y.status_code == 422 and "reglas" in y.json()["detail"]
