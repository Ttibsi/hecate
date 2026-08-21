from contextlib import contextmanager
import os
import re
import subprocess
import sys
import tempfile


AFTER_COLON = re.compile(":.+$")


class CommandFailed(Exception):
    pass


class DeadServer(CommandFailed):
    pass


def _extract_names(output):
    return list(filter(None, [
        AFTER_COLON.sub("", l)
        for l in output.split("\n")
    ]))


TMUX = os.getenv("HECATE_TMUX_BINARY") or "tmux"


class Tmux(object):
    def __init__(self, name):
        self.name = name
        try:
            subprocess.check_output(
                [TMUX, "-u", "-L", self.name, "list-sessions"],
                stderr=subprocess.STDOUT
            )
        except subprocess.CalledProcessError:
            self.new_session()

    def execute_command(self, *command):
        try:
            cmd = [TMUX, "-u", "-f", os.devnull, "-L", self.name] + list(map(str, command))
            co = subprocess.check_output(
                cmd,
                stderr=subprocess.STDOUT
            )

            if "list-buffers" in command:
                return self.cleaned_up_output(co)

            return co.decode()
        except subprocess.CalledProcessError as e:
            if b"failed to connect to server: Connection refused" in e.output:
                raise DeadServer(e.output)
            raise CommandFailed(e.output)
        except UnicodeDecodeError:
            print(f"Failed CMD: {cmd}", file=sys.stderr)

    # When calling `list-buffers`, tmux reads the first 200 utf8 characters in a 
    # buffer, but that gets turned into raw bytes by python's `check_output()`.
    # We need to strip off any trailing unicode continuation characters where 
    # only half a character was pulled in to hecate
    def cleaned_up_output(self, raw_output: bytes) -> str:
        if not len(raw_output):
            return ""

        before, _, after = raw_output.rpartition(b"\\n")
        out = before if len(before) else after
        while out[-1] > 127:
            out = out[:-1]

        return out.decode()

    def new_session(
        self, width=80, height=24, window=None, name=None, command=None
    ):
        arguments = ["new-session", "-d", "-x", width, "-y", height]
        if window is not None:
            arguments.extend([
                "-s", window
            ])
        if name is not None:
            arguments.extend(["-s", name])
        if command is not None:
            arguments.append(command)
        self.execute_command(*arguments)

    def kill_session(self, name):
        self.execute_command("kill-session", "-t", name)

    def buffers(self):
        return _extract_names(self.execute_command("list-buffers"))

    def panes(self, session=None):
        if session is None:
            return _extract_names(self.execute_command("list-panes"))
        else:
            return _extract_names(self.execute_command(
                "list-panes", "-t", session
            ))

    def select_pane(self, pane):
        self.execute_command("select-pane", "-t", pane)

    def send_keys(self, pane, keys):
        self.execute_command(*(
            ["send-keys", "-t", pane] + list(keys)
        ))

    def send_key(self, pane, key):
        self.execute_command(*(
            ["send-keys", "-t", pane, key]
        ))

    def sessions(self):
        return _extract_names(self.execute_command("list-sessions"))

    def windows(self):
        return _extract_names(self.execute_command("list-windows"))

    def set_buffer(self, buf, data):
        self.execute_command("set-buffer", "-b", buf, data)

    def get_buffer(self, buf):
        with self.temp() as t:
            self.execute_command("save-buffer", "-b", buf, t)
            with open(t, encoding="utf-8") as o:
                return o.read()

    def new_buffer(self, data):
        with self.temp() as t:
            with open(t, "w", encoding="utf-8") as o:
                o.write(data)
            self.execute_command("load-buffer", t)

    def a_buffer(self):
        buffers = self.buffers()
        if buffers:
            return buffers[0]
        self.new_buffer("a_buffer")
        buffers = self.buffers()
        assert buffers
        return buffers[0]

    def capture_pane(self, pane):
        buf = self.a_buffer()
        self.execute_command("capture-pane", "-b", buf, "-t", pane)
        return self.get_buffer(buf)

    def shutdown(self):
        try:
            o = open("/dev/null")
            subprocess.check_call(
                [TMUX, "-u", "-L", self.name, "kill-server"],
                stderr=o
            )
        except subprocess.CalledProcessError:
            pass
        finally:
            o.close()

    @contextmanager
    def temp(self):
        try:
            fd, name = tempfile.mkstemp()
            os.close(fd)
            yield name
        finally:
            try:
                os.unlink(name)
            except os.FileNotFoundError:
                pass
