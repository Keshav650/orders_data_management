"""Run: python app.py. Local-only HTTP server for Amazon Ledger."""
import argparse
import csv
import io
import json
import os
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
from recon.engine import (
    report, products_list, save_products, save_settings,
    save_recovery, order_detail, create_product, set_product_disabled
)
from recon.importer import parse_file, preview, commit_file, day, scan_local_folder

ROOT = Path(__file__).resolve().parent


def make_server(db_path, port=8775, host='127.0.0.1'):
    with connect(db_path):
        pass
    pending, pending_lock = {}, threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(60)

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
                parsed = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                query['skus'] = parse_qs(parsed.query).get('skus', [])
                path = parsed.path

                with connect(db_path) as db:
                    if path == '/api/report':
                        self.send(report(db, **{k: v for k, v in query.items() if k in ('start', 'end', 'as_of', 'mode', 'sku', 'skus')}))
                    elif path == '/api/products':
                        self.send(products_list(db, day(query.get('date', date.today().isoformat()))))
                    elif path == '/api/product-history':
                        self.send([dict(r) for r in db.execute('SELECT * FROM product_versions WHERE sku=? ORDER BY effective_date DESC, id DESC', (query.get('sku', ''),))])
                    elif path == '/api/imports':
                        self.send([dict(r) for r in db.execute(
                            'SELECT i.id, i.name, i.kind, COALESCE(r.applied_at, i.imported_at) imported_at, COALESCE(r.summary, i.summary) summary '
                            'FROM imports i LEFT JOIN import_runs r ON r.id=(SELECT MAX(id) FROM import_runs WHERE import_id=i.id) '
                            'ORDER BY imported_at DESC, i.id DESC'
                        )])
                    elif path == '/api/order':
                        self.send(order_detail(db, query.get('id', '')))
                    elif path == '/api/scan-downloads':
                        input_dir = ROOT / 'input'
                        input_dir.mkdir(parents=True, exist_ok=True)
                        found_input = scan_local_folder(input_dir)[:30]
                        items = []
                        for f in found_input:
                            items.append({
                                'path': str(f),
                                'name': f.name,
                                'size': f.stat().st_size,
                                'modified': date.fromtimestamp(f.stat().st_mtime).isoformat()
                            })
                        self.send(items)
                    elif path == '/api/backup':
                        with tempfile.TemporaryDirectory() as temp:
                            dest = Path(temp) / 'backup.sqlite3'
                            with closing(sqlite3.connect(dest)) as target:
                                db.backup(target)
                            data = dest.read_bytes()
                        self.send(data, content_type='application/octet-stream', filename=f'amazon-backup-{date.today()}.sqlite3')
                    elif path == '/api/export':
                        result = report(db, **{k: v for k, v in query.items() if k in ('start', 'end', 'as_of', 'mode', 'sku', 'skus')})
                        out = io.StringIO()
                        fields = [
                            'amazon_order_id', 'order_item_id', 'sku', 'quantity', 'purchase_date', 'status',
                            'payment_state', 'settlement', 'product_cost', 'packaging', 'contribution',
                            'product_sales', 'easy_ship_fees', 'selling_fees', 'refunds', 'safet', 'taxes',
                            'tcs_tds', 'ship_city', 'ship_state', 'issues'
                        ]
                        money_fields = {'settlement', 'product_cost', 'packaging', 'contribution', 'product_sales', 'easy_ship_fees', 'selling_fees', 'refunds', 'safet', 'taxes', 'tcs_tds'}
                        writer = csv.DictWriter(out, fieldnames=fields)
                        writer.writeheader()
                        for row in result['orders']:
                            if query.get('awaiting') == 'true' and (row['payment_count'] or row['status'] == 'CANCELLED'):
                                continue
                            clean = {}
                            for k in fields:
                                v = row.get(k)
                                if k in money_fields:
                                    clean[k] = '' if v is None else f'{v / 100:.2f}'
                                elif isinstance(v, list):
                                    clean[k] = '; '.join(v)
                                elif isinstance(v, str) and v.startswith(('=', '+', '-', '@', '\t', '\r')):
                                    clean[k] = "'" + v
                                else:
                                    clean[k] = v
                            writer.writerow(clean)
                        self.send(('\ufeff' + out.getvalue()).encode('utf-8'), content_type='text/csv; charset=utf-8', filename=f'amazon-orders-{date.today()}.csv')
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
                self.send({'error': 'The request could not be completed.'}, 500)

        def do_POST(self):
            try:
                if self.headers.get_content_type() != 'application/json':
                    raise ValueError('Expected JSON')
                length = int(self.headers.get('Content-Length', 0))
                if not 0 < length <= 80 * 1024 * 1024:
                    raise ValueError('Request too large or empty')
                body = json.loads(self.rfile.read(length))
                path = urlparse(self.path).path

                if path == '/api/stop':
                    if self.client_address[0] != '127.0.0.1':
                        self.send({'error': 'Stop permitted only from local machine'}, 403)
                        return
                    self.send({'stopped': True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return

                with connect(db_path) as db:
                    if path == '/api/import/preview':
                        import base64
                        payload = base64.b64decode(body['payload'], validate=True)
                        parsed = parse_file(Path(body['name']).name, payload, body.get('kind'), body.get('effective_date', '0001-01-01'))
                        import secrets
                        token = secrets.token_urlsafe(24)
                        with pending_lock:
                            for old in list(pending):
                                if time.monotonic() - pending[old][0] > 1800:
                                    del pending[old]
                            pending[token] = (time.monotonic(), parsed)
                        self.send(dict(preview(db, parsed), token=token))

                    elif path == '/api/import/commit':
                        with pending_lock:
                            batches = [pending[t][1] for t in body['tokens'] if t in pending]
                            if len(batches) != len(body['tokens']):
                                raise ValueError('Preview expired; please preview files again')
                            results = [commit_file(db, batch) for batch in batches]
                            db.commit()
                            for token in body['tokens']:
                                del pending[token]
                        self.send({'imported': results})

                    elif path == '/api/import/scan-load':
                        file_path = Path(body['path'])
                        if not file_path.exists():
                            raise ValueError('File does not exist')
                        payload = file_path.read_bytes()
                        parsed = parse_file(file_path.name, payload, body.get('kind'), body.get('effective_date', '0001-01-01'))
                        result = commit_file(db, parsed)
                        db.commit()
                        self.send({'imported': result})

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
                self.send({'error': 'Request failed.'}, 500)

    return ThreadingHTTPServer((host, port), Handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8775)
    parser.add_argument('--db', default=str(ROOT / 'data' / 'amazon.sqlite3'))
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    server = make_server(args.db, args.port, host=args.host)
    print(f'Amazon Ledger running at http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
