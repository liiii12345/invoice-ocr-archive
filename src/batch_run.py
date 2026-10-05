#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
批量测试入口：把一整批图片丢进来，逐张跑「OCR → 票种判定 → 抽取 → 合规链路」，
最后出一张汇总 CSV + 一份总览 JSON（含字段级准确率）。

一句用法：
    python src/batch_run.py --input <目录|glob|清单.txt|清单.csv> [选项]

输入来源（--input 可重复传，会合并去重）：
    --input D:/data/batch_1            递归扫目录里的图片（jpg/jpeg/png/webp/bmp）
    --input "D:/data/**/*.jpg"         glob 模式，Windows 下用引号包住防止 shell 展开
    --input list.txt                   每行一个图片路径，# 开头是注释，空行忽略
    --input truth.csv                  第一列是文件名；见 --base 拼绝对路径

常用选项：
    --truth batch1_1.csv              真值表（第一列文件名，第二列是 JSON 或扁平键值对）
                                       传了它才会额外算字段级准确率与明细一致率
    --base D:/data/batch_1             清单里只写文件名时用，用来拼绝对路径
    --extractor rule|llm|hybrid       抽取策略，默认 rule
    --limit N                          只跑前 N 张（pilot 摸底用）
    --offset N                         跳过前 N 张
    --out DIR                          输出目录，默认 ./batch_out
    --resume                           跳过上一次 results.csv 里已成功的那批（断点续跑）
    --detail                           额外写 results_detail.json（含 OCR 全文与坐标，体积大）
    --workers N                        多进程并行，默认 1（OCR 模型单进程最稳；>1 时每个子进程各加载一份模型）

输出（都在 --out 目录下）：
    results.csv            一文件一行的汇总表
    summary.json           总览：数量/成败/票种分布/字段准确率/耗时统计
    failed.txt             失败清单（带原因）
    results_detail.json    可选（要 --detail）：每张的 OCR 行、字段证据坐标、完整记录

字段准确率口径（与 src/eval_extract.py 完全一致，便于数字对得上）：
    文本字段（invoice_number / invoice_date / seller_name / client_name）：归一化后全等且非空
    金额字段（tax / total）：parse_amount 后差值 < 0.02
    明细：条数一致且同序 quantity / total_price 都对，才算「明细全对」
"""
import os, sys, csv, json, time, glob, math, argparse
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
FIELDS = ["invoice_number", "invoice_date", "seller_name", "client_name", "tax", "total"]
TXT_FIELD = {"invoice_number", "invoice_date", "seller_name", "client_name"}
NUM_FIELD = {"tax", "total"}


# ---------------------------------------------------------------- 输入列举
def _expand_userglob(pattern):
    """先试 glob 模块；Windows 下含 ** 的模式交给 pathlib 递归更稳。"""
    hits = [p for p in glob.glob(pattern, recursive=True) if os.path.isfile(p)]
    if hits:
        return hits
    try:
        from pathlib import Path
        return [str(p) for p in Path().glob(pattern)
                if p.is_file() and p.suffix.lower() in IMG_EXT]
    except Exception:
        return []


def collect_inputs(args):
    """把 --input 的四种形态统一成绝对路径列表。"""
    out = []
    for src in (args.input or []):
        if os.path.isdir(src):
            for dp, _, fns in os.walk(src):
                for fn in fns:
                    if os.path.splitext(fn)[1].lower() in IMG_EXT:
                        out.append(os.path.join(dp, fn))
        elif os.path.isfile(src):
            ext = os.path.splitext(src)[1].lower()
            if ext == ".txt":
                with open(src, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            out.append(line)
            elif ext == ".csv":
                out.extend(_collect_from_csv(src, args))
            else:
                out.append(src)
        elif any(ch in src for ch in "*?["):
            out.extend(_expand_userglob(src))
        else:
            print("[warn] 路径不存在，已跳过：%s" % src)
    # 去重（保持顺序）
    seen, uniq = set(), []
    for p in out:
        ap = os.path.abspath(p)
        if ap not in seen:
            seen.add(ap)
            uniq.append(ap)
    return uniq


def _collect_from_csv(path, args):
    """CSV 清单：第一列当文件名，配合 --base 拼绝对路径（也支持直接写绝对路径）。"""
    base = args.base or os.path.dirname(os.path.abspath(path))
    out = []
    csv.field_size_limit(10 ** 7)
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip():
                continue
            name = row[0].strip()
            p = name if os.path.isabs(name) else os.path.join(base, name)
            if os.path.isfile(p):
                out.append(p)
            else:
                print("[warn] 清单里的文件找不到：%s" % p)
    return out


# ---------------------------------------------------------------- 真值
def load_truth(path, name_col=0):
    """真值表：返回 {文件名: {flat_dict}}。第二列若是 JSON 串就解析，否则当扁平键值。"""
    truth = {}
    if not path or not os.path.isfile(path):
        return truth
    csv.field_size_limit(10 ** 7)
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        for row in csv.reader(f):
            if not row or len(row) <= name_col:
                continue
            name = row[name_col].strip()
            if not name or name.lower() == "file name":
                continue
            raw = row[1] if len(row) > 1 else ""
            try:
                obj = json.loads(raw)          # 数据集那种「第二列是 JSON」
            except Exception:
                obj = _flatten_kv(row)         # 退化为扁平「键=值」行
            truth[name] = obj
    return truth


def _flatten_kv(row):
    d = {}
    for cell in row[1:]:
        if "=" in cell:
            k, v = cell.split("=", 1)
            d[k.strip()] = v.strip()
    return d


def _truth_payload(obj):
    """真值取字段的兼容写法：JSON 嵌套（invoice/seller…）与扁平两种都吃。"""
    def dig(*keys, default=""):
        cur = obj
        for k in keys:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return default
        return cur if cur is not None else default
    return {
        "invoice_number": dig("invoice", "invoice_number", default=""),
        "invoice_date": dig("invoice", "invoice_date", default=""),
        "seller_name": dig("invoice", "seller_name", default=""),
        "client_name": dig("invoice", "client_name", default=""),
        "tax": dig("subtotal", "tax", default=""),
        "total": dig("subtotal", "total", default=""),
    }


def _norm(s):
    from invoice_extract import _norm as nx
    return nx(s)


def _amount(s):
    from invoice_extract import parse_amount
    return parse_amount(s)


# ---------------------------------------------------------------- 单文件处理
def _worker(job):
    """--workers > 1 时子进程跑这里（Windows 是 spawn，模型各进程一份）。"""
    from ocr_service import run_ocr
    path, extractor = job
    try:
        r = run_ocr(path, extractor)
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    if r.get("ok"):
        return r
    return r


def run_one(path, extractor):
    from ocr_service import run_ocr
    return run_ocr(path, extractor)


# ---------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser(description="批量 OCR + 抽取测试（出汇总表与准确率）")
    ap.add_argument("--input", action="append", default=[],
                    help="目录 / glob / 清单.txt / 清单.csv，可重复传")
    ap.add_argument("--truth", default="", help="真值 CSV，第一列文件名")
    ap.add_argument("--name-col", type=int, default=0, help="真值 CSV 里文件名所在列，默认 0")
    ap.add_argument("--base", default="", help="清单里只写文件名时用来拼路径")
    ap.add_argument("--extractor", default="rule", choices=["rule", "llm", "hybrid"])
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--out", default="")
    ap.add_argument("--resume", action="store_true", help="跳过上次已成功的文件")
    ap.add_argument("--detail", action="store_true", help="额外写 results_detail.json")
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()

    files = collect_inputs(args)
    if args.offset:
        files = files[args.offset:]
    if args.limit:
        files = files[:args.limit]
    if not files:
        raise SystemExit("没有可跑的输入。--input 指向目录 / glob / 清单文件都行。")

    outdir = args.out or os.path.join(HERE, "batch_out")
    os.makedirs(outdir, exist_ok=True)
    res_csv = os.path.join(outdir, "results.csv")
    sum_json = os.path.join(outdir, "summary.json")
    fail_txt = os.path.join(outdir, "failed.txt")

    # 断点续跑：上次成功的记下来
    done = set()
    if args.resume and os.path.isfile(res_csv):
        # 必须 utf-8-sig：results.csv 写的是带 BOM 的编码，用 utf-8 读首列名会变成 \ufeffname
        with open(res_csv, encoding="utf-8-sig", errors="replace") as f:
            for row in csv.DictReader(f):
                if row.get("ok") == "True":
                    done.add(row["name"])
        print("[resume] 上次已成功 %d 个，本次跳过" % len(done))

    truth = load_truth(args.truth, args.name_col)
    if truth:
        print("[truth] 载入真值 %d 条（%s）" % (len(truth), os.path.basename(args.truth)))

    print("[plan] 待跑 %d 张 | extractor=%s | workers=%d" % (len(files), args.extractor, args.workers))
    t_all = time.time()          # 计时必须从「真正开始跑」起算
    if args.workers > 1:
        import multiprocessing
        ctx = multiprocessing.get_context("spawn")   # Windows 必须 spawn，fork 不安全
        pool = ctx.Pool(args.workers)
        jobs = [(p, args.extractor) for p in files]
        raws = pool.map(_worker, jobs, chunksize=1)
        pool.close()
        pool.join()
    else:
        raws = [run_one(p, args.extractor) for p in files]

    # ---------------------------------------------------------- 汇总
    rows, hit = [], {k: 0 for k in FIELDS}
    vtype_cnt, ok_cnt, fail_cnt = Counter(), 0, 0
    elapses, item_full, item_all, has_item = [], 0, 0, 0
    field_cnt = {k: 0 for k in FIELDS}          # 有值的张数（用于「非空率」）
    failures, details = [], []

    header = (["name", "ok", "vtype", "vtype_name", "avg_conf", "line_count", "elapsed",
               "extractor"] + FIELDS + ["item_count", "error"])
    fcsv = open(res_csv, "w", encoding="utf-8-sig", newline="")
    writer = csv.writer(fcsv)
    writer.writerow(header)

    for path, r in zip(files, raws):
        name = os.path.basename(path)
        ok = bool(r.get("ok"))
        if ok:
            ok_cnt += 1
            fields = r.get("fields") or {}
            v = r.get("vtype") or {}
            vtype_str = v.get("code") or "unknown"
            vtype_nm = v.get("short") or v.get("name") or ""
            vtype_cnt[str(vtype_str)] += 1
            conf = r.get("avg_conf") or 0.0
            el = r.get("elapsed") or 0.0
            elapses.append(el)
            row = [name, "True", str(vtype_str), str(vtype_nm), conf,
                   r.get("line_count", 0), el, args.extractor]
            for k in FIELDS:
                val = (fields.get(k) or {}).get("value", "") if isinstance(fields.get(k), dict) else ""
                row.append(val)
                if str(val).strip():
                    field_cnt[k] += 1
            items = r.get("items") or []
            row.append(len(items))
            if args.detail:
                details.append({"name": name, **r})
        else:
            fail_cnt += 1
            err = r.get("error") or r.get("message") or "unknown"
            failures.append("%s | %s" % (name, err))
            vtype_cnt["<失败>"] += 1
            row = [name, "False", "", "", 0, 0, 0, args.extractor] \
                + [""] * len(FIELDS) + [0, err[:200]]

        writer.writerow(row)
        fcsv.flush()          # 边跑边落盘，中断也不丢已跑结果
        print("  [%d/%d] %-32s %s %-10s conf=%s %ss" % (
            len(rows) + 1, len(files), name[:32], "OK " if ok else "FAIL",
            str(vtype_str)[:10] if ok else "", round(conf, 3) if ok else "-",
            row[6] if ok else "-"))
        rows.append(row)

        # 准确率（有真值时）
        if ok and name in truth:
            tp = _truth_payload(truth[name])
            got = {k: ((r.get("fields") or {}).get(k) or {}).get("value", "") for k in FIELDS}
            for k in FIELDS:
                if k in NUM_FIELD:
                    a, b = _amount(got[k]), _amount(tp[k])
                    good = a is not None and b is not None and abs(a - b) < 0.02
                else:
                    g = str(got[k] or "")
                    good = g != "" and _norm(g) == _norm(tp[k])
                if good:
                    hit[k] += 1
            ti = truth[name].get("items", []) if isinstance(truth[name], dict) else []
            gi = r.get("items") or []
            if ti or gi:
                has_item += 1
                item_all += 1
                same = len(ti) == len(gi) and all(
                    (_amount(gi[i].get("quantity")) or -1) == (_amount(ti[i].get("quantity")) or -2)
                    and abs((_amount(gi[i].get("total_price")) or 0)
                            - (_amount(ti[i].get("total_price")) or -99)) < 0.02
                    for i in range(min(len(ti), len(gi))))
                if same:
                    item_full += 1

    fcsv.close()
    with open(fail_txt, "w", encoding="utf-8") as f:
        for line in failures:
            f.write(line + "\n")

    elapses_sorted = sorted(elapses)
    def pct(p):
        if not elapses_sorted:
            return 0.0
        i = min(len(elapses_sorted) - 1, int(len(elapses_sorted) * p))
        return round(elapses_sorted[i], 3)

    compared = sum(hit.values())
    n_ok = max(1, ok_cnt)
    summary = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "extractor": args.extractor,
        "total": len(files),
        "ok": ok_cnt,
        "failed": fail_cnt,
        "vtype_distribution": dict(vtype_cnt),
        "field_nonempty_rate": {k: round(field_cnt[k] / n_ok, 4) for k in FIELDS},
        "field_accuracy_vs_truth": (
            {k: round(hit[k] / n_ok, 4) if compared else None for k in FIELDS}
            if truth else None),
        "items_full_match": "%d/%d" % (item_full, item_all) if has_item else None,
        "elapsed": {
            "sum": round(sum(elapses), 2),
            "avg": round(sum(elapses) / n_ok, 3),
            "p50": pct(0.5), "p90": pct(0.9), "max": round(max(elapses), 3) if elapses else 0,
            "wall": round(time.time() - t_all, 2),
        },
        "throughput": "%s 张/分钟" % round(len(files) / ((time.time() - t_all) / 60), 1)
        if (time.time() - t_all) > 0 else None,
    }
    with open(sum_json, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    if args.detail:
        with open(os.path.join(outdir, "results_detail.json"), "w", encoding="utf-8") as f:
            json.dump(details, f, ensure_ascii=False)

    print("\n=== 汇总 ===")
    print("总数 %d | 成功 %d | 失败 %d | 总耗时 %.1fs | %s" % (
        len(files), ok_cnt, fail_cnt, time.time() - t_all, summary["throughput"]))
    print("票种分布：%s" % json.dumps(summary["vtype_distribution"], ensure_ascii=False))
    print("字段非空率：%s" % json.dumps(summary["field_nonempty_rate"], ensure_ascii=False))
    if truth:
        print("字段准确率（对真值）：%s" % json.dumps(summary["field_accuracy_vs_truth"], ensure_ascii=False))
        if summary["items_full_match"]:
            print("明细：数量与金额全对 %s" % summary["items_full_match"])
    print("耗时：avg %.2fs / p90 %.2fs / max %.2fs" % (
        summary["elapsed"]["avg"], summary["elapsed"]["p90"], summary["elapsed"]["max"]))
    print("输出：%s" % outdir)


if __name__ == "__main__":
    main()
