"""
Thinking parser for chain-of-thought models (Qwen3-Thinking, DeepSeek-R1, ...).

Modelos como `Qwen3-VL-8B-Thinking` (alias: AgentMax) emiten razonamiento
interno entre `<think>...</think>` antes del texto final. AgentMax fue
entrenado con un dataset que le ENSEÑA a razonar internamente pero NO mostrar
esos bloques al usuario. Sin embargo el modelo base sigue produciendolos en
la salida HTTP: depende de nosotros separarlos del texto que llega al usuario.

Este modulo:
  - separa el bloque <think> del texto final
  - tolera multiples bloques, mayusculas/minusculas, saltos de linea
  - tolera bloques truncados (stream cortado a mitad)
  - es pure-python, sin dependencias

Uso:
    from core.ai.thinking_parser import split_thinking
    thinking, clean = split_thinking(raw)
"""

from __future__ import annotations

import re
from collections.abc import Iterator

# Greedy con flag DOTALL: think es multi-linea, varios bloques posibles.
# Insensible a mayusculas (algunos modelos emiten <Think>, </THINK>).
_THINK_BLOCK_RE = re.compile(
    r"<\s*think\s*>(.*?)<\s*/\s*think\s*>", flags=re.IGNORECASE | re.DOTALL
)

# Caso: stream cortado a mitad de un bloque -> hay <think> pero falta </think>.
# Tomamos desde la apertura hasta el fin de la cadena como pensamiento.
_THINK_OPEN_RE = re.compile(r"<\s*think\s*>", flags=re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"<\s*/\s*think\s*>", flags=re.IGNORECASE)


def split_thinking(raw: str) -> tuple[str, str]:
    """
    Returns ``(thinking, clean_text)``.

    - ``thinking`` es la concatenacion de todos los bloques <think>...</think>
      con separador "\n\n---\n\n" (o "" si no habia).
    - ``clean_text`` es la respuesta sin <think> y sin la tag.

    Casos manejados:
      - 0 bloques        -> ("", raw)
      - 1+ bloques bien formados
      - bloque truncado: <think> sin cierre (stream cortado)
      - tags con espacios o mayusculas: < THINK >, </Think>
    """
    if not raw:
        return "", ""

    # 1) Bloques bien cerrados
    thinkings: list[str] = []

    def _capture(match: re.Match[str]) -> str:
        thinkings.append(match.group(1).strip())
        return ""  # se borra del texto final

    cleaned = _THINK_BLOCK_RE.sub(_capture, raw)

    # 2) Bloques abiertos sin cerrar (truncados): desde <think> hasta EOF
    open_match = _THINK_OPEN_RE.search(cleaned)
    if open_match:
        # Si quedo un cierre suelto sin apertura, lo dropeamos tambien.
        truncated = cleaned[open_match.end() :]
        thinkings.append(truncated.strip())
        cleaned = cleaned[: open_match.start()]

    # Cierres sueltos sin apertura: borrarlos.
    cleaned = _THINK_CLOSE_RE.sub("", cleaned)

    # Colapsa whitespace alrededor de donde estaban los bloques.
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    thinking_text = "\n\n---\n\n".join(t for t in thinkings if t)
    return thinking_text, cleaned


def iter_segments(raw: str) -> Iterator[tuple[str, str]]:
    """
    Yields ``("think", body)`` or ``("text", body)`` segments en orden.

    Util para UI que quiere renderizar el razonamiento colapsado y la respuesta
    visible en bloques separados sin perder el orden.
    """
    if not raw:
        return
    pos = 0
    for match in _THINK_BLOCK_RE.finditer(raw):
        if match.start() > pos:
            txt = raw[pos : match.start()].strip()
            if txt:
                yield ("text", txt)
        yield ("think", match.group(1).strip())
        pos = match.end()
    tail = raw[pos:]
    # bloque truncado al final
    open_m = _THINK_OPEN_RE.search(tail)
    if open_m:
        before = tail[: open_m.start()].strip()
        if before:
            yield ("text", before)
        truncated = tail[open_m.end() :].strip()
        if truncated:
            yield ("think", truncated)
        return
    tail = _THINK_CLOSE_RE.sub("", tail).strip()
    if tail:
        yield ("text", tail)


def has_thinking(raw: str) -> bool:
    """Quick check sin construir nada."""
    return bool(raw) and bool(_THINK_OPEN_RE.search(raw))


__all__ = ["split_thinking", "iter_segments", "has_thinking"]
