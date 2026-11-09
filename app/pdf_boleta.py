"""PDF de la BOLETA DE PAGO mensual, imitando el formato de la empresa (modelo entregado por el dueño):
dos copias por hoja (empleador y trabajador), cabecera de datos del legajo, ingresos / deducciones / aportes y totales."""
import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .formatos import dmy, monto

W = 182 * mm

# nombres cortos como en la boleta de la empresa
NOMBRES = {"@HABER_BASICO": "REMUNERACIÓN", "@ASIGNACION_FAMILIAR": "ASIGNACION FAMILIAR", "@HORAS_EXTRAS_25": "HORAS EXTRAS 25%",
           "@HORAS_EXTRAS_35": "HORAS EXTRAS 35%", "@REMUNERA_VACACIONAL": "REMUNERACION VACACIONAL", "@SUBS_ENFERMEDAD": "SUBSIDIO ENFERMEDAD",
           "@FONDO_PENSIONES": "FONDO PENSIONES", "@SEGURO_INVALIDEZ": "SEGURO INVALIDEZ", "@COMISION_VARIABLE": "COMISION VARIABLE",
           "@SNP_ONP": "SNP / ONP", "@IMP_RENTA_5TA": "IMP.RENTA 5TA CATEG.", "@QUINCENA": "QUINCENA", "@DESCUENTO_EPS": "DESCUENTO EPS",
           "@ESSALUD": "ESSALUD", "@APORTE_EPS": "APORTE DE EPS", "@GRATIFICACION": "GRATIFICACION", "@BONIF_EXTRA_29351": "BONIF. EXTRA L.29351"}


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def _valor(detalle, codigo) -> Decimal:
    return sum((_d(d["monto"]) for d in detalle if d["codigo"] == codigo), Decimal(0))


def _una_boleta(empresa, periodo, it, st):
    n7 = ParagraphStyle("n7", parent=st["BodyText"], fontSize=7, leading=8.4)
    n7b = ParagraphStyle("n7b", parent=n7, fontName="Helvetica-Bold")
    emp, res, det, desc, snap = it["empleado"], it["resultado"], it["detalle"], it["descripciones"], it["snapshot"] or {}
    dias_trab = _valor(det, "@DIAS_TRABAJADOS")
    no_trab = sum((_valor(det, c) for c in ("@DIAS_FALTAS", "@DIAS_SUSPENSION", "@DIAS_LIC_SIN_GOCE")), Decimal(0))
    regimen = (f"*AFP {emp.afp_nombre or ''}" + (f" - {emp.cuspp}" if emp.cuspp else "")) if emp.sistema_pensionario == "AFP" else "*SNP (ONP 13%)"
    if emp.sistema_pensionario == "AFP" and emp.tipo_comision == "MIXTA":
        regimen += " (mixta)"

    el = [Paragraph(f"<b>{empresa.razon_social}</b>", ParagraphStyle("e", parent=n7, fontSize=9, leading=11)),
          Paragraph(f"{empresa.direccion_fiscal or ''}", n7), Paragraph(f"R.U.C. {empresa.ruc}", n7),
          Paragraph("<b>BOLETA DE PAGO</b>", ParagraphStyle("t", parent=n7, fontSize=10, alignment=1, leading=12)),
          Paragraph(f"<b>DE</b> {dmy(periodo.fecha_inicio)} <b>AL</b> {dmy(periodo.fecha_fin)}", ParagraphStyle("t2", parent=n7, alignment=1, fontSize=8)), Spacer(1, 2)]

    def cab(filas, anchos):
        t = Table(filas, colWidths=[W * a for a in anchos])
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 6.6), ("BOX", (0, 0), (-1, -1), 0.5, colors.black), ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.black),
                               ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                               *[("FONTNAME", (0, r), (-1, r), "Helvetica-Bold") for r in range(0, len(filas), 2)],
                               *[("BACKGROUND", (0, r), (-1, r), colors.HexColor("#EDEDED")) for r in range(0, len(filas), 2)]]))
        return t

    cese = emp.fecha_cese if (emp.fecha_cese and emp.fecha_cese <= periodo.fecha_fin) else None
    el.append(cab([["CODIGO", "APELLIDOS Y NOMBRES", "CALIFICACION", "FECHA ING.", "FECHA CESE", "SUELDO"],
                   [emp.documento_identidad, f"{emp.apellidos} {emp.nombres}", emp.calificacion or "", dmy(emp.fecha_ingreso), dmy(cese), monto(_valor(det, "@SUELDO_TEORICO"))]],
                  [0.11, 0.33, 0.20, 0.12, 0.12, 0.12]))
    el.append(cab([["DOC. IDEN.", "ESSALUD", "REGIMEN PENSIONARIO", "SEDE", "OBSERVACIONES"],
                   [f"{emp.tipo_documento}-{emp.documento_identidad}", "", regimen, emp.sede or "", ""]], [0.15, 0.12, 0.33, 0.20, 0.20]))
    el.append(cab([["CENTRO DE COSTO (PRINCIPAL)", "AREA", "CARGO", "CATEGORIA"], [emp.centro_costo or "", emp.area or "", emp.puesto or "", emp.categoria or ""]], [0.28, 0.27, 0.25, 0.20]))
    el.append(cab([["DIAS SUBSID.", "DIAS NO TRAB./NO SUBS.", "DIAS TRAB.", "HORAS TRAB.", "HRS. EXT. SIMP. 25%", "HRS. EXT. SIMP. 35%", "HRS. EXT. DOBLES"],
                   [monto(_valor(det, "@DIAS_SUBSIDIO")), monto(no_trab), monto(dias_trab), monto(dias_trab * 8), monto(_valor(det, "@HEXT_25")),
                    monto(_valor(det, "@HEXT_35")), "0.00"]], [0.11, 0.19, 0.10, 0.11, 0.17, 0.16, 0.16]))
    el.append(Paragraph("VACACIONES:", n7))

    def lista(cat, con_cant=False):
        filas = []
        for d in det:
            if d["categoria"] == cat and _d(d["monto"]) != 0:
                nombre = NOMBRES.get(d["codigo"]) or (desc.get(d["codigo"]) or d["codigo"]).upper()
                if con_cant:
                    filas.append([nombre, monto(dias_trab) if d["codigo"] == "@HABER_BASICO" else "", monto(d["monto"])])
                else:
                    filas.append([nombre, monto(d["monto"])])
        return filas or ([["", "", ""]] if con_cant else [["", ""]])

    ing, ded, apo = lista("INGRESO", True), lista("DESCUENTO"), lista("APORTE")
    filas = max(len(ing), len(ded), len(apo), 4)
    fila = []
    for i in range(filas):
        a = ing[i] if i < len(ing) else ["", "", ""]
        b = ded[i] if i < len(ded) else ["", ""]
        c = apo[i] if i < len(apo) else ["", ""]
        fila.append([a[0], a[1], a[2], b[0], b[1], c[0], c[1]])
    pn = ParagraphStyle("pn", parent=n7, fontSize=6.6, leading=7.4)
    fila = [[Paragraph(r[0], pn), r[1], r[2], Paragraph(r[3], pn), r[4], Paragraph(r[5], pn), r[6]] for r in fila]
    t = Table([["INGRESOS", "", "", "DEDUCCIONES", "", "APORTACIONES DEL EMPLEADOR", ""]] + fila,
              colWidths=[W * 0.21, W * 0.05, W * 0.10, W * 0.22, W * 0.09, W * 0.23, W * 0.10], repeatRows=1)
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 6.8), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                           ("ALIGN", (2, 1), (2, -1), "RIGHT"), ("ALIGN", (4, 1), (4, -1), "RIGHT"), ("ALIGN", (6, 1), (6, -1), "RIGHT"),
                           ("ALIGN", (1, 1), (1, -1), "RIGHT"), ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black), ("VALIGN", (0, 1), (-1, -1), "TOP"),
                           ("TOPPADDING", (0, 0), (-1, -1), 0.6), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.6)]))
    el += [Spacer(1, 1), t, Spacer(1, 4)]
    totales = Table([["TOTAL INGRESOS", monto(res.remuneracion_computable), "TOTAL DESCUENTOS", monto(res.total_descuentos), "TOTAL APORTACION", monto(res.aportes_empleador)],
                     ["", "", "", "", "NETO A PAGAR", monto(res.neto_a_pagar)]], colWidths=[W * 0.17, W * 0.16, W * 0.19, W * 0.16, W * 0.17, W * 0.15])
    totales.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7.5), ("BOX", (0, 0), (-1, -1), 0.5, colors.black), ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.black),
                                 ("ALIGN", (1, 0), (1, 1), "RIGHT"), ("ALIGN", (3, 0), (3, 1), "RIGHT"), ("ALIGN", (5, 0), (5, 1), "RIGHT"),
                                 ("FONTNAME", (4, 1), (5, 1), "Helvetica-Bold"), ("TOPPADDING", (0, 0), (-1, -1), 1.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2)]))
    rep = " ".join(x for x in (empresa.representante_legal, empresa.cargo_representante) if x)
    firmas = Table([["", ""], [Paragraph(f"{empresa.razon_social}" + (f"<br/>{rep}" if rep else ""), n7), Paragraph(f"RECIBI CONFORME : {emp.apellidos} {emp.nombres}", n7)]],
                   colWidths=[W * 0.45, W * 0.55], rowHeights=[7 * mm, None])
    firmas.setStyle(TableStyle([("LINEABOVE", (0, 1), (0, 1), 0.4, colors.black), ("LINEABOVE", (1, 1), (1, 1), 0.4, colors.black), ("ALIGN", (0, 1), (-1, 1), "CENTER")]))
    return el + [totales, Spacer(1, 1), firmas]


def generar(empresa, periodo, items) -> bytes:
    """items: lista de dicts {empleado, resultado, detalle[{codigo,categoria,monto}], descripciones{codigo: texto}, snapshot}."""
    st = getSampleStyleSheet()
    buf = io.BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=7 * mm, bottomMargin=6 * mm, title="Boleta de pago")
    story = []
    for i, it in enumerate(items):
        if i:
            story.append(PageBreak())
        story += _una_boleta(empresa, periodo, it, st)
        story += [Spacer(1, 2), HRFlowable(width="100%", thickness=0.4, dash=(3, 3), color=colors.grey), Spacer(1, 3)]
        story += _una_boleta(empresa, periodo, it, st)
    pdf.build(story)
    return buf.getvalue()
