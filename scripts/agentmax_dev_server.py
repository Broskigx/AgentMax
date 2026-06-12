#!/usr/bin/env python3
"""Explicit development server with reduced, clearly labelled capabilities."""

from __future__ import annotations

import os
import runpy
from pathlib import Path

os.environ["AGENTMAX_RUNTIME_MODE"] = "dev"
os.environ["AGENTMAX_PRODUCT_MODE"] = "0"
os.environ["AGENTMAX_SERVER_WRAPPER"] = "dev"
os.environ.setdefault("AGENTMAX_IPC_AUTH", "0")

print("[agentmax_dev_server] DEVELOPMENT MODE", flush=True)
runpy.run_path(
    str(Path(__file__).resolve().with_name("agentpilot_test_server.py")),
    run_name="__main__",
)
