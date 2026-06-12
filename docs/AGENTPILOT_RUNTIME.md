# AgentMax Runtime

## Modelo Objetivo

- Base: `unsloth/Qwen3-VL-8B-Thinking`
- Adapter esperado: `outputs/AgentMax-v2-1-tools-supervisor/adapter`

La UI no debe romper si el modelo local no está cargado. Debe mostrar estado de modelo no disponible y permitir configurar endpoint local/cloud.

## Endpoints De Beta

Servidor de prueba:

```powershell
python scripts\AgentMax_test_server.py
```

Endpoints:

- `http://127.0.0.1:7790/api/chat`
- `http://127.0.0.1:7790/api/status`
- `http://127.0.0.1:7790/api/tokens`
- `http://127.0.0.1:1235/v1/chat/completions`
- `http://127.0.0.1:1235/v1/models`

## Sanitizer

AgentMax puede usar un modelo Thinking, pero la UI no muestra pensamiento interno:

- elimina bloques `<think>...</think>`;
- si aparece `</think>` suelto, conserva solo lo posterior;
- elimina etiquetas `<think>` y `</think>`;
- evita mostrar system/developer prompt repetido;
- recorta respuestas demasiado largas.

Implementaciones:

- `ui/src/lib/responseSanitizer.ts`
- `core/ai/response_sanitizer.py`

## Screenshot / Visión

`screenshot_base64` en Tauri captura PNG real en Windows. La UI muestra preview, resolución, tamaño y timestamp. No guarda la imagen de forma permanente salvo que el usuario la adjunte/envíe al agente. Si OCR/modelo vision no está disponible, queda marcado como unavailable y se usa metadata de imagen.

## Configuración Recomendada

- Modo local para pruebas con modelo propio.
- Free/Starter/Pro para límites de beta sin pagos reales.
- Guardar raw response solo en debug y siempre preferir `sanitized_response` para dataset.
