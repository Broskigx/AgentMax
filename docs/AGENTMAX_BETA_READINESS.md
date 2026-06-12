# AgentMax Beta Readiness

Estado objetivo: **Technical Beta Candidate**, no producción.

## Qué Incluye La Beta

- Chat principal profesional con panel lateral y panel de inspección.
- Panel visible de `Agent Thinking` con estados seguros, sin chain-of-thought crudo.
- Checklist operativo por tarea.
- Tokens/créditos locales con planes `Free`, `Starter`, `Pro`, `Local`.
- Screenshot real por Tauri cuando la app corre como desktop.
- Registry visible de tools con disponibilidad, riesgo y aprobación.
- Stop Agent y estados de `Awaiting approval`, `Screenshot required`, `User control`.
- Sanitizer de salida para `<think>` en frontend y backend Python.
- Logs/redacción y scripts para convertir ejemplos approved en SFT.
- Rescue Mode externo para diagnóstico cuando la UI no arranca.

## Cómo Correr

```powershell
cd C:\path\to\AgentMax
python scripts\AgentMax_test_server.py
cd ui
npm run dev
```

Para app Tauri:

```powershell
cd C:\path\to\AgentMax\ui
npm run tauri dev
```

## Checklist De Beta

- [x] UI build pasa con `npm run build`.
- [x] Tokens frontend persistentes y reset diario.
- [x] Tools registry visible con stubs seguros.
- [x] Screenshot integrado por Tauri y fallback unavailable.
- [x] Sanitizer `<think>` implementado.
- [x] Supervisor Python testeable.
- [x] Scripts de logs/dataset/redacción.
- [x] `cargo check` validado en este entorno.
- [x] `tauri build` validado en este entorno.
- [ ] Conectar modelo real `unsloth/Qwen3-VL-8B-Thinking` + adapter V2.1.
- [ ] Validar OCR real si se decide instalar Tesseract.

## Validación Ejecutada

- `npm run build`: pasa.
- `cargo check`: pasa con warnings de funciones/constantes Rust no usadas.
- `npm run tauri build`: pasa; genera MSI y NSIS.
- `python -m pytest tests\test_AgentMax_beta_services.py tests\test_tools_backend.py`: pasa.
- `python -m pytest tests\test_security.py`: pasa durante la corrida crítica.
- `python -m pytest`: bloqueado en colección porque falta `email_validator` instalado en el entorno local. La dependencia ya está declarada en `backend/requirements.txt` y en el extra `server` del `pyproject.toml`.

## Cómo Revertir

Los cambios están en la rama `codex/AgentMax-technical-beta-candidate`. Para revisar o descartar sin tocar otras ramas:

```powershell
git diff
git switch main
```

No se hizo `git reset`, no se borraron adapters, checkpoints ni módulos existentes.
