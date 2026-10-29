"""Web server for mininet-ui.

Run from the project root:   sudo python3 backend/app.py
Then open                    http://localhost:8080

Without sudo the editor and script export still work, but Run/Stop and host
commands are disabled (Mininet needs root).
"""
import atexit
import os
import sys

from flask import Flask, jsonify, request, send_from_directory

import onos
from runner import NetworkRunner, RunnerError
from topology import TopologyError, generate_script, normalize

HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.join(HERE, '..', 'frontend')
HOST = os.environ.get('HOST', '127.0.0.1')   # anything else exposes a root shell
PORT = int(os.environ.get('PORT', 8080))
IS_ROOT = hasattr(os, 'geteuid') and os.geteuid() == 0

app = Flask(__name__, static_folder=FRONTEND, static_url_path='')
runner = NetworkRunner()


def body():
    """The request's JSON body, or {} if it has none."""
    return request.get_json(silent=True) or {}


def error(message, status=400, **extra):
    return jsonify(error=message, **extra), status


def need_root():
    if not IS_ROOT:
        raise RunnerError('Backend is not running as root; restart it with '
                          '`sudo python3 backend/app.py`')


@app.errorhandler(TopologyError)
def topology_error(e):
    return error('Topology has errors', errors=e.errors)


@app.errorhandler(RunnerError)
def runner_error(e):
    return error(str(e), 409)


@app.get('/')
def index():
    return send_from_directory(FRONTEND, 'index.html')


@app.post('/api/validate')
def validate():
    _, warnings = normalize(body())
    return jsonify(ok=True, warnings=warnings)


@app.post('/api/export')
def export():
    return jsonify(script=generate_script(body()))


@app.get('/api/status')
def status():
    return jsonify(root=IS_ROOT, **runner.status())


@app.post('/api/start')
def start():
    need_root()
    try:
        warnings = runner.start(body())
    except (TopologyError, RunnerError):
        raise
    except Exception as e:      # Mininet failures: show them in the UI
        return error('Mininet failed to start: %s' % e, 500)
    return jsonify(ok=True, warnings=warnings, **runner.status())


@app.post('/api/stop')
def stop():
    need_root()
    runner.stop()
    return jsonify(ok=True)


@app.post('/api/cmd')
def cmd():
    data = body()
    return jsonify(output=runner.run_cmd(data.get('node', ''), data.get('cmd', '')))


@app.post('/api/ping')
def ping():
    data = body()
    return jsonify(output=runner.ping(data.get('src', ''), data.get('dst', '')))


@app.post('/api/pingall')
def pingall():
    return jsonify(output=runner.pingall())


@app.post('/api/iperf')
def iperf():
    data = body()
    return jsonify(output=runner.iperf(data.get('src', ''), data.get('dst', '')))


@app.get('/api/onos')
def onos_view():
    try:
        return jsonify(ok=True, **onos.snapshot())
    except Exception as e:      # ONOS down, wrong password, ...
        return jsonify(ok=False, error='ONOS unreachable at %s: %s' % (onos.ONOS_URL, e))


@atexit.register
def shutdown():
    if runner.net is not None:
        runner.stop()


if __name__ == '__main__':
    if not IS_ROOT:
        print('WARNING: not running as root; Run/Stop will be disabled.',
              file=sys.stderr)
    print('mininet-ui on http://%s:%d' % (HOST, PORT))
    # threaded=True so the ONOS live view keeps updating during a long iperf.
    app.run(host=HOST, port=PORT, threaded=True)
