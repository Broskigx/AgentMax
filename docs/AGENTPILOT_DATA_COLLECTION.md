# AgentMax Data Collection

## Objetivo

Recolectar ejemplos reales de uso para futuros fine-tuning sin capturar secretos ni convertir fallos peligrosos en datos de entrenamiento.

## Carpetas

- `data/AgentMax_logs/raw`: logs completos de sesión para uso local/debug.
- `data/AgentMax_logs/redacted`: logs redactados.
- `data/AgentMax_logs/approved`: ejemplos revisados por humano.
- `data/AgentMax_logs/rejected`: ejemplos rechazados.

Solo `approved` puede convertirse en dataset SFT.

## Campos Recomendados

- `user_message`
- `assistant_response`
- `sanitized_response`
- `tool_calls`
- `tool_results`
- `screenshot_metadata`
- `approvals`
- `denials`
- `outcome`
- `safety_flags`
- `token_usage`
- `errors`

## Redacción

El redactor reemplaza:

- emails -> `<EMAIL>`
- tokens/secrets -> `<SECRET>`
- rutas de usuario -> `<USER_PATH>`
- IPs privadas -> `<PRIVATE_IP>`
- nombres de máquina -> `<MACHINE>`

## Scripts

```powershell
python scripts\dataset\redact_AgentMax_logs.py
python scripts\dataset\audit_AgentMax_logs.py data\AgentMax_logs\approved
python scripts\dataset\build_AgentMax_dataset_from_logs.py
```

## Reglas De Aprobación

- No aprobar ejemplos con `<think>`.
- No aprobar ejemplos donde AgentMax inventó archivos, rutas, logs o resultados.
- No aprobar acciones destructivas sin confirmación.
- Preferir ejemplos con evidencia real, tool output y resultado verificable.
- Fallos útiles pueden ir a `rejected` para DPO/eval, no a SFT approved.
