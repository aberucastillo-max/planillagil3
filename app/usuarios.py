"""Usuarios, contraseñas y sesiones. Sin dependencias nuevas: scrypt (hashlib) y tokens aleatorios guardados con hash."""
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
import urllib.request
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from .models import Auditoria, ResetClave, Sesion, Usuario

log = logging.getLogger("planilla")

ROLES = ("PROPIETARIO", "OPERADOR", "CONSULTA")
MAX_INTENTOS = 5                    # intentos fallidos seguidos antes de bloquear la cuenta
BLOQUEO_MIN = 15
SESION_MAX_H = 12                   # duración máxima de una sesión
SESION_INACTIVA_H = 2               # se cierra sola tras este tiempo sin usarla
RESET_MIN = 60
CLAVE_MIN = 10
_N, _R, _P = 2 ** 14, 8, 1
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def hash_token(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


# ------------------------------------------------------------------ contraseñas
def hash_clave(clave: str) -> str:
    sal = os.urandom(16)
    h = hashlib.scrypt(clave.encode(), salt=sal, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(sal).decode()}${base64.b64encode(h).decode()}"


def verificar_clave(clave: str, guardado: str) -> bool:
    try:
        _, n, r, p, sal, h = guardado.split("$")
        calc = hashlib.scrypt(clave.encode(), salt=base64.b64decode(sal), n=int(n), r=int(r), p=int(p), dklen=32)
        return secrets.compare_digest(calc, base64.b64decode(h))
    except Exception:
        return False


_FALSO = hash_clave("clave-falsa-para-igualar-tiempos")


def validar_clave_nueva(clave: str, email: str = "") -> str:
    if len(clave or "") < CLAVE_MIN:
        return f"La contraseña debe tener al menos {CLAVE_MIN} caracteres."
    if not re.search(r"[A-Za-z]", clave) or not re.search(r"\d", clave):
        return "La contraseña debe combinar letras y números."
    if email and clave.lower() == email.lower():
        return "La contraseña no puede ser igual al correo."
    return ""


def clave_temporal() -> str:
    alfabeto = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alfabeto) for _ in range(12)) + "7a"


def email_valido(e: str) -> bool:
    return bool(_EMAIL.match(e or "")) and len(e) <= 150


# ------------------------------------------------------------------ límite por IP (en memoria, por instancia)
_INTENTOS_IP = defaultdict(deque)


def limite_ip(ip: str, max_: int = 20, ventana_s: int = 600) -> bool:
    """True si la IP ya superó los intentos permitidos en la ventana."""
    q, t = _INTENTOS_IP[ip], time.time()
    while q and t - q[0] > ventana_s:
        q.popleft()
    if len(q) >= max_:
        return True
    q.append(t)
    return False


# ------------------------------------------------------------------ inicio y fin de sesión
class ErrorAcceso(Exception):
    def __init__(self, msg, status=401):
        super().__init__(msg)
        self.status = status


def iniciar_sesion(db: Session, email: str, clave: str, ip: str = "") -> tuple:
    email = (email or "").strip().lower()
    u = db.query(Usuario).filter(Usuario.email == email).first()
    if u and u.bloqueado_hasta and aware(u.bloqueado_hasta) > ahora():
        mins = int((aware(u.bloqueado_hasta) - ahora()).total_seconds() // 60) + 1
        raise ErrorAcceso(f"Cuenta bloqueada por intentos fallidos. Vuelva a intentar en {mins} min o recupere su contraseña.", 423)
    ok = verificar_clave(clave or "", u.clave_hash if u else _FALSO)
    if not u or not ok or not u.activo or not u.cuenta_activa:
        if u:
            u.intentos_fallidos = (u.intentos_fallidos or 0) + 1
            if u.intentos_fallidos >= MAX_INTENTOS:
                u.bloqueado_hasta = ahora() + timedelta(minutes=BLOQUEO_MIN)
                u.intentos_fallidos = 0
            db.add(Auditoria(cuenta_id=u.cuenta_id, accion="LOGIN_FALLIDO", detalle=f"{email} {ip}"))
            db.commit()
        raise ErrorAcceso("Correo o contraseña incorrectos.")
    u.intentos_fallidos, u.bloqueado_hasta, u.ultimo_acceso = 0, None, ahora()
    token = "ps_" + secrets.token_urlsafe(32)
    exp = ahora() + timedelta(hours=SESION_MAX_H)
    db.add(Sesion(usuario_id=u.id, token_hash=hash_token(token), expira=exp))
    db.add(Auditoria(cuenta_id=u.cuenta_id, accion="LOGIN", detalle=f"{email} {ip}"))
    db.commit()
    return token, exp, u


def usuario_de_token(db: Session, token: str):
    """Devuelve el usuario de una sesión vigente (y la renueva el uso) o None."""
    s = db.query(Sesion).filter(Sesion.token_hash == hash_token(token)).first()
    if not s:
        return None
    t = ahora()
    if aware(s.expira) <= t or t - aware(s.ultimo_uso) > timedelta(hours=SESION_INACTIVA_H):
        db.delete(s)
        db.commit()
        return None
    u = db.get(Usuario, s.usuario_id)
    if not u or not u.activo:
        return None
    if t - aware(s.ultimo_uso) > timedelta(minutes=5):
        s.ultimo_uso = t
        db.commit()
    return u


def cerrar_sesion(db: Session, token: str):
    s = db.query(Sesion).filter(Sesion.token_hash == hash_token(token)).first()
    if s:
        db.delete(s)
        db.commit()


def cerrar_todas(db: Session, usuario_id: int):
    db.query(Sesion).filter(Sesion.usuario_id == usuario_id).delete()


# ------------------------------------------------------------------ recuperación por correo
def crear_reset(db: Session, u: Usuario) -> str:
    token = secrets.token_urlsafe(32)
    db.add(ResetClave(usuario_id=u.id, token_hash=hash_token(token), expira=ahora() + timedelta(minutes=RESET_MIN)))
    db.commit()
    return token


def canjear_reset(db: Session, token: str, nueva: str):
    r = db.query(ResetClave).filter(ResetClave.token_hash == hash_token(token or "")).first()
    if not r or r.usado or aware(r.expira) <= ahora():
        raise ErrorAcceso("El enlace no es válido o ya venció. Pida uno nuevo.", 400)
    u = db.get(Usuario, r.usuario_id)
    msg = validar_clave_nueva(nueva, u.email)
    if msg:
        raise ErrorAcceso(msg, 422)
    u.clave_hash, u.debe_cambiar_clave, u.intentos_fallidos, u.bloqueado_hasta = hash_clave(nueva), False, 0, None
    r.usado = True
    cerrar_todas(db, u.id)
    db.add(Auditoria(cuenta_id=u.cuenta_id, accion="CLAVE_RESTABLECIDA", detalle=u.email))
    db.commit()


def correo_configurado() -> bool:
    return bool(os.getenv("RESEND_API_KEY") and os.getenv("MAIL_FROM") and os.getenv("FRONT_URL"))


def enviar_correo(destino: str, asunto: str, html: str) -> bool:
    """Envía por Resend (https://resend.com). Si no está configurado, no envía y devuelve False."""
    if not correo_configurado():
        return False
    try:
        req = urllib.request.Request("https://api.resend.com/emails", method="POST",
                                     data=json.dumps({"from": os.environ["MAIL_FROM"], "to": [destino], "subject": asunto, "html": html}).encode(),
                                     headers={"Authorization": "Bearer " + os.environ["RESEND_API_KEY"], "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
        return True
    except Exception as e:      # el aviso al usuario es genérico; el detalle queda en el registro del servidor
        log.warning("No se pudo enviar el correo: %s", e)
        return False


def enlace_reset(token: str) -> str:
    return os.environ["FRONT_URL"].rstrip("/") + "/?restablecer=" + token
