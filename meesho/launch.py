"""Double-click launcher: start a hidden server and wait for readiness."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import build_opener, ProxyHandler
import webbrowser

ROOT = Path(__file__).resolve().parent
URL = 'http://127.0.0.1:8765'
HTTP = build_opener(ProxyHandler({}))


def ready():
    try:
        with HTTP.open(URL + '/api/imports', timeout=1) as response:
            if not isinstance(json.load(response), list):
                return False
        with HTTP.open(URL + '/', timeout=1) as response:
            return b'Meesho Ledger' in response.read()
    except (OSError, ValueError):
        return False


def start(open_browser=True):
    if not ready():
        try:
            import openpyxl  # Check dependencies before hiding the server console.
        except ImportError as exc:
            raise RuntimeError(
                f'Missing Python dependency: {exc}\nInstall dependencies with:\n'
                f'"{sys.executable}" -m pip install -r "{ROOT / "requirements.txt"}"'
            ) from exc
        folder = ROOT / 'data'
        folder.mkdir(exist_ok=True)
        error_log = folder / 'server-error.log'
        with (folder / 'server.log').open('ab') as stdout, error_log.open('ab') as stderr:
            stderr.write(f'\n--- Startup {time.strftime("%Y-%m-%d %H:%M:%S")} ---\n'.encode())
            stderr.flush()
            offset = stderr.tell()
            process = subprocess.Popen(
                [sys.executable, str(ROOT / 'app.py')], cwd=ROOT,
                stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                start_new_session=os.name != 'nt',
            )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if ready():
                break
            if process.poll() is not None:
                with error_log.open('rb') as log:
                    log.seek(offset)
                    detail = log.read().decode('utf-8', errors='replace')[-5000:]
                raise RuntimeError(f'The server exited during startup.\n{detail}\nLog: {error_log}')
            time.sleep(0.3)
        else:
            raise RuntimeError(f'The app did not become ready within 30 seconds. Check {error_log}')
    print(f'Meesho Ledger is ready: {URL}', flush=True)
    if open_browser:
        try:
            if not webbrowser.open(URL):
                print(f'Open this address in your browser: {URL}', flush=True)
        except webbrowser.Error:
            print(f'Open this address in your browser: {URL}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true', help='Verify startup without opening a browser')
    parser.add_argument('--team', action='store_true', help='Start password-protected LAN mode on port 8766')
    parser.add_argument('--local', action='store_true', help='Optional personal-only mode on port 8765')
    parser.add_argument('--stop-team', action='store_true')
    args = parser.parse_args()
    try:
        if args.stop_team:
            from team_launch import stop
            stop()
        elif not args.local:
            from team_launch import start as team_start
            team_start(open_browser=not args.no_browser)
        else:
            start(open_browser=not args.no_browser)
    except Exception as exc:
        print(f'\nCould not start Meesho Ledger:\n{exc}', file=sys.stderr, flush=True)
        sys.exit(1)
