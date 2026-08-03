"""
M-flow frontend UI launcher.

Handles development and production frontend server startup,
including optional backend and MCP server orchestration.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import webbrowser
import zipfile
from pathlib import Path
from typing import Callable

import requests

from m_flow.shared.logging_utils import get_logger
from m_flow.version import get_version

from .node_setup import check_node_npm, get_nvm_dir, get_nvm_sh_path
from .npm_utils import run_npm_command

_log = get_logger()

_CACHE_SUBDIR = ".m_flow/ui-cache"
_DEP_INSTALL_TIMEOUT = 300  # seconds


# ===================== Port Checking =====================


def _port_available(port: int) -> bool:
    """Return True if port is free on localhost."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            return s.connect_ex(("localhost", port)) != 0
    except Exception:
        return False


def _check_ports(ports: list[tuple[int, str]]) -> tuple[bool, list[str]]:
    """Check availability of multiple ports."""
    blocked = []
    for p, name in ports:
        if not _port_available(p):
            blocked.append(f"{name} (port {p})")
            _log.error("Port %d in use for %s", p, name)
    return len(blocked) == 0, blocked


# ===================== Process Output Streaming =====================


def _stream_output(proc: subprocess.Popen, stream: str, tag: str, color: str = "") -> threading.Thread:
    """Stream subprocess output with a prefix."""

    def reader():
        src = getattr(proc, stream)
        if not src:
            return
        reset = "\033[0m" if color else ""
        try:
            for line in iter(src.readline, b""):
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    _log.info("%s%s%s %s", color, tag, reset, text)
        except Exception:
            pass
        finally:
            src.close()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    return t


# ===================== Version Utilities =====================


def _normalize_version(v: str) -> str:
    """Strip dev suffixes for comparison."""
    for suffix in ("-local", "-dev", "-alpha", "-beta"):
        v = v.replace(suffix, "")
    return v.strip()


# ===================== Cache Management =====================


def _cache_dir() -> Path:
    d = Path.home() / _CACHE_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download_url() -> tuple[str, str]:
    """Return (download_url, version)."""
    ver = get_version()
    clean = ver.replace("-local", "")
    url = f"https://github.com/FlowElement-xinliuyuansu/m_flow/archive/refs/tags/v{clean}.zip"
    return url, ver


def download_frontend_assets(force: bool = False) -> bool:
    """
    Download and cache frontend from GitHub release.

    Returns True on success.
    """
    cache = _cache_dir()
    frontend = cache / "frontend"
    ver_file = cache / "version.txt"

    if not force and frontend.exists() and ver_file.exists():
        try:
            cached = ver_file.read_text().strip()
            current = get_version()
            if _normalize_version(cached) == _normalize_version(current):
                _log.debug("Frontend cached for %s", current)
                return True
            _log.info("Version mismatch: cached=%s current=%s", cached, current)
            shutil.rmtree(frontend, ignore_errors=True)
            ver_file.unlink(missing_ok=True)
        except Exception as e:
            _log.debug("Cache check failed: %s", e)
            shutil.rmtree(frontend, ignore_errors=True)
            ver_file.unlink(missing_ok=True)

    url, ver = _download_url()
    _log.info("Downloading M-flow frontend v%s...", ver.replace("-local", ""))

    try:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "archive.zip"
            _log.info("URL: %s", url)
            resp = requests.get(url, stream=True, timeout=60)
            resp.raise_for_status()
            with open(archive, "wb") as f:
                f.writelines(resp.iter_content(8192))

            shutil.rmtree(frontend, ignore_errors=True)
            extract = Path(tmp) / "extracted"
            with zipfile.ZipFile(archive) as z:
                z.extractall(extract)

            # Locate m_flow-frontend
            src = None
            for root, dirs, _ in os.walk(extract):
                if "m_flow-frontend" in dirs:
                    src = Path(root) / "m_flow-frontend"
                    break

            if not src or not src.exists():
                _log.error("m_flow-frontend not found in archive")
                return False

            shutil.copytree(src, frontend)
            ver_file.write_text(ver)
            _log.info("✓ Frontend cached successfully")
            return True

    except requests.RequestException as e:
        if "404" in str(e):
            _log.error("Release v%s not found on GitHub", ver.replace("-local", ""))
        else:
            _log.error("Download failed: %s", e)
        return False
    except Exception as e:
        _log.error("Frontend download error: %s", e)
        return False


def find_frontend_path() -> Path | None:
    """Locate frontend directory (dev or cached)."""
    here = Path(__file__)
    for parent in here.parents[2:5]:
        candidate = parent / "m_flow-frontend"
        if candidate.exists() and (candidate / "package.json").exists():
            _log.debug("Dev frontend: %s", candidate)
            return candidate

    cached = _cache_dir() / "frontend"
    if cached.exists() and (cached / "package.json").exists():
        _log.debug("Cached frontend: %s", cached)
        return cached
    return None


def _install_deps(path: Path) -> bool:
    """Run npm install if node_modules missing."""
    if (path / "node_modules").exists():
        return True
    _log.info("Installing frontend dependencies...")
    try:
        res = run_npm_command(["npm", "install"], path, timeout=_DEP_INSTALL_TIMEOUT)
        if res.returncode == 0:
            _log.info("Dependencies installed")
            return True
        _log.error("npm install failed: %s", res.stderr)
        return False
    except Exception as e:
        _log.error("Dependency install error: %s", e)
        return False


def _is_dev_frontend(path: Path) -> bool:
    """Check if frontend has Next.js (dev mode)."""
    pkg = path / "package.json"
    if not pkg.exists():
        return False
    try:
        data = json.loads(pkg.read_text())
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        return "next" in deps
    except Exception:
        return False


def _prompt_download() -> bool:
    """Ask user to download frontend."""
    try:
        _log.info("=" * 60)
        _log.info("🎨 M-flow UI Setup")
        _log.info("=" * 60)
        _log.info("Frontend not found. Download from GitHub?")
        _log.info("• Cached in ~/.m_flow/ui-cache/")
        print("• Requires Node.js for npm install")
        ans = input("\nDownload? (y/N): ").strip().lower()
        return ans in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        return False


# ===================== Main Entry Point =====================


def start_ui(
    pid_callback: Callable[[int], None],
    port: int = 3000,
    open_browser: bool = True,
    auto_download: bool = False,
    start_backend: bool = False,
    backend_port: int = 8000,
    start_mcp: bool = False,
    mcp_port: int = 8001,
) -> subprocess.Popen | None:
    """
    Start the M-flow frontend server.

    Optionally starts backend API and MCP servers.

    Args:
        pid_callback: Called with PID of each spawned process.
        port: Frontend port.
        open_browser: Auto-open browser.
        auto_download: Download without prompting.
        start_backend: Also start uvicorn backend.
        backend_port: Backend port.
        start_mcp: Also start MCP server (Docker).
        mcp_port: MCP server port.

    Returns:
        Frontend subprocess or None on failure.
    """
    _log.info("Launching M-flow UI...")

    # Port checks
    ports = [(port, "Frontend")]
    if start_backend:
        ports.append((backend_port, "Backend"))
    if start_mcp:
        ports.append((mcp_port, "MCP"))

    ok, blocked = _check_ports(ports)
    if not ok:
        _log.error("Ports in use: %s", ", ".join(blocked))
        return None
    _log.info("✓ Ports available")

    # MCP server
    if start_mcp:
        _log.info("Starting MCP server (Docker)...")
        try:
            import uuid as _uuid

            img = "m_flow/m_flow-mcp:main"
            subprocess.run(["docker", "pull", img], check=True)
            cname = f"m_flow-mcp-{_uuid.uuid4().hex[:8]}"
            cmd = [
                "docker",
                "run",
                "--name",
                cname,
                "-p",
                f"{mcp_port}:8000",
                "--rm",
                "-e",
                "TRANSPORT_MODE=sse",
            ]
            if start_backend:
                cmd += ["-e", f"API_URL=http://localhost:{backend_port}"]
            else:
                cmd += ["--env-file", str(Path.cwd() / ".env")]
            cmd.append(img)
            mcp_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid if hasattr(os, "setsid") else None,
            )
            _stream_output(mcp_proc, "stdout", "[MCP]", "\033[34m")
            _stream_output(mcp_proc, "stderr", "[MCP]", "\033[34m")
            pid_callback((mcp_proc.pid, cname))
            _log.info("✓ MCP at http://127.0.0.1:%d/sse", mcp_port)
        except Exception as e:
            _log.error("MCP start failed: %s", e)

    # Backend server
    backend_proc = None
    if start_backend:
        _log.info("Starting backend API...")
        import sys

        backend_proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "m_flow.api.client:app",
                "--host",
                "localhost",
                "--port",
                str(backend_port),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            preexec_fn=os.setsid if hasattr(os, "setsid") else None,
        )
        _stream_output(backend_proc, "stdout", "[BACKEND]", "\033[32m")
        _stream_output(backend_proc, "stderr", "[BACKEND]", "\033[32m")
        pid_callback(backend_proc.pid)
        time.sleep(2)
        if backend_proc.poll() is not None:
            _log.error("Backend failed to start")
            return None
        _log.info("✓ Backend at http://localhost:%d", backend_port)

    # Find or download frontend
    frontend = find_frontend_path()
    if not frontend:
        _log.info("Frontend not found locally")
        if auto_download or _prompt_download():
            if download_frontend_assets():
                frontend = find_frontend_path()
        if not frontend:
            _log.error("Frontend unavailable")
            return None

    # Node.js check
    node_ok, msg = check_node_npm()
    if not node_ok:
        _log.error("Node.js required: %s", msg)
        return None

    # Install dependencies
    if not _install_deps(frontend):
        return None

    # Prepare env
    env = os.environ.copy()
    env["HOST"] = "localhost"
    env["PORT"] = str(port)

    nvm_sh = get_nvm_sh_path()
    if platform.system() != "Windows" and nvm_sh.exists():
        nvm_dir = get_nvm_dir()
        node_vers = nvm_dir / "versions" / "node"
        if node_vers.exists():
            vers = sorted(node_vers.iterdir(), reverse=True)
            if vers:
                bin_path = vers[0] / "bin"
                if bin_path.exists():
                    env["PATH"] = f"{bin_path}:{env.get('PATH', '')}"

    # Start frontend
    _log.info("Starting frontend at http://localhost:%d", port)
    try:
        if platform.system() == "Windows":
            proc = subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=frontend,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True,
            )
        elif nvm_sh.exists():
            proc = subprocess.Popen(
                ["bash", "-c", f"source {nvm_sh} && npm run dev"],
                cwd=frontend,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid if hasattr(os, "setsid") else None,
            )
        else:
            proc = subprocess.Popen(
                ["npm", "run", "dev"],
                cwd=frontend,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid if hasattr(os, "setsid") else None,
            )

        _stream_output(proc, "stdout", "[FRONTEND]", "\033[33m")
        _stream_output(proc, "stderr", "[FRONTEND]", "\033[33m")
        pid_callback(proc.pid)

        time.sleep(3)
        if proc.poll() is not None:
            _log.error("Frontend failed to start")
            return None

        if open_browser:

            def delayed():
                time.sleep(5)
                try:
                    webbrowser.open(f"http://localhost:{port}")
                except Exception:
                    pass

            threading.Thread(target=delayed, daemon=True).start()

        _log.info("✓ UI starting at http://localhost:%d", port)
        return proc

    except Exception as e:
        _log.error("Frontend start error: %s", e)
        if backend_proc:
            try:
                backend_proc.terminate()
                backend_proc.wait(timeout=5)
            except Exception:
                backend_proc.kill()
        return None
