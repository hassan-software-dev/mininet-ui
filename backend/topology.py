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
# Host default route, passed to `ip route add default ...`: "via <ip>",
# "dev <interface>" or just an interface name such as "h1-eth0".
ROUTE_RE = re.compile(r'^(via \d{1,3}(\.\d{1,3}){3}( dev [\w-]+)?|dev [\w-]+|[\w-]+)$')
CORES_RE = re.compile(r'^\d+([,-]\d+)*$')      # CPU cores, e.g. "0", "0,2", "0-1"
COLLECTOR_RE = re.compile(r'^\d{1,3}(\.\d{1,3}){3}:\d{1,5}$')   # sFlow/NetFlow "ip:port"
INTF_RE = re.compile(r'^[\w.-]{1,15}$')                          # e.g. "lo", "enp0s3"
TRUE = (True, 'true', '1', 1)                   # checkbox values that mean "on"

# Choices for the select boxes (first one is Mininet's default).
OF_VERSIONS = ('OpenFlow13', 'OpenFlow10', 'OpenFlow11', 'OpenFlow12',
               'OpenFlow14', 'OpenFlow15')
FAIL_MODES = ('secure', 'standalone')
DATAPATHS = ('kernel', 'user')
SCHEDULERS = ('cfs', 'rt')
CONTROLLER_PROTOCOLS = ('tcp', 'ssl')
# Node names become variable names in the exported script, so they must not
# clash with Python keywords or names the script itself uses.
RESERVED_NAMES = set(keyword.kwlist) | {
    'net', 'info', 'build', 'start_network', 'CLI', 'Mininet', 'TCLink', 'OVSSwitch',
    'RemoteController', 'CPULimitedHost', 'setLogLevel'}

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


def _choice(value, choices, field, label, errors):
    """An optional select-box value; blank means the default (first choice)."""
    value = str(value or '').strip() or choices[0]
    if value not in choices:
        errors.append('%s: %s must be one of %s' % (label, field, ', '.join(choices)))
        return choices[0]
    return value


def _int(value, default, field, errors, lo, hi):
    """An optional whole number with a default (network settings)."""
    num = _number(value, field, 'Network', errors, lo, hi)
    return default if num is None else int(num)


def _settings(topo, errors):
    """Validate the network-wide settings (like MiniEdit's Preferences).

    Returns (Mininet() keyword arguments, default OpenFlow versions for
    switches, sFlow settings or None, NetFlow settings or None).
    """
    s = topo.get('settings') or {}
    net = {}
    base = str(s.get('ipBase') or '').strip()
    if base:
        try:
            network = ipaddress.ip_network(base, strict=False)
            if network.version != 4:
                raise ValueError
            net['ipBase'] = str(network)
        except ValueError:
            errors.append('Network: IP base must look like 10.0.0.0/8')
    if s.get('autoStaticArp') in TRUE:
        net['autoStaticArp'] = True
    port = _number(s.get('listenPort'), 'listen port', 'Network', errors, 1, 65535)
    if port is not None:
        net['listenPort'] = int(port)

    versions = s.get('openFlowVersions') or ['OpenFlow13']
    if isinstance(versions, str):
        versions = versions.split(',')
    if any(v not in OF_VERSIONS for v in versions):
        errors.append('Network: unknown OpenFlow version in %s' % ', '.join(versions))
    protocols = ','.join(sorted(v for v in versions if v in OF_VERSIONS)) or 'OpenFlow13'

    sflow = netflow = None
    target = str(s.get('sflowTarget') or '').strip()
    if target:
        if not COLLECTOR_RE.match(target):
            errors.append('Network: sFlow collector must look like 127.0.0.1:6343')
        agent = str(s.get('sflowAgent') or '').strip() or 'lo'
        if not INTF_RE.match(agent):
            errors.append('Network: sFlow agent must be an interface name such as lo')
        sflow = {'target': target, 'agent': agent,
                 'sampling': _int(s.get('sflowSampling'), 400, 'sFlow sampling', errors, 1, 1000000),
                 'header': _int(s.get('sflowHeader'), 128, 'sFlow header', errors, 64, 256),
                 'polling': _int(s.get('sflowPolling'), 30, 'sFlow polling', errors, 0, 3600)}
    target = str(s.get('netflowTarget') or '').strip()
    if target:
        if not COLLECTOR_RE.match(target):
            errors.append('Network: NetFlow collector must look like 127.0.0.1:2055')
        netflow = {'target': target,
                   'timeout': _int(s.get('netflowTimeout'), 600, 'NetFlow timeout', errors, 0, 86400),
                   'addId': s.get('netflowAddId') in TRUE}
    return net, protocols, sflow, netflow


def monitoring_commands(spec):
    """ovs-vsctl commands (argument lists) that turn on sFlow / NetFlow on
    every switch, as MiniEdit does. Used by both Run and Export."""
    names = [s['name'] for s in spec['switches']]
    commands = []
    if spec['sflow'] and names:
        f = spec['sflow']
        cmd = ['ovs-vsctl', '--', '--id=@sflow', 'create', 'sFlow',
               'agent=%s' % f['agent'], 'targets="%s"' % f['target'],
               'sampling=%d' % f['sampling'], 'header=%d' % f['header'],
               'polling=%d' % f['polling']]
        for name in names:
            cmd += ['--', 'set', 'Bridge', name, 'sflow=@sflow']
        commands.append(cmd)
    if spec['netflow'] and names:
        f = spec['netflow']
        cmd = ['ovs-vsctl', '--', '--id=@netflow', 'create', 'NetFlow',
               'targets="%s"' % f['target'], 'active_timeout=%d' % f['timeout']]
        if f['addId']:
            cmd.append('add_id_to_interface=true')
        for name in names:
            cmd += ['--', 'set', 'Bridge', name, 'netflow=@netflow']
        commands.append(cmd)
    return commands


# ---- keyword arguments for the Mininet API, shared by Run and Export ----

def host_params(host):
    """Keyword arguments for net.addHost() (besides the name)."""
    return {k: host[k] for k in ('ip', 'mac', 'defaultRoute', 'sched', 'cpu', 'cores')
            if k in host}


def host_limited(host):
    """Does this host need CPULimitedHost (a CPU limit or pinned cores)?"""
    return 'cpu' in host or 'cores' in host


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
    spec['net'], default_protocols, spec['sflow'], spec['netflow'] = _settings(topo, errors)
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
            route = str(node.get('defaultRoute') or '').strip()
            if route:
                if ROUTE_RE.match(route):
                    host['defaultRoute'] = route
                else:
                    errors.append('%s: default route must look like "via 10.0.0.254" '
                                  'or "%s-eth0"' % (name, name))
            # CPU limit / cores: Mininet's CPULimitedHost (uses cgroups).
            cpu = _number(node.get('cpu'), 'CPU limit', name, errors, 0.01, 1)
            cores = str(node.get('cores') or '').strip()
            if cores and not CORES_RE.match(cores):
                errors.append('%s: cores must look like 0, 0,2 or 0-1' % name)
                cores = ''
            if cpu is not None or cores:
                host['sched'] = _choice(node.get('sched'), SCHEDULERS, 'scheduler',
                                        name, errors)
                if cpu is not None:
                    host['cpu'] = cpu
                if cores:
                    host['cores'] = cores
            for key in ('startCommand', 'stopCommand'):
                command = str(node.get(key) or '').strip()
                if command:
                    host[key] = command
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
            # Keyword arguments for net.addSwitch(); only non-defaults besides
            # protocols, which is always given (Open vSwitch's default is 1.0).
            # Blank = the network's default OpenFlow version(s).
            opts = {'protocols': default_protocols if _blank(node.get('protocols')) else
                    _choice(node.get('protocols'), OF_VERSIONS, 'OpenFlow version', name, errors)}
            fail_mode = _choice(node.get('failMode'), FAIL_MODES, 'fail mode', name, errors)
            if fail_mode != 'secure':
                opts['failMode'] = fail_mode
            datapath = _choice(node.get('datapath'), DATAPATHS, 'datapath', name, errors)
            if datapath != 'kernel':
                opts['datapath'] = datapath
            if node.get('stp') in TRUE:
                opts['stp'] = True
            listen = _number(node.get('listenPort'), 'listen port', name, errors, 1, 65535)
            if listen is not None:
                opts['listenPort'] = int(listen)
            # 'controllers' is filled in from the control links below.
            spec['switches'].append({'name': name, 'dpid': dpid, 'controllers': [],
                                     'opts': opts})

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
            protocol = _choice(node.get('protocol'), CONTROLLER_PROTOCOLS, 'protocol',
                               name, errors)
            spec['controllers'].append({'name': name, 'ip': ip, 'port': port,
                                        'protocol': protocol})

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
            # A control link: the switch connects to this controller.
            types = sorted((a['type'], b['type']))
            if types != ['controller', 'switch']:
                errors.append('%s: a controller can only be linked to a switch' % label)
                continue
            sw, ctl = (a, b) if a['type'] == 'switch' else (b, a)
            sw_spec = next(s for s in spec['switches'] if s['name'] == sw['name'])
            if ctl['name'] not in sw_spec['controllers']:
                sw_spec['controllers'].append(ctl['name'])
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
        jitter = str(link.get('jitter') or '').strip()
        if jitter:
            if jitter.replace('.', '', 1).isdigit():
                jitter += 'ms'
            if DELAY_RE.match(jitter):
                opts['jitter'] = jitter
            else:
                errors.append('%s: jitter must look like 1ms, 500us or 1s' % label)
        queue = _number(link.get('max_queue_size'), 'queue size', label, errors, 1, 1000000)
        if queue is not None:
            opts['max_queue_size'] = int(queue)
        spec['links'].append({'src': a['name'], 'dst': b['name'], 'opts': opts})
        linked.update((a['name'], b['name']))

    for host in spec['hosts']:
        if host['name'] not in linked:
            warnings.append('%s is not connected to anything' % host['name'])
    if spec['switches'] and not spec['controllers']:
        warnings.append('No controller: switches will have no flow rules')
    else:
        for sw in spec['switches']:
            if not sw['controllers']:
                warnings.append('%s is not linked to a controller, so it will not '
                                'forward traffic (draw a link from it to the '
                                'controller)' % sw['name'])
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
    limited = any(host_limited(h) for h in spec['hosts'])
    monitoring = monitoring_commands(spec)
    net_args = ''.join(', %s=%r' % kv for kv in spec['net'].items())
    out += ['import subprocess'] if monitoring else []
    out += [
        'from mininet.net import Mininet',
        'from mininet.node import RemoteController, OVSSwitch%s'
        % (', CPULimitedHost' if limited else ''),
        'from mininet.link import TCLink',
        'from mininet.cli import CLI',
        'from mininet.log import setLogLevel, info',
        '',
        '',
        'def start_network():',
        '    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,',
        '                  autoSetMacs=False, build=False%s)' % net_args,
        '',
        "    info('*** Adding controllers\\n')",
    ]
    for c in spec['controllers']:
        args = [('ip', c['ip']), ('port', c['port'])]
        if c['protocol'] != 'tcp':
            args.append(('protocol', c['protocol']))
        out.append('    %s = net.addController(%r, controller=RemoteController, %s)'
                   % (c['name'], c['name'], _kwargs(args)))
    out += ['', "    info('*** Adding switches\\n')"]
    for s in spec['switches']:
        out.append('    %s = net.addSwitch(%r, %s)' % (
            s['name'], s['name'], _kwargs([('dpid', s['dpid'])] + list(s['opts'].items()))))
    out += ['', "    info('*** Adding hosts\\n')"]
    for h in spec['hosts']:
        args = [_kwargs(host_params(h).items())]
        if host_limited(h):
            args.insert(0, 'cls=CPULimitedHost')      # a class, so not quoted
        args = ', '.join(a for a in args if a)
        out.append('    %s = net.addHost(%r%s)' % (h['name'], h['name'],
                                                 ', ' + args if args else ''))
    out += ['', "    info('*** Adding links\\n')"]
    for l in spec['links']:
        args = ', ' + _kwargs(sorted(l['opts'].items())) if l['opts'] else ''
        out.append('    net.addLink(%s, %s%s)' % (l['src'], l['dst'], args))
    # Not net.start(): that would connect every switch to every controller.
    out += ['', '    net.build()', "    info('*** Starting controllers\\n')"]
    out += ['    %s.start()' % c['name'] for c in spec['controllers']]
    out += ["    info('*** Starting switches (each with the controllers it is linked to)\\n')"]
    out += ['    %s.start([%s])' % (s['name'], ', '.join(s['controllers']))
            for s in spec['switches']]
    if monitoring:
        out.append("    info('*** Turning on sFlow / NetFlow\\n')")
        out += ['    subprocess.run(%r)' % cmd for cmd in monitoring]
    starts = [h for h in spec['hosts'] if 'startCommand' in h]
    if starts:
        out.append("    info('*** Running start commands\\n')")
        out += ['    %s.cmd(%r)' % (h['name'], h['startCommand']) for h in starts]
    out += [
        '    return net',
        '',
        '',
        "if __name__ == '__main__':",
        "    setLogLevel('info')",
        '    net = start_network()',
        '    CLI(net)',
    ]
    out += ["    net.get(%r).cmd(%r)" % (h['name'], h['stopCommand'])
            for h in spec['hosts'] if 'stopCommand' in h]
    out += [
        '    net.stop()',
        '',
    ]
    return '\n'.join(out)
