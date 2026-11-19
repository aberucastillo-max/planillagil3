"""Vigencia de los planes (Planilla Ágil): fecha de corte por cuenta, días de gracia y solo lectura.

Reglas (decididas por el dueño):
  · Cada cuenta tiene su propia fecha de vencimiento (vence_el). Sin fecha = sin vencimiento (p. ej. plan GRATIS).
  · Aviso desde 15 días antes. Al vencer, 7 días de gracia con todo normal (gracia_dias, editable por cuenta).
  · Pasada la gracia: solo lectura (consultar y descargar), no calcular, cerrar ni editar. Nunca se borran datos.
  · Renovar suma el período desde la fecha de corte vigente (no se pierden días); si ya venció, desde hoy.
  · El administrador puede suspender (activa = False): sin acceso."""
import calendar
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from .models import Auditoria, Cuenta, Pago

AVISO_DIAS = 15
LIMITES_PLAN = {"GRATIS": (1, 10), "FREELANCER": (20, 10)}          # (max_empresas, max_trabajadores por empresa)
ESTADOS = ("VIGENTE", "POR_VENCER", "EN_GRACIA", "VENCIDA", "SUSPENDIDA", "SIN_VENCIMIENTO")


def estado(c: Cuenta, hoy: Optional[date] = None) -> str:
    hoy = hoy or date.today()
    if not c.activa:
        return "SUSPENDIDA"
    if c.vence_el is None:
        return "SIN_VENCIMIENTO"
    dias = (c.vence_el - hoy).days
    if dias > AVISO_DIAS:
        return "VIGENTE"
    if dias >= 0:
        return "POR_VENCER"
    if -dias <= (c.gracia_dias if c.gracia_dias is not None else 7):
        return "EN_GRACIA"
    return "VENCIDA"


def dias_restantes(c: Cuenta, hoy: Optional[date] = None) -> Optional[int]:
    return None if c.vence_el is None else (c.vence_el - (hoy or date.today())).days


def solo_lectura(c: Cuenta) -> bool:
    return estado(c) == "VENCIDA"


def sumar_meses(d: date, meses: int) -> date:
    m = d.month - 1 + meses
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def renovar(db: Session, c: Cuenta, meses: int, plan: Optional[str] = None, monto=None, referencia: Optional[str] = None,
            nota: Optional[str] = None, metodo: str = "MANUAL", hoy: Optional[date] = None) -> Pago:
    hoy = hoy or date.today()
    antes = c.vence_el
    base = antes if (antes is not None and antes >= hoy) else hoy
    c.vence_el = sumar_meses(base, meses)
    if plan and plan != c.plan:
        c.plan = plan
        if plan in LIMITES_PLAN:
            c.max_empresas, c.max_trabajadores = LIMITES_PLAN[plan]
    c.activa = True
    pago = Pago(cuenta_id=c.id, fecha=hoy, plan=c.plan, meses=meses, monto=None if monto in (None, "") else Decimal(str(monto)),
                metodo=metodo, referencia=(referencia or None), vence_antes=antes, vence_despues=c.vence_el, nota=(nota or None))
    db.add(pago)
    db.add(Auditoria(cuenta_id=c.id, accion="ADMIN_RENUEVA_PLAN", detalle=f"{meses} mes(es) · {c.plan} · vence {c.vence_el}"))
    return pago
