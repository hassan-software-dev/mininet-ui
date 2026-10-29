# Project: Packet Tracer–style GUI for Mininet + ONOS

## Context
- Course: Data Communication & Networking (Habib University, Fall 2026), SDN group project.
- Task 1 (due 30 Oct 2026): Mininet + external SDN controller, topology visible in controller GUI. Done/nearly done.
- Task 2 (due 20 Nov 2026): controller-driven SDN function (observe → decide → act), likely DDoS detection with sFlow-RT + ONOS REST API.
- This GUI tool is a side project to replace MiniEdit; it does NOT count as the Task 2 SDN function by itself.

## Environment (Ubuntu 22.04 VirtualBox VM)
- Mininet 2.3.0 from apt (`/usr/lib/python3/dist-packages/mininet`), Python 3.10, Open vSwitch.
- ONOS 2.7.0 in Docker, container name `onos`, ports 8181 (GUI/REST), 8101 (CLI ssh), 6653/6633 (OpenFlow).
  - Credentials: onos / rocks. Apps active: org.onosproject.openflow, org.onosproject.fwd.
  - REST: `curl -u onos:rocks http://localhost:8181/onos/v1/devices` (also /links, /hosts, /flows).
  - CLI ssh needs: `ssh -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa -p 8101 onos@localhost`
- Switches must use OpenFlow 1.3; controller = RemoteController 127.0.0.1:6653.
- Desktop: MiniEdit (Tk) only renders under "Ubuntu on Xorg"; Wayland session shows a black canvas. Prefer a browser-based UI.
- Mininet must run as root (sudo); run `sudo mn -c` between runs.

## Lessons from MiniEdit (avoid these bugs)
- Python 2 leftovers: file opened 'wb' then written str; `dict(a.items() + b.items())`.
- Version mismatch: master MiniEdit imports `StrictVersion` from mininet.util (not in 2.3.0).

## Goal for the tool
A "Cisco Packet Tracer–like" network builder for Mininet:
1. Browser canvas: drag-and-drop hosts, OpenFlow switches, controller; draw links.
2. Properties panel: host IP/MAC, switch DPID, link bandwidth/delay, controller IP/port.
3. Save/load topology as JSON.
4. Export a standalone Mininet Python script (RemoteController to ONOS, OpenFlow13, TCLink).
5. Run/Stop the network from the UI (Python backend running Mininet as root).
6. Click a host → run ping/iperf/commands, show output in the UI.
7. Live view: pull devices/links/hosts/flows from ONOS REST and show connection status on the canvas.

## Suggested architecture
- Backend: Python 3 (Flask or FastAPI) using the Mininet API directly; started with sudo.
- Frontend: single-page HTML/JS (e.g., Cytoscape.js or plain SVG) served by the backend.
- Keep it simple and readable; every group member must be able to explain it.
