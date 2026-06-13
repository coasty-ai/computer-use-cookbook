# Coasty Computer Use API Cookbook

> [English](README.md) · [简体中文](README.zh-CN.md) · [Español](README.es.md) · [日本語](README.ja.md) · **हिन्दी** · [Français](README.fr.md) · [Deutsch](README.de.md) · [Português (BR)](README.pt-BR.md) · [한국어](README.ko.md)

[Coasty Computer Use API](https://coasty.ai/docs) के लिए प्रोडक्शन-गुणवत्ता वाले,
बहु-भाषी उदाहरण — एक स्क्रीन देखें, उस पर कार्य करें
(क्लिक/टाइप/स्क्रॉल), स्वायत्त एजेंट कार्य चलाएँ, और बहु-चरणीय
वर्कफ़्लो को व्यवस्थित करें।

हर उपयोग-मामला एक चलाने योग्य उदाहरण के रूप में आता है, **ऑफ़लाइन टेस्ट के साथ**,
**Python** और **TypeScript** में (प्राथमिक), **Go** में (मुख्य उपसमुच्चय), और एक शुद्ध
**cURL/bash** क्विकस्टार्ट के रूप में — सभी प्रत्येक भाषा के लिए एक पतले, टाइप्ड, साझा क्लाइंट
पर निर्मित। एक साथ बंडल किया गया **ऑफ़लाइन मॉक सर्वर (mock server)** पूरे API का अनुकरण करता है
ताकि आप सब कुछ शून्य नेटवर्क और शून्य खर्च के साथ चला सकें।

| डायरेक्टरी | अंदर क्या है |
| --- | --- |
| [`python/`](python/) | साझा क्लाइंट (`src/coasty/`) + उदाहरण 01–10 + 350 टेस्ट |
| [`typescript/`](typescript/) | साझा क्लाइंट (`src/coasty/`) + उदाहरण 01–10 + 331 टेस्ट |
| [`go/`](go/) | केवल-stdlib क्लाइंट पैकेज + 4 उदाहरण + टेबल-संचालित टेस्ट |
| [`curl/`](curl/) | `quickstart.sh` — पूरा मुख्य API टिप्पणी-सहित bash में |
| [`mock/`](mock/) | `https://coasty.ai/v1` का ऑफ़लाइन FastAPI मॉक + 161 टेस्ट |
| [`docs/API_NOTES.md`](docs/API_NOTES.md) | वह संक्षिप्त API अनुबंध जिसके विरुद्ध यह रिपॉज़िटरी बनाई गई है |
| [`COOKBOOK.md`](COOKBOOK.md) | सूचकांक: उपयोग-मामला → फ़ाइल → रन कमांड → एंडपॉइंट → लागत |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | क्लाइंट + मॉक-सर्वर डिज़ाइन |
| [`SUMMARY.md`](SUMMARY.md) | क्या बनाया गया, कवरेज, और लाइव डॉक्स से विचलन |

## पूर्वापेक्षाएँ

- **Python 3.11+** और/या **Node 20+** (अपना ट्रैक चुनें); Go ट्रैक के लिए
  **Go 1.22+**।
- `make` (Windows पर Git Bash/WSL) सुविधाजनक है पर वैकल्पिक — हर Makefile
  टारगेट का अंतर्निहित कमांड नीचे और हर ट्रैक के README में सूचीबद्ध है।
- bash क्विकस्टार्ट के लिए `curl` (+ वैकल्पिक रूप से `jq`)।

## सेटअप (5 मिनट से कम में)

```bash
git clone https://github.com/coasty-ai/computer-use-cookbook
cd computer-use-cookbook
cp .env.example .env        # then edit .env
```

अपनी API कुंजी `.env` में रखें (इसे कभी कमिट न करें — `.gitignore` पहले से ही इसे बाहर रखता है):

```dotenv
COASTY_API_KEY=sk-coasty-test-...   # sandbox key: NEVER bills. Start here.
```

> **खोजबीन करते समय एक सैंडबॉक्स (sandbox) कुंजी का उपयोग करें** (`sk-coasty-test-*`): यह एक लाइव
> (live) कुंजी जैसी ही validation और logic चलाती है पर आपके वॉलेट (wallet) से कभी राशि नहीं काटती,
> और हर उदाहरण `$0 (sandbox)` प्रिंट करता है। कुंजियाँ यहाँ बनाएँ
> <https://coasty.ai/developers/keys>।

फिर कोई एक (या हर एक) ट्रैक इंस्टॉल करें:

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

## अपना पहला उदाहरण चलाएँ

मुफ़्त, कुंजी का कोई जोखिम नहीं, हर जगह काम करता है:

```bash
cd python && .venv/Scripts/python.exe examples/ex04_parse.py        # /v1/parse is free
cd typescript && npx tsx src/examples/ex04-parse.ts
```

या बंडल किए गए मॉक सर्वर के विरुद्ध पूरा API **ऑफ़लाइन** चलाएँ:

```bash
# terminal 1 — start the mock (http://127.0.0.1:8787/v1)
cd mock && .venv/Scripts/python.exe -m coasty_mock --port 8787

# terminal 2 — point any example at it (any well-formed key works offline)
export COASTY_API_KEY=sk-coasty-test-000000000000000000000000000000000000000000000000
export COASTY_BASE_URL=http://127.0.0.1:8787/v1
cd python && .venv/Scripts/python.exe examples/ex05_runs.py \
  --machine-id mch_test_demo --task "Download the latest invoice" --events
```

रन कमांड, एंडपॉइंट और प्रति-उदाहरण लागत अनुमानों के साथ सभी दस उपयोग-मामलों के लिए
[COOKBOOK.md](COOKBOOK.md) देखें।

## लागत चेतावनियाँ और खर्च सुरक्षा

Coasty एक प्रीपेड USD वॉलेट से बिल करता है (1 क्रेडिट = $0.01)। यह रिपॉज़िटरी इस तरह बनाई गई है
कि आकस्मिक खर्च कठिन हो:

- **हर बिल योग्य उदाहरण पहले एक मदवार लागत अनुमान प्रिंट करता है** और जब तक आप
  `--confirm` पास नहीं करते (या `COASTY_CONFIRM_SPEND=1` सेट नहीं करते) तब तक एक लाइव
  कुंजी के विरुद्ध चलने से मना कर देता है। सैंडबॉक्स कुंजियाँ `$0 (sandbox)` लेबल के साथ आगे बढ़ती हैं।
- मशीन उदाहरण `ttl_minutes` सेट करते हैं ताकि एक भूली हुई VM स्वयं ही समाप्त हो जाए, और
  `finally` में रुक/समाप्त हो जाती है।
- टेस्ट सूट **कभी नेटवर्क को नहीं छूते** — सारा HTTP मॉक किया जाता है, और
  वैकल्पिक e2e पथ स्थानीय मॉक सर्वर का उपयोग करता है।
- लाइव स्मोक टेस्ट दोहरे-गेटेड हैं: ये केवल तभी चलते हैं जब `COASTY_RUN_LIVE=1` हो
  **और** कॉन्फ़िगर की गई कुंजी एक सैंडबॉक्स कुंजी हो।
- संदर्भ मूल्य `docs/API_NOTES.md` में रहते हैं; किसी भी चीज़ का अनुमान
  उदाहरण 10 (`ex10_cost_helper.py` / `ex10-cost-helper.ts`) से लगाएँ।

## रिपॉज़िटरी का सत्यापन (किसी नेटवर्क की आवश्यकता नहीं)

```bash
make test lint typecheck          # all tracks, from the repo root
```

`make` के बिना, प्रति ट्रैक:

```bash
cd python      && .venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m mypy && .venv/Scripts/python.exe -m ruff check src tests examples && .venv/Scripts/python.exe -m black --check src tests examples
cd typescript  && npm test && npm run typecheck && npm run lint
cd go          && go test ./... && go vet ./... && gofmt -l .
cd mock        && .venv/Scripts/python.exe -m pytest && .venv/Scripts/python.exe -m mypy
cd curl        && bash tests/smoke.sh
```

CI (GitHub Actions) हर पुश पर यही मैट्रिक्स चलाता है: Python 3.11/3.12,
Node 20/22, Go stable, मॉक सूट, और curl स्मोक टेस्ट।

## समस्या-निवारण

**401 INVALID_API_KEY** — कुंजी अनुपस्थित, खराब (malformed), या रद्द की गई है। एक
कच्ची (raw) `sk-coasty-...` कुंजी `X-API-Key` में भेजें (शाब्दिक शब्द "Bearer" को `X-API-Key`
में **न** चिपकाएँ; यदि आप bearer प्रमाणीकरण पसंद करते हैं तो `Authorization: Bearer <key>` हेडर
का उपयोग करें)। जाँचें कि `.env` रिपॉज़िटरी रूट में है और आपका
शेल `COASTY_API_KEY` को ओवरराइड नहीं कर रहा।

**402 INSUFFICIENT_CREDITS** — आपका प्रीपेड वॉलेट अनुरोध को कवर नहीं कर सकता;
एरर बॉडी आपको `required` बनाम `balance` बताती है। यहाँ टॉप-अप करें
<https://coasty.ai/credits> या किसी सैंडबॉक्स कुंजी (मुफ़्त) पर स्विच करें।
**वॉलेट न्यूनतम** पर ध्यान दें: एक मशीन प्रोविज़न करने और शेड्यूल बनाने/चलाने के लिए
कम-से-कम **$0.20 (20 क्रेडिट)** का बैलेंस आवश्यक है — यह एक रनवे गेट है,
न कि शुल्क — और एक रन शुरू करने के लिए वॉलेट का कम-से-कम एक चरण कवर करना आवश्यक है।

**403 INSUFFICIENT_SCOPE** — कुंजी मान्य है पर उसमें एक स्कोप (scope) की कमी है (बॉडी
`required_scope` और आपके `current_scopes` का नाम बताती है)। `terminal:exec`,
`files:write`, और `browser:execute` जैसे उन्नत स्कोप कुंजी निर्माण के समय अनुरोध किए जाने
चाहिए — कुंजी को फिर से मिंट (re-mint) करें।

**क्लिक गलत जगह पड़ते हैं** — सबसे बड़ी समस्या: निर्देशांक उस स्क्रीनशॉट के स्पेस में
वापस आते हैं जो आपने *भेजा* था। यदि आप अपलोड करने से पहले डाउनस्केल करते हैं, तो
डाउनस्केल किए गए `screen_width`/`screen_height` पास करें और लौटाए गए x/y को वापस
गुणा करें। उदाहरण 01/02 इस पैटर्न को दिखाते हैं।

**`make` नहीं मिला (Windows)** — Git Bash या WSL का उपयोग करें, या ऊपर दिए गए
प्रत्यक्ष कमांड चलाएँ; हर Makefile उन पर एक पतला आवरण मात्र है।

**रन के बीच में वॉलेट खाली हो गया** — रन `WALLET_EXHAUSTED` के साथ विफल हो जाता है (पूर्ण
हुए चरण बिल किए हुए ही रहते हैं); एक मशीन को **रोका जाता है, कभी नष्ट नहीं किया जाता**, और
`suspended_for_billing` के रूप में फ़्लैग किया जाता है — टॉप-अप करें और इसे फिर से शुरू करें।
