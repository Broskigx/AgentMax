"""Upload **redacted** beta logs to the private AgentMax dataset repo on GitHub.

Design constraints (privacy + secret hygiene):
- Uploads ONLY the ``redacted/`` (or ``approved/``) JSONL — never the raw logs.
- Requires explicit opt-in (``consent.is_enabled()``) on the tester path.
- The GitHub token is read from the environment (``AGENTMAX_BETA_UPLOAD_TOKEN``)
  and is **never** baked into the shipped binary. Provision a narrow,
  contents-write fine-grained token per tester, or run this maintainer-side.

Config (env):
    AGENTMAX_BETA_DATA_REPO     "owner/repo" of the private dataset repository
    AGENTMAX_BETA_UPLOAD_TOKEN  GitHub token with contents:write on that repo
    AGENTMAX_BETA_TESTER_ID     optional label for the uploading tester

CLI (maintainer or opted-in tester):
    python -m core.data_collection.uploader --source "$AGENTMAX_DATA_DIR/data/AgentMax_logs/redacted"  # or resolved data dir
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import pathlib
from datetime import UTC, datetime

import httpx

from core.data_collection import consent

log = logging.getLogger(__name__)

_API = "https://api.github.com"
_REPO_ENV = "AGENTMAX_BETA_DATA_REPO"
_TOKEN_ENV = "AGENTMAX_BETA_UPLOAD_TOKEN"  # noqa: S105 — env-var name, not a secret
_TESTER_ENV = "AGENTMAX_BETA_TESTER_ID"


def _config() -> tuple[str | None, str | None, str]:
    repo = os.environ.get(_REPO_ENV, "").strip() or None
    token = os.environ.get(_TOKEN_ENV, "").strip() or None
    tester = os.environ.get(_TESTER_ENV, "").strip() or "anon"
    return repo, token, tester


def upload_file(
    client: httpx.Client, repo: str, token: str, local: pathlib.Path, dest: str
) -> bool:
    """PUT one file to the repo via the Contents API. Returns True on success."""
    content_b64 = base64.b64encode(local.read_bytes()).decode()
    resp = client.put(
        f"{_API}/repos/{repo}/contents/{dest}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json={"message": f"beta-data: {dest}", "content": content_b64},
    )
    if resp.status_code in (200, 201):
        return True
    if resp.status_code == 422:  # already exists — fine, treat as done
        return True
    log.warning("uploader.put_failed status=%s dest=%s", resp.status_code, dest)
    return False


def sync(source: pathlib.Path, *, require_consent: bool = True) -> int:
    """Upload every ``*.jsonl`` under ``source`` to the dataset repo.

    Returns the number of files uploaded (0 if disabled/misconfigured).
    """
    if require_consent and not consent.is_enabled():
        log.info("uploader.skipped: beta data sharing not enabled")
        return 0

    repo, token, tester = _config()
    if not repo or not token:
        log.info("uploader.skipped: %s / %s not configured", _REPO_ENV, _TOKEN_ENV)
        return 0
    if not source.is_dir():
        log.info("uploader.skipped: no source dir %s", source)
        return 0

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    files = sorted(source.glob("*.jsonl"))
    uploaded = 0
    with httpx.Client(timeout=30.0) as client:
        for f in files:
            dest = f"beta-data/{tester}/{stamp}/{f.name}"
            if upload_file(client, repo, token, f, dest):
                uploaded += 1
    log.info("uploader.done uploaded=%d/%d repo=%s", uploaded, len(files), repo)
    return uploaded


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Upload redacted beta logs to the dataset repo.")
    ap.add_argument("--source", default="data/AgentMax_logs/redacted")
    ap.add_argument(
        "--no-consent-check",
        action="store_true",
        help="maintainer mode: skip the tester opt-in gate",
    )
    args = ap.parse_args()
    n = sync(pathlib.Path(args.source), require_consent=not args.no_consent_check)
    print(f"Uploaded {n} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
