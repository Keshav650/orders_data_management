"""Start/stop only Flipkart Ledger. Uses the running Python interpreter."""
import argparse
import json
import os
import subprocess
import sys
import time
import webbrowser
from http.client import RemoteDisconnected
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request,build_opener,ProxyHandler

ROOT=Path(__file__).resolve().parent
URL='http://127.0.0.1:8770'
HTTP=build_opener(ProxyHandler({}))

def running():
    try:
        with HTTP.open(URL+'/api/meta',timeout=2) as response:
            if json.load(response).get('app')!='flipkart-ledger':
                raise RuntimeError('Port 8770 belongs to another application')
        return True
    except URLError as exc:
        if isinstance(exc.reason,(ConnectionRefusedError,TimeoutError)):
            return False
        raise
    except (ConnectionResetError,RemoteDisconnected,TimeoutError):
        return False

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--stop',action='store_true')
    parser.add_argument('--no-browser',action='store_true')
    args=parser.parse_args()
    active=running()
    if args.stop:
        if active:
            with HTTP.open(Request(URL+'/api/stop',data=b'{}',headers={'Content-Type':'application/json'}),timeout=5):
                pass
            for _ in range(30):
                time.sleep(.2)
                if not running():
                    break
        print('Flipkart stopped. Saved data is preserved.' if active else 'Flipkart is already stopped.')
        return
    if not active:
        (ROOT/'data').mkdir(exist_ok=True)
        kwargs=dict(cwd=ROOT,stdin=subprocess.DEVNULL)
        if os.name=='nt':
            kwargs['creationflags']=subprocess.CREATE_NO_WINDOW
        else:
            kwargs['start_new_session']=True
        with (ROOT/'data'/'server.log').open('ab') as log:
            child=subprocess.Popen([sys.executable,str(ROOT/'app.py')],stdout=log,stderr=log,**kwargs)
        for _ in range(50):
            if running():
                break
            if child.poll() is not None:
                raise RuntimeError('Startup failed. See data/server.log')
            time.sleep(.2)
        else:
            raise RuntimeError('Startup timed out. See data/server.log')
    print('Flipkart Ledger: '+URL)
    if not args.no_browser:
        webbrowser.open(URL)

if __name__=='__main__':
    main()
