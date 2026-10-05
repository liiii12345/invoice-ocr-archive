# -*- coding: utf-8 -*-
"""对比评测：纯规则抽取 vs 纯 LLM 抽取 vs 混合（LLM 主抽 + 规则兜底 + 交叉校验）"""
import sys, os, csv, json, glob, time
sys.stdout.reconfigure(encoding="utf-8")
from rapidocr_onnxruntime import RapidOCR
from invoice_extract import extract as rule_extract, parse_amount, _norm
from llm_extract import llm_extract, LLMConfig

BASE = r"D:/BaiduNetdiskDownload/考公资料大全/archive/batch_1"
csv.field_size_limit(10 ** 7)
truth = {}
for fn in ["batch1_1.csv", "batch1_3.csv"]:
    for row in csv.reader(open(os.path.join(BASE, fn), encoding="utf-8", errors="replace")):
        if len(row) >= 3 and row[0].strip() != "File Name":
            try: truth.setdefault(row[0].strip(), json.loads(row[1]))
            except Exception: pass

F = ["invoice_number", "invoice_date", "seller_name", "seller_address",
     "client_name", "client_address", "tax", "total"]
imgs = sorted(glob.glob(os.path.join(BASE, "batch1_1", "*.jpg")))[:4] \
     + sorted(glob.glob(os.path.join(BASE, "batch1_3", "*.jpg")))[:4]

cfg = LLMConfig()
print("LLM:", cfg.describe(), "| ready:", cfg.ready())
eng = RapidOCR(); eng(imgs[0])

def ok(a, b):
    a, b = str(a or "").strip(), str(b or "").strip()
    if not b: return None                      # 真值为空 → 不计
    if not a: return False
    na, nb = parse_amount(a), parse_amount(b)
    return abs(na - nb) < 0.02 if (na is not None and nb is not None) else _norm(a) == _norm(b)

acc = {"rule": [0, 0], "llm": [0, 0], "hybrid": [0, 0]}
item_acc = {"rule": [0, 0], "llm": [0, 0], "hybrid": [0, 0]}
tot_conf = tot_items_conf = 0
rows = []
t0 = time.time()

for p in imgs:
    name = os.path.basename(p)
    res, _ = eng(p)
    lines = [{"text": str(it[1]), "conf": float(it[2]),
              "box": [[int(q[0]), int(q[1])] for q in it[0]]} for it in res]
    r = rule_extract(lines)
    rf = {k: v for k, v in r.items() if not k.startswith("_")}
    ri = r["_items"]
    lf, li, meta = llm_extract(lines, cfg)
    t = truth.get(name, {})
    tv = {"invoice_number": t["invoice"]["invoice_number"], "invoice_date": t["invoice"]["invoice_date"],
          "seller_name": t["invoice"]["seller_name"], "seller_address": t["invoice"]["seller_address"],
          "client_name": t["invoice"]["client_name"], "client_address": t["invoice"]["client_address"],
          "tax": t["subtotal"]["tax"], "total": t["subtotal"]["total"]}
    ti = t["items"]

    for k in F:
        e = ok(rf[k]["value"], tv[k]);  acc["rule"][1] += e is not None; acc["rule"][0] += 1 if e else 0
        e = ok(lf[k]["value"], tv[k]);  acc["llm"][1] += e is not None;  acc["llm"][0] += 1 if e else 0
        hv = lf[k]["value"] or rf[k]["value"]
        e = ok(hv, tv[k]);              acc["hybrid"][1] += e is not None; acc["hybrid"][0] += 1 if e else 0

    def items_ok(got):
        if len(got) != len(ti): return False
        return all(abs((parse_amount(g["total_price"]) or -1) - (parse_amount(x["total_price"]) or -2)) < 0.02
                   and abs((parse_amount(g["quantity"]) or -1) - (parse_amount(x["quantity"]) or -2)) < 0.02
                   for g, x in zip(got, ti))
    for key, got in (("rule", ri), ("llm", li), ("hybrid", li)):
        item_acc[key][1] += 1; item_acc[key][0] += 1 if items_ok(got) else 0

    # 冲突统计（用混合逻辑重算一遍）
    c = sum(1 for k in F if rf[k]["value"] and lf[k]["value"] and not ok(rf[k]["value"], lf[k]["value"]))
    ic = sum(1 for a, b in zip(li, ri)
             if not (abs((parse_amount(a["total_price"]) or -1) - (parse_amount(b["total_price"]) or -2)) < 0.02))
    tot_conf += c; tot_items_conf += ic
    rows.append((name, c, ic, meta.get("elapsed"), (meta.get("usage") or {}).get("total_tokens")))

print("=" * 78)
print("对比评测：%d 张，总耗时 %.0f 秒" % (len(imgs), time.time() - t0))
print("=" * 78)
for r_ in rows:
    print("  %-18s 字段冲突 %d ｜ 明细冲突 %d 项 ｜ LLM %.1fs ｜ %s tokens" % r_)
print("-" * 78)
print("【字段级准确率（8 字段，真值非空才计入）】")
for k in ("rule", "llm", "hybrid"):
    o, t_ = acc[k]
    print("  %-8s %3d/%3d = %5.1f%%" % (k, o, t_, o / t_ * 100 if t_ else 0))
print("【明细完全一致（条数+每项数量+金额）】")
for k in ("rule", "llm", "hybrid"):
    o, t_ = item_acc[k]
    print("  %-8s %3d/%3d = %5.1f%%" % (k, o, t_, o / t_ * 100 if t_ else 0))
print("-" * 78)
print("合计：字段冲突 %d 次 ｜ 明细冲突 %d 项（均已标记待复核）" % (tot_conf, tot_items_conf))
