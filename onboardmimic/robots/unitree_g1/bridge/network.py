"""Resolve the direct robot NIC and write a private DDS XML copy; never change routes."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
from urllib.parse import urlparse,unquote

def choose(addresses,routes):
    candidates={}
    for dev in addresses:
        if 'UP' in dev.get('flags',[]):
            ips=[a['local'] for a in dev.get('addr_info',[]) if a.get('family')=='inet' and ipaddress.ip_address(a['local']) in ipaddress.ip_network('192.168.123.0/24')]
            if ips:candidates[dev['ifname']]=ips
    if len(routes)!=1 or routes[0].get('gateway') or routes[0].get('dev') not in candidates:
        raise ValueError('No direct robot NIC; refusing to guess eth0')
    route=routes[0]
    if route.get('prefsrc') not in candidates[route['dev']]:raise ValueError('Robot route source mismatch')
    return route['dev']

def detect():
    def ip(*args):
        return json.loads(subprocess.check_output(['ip','-j',*args],text=True,timeout=3))
    return choose(ip('address','show'),ip('route','get','192.168.123.161'))

def configure(template,output,interface):
    uri=urlparse(str(template))
    if uri.scheme not in ('','file') or uri.netloc:raise ValueError('Expected local DDS XML file')
    tree=ET.parse(unquote(uri.path));ns={'c':'https://cdds.io/config'}
    nodes=tree.findall('.//c:General/c:Interfaces/c:NetworkInterface',ns)
    legacy=tree.findall('.//c:General/c:NetworkInterfaceAddress',ns)
    if len(nodes)+len(legacy)!=1:raise ValueError('DDS template must select exactly one NIC')
    if nodes:nodes[0].set('name',interface)
    else:legacy[0].text=interface
    ET.register_namespace('',ns['c']);output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    tmp=output.with_name(output.name+'.'+str(os.getpid())+'.tmp')
    try:
        with tmp.open('xb') as f:
            os.fchmod(f.fileno(),0o600);tree.write(f,encoding='utf-8',xml_declaration=True)
        tmp.replace(output)
    finally:
        if tmp.exists():tmp.unlink()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--template',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    interface=detect()
    configure(a.template,a.output,interface);print('Robot DDS NIC:',interface)
