"""Real local HTTP servers; hot assets must preserve sessions and replay objects."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch
from urllib.request import urlopen
from onboardmimic_lab.console import make_server
from onboardmimic_lab.dataset_io import write_json


class VersionedAssetsTests(unittest.TestCase):
    def test_hot_panels_and_css_with_pinned_old_version_no_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);registry=root/'registry.json'
            for version in ('a','b'):
                for relative in ('lab/src/onboardmimic_lab/static','onboardmimic/console/static'):
                    d=root/version/relative;d.mkdir(parents=True)
                    for name in ('collection-panel.html','replay-panel.html'): (d/name).write_text('<p>'+version+' panel</p>')
                    (d/'lab.css').write_text('/* '+version+' */')
                    (d/'style.css').write_text('/* '+version+' */')
                    (d/'index.html').write_text('<link href="/style.css"><p>'+version+'</p>')
            def switch(v):write_json(registry,dict(schema=1,active=v*64,releases={x*64:str(root/x) for x in ('a','b')}))
            switch('a')
            with patch.dict(os.environ,{'OM_ASSET_REGISTRY':str(registry)}):
                replay=Mock();server=make_server(Mock(),replay=replay)
                threads=[]
                for s in (server,):
                    t=threading.Thread(target=s.serve_forever,daemon=True);t.start();threads.append(t)
                def get(s,path):return urlopen('http://127.0.0.1:'+str(s.server_port)+path).read().decode()
                try:
                    token=get(server,'/api/session')
                    self.assertIn('a panel',get(server,'/embed'))
                    switch('b')
                    self.assertIn('b panel',get(server,'/embed'));self.assertIn('asset_version='+'b'*64,get(server,'/embed'))
                    self.assertEqual(get(server,'/lab.css?asset_version='+'a'*64),'/* a */')
                    self.assertEqual(token,get(server,'/api/session'))
                    replay.close.assert_not_called();self.assertTrue(all(t.is_alive() for t in threads))
                finally:
                    for s in (server,):s.shutdown();s.server_close()
                    for t in threads:t.join()
