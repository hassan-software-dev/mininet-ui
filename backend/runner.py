"""Runs one Mininet network in-process and executes commands on its hosts.

Mininet is imported lazily (inside start()) so the web server can still be
started without Mininet installed, e.g. to edit topologies and export scripts.

Mininet node objects are not thread-safe (each host is one bash shell), and
Flask serves requests on several threads, so every call that touches the
network takes self.lock.
"""
import shlex
import threading
import time

from topology import normalize

CMD_TIMEOUT = 30            # seconds before a user command is killed


class RunnerError(Exception):
    pass


class NetworkRunner:

    def __init__(self):
        self.net = None
        self.spec = None
        self._connected = {}        # last switch_connections() answer
        self.lock = threading.Lock()

    # ---- lifecycle -------------------------------------------------------

    def status(self):
        if self.net is None:
            return {'running': False}
        return {
            'running': True,
            'hosts': [h['name'] for h in self.spec['hosts']],
            'switches': [s['name'] for s in self.spec['switches']],
            'connected': self.switch_connections(),
        }

    def switch_connections(self):
        """{switch name: True if connected to a controller}, asked from Open
        vSwitch. If a long command (e.g. iperf) holds the lock, the last
        answer is returned instead of waiting."""
        if self.lock.acquire(timeout=0.2):
            try:
                if self.net is not None:
                    self._connected = {s.name: bool(s.connected())
                                       for s in self.net.switches}
            finally:
                self.lock.release()
        return self._connected

    def start(self, topo):
        """Build and start the network. Returns a list of warnings."""
        spec, warnings = normalize(topo)    # raises TopologyError
        from mininet.net import Mininet
        from mininet.node import RemoteController, OVSSwitch
        from mininet.link import TCLink
        from mininet.clean import cleanup

        with self.lock:
            if self.net is not None:
                raise RunnerError('Network is already running')
            cleanup()           # same as `sudo mn -c`: remove leftovers
            net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                          autoSetMacs=False, build=False)
            try:
                for c in spec['controllers']:
                    net.addController(c['name'], controller=RemoteController,
                                      ip=c['ip'], port=c['port'])
                for s in spec['switches']:
                    net.addSwitch(s['name'], dpid=s['dpid'], protocols='OpenFlow13')
                for h in spec['hosts']:
                    params = {k: h[k] for k in ('ip', 'mac') if k in h}
                    net.addHost(h['name'], **params)
                for l in spec['links']:
                    net.addLink(l['src'], l['dst'], **l['opts'])
                net.start()
            except Exception:
                try:
                    net.stop()
                finally:
                    cleanup()
                raise
            self.net, self.spec = net, spec

            if spec['controllers'] and spec['switches']:
                if not net.waitConnected(timeout=10):
                    warnings.append('Some switches did not connect to the '
                                    'controller within 10 s (is it running?)')
        return warnings

    def stop(self):
        with self.lock:
            if self.net is None:
                raise RunnerError('Network is not running')
            try:
                self.net.stop()
            finally:
                self.net, self.spec, self._connected = None, None, {}

    # ---- commands --------------------------------------------------------

    def _host(self, name):
        if self.net is None:
            raise RunnerError('Network is not running')
        if name not in [h['name'] for h in self.spec['hosts']]:
            raise RunnerError('No host named %r' % name)
        return self.net.get(name)

    def run_cmd(self, name, cmd):
        """Run a shell command on a host and return its output."""
        if not cmd.strip():
            raise RunnerError('Command is empty')
        with self.lock:
            host = self._host(name)
            # `timeout` stops commands like a bare `ping` from hanging forever.
            return host.cmd('timeout %d sh -c %s' % (CMD_TIMEOUT, shlex.quote(cmd)))

    def ping(self, src, dst, count=4):
        with self.lock:
            a, b = self._host(src), self._host(dst)
            return a.cmd('ping -c %d -W 1 %s' % (count, b.IP()))

    def pingall(self):
        """Ping every host from every other host, like Mininet's `pingall`."""
        with self.lock:
            if self.net is None:
                raise RunnerError('Network is not running')
            hosts = self.net.hosts
            lines, sent, lost = [], 0, 0
            for a in hosts:
                reached = []
                for b in hosts:
                    if a is b:
                        continue
                    out = a.cmd('ping -c 1 -W 1 %s' % b.IP())
                    ok = ' 0% packet loss' in out
                    sent += 1
                    lost += 0 if ok else 1
                    reached.append(b.name if ok else 'X')
                lines.append('%s -> %s' % (a.name, ' '.join(reached)))
            pct = 100 * lost // sent if sent else 0
            lines.append('*** Results: %d%% dropped (%d/%d received)'
                         % (pct, sent - lost, sent))
            return '\n'.join(lines)

    def iperf(self, src, dst, seconds=5):
        """TCP iperf from src (client) to dst (server).

        Not net.iperf(): that waits forever for the server if the hosts
        can't reach each other, which would hold the lock for good.
        """
        with self.lock:
            a, b = self._host(src), self._host(dst)
            b.cmd('iperf -s -p 5001 > /dev/null 2>&1 &')
            time.sleep(0.5)     # let the server start listening
            try:
                return a.cmd('timeout %d iperf -c %s -p 5001 -t %d'
                             % (seconds + 10, b.IP(), seconds))
            finally:
                b.cmd('kill %iperf')
