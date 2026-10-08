# -*- coding: utf-8 -*-
"""
发票字段抽取器 —— 输入真实 OCR 的文本行（含坐标框与逐行置信度），输出结构化字段 + 证据。

原理：不依赖任何真值。仅用 OCR 的「文本 + 坐标」做版面推理：
  · 左右分栏（x 阈值）区分卖方 / 买方
  · ITEMS 表按列区间取 序号 / 描述 / 数量 / 含税金额
  · SUMMARY 区按列区间取 税额 / 价税合计
每个字段都返回「用了哪几行 + 那些行的坐标框 + 置信度」，供前端做图上描边与复核。
"""
import re

# ── 金额/数字归一化（兼容 1,234.56 / 1.234,56 / 1 234,56 / 1234）──
_NUM_RE = re.compile(r"^[0-9][0-9.,]*$")


def parse_amount(x):
    s = re.sub(r"\s", "", str(x).strip().replace("\u00a0", " "))
    s = s.lstrip("$€£¥")
    if not s or s.endswith("%") or not _NUM_RE.match(s):
        return None
    if "," in s and "." in s:
        s2 = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        p = s.split(",")
        s2 = s.replace(",", "") if (len(p) == 2 and len(p[1]) == 3) else s.replace(",", ".")
    else:
        s2 = s
    try:
        v = float(s2)
        return v if v == v and abs(v) != float("inf") else None
    except ValueError:
        return None


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


class Line:
    __slots__ = ("text", "conf", "box", "x", "y", "x2", "y2")

    def __init__(self, text, conf, box):
        self.text, self.conf, self.box = text, conf, box
        self.x = min(p[0] for p in box); self.y = min(p[1] for p in box)
        self.x2 = max(p[0] for p in box); self.y2 = max(p[1] for p in box)


def to_lines(raw_lines):
    """raw_lines: [{'text','conf','box'}] → [Line]，并按阅读顺序排序"""
    ls = [Line(l["text"], float(l["conf"]), l["box"]) for l in raw_lines]
    ls.sort(key=lambda l: (l.y // 12, l.x))
    return ls


def _ev(lines):
    return {
        "lines": [{"text": l.text, "conf": round(l.conf, 4), "box": l.box} for l in lines],
        "conf": round(min(l.conf for l in lines), 4) if lines else 0.0,
        "box": [min(l.x for l in lines), min(l.y for l in lines),
                max(l.x2 for l in lines), max(l.y2 for l in lines)] if lines else None,
    }


# ── 中文票面「标签：值」解析 ──
# 上面的版面规则是为英文发票写的（invoice no / date of issue / seller: / items 表头），
# 中文票面（发票号码 / 开票日期 / 销售方 / 价税合计）一个都匹配不上 —— 实测中文图主字段全空。
# 这里补一层中文解析：按全角/半角冒号拆「标签：值」，只填空着的字段，不覆盖英文逻辑已抽到的结果。
_CN_FIELDS = [
    ("invoice_code",   r"发票代码|代码",                          "num"),
    ("invoice_number", r"发票号码|发票号|票号",                    "num"),
    ("invoice_date",   r"开票日期|开票日|日期",                    "date"),
    ("seller_name",    r"销售方(名称)?|卖方(名称)?",                "text"),
    ("client_name",    r"购买方(名称)?|买方(名称)?|客户(名称)?",      "text"),
    ("tax_rate",       r"税率",                                 "text"),
    ("tax",            r"税额",                                 "money"),
    ("total",          r"价税合计|合计金额",                       "money"),
]
# 大写金额（壹贰叁…）与「不含税合计」都不是价税合计/税额
_CN_UPPER = re.compile(r"[壹贰叁肆伍陆柒捌玖拾佰仟万亿圆整]")


def _cn_value(tail, kind):
    if kind == "money":
        m = re.search(r"([0-9][0-9,]*(?:\.[0-9]{1,2})?)", tail)
        return m.group(1).replace(",", "") if m else ""
    if kind == "date":
        m = re.search(r"(\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2})", tail)
        return re.sub(r"\s", "", m.group(1)) if m else ""
    if kind == "num":
        m = re.search(r"([0-9]{6,})", tail)
        return m.group(1) if m else ""
    return tail.strip()


def _cn_fill(L, out):
    for l in L:
        t = (l.text or "").strip()
        if not t:
            continue
        for key, pat, kind in _CN_FIELDS:
            if not re.search(pat, t):
                continue
            if kind == "money" and (_CN_UPPER.search(t) or "不含税" in t):
                continue
            cur = out.get(key) or {}
            if cur.get("value"):
                continue
            parts = re.split(r"[:：]\s*", t, maxsplit=1)
            tail = parts[1] if len(parts) == 2 else t
            tail = re.sub(r"^\s*" + pat + r"\s*[:：]?\s*", "", tail).strip()
            v = _cn_value(tail, kind)
            if v:
                out[key] = {"value": v, "evidence": _ev([l])}


def extract(raw_lines, img_w=1654):
    """返回 {字段: {'value':..., 'evidence':{...}}}；未抽到则 value 为空"""
    L = to_lines(raw_lines)
    mid = img_w * 0.48          # 左右分栏阈值
    out = {}

    def put(k, value, ev=None):
        out[k] = {"value": value, "evidence": ev}

    # ── 1. 发票号码 ──
    m = next((l for l in L if re.search(r"invoice\s*no", l.text, re.I)), None)
    if m:
        g = re.search(r"invoice\s*no[:\s]*([0-9]{6,})", m.text, re.I)
        if g:
            put("invoice_number", g.group(1), _ev([m]))
        else:  # 号码在右侧另一行
            cand = [l for l in L if abs(l.y - m.y) < 30 and l.x > m.x and re.search(r"[0-9]{6,}", l.text)]
            put("invoice_number", (re.findall(r"[0-9]{6,}", cand[0].text)[0] if cand else ""), _ev(cand[:1] or [m]))
    else:
        put("invoice_number", "", None)

    # ── 2. 开票日期 ──
    d = next((l for l in L if re.search(r"date\s*of\s*issue", l.text, re.I)), None)
    date_val, date_ev = "", None
    if d:
        same = re.search(r"(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})", d.text)
        if same:
            date_val, date_ev = same.group(1), _ev([d])
        else:
            cand = [l for l in L if abs(l.y - d.y) < 30 and l.x > d.x
                    and re.search(r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}", l.text)]
            if cand:
                date_val = re.search(r"(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})", cand[0].text).group(1)
                date_ev = _ev([d, cand[0]])
    put("invoice_date", date_val, date_ev)

    # ── 3/4. 卖方（左栏）与买方（右栏） ──
    # 注意：真实 OCR 会把 "Tax Id" 误读为 "Tax ld"（I→l），版块边界必须用宽松匹配
    BOUNDARY = re.compile(r"^\s*tax|iban|items|summary", re.I)

    def party(anchor_re, side):
        a = next((l for l in L if re.search(anchor_re, l.text, re.I)), None)
        if not a:
            return "", None, "", []
        below = [l for l in L if l.y > a.y + 10
                 and ((l.x < mid) if side == "L" else (l.x >= mid))]
        below = [l for l in below if not BOUNDARY.search(l.text)]
        if not below:
            return "", None, "", []
        name = below[0]
        # 地址：紧跟其后、遇到下一个版块前，最多 3 行
        addr = []
        for l in below[1:]:
            if BOUNDARY.search(l.text):
                break
            if l.y - name.y > 160 or len(addr) >= 3:
                break
            addr.append(l)
        return name.text, name, "\n".join(l.text for l in addr), addr

    sn, sn_l, sa, sa_l = party(r"seller\s*:", "L")
    put("seller_name", sn, _ev([sn_l]) if sn_l else None)
    put("seller_address", sa, _ev(sa_l) if sa_l else None)

    cn, cn_l, ca, ca_l = party(r"client\s*:", "R")
    put("client_name", cn, _ev([cn_l]) if cn_l else None)
    put("client_address", ca, _ev(ca_l) if ca_l else None)

    # ── 5. 明细项（ITEMS 表） ──
    items_hdr = next((l for l in L if re.search(r"^\s*items\s*$", l.text, re.I)), None)
    summary_hdr = next((l for l in L if re.search(r"^\s*summary\s*$", l.text, re.I)), None)
    y_top = (items_hdr.y + 10) if items_hdr else 0
    y_bot = summary_hdr.y if summary_hdr else 10 ** 6

    # 序号行：形如「3.」且靠左
    idx_rows = [l for l in L if y_top < l.y < y_bot and re.match(r"^\d{1,2}\s*[.、)]?$", l.text.strip()) and l.x < mid * 0.35]
    items, items_ev = [], []
    for i, ir in enumerate(idx_rows):
        y_next = idx_rows[i + 1].y if i + 1 < len(idx_rows) else y_bot
        band = [l for l in L if abs(l.y - ir.y) <= 26]           # 同一行的各列
        descs = [l for l in L if ir.x < l.x < 640 and ir.y - 8 <= l.y < min(y_next, ir.y + 130)
                 and not re.match(r"^\d{1,2}\s*[.、)]?$", l.text.strip())
                 and not re.search(r"^(no|description|qty|um|net|vat|gross)", l.text, re.I)]
        qty_l = next((l for l in band if 620 <= l.x < 880 and re.search(r"[\d.,]+\s*each", l.text, re.I)), None) \
                or next((l for l in band if 620 <= l.x < 880 and parse_amount(l.text) is not None), None)
        gross_l = next((l for l in band if l.x > 1300 and parse_amount(l.text) is not None), None)
        qn = parse_amount(re.sub(r"each", "", qty_l.text, flags=re.I)) if qty_l else None
        gn = parse_amount(gross_l.text) if gross_l else None
        if not descs and gn is None:
            continue
        items.append({
            "description": "\n".join(l.text for l in descs),
            "quantity": ("%.2f" % qn) if qn is not None else "",
            "total_price": ("%.2f" % gn) if gn is not None else "",
        })
        items_ev += descs + ([qty_l] if qty_l else []) + ([gross_l] if gross_l else [])

    # ── 6. 税额 / 价税合计（SUMMARY 区，按列取） ──
    tax_val, total_val, tax_ev, tot_ev = "", "", None, None
    if summary_hdr:
        srows = [l for l in L if l.y > summary_hdr.y]
        tot_row = next((l for l in srows if re.search(r"total", l.text, re.I) and l.x < mid), None)
        anchor_y = tot_row.y if tot_row else (min((l.y for l in srows), default=0) if srows else 0)
        band = [l for l in srows if abs(l.y - anchor_y) <= 20]
        # 合计：最右列
        cand = sorted([l for l in band if l.x > 1300 and parse_amount(l.text) is not None], key=lambda l: -l.x)
        if cand:
            total_val, tot_ev = "%.2f" % parse_amount(cand[0].text), _ev([cand[0]])
        # 税额：VAT 列
        cand2 = [l for l in band if 1080 <= l.x <= 1290 and parse_amount(l.text) is not None]
        if cand2:
            tax_val, tax_ev = "%.2f" % parse_amount(cand2[0].text), _ev([cand2[0]])
    put("tax", tax_val, tax_ev)
    put("total", total_val, tot_ev)

    # ── 6.1 中文票面补充：英文规则抽不到的字段，按「标签：值」再解一次（只填空）──
    _cn_fill(L, out)

    # ── 7. 该版式不存在的字段：保持空（诚实呈现「未识别」） ──
    for k in ("due_date", "discount", "bank_name", "account_number"):
        put(k, "", None)

    # ── 5.1 单元格级增强：自适应列还原（替代硬编码 640/880/1300 阈值，跨图宽泛化）──
    # 原「坐标近似」逻辑作为兜底：自适应列抽得更多或更准时优先采用，否则回退。
    try:
        from table_struct import restore_table
        enh = restore_table(L, img_w)
        if enh and len(enh) >= len(items):
            items, items_ev = enh, []
    except Exception:  # noqa  增强失败绝不影响主流程
        pass

    out["_items"] = items
    out["_items_evidence"] = _ev(items_ev) if items_ev else {"lines": [], "conf": 0.0, "box": None}
    return out
