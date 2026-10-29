// Topology model: the data behind the canvas, plus helpers to change it.
// The topology object is exactly what gets saved as JSON and sent to the
// backend (see backend/topology.py for the format).

const STORAGE_KEY = 'mininet-ui.topology';
// 2: switches connect only to controllers they have a link to.
const TOPOLOGY_VERSION = 2;

const state = {
  topo: { version: TOPOLOGY_VERSION, nodes: [], links: [], notes: [] },
  tool: 'select',        // select | host | switch | controller | link | note | delete
  selected: null,        // { kind: 'node' | 'link' | 'note', id }
  linkStart: null,       // node id while drawing a link
  running: false,        // network is running on the backend
  connected: {},         // switch name -> connected to a controller (from OVS)
  live: null,            // last controller snapshot (live view), or null
};

let idCounter = 0;
function newId(prefix) {
  idCounter += 1;
  return prefix + Date.now().toString(36) + idCounter;
}

function findNode(id) {
  return state.topo.nodes.find(n => n.id === id);
}

function findLink(id) {
  return state.topo.links.find(l => l.id === id);
}

function nodesOfType(type) {
  return state.topo.nodes.filter(n => n.type === type);
}

// Number N formatted as fixed-width hex, e.g. hex(10, 4) -> "000a".
function hex(n, width) {
  return n.toString(16).padStart(width, '0');
}

function macFor(n) {
  return hex(n, 12).match(/../g).join(':');
}

// Smallest N >= start whose defaults (name, IP, MAC, DPID) are all unused,
// so new nodes never clash with existing ones even after renames/edits.
function freeNumber(prefix, used, start = 1) {
  const names = new Set(state.topo.nodes.map(n => n.name));
  let n = start;
  while (names.has(prefix + n) || used(n)) n += 1;
  return n;
}

function addNode(type, x, y) {
  const node = { id: newId('n'), type, x: Math.round(x), y: Math.round(y) };
  if (type === 'host') {
    const ips = new Set(nodesOfType('host').map(h => (h.ip || '').split('/')[0]));
    const macs = new Set(nodesOfType('host').map(h => (h.mac || '').toLowerCase()));
    const n = freeNumber('h', i => ips.has(ipFor(i)) || macs.has(macFor(i)));
    Object.assign(node, { name: 'h' + n, ip: ipFor(n) + '/8', mac: macFor(n) });
  } else if (type === 'switch') {
    const dpids = new Set(nodesOfType('switch').map(s => parseInt(s.dpid, 16)));
    const n = freeNumber('s', i => dpids.has(i));
    Object.assign(node, { name: 's' + n, dpid: hex(n, 16) });
  } else {
    const n = freeNumber('c', () => false, 0);   // first controller is c0
    Object.assign(node, { name: 'c' + n, ip: '127.0.0.1', port: 6653 });
  }
  state.topo.nodes.push(node);
  return node;
}

// 10.0.0.1 ... 10.0.0.254, 10.0.1.1 ... (inside Mininet's 10.0.0.0/8)
function ipFor(n) {
  return '10.0.' + Math.floor((n - 1) / 254) + '.' + ((n - 1) % 254 + 1);
}

// Returns the new link, or an error message string.
function addLink(srcId, dstId) {
  const a = findNode(srcId), b = findNode(dstId);
  if (!a || !b || a === b) return 'Pick two different nodes';
  if (a.type === 'controller' || b.type === 'controller') {
    // A control link: the switch will connect to this controller.
    if (!(a.type === 'switch' || b.type === 'switch') || a.type === b.type) {
      return 'A controller can only be linked to a switch';
    }
    if (state.topo.links.some(l => (l.src === a.id && l.dst === b.id) ||
                                   (l.src === b.id && l.dst === a.id))) {
      return 'This switch is already linked to this controller';
    }
  }
  const link = { id: newId('l'), src: a.id, dst: b.id, bw: '', delay: '', loss: '' };
  state.topo.links.push(link);
  return link;
}

// A link between a switch and a controller (OpenFlow control channel),
// as opposed to a data link that carries traffic.
function isControlLink(link) {
  const a = findNode(link.src), b = findNode(link.dst);
  return !!(a && b) && (a.type === 'controller' || b.type === 'controller');
}

function deleteNode(id) {
  state.topo.nodes = state.topo.nodes.filter(n => n.id !== id);
  state.topo.links = state.topo.links.filter(l => l.src !== id && l.dst !== id);
  if (state.selected && state.selected.id === id) state.selected = null;
}

function deleteLink(id) {
  state.topo.links = state.topo.links.filter(l => l.id !== id);
  if (state.selected && state.selected.id === id) state.selected = null;
}

function setTopology(topo) {
  if (!topo || !Array.isArray(topo.nodes) || !Array.isArray(topo.links)) {
    throw new Error('Not a topology file (needs "nodes" and "links" arrays)');
  }
  state.topo = { version: TOPOLOGY_VERSION, nodes: topo.nodes, links: topo.links,
                 notes: Array.isArray(topo.notes) ? topo.notes : [] };
  // Before version 2 every switch was linked to every controller
  // automatically; add those control links so old files work the same.
  if (!topo.version) {
    for (const c of nodesOfType('controller')) {
      for (const s of nodesOfType('switch')) addLink(s.id, c.id);
    }
  }
  state.selected = null;
  state.linkStart = null;
}

// Text notes on the workspace (like Packet Tracer's "Place Note"). They are
// only for people reading the drawing; Mininet ignores them.
function findNote(id) {
  return state.topo.notes.find(n => n.id === id);
}

function addNote(x, y) {
  const note = { id: newId('t'), x: Math.round(x), y: Math.round(y), text: 'Note' };
  state.topo.notes.push(note);
  return note;
}

function deleteNote(id) {
  state.topo.notes = state.topo.notes.filter(n => n.id !== id);
  if (state.selected && state.selected.id === id) state.selected = null;
}

// Keep the work-in-progress across page reloads.
function persist() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(state.topo));
}

function restore() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) setTopology(JSON.parse(saved));
  } catch (e) {
    console.warn('Ignoring saved topology:', e);
  }
}
