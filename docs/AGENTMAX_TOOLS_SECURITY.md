# AgentMax Tools Security

## Política

- Tools read-only pueden ejecutarse cuando el supervisor ya autorizó observación.
- Tools que escriben, instalan, reinician, cierran procesos, cambian configuración o tocan credenciales requieren confirmación humana explícita.
- Comandos peligrosos se bloquean aunque el modelo los proponga.
- Si una tool falla 2 o 3 veces con el mismo error, el agente debe detenerse, resumir y pedir decisión.
- Si el usuario mueve mouse/teclado, AgentMax debe pausar y ceder control.

## Tools Beta

- `screenshot`: low, read-only, Tauri real.
- `run_cmd`: medium/high, requiere aprobación, Tauri real.
- `run_powershell`: medium/high, requiere aprobación, Tauri real.
- `read_file`: low, read-only, backend.
- `write_file`: high, requiere aprobación.
- `list_dir`: low, read-only.
- `search_files`: low, read-only.
- `open_app`: medium, requiere aprobación.
- `move_mouse`: medium, requiere aprobación.
- `click`: high, requiere aprobación y evidencia visual.
- `type_text`: high, requiere aprobación.
- `wait`: low, read-only.
- `stop_task`: low.
- `browser_web`: unavailable hasta exponer conector.
- `ocr`: unavailable/stub hasta instalar OCR real.

## Patrones Bloqueados

- `rm -rf`
- `del /s`
- `format`
- `shutdown`
- `taskkill` sin alcance claro
- `Stop-Process`
- `Remove-Item -Recurse/-Force`
- `iwr/irm/curl/wget | iex/powershell/cmd`
- `Set-ExecutionPolicy Bypass`

## Comandos Permitidos Como Observación

- `tasklist`
- `whoami`
- `systeminfo`
- `dir` / `ls`
- `Get-Process`
- `Get-WinEvent`
- `wevtutil qe Application`

## Archivos Relacionados

- Frontend registry: `ui/src/lib/toolRegistry.ts`
- Frontend supervisor: `ui/src/lib/toolSupervisor.ts`
- Rust shell sandbox: `ui/src-tauri/src/commands.rs`
- Python supervisor: `core/tools/safety_supervisor.py`
- Python executor: `core/tools/executor.py`
- Security agent: `core/agents/security_agent.py`
