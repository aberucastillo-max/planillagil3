"""Orquestador del cálculo mensual. Lee el Diccionario, aplica reglas.py y guarda detalle + resultado + snapshot."""
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session

from . import reglas as R
from .diccionario import ErrorDeNegocio, ParametrosPeriodo, cargar_parametros
from .models import (Auditoria, Ausentismo, Beneficio, Detalle, Diccionario, Empleado, Empresa, Novedad,
                     Periodo, RentaPrevia5ta, Resultado, Sueldo, Vacacion)

CERO = Decimal("0")
HANDLED_NOVEDADES = {"@HEXT_25", "@HEXT_35", "@DIAS_TRABAJADOS"}   # tienen tratamiento especial
EXTRAS_5TA = ("@GRATIFICACION", "@BONIF_EXTRA_29351")              # no se multiplican por meses restantes


def D(x) -> Decimal:
    return R.D(str(x)) if not isinstance(x, Decimal) else x


def exigir_abierto(periodo: Periodo):
    if periodo.estado == "CERRADO":
        raise ErrorDeNegocio("Error: El periodo está CERRADO. La información histórica es inmutable.", 403)


def sueldo_a_fecha(db: Session, empleado_id: int, fecha: date) -> Optional[Decimal]:
    s = (db.query(Sueldo).filter(Sueldo.empleado_id == empleado_id, Sueldo.fecha_vigencia <= fecha)
         .order_by(Sueldo.fecha_vigencia.desc()).first())
    return D(s.monto) if s else None


def _historial_essalud(db, emp, periodo, pr) -> List[dict]:
    idx = periodo.anio * 12 + periodo.mes
    por_mes: Dict[int, dict] = {}
    q = (db.query(Detalle, Periodo).join(Periodo, Periodo.id == Detalle.periodo_id)
         .filter(Detalle.empleado_id == emp.id, Periodo.empresa_id == periodo.empresa_id,
                 Detalle.categoria == "INGRESO"))
    for d, p in q:
        i = p.anio * 12 + p.mes
        if not (idx - 12 <= i < idx):
            continue
        m = por_mes.setdefault(i, {"fijo": CERO, "variables": defaultdict(lambda: CERO), "subsidios": CERO})
        c = pr.conceptos.get(d.concepto_codigo)
        if d.concepto_codigo == "@SUBS_ENFERMEDAD":
            m["subsidios"] += D(d.monto)
        elif c and c.afecta_essalud:
            if c.naturaleza == "VARIABLE":
                m["variables"][d.concepto_codigo] += D(d.monto)
            else:
                m["fijo"] += D(d.monto)
    return [por_mes[i] for i in sorted(por_mes)]


FASE_PRINCIPAL = "Fin de Mes"
FASE_QUINCENA = "Quincena"


def periodos_vigentes(db: Session, empresa_id: int) -> dict:
    """Periodos que cuentan para las acumulaciones (5ta, promedios de variables, última gratificación, liquidación): solo «Fin de Mes».
    La quincena es un ADELANTO (no calcula aportes, 5ta ni bases) y nunca se acumula: Fin de Mes la descuenta en su boleta."""
    return {(p.anio, p.mes): p for p in db.query(Periodo).filter(Periodo.empresa_id == empresa_id, Periodo.fase == FASE_PRINCIPAL).order_by(Periodo.id)}


def ids_vigentes(db: Session, empresa_id: int) -> set:
    return {p.id for p in periodos_vigentes(db, empresa_id).values()}


def periodo_vigente(db: Session, periodo: Periodo) -> Periodo:
    return periodos_vigentes(db, periodo.empresa_id).get((periodo.anio, periodo.mes), periodo)


def _previos_5ta(db, emp, periodo):
    base, ret = (db.query(func.coalesce(func.sum(Resultado.base_5ta_regular), 0),
                          func.coalesce(func.sum(Resultado.ret_5ta_regular), 0))
                 .join(Periodo, Periodo.id == Resultado.periodo_id)
                 .filter(Resultado.empleado_id == emp.id, Periodo.empresa_id == periodo.empresa_id,
                         Periodo.anio == periodo.anio, Periodo.mes < periodo.mes,
                         Periodo.id.in_(ids_vigentes(db, periodo.empresa_id))).one())
    otros_i, otros_r = (db.query(func.coalesce(func.sum(RentaPrevia5ta.ingreso_afecto), 0),
                                 func.coalesce(func.sum(RentaPrevia5ta.retencion), 0))
                        .filter(RentaPrevia5ta.empleado_id == emp.id, RentaPrevia5ta.anio == periodo.anio,
                                RentaPrevia5ta.mes <= periodo.mes).one())
    return D(base), D(ret), D(otros_i), D(otros_r)


def recalcular_quincena(db: Session, periodo: Periodo) -> dict:
    """PROCESO DE QUINCENA: genera el ADELANTO de cada colaborador vigente al día 15 (porcentaje del sueldo o monto fijo, según lo
    configurado en la empresa). No calcula AFP/ONP, EsSalud ni 5ta: eso lo hace el proceso de planilla (Fin de Mes), que descuenta
    este adelanto como @QUINCENA."""
    exigir_abierto(periodo)
    empresa = db.get(Empresa, periodo.empresa_id)
    if not empresa.usa_quincena:
        raise ErrorDeNegocio("Esta empresa no tiene activada la quincena (adelanto): actívela en «Datos de la empresa».", 409)
    dic = db.get(Diccionario, periodo.diccionario_id)
    dia15 = date(periodo.anio, periodo.mes, 15)
    pct = empresa.quincena_modo == "PORCENTAJE"
    valor = D(empresa.quincena_valor)
    db.query(Detalle).filter(Detalle.periodo_id == periodo.id).delete(synchronize_session=False)
    db.query(Resultado).filter(Resultado.periodo_id == periodo.id).delete(synchronize_session=False)
    alertas: List[str] = []
    n = 0
    for emp in (db.query(Empleado).filter(Empleado.empresa_id == periodo.empresa_id, Empleado.fecha_ingreso <= periodo.fecha_fin,
                                          or_(Empleado.fecha_cese.is_(None), Empleado.fecha_cese >= periodo.fecha_inicio))
                .order_by(Empleado.apellidos, Empleado.nombres)):
        nombre = f"{emp.apellidos}, {emp.nombres}"
        if emp.fecha_ingreso > dia15:
            alertas.append(f"{nombre}: ingresó después del 15, no recibe adelanto de quincena.")
            continue
        if emp.fecha_cese is not None and emp.fecha_cese < dia15:
            alertas.append(f"{nombre}: cesó antes del 15, no recibe adelanto de quincena.")
            continue
        sueldo = sueldo_a_fecha(db, emp.id, max(periodo.fecha_inicio, emp.fecha_ingreso))
        if sueldo is None:
            alertas.append(f"{nombre}: sin sueldo vigente, no se calculó su adelanto.")
            continue
        adelanto = R.q2(sueldo * valor / 100) if pct else R.q2(valor)
        if adelanto <= 0:
            continue
        formula = f"{sueldo} × {valor:.2f} % del sueldo" if pct else f"monto fijo S/ {adelanto}"
        db.add(Detalle(periodo_id=periodo.id, empleado_id=emp.id, concepto_codigo="@ADELANTO_QUINCENA", categoria="INGRESO",
                       monto=adelanto, origen="CALCULO", formula=formula))
        db.add(Resultado(periodo_id=periodo.id, empleado_id=emp.id, remuneracion_computable=adelanto, total_descuentos=CERO,
                         neto_a_pagar=adelanto, snapshot={"colaborador": nombre, "documento": emp.documento_identidad, "puesto": emp.puesto,
                                                          "sede": emp.sede, "banco": emp.banco, "cuenta": emp.numero_cuenta, "sueldo": str(sueldo),
                                                          "tipo": "ADELANTO_QUINCENA", "diccionario": f"{dic.codigo} v{dic.version}",
                                                          "modo": empresa.quincena_modo, "valor": str(valor)}))
        if not emp.numero_cuenta:
            alertas.append(f"{nombre}: sin cuenta de abono.")
        n += 1
    if n == 0:
        raise ErrorDeNegocio("No hay colaboradores con adelanto de quincena en este mes.", 409)
    db.commit()
    alertas.append("La quincena es un adelanto: no calcula aportes ni 5ta. Después de procesarla (o corregirla), vuelva a procesar la planilla de fin de mes para que la descuente.")
    return {"colaboradores": n, "alertas": alertas, "bloqueados_por_liquidacion": 0, "tipo": "ADELANTO"}


def recalcular_periodo(db: Session, periodo: Periodo) -> dict:
    if periodo.fase == FASE_QUINCENA:
        return recalcular_quincena(db, periodo)
    exigir_abierto(periodo)
    dic = db.get(Diccionario, periodo.diccionario_id)
    pr: ParametrosPeriodo = cargar_parametros(db, periodo.diccionario_id, periodo.fecha_inicio, empresa_id=periodo.empresa_id, periodo_id=periodo.id)
    fi, ff = periodo.fecha_inicio, periodo.fecha_fin
    base = int(pr["BASE_DIAS"])

    empleados: List[Empleado] = (
        db.query(Empleado)
        .filter(Empleado.empresa_id == periodo.empresa_id, Empleado.fecha_ingreso <= ff,
                or_(Empleado.fecha_cese.is_(None), Empleado.fecha_cese >= fi))
        .order_by(Empleado.apellidos, Empleado.nombres).all())
    if not empleados:
        raise ErrorDeNegocio("No hay colaboradores vigentes en este periodo.", 409)

    # sueldo: se toma a la fecha de inicio del periodo (o a la de ingreso si ingresó dentro del mes)
    sueldos, sin_sueldo = {}, []
    for e in empleados:
        s = sueldo_a_fecha(db, e.id, max(fi, e.fecha_ingreso))
        if s is None:
            sin_sueldo.append(f"{e.apellidos}, {e.nombres} ({e.documento_identidad})")
        sueldos[e.id] = s
    if sin_sueldo:
        raise ErrorDeNegocio("Colaboradores sin sueldo vigente: " + "; ".join(sin_sueldo), 422)

    novedades: Dict[int, Dict[str, Decimal]] = defaultdict(dict)
    for n in db.query(Novedad).filter(Novedad.periodo_id == periodo.id):
        c = pr.conceptos.get(n.concepto_codigo)
        if c is None:
            raise ErrorDeNegocio(f"Novedad con concepto inexistente en el Diccionario: {n.concepto_codigo}", 422)
        if c.calculado:
            raise ErrorDeNegocio(f"{n.concepto_codigo} lo calcula el motor; no admite carga manual.", 422)
        novedades[n.empleado_id][n.concepto_codigo] = D(n.valor)

    from .liquidacion import bloqueados_del_periodo     # import local: liquidacion importa este módulo
    bloqueados = bloqueados_del_periodo(db, periodo.id)  # liquidación CERRADA => su boleta queda intacta
    q_det = db.query(Detalle).filter(Detalle.periodo_id == periodo.id)
    q_res = db.query(Resultado).filter(Resultado.periodo_id == periodo.id)
    if bloqueados:
        q_det = q_det.filter(Detalle.empleado_id.notin_(bloqueados))
        q_res = q_res.filter(Resultado.empleado_id.notin_(bloqueados))
    q_det.delete(synchronize_session=False)
    q_res.delete(synchronize_session=False)

    alertas: List[str] = []
    # adelantos de quincena del mes (si la empresa los usa): se descuentan en esta boleta como @QUINCENA
    quincena_pagada: Dict[int, Decimal] = {}
    for r_q, p_q in (db.query(Resultado, Periodo).join(Periodo, Periodo.id == Resultado.periodo_id)
                     .filter(Periodo.empresa_id == periodo.empresa_id, Periodo.anio == periodo.anio, Periodo.mes == periodo.mes,
                             Periodo.fase == FASE_QUINCENA)):
        quincena_pagada[r_q.empleado_id] = D(r_q.neto_a_pagar)
    for emp in empleados:
        if emp.id in bloqueados:
            continue
        nombre = f"{emp.apellidos}, {emp.nombres}"
        nov = novedades.get(emp.id, {})
        sueldo = sueldos[emp.id]
        filas: List[dict] = []

        def add(codigo, monto, formula="", origen="CALCULO"):
            c = pr.conceptos[codigo]
            m = R.q2(monto)
            if m != 0:
                filas.append({"codigo": codigo, "categoria": c.categoria, "monto": m,
                              "formula": formula, "origen": origen})

        def bolsa(flag):
            return sum((f["monto"] for f in filas
                        if f["categoria"] == "INGRESO" and getattr(pr.conceptos[f["codigo"]], flag)), CERO)

        # ---- días (base 30)
        dvinc = R.dias_vinculo(emp.fecha_ingreso, emp.fecha_cese, fi, ff, base)
        dias_aus: Dict[str, int] = defaultdict(int)
        for a in db.query(Ausentismo).filter(Ausentismo.empleado_id == emp.id, Ausentismo.estado == "REGISTRADO",
                                             Ausentismo.fecha_inicio <= ff, Ausentismo.fecha_fin >= fi):
            ts = pr.suspensiones.get(a.tipo_suspension_id)
            if ts is None:
                raise ErrorDeNegocio(f"Ausentismo {a.id} con tipo de suspensión fuera del Diccionario.", 422)
            dias_aus[ts.concepto_codigo] += R.dias_calendario_solapados(a.fecha_inicio, a.fecha_fin, fi, ff)
        dias_vac = sum(R.dias_calendario_solapados(v.fecha_inicio, v.fecha_fin, fi, ff)
                       for v in db.query(Vacacion).filter(Vacacion.empleado_id == emp.id,
                                                          Vacacion.estado == "REGISTRADO",
                                                          Vacacion.fecha_inicio <= ff, Vacacion.fecha_fin >= fi))
        faltas, susp = dias_aus["@DIAS_FALTAS"], dias_aus["@DIAS_SUSPENSION"]
        sin_goce, subs = dias_aus["@DIAS_LIC_SIN_GOCE"], dias_aus["@DIAS_SUBSIDIO"]
        dmed, con_goce = dias_aus["@DIAS_DMEDICO"], dias_aus["@DIAS_LIC_CON_GOCE"]
        if "@DIAS_TRABAJADOS" in nov:
            dias_trab = int(nov["@DIAS_TRABAJADOS"])
        else:
            dias_trab = max(dvinc - faltas - susp - sin_goce - subs - dias_vac, 0)

        add("@SUELDO_TEORICO", sueldo)
        add("@DIAS_TRABAJADOS", dias_trab)
        for cod, v in (("@DIAS_FALTAS", faltas), ("@DIAS_SUSPENSION", susp), ("@DIAS_LIC_SIN_GOCE", sin_goce),
                       ("@DIAS_SUBSIDIO", subs), ("@DIAS_DMEDICO", dmed), ("@DIAS_LIC_CON_GOCE", con_goce)):
            add(cod, v)

        # ---- ingresos calculados
        add("@HABER_BASICO", sueldo / base * dias_trab, f"{sueldo} / {base} x {dias_trab} días")
        af_mensual = R.q2(pr["RMV"] * pr["ASIG_FAM_PCT"]) if emp.asignacion_familiar else CERO
        if af_mensual and (dias_trab + dias_vac) > 0:
            add("@ASIGNACION_FAMILIAR", af_mensual / base * (dias_trab + dias_vac),
                f"{af_mensual} / {base} x {dias_trab + dias_vac} días (10 % RMV)")
        if dias_vac:
            add("@REMUNERA_VACACIONAL", sueldo / base * dias_vac, f"{sueldo} / {base} x {dias_vac} días de vacaciones")
        vh = R.valor_hora(sueldo, af_mensual, base, int(pr["HORAS_DIA"]))
        h25, h35 = nov.get("@HEXT_25", CERO), nov.get("@HEXT_35", CERO)
        if h25:
            add("@HORAS_EXTRAS_25", R.monto_horas_extras(h25, vh, pr["HE_RECARGO_1"]),
                f"{h25} h x {vh:.4f} x {1 + pr['HE_RECARGO_1']}")
        if h35:
            add("@HORAS_EXTRAS_35", R.monto_horas_extras(h35, vh, pr["HE_RECARGO_2"]),
                f"{h35} h x {vh:.4f} x {1 + pr['HE_RECARGO_2']}")
        if h25 or h35:
            add("@HEXT_25", h25), add("@HEXT_35", h35)
        if subs:
            bd = R.base_diaria_subsidio(_historial_essalud(db, emp, periodo, pr), sueldo)
            add("@SUBS_ENFERMEDAD", bd * subs, f"base diaria {bd} x {subs} días (12 meses / 360)")

        # ---- novedades (cualquier concepto del Diccionario; ya no se descartan en silencio)
        for cod, val in nov.items():
            if cod in HANDLED_NOVEDADES:
                continue
            add(cod, val, "Novedad cargada", origen="NOVEDAD")

        # ---- adelanto de quincena ya pagado: se descuenta (salvo que el usuario cargue @QUINCENA a mano como novedad)
        if "@QUINCENA" not in nov and quincena_pagada.get(emp.id):
            add("@QUINCENA", quincena_pagada[emp.id], "Adelanto de quincena pagado en el mes")

        # ---- gratificación y bonificación de julio y diciembre (si el usuario no las cargó como novedad)
        if "@GRATIFICACION" not in nov and "@BONIF_EXTRA_29351" not in nov:
            from .semestrales import gratificacion_automatica     # import local: semestrales importa este módulo
            g = gratificacion_automatica(db, emp, periodo, pr)
            if g:
                add("@GRATIFICACION", g["grat"], g["formula"])
                add("@BONIF_EXTRA_29351", g["bonif"], g["formula_bonif"])

        # ---- bolsas acumuladoras
        base_prev, base_ess = bolsa("afecta_afp_onp"), bolsa("afecta_essalud")
        base_5ta, base_dir = bolsa("afecta_5ta_regular"), bolsa("afecta_5ta_directa")

        # ---- sistema pensionario
        if emp.sistema_pensionario == "AFP":
            afp = pr.afps.get((emp.afp_nombre or "").upper())
            if afp is None:
                raise ErrorDeNegocio(f"{nombre}: AFP '{emp.afp_nombre}' no existe en el Diccionario.", 422)
            tope_prima = pr.p.get("RMA_PRIMA", afp.tope_prima)        # remuneración máxima asegurable vigente (Diccionario)
            com = afp.comision_planilla(emp.tipo_comision)       # flujo, o el componente mensual de la mixta (la de saldo la cobra la AFP)
            ap, co, pri = R.calcular_afp(base_prev, afp.aporte, com, afp.prima, tope_prima)
            add("@FONDO_PENSIONES", ap, f"{base_prev} x {afp.aporte}")
            add("@COMISION_VARIABLE", co, f"{base_prev} x {com}" + (" (comisión mixta: componente mensual)" if emp.tipo_comision == "MIXTA" else " (comisión sobre flujo)"))
            add("@SEGURO_INVALIDEZ", pri, f"min({base_prev}, tope {tope_prima}) x {afp.prima}")
            if not emp.cuspp:
                alertas.append(f"{nombre}: falta CUSPP (AFPnet).")
        else:
            add("@SNP_ONP", R.calcular_onp(base_prev, pr["ONP_TASA"]), f"{base_prev} x {pr['ONP_TASA']}")

        # ---- quinta categoría
        extra = sum((f["monto"] for f in filas if f["codigo"] in EXTRAS_5TA), CERO)
        prev_emp, ret_emp, prev_otros, ret_otros = _previos_5ta(db, emp, periodo)
        reg = R.retencion_5ta_regular(
            mes=periodo.mes, base_ordinaria_mes=base_5ta - extra, extra_mes=extra,
            gratificacion_proy=R.gratificacion_proyectada(sueldo, periodo.mes),
            previos_empresa=prev_emp, retenido_empresa=ret_emp, previos_otros=prev_otros,
            retenido_otros=ret_otros, uit=pr["UIT"], deduccion_uit=pr["DEDUCCION_5TA_UIT"],
            deduccion_adicional_uit=pr["DEDUCCION_ADICIONAL_5TA_UIT"], escala=pr.escala)
        directa = R.q2(base_dir * pr["TASA_5TA_DIRECTA"])
        if emp.fecha_cese is not None and fi <= emp.fecha_cese <= ff:
            # REGLA DEL DUEÑO: en la boleta del mes del cese NO se retiene la 5ta regular; todo se aplica en la liquidación
            # (la base del mes sí cuenta en el acumulado, así que el ajuste final de la liquidación es el correcto).
            reg = CERO
            alertas.append(f"{nombre}: cesado, la retención de 5ta categoría del mes se aplica en su liquidación (ciérrela para que se descuente).")
        add("@IMP_RENTA_5TA", reg + directa, f"regular {reg} + directa {directa}")

        # ---- descuento EPS del trabajador (Beneficios vigentes en el periodo)
        eps = sum((D(b.monto) for b in db.query(Beneficio).filter(
            Beneficio.empleado_id == emp.id, Beneficio.tipo == "EPS", Beneficio.fecha_inicio <= ff,
            or_(Beneficio.fecha_termino.is_(None), Beneficio.fecha_termino >= fi))), CERO)
        add("@DESCUENTO_EPS", eps, "Beneficio EPS vigente")

        # ---- aportes del empleador (siempre separados)
        rmv_min = R.rmv_minima_essalud(pr["RMV"], subs)          # mínimo = RMV, prorrateado por días de subsidio
        ess, aeps = R.calcular_essalud(base_ess, rmv_min, pr["ESSALUD_TASA"], pr["CREDITO_EPS_TASA"],
                                       emp.cobertura_eps)
        add("@ESSALUD", ess, f"base {base_ess} (mín. {rmv_min})")
        add("@APORTE_EPS", aeps, "Crédito EPS")

        # ---- totales
        ingresos = sum((f["monto"] for f in filas if f["categoria"] == "INGRESO"), CERO)
        descuentos = sum((f["monto"] for f in filas if f["categoria"] == "DESCUENTO"), CERO)
        aportes = sum((f["monto"] for f in filas if f["categoria"] == "APORTE"), CERO)
        neto = ingresos - descuentos
        if neto <= 0:
            alertas.append(f"{nombre}: neto a pagar {neto}.")
        if not emp.numero_cuenta:
            alertas.append(f"{nombre}: sin cuenta de abono.")

        db.add_all([Detalle(periodo_id=periodo.id, empleado_id=emp.id, concepto_codigo=f["codigo"],
                            categoria=f["categoria"], monto=f["monto"], origen=f["origen"], formula=f["formula"])
                    for f in filas])
        db.add(Resultado(
            periodo_id=periodo.id, empleado_id=emp.id, remuneracion_computable=ingresos,
            total_descuentos=descuentos, neto_a_pagar=neto, aportes_empleador=aportes,
            base_previsional=base_prev, base_essalud=base_ess, base_5ta_regular=base_5ta,
            ret_5ta_regular=reg, ret_5ta_directa=directa, dias_trabajados=dias_trab, dias_subsidio=subs,
            horas_extras=h25 + h35,
            snapshot={"colaborador": nombre, "documento": emp.documento_identidad, "puesto": emp.puesto,
                      "fecha_cese": emp.fecha_cese.isoformat() if emp.fecha_cese else None,      # para saber si la boleta se calculó antes de registrar el cese
                      "sede": emp.sede, "sistema_pensionario": emp.sistema_pensionario, "afp": emp.afp_nombre,
                      "banco": emp.banco, "cuenta": emp.numero_cuenta, "sueldo": str(sueldo),
                      "diccionario": f"{dic.codigo} v{dic.version}",
                      "parametros": {k: str(v) for k, v in pr.p.items()}}))
    db.commit()
    return {"colaboradores": len(empleados) - len(bloqueados & {e.id for e in empleados}), "alertas": alertas,
            "bloqueados_por_liquidacion": len(bloqueados & {e.id for e in empleados})}


def cerrar_periodo(db: Session, periodo: Periodo, cuenta_id: int):
    exigir_abierto(periodo)
    if db.query(Resultado).filter(Resultado.periodo_id == periodo.id).count() == 0:
        raise ErrorDeNegocio("No se puede cerrar un periodo sin resultados: ejecute Recalcular.", 409)
    periodo.estado, periodo.cerrado_en = "CERRADO", datetime.now(timezone.utc)
    db.add(Auditoria(cuenta_id=cuenta_id, empresa_id=periodo.empresa_id, accion="CERRAR_PERIODO",
                     detalle=f"{periodo.mes:02d}/{periodo.anio} {periodo.fase}"))
    db.commit()


def reabrir_periodo(db: Session, periodo: Periodo, cuenta_id: int, motivo: str):
    if periodo.estado != "CERRADO":
        raise ErrorDeNegocio("Solo se puede reabrir un periodo CERRADO.", 409)
    if db.bind.dialect.name == "postgresql":      # permite el UPDATE bajo el trigger de inmutabilidad
        db.execute(text("SELECT set_config('app.reapertura', 'on', true)"))
    periodo.estado, periodo.cerrado_en = "ABIERTO", None
    db.add(Auditoria(cuenta_id=cuenta_id, empresa_id=periodo.empresa_id, accion="REABRIR_PERIODO", detalle=motivo))
    db.commit()
