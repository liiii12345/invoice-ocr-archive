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

    # ── 7. 该版式不存在的字段：保持空（诚实呈现「未识别」） ──
    for k in ("due_date", "discount", "bank_name", "account_number"):
        put(k, "", None)

    out["_items"] = items
    out["_items_evidence"] = _ev(items_ev) if items_ev else {"lines": [], "conf": 0.0, "box": None}
    return out
