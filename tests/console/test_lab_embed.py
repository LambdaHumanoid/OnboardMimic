import json
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock
from urllib.request import urlopen
from onboardmimic.console.lab_embed import describe
from onboardmimic.console.server import Console, make_server

class LabEmbedTest(unittest.TestCase):
    def test_disabled_and_exact_origin(self):
        self.assertFalse(describe()['enabled'])
        for url, origin in [('http://127.0.0.1:8773/embed', 'http://127.0.0.1:8773'),
                            ('https://lab.example.com/embed', 'https://lab.example.com'),
                            ('http://[::1]:8773/embed', 'http://[::1]:8773')]:
            self.assertEqual(describe(url)['origin'], origin)
            self.assertEqual(describe(url)['height_px'], 540)

    def test_credentials_query_and_csp_injection_rejected(self):
        for url in ['javascript:alert(1)', '//host/embed', 'http://u:p@host/embed',
                    'http://host/embed?token=secret', 'http://host/embed#secret',
                    "http://host;frame-src*/embed", 'http://*/embed',
                    'http://host/other', 'http://host:0/embed', 'http://host:bad/embed',
                    'http://host\n/embed', 'http://%68ost/embed', 'http://host\\evil/embed']:
            with self.subTest(url=url), self.assertRaises(ValueError): describe(url)

    def test_http_config_has_no_gateway_or_lab_side_effects(self):
        backend=Mock(); runner=Mock()
        console=Console(backend, Path(__file__).resolve().parents[2]/'onboardmimic/console/static',
                        runner=runner,lab_embed_url='http://127.0.0.1:8773/embed')
        server=make_server(console);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            with urlopen(base+'/api/console-config') as response:
                value=json.load(response)
                self.assertEqual(response.headers['Content-Security-Policy'],"frame-src http://127.0.0.1:8773; object-src 'none'")
            self.assertEqual(value,dict(schema_version=1,lab_embed=describe('http://127.0.0.1:8773/embed')))
            self.assertNotIn(console.token,json.dumps(value))
            with urlopen(base+'/') as response:
                self.assertIn(b'id="lab-embed-region" hidden',response.read())
            with urlopen(base+'/lab-embed.js') as response:
                script=response.read()
                self.assertIn(b'collection-panel',script)
                self.assertNotIn(b'X-Control-Token',script)
            backend.assert_not_called(); backend.status.assert_not_called()
            backend.command.assert_not_called();backend.control.assert_not_called();runner.assert_not_called()
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__': unittest.main()
