"""Tests for backend/topology.py. No Mininet or root needed:

    python3 -m unittest discover tests
"""
import json
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, os.path.join(ROOT, 'backend'))

from topology import TopologyError, generate_script, normalize  # noqa: E402


def topo(nodes, links=()):
    return {'nodes': list(nodes), 'links': list(links)}


H1 = {'id': 'a', 'type': 'host', 'name': 'h1', 'ip': '10.0.0.1', 'mac': '00:00:00:00:00:01'}
H2 = {'id': 'b', 'type': 'host', 'name': 'h2', 'ip': '10.0.0.2/8', 'mac': '00:00:00:00:00:02'}
S1 = {'id': 'c', 'type': 'switch', 'name': 's1', 'dpid': '1'}
C0 = {'id': 'd', 'type': 'controller', 'name': 'c0', 'ip': '127.0.0.1', 'port': '6653'}


class NormalizeTests(unittest.TestCase):

    def errors(self, t):
        with self.assertRaises(TopologyError) as ctx:
            normalize(t)
        return ' | '.join(ctx.exception.errors)

    def test_valid_topology(self):
        spec, warnings = normalize(topo(
            [H1, H2, S1, C0],
            [{'id': 'l1', 'src': 'a', 'dst': 'c', 'bw': '10', 'delay': '5', 'loss': ''},
             {'id': 'l2', 'src': 'b', 'dst': 'c'},
             {'id': 'l3', 'src': 'd', 'dst': 'c'}]))      # control link c0-s1
        self.assertEqual(warnings, [])
        self.assertEqual(spec['switches'][0]['controllers'], ['c0'])
        self.assertEqual(len(spec['links']), 2)               # control link isn't a data link
        self.assertEqual(spec['hosts'][0]['ip'], '10.0.0.1/8')     # default prefix
        self.assertEqual(spec['switches'][0]['dpid'], '0000000000000001')
        self.assertEqual(spec['controllers'][0]['port'], 6653)
        self.assertEqual(spec['links'][0], {'src': 'h1', 'dst': 's1',
                                            'opts': {'bw': 10, 'delay': '5ms'}})
        self.assertEqual(spec['links'][1]['opts'], {})

    def test_duplicates(self):
        h = dict(H1, id='x', name='h9')
        s = dict(S1, id='y', name='s9', dpid='0x0001')
        msg = self.errors(topo([H1, h, S1, s]))
        self.assertIn('duplicate IP', msg)
        self.assertIn('duplicate MAC', msg)
        self.assertIn('duplicate DPID', msg)

    def test_bad_names(self):
        msg = self.errors(topo([dict(H1, name='1abc'), dict(S1, name='in')]))
        self.assertIn('name must start with a letter', msg)
        self.assertIn('reserved name', msg)

    def test_bad_link_values(self):
        msg = self.errors(topo([H1, S1], [
            {'id': 'l', 'src': 'a', 'dst': 'c', 'bw': '5000', 'delay': 'soon', 'loss': 'x'}]))
        self.assertIn('bandwidth must be between', msg)
        self.assertIn('delay must look like', msg)
        self.assertIn('loss must be a number', msg)

    def test_controller_only_links_to_switches(self):
        msg = self.errors(topo([H1, S1, C0], [{'id': 'l', 'src': 'a', 'dst': 'd'}]))
        self.assertIn('a controller can only be linked to a switch', msg)

    def test_switch_without_control_link_warns(self):
        _, warnings = normalize(topo([H1, S1, C0], [{'id': 'l', 'src': 'a', 'dst': 'c'}]))
        self.assertEqual(len(warnings), 1)
        self.assertIn('s1 is not linked to a controller', warnings[0])

    def test_warnings(self):
        _, warnings = normalize(topo([H1, S1]))
        self.assertIn('h1 is not connected to anything', warnings)
        self.assertIn('No controller: switches will have no flow rules', warnings)


class ScriptTests(unittest.TestCase):

    def test_example_exports_valid_python(self):
        with open(os.path.join(ROOT, 'examples', 'two-switches.json')) as f:
            script = generate_script(json.load(f))
        compile(script, 'topology.py', 'exec')      # raises SyntaxError if broken
        self.assertIn("protocols='OpenFlow13'", script)
        self.assertIn('controller=RemoteController', script)
        self.assertIn('net.addLink(s1, s2, bw=100, delay=\'2ms\')', script)
        self.assertIn('    c0.start()\n', script)
        self.assertIn('    s1.start([c0])\n', script)
        self.assertNotIn('net.start()', script)     # would link every switch to every controller

    def test_notes_become_comments(self):
        t = topo([H1, S1], [{'id': 'l', 'src': 'a', 'dst': 'c'}])
        t['notes'] = [{'x': 0, 'y': 0, 'text': 'Office LAN\nfloor 2'}]
        script = generate_script(t)
        compile(script, 'topology.py', 'exec')
        self.assertIn('# Note: Office LAN\n#       floor 2\n', script)


if __name__ == '__main__':
    unittest.main()
