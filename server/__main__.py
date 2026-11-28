from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import threading
import webbrowser

import uvicorn

from quorune.python_runtime import require_supported_python


ROOT = Path(__file__).resolve().parents[1]

require_supported_python()


def _browser_needs_build(web_root: Path) -> bool:
    index = web_root / "dist" / "index.html"
    if not index.is_file():
        return True
    built_at = index.stat().st_mtime
    sources = [web_root / "index.html", web_root / "package-lock.json"]
    sources.extend((web_root / "src").rglob("*"))
    return any(path.is_file() and path.stat().st_mtime > built_at for path in sources)


def _prepare_browser(web_root: Path) -> None:
    if not _browser_needs_build(web_root):
        return
    npm = shutil.which("npm")
    if npm is None:
        raise SystemExit(
            "The browser needs to be built, but npm was not found. Install Node.js 22+ and rerun."
        )
    install_marker = web_root / "node_modules" / ".package-lock.json"
    lockfile = web_root / "package-lock.json"
    dependencies_stale = (
        not install_marker.is_file()
        or (lockfile.is_file() and lockfile.stat().st_mtime > install_marker.stat().st_mtime)
    )
    if dependencies_stale:
        print("Installing browser dependencies…", flush=True)
        subprocess.run([npm, "ci"], cwd=web_root, check=True)
    print("Building the Quorune browser…", flush=True)
    subprocess.run([npm, "run", "build"], cwd=web_root, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="quorune-server",
        description="Start the local Quorune server and browser client.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8000, type=int)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument(
        "--no-build-browser",
        action="store_true",
        help="Do not build the React client when its sources changed",
    )
    parser.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        help="Open the local browser after startup (opt in)",
    )
    parser.add_argument(
        "--no-open",
        dest="open_browser",
        action="store_false",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(open_browser=False)
    parser.add_argument(
        "--auto-pass-empty-priority",
        action="store_true",
        help="Use engine-managed empty priority windows for new playtest games",
    )
    parser.add_argument(
        "--full-priority-windows",
        action="store_true",
        help="Keep every empty priority window available in new games",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use the configured local card database without Scryfall update checks",
    )
    args = parser.parse_args()
    web_root = ROOT / "web"
    if not args.no_build_browser:
        _prepare_browser(web_root)
    os.environ.setdefault("MTG_WEB_DIST", str(web_root / "dist"))
    if args.offline:
        os.environ["MTG_AUTO_UPDATE_CARDS"] = "0"
    if args.full_priority_windows:
        os.environ["MTG_AUTO_PASS_EMPTY_PRIORITY"] = "0"
    elif args.auto_pass_empty_priority:
        os.environ["MTG_AUTO_PASS_EMPTY_PRIORITY"] = "1"
    url = f"http://{args.host}:{args.port}"
    if args.open_browser:
        opener = threading.Timer(1.2, lambda: webbrowser.open(url))
        opener.daemon = True
        opener.start()
    print(f"Quorune: {url}", flush=True)
    print("The server manages card-data updates and the local image cache.", flush=True)
    uvicorn.run(
        "server.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
