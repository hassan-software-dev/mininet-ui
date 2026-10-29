// UI glue: toolbar, properties panel, console, and calls to the backend.

const $ = id => document.getElementById(id);
const POLL_MS = 3000;

let isRoot = true;      // backend runs as root (needed for Run)
let busy = false;       // a network request to Mininet is in flight

// ---- console -------------------------------------------------------------

function notify(message, cls) {
  const out = $('console-out');
  const line = document.createElement('div');
  if (cls) line.className = cls;
  line.textContent = message;
  out.appendChild(line);
  out.scrollTop = out.scrollHeight;
}

// Call the backend. GET when body is undefined, otherwise POST JSON.
// Throws an Error (with .errors for topology problems) on failure.
async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({ error: res.status + ' ' + res.statusText }));
  if (!res.ok || data.error) {
    const err = new Error(data.error || 'Request failed');
    err.errors = data.errors || [];
    throw err;
  }
  return data;
}

function showError(e) {
  notify('Error: ' + e.message, 'err');
  (e.errors || []).forEach(msg => notify('  - ' + msg, 'err'));
}

function showWarnings(warnings) {
  (warnings || []).forEach(w => notify('Warning: ' + w, 'warn'));
}

// Run a backend action that uses Mininet, with the UI marked busy.
async function mininetAction(label, path, body) {
  if (busy) return notify('Busy, wait for the previous command to finish', 'warn');
  busy = true;
  updateControls();
  notify('$ ' + label, 'cmd');
  try {
    const data = await api(path, body);
    if (data.output !== undefined) notify(data.output.trimEnd() || '(no output)');
    return data;
  } catch (e) {
    showError(e);
  } finally {
    busy = false;
    updateControls();
  }
}

// ---- toolbar ---------------------------------------------------------------

function setTool(tool) {
  state.tool = tool;
  state.linkStart = null;
  $('rubber-band').setAttribute('visibility', 'hidden');
  document.querySelectorAll('#tools button').forEach(b =>
    b.classList.toggle('active', b.dataset.tool === tool));
  render();
}

document.querySelectorAll('#tools button').forEach(b =>
  b.addEventListener('click', () => setTool(b.dataset.tool)));

function download(filename, content, type) {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

$('btn-new').addEventListener('click', () => {
  if (state.running) return notify('Stop the network first', 'warn');
  if (state.topo.nodes.length && !confirm('Discard the current topology?')) return;
  setTopology({ nodes: [], links: [] });
  onTopologyChanged();
});

$('btn-open').addEventListener('click', () => $('file-input').click());

$('file-input').addEventListener('change', async evt => {
  const file = evt.target.files[0];
  evt.target.value = '';          // allow re-opening the same file
  if (!file) return;
  if (state.running) return notify('Stop the network first', 'warn');
  try {
    setTopology(JSON.parse(await file.text()));
    onTopologyChanged();
    notify('Opened ' + file.name, 'ok');
  } catch (e) {
    showError(e);
  }
});

$('btn-save').addEventListener('click', () => {
  download('topology.json', JSON.stringify(state.topo, null, 2), 'application/json');
});

$('btn-export').addEventListener('click', async () => {
  try {
    const data = await api('/api/export', state.topo);
    download('topology.py', data.script, 'text/x-python');
    notify('Exported topology.py (run it with: sudo python3 topology.py)', 'ok');
  } catch (e) {
    showError(e);
  }
});

$('btn-run').addEventListener('click', async () => {
  notify('Starting network (this takes a few seconds)...');
  const data = await mininetAction('start network', '/api/start', state.topo);
  if (data) {
    state.running = true;
    showWarnings(data.warnings);
    notify('Network running: ' + data.hosts.length + ' hosts, ' +
           data.switches.length + ' switches', 'ok');
  }
  updateControls();
});

$('btn-stop').addEventListener('click', async () => {
  if (await mininetAction('stop network', '/api/stop', {})) {
    state.running = false;
    notify('Network stopped', 'ok');
  }
  updateControls();
});

function updateControls() {
  $('btn-run').disabled = state.running || busy || !isRoot;
  $('btn-run').title = isRoot ? 'Start the network in Mininet'
                              : 'Backend is not running as root (sudo)';
  $('btn-stop').disabled = !state.running || busy;
  const badge = $('net-status');
  badge.textContent = busy ? 'working...' : state.running ? 'running' : 'stopped';
  badge.className = 'badge' + (busy ? ' busy' : state.running ? ' running' : '');
  ['btn-cmd', 'btn-ping', 'btn-iperf', 'btn-pingall'].forEach(id => {
    $(id).disabled = !state.running || busy;
  });
  renderProps();
}

// ---- properties panel ------------------------------------------------------

const FIELDS = {
  host: [['name', 'Name'], ['ip', 'IP address (CIDR)'], ['mac', 'MAC address']],
  switch: [['name', 'Name'], ['dpid', 'DPID (16 hex digits)']],
  controller: [['name', 'Name'], ['ip', 'Controller IP'], ['port', 'OpenFlow port']],
  link: [['bw', 'Bandwidth (Mbit/s, blank = unlimited)'],
         ['delay', 'Delay (e.g. 5ms, blank = none)'],
         ['loss', 'Packet loss (%)']],
};

function addField(body, obj, key, label) {
  const wrap = document.createElement('label');
  wrap.textContent = label;
  const input = document.createElement('input');
  input.value = obj[key] == null ? '' : obj[key];
  input.disabled = state.running;
  input.addEventListener('input', () => {
    obj[key] = input.value.trim();
    render();
    persist();
    if (key === 'name') updateHostLists();
  });
  wrap.appendChild(input);
  body.appendChild(wrap);
}

function addInfo(body, cls, lines) {
  const div = document.createElement('div');
  div.className = cls;
  div.innerText = lines.join('\n');
  body.appendChild(div);
  return div;
}

// Extra details from ONOS for the selected switch or host.
function liveInfo(node) {
  const o = state.onos;
  if (!o || !o.ok) return null;
  if (node.type === 'switch') {
    const id = onosDeviceId(node);
    const dev = o.devices.find(d => d.id === id);
    if (!dev) return ['ONOS: not connected', id];
    return ['ONOS: ' + (dev.available ? 'available' : 'unavailable'), id,
            'Flow rules: ' + (o.flows[id] || 0)];
  }
  if (node.type === 'host') {
    const h = o.hosts.find(x => x.mac === String(node.mac || '').toLowerCase());
    if (!h) return ['ONOS: host not discovered yet', '(send some traffic, e.g. ping)'];
    return ['ONOS: discovered', 'IPs: ' + h.ips.join(', ')]
      .concat(h.locations.map(l => 'at ' + l.device + ' port ' + l.port));
  }
  return null;
}

// Fill the ONOS info box of the properties panel (if there is one).
function renderLive() {
  const box = $('props-live');
  if (!box) return;
  const node = state.selected && findNode(state.selected.id);
  const lines = node && liveInfo(node);
  box.hidden = !lines;
  box.innerText = lines ? lines.join('\n') : '';
}

// The form is only rebuilt when the selection or run state changes, so the
// 3-second polls don't wipe an input while the user is typing in it.
let propsKey = null;

function renderProps() {
  const key = JSON.stringify([state.selected, state.running, busy]);
  if (key === propsKey) return renderLive();
  propsKey = key;
  const body = $('props-body');
  body.replaceChildren();
  const sel = state.selected;
  const obj = sel && (sel.kind === 'node' ? findNode(sel.id) : findLink(sel.id));
  if (!obj) {
    addInfo(body, 'hint', [
      'Pick a tool, then click the canvas to place hosts, switches and a controller.',
      'Link tool: click two nodes to connect them.',
      'Drag nodes to move them. Del deletes the selection, Esc cancels.',
      'Every switch connects to every controller automatically (dashed lines).',
    ]);
    return;
  }
  const kind = sel.kind === 'node' ? obj.type : 'link';
  if (kind === 'link') {
    const name = id => (findNode(id) || { name: '?' }).name;
    addInfo(body, 'hint', [name(obj.src) + ' ↔ ' + name(obj.dst)]);
  }
  FIELDS[kind].forEach(([key, label]) => addField(body, obj, key, label));
  if (state.running) addInfo(body, 'hint', ['Stop the network to edit properties.']);

  addInfo(body, 'live', []).id = 'props-live';
  renderLive();

  if (kind === 'host' && state.running) {
    const actions = document.createElement('div');
    actions.className = 'actions';
    for (const [label, cmd] of [['ifconfig', 'ifconfig'], ['route', 'route -n'],
                                ['arp', 'arp -n']]) {
      const b = document.createElement('button');
      b.textContent = label;
      b.disabled = busy;
      b.addEventListener('click', () => runCommand(obj.name, cmd));
      actions.appendChild(b);
    }
    body.appendChild(actions);
  }
}

// Called by the canvas when the selection changes.
function onSelectionChanged() {
  const node = state.selected && findNode(state.selected.id);
  if (node && node.type === 'host') $('cmd-node').value = node.name;
  render();
  renderProps();
}

// Called whenever nodes or links are added/removed.
function onTopologyChanged() {
  propsKey = null;        // node/link objects may have been replaced
  persist();
  render();
  renderProps();
  updateHostLists();
}

// ---- console bar: commands on hosts ----------------------------------------

function updateHostLists() {
  const names = nodesOfType('host').map(h => h.name);
  for (const id of ['cmd-node', 'cmd-target']) {
    const select = $(id);
    const old = select.value;
    select.replaceChildren(...names.map(n => new Option(n, n)));
    if (names.includes(old)) select.value = old;
  }
  // Default the target to a different host than the source.
  if ($('cmd-target').value === $('cmd-node').value && names.length > 1) {
    $('cmd-target').value = names.find(n => n !== $('cmd-node').value);
  }
}

function runCommand(node, cmd) {
  return mininetAction(node + '> ' + cmd, '/api/cmd', { node, cmd });
}

$('btn-cmd').addEventListener('click', () => {
  const cmd = $('cmd-input').value.trim();
  if (cmd) runCommand($('cmd-node').value, cmd);
});

$('cmd-input').addEventListener('keydown', evt => {
  if (evt.key === 'Enter' && !$('btn-cmd').disabled) $('btn-cmd').click();
});

$('btn-ping').addEventListener('click', () => {
  const src = $('cmd-node').value, dst = $('cmd-target').value;
  mininetAction(`ping ${src} -> ${dst}`, '/api/ping', { src, dst });
});

$('btn-iperf').addEventListener('click', () => {
  const src = $('cmd-node').value, dst = $('cmd-target').value;
  mininetAction(`iperf ${src} -> ${dst}`, '/api/iperf', { src, dst });
});

$('btn-pingall').addEventListener('click', () => {
  mininetAction('pingall', '/api/pingall', {});
});

$('btn-clear').addEventListener('click', () => $('console-out').replaceChildren());

// ---- keyboard shortcuts ---------------------------------------------------------

document.addEventListener('keydown', evt => {
  if (['INPUT', 'SELECT', 'TEXTAREA'].includes(evt.target.tagName)) return;
  if (evt.key === 'Escape') {
    setTool('select');
  } else if ((evt.key === 'Delete' || evt.key === 'Backspace') && state.selected) {
    if (editingLocked()) return;
    if (state.selected.kind === 'node') deleteNode(state.selected.id);
    else deleteLink(state.selected.id);
    onTopologyChanged();
  }
});

// ---- polling: backend status and ONOS live view ----------------------------------

async function pollStatus() {
  try {
    const data = await api('/api/status');
    isRoot = data.root;
    if (!busy) state.running = data.running;
  } catch (e) {
    // backend restarting; try again next time
  }
  updateControls();
}

async function pollOnos() {
  if (!$('onos-live').checked) return;
  try {
    const data = await api('/api/onos');
    state.onos = data;
    $('onos-status').textContent = data.ok
      ? `${data.devices.length} devices, ${data.links.length} links, ${data.hosts.length} hosts`
      : data.error;
  } catch (e) {
    state.onos = null;
    $('onos-status').textContent = 'backend unreachable';
  }
  render();
  renderProps();
}

$('onos-live').addEventListener('change', () => {
  if ($('onos-live').checked) {
    pollOnos();
  } else {
    state.onos = null;
    $('onos-status').textContent = '';
    render();
    renderProps();
  }
});

// ---- start up ----------------------------------------------------------------

restore();
onTopologyChanged();
pollStatus();
setInterval(pollStatus, POLL_MS);
setInterval(pollOnos, POLL_MS);
notify('Ready. Place nodes with the toolbar, then press Run.');
