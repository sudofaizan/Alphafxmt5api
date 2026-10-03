#!/usr/bin/env python3
"""
AlphaFX MT5 VPS — auto-update server.py from GitHub (git pull) and restart.

  python auto_updater.py

Uses git fetch/pull (no raw URL / CDN cache). Requires git on PATH.

Environment (optional):
  ALPHAFX_GIT_REPO       — default https://github.com/sudofaizan/alphafx-live.git
  ALPHAFX_GIT_BRANCH     — default main
  ALPHAFX_GIT_FILE       — file inside repo (default server.py)
  ALPHAFX_REPO_DIR       — clone folder (default ./_repo next to this script)
  ALPHAFX_POLL_SECONDS   — check interval (default 60)
  ALPHAFX_SERVER_FILE    — local server.py path
  ALPHAFX_UPDATE_MODE    — git (default) or raw (legacy URL fetch)
"""
from __future__ import annotations

import hashlib
import os
import shutil
import signal
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

GIT_REPO = os.environ.get("ALPHAFX_GIT_REPO", "https://github.com/sudofaizan/Alphafxmt5api.git")
GIT_BRANCH = os.environ.get("ALPHAFX_GIT_BRANCH", "main")
GIT_FILE = os.environ.get("ALPHAFX_GIT_FILE", "server.py")
UPDATE_MODE = os.environ.get("ALPHAFX_UPDATE_MODE", "git").strip().lower()
REMOTE_URL = os.environ.get(
    "ALPHAFX_SERVER_URL",
    "https://raw.githubusercontent.com/sudofaizan/alphafx-live/refs/heads/main/server.py",
)
POLL_SECONDS = max(15, int(os.environ.get("ALPHAFX_POLL_SECONDS", "60")))
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = Path(os.environ.get("ALPHAFX_REPO_DIR", str(SCRIPT_DIR / "_repo")))
SERVER_FILE = Path(os.environ.get("ALPHAFX_SERVER_FILE", str(SCRIPT_DIR / "server.py")))

_server_proc: subprocess.Popen | None = None
_stop = False
_ssl_warned = False


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [updater] {msg}", flush=True)


def file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def write_server(content: bytes, source: str = "") -> None:
    backup = SERVER_FILE.with_suffix(".py.bak")
    tmp = SERVER_FILE.with_suffix(".py.new")
    tmp.write_bytes(content)
    if SERVER_FILE.is_file():
        backup.write_bytes(SERVER_FILE.read_bytes())
    tmp.replace(SERVER_FILE)
    extra = f" ({source})" if source else ""
    log(f"updated {SERVER_FILE.name}{extra} (backup: {backup.name})")


def start_server() -> subprocess.Popen:
    log(f"starting: {sys.executable} {SERVER_FILE.name}")
    return subprocess.Popen(
        [sys.executable, str(SERVER_FILE)],
        cwd=str(SERVER_FILE.parent),
    )


def stop_server() -> None:
    global _server_proc
    if _server_proc is None:
        return
    if _server_proc.poll() is not None:
        _server_proc = None
        return
    log(f"stopping server (pid {_server_proc.pid})")
    _server_proc.terminate()
    try:
        _server_proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        log("server did not exit — killing")
        _server_proc.kill()
        _server_proc.wait(timeout=5)
    _server_proc = None


def restart_server() -> None:
    global _server_proc
    stop_server()
    _server_proc = start_server()


def git_available() -> bool:
    try:
        r = subprocess.run(
            ["git", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def git_run(args: list[str], cwd: Path, check: bool = False) -> subprocess.CompletedProcess:
    cmd = ["git"] + args
    log(f"git {' '.join(args)}")
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
        check=check,
    )


def git_head(cwd: Path) -> str:
    r = git_run(["rev-parse", "HEAD"], cwd)
    return (r.stdout or "").strip()


def ensure_git_repo() -> None:
    if (REPO_DIR / ".git").is_dir():
        return
    if REPO_DIR.exists():
        shutil.rmtree(REPO_DIR)
    REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
    log(f"cloning {GIT_REPO} (branch {GIT_BRANCH}) → {REPO_DIR.name}/")
    r = subprocess.run(
        [
            "git", "clone",
            "--depth", "1",
            "--branch", GIT_BRANCH,
            "--single-branch",
            GIT_REPO,
            str(REPO_DIR),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if r.returncode != 0:
        raise RuntimeError(f"git clone failed: {r.stderr or r.stdout}")


def sync_server_from_repo(commit: str) -> bool:
    src = REPO_DIR / GIT_FILE
    if not src.is_file():
        raise RuntimeError(f"{GIT_FILE} not found in repo at {src}")
    content = src.read_bytes()
    if len(content) < 100:
        raise RuntimeError(f"{GIT_FILE} in repo is too small")
    new_hash = content_hash(content)
    if file_hash(SERVER_FILE) == new_hash:
        return False
    write_server(content, source=f"git {commit[:8]}")
    return True


def check_and_update_git() -> bool:
    if not git_available():
        log("git not found on PATH — install Git for Windows or set ALPHAFX_UPDATE_MODE=raw")
        return False

    try:
        ensure_git_repo()
    except RuntimeError as e:
        log(str(e))
        return False

    old_commit = git_head(REPO_DIR)
    fetch = git_run(["fetch", "origin", GIT_BRANCH], REPO_DIR)
    if fetch.returncode != 0:
        log(f"git fetch failed: {(fetch.stderr or fetch.stdout).strip()}")
        return False

    remote_ref = f"origin/{GIT_BRANCH}"
    remote_rev = git_run(["rev-parse", remote_ref], REPO_DIR)
    if remote_rev.returncode != 0:
        log(f"cannot resolve {remote_ref}: {(remote_rev.stderr or '').strip()}")
        return False
    new_commit = (remote_rev.stdout or "").strip()

    if new_commit == old_commit:
        return False

    pull = git_run(["pull", "--ff-only", "origin", GIT_BRANCH], REPO_DIR)
    if pull.returncode != 0:
        log(f"git pull failed: {(pull.stderr or pull.stdout).strip()}")
        return False

    pulled = git_head(REPO_DIR)
    log(f"new commit: {old_commit[:8]} → {pulled[:8]}")
    return sync_server_from_repo(pulled)


# --- legacy raw URL mode (optional) ---
def ssl_verify_enabled() -> bool:
    return os.environ.get("ALPHAFX_SSL_VERIFY", "1").strip().lower() not in ("0", "false", "no")


def make_ssl_context(verify: bool) -> ssl.SSLContext:
    if not verify:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch_remote_raw() -> bytes:
    global _ssl_warned
    req = urllib.request.Request(
        REMOTE_URL,
        headers={
            "User-Agent": "AlphaFX-VPS-Updater/1.0",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )

    def _get(ctx: ssl.SSLContext) -> bytes:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            return resp.read()

    verify = ssl_verify_enabled()
    try:
        data = _get(make_ssl_context(verify))
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        ssl_failed = "CERTIFICATE_VERIFY_FAILED" in str(reason)
        if verify and ssl_failed:
            if not _ssl_warned:
                log("SSL verify failed — retrying without verify")
                _ssl_warned = True
            data = _get(make_ssl_context(False))
        else:
            raise
    if not data or len(data) < 100:
        raise RuntimeError("remote file too small or empty")
    return data


def check_and_update_raw() -> bool:
    try:
        remote = fetch_remote_raw()
    except (urllib.error.URLError, TimeoutError, RuntimeError) as e:
        log(f"raw fetch failed: {e}")
        return False
    remote_hash = content_hash(remote)
    if file_hash(SERVER_FILE) == remote_hash:
        return False
    log(f"new raw version (hash {remote_hash[:8]})")
    write_server(remote, source="raw url")
    return True


def check_and_update() -> bool:
    if UPDATE_MODE == "raw":
        return check_and_update_raw()
    return check_and_update_git()


def ensure_local_server() -> None:
    if SERVER_FILE.is_file():
        return
    log(f"{SERVER_FILE.name} missing — pulling from GitHub")
    if UPDATE_MODE == "raw":
        write_server(fetch_remote_raw(), source="raw url")
        return
    ensure_git_repo()
    commit = git_head(REPO_DIR)
    sync_server_from_repo(commit)


def on_signal(signum, _frame) -> None:
    global _stop
    log(f"signal {signum} — shutting down")
    _stop = True


def main() -> int:
    global _server_proc, _stop

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, on_signal)

    log(f"mode:   {UPDATE_MODE}")
    if UPDATE_MODE == "git":
        log(f"repo:   {GIT_REPO}")
        log(f"branch: {GIT_BRANCH}")
        log(f"file:   {GIT_FILE}")
        log(f"clone:  {REPO_DIR}")
    else:
        log(f"url:    {REMOTE_URL}")
    log(f"local:  {SERVER_FILE}")
    log(f"poll:   every {POLL_SECONDS}s")

    ensure_local_server()
    _server_proc = start_server()

    while not _stop:
        for _ in range(POLL_SECONDS):
            if _stop:
                break
            if _server_proc and _server_proc.poll() is not None:
                code = _server_proc.returncode
                log(f"server exited (code {code}) — restarting in 5s")
                time.sleep(5)
                if not _stop:
                    _server_proc = start_server()
                break
            time.sleep(1)
        else:
            if check_and_update():
                restart_server()

    stop_server()
    log("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
