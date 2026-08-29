# doppel_eval

Repository-only evaluation tooling for the Doppel shadow harness. It is
**not** part of the installed MemoEcho runtime package:

- `pyproject.toml` only ships `app*`; running `python -m doppel_eval`
  requires a source checkout of this repo (or adding `doppel_eval*` to
  the package discovery if you want it installed).
- All data it produces is synthetic; it never connects to QQ.
- It needs `doppel_memory` on the import path:
  `$env:DOPPEL_IMPORT_PATH = "D:\project\Doppel"`.

## Commands

```powershell
# generate datasets (adapter / quality / load; load supports multi-tenant via --owners)
python -m doppel_eval generate --tier adapter  --out data\doppel\adapter.jsonl
python -m doppel_eval generate --tier load --events 10000 --owners 5 --out data\doppel\load.jsonl

# quality: knowledge scenes -> Doppel -> audit JSON (strict exit by default)
python -m doppel_eval replay --scenarios --out data\doppel\replay.json
python -m doppel_eval replay --scenarios --allow-quality-failures  # contract safety only

# load: streaming throughput / idempotence / scope isolation
python -m doppel_eval load --dataset data\doppel\load.jsonl --replay-twice --out data\doppel\load-report.json
```

## Evaluation modes

- **contract** (default): gold memories are injected, then the query layer
  is verified (recall / scope / subject / temporal / evidence / leakage).
  This is a *personal-query-contract* benchmark — it does **not** exercise
  extraction or consolidation.
- **e2e**: runs the real `PersonalMemoryMiner`, deterministic consolidator and
  personal-memory query without injecting gold records. It uses only synthetic
  scenes and requires a structured-output provider.

The report marks `mode` and `label` explicitly so a contract run is never
mistaken for an end-to-end memory-quality result.

## Budgeted LLM E2E

The E2E runner has local preflight limits, provider-reported token accounting,
one retry for the provider's intermittent invalid-JSON response, and a
content-addressed cache. Cached synthetic responses never contain the API key and
do not consume the provider-call budget.

```powershell
$env:DOPPEL_IMPORT_PATH = "D:\project\Doppel"
$env:DOPPEL_MODEL = "deepseek-v4-flash"
$env:DOPPEL_OPENAI_BASE_URL = "https://api.deepseek.com/v1"
$env:DOPPEL_SCHEMA_MODE = "json_object"
$env:DOPPEL_MAX_TOKENS_PARAMETER = "max_tokens"
$env:DOPPEL_THINKING = "disabled"
$env:DOPPEL_API_KEY = "..."

python -m doppel_eval e2e `
  --cases noise-only,temporary-trip,replay-idempotence `
  --max-scenes 3 `
  --max-calls 4 `
  --max-output-per-call 1024 `
  --max-input-tokens 30000 `
  --max-output-tokens 4096 `
  --max-total-tokens 34096 `
  --out data\doppel\e2e-report.json
```

The call-count limit is exact. Input-token preflight is deliberately conservative;
provider-reported usage replaces estimates in the final report. A successful HTTP
response is charged even when downstream domain validation rejects its content.
Provider failures and unexpected processing errors are hard failures even when
`--allow-quality-failures` is used. Evidence/subject mismatches rejected by Doppel's
trusted boundary are reported separately as `safety_rejections`: they prove the
guard worked, while still exposing extractor behavior for quality review.

Travel-count E2E cases are intentionally excluded from the gold-record contract set:

```powershell
python -m doppel_eval e2e `
  --cases travel-count-two-distinct,travel-count-repeat-same,travel-count-cancelled-plan `
  --max-scenes 3 `
  --max-calls 4 `
  --max-output-per-call 1024 `
  --out data\doppel\e2e-travel-count.json
```

Their query audit includes `count_status`, `count_value`,
`distinct_event_keys`, and `count_ok`. A scene cannot pass merely because a trip
was recalled; the distinct stable event-key count must match the expectation.

## Live shadow modes

The MemoEcho runtime keeps Doppel completely off by default. Enable durable
event shadowing explicitly:

```powershell
$env:DOPPEL_SHADOW_ENABLED = "1"
$env:DOPPEL_IMPORT_PATH = "D:\project\Doppel"
```

This default mode only ingests normalized events. To additionally run
Doppel's online `PersonalMemoryExtractor` for self-contained facts, configure
an OpenAI-compatible structured-output model:

```powershell
$env:DOPPEL_SHADOW_EXTRACT_ENABLED = "1"
$env:DOPPEL_MODEL = "your-model-id"
$env:DOPPEL_OPENAI_BASE_URL = "http://localhost:11434/v1"
$env:DOPPEL_API_KEY = "..."  # omit when the local endpoint needs no key
```

For DeepSeek Chat Completions, use its JSON-object and token parameter names,
and disable thinking for low-cost extraction runs:

```powershell
$env:DOPPEL_MODEL = "deepseek-v4-flash"
$env:DOPPEL_OPENAI_BASE_URL = "https://api.deepseek.com"
$env:DOPPEL_SCHEMA_MODE = "json_object"
$env:DOPPEL_MAX_COMPLETION_TOKENS = "1024"
$env:DOPPEL_MAX_TOKENS_PARAMETER = "max_tokens"
$env:DOPPEL_THINKING = "disabled"
```

When the Doppel-specific variables are absent, the runtime reuses MemoEcho's
`OPENAI_MODEL`, `OPENAI_BASE_URL`, and `OPENAI_API_KEY` values. Explicit
`DOPPEL_*` values always take precedence.

The key is only passed to the provider and is never written to the shadow
inbox or trace. Online extraction writes evidence-bound candidate memories;
periodic mining and consolidation remain separate batch stages.
