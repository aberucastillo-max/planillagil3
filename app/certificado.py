"""Certificado de rentas y retenciones de quinta categoría (Art. 45 del Reglamento de la LIR, D.S. 122-94-EF).
Se arma a partir de la liquidación del cesado: renta bruta del año hasta el cese, 7 UIT de deducción, impuesto, retenciones y devolución."""
import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .calculo import ids_vigentes
from .diccionario import cargar_parametros
from .formatos import dmy, monto
from .letras import monto_en_letras
from .models import Detalle, Empresa, Empleado, Periodo, Resultado

CERO = Decimal("0.00")
RUBROS = [("sueldo", "SUELDO"), ("utilidades", "PART. UTILIDADES"), ("comisiones", "COMISIONES"), ("gratificaciones", "GRATIFICACIONES"),
          ("vacaciones", "VACACIONES"), ("asignacion_familiar", "ASIGNACION FAMILIAR"), ("reintegros", "REINTEGROS"),
          ("otras", "OTRAS REMUNERACIONES"), ("otras_empresas", "INGRESOS DE OTRAS EMPRESAS")]


def _d(x) -> Decimal:
    return Decimal(str(x or 0))


def clasificar(codigo: str) -> str:
    """Rubro del certificado al que pertenece un concepto del Diccionario."""
    c = (codigo or "").upper()
    if c == "@HABER_BASICO":
        return "sueldo"
    if "UTILIDAD" in c:
        return "utilidades"
    if "COMISION" in c:
        return "comisiones"
    if c == "@GRATIFICACION" or "BONIF_EXTRA" in c:
        return "gratificaciones"
    if "VACACION" in c:
        return "vacaciones"
    if "ASIGNACION_FAMILIAR" in c:
        return "asignacion_familiar"
    if "REINTEGRO" in c:
        return "reintegros"
    return "otras"


def datos(db, empresa: Empresa, emp: Empleado, doc: dict) -> dict:
    """Datos del certificado. `doc` es el documento de la liquidación (cerrada o en cálculo)."""
    q = (doc.get("snapshot") or {}).get("quinta") or {}
    det = q.get("detalle") or {}
    periodo = db.get(Periodo, doc["periodo"]["id"])
    rub = {k: CERO for k, _ in RUBROS}
    # 1) lo pagado en las boletas del año hasta el mes del cese, concepto por concepto (solo lo que afecta a la renta de 5ta)
    vig = ids_vigentes(db, empresa.id)
    periodos = (db.query(Periodo).join(Resultado, Resultado.periodo_id == Periodo.id)
                .filter(Resultado.empleado_id == emp.id, Periodo.empresa_id == empresa.id, Periodo.anio == periodo.anio,
                        Periodo.mes <= periodo.mes, Periodo.id.in_(vig)).order_by(Periodo.mes, Periodo.id).all())
    for p in periodos:
        pr = cargar_parametros(db, p.diccionario_id, p.fecha_inicio, empresa_id=empresa.id, periodo_id=p.id)
        for d in db.query(Detalle).filter(Detalle.periodo_id == p.id, Detalle.empleado_id == emp.id, Detalle.categoria == "INGRESO"):
            c = pr.conceptos.get(d.concepto_codigo)
            if c and c.afecta_5ta_regular:
                rub[clasificar(d.concepto_codigo)] += _d(d.monto)
    # 2) lo que paga la liquidación y afecta a la 5ta
    for f in det.get("liquidacion", []):
        rub[clasificar(f["codigo"])] += _d(f["monto"])
    # 3) ingresos percibidos en otras empresas (rentas previas)
    previas = det.get("rentas_previas", [])
    rub["otras_empresas"] = sum((_d(x["ingreso"]) for x in previas), CERO)
    bruta = _d(q.get("renta_bruta"))
    clasificado = sum(rub.values(), CERO)
    dif = (bruta - clasificado).quantize(CERO)
    if abs(dif) >= Decimal("0.01"):                       # la renta bruta del cálculo manda: la diferencia va a «otras remuneraciones»
        rub["otras"] += dif
    rub = {k: v.quantize(CERO) for k, v in rub.items()}
    ajuste = _d(q.get("ajuste"))
    ret_emp, ret_otros = _d(det.get("retenido_empresa")), _d(det.get("retenido_otros"))
    impuesto = _d(q.get("impuesto_anual"))
    out = {
        "ejercicio": periodo.anio, "fecha": doc["fecha_cese"], "definitivo": doc["estado"] == "CERRADA",
        "empresa": {"razon_social": empresa.razon_social, "ruc": empresa.ruc, "domicilio_fiscal": empresa.direccion_fiscal,
                    "representante": empresa.representante_legal, "cargo": empresa.cargo_representante},
        "trabajador": {"nombre": f"{emp.apellidos} {emp.nombres}".strip(), "dni": emp.documento_identidad, "puesto": emp.puesto},
        "rubros": [{"clave": k, "etiqueta": e, "monto": rub[k]} for k, e in RUBROS],
        "remuneracion_bruta": bruta.quantize(CERO), "deduccion_uit": _d(det.get("deduccion_uit")) + _d(det.get("deduccion_adicional_uit")),
        "deduccion": _d(det.get("deduccion")).quantize(CERO), "renta_neta": _d(q.get("renta_neta")).quantize(CERO),
        "impuesto": impuesto.quantize(CERO), "creditos": CERO,
        "retenido_empresa": (ret_emp + max(ajuste, CERO)).quantize(CERO), "retenido_otros": ret_otros.quantize(CERO),
        "retenido_total": (ret_emp + ret_otros + max(ajuste, CERO)).quantize(CERO), "devolucion": max(-ajuste, CERO).quantize(CERO),
        "rentas_previas": [{"ruc": x["ruc"], "razon_social": x["razon_social"], "ingreso": _d(x["ingreso"]), "retencion": _d(x["retencion"])} for x in previas],
        "conciliado": abs(dif) < Decimal("0.01"), "diferencia": dif,
    }
    out["texto"] = _texto_legal(out)
    out["faltan"] = [n for n, v in (("Domicilio fiscal de la empresa", empresa.direccion_fiscal), ("Representante legal", empresa.representante_legal))
                     if not (v or "").strip()]
    return out


# ------------------------------------------------------------------ PDF con el formato del modelo
def _texto_legal(c: dict) -> str:
    e, t = c["empresa"], c["trabajador"]
    rep = e["representante"] or "________________"
    letras = monto_en_letras(c["impuesto"]).replace(" SOLES", "")
    return (f"{e['razon_social']} CON RUC. No. {e['ruc']} CON DOMICILIO FISCAL {e['domicilio_fiscal'] or '________________'}, REPRESENTADA POR EL SR.(A) {rep}, "
            f"CERTIFICA QUE A NUESTRO(A) TRABAJADOR(A) SR(A): {t['nombre']} CON DNI {t['dni']}; SE HA RETENIDO EL IMPUESTO A LA RENTA, POR EL "
            f"IMPORTE DE: {letras} ( S/. {monto(c['impuesto'])}), COMO PAGO A CUENTA DEL IMPUESTO A LA RENTA CORRESPONDIENTE AL EJERCICIO GRAVABLE {c['ejercicio']}. "
            f"CALCULADO EN BASE A LAS SIGUIENTES RENTAS, DEDUCCIONES Y CREDITOS:")


def generar_pdf(c: dict) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
                            title=f"Certificado de rentas y retenciones de 5ta {c['ejercicio']}")
    base = ParagraphStyle("b", fontName="Helvetica", fontSize=8.3, leading=10.2)
    ctr = ParagraphStyle("c", parent=base, alignment=TA_CENTER, fontName="Helvetica-Bold", fontSize=10.5, leading=13)
    ctr2 = ParagraphStyle("c2", parent=base, alignment=TA_CENTER, fontSize=8.8)
    der = ParagraphStyle("d", parent=base, alignment=TA_RIGHT)
    jus = ParagraphStyle("j", parent=base, alignment=TA_JUSTIFY, fontSize=8.3, leading=10.8)
    neg = ParagraphStyle("n", parent=base, fontName="Helvetica-Bold")
    W = A4[0] - 28 * mm
    el = [Paragraph("Pág. &nbsp; 0001", der), Spacer(1, 2 * mm),
          Paragraph("CERTIFICADO DE RENTAS Y RETENCIONES DE QUINTA CATEGORIA", ctr),
          Paragraph("(Art. 45 del Reglamento del Impuesto a la Renta - D.S.No. 122-94-EF)", ctr2)]
    cab = Table([[Paragraph(f"<b>EJERCICIO GRAVABLE &nbsp; {c['ejercicio']}</b>", ctr2), Paragraph(f"<b>FECHA : &nbsp; {dmy(c['fecha'])}</b>", der)]], colWidths=[W * 0.62, W * 0.38])
    cab.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))
    el += [cab, Spacer(1, 6 * mm)]
    caja = Table([[Paragraph(_texto_legal(c), jus)]], colWidths=[W])
    caja.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, colors.black), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    el += [caja, Spacer(1, 5 * mm)]
    cw = [W * 0.46, W * 0.07, W * 0.47]
    cw2 = [W * 0.42, W * 0.20, W * 0.38]
    cwd = [W * 0.55, W * 0.07, W * 0.38]
    num = ParagraphStyle("num", parent=base, alignment=TA_RIGHT)
    def fila(etq, importe="", s=base):
        return [Paragraph(etq, s), Paragraph("S/." if importe != "" else "", base), Paragraph(importe, num)]
    # 1. rentas brutas
    filas = [[Paragraph("1. RENTAS BRUTAS", base), "", ""]]
    for r in c["rubros"]:
        filas.append(fila(r["etiqueta"], monto(r["monto"]) if r["monto"] != 0 else ""))
    filas += [fila("<b>REMUNERACION BRUTA TOTAL</b>", f"<b>{monto(c['remuneracion_bruta'])}</b>", neg)]
    t1 = Table(filas, colWidths=cw)
    t1.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, colors.black), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (0, -1), 22), ("RIGHTPADDING", (2, 0), (2, -1), 22),
                            ("TOPPADDING", (0, 0), (-1, -1), 1.3), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.3), ("LEFTPADDING", (0, 0), (0, 0), 14)]))
    el += [t1, Spacer(1, 3 * mm)]
    def caja2(filas_, anchos=None):
        t = Table(filas_, colWidths=anchos or cw)
        t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, colors.black), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("LEFTPADDING", (0, 0), (0, -1), 14), ("RIGHTPADDING", (2, 0), (2, -1), 14),
                               ("TOPPADDING", (0, 0), (-1, -1), 1.8), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.8)]))
        return t
    el += [caja2([[Paragraph("2. DEDUCCIONES SOBRE LA RENTA DE 5TA. CATEGORIA<br/>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;" + f"{c['deduccion_uit']:.0f} UIT (Unidades Impositivas Tributarias)", base), Paragraph("S/.", base), Paragraph(f"&lt; &nbsp;{monto(c['deduccion'])} &nbsp;&gt;", num)]], cwd), Spacer(1, 2 * mm)]
    el += [caja2([fila("3. RENTA NETA IMPONIBLE (1 - 2)", monto(c["renta_neta"]))]), Spacer(1, 2 * mm)]
    el += [caja2([fila("4. IMPUESTO A LA RENTA", monto(c["impuesto"]))]), Spacer(1, 2 * mm)]
    par = lambda v: f"(&nbsp;{v}&nbsp;)"
    el += [caja2([[Paragraph("5. CREDITOS CONTRA EL IMPUESTO", base), Paragraph("S/. &nbsp;" + par(monto(c["creditos"])), base), Paragraph(monto(c["creditos"]), num)],
                  ["", Paragraph("S/. &nbsp;" + par("&nbsp;" * 8), base), ""]], cw2), Spacer(1, 2 * mm)]
    el += [caja2([[Paragraph("6. TOTAL IMPUESTO RETENIDO (4 - 5)", base), Paragraph("S/. &nbsp;" + par(monto(c["retenido_empresa"])), base), Paragraph(monto(c["retenido_total"]), num)],
                  ["", Paragraph("S/. &nbsp;" + par(monto(c["retenido_otros"]) if c["retenido_otros"] else "&nbsp;" * 8), base), ""]], cw2), Spacer(1, 2 * mm)]
    el += [caja2([[Paragraph("7. DEVOLUCION DE 5TA", base), Paragraph("S/. &nbsp;" + par(monto(c["devolucion"])), base), Paragraph(monto(c["devolucion"]), num)],
                  ["", Paragraph("S/. &nbsp;" + par("0.00"), base), ""]], cw2), Spacer(1, 14 * mm)]
    e, t = c["empresa"], c["trabajador"]
    huella = Table([[""]], colWidths=[17 * mm], rowHeights=[22 * mm])
    huella.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999"))]))
    izq = [Paragraph(f"<b>{e['razon_social']}</b>", ctr2), Paragraph(e["representante"] or "", ctr2)]
    der_ = [Paragraph(f"<b>{t['nombre']}</b>", ctr2), Paragraph(t["puesto"] or "", ctr2), Paragraph(f"DNI {t['dni']}", ctr2)]
    firmas = Table([["", "", "", huella], [izq, "", der_, ""]], colWidths=[W * 0.44, W * 0.04, W * 0.38, W * 0.14], rowHeights=[22 * mm, None])
    firmas.setStyle(TableStyle([("LINEABOVE", (0, 1), (0, 1), 0.6, colors.black), ("LINEABOVE", (2, 1), (2, 1), 0.6, colors.black), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("VALIGN", (3, 0), (3, 0), "BOTTOM")]))
    el.append(firmas)

    def marca(canvas, _):
        if not c["definitivo"]:
            canvas.saveState(); canvas.setFont("Helvetica-Bold", 70); canvas.setFillColorRGB(0.88, 0.88, 0.88)
            canvas.translate(105 * mm, 120 * mm); canvas.rotate(45); canvas.drawCentredString(0, 0, "BORRADOR"); canvas.restoreState()
    doc.build(el, onFirstPage=marca, onLaterPages=marca)
    return buf.getvalue()
