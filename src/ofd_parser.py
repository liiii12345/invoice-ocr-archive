# -*- coding: utf-8 -*-
"""数电票 OFD 原生解析 —— 纯本地、仅标准库（zipfile）。

OFD 是基于 XML 的版式文件容器（本质是 zip）。数电票 OFD 内通常把结构化
「全电发票 XML 原件」打包进去（放在 Doc_*/Document.xml 的 <CustomTags> 或独立文件）。
本解析器：
  ① 解包 OFD（zip）
  ② 在压缩树里定位含结构化发票数据的 XML 片段（按命名空间 / 关键标签模糊匹配）
  ③ 原样返回该 XML 文本，交给 einvoice_xml.parse 做字段解析 + 验签
  ④ 标注内嵌签名文件是否存在（OFD 原生签名走国密 GM/T 0031，验真单独列为路线项）

守真实性红线：找不到内嵌 XML 就如实返回 None，绝不编造字段；
OFD 原生签名不在此强行验（国密 SM2 验签补全见完善路线），只标记存在。
"""
import re
import io
import zipfile

# 用来判断「这段 XML 是不是发票结构化数据」的标记
_MARKERS = ["urn:cn:std:evoucher:invoice:2025", "invoicenumber", "发票号码",
            "价税合计", "电子发票", "fphm", "jshj", "fpdm", "<invoice", "invoicedate"]


def extract_xml(ofd_bytes):
    """从 OFD 字节里抽取结构化发票 XML 文本。

    返回 (xml_text_or_None, info)。info 含 ofd 是否合法、文件清单、命中路径、签名文件清单。
    """
    info = {"ofd": True, "files": [], "found": None, "via": None, "signatures": [], "error": ""}
    try:
        z = zipfile.ZipFile(io.BytesIO(ofd_bytes))
    except Exception as e:  # noqa
        info["ofd"] = False
        info["error"] = "不是合法的 OFD / zip 容器：%s" % e
        return None, info

    names = z.namelist()
    info["files"] = names
    info["signatures"] = [n for n in names if re.search(r"signature\.xml$", n, re.I)]

    # ① 优先：任何 xml 中含发票结构化标记 → 直接返回整段
    for n in names:
        if not n.lower().endswith(".xml"):
            continue
        try:
            data = z.read(n).decode("utf-8", "ignore")
        except Exception:  # noqa
            continue
        if any(m.lower() in data.lower() for m in _MARKERS):
            info["found"] = n
            info["via"] = "direct"
            return data, info

    # ② 退一步：找 Document.xml 里 <CustomTags>/<Annotations> 内嵌的 XML 片段
    for n in names:
        if re.search(r"document\.xml$", n, re.I):
            data = z.read(n).decode("utf-8", "ignore")
            m = re.search(r"<(?:customtags|annotations)[^>]*>(.*?)</(?:customtags|annotations)>",
                          data, re.S | re.I)
            if m and m.group(1).strip():
                info["found"] = n
                info["via"] = "customtags"
                return m.group(1), info

    info["error"] = "OFD 内未找到结构化发票 XML（可能仅有版式，无内嵌数据原件）"
    return None, info


def _selftest():
    """造一个最小 OFD（zip）验证抽取，不依赖任何真实票 / 保密数据。"""
    import zipfile as _zf
    import tempfile
    import os as _os

    mock_xml = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:cn:std:evoucher:invoice:2025">
  <InvoiceBasicInfo><InvoiceCode>011002100311</InvoiceCode>
    <InvoiceNumber>25117000000999999999</InvoiceNumber><InvoiceDate>2026-06-01</InvoiceDate></InvoiceBasicInfo>
  <Summary><TaxAmount>13.00</TaxAmount><TotalAmount>113.00</TotalAmount></Summary>
</Invoice>"""
    # 最小 OFD：OFD.xml 声明 + Doc_0/Document.xml 用 CustomTags 包住结构化 XML
    ofd_xml = ('<?xml version="1.0"?><OFD><DocBody><DocRoot>Doc_0/Document.xml</DocRoot>'
               '</DocBody></OFD>')
    doc_xml = ('<?xml version="1.0"?><Document><CustomTags>%s</CustomTags></Document>'
               % mock_xml)
    sig_xml = '<?xml version="1.0"?><Signature>mock-ofd-signature</Signature>'

    buf = io.BytesIO()
    with _zf.ZipFile(buf, "w") as z:
        z.writestr("OFD.xml", ofd_xml)
        z.writestr("Doc_0/Document.xml", doc_xml)
        z.writestr("Signs/Sign_0/Signature.xml", sig_xml)
    raw = buf.getvalue()

    xml, info = extract_xml(raw)
    ok = 0
    total = 0

    def check(name, cond, extra=""):
        nonlocal ok, total
        total += 1
        ok += bool(cond)
        print("  %s %s%s" % ("✓" if cond else "✗", name, ("  " + extra) if extra else ""))

    check("1. OFD 合法且抽取到 XML", xml is not None, info.get("error", ""))
    check("2. 命中路径含 Document.xml", "Document.xml" in (info.get("found") or ""),
          info.get("found"))
    check("3. 内嵌 XML 含发票号码", xml is not None and "25117000000999999999" in xml)
    check("4. 识别到内嵌签名文件", len(info.get("signatures", [])) == 1,
          str(info.get("signatures")))
    check("5. 抽取方式命中（direct/customtags）", info.get("via") in ("direct", "customtags"),
          info.get("via"))

    # 非法 OFD → 如实返回 ofd=False
    bad, info2 = extract_xml(b"not a zip at all")
    check("6. 非法 OFD 如实返回 ofd=False", bad is None and info2["ofd"] is False)

    print("-" * 70)
    print("OFD 解析自测通过 %d / %d" % (ok, total))
    return ok == total


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if _selftest() else 1)
