#!/usr/bin/env python3
"""Run the editor and deploy verified builds for a GitHub branch."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import tarfile
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

API = "https://api.github.com/repos/imagepit/OpenChatCut"
ASSET = "openchatcut-web-darwin-arm64.tar.gz"


def read_api(path):
    request = Request(API + path, headers={"Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def prepare_release(options, active, rejected):
    commit = read_api(f"/commits/{quote(options.ref, safe='')}")["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("Invalid commit SHA")
    if commit == active or commit == rejected:
        return None

    prefix = "openchatcut-web-" if options.ref == "main" else "openchatcut-web-test-"
    tag = prefix + commit
    try:
        release = read_api(f"/releases/tags/{tag}")
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
    asset, = (item for item in release["assets"] if item["name"] == ASSET)
    digest = asset["digest"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("Missing release asset digest")

    target = options.root / commit
    if target.is_dir() and (target / "VERIFIED_DIGEST").read_text().strip() == digest:
        return commit
    url = f"https://github.com/imagepit/OpenChatCut/releases/download/{tag}/{ASSET}"
    with tempfile.TemporaryDirectory(dir=options.root) as temporary:
        archive = Path(temporary) / ASSET
        hashed = hashlib.sha256()
        with urlopen(url, timeout=45) as response, archive.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                hashed.update(chunk)
                output.write(chunk)
        if "sha256:" + hashed.hexdigest() != digest:
            raise ValueError("Release asset checksum mismatch")
        with tarfile.open(archive) as package:
            package.extractall(temporary, filter="data")
        candidate = Path(temporary) / "openchatcut-web"
        if (candidate / "BUILD_COMMIT").read_text().strip() != commit:
            raise ValueError("Package commit mismatch")
        subprocess.run(["npm", "ci", "--no-audit", "--no-fund", "--loglevel=error"],
                       cwd=candidate, check=True, timeout=300)
        (candidate / "VERIFIED_DIGEST").write_text(digest + "\n")
        if target.exists():
            raise ValueError("Release already exists with a different digest")
        candidate.rename(target)
    return commit


def stop(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def start(options, commit):
    if commit is None:
        cwd = options.fallback
        args = ["node", "scripts/dev-profile.mjs", "--host", "127.0.0.1",
                "--port", str(options.port), "--no-open"]
    else:
        cwd = options.root / commit
        args = ["node", "--import", "tsx", "scripts/start-web.mjs"]
    env = {**os.environ, "OPENCHATCUT_WEB_PORT": str(options.port)}
    process = subprocess.Popen(args, cwd=cwd, env=env, start_new_session=True)
    try:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and process.poll() is None:
            try:
                with urlopen(f"http://127.0.0.1:{options.port}/", timeout=2) as response:
                    html = response.read()
                    if b"<title>OpenChatCut</title>" in html and (
                        commit is None or b"/assets/" in html
                    ):
                        print(f"Serving {commit or 'local checkout'} on port {options.port}", flush=True)
                        return process
            except OSError:
                pass
            time.sleep(1)
        raise RuntimeError(f"OpenChatCut startup failed for {commit or 'local checkout'}")
    except BaseException:
        stop(process)
        raise


def write_state(path, active, previous):
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps({"active": active, "previous": previous}) + "\n")
    pending.replace(path)


def run(options):
    options.root.mkdir(parents=True, exist_ok=True)
    path = options.root / "current.json"
    state = json.loads(path.read_text()) if path.exists() else {}
    active, previous = state.get("active"), state.get("previous")
    rejected = None
    try:
        process = start(options, active)
    except (OSError, RuntimeError):
        if active is None:
            raise
        rejected, active = active, previous
        process = start(options, active)
        write_state(path, active, None)

    executor = ThreadPoolExecutor(max_workers=1)
    try:
        pending = None
        next_check = 0
        while process.poll() is None:
            if pending is None and time.monotonic() >= next_check:
                pending = executor.submit(prepare_release, options, active, rejected)
            if pending is not None and pending.done():
                candidate = None
                try:
                    candidate = pending.result()
                except Exception as error:
                    print(f"Update preparation failed: {error}", flush=True)
                pending = None
                next_check = time.monotonic() + options.interval
                if candidate is not None:
                    stop(process)
                    try:
                        process = start(options, candidate)
                        write_state(path, candidate, active)
                    except (OSError, RuntimeError) as error:
                        print(f"Rolling back {candidate}: {error}", flush=True)
                        stop(process)
                        rejected = candidate
                        process = start(options, active)
                    else:
                        active = candidate
            time.sleep(1)
        raise RuntimeError("OpenChatCut process stopped")
    finally:
        stop(process)
        executor.shutdown(wait=False, cancel_futures=True)


def terminate(_signum, _frame):
    raise SystemExit(0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--fallback", type=Path, required=True)
    parser.add_argument("--port", type=int, default=5199)
    parser.add_argument("--ref", choices=["main", "codex/core-187-openchatcut-update"],
                        default="main")
    parser.add_argument("--interval", type=int, default=180)
    options = parser.parse_args()
    signal.signal(signal.SIGTERM, terminate)
    run(options)


if __name__ == "__main__":
    main()
