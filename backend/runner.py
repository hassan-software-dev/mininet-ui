"""Runs one Mininet network in-process and executes commands on its hosts.

Mininet is imported lazily (inside start()) so the web server can still be
started without Mininet installed, e.g. to edit topologies and export scripts.

Mininet node objects are not thread-safe (each host is one bash shell), and
Flask serves requests on several threads, so every call that touches the
network takes self.lock.
"""
import shlex
import subprocess
import threading
import time

from topology import host_limited, host_params, monitoring_commands, normalize

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
                    # Only switches linked to a controller can connect.
                    self._connected = {s['name']: bool(self.net.get(s['name']).connected())
                                       for s in self.spec['switches'] if s['controllers']}
            finally:
                self.lock.release()
        return self._connected

    def start(self, topo):
        """Build and start the network. Returns a list of warnings."""
        spec, warnings = normalize(topo)    # raises TopologyError
        from mininet.net import Mininet
        from mininet.node import CPULimitedHost, RemoteController, OVSSwitch
        from mininet.link import TCLink
        from mininet.clean import cleanup

        with self.lock:
            if self.net is not None:
                raise RunnerError('Network is already running')
            cleanup()           # same as `sudo mn -c`: remove leftovers
            net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                          autoSetMacs=False, build=False, **spec['net'])
            try:
                for c in spec['controllers']:
                    net.addController(c['name'], controller=RemoteController,
                                      ip=c['ip'], port=c['port'], protocol=c['protocol'])
                for s in spec['switches']:
                    net.addSwitch(s['name'], dpid=s['dpid'], **s['opts'])
                for h in spec['hosts']:
                    cls = CPULimitedHost if host_limited(h) else None
                    net.addHost(h['name'], cls=cls, **host_params(h))
                for l in spec['links']:
                    net.addLink(l['src'], l['dst'], **l['opts'])
                # Not net.start(): that connects every switch to every
                # controller. Each switch gets only the ones it's linked to.
                net.build()
                for c in net.controllers:
                    c.start()
                for s in spec['switches']:
                    net.get(s['name']).start([net.get(c) for c in s['controllers']])
                for cmd in monitoring_commands(spec):       # sFlow / NetFlow
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode:
                        warnings.append('sFlow/NetFlow setup failed: ' + result.stderr.strip())
            except Exception:
                try:
                    net.stop()
                finally:
                    cleanup()
                raise
            self.net, self.spec = net, spec

            # Wait up to 10 s for the switches that have a controller.
            controlled = [net.get(s['name']) for s in spec['switches'] if s['controllers']]
            deadline = time.time() + 10
            while controlled and time.time() < deadline:
                controlled = [s for s in controlled if not s.connected()]
                if controlled:
                    time.sleep(0.5)
            if controlled:
                warnings.append('%s did not connect to the controller within 10 s '
                                '(is it running?)' % ', '.join(s.name for s in controlled))

            # Hosts' start commands (e.g. a server ending in "&").
            for h in spec['hosts']:
                if 'startCommand' in h:
                    out = self._timed_cmd(net.get(h['name']), h['startCommand']).strip()
                    if out:
                        warnings.append('%s start command said: %s' % (h['name'], out))
        return warnings

    def _timed_cmd(self, host, cmd):
        """Run cmd on host; `timeout` stops it from hanging forever."""
        return host.cmd('timeout %d sh -c %s' % (CMD_TIMEOUT, shlex.quote(cmd)))

    def stop(self):
        """Stop the network. Returns how many seconds it took; the parts are
        printed to the server log, to see what is slow."""
        with self.lock:
            if self.net is None:
                raise RunnerError('Network is not running')
            began = time.time()
            try:
                for h in self.spec['hosts']:
                    if 'stopCommand' in h:
                        self._timed_cmd(self.net.get(h['name']), h['stopCommand'])
                commands_done = time.time()
                self.net.stop()     # Mininet removes links, switches, hosts one by one
            finally:
                self.net, self.spec, self._connected = None, None, {}
            took = time.time() - began
            print('Network stopped in %.1f s (stop commands %.1f s, Mininet %.1f s)'
                  % (took, commands_done - began, time.time() - commands_done), flush=True)
            return took

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
            return self._timed_cmd(self._host(name), cmd)

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
