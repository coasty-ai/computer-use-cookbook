# Coasty Computer Use API Cookbook

> [English](README.md) · [简体中文](README.zh-CN.md) · [Español](README.es.md) · [日本語](README.ja.md) · [हिन्दी](README.hi.md) · [Français](README.fr.md) · **Deutsch** · [Português (BR)](README.pt-BR.md) · [한국어](README.ko.md)

Produktionsreife, mehrsprachige Beispiele für die
[Coasty Computer Use API](https://coasty.ai/docs) — einen Bildschirm sehen, darauf
reagieren (klicken/tippen/scrollen), autonome Agentenaufgaben ausführen und
mehrstufige Workflows orchestrieren.

Jeder Anwendungsfall wird als lauffähiges Beispiel **mit Offline-Tests**
ausgeliefert, in **Python** und **TypeScript** (primär), **Go** (Kern-Teilmenge)
sowie als reiner **cURL/bash**-Schnellstart — alle aufgebaut auf einem schlanken,
typisierten, gemeinsam genutzten Client pro Sprache. Ein mitgelieferter
**Offline-Mock-Server (mock server)** emuliert die gesamte API, sodass du alles
ohne Netzwerk und ohne Kosten ausführen kannst.

| Verzeichnis | Inhalt |
| --- | --- |
| [`python/`](python/) | Gemeinsamer Client (`src/coasty/`) + Beispiele 01–10 + 350 Tests |
| [`typescript/`](typescript/) | Gemeinsamer Client (`src/coasty/`) + Beispiele 01–10 + 331 Tests |
| [`go/`](go/) | Client-Paket nur mit Stdlib + 4 Beispiele + tabellengetriebene Tests |
| [`curl/`](curl/) | `quickstart.sh` — die gesamte Kern-API in kommentiertem bash |
| [`mock/`](mock/) | Offline-FastAPI-Mock von `https://coasty.ai/v1` + 161 Tests |
| [`docs/API_NOTES.md`](docs/API_NOTES.md) | Destillierter API-Vertrag, gegen den dieses Repo gebaut ist |
| [`COOKBOOK.md`](COOKBOOK.md) | Index: Anwendungsfall → Datei → Ausführungsbefehl → Endpunkte → Kosten |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Design von Client + Mock-Server |
| [`SUMMARY.md`](SUMMARY.md) | Was gebaut wurde, Abdeckung, Abweichungen von den Live-Docs |

## Voraussetzungen

- **Python 3.11+** und/oder **Node 20+** (wähle deinen Pfad); **Go 1.22+** für den
  Go-Pfad.
- `make` (Git Bash/WSL unter Windows) ist praktisch, aber optional — der
  zugrunde liegende Befehl jedes Makefile-Targets ist unten und in der README jedes
  Pfads aufgeführt.
- `curl` (+ optional `jq`) für den bash-Schnellstart.

## Einrichtung (unter 5 Minuten)

```bash
git clone https://github.com/coasty-ai/computer-use-cookbook
cd computer-use-cookbook
cp .env.example .env        # then edit .env
```

Trage deinen API-Schlüssel in `.env` ein (committe ihn niemals — `.gitignore`
schließt ihn bereits aus):

```dotenv
COASTY_API_KEY=sk-coasty-test-...   # sandbox key: NEVER bills. Start here.
```

> **Verwende einen Sandbox-Schlüssel (sandbox key)** (`sk-coasty-test-*`) während des
> Erkundens: Er durchläuft dieselbe Validierung und Logik wie ein Live-Schlüssel
> (live key), belastet aber niemals dein Wallet, und jedes Beispiel gibt
> `$0 (sandbox)` aus. Erstelle Schlüssel unter
> <https://coasty.ai/developers/keys>.

Installiere anschließend einen (oder jeden) Pfad:

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

## Führe dein erstes Beispiel aus

Kostenlos, kein Schlüsselrisiko, funktioniert überall:

```bash
cd python && .venv/Scripts/python.exe examples/ex04_parse.py        # /v1/parse is free
cd typescript && npx tsx src/examples/ex04-parse.ts
```

Oder führe die gesamte API **offline** gegen den mitgelieferten Mock-Server aus:

```bash
# terminal 1 — start the mock (http://127.0.0.1:8787/v1)
cd mock && .venv/Scripts/python.exe -m coasty_mock --port 8787

# terminal 2 — point any example at it (any well-formed key works offline)
export COASTY_API_KEY=sk-coasty-test-000000000000000000000000000000000000000000000000
export COASTY_BASE_URL=http://127.0.0.1:8787/v1
cd python && .venv/Scripts/python.exe examples/ex05_runs.py \
  --machine-id mch_test_demo --task "Download the latest invoice" --events
```

Siehe [COOKBOOK.md](COOKBOOK.md) für alle zehn Anwendungsfälle mit
Ausführungsbefehlen, Endpunkten und Kostenschätzungen pro Beispiel.

## Kostenwarnungen & Ausgabensicherheit

Coasty rechnet über ein im Voraus aufgeladenes USD-Wallet ab (1 Credit = $0,01).
Dieses Repo ist so gebaut, dass versehentliche Ausgaben erschwert werden:

- **Jedes abrechenbare Beispiel gibt zuerst eine detaillierte Kostenschätzung
  aus** und verweigert die Ausführung gegen einen Live-Schlüssel, sofern du nicht
  `--confirm` übergibst (oder `COASTY_CONFIRM_SPEND=1` setzt). Sandbox-Schlüssel
  laufen mit der Kennzeichnung `$0 (sandbox)` weiter.
- Maschinenbeispiele setzen `ttl_minutes`, damit sich eine vergessene VM selbst
  beendet, und stoppen/terminieren im `finally`.
- Die Test-Suites **berühren niemals das Netzwerk** — sämtliches HTTP ist gemockt,
  und der optionale E2E-Pfad nutzt den lokalen Mock-Server.
- Live-Smoke-Tests sind doppelt abgesichert: Sie laufen nur, wenn
  `COASTY_RUN_LIVE=1` ist **und** der konfigurierte Schlüssel ein Sandbox-Schlüssel
  ist.
- Referenzpreise stehen in `docs/API_NOTES.md`; schätze beliebiges mit Beispiel 10
  (`ex10_cost_helper.py` / `ex10-cost-helper.ts`).

## Das Repo verifizieren (kein Netzwerk nötig)

```bash
make test lint typecheck          # all tracks, from the repo root
```

Ohne `make`, pro Pfad:

```bash
cd python      && .venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m mypy && .venv/Scripts/python.exe -m ruff check src tests examples && .venv/Scripts/python.exe -m black --check src tests examples
cd typescript  && npm test && npm run typecheck && npm run lint
cd go          && go test ./... && go vet ./... && gofmt -l .
cd mock        && .venv/Scripts/python.exe -m pytest && .venv/Scripts/python.exe -m mypy
cd curl        && bash tests/smoke.sh
```

CI (GitHub Actions) führt bei jedem Push dieselbe Matrix aus: Python 3.11/3.12,
Node 20/22, Go stable, die Mock-Suite und den curl-Smoke-Test.

## Fehlerbehebung

**401 INVALID_API_KEY** — der Schlüssel fehlt, ist fehlerhaft oder wurde
widerrufen. Sende einen rohen `sk-coasty-...`-Schlüssel in `X-API-Key` (füge
**nicht** das wörtliche Wort "Bearer" in `X-API-Key` ein; verwende den Header
`Authorization: Bearer <key>`, falls du Bearer-Authentifizierung bevorzugst).
Prüfe, dass `.env` im Repo-Root liegt und dass deine Shell `COASTY_API_KEY` nicht
überschreibt.

**402 INSUFFICIENT_CREDITS** — dein vorausbezahltes Wallet kann die Anfrage nicht
decken; der Fehler-Body nennt dir `required` vs. `balance`. Lade auf unter
<https://coasty.ai/credits> oder wechsle zu einem Sandbox-Schlüssel (kostenlos).
Beachte die **Wallet-Mindestbeträge**: Das Bereitstellen einer Maschine sowie das
Erstellen/Auslösen von Zeitplänen erfordert einen Kontostand von mindestens
**$0,20 (20 Credits)** — das ist eine Laufzeitschranke, keine Gebühr — und das
Starten eines Runs erfordert, dass das Wallet mindestens einen Schritt deckt.

**403 INSUFFICIENT_SCOPE** — der Schlüssel ist gültig, aber es fehlt ein Scope (der
Body nennt `required_scope` und deine `current_scopes`). Erweiterte Scopes wie
`terminal:exec`, `files:write` und `browser:execute` müssen bei der
Schlüsselerstellung angefordert werden — präge den Schlüssel neu.

**Klicks landen an der falschen Stelle** — die Falle Nummer 1: Koordinaten kommen
im Raum des Screenshots zurück, den du *gesendet* hast. Wenn du vor dem Hochladen
herunterskalierst, übergib die herunterskalierten Werte `screen_width`/`screen_height`
und multipliziere die zurückgegebenen x/y wieder hoch. Die Beispiele 01/02 zeigen
das Muster.

**`make` nicht gefunden (Windows)** — verwende Git Bash oder WSL oder führe die
obigen direkten Befehle aus; jedes Makefile ist nur ein dünner Wrapper darüber.

**Wallet ist mitten im Run leergelaufen** — der Run schlägt mit `WALLET_EXHAUSTED`
fehl (abgeschlossene Schritte bleiben abgerechnet); eine Maschine wird **gestoppt,
niemals zerstört**, und mit `suspended_for_billing` gekennzeichnet — lade auf und
starte sie erneut.
