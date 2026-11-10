"""PDF y Excel de la LIQUIDACIÓN DE BENEFICIOS SOCIALES, imitando el formato de la empresa (modelo entregado por el dueño).
Una liquidación sin cerrar sale marcada como BORRADOR. Los datos salen del legajo, el cálculo y la empresa."""
import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak

from . import reglas as R
from .formatos import a_fecha, dmy, fecha_larga, monto, motivo_texto, regimen_pensiones
from .letras import monto_en_letras

GRIS = colors.HexColor("#BDBDBD")
GRIS_CLARO = colors.HexColor("#E6E6E6")


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def _tiempo_formato(snap) -> str:
    y, m, d = R.tiempo_servicio(a_fecha(snap["fecha_ingreso"]), a_fecha(snap["fecha_cese"]))
    return f"{y:02d} Años, {m:02d} Meses y {d:02d} Dias"


def _por_codigo(doc, *codigos):
    return [c for c in doc["conceptos"] if c["codigo"] in codigos]


def _marca_agua(doc):
    def f(canvas, _):
        if doc["estado"] != "CERRADA":
            canvas.saveState()
            canvas.setFont("Helvetica-Bold", 62 if doc.get("simulacion") else 70)
            canvas.setFillColorRGB(0.88, 0.88, 0.88)
            canvas.translate(105 * mm, 120 * mm)
            canvas.rotate(45)
            canvas.drawCentredString(0, 0, "SIMULACIÓN" if doc.get("simulacion") else "BORRADOR")
            canvas.restoreState()
    return f


def filas_5ta(d5: dict):
    """Detalle de la renta de 5ta categoría como filas [concepto, importe, retención, estilo]: de dónde sale la renta bruta."""
    f = []
    mm = lambda m, a: f"{int(m):02d}/{a}"
    f.append(["A. BOLETAS DE LA EMPRESA EN EL AÑO (enero hasta el mes del cese)", "Base afecta", "Retenido", "t"])
    for b in d5["boletas"]:
        f.append([f"   {mm(b['mes'], b['anio'])} · {b['fase']}", monto(b["base"]), monto(b["retencion"]), ""])
    if not d5["boletas"]:
        f.append(["   (no hay boletas calculadas en el año)", "", "", ""])
    for b in d5.get("boletas_no_sumadas", []):
        f.append([f"   {mm(b['mes'], b['anio'])} · {b['fase']} — NO SE SUMA (otra fase del mismo mes; ya cuenta «Fin de Mes»)", monto(b["base"]), monto(b["retencion"]), ""])
    f.append(["   Subtotal A", monto(d5["subtotales"]["boletas"]), monto(d5["retenido_empresa"]), "s"])
    f.append(["B. RENTAS DE EMPLEADORES ANTERIORES (Legajo)", "Ingreso afecto", "Retenido", "t"])
    for r in d5["rentas_previas"]:
        f.append([f"   desde {mm(r['mes'], r['anio'])} · RUC {r['ruc']} · {r['razon_social']}", monto(r["ingreso"]), monto(r["retencion"]), ""])
    if not d5["rentas_previas"]:
        f.append(["   (ninguna registrada)", "0.00", "0.00", ""])
    f.append(["   Subtotal B", monto(d5["subtotales"]["rentas_previas"]), monto(d5["retenido_otros"]), "s"])
    f.append(["C. ESTA LIQUIDACIÓN (solo conceptos afectos a 5ta categoría)", "Monto", "", "t"])
    for c in d5["liquidacion"]:
        f.append([f"   {c['descripcion']}", monto(c["monto"]), "", ""])
    if not d5["liquidacion"]:
        f.append(["   (ningún concepto afecto)", "0.00", "", ""])
    f.append(["   Subtotal C", monto(d5["subtotales"]["liquidacion"]), "", "s"])
    f.append(["RENTA BRUTA ANUAL  (A + B + C)", monto(d5["renta_bruta"]), "", "t"])
    n_uit = _d(d5["deduccion_uit"]) + _d(d5["deduccion_adicional_uit"])
    f.append([f"(−) Deducción de {n_uit:.0f} UIT × S/ {monto(d5['uit'])}", monto(d5["deduccion"]), "", ""])
    f.append(["RENTA NETA", monto(d5["renta_neta"]), "", "t"])
    f.append(["IMPUESTO POR TRAMOS (escala acumulativa)", "Renta en el tramo", "Impuesto", "t"])
    for t in d5["tramos"]:
        if _d(t["monto_en_tramo"]) > 0:
            hasta = f"hasta {_d(t['hasta_uit']):.0f} UIT" if t["hasta_uit"] is not None else "más de " + f"{_d(t['desde_uit']):.0f} UIT"
            f.append([f"   {hasta} (S/ {monto(t['desde'])} a {monto(t['hasta']) if t['hasta'] else 'en adelante'}) al {_d(t['tasa']):.0f} %", monto(t["monto_en_tramo"]), monto(t["impuesto"]), ""])
    f.append(["IMPUESTO ANUAL", "", monto(d5["impuesto_anual"]), "t"])
    f.append(["(−) Retenido por la empresa (boletas)", "", monto(d5["retenido_empresa"]), ""])
    f.append(["(−) Retenido por empleadores anteriores", "", monto(d5["retenido_otros"]), ""])
    aj = _d(d5["ajuste"])
    f.append(["AJUSTE = " + ("RETENCIÓN ADICIONAL en esta liquidación" if aj >= 0 else "DEVOLUCIÓN del exceso retenido (otro ingreso)"), "", monto(abs(aj)), "t"])
    return f


def generar_pdf(empresa, doc: dict, con_formulas: bool = False, con_firmas: bool = True) -> bytes:
    snap = doc["snapshot"]
    st = getSampleStyleSheet()
    n8 = ParagraphStyle("n8", parent=st["BodyText"], fontSize=7.8, leading=8.8)
    n8b = ParagraphStyle("n8b", parent=n8, fontName="Helvetica-Bold")
    pq = ParagraphStyle("pq", parent=n8, fontSize=7, leading=8.4, textColor=colors.HexColor("#444444"))
    buf = io.BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=8 * mm, bottomMargin=8 * mm,
                            title="Liquidación de beneficios sociales")
    W = 182 * mm
    el = []
    if doc.get("simulacion"):
        el.append(Paragraph("<font color='#B91C1C'><b>SIMULACIÓN para pruebas: hay conceptos deshabilitados; estos montos NO son la liquidación real.</b></font>", n8))
    if doc["estado"] != "CERRADA":
        el.append(Paragraph("<font color='#B45309'><b>BORRADOR · liquidación sin cerrar: no se guarda ni viaja al PLAME, AFP o resúmenes hasta cerrarla.</b></font>", n8))
    el += [Paragraph(f"<b>{empresa.razon_social}</b>", ParagraphStyle("e", parent=n8, fontSize=11, leading=13)),
           Paragraph(f"DIRECCION : {empresa.direccion_fiscal}" if empresa.direccion_fiscal else "", n8),
           Paragraph(f"R.U.C. : {empresa.ruc}", n8), Spacer(1, 5),
           Paragraph("<u><b>LIQUIDACION DE BENEFICIOS SOCIALES</b></u>", ParagraphStyle("t", parent=n8, fontSize=11, alignment=1, leading=13)),
           Spacer(1, 6), Paragraph(f"<b>FECHA</b>&nbsp;&nbsp;&nbsp;{fecha_larga(snap['fecha_cese'])}", ParagraphStyle("f", parent=n8, fontSize=9)),
           Spacer(1, 5), Paragraph("<u><b>DATOS DEL TRABAJADOR</b></u>", n8)]

    izq = [f"APELLIDOS, NOMBRES : {snap['colaborador'].replace(', ', ' ', 1)}", f"CODIGO : {snap['documento']}",
           f"FECHA DE INGRESO : {dmy(snap['fecha_ingreso'])}", f"MOTIVO DE RETIRO : {motivo_texto(doc.get('motivo_cese'), doc.get('clase_despido'))}",
           f"TIEMPO DE SERVICIOS EN LA EMPRESA : {_tiempo_formato(snap)}"]
    der = [f"UNIDAD : {snap.get('sede') or ''}", f"PLANILLA : {snap.get('categoria') or ''}", f"FECHA DE EGRESO : {dmy(snap['fecha_cese'])}",
           f"PUESTO DE TRABAJO : {(snap.get('puesto') or '')}", f"REGIMEN PENSIONES : {regimen_pensiones(snap)}"]
    t = Table([[Paragraph(a, n8), Paragraph(b, n8)] for a, b in zip(izq, der)], colWidths=[W * 0.55, W * 0.45])
    t.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 0.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5), ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.black)]))
    el += [t, Spacer(1, 4)]

    # ---- remuneración computable
    sueldo, af, var = _d(snap["sueldo"]), _d(snap["asignacion_familiar"]), _d(snap["variables_promedio"])
    sexto, rc_cts = _d(snap["sexto_gratificacion"]), _d(snap["rc_cts"])
    rc = sueldo + af + var
    pend = _d((snap.get("cts_anterior") or {}).get("rc", 0))
    filas = [["REMUNERACION COMPUTABLE", "C.T.S.", "C.T.S. Pendiente", "Gratificación", "Vacaciones"],
             ["SEXTO GRATIFICACION", monto(sexto), "0.00", "", ""],
             ["SUELDO BASICO", monto(sueldo), "0.00", monto(sueldo), monto(sueldo)]]
    if af:
        filas.append(["ASIGNACION FAMILIAR", monto(af), "0.00", monto(af), monto(af)])
    if var:
        filas.append(["PROMEDIO DE VARIABLES", monto(var), "0.00", monto(var), monto(var)])
    filas.append(["TOTAL REMUNERACION COMPUTABLE", monto(rc_cts), monto(pend), monto(rc), monto(rc)])
    t = Table(filas, colWidths=[W * 0.40, W * 0.15, W * 0.15, W * 0.15, W * 0.15])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                           ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("LINEABOVE", (0, -1), (-1, -1), 0.6, colors.black),
                           ("TOPPADDING", (0, 0), (-1, -1), 0.4), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.4)]))
    el += [t, Spacer(1, 2)]
    caja = Table([[f"REMUNERACION COMPUTABLE PARA CTS:   Por Año : {monto(rc_cts)}", f"Por Mes : {monto(rc_cts / 12)}", f"Por Día : {monto(rc_cts / 360)}"]],
                 colWidths=[W * 0.5, W * 0.25, W * 0.25])
    caja.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.black), ("FONTSIZE", (0, 0), (-1, -1), 8), ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold")]))
    el += [caja, Spacer(1, 4)]

    def barra(titulo, derecha=None, grande=False):
        t = Table([[titulo, derecha or ""]], colWidths=[W * 0.7, W * 0.3])
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), GRIS), ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                               ("FONTSIZE", (0, 0), (-1, -1), 12 if grande else 9), ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                               ("TOPPADDING", (0, 0), (-1, -1), 1.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2)]))
        return t

    def tabla(filas, estilos=()):
        t = Table(filas, colWidths=[W * 0.42, W * 0.22, W * 0.18, W * 0.18])
        t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                               ("TOPPADDING", (0, 0), (-1, -1), 0.4), ("BOTTOMPADDING", (0, 0), (-1, -1), 0.4), *estilos]))
        return t

    # ---- INGRESOS
    el.append(barra("INGRESOS"))
    f, est = [], []

    def titulo(t):
        f.append([t, "", "", ""])
        est.append(("FONTNAME", (0, len(f) - 1), (0, len(f) - 1), "Helvetica-Bold"))

    cts = _por_codigo(doc, "@CTS_TRUNCA")
    c = snap.get("cts")
    ant = snap.get("cts_anterior")
    if c or ant:
        titulo("COMPENSACIÓN POR TIEMPO DE SERVICIO :")
    if c:
        f.append([f"Ultimo Periodo de C.T.S. por cancelar : {dmy(c['desde'])} al {dmy(c['hasta'])}", "", "", ""])
        f.append([f"   {int(c['meses']):02d} Dozavos", f"x S/. {c['por_mes']}", monto(c["monto_meses"]), ""])
        f.append([f"   {int(c['dias']):02d} Treintavos de Dozavo", f"x S/. {c['por_dia']}", monto(c["monto_dias"]), monto(_d(c["monto_meses"]) + _d(c["monto_dias"]))])
    if ant:
        rc_a = _d(ant["rc"])
        f.append([f"C.T.S. Pendiente : {dmy(ant['desde'])} al {dmy(ant['hasta'])}", f"x S/. {monto(rc_a / 12)}", "", monto(ant["monto"])])
    ven, tru = _por_codigo(doc, "@VACACIONES_VENCIDAS"), _por_codigo(doc, "@VACACIONES_TRUNCAS")
    vac = snap["vacaciones"]
    if ven or tru:
        titulo("VACACIONES :")
    if tru:
        f.append(["   Vacaciones Truncas :", "", "", ""])
        f.append([f"      Periodo {vac['periodo_truncas'].replace('-', ' - ')}", f"{vac['truncas']} dias", monto(tru[0]["monto"]), monto(tru[0]["monto"])])
    if ven:
        f.append(["   Vacaciones Pendientes :", "", "", ""])
        f.append([f"      Periodo {vac['periodo_vencidas'].replace('-', ' - ')}", f"{vac['vencidas']} dias", monto(ven[0]["monto"]), monto(ven[0]["monto"])])
    gr = _por_codigo(doc, "@GRATIFICACION")
    if gr:
        g = snap.get("gratificacion") or {"meses": 0, "dias": 0}
        titulo("GRATIFICACIONES :")
        f.append(["   Gratificación Trunca :", f"{g['meses']} meses y {g['dias']} dias", monto(gr[0]["monto"]), monto(gr[0]["monto"])])
    manual_ing = [c for c in doc["conceptos"] if c.get("manual") and c["categoria"] == "INGRESO" and c.get("habilitado", True)]
    otros = _por_codigo(doc, "@BONIF_EXTRA_29351", "@DEVOLUCION_5TA", "@INDEMNIZACION_DESPIDO") + manual_ing
    if otros:
        titulo("OTROS INGRESOS :")
        nombres = {"@BONIF_EXTRA_29351": "BONIF. EXTRA L.29351", "@DEVOLUCION_5TA": "DEVOLUCION RETENCION 5TA CATEGORIA",
                   "@INDEMNIZACION_DESPIDO": "INDEMNIZACION POR DESPIDO ARBITRARIO"}
        for o in otros:
            f.append([f"   {o['descripcion'].upper() if o.get('manual') else nombres[o['codigo']]}", "", monto(o["monto"]), ""])
        f[-1][3] = monto(sum((_d(o["monto"]) for o in otros), Decimal(0)))
    if f:
        el.append(tabla(f, est))
    el += [barra("TOTAL INGRESOS", f"S/. {monto(doc['total_ingresos'])}"), Spacer(1, 3)]

    # ---- DESCUENTOS
    el.append(barra("DESCUENTOS"))
    f, est = [], []
    pen = snap.get("pension") or {}
    base_pen = monto(pen.get("base", 0))
    lineas = []
    if snap.get("sistema_pensionario") == "AFP":
        for cod, nom, pct in (("@FONDO_PENSIONES", "FONDO PENSIONES", pen.get("aporte_pct")), ("@COMISION_VARIABLE", "COMISION VARIABLE", pen.get("comision_pct")),
                              ("@SEGURO_INVALIDEZ", "SEGURO INVALIDEZ", pen.get("prima_pct"))):
            x = _por_codigo(doc, cod)
            if x:
                lineas.append((nom, pct, x[0]["monto"]))
        if lineas:
            titulo("POR DESCUENTO DE AFP :")
            for nom, pct, m in lineas:
                f.append([f"   {nom}", f"{_d(pct):.2f} %   {base_pen}", monto(m), ""])
            f[-1][3] = monto(sum((_d(m) for _, _, m in lineas), Decimal(0)))
    else:
        x = _por_codigo(doc, "@SNP_ONP")
        if x:
            titulo("POR DESCUENTO DE ONP :")
            f.append(["   SISTEMA NACIONAL DE PENSIONES", f"{_d(pen.get('onp_pct')):.2f} %   {base_pen}", monto(x[0]["monto"]), monto(x[0]["monto"])])
    r5 = _por_codigo(doc, "@IMP_RENTA_5TA")
    if r5:
        titulo("IMPUESTO A LA RENTA :")
        f.append(["   RETENCION 5TA CATEGORIA", "", monto(r5[0]["monto"]), monto(r5[0]["monto"])])
    man_desc = [c for c in doc["conceptos"] if c.get("manual") and c["categoria"] == "DESCUENTO" and c.get("habilitado", True)]
    if man_desc:
        titulo("OTROS DESCUENTOS :")
        for c in man_desc:
            f.append([f"   {c['descripcion'].upper()}", "", monto(c["monto"]), ""])
        f[-1][3] = monto(sum((_d(c["monto"]) for c in man_desc), Decimal(0)))
    if f:
        el.append(tabla(f, est))
    el += [barra("TOTAL DESCUENTOS", f"S/. {monto(doc['total_descuentos'])}"), Spacer(1, 3),
           barra("NETO A PAGAR", f"S/. {monto(doc['neto_a_pagar'])}", grande=True), Spacer(1, 2)]

    # ---- APORTES
    es = snap.get("essalud") or {}
    el.append(barra("APORTES"))
    f, est = [], []
    titulo("APORTACIONES DEL EMPLEADOR :")
    if es.get("con_eps"):
        f.append(["   APORTE DE EPS", f"{_d(es.get('tasa_eps_pct')):.2f} %   {monto(es.get('base', 0))}", monto(doc["aporte_eps"]), ""])
    f.append(["   ESSALUD", f"{_d(es.get('tasa_essalud_pct')):.2f} %   {monto(es.get('base', 0))}", monto(doc["aporte_essalud"]),
              monto(_d(doc["aporte_essalud"]) + _d(doc["aporte_eps"]))])
    el += [tabla(f, est), barra("TOTAL APORTES", f"S/. {monto(_d(doc['aporte_essalud']) + _d(doc['aporte_eps']))}"), Spacer(1, 3)]

    el.append(Paragraph(f"Recibí de {empresa.razon_social} la cantidad de S/. {monto(doc['neto_a_pagar'])} ({monto_en_letras(doc['neto_a_pagar'])}) por liquidación de los "
                        "derechos que me corresponden de acuerdo a las disposiciones legales vigentes, estando conforme con ellos.", n8))
    if con_firmas:
        rep = " · ".join(x for x in (empresa.representante_legal, empresa.cargo_representante) if x)
        firmas = Table([["", ""], [Paragraph(f"p. {empresa.razon_social}" + (f"<br/>{rep}" if rep else ""), n8),
                                   Paragraph(f"Firma: {snap['colaborador'].replace(', ', ' ', 1)}<br/>DNI {snap['documento']}", n8)]],
                       colWidths=[W * 0.5, W * 0.5], rowHeights=[11 * mm, None])
        firmas.setStyle(TableStyle([("LINEABOVE", (0, 1), (0, 1), 0.5, colors.black), ("LINEABOVE", (1, 1), (1, 1), 0.5, colors.black),
                                    ("ALIGN", (0, 1), (-1, 1), "CENTER"), ("LEFTPADDING", (0, 1), (0, 1), 10), ("RIGHTPADDING", (0, 1), (0, 1), 40)]))
        el += [Spacer(1, 3), firmas]
    if doc.get("avisos"):
        el += [Spacer(1, 4)] + [Paragraph("• " + a, pq) for a in doc["avisos"]]
    if con_formulas:
        el += [PageBreak(), Paragraph("<b>DETALLE DEL CÁLCULO</b> (para revisión interna)", n8b), Spacer(1, 4)]
        data = [["Concepto", "Monto (S/)", "Cálculo"]] + [[Paragraph(c["descripcion"] or c["codigo"], n8), monto(c["monto"]), Paragraph(c["formula"] or "", pq)] for c in doc["conceptos"]]
        t = Table(data, colWidths=[W * 0.28, W * 0.14, W * 0.58], repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GRIS_CLARO), ("FONTSIZE", (0, 0), (-1, 0), 8), ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDDDDD"))]))
        el.append(t)
        d5 = (snap.get("quinta") or {}).get("detalle")
        if d5:
            filas = filas_5ta(d5)
            el += [Spacer(1, 10), Paragraph(f"<b>DETALLE DE LA RENTA DE 5TA CATEGORÍA</b> — de dónde sale la renta bruta de S/ {monto(d5['renta_bruta'])}", n8b), Spacer(1, 3)]
            t5 = Table([[Paragraph(r[0].replace(' ', '&nbsp;', 3) if r[3] == '' else f"<b>{r[0]}</b>", pq), r[1], r[2]] for r in filas], colWidths=[W * 0.62, W * 0.19, W * 0.19])
            t5.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7.2), ("ALIGN", (1, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                    ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                                    *[("BACKGROUND", (0, i), (-1, i), GRIS_CLARO) for i, r in enumerate(filas) if r[3] == "t"],
                                    *[("FONTNAME", (0, i), (-1, i), "Helvetica-Bold") for i, r in enumerate(filas) if r[3] in ("t", "s")]]))
            el.append(t5)
        pri = (snap.get("pension") or {}).get("prima")
        if pri:
            filas = [["Base AFP de la boleta del mes", monto(pri["base_boleta"])], ["(+) Base AFP de esta liquidación", monto(pri["base_liquidacion"])],
                     ["Base total", monto(pri["base_total"])], ["Tope de remuneración asegurable del periodo", monto(pri["tope_rma"])],
                     ["Base con tope (la menor)", monto(pri["base_con_tope"])], [f"× tasa de la prima {_d(pri['tasa_pct']):.2f} %  =  PRIMA MÁXIMA DEL MES", monto(pri["maxima_mes"])],
                     ["(−) Ya cobrado en la boleta", monto(pri["cobrado_boleta"])], ["PRIMA ADICIONAL EN ESTA LIQUIDACIÓN", monto(pri["adicional"])]]
            el += [Spacer(1, 10), Paragraph("<b>SEGURO DE INVALIDEZ (PRIMA AFP) CON TOPE</b> — boleta + liquidación no superan el máximo", n8b), Spacer(1, 3)]
            tp = Table(filas, colWidths=[W * 0.75, W * 0.25])
            tp.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7.5), ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                                    ("FONTNAME", (0, 5), (-1, 5), "Helvetica-Bold"), ("FONTNAME", (0, 7), (-1, 7), "Helvetica-Bold"), ("BACKGROUND", (0, 7), (-1, 7), GRIS_CLARO)]))
            el.append(tp)
    marca = _marca_agua(doc)
    pdf.build(el, onFirstPage=marca, onLaterPages=marca)
    return buf.getvalue()


def generar_excel(empresa, doc: dict) -> bytes:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    snap = doc["snapshot"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Liquidación"
    negrita, gris = Font(bold=True), PatternFill("solid", fgColor="BDBDBD")
    ws.append([empresa.razon_social]); ws["A1"].font = Font(bold=True, size=13)
    ws.append([f"R.U.C. {empresa.ruc}" + (f" · {empresa.direccion_fiscal}" if empresa.direccion_fiscal else "")])
    ws.append(["LIQUIDACION DE BENEFICIOS SOCIALES" + ("  (SIMULACIÓN para pruebas: conceptos deshabilitados)" if doc.get("simulacion") else ("  (BORRADOR: sin cerrar)" if doc["estado"] != "CERRADA" else ""))]); ws["A3"].font = Font(bold=True, size=12)
    ws.append([fecha_larga(snap["fecha_cese"])])
    ws.append([])
    datos = [("Apellidos y nombres", snap["colaborador"]), ("Código (documento)", snap["documento"]), ("Fecha de ingreso", dmy(snap["fecha_ingreso"])),
             ("Fecha de egreso", dmy(snap["fecha_cese"])), ("Motivo de retiro", motivo_texto(doc.get("motivo_cese"), doc.get("clase_despido"))),
             ("Tiempo de servicios", _tiempo_formato(snap)), ("Unidad (sede)", snap.get("sede") or ""), ("Planilla (categoría)", snap.get("categoria") or ""),
             ("Puesto de trabajo", snap.get("puesto") or ""), ("Régimen de pensiones", regimen_pensiones(snap))]
    for k, v in datos:
        ws.append([k, v]); ws.cell(ws.max_row, 1).font = negrita
    ws.append([])
    ws.append(["REMUNERACIÓN COMPUTABLE", "C.T.S.", "C.T.S. pendiente", "Gratificación", "Vacaciones"])
    for c in ws[ws.max_row]:
        c.font = negrita
    sueldo, af, var = _d(snap["sueldo"]), _d(snap["asignacion_familiar"]), _d(snap["variables_promedio"])
    rc = sueldo + af + var
    ws.append(["Sexto de gratificación", float(_d(snap["sexto_gratificacion"])), 0, "", ""])
    ws.append(["Sueldo básico", float(sueldo), 0, float(sueldo), float(sueldo)])
    if af:
        ws.append(["Asignación familiar", float(af), 0, float(af), float(af)])
    if var:
        ws.append(["Promedio de variables", float(var), 0, float(var), float(var)])
    ws.append(["TOTAL REMUNERACIÓN COMPUTABLE", float(_d(snap["rc_cts"])), float(_d((snap.get("cts_anterior") or {}).get("rc", 0))), float(rc), float(rc)])
    ws.append([])
    for titulo_, cat in (("INGRESOS", "INGRESO"), ("DESCUENTOS", "DESCUENTO")):
        ws.append([titulo_, "Cálculo", "Monto (S/)"])
        for c in ws[ws.max_row]:
            c.font = negrita; c.fill = gris
        for c in doc["conceptos"]:
            if c["categoria"] == cat:
                ws.append([c["descripcion"] or c["codigo"], c["formula"] or "", float(_d(c["monto"]))])
        ws.append([f"TOTAL {titulo_}", "", float(_d(doc["total_ingresos"] if cat == "INGRESO" else doc["total_descuentos"]))])
        ws.cell(ws.max_row, 1).font = negrita
        ws.append([])
    ws.append(["NETO A PAGAR", "", float(_d(doc["neto_a_pagar"]))]); ws.cell(ws.max_row, 1).font = Font(bold=True, size=12)
    ws.append([monto_en_letras(doc["neto_a_pagar"])])
    ws.append([])
    es = snap.get("essalud") or {}
    ws.append(["APORTACIONES DEL EMPLEADOR", "Tasa / base", "Monto (S/)"]); [setattr(c, "font", negrita) for c in ws[ws.max_row]]
    if es.get("con_eps"):
        ws.append(["Aporte de EPS", f"{_d(es.get('tasa_eps_pct')):.2f} %  {monto(es.get('base', 0))}", float(_d(doc["aporte_eps"]))])
    ws.append(["EsSalud", f"{_d(es.get('tasa_essalud_pct')):.2f} %  {monto(es.get('base', 0))}", float(_d(doc["aporte_essalud"]))])
    ws.column_dimensions["A"].width = 38; ws.column_dimensions["B"].width = 58
    for col in "CDE":
        ws.column_dimensions[col].width = 18
    for fila in ws.iter_rows():
        for c in fila:
            if isinstance(c.value, float):
                c.number_format = "#,##0.00"; c.alignment = Alignment(horizontal="right")
    d5 = (snap.get("quinta") or {}).get("detalle")
    if d5:
        w5 = wb.create_sheet("Detalle 5ta categoría")
        w5.append([f"DETALLE DE LA RENTA DE 5TA CATEGORÍA — renta bruta anual S/ {monto(d5['renta_bruta'])}"]); w5["A1"].font = Font(bold=True, size=12)
        w5.append([snap["colaborador"]])
        w5.append([])
        for r in filas_5ta(d5):
            w5.append([r[0], r[1], r[2]])
            if r[3] in ("t", "s"):
                for c in w5[w5.max_row]:
                    c.font = negrita
                    if r[3] == "t":
                        c.fill = gris
        w5.column_dimensions["A"].width = 78
        w5.column_dimensions["B"].width = 20
        w5.column_dimensions["C"].width = 20
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
