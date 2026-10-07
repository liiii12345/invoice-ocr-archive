# -*- coding: utf-8 -*-
"""数电票（全面数字化电子发票）XML 原生解析 —— 纯本地、仅标准库。

为什么做：OCR 是「看图猜字」，对数电票这类有标准结构化 XML 原件的票据，
直接读 XML 比 OCR 更准、更快、且自带税务数字签名可验真。
本项目脱敏复刻版：解析逻辑真实可用，签名验真复用 compliance_provider._xml_verify
（Mock 用本地密钥、Real 用税务根证书，算法同一份）。

兼容两种格式（都不依赖标签顺序 / 命名空间前缀）：
  ① 本仓库「示意」格式（compliance_provider 生成，NS=urn:cn:std:evoucher:invoice:2025）
     InvoiceBasicInfo / SellerInfo / BuyerInfo / InvoiceDetails / Summary
  ② 真实数电票常见标签（不同版式标签名有差异，按候选名模糊匹配）：
     InvoiceNumber / InvoiceDate / SellerName / BuyerName / TotalAmount / TaxAmount …

输出与 invoice_extract.extract 同形：{fields:{key:{value}}, _items:[...]}，
供 run_ocr 直接包装成统一结构，前端复用同一套字段渲染。
"""
import re
import xml.etree.ElementTree as ET

# 候选标签（local name，小写）→ 规范字段
FIELD_MAP = {
    "invoice_number": ["invoicenumber", "fphm", "fph"],
    "invoice_code":   ["invoicecode", "fpdm"],
    "invoice_date":   ["invoicedate", "kprq", "issuedate"],
    "seller_name":    ["sellername", "xfmc", "seller", "xfsmc"],
    "seller_taxid":   ["sellertaxid", "xfsh", "xftaxno", "sellerid"],
    "client_name":    ["buyername", "gfmc", "buyer", "gfsmc", "clientname"],
    "client_taxid":   ["buyertaxid", "gfsh", "gftaxno", "buyerid", "clienttaxid"],
    "total":          ["totalamount", "total", "jshj", "priceandtaxtotal",
                       "amountincludetax", "hjje"],
    "tax":            ["taxamount", "tax", "se", "hjse", "taxfee"],
    "tax_rate":       ["taxrate", "sl", "taxratename"],
    "remark":         ["remark", "bz"],
}
# 反向：local name → 规范字段
_TO_FIELD = {}
for _f, _cs in FIELD_MAP.items():
    for _c in _cs:
        _TO_FIELD[_c] = _f

# 明细容器与子字段的候选 local name
ITEM_CONTAINERS = ["item", "commodity", "detail", "row", "mx", "invoiceitem", "goods"]
ITEM_NAME = ["itemname", "name", "goodsname", "xmmc", "description", "commodityname"]
ITEM_QTY = ["quantity", "qty", "count", "sl", "num", "amount"]
ITEM_PRICE = ["price", "unitprice", "dj", "dwjg"]
ITEM_AMOUNT = ["amount", "total", "je", "xmjje", "money", "lineamount", "networth", "grossworth"]
ITEM_TAX = ["taxamount", "tax", "se", "xmse"]


def _local(tag):
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _text(el):
    t = (el.text or "").strip()
    return t


def _child_text(parent, names):
    """在 parent 的直接子节点里按候选 local name 取首个非空文本。"""
    for c in parent:
        if _local(c.tag).lower() in names and (c.text or "").strip():
            return c.text.strip()
    return ""


def _collect(root):
    """遍历所有元素，返回 {local_lower: [texts]}（同名多个值都收，取首个非空）。"""
    bag = {}
    for el in root.iter():
        ln = _local(el.tag).lower()
        t = _text(el)
        if t:
            bag.setdefault(ln, t)
    return bag


def _extract_fields(bag):
    out = {}
    for f, cs in FIELD_MAP.items():
        for c in cs:
            if c in bag:
                out[f] = bag[c]
                break
    return out


def _extract_items(root):
    """找重复出现的明细容器节点，提取 名称/数量/单价/金额/税额。"""
    items = []
    # 优先：直接找 ITEM_CONTAINERS 标签的节点
    for el in root.iter():
        ln = _local(el.tag).lower()
        if ln in ITEM_CONTAINERS:
            sub = {_local(c.tag).lower(): _text(c) for c in el.iter() if c is not el}
            name = next((sub[k] for k in ITEM_NAME if k in sub), "")
            qty = next((sub[k] for k in ITEM_QTY if k in sub), "")
            price = next((sub[k] for k in ITEM_PRICE if k in sub), "")
            amt = next((sub[k] for k in ITEM_AMOUNT if k in sub), "")
            tax = next((sub[k] for k in ITEM_TAX if k in sub), "")
            if name or amt:
                items.append({
                    "description": name,
                    "quantity": _money(qty) if qty else "",
                    "unit_price": _money(price) if price else "",
                    "total_price": _money(amt) if amt else "",
                    "tax": _money(tax) if tax else "",
                })
    if items:
        return items
    # 退而求其次：在 InvoiceDetails / Commodities 下找 Item/Commodity 子节点
    for el in root.iter():
        ln = _local(el.tag).lower()
        if ln in ("invoicedetails", "commodities", "details", "goodslist", "mxlist"):
            for child in el:
                cln = _local(child.tag).lower()
                if cln in ITEM_CONTAINERS or cln in ("commodity", "good"):
                    sub = {_local(c.tag).lower(): _text(c) for c in child}
                    name = next((sub[k] for k in ITEM_NAME if k in sub), "")
                    amt = next((sub[k] for k in ITEM_AMOUNT if k in sub), "")
                    qty = next((sub[k] for k in ITEM_QTY if k in sub), "")
                    if name or amt:
                        items.append({
                            "description": name,
                            "quantity": _money(qty) if qty else "",
                            "total_price": _money(amt) if amt else "",
                        })
    return items


def _money(x):
    if x is None:
        return ""
    s = re.sub(r"[^0-9.,\-]", "", str(x))
    if not s:
        return ""
    try:
        return "%.2f" % float(s)
    except ValueError:
        return s


def parse(xml_text):
    """解析数电票 XML → {fields:{k:{value}}, _items, source, ok, error}。

    fields 用 {key:{value}} 包装，与 invoice_extract 同形，但 evidence=None
    （原生解析无需图上描边；前端按 source=native_xml 渲染时跳过证据图）。
    """
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        return {"ok": False, "error": "XML 解析失败：%s" % e, "fields": {}, "_items": []}

    bag = _collect(root)
    flat = _extract_fields(bag)
    # 销售方 / 购买方名称、税号常包在 SellerInfo/BuyerInfo 下，需定向取（不能靠扁平候选，
    # 否则 Name 会同时命中买卖方）。
    for el in root.iter():
        ln = _local(el.tag).lower()
        if ln in ("sellerinfo", "xfxx", "seller"):
            nm = _child_text(el, ["name", "xfmc", "sellername", "mc"])
            if nm:
                flat["seller_name"] = nm
            tid = _child_text(el, ["taxid", "sh", "taxno", "xfsh"])
            if tid:
                flat["seller_taxid"] = tid
        if ln in ("buyerinfo", "gfxx", "buyer", "clientinfo"):
            nm = _child_text(el, ["name", "gfmc", "buyername", "clientname", "mc"])
            if nm:
                flat["client_name"] = nm
            tid = _child_text(el, ["taxid", "sh", "taxno", "gfsh", "clienttaxid"])
            if tid:
                flat["client_taxid"] = tid
    items = _extract_items(root)

    fields = {k: {"value": v, "evidence": None, "source": "native_xml", "status": "native"}
              for k, v in flat.items() if v != ""}
    # 保证前端字段表存在的键都有占位（空值也呈现，保持与 OCR 版结构一致）
    for k in ("invoice_number", "invoice_date", "seller_name", "client_name",
              "tax", "total", "invoice_code", "seller_taxid", "client_taxid", "tax_rate"):
        fields.setdefault(k, {"value": "", "evidence": None, "source": "native_xml",
                              "status": "native"})

    return {"ok": True, "source": "native_xml", "fields": fields, "_items": items,
            "has_items": bool(items)}


def _selftest():
    """用一段「示意」数电票 XML 验证解析（不依赖任何保密数据 / 不依赖 cryptography）。"""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:cn:std:evoucher:invoice:2025">
  <InvoiceBasicInfo>
    <InvoiceCode>011002100311</InvoiceCode>
    <InvoiceNumber>25117000000123456789</InvoiceNumber>
    <InvoiceDate>2026-03-15</InvoiceDate>
    <InvoiceTypeName>电子发票（增值税专用发票）</InvoiceTypeName>
  </InvoiceBasicInfo>
  <SellerInfo><Name>示例销售方科技有限公司</Name><Address>示例市示例路 1 号</Address></SellerInfo>
  <BuyerInfo><Name>示例采购方有限公司</Name><Address>示例市采购路 2 号</Address></BuyerInfo>
  <InvoiceDetails>
    <Item><ItemName>增值税发票防伪系统 V2.0</ItemName><Quantity>2</Quantity><Amount>1000.00</Amount></Item>
    <Item><ItemName>技术服务费</ItemName><Quantity>1</Quantity><Amount>500.00</Amount></Item>
  </InvoiceDetails>
  <Summary><TaxAmount>195.00</TaxAmount><TotalAmount>1695.00</TotalAmount></Summary>
</Invoice>"""
    r = parse(xml)
    ok = 0
    total = 0

    def check(name, cond, extra=""):
        nonlocal ok, total
        total += 1
        ok += bool(cond)
        print("  %s %s%s" % ("✓" if cond else "✗", name, ("  " + extra) if extra else ""))

    check("1. 解析成功", r["ok"], r.get("error", ""))
    check("2. 发票号码提取", r["fields"]["invoice_number"]["value"] == "25117000000123456789")
    check("3. 开票日期提取", r["fields"]["invoice_date"]["value"] == "2026-03-15")
    check("4. 销售方提取", "示例销售方" in r["fields"]["seller_name"]["value"])
    check("5. 购买方提取", "示例采购方" in r["fields"]["client_name"]["value"])
    check("6. 价税合计提取", r["fields"]["total"]["value"] == "1695.00",
          r["fields"]["total"]["value"])
    check("7. 税额提取", r["fields"]["tax"]["value"] == "195.00")

    items = r["_items"]
    check("8. 明细 2 行", len(items) == 2, "got %d" % len(items))
    if items:
        check("9. 明细名称", "增值税发票防伪系统" in items[0]["description"])
        check("10. 明细金额", items[0]["total_price"] == "1000.00", items[0]["total_price"])
        check("11. 明细数量", items[0]["quantity"] == "2.00", items[0]["quantity"])

    # 真实数电票常见标签形式（不同命名）也应能解析
    xml2 = """<?xml version="1.0"?>
<FP><FPHM>24417000000555555555</FPHM><KPRQ>2026-05-20</KPRQ>
<XFMC>另一家销售公司</XFMC><GFMC>某采购公司</GFMC>
<JSHJ>1130.00</JSHJ><HJJE>1000.00</HJJE><HJSE>130.00</HJSE>
<MX><XM><XMMC>办公用品</XMMC><SL>5</SL><XMJJE>1000.00</XMJJE></XM></MX></FP>"""
    r2 = parse(xml2)
    check("12. 真实标签形式可解析", r2["ok"] and r2["fields"]["invoice_number"]["value"]
          == "24417000000555555555", r2.get("error", ""))
    check("13. 真实标签价税合计", r2["fields"]["total"]["value"] == "1130.00",
          r2["fields"]["total"]["value"])
    check("14. 真实标签明细", len(r2["_items"]) == 1 and r2["_items"][0]["description"] == "办公用品")

    print("-" * 70)
    print("数电票 XML 解析自测通过 %d / %d" % (ok, total))
    return ok == total


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if _selftest() else 1)
