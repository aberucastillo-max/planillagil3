"""Utilidades de presentación compartidas por los PDF y el Excel."""
from datetime import date
from decimal import Decimal

MESES = ["", "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SETIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
MOTIVOS = {"01": "RENUNCIA VOLUNTARIA", "02": "DESPIDO", "03": "MUTUO DISENSO", "04": "VENCIMIENTO DE CONTRATO",
           "05": "INVALIDEZ ABSOLUTA", "06": "JUBILACION", "07": "FALLECIMIENTO", "08": "CESE COLECTIVO"}


def monto(x) -> str:
    return f"{Decimal(str(x)):,.2f}"


def a_fecha(x) -> date:
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def dmy(x) -> str:
    if x in (None, ""):
        return ""
    d = a_fecha(x)
    return f"{d.day:02d}/{d.month:02d}/{d.year}"


def fecha_larga(x) -> str:
    d = a_fecha(x)
    return f"LIMA, {d.day} DE {MESES[d.month]} DEL {d.year}"


def motivo_texto(codigo, clase=None) -> str:
    t = MOTIVOS.get(codigo or "", codigo or "—")
    if codigo == "02" and clase:
        t += " " + {"ARBITRARIO": "ARBITRARIO", "JUSTIFICADO": "JUSTIFICADO", "NULO": "NULO"}.get(clase, clase)
    return t


def regimen_pensiones(snap: dict) -> str:
    if snap.get("sistema_pensionario") == "AFP":
        t = f"*AFP {snap.get('afp') or ''}".strip()
        return t + (" (mixta)" if (snap.get("pension") or {}).get("tipo_comision") == "MIXTA" else "")
    return "*SNP (ONP 13%)"
