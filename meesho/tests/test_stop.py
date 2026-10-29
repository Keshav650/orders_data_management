import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app import make_server
import stop_run


class StopTests(unittest.TestCase):
    def check_stop(self, team):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'data').mkdir()
            access = {'username': 'team', 'password': 'stop-test-password-123'}
            (root / 'data' / 'team-access.json').write_text(json.dumps(access))
            server = make_server(root / 'ledger.sqlite3', 0, **(access if team else {}))
            def serve():
                try:
                    server.serve_forever()
                finally:
                    server.server_close()
            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            try:
                with patch.object(stop_run, 'ROOT', root):
                    stop_run.stop_server(server.server_port, team)
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
                self.assertTrue((root / 'ledger.sqlite3').exists())
                stop_run.stop_server(server.server_port, team)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_personal_shutdown(self):
        self.check_stop(False)

    def test_team_shutdown(self):
        self.check_stop(True)
