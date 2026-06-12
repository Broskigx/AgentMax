# AgentMax V2 SFT Dataset

Dataset conversacional SFT para corregir malos hábitos de AgentMax:

- no inventar archivos, logs, rutas ni resultados;
- no mostrar razonamiento oculto ni etiquetas de pensamiento;
- pedir evidencia mínima cuando falta contexto;
- diferenciar plan, permiso, ejecución y resultado verificado;
- manejar acciones peligrosas con confirmación humana y mínimo privilegio;
- pausar ante loops o intervención del usuario;
- responder con identidad breve y estilo técnico.

## Archivos

- `AgentMax_sft_v2.jsonl`: 1440 ejemplos de entrenamiento.
- `AgentMax_eval_v2.jsonl`: 100 ejemplos de evaluación manual/automática.
- `AgentMax_rejected_patterns.txt`: patrones que el modelo no debe aprender a emitir.
- `train.jsonl`, `validation.jsonl`, `test.jsonl`: generados por `split_AgentMax_v2_dataset.py`.

## Categorías

- `backend_debug_no_context`: 160
- `backend_debug_with_logs`: 160
- `safe_tool_use`: 140
- `anti_invention_humility`: 120
- `security_dangerous_actions`: 140
- `other_ai_unbounded`: 60
- `anti_loop_user_control`: 100
- `windows_desktop_automation`: 120
- `identity_style`: 80
- `code_review`: 120
- `long_task_planning`: 80
- `correct_refusals`: 60
- `multimodal_vision`: 60
- `checkpoint_evaluation`: 40

## Formato

Cada línea:

```json
{"category":"...","risk_level":"low","skills":["no_invention"],"messages":[{"role":"system","content":"..."},{"role":"user","content":"..."},{"role":"assistant","content":"..."}]}
```

## Auditoría

```bash
python scripts/dataset/audit_AgentMax_v2_dataset.py
```

El auditor falla con código 1 si detecta patrones prohibidos, JSON inválido, roles faltantes, respuestas demasiado cortas, archivos inventados comunes o comandos destructivos.

## Split reproducible

```bash
python scripts/dataset/split_AgentMax_v2_dataset.py
```

Usa `seed=42` y divide `AgentMax_sft_v2.jsonl` en 85% train, 10% validation y 5% test, manteniendo proporción por categoría.

## Entrenamiento con Unsloth / SFTTrainer

Ejemplo orientativo:

```python
from datasets import load_dataset
from trl import SFTTrainer, SFTConfig

dataset = load_dataset("json", data_files={
    "train": "datasets/AgentMax_v2/train.jsonl",
    "validation": "datasets/AgentMax_v2/validation.jsonl",
})

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset["train"],
    eval_dataset=dataset["validation"],
    args=SFTConfig(
        dataset_text_field=None,
        max_length=4096,
        packing=False,
        output_dir="outputs/AgentMax-v2",
    ),
)
```

Aplica el chat template del modelo antes de entrenar si tu pipeline no consume directamente el campo `messages`.
