"""纯标准库 HTTP 服务：编辑/审校 API、编辑器静态资源、已发布展厅托管。"""
from __future__ import annotations

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from . import assets as assets_mod
from . import config, publisher
from .db import connect, init_db
from .store import Store, StoreError

mimetypes.add_type("application/javascript", ".js")


class Handler(BaseHTTPRequestHandler):
    server_version = "YugangHarbor/1.0"

    # ---------- 基础工具 ----------
    def _send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise StoreError("请求体不是合法 JSON", 400)

    def _editor(self):
        try:
            return int(self.headers.get("X-Editor-Id") or config.DEFAULT_EDITOR)
        except ValueError:
            return config.DEFAULT_EDITOR

    def log_message(self, fmt, *args):
        pass  # 安静运行；测试不污染输出

    # ---------- 路由 ----------
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/state":
                with Store() as st:
                    return self._send_json(st.state())
            if path == "/api/editors":
                with Store() as st:
                    return self._send_json(st.list_editors())
            if path == "/api/timeline":
                with Store() as st:
                    return self._send_json(st.timeline_events())
            if path == "/api/releases":
                return self._send_json(publisher.list_releases())
            if path == "/api/diff":
                return self._send_json(publisher.diff_against_current())
            if path == "/api/validate":
                return self._send_json(publisher.validate_current())
            if path.startswith("/api/boats/") and path.endswith("/detail"):
                bid = int(path.split("/")[3])
                with Store() as st:
                    return self._send_json(st.get_boat(bid))
            if path.startswith("/api/interviews/"):
                iid = int(path.rsplit("/", 1)[-1])
                with Store() as st:
                    return self._send_json(st.get_interview(iid))
            if path == "/api/current-release":
                return self._send_json(publisher.current_release())
            return self._serve_static(path)
        except StoreError as e:
            return self._send_json({"error": str(e)}, e.status)
        except FileNotFoundError:
            return self._send_json({"error": "not found"}, 404)
        except Exception as e:  # noqa: BLE001
            return self._send_json({"error": str(e)}, 500)

    def do_POST(self):
        self._mutate()

    def do_PATCH(self):
        self._mutate()

    def _mutate(self):
        path = urlparse(self.path).path
        parts = [p for p in path.strip("/").split("/") if p]
        try:
            data = self._body()
            ed = self._editor()
            with Store() as st:
                resp = self._dispatch(st, parts, data, ed)
                st.conn.commit()
            return self._send_json(resp if resp is not None else {"ok": True})
        except StoreError as e:
            return self._send_json({"error": str(e)}, e.status)
        except publisher.PublishError as e:
            return self._send_json({"error": str(e)}, 409)
        except ValueError as e:
            return self._send_json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            return self._send_json({"error": str(e)}, 500)

    def _dispatch(self, st: Store, parts, data, ed):
        P = parts
        # /api/...
        if P[0] != "api" or len(P) < 2:
            raise StoreError("未知路径", 404)
        res = P[1]

        if res == "boats":
            if len(P) == 2:
                return {"id": st.create_boat(data, ed)}
            bid = int(P[2])
            sub = P[3]
            if sub == "names":
                return {"id": st.add_boat_name(bid, data)}
            if sub == "owners":
                return {"id": st.add_boat_owner(bid, data)}
            if sub == "events":
                return {"id": st.add_boat_event(bid, data)}
            raise StoreError("未知船只子资源", 404)

        if res == "jargons" and len(P) == 2:
            return {"id": st.create_jargon(data)}
        if res == "places" and len(P) == 2:
            return {"id": st.create_place(data)}

        if res == "photos":
            if len(P) == 2:
                return {"id": st.create_photo(data)}
            pid = int(P[2])
            sub = P[3]
            if sub == "license":
                st.update_photo_license(pid, data)
                return {"ok": True}
            if sub == "caption":
                st.update_photo_caption(pid, data)
                return {"ok": True}
            if sub == "refs":
                st.add_photo_ref(pid, data)
                return {"ok": True}
            if sub == "withdraw":
                tid = st.withdraw_photo(pid, data.get("reason", ""), ed)
                return {"task_id": tid}
            if sub == "raw":
                return self._upload_photo(st, pid, data)
            raise StoreError("未知照片子资源", 404)

        if res == "interviews":
            if len(P) == 2:
                return {"id": st.create_interview(data)}
            iid = int(P[2])
            if P[3] == "segments":
                return {"id": st.add_segment(iid, data)}
            if P[3] == "withdraw":
                tid = st.withdraw_interview(iid, data.get("reason", ""), ed)
                return {"task_id": tid}
            raise StoreError("未知访谈子资源", 404)

        if res == "segments":
            sid = int(P[2])
            if P[3] == "withdraw":
                st.withdraw_segment(sid, data.get("reason", ""), ed)
                return {"ok": True}

        if res == "statements":
            if len(P) == 2:
                return {"id": st.create_statement(data, ed)}
            sid = int(P[2])
            if len(P) == 3:
                st.update_statement(sid, data, ed)
                return {"ok": True}
            if P[3] == "reviews":
                st.review_statement(sid, data["action"], data.get("note"), ed)
                return {"ok": True}

        if res == "candidates":
            if len(P) == 2:
                return {"id": st.create_candidate(data, ed)}
            cid = int(P[2])
            if P[3] == "evidence":
                return {"id": st.add_evidence(cid, data, ed)}
            if P[3] == "reject":
                st.reject_candidate(cid)
                return {"ok": True}
            if P[3] == "merge":
                mid = st.merge_boats(cid, ed, data.get("surviving_boat_id"),
                                     data.get("absorbed_boat_id"))
                return {"merge_id": mid}

        if res == "merges":
            mid = int(P[2])
            if P[3] == "undo":
                st.undo_merge(mid, ed)
                return {"ok": True}

        if res == "tasks" and len(P) == 3 and P[2].isdigit():
            st.complete_task(int(P[2]))
            return {"ok": True}

        if res == "publish":
            return publisher.publish(note=data.get("note", ""), editor_id=ed,
                                     expected_hash=data.get("expected_hash"))
        if res == "releases":
            return publisher.rollback(int(P[2])) if len(P) > 3 and P[3] == "rollback" \
                else _bad()
        raise StoreError(f"未知 API：{ '/'.join(P) }", 404)

    def _upload_photo(self, st, pid, data):
        st._one("photos", pid)
        raw = assets_mod.decode_upload(data.get("content", ""),
                                       data.get("encoding", "base64"))
        rel = assets_mod.save_raw("photos", pid, data.get("filename", "photo.bin"), raw)
        st.conn.execute("UPDATE photos SET asset_path=? WHERE id=?", (rel, pid))
        # 派生缩略图，登记为独立资源（撤稿时一并纳入）
        thumb = assets_mod.derive_thumbnail(rel, pid)
        if thumb:
            st.conn.execute("UPDATE photos SET thumb_path=? WHERE id=?", (thumb, pid))
        return {"asset_path": rel, "thumb_path": thumb}

    # ---------- 静态资源 ----------
    def _serve_static(self, path):
        # 已发布展厅：/site/... -> releases/current/...
        if path == "/site" or path == "/site/":
            path = "/site/index.html"
        if path.startswith("/site/"):
            rel = path[len("/site/"):]
            return self._serve_file(config.CURRENT_LINK / rel,
                                    allow_missing_dir=True)
        # 编辑器
        if path == "/":
            return self._serve_file(config.WEB_DIR / "index.html")
        if path in ("/app.js", "/app.css"):
            return self._serve_file(config.WEB_DIR / path.lstrip("/"))
        self._send_json({"error": "not found"}, 404)

    def _serve_file(self, fpath, allow_missing_dir=False):
        fpath = os.path.normpath(str(fpath))
        if allow_missing_dir and not os.path.exists(fpath):
            page = ("<!doctype html><meta charset='utf-8'>"
                    "<body style='font-family:sans-serif;padding:40px'>"
                    "<h2>展厅尚未发布</h2><p>请先在编辑器中完成一次发布。</p>"
                    "<p><a href='/'>返回编辑器</a></p>")
            data = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        elif not os.path.isfile(fpath):
            return self._send_json({"error": "not found"}, 404)
        else:
            data = open(fpath, "rb").read()
        ctype = mimetypes.guess_type(fpath)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _bad():
    raise StoreError("未知路径", 404)


def main():
    init_db()
    port = int(os.environ.get("YUGANG_PORT", "8000"))
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"渔港记忆展厅：编辑器 http://localhost:{port}/  展厅 http://localhost:{port}/site/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


if __name__ == "__main__":
    main()
