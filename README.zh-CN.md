# Coasty Computer Use API Cookbook

> [English](README.md) · **简体中文** · [Español](README.es.md) · [日本語](README.ja.md) · [हिन्दी](README.hi.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português (BR)](README.pt-BR.md) · [한국어](README.ko.md)

面向
[Coasty Computer Use API](https://coasty.ai/docs) 的生产级、多语言示例——看见屏幕、对其执行操作
（点击/输入/滚动）、运行自主智能体任务，并编排多步骤
工作流。

每个用例都以可运行的示例形式提供，**并附带离线测试**，覆盖
**Python** 与 **TypeScript**（主要语言）、**Go**（核心子集），以及一个纯
**cURL/bash** 快速上手示例——它们都构建在每种语言一个轻量、强类型、共享的客户端之上。内置的**离线 mock 服务器**模拟了整个 API，让你无需任何网络、无任何花费即可运行一切。

| 目录 | 内容 |
| --- | --- |
| [`python/`](python/) | 共享客户端（`src/coasty/`）+ 示例 01–10 + 350 个测试 |
| [`typescript/`](typescript/) | 共享客户端（`src/coasty/`）+ 示例 01–10 + 331 个测试 |
| [`go/`](go/) | 仅依赖标准库的客户端包 + 4 个示例 + 表驱动测试 |
| [`curl/`](curl/) | `quickstart.sh`——以带注释的 bash 覆盖整个核心 API |
| [`mock/`](mock/) | 对 `https://coasty.ai/v1` 的离线 FastAPI mock + 161 个测试 |
| [`docs/API_NOTES.md`](docs/API_NOTES.md) | 本仓库所基于的精炼版 API 约定 |
| [`COOKBOOK.md`](COOKBOOK.md) | 索引：用例 → 文件 → 运行命令 → 端点 → 成本 |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | 客户端 + mock 服务器设计 |
| [`SUMMARY.md`](SUMMARY.md) | 构建了什么、覆盖范围、以及与线上文档的差异 |

## 前置条件

- **Python 3.11+** 和/或 **Node 20+**（选择你的技术路线）；Go 路线需要
  **Go 1.22+**。
- `make`（Windows 上使用 Git Bash/WSL）很方便但并非必需——每个 Makefile
  目标对应的底层命令都列在下文以及各路线的 README 中。
- bash 快速上手示例需要 `curl`（可选 `jq`）。

## 安装设置（5 分钟以内）

```bash
git clone https://github.com/coasty-ai/computer-use-cookbook
cd computer-use-cookbook
cp .env.example .env        # then edit .env
```

将你的 API 密钥填入 `.env`（切勿提交它——`.gitignore` 已将其排除）：

```dotenv
COASTY_API_KEY=sk-coasty-test-...   # sandbox key: NEVER bills. Start here.
```

> **探索阶段请使用沙盒（sandbox）密钥**（`sk-coasty-test-*`）：它执行与
> 正式（live）密钥完全相同的校验和逻辑，但绝不会扣减你的钱包余额，而且每个
> 示例都会打印 `$0 (sandbox)`。前往
> <https://coasty.ai/developers/keys> 创建密钥。

然后安装其中一条（或全部）路线：

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

## 运行你的第一个示例

免费、无密钥风险、随处可用：

```bash
cd python && .venv/Scripts/python.exe examples/ex04_parse.py        # /v1/parse is free
cd typescript && npx tsx src/examples/ex04-parse.ts
```

或者针对内置的 mock 服务器**离线**运行整个 API：

```bash
# terminal 1 — start the mock (http://127.0.0.1:8787/v1)
cd mock && .venv/Scripts/python.exe -m coasty_mock --port 8787

# terminal 2 — point any example at it (any well-formed key works offline)
export COASTY_API_KEY=sk-coasty-test-000000000000000000000000000000000000000000000000
export COASTY_BASE_URL=http://127.0.0.1:8787/v1
cd python && .venv/Scripts/python.exe examples/ex05_runs.py \
  --machine-id mch_test_demo --task "Download the latest invoice" --events
```

全部十个用例的运行命令、端点以及每个示例的成本估算，请参见
[COOKBOOK.md](COOKBOOK.md)。

## 成本警告与花费安全

Coasty 通过预付的美元钱包（wallet）计费（1 credit = $0.01）。本仓库的设计旨在
让意外花费难以发生：

- **每个计费示例都会先打印逐项成本估算**，并且
  在你未传入 `--confirm`（或设置
  `COASTY_CONFIRM_SPEND=1`）之前拒绝针对正式（live）密钥运行。沙盒（sandbox）密钥则会带着 `$0 (sandbox)` 标签继续运行。
- 机器（machine）示例会设置 `ttl_minutes`，这样被遗忘的 VM 会自行终止，并在
  `finally` 中停止/终止。
- 测试套件**绝不接触网络**——所有 HTTP 都被 mock，
  可选的端到端（e2e）路径使用本地 mock 服务器。
- 正式（live）冒烟测试有双重门控：它们仅在 `COASTY_RUN_LIVE=1`
  **且**所配置的密钥为沙盒（sandbox）密钥时才会运行。
- 参考价格位于 `docs/API_NOTES.md`；任何估算都可用
  示例 10（`ex10_cost_helper.py` / `ex10-cost-helper.ts`）完成。

## 验证仓库（无需网络）

```bash
make test lint typecheck          # all tracks, from the repo root
```

如果没有 `make`，则按路线分别执行：

```bash
cd python      && .venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m mypy && .venv/Scripts/python.exe -m ruff check src tests examples && .venv/Scripts/python.exe -m black --check src tests examples
cd typescript  && npm test && npm run typecheck && npm run lint
cd go          && go test ./... && go vet ./... && gofmt -l .
cd mock        && .venv/Scripts/python.exe -m pytest && .venv/Scripts/python.exe -m mypy
cd curl        && bash tests/smoke.sh
```

CI（GitHub Actions）在每次推送时运行相同的矩阵：Python 3.11/3.12、
Node 20/22、Go stable、mock 套件，以及 curl 冒烟测试。

## 故障排查

**401 INVALID_API_KEY**——密钥缺失、格式错误或已被吊销。请在 `X-API-Key`
中发送原始的 `sk-coasty-...` 密钥（**不要**把字面单词
"Bearer" 粘贴进 `X-API-Key`；如果你偏好 bearer 认证，请使用 `Authorization: Bearer <key>` 头）。检查
`.env` 是否位于仓库根目录，以及你的
shell 是否覆盖了 `COASTY_API_KEY`。

**402 INSUFFICIENT_CREDITS**——你的预付钱包（wallet）无法支付该请求；
错误正文会告诉你 `required` 与 `balance`。前往
<https://coasty.ai/credits> 充值，或切换到沙盒（sandbox）密钥（免费）。注意
**钱包最低余额要求**：预置一台机器以及创建/触发计划任务（schedule）
都需要至少 **$0.20（20 credits）**的余额——这是一道运行余量门槛，
而非一笔费用——并且启动一次运行需要钱包至少能覆盖一个步骤。

**403 INSUFFICIENT_SCOPE**——密钥有效但缺少某个 scope（正文会
列出 `required_scope` 和你的 `current_scopes`）。诸如
`terminal:exec`、`files:write` 和 `browser:execute` 这类提升权限的 scope 必须在
创建密钥时申请——请重新生成密钥。

**点击落在了错误的位置**——头号陷阱：坐标返回时
所处的是你*发送*的那张截图的坐标空间。如果你在上传前进行了缩小，
请传入缩小后的 `screen_width`/`screen_height`，并把返回的
x/y 按比例放大回去。示例 01/02 展示了这一模式。

**找不到 `make`（Windows）**——请使用 Git Bash 或 WSL，或运行上文的直接
命令；每个 Makefile 都只是它们之上的一层轻量封装。

**运行途中钱包耗尽**——运行会以 `WALLET_EXHAUSTED` 失败（已完成的
步骤仍照常计费）；机器会被**停止，而绝不销毁**，并被标记为
`suspended_for_billing`——充值后再次启动它即可。
