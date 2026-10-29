"""Tests for the live-view parsers in backend/controllers.py, using sample
REST responses (no controller needed):

    python3 -m unittest discover tests
"""
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, os.path.join(ROOT, 'backend'))

from controllers import parse_odl, parse_onos  # noqa: E402

D1, D2 = '0000000000000001', '000000000000000a'

EXPECTED = {
    'devices': [{'dpid': D1, 'available': True}, {'dpid': D2, 'available': True}],
    'links': [{'src': D1, 'srcPort': '3', 'dst': D2, 'dstPort': '1'}],
    'hosts': [{'mac': '00:00:00:00:00:01', 'ips': ['10.0.0.1'],
               'locations': [{'dpid': D1, 'port': '1'}]}],
    'flows': {D1: 2, D2: 1},
}


def without_ids(snapshot):
    """Device ids differ per controller; compare everything else."""
    snapshot = dict(snapshot)
    snapshot['devices'] = [{k: v for k, v in d.items() if k != 'id'}
                           for d in snapshot['devices']]
    return snapshot


class OnosTests(unittest.TestCase):

    def test_parse(self):
        out = parse_onos(
            {'devices': [{'id': 'of:' + D1, 'available': True},
                         {'id': 'of:' + D2, 'available': True}]},
            {'links': [{'src': {'device': 'of:' + D1, 'port': '3'},
                        'dst': {'device': 'of:' + D2, 'port': '1'}}]},
            {'hosts': [{'mac': '00:00:00:00:00:01', 'ipAddresses': ['10.0.0.1'],
                        'locations': [{'elementId': 'of:' + D1, 'port': '1'}]}]},
            {'flows': [{'deviceId': 'of:' + D1}, {'deviceId': 'of:' + D1},
                       {'deviceId': 'of:' + D2}]})
        self.assertEqual(without_ids(out), EXPECTED)
        self.assertEqual(out['devices'][0]['id'], 'of:' + D1)


def odl_topology():
    return {'topology': [{
        'topology-id': 'flow:1',
        'node': [
            {'node-id': 'openflow:1'},
            {'node-id': 'openflow:10'},
            {'node-id': 'host:00:00:00:00:00:01',
             'host-tracker-service:addresses': [{'mac': '00:00:00:00:00:01',
                                                 'ip': '10.0.0.1'}],
             'host-tracker-service:attachment-points': [{'tp-id': 'openflow:1:1'}]},
        ],
        'link': [
            {'source': {'source-node': 'openflow:1', 'source-tp': 'openflow:1:3'},
             'destination': {'dest-node': 'openflow:10', 'dest-tp': 'openflow:10:1'}},
            # host links are listed too and must be skipped
            {'source': {'source-node': 'host:00:00:00:00:00:01',
                        'source-tp': 'host:00:00:00:00:00:01'},
             'destination': {'dest-node': 'openflow:1', 'dest-tp': 'openflow:1:1'}},
        ],
    }]}


def odl_inventory():
    return {'node': [
        {'id': 'openflow:1', 'flow-node-inventory:table': [
            {'id': 0, 'flow': [{'id': 'a'}, {'id': 'b'}]}, {'id': 1}]},
        {'id': 'openflow:10', 'flow-node-inventory:table': [
            {'id': 0, 'flow': [{'id': 'c'}]}]},
    ]}


class OdlTests(unittest.TestCase):

    def test_parse_rfc8040(self):
        out = parse_odl({'network-topology:network-topology': odl_topology()},
                        {'opendaylight-inventory:nodes': odl_inventory()})
        self.assertEqual(without_ids(out), EXPECTED)
        self.assertEqual(out['devices'][1]['id'], 'openflow:10')

    def test_parse_old_restconf(self):
        out = parse_odl({'network-topology': odl_topology()},
                        {'nodes': odl_inventory()})
        self.assertEqual(without_ids(out), EXPECTED)

    def test_no_inventory(self):
        out = parse_odl({'network-topology': odl_topology()}, {})
        self.assertEqual(out['flows'], {})


if __name__ == '__main__':
    unittest.main()
