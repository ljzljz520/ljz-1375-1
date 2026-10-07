# -*- coding: utf-8 -*-
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request

from app import db as dbmod
from app.server import serve


class PublishBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dbp = os.path.join(self.tmp, "hall.db")
        self.root = os.path.join(self.tmp, "site")
        dbmod.init_db(self.dbp)
        self.httpd = serve(self.dbp, self.root, port=0)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        time.sleep(0.05)

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close()

    def api(self, path, body=None, user=1):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body or {}).encode(),
            headers={"Content-Type": "application/json",
                     "X-User-Id": str(user)})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def get(self, path):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{path}") as r:
            return json.loads(r.read())

    def _approved_statement(self, topic, when, user1=1, user2=2):
        sid = self.api("/api/statements", {"topic": topic, "content": "c",
                                           "occurred_at": when,
                                           "source": "档案甲"})["result"]
        self.api(f"/api/statements/{sid}/submit", {}, user1)
        self.api(f"/api/statements/{sid}/review", {"approve": True}, user2)
        return sid


class TestDiffFrozenVsLive(PublishBase):
    def test_diff_after_live_change(self):
        sid = self._approved_statement("冻结时的陈述", "1950")
        self.api("/api/publish", {"note": "v1"})
        d1 = self.get("/api/diff")
        self.assertFalse(d1["changes"])  # 刚发布，冻结=最新
        # 动态库改动：新陈述送审通过 + 老陈述更新
        self._approved_statement("后来新增", "1999")
        self.api(f"/api/statements/{sid}/update",
                 {"content": "修订后的正文"})
        self.api(f"/api/statements/{sid}/submit")
        self.api(f"/api/statements/{sid}/review", {"approve": True})
        d2 = self.get("/api/diff")
        self.assertIn("statements", d2["changes"])
        self.assertIn(sid, d2["changes"]["statements"]["changed"])
        self.assertEqual(d2["changes"]["statements"]["added"], [sid + 1])

    def test_unapproved_not_in_diff_snapshot(self):
        sid = self.api("/api/statements",
                       {"topic": "草稿", "content": "x",
                        "occurred_at": "1950"})["result"]
        d = self.get("/api/diff")
        self.assertEqual(d["latest_counts"]["statements"], 0)

    def test_open_takedown_blocks_publish(self):
        sid = self._approved_statement("x", "1950")
        tk = self.api("/api/takedowns",
                      {"reason": "处理中",
                       "targets": [{"type": "statement", "id": sid}]})["result"]
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError):
            self.api("/api/publish", {"note": "应被阻止"})
        # force 或执行撤稿后可发布
        v = self.api("/api/publish", {"note": "强制执行", "force": True})
        self.assertTrue(v["result"])

    def test_source_status_visible_on_public(self):
        self._approved_statement("带来源的陈述", "1958..1962")
        self.api("/api/publish", {"note": "v1"})
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/pub/timeline.html" % self.port) as r:
            html = r.read().decode()
        self.assertIn("来源：档案甲", html)
        self.assertIn("年代区间", html)
        # 区间重叠时的“存疑”措辞（再加一个重叠区间）
        self._approved_statement("重叠区间", "1960..1965")
        self.api("/api/publish", {"note": "v2"})
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/pub/timeline.html" % self.port) as r:
            html = r.read().decode()
        self.assertIn("存疑", html)


if __name__ == "__main__":
    unittest.main()
