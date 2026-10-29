"""Topology model: validation, defaults and Mininet script export.

This module does NOT import Mininet, so it works (and can be tested) on any
machine. The frontend sends a topology as plain JSON:

{
  "nodes": [
    {"id": "n1", "type": "host", "name": "h1", "x": 100, "y": 80,
     "ip": "10.0.0.1/8", "mac": "00:00:00:00:00:01"},
    {"id": "n2", "type": "switch", "name": "s1", "dpid": "0000000000000001"},
    {"id": "n3", "type": "controller", "name": "c0",
     "ip": "127.0.0.1", "port": 6653}
  ],
  "links": [
    {"id": "l1", "src": "n1", "dst": "n2", "bw": 10, "delay": "5ms", "loss": 0}
  ]
}

`id` is an internal key used by links (so nodes can be renamed); `name` is
the Mininet node name. normalize() turns this into a "spec" that both the
script exporter (below) and the live runner (runner.py) consume.
"""
import ipaddress
import keyword
import re

NODE_TYPES = ('host', 'switch', 'controller')

# Interface names look like "<name>-eth<N>" and Linux limits them to 15
# characters, so node names are kept short.
NAME_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9]{0,7}$')
MAC_RE = re.compile(r'^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$')
DPID_RE = re.compile(r'^[0-9a-fA-F]{1,16}$')
DELAY_RE = re.compile(r'^\d+(\.\d+)?(us|ms|s)$')
# Node names become variable names in the exported script, so they must not
# clash with Python keywords or names the script itself uses.
RESERVED_NAMES = set(keyword.kwlist) | {
    'net', 'info', 'build', 'CLI', 'Mininet', 'TCLink', 'OVSSwitch',
    'RemoteController', 'setLogLevel'}

DEFAULT_PREFIX = 8          # same as Mininet's default ipBase 10.0.0.0/8
DEFAULT_CONTROLLER_PORT = 6653
MAX_BW_MBPS = 1000          # TCLink/HTB limit


class TopologyError(ValueError):
    """Raised when a topology cannot be built. `errors` lists every problem."""

    def __init__(self, errors):
        super().__init__('; '.join(errors))
        self.errors = errors


def _blank(value):
    return value is None or (isinstance(value, str) and value.strip() == '')


def _number(value, field, label, errors, lo, hi):
    """Parse an optional number; returns None if blank or invalid."""
    if _blank(value):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        errors.append('%s: %s must be a number' % (label, field))
        return None
    if not lo <= num <= hi:
        errors.append('%s: %s must be between %s and %s' % (label, field, lo, hi))
        return None
    return int(num) if num.is_integer() else num


def normalize(topo):
    """Validate a topology and return (spec, warnings).

    spec = {"controllers": [...], "switches": [...], "hosts": [...],
            "links": [{"src": name, "dst": name, "opts": {...}}]}

    Raises TopologyError listing every problem found.
    """
    errors, warnings = [], []
    if not isinstance(topo, dict):
        raise TopologyError(['Topology must be a JSON object'])
    nodes = topo.get('nodes') or []
    links = topo.get('links') or []

    spec = {'controllers': [], 'switches': [], 'hosts': [], 'links': []}
    by_id = {}              # node id -> node dict from the input
    names, ips, macs, dpids = set(), set(), set(), set()

    for node in nodes:
        ntype = node.get('type')
        name = str(node.get('name', '')).strip()
        label = name or str(node.get('id', '?'))
        if ntype not in NODE_TYPES:
            errors.append('%s: unknown node type %r' % (label, ntype))
            continue
        if not NAME_RE.match(name):
            errors.append('%s: name must start with a letter and be 1-8 '
                          'letters/digits' % label)
        elif name in RESERVED_NAMES:
            errors.append('%s: reserved name, pick another' % name)
        elif name in names:
            errors.append('%s: duplicate name' % name)
        names.add(name)
        by_id[node.get('id')] = node

        if ntype == 'host':
            ip = str(node.get('ip') or '').strip()
            mac = str(node.get('mac') or '').strip().lower()
            host = {'name': name}
            if ip:
                if '/' not in ip:
                    ip += '/%d' % DEFAULT_PREFIX
                try:
                    addr = ipaddress.ip_interface(ip)
                    if addr.version != 4:
                        raise ValueError
                    host['ip'] = str(addr)
                    if addr.ip in ips:
                        errors.append('%s: duplicate IP %s' % (name, addr.ip))
                    ips.add(addr.ip)
                except ValueError:
                    errors.append('%s: invalid IPv4 address %r' % (name, ip))
            if mac:
                if not MAC_RE.match(mac):
                    errors.append('%s: invalid MAC %r' % (name, mac))
                elif mac in macs:
                    errors.append('%s: duplicate MAC %s' % (name, mac))
                else:
                    host['mac'] = mac
                macs.add(mac)
            spec['hosts'].append(host)

        elif ntype == 'switch':
            dpid = str(node.get('dpid') or '').strip().lower()
            if dpid.startswith('0x'):
                dpid = dpid[2:]
            if not DPID_RE.match(dpid):
                errors.append('%s: DPID must be 1-16 hex digits' % name)
            else:
                dpid = dpid.zfill(16)
                if int(dpid, 16) == 0:
                    errors.append('%s: DPID must not be zero' % name)
                elif dpid in dpids:
                    errors.append('%s: duplicate DPID %s' % (name, dpid))
                dpids.add(dpid)
            spec['switches'].append({'name': name, 'dpid': dpid})

        else:  # controller
            ip = str(node.get('ip') or '127.0.0.1').strip()
            try:
                ipaddress.IPv4Address(ip)
            except ValueError:
                errors.append('%s: invalid controller IP %r' % (name, ip))
            port = node.get('port')
            port = DEFAULT_CONTROLLER_PORT if _blank(port) else port
            try:
                port = int(port)
                if not 1 <= port <= 65535:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append('%s: port must be 1-65535' % name)
            spec['controllers'].append({'name': name, 'ip': ip, 'port': port})

    linked = set()
    for link in links:
        a, b = by_id.get(link.get('src')), by_id.get(link.get('dst'))
        if a is None or b is None:
            errors.append('Link %s: endpoint does not exist' % link.get('id', '?'))
            continue
        label = 'Link %s-%s' % (a['name'], b['name'])
        if a is b:
            errors.append('%s: cannot connect a node to itself' % label)
            continue
        if 'controller' in (a['type'], b['type']):
            errors.append('%s: controllers are not linked; every switch '
                          'connects to every controller automatically' % label)
            continue
        opts = {}
        bw = _number(link.get('bw'), 'bandwidth', label, errors, 0.001, MAX_BW_MBPS)
        if bw is not None:
            opts['bw'] = bw
        delay = str(link.get('delay') or '').strip()
        if delay:
            if delay.replace('.', '', 1).isdigit():
                delay += 'ms'       # bare numbers mean milliseconds
            if DELAY_RE.match(delay):
                opts['delay'] = delay
            else:
                errors.append('%s: delay must look like 5ms, 100us or 1s' % label)
        loss = _number(link.get('loss'), 'loss', label, errors, 0, 100)
        if loss:
            opts['loss'] = loss
        spec['links'].append({'src': a['name'], 'dst': b['name'], 'opts': opts})
        linked.update((a['name'], b['name']))

    for host in spec['hosts']:
        if host['name'] not in linked:
            warnings.append('%s is not connected to anything' % host['name'])
    if spec['switches'] and not spec['controllers']:
        warnings.append('No controller: switches will have no flow rules')
    if not spec['hosts'] and not spec['switches']:
        errors.append('Topology is empty')

    if errors:
        raise TopologyError(errors)
    return spec, warnings


def _kwargs(pairs):
    """Format (key, value) pairs as Python keyword arguments."""
    return ', '.join('%s=%r' % (k, v) for k, v in pairs)


def generate_script(topo):
    """Return a standalone Mininet Python 3 script for the topology."""
    spec, _ = normalize(topo)
    out = [
        '#!/usr/bin/env python3',
        '"""Mininet topology exported by mininet-ui.',
        '',
        'Run with:  sudo python3 <this file>',
        '"""',
    ]
    # Notes from the drawing become comments.
    for note in topo.get('notes') or []:
        for i, line in enumerate(str(note.get('text', '')).strip().splitlines()):
            out.append(('# Note: ' if i == 0 else '#       ') + line)
    out += [
        'from mininet.net import Mininet',
        'from mininet.node import RemoteController, OVSSwitch',
        'from mininet.link import TCLink',
        'from mininet.cli import CLI',
        'from mininet.log import setLogLevel, info',
        '',
        '',
        'def build():',
        '    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,',
        '                  autoSetMacs=False, build=False)',
        '',
        "    info('*** Adding controllers\\n')",
    ]
    for c in spec['controllers']:
        out.append('    %s = net.addController(%r, controller=RemoteController, %s)'
                   % (c['name'], c['name'], _kwargs([('ip', c['ip']), ('port', c['port'])])))
    out += ['', "    info('*** Adding switches\\n')"]
    for s in spec['switches']:
        out.append("    %s = net.addSwitch(%r, dpid=%r, protocols='OpenFlow13')"
                   % (s['name'], s['name'], s['dpid']))
    out += ['', "    info('*** Adding hosts\\n')"]
    for h in spec['hosts']:
        extra = [(k, h[k]) for k in ('ip', 'mac') if k in h]
        args = ', ' + _kwargs(extra) if extra else ''
        out.append('    %s = net.addHost(%r%s)' % (h['name'], h['name'], args))
    out += ['', "    info('*** Adding links\\n')"]
    for l in spec['links']:
        args = ', ' + _kwargs(sorted(l['opts'].items())) if l['opts'] else ''
        out.append('    net.addLink(%s, %s%s)' % (l['src'], l['dst'], args))
    out += [
        '',
        '    return net',
        '',
        '',
        "if __name__ == '__main__':",
        "    setLogLevel('info')",
        '    net = build()',
        '    net.start()',
        '    CLI(net)',
        '    net.stop()',
        '',
    ]
    return '\n'.join(out)
