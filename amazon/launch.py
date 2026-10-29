"""Double-click launcher: start Amazon Ledger server and open browser."""
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
PORT = 8775
URL = f'http://127.0.0.1:{PORT}'
HTTP = build_opener(ProxyHandler({}))


def ready():
    try:
        with HTTP.open(URL + '/api/imports', timeout=1) as response:
            if not isinstance(json.load(response), list):
                return False
        with HTTP.open(URL + '/', timeout=1) as response:
            return b'Amazon Ledger' in response.read()
    except (OSError, ValueError):
        return False


def start(open_browser=True):
    if not ready():
        try:
            import openpyxl
        except ImportError as exc:
            raise RuntimeError(
                f'Missing Python dependency: {exc}\n'
                f'Install with:\n"{sys.executable}" -m pip install -r "{ROOT / "requirements.txt"}"'
            ) from exc
        
        data_folder = ROOT / 'data'
        data_folder.mkdir(exist_ok=True)
        error_log = data_folder / 'server-error.log'
        
        with (data_folder / 'server.log').open('ab') as stdout, error_log.open('ab') as stderr:
            stderr.write(f'\n--- Amazon Ledger Startup {time.strftime("%Y-%m-%d %H:%M:%S")} ---\n'.encode())
            stderr.flush()
            offset = stderr.tell()
            process = subprocess.Popen(
                [sys.executable, str(ROOT / 'app.py'), '--port', str(PORT)],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                start_new_session=os.name != 'nt'
            )

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if ready():
                break
            if process.poll() is not None:
                with error_log.open('rb') as log:
                    log.seek(offset)
                    detail = log.read().decode('utf-8', errors='replace')[-5000:]
                raise RuntimeError(f'Server exited during startup.\n{detail}\nLog: {error_log}')
            time.sleep(0.3)
        else:
            raise RuntimeError(f'Amazon Ledger did not become ready within 30 seconds. Check {error_log}')

    print(f'Amazon Ledger is running at {URL}', flush=True)
    if open_browser:
        try:
            if not webbrowser.open(URL):
                print(f'Open this URL in your browser: {URL}', flush=True)
        except webbrowser.Error:
            print(f'Open this URL in your browser: {URL}', flush=True)


def stop():
    from urllib.request import Request, urlopen
    try:
        req = Request(f'{URL}/api/stop', data=json.dumps({}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        with urlopen(req, timeout=3) as resp:
            print('Amazon Ledger stopped successfully.')
    except Exception:
        print('Amazon Ledger was not running or is already stopped.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--stop', action='store_true')
    args = parser.parse_args()
    if args.stop:
        stop()
    else:
        start(open_browser=not args.no_browser)
