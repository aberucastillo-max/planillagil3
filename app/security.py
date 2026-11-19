"""Acceso: (1) cuenta con su clave X-API-Key; (2) ADMINISTRADOR con X-Admin-Token (acceso a todas las cuentas y RUC).
El código de administrador es ADMIN_TOKEN; si no existe se usa SETUP_TOKEN (etapa de pruebas)."""
import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from . import usuarios as U
from . import suscripcion as SUS
from .models import Cuenta, Empresa, Usuario


def hash_key(clave: str) -> str:
    return hashlib.sha256(clave.encode()).hexdigest()


def generar_api_key() -> str:
    return "pa_" + secrets.token_urlsafe(32)


def token_admin() -> str:
    return os.getenv("ADMIN_TOKEN") or os.getenv("SETUP_TOKEN") or ""


def _admin_valido(token: Optional[str]) -> bool:
    esperado = token_admin()
    return bool(esperado) and bool(token) and secrets.compare_digest(token.encode(), esperado.encode())


@dataclass
class Acceso:
    cuenta: Optional[Cuenta]
    admin: bool
    usuario: Optional[Usuario] = None
    token: str = ""


def _exigir_plan_vigente(cuenta, request: Request):
    """Plan vencido (pasada la gracia): solo lectura. Consultar y descargar siguen funcionando; no se calcula, cierra ni edita."""
    if cuenta is not None and request.method not in ("GET", "HEAD", "OPTIONS") and "/auth/" not in request.url.path and SUS.solo_lectura(cuenta):
        raise HTTPException(402, f"Su plan venció el {cuenta.vence_el:%d/%m/%Y}. Su información está a salvo y puede consultarla y descargarla; "
                                 "para seguir calculando o editando, renueve su plan con el administrador.")


def get_acceso(request: Request, x_api_key: str = Header(None, alias="X-API-Key"), x_admin_token: str = Header(None, alias="X-Admin-Token"),
               authorization: str = Header(None), db: Session = Depends(get_db)) -> Acceso:
    if authorization and authorization.lower().startswith("bearer ") and not x_admin_token:
        token = authorization[7:].strip()
        u = U.usuario_de_token(db, token)
        if not u or not u.cuenta_activa:
            raise HTTPException(401, "Su sesión venció. Inicie sesión de nuevo.")
        ruta = request.url.path
        if u.debe_cambiar_clave and "/auth/" not in ruta:
            raise HTTPException(403, "Debe cambiar su contraseña temporal antes de continuar.")
        if u.rol == "CONSULTA" and request.method not in ("GET", "HEAD", "OPTIONS") and "/auth/" not in ruta:
            raise HTTPException(403, "Su usuario es de solo consulta: no puede modificar datos.")
        _exigir_plan_vigente(u.cuenta, request)
        return Acceso(u.cuenta, False, u, token)
    if x_admin_token:
        if not _admin_valido(x_admin_token):
            raise HTTPException(401, "Código de administrador incorrecto, o el modo administrador no está habilitado.")
        return Acceso(None, True)
    if not x_api_key:
        raise HTTPException(401, "Falta la cabecera X-API-Key.")
    cuenta = db.query(Cuenta).filter(Cuenta.api_key_hash == hash_key(x_api_key), Cuenta.activa.is_(True)).first()
    if not cuenta:
        raise HTTPException(401, "Credenciales incorrectas.")
    _exigir_plan_vigente(cuenta, request)
    return Acceso(cuenta, False)


def get_cuenta(request: Request, acceso: Acceso = Depends(get_acceso), db: Session = Depends(get_db)) -> Cuenta:
    """Cuenta efectiva. Para el administrador es la dueña de la empresa sobre la que actúa (sus límites de plan se respetan)."""
    if not acceso.admin:
        return acceso.cuenta
    eid = request.path_params.get("empresa_id")
    if eid is None:
        raise HTTPException(400, "Elija una empresa para esta acción.")
    emp = db.get(Empresa, int(eid))
    if not emp:
        raise HTTPException(404, "Empresa no encontrada.")
    return db.get(Cuenta, emp.cuenta_id)


def exigir_propietario(acceso: Acceso = Depends(get_acceso)) -> Acceso:
    """Gestionar usuarios: el propietario de la cuenta (o el administrador). Una clave API de cuenta equivale a propietario."""
    if acceso.admin or acceso.usuario is None or acceso.usuario.rol == "PROPIETARIO":
        return acceso
    raise HTTPException(403, "Solo el propietario de la cuenta puede gestionar usuarios.")


def exigir_admin(acceso: Acceso = Depends(get_acceso)) -> Acceso:
    if not acceso.admin:
        raise HTTPException(403, "Solo el administrador puede hacer esto.")
    return acceso


def empresa_actual(empresa_id: int, cuenta: Cuenta = Depends(get_cuenta), db: Session = Depends(get_db)) -> Empresa:
    """Aísla los datos: una cuenta solo ve sus propias empresas (el administrador, las de la cuenta que elija)."""
    emp = db.query(Empresa).filter(Empresa.id == empresa_id, Empresa.cuenta_id == cuenta.id).first()
    if not emp:
        raise HTTPException(404, "Empresa no encontrada.")
    return emp
