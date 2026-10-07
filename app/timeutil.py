"""事件时间的规范化、展示与排序。

史料中事件发生时间可能不确切。统一以结构表示：

    {"type": "point",  "date": "1962-05", "precision": "month", "approx": true}
    {"type": "range",  "start": "1958", "end": "1962"}
    {"type": "undated", "note": "受访者记不清，约在大跃进之后"}

关键约束：
- 只有确切/可排序的事件时间可用于时间轴排序；
- 未定(undated)与无界区间一律落到“未定年代”分组，绝不依据采集时间、
  主键或导入顺序编造先后；
- 区间用上下界可比较时才排序，否则同样落入未定分组。
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

# 精度 -> 填充到的 (年,月,日)
_PREC_FILL = {"year": (1, 1), "month": (1, 1), "day": None}
_PREC_ORDER = {"year": 0, "month": 1, "day": 2}


def now_iso() -> str:
    """资料系统时间（采集/入库/发布）一律 UTC、秒级 ISO8601。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_date_token(s: str):
    """返回 (year, month, day, precision) 或 None。"""
    if s is None:
        return None
    s = str(s).strip()
    m = re.fullmatch(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", s)
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else None
    day = int(m.group(3)) if m.group(3) else None
    prec = "day" if day else ("month" if month else "year")
    return year, month, day, prec


def normalize_event_time(payload: dict | None) -> dict:
    """校验并规范化外部输入的事件时间。非法输入抛 ValueError。"""
    if not payload:
        return {"type": "undated", "note": ""}
    t = payload.get("type", "undated")
    approx = bool(payload.get("approx", False))
    if t == "point":
        parsed = _parse_date_token(payload.get("date"))
        if not parsed:
            raise ValueError("point 类型需要合法 date: YYYY[-MM[-DD]]")
        y, mo, d, prec = parsed
        # 以输入精度为准，但允许调用方显式给 precision（不能比输入更细）
        want = payload.get("precision")
        if want in _PREC_ORDER and _PREC_ORDER[want] <= _PREC_ORDER[prec]:
            prec = want
        token = f"{y:04d}"
        if prec in ("month", "day"):
            token += f"-{mo or 1:02d}"
        if prec == "day":
            token += f"-{d or 1:02d}"
        out = {"type": "point", "date": token, "precision": prec}
        if approx:
            out["approx"] = True
        if payload.get("note"):
            out["note"] = str(payload["note"])
        return out
    if t == "range":
        s = _parse_date_token(payload.get("start")) if payload.get("start") else None
        e = _parse_date_token(payload.get("end")) if payload.get("end") else None
        if not s and not e:
            # 完全无界 -> 视为未定，但保留为区间语义说明
            return {"type": "undated", "note": str(payload.get("note") or "")}
        def tok(p):
            if not p:
                return None
            y, mo, d, prec = p
            if prec == "year":
                return f"{y:04d}"
            if prec == "month":
                return f"{y:04d}-{mo:02d}"
            return f"{y:04d}-{mo:02d}-{d:02d}"
        start, end = tok(s), tok(e)
        if start and end and start > end:
            raise ValueError("区间开始晚于结束")
        out = {"type": "range", "start": start, "end": end}
        if approx:
            out["approx"] = True
        if payload.get("note"):
            out["note"] = str(payload["note"])
        return out
    if t == "undated":
        return {"type": "undated", "note": str(payload.get("note") or "")}
    raise ValueError(f"未知事件时间类型: {t}")


def _earliest(token: str | None):
    if not token:
        return None
    return _parse_date_token(token)


def sort_key(ev: dict):
    """可排序时间轴条目的排序键。

    规则（不使用 id / 采集时间 / 导入顺序）：
    - point: 取该日期；
    - range: 两端都有时以起点排序（起点并列再以终点排），只有一端有界时
      仅在能确定下界时排入，否则视为未定；
    - undated 或下界缺失：返回 None，调用方应归入“未定年代”。
    """
    t = ev.get("type")
    if t == "point":
        p = _parse_date_token(ev.get("date"))
        if not p:
            return None
        y, mo, d, _ = p
        return (y, mo or 1, d or 1, 1)
    if t == "range":
        s = _parse_date_token(ev.get("start")) if ev.get("start") else None
        if not s:
            return None
        y, mo, d, _ = s
        e = _parse_date_token(ev.get("end")) if ev.get("end") else None
        end_key = (e[0], e[1] or 1, e[2] or 1) if e else (9999, 12, 31)
        return (y, mo or 1, d or 1, 0, end_key[0], end_key[1], end_key[2])
    return None


def display(ev: dict | None) -> str:
    """面向网页/静态页的中文展示，显式表达不确定性。"""
    if not ev:
        return "年代未定"
    t = ev.get("type", "undated")
    approx = "约" if ev.get("approx") else ""
    if t == "point":
        p = _parse_date_token(ev.get("date"))
        if not p:
            return "年代未定"
        y, mo, d, prec = p
        if prec == "year":
            s = f"{y:04d}年"
        elif prec == "month":
            s = f"{y:04d}年{mo or 1}月"
        else:
            s = f"{y:04d}年{mo or 1}月{d or 1}日"
        return approx + s
    if t == "range":
        s = _fmt_partial(ev.get("start"))
        e = _fmt_partial(ev.get("end"))
        if s and e:
            return f"{approx}{s} 至 {e}"
        if s:
            return f"{approx}{s} 或更晚"
        if e:
            return f"{approx}不晚于 {e}"
        return "年代未定"
    note = ev.get("note")
    return f"年代未定（{note}）" if note else "年代未定"


def _fmt_partial(token):
    if not token:
        return None
    p = _parse_date_token(token)
    if not p:
        return None
    y, mo, d, _ = p
    if mo is None:
        return f"{y:04d}年"
    if d is None:
        return f"{y:04d}年{mo}月"
    return f"{y:04d}年{mo}月{d}日"


def certainty_label(ev: dict | None) -> str:
    """不确定性徽标：确切 / 约 / 区间 / 未定。"""
    if not ev:
        return "未定"
    t = ev.get("type", "undated")
    if t == "undated":
        return "未定"
    if t == "range":
        return "区间"
    return "约" if ev.get("approx") else "确切"


def buckets(events: list[dict]) -> dict:
    """把条目拆成“可排序”与“未定年代”两组；可排序组稳定按时间排。

    同时间并列时用标题做次级键，绝不使用行 id，避免导入顺序泄漏为先后。
    """
    dated, undated = [], []
    for ev in events:
        et = ev.get("event_time")
        k = sort_key(et) if et else None
        if k is None:
            undated.append(ev)
        else:
            dated.append((k, ev))
    dated.sort(key=lambda x: (x[0], str(x[1].get("title") or "")))
    return {
        "dated": [ev for _, ev in dated],
        "undated": undated,
    }
