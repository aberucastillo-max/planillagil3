"""Carga masiva de NOVEDADES del periodo (conceptos variables por colaborador) desde Excel.

Mismas reglas que la carga masiva de personal: revisión previa fila por fila sin guardar nada y carga TODO O NADA.
Cada fila: DNI + código del concepto + monto. Las columnas Año y Mes son opcionales: si vienen llenas deben coincidir con el periodo elegido
(así no se carga por error el archivo de otro mes). Si la novedad ya existe en el periodo (mismo colaborador y concepto) se ACTUALIZA."""
import io
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter as L
from openpyxl.worksheet.properties import PageSetupProperties

from . import liquidacion as LQ
from .carga_masiva import _norm, _texto
from .diccionario import ErrorDeNegocio
from .models import Concepto, Empleado, Novedad

MAX_FILAS = 2000
MESES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
ALIAS = {"DNI": "dni", "NDEDOCUMENTO": "dni", "DOCUMENTO": "dni", "CODIGO": "codigo", "CODIGODELCONCEPTO": "codigo", "CODIGOCONCEPTO": "codigo", "CONCEPTO": "codigo",
         "MONTO": "monto", "MONTOS": "monto", "VALOR": "monto", "ANO": "anio", "ANIO": "anio", "MES": "mes"}
OBLIGATORIAS = {"dni": "DNI", "codigo": "Código del concepto", "monto": "Monto (S/)"}


def _monto(v):
    """(Decimal|None, error|None): hasta 2 decimales; acepta número de Excel o texto con coma o punto."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, "Falta el monto."
    if isinstance(v, (int, float)):
        d = Decimal(str(v))
    else:
        s = re.sub(r"(?i)s/\.?|\s", "", str(v))
        s = s.replace(",", ".") if ("," in s and "." not in s) else s.replace(",", "")
        try:
            d = Decimal(s)
        except InvalidOperation:
            return None, f"Monto no numérico «{v}»."
    cent = d * 100
    if abs(cent - cent.to_integral_value()) > Decimal("0.0000001"):
        return None, "El monto tiene más de 2 decimales."
    return d.quantize(Decimal("0.01")), None


def _mes(v):
    s = _texto(v).lower()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().replace("setiembre", "septiembre")
    return MESES.index(s) if s in MESES else -1


def leer_excel(contenido: bytes) -> list:
    try:
        wb = load_workbook(io.BytesIO(contenido), data_only=True)
    except Exception:
        raise ErrorDeNegocio("El archivo no es un Excel (.xlsx) válido.")
    hoja = next((wb[n] for n in wb.sheetnames if _norm(n) == "NOVEDADES"), None)
    if hoja is None:
        raise ErrorDeNegocio("No se encontró la hoja «Novedades». Use la plantilla descargada desde el sistema.")
    enc = {}
    for j, c in enumerate(next(hoja.iter_rows(min_row=1, max_row=1)), 1):
        k = ALIAS.get(_norm(c.value))
        if k and k not in enc:
            enc[k] = j
    faltan = [et for k, et in OBLIGATORIAS.items() if k not in enc]
    if faltan:
        raise ErrorDeNegocio("Faltan columnas obligatorias en la hoja «Novedades»: " + ", ".join(faltan) + ". No cambie los encabezados de la plantilla.")
    filas = []
    for r in range(2, hoja.max_row + 1):
        v = {k: hoja.cell(r, j).value for k, j in enc.items()}
        if all(_texto(x) == "" for x in v.values()) or _texto(v.get("dni")).upper().startswith("EJEMPLO"):
            continue
        v["_fila"] = r
        filas.append(v)
    if not filas:
        raise ErrorDeNegocio("El archivo no tiene novedades para cargar (desde la fila 3 de la hoja «Novedades»).")
    if len(filas) > MAX_FILAS:
        raise ErrorDeNegocio(f"El archivo tiene {len(filas)} filas: el máximo es {MAX_FILAS} por carga.")
    return filas


def analizar(db, empresa, periodo, filas: list):
    conceptos = {c.codigo: c for c in db.query(Concepto).filter(Concepto.diccionario_id == periodo.diccionario_id)}
    emps = {e.documento_identidad: e for e in db.query(Empleado).filter(Empleado.empresa_id == empresa.id)}
    bloqueados = LQ.bloqueados_del_periodo(db, periodo.id)
    existentes = {(n.empleado_id, n.concepto_codigo): n for n in db.query(Novedad).filter(Novedad.periodo_id == periodo.id)}
    informe, ops, vistos = [], [], {}
    for v in filas:
        err, avi = [], []
        doc = _texto(v.get("dni"))
        if re.fullmatch(r"\d{1,7}", doc) and doc.zfill(8) in emps and doc not in emps:
            doc = doc.zfill(8); avi.append("El DNI tenía menos de 8 dígitos: se completó con ceros a la izquierda.")
        emp = emps.get(doc)
        cod = _texto(v.get("codigo")).upper().replace(" ", "")
        if cod and not cod.startswith("@") and ("@" + cod) in conceptos:
            cod = "@" + cod
        c = conceptos.get(cod)
        monto, e_m = _monto(v.get("monto"))
        if not doc:
            err.append("Falta el DNI.")
        elif emp is None:
            err.append("DNI inexistente en esta empresa.")
        elif emp.id in bloqueados:
            err.append("Liquidación CERRADA: su boleta está bloqueada.")
        if not cod:
            err.append("Falta el código del concepto.")
        elif c is None:
            err.append(f"Código de concepto inválido «{cod}» (vea la hoja «Conceptos» de la plantilla).")
        elif c.calculado:
            err.append("Concepto calculado por el motor: no admite carga.")
        if e_m:
            err.append(e_m)
        anio = _texto(v.get("anio")); mes = _mes(v.get("mes"))
        if anio and (not anio.isdigit() or int(anio) != periodo.anio):
            err.append(f"El año {anio} no coincide con el periodo elegido ({periodo.anio}).")
        if mes is not None and mes != periodo.mes:
            err.append(f"El mes «{_texto(v.get('mes'))}» no coincide con el periodo elegido ({MESES[periodo.mes].capitalize()}).")
        clave = (emp.id if emp else doc, cod)
        if clave in vistos:
            err.append(f"Colaborador y concepto repetidos: ya aparecen en la fila {vistos[clave]}.")
        else:
            vistos[clave] = v["_fila"]
        if emp is not None and emp.fecha_cese is not None and emp.fecha_cese < periodo.fecha_inicio:
            avi.append("El colaborador cesó antes de este periodo: no entra en su planilla.")
        previo = existentes.get((emp.id, cod)) if emp else None
        fila = {"fila": v["_fila"], "documento": doc, "colaborador": f"{emp.apellidos}, {emp.nombres}" if emp else "", "codigo": cod,
                "descripcion": c.descripcion if c else "", "monto": str(monto) if monto is not None else _texto(v.get("monto")),
                "accion": "ACTUALIZAR" if previo else "NUEVA", "antes": str(previo.valor) if previo else "", "errores": err, "avisos": avi}
        fila["estado"] = "ERROR" if err else ("AVISO" if avi else "OK")
        informe.append(fila)
        if not err:
            ops.append((emp.id, cod, monto))
    res = {"total": len(informe), "nuevas": sum(1 for f in informe if f["accion"] == "NUEVA" and not f["errores"]),
           "actualizadas": sum(1 for f in informe if f["accion"] == "ACTUALIZAR" and not f["errores"]),
           "con_aviso": sum(1 for f in informe if f["estado"] == "AVISO"), "con_error": sum(1 for f in informe if f["estado"] == "ERROR")}
    return {"resumen": res, "filas": informe, "puede_cargar": res["con_error"] == 0 and res["total"] > 0}, ops


def aplicar(db, periodo, ops: list) -> dict:
    nuevas = actualizadas = 0
    try:
        for emp_id, cod, valor in ops:
            n = db.query(Novedad).filter(Novedad.periodo_id == periodo.id, Novedad.empleado_id == emp_id, Novedad.concepto_codigo == cod).first()
            if n:
                n.valor = valor; actualizadas += 1
            else:
                db.add(Novedad(periodo_id=periodo.id, empleado_id=emp_id, concepto_codigo=cod, valor=valor)); nuevas += 1
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"guardadas": nuevas + actualizadas, "nuevas": nuevas, "actualizadas": actualizadas}


def plantilla(periodo, conceptos: list, empleados: list) -> bytes:
    """conceptos: [(código, descripción, categoría)]; empleados: [(documento, nombre, estado)]."""
    AZUL, GRIS, BLANCO = "1F4FBF", "64748B", "FFFFFF"
    F = lambda **k: Font(name="Arial", size=k.pop("size", 10), **k)
    fill = lambda c: PatternFill("solid", start_color=c, end_color=c)
    borde = Border(*(Side(style="thin", color="CBD5E1"),) * 4)
    wb = Workbook(); w = wb.active; w.title = "Léeme"; w.sheet_view.showGridLines = False
    w.column_dimensions["A"].width = 4; w.column_dimensions["B"].width = 26; w.column_dimensions["C"].width = 92
    w["B2"] = "Planilla Ágil · Plantilla de carga masiva de novedades"; w["B2"].font = F(size=15, bold=True, color=AZUL)
    w["B3"] = f"Periodo: {MESES[periodo.mes].capitalize()} {periodo.anio}. Todo o nada: con un solo error no se carga ninguna fila."; w["B3"].font = F(italic=True, color=GRIS)
    pasos = ["Llene la hoja «Novedades»: una fila por colaborador y concepto, desde la fila 3. La fila 2 es un ejemplo y se ignora (puede borrarla).",
             "Obligatorias (encabezado azul): DNI, Código del concepto y Monto. Año y Mes son opcionales: si los llena deben coincidir con el periodo elegido.",
             "Los códigos permitidos están en la hoja «Conceptos» (solo los que el motor no calcula). Puede escribirlos con o sin «@».",
             "Monto con hasta 2 decimales. Si el colaborador ya tiene esa novedad en el periodo, se actualiza con el monto del archivo.",
             "En Planilla Ágil: Cálculo de Planilla → Novedades del periodo → Masivo Conceptos → suba el archivo. Se revisa fila por fila y solo se guarda al confirmar.",
             "Después de cargar, use «Procesar Planilla» para recalcular."]
    w["B5"] = "CÓMO USARLA"; w["B5"].font = F(bold=True, color=AZUL)
    for i, t in enumerate(pasos, 1):
        w.cell(5 + i, 2, f"{i}.").font = F(bold=True); w.cell(5 + i, 2).alignment = Alignment(vertical="top", horizontal="right")
        w.cell(5 + i, 3, t).font = F(); w.cell(5 + i, 3).alignment = Alignment(wrap_text=True, vertical="top")
    n = wb.create_sheet("Novedades"); n.freeze_panes = "A3"
    cols = [("Año", False, periodo.anio, "Opcional. Si lo llena debe ser el año del periodo."), ("Mes", False, MESES[periodo.mes].capitalize(), "Opcional. Número o nombre del mes del periodo."),
            ("DNI *", True, "EJEMPLO 45892134", "Obligatorio. Documento del colaborador (escríbalo como texto)."), ("Código del concepto *", True, conceptos[0][0] if conceptos else "@COMISIONES", "Obligatorio. Vea la hoja «Conceptos»."),
            ("Monto (S/) *", True, 350.50, "Obligatorio. Hasta 2 decimales.")]
    for j, (t, ob, ej, nota) in enumerate(cols, 1):
        c = n.cell(1, j, t); c.font = F(bold=True, color=BLANCO); c.fill = fill(AZUL if ob else GRIS); c.alignment = Alignment(horizontal="center", vertical="center"); c.border = borde
        c.comment = Comment(nota, "Planilla Ágil"); e = n.cell(2, j, ej); e.font = F(italic=True, color=GRIS); e.fill = fill("F1F5F9"); e.border = borde
        n.column_dimensions[L(j)].width = 24 if j != 3 else 22
    for r in range(2, 502):
        n.cell(r, 3).number_format = "@"; n.cell(r, 5).number_format = "#,##0.00"
    k = wb.create_sheet("Conceptos")
    for j, t in enumerate(["Código", "Descripción", "Categoría"], 1):
        c = k.cell(1, j, t); c.font = F(bold=True, color=BLANCO); c.fill = fill(AZUL)
    for i, (cod, desc, cat) in enumerate(conceptos, 2):
        for j, v in enumerate([cod, desc, cat], 1): k.cell(i, j, v).font = F()
    k.column_dimensions["A"].width = 30; k.column_dimensions["B"].width = 52; k.column_dimensions["C"].width = 16
    m = wb.create_sheet("Colaboradores")
    for j, t in enumerate(["DNI", "Colaborador", "Estado"], 1):
        c = m.cell(1, j, t); c.font = F(bold=True, color=BLANCO); c.fill = fill(AZUL)
    for i, (doc, nom, est) in enumerate(empleados, 2):
        for j, v in enumerate([doc, nom, est], 1): m.cell(i, j, v).font = F()
    m.column_dimensions["A"].width = 16; m.column_dimensions["B"].width = 44; m.column_dimensions["C"].width = 12
    for ws in wb.worksheets:
        ws.page_setup.orientation = "landscape"; ws.page_setup.fitToWidth = 1; ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    buf = io.BytesIO(); wb.save(buf)
    return buf.getvalue()
