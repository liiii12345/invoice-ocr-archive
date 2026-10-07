# -*- coding: utf-8 -*-
"""跨文档查重引擎 —— 纯本地、零依赖（仅标准库 sqlite3）。

用途：堵「重复报销」。每张识别/上传的发票，按
    （发票代码 + 发票号码 + 开票日期 + 价税合计）
生成不可逆指纹，登记进本地 SQLite 档案库；再次遇到同一指纹即报重复。

设计原则（守真实性红线）：
  · 只在字段齐全时生成指纹；字段不足就如实返回 skippable=True，绝不伪造「未重复」。
  · 本机运行、数据不出本机；库文件默认落在项目内 archive.db（已在 .gitignore 忽略上传）。
  · 注册与查询分离：register() 顺手查重，check() 供界面手动按票号复核。
  · 可一键清空（演示/测试污染），不删表结构。
"""
import os, sqlite3, hashlib, re, time

DB_PATH = os.environ.get("OCR_ARCHIVE_DB") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "archive.db")


def _norm_amount(x):
    if x is None:
        return ""
    s = re.sub(r"[^0-9.]", "", str(x))
    if not s:
        return ""
    try:
        return "%.2f" % float(s)
    except ValueError:
        return ""


def _norm_date(d):
    """尽量归一化成 YYYY-MM-DD；识别不了就原样返回（仍可作为指纹一部分）。"""
    if not d:
        return ""
    s = str(d).strip().replace("年", "-").replace("月", "-").replace("日", "")
    m = re.search(r"(\d{4})[-/.\s](\d{1,2})[-/.\s](\d{1,2})", s)
    if m:
        y, mo, da = m.groups()
        return "%s-%02d-%02d" % (y, int(mo), int(da))
    m2 = re.search(r"(\d{1,2})[-/.\s](\d{1,2})[-/.\s](\d{2,4})", s)
    if m2:
        a, b, c = m2.groups()
        if len(c) == 2:
            c = "20" + c
        # 月/日不分时尽量推断
        return "%s-%02d-%02d" % (c, int(a), int(b))
    return s


def fingerprint(rec):
    """rec: 扁平字段 dict（invoice_number/invoice_code/invoice_date/total）。
    返回 32 位指纹或 None（信息不足，无法生成）。"""
    num = str(rec.get("invoice_number") or "").strip()
    code = str(rec.get("invoice_code") or "").strip()
    date = _norm_date(rec.get("invoice_date") or "")
    amt = _norm_amount(rec.get("total") or "")
    if not (num or code) or not (date and amt):
        # 号码/代码 与 （日期+金额）至少各有一项，否则指纹区分度太低
        return None
    blob = "|".join([code, num, date, amt])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _flat(fields):
    """把 run_ocr 返回的 {key:{value,...}} 或扁平 {key:value} 都压成扁平 dict。"""
    out = {}
    for k, v in (fields or {}).items():
        if isinstance(v, dict):
            out[k] = v.get("value", "")
        else:
            out[k] = v
    return out


class DedupDB:
    def __init__(self, path=None):
        self.path = path or DB_PATH
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS invoices(
                id INTEGER PRIMARY KEY, fp TEXT, code TEXT, number TEXT, date TEXT,
                amount TEXT, seller TEXT, buyer TEXT, vtype TEXT, source TEXT,
                created TEXT)""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_fp ON invoices(fp)")
        self.conn.commit()

    def register(self, fields, vtype=None, source="upload"):
        """登记一张发票，并顺手查重。
        返回 {registered, dup, skippable, reason, existing?, fp?, record?}"""
        f = _flat(fields)
        fp = fingerprint(f)
        if not fp:
            return {"registered": False, "skippable": True,
                    "reason": "字段不足（需发票号码/代码 + 开票日期 + 价税合计），无法生成指纹",
                    "fp": None}
        row = self.conn.execute(
            "SELECT id,source,created,code,number,date,amount,seller,buyer,vtype FROM invoices WHERE fp=?",
            (fp,)).fetchone()
        if row:
            return {"registered": False, "dup": True, "skippable": False, "fp": fp,
                    "existing": {"id": row[0], "source": row[1], "created": row[2],
                                 "number": row[4], "date": row[5], "amount": row[6],
                                 "seller": row[7]}}
        cur = self.conn.execute(
            "INSERT INTO invoices(fp,code,number,date,amount,seller,buyer,vtype,source,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (fp, f.get("invoice_code", ""), f.get("invoice_number", ""),
             _norm_date(f.get("invoice_date", "")), _norm_amount(f.get("total", "")),
             f.get("seller_name", ""), f.get("client_name", ""), vtype or "",
             source, time.strftime("%Y-%m-%d %H:%M:%S")))
        self.conn.commit()
        return {"registered": True, "dup": False, "skippable": False, "fp": fp,
                "record": {"id": cur.lastrowid, "number": f.get("invoice_number", ""),
                           "amount": _norm_amount(f.get("total", ""))}}

    def check(self, number="", code="", date="", amount=""):
        """按票号/代码/日期/金额手动查重（不登记）。"""
        fp = fingerprint({"invoice_number": number, "invoice_code": code,
                          "invoice_date": date, "total": amount})
        if not fp:
            return {"checked": False, "reason": "信息不足，无法生成指纹", "hits": []}
        rows = self.conn.execute(
            "SELECT id,source,created,code,number,date,amount,seller,buyer FROM invoices WHERE fp=?",
            (fp,)).fetchall()
        hits = [{"id": r[0], "source": r[1], "created": r[2], "code": r[3],
                 "number": r[4], "date": r[5], "amount": r[6], "seller": r[7]}
                for r in rows]
        return {"checked": True, "dup": bool(hits), "fp": fp, "hits": hits}

    def list_all(self, limit=200):
        rows = self.conn.execute(
            "SELECT id,code,number,date,amount,seller,buyer,vtype,source,created FROM invoices"
            " ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "code": r[1], "number": r[2], "date": r[3], "amount": r[4],
                 "seller": r[5], "buyer": r[6], "vtype": r[7], "source": r[8],
                 "created": r[9]} for r in rows]

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]

    def clear(self):
        self.conn.execute("DELETE FROM invoices")
        self.conn.commit()


def _selftest():
    import tempfile, os as _os
    tmp = _os.path.join(tempfile.gettempdir(), "dedup_test_%d.db" % int(time.time() * 1000))
    db = DedupDB(tmp)
    ok = 0
    total = 0

    def check(name, cond, extra=""):
        nonlocal ok, total
        total += 1
        ok += bool(cond)
        print("  %s %s%s" % ("✓" if cond else "✗", name, ("  " + extra) if extra else ""))

    a = {"invoice_number": "12847181", "invoice_date": "2012-03-03",
         "total": "6860.45", "seller_name": "Fitzpatrick"}
    r1 = db.register(a, vtype="intl-commercial", source="upload")
    check("1. 首次登记成功", r1["registered"] and not r1["dup"], "fp=%s" % r1["fp"])

    # 完全相同 → 应判定重复
    r2 = db.register(a, vtype="intl-commercial", source="upload")
    check("2. 同号同金额 → 报重复", r2["dup"] and not r2["registered"],
          "命中 source=%s" % (r2.get("existing") or {}).get("source"))

    # 金额不同 → 不算重复（应新登记）
    r3 = db.register({**a, "total": "9999.00"}, source="upload")
    check("3. 同号不同金额 → 不重复（新登记）", r3["registered"] and not r3["dup"])

    # 字段不足 → 跳过，不伪造
    r4 = db.register({"invoice_number": "12847181"}, source="upload")
    check("4. 字段不足 → 如实跳过（不伪造未重复）", r4["skippable"] and not r4.get("registered"))

    # 手动查重
    c = db.check(number="12847181", date="2012-03-03", amount="6860.45")
    check("5. 手动查重命中", c["checked"] and c["dup"] and len(c["hits"]) == 1)
    c2 = db.check(number="12847181", date="2012-03-03", amount="0000.00")
    check("6. 金额错 → 查重不命中", c2["checked"] and not c2["dup"])

    check("7. 档案库共 2 条有效记录", db.count() == 2, "count=%d" % db.count())

    db.clear()
    check("8. 清空后归零", db.count() == 0)

    db.conn.close()
    _os.remove(tmp)
    print("-" * 70)
    print("查重引擎自测通过 %d / %d" % (ok, total))
    return ok == total


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if _selftest() else 1)
