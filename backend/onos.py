"""Tiny ONOS REST client used for the live view.

The browser can't call ONOS directly (different port => CORS, and we don't
want the password in the page), so the backend fetches and forwards.
Configure with env vars ONOS_URL, ONOS_USER, ONOS_PASS.
"""
import base64
import json
import os
import urllib.request

ONOS_URL = os.environ.get('ONOS_URL', 'http://localhost:8181/onos/v1')
ONOS_USER = os.environ.get('ONOS_USER', 'onos')
ONOS_PASS = os.environ.get('ONOS_PASS', 'rocks')
TIMEOUT = 3


def _get(path):
    req = urllib.request.Request(ONOS_URL + path)
    token = base64.b64encode(('%s:%s' % (ONOS_USER, ONOS_PASS)).encode()).decode()
    req.add_header('Authorization', 'Basic ' + token)
    req.add_header('Accept', 'application/json')
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def snapshot():
    """Return devices, links, hosts and flow counts in a compact form."""
    devices = [{'id': d['id'], 'available': d.get('available', False)}
               for d in _get('/devices').get('devices', [])]
    links = [{'src': l['src']['device'], 'srcPort': l['src']['port'],
              'dst': l['dst']['device'], 'dstPort': l['dst']['port'],
              'state': l.get('state', '')}
             for l in _get('/links').get('links', [])]
    hosts = []
    for h in _get('/hosts').get('hosts', []):
        # ONOS 2.x uses "locations" (a list); 1.x used "location".
        locs = h.get('locations') or ([h['location']] if 'location' in h else [])
        hosts.append({'mac': h.get('mac', '').lower(),
                      'ips': h.get('ipAddresses', []),
                      'locations': [{'device': l.get('elementId'),
                                     'port': l.get('port')} for l in locs]})
    flows = {}
    for f in _get('/flows').get('flows', []):
        flows[f['deviceId']] = flows.get(f['deviceId'], 0) + 1
    return {'devices': devices, 'links': links, 'hosts': hosts, 'flows': flows}
