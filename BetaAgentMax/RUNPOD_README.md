# AgentMax - Entrenar Qwen3-32B en RunPod (A40)

Paso a paso super simple.

## Que vas a obtener

- Modelo: **Qwen3-32B con thinking** fine-tuneado para AgentMax
- Dataset: **4000 train + 800 valid** (regenerados)
- Epochs: **4** con early-stopping (load_best_model_at_end)
- Tiempo estimado: **~6-8 horas** en A40 48GB (con packing on)
- Costo estimado: **~$3-4** (sobre $10 cargados)

## Paso 1 - Deploy del Pod

1. RunPod -> **Pods** -> **Deploy**
2. GPU: **A40 48 GB** ($0.44/hr, High disponibilidad)
3. Template: `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`
4. Container disk: **40 GB**
5. Volume: **50 GB** (montado en `/workspace`)
6. Click **Deploy**

## Paso 2 - Subir el ZIP

Dos opciones:

**Opcion A - File Browser (mas facil):**
- Connect -> **Files** (boton arriba a la derecha)
- Arrastra `AgentMax-RunPod.zip` a `/workspace/`
- En Web Terminal:
  ```bash
  cd /workspace
  unzip AgentMax-RunPod.zip
  cd AgentMax-RunPod
  ```

**Opcion B - scp desde tu PC:**
```bash
scp -P <PUERTO> AgentMax-RunPod.zip root@<IP_POD>:/workspace/
```

## Paso 3 - Lanzar entrenamiento

En el Web Terminal del pod:
```bash
cd /workspace/AgentMax-RunPod
chmod +x runpod_setup.sh
./runpod_setup.sh
```

El script:
1. Verifica GPU
2. Instala unsloth + deps (3-8 min 1ra vez)
3. Lanza el training y muestra los logs en vivo

## Paso 4 - Si se cae el SSH

El entrenamiento sigue corriendo. Para reconectarte:
```bash
ssh root@<IP> -p <PUERTO>
cd /workspace/AgentMax-RunPod
tail -f train.log
```

O alternativamente lanzalo con `tmux` desde el principio:
```bash
tmux new -s train
./runpod_setup.sh
# Ctrl+B, D para detach
# tmux attach -t train para volver
```

## Paso 5 - Bajar el adapter cuando termine

```bash
cd /workspace/AgentMax-RunPod/outputs
tar czf AgentMax-qwen3-32b-adapter.tar.gz AgentMax-qwen3-32b-v1.0/adapter
ls -lh AgentMax-qwen3-32b-adapter.tar.gz
```

Descargas el `.tar.gz` desde **Files** en RunPod (~200-400 MB).

## Paso 6 - APAGAR EL POD (importante!)

- RunPod -> Pods -> tu pod -> **Stop**
- Stop NO borra los archivos del volume (solo pausa la GPU).
- Si terminaste de todo: **Terminate** (borra todo, deja de pagar storage).

## Hyperparams (referencia)

| Param | Valor | Razon |
|---|---|---|
| Modelo | unsloth/Qwen3-32B-bnb-4bit | 32B con thinking nativo |
| LoRA rank | 32 | Suficiente para AgentMax |
| LoRA alpha | 32 | rank == alpha (estandar) |
| Learning rate | 1.5e-4 | Conservador para 32B |
| Batch size | 2 | Cabe en A40 48GB |
| Grad accum | 8 | Effective batch = 16 |
| Max seq len | 4096 | Con packing on |
| Optimizer | adamw_8bit | Ahorra VRAM |
| LR scheduler | cosine | Mejor que linear para >2 epochs |
| Warmup ratio | 3% | Suaviza el inicio |
| Eval steps | 50 | load_best_model_at_end |

## Problemas comunes

- **OOM (Out of memory)**: bajar `BATCH_SIZE` a 1 y subir `GRADIENT_ACCUMULATION_STEPS` a 16.
- **Descarga lenta del modelo (60 GB)**: cuestion de la red de RunPod, paciencia. Cache queda en `/workspace/hf_cache` para corridas futuras.
- **`xformers` complains**: ignorar, unsloth lo maneja con su propio backend.
- **`bitsandbytes` cuda mismatch**: la imagen runpod/pytorch viene con cuda 12.4, todo OK.
