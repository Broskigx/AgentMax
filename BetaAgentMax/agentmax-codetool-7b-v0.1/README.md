# AgentMax-CodeTool-7B-v0.1

Synthetic fine-tuning dataset + Unsloth training pipeline for a local code/tool agent.

**Base model:** `Qwen/Qwen2.5-7B-Instruct` (~7.1B params)  
**VRAM objetivo:** 6 GB (QLoRA 4-bit + gradient checkpointing)  
**RAM objetivo:** 32 GB

El modelo aprende a:

- elegir la tool correcta en vez de defaults a shell,
- leer archivos/logs antes de diagnosticar,
- generar `tool_calls` válidos,
- pedir confirmación ante acciones destructivas,
- no simular ejecución ni inventar resultados,
- no repetir comandos sin cambiar la causa.

---

## Estructura

```
training/
  requirements.txt                   # Dependencias para entrenar
  unsloth_train.py                   # Entrenamiento QLoRA con Unsloth
  unsloth_inference.py               # Chat / merge / one-shot inference
  AgentMax-codetool-7b-v0.1/
    data/
      train.jsonl                    # 80 ejemplos de entrenamiento
      valid.jsonl                    # 20 ejemplos de validación
      README.md
    configs/
      tools_schema.json              # Schema de tools del agente
      dataset_rules.md               # Reglas del dataset
    scripts/
      validate_dataset.py            # Valida formato del dataset
      dataset_stats.py               # Estadísticas del dataset
    README.md
```

---

## Validar Dataset

```powershell
# Desde la raíz del repo
.\.venv\Scripts\python.exe training\AgentMax-codetool-7b-v0.1\scripts\validate_dataset.py
.\.venv\Scripts\python.exe training\AgentMax-codetool-7b-v0.1\scripts\dataset_stats.py
```

---

## Entrenar

```powershell
cd training

# 1. Instalar dependencias (recomendado: entorno limpio con Python 3.10+)
pip install -r requirements.txt

# 2. Ejecutar entrenamiento
python unsloth_train.py
```

Esto descarga Qwen2.5-7B-Instruct en 4-bit, aplica LoRA, entrena ~50 steps (2-5 min en 6GB VRAM) y guarda el adapter en `outputs/AgentMax-codetool-7b-v0.1/adapter/`.

### Ajustes para 6GB VRAM

El script ya está configurado para 6GB. Si ves OOM (out of memory):

| Parámetro | Default | Para 6GB justos |
|-----------|---------|-----------------|
| `MAX_SEQ_LENGTH` | 2048 | 1536 |
| `LORA_RANK` | 16 | 8 |
| `BATCH_SIZE` | 1 | 1 (no bajar más) |
| `GRADIENT_ACCUMULATION_STEPS` | 8 | 4 |

---

## Inferencia

```powershell
# Chat interactivo
python unsloth_inference.py --adapter outputs/AgentMax-codetool-7b-v0.1/adapter

# One-shot
python unsloth_inference.py --adapter outputs/AgentMax-codetool-7b-v0.1/adapter --prompt "Diagnostica por qué falla npm run build"

# Merge LoRA → modelo completo (para exportar a GGUF)
python unsloth_inference.py --adapter outputs/AgentMax-codetool-7b-v0.1/adapter --merge outputs/merged
```

---

## Exportar a GGUF para LM Studio / Ollama

Después del merge, convertí a GGUF con `llama.cpp`:

```powershell
git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
pip install -r requirements.txt

python convert-hf-to-gguf.py path/to/outputs/merged --outfile AgentMax-codetool-7b-v0.1.gguf --outtype q4_k_m
```

Cargá el `.gguf` en LM Studio (o serví con Ollama) y configurá AgentMax:

```env
AGENTMAX_BACKEND=lmstudio
AGENTMAX_LMS_HOST=127.0.0.1
AGENTMAX_LMS_PORT=1234
AGENTMAX_MODEL=AgentMax-codetool-7b-v0.1
```

---

## Expandir el Dataset

Agregá líneas a `data/train.jsonl` o `data/valid.jsonl`. Cada línea debe tener:

- `category`: etiqueta corta
- `messages`: array con roles `system`, `user`, `assistant`, `tool`

Validá siempre con `validate_dataset.py` después de agregar ejemplos.

### Reglas

- No incluir secrets, API keys, tokens, datos reales
- No claims de "fixed" sin tool results
- No simular ejecución
- Preferir `read_files`, `search_repo`, `git_status` sobre `run_command`
- Usar `ask_confirmation` antes de acciones destructivas
