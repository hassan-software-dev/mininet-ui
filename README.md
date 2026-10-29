# Mininet UI

A browser-based, Packet Tracer–style network builder for Mininet with an
external SDN controller: ONOS or OpenDaylight (replacement for MiniEdit).
Draw hosts, OpenFlow 1.3 switches and a remote controller, run the network,
ping/iperf between hosts, and watch what the controller sees, all from the browser.

## Quick start

```bash
cd mininet-ui
chmod +x run.sh      # once (needed if the files were copied from Windows)
./run.sh             # as your normal user, not sudo
```

`run.sh` installs whatever is missing (`python3-flask`, `mininet`,
`openvswitch-switch`, `iperf`) with apt, starts Open vSwitch, starts the
`onos` Docker container if it exists but is stopped, then starts the server
and opens <http://localhost:8080> in your browser. It asks for your sudo
password when needed. `./run.sh --no-browser` skips opening the browser;
Ctrl+C stops the server. It does not install ONOS or OpenDaylight.

## Manual setup (Ubuntu 22.04 VM)

```bash
sudo apt install python3-flask      # system package, so `sudo python3` sees it
```

Mininet 2.3.0, Open vSwitch and a controller are assumed to already be
installed: ONOS (Docker container `onos`, as in the Task 1 setup) and/or
OpenDaylight.

## Run

```bash
docker start onos                   # or start OpenDaylight instead
cd mininet-ui
sudo python3 backend/app.py
```

Open <http://localhost:8080> in the VM's browser (works on Wayland, unlike MiniEdit).

Without `sudo` the editor and **Export .py** still work; **Run** is disabled.

Settings via environment variables (use `sudo -E` or `sudo VAR=... python3 ...`):

| Variable    | Default                          |
|-------------|----------------------------------|
| `PORT`      | `8080`                           |
| `HOST`      | `127.0.0.1` (keep it: the server runs commands as root) |
| `ONOS_URL`  | `http://localhost:8181/onos/v1`  |
| `ONOS_USER` / `ONOS_PASS` | `onos` / `rocks`   |
| `ODL_URL`   | `http://localhost:8181`          |
| `ODL_USER` / `ODL_PASS` | `admin` / `admin`   |

ONOS and OpenDaylight both use port 8181 by default, so run one at a time,
or move one of them and set its URL.

## Using it

1. **Build**: pick Host / Switch / Controller and click the canvas. Pick
   **Link** and click two nodes. Drag to move. `Del` deletes, `Esc` cancels.
   Every switch is connected to every controller automatically (dashed lines).
2. **Edit**: click a node or link and change it in the Properties panel
   (host IP/MAC, switch DPID, controller IP/port, link bandwidth/delay/loss).
   New nodes get unique defaults (h1 = 10.0.0.1, 00:00:00:00:00:01, ...).
3. **Save / Open**: download or load the topology as JSON. The current
   drawing is also kept in the browser between reloads. Try
   `examples/two-switches.json`.
4. **Export .py**: downloads a standalone Mininet script
   (`RemoteController`, `OpenFlow13`, `TCLink`). Run it with `sudo python3 topology.py`.
5. **Run / Stop**: starts the network inside the backend (it runs the
   equivalent of `sudo mn -c` first). The topology is locked while running.
6. **Console**: pick a host and type a command, or use Ping / iperf / Ping all.
   Clicking a host on the canvas selects it in the console. Commands time out after 30 s.
7. **Live view**: choose ONOS or OpenDaylight in the toolbar. Every 3 s
   the backend fetches switches/links/hosts/flows from that controller's
   REST API and the canvas shows:
   - green dot on a switch = connected to the controller, red = not;
   - green link = the controller discovered it (LLDP for switch-switch links,
     host location for host links); red dashed = not (yet);
   - green dot on a host = the controller has seen its MAC. Hosts appear only
     after they send traffic, so run **Ping all** once. While the controller
     reports no hosts at all, hosts are left uncoloured (unknown).
   - select a switch to see its controller id and number of flow rules.

   The network itself is the same for both controllers (`RemoteController`,
   OpenFlow 1.3, port 6653); only the live view differs.

## How it works

```
browser (frontend/)            backend/ (Flask, runs as root)
  state.js   topology model  ──JSON──▶ app.py      HTTP API
  canvas.js  SVG drawing                topology.py validate + export script
  ui.js      panels, console            runner.py   Mininet API (start/stop/cmd)
                                        controllers.py  ONOS / ODL REST ──▶ :8181
```

- The topology is a JSON object with `nodes` and `links` (format in
  [backend/topology.py](backend/topology.py)). The same file is saved, loaded,
  exported and run.
- `topology.normalize()` validates everything (names, unique IP/MAC/DPID,
  link values) and produces one "spec" used both by the script exporter and
  by `runner.py`, so the exported script and the Run button build the same network.
- `runner.py` uses the Mininet Python API directly. A lock serialises access
  because each Mininet host is a single shell.
- `controllers.py` reads the controller's REST API on the server side (so
  the browser never needs its password and there are no CORS issues) and
  converts ONOS and OpenDaylight data into one format keyed by DPID.
  For OpenDaylight it tries the RFC 8040 paths (`/rests/data/...`, Fluorine
  and later) and falls back to the old `/restconf/operational/...` ones.

### API

| Method | Path            | Body                 | Result |
|--------|-----------------|----------------------|--------|
| GET    | `/api/status`   |                      | `{root, running, hosts, switches}` |
| POST   | `/api/validate` | topology             | `{warnings}` or `{error, errors}` |
| POST   | `/api/export`   | topology             | `{script}` |
| POST   | `/api/start`    | topology             | `{warnings, hosts, switches}` |
| POST   | `/api/stop`     |                      | `{ok}` |
| POST   | `/api/cmd`      | `{node, cmd}`        | `{output}` |
| POST   | `/api/ping`     | `{src, dst}`         | `{output}` |
| POST   | `/api/iperf`    | `{src, dst}`         | `{output}` |
| POST   | `/api/pingall`  |                      | `{output}` |
| GET    | `/api/live?controller=onos|odl` |  | `{ok, label, devices, links, hosts, flows}` |

## Tests

```bash
python3 -m unittest discover tests     # no root or Mininet needed
```

## Troubleshooting

- **Switches stay red**: check ONOS is up (`docker ps`) and the
  `org.onosproject.openflow` app is active; controller IP/port must be
  `127.0.0.1:6653`.
- **Ping fails**: activate `org.onosproject.fwd` in ONOS. OpenDaylight has
  no forwarding app in recent releases; older ones (e.g. Oxygen) need the
  `odl-l2switch-switch-ui` feature.
- **OpenDaylight: switches stay red**: install the OpenFlow plugin feature
  (`feature:install odl-openflowplugin-flow-services-rest` in the Karaf
  console) so the operational topology is available over REST.
- **OpenDaylight: hosts never appear**: host tracking comes from l2switch,
  which newer releases no longer ship.
- **Run says "already running" / leftovers after a crash**: Stop, or restart
  the backend and run `sudo mn -c`.
