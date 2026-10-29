"""Password-protected team launcher for a trusted private LAN."""
import base64
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler
import webbrowser
from team_auth import read_credentials

ROOT = Path(__file__).resolve().parent
PORT = 8766
URL = f'http://127.0.0.1:{PORT}'
CONFIG = ROOT / 'data' / 'team-access.json'
HTTP = build_opener(ProxyHandler({}))


def credentials(create=True):
    if not CONFIG.exists():
        if not create:
            raise RuntimeError('Team mode has not been configured yet.')
        CONFIG.parent.mkdir(exist_ok=True)
        # Exclusive creation avoids two launchers replacing each other's secret.
        try:
            with CONFIG.open('x') as f:
                json.dump(dict(username='team', password=secrets.token_urlsafe(24)), f, indent=2)
        except FileExistsError:
            pass
    return read_credentials(CONFIG)


def request(path, access, body=None):
    token = base64.b64encode(f"{access['username']}:{access['password']}".encode()).decode()
    headers = {'Authorization': 'Basic ' + token}
    if body is not None:
        headers['Content-Type'] = 'application/json'
    return HTTP.open(Request(URL + path, data=body, headers=headers), timeout=3)


def ready(access):
    try:
        with request('/api/team/status', access) as response:
            return json.load(response).get('team_mode') is True
    except HTTPError as exc:
        if exc.code == 401:
            raise RuntimeError('Port 8766 is already using different credentials. Stop that server before starting again.') from exc
        return False
    except (OSError, ValueError):
        return False


def start(open_browser=True):
    access = credentials()
    if not ready(access):
        import openpyxl
        with (ROOT / 'data' / 'team-server.log').open('ab') as log:
            process = subprocess.Popen(
                [sys.executable, str(ROOT / 'app.py'), '--host', '0.0.0.0', '--port', str(PORT), '--team-config', str(CONFIG)],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                start_new_session=os.name != 'nt')
        deadline = time.monotonic() + 30
        while not ready(access):
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError('Team server did not start. See data/team-server.log; port 8766 may already be in use.')
            time.sleep(.3)
    addresses = sorted(set(socket.gethostbyname_ex(socket.gethostname())[2]) - {'127.0.0.1'})
    print('\nMeesho Ledger Team is ready. Keep this computer powered on.')
    for address in addresses:
        print(f'Team address: http://{address}:{PORT}')
    print(f"Username: {access['username']}\nPassword: {access['password']}")
    print(f'Credentials are saved in {CONFIG}')
    print('Use only on your trusted private network. See TEAM_SETUP.md for the firewall step.')
    if open_browser:
        webbrowser.open(URL)


def stop():
    access = credentials(create=False)
    with request('/api/team/stop', access, b'{}') as response:
        json.load(response)
    print('Team server stopped. The personal app and database are unchanged.')
