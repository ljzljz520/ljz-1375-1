"""测试夹具：在临时目录启动真实 HTTP 服务，返回 base64 极简请求客户端。"""
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Server:
    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="yugang_test_")
        self.port = free_port()
        env = dict(os.environ)
        env.update({"YUGANG_DATA": self.tmp, "YUGANG_PORT": str(self.port)})
        env.pop("YUGANG_FAULT_AFTER_FILES", None)
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "run.py")], cwd=ROOT, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.base = f"http://127.0.0.1:{self.port}"
        self._wait()

    def _wait(self):
        for _ in range(60):
            try:
                self.get("/api/editors")
                return
            except Exception:
                time.sleep(0.15)
        raise RuntimeError("server did not start (set YUGANG_PORT to debug)")

    def _req(self, method, path, body=None, editor=1, env=None):
        url = self.base + path
        data = None
        headers = {"X-Editor-Id": str(editor)}
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                raw = r.read()
                return r.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw.decode("utf-8", "replace")}

    def get(self, p, **kw): return self._req("GET", p, **kw)
    def post(self, p, body=None, **kw): return self._req("POST", p, body or {}, **kw)

    def raw(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return r.status, r.read()

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
            self.proc.wait(timeout=5)
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    def data_path(self, *p):
        return Path(self.tmp).joinpath(*p)


def tiny_png_b64():
    import struct
    import zlib
    import binascii
    raw = bytearray()
    for y in range(2):
        raw.append(0)
        for x in range(2):
            raw += bytes((100 + x * 50, 80, 60))
    comp = zlib.compress(bytes(raw), 9)

    def chunk(tag, d):
        return struct.pack(">I", len(d)) + tag + d + struct.pack(
            ">I", binascii.crc32(tag + d) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", comp) + chunk(b"IEND", b"")
    return base64.b64encode(png).decode()
