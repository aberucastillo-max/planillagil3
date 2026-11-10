# Planilla Ágil API v2

Motor de nómina peruana (Régimen General) guiado por un **Diccionario versionado**.
Python 3.9+ · FastAPI · SQLAlchemy · PostgreSQL (Neon).

## Estructura
- `app/reglas.py` reglas matemáticas puras (Decimal), sin base de datos
- `app/diccionario.py` + `app/seed.py` parámetros, conceptos, AFP, escala 5ta y suspensiones por régimen y fecha
- `app/calculo.py` orquestador del cálculo, cierre y reapertura
- `app/exportadores.py` AFPnet y paquete PLAME
- `app/main.py` endpoints · `app/security.py` API key por cuenta y aislamiento por empresa
- `sql/integridad_postgres.sql` triggers de inmutabilidad (defensa en la base)
- `tests/` 16 pruebas (`pytest`)

## Sin instalar nada (recomendado)
La API prepara sola la base al iniciar (tablas, Diccionario y bloqueo de periodos cerrados).
En Render defina `DATABASE_URL` y `SETUP_TOKEN` (un código que usted invente). Luego abra
`https://TU-API.onrender.com/setup`, escriba el código y el nombre, y copie su clave.
Si la base tiene tablas de la versión anterior, ejecute una vez en Neon > SQL Editor:
`DROP SCHEMA public CASCADE; CREATE SCHEMA public;`

## Configuración rápida (Windows)
Doble clic en `CONFIGURAR.bat`, pegue la cadena de Neon y copie la clave de `MI_CLAVE.txt`.

## Local
```bash
pip install -r requirements.txt
export DATABASE_URL="postgresql://USUARIO:CLAVE@HOST/neondb?sslmode=require"   # nunca en el código
python -m app.cli init-db                      # crea tablas + Diccionario RG_728 v1
# ejecute sql/integridad_postgres.sql en el SQL Editor de Neon
python -m app.cli crear-cuenta --nombre "Mi cuenta" --plan GRATIS   # imprime la API key (una sola vez)
uvicorn app.main:app --reload                  # documentación en /docs
pytest
```

## Publicar (gratis, para pruebas)
- Base de datos: Neon (plan gratuito).
- API: Render → *New Web Service* desde un repositorio de GitHub.
  Build: `pip install -r requirements.txt` · Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  Variables: `DATABASE_URL`, `ALLOWED_ORIGINS` (dominio del frontend; para CodePen `https://cdpn.io,https://codepen.io`).
- Frontend estático: Cloudflare Pages o GitHub Pages.

## Conectar el frontend
Abra `planilla_agil_frontend_v2.html` (en CodePen o publicado), pulse el engranaje de la cabecera, pegue la URL de la API y la clave
que imprimió `crear-cuenta`. Sin esos datos el HTML funciona en modo demo. La clave se guarda solo en sessionStorage.

## Liquidación por cese (etapa 2)
Módulo `Liquidaciones`: cesados del periodo, procesar desde el listado de ceses del mes, ABIERTA (calculada) y CERRADA,
cierre que bloquea la boleta del colaborador, ajuste final de 5ta, topes aplicados una vez sobre la base del mes y PDF.
AFPnet y PLAME salen en **un solo archivo por periodo**: la liquidación CERRADA se suma a la boleta del mes.
Endpoints: `GET/POST /empresas/{id}/liquidaciones`, `GET/PATCH /liquidaciones/{id}`, `POST .../cerrar`, `POST .../reabrir`,
`DELETE .../{id}` (solo ABIERTA), `GET .../{id}/pdf`. Fórmulas y supuestos: documento «Propuesta de lógica» y documento de control.
Al arrancar, la API agrega sola las columnas y los parámetros nuevos (idempotente).

## Beneficios semestrales y reingreso (etapa 3)
- **Gratificación:** las boletas de julio y diciembre la calculan solas (semestre anterior, meses completos, más la
  bonificación extraordinaria); una novedad manual de `@GRATIFICACION` tiene prioridad. Parámetro `GRATIFICACION_AUTOMATICA` (1/0).
- **CTS semestral:** `POST /empresas/{id}/cts-semestral/calcular` (NOV = mayo-octubre, MAY = noviembre-abril), `GET`, `depositar`,
  `revertir` y `exportar` (Excel). Lo depositado no se recalcula y se da por pagado en la liquidación posterior.
- **Reingreso:** `POST /empresas/{id}/empleados/{id}/reingreso` abre un nuevo ciclo y conserva el historial (`GET .../vinculos`).

## Liquidación, formatos y parámetros (etapa 4)
- **La liquidación no se guarda como borrador.** `GET .../liquidaciones/vista`, `.../pdf` y `.../excel` la calculan al momento. Solo
  `POST .../liquidaciones/cerrar` la guarda, y **solo lo cerrado** viaja al AFPnet, al PLAME y a los resúmenes. `POST .../{id}/reabrir`
  (con motivo) anula el cierre. Al arrancar se eliminan los borradores ABIERTA de versiones anteriores.
- **Formatos de la empresa:** PDF de la liquidación (una hoja; BORRADOR si no está cerrada), Excel de la liquidación y boletas mensuales en
  PDF (`.../planillas/{id}/boletas/pdf`, dos copias por hoja). Salen de los datos del legajo y de `PATCH /empresas/{id}` (dirección, representante).
- **AFP:** tasa de comisión por saldo y componente mensual de la mixta en las tasas oficiales; cada trabajador tiene su `tipo_comision`
  (FLUJO o MIXTA) y eso define lo que se descuenta en la boleta y en la liquidación.
- **Parámetros editables por periodo** (`GET/PUT .../periodos/{id}/parametros`): UIT, RMV, tasas, tope de prima y tasas por AFP. Al abrir un
  mes se heredan los del mes anterior; un periodo CERRADO es de solo lectura.

- **Rentas previas de 5ta categoría** (empleadores anteriores): `GET/POST/PUT/DELETE .../empleados/{id}/rentas-previas-5ta`. El Legajo muestra el
  historial con totales y permite editar y eliminar; no se duplican registros idénticos y, si un periodo CERRADO ya las incorporó, no se cambian.

- **5ta del cesado:** en la boleta del mes del cese NO se retiene la 5ta regular (la base sí cuenta); la liquidación aplica el ajuste completo.
- **Opciones de la liquidación en tiempo real** (parámetros de `vista`, `pdf` y `excel`; no se guardan): `cts_anterior_pagada`,
  `vac_modo` (FORMULA, CERO, DIAS o MONTO) con `vac_valor`, y `excluir` (claves de conceptos deshabilitados para SIMULAR; una simulación no
  se puede cerrar). Al cerrar solo se guardan la CTS y la decisión de vacaciones pendientes.

- **Una boleta por mes en las acumulaciones.** Cada fase (Fin de Mes, Quincena) calcula el mes completo; la 5ta, los promedios de variables, la
  última gratificación y la liquidación usan solo el periodo «Fin de Mes» de cada mes (si no existe, el más reciente). Las otras fases se muestran
  como «NO SE SUMA». El detalle de la renta de 5ta (boletas + rentas previas + conceptos afectos de la liquidación) está en `snapshot.quinta.detalle`,
  en la pantalla, en el PDF con detalle y en la hoja «Detalle 5ta categoría» del Excel.

- **Cerrar y reabrir una liquidación en Postgres:** los triggers de integridad impiden tocar los renglones de una liquidación CERRADA. Por eso al cerrar
  se crea sin cerrar, se insertan los renglones y el último paso la marca CERRADA; al reabrir, primero deja de estar cerrada y luego se borra. Los errores
  inesperados o los rechazos de la base llegan al navegador como JSON con su mensaje (y con CORS), no como «Failed to fetch».
- **Prima del seguro AFP con tope:** boleta + liquidación no superan `tope de remuneración asegurable del periodo × tasa de la prima`; la liquidación
  resta lo realmente cobrado en la boleta y avisa si la boleta se calculó con otro tope.
- Las pruebas también se verificaron contra un PostgreSQL real con los triggers de producción.

- **Diagnóstico de conexión:** `GET /api/v1/health` devuelve la versión de la API (`VERSION_API`) y el tipo de base. Cuando una petición no recibe respuesta,
  la pantalla consulta `/health` y dice si la API está dormida, desactualizada (versión menor a la esperada) o activa con un error interno (ver Logs).
  En el engranaje hay un enlace «Probar la conexión con la API».

- **Datos adicionales de la liquidación** (`GET/POST/PUT/DELETE .../liquidaciones/manuales`): ingresos y descuentos manuales (suma graciosa, descuentos
  otros, comisiones pendientes, quincena...) ligados a conceptos del Diccionario que el motor no calcula (`@SUMA_GRACIOSA` y `@DESCUENTOS_OTROS` son nuevos).
  Participan en el cálculo global (totales, bases de AFP, EsSalud y 5ta según sus afectaciones, neto, PDF y Excel). No se pueden tocar con la
  liquidación CERRADA; se descartan si el cese se anula.

- **Quincena = adelanto, configurable por empresa** (`usa_quincena`, `quincena_modo` PORCENTAJE o MONTO, `quincena_valor`; se fija al registrar la empresa y
  se edita en «Datos de la empresa»). El proceso de quincena (periodo con fase «Quincena») solo genera el adelanto de cada colaborador (`@ADELANTO_QUINCENA`):
  no calcula AFP/ONP, EsSalud ni 5ta, no entra en ninguna acumulación y no genera AFPnet, PLAME ni boletas. La planilla de fin de mes lo descuenta como
  `@QUINCENA` (una novedad manual de `@QUINCENA` tiene prioridad). En pantalla hay dos botones: «Procesar Quincena» y «Procesar Planilla».
- La **suma graciosa** (`@SUMA_GRACIOSA`) afecta a 5ta (no a AFP/ONP ni EsSalud).

## Administrador del software (pruebas)
Con el código de instalación (`ADMIN_TOKEN`; si no existe, `SETUP_TOKEN`) se entra como **administrador**: en el engranaje del HTML
elija «Administrador». Ve todas las cuentas y todos los RUC, crea cuentas (devuelve su clave una sola vez) y empresas en la cuenta que
elija, y trabaja en cualquier empresa respetando el plan de su cuenta. Rutas: `GET/POST /api/v1/admin/cuentas`, `PATCH /api/v1/admin/cuentas/{id}`.
Es un acceso total: úselo solo en pruebas o con un `ADMIN_TOKEN` propio y secreto; cuando haya login con usuarios, se reemplaza.

## Antes de producción (pendiente)
Gratificación, CTS, utilidades y liquidación · asiento contable y boletas PDF · reingreso (ciclos laborales) ·
saldo vacacional · login con usuarios (hoy API key por cuenta) · migraciones con Alembic ·
validar contra la norma vigente: UIT/RMV, tasas AFP, códigos PLAME (Tablas 17, 21 y 22) y columnas de AFPnet.

## Post Planilla y configuración con periodo de aplicación (versiones f2 y f3)

Cada registro de configuración **rige desde un mes** y vale hasta que se registre otro posterior para la misma clave. No se puede
registrar ni eliminar un periodo de aplicación que ya alcanzó a un periodo CERRADO. Precedencia al calcular: Diccionario, luego lo
configurado por la empresa y, por último, las ediciones propias del periodo en «Soporte de Fórmula».

| Ruta (bajo `/api/v1/empresas/{empresa_id}`) | Para qué sirve |
| --- | --- |
| `GET/POST /config/plame`, `DELETE /config/plame/{id}` | Código PLAME por concepto |
| `GET/POST /config/cuentas`, `DELETE /config/cuentas/{id}` | Plan de cuentas contables por concepto (debe y haber), incluido `@NETO_A_PAGAR` |
| `GET /planillas/{periodo_id}/asiento` | Asiento del periodo de fin de mes (planilla más liquidaciones cerradas); provisional si el periodo está abierto |
| `GET/POST /config/vigencias/{familia}`, `DELETE /config/vigencias/{familia}/{id}` | Familias: `PARAMETRO`, `TASA_AFP`, `AFECTACION`, `SUSPENSION`, `CENTRO_COSTO`, `PUESTO`, `AREA` |
| `GET /config/catalogos/verificar?periodo_id=` | Colaboradores con centro de costo, puesto o área sin asignar, inexistente o no vigente en el periodo |

El HTML (`planilla_agil_frontend_v3.html`) exige la versión `2026-11-11-f6` o posterior de la API.

## Certificado de rentas y retenciones de quinta categoría (versión f4)

Se emite desde la liquidación del cesado (Art. 45 del Reglamento de la LIR, D.S. 122-94-EF). Sin cerrar sale como BORRADOR; al cerrar la
liquidación sale definitivo. Los rubros (sueldo, utilidades, comisiones, gratificaciones, vacaciones, asignación familiar, reintegros, otras
remuneraciones e ingresos de otras empresas) suman siempre la renta bruta del cálculo de 5ta.

| Ruta (bajo `/api/v1/empresas/{empresa_id}`) | Para qué sirve |
| --- | --- |
| `GET /liquidaciones/certificado-5ta?periodo_id=&empleado_id=` | Datos del certificado, texto legal y lista de datos que faltan |
| `GET /liquidaciones/certificado-5ta/pdf?periodo_id=&empleado_id=` | PDF con el formato del modelo |

El texto del certificado usa la razón social, el RUC, el domicilio fiscal y el representante legal de la empresa, y el nombre y DNI del colaborador.
Cerrar una liquidación congela la boleta del mes del cesado; reabrirla la desbloquea para procesarla de nuevo.
