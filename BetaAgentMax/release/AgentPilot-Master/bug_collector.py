#!/usr/bin/env python3
"""
BugCollector — captura errores de training, crashes y feedback de chat.
Los guarda en la DB cifrada para usarlos en fine-tuning automatico.
"""
import json, os, sys, time, traceback
from typing import Optional

# Importar crypto_db si esta disponible
try:
    from crypto_db import get_db, CryptoDB
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False

# Fallback: guardar en JSONL plano
FALLBACK_PATH = os.path.join(os.path.dirname(__file__), "data", "bugs_fallback.jsonl")


class BugCollector:
    def __init__(self, db: Optional["CryptoDB"] = None,
                 db_path: str = "data/NixControl.db",
                 password: str = "NixControl_default"):
        if db:
            self.db = db
        elif _DB_AVAILABLE:
            try:
                self.db = get_db(db_path, password)
            except Exception:
                self.db = None
        else:
            self.db = None

    def _save(self, bug_type: str, data: dict, severity: str = "medium"):
        data["_timestamp"] = time.time()
        if self.db:
            try:
                bid = self.db.save_bug(bug_type, data, severity)
                return bid
            except Exception as e:
                print(f"[BugCollector] DB error: {e}", file=sys.stderr)

        # Fallback: JSONL plano
        try:
            os.makedirs(os.path.dirname(FALLBACK_PATH), exist_ok=True)
            with open(FALLBACK_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": bug_type, "severity": severity, **data}, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[BugCollector] Fallback write error: {e}", file=sys.stderr)
        return None

    def capture_training_error(self, exception: Exception, config: dict, stage: str = "training") -> Optional[int]:
        """Captura errores durante el training loop."""
        try:
            import torch
            vram_info = {}
            if torch.cuda.is_available():
                vram_info = {
                    "vram_allocated_gb": round(torch.cuda.memory_allocated() / 1e9, 2),
                    "vram_reserved_gb":  round(torch.cuda.memory_reserved() / 1e9, 2),
                    "vram_peak_gb":      round(torch.cuda.max_memory_allocated() / 1e9, 2),
                }
        except ImportError:
            vram_info = {}

        data = {
            "exception_type": type(exception).__name__,
            "exception_msg":  str(exception)[:500],
            "traceback":      traceback.format_exc()[:2000],
            "stage":          stage,
            "config":         {k: v for k, v in config.items() if isinstance(v, (str, int, float, bool))},
            **vram_info,
        }

        severity = "critical" if "CUDA out of memory" in str(exception) else "high"
        bid = self._save("training_error", data, severity)
        print(f"[BugCollector] Error de training capturado (ID={bid}): {type(exception).__name__}", flush=True)
        return bid

    def capture_oom(self, config: dict, batch_size: int, seq_len: int) -> Optional[int]:
        """Captura especificamente errores OOM para aprender configuraciones validas."""
        data = {
            "batch_size": batch_size,
            "seq_len":    seq_len,
            "config":     config,
            "suggestion": f"Reducir batch_size a {max(1, batch_size // 2)} o seq_len a {max(512, seq_len // 2)}",
        }
        return self._save("oom_error", data, severity="high")

    def capture_chat_feedback(self, user_input: str, model_output: str,
                              corrected_output: str, session_id: str = "") -> Optional[int]:
        """Captura correcciones del usuario para fine-tuning."""
        data = {
            "input":      user_input[:1000],
            "bad_output": model_output[:2000],
            "correction": corrected_output[:2000],
            "session_id": session_id,
        }
        return self._save("chat_feedback", data, severity="medium")

    def capture_tool_error(self, tool_name: str, args: dict, error: str) -> Optional[int]:
        """Captura fallos de herramientas del agente."""
        data = {
            "tool":  tool_name,
            "args":  {k: str(v)[:200] for k, v in args.items()},
            "error": error[:500],
        }
        return self._save("tool_error", data, severity="medium")

    def export_for_finetuning(self) -> list[dict]:
        """
        Convierte bugs capturados → ejemplos de entrenamiento en formato messages.
        Usa el sistema de prompt de NixControl.
        """
        system = (
            "Eres NixControl, un agente de IA de escritorio desarrollado por NixControl. "
            "Usas herramientas reales y reportas errores con honestidad."
        )

        examples = []

        if self.db:
            bugs = self.db.load_bugs(only_unfixed=True, limit=1000)
        else:
            bugs = self._load_fallback()

        for bug in bugs:
            btype = bug.get("_bug_type", bug.get("type", ""))

            if btype == "training_error":
                ex = self._bug_to_training_example(bug, system)
            elif btype == "chat_feedback":
                ex = self._feedback_to_example(bug, system)
            elif btype == "oom_error":
                ex = self._oom_to_example(bug, system)
            elif btype == "tool_error":
                ex = self._tool_error_to_example(bug, system)
            else:
                continue

            if ex:
                examples.append({"messages": ex, "_source_bug_id": bug.get("_id")})

        return examples

    def _bug_to_training_example(self, bug: dict, system: str) -> list[dict]:
        exc_type = bug.get("exception_type", "UnknownError")
        stage    = bug.get("stage", "training")
        cfg      = bug.get("config", {})
        return [
            {"role": "system",    "content": system},
            {"role": "user",      "content": f"Inicia el entrenamiento con batch_size={cfg.get('batch_size',1)} y lora_rank={cfg.get('lora_rank',16)}"},
            {"role": "assistant", "content": f"Error en etapa '{stage}': {exc_type}. Diagnosticando..."},
            {"role": "tool",      "content": json.dumps({"ok": False, "error": exc_type, "stage": stage})},
            {"role": "assistant", "content": f"El entrenamiento fallo con {exc_type} en la etapa {stage}. "
                                             f"Recomiendo revisar la configuracion y reducir batch_size o seq_length."},
        ]

    def _feedback_to_example(self, bug: dict, system: str) -> list[dict]:
        return [
            {"role": "system",    "content": system},
            {"role": "user",      "content": bug.get("input", "")},
            {"role": "assistant", "content": bug.get("correction", "")},
        ]

    def _oom_to_example(self, bug: dict, system: str) -> list[dict]:
        bs  = bug.get("batch_size", 1)
        seq = bug.get("seq_len", 2048)
        sug = bug.get("suggestion", "Reducir batch_size o seq_len")
        return [
            {"role": "system",    "content": system},
            {"role": "user",      "content": f"Entrena con batch_size={bs}, seq_len={seq}"},
            {"role": "assistant", "content": f"CUDA OOM con batch_size={bs}, seq_len={seq}. {sug}"},
        ]

    def _tool_error_to_example(self, bug: dict, system: str) -> list[dict]:
        tool  = bug.get("tool", "unknown")
        error = bug.get("error", "")
        return [
            {"role": "system",    "content": system},
            {"role": "user",      "content": f"Ejecuta la herramienta {tool}"},
            {"role": "assistant", "content": f"La herramienta {tool} fallo: {error}. Intento alternativa."},
            {"role": "tool",      "content": json.dumps({"ok": False, "error": error})},
            {"role": "assistant", "content": f"Error en {tool}. Diagnostico con screenshot y estrategia alternativa."},
        ]

    def _load_fallback(self) -> list[dict]:
        if not os.path.exists(FALLBACK_PATH):
            return []
        bugs = []
        with open(FALLBACK_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        bugs.append(json.loads(line))
                    except Exception:
                        pass
        return bugs

    def status(self) -> dict:
        if self.db:
            s = self.db.stats()
            return {"bugs_open": s["bugs_open"], "bugs_total": s["bugs_total"], "db": s["db_path"]}
        # Count fallback
        count = 0
        if os.path.exists(FALLBACK_PATH):
            with open(FALLBACK_PATH) as f:
                count = sum(1 for l in f if l.strip())
        return {"bugs_open": count, "bugs_total": count, "db": FALLBACK_PATH}


# ── Integracion con train_ddp.py ──────────────────────────────────────────────
def install_training_hook(collector: BugCollector):
    """
    Envuelve el bloque trainer.train() para capturar errores automaticamente.
    Uso: install_training_hook(collector) antes de trainer.train()
    """
    import builtins
    _original_excepthook = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            _original_excepthook(exc_type, exc_value, exc_tb)
            return
        collector.capture_training_error(exc_value, {}, stage="unhandled")
        _original_excepthook(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


if __name__ == "__main__":
    col = BugCollector(db_path="data/test_bugs.db", password="test123")
    print("Status inicial:", col.status())

    try:
        raise MemoryError("CUDA out of memory. Tried to allocate 2.5 GiB")
    except MemoryError as e:
        col.capture_training_error(e, {"batch_size": 4, "lora_rank": 64}, stage="forward_pass")

    col.capture_chat_feedback(
        "Abre Chrome", "Abriendo Firefox...",
        "Abriendo Chrome.", "test-session-1"
    )

    examples = col.export_for_finetuning()
    print(f"Ejemplos exportados para fine-tuning: {len(examples)}")
    print("Status final:", col.status())
