# -*- coding: utf-8 -*-
"""端到端真实评测：图片 → 真实 OCR → 规则抽取 → 与数据集真值比对，得到字段级准确率"""
import sys, os, csv, json, glob, time
sys.stdout.reconfigure(encoding="utf-8")
from rapidocr_onnxruntime import RapidOCR
from invoice_extract import extract, parse_amount, _norm

# 数据集根目录不写死：各人机器上位置不同，而且这个路径会跟着文件进公开仓库。
# 用环境变量 OCR_DATASET_ROOT 指定，或用 --base 命令行参数覆盖。
BASE = os.environ.get("OCR_DATASET_ROOT", "")
if not BASE:
    sys.exit("未配置数据集根目录。设环境变量 OCR_DATASET_ROOT=<batch_1 所在目录> 再跑；\n"
             "例：OCR_DATASET_ROOT=D:/data/archive/batch_1 python %s" % os.path.basename(__file__))
if not os.path.isdir(BASE):
    sys.exit("OCR_DATASET_ROOT 指向的目录不存在：%s" % BASE)
csv.field_size_limit(10 ** 7)

# 真值索引
truth = {}
for fn in ["batch1_1.csv", "batch1_2.csv", "batch1_3.csv"]:
    with open(os.path.join(BASE, fn), encoding="utf-8", errors="replace") as f:
        r = csv.reader(f); next(r)
        for row in r:
            if len(row) >= 3:
                truth.setdefault(row[0].strip(), json.loads(row[1]))

imgs = sorted(glob.glob(os.path.join(BASE, "batch1_1", "*.jpg")))[:10] \
     + sorted(glob.glob(os.path.join(BASE, "batch1_3", "*.jpg")))[:5]

eng = RapidOCR()
eng(imgs[0])  # 预热

TXT = {"invoice_number", "invoice_date", "seller_name", "client_name"}
NUM = {"tax", "total"}
F = ["invoice_number", "invoice_date", "seller_name", "client_name", "tax", "total"]
hit = {k: 0 for k in F}
item_hit = item_all = 0
t0 = time.time()
rows_out = []

for p in imgs:
    name = os.path.basename(p)
    res, _ = eng(p)
    lines = [{"text": str(it[1]), "conf": float(it[2]),
              "box": [[int(pt[0]), int(pt[1])] for pt in it[0]]} for it in (res or [])]
    got = extract(lines)
    t = truth.get(name, {})
    tv = {
        "invoice_number": t.get("invoice", {}).get("invoice_number", ""),
        "invoice_date": t.get("invoice", {}).get("invoice_date", ""),
        "seller_name": t.get("invoice", {}).get("seller_name", ""),
        "client_name": t.get("invoice", {}).get("client_name", ""),
        "tax": t.get("subtotal", {}).get("tax", ""),
        "total": t.get("subtotal", {}).get("total", ""),
    }
    ok = []
    for k in F:
        g = str(got[k]["value"] or "")
        if k in NUM:
            a, b = parse_amount(g), parse_amount(tv[k])
            good = a is not None and b is not None and abs(a - b) < 0.02
        else:
            good = _norm(g) == _norm(tv[k]) and g != ""
        if good: hit[k] += 1
        ok.append("%s=%s" % (k[:4], "✓" if good else "✗"))
    # 明细条数 + 每条的数量/含税金额
    ti = t.get("items", [])
    gi = got["_items"]
    if len(gi) == len(ti):
        item_all += 1
        one = all(
            (parse_amount(gi[i]["quantity"]) or -1) == (parse_amount(ti[i]["quantity"]) or -2)
            and abs((parse_amount(gi[i]["total_price"]) or 0) - (parse_amount(ti[i]["total_price"]) or -99)) < 0.02
            for i in range(len(ti)))
        if one: item_hit += 1
        rows_out.append((name, len(ti), len(gi), "明细全对" if one else "明细有差"))
    else:
        item_all += 1
        rows_out.append((name, len(ti), len(gi), "条数不符"))

print("=" * 72)
print("端到端真实评测：%d 张，用时 %.1f 秒（纯 CPU）" % (len(imgs), time.time() - t0))
print("=" * 72)
for l in rows_out: print("  %-18s 真值明细 %d ｜ 抽出 %d ｜ %s" % l)
print("-" * 72)
print("【字段级准确率】")
for k in F:
    print("  %-16s %2d/%d  = %5.1f%%" % (k, hit[k], len(imgs), hit[k] / len(imgs) * 100))
print("  %-16s %2d/%d  = %5.1f%%" % ("明细完全一致", item_hit, item_all, item_hit / item_all * 100))
tot = sum(hit.values())
print("-" * 72)
print("  %-16s %2d/%d  = %5.1f%%" % ("关键字段合计", tot, len(imgs) * len(F), tot / (len(imgs) * len(F)) * 100))
