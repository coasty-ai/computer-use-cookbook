# Coasty Computer Use API Cookbook

> [English](README.md) · [简体中文](README.zh-CN.md) · [Español](README.es.md) · [日本語](README.ja.md) · [हिन्दी](README.hi.md) · **Français** · [Deutsch](README.de.md) · [Português (BR)](README.pt-BR.md) · [한국어](README.ko.md)

Exemples multilingues de qualité production pour la
[Coasty Computer Use API](https://coasty.ai/docs) — observer un écran, agir
dessus (cliquer/saisir/faire défiler), exécuter des tâches d'agent autonomes et
orchestrer des workflows en plusieurs étapes.

Chaque cas d'usage est livré sous forme d'exemple exécutable **accompagné de
tests hors ligne**, en **Python** et **TypeScript** (langages principaux),
**Go** (sous-ensemble du cœur), ainsi qu'un démarrage rapide en pur
**cURL/bash** — le tout reposant sur un client partagé, fin et typé, propre à
chaque langage. Un **serveur mock hors ligne** intégré émule l'API tout entière,
de sorte que vous pouvez tout exécuter sans aucun réseau et sans aucune dépense.

| Répertoire | Contenu |
| --- | --- |
| [`python/`](python/) | Client partagé (`src/coasty/`) + exemples 01–10 + 350 tests |
| [`typescript/`](typescript/) | Client partagé (`src/coasty/`) + exemples 01–10 + 331 tests |
| [`go/`](go/) | Paquet client basé uniquement sur la bibliothèque standard + 4 exemples + tests pilotés par table |
| [`curl/`](curl/) | `quickstart.sh` — tout le cœur de l'API en bash commenté |
| [`mock/`](mock/) | Mock FastAPI hors ligne de `https://coasty.ai/v1` + 161 tests |
| [`docs/API_NOTES.md`](docs/API_NOTES.md) | Contrat d'API distillé sur lequel ce dépôt est construit |
| [`COOKBOOK.md`](COOKBOOK.md) | Index : cas d'usage → fichier → commande d'exécution → endpoints → coût |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Conception du client + du serveur mock |
| [`SUMMARY.md`](SUMMARY.md) | Ce qui a été construit, la couverture, les écarts par rapport à la doc live |

## Prérequis

- **Python 3.11+** et/ou **Node 20+** (choisissez votre piste) ; **Go 1.22+**
  pour la piste Go.
- `make` (Git Bash/WSL sous Windows) est pratique mais facultatif — la commande
  sous-jacente de chaque cible du Makefile est listée ci-dessous et dans le
  README de chaque piste.
- `curl` (+ éventuellement `jq`) pour le démarrage rapide en bash.

## Installation (en moins de 5 minutes)

```bash
git clone https://github.com/coasty-ai/computer-use-cookbook
cd computer-use-cookbook
cp .env.example .env        # then edit .env
```

Placez votre clé d'API dans `.env` (ne la committez jamais — `.gitignore`
l'exclut déjà) :

```dotenv
COASTY_API_KEY=sk-coasty-test-...   # sandbox key: NEVER bills. Start here.
```

> **Utilisez une clé sandbox** (`sk-coasty-test-*`) pendant l'exploration :
> elle applique les mêmes validations et la même logique qu'une clé live, mais
> ne débite jamais votre portefeuille (wallet), et chaque exemple affiche
> `$0 (sandbox)`. Créez des clés sur <https://coasty.ai/developers/keys>.

Installez ensuite une piste (ou toutes) :

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

## Exécutez votre premier exemple

Gratuit, sans risque lié à la clé, fonctionne partout :

```bash
cd python && .venv/Scripts/python.exe examples/ex04_parse.py        # /v1/parse is free
cd typescript && npx tsx src/examples/ex04-parse.ts
```

Ou exécutez toute l'API **hors ligne** contre le serveur mock intégré :

```bash
# terminal 1 — start the mock (http://127.0.0.1:8787/v1)
cd mock && .venv/Scripts/python.exe -m coasty_mock --port 8787

# terminal 2 — point any example at it (any well-formed key works offline)
export COASTY_API_KEY=sk-coasty-test-000000000000000000000000000000000000000000000000
export COASTY_BASE_URL=http://127.0.0.1:8787/v1
cd python && .venv/Scripts/python.exe examples/ex05_runs.py \
  --machine-id mch_test_demo --task "Download the latest invoice" --events
```

Consultez [COOKBOOK.md](COOKBOOK.md) pour les dix cas d'usage, avec leurs
commandes d'exécution, leurs endpoints et des estimations de coût par exemple.

## Avertissements sur les coûts et sécurité des dépenses

Coasty facture sur un portefeuille (wallet) prépayé en USD (1 crédit = $0.01).
Ce dépôt est conçu pour rendre les dépenses accidentelles difficiles :

- **Chaque exemple facturable affiche d'abord une estimation de coût détaillée**
  et refuse de s'exécuter avec une clé live à moins de passer `--confirm` (ou de
  définir `COASTY_CONFIRM_SPEND=1`). Les clés sandbox poursuivent avec l'étiquette
  `$0 (sandbox)`.
- Les exemples de machines définissent `ttl_minutes` afin qu'une VM oubliée
  s'arrête d'elle-même, et appellent stop/terminate dans `finally`.
- Les suites de tests **ne touchent jamais au réseau** — tout le HTTP est mocké,
  et le chemin e2e facultatif utilise le serveur mock local.
- Les smoke tests live sont doublement verrouillés : ils ne s'exécutent que
  lorsque `COASTY_RUN_LIVE=1` **et** que la clé configurée est une clé sandbox.
- Les prix de référence se trouvent dans `docs/API_NOTES.md` ; estimez n'importe
  quoi avec l'exemple 10 (`ex10_cost_helper.py` / `ex10-cost-helper.ts`).

## Vérifier le dépôt (aucun réseau requis)

```bash
make test lint typecheck          # all tracks, from the repo root
```

Sans `make`, par piste :

```bash
cd python      && .venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m mypy && .venv/Scripts/python.exe -m ruff check src tests examples && .venv/Scripts/python.exe -m black --check src tests examples
cd typescript  && npm test && npm run typecheck && npm run lint
cd go          && go test ./... && go vet ./... && gofmt -l .
cd mock        && .venv/Scripts/python.exe -m pytest && .venv/Scripts/python.exe -m mypy
cd curl        && bash tests/smoke.sh
```

La CI (GitHub Actions) exécute la même matrice à chaque push : Python 3.11/3.12,
Node 20/22, Go stable, la suite mock et le smoke test curl.

## Dépannage

**401 INVALID_API_KEY** — la clé est absente, mal formée ou révoquée. Envoyez
une clé `sk-coasty-...` brute dans `X-API-Key` (ne collez **pas** le mot
littéral « Bearer » dans `X-API-Key` ; utilisez l'en-tête
`Authorization: Bearer <key>` si vous préférez l'authentification par bearer).
Vérifiez que `.env` se trouve à la racine du dépôt et que votre shell ne
surcharge pas `COASTY_API_KEY`.

**402 INSUFFICIENT_CREDITS** — votre portefeuille prépayé ne peut pas couvrir
la requête ; le corps de l'erreur vous indique `required` par rapport à
`balance`. Rechargez sur <https://coasty.ai/credits> ou passez à une clé sandbox
(gratuite). Notez les **minimums du portefeuille** : provisionner une machine et
créer/déclencher des planifications exigent un solde d'au moins
**$0.20 (20 crédits)** — il s'agit d'un seuil de marge (runway), non d'un frais —
et démarrer un run exige que le portefeuille couvre au moins une étape.

**403 INSUFFICIENT_SCOPE** — la clé est valide mais ne dispose pas d'un scope
(le corps nomme `required_scope` et vos `current_scopes`). Les scopes élevés
comme `terminal:exec`, `files:write` et `browser:execute` doivent être demandés
à la création de la clé — recréez la clé.

**Les clics atterrissent au mauvais endroit** — le piège n°1 : les coordonnées
sont renvoyées dans l'espace de la capture d'écran que vous avez *envoyée*. Si
vous réduisez la résolution avant l'envoi, transmettez les
`screen_width`/`screen_height` réduits et remultipliez les x/y renvoyés.
Les exemples 01/02 illustrent ce schéma.

**`make` introuvable (Windows)** — utilisez Git Bash ou WSL, ou exécutez les
commandes directes ci-dessus ; chaque Makefile n'est qu'une fine enveloppe
autour d'elles.

**Le portefeuille s'est vidé en plein run** — le run échoue avec
`WALLET_EXHAUSTED` (les étapes terminées restent facturées) ; une machine est
**arrêtée, jamais détruite**, et marquée `suspended_for_billing` — rechargez et
redémarrez-la.
