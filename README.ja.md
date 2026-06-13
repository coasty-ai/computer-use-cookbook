# Coasty Computer Use API Cookbook

> [English](README.md) · [简体中文](README.zh-CN.md) · [Español](README.es.md) · **日本語** · [हिन्दी](README.hi.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português (BR)](README.pt-BR.md) · [한국어](README.ko.md)

[Coasty Computer Use API](https://coasty.ai/docs) 向けの、プロダクション品質のマルチ言語サンプル集です。画面を認識し、それに対して操作（クリック / 入力 / スクロール）を行い、自律エージェントのタスクを実行し、複数ステップのワークフローをオーケストレーションします。

すべてのユースケースは、**オフラインテスト付き**で実行可能なサンプルとして提供されます。提供言語は **Python** と **TypeScript**（メイン）、**Go**（コア機能のサブセット）、そして純粋な **cURL/bash** によるクイックスタートです。いずれも、言語ごとに用意された薄く型付けされた共有クライアント 1 つの上に構築されています。同梱の**オフラインモックサーバー（mock server）**が API 全体をエミュレートするため、ネットワーク接続も課金も一切なしですべてを実行できます。

| ディレクトリ | 内容 |
| --- | --- |
| [`python/`](python/) | 共有クライアント (`src/coasty/`) + サンプル 01〜10 + 350 テスト |
| [`typescript/`](typescript/) | 共有クライアント (`src/coasty/`) + サンプル 01〜10 + 331 テスト |
| [`go/`](go/) | 標準ライブラリのみのクライアントパッケージ + 4 サンプル + テーブル駆動テスト |
| [`curl/`](curl/) | `quickstart.sh` — コア API 全体をコメント付き bash で記述 |
| [`mock/`](mock/) | `https://coasty.ai/v1` のオフライン FastAPI モック + 161 テスト |
| [`docs/API_NOTES.md`](docs/API_NOTES.md) | 本リポジトリが準拠する、要点をまとめた API 仕様 |
| [`COOKBOOK.md`](COOKBOOK.md) | 索引: ユースケース → ファイル → 実行コマンド → エンドポイント → コスト |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | クライアント + モックサーバーの設計 |
| [`SUMMARY.md`](SUMMARY.md) | 構築したもの、カバレッジ、ライブドキュメントとの差異 |

## 前提条件

- **Python 3.11+** および / または **Node 20+**（使うトラックを選択）。Go トラックには **Go 1.22+** が必要です。
- `make`（Windows では Git Bash/WSL）があると便利ですが必須ではありません。各 Makefile ターゲットの背後にあるコマンドはすべて以下と各トラックの README に記載されています。
- bash クイックスタートには `curl`（必要に応じて `jq`）が必要です。

## セットアップ（5 分以内）

```bash
git clone https://github.com/coasty-ai/computer-use-cookbook
cd computer-use-cookbook
cp .env.example .env        # then edit .env
```

API キーを `.env` に記述してください（決してコミットしないでください。`.gitignore` で既に除外されています）:

```dotenv
COASTY_API_KEY=sk-coasty-test-...   # sandbox key: NEVER bills. Start here.
```

> **試している間はサンドボックスキー（sandbox key、`sk-coasty-test-*`）を使ってください。** ライブキー（live key）と同じバリデーションとロジックで動作しますが、ウォレット（wallet）から引き落とされることは決してなく、すべてのサンプルが `$0 (sandbox)` と表示します。キーの作成は <https://coasty.ai/developers/keys> から行えます。

続いて、いずれか（またはすべて）のトラックをインストールします:

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

## 最初のサンプルを実行する

無料で、キーのリスクもなく、どこでも動作します:

```bash
cd python && .venv/Scripts/python.exe examples/ex04_parse.py        # /v1/parse is free
cd typescript && npx tsx src/examples/ex04-parse.ts
```

あるいは、同梱のモックサーバーに対して API 全体を**オフライン**で実行します:

```bash
# terminal 1 — start the mock (http://127.0.0.1:8787/v1)
cd mock && .venv/Scripts/python.exe -m coasty_mock --port 8787

# terminal 2 — point any example at it (any well-formed key works offline)
export COASTY_API_KEY=sk-coasty-test-000000000000000000000000000000000000000000000000
export COASTY_BASE_URL=http://127.0.0.1:8787/v1
cd python && .venv/Scripts/python.exe examples/ex05_runs.py \
  --machine-id mch_test_demo --task "Download the latest invoice" --events
```

実行コマンド、エンドポイント、サンプルごとのコスト見積もりを含む 10 個すべてのユースケースについては [COOKBOOK.md](COOKBOOK.md) を参照してください。

## コストに関する警告と課金の安全対策

Coasty はプリペイド方式の USD ウォレットから課金します（1 クレジット = $0.01）。本リポジトリは、意図しない課金が起こりにくいように作られています:

- **課金対象のサンプルはすべて、最初に項目別のコスト見積もりを表示し**、`--confirm` を渡す（または `COASTY_CONFIRM_SPEND=1` を設定する）まで、ライブキーに対しての実行を拒否します。サンドボックスキーは `$0 (sandbox)` ラベル付きでそのまま進行します。
- マシン関連のサンプルは `ttl_minutes` を設定するため、放置された VM は自動的に終了します。また、`finally` 内で停止 / 終了処理を行います。
- テストスイートは**ネットワークに一切アクセスしません**。HTTP はすべてモック化されており、任意の e2e 経路はローカルのモックサーバーを使用します。
- ライブのスモークテストは二重にゲートされています。`COASTY_RUN_LIVE=1` であり、**かつ**設定されたキーがサンドボックスキーである場合にのみ実行されます。
- 参考価格は `docs/API_NOTES.md` に記載されています。見積もりはサンプル 10（`ex10_cost_helper.py` / `ex10-cost-helper.ts`）で行えます。

## リポジトリの検証（ネットワーク不要）

```bash
make test lint typecheck          # all tracks, from the repo root
```

`make` がない場合は、トラックごとに実行します:

```bash
cd python      && .venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m mypy && .venv/Scripts/python.exe -m ruff check src tests examples && .venv/Scripts/python.exe -m black --check src tests examples
cd typescript  && npm test && npm run typecheck && npm run lint
cd go          && go test ./... && go vet ./... && gofmt -l .
cd mock        && .venv/Scripts/python.exe -m pytest && .venv/Scripts/python.exe -m mypy
cd curl        && bash tests/smoke.sh
```

CI（GitHub Actions）は、プッシュのたびに同じマトリクスを実行します: Python 3.11/3.12、Node 20/22、Go stable、モックスイート、そして curl スモークテストです。

## トラブルシューティング

**401 INVALID_API_KEY** — キーが欠落しているか、形式が不正であるか、失効しています。生の `sk-coasty-...` キーを `X-API-Key` で送信してください（`X-API-Key` に文字列 "Bearer" をそのまま貼り付けないでください。ベアラー認証を使いたい場合は `Authorization: Bearer <key>` ヘッダーを使ってください）。`.env` がリポジトリのルートにあること、そしてシェルが `COASTY_API_KEY` を上書きしていないことを確認してください。

**402 INSUFFICIENT_CREDITS** — プリペイドのウォレットでリクエストを賄えません。エラー本文に `required`（必要額）と `balance`（残高）が示されます。<https://coasty.ai/credits> でチャージするか、サンドボックスキー（無料）に切り替えてください。**ウォレットの最低残高**に注意してください: マシンのプロビジョニング、およびスケジュールの作成 / 起動には、少なくとも **$0.20（20 クレジット）**の残高が必要です。これは手数料ではなく稼働余力（runway）のためのゲートです。また、ラン（run）を開始するには、ウォレットが少なくとも 1 ステップ分を賄える必要があります。

**403 INSUFFICIENT_SCOPE** — キーは有効ですが、スコープが不足しています（本文に `required_scope` とあなたの `current_scopes` が示されます）。`terminal:exec`、`files:write`、`browser:execute` といった高い権限のスコープは、キー作成時にリクエストする必要があります。キーを再発行してください。

**クリックが意図しない場所に着地する** — 最も多い落とし穴です。座標は、あなたが*送信した*スクリーンショットの座標空間で返ってきます。アップロード前に縮小（ダウンスケール）している場合は、縮小後の `screen_width`/`screen_height` を渡し、返ってきた x/y を元のスケールに掛け戻してください。サンプル 01/02 がこのパターンを示しています。

**`make` が見つからない（Windows）** — Git Bash か WSL を使うか、上記の直接コマンドを実行してください。各 Makefile はそれらを薄くラップしているだけです。

**ラン途中でウォレットが空になった** — ランは `WALLET_EXHAUSTED` で失敗します（完了済みのステップは課金されたままです）。マシンは**停止されるだけで破棄されることはなく**、`suspended_for_billing` のフラグが付きます。チャージして再度起動してください。
