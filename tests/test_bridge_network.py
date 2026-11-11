"""Offline NIC selection and XML preservation: no network configuration is changed."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('bridge_network',Path(__file__).resolve().parents[1]/'onboardmimic/robots/unitree_g1/bridge/network.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class NetworkTests(unittest.TestCase):
    def test_direct_route_selects_actual_nic_and_rejects_wifi_gateway(self):
        addresses=[dict(ifname='eth1',flags=['UP'],addr_info=[dict(family='inet',local='192.168.123.164')])]
        route=dict(dev='eth1',prefsrc='192.168.123.164')
        self.assertEqual(m.choose(addresses,[route]),'eth1')
        for routes in ([],[dict(route,gateway='192.168.123.1')],[dict(route,dev='wlan0')],[dict(route,prefsrc='10.0.0.2')]):
            with self.subTest(routes=routes),self.assertRaises(ValueError):m.choose(addresses,routes)

    def test_private_xml_copy_preserves_template_and_other_settings(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);template=p/'template.xml';output=p/'runtime/copy.xml'
            for selection in ('<Interfaces><NetworkInterface name="eth0"/></Interfaces>','<NetworkInterfaceAddress>eth0</NetworkInterfaceAddress>'):
                original='<CycloneDDS xmlns="https://cdds.io/config"><Domain><General>'+selection+'<AllowMulticast>false</AllowMulticast></General></Domain></CycloneDDS>'
                template.write_text(original);m.configure(template.as_uri(),output,'eth1')
                self.assertEqual(template.read_text(),original)
                self.assertIn('eth1',output.read_text());self.assertIn('false',output.read_text())
                self.assertEqual(output.stat().st_mode & 0o777,0o600)
            template.write_text('<CycloneDDS/>')
            with self.assertRaises(ValueError):m.configure(template,output,'eth1')

    def test_shipped_template_selects_one_nic(self):
        template=Path(__file__).resolve().parents[1]/'onboardmimic/robots/unitree_g1/systemd/cyclonedds.xml.example'
        original=template.read_bytes()
        with tempfile.TemporaryDirectory() as d:
            output=Path(d)/'dds.xml'
            m.configure(template,output,'robot-test-nic')
            self.assertIn('<NetworkInterfaceAddress>robot-test-nic</NetworkInterfaceAddress>',output.read_text())
            self.assertIn('<AllowMulticast>spdp</AllowMulticast>',output.read_text())
        self.assertEqual(template.read_bytes(),original)

if __name__=='__main__':unittest.main()
