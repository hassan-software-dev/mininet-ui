// SVG canvas: draws the topology and turns mouse actions into model changes.
// render() redraws everything from `state`; topologies are small, so this
// keeps the code simple without any noticeable cost.

const SVG_NS = 'http://www.w3.org/2000/svg';
const svg = document.getElementById('canvas');
const layerControl = document.getElementById('layer-control');
const layerLinks = document.getElementById('layer-links');
const layerNodes = document.getElementById('layer-nodes');
const rubberBand = document.getElementById('rubber-band');

let drag = null;   // { id, dx, dy, moved } while moving a node

// Create an SVG element with attributes and append it to parent.
function el(tag, attrs, parent) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}

function text(parent, x, y, cls, content) {
  const t = el('text', { x, y, class: cls }, parent);
  t.textContent = content;
  return t;
}

// ---- live status from the controller (ONOS or OpenDaylight) -------------
// The backend reports everything keyed by DPID (16 lower-case hex digits),
// whichever controller it came from (see backend/controllers.py).

function dpidOf(sw) {
  return String(sw.dpid || '').toLowerCase().replace(/^0x/, '').padStart(16, '0');
}

function liveHost(host) {
  const mac = String(host.mac || '').toLowerCase();
  return state.live.hosts.find(h => h.mac === mac);
}

// No hosts at all means "unknown", not "down": hosts only show up after
// traffic, and OpenDaylight without l2switch never reports them.
function hostsKnown() {
  return state.live.hosts.length > 0;
}

// 'up' / 'down' for a node, or null when the live view is off.
function nodeStatus(node) {
  const o = state.live;
  if (!o || !o.ok) return null;
  if (node.type === 'switch') {
    const dev = o.devices.find(d => d.dpid === dpidOf(node));
    return dev && dev.available ? 'up' : 'down';
  }
  if (node.type === 'host' && hostsKnown()) return liveHost(node) ? 'up' : 'down';
  return null;
}

function linkStatus(link) {
  const o = state.live;
  if (!o || !o.ok) return null;
  const a = findNode(link.src), b = findNode(link.dst);
  if (a.type === 'switch' && b.type === 'switch') {
    const da = dpidOf(a), db = dpidOf(b);
    const found = o.links.some(l => (l.src === da && l.dst === db) ||
                                    (l.src === db && l.dst === da));
    return found ? 'up' : 'down';
  }
  const host = a.type === 'host' ? a : b, sw = a.type === 'switch' ? a : b;
  if (host.type !== 'host' || sw.type !== 'switch' || !hostsKnown()) return null;
  const h = liveHost(host);
  const found = h && h.locations.some(l => l.dpid === dpidOf(sw));
  return found ? 'up' : 'down';
}

// ---- drawing -----------------------------------------------------------

function subtitle(node) {
  if (node.type === 'host') return node.ip || '';
  if (node.type === 'switch') return 'dpid ' + String(node.dpid || '').replace(/^0+/, '');
  return (node.ip || '') + ':' + (node.port || '');
}

function drawNode(node) {
  const cls = ['node', node.type];
  if (state.selected && state.selected.id === node.id) cls.push('selected');
  if (state.linkStart === node.id) cls.push('link-start');
  const g = el('g', { class: cls.join(' '), 'data-id': node.id,
                      transform: `translate(${node.x},${node.y})` }, layerNodes);

  if (node.type === 'host') {
    el('rect', { class: 'halo', x: -27, y: -23, width: 54, height: 46, rx: 8 }, g);
    el('rect', { class: 'shape', x: -20, y: -16, width: 40, height: 26, rx: 4 }, g);
    el('path', { class: 'glyph', d: 'M0 10 V16 M-9 16 H9' }, g);
  } else if (node.type === 'switch') {
    el('rect', { class: 'halo', x: -33, y: -21, width: 66, height: 42, rx: 9 }, g);
    el('rect', { class: 'shape', x: -26, y: -14, width: 52, height: 28, rx: 6 }, g);
    el('path', { class: 'glyph',
                 d: 'M-14 -5 H14 M8 -10 L14 -5 L8 0 M14 5 H-14 M-8 0 L-14 5 L-8 10' }, g);
  } else {
    el('circle', { class: 'halo', r: 27 }, g);
    el('circle', { class: 'shape', r: 20 }, g);
    text(g, 0, 6, 'glyph', 'C');
  }
  text(g, 0, 36, 'name', node.name);
  text(g, 0, 49, 'sub', subtitle(node));

  const status = nodeStatus(node);
  if (status) el('circle', { class: 'dot ' + status, cx: 20, cy: -16, r: 5 }, g);
}

function linkLabel(link) {
  const parts = [];
  if (link.bw !== '' && link.bw != null) parts.push(link.bw + ' Mb/s');
  if (link.delay) parts.push(/^[\d.]+$/.test(link.delay) ? link.delay + 'ms' : link.delay);
  if (link.loss) parts.push(link.loss + '% loss');
  return parts.join(' · ');
}

function drawLink(link) {
  const a = findNode(link.src), b = findNode(link.dst);
  if (!a || !b) return;
  const cls = ['link'];
  if (state.selected && state.selected.id === link.id) cls.push('selected');
  const status = linkStatus(link);
  if (status) cls.push(status);
  const g = el('g', { class: cls.join(' '), 'data-id': link.id }, layerLinks);
  const pts = { x1: a.x, y1: a.y, x2: b.x, y2: b.y };
  el('line', Object.assign({ class: 'wire' }, pts), g);
  el('line', Object.assign({ class: 'hit' }, pts), g);   // wide invisible click target
  const label = linkLabel(link);
  if (label) text(g, (a.x + b.x) / 2, (a.y + b.y) / 2 - 6, '', label);
}

// Dashed lines from each controller to every switch (not real links).
function drawControlLines() {
  for (const c of nodesOfType('controller')) {
    for (const s of nodesOfType('switch')) {
      el('line', { x1: c.x, y1: c.y, x2: s.x, y2: s.y }, layerControl);
    }
  }
}

function render() {
  layerControl.replaceChildren();
  layerLinks.replaceChildren();
  layerNodes.replaceChildren();
  drawControlLines();
  state.topo.links.forEach(drawLink);
  state.topo.nodes.forEach(drawNode);
  svg.setAttribute('class', 'tool-' + state.tool);
}

// ---- interaction ---------------------------------------------------------

// Mouse event -> canvas coordinates.
function svgPoint(evt) {
  const p = svg.createSVGPoint();
  p.x = evt.clientX;
  p.y = evt.clientY;
  return p.matrixTransform(svg.getScreenCTM().inverse());
}

function editingLocked() {
  if (state.running) {
    notify('Stop the network before changing the topology', 'warn');
    return true;
  }
  return false;
}

function select(kind, id) {
  state.selected = kind ? { kind, id } : null;
  onSelectionChanged();
}

function onNodeDown(evt, id, p) {
  const node = findNode(id);
  if (state.tool === 'delete') {
    if (!editingLocked()) { deleteNode(id); onTopologyChanged(); }
  } else if (state.tool === 'link') {
    if (editingLocked()) return;
    if (!state.linkStart) {
      state.linkStart = id;
    } else {
      const result = addLink(state.linkStart, id);
      state.linkStart = null;
      rubberBand.setAttribute('visibility', 'hidden');
      if (typeof result === 'string') notify(result, 'warn');
      else { select('link', result.id); onTopologyChanged(); }
    }
    render();
  } else {
    // Any other tool: select, and start dragging (allowed while running,
    // since positions only matter for the drawing).
    select('node', id);
    drag = { id, dx: p.x - node.x, dy: p.y - node.y, moved: false };
    svg.setPointerCapture(evt.pointerId);
  }
}

svg.addEventListener('pointerdown', evt => {
  if (evt.button !== 0) return;
  const p = svgPoint(evt);
  const nodeEl = evt.target.closest('.node');
  const linkEl = evt.target.closest('.link');

  if (nodeEl) {
    onNodeDown(evt, nodeEl.dataset.id, p);
  } else if (linkEl) {
    if (state.tool === 'delete') {
      if (!editingLocked()) { deleteLink(linkEl.dataset.id); onTopologyChanged(); }
    } else {
      select('link', linkEl.dataset.id);
    }
  } else if (['host', 'switch', 'controller'].includes(state.tool)) {
    if (editingLocked()) return;
    const node = addNode(state.tool, p.x, p.y);
    select('node', node.id);
    onTopologyChanged();
  } else {
    state.linkStart = null;
    rubberBand.setAttribute('visibility', 'hidden');
    select(null);
  }
});

svg.addEventListener('pointermove', evt => {
  const p = svgPoint(evt);
  if (drag) {
    const node = findNode(drag.id);
    node.x = Math.round(p.x - drag.dx);
    node.y = Math.round(p.y - drag.dy);
    drag.moved = true;
    render();
  } else if (state.linkStart) {
    const a = findNode(state.linkStart);
    rubberBand.setAttribute('x1', a.x);
    rubberBand.setAttribute('y1', a.y);
    rubberBand.setAttribute('x2', p.x);
    rubberBand.setAttribute('y2', p.y);
    rubberBand.setAttribute('visibility', 'visible');
  }
});

svg.addEventListener('pointerup', () => {
  if (drag && drag.moved) persist();
  drag = null;
});
