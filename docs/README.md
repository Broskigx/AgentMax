# AgentMax Documentation

Start with the top-level [README](../README.md), [ARCH.md](../ARCH.md), and
[PROJECT_STRUCTURE.md](../PROJECT_STRUCTURE.md). This folder holds the deeper
references, audits, and release material.

## Architecture & runtime

| Document | What it covers |
|----------|----------------|
| [AgentMax_ARCHITECTURE_AUDIT.md](AgentMax_ARCHITECTURE_AUDIT.md) | Agent/runtime architecture deep dive |
| [AgentMax_RUNTIME.md](AgentMax_RUNTIME.md) | Runtime boot order and lifecycle |
| [api.md](api.md) | IPC / REST API reference |
| [desktop-automation.md](desktop-automation.md) | Native desktop automation contracts and diagnostics |
| [platform-support.md](platform-support.md) | Windows, Linux, and macOS support matrix |
| [deployment.md](deployment.md) · [../DEPLOYMENT.md](../DEPLOYMENT.md) | Deployment guide |

## Security & privacy

| Document | What it covers |
|----------|----------------|
| [AgentMax_SECURITY.md](AgentMax_SECURITY.md) | Security model and licensing/anti-tamper |
| [AGENTMAX_TOOLS_SECURITY.md](AGENTMAX_TOOLS_SECURITY.md) | Tool-execution safety (deny-list, consent, gating) |
| [AgentMax_DATA_COLLECTION.md](AgentMax_DATA_COLLECTION.md) | What telemetry/data is collected and how it is redacted |
| [SECURITY_REVIEW.md](SECURITY_REVIEW.md) | Source security review — crypto, sandboxing, SSRF, findings |
| [../SECURITY.md](../SECURITY.md) | Vulnerability reporting policy |

## Local model (AgentMax V2.1)

| Document | What it covers |
|----------|----------------|
| [AgentMax_V2_1_LOCAL_SETUP.md](AgentMax_V2_1_LOCAL_SETUP.md) | Running the local Qwen3-VL adapter |
| [AgentMax_release_dataset.md](AgentMax_release_dataset.md) | Training-dataset notes |
| [../README_TRAIN_AgentMax_V2.md](../README_TRAIN_AgentMax_V2.md) | Fine-tuning the model |

## Release & QA

| Document | What it covers |
|----------|----------------|
| [BETA_TESTER_GUIDE.md](BETA_TESTER_GUIDE.md) | Install/run guide handed to closed-beta testers |
| [beta-release-checklist.md](beta-release-checklist.md) | Current beta verification checklist |
| [RELEASE_BETA_CHECKLIST.md](RELEASE_BETA_CHECKLIST.md) | Pre-release checklist |
| [DEMO_FLOW_AGENTMAX_BETA.md](DEMO_FLOW_AGENTMAX_BETA.md) | Scripted demo walkthrough |

## Audit reports (point-in-time)

These are historical snapshots — they reflect the state of the project on their
date, not necessarily the current code.

| Document | Date / focus |
|----------|--------------|
| [qa_critical_audit_2026-05-21.md](qa_critical_audit_2026-05-21.md) | Critical QA audit (2026-05-21) |
| [AGENTMAX_BETA_AUDIT.md](AGENTMAX_BETA_AUDIT.md) | Beta audit |
| [AGENTMAX_BETA_READINESS.md](AGENTMAX_BETA_READINESS.md) | Beta readiness review |
| [AGENTMAX_FINAL_POLISH_AUDIT.md](AGENTMAX_FINAL_POLISH_AUDIT.md) | Final-polish audit |
