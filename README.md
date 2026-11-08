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

## Antes de producción (pendiente)
Gratificación, CTS, utilidades y liquidación · asiento contable y boletas PDF · reingreso (ciclos laborales) ·
saldo vacacional · login con usuarios (hoy API key por cuenta) · migraciones con Alembic ·
validar contra la norma vigente: UIT/RMV, tasas AFP, códigos PLAME (Tablas 17, 21 y 22) y columnas de AFPnet.
