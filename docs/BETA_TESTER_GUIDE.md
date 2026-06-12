# AgentMax — Closed Beta Tester Guide

Welcome, and thanks for testing AgentMax. This guide gets you from download to
first task in a few minutes.

> **Confidential.** AgentMax is proprietary, closed-source software. Please don't
> share the binary, screenshots of internals, or this guide outside the beta.

## 1. Install

Download the binary for your OS from the beta release (single file, no installer):

| OS | File |
|----|------|
| Windows | `AgentMax.exe` |
| Linux | `AgentMax` (`chmod +x AgentMax`) |
| macOS | `AgentMax` (`chmod +x AgentMax`; right-click → Open the first time) |

There's nothing to install — the binary is self-contained. It does **not** bundle
the heavy ML stack; AgentMax runs via LM Studio (see §3).

## 2. Pick a backend (one-time)

Create a `.env` file next to the binary (copy `.env.example`) and choose one:

**A) Claude (cloud — easiest):**
```
AGENTMAX_BACKEND=claude
ANTHROPIC_API_KEY=<your Anthropic API key>
```

**B) LM Studio (local / offline — runs AgentMax):**
```
AGENTMAX_BACKEND=lmstudio
AGENTMAX_LMS_HOST=127.0.0.1
AGENTMAX_LMS_PORT=1234
```

## 3. Using AgentMax (the local model)

AgentMax is the Qwen3-VL-Thinking model fine-tuned for desktop control. To use it:

1. Open **LM Studio** → load the AgentMax model → **Local Server → Start**.
2. Set `AGENTMAX_BACKEND=lmstudio` in your `.env`.
3. Launch AgentMax — it now drives AgentMax locally, fully offline.

## 4. First run

Launch the binary. On first run you'll see a **one-time data-sharing prompt**
(see §5). Then type a task at the prompt:

```
╰─➤ open notepad and write "hello from AgentMax"
```

Commands: `status`, `panic` (emergency stop), `exit`.

## 5. Optional: help improve AgentMax (opt-in)

If you opt in, AgentMax stores a **redacted** log of your tasks (the command,
which tools ran, and the outcome) to help train future AgentMax models.

- **Off by default** — nothing is collected unless you say yes.
- An automatic redactor strips emails, secrets/API keys, IPs, usernames, file
  paths, machine names, and raw screenshots **before** anything is written.
- Data goes only to the private AgentMax dataset repo.
- Change your mind anytime: set `AGENTMAX_BETA_DATA_OPTIN=0` in `.env`, or delete
  `data/AgentMax_logs/.consent.json`.

## 6. Reporting bugs

Send the maintainer:
- What you typed and what happened (vs. what you expected)
- Your OS and backend (Claude / LM Studio)
- The log file: `runtime_logs/` next to the binary

Do **not** open public issues — this is a closed beta.

## 7. Known limits (beta)

- Windows is the primary, best-tested platform; Linux/macOS builds are early.
- The local in-process `local_peft` backend needs a GPU + the source install;
  from the binary, use **LM Studio** for AgentMax.
- Destructive actions ask for confirmation; `panic` stops everything.
