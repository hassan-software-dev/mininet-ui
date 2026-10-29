#!/usr/bin/env python3
"""Desktop window for mininet-ui: the same interface in its own app window
(GTK + WebKit) instead of a browser tab.

The window runs as the normal user; the server (backend/app.py) runs as root
and is started by run.sh. Closing the window shuts the server down.

    python3 desktop/window.py [URL] [--keep-server]

Needs:  sudo apt install python3-gi gir1.2-webkit2-4.0
"""
import json
import os
import signal
import sys
import urllib.request

# GPU-accelerated drawing in WebKitGTK is glitchy in virtual machines
# (VirtualBox): parts of the page, such as icons, vanish after resizing or
# going full screen. Software drawing is plenty fast for this page.
# Must be set before WebKit is loaded.
os.environ.setdefault('WEBKIT_DISABLE_COMPOSITING_MODE', '1')

import gi  # noqa: E402
gi.require_version('Gtk', '3.0')
try:
    gi.require_version('WebKit2', '4.1')     # Ubuntu 24.04
except ValueError:
    gi.require_version('WebKit2', '4.0')     # Ubuntu 22.04
from gi.repository import GLib, Gtk, WebKit2  # noqa: E402

# Matches mininet-ui.desktop (written by run.sh), so the dock shows our icon.
GLib.set_prgname('mininet-ui')

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, '..', 'frontend', 'icon.svg')
ARGS = [a for a in sys.argv[1:] if not a.startswith('--')]
URL = ARGS[0] if ARGS else 'http://localhost:8080'
KEEP_SERVER = '--keep-server' in sys.argv

# File types offered in the Save dialog, by extension.
FILE_TYPES = {'.mn': 'MiniEdit topology (*.mn)', '.json': 'Topology JSON (*.json)',
              '.py': 'Mininet Python script (*.py)'}


def api(path, body=None):
    """Call the mininet-ui server (same API the web page uses)."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(URL + path, data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


class Window(Gtk.Window):

    def __init__(self):
        super().__init__(title='Mininet UI')
        self.set_default_size(1400, 900)
        if os.path.exists(ICON):
            self.set_icon_from_file(ICON)
        self.folder = os.path.expanduser('~')    # last folder used in Save

        # The page talks to us through window.webkit.messageHandlers.mininetui
        # (see download() in frontend/ui.js).
        manager = WebKit2.UserContentManager()
        manager.register_script_message_handler('mininetui')
        manager.connect('script-message-received::mininetui', self.on_message)
        self.view = WebKit2.WebView.new_with_user_content_manager(manager)
        settings = self.view.get_settings()
        if hasattr(WebKit2, 'HardwareAccelerationPolicy'):   # see the note at the top
            settings.set_hardware_acceleration_policy(WebKit2.HardwareAccelerationPolicy.NEVER)
        self.view.load_uri(URL)
        self.add(self.view)
        self.connect('delete-event', self.on_close)

    def run_js(self, code):
        if hasattr(self.view, 'evaluate_javascript'):      # WebKitGTK >= 2.40
            self.view.evaluate_javascript(code, -1, None, None, None, None, None)
        else:
            self.view.run_javascript(code, None, None, None)

    def on_message(self, manager, result):
        value = result.get_js_value() if hasattr(result, 'get_js_value') else result
        msg = json.loads(value.to_string())
        if msg.get('action') == 'save':
            self.save_file(msg['name'], msg['files'])

    def save_file(self, name, files):
        """Ask where to save; `files` maps extension -> content, and the
        extension the user picks decides which content is written."""
        dialog = Gtk.FileChooserDialog(title='Save', transient_for=self,
                                       action=Gtk.FileChooserAction.SAVE)
        dialog.add_buttons('_Cancel', Gtk.ResponseType.CANCEL,
                           '_Save', Gtk.ResponseType.ACCEPT)
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_folder(self.folder)
        dialog.set_current_name(name)
        filters = {}
        for ext in files:
            f = Gtk.FileFilter()
            f.set_name(FILE_TYPES.get(ext, '*' + ext))
            f.add_pattern('*' + ext)
            dialog.add_filter(f)
            filters[f] = ext
        path = None
        if dialog.run() == Gtk.ResponseType.ACCEPT:
            path = dialog.get_filename()
            ext = os.path.splitext(path)[1].lower()
            if ext not in files:            # no/unknown extension: use the filter's
                ext = filters.get(dialog.get_filter(), next(iter(files)))
                path += ext
            with open(path, 'w') as f:
                f.write(files[ext])
            self.folder = os.path.dirname(path)
        dialog.destroy()
        self.run_js('appSaveDone(%s)' % json.dumps(path))

    def on_close(self, *args):
        try:
            running = api('/api/status').get('running')
        except OSError:
            running = False                 # server already gone
        if running:
            dialog = Gtk.MessageDialog(transient_for=self, modal=True,
                                       message_type=Gtk.MessageType.QUESTION,
                                       buttons=Gtk.ButtonsType.YES_NO,
                                       text='The network is still running.')
            dialog.format_secondary_text('Stop it and quit Mininet UI?')
            answer = dialog.run()
            dialog.destroy()
            if answer != Gtk.ResponseType.YES:
                return True                 # keep the window open
        if not KEEP_SERVER:
            try:
                api('/api/shutdown', {'confirm': True})
            except OSError:
                pass
        Gtk.main_quit()
        return False


if __name__ == '__main__':
    signal.signal(signal.SIGINT, signal.SIG_DFL)    # Ctrl+C in the terminal quits
    Window().show_all()
    Gtk.main()
