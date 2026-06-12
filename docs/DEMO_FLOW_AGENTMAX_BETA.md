# AgentMax — Demo Flow (Technical Beta Candidate)

> Three demos showing AgentMax capabilities for beta recording.

---

## Demo 1: Normal Chat + Tokens + Screenshot

**Duration:** ~3 minutes  
**Goal:** Show AgentMax as a working AI desktop assistant.

### Steps

1. **Open AgentMax**
   - Start the app (Tauri desktop or `npm run dev`)
   - Onboarding appears (show for ~2s then dismiss)

2. **Show the Main Screen**
   - **Left sidebar:** AgentMax logo, status indicators, token balance
   - **Center:** Chat area with empty state
   - **Right panel:** Thinking stages, checklist, tools registry, screenshot
   - **Top bar:** "Technical Beta Candidate" badge, Stop Agent button

3. **Check Tokens**
   - Sidebar shows: "Saldo disponible" with token count
   - Plan selector: Free / Starter / Pro / Local
   - Click the plan to show different limits

4. **Click Screenshot**
   - Click camera button in input bar
   - Screenshot preview appears in right panel
   - Show metadata: resolution, size, timestamp
   - Click "Enviar al agente" → attaches to chat

5. **Send a Simple Message**
   - Type: `"¿Cuántos tokens me quedan?"`
   - Send → observe the message bubble
   - Show assistant response
   - Checklist on right updates

6. **Review Session Logs**
   - Open browser DevTools → Application → Local Storage
   - Show `AgentMax.session.*` entries with redacted data

### Expected Result

Clean, professional UI. Chat works. Tokens display. Screenshot captures (if Tauri). AgentMax feels ready to test.

---

## Demo 2: Tools + Safety + Permissions

**Duration:** ~4 minutes  
**Goal:** Show that tools are controlled and safety is active.

### Steps

1. **Open Tools Panel**
   - Right sidebar shows tool registry
   - Each tool has: name, risk level, status (ready/unavailable)
   - Read-only tools: `run_cmd`, `list_dir`, `search_files`
   - Destructive tools: `write_file`, `kill_process` (require approval)

2. **Run a Read-Only Command**
   - Open Terminal panel (sidebar button)
   - Type: `dir .` or `echo "hello"`
   - Terminal shows: command, stdout, exit code, duration
   - No permission prompt needed (read-only authorized)

3. **Attempt a Sensitive Action**
   - Try a destructive command (Truncated — would need `runShellCommand`)
   - Show that supervisor intercepts dangerous patterns

4. **Show Tool Registry Status**
   - Right panel: 14 tools listed
   - Colors: green = ready, gray = unavailable
   - Risk badges: low, medium, high, critical

5. **Test the "Stop Agent" Button**
   - Click Stop Agent (top bar)
   - Button is always visible during task execution

### Expected Result

Tools are visible, permission-controlled, and supervised. Read-only runs freely. Destructive actions blocked. User feels in control.

---

## Demo 3: AgentMax Preview + Sanitizer

**Duration:** ~3 minutes  
**Goal:** Show AgentMax Preview with graceful fallback and clean output.

### Steps

1. **Show AgentMax in Settings**
   - Open AISettings (top-right gear icon)
   - Show backend options: LM Studio, AgentMax Preview, API, Mock/Demo
   - "AgentMax Preview" has badge: ⚠️ Experimental

2. **Case A: AgentMax Not Available**
   - If AgentMax adapter/runtime is missing:
   - Select AgentMax Preview
   - App shows: "AgentMax Preview is not configured yet"
   - No crash, no stack trace
   - User can switch to LM Studio or API backend

3. **Case B: AgentMax Responds with `<think>`**
   - Agent sends raw: `<think>Reasoning steps...</think>Visible response`
   - Sanitizer strips the `<think>` block
   - User sees only: `Visible response`
   - Developer logs show: `reasoning_leak_detected: true`
   - No visible `<think>` in the UI

4. **Show AgentMax Logs**
   - AgentMax panel records:
     - Messages sent/received
     - HTTP requests with status codes
     - Errors and timeouts
     - Request/response pairs

5. **Fallback to LM Studio**
   - Click LM Studio button in settings
   - Status changes to "Conectado" / "Sin conexion"
   - Normal chat continues without disruption

### Expected Result

AgentMax clearly marked as Preview. If unavailable, fallback is clean. `<think>` tags never reach the user. Session logs capture everything for future fine-tuning.

---

## Recording Tips

- **Screen resolution:** 1920×1080 or 2560×1440
- **Frame rate:** 30fps
- **Audio:** Optional voiceover in Spanish or English
- **Background:** Dark theme (already default)
- **Do not show:** Stack traces, debug logs, raw `<think>` content
- **Do show:** Clean UI, token balance, permissions, graceful error handling

---

*AgentMax Technical Beta Candidate — 2026-05-21*
