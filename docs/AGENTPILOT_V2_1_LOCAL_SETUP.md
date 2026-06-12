# AgentMax V2.1 — Local Adapter Setup

This document describes how AgentMax V2.1 lives on your machine and how
AgentMax must use it.

> Honest disclaimer:
> AgentMax V2.1 is a **PEFT/LoRA adapter**, not a full model. It only works
> when applied on top of the matching base model. If something doesn't behave
> as expected, re-read this document; do not "fix" by editing the adapter
> files.

---

## 1. What was installed

| Item | Path |
|---|---|
| Original archive (untouched) | `C:\path\to\AgentMax\AgentMax_V2_1_ADAPTER_FOR_BETA.tar.gz` |
| Timestamped backup | `C:\path\to\AgentMax\backups\AgentMax\AgentMax_V2_1_ADAPTER_FOR_BETA_20260524_000856.tar.gz` |
| Normalized adapter | `C:\path\to\AgentMax\models\AgentMax\V2.1\adapter\` |
| Manifest | `C:\path\to\AgentMax\models\AgentMax\V2.1\AgentMax_v2_1_manifest.json` |
| Training metadata | `C:\path\to\AgentMax\models\AgentMax\V2.1\run_metadata.json` |
| Temp extract (safe to delete) | `C:\path\to\AgentMax\models\AgentMax\V2.1_extract_tmp\` |

The original `.tar.gz` was **NOT moved, deleted, or modified**. The backup is
a byte-for-byte copy.

## 2. What this adapter is — and isn't

- ✅ It is a **PEFT LoRA adapter** (`peft_type: LORA`, rank 32, alpha 32).
- ✅ It carries the tokenizer + processor + chat template needed at inference.
- ✅ It expects **`unsloth/Qwen3-VL-8B-Thinking`** as base (the `adapter_config.json`
   records the bnb-4bit variant `unsloth/qwen3-vl-8b-thinking-unsloth-bnb-4bit`;
   both names resolve to the same family).
- ❌ It is **NOT** a full model checkpoint.
- ❌ It is **NOT** GGUF and **must not** be converted/exported to GGUF until it
   passes behavioural eval. The manifest documents this restriction.
- ❌ It has **no `config.json`** — that's expected; PEFT adapters never carry one.
   Do not "fix" by adding one.

## 3. Files inside `models/AgentMax/V2.1/adapter/`

| File | Required | Size | Purpose |
|---|---|---:|---|
| `adapter_config.json` | yes | 1.5 KB | LoRA config + base model reference |
| `adapter_model.safetensors` | yes | 349 MB | LoRA weights (only thing that diffs the base) |
| `tokenizer.json` | yes | 11 MB | Tokenizer + chat template merged |
| `tokenizer_config.json` | yes | 5.1 KB | Tokenizer behaviour |
| `processor_config.json` | yes | 1.3 KB | VL processor config |
| `chat_template.jinja` | yes | 5.2 KB | Chat template (with `<think>` rendering hints) |
| `README.md` | optional | 5.2 KB | Card from training side |

SHA-256 of `adapter_model.safetensors` is recorded in the manifest.

## 4. How AgentMax loads it (high level)

The runtime treats AgentMax V2.1 as a **local PEFT adapter** provider. The
LM Studio path is unchanged — that's a separate provider. The adapter path is
used by the in-process loader (transformers + peft + unsloth):

```python
# Pseudocode — wired into core/runtime when the local-PEFT loader lands.
from unsloth import FastVisionModel
model, tok = FastVisionModel.from_pretrained(
    "unsloth/Qwen3-VL-8B-Thinking",
    load_in_4bit=True,
)
model.load_adapter(
    r"C:\path\to\AgentMax\models\AgentMax\V2.1\adapter",
    adapter_name="AgentMax_v2_1",
)
model.set_adapter("AgentMax_v2_1")
FastVisionModel.for_inference(model)
```

**Today** (May 2026) the runtime ships with the LM Studio HTTP path active.
Use that route until the local-PEFT in-process loader is wired up. See §6.

## 5. Sanitizer expectations

The model is a **thinking** checkpoint. It may emit `<think>...</think>`
blocks before the final answer. Two layers must always run between the model
and the UI:

1. **`core/ai/thinking_parser.py`** — strips `<think>` and exposes the clean
   text to upstream agents. Already wired into `LMStudioClient` when
   `AGENTMAX_OBSERVABILITY=1` (default ON).
2. **`core/ai/response_sanitizer.py`** — `sanitize_agent_response(raw)`
   additionally strips stray `system:` / `developer:` prompt fragments and
   caps the response.

Both layers are idempotent. The raw response is only kept in debug logs and
internal bus events (`ai.thinking`), never in the user-visible chat.

> Reminder: the WebSocket broadcasts all bus events, including `ai.thinking`.
> If you expose the WS publicly turn on `AGENTMAX_IPC_AUTH=1` first
> (see `docs/AgentMax_SECURITY.md`).

## 6. Connecting the adapter to AgentMax

**Recommended for now (LM Studio route):**
1. Convert/load this adapter into LM Studio over the merged-with-base model
   (LM Studio does not yet load raw PEFT adapters — you would have to merge
   first, which we have NOT done in this run).
2. Or skip LM Studio entirely and run AgentMax V2.1 in-process when the
   PEFT loader is added.

**Future (local PEFT route — not wired in this PR):**
1. Add a `LocalPEFTProvider` to `core/ai/` that wraps the unsloth loader above.
2. Register it in the model registry (`models/AgentMax/models.json` —
   created here as a placeholder).
3. Switch backend via `AGENTMAX_BACKEND=local_peft` and point at this adapter.

The manifest already declares the right metadata, so registering the
provider only requires reading the JSON.

## 7. Reverting / rollback

If anything goes wrong with the installed adapter, the original is safe:

```powershell
# Step 1: archive (or rename) the working dir to keep evidence
Rename-Item `
  C:\path\to\AgentMax\models\AgentMax\V2.1\adapter `
  adapter.broken_$((Get-Date -Format "yyyyMMdd_HHmmss"))

# Step 2: restore from the timestamped backup
python -c "import tarfile; tarfile.open(r'C:\path\to\AgentMax\backups\AgentMax\AgentMax_V2_1_ADAPTER_FOR_BETA_20260524_000856.tar.gz').extractall(r'C:\path\to\AgentMax\models\AgentMax\V2.1_extract_tmp')"

# Step 3: move the inner adapter back to the canonical location
Copy-Item -Recurse `
  C:\path\to\AgentMax\models\AgentMax\V2.1_extract_tmp\AgentMax-Train-Qwen3-32B\outputs\AgentMax-v2-1-tools-supervisor\adapter `
  C:\path\to\AgentMax\models\AgentMax\V2.1\adapter
```

The original archive at the project root is the absolute fallback.

## 8. Verifying the install

Run:

```powershell
python scripts/verify_AgentMax_v2_1_adapter.py
```

Exit code 0 = ready. Other exit codes documented in the script header.

With sha256 integrity check (slow, ~5–15 s):

```powershell
python scripts/verify_AgentMax_v2_1_adapter.py --check-hash
```

## 9. Do not

- ❌ Do not delete the original tar.gz.
- ❌ Do not load the adapter standalone — it is not a full model.
- ❌ Do not export to GGUF until behavioural eval passes.
- ❌ Do not edit any file inside `adapter/`.
- ❌ Do not add a `config.json` — PEFT adapters do not have one.
- ❌ Do not trust the model output without running the sanitizer.
