# Coasty Computer Use API Cookbook

> [English](README.md) · [简体中文](README.zh-CN.md) · [Español](README.es.md) · [日本語](README.ja.md) · [हिन्दी](README.hi.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português (BR)](README.pt-BR.md) · **한국어**

[Coasty Computer Use API](https://coasty.ai/docs)를 위한 프로덕션 품질의 다국어
예제 모음입니다 — 화면을 보고, 그 위에서 동작하며(클릭/입력/스크롤), 자율 에이전트
작업을 실행하고, 여러 단계로 이루어진 워크플로를 오케스트레이션합니다.

모든 사용 사례는 **오프라인 테스트가 포함된** 실행 가능한 예제로 제공되며,
**Python**과 **TypeScript**(주력), **Go**(핵심 일부), 그리고 순수
**cURL/bash** 빠른 시작으로 구성됩니다 — 모두 언어별로 하나의 얇고 타입이
지정된 공유 클라이언트 위에 구축되었습니다. 번들로 제공되는 **오프라인 mock
서버(mock server)**가 전체 API를 에뮬레이션하므로, 네트워크 없이 비용 지출
없이 모든 것을 실행할 수 있습니다.

| 디렉터리 | 내용 |
| --- | --- |
| [`python/`](python/) | 공유 클라이언트(`src/coasty/`) + 예제 01–10 + 테스트 350개 |
| [`typescript/`](typescript/) | 공유 클라이언트(`src/coasty/`) + 예제 01–10 + 테스트 331개 |
| [`go/`](go/) | 표준 라이브러리만 사용하는 클라이언트 패키지 + 예제 4개 + 테이블 기반 테스트 |
| [`curl/`](curl/) | `quickstart.sh` — 주석이 달린 bash로 작성한 전체 핵심 API |
| [`mock/`](mock/) | `https://coasty.ai/v1`의 오프라인 FastAPI mock + 테스트 161개 |
| [`docs/API_NOTES.md`](docs/API_NOTES.md) | 이 저장소가 기반으로 삼은 API 계약을 정리한 문서 |
| [`COOKBOOK.md`](COOKBOOK.md) | 색인: 사용 사례 → 파일 → 실행 명령 → 엔드포인트 → 비용 |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | 클라이언트 + mock 서버 설계 |
| [`SUMMARY.md`](SUMMARY.md) | 구축한 내용, 커버리지, 라이브 문서와의 차이점 |

## 사전 준비물

- **Python 3.11+** 및/또는 **Node 20+**(원하는 트랙 선택), Go 트랙의 경우
  **Go 1.22+**.
- `make`(Windows에서는 Git Bash/WSL)가 있으면 편리하지만 선택 사항입니다 — 모든
  Makefile 타깃의 실제 명령은 아래와 각 트랙의 README에 정리되어 있습니다.
- bash 빠른 시작을 위한 `curl`(선택적으로 `jq`).

## 설정 (5분 이내)

```bash
git clone https://github.com/coasty-ai/computer-use-cookbook
cd computer-use-cookbook
cp .env.example .env        # then edit .env
```

API 키를 `.env`에 넣으세요(절대 커밋하지 마세요 — `.gitignore`가 이미 제외하고
있습니다):

```dotenv
COASTY_API_KEY=sk-coasty-test-...   # sandbox key: NEVER bills. Start here.
```

> 탐색하는 동안에는 **sandbox 키를 사용하세요**(`sk-coasty-test-*`): live 키와
> 동일한 검증과 로직을 실행하지만 절대로 지갑(wallet)에서 차감하지 않으며, 모든
> 예제가 `$0 (sandbox)`를 출력합니다. 키는
> <https://coasty.ai/developers/keys>에서 생성하세요.

그런 다음 하나(또는 모든) 트랙을 설치하세요:

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

## 첫 예제 실행하기

무료이고, 키 관련 위험이 없으며, 어디서나 동작합니다:

```bash
cd python && .venv/Scripts/python.exe examples/ex04_parse.py        # /v1/parse is free
cd typescript && npx tsx src/examples/ex04-parse.ts
```

또는 번들로 제공되는 mock 서버를 대상으로 전체 API를 **오프라인**으로 실행하세요:

```bash
# terminal 1 — start the mock (http://127.0.0.1:8787/v1)
cd mock && .venv/Scripts/python.exe -m coasty_mock --port 8787

# terminal 2 — point any example at it (any well-formed key works offline)
export COASTY_API_KEY=sk-coasty-test-000000000000000000000000000000000000000000000000
export COASTY_BASE_URL=http://127.0.0.1:8787/v1
cd python && .venv/Scripts/python.exe examples/ex05_runs.py \
  --machine-id mch_test_demo --task "Download the latest invoice" --events
```

실행 명령, 엔드포인트, 예제별 비용 추정치가 포함된 열 가지 사용 사례 전체는
[COOKBOOK.md](COOKBOOK.md)를 참고하세요.

## 비용 경고 및 지출 안전장치

Coasty는 선불 USD 지갑(wallet)에서 비용을 청구합니다(1 credit = $0.01). 이
저장소는 의도치 않은 지출을 어렵게 만들도록 설계되었습니다:

- **모든 청구 대상 예제는 항목별 비용 추정치를 먼저 출력하며**, `--confirm`을
  전달하거나(또는 `COASTY_CONFIRM_SPEND=1`을 설정하지) 않는 한 live 키를
  대상으로 실행하기를 거부합니다. sandbox 키는 `$0 (sandbox)` 라벨과 함께
  진행됩니다.
- 머신 예제는 `ttl_minutes`를 설정하여 잊어버린 VM이 스스로 종료되도록 하며,
  `finally`에서 정지/종료를 수행합니다.
- 테스트 스위트는 **절대 네트워크에 접근하지 않습니다** — 모든 HTTP는 모킹되며,
  선택적인 e2e 경로는 로컬 mock 서버를 사용합니다.
- Live 스모크 테스트는 이중으로 게이팅됩니다: `COASTY_RUN_LIVE=1`이고 **동시에**
  구성된 키가 sandbox 키인 경우에만 실행됩니다.
- 참조 가격은 `docs/API_NOTES.md`에 있습니다. 무엇이든 예제 10
  (`ex10_cost_helper.py` / `ex10-cost-helper.ts`)으로 추정하세요.

## 저장소 검증하기 (네트워크 불필요)

```bash
make test lint typecheck          # all tracks, from the repo root
```

`make` 없이, 트랙별로:

```bash
cd python      && .venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m mypy && .venv/Scripts/python.exe -m ruff check src tests examples && .venv/Scripts/python.exe -m black --check src tests examples
cd typescript  && npm test && npm run typecheck && npm run lint
cd go          && go test ./... && go vet ./... && gofmt -l .
cd mock        && .venv/Scripts/python.exe -m pytest && .venv/Scripts/python.exe -m mypy
cd curl        && bash tests/smoke.sh
```

CI(GitHub Actions)는 모든 푸시마다 동일한 매트릭스를 실행합니다: Python
3.11/3.12, Node 20/22, Go stable, mock 스위트, 그리고 curl 스모크 테스트.

## 문제 해결

**401 INVALID_API_KEY** — 키가 없거나, 형식이 잘못되었거나, 폐기되었습니다. 원본
`sk-coasty-...` 키를 `X-API-Key`로 보내세요("Bearer"라는 단어 자체를
`X-API-Key`에 붙여넣지 **마세요**. bearer 인증을 선호한다면
`Authorization: Bearer <key>` 헤더를 사용하세요). `.env`가 저장소 루트에 있는지,
그리고 셸이 `COASTY_API_KEY`를 덮어쓰고 있지 않은지 확인하세요.

**402 INSUFFICIENT_CREDITS** — 선불 지갑이 요청 비용을 감당할 수 없습니다. 오류
본문에 `required`와 `balance`가 표시됩니다. <https://coasty.ai/credits>에서
충전하거나 sandbox 키(무료)로 전환하세요. **지갑 최소 잔액**에 유의하세요:
머신을 프로비저닝하고 스케줄을 생성/실행하려면 최소 **$0.20(20 credits)** 이상의
잔액이 필요합니다 — 이는 수수료가 아니라 운영 가능 기간(runway)을 위한
게이트입니다 — 그리고 run을 시작하려면 지갑이 최소 한 단계 이상을 감당할 수
있어야 합니다.

**403 INSUFFICIENT_SCOPE** — 키는 유효하지만 스코프(scope)가 부족합니다(본문에
`required_scope`와 `current_scopes`가 표시됩니다). `terminal:exec`,
`files:write`, `browser:execute`와 같은 상위 권한 스코프는 키 생성 시
요청해야 합니다 — 키를 다시 발급하세요.

**클릭이 엉뚱한 곳에 떨어짐** — 가장 흔한 함정: 좌표는 여러분이 *보낸* 스크린샷의
좌표 공간으로 반환됩니다. 업로드하기 전에 다운스케일했다면, 다운스케일된
`screen_width`/`screen_height`를 전달하고 반환된 x/y를 다시 곱해서 키우세요.
예제 01/02에서 이 패턴을 보여줍니다.

**`make`를 찾을 수 없음(Windows)** — Git Bash나 WSL을 사용하거나 위의 직접
명령을 실행하세요. 모든 Makefile은 그 명령들을 감싼 얇은 래퍼일 뿐입니다.

**run 도중 지갑이 소진됨** — run은 `WALLET_EXHAUSTED`로 실패합니다(완료된
단계는 청구된 상태로 남습니다). 머신은 **정지될 뿐 절대 파괴되지 않으며**,
`suspended_for_billing`으로 표시됩니다 — 충전한 뒤 다시 시작하세요.
