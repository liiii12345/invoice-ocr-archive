#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成控制台演示用的【合成样例数据】（synthetic_demo_data.json）。

为什么全合成：
  演示数据里绝不出现真实发票影像 / 真实开票方与购买方 / 真实金额。
  这里按与真实 ingest 完全一致的 schema 造 3 条样例：票面正文由脚本拼出，
  预览图由 PIL 现画（占位文档版式 + 「合成样例」水印），不含任何扫描件。

用法：
    python tools/make_demo_data.py            # 输出 data/synthetic_demo_data.json
"""
import os, json, hashlib, datetime, base64, io, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "data", "synthetic_demo_data.json")

SELLER = "示例科技有限公司"          # 占位供方
BUYER = "示例采购中心"               # 占位需方
SELLER_TIN = "910000000000000000S"   # 占位纳税人识别号
BUYER_TIN = "910000000000000000B"
LLM_MODEL = "qwen-vl-ocr-latest"


# ---------------------------------------------------------------- 预览图（现画）
def _font(size):
    """优先用系统中文字体，没有就退回 PIL 默认字体。"""
    try:
        from PIL import ImageFont
        for p in (r"C:\Windows\Fonts\msyh.ttc", "/System/Library/Fonts/PingFang.ttc",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    pass
        return ImageFont.load_default()
    except Exception:
        return None


def _text(d, xy, s, fill, font, anchor="la"):
    if s is None:
        return
    d.text(xy, s, fill=fill, font=font, anchor=anchor)


def make_preview(w=420, h=560, code="SYN-0001"):
    """画一张占位「文档页」当预览图——不是任何真实票据的扫描件。"""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), (247, 248, 250))
    d = ImageDraw.Draw(img)
    f_t = _font(22)      # 标题
    f_s = _font(14)      # 正文

    d.rectangle([0, 0, w, 74], fill=(47, 111, 235))            # 页眉色条
    _text(d, (24, 24), "SYNTHETIC SAMPLE", (255, 255, 255), f_t)
    _text(d, (24, 52), "合成样例 · 非真实票据", (226, 235, 255), f_s)

    d.rectangle([24, 94, w - 24, h - 24], fill=(255, 255, 255))
    d.rectangle([24, 94, w - 24, h - 24], outline=(214, 217, 222))

    y = 120
    _text(d, (40, y), "电子发票（普通发票）", (31, 35, 41), f_t); y += 34
    _text(d, (40, y), "发票代码 %s" % code, (90, 96, 105), f_s); y += 24
    _text(d, (40, y), "销售方：%s" % SELLER, (90, 96, 105), f_s); y += 22
    _text(d, (40, y), "购买方：%s" % BUYER, (90, 96, 105), f_s); y += 30

    # 表格骨架：表头 + 6 行写入线 + 合计行
    d.line([40, y, w - 40, y], fill=(228, 231, 236)); y += 12
    for i in range(6):
        d.line([40, y, w - 40, y], fill=(238, 240, 244)); y += 14
        d.line([40, y, 40 + (150 if i % 2 else 90), y + 2], fill=(196, 201, 209))
        y += 18
    d.line([40, y, w - 40, y], fill=(228, 231, 236)); y += 14
    d.line([40, y - 6, w - 40, y - 6], fill=(228, 231, 236))

    # 斜向水印，明确「这不是真票」
    try:
        d.text((w // 2, h - 130), "合成样例 / SAMPLE",
               fill=(214, 220, 232), font=_font(30), anchor="mm")
    except Exception:
        pass

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def _mk(idx, code, title, summary, items, total, tax, date, ocr_s, llm_s, conf):
    net = round(total - tax, 2)
    rows = ["| 项 | 名称 | 数量 | 单价 | 金额 |", "| :-- | :--- | --: | --: | --: |"]
    for n, (name, qty, price) in enumerate(items, 1):
        amt = round(price * qty, 2)
        rows.append("| %d | %s | %g | %.2f | %.2f |" % (n, name, qty, price, amt))
    rows += ["| | **合计（不含税）** | | | %.2f |" % net,
             "| | **税额** | | | %.2f |" % tax,
             "| | **价税合计** | | | **%.2f** |" % total]
    md = ("# %s\n\n> 合成样例：由 tools/make_demo_data.py 生成，"
          "不含任何真实票据信息。\n\n销售方：%s（%s）　购买方：%s（%s）　开票日期：%s\n\n%s\n"
          % (title, SELLER, SELLER_TIN, BUYER, BUYER_TIN, date, "\n".join(rows)))

    return {
        "id": idx,
        "sha": _sha("%s|%s" % (code, title)),
        "doc_type": "发票票据",
        "title": title,
        "amount_text": "¥%s" % format(total, ",.2f"),
        "confidence": conf,
        "ingested_at": date + "T09:26:%02dZ" % (idx * 17 % 60),
        "ocr_s": round(ocr_s, 2),
        "llm_s": round(llm_s, 2),
        "completeness": None,
        "details": {
            "doc_type": "发票票据",
            "title": title,
            "summary": summary,
            "parties": [{"role": "seller", "name": SELLER},
                        {"role": "buyer", "name": BUYER}],
            "primary_date": date,
            "primary_amount_text": "¥%s" % format(total, ",.2f"),
            "primary_amount_value": total,
            "computed_total_value": total,
            "monthly_property_fee_value": None,
            "monthly_property_fee_text": None,
            "key_dates": [date],
            "amounts": [
                {"label": "价税合计", "text": "¥%s" % format(total, ",.2f"),
                 "value": total, "unit": None, "is_total_component": True,
                 "is_installment": False, "period_start": None, "period_end": None,
                 "evidence": "价税合计 %.2f" % total},
                {"label": "不含税金额", "text": "¥%s" % format(net, ",.2f"),
                 "value": net, "unit": None, "is_total_component": False,
                 "is_installment": False, "period_start": None, "period_end": None,
                 "evidence": "合计 %.2f" % net},
                {"label": "税额", "text": "¥%s" % format(tax, ",.2f"),
                 "value": tax, "unit": None, "is_total_component": False,
                 "is_installment": False, "period_start": None, "period_end": None,
                 "evidence": "税额 %.2f" % tax},
            ],
            "seals": [],
            "fields": [{"key": "发票代码", "value": code},
                       {"key": "销售方", "value": SELLER},
                       {"key": "购买方", "value": BUYER}],
            "person_identities": [],
            "obligations": [],
            "sub_agreements": [],
            "completeness": None,
            "identity_issues": [],
            "raw_evidence": {"markdown_rows": len(rows)},
            "llm_model": LLM_MODEL,
            "llm_usage": None,
            "extraction_error": None,
            "field_verdicts": [],
            "fusion_overall_confidence": conf,
        },
        "raw_text": md,
        "markdown": md,
        "preview_img": make_preview(code=code),
    }


def build():
    docs = [
        _mk(1, "SYN0001", "办公设备采购发票（合成样例）",
            "采购笔记本电脑 2 台、外接显示器 3 台，价税合计 ¥%s" % format(5584.00, ",.2f"),
            [("笔记本电脑 14 寸（示例型号）", 2, 2200.00),
             ("外接显示器 27 寸（示例型号）", 3, 600.00),
             ("键盘（示例）", 5, 99.00)],
            5584.00, 384.00, "2026-01-06", 18.61, 13.47, 0.62),
        _mk(2, "SYN0002", "会议服务费发票（合成样例）",
            "第三方会议场地与设备租赁服务，价税合计 ¥%s" % format(3460.00, ",.2f"),
            [("会议场地租赁", 2, 1200.00), ("音响设备租赁", 1, 680.00)],
            3460.00, 420.00, "2026-01-08", 22.32, 19.53, 0.70),
        _mk(3, "SYN0003", "装饰材料采购发票（合成样例）",
            "采购办公区装饰用板材与五金件，价税合计 ¥%s" % format(8750.50, ",.2f"),
            [("装饰板材（示例）", 40, 168.00), ("五金件套装（示例）", 12, 45.03)],
            8750.50, 1150.50, "2026-01-09", 20.79, 20.44, 0.70),
    ]
    payload = {"docs": docs, "total": len(docs)}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print("wrote", OUT, os.path.getsize(OUT), "bytes, docs =", len(docs))


if __name__ == "__main__":
    build()
