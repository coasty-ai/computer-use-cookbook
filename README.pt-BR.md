# Coasty Computer Use API Cookbook

> [English](README.md) · [简体中文](README.zh-CN.md) · [Español](README.es.md) · [日本語](README.ja.md) · [हिन्दी](README.hi.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · **Português (BR)** · [한국어](README.ko.md)

Exemplos multilíngues de qualidade de produção para a
[Coasty Computer Use API](https://coasty.ai/docs) — veja uma tela, aja sobre ela
(clicar/digitar/rolar), execute tarefas autônomas de agente e orquestre fluxos de
trabalho de múltiplas etapas.

Cada caso de uso vem como um exemplo executável **com testes offline**, em
**Python** e **TypeScript** (principais), **Go** (subconjunto central) e um
quickstart puro em **cURL/bash** — tudo construído sobre um único cliente
compartilhado, enxuto e tipado, por linguagem. Um **servidor mock offline**
incluído emula toda a API, para que você possa executar tudo sem rede e sem
gastos.

| Diretório | O que há dentro |
| --- | --- |
| [`python/`](python/) | Cliente compartilhado (`src/coasty/`) + exemplos 01–10 + 350 testes |
| [`typescript/`](typescript/) | Cliente compartilhado (`src/coasty/`) + exemplos 01–10 + 331 testes |
| [`go/`](go/) | Pacote de cliente somente com a stdlib + 4 exemplos + testes table-driven |
| [`curl/`](curl/) | `quickstart.sh` — toda a API central em bash comentado |
| [`mock/`](mock/) | Mock offline em FastAPI de `https://coasty.ai/v1` + 161 testes |
| [`docs/API_NOTES.md`](docs/API_NOTES.md) | Contrato destilado da API contra o qual este repositório foi construído |
| [`COOKBOOK.md`](COOKBOOK.md) | Índice: caso de uso → arquivo → comando de execução → endpoints → custo |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Design do cliente + servidor mock |
| [`SUMMARY.md`](SUMMARY.md) | O que foi construído, cobertura, desvios em relação aos docs ao vivo |

## Pré-requisitos

- **Python 3.11+** e/ou **Node 20+** (escolha sua trilha); **Go 1.22+** para a
  trilha Go.
- `make` (Git Bash/WSL no Windows) é conveniente, mas opcional — o comando
  subjacente de cada target do Makefile está listado abaixo e no README de cada trilha.
- `curl` (+ opcionalmente `jq`) para o quickstart em bash.

## Configuração (em menos de 5 minutos)

```bash
git clone https://github.com/coasty-ai/computer-use-cookbook
cd computer-use-cookbook
cp .env.example .env        # then edit .env
```

Coloque sua chave de API em `.env` (nunca faça commit dela — o `.gitignore` já a exclui):

```dotenv
COASTY_API_KEY=sk-coasty-test-...   # sandbox key: NEVER bills. Start here.
```

> **Use uma chave de sandbox** (`sk-coasty-test-*`) enquanto explora: ela executa a
> mesma validação e lógica de uma chave live, mas nunca debita da sua carteira
> (wallet), e cada exemplo imprime `$0 (sandbox)`. Crie chaves em
> <https://coasty.ai/developers/keys>.

Depois instale uma (ou todas) as trilhas:

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

## Execute seu primeiro exemplo

Gratuito, sem risco de chave, funciona em qualquer lugar:

```bash
cd python && .venv/Scripts/python.exe examples/ex04_parse.py        # /v1/parse is free
cd typescript && npx tsx src/examples/ex04-parse.ts
```

Ou execute toda a API **offline** contra o servidor mock incluído:

```bash
# terminal 1 — start the mock (http://127.0.0.1:8787/v1)
cd mock && .venv/Scripts/python.exe -m coasty_mock --port 8787

# terminal 2 — point any example at it (any well-formed key works offline)
export COASTY_API_KEY=sk-coasty-test-000000000000000000000000000000000000000000000000
export COASTY_BASE_URL=http://127.0.0.1:8787/v1
cd python && .venv/Scripts/python.exe examples/ex05_runs.py \
  --machine-id mch_test_demo --task "Download the latest invoice" --events
```

Veja o [COOKBOOK.md](COOKBOOK.md) para todos os dez casos de uso com comandos de
execução, endpoints e estimativas de custo por exemplo.

## Avisos de custo & segurança de gastos

A Coasty cobra de uma carteira (wallet) pré-paga em USD (1 crédito = $0,01). Este
repositório foi construído para tornar difíceis os gastos acidentais:

- **Todo exemplo cobrável imprime primeiro uma estimativa de custo detalhada** e
  se recusa a executar contra uma chave live a menos que você passe `--confirm` (ou
  defina `COASTY_CONFIRM_SPEND=1`). Chaves de sandbox prosseguem com um rótulo `$0 (sandbox)`.
- Os exemplos de máquina definem `ttl_minutes` para que uma VM esquecida se
  encerre sozinha, e fazem stop/terminate no `finally`.
- As suítes de teste **nunca tocam a rede** — todo HTTP é mockado, e o caminho
  e2e opcional usa o servidor mock local.
- Os smoke tests live têm dupla proteção: só rodam quando `COASTY_RUN_LIVE=1`
  **e** a chave configurada é uma chave de sandbox.
- Os preços de referência ficam em `docs/API_NOTES.md`; estime qualquer coisa com
  o exemplo 10 (`ex10_cost_helper.py` / `ex10-cost-helper.ts`).

## Verificando o repositório (sem precisar de rede)

```bash
make test lint typecheck          # all tracks, from the repo root
```

Sem o `make`, por trilha:

```bash
cd python      && .venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m mypy && .venv/Scripts/python.exe -m ruff check src tests examples && .venv/Scripts/python.exe -m black --check src tests examples
cd typescript  && npm test && npm run typecheck && npm run lint
cd go          && go test ./... && go vet ./... && gofmt -l .
cd mock        && .venv/Scripts/python.exe -m pytest && .venv/Scripts/python.exe -m mypy
cd curl        && bash tests/smoke.sh
```

A CI (GitHub Actions) roda a mesma matriz a cada push: Python 3.11/3.12,
Node 20/22, Go stable, a suíte do mock e o smoke test do curl.

## Solução de problemas

**401 INVALID_API_KEY** — a chave está ausente, malformada ou foi revogada. Envie
uma chave `sk-coasty-...` crua em `X-API-Key` (**não** cole a palavra literal
"Bearer" em `X-API-Key`; use o header `Authorization: Bearer <key>` se preferir a
autenticação por bearer). Verifique se o `.env` está na raiz do repositório e se o
seu shell não está sobrescrevendo `COASTY_API_KEY`.

**402 INSUFFICIENT_CREDITS** — sua carteira pré-paga não cobre a requisição;
o corpo do erro informa `required` vs `balance`. Recarregue em
<https://coasty.ai/credits> ou troque para uma chave de sandbox (gratuita). Observe
os **mínimos de carteira (wallet minimums)**: provisionar uma máquina e criar/disparar
schedules exigem um saldo de pelo menos **$0,20 (20 créditos)** — isso é uma barreira
de margem de operação, não uma taxa — e iniciar um run exige que a carteira cubra
pelo menos uma etapa.

**403 INSUFFICIENT_SCOPE** — a chave é válida, mas falta um scope (o corpo
nomeia o `required_scope` e seus `current_scopes`). Scopes elevados como
`terminal:exec`, `files:write` e `browser:execute` precisam ser solicitados na
criação da chave — gere a chave novamente.

**Os cliques caem no lugar errado** — a armadilha número 1: as coordenadas
retornam no espaço do screenshot que você *enviou*. Se você reduzir a escala antes
de fazer o upload, passe os valores reduzidos de `screen_width`/`screen_height` e
multiplique os x/y retornados de volta para cima. Os exemplos 01/02 mostram o padrão.

**`make` não encontrado (Windows)** — use o Git Bash ou WSL, ou execute os comandos
diretos acima; todo Makefile é um wrapper enxuto sobre eles.

**A carteira esvaziou no meio de um run** — o run falha com `WALLET_EXHAUSTED` (as
etapas concluídas permanecem cobradas); uma máquina é **parada (stopped), nunca
destruída**, e marcada como `suspended_for_billing` — recarregue e inicie-a novamente.
