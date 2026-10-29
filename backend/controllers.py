"""Read what the SDN controller sees, for the live view.

Supports ONOS and OpenDaylight. Both are converted to the same small format,
keyed by switch DPID, so the frontend doesn't care which controller is used:

{
  "devices": [{"dpid": "0000000000000001", "id": "of:0000000000000001",
               "available": true}],
  "links":   [{"src": dpid, "srcPort": "2", "dst": dpid, "dstPort": "1"}],
  "hosts":   [{"mac": "00:00:00:00:00:01", "ips": ["10.0.0.1"],
               "locations": [{"dpid": dpid, "port": "1"}]}],
  "flows":   {dpid: number of flow rules}
}

The browser can't call the controllers directly (different port => CORS, and
we don't want passwords in the page), so the backend fetches and forwards.
The parse_* functions take already-decoded JSON so they can be unit-tested.
"""
import base64
import json
import os
import urllib.error
import urllib.request

TIMEOUT = 3

CONTROLLERS = {
    'onos': {
        'label': 'ONOS',
        'url': os.environ.get('ONOS_URL', 'http://localhost:8181/onos/v1'),
        'user': os.environ.get('ONOS_USER', 'onos'),
        'password': os.environ.get('ONOS_PASS', 'rocks'),
    },
    'odl': {
        'label': 'OpenDaylight',
        'url': os.environ.get('ODL_URL', 'http://localhost:8181'),
        'user': os.environ.get('ODL_USER', 'admin'),
        'password': os.environ.get('ODL_PASS', 'admin'),
    },
}


def _get(ctl, path):
    req = urllib.request.Request(ctl['url'] + path)
    token = base64.b64encode(('%s:%s' % (ctl['user'], ctl['password'])).encode())
    req.add_header('Authorization', 'Basic ' + token.decode())
    req.add_header('Accept', 'application/json')
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode())


def snapshot(kind):
    """Fetch and convert the view of controller `kind` ('onos' or 'odl')."""
    ctl = CONTROLLERS[kind]
    return onos_snapshot(ctl) if kind == 'onos' else odl_snapshot(ctl)


# ---- ONOS -------------------------------------------------------------------

def _onos_dpid(device_id):
    """'of:0000000000000001' -> '0000000000000001'; None if not OpenFlow."""
    return device_id[3:].lower() if device_id.startswith('of:') else None


def parse_onos(devices, links, hosts, flows):
    """Convert the JSON of ONOS /devices, /links, /hosts and /flows."""
    out = {'devices': [], 'links': [], 'hosts': [], 'flows': {}}
    for d in devices.get('devices', []):
        dpid = _onos_dpid(d['id'])
        if dpid:
            out['devices'].append({'dpid': dpid, 'id': d['id'],
                                   'available': d.get('available', False)})
    for l in links.get('links', []):
        out['links'].append({'src': _onos_dpid(l['src']['device']),
                             'srcPort': l['src']['port'],
                             'dst': _onos_dpid(l['dst']['device']),
                             'dstPort': l['dst']['port']})
    for h in hosts.get('hosts', []):
        # ONOS 2.x uses "locations" (a list); 1.x used "location".
        locs = h.get('locations') or ([h['location']] if 'location' in h else [])
        out['hosts'].append({
            'mac': h.get('mac', '').lower(),
            'ips': h.get('ipAddresses', []),
            'locations': [{'dpid': _onos_dpid(l.get('elementId', '')),
                           'port': l.get('port')} for l in locs]})
    for f in flows.get('flows', []):
        dpid = _onos_dpid(f['deviceId'])
        out['flows'][dpid] = out['flows'].get(dpid, 0) + 1
    return out


def onos_snapshot(ctl):
    return parse_onos(_get(ctl, '/devices'), _get(ctl, '/links'),
                      _get(ctl, '/hosts'), _get(ctl, '/flows'))


# ---- OpenDaylight --------------------------------------------------------------

# Newer ODL (Fluorine and later) serves RFC 8040 paths under /rests; older
# releases (e.g. Oxygen, with l2switch) use /restconf. We try both.
ODL_TOPOLOGY = ['/rests/data/network-topology:network-topology?content=nonconfig',
                '/restconf/operational/network-topology:network-topology']
ODL_INVENTORY = ['/rests/data/opendaylight-inventory:nodes?content=nonconfig',
                 '/restconf/operational/opendaylight-inventory:nodes']


def _get_first(ctl, paths):
    """GET the first path that exists (404 means: try the next one)."""
    for i, path in enumerate(paths):
        try:
            return _get(ctl, path)
        except urllib.error.HTTPError as e:
            if e.code != 404 or i == len(paths) - 1:
                raise


def _odl_dpid(node_id):
    """'openflow:10' or 'openflow:10:3' -> '000000000000000a'."""
    return '%016x' % int(node_id.split(':')[1])


def _odl_port(tp_id):
    """'openflow:10:3' -> '3'."""
    parts = tp_id.split(':')
    return parts[2] if len(parts) > 2 else None


def _unwrap(data, module, container):
    """RFC 8040 prefixes the top-level key with the module name, e.g.
    'opendaylight-inventory:nodes'; old RESTCONF just uses 'nodes'."""
    return data.get(module + ':' + container) or data.get(container) or {}


def parse_odl(topology, inventory):
    """Convert ODL's operational network-topology and inventory JSON."""
    out = {'devices': [], 'links': [], 'hosts': [], 'flows': {}}
    topologies = _unwrap(topology, 'network-topology', 'network-topology')
    for topo in topologies.get('topology', []):
        for node in topo.get('node', []):
            node_id = node.get('node-id', '')
            if node_id.startswith('openflow:'):
                # Only connected switches appear in the operational topology.
                out['devices'].append({'dpid': _odl_dpid(node_id), 'id': node_id,
                                       'available': True})
            elif node_id.startswith('host:'):
                # Host nodes need ODL's host tracker (l2switch).
                addrs = node.get('host-tracker-service:addresses', [])
                points = node.get('host-tracker-service:attachment-points', [])
                mac = addrs[0]['mac'] if addrs and 'mac' in addrs[0] else node_id[5:]
                out['hosts'].append({
                    'mac': mac.lower(),
                    'ips': [a['ip'] for a in addrs if 'ip' in a],
                    'locations': [{'dpid': _odl_dpid(p['tp-id']),
                                   'port': _odl_port(p['tp-id'])}
                                  for p in points
                                  if p.get('tp-id', '').startswith('openflow:')]})
        for link in topo.get('link', []):
            src, dst = link.get('source', {}), link.get('destination', {})
            # ODL also lists host<->switch links here; keep switch<->switch only.
            if (src.get('source-node', '').startswith('openflow:') and
                    dst.get('dest-node', '').startswith('openflow:')):
                out['links'].append({'src': _odl_dpid(src['source-node']),
                                     'srcPort': _odl_port(src.get('source-tp', '')),
                                     'dst': _odl_dpid(dst['dest-node']),
                                     'dstPort': _odl_port(dst.get('dest-tp', ''))})
    for node in _unwrap(inventory, 'opendaylight-inventory', 'nodes').get('node', []):
        if node.get('id', '').startswith('openflow:'):
            tables = node.get('flow-node-inventory:table', [])
            out['flows'][_odl_dpid(node['id'])] = sum(
                len(t.get('flow', [])) for t in tables)
    return out


def odl_snapshot(ctl):
    topology = _get_first(ctl, ODL_TOPOLOGY)
    try:
        inventory = _get_first(ctl, ODL_INVENTORY)
    except urllib.error.HTTPError:
        inventory = {}      # no switches yet, or inventory model not installed
    return parse_odl(topology, inventory)
