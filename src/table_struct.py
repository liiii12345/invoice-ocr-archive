# -*- coding: utf-8 -*-
"""明细表格结构还原 —— 把 OCR 的「整行文本」还原成单元格级明细。

关键认知（守工程诚实）：
  · RapidOCR 等通用 OCR 只返回「行级」文本框，本身没有单元格边界。
    真正的单元格级数据来自两条路：
      ① 数电票 XML 原件（结构化，天然单元格级）—— 见 einvoice_xml.py，已覆盖；
      ② 独立表格识别引擎（PP-Structure / 国表格模型）—— 可选增强，检测 OPEN 接口。
  · 对图片 OCR，本模块做「自适应列检测」：根据数据区各段的 x 坐标峰值自动分列，
    取代 invoice_extract 里写死的 640/880/1300 阈值——后者只在 1654 宽图像上成立，
    换尺寸/版面就失效。自适应列跨图宽与版面泛化更好，是纯本地的真实准确率改进。
  · 角色判定：最右列=金额；靠右且整列可解析数量=数量；其余=名称；最左纯序号列忽略。

输出与 invoice_extract 同形：[{description, quantity, total_price}]。
"""
import re


def _parse_amount(x):
    if not x:
        return None
    s = re.sub(r"[^0-9.,]", "", str(x))
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _is_numeric_col(segments):
    """该列所有文本是否都能解析为数量（含 each/倍数字）。"""
    if not segments:
        return False
    cnt = 0
    for t in segments:
        if _parse_amount(t) is not None or re.search(r"\beach\b", t, re.I):
            cnt += 1
    return cnt >= max(1, len(segments) * 0.6)


def adaptive_columns(lines, y_top=0, y_bot=10 ** 9, img_w=1654):
    """在数据区收集各段 x 中心，按列间隙聚类成列锚点（自适应，不写死宽度）。"""
    xs = sorted(l.x for l in lines if y_top < l.y < y_bot)
    if not xs:
        return []
    gap = max(24, img_w * 0.035)        # 列间隙阈值：随图宽缩放
    clusters = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] < gap:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    return [round(sum(c) / len(c)) for c in clusters]


def restore_table(lines, img_w=1654):
    """从 OCR 行还原明细单元格。返回 items: [{description, quantity, total_price}]。"""
    if not lines:
        return []
    # 数据区边界：ITEMS/明细 表头下、SUMMARY/合计 上
    y_top, y_bot = 0, 10 ** 9
    for l in lines:
        t = l.text.lower()
        if re.search(r"^\s*items?\s*$", t) or "货物或应税" in t or "明细" in t:
            y_top = max(y_top, l.y)
        if re.search(r"^\s*summary\s*$", t) or "合计" in t or "总计" in t:
            y_bot = min(y_bot, l.y)
    band = [l for l in lines if y_top < l.y < y_bot] or list(lines)
    if not band:
        return []

    # 按自适应列聚类出列成员（用成员本身判定角色，避免固定窗口误并入邻列）
    gap = max(24, img_w * 0.035)
    segs_sorted = sorted(band, key=lambda l: l.x)
    clusters = [[segs_sorted[0]]]
    for l in segs_sorted[1:]:
        if l.x - clusters[-1][-1].x < gap:
            clusters[-1].append(l)
        else:
            clusters.append([l])
    if len(clusters) < 2:
        return []
    cols = [round(sum(s.x for s in c) / len(c)) for c in clusters]

    # 角色：最右=金额；次右且纯数字列=数量；最左纯序号列忽略；其余=名称
    roles = {cols[i]: None for i in range(len(cols))}
    roles[cols[-1]] = "amount"
    if len(cols) >= 2 and _is_numeric_col([s.text for s in clusters[-2]]):
        roles[cols[-2]] = "qty"
    for i, c in enumerate(cols):
        if roles[c] is not None:
            continue
        if clusters[i] and all(re.match(r"^\d{1,2}\s*[.、)]?$", s.text.strip()) for s in clusters[i]):
            roles[c] = "idx"            # 纯序号列：忽略，不污染名称列
        else:
            roles[c] = "name"

    # 数据行聚类（同 y±14 为同一行）
    rows, cur, cur_y = [], [], None
    for l in sorted(band, key=lambda l: l.y):
        if cur_y is None or abs(l.y - cur_y) <= 14:
            cur.append(l)
            cur_y = l.y if cur_y is None else cur_y
        else:
            rows.append(cur)
            cur, cur_y = [l], l.y
    if cur:
        rows.append(cur)

    items = []
    for row in rows:
        # 跳过纯序号行（如 "3."）
        if len(row) == 1 and re.match(r"^\d{1,2}\s*[.、)]?$", row[0].text.strip()):
            continue
        cells = {"name": [], "qty": [], "amount": []}
        for l in row:
            cx = (l.x + l.x2) / 2
            role = min(cols, key=lambda c: abs(c - cx))
            r = roles.get(role, "name")
            if r == "name":
                cells["name"].append(l.text.strip())
            elif r == "qty":
                cells["qty"].append(l.text.strip())
            elif r == "amount":
                cells["amount"].append(l.text.strip())
            # r == "idx" ：纯序号列，忽略
        desc = " ".join(cells["name"]).strip()
        amt = _parse_amount(" ".join(cells["amount"]))
        qty = _parse_amount(re.sub(r"each", "", " ".join(cells["qty"]), flags=re.I))
        if not desc and amt is None:
            continue
        items.append({
            "description": desc,
            "quantity": ("%.2f" % qty) if qty is not None else "",
            "total_price": ("%.2f" % amt) if amt is not None else "",
        })
    return items


def table_engine_available():
    """可选增强：检测是否安装了独立表格识别引擎（PP-Structure 等）。
    本环境默认未装；装好后自动启用，否则回退自适应列。"""
    try:
        import importlib
        importlib.import_module("paddleocr")
        return True
    except Exception:  # noqa
        return False


def _selftest():
    """合成英文商发表格（每行各字段一个段），验证自适应列 + 角色判定。"""
    class L:
        def __init__(self, text, x, y, x2=None):
            self.text = text
            self.x = x
            self.y = y
            self.x2 = x2 or (x + len(text) * 7)
            self.conf = 0.9

    lines = [
        # 表头（仅用于边界识别，参与自适应但最后不计入 items）
        L("items", 50, 100), L("summary", 50, 600),
        # 数据行 1
        L("1.", 55, 200), L("HP Desktop", 120, 200), L("4 each", 620, 200), L("615.78", 1350, 200),
        # 数据行 2
        L("2.", 55, 230), L("Custom Build", 120, 230), L("3 each", 620, 230), L("4620.00", 1350, 230),
    ]
    items = restore_table(lines, img_w=1400)
    ok = 0
    total = 0

    def check(name, cond, extra=""):
        nonlocal ok, total
        total += 1
        ok += bool(cond)
        print("  %s %s%s" % ("✓" if cond else "✗", name, ("  " + extra) if extra else ""))

    check("1. 还原出 2 行明细", len(items) == 2, "got %d" % len(items))
    if items:
        check("2. 名称列正确", items[0]["description"] == "HP Desktop", items[0]["description"])
        check("3. 数量列正确", items[0]["quantity"] == "4.00", items[0]["quantity"])
        check("4. 金额列正确", items[0]["total_price"] == "615.78", items[0]["total_price"])
        check("5. 第二行名称", items[1]["description"] == "Custom Build", items[1]["description"])
        check("6. 第二行金额", items[1]["total_price"] == "4620.00", items[1]["total_price"])

    # 自适应：换图宽（窄图）仍能分列
    lines2 = [L("items", 30, 100), L("summary", 30, 600),
              L("1.", 35, 200), L("键盘", 70, 200), L("10 each", 360, 200), L("300.00", 760, 200)]
    items2 = restore_table(lines2, img_w=800)
    check("7. 窄图自适应分列", len(items2) == 1 and items2[0]["description"] == "键盘"
          and items2[0]["total_price"] == "300.00", str(items2))

    print("-" * 70)
    print("表格结构还原自测通过 %d / %d" % (ok, total))
    return ok == total


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if _selftest() else 1)
