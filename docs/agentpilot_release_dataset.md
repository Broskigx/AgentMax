# AgentMax Release Dataset

Este documento describe el modo de aprendizaje local agregado para preparar AgentMax para usuarios reales sin recolectar datos de forma opaca.

## Principios

- La captura queda en disco local en `data/agentmax_training/examples.jsonl`.
- El texto se redacta antes de guardarse: claves, tokens, emails y cadenas largas sensibles se reemplazan.
- Las imagenes no se guardan completas por defecto. Se registra metadata real: tipo, tamano, dimensiones y hash SHA-256.
- Para guardar imagenes completas hay que activar `AGENTMAX_DATASET_STORE_IMAGES=1` o cambiar `/api/dataset/settings`.
- El dataset se escribe en JSONL para poder cargarlo directo en pipelines de evaluacion o fine-tuning.

## Variables

```env
AGENTMAX_DATASET_ENABLED=1
AGENTMAX_DATASET_STORE_IMAGES=0
AGENTMAX_ADMIN_KEY=
```

## Endpoints

- `GET /api/dataset/status`: estado, ruta, contador de ejemplos y modo de imagenes.
- `POST /api/dataset/settings`: activa o pausa dataset local y guardado de imagenes.
- `POST /api/dataset/event`: registra fallos del cliente o eventos manuales.
- `POST /api/chat`: registra cada ejemplo con prompt, respuesta, tokens, checklist e imagenes detectadas.

## Formato JSONL

Cada linea contiene:

```json
{
  "schema_version": "AgentMax.training.v1",
  "id": "ex-...",
  "created_at": "2026-05-23T00:00:00+00:00",
  "kind": "chat",
  "status": "success",
  "prompt": "texto redactado",
  "assistant": "respuesta redactada",
  "error": null,
  "images": [],
  "labels": {
    "needs_tools": false,
    "needs_vision": false,
    "failure_type": null
  },
  "training": {
    "messages": [
      {"role": "user", "content": "texto redactado", "images": []},
      {"role": "assistant", "content": "respuesta redactada"}
    ],
    "ideal_action": "none"
  }
}
```

## Vision

El servidor de primera corrida detecta imagenes de forma real por bytes para PNG, JPEG, GIF y WebP. Eso confirma formato, dimensiones, tamano y hash. OCR, objetos, lectura de UI y razonamiento visual requieren conectar un modelo de vision real en la siguiente fase.
