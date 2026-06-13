# Coasty Computer Use API Cookbook

> [English](README.md) · [简体中文](README.zh-CN.md) · **Español** · [日本語](README.ja.md) · [हिन्दी](README.hi.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português (BR)](README.pt-BR.md) · [한국어](README.ko.md)

Ejemplos multilenguaje de calidad de producción para la
[Coasty Computer Use API](https://coasty.ai/docs): ver una pantalla, actuar
sobre ella (hacer clic / escribir / desplazarse), ejecutar tareas autónomas de
agentes y orquestar flujos de trabajo de varios pasos.

Cada caso de uso se entrega como un ejemplo ejecutable **con pruebas sin
conexión**, en **Python** y **TypeScript** (principales), **Go** (subconjunto
central) y un inicio rápido en **cURL/bash** puro, todos construidos sobre un
único cliente compartido, ligero y tipado por lenguaje. Un **servidor mock
(mock server) sin conexión** incluido emula toda la API para que puedas
ejecutarlo todo sin red y sin gasto alguno.

| Directorio | Qué contiene |
| --- | --- |
| [`python/`](python/) | Cliente compartido (`src/coasty/`) + ejemplos 01–10 + 350 pruebas |
| [`typescript/`](typescript/) | Cliente compartido (`src/coasty/`) + ejemplos 01–10 + 331 pruebas |
| [`go/`](go/) | Paquete de cliente solo con la biblioteca estándar + 4 ejemplos + pruebas basadas en tablas |
| [`curl/`](curl/) | `quickstart.sh`: toda la API central en bash comentado |
| [`mock/`](mock/) | Mock sin conexión de `https://coasty.ai/v1` en FastAPI + 161 pruebas |
| [`docs/API_NOTES.md`](docs/API_NOTES.md) | Contrato de la API destilado contra el que se construye este repositorio |
| [`COOKBOOK.md`](COOKBOOK.md) | Índice: caso de uso → archivo → comando de ejecución → endpoints → costo |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Diseño del cliente + servidor mock |
| [`SUMMARY.md`](SUMMARY.md) | Qué se construyó, cobertura y desviaciones respecto a la documentación en producción |

## Requisitos previos

- **Python 3.11+** o **Node 20+** (elige tu vía); **Go 1.22+** para la vía de
  Go.
- `make` (Git Bash/WSL en Windows) es conveniente pero opcional: el comando
  subyacente de cada objetivo del Makefile aparece abajo y en el README de cada
  vía.
- `curl` (+ opcionalmente `jq`) para el inicio rápido en bash.

## Configuración (menos de 5 minutos)

```bash
git clone https://github.com/coasty-ai/computer-use-cookbook
cd computer-use-cookbook
cp .env.example .env        # then edit .env
```

Coloca tu clave de API en `.env` (nunca la confirmes en el control de versiones;
`.gitignore` ya la excluye):

```dotenv
COASTY_API_KEY=sk-coasty-test-...   # sandbox key: NEVER bills. Start here.
```

> **Usa una clave de sandbox** (`sk-coasty-test-*`) mientras exploras: ejecuta
> la misma validación y lógica que una clave live, pero nunca debita tu wallet,
> y cada ejemplo imprime `$0 (sandbox)`. Crea claves en
> <https://coasty.ai/developers/keys>.

Luego instala una (o todas) las vías:

```bash
# Python
cd python && python -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev,local]"
# (.venv/bin/python on macOS/Linux; [local] adds pyautogui/mss for example 01)

# TypeScript
cd typescript && npm ci

# Go — nothing to install beyond the toolchain
cd go && go build ./...

# Mock server (optional but recommended)
cd mock && python -m venv .venv && .venv/Scripts/python.exe -m pip install -e ".[dev]"
```

## Ejecuta tu primer ejemplo

Gratis, sin riesgo de claves, funciona en cualquier parte:

```bash
cd python && .venv/Scripts/python.exe examples/ex04_parse.py        # /v1/parse is free
cd typescript && npx tsx src/examples/ex04-parse.ts
```

O ejecuta toda la API **sin conexión** contra el servidor mock incluido:

```bash
# terminal 1 — start the mock (http://127.0.0.1:8787/v1)
cd mock && .venv/Scripts/python.exe -m coasty_mock --port 8787

# terminal 2 — point any example at it (any well-formed key works offline)
export COASTY_API_KEY=sk-coasty-test-000000000000000000000000000000000000000000000000
export COASTY_BASE_URL=http://127.0.0.1:8787/v1
cd python && .venv/Scripts/python.exe examples/ex05_runs.py \
  --machine-id mch_test_demo --task "Download the latest invoice" --events
```

Consulta [COOKBOOK.md](COOKBOOK.md) para los diez casos de uso con sus comandos
de ejecución, endpoints y estimaciones de costo por ejemplo.

## Avisos de costo y seguridad de gasto

Coasty factura contra un wallet prepago en USD (1 crédito = $0.01). Este
repositorio está diseñado para dificultar el gasto accidental:

- **Cada ejemplo facturable imprime primero una estimación de costo
  detallada** y se niega a ejecutarse contra una clave live a menos que pases
  `--confirm` (o establezcas `COASTY_CONFIRM_SPEND=1`). Las claves de sandbox
  continúan con la etiqueta `$0 (sandbox)`.
- Los ejemplos de máquinas establecen `ttl_minutes` para que una VM olvidada se
  termine sola, y detienen/terminan en el bloque `finally`.
- Las suites de pruebas **nunca tocan la red**: todo el HTTP está simulado
  (mocked) y la ruta e2e opcional usa el servidor mock local.
- Las pruebas de humo (smoke tests) en vivo tienen doble protección: solo se
  ejecutan cuando `COASTY_RUN_LIVE=1` **y** la clave configurada es una clave de
  sandbox.
- Los precios de referencia viven en `docs/API_NOTES.md`; estima cualquier cosa
  con el ejemplo 10 (`ex10_cost_helper.py` / `ex10-cost-helper.ts`).

## Verificar el repositorio (sin necesidad de red)

```bash
make test lint typecheck          # all tracks, from the repo root
```

Sin `make`, por vía:

```bash
cd python      && .venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m mypy && .venv/Scripts/python.exe -m ruff check src tests examples && .venv/Scripts/python.exe -m black --check src tests examples
cd typescript  && npm test && npm run typecheck && npm run lint
cd go          && go test ./... && go vet ./... && gofmt -l .
cd mock        && .venv/Scripts/python.exe -m pytest && .venv/Scripts/python.exe -m mypy
cd curl        && bash tests/smoke.sh
```

La CI (GitHub Actions) ejecuta la misma matriz en cada push: Python 3.11/3.12,
Node 20/22, Go stable, la suite del mock y la prueba de humo de curl.

## Resolución de problemas

**401 INVALID_API_KEY**: la clave falta, está malformada o fue revocada. Envía
una clave `sk-coasty-...` en bruto en `X-API-Key` (**no** pegues la palabra
literal "Bearer" dentro de `X-API-Key`; usa el encabezado
`Authorization: Bearer <key>` si prefieres la autenticación bearer). Comprueba
que `.env` esté en la raíz del repositorio y que tu shell no esté
sobrescribiendo `COASTY_API_KEY`.

**402 INSUFFICIENT_CREDITS**: tu wallet prepago no puede cubrir la solicitud; el
cuerpo del error te indica `required` frente a `balance`. Recarga en
<https://coasty.ai/credits> o cambia a una clave de sandbox (gratis). Ten en
cuenta los **mínimos del wallet**: aprovisionar una máquina y crear/disparar
programaciones (schedules) requieren un saldo de al menos **$0.20 (20
créditos)** —es una barrera de margen de maniobra, no una tarifa— e iniciar un
run requiere que el wallet cubra al menos un paso.

**403 INSUFFICIENT_SCOPE**: la clave es válida pero carece de un scope (el
cuerpo nombra `required_scope` y tus `current_scopes`). Los scopes elevados como
`terminal:exec`, `files:write` y `browser:execute` deben solicitarse al crear la
clave: vuelve a generar la clave.

**Los clics caen en el lugar equivocado**: el error más común. Las coordenadas
regresan en el espacio de la captura de pantalla que *enviaste*. Si reduces la
escala antes de subir, pasa el `screen_width`/`screen_height` reducido y
multiplica de nuevo hacia arriba la x/y devuelta. Los ejemplos 01/02 muestran el
patrón.

**`make` no encontrado (Windows)**: usa Git Bash o WSL, o ejecuta los comandos
directos de arriba; cada Makefile es un envoltorio ligero sobre ellos.

**El wallet se quedó sin fondos a mitad de un run**: el run falla con
`WALLET_EXHAUSTED` (los pasos completados quedan facturados); una máquina se
**detiene, nunca se destruye**, y queda marcada como `suspended_for_billing`:
recarga e iníciala de nuevo.
