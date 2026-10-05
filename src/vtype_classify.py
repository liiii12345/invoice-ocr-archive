# -*- coding: utf-8 -*-
"""
票种判定器 —— 基于 OCR 文本 + 版面特征的内容分类（不是文件名哈希）

设计：
1. 维护「票种画像库」：每种票的特征关键词（必须命中 / 任一命中）、版面结构特征，
   以及**该票种对应的归档合规要求**（法定原件格式、是否需验签、走哪个查验平台）。
2. 分类时对每个画像打分，取最高分；置信度 = 命中证据强度。
3. 置信度低或无匹配 → 返回 unknown，交由人工判定（不瞎猜）。

关键：票种判定不只是「贴个标签」，它决定后续的合规要求。
所以画像库里每条都带 requirements。
"""
import re

# ── 票种画像库 ──
# must_all: 必须全部出现的关键词（用于强特征）
# any_of:   任一出现即可加分
# layout:   版面结构特征（需多个同时出现）
PROFILES = [
    {
        "code": "cn-e-spec", "name": "全电发票·增值税专用发票", "short": "数电专票",
        "must_all": [], "any_of": ["增值税专用发票", "数电票（增值税专用发票）", "电子发票（增值税专用发票）"],
        "layout": ["价税合计", "税额"], "region": "CN",
        "req": {"legal_original": "XML（含税务数字签名）", "signature_check": True,
                "verify": "全国增值税发票查验平台 / 税务数字账户",
                "archive": "财会〔2020〕6号 + 财会〔2025〕9号 + DA/T 94（四性检测）",
                "need_xml": True},
    },
    {
        "code": "cn-e-gen", "name": "全电发票·增值税普通发票", "short": "数电普票",
        "must_all": [], "any_of": ["增值税普通发票", "电子发票（普通发票）", "数电票（普通发票）"],
        "layout": ["价税合计"], "region": "CN",
        "req": {"legal_original": "XML（含税务数字签名）", "signature_check": True,
                "verify": "全国增值税发票查验平台 / 税务数字账户",
                "archive": "财会〔2020〕6号 + 财会〔2025〕9号 + DA/T 94（四性检测）",
                "need_xml": True},
    },
    {
        "code": "cn-p-spec", "name": "纸质增值税专用发票", "short": "纸质专票",
        "must_all": [], "any_of": ["增值税专用发票"], "layout": ["发票代码", "发票号码", "价税合计"],
        "region": "CN", "exclude_if": ["电子发票"],
        "req": {"legal_original": "纸质原件（+ 影像）", "signature_check": False,
                "verify": "全国增值税发票查验平台",
                "archive": "纸质档案 + 电子影像；如以打印件入账需同时留存 XML",
                "need_xml": False},
    },
    {
        "code": "cn-train", "name": "铁路电子客票", "short": "铁路客票",
        "must_all": [], "any_of": ["铁路电子客票", "中国铁路", "12306"],
        "layout": ["车次", "出发站"], "region": "CN",
        "req": {"legal_original": "XML / OFD（电子客票）", "signature_check": True,
                "verify": "铁路12306 电子发票服务平台",
                "archive": "财会〔2025〕9号（电子凭证会计数据标准）", "need_xml": True},
    },
    {
        "code": "cn-flight", "name": "航空运输电子客票行程单", "short": "机票行程单",
        "must_all": [], "any_of": ["航空运输电子客票行程单", "电子客票行程单"],
        "layout": ["航班号", "承运人"], "region": "CN",
        "req": {"legal_original": "XML / OFD", "signature_check": True,
                "verify": "民航电子行程单查验", "archive": "财会〔2025〕9号", "need_xml": True},
    },
    {
        "code": "cn-taxi", "name": "出租车票 / 打车发票", "short": "出租车票",
        "must_all": [], "any_of": ["出租汽车", "出租车发票", "打车"], "layout": ["单价", "里程"],
        "region": "CN",
        "req": {"legal_original": "纸质原件 / 电子发票", "signature_check": False,
                "verify": "视票据形式", "archive": "内部报销凭证管理", "need_xml": False},
    },
    {
        "code": "cn-receipt", "name": "财政电子票据", "short": "财政票据",
        "must_all": [], "any_of": ["财政电子票据", "非税收入"],
        "layout": ["票据代码", "缴款人"], "region": "CN",
        "req": {"legal_original": "XML", "signature_check": True,
                "verify": "财政电子票据查验平台", "archive": "财会〔2025〕9号", "need_xml": True},
    },
    {
        "code": "intl-commercial", "name": "英文商业发票（含 VAT）", "short": "英文商发",
        "must_all": ["vat"], "any_of": ["invoice no", "invoice number", "gross worth", "net worth"],
        "layout": ["items", "summary"], "region": "INTL",
        "req": {"legal_original": "发票影像（PDF / 图片）", "signature_check": False,
                "verify": "不适用（非国内税务票据；可按合同/供应商系统核对）",
                "archive": "企业内部凭证 + 合同附件管理（不适用国内电子会计档案强制标准）",
                "need_xml": False},
    },
    {
        "code": "intl-generic", "name": "通用商业发票（无 VAT）", "short": "通用商发",
        "must_all": [], "any_of": ["invoice", "commercial invoice", "bill to", "ship to"],
        "layout": [], "region": "INTL",
        "req": {"legal_original": "发票影像（PDF / 图片）", "signature_check": False,
                "verify": "不适用", "archive": "企业内部凭证管理", "need_xml": False},
    },
]


def _norm(s):
    return re.sub(r"\s+", " ", str(s or "")).lower()


def classify(text, lines=None):
    """text: OCR 全文；lines: 可选的 OCR 行（用于版面特征）
    返回 {code, name, short, confidence, evidence, candidates, requirements, region}"""
    t = _norm(text)
    if lines:
        t = t + " " + _norm(" ".join(str(l.get("text", "")) for l in lines))

    scored = []
    for p in PROFILES:
        # 排除条件
        if any(x.lower() in t for x in p.get("exclude_if", [])):
            continue
        must = p.get("must_all", [])
        if must and not all(m.lower() in t for m in must):
            continue
        hits = [m for m in must if m.lower() in t]
        hits += [a for a in p.get("any_of", []) if a.lower() in t]
        lay = [x for x in p.get("layout", []) if x.lower() in t]
        if not hits and not lay:
            continue
        # 评分：any_of 关键词权重高，版面特征权重低
        expected = max(1, len(p.get("any_of", [])) or len(p.get("layout", [])))
        kw_score = min(1.0, len(hits) / max(1, len(p.get("any_of", [])) or expected))
        lay_score = (len(lay) / len(p["layout"])) if p.get("layout") else 0
        score = 0.75 * kw_score + 0.25 * lay_score
        if not p.get("any_of"):
            score = 0.5 * (len(lay) / max(1, len(p["layout"]))) if p.get("layout") else 0
        scored.append({"profile": p, "score": round(score, 3),
                       "hits": sorted(set(hits)), "layout_hits": sorted(set(lay))})
    if not scored:
        return {"code": "unknown", "name": "未识别票种", "short": "未知", "confidence": 0.0,
                "evidence": [], "candidates": [], "region": "",
                "requirements": {"legal_original": "待人工判定", "signature_check": None,
                                 "verify": "待人工判定", "archive": "待人工判定", "need_xml": None}}

    scored.sort(key=lambda x: -x["score"])
    top = scored[0]
    ident = [h for h in top["hits"] if h in top["profile"].get("any_of", [])]
    conf = min(0.99, 0.55 + 0.45 * top["score"]) if ident else min(0.60, top["score"])
    # 与第二名差距过小 → 降低置信度（易混淆）
    if len(scored) > 1 and top["score"] - scored[1]["score"] < 0.2:
        conf = min(conf, 0.62)
    return {
        "code": top["profile"]["code"], "name": top["profile"]["name"],
        "short": top["profile"]["short"], "confidence": round(conf, 3),
        "region": top["profile"].get("region", ""),
        "evidence": top["hits"][:6], "layout_evidence": top["layout_hits"][:4],
        "requirements": top["profile"]["req"],
        "candidates": [{"code": s["profile"]["code"], "name": s["profile"]["name"],
                        "score": s["score"]} for s in scored[:3]],
    }


# ── 单元测试：用合成文本验证分类器能区分各票种 ──
CASES = [
    ("数电专票（含关键词）", "电子发票（增值税专用发票） 发票号码 12345678 开票日期 2026年03月15日 "
        "购买方名称 某某公司 价税合计（小写）¥10000.00 税率 13% 税额 1300.00", "cn-e-spec"),
    ("数电普票（含关键词）", "电子发票（普通发票） 发票号码 87654321 价税合计 ¥500.00 税额 45.45", "cn-e-gen"),
    ("纸质专票（无「电子发票」字样）", "增值税专用发票 发票代码 011002100311 发票号码 12345678 "
        "开票日期 2026年03月15日 价税合计 ¥10000.00 税额 1300.00", "cn-p-spec"),
    ("铁路电子客票", "中国铁路 铁路电子客票 车次 G1234 出发站 北京南 到达站 上海虹桥 "
        "乘车日期 2026-03-15 票价 ¥553.00 12306", "cn-train"),
    ("机票行程单", "航空运输电子客票行程单 航班号 CA1234 承运人 中国国际航空 票价 1200.00", "cn-flight"),
    ("财政电子票据", "财政电子票据 票据代码 110000 非税收入 缴款人 张三 金额 200.00", "cn-receipt"),
    ("出租车票", "出租汽车专用发票 单价 2.60 里程 12.4 金额 32.00", "cn-taxi"),
    ("英文商业发票（本数据集）", "Invoice no: 12847181 Date of issue: 03/03/2012 Seller: Fitzpatrick and Sons "
        "Client: Duncan PLC ITEMS No. Description Qty Net price Net worth VAT [%] Gross worth "
        "SUMMARY VAT [%] VAT Net worth Gross worth Total $ 6 860,45", "intl-commercial"),
    ("仅凭版面特征（无法判种）", "发票代码 011002100311 发票号码 12345678 价税合计 ¥100.00", None),
]


def _selftest():
    ok = 0
    print("=" * 78)
    print("票种分类器单元测试（合成文本）")
    print("=" * 78)
    for name, txt, expect in CASES:
        r = classify(txt)
        if expect is None:
            good = r["confidence"] < 0.60          # 无法判种时应低置信度、交人工
        else:
            good = r["code"] == expect and r["confidence"] >= 0.60
        ok += good
        print("  %s %-24s → %-10s conf=%.2f  XML=%-5s 验签=%s"
              % ("✓" if good else "✗", name, r["short"], r["confidence"],
                 r["requirements"]["need_xml"], r["requirements"]["signature_check"]))
        if not good:
            print("      期望 %s；候选=%s" % (expect, r["candidates"]))
        else:
            print("      证据关键词: %s%s" % (r["evidence"],
                  ("  版面:" + str(r["layout_evidence"])) if r["layout_evidence"] else ""))
    print("-" * 78)
    print("通过 %d / %d" % (ok, len(CASES)))
    return ok == len(CASES)


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if _selftest() else 1)
