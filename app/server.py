# -*- coding: utf-8 -*-
"""标准库 HTTP 服务：/api/* JSON 接口 + Web 编辑器 + /pub 公开静态版本。"""
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import db as dbmod
from . import repo
from . import publisher
from .snapshot import build_snapshot, diff_snapshots
from .db import load_tp
from .timeutil import display


class ApiError(Exception):
    def __init__(self, code, msg):
        self.code = code
        super().__init__(msg)


def make_handler(db_path, pub_root, static_dir):
    conn_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        server_version = "YugangMemory/1.0"

        def log_message(self, *a):
            pass

        # ---- 工具 ----
        def _conn(self):
            return dbmod.connect(db_path)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            try:
                return json.loads(raw.decode() or "{}")
            except json.JSONDecodeError:
                raise ApiError(400, "请求体不是合法 JSON")

        def _uid(self, body):
            uid = int(self.headers.get("X-User-Id") or body.get("user_id") or 0)
            if not uid:
                raise ApiError(401, "缺少用户身份（X-User-Id）")
            return uid

        def _send(self, obj, code=200, ctype="application/json; charset=utf-8"):
            data = (json.dumps(obj, ensure_ascii=False).encode()
                    if not isinstance(obj, bytes) else obj)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _file(self, path, ctype="text/html; charset=utf-8"):
            with open(path, "rb") as fh:
                self._send(fh.read(), ctype=ctype)

        # ---- 路由 ----
        def do_GET(self):
            try:
                path = self.path.split("?")[0]
                if path.startswith("/pub/") or path == "/pub":
                    return self._public(path)
                if path == "/" or path == "/editor":
                    return self._file(os.path.join(static_dir, "editor.html"))
                if path == "/api/state":
                    return self._state()
                if path == "/api/timeline":
                    with conn_lock, self._conn() as c:
                        tl = repo.timeline(c)
                    return self._send(tl)
                if path == "/api/diff":
                    return self._diff()
                if path.startswith("/api/pub/"):
                    pid = int(path.rsplit("/", 1)[-1])
                    with self._conn() as c:
                        r = c.execute(
                            "SELECT * FROM publications WHERE id=?",
                            (pid,)).fetchone()
                    if not r:
                        raise ApiError(404, "版本不存在")
                    return self._send({"id": r["id"], "dir": r["dir"],
                                       "note": r["note"],
                                       "snapshot": json.loads(r["snapshot"])})
                if path == "/api/pubs":
                    with self._conn() as c:
                        rows = [dict(r) for r in c.execute(
                            "SELECT id,note,created_at FROM publications "
                            "ORDER BY id").fetchall()]
                        cur = publisher.current_pub_id(c)
                    return self._send({"publications": rows, "current": cur})
                raise ApiError(404, "未知路径")
            except ApiError as e:
                self._send({"error": str(e)}, e.code)
            except Exception as e:  # noqa
                self._send({"error": f"{type(e).__name__}: {e}"}, 500)

        def do_POST(self):
            try:
                body = self._body()
                path = self.path.split("?")[0]
                with conn_lock:
                    c = self._conn()
                    try:
                        result = self._route_post(c, path, body)
                        c.commit()
                    except Exception:
                        c.rollback()
                        raise
                    finally:
                        c.close()
                self._send({"ok": True, "result": result})
            except ApiError as e:
                self._send({"error": str(e)}, e.code)
            except (repo.DomainError, publisher.ConsistencyError,
                    ValueError) as e:
                self._send({"error": str(e)}, 400)
            except Exception as e:  # noqa
                self._send({"error": f"{type(e).__name__}: {e}"}, 500)

        # ---- POST 路由表 ----
        def _route_post(self, c, path, b):
            def uid():
                return self._uid(b)

            if path == "/api/ships":
                return repo.create_ship(c, uid(), b["name"], b.get("note", ""))
            if m := re.fullmatch(r"/api/ships/(\d+)/names", path):
                return repo.add_ship_name(
                    c, uid(), int(m.group(1)), b["name"],
                    b.get("used_from"), b.get("used_to"),
                    b.get("source", ""), b.get("make_current", False))
            if m := re.fullmatch(r"/api/ships/(\d+)/owners", path):
                return repo.add_ship_owner(
                    c, uid(), int(m.group(1)), b["owner"],
                    b.get("occurred_at"), b.get("source", ""))
            if m := re.fullmatch(r"/api/ships/(\d+)/events", path):
                return repo.add_ship_event(
                    c, uid(), int(m.group(1)), b["type"], b["title"],
                    b.get("occurred_at"), b.get("collected_at"),
                    b.get("detail", ""), b.get("source", ""))

            if path == "/api/jargons":
                return repo.create_jargon(
                    c, uid(), b["term"], b.get("meaning", ""),
                    b.get("occurred_at"), b.get("collected_at"),
                    b.get("source", ""))
            if m := re.fullmatch(r"/api/jargons/(\d+)/status", path):
                repo.set_jargon_status(c, uid(), int(m.group(1)), b["status"])
                return "ok"

            if path == "/api/locations":
                return repo.create_location(
                    c, uid(), b["name"], b.get("lat"), b.get("lng"),
                    b.get("note", ""))

            if path == "/api/assets":
                # path 指向服务器可读文件（演示环境），复制登记
                return repo.create_asset(
                    c, uid(), b["kind"], b["path"], b.get("mime", ""),
                    b.get("derived_from"))

            if path == "/api/photos":
                return repo.create_photo(
                    c, uid(), b.get("asset_id"), b.get("caption", ""),
                    b.get("license", ""), b.get("occurred_at"),
                    b.get("collected_at"), b.get("location_ids", []),
                    b.get("ship_ids", []))
            if m := re.fullmatch(r"/api/photos/(\d+)/caption", path):
                repo.set_photo_caption(c, uid(), int(m.group(1)),
                                       b["caption"], b["status"]); return "ok"
            if m := re.fullmatch(r"/api/photos/(\d+)/license", path):
                repo.set_photo_license(c, uid(), int(m.group(1)),
                                       b["license"], b["status"]); return "ok"
            if m := re.fullmatch(r"/api/photos/(\d+)/locations", path):
                repo.link_photo_location(
                    c, uid(), int(m.group(1)), b["location_id"]); return "ok"
            if m := re.fullmatch(r"/api/photos/(\d+)/status", path):
                repo.set_photo_status(c, uid(), int(m.group(1)),
                                      b["status"]); return "ok"

            if path == "/api/interviews":
                return repo.create_interview(
                    c, uid(), b["interviewee"], b.get("collected_at"),
                    b.get("audio_asset"))
            if m := re.fullmatch(r"/api/interviews/(\d+)/segments", path):
                return repo.add_segment(
                    c, uid(), int(m.group(1)), b["text"],
                    b.get("tcin"), b.get("tcout"))
            if m := re.fullmatch(r"/api/segments/(\d+)/withdraw", path):
                repo.withdraw_segment(c, uid(), int(m.group(1))); return "ok"
            if m := re.fullmatch(r"/api/interviews/(\d+)/consent", path):
                repo.set_interview_consent(c, uid(), int(m.group(1)),
                                           b["consent"]); return "ok"
            if m := re.fullmatch(r"/api/interviews/(\d+)/status", path):
                repo.set_interview_status(c, uid(), int(m.group(1)),
                                         b["status"]); return "ok"

            if path == "/api/statements":
                return repo.create_statement(
                    c, uid(), b["topic"], b["content"],
                    b.get("occurred_at"), b.get("collected_at"),
                    b.get("ship_id"), b.get("location_id"),
                    b.get("interview_id"), b.get("source", ""))
            if m := re.fullmatch(r"/api/statements/(\d+)/update", path):
                b.pop("user_id", None)
                repo.update_statement(c, uid(), int(m.group(1)), **b)
                return "ok"
            if m := re.fullmatch(r"/api/statements/(\d+)/submit", path):
                repo.submit_statement(c, uid(), int(m.group(1))); return "ok"
            if m := re.fullmatch(r"/api/statements/(\d+)/review", path):
                repo.review_statement(
                    c, uid(), int(m.group(1)), bool(b.get("approve")))
                return "ok"
            if m := re.fullmatch(r"/api/statements/(\d+)/withdraw", path):
                repo.withdraw_statement(c, uid(), int(m.group(1))); return "ok"

            if path == "/api/candidates":
                return repo.create_candidate(
                    c, uid(), b["ship_a"], b["ship_b"], b.get("note", ""))
            if m := re.fullmatch(r"/api/candidates/(\d+)/evidence", path):
                return repo.add_evidence(
                    c, uid(), int(m.group(1)), b["side"], b["content"])
            if m := re.fullmatch(r"/api/candidates/(\d+)/merge", path):
                return repo.merge_candidate(
                    c, uid(), int(m.group(1)), b["keep_ship_id"])
            if m := re.fullmatch(r"/api/merges/(\d+)/undo", path):
                repo.undo_merge(c, uid(), int(m.group(1))); return "ok"

            if path == "/api/takedowns":
                return repo.create_takedown(
                    c, uid(), b["reason"],
                    [(t["type"], t["id"]) for t in b["targets"]],
                    b.get("cascade", True))
            if m := re.fullmatch(r"/api/takedowns/(\d+)/execute", path):
                return repo.execute_takedown(c, uid(), int(m.group(1)))
            if m := re.fullmatch(r"/api/takedowns/(\d+)/reinstate", path):
                repo.reinstate_takedown(c, uid(), int(m.group(1)))
                return "ok"

            if path == "/api/publish":
                return publisher.publish(
                    c, pub_root, uid(), b.get("note", ""),
                    fail_after_render=bool(b.get("fail_after_render")),
                    force=bool(b.get("force")))
            if path == "/api/publish/activate":
                return publisher.activate_pub(
                    c, pub_root, uid(), int(b["publication_id"]))
            raise ApiError(404, f"未知接口: {path}")

        # ---- 编辑器聚合状态 ----
        def _state(self):
            with self._conn() as c:
                out = {
                    "users": [dict(r) for r in c.execute(
                        "SELECT id,username,role FROM users").fetchall()],
                    "ships": repo.list_ships(c, include_superseded=True),
                    "jargons": repo.list_jargons(c),
                    "locations": repo.list_locations(c),
                    "photos": repo.list_photos(c),
                    "interviews": [dict(r) for r in c.execute(
                        "SELECT * FROM interviews ORDER BY id").fetchall()],
                    "statements": repo.list_statements(c),
                    "candidates": [dict(r) for r in c.execute(
                        "SELECT * FROM identity_candidates ORDER BY id").fetchall()],
                    "evidence": [dict(r) for r in c.execute(
                        "SELECT * FROM identity_evidence ORDER BY id").fetchall()],
                    "merges": [dict(r) for r in c.execute(
                        "SELECT * FROM ship_merges ORDER BY id").fetchall()],
                    "takedowns": [dict(r) for r in c.execute(
                        "SELECT * FROM takedown_tasks ORDER BY id").fetchall()],
                    "assets": [dict(r) for r in c.execute(
                        "SELECT * FROM assets ORDER BY id").fetchall()],
                }
                out["interviews"] = []
                for r in c.execute("SELECT * FROM interviews ORDER BY id").fetchall():
                    d = dict(r)
                    d["segments"] = repo.list_segments(c, d["id"])
                    out["interviews"].append(d)
                for s in out["ships"]:
                    full = repo.get_ship(c, s["id"])
                    s["names"], s["owners"], s["events"] = (
                        full["names"], full["owners"], full["events"])
                cur = publisher.current_pub_id(c)
                out["current_pub"] = cur
            return self._send(out)

        def _diff(self):
            """发布前：冻结内容图 vs 动态查询最新陈述。"""
            with self._conn() as c:
                cur = publisher.current_pub_id(c)
                latest = build_snapshot(c)
                if not cur:
                    return self._send({"has_current": False,
                                       "changes": None,
                                       "latest_counts": _counts(latest)})
                row = c.execute("SELECT snapshot FROM publications WHERE id=?",
                                (cur,)).fetchone()
                frozen = json.loads(row["snapshot"])
                return self._send({"has_current": True, "current_id": cur,
                                   "changes": diff_snapshots(frozen, latest),
                                   "latest_counts": _counts(latest)})

        # ---- 公开静态版本服务 ----
        def _public(self, path):
            with self._conn() as c:
                d = publisher.current_dir(pub_root, c)
            if not d:
                raise ApiError(404, "展厅尚未发布")
            if path in ("/pub", "/pub/"):
                rel = "index.html"
            else:
                rel = path[len("/pub/"):]
                if rel.endswith("/"):
                    rel += "index.html"
            target = os.path.normpath(os.path.join(d, rel))
            if not target.startswith(os.path.abspath(d)):
                raise ApiError(403, "越界路径")
            if not os.path.isfile(target):
                raise ApiError(404, "版本内无此资源")
            ctype = "application/octet-stream"
            if target.endswith(".html"):
                ctype = "text/html; charset=utf-8"
            elif target.endswith(".json"):
                ctype = "application/json; charset=utf-8"
            elif target.endswith(".geojson"):
                ctype = "application/geo+json; charset=utf-8"
            elif target.endswith(".txt"):
                ctype = "text/plain; charset=utf-8"
            elif target.endswith(".png"):
                ctype = "image/png"
            elif target.endswith(".jpg"):
                ctype = "image/jpeg"
            self._file(target, ctype)

    return Handler


def _counts(snap):
    return {k: len(snap[k]) for k in
            ("ships", "jargons", "photos", "interviews", "statements",
             "map", "timeline")}


def serve(db_path, pub_root, host="127.0.0.1", port=8070):
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    dbmod.init_db(db_path)
    os.makedirs(pub_root, exist_ok=True)
    static_dir = os.path.join(os.path.dirname(__file__), "web")
    httpd = ThreadingHTTPServer((host, port),
                               make_handler(db_path, pub_root, static_dir))
    return httpd


if __name__ == "__main__":
    import sys
    p = int(sys.argv[2]) if len(sys.argv) > 2 else 8070
    root = sys.argv[1] if len(sys.argv) > 1 else "data"
    httpd = serve(os.path.join(root, "hall.db"),
                  os.path.join(root, "site"), port=p)
    print(f"渔港记忆展厅 http://127.0.0.1:{p}/")
    httpd.serve_forever()
