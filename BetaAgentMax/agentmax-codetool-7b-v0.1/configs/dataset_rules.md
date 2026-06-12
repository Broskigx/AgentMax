# Dataset Rules

This dataset trains AgentMax-CodeTool-7B-v0.1 to behave like a cautious local coding and tool agent for AgentMax.

## Hard Rules

- Every JSONL line must be valid JSON.
- Every example must contain `messages`.
- Allowed roles: `system`, `user`, `assistant`, `tool`.
- Assistant `tool_calls` must be a list.
- Each tool call must contain `name` and `arguments`.
- `arguments` must be a JSON object.
- Tool names must exist in `tools_schema.json`.
- Do not claim work is fixed without relevant tool results.
- Do not invent command output, file contents, diffs, test results, or git state.
- Prefer `read_files`, `search_repo`, `list_dir`, `git_status`, and `git_diff` over `run_command` when they answer the need.
- Prefer `patch_file` over `write_file` for small edits.
- Use `ask_confirmation` before destructive deletes, force pushes, remote code execution, policy changes, or mass process termination.
- Do not include real secrets, API keys, credentials, private keys, auth tokens, or customer data.
- Do not expose mocked endpoints as production security.
- Do not train the model to simulate execution or repeat the same command indefinitely.

## Required Coverage

The dataset covers these categories:

1. npm run build falla en ui
2. dependencia faltante en package-lock
3. Tauri build falla
4. FastAPI router import roto
5. docker-compose apunta a carpetas inexistentes
6. .gitignore ignora ui/
7. endpoints de seguridad mockeados
8. backend sin .env.example
9. puertos inconsistentes
10. usuario pide borrar carpetas
11. usuario pide git push --force
12. test fallido
13. comando repetido
14. tool falla
15. archivo no existe
16. usuario pide feature nueva pero hay P0 antes
17. resumen final con git diff
18. ejecutar tests de forma segura
19. leer logs y diagnosticar
20. pedir confirmacion por riesgo

## Expansion Guidance

Add small realistic conversations. Include a tool result whenever the assistant makes a claim based on execution or file contents. Keep examples focused on behavior, not memorizing the repository.
