"""Run: python app.py. Local-only HTTP server; no remote service required."""
import argparse
import base64
import csv
import io
import json
import secrets
import socket
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from recon.database import connect
from recon.engine import report, products, save_products, save_settings, save_recovery, order_detail, create_product, set_product_disabled
from recon.importer import parse_file, preview, commit_file, day
from team_auth import read_credentials, validate as validate_credentials

ROOT = Path(__file__).resolve().parent


def make_server(db_path, port=8765, host='127.0.0.1', username='team', password=None, credential_path=None):
    if host not in ('127.0.0.1', 'localhost') and not password:
        raise ValueError('Network mode requires a team password')
    if password:
        validate_credentials(dict(username=username, password=password))
    with connect(db_path) as db:
        pass
    pending, pending_lock = {}, threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(60)

        def allowed(self):
            requested_host = self.headers.get('Host', '').split(':')[0].lower()
            allowed_hosts = {'127.0.0.1', 'localhost'}
            if host not in ('127.0.0.1', 'localhost'):
                allowed_hosts.update({self.connection.getsockname()[0], socket.gethostname().lower(), socket.getfqdn().lower()})
            if requested_host not in allowed_hosts:
                self.send({'error': 'Unrecognized server address'}, 403)
                return False
            if password:
                try:
                    active = read_credentials(credential_path) if credential_path else dict(username=username, password=password)
                except (ValueError, OSError):
                    self.send({'error': 'Team login configuration is invalid. Correct team-access.json on the host.'}, 503)
                    return False
                expected = 'Basic ' + base64.b64encode(f"{active['username']}:{active['password']}".encode()).decode()
                if not secrets.compare_digest(self.headers.get('Authorization', '').encode(), expected.encode()):
                    self.send_response(401)
                    self.send_header('WWW-Authenticate', 'Basic realm="Meesho Ledger Team", charset="UTF-8"')
                    self.send_header('Content-Length', '0')
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    return False
            return True

        def send(self, value, code=200, content_type='application/json; charset=utf-8', filename=None):
            data = json.dumps(value, default=str).encode() if isinstance(value, (dict, list)) else value
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            if filename:
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            try:
                if not self.allowed():
                    return
                parsed = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                query['skus'] = parse_qs(parsed.query).get('skus', [])
                path = parsed.path
                with connect(db_path) as db:
                    if path == '/api/report':
                        self.send(report(db, **{k: v for k, v in query.items() if k in ('start', 'end', 'as_of', 'mode', 'sku', 'skus')}))
                    elif path == '/api/team/status':
                        self.send({'team_mode': bool(password)})
                    elif path == '/api/products':
                        self.send(products(db, day(query.get('date', date.today().isoformat()))))
                    elif path == '/api/product-history':
                        self.send([dict(r) for r in db.execute('SELECT * FROM product_versions WHERE sku=? ORDER BY effective_date DESC,id DESC', (query.get('sku', ''),))])
                    elif path == '/api/imports':
                        self.send([dict(r) for r in db.execute('SELECT i.id,i.name,i.kind,COALESCE(r.applied_at,i.imported_at) imported_at,COALESCE(r.summary,i.summary) summary FROM imports i LEFT JOIN import_runs r ON r.id=(SELECT MAX(id) FROM import_runs WHERE import_id=i.id) ORDER BY imported_at DESC,i.id DESC')])
                    elif path == '/api/order':
                        self.send(order_detail(db, query.get('id', '')))
                    elif path == '/api/backup':
                        with tempfile.TemporaryDirectory() as temp:
                            dest = Path(temp) / 'backup.sqlite3'
                            with closing(sqlite3.connect(dest)) as target:
                                db.backup(target)
                            data = dest.read_bytes()
                        self.send(data, content_type='application/octet-stream', filename=f'meesho-backup-{date.today()}.sqlite3')
                    elif path == '/api/export':
                        result = report(db, **{k: v for k, v in query.items() if k in ('start', 'end', 'as_of', 'mode', 'sku', 'skus')})
                        out = io.StringIO()
                        fields = ['sub_order_id', 'sku', 'quantity', 'order_date', 'status', 'payment_state', 'payment_date', 'due_date',
                                  'payment_count', 'settlement', 'listed_value', 'product_cost', 'packaging', 'contribution', 'shipping', 'reverse_shipping', 'tds', 'tcs', 'recovery', 'issues']
                        money_fields = {'settlement', 'listed_value', 'product_cost', 'packaging', 'contribution', 'shipping', 'reverse_shipping', 'tds', 'tcs'}
                        writer = csv.DictWriter(out, fieldnames=fields)
                        writer.writeheader()
                        for row in result['orders']:
                            if query.get('awaiting') == 'true' and (row['payment_count'] or row['status'] == 'CANCELLED'):
                                continue
                            clean = {}
                            for k in fields:
                                v = row[k]
                                if k in money_fields:
                                    v = '' if v is None else f'{v / 100:.2f}'
                                elif isinstance(v, list):
                                    v = '; '.join(v)
                                elif isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')):
                                    v = "'" + v
                                clean[k] = v
                            writer.writerow(clean)
                        self.send(('\ufeff' + out.getvalue()).encode(), content_type='text/csv; charset=utf-8', filename='orders-financial-detail.csv')
                    elif path in ('/', '/app.js', '/style.css'):
                        filename = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[path]
                        mime = {'/': 'text/html', '/app.js': 'text/javascript', '/style.css': 'text/css'}[path]
                        self.send((ROOT / 'static' / filename).read_bytes(), content_type=mime + '; charset=utf-8')
                    else:
                        self.send({'error': 'Not found'}, 404)
            except (ValueError, KeyError) as exc:
                self.send({'error': str(exc)}, 400)
            except Exception:
                import traceback
                traceback.print_exc()
                self.send({'error': 'The request could not be completed. See the application log.'}, 500)

        def do_POST(self):
            try:
                host = self.headers.get('Host', '')
                origin = self.headers.get('Origin')
                if not self.allowed():
                    return
                if origin and origin not in (f'http://{host}', f'https://{host}'):
                    self.send({'error': 'Cross-origin requests are not permitted'}, 403)
                    return
                if self.headers.get_content_type() != 'application/json':
                    raise ValueError('Expected JSON')
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 75 * 1024 * 1024:
                    raise ValueError('Request exceeds 75 MB or is empty')
                body = json.loads(self.rfile.read(length))
                path = urlparse(self.path).path
                if path in ('/api/team/stop', '/api/stop'):
                    if (path == '/api/team/stop' and not password) or self.client_address[0] != '127.0.0.1':
                        self.send({'error': 'Stop team mode from the host computer'}, 403)
                        return
                    self.send({'stopped': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                with connect(db_path) as db:
                    if path == '/api/import/preview':
                        payload = base64.b64decode(body['payload'], validate=True)
                        if len(payload) > 50 * 1024 * 1024:
                            raise ValueError('Each upload must be 50 MB or smaller')
                        parsed = parse_file(Path(body['name']).name, payload, body['kind'], body.get('effective_date', '0001-01-01'))
                        token = secrets.token_urlsafe(24)
                        with pending_lock:
                            for old in list(pending):
                                if time.monotonic() - pending[old][0] > 1800:
                                    del pending[old]
                            if len(pending) >= 15:
                                raise ValueError('Too many pending previews; commit existing previews first')
                            pending[token] = (time.monotonic(), parsed)
                        self.send(dict(preview(db, parsed), token=token))
                    elif path == '/api/import/commit':
                        with pending_lock:
                            batches = [pending[t][1] for t in body['tokens'] if t in pending]
                            if len(batches) != len(body['tokens']):
                                raise ValueError('Preview expired; preview the files again')
                            results = [commit_file(db, batch) for batch in batches]
                            db.commit()
                            for token in body['tokens']:
                                del pending[token]
                        self.send({'imported': results})
                    elif path == '/api/products/create':
                        create_product(db, body)
                        db.commit()
                        self.send({'saved': True})
                    elif path == '/api/products/status':
                        set_product_disabled(db, body['sku'], body['disabled'])
                        db.commit()
                        self.send({'saved': True})
                    elif path == '/api/products':
                        save_products(db, body['updates'], body['effective_date'])
                        db.commit()
                        self.send({'saved': len(body['updates'])})
                    elif path == '/api/settings':
                        save_settings(db, body)
                        db.commit()
                        self.send({'saved': True})
                    elif path == '/api/recovery':
                        save_recovery(db, body)
                        db.commit()
                        self.send({'saved': True})
                    else:
                        self.send({'error': 'Not found'}, 404)
            except (ValueError, KeyError, TypeError) as exc:
                self.send({'error': str(exc)}, 400)
            except Exception:
                import traceback
                traceback.print_exc()
                self.send({'error': 'Import or update failed. No partial changes were saved.'}, 500)

    return ThreadingHTTPServer((host, port), Handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', default=str(ROOT / 'data' / 'meesho.sqlite3'))
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--team-config', type=Path, help='Local JSON file containing username and password')
    args = parser.parse_args()
    credentials = read_credentials(args.team_config) if args.team_config else {}
    server = make_server(args.db, args.port, host=args.host, credential_path=args.team_config, **credentials)
    print(f'Meesho Ledger running at http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

