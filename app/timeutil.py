# -*- coding: utf-8 -*-
"""
年代/时间点模型：区分三种时间（事件发生、资料采集、公开），
未知年代以区间或未定表示。时间轴排序只允许依据年代本身，
绝不依据导入顺序（rowid/id）编造先后。

时间点(TimePoint)的 JSON 存储形态：
{
  "kind": "exact" | "range" | "undated",
  "start": "YYYY-MM-DD" | "YYYY-MM" | "YYYY" | None,
  "end":   "YYYY-MM-DD" | "YYYY-MM" | "YYYY" | None,
  "label": "原始著录文字，原样保留",
  "note":  "考证备注"
}
"""
import re

# 各精度允许的最大长度（用于区间端点归一）
_PREC = {"year": 4, "month": 7, "day": 10}

_PART_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")


def validate_part(p):
    """校验一个 ISO 边界串，返回精度；非法抛 ValueError。"""
    if p is None:
        raise ValueError("时间边界为空")
    m = _PART_RE.match(p)
    if not m:
        raise ValueError(f"非法时间边界: {p!r}")
    y, mo, d = m.group(1), m.group(2), m.group(3)
    yi = int(y)
    if yi < 1 or yi > 9999:
        raise ValueError(f"年份越界: {p!r}")
    prec = "year"
    if mo:
        prec = "month"
        mi = int(mo)
        if not 1 <= mi <= 12:
            raise ValueError(f"月份越界: {p!r}")
    if d:
        prec = "day"
        di = int(d)
        if not 1 <= di <= 31:
            raise ValueError(f"日期越界: {p!r}")
        if not mo:
            raise ValueError(f"有日无月: {p!r}")
        # 真实日历校验（2月30日之类）
        import datetime
        try:
            datetime.date(yi, int(mo), di)
        except ValueError:
            raise ValueError(f"非法日历日期: {p!r}")
    return prec


def lower_bound(p):
    """边界串的最早可能时刻（字符串比较用）。"""
    validate_part(p)
    if len(p) == 4:
        return p + "-01-01"
    if len(p) == 7:
        return p + "-01"
    return p


def upper_bound(p):
    """边界串的最晚可能时刻（字符串比较用）。"""
    validate_part(p)
    if len(p) == 4:
        return p + "-12-31"
    if len(p) == 7:
        year, month = int(p[:4]), int(p[5:7])
        last = 29 if month == 2 and (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)) else \
               [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
        return f"{p}-{last:02d}"
    return p


def make_tp(kind, start=None, end=None, label=None, note=None):
    """构造规范化时间点；非法输入立即报错，拒绝静默猜年代。"""
    if kind not in ("exact", "range", "undated"):
        raise ValueError(f"非法时间点类型: {kind!r}")
    tp = {"kind": kind, "start": None, "end": None,
          "label": label or "", "note": note or ""}
    if kind == "exact":
        if start is None:
            raise ValueError("exact 时间点必须提供 start")
        validate_part(start)
        tp["start"] = start
        tp["end"] = start
    elif kind == "range":
        if not start or not end:
            raise ValueError("range 时间点必须同时提供 start 与 end")
        validate_part(start)
        validate_part(end)
        if upper_bound(start) > lower_bound(end):
            raise ValueError(f"时间区间上下限倒置: {start} > {end}")
        tp["start"] = start
        tp["end"] = end
    # undated: start/end 均为 None
    return tp


def parse_tp(s, label=None):
    """
    解析著录串：
      '1958' / '1958-03' / '1958-03-12' -> exact
      '1958..1962' / '1958-01..1962-12'  -> range（.. 分隔）
      '' / None / '未知' / '年代不详'      -> undated（原样保留 label）
    不识别的格式直接报错，不允许猜测。
    """
    if s is None:
        return make_tp("undated", label=label)
    s = s.strip()
    if s == "" or s in ("未知", "未定", "年代不详", "待考", "?"):
        return make_tp("undated", label=label or s)
    if ".." in s:
        a, b = s.split("..", 1)
        return make_tp("range", a.strip(), b.strip(), label=label)
    return make_tp("exact", start=s, label=label)


def display(tp):
    """面向公众的中文表述，明确呈现不确定性。"""
    if not tp:
        return "年代未定"
    k = tp["kind"]
    label = (tp.get("label") or "").strip()
    if k == "exact":
        s = tp["start"]
        if len(s) == 4:
            out = f"{s}年"
        elif len(s) == 7:
            out = f"{int(s[:4])}年{int(s[5:7])}月"
        else:
            out = f"{int(s[:4])}年{int(s[5:7])}月{int(s[8:10])}日"
        if label and label != s:
            out = f"{out}（原始著录：{label}）"
        return out
    if k == "range":
        a, b = tp["start"], tp["end"]
        out = f"约{a}—{b}（年代区间）"
        if label:
            out += f"（原始著录：{label}）"
        return out
    return f"年代未定" + (f"（原始著录：{label}）" if label else "")


def is_undated(tp):
    return not tp or tp.get("kind") == "undated" or not tp.get("start")


def sort_key(tp):
    """
    时间轴排序键。只允许用年代信息：
      精确日 > 精确月 > 精确年：按同精度自然序；
      区间：按下限最早可能日、上限最晚可能日排序，并标记顺序强度；
      未定：全部沉底，彼此之间顺序未定义（不返回可区分的次级键）。
    返回 (组, 起始下限, 结束上限)。
    """
    if is_undated(tp):
        return (9, "", "")
    # exact 与 range 统一按物理可能区间排序
    return (0, lower_bound(tp["start"]), upper_bound(tp["end"]))


def order_strength(a, b):
    """
    判定两个已排序时间点先后关系的证据强度：
      "firm"   —— 物理区间不相交，先后确定；
      "weak"   —— 区间重叠或精度不足，只能按著录下限排，不能断言先后；
      ""       —— 任一是未定。
    """
    if is_undated(a) or is_undated(b):
        return ""
    if upper_bound(a["end"]) < lower_bound(b["start"]):
        return "firm"
    return "weak"
