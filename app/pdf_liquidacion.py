"""PDF de la liquidación por cese (un PDF por liquidación).
Contenido de la propuesta: empresa y RUC, datos del colaborador, fechas de ingreso y cese, tiempo de servicio, motivo,
detalle de conceptos con sus montos, descuentos y neto. Opciones (por confirmar en la propuesta): fórmulas y firmas.
Una liquidación ABIERTA sale marcada como BORRADOR."""
import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

MOTIVOS = {"01": "Renuncia", "02": "Despido", "03": "Mutuo disenso", "04": "Vencimiento de contrato",
           "05": "Invalidez absoluta", "06": "Jubilación", "07": "Fallecimiento", "08": "Cese colectivo"}
CLASES = {"JUSTIFICADO": "justificado", "ARBITRARIO": "arbitrario", "NULO": "nulo"}


def _s(monto) -> str:
    return f"{Decimal(monto):,.2f}"


def _fecha(f) -> str:
    return f"{f.day:02d}/{f.month:02d}/{f.year}" if hasattr(f, "day") else str(f)


def _iso(texto: str) -> str:
    partes = str(texto).split("-")
    return f"{partes[2]}/{partes[1]}/{partes[0]}" if len(partes) == 3 else str(texto)


def generar(empresa, liq, detalle, descripciones, con_formulas=True, con_firmas=False) -> bytes:
    snap = liq.snapshot or {}
    st = getSampleStyleSheet()
    pequeno = ParagraphStyle("p", parent=st["BodyText"], fontSize=8, leading=10, textColor=colors.HexColor("#444444"))
    normal = ParagraphStyle("n", parent=st["BodyText"], fontSize=9, leading=11)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                            bottomMargin=16 * mm, title="Liquidación de beneficios sociales")
    el = []
    if liq.estado != "CERRADA":
        el.append(Paragraph("<font color='#B45309'><b>BORRADOR · liquidación abierta, no es un documento definitivo</b></font>", normal))
        el.append(Spacer(1, 4))
    el.append(Paragraph("<b>LIQUIDACIÓN DE BENEFICIOS SOCIALES</b>", ParagraphStyle("t", parent=st["Title"], fontSize=15)))
    el.append(Paragraph(f"{empresa.razon_social} · RUC {empresa.ruc}", normal))
    el.append(Spacer(1, 6))

    motivo = MOTIVOS.get(liq.motivo_cese or "", liq.motivo_cese or "—")
    if liq.clase_despido:
        motivo += f" ({CLASES.get(liq.clase_despido, liq.clase_despido)})"
    datos = [
        ["Colaborador", snap.get("colaborador", "")], ["Documento", snap.get("documento", "")],
        ["Puesto", snap.get("puesto") or "—"], ["Fecha de ingreso", _iso(snap.get("fecha_ingreso", ""))],
        ["Fecha de cese", _fecha(liq.fecha_cese)], ["Tiempo de servicio", snap.get("tiempo_servicio", "")],
        ["Motivo de cese", motivo], ["Remuneración computable", f"S/ {_s(snap.get('remuneracion_computable', 0))}"],
    ]
    t = Table(datos, colWidths=[45 * mm, 125 * mm])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555555")),
                           ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD")),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    el += [t, Spacer(1, 8)]

    def bloque(titulo, categoria, signo=""):
        filas = [d for d in detalle if d.categoria == categoria]
        if not filas:
            return
        cab = ["Concepto", "Cálculo", "Monto (S/)"] if con_formulas else ["Concepto", "Monto (S/)"]
        data = [cab]
        for d in filas:
            nombre = descripciones.get(d.concepto_codigo, d.concepto_codigo)
            if con_formulas:
                data.append([Paragraph(nombre, normal), Paragraph(d.formula or "", pequeno), signo + _s(d.monto)])
            else:
                data.append([Paragraph(nombre, normal), signo + _s(d.monto)])
        anchos = [55 * mm, 90 * mm, 25 * mm] if con_formulas else [135 * mm, 35 * mm]
        tb = Table(data, colWidths=anchos, repeatRows=1)
        tb.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EAF6")), ("FONTSIZE", (0, 0), (-1, 0), 9),
                                ("ALIGN", (-1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD"))]))
        el.extend([Paragraph(f"<b>{titulo}</b>", normal), Spacer(1, 2), tb, Spacer(1, 6)])

    bloque("INGRESOS", "INGRESO")
    bloque("DESCUENTOS", "DESCUENTO")
    tot = Table([["Total ingresos", _s(liq.total_ingresos)], ["Total descuentos", _s(liq.total_descuentos)],
                 ["NETO A PAGAR (S/)", _s(liq.neto_a_pagar)]], colWidths=[135 * mm, 35 * mm])
    tot.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
                             ("FONTSIZE", (0, 2), (-1, 2), 11), ("LINEABOVE", (0, 2), (-1, 2), 0.8, colors.black)]))
    el.append(tot)
    if liq.avisos:
        el += [Spacer(1, 8), Paragraph("<b>Observaciones</b>", normal)] + [Paragraph("• " + a, pequeno) for a in liq.avisos]
    if float(liq.ret_5ta_ajuste or 0) < 0:
        el.append(Paragraph("• El descuento de quinta categoría es negativo: es la devolución del exceso retenido.", pequeno))
    if con_firmas:
        firmas = Table([["", ""], ["Empleador", "Colaborador (recibí conforme)"]], colWidths=[85 * mm, 85 * mm], rowHeights=[22 * mm, 6 * mm])
        firmas.setStyle(TableStyle([("LINEABOVE", (0, 1), (-1, 1), 0.5, colors.black), ("ALIGN", (0, 1), (-1, 1), "CENTER"),
                                    ("FONTSIZE", (0, 1), (-1, 1), 8)]))
        el += [Spacer(1, 18), firmas]
    doc.build(el)
    return buf.getvalue()
