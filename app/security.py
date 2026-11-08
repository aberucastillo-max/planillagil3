"""Autenticación por API key POR CUENTA (se guarda solo el hash). Siguiente paso comercial: login + JWT."""
import hashlib
import secrets

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .db import get_db
from .models import Cuenta, Empresa


def hash_key(clave: str) -> str:
    return hashlib.sha256(clave.encode()).hexdigest()


def generar_api_key() -> str:
    return "pa_" + secrets.token_urlsafe(32)


def get_cuenta(x_api_key: str = Header(None, alias="X-API-Key"), db: Session = Depends(get_db)) -> Cuenta:
    if not x_api_key:
        raise HTTPException(401, "Falta la cabecera X-API-Key.")
    cuenta = db.query(Cuenta).filter(Cuenta.api_key_hash == hash_key(x_api_key), Cuenta.activa.is_(True)).first()
    if not cuenta:
        raise HTTPException(401, "Credenciales incorrectas.")
    return cuenta


def empresa_actual(empresa_id: int, cuenta: Cuenta = Depends(get_cuenta), db: Session = Depends(get_db)) -> Empresa:
    """Aísla los datos: una cuenta solo ve sus propias empresas."""
    emp = db.query(Empresa).filter(Empresa.id == empresa_id, Empresa.cuenta_id == cuenta.id).first()
    if not emp:
        raise HTTPException(404, "Empresa no encontrada.")
    return emp
