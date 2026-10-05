# -*- coding: utf-8 -*-
"""抽取样例（v2）：按「校验器考点」挑选，覆盖真实数据里存在的各种异常情形"""
import csv, json, io, base64, os, glob
from collections import defaultdict, Counter
from PIL import Image

csv.field_size_limit(10 ** 7)
BASE = r"D:/BaiduNetdiskDownload/考公资料大全/archive/batch_1"
OUT = os.path.dirname(os.path.abspath(__file__))

def num(x):
    s = str(x).strip().replace("\u00a0", " ").replace(" ", "")
    if s == "" or s.endswith("%"):
        return None
    if "," in s and "." in s:
        return float(s.replace(".", "").replace(",", ".")) if s.rfind(",") > s.rfind(".") else float(s.replace(",", ""))
    if "," in s:
        p = s.split(",")
        return float(s.replace(",", "")) if (len(p) == 2 and len(p[1]) == 3) else float(s.replace(",", "."))
    try:
        return float(s)
    except Exception:
        return None

rows = []
for fn in ["batch1_1.csv", "batch1_2.csv", "batch1_3.csv"]:
    with open(os.path.join(BASE, fn), "r", encoding="utf-8", errors="replace") as f:
        r = csv.reader(f); next(r)
        for row in r:
            if len(row) >= 3:
                rows.append((fn, row))

# 分类
cats = defaultdict(list)
namecount = Counter(r[1][0].strip() for r in rows)
for fn, row in rows:
    name = row[0].strip()
    d = json.loads(row[1])
    items = d["items"]; sub = d["subtotal"]
    s = sum((num(i.get("total_price")) or 0) for i in items)
    t = num(sub.get("total")); tax = num(sub.get("tax"))
    raw_total = str(sub.get("total", ""))
    eur = " " in raw_total or ("," in raw_total and "." not in raw_total)
    tag = None
    if namecount[name] > 1:
        tag = "dup"
    elif " " in raw_total:
        tag = "euro"
    elif tax is not None and abs(s - (t + tax)) <= 0.02 or (tax is not None and abs(s - (t - tax)) <= 0.02):
        tag = "caliber"
    elif t is not None and 0.02 < abs(s - t) <= 0.06:
        tag = "round"
    elif str(sub.get("tax", "")).strip().endswith("%"):
        tag = "rate"
    else:
        tag = "normal"
    cats[tag].append((fn, row, len(items)))

print("分类统计:", {k: len(v) for k, v in cats.items()})

# 挑选：重点覆盖异常类型 + 明细条数 1..7
picked = []
def take(tag, n, prefer_items=None):
    got = 0
    seen = [(p[0], p[1][0]) for p in picked]
    for fn, row, ic in sorted(cats[tag], key=lambda x: (x[2], x[1][0])):
        if prefer_items is not None and ic != prefer_items:
            continue
        # dup 类允许同一文件重复纳入（数据集本身就有重复行），其余去重
        if tag != "dup" and (fn, row[0]) in seen:
            continue
        if len(picked) < 17:
            picked.append((fn, row, tag)); seen.append((fn, row[0])); got += 1
            if got >= n: return
take("dup", 2)          # 同文件重复录入（同发票号）→ 查重考点
take("caliber", 3)      # 明细与合计口径不同（差值=税额）
take("round", 2)        # 徽小舍入不平
take("rate", 1)         # tax 为税率
take("euro", 2)         # 欧式数字格式
# 补齐到 16，按明细条数 1..7 轮询
buckets = defaultdict(list)
for fn, row, ic in cats["normal"]:
    buckets[ic].append((fn, row, "normal"))
i = 0
while len(picked) < 17:
    added = False
    for k in sorted(buckets):
        if i < len(buckets[k]):
            picked.append(buckets[k][i]); added = True
            if len(picked) >= 17: break
    if not added: break
    i += 1

img_index = {}
for sub in ["batch1_1", "batch1_2", "batch1_3"]:
    for p in glob.glob(os.path.join(BASE, sub, "*.jpg")):
        img_index[os.path.basename(p)] = p

def thumb_b64(path, max_w=620):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    if w > max_w:
        im = im.resize((max_w, int(h * max_w / w)), Image.LANCZOS)
    buf = io.BytesIO(); im.save(buf, format="JPEG", quality=72, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

out = []
for idx, (fn, row, tag) in enumerate(picked):
    name = row[0].strip()
    out.append({
        "fileName": name,
        "batch": name.split("-")[0],
        "csvBatch": fn,
        "truth": json.loads(row[1]),
        "ocrText": row[2].strip()[:1600],
        "thumb": thumb_b64(img_index[name]),
    })

with open(os.path.join(OUT, "data.js"), "w", encoding="utf-8") as f:
    f.write("window.OCR_DATA = ")
    json.dump(out, f, ensure_ascii=False)
    f.write(";\n")

print("抽取样例:", len(out))
for s in out:
    t = s["truth"]
    print("  %-18s items=%d total=%-14s tax=%-8s seller=%s" % (
        s["fileName"], len(t["items"]), repr(t["subtotal"].get("total")),
        repr(t["subtotal"].get("tax")), t["invoice"]["seller_name"][:22]))
