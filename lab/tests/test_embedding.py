import json
import threading
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from onboardmimic_lab.console import make_server, embed_origin


class EmbedTest(unittest.TestCase):
    def server(self, origins=()):
        client=Mock();client.command.return_value={'state':'idle'}
        server=make_server(client,embed_origins=origins)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def close():server.shutdown();server.server_close();thread.join()
        self.addCleanup(close)
        return client,'http://127.0.0.1:'+str(server.server_port)

    def test_default_deny_and_explicit_origins_only_on_embed(self):
        _,url=self.server()
        with urlopen(url+'/embed') as r:self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])
        _,url=self.server(['http://127.0.0.1:8769','https://console.example.test','http://127.0.0.1:8769'])
        with urlopen(url+'/embed') as r:
            policy=r.headers['Content-Security-Policy']
            self.assertIn('frame-ancestors http://127.0.0.1:8769 https://console.example.test;',policy)
            self.assertEqual(policy.count('http://127.0.0.1:8769'),1)
            self.assertIn(b'<body class="embedded" ',r.read())
            self.assertIsNone(r.headers.get('Access-Control-Allow-Origin'))
        with urlopen(url+'/') as r:self.assertIn("frame-ancestors 'none'",r.headers['Content-Security-Policy'])

    def test_reject_csp_injection_paths_and_wildcards(self):
        for value in ('*',"'self'",'http://*.example.com','http://host/path','http://host/',
                      'http://host?x=1','http://host#frame','http://user@host','http://host:0',
                      'http://host:65536','http://host; frame-ancestors *','http://host\r\nX-Test:yes',
                      'http://bad..host','http://-host','https:',None):
            with self.subTest(value=value),self.assertRaises(ValueError):embed_origin(value)
        self.assertEqual(embed_origin('http://[::1]:8769'),'http://[::1]:8769')

    def test_parent_embedding_permission_does_not_grant_api_write_access(self):
        parent='http://127.0.0.1:8769'
        client,url=self.server([parent])
        with urlopen(url+'/api/session') as r:token=json.load(r)['token']
        for origin,secret in ((parent,token),(url,'wrong')):
            with self.subTest(origin=origin),self.assertRaises(HTTPError) as err:
                urlopen(Request(url+'/api/collection',data=b'{"action":"stop"}',headers={'Origin':origin,'X-Control-Token':secret}))
            self.assertEqual(err.exception.code,403)
        client.command.assert_not_called()
        with urlopen(Request(url+'/api/collection',data=b'{"action":"stop"}',headers={'Origin':url,'X-Control-Token':token})) as r:
            self.assertEqual(r.status,200)
        client.command.assert_called_once_with({'action':'stop'})
