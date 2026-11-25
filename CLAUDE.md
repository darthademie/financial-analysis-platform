# CLAUDE.md

Guía del proyecto para Claude. Léela entera antes de tocar código.

## Contexto y objetivo de este fork

- Aplicación original de un amigo (autor de los commits: «Asier ZD»). Este repositorio es un **fork** cuyo objetivo es
  **llevarla a Android** reaprovechando el código actual (ver «Versión Android»).
- El usuario tiene un **pipeline de CI (Jenkinsfile de su TFG) que compila proyectos Android con Gradle** y quiere
  enchufarlo a este proyecto (carpeta `android/`). Pregunta por los detalles del pipeline (herramienta, ruta
  esperada del proyecto Gradle, firma, artefactos) antes de adaptar nada a él; no los supongas.
- Licencia **GPL-3.0**: cualquier APK/AAB distribuido es obra derivada y debe ofrecer el código fuente. No cambiar la
  licencia ni quitar la atribución.
- Idioma: todo (UI, mensajes de error, docstrings, comentarios, README, commits de producto) está en **español de
  España**. Mantenerlo. Los nombres de identificadores están en inglés.

## Qué es la aplicación

Control de gastos personal **sin login, local y sin conexión a internet** (no hay CDN, analítica ni llamadas externas;
es un principio de diseño, no un accidente). Importa extractos de BBVA, Santander y Trade Republic (y de cualquier banco
con mapeo manual de columnas), clasifica los movimientos por reglas, detecta alertas (comisiones, duplicados, subidas de
precio, recibos ausentes…), hace análisis financiero, presupuestos y una vista de inversiones con coste FIFO y TIR.

## Arquitectura actual

```
Navegador ──fetch /api/*──> FastAPI (app/main.py) ──> SQLite (DATA_DIR/finanzas.db)
          <── estáticos ──  StaticFiles(app/static)   (montado en "/" al final de main.py)
```

- **Backend**: Python 3.12, FastAPI 0.162, Pydantic v2, uvicorn. Dependencias en `requirements.txt` (también
  `openpyxl`, `xlrd`, `defusedxml`, `python-multipart`). Desarrollo: `pytest`, `httpx`, `xlwt`.
- **Frontend**: JavaScript ES modules **sin dependencias ni paso de compilación** (`app/static`). Gráficos SVG propios.
- **Despliegue**: contenedor Podman (`Containerfile`, `compose.yaml`), publicado solo en `127.0.0.1:8765` → 8000.
  El volumen `./data` guarda la base de datos (en `.gitignore`; nunca subir datos bancarios).
- **Importes siempre en céntimos (int)**. En inversiones, títulos y precios se guardan como texto `Decimal`.
- **Fechas** como texto ISO `YYYY-MM-DD`; `occurred_at` (fecha-hora) y `seq` desempatan el orden dentro del día.

### Comandos

```bash
podman-compose up -d --build        # arrancar (http://localhost:8765)
podman-compose down                  # parar (no usar `restart`: falla en Podman 3.4 rootless)
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests     # 93 tests en tests/test_app.py
python samples/generar_ejemplo.py    # regenera samples/*.xlsx|csv (datos ficticios con alertas plantadas)
```

En esta máquina Windows **no hay Python instalado** (solo el alias de la Microsoft Store); sí hay Node 22 y npm.
Para ejecutar los tests habrá que instalar Python 3.12 o usar el contenedor/WSL.

## Mapa del backend (`app/`)

| Fichero | Responsabilidad |
|---|---|
| `main.py` | Todos los endpoints `/api/*`, middleware de seguridad, `lifespan` (init_db → reclassify_all → `_after_data_change`). |
| `db.py` | Esquema SQLite (`SCHEMA`), migración ligera por columnas (`ADDED_COLUMNS` + `ALTER TABLE`), `connect()` con WAL y FK, `get_settings()`. |
| `seed.py` | Categorías del sistema (id, nombre, kind, grupo 50/30/20), marcas españolas (`BRANDS`), reglas genéricas (`GENERIC`) con prioridad y signo, `DEFAULT_SETTINGS` (umbrales de alertas). |
| `categorize.py` | `normalize()` (mayúsculas, sin acentos ni signos), `clean_merchant()` (quita prefijos tipo «PAGO CON TARJETA EN», ruido y formas jurídicas; se queda con 2 palabras significativas), `Classifier`, `reclassify_all()`. |
| `recurring.py` | Detección de cargos/ingresos periódicos por (cuenta, comercio, dirección): frecuencia por mediana de huecos, regularidad ≥70 %, variación de importe acotada. |
| `analytics.py` | `load_txs`, totales, filas mensuales, serie de saldo, desglose por categoría/comercio, `dashboard()` y `analysis()` (50/30/20, mapa de calor, patrón semanal, Pareto, comisiones, previsión ±σ√t, tendencia, YoY). |
| `alerts.py` | Motor de alertas (`ALERT_TYPES`). Cada alerta tiene huella estable; `refresh_alerts()` marca todo inactivo y hace upsert, conservando el `status` que puso el usuario. |
| `transfers.py` | Propuestas de traspasos entre cuentas propias (importes opuestos, cuentas distintas, ventana de días). Nada se excluye hasta que el usuario confirma; decisiones por huella en `transfer_decisions`, sobreviven a deshacer/reimportar. |
| `investments.py` | Reproduce `investment_events`: lotes FIFO, ventas, cancelaciones, recompensas, traspasos de títulos (coste en tránsito), dividendos; `xirr()` por bisección; rentabilidad de la cuenta remunerada; `overview()`, `portfolio_value()`. |
| `ingest/` | Importación (ver abajo). |

### Importación (`app/ingest/`)

- `__init__.py` importa **automáticamente** todos los módulos de la carpeta salvo `base`, `readers`, `tabular`,
  `service`: cada banco se registra con `@register`.
- `base.py`: contrato. `ParsedTx`, `ParsedEvent` (inversión), `Sheet`, `RawFile`, `ParseResult`, `FormatError` (su
  mensaje se muestra tal cual al usuario), `BankParser` (`detect()` → confianza 0..1, `parse()`), `detect_parser()`
  (umbral 0,5, gana la mayor confianza). Constantes `MOV_ORDER_FEE`, `MOV_WITHHOLDING`, `MOV_INTEREST` (textos de
  `movement` que otros módulos comparan literalmente: no cambiarlos sin revisar alerts/recurring/investments).
- `readers.py`: detecta formato por **contenido**, no extensión: PDF (rechazado con explicación), xlsx (zip, límite
  200 MB descomprimido), xls (OLE), HTML disfrazado de xls, CSV/TSV (elige separador por consistencia de columnas;
  decodifica utf-8-sig → cp1252 → latin-1).
- `tabular.py`: `parse_date` (formatos españoles, serie de Excel), `parse_amount` (→ céntimos, admite `1.234,56`,
  `(45,00)`, `12,30-`…), sinónimos de cabecera (`SYNONYMS`), `find_header` (primeras 40 filas), `find_iban`,
  `iban_entity` (0182 BBVA, 0049 Santander), `TableBankParser` (un banco tabular solo implementa `detect`).
- `bbva.py`, `santander.py`: detección por palabra en títulos/nombre de fichero, código de entidad del IBAN o firma de
  columnas (Santander puntúa 0,55 con columnas genéricas para que cualquier prueba más fuerte gane).
- `traderepublic.py`: CSV de 23 columnas en inglés. Divide `amount`/`fee`/`tax` en movimientos distintos con
  `external_id` `id`, `id:fee`, `id:tax`. Compras/ventas → categoría fija `inversiones` (kind `transfer`, ni gasto ni
  ingreso). Tarjeta → categoría de respaldo por MCC. `computed_balance=True` (el extracto no trae saldo).
- `service.py`: `parse_upload`, mapeo manual (`ManualMapping`), `raw_preview`, `fingerprints()` (sha256 de
  `external_id`, o de cuenta+fechas+importe+concepto+saldo + nº de aparición: reimportar no duplica, pero un cargo doble
  real se conserva), `preview()`, `store()` (INSERT OR IGNORE por huella), `recompute_computed_balances()` (si la suma
  baja de −1 € se considera historial incompleto y el saldo queda `NULL`, no se inventa).

### Modelo de datos (SQLite)

`accounts` (+`balance_computed`), `imports`, `categories` (kind `income|expense|transfer`, grp
`income|needs|wants|savings|internal|unknown`), `rules` (source `system|user`, sign `any|in|out`, prioridad, `UNIQUE
(pattern, sign, source)`), `transactions` (fingerprint UNIQUE, `category_source rule|user|default`, `fixed_category`,
`fallback_category`, `seq`, `occurred_at`), `budgets`, `alerts` (fingerprint UNIQUE, status `new|reviewed|dismissed`,
`active`), `transfer_decisions`, `investment_events`, `investment_prices`, `settings` (clave → valor float).

Borrar una importación borra sus movimientos y, si queda vacía, la cuenta (CASCADE en FKs).

### Reglas de negocio que no se deben romper

- Ingresos = suma de categorías `income`; gastos = −suma de categorías `expense`. Una devolución clasificada en una
  categoría de gasto **reduce** ese gasto. `transfer` (internos, inversiones) no cuenta como ninguno.
- Los periodos se calculan desde el **último movimiento importado**, no desde hoy (frontend `currentRange()`).
- Medias mensuales solo con **meses completos** (`complete_months`).
- Clasificación: reglas de usuario primero; luego sistema por prioridad desc y longitud de patrón desc. Coincidencia por
  palabra completa (patrón rodeado de espacios sobre concepto+movimiento+notas normalizados). `fixed_category` gana a
  todo; `fallback_category` solo si ninguna regla casa. Lo puesto a mano (`category_source='user'`) nunca se
  reclasifica.
- Tras **cualquier** cambio de datos se llama a `_after_data_change()`: aplica traspasos confirmados, recalcula saldos
  calculados, commit y refresca alertas. Las nuevas mutaciones deben hacer lo mismo.
- Al arrancar, `init_db` hace upsert de categorías y reglas del sistema sin tocar reglas de usuario, ajustes ni reglas
  del sistema desactivadas; después `reclassify_all`.
- Propuesta de cuenta al importar: solo por IBAN o si hay una única cuenta de ese banco; ante la duda, cuenta nueva
  (mezclar cuentas es peor que elegir).

### Seguridad (diseñada para «sin login en localhost»)

- `TrustedHostMiddleware` con `ALLOWED_HOSTS` (por defecto `localhost,127.0.0.1,[::1]`) contra DNS rebinding.
- Middleware `protect`: rechaza métodos no seguros cuyo `Origin` no coincida con `Host`; añade CSP
  `default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`, `nosniff`, `no-referrer`,
  `no-cache`.
- Sin OpenAPI/docs. Subidas limitadas a 25 MB. Exportación CSV neutraliza fórmulas (`_csv_safe`).
- Frontend: todo texto del banco entra por `textContent` (helper `h()`), nunca `innerHTML`.

## Mapa del frontend (`app/static/`)

- `index.html`: esqueleto (`aside.side` con `#nav`, `#filters`, `#view`, panel `#more`, `#tooltip`, `#toasts`).
  Carga `js/main.js` como módulo. Rutas relativas (`styles.css`, `favicon.svg`).
- `js/main.js`: router por **hash** (`#/inicio`, `#/movimientos?category=…`, etc.), `ROUTES` (las marcadas `more` van
  al panel «Más» en móvil), estado global (periodo, cuentas ocultas en `localStorage`, tema), barra de cuentas,
  filtros de periodo. Cada vista recibe `{ params, meta, filters, navigate, reload, refreshMeta }` y devuelve un nodo.
- `js/ui.js`: `h()`/`s()` (DOM/SVG), formatos `eur`, `pct`, `fmtDate`…, **`api(path, options)`: único punto de
  acceso al backend** (hace `fetch` a rutas absolutas `/api/...`, admite `json` o `body: FormData`), `qs()`, toasts,
  `confirmButton` de dos pasos, `segmented`, `labelTables` (tablas → fichas en móvil).
- `js/charts.js`: líneas, columnas, tarta, sparkline, mapa de calor, tooltip táctil (primer toque muestra, segundo
  navega), cada gráfico con vista de tabla accesible.
- `js/views/*.js`: `dashboard`, `transactions`, `analysis` (pestañas, recuerda la última en `localStorage`),
  `investments`, `alerts` (incluye traspasos), `budgets`, `import` (vista previa → mapeo manual si hace falta →
  commit; historial con «Deshacer»), `settings` (reglas, categorías, umbrales, renombrar cuentas).
- `styles.css`: variables de tema claro/oscuro (`data-theme` o `prefers-color-scheme`), paleta `--series-*`.
  Responsive ya hecho (último commit): `≤1100px` carril lateral, `≤900px`, `≤600px` barra inferior fija con
  `env(safe-area-inset-bottom)`, `pointer: coarse` aumenta áreas táctiles.

Endpoints que usa el frontend: `GET /api/meta|dashboard|analysis|transactions|transactions.csv|alerts|transfers|
investments|budgets|rules|settings|imports`, `PATCH /api/transactions/{id}|alerts/{id}|rules/{id}|accounts/{id}`,
`POST /api/alerts/bulk|transfers/decide|transfers/undo|rules|categories|import/preview|import/commit`,
`PUT /api/budgets/{cat}|investments/prices/{isin}|settings`, `DELETE /api/budgets/{cat}|investments/prices/{isin}|
rules/{id}|imports/{id}`. Casi todos aceptan `accounts=1,2` (sin valor = todas) y `from`/`to`.

## Versión Android (`android/`)

**Decisión tomada**: proyecto Android nativo estándar (Gradle/`gradlew`, Java, sin Capacitor ni Node) con un WebView
que muestra `app/static` y **Chaquopy** ejecutando el backend Python de `app/` dentro del móvil. Se eligió para
reaprovechar el mismo código que el contenedor: **no se duplica nada**, Gradle copia `app/` y `app/static` al APK en
cada build. Detalles en `android/README.md`.

- Sin servidor ni puerto: `android/app/src/main/assets/android-bridge.js` (insertado en index.html por
  `WebAssets.java`) desvía `fetch("/api/...")` a Python por `WebViewCompat.addWebMessageListener` (solo origen
  `https://appassets.androidplatform.net`). `MainActivity` usa un único hilo para Python y llama a
  `finanzas_bridge.handle()`. Sin permiso INTERNET; `allowBackup=false`.
- **pydantic-core no existe para Android en Chaquopy**, así que `android/app/src/main/python/{fastapi,pydantic,starlette}`
  son sustitutos mínimos con solo lo que usa `app/main.py`. **Si cambias `main.py` usando más API de FastAPI/Pydantic
  (otro tipo de parámetro, `response_model`, `Path` con restricciones, validadores…), amplía el sustituto** y pasa
  `sh android/scripts/test-python.sh` (ejecuta `tests/` y `android/tests/` contra el sustituto en un contenedor
  python:3.12-slim, sin FastAPI). Si añades una dependencia a `requirements.txt`, añádela también (URL + SHA-256 del
  wheel, debe ser Python puro) en `vendorPythonWheels` de `android/app/build.gradle.kts`.
- El build **no necesita Python ni Node** en la máquina (pyc desactivado, wheels descargados por Gradle). Versiones:
  Gradle 9.9.2, AGP 9.4.0 (máximo que admite Android Studio; no subir sin comprobarlo), Chaquopy 17.2.0, Python 3.12, compileSdk 37, targetSdk 36, minSdk 26, solo 64 bits.
- Compilar en esta máquina: `JAVA_HOME="/c/Program Files/Android/Android Studio/jbr"` (JDK 25) y `cd android &&
  ./gradlew assembleDebug`. SDK en `%LOCALAPPDATA%/Android/Sdk` (`local.properties`, no versionado). No hay emulador
  ni imágenes de sistema instaladas.
- CI: `android/Jenkinsfile` (adaptado del TFG del usuario; job con «Script Path» `android/Jenkinsfile`, rama `android`,
  repo `darthademie/financial-analysis-platform`, credencial `github-darthademie`). Etapas: pruebas Python contra el
  sustituto (python:3.12-slim) → GitOps de `versionCode = N`/`versionName = "1.N"` en `android/app/build.gradle.kts`
  con commit `[skip ci]` → build en `mingc/android-build-box` (`cd android && ./gradlew assembleDebug lintDebug`,
  comprobado en local) → apkleaks → GitHub Release prerelease con `--target android`. Usa `docker run` con `tar` por
  stdin (no monta volúmenes). Mantén `versionCode = …`/`versionName = "…"` en una línea cada uno para que el `sed` funcione.
- Pendiente: prueba en dispositivo real, firma de release, adaptar textos de escritorio («tu ordenador», «¿Sigue en
  marcha el contenedor?» en `ui.js`, `import.js`, `dashboard.js`). La guía «cómo descargarlo de tu banco» asume
  la web en ordenador.

## Convenciones de código

- Python: `from __future__ import annotations`, tipado moderno (`list[int] | None`), dataclasses, SQL parametrizado con
  `?` (las listas de cuentas se expanden con `",".join("?" * n)`). Comentarios y docstrings breves en español que
  explican el *porqué*. Sin ORM.
- Errores de usuario: `HTTPException(422, "mensaje en español")` o `FormatError("…")`.
- JS: módulos ES, funciones flecha, construcción de DOM con `h()`; sin frameworks; accesibilidad cuidada (`aria-*`,
  estados con icono + texto, no solo color).
- Añadir un banco: nuevo `app/ingest/<banco>.py` con `@register` sobre `TableBankParser` (ver README), con tests.
- Añadir columna a una tabla existente: declararla en `ADDED_COLUMNS` de `db.py` además de en `SCHEMA`.
- Cualquier cambio de lógica debe acompañarse de tests en `tests/test_app.py` (usan `TestClient` con `DATA_DIR` en
  `tmp_path` y generan Excel/CSV en memoria).
