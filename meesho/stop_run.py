"""Stop Meesho Ledger on this computer without importing app dependencies."""
import base64
import json
from pathlib import Path
from http.client import RemoteDisconnected
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parent
HTTP = build_opener(ProxyHandler({}))


def stop_server(port, team=False):
    url = f'http://127.0.0.1:{port}'
    # Confirm this is our app before sending a shutdown request.
    try:
        HTTP.open(url + '/', timeout=3).close()
    except HTTPError as exc:
        exc.close()
        if exc.code != 401:
            raise RuntimeError(f'Unexpected service on port {port}; stop it manually.') from exc
    except URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            print(f'No server running on port {port}.')
            return
        raise
    headers = {'Content-Type': 'application/json'}
    if team:
        access = json.loads((ROOT / 'data' / 'team-access.json').read_text())
        token = base64.b64encode(f"{access['username']}:{access['password']}".encode()).decode()
        headers['Authorization'] = 'Basic ' + token
    with HTTP.open(Request(url + '/api/team/status', headers=headers), timeout=3) as response:
        status = json.load(response)
    if status.get('team_mode') is not team:
        raise RuntimeError(f'Unexpected server mode on port {port}.')
    endpoint = '/api/team/stop' if team else '/api/stop'
    with HTTP.open(Request(url + endpoint, data=b'{}', headers=headers), timeout=3) as response:
        if json.load(response).get('stopped') is not True:
            raise RuntimeError(f'Server on port {port} did not confirm shutdown.')
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            HTTP.open(url + '/', timeout=3).close()
        except HTTPError as exc:
            exc.close()
        except URLError as exc:
            if isinstance(exc.reason, ConnectionRefusedError):
                print(f'Stopped {"team" if team else "personal"} server on port {port}. Saved data is preserved.')
                return
            if not isinstance(exc.reason, (ConnectionResetError, TimeoutError)):
                raise
        except (ConnectionResetError, RemoteDisconnected, TimeoutError):
            pass
        time.sleep(.2)
    raise RuntimeError(f'Shutdown requested, but port {port} is still responding.')


if __name__ == '__main__':
    failed = False
    for port, team in ((8766, True), (8765, False)):
        try:
            stop_server(port, team)
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            print(f'Could not stop port {port}: {exc}', file=sys.stderr)
            failed = True
    sys.exit(1 if failed else 0)
