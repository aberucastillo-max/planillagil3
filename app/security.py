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
from .models import Cuenta, Empresa


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


def get_acceso(x_api_key: str = Header(None, alias="X-API-Key"), x_admin_token: str = Header(None, alias="X-Admin-Token"),
               db: Session = Depends(get_db)) -> Acceso:
    if x_admin_token:
        if not _admin_valido(x_admin_token):
            raise HTTPException(401, "Código de administrador incorrecto, o el modo administrador no está habilitado.")
        return Acceso(None, True)
    if not x_api_key:
        raise HTTPException(401, "Falta la cabecera X-API-Key.")
    cuenta = db.query(Cuenta).filter(Cuenta.api_key_hash == hash_key(x_api_key), Cuenta.activa.is_(True)).first()
    if not cuenta:
        raise HTTPException(401, "Credenciales incorrectas.")
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
