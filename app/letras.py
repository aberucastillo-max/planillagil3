"""Monto en letras para el recibo de la liquidación: 11752.09 -> ONCE MIL SETECIENTOS CINCUENTA Y DOS CON 09/100 SOLES."""
from decimal import ROUND_HALF_UP, Decimal

_U = ["", "UNO", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE", "DIEZ", "ONCE", "DOCE", "TRECE",
      "CATORCE", "QUINCE", "DIECISEIS", "DIECISIETE", "DIECIOCHO", "DIECINUEVE", "VEINTE", "VEINTIUNO", "VEINTIDOS",
      "VEINTITRES", "VEINTICUATRO", "VEINTICINCO", "VEINTISEIS", "VEINTISIETE", "VEINTIOCHO", "VEINTINUEVE"]
_D = {3: "TREINTA", 4: "CUARENTA", 5: "CINCUENTA", 6: "SESENTA", 7: "SETENTA", 8: "OCHENTA", 9: "NOVENTA"}
_C = {1: "CIENTO", 2: "DOSCIENTOS", 3: "TRESCIENTOS", 4: "CUATROCIENTOS", 5: "QUINIENTOS", 6: "SEISCIENTOS",
      7: "SETECIENTOS", 8: "OCHOCIENTOS", 9: "NOVECIENTOS"}


def _menor_mil(n: int) -> str:
    if n == 0:
        return ""
    if n == 100:
        return "CIEN"
    c, r = divmod(n, 100)
    partes = [_C[c]] if c else []
    if r < 30:
        if r:
            partes.append(_U[r])
    else:
        d, u = divmod(r, 10)
        partes.append(_D[d] + (" Y " + _U[u] if u else ""))
    return " ".join(partes)


def _apocope(texto: str) -> str:
    return texto[:-1] if texto.endswith("UNO") else texto            # UNO -> UN delante de MIL / MILLONES


def entero_en_letras(n: int) -> str:
    if n == 0:
        return "CERO"
    millones, resto = divmod(n, 1_000_000)
    miles, unidades = divmod(resto, 1000)
    partes = []
    if millones:
        partes.append("UN MILLON" if millones == 1 else _apocope(_menor_mil(millones)) + " MILLONES")
    if miles:
        partes.append("MIL" if miles == 1 else _apocope(_menor_mil(miles)) + " MIL")
    if unidades:
        partes.append(_menor_mil(unidades))
    return " ".join(partes)


def monto_en_letras(monto) -> str:
    q = Decimal(str(monto)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    entero = int(abs(q))
    centavos = int((abs(q) - entero) * 100)
    return f"{entero_en_letras(entero)} CON {centavos:02d}/100 SOLES"
