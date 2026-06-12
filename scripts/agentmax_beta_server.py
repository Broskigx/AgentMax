#!/usr/bin/env python3
"""Explicit limited closed-beta fallback server."""

from __future__ import annotations

import os
import runpy
from pathlib import Path

os.environ["AGENTMAX_RUNTIME_MODE"] = "beta_fallback"
os.environ["AGENTMAX_PRODUCT_MODE"] = "0"
os.environ["AGENTMAX_SERVER_WRAPPER"] = "beta"
os.environ.setdefault("AGENTMAX_IPC_AUTH", "1")

print(
    "[agentmax_beta_server] LIMITED MODE: desktop runtime is unavailable; "
    "capabilities are reduced.",
    flush=True,
)
runpy.run_path(
    str(Path(__file__).resolve().with_name("agentpilot_test_server.py")),
    run_name="__main__",
)
