import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from app import make_server
import team_launch
from team_auth import read_credentials


class TeamTests(unittest.TestCase):
    def test_short_custom_password_validation_and_live_reload(self):
        with tempfile.TemporaryDirectory() as temp:
            config=Path(temp)/'access.json'
            config.write_text(json.dumps(dict(username='owner', password='123456789')), encoding='utf-8-sig')
            server=make_server(Path(temp)/'db.sqlite3',0,password='123456789',username='owner',credential_path=config)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            http=build_opener(ProxyHandler({}))
            def access(username,password):
                token=base64.b64encode(f'{username}:{password}'.encode()).decode()
                with http.open(Request(f'http://127.0.0.1:{server.server_port}/api/team/status',headers={'Authorization':'Basic '+token})) as r:
                    return r.status
            try:
                self.assertEqual(access('owner','123456789'),200)
                config.write_text(json.dumps(dict(username='newowner',password='changed-password')))
                self.assertEqual(access('newowner','changed-password'),200)
                with self.assertRaises(HTTPError) as err:access('owner','123456789')
                self.assertEqual(err.exception.code,401)
                config.write_text(json.dumps(dict(username='newowner',password='short')))
                with self.assertRaisesRegex(ValueError,'at least 8'):read_credentials(config)
                with self.assertRaises(HTTPError) as err:access('newowner','changed-password')
                self.assertEqual(err.exception.code,503)
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_credentials_persist_across_launches(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(team_launch, 'CONFIG', Path(temp)/'team-access.json'):
                first = team_launch.credentials()
                self.assertEqual(first['username'], 'team')
                self.assertEqual(team_launch.credentials(), first)
                self.assertEqual(team_launch.credentials(create=False), first)

    def test_network_bind_requires_password(self):
        with self.assertRaises(ValueError):
            make_server('unused.sqlite3', 0, host='0.0.0.0')

    def test_team_auth_shared_database_and_origin(self):
        with tempfile.TemporaryDirectory() as temp:
            server = make_server(Path(temp)/'team.sqlite3', 0, host='0.0.0.0', password='a-long-test-password-1234')
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f'http://127.0.0.1:{server.server_port}'
            auth = 'Basic ' + base64.b64encode(b'team:a-long-test-password-1234').decode()
            http = build_opener(ProxyHandler({}))
            def call(path, body=None, authorized=True, extra=None):
                headers = {'Authorization': auth} if authorized else {}
                if body is not None: headers['Content-Type'] = 'application/json'
                headers.update(extra or {})
                request = Request(url+path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
                with http.open(request) as response:
                    return response.read()
            try:
                for path in ('/', '/app.js', '/api/report', '/api/backup', '/api/export'):
                    with self.assertRaises(HTTPError) as err:
                        call(path, authorized=False)
                    self.assertEqual(err.exception.code, 401)
                with self.assertRaises(HTTPError) as err:
                    call('/api/products/create', {'sku':'unauthorized'}, authorized=False)
                self.assertEqual(err.exception.code, 401)
                self.assertIn(b'Meesho Ledger', call('/'))
                self.assertTrue(json.loads(call('/api/team/status'))['team_mode'])
                with self.assertRaises(HTTPError) as err:
                    call('/api/settings', {'gst_rate':'18','include_gst':True}, extra={'Origin':'http://evil.example'})
                self.assertEqual(err.exception.code,403)
                with self.assertRaises(HTTPError) as err:
                    call('/api/report', extra={'Host':'evil.example'})
                self.assertEqual(err.exception.code,403)
                def create(i):
                    return call('/api/products/create',dict(sku=f'Team SKU {i}',cost='10',packaging='2',effective_date='2026-07-01'))
                with ThreadPoolExecutor(max_workers=4) as pool:
                    list(pool.map(create, range(4)))
                self.assertEqual(len(json.loads(call('/api/products'))),4)
                # Files selected on any authenticated client's computer travel
                # through the same upload API into the shared host database.
                payload = b'Sub Order No,Order Date,SKU,Quantity,Reason for Credit Entry\nremote_1,2026-07-01,Team SKU 0,1,DELIVERED\n'
                preview=json.loads(call('/api/import/preview',dict(name='remote-orders.csv',kind='orders',payload=base64.b64encode(payload).decode())))
                call('/api/import/commit',{'tokens':[preview['token']]})
                call('/api/products',{'effective_date':'2026-07-01','updates':[{'sku':'Team SKU 0','cost':'25','packaging':'4'}]})
                result=json.loads(call('/api/report?mode=custom&start=2026-07-01&end=2026-07-31&as_of=2026-08-31'))
                self.assertEqual(result['orders'][0]['sub_order_id'],'remote_1')
                self.assertEqual(result['orders'][0]['product_cost'],2500)
                self.assertEqual(result['orders'][0]['packaging'],400)
                self.assertTrue(call('/api/backup').startswith(b'SQLite format 3'))
            finally:
                server.shutdown();server.server_close();thread.join()
