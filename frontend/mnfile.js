// MiniEdit .mn files: read and write the format of Mininet's MiniEdit
// (mininet/examples/miniedit.py), so topologies can move between the tools.
//
// A .mn file is JSON with "hosts", "switches", "controllers" and "links";
// nodes are referenced by name and coordinates are strings. Only OpenFlow
// 1.3 + remote controllers are used here, so other MiniEdit settings are
// converted (with a note) when loading.

const MINIEDIT_VERSION = '2.2.0.1';

// MiniEdit numbers every node within its type ("nodeNum"). Use the digits
// at the end of the name (h5 -> 5) when they are unique, otherwise the next
// free number.
function nodeNumbers(nodes) {
  const used = new Set(), result = new Map();
  for (const n of nodes) {
    const m = /(\d+)$/.exec(n.name);
    const num = m ? parseInt(m[1], 10) : NaN;
    if (num >= 1 && !used.has(num)) { used.add(num); result.set(n.id, num); }
  }
  let next = 1;
  for (const n of nodes) {
    if (result.has(n.id)) continue;
    while (used.has(next)) next += 1;
    used.add(next);
    result.set(n.id, next);
  }
  return result;
}

// Copy the fields that are set (not blank) from one object to another.
function copyFields(from, to, keys) {
  for (const k of keys) {
    if (from[k] !== '' && from[k] != null && from[k] !== false) to[k] = from[k];
  }
}

// The fields of a MiniEdit opts object that are set, as a new object.
function pick(opts, keys) {
  const result = {};
  copyFields(opts, result, keys);
  return result;
}

// MiniEdit's global "OpenFlow versions" ticks: those used by any switch.
function openFlowVersions(switches) {
  const used = new Set(switches.map(s => s.protocols || 'OpenFlow13'));
  const tick = v => (used.has('OpenFlow' + v) ? '1' : '0');
  return { ovsOf10: tick('10'), ovsOf11: tick('11'), ovsOf12: tick('12'), ovsOf13: tick('13') };
}

function numberOrString(value) {
  const s = String(value == null ? '' : value).trim();
  return s !== '' && !isNaN(Number(s)) ? Number(s) : s;
}

// Our topology -> MiniEdit .mn object.
function toMiniEdit(topo) {
  const byType = type => topo.nodes.filter(n => n.type === type);
  const name = id => (topo.nodes.find(n => n.id === id) || {}).name;
  const pos = n => ({ x: String(n.x), y: String(n.y) });
  const isControl = l => [l.src, l.dst].some(id =>
    (topo.nodes.find(n => n.id === id) || {}).type === 'controller');

  const controllers = byType('controller').map(c => Object.assign(pos(c), {
    opts: { hostname: c.name, controllerType: 'remote', controllerProtocol: c.protocol || 'tcp',
            remoteIP: c.ip || '127.0.0.1', remotePort: Number(c.port) || 6653 },
  }));

  // MiniEdit keeps control links as each switch's list of controller names.
  const controllersOf = sw => topo.links
    .filter(l => isControl(l) && (l.src === sw.id || l.dst === sw.id))
    .map(l => name(l.src === sw.id ? l.dst : l.src));

  const swNums = nodeNumbers(byType('switch'));
  const switches = byType('switch').map(s => {
    const num = swNums.get(s.id);
    const opts = { hostname: s.name, nodeNum: num, switchType: 'default',
                   controllers: controllersOf(s) };
    if (s.dpid) opts.dpid = s.dpid;
    if (s.listenPort) opts.dpctl = String(s.listenPort);    // MiniEdit's name for it
    // Not in MiniEdit (it ignores them), kept for us:
    copyFields(s, opts, ['protocols', 'failMode', 'datapath', 'stp']);
    return Object.assign({ number: String(num) }, pos(s), { opts });
  });

  const hostNums = nodeNumbers(byType('host'));
  const hosts = byType('host').map(h => {
    const num = hostNums.get(h.id);
    const opts = { hostname: h.name, nodeNum: num, sched: 'host' };
    if (h.ip) opts.ip = h.ip;
    if (h.mac) opts.mac = h.mac;      // ignored by MiniEdit, kept for us
    copyFields(h, opts, ['defaultRoute', 'cores', 'startCommand', 'stopCommand']);
    if (h.cpu) opts.cpu = Number(h.cpu);
    // MiniEdit's sched "host" means no CPU limit.
    if (h.cpu || h.cores) opts.sched = h.sched || 'cfs';
    return Object.assign({ number: String(num) }, pos(h), { opts });
  });

  const links = topo.links.filter(l => !isControl(l)).map(l => {
    const opts = {};
    for (const key of ['bw', 'delay', 'loss', 'jitter', 'max_queue_size']) {
      if (l[key] !== '' && l[key] != null) opts[key] = numberOrString(l[key]);
    }
    return { src: name(l.src), dest: name(l.dst), opts };
  });

  return {
    application: {
      ipBase: '10.0.0.0/8', terminalType: 'xterm', startCLI: '0',
      switchType: 'ovs', dpctl: '',
      openFlowVersions: openFlowVersions(byType('switch')),
      sflow: { sflowTarget: '', sflowHeader: '128', sflowSampling: '400', sflowPolling: '30' },
      netflow: { nflowTarget: '', nflowTimeout: '600', nflowAddId: '0' },
    },
    controllers, hosts, switches, links,
    // Not part of MiniEdit's format; MiniEdit ignores keys it doesn't know.
    notes: (topo.notes || []).map(n => ({ x: String(n.x), y: String(n.y), text: n.text })),
    version: MINIEDIT_VERSION,
  };
}

// MiniEdit .mn object -> our topology. `notes` collects conversion messages.
function fromMiniEdit(data, notes) {
  const nodes = [], links = [], idByName = {};
  // MiniEdit sets the OpenFlow version for all switches; use the newest ticked one.
  const versions = (data.application || {}).openFlowVersions || {};
  const defaultVersion = ['13', '12', '11', '10'].map(v => 'OpenFlow' + v)
    .find(v => versions['ovsOf' + v.slice(-2)] === '1') || 'OpenFlow13';
  const place = (type, item, fields) => {
    const node = Object.assign({ id: newId('n'), type,
                                 x: Math.round(Number(item.x) || 100),
                                 y: Math.round(Number(item.y) || 100) }, fields);
    nodes.push(node);
    idByName[node.name] = node.id;
  };

  for (const c of data.controllers || []) {
    const o = c.opts || {};
    const name = o.hostname || 'c' + nodes.length;
    if (o.controllerType && o.controllerType !== 'remote') {
      notes.push(`${name}: MiniEdit "${o.controllerType}" controller loaded as a ` +
                 `remote controller`);
    }
    place('controller', c, { name, ip: o.remoteIP || '127.0.0.1',
                             port: o.remotePort || 6653, protocol: o.controllerProtocol || 'tcp' });
  }

  for (const s of data.switches || []) {
    const o = s.opts || {};
    const num = parseInt(s.number || o.nodeNum, 10) || 1;
    const name = o.hostname || 's' + num;
    if (o.switchType && !['default', 'ovs'].includes(o.switchType)) {
      notes.push(`${name}: MiniEdit "${o.switchType}" loaded as an OpenFlow switch`);
    }
    place('switch', s, Object.assign({ name, dpid: o.dpid || hex(num, 16),
                                       protocols: o.protocols || defaultVersion,
                                       listenPort: o.dpctl || '' },
                                     pick(o, ['failMode', 'datapath', 'stp'])));
    // The switch's controllers become control links.
    for (const ctl of o.controllers || []) {
      if (idByName[ctl]) {
        links.push({ id: newId('l'), src: idByName[name], dst: idByName[ctl],
                     bw: '', delay: '', loss: '' });
      }
    }
  }

  for (const h of data.hosts || []) {
    const o = h.opts || {};
    const num = parseInt(h.number || o.nodeNum, 10) || 1;
    // MiniEdit fills in IPs from ipBase when they are left blank.
    place('host', h, Object.assign({ name: o.hostname || 'h' + num,
                                     ip: o.ip || ipFor(num) + '/8', mac: o.mac || macFor(num),
                                     cpu: o.cpu ? String(o.cpu) : '',
                                     sched: ['cfs', 'rt'].includes(o.sched) ? o.sched : '' },
                                   pick(o, ['defaultRoute', 'cores', 'startCommand', 'stopCommand'])));
  }

  for (const l of data.links || []) {
    const src = idByName[l.src], dst = idByName[l.dest];
    if (!src || !dst) {
      notes.push(`Skipped link ${l.src}-${l.dest}: unknown device`);
      continue;
    }
    const o = l.opts || {};
    const val = key => (o[key] == null ? '' : String(o[key]));
    links.push({ id: newId('l'), src, dst, bw: val('bw'), delay: val('delay'),
                 loss: val('loss'), jitter: val('jitter'), max_queue_size: val('max_queue_size') });
  }
  const textNotes = (data.notes || []).map(n => ({
    id: newId('t'), x: Math.round(Number(n.x) || 0), y: Math.round(Number(n.y) || 0),
    text: String(n.text || ''),
  }));
  // Control links come from the file, so no automatic ones (see setTopology).
  return { version: TOPOLOGY_VERSION, nodes, links, notes: textNotes };
}

// Parse the text of an opened file (.mn or .json) into our topology.
function parseTopologyFile(text, notes) {
  const data = JSON.parse(text);
  if (Array.isArray(data.nodes)) return data;                  // our JSON
  if (data.hosts || data.switches || data.controllers) {        // MiniEdit
    return fromMiniEdit(data, notes);
  }
  throw new Error('Not a topology file (.mn from MiniEdit, or .json from this tool)');
}
