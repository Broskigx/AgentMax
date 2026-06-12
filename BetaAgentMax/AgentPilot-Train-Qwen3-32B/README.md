# AgentMax - Fine-Tuning Qwen3-32B con Thinking

Carpeta self-contained con todo lo necesario para entrenar en RunPod.

## Estructura

```
AgentMax-Train-Qwen3-32B/
├── README.md                        <- este archivo
├── train.py                         <- script de entrenamiento
├── data/
│   ├── train.jsonl                  <- 4000 ejemplos
│   └── valid.jsonl                  <- 800 ejemplos
└── scripts/
    ├── setup.sh                     <- instala deps en el pod
    ├── train.sh                     <- lanza train.py con log
    ├── verify_dataset.py            <- chequeo local pre-entrenamiento
    ├── download_adapter.sh          <- comprime adapter al final
    └── generate_dataset.py          <- regenerar dataset (opcional)
```

## Plan completo (manana cuando arranques)

### 0) Local: verificar dataset (opcional, 10 seg)
```bash
python scripts/verify_dataset.py
```
Debe decir "OK Dataset listo para entrenar" - confirma que los 4800 ejemplos parsean bien.

### 1) RunPod: deployar Pod
- GPU: **A40 48 GB** ($0.44/hr, High availability)
- Template: `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`
- Container disk: **40 GB**
- Volume: **50 GB** montado en `/workspace`
- Click **Deploy** -> esperar ~1 min

### 2) Subir esta carpeta al pod
**Opcion A - File Browser:**
- Click en **Connect -> Files**
- Arrastra `AgentMax-Train-Qwen3-32B.zip` a `/workspace/`
- En Web Terminal:
  ```bash
  cd /workspace
  unzip AgentMax-Train-Qwen3-32B.zip
  cd AgentMax-Train-Qwen3-32B
  ```

**Opcion B - scp desde tu PC:**
```bash
scp -P <PUERTO> AgentMax-Train-Qwen3-32B.zip root@<IP>:/workspace/
```

### 3) Instalar dependencias (1 vez por pod)
```bash
chmod +x scripts/*.sh
bash scripts/setup.sh
```
Dura 3-8 min. Verifica GPU, instala unsloth+trl+transformers+peft, etc.

### 4) Lanzar entrenamiento en tmux
Para que sobreviva si se desconecta tu SSH:
```bash
tmux new -s train
bash scripts/train.sh
# Ctrl+B, D       <- detach (deja corriendo)
# tmux attach -t train  <- volver a ver
```

O en foreground (si no te vas a desconectar):
```bash
python train.py
```

### 5) Mientras entrena
- **Ver progreso en otra terminal:**
  ```bash
  tail -f train-*.log
  ```
- **GPU stats:**
  ```bash
  watch -n 2 nvidia-smi
  ```
- **Tiempo esperado**: ~6-8 horas para 4 epochs sobre 4000 samples

### 6) Cuando termine, bajar el adapter
```bash
bash scripts/download_adapter.sh
```
Crea `AgentMax-qwen3-32b-adapter-YYYYMMDD.tar.gz` (~300-400 MB).
Descargalo desde Files o por scp.

### 7) APAGAR EL POD (importante!)
- RunPod -> Pods -> tu pod -> **Stop**
- "Stop" pausa la GPU (no pagas mas, pero los archivos quedan).
- "Terminate" borra TODO (incluyendo el adapter si no lo bajaste).

## Hyperparams aplicados

| Param | Valor | Razon |
|---|---|---|
| **Modelo** | `unsloth/Qwen3-32B-bnb-4bit` | 32B con thinking nativo |
| Cuant. | 4-bit (QLoRA) | Entra en 48GB |
| LoRA rank | 32 | Suficiente capacidad |
| LoRA alpha | 32 | rank == alpha |
| LoRA dropout | 0 | unsloth fastpath |
| Target modules | q, k, v, o, gate, up, down | Todo el bloque atencion+MLP |
| Learning rate | 1.5e-4 | Conservador para 32B |
| LR scheduler | cosine | Mejor que linear para >2 epochs |
| Warmup ratio | 3% | Suaviza el inicio |
| Weight decay | 0.01 | Regularizacion |
| Batch size | 2 | A40 48GB lo permite |
| Grad accum | 8 | Effective batch = 16 |
| Max seq len | 4096 | Con packing on |
| **Epochs** | **4** | Sweet spot anti-overfit |
| Packing | ON | +30% velocidad |
| Optim | adamw_8bit | Ahorra VRAM |
| Eval | cada 50 steps | + load_best_model_at_end |
| BF16 | auto | Ampere lo soporta |

## Costos esperados (RunPod A40)

| Etapa | Tiempo | Costo |
|---|---|---|
| Setup (deps + descarga modelo) | ~15 min | $0.11 |
| Training (4 epochs) | ~6-8 h | $2.64 - $3.52 |
| Compress + download | ~5 min | $0.04 |
| **Total** | **~7-9 h** | **~$3-4** |

Con $10 cargados te sobran ~$6 para una 2da corrida si queres iterar.

## Troubleshooting

**OOM (Out of memory)**:
- Editar `train.py`: `BATCH_SIZE=1` y `GRADIENT_ACCUMULATION_STEPS=16`.

**Descarga del modelo lenta**:
- Es normal la 1ra vez (~18 GB).
- Queda cacheado en `/workspace/hf_cache` para futuras corridas.

**`xformers` warning**:
- Ignorable. unsloth usa su propio backend.

**Train no arranca - error de tokenizer/chat_template**:
- Asegurate que `unsloth` esta actualizado: `pip install -U unsloth`.
- Qwen3 necesita unsloth >= 2025.x

**Loss diverge / nan**:
- Bajar learning rate a `1e-4`.

## Despues del entrenamiento

Para usar el adapter en LM Studio / inferencia local:
```python
from unsloth import FastLanguageModel
model, tok = FastLanguageModel.from_pretrained(
    "unsloth/Qwen3-32B-bnb-4bit",
    max_seq_length=4096,
    load_in_4bit=True,
)
model.load_adapter("path/to/adapter")
FastLanguageModel.for_inference(model)

messages = [{"role": "user", "content": "Tu prompt aqui"}]
inputs = tok.apply_chat_template(
    messages,
    tokenize=True,
    add_generation_prompt=True,
    enable_thinking=True,    # <- toggle thinking mode
    return_tensors="pt",
).to("cuda")
out = model.generate(inputs, max_new_tokens=512, temperature=0.6)
print(tok.decode(out[0], skip_special_tokens=True))
```

Para mergear con el base model y exportar a GGUF (para LM Studio):
```bash
# en el pod, antes de bajarlo
python -c "
from unsloth import FastLanguageModel
m, t = FastLanguageModel.from_pretrained('unsloth/Qwen3-32B-bnb-4bit', max_seq_length=4096, load_in_4bit=True)
m.load_adapter('outputs/AgentMax-qwen3-32b-v1.0/adapter')
m.save_pretrained_merged('outputs/AgentMax-qwen3-32b-merged', t, save_method='merged_16bit')
m.save_pretrained_gguf('outputs/AgentMax-qwen3-32b-gguf', t, quantization_method='q4_k_m')
"
```
