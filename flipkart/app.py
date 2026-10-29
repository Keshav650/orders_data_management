"""Local Flipkart reconciliation server; no dependency on the Meesho project."""
import argparse
import base64
import csv
import io
import json
import secrets
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from core import connect, parse_file, preview, commit_file, report, products, save_product, save_recovery, save_manual_return, save_payment_review, save_status_correction, order_detail, today, rupees

ROOT=Path(__file__).resolve().parent
DB_PATH=ROOT/'data'/'flipkart.sqlite3'
PORT=8770
MAX_UPLOAD=25*1024*1024


def make_server(db_path=DB_PATH,port=PORT):
    with closing(connect(db_path)):
        pass
    pending={}
    lock=threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def send(self,value,status=200,kind='application/json; charset=utf-8',filename=None):
            data=json.dumps(value,ensure_ascii=False).encode() if isinstance(value,(dict,list)) else value
            self.send_response(status)
            self.send_header('Content-Type',kind)
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
            if filename:
                self.send_header('Content-Disposition',f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(data)

        def allowed(self,post=False):
            valid={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            if self.headers.get('Host','').lower() not in valid:
                self.send({'error':'Unrecognized server address'},403)
                return False
            if post:
                origin=self.headers.get('Origin')
                if origin and origin not in {f'http://{h}' for h in valid}:
                    self.send({'error':'Cross-site changes are not allowed'},403)
                    return False
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                    self.send({'error':'Expected JSON request'},415)
                    return False
            return True

        def do_GET(self):
            try:
                if not self.allowed():
                    return
                parsed=urlparse(self.path)
                query={k:v[0] for k,v in parse_qs(parsed.query).items()}
                path=parsed.path
                if path in ('/','/app.js','/style.css'):
                    name='index.html' if path=='/' else path[1:]
                    mime={'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','style.css':'text/css; charset=utf-8'}[name]
                    self.send((ROOT/'static'/name).read_bytes(),kind=mime)
                    return
                if path=='/api/meta':
                    self.send(dict(app='flipkart-ledger',today=today(),scale=1_000_000))
                    return
                if path=='/api/input':
                    self.send([dict(name=p.name,size=p.stat().st_size) for p in sorted((ROOT/'input').iterdir()) if p.is_file() and p.suffix.lower() in ('.csv','.xlsx')])
                    return
                with closing(connect(db_path)) as db:
                    if path=='/api/report':
                        self.send(report(db,**{k:v for k,v in query.items() if k in ('start','end','as_of','sku')}))
                    elif path=='/api/products':
                        self.send(products(db,query.get('date')))
                    elif path=='/api/order':
                        self.send(order_detail(db,query.get('id',''),query.get('as_of')))
                    elif path=='/api/imports':
                        values=[dict(r) for r in db.execute('SELECT id,name,kind,imported_at,summary FROM file_imports ORDER BY id DESC')]
                        for r in values:
                            r['summary']=json.loads(r['summary'])
                        self.send(values)
                    elif path=='/api/backup':
                        with tempfile.TemporaryDirectory(dir=ROOT/'data') as folder:
                            dest=Path(folder)/'backup.sqlite3'
                            with closing(sqlite3.connect(dest)) as backup:
                                db.backup(backup)
                            self.send(dest.read_bytes(),kind='application/octet-stream',filename='flipkart-backup.sqlite3')
                    elif path=='/api/export':
                        result=report(db,**{k:v for k,v in query.items() if k in ('start','end','as_of','sku')})
                        out=io.StringIO(newline='')
                        fields=['item_id','order_id','sku','ordered_on','quantity','status','forward_tracking_id','reverse_tracking_id','state','net','profit','provisional','purchase_total','packaging','policy','return_type','return_category','recovery','issues']
                        writer=csv.DictWriter(out,fields)
                        writer.writeheader()
                        for row in result['orders']:
                            values={k:row[k] for k in fields}
                            for k in ('net','profit','provisional','purchase_total','packaging'):
                                values[k]='' if values[k] is None else f'{rupees(values[k]):.6f}'.rstrip('0').rstrip('.')
                            values['issues']='; '.join(values['issues'])
                            # Neutralize spreadsheet formula injection in imported text.
                            values={k:("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) and k not in ('net','profit','provisional','purchase_total','packaging') else v) for k,v in values.items()}
                            writer.writerow(values)
                        self.send(out.getvalue().encode('utf-8-sig'),kind='text/csv; charset=utf-8',filename='flipkart-orders.csv')
                    else:
                        self.send({'error':'Not found'},404)
            except (ValueError,KeyError,OSError) as exc:
                self.send({'error':str(exc)},400)

        def do_POST(self):
            try:
                if not self.allowed(True):
                    return
                size=int(self.headers.get('Content-Length','0'))
                if size<=0 or size>MAX_UPLOAD*2:
                    raise ValueError('Request is empty or exceeds 25 MB file limit')
                value=json.loads(self.rfile.read(size))
                path=urlparse(self.path).path
                if path=='/api/import/preview':
                    if 'input_name' in value:
                        name=value['input_name']
                        target=(ROOT/'input'/name).resolve()
                        if target.parent!=(ROOT/'input').resolve() or not target.is_file():
                            raise ValueError('Input file must be inside the Flipkart input folder')
                        if target.stat().st_size>MAX_UPLOAD:
                            raise ValueError('File exceeds 25 MB')
                        payload=target.read_bytes()
                    else:
                        name=Path(value['name']).name
                        payload=base64.b64decode(value['data'],validate=True)
                    if len(payload)>MAX_UPLOAD:
                        raise ValueError('File exceeds 25 MB')
                    result=parse_file(name,payload)
                    token=secrets.token_urlsafe(24)
                    with lock:
                        expired=[k for k,v in pending.items() if time.monotonic()-v[2]>1800]
                        for k in expired:
                            pending.pop(k)
                        if len(pending)>=20:
                            pending.pop(next(iter(pending)))
                        pending[token]=(result,payload,time.monotonic())
                    self.send(preview(result)|dict(token=token))
                    return
                with closing(connect(db_path)) as db:
                    if path=='/api/import/commit':
                        with lock:
                            data=pending.get(value['token'])
                            if not data or time.monotonic()-data[2]>1800:
                                raise ValueError('Preview expired. Preview the file again.')
                            result=commit_file(db,data[0],data[1])
                            pending.pop(value['token'],None)
                        self.send(result)
                    elif path=='/api/product':
                        save_product(db,value)
                        self.send(dict(saved=True))
                    elif path=='/api/order-status':
                        save_status_correction(db,value)
                        self.send(dict(saved=True))
                    elif path=='/api/payment-review':
                        save_payment_review(db,value)
                        self.send(dict(saved=True))
                    elif path=='/api/recovery':
                        save_recovery(db,value)
                        self.send(dict(saved=True))
                    elif path=='/api/return':
                        save_manual_return(db,value)
                        self.send(dict(saved=True))
                    elif path=='/api/stop':
                        self.send(dict(stopped=True))
                        threading.Thread(target=self.server.shutdown,daemon=True).start()
                    else:
                        self.send({'error':'Not found'},404)
            except (ValueError,KeyError,OSError,TypeError,sqlite3.Error) as exc:
                self.send({'error':str(exc)},400)

    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    server.daemon_threads=True
    return server

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=PORT)
    parser.add_argument('--db',type=Path,default=DB_PATH)
    args=parser.parse_args()
    server=make_server(args.db,args.port)
    print(f'Flipkart Ledger: http://127.0.0.1:{server.server_port}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
