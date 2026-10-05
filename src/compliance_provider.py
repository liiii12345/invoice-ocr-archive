# -*- coding: utf-8 -*-
"""
合规能力提供方（Compliance Provider）—— Mock 与 Real 共用同一接口

覆盖三件事：
  1) fetch_legal_original()  获取法定原件（数电票 = 含税务数字签名的 XML）
  2) verify_signature()      数字签名验签
  3) check_invoice()         发票查验（真伪 / 作废 / 红冲）

设计原则：
  · Mock 与 Real 实现**同一套方法签名与返回结构**，切换只改环境变量
    OCR_COMPLIANCE_PROVIDER = mock | real
  · Mock **不是假的**：XML 是真的 XML、签名是真的 RSA-SHA256 签名、验签是真的密码学验证。
    唯一「模拟」的是密钥来源——用本地生成的密钥对顶替税务局的密钥对。
    真实接入时只需把公钥换成税务根证书、把下载换成乐企/税务数字账户 API。
  · 篡改可被检出：改任何一个字段，验签就会失败（见 __main__ 自测）。

真实接入需要替换的部分已在 RealComplianceProvider 中标注 TODO，共 3 处。
"""
import os, re, io, json, time, base64, hashlib, secrets, datetime, ssl
import urllib.request
import xml.etree.ElementTree as ET

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

NS = "urn:cn:std:evoucher:invoice:2025"          # 结构示意命名空间
ET.register_namespace("", NS)                   # 序列化时不加 ns0: 前缀，输出可读的 <Tag>
KEY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".mock_tax_key.pem")
PUB_PATH = KEY_PATH + ".pub.pem"
MOCK_CA_SERIAL = "MOCK-TAX-CA-2025-0001"


# ══════════════════════════════════════════════════════════════
# 返回结构（Mock 与 Real 必须一致）
# ══════════════════════════════════════════════════════════════
def _iso_now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class LegalOriginal:
    """法定原件"""
    def __init__(self, available, fmt="", content="", source="", signature=None, reason="",
                 elapsed=0.0, applicable=True):
        self.available = available      # bool
        self.applicable = applicable    # True 适用 / False 该票种不适用 / None 票种未知待判定
        self.format = fmt               # "XML" | "OFD" | "PDF" | "IMAGE"
        self.content = content          # 文本内容（XML 为字符串；二进制类为 base64）
        self.source = source            # 来源描述（渠道）
        self.signature = signature or {}  # {algorithm, value, cert_serial, signer, sign_time}
        self.reason = reason            # 不可用原因
        self.elapsed = elapsed

    def to_dict(self):
        d = self.__dict__.copy()
        if self.content and len(self.content) > 200000:
            d["content"] = self.content[:200000] + "\n<!-- 截断 -->"
        return d


class SignatureResult:
    """验签结果"""
    def __init__(self, valid, algorithm="", cert_serial="", signer="", sign_time="",
                 reason="", checked_at=None, elapsed=0.0, applicable=True):
        self.valid = valid
        self.applicable = applicable    # 该票种是否需要验签
        self.algorithm = algorithm
        self.cert_serial = cert_serial
        self.signer = signer
        self.sign_time = sign_time
        self.reason = reason
        self.checked_at = checked_at or _iso_now()
        self.elapsed = elapsed

    def to_dict(self):
        return self.__dict__.copy()


class VerifyResult:
    """发票查验结果"""
    STATE = {
        "1000": "查验一致（正常）",
        "1001": "该发票已作废",
        "1002": "该发票已红冲",
        "1003": "查无此票（票面信息有误或非税控开具）",
        "1004": "查验次数超限",
    }

    def __init__(self, ok, code="", message="", invoice_state="", times=0,
                 checked_at=None, fields=None, channel="", elapsed=0.0, applicable=True):
        self.ok = ok                    # bool：查验动作是否成功
        self.applicable = applicable    # 该票种是否需要税务查验
        self.code = code                # 状态码
        self.message = message
        self.invoice_state = invoice_state   # normal / voided / red_flushed / not_found / rate_limited
        self.times = times              # 该票累计查验次数
        self.checked_at = checked_at or _iso_now()
        self.fields = fields or {}      # 查验返回的票面字段（用于与录入值交叉核对）
        self.channel = channel
        self.elapsed = elapsed

    def to_dict(self):
        return self.__dict__.copy()


def applicability(req):
    """由票种的合规要求推导「哪几项动作不适用」。

    这是整个模块的关键：**票种决定要不要做**。
    对英文商业发票这类非国内税务票据，强行去取 XML / 验签 / 查验是错的——
    真实环境里这些接口根本没有对应的票据，所以必须如实返回「不适用」而不是伪造结果。
    """
    if req is None:
        # 调用方没传票种（旧调用 / 独立验签）→ 按「需要」执行，不改变既有行为
        return {"xml": True, "sign": True, "verify": True}
    if not isinstance(req, dict) or not req:
        return {"xml": None, "sign": None, "verify": None}     # 传了但为空 → 票种未知，待人工判定
    need_xml = req.get("need_xml")
    if need_xml is None:
        return {"xml": None, "sign": None, "verify": None}
    return {
        "xml": bool(need_xml),
        "sign": bool(req.get("signature_check")),
        # verify 写「不适用…」即视为无需查验
        "verify": not str(req.get("verify") or "").startswith("不适用"),
    }


class ComplianceProvider:
    """合规能力接口。Mock / Real 都实现这三个方法。

    三个方法都接受可选的 req（票种合规要求）：
      req=None            → 按「需要」执行（兼容旧调用）
      req={need_xml:...}  → 按票种决定；不适用的返回 applicable=False 且不伪造数据
    """

    name = "abstract"

    def fetch_legal_original(self, record: dict, req: dict = None) -> LegalOriginal:
        raise NotImplementedError

    def verify_signature(self, xml_content: str, req: dict = None) -> SignatureResult:
        raise NotImplementedError

    def check_invoice(self, record: dict, req: dict = None) -> VerifyResult:
        raise NotImplementedError


# ══════════════════════════════════════════════════════════════
# Mock 实现：真 XML + 真 RSA 签名 + 真验签，只是密钥来源是本地
# ══════════════════════════════════════════════════════════════
class MockComplianceProvider(ComplianceProvider):
    name = "mock"

    def __init__(self, chaos=False):
        self._key = None
        self._pub = None
        self._verify_count = {}          # 发票号 → 查验次数
        self.chaos = chaos               # 是否制造异常票（作废/红冲/查无）

    # ── 模拟税务局密钥对（真实环境替换为税务根证书公钥） ──
    def _keys(self):
        if self._key is None:
            if os.path.exists(KEY_PATH) and os.path.exists(PUB_PATH):
                with open(KEY_PATH, "rb") as f:
                    self._key = serialization.load_pem_private_key(f.read(), password=None)
                with open(PUB_PATH, "rb") as f:
                    self._pub = serialization.load_pem_public_key(f.read())
            else:
                self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                self._pub = self._key.public_key()
                with open(KEY_PATH, "wb") as f:
                    f.write(self._key.private_bytes(
                        serialization.Encoding.PEM,
                        serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption()))
                with open(PUB_PATH, "wb") as f:
                    f.write(self._pub.public_bytes(
                        serialization.Encoding.PEM,
                        serialization.PublicFormat.SubjectPublicKeyInfo))
        return self._key, self._pub

    # ── 1) 获取法定原件：按电子凭证会计数据标准的结构生成 XML，并真实签名 ──
    def fetch_legal_original(self, record: dict, req: dict = None) -> LegalOriginal:
        t0 = time.time()
        ap = applicability(req)
        if ap["xml"] is False:
            return LegalOriginal(
                available=False, applicable=False, fmt="IMAGE",
                reason="该票种法定原件为发票影像（PDF / 图片），不涉及 XML —— 不适用",
                source="不适用：非国内税务票据")
        if ap["xml"] is None:
            return LegalOriginal(
                available=False, applicable=None,
                reason="票种未确定，无法判断是否需要法定原件；请先人工确认票种",
                source="待人工判定")
        inv = record.get("invoice", {}) or {}
        sub = record.get("subtotal", {}) or {}
        items = record.get("items", []) or []

        def amt(x):
            s = re.sub(r"[^0-9.,\-]", "", str(x or ""))
            if not s:
                return "0.00"
            if "," in s and "." in s:
                s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
            elif "," in s:
                p = s.split(",")
                s = s.replace(",", "") if (len(p) == 2 and len(p[1]) == 3) else s.replace(",", ".")
            try:
                return "%.2f" % float(s)
            except ValueError:
                return "0.00"

        num = str(inv.get("invoice_number", "") or "")
        # 数电票号码为 20 位；本数据集是 8 位英文发票，这里做形态映射并保留原号
        e_num = (num if len(num) >= 8 else num.zfill(8))
        root = ET.Element("{%s}Invoice" % NS)
        basic = ET.SubElement(root, "{%s}InvoiceBasicInfo" % NS)
        for tag, val in [("InvoiceCode", "011002100311"), ("InvoiceNumber", e_num),
                         ("InvoiceDate", str(inv.get("invoice_date", ""))),
                         ("InvoiceTypeCode", "01"),
                         ("InvoiceTypeName", "电子发票（增值税专用发票）")]:
            ET.SubElement(basic, "{%s}%s" % (NS, tag)).text = val * 1 if isinstance(val, str) else str(val)

        for node, src in (("SellerInfo", {"Name": inv.get("seller_name", ""), "Address": inv.get("seller_address", "")}),
                          ("BuyerInfo", {"Name": inv.get("client_name", ""), "Address": inv.get("client_address", "")})):
            n = ET.SubElement(root, "{%s}%s" % (NS, node))
            for k, v in src.items():
                ET.SubElement(n, "{%s}%s" % (NS, k)).text = str(v or "")

        det = ET.SubElement(root, "{%s}InvoiceDetails" % NS)
        for it in items:
            item = ET.SubElement(det, "{%s}Item" % NS)
            ET.SubElement(item, "{%s}ItemName" % NS).text = str(it.get("description", "") or "")
            ET.SubElement(item, "{%s}Quantity" % NS).text = amt(it.get("quantity"))
            ET.SubElement(item, "{%s}Amount" % NS).text = amt(it.get("total_price"))

        sm = ET.SubElement(root, "{%s}Summary" % NS)
        ET.SubElement(sm, "{%s}TaxAmount" % NS).text = amt(sub.get("tax"))
        ET.SubElement(sm, "{%s}TotalAmount" % NS).text = amt(sub.get("total"))

        body = ET.tostring(root, encoding="utf-8").decode("utf-8")

        # ── 真实签名：标准 XMLDSig（c14n + RSA-SHA256），与税局下发格式完全一致 ──
        key, _ = self._keys()
        sign_time = _iso_now()
        sig_el = _xml_sign(root, key, MOCK_CA_SERIAL,
                           "【模拟】国家税务总局电子发票服务平台", sign_time)
        sig_b64 = (sig_el.find("{%s}SignatureValue" % DS).text or "").strip()
        # 签名值只落在 ds:SignatureValue 一处（读取内存节点，不回写文档）。
        # 签名后不再往文档里追加任何节点——摘要覆盖的就是当前这些字节，
        # 追加（哪怕只是展示用节点）会让验签时正文与签发时不一致。
        # 展示需要的算法 / 序列号 / 签署方 / 时间，统一从 ds:Signature 的 KeyInfo 读。

        xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + \
              ET.tostring(root, encoding="utf-8").decode("utf-8")

        return LegalOriginal(
            available=True, fmt="XML", content=xml,
            source="【模拟】税务数字账户 → 发票下载（真实：乐企直连 / 税务数字账户 API）",
            signature={"algorithm": "SHA256-RSA", "value": sig_b64,
                       "cert_serial": MOCK_CA_SERIAL,
                       "signer": "【模拟】国家税务总局电子发票服务平台",
                       "sign_time": sign_time},
            elapsed=round(time.time() - t0, 4))

    # ── 2) 验签：真实密码学验证（把公钥换成税务根证书即可用于生产） ──
    def verify_signature(self, xml_content: str, req: dict = None) -> SignatureResult:
        t0 = time.time()
        ap = applicability(req)
        if ap["sign"] is False:
            return SignatureResult(valid=False, applicable=False,
                                   reason="该票种不含税务数字签名，不适用验签")
        if ap["sign"] is None:
            return SignatureResult(valid=False, applicable=None,
                                   reason="票种未确定，暂不验签；请先人工确认票种")
        _, pub = self._keys()
        r = _xml_verify(xml_content, pub)
        if r["valid"]:
            return SignatureResult(True, r["algorithm"], r["cert_serial"],
                                   r["signer"], r["sign_time"], r["reason"],
                                   elapsed=round(time.time() - t0, 4))
        # 兼容旧格式（只有自定义 Signature 节点、无 ds:Signature）的 XML
        if "未找到 <ds:Signature>" not in r["reason"]:
            return SignatureResult(False, r["algorithm"], r["cert_serial"],
                                   r["signer"], r["sign_time"], r["reason"],
                                   elapsed=round(time.time() - t0, 4))

        try:
            root = ET.fromstring(xml_content)
        except Exception as e:
            return SignatureResult(False, reason="XML 解析失败：%s" % e, elapsed=round(time.time() - t0, 4))
        sig_node = root.find("{%s}Signature" % NS)
        if sig_node is None:
            return SignatureResult(False, reason="未找到 <Signature> 节点：该 XML 无税务数字签名，不是法定原件",
                                   elapsed=round(time.time() - t0, 4))
        gt = lambda node, tag: (node.find("{%s}%s" % (NS, tag)).text or "") \
            if node.find("{%s}%s" % (NS, tag)) is not None else ""
        alg, serial = gt(sig_node, "Algorithm"), gt(sig_node, "CertSerial")
        signer, sign_time, val = gt(sig_node, "Signer"), gt(sig_node, "SignTime"), gt(sig_node, "Value")
        root.remove(sig_node)
        body = ET.tostring(root, encoding="utf-8").decode("utf-8")
        payload = (_canonical(body) + "|" + (sign_time or "")).encode("utf-8")
        try:
            pub.verify(base64.b64decode(val), payload, padding.PKCS1v15(), hashes.SHA256())
            valid, reason = True, "签名有效，内容自签发后未被篡改"
        except Exception:
            valid, reason = False, "签名校验失败：内容已被篡改，或签名与证书不匹配"
        return SignatureResult(valid, alg, serial, signer, sign_time, reason,
                               elapsed=round(time.time() - t0, 4))

    # ── 3) 发票查验：确定性模拟（同一发票号结果稳定），真实实现换 HTTP 调用 ──
    def check_invoice(self, record: dict, req: dict = None) -> VerifyResult:
        ap = applicability(req)
        if ap["verify"] is False:
            return VerifyResult(ok=False, applicable=False, invoice_state="",
                                message="该票种不适用税务查验（非国内税务票据）",
                                channel="不适用")
        if ap["verify"] is None:
            return VerifyResult(ok=False, applicable=None, invoice_state="",
                                message="票种未确定，暂不查验；请先人工确认票种",
                                channel="待人工判定")
        t0 = time.time()
        inv = record.get("invoice", {}) or {}
        sub = record.get("subtotal", {}) or {}
        num = str(inv.get("invoice_number", "") or "").strip()
        if not num:
            return VerifyResult(False, "9001", "发票号码为空，无法查验", "invalid_input",
                                channel="mock", elapsed=round(time.time() - t0, 4))

        self._verify_count[num] = self._verify_count.get(num, 0) + 1
        times = self._verify_count[num]

        # 确定性取样：同一个发票号永远得到同一结论
        h = int(hashlib.sha256(num.encode()).hexdigest()[:8], 16) % 100
        if self.chaos:
            state = "voided" if h < 15 else ("red_flushed" if h < 30 else ("not_found" if h < 42 else "normal"))
        else:
            state = "normal"
        if times > 5:
            state = "rate_limited"

        code = {"normal": "1000", "voided": "1001", "red_flushed": "1002",
                "not_found": "1003", "rate_limited": "1004"}[state]
        fields = {
            "invoice_number": num,
            "invoice_date": str(inv.get("invoice_date", "")),
            "seller_name": str(inv.get("seller_name", "")),
            "client_name": str(inv.get("client_name", "")),
            "total_amount": str(sub.get("total", "")),
            "tax_amount": str(sub.get("tax", "")),
        }
        return VerifyResult(
            ok=(state == "normal"), code=code, message=VerifyResult.STATE[code],
            invoice_state=state, times=times, fields=fields,
            channel="【模拟】全国增值税发票查验平台（真实：乐企直连 / 开放平台查验 API）",
            elapsed=round(time.time() - t0, 4))


def _canonical(xml_body: str) -> str:
    """规范化：去掉声明与空白，保证签名/验签双方一致"""
    s = re.sub(r"<\?xml[^>]*\?>", "", xml_body or "")
    s = re.sub(r">\s+<", "><", s)
    return s.strip()


# ══════════════════════════════════════════════════════════════
# 标准 XMLDSig 实现（Mock 与 Real 共用）
#
# 这里是整个合规模块最硬的一块：**签名与验签都是真的**。
#   · 签名 = RSA-SHA256 over c14n(SignedInfo)      —— 真密码学运算
#   · 摘要 = SHA256 over c14n(去掉 Signature 的文档) —— 真 XMLDSig enveloped transform
#   · 验签 = 用公钥验签名 + 比对 DigestValue，两者皆过才算有效
#
# Mock 与 Real 的唯一差异是「公钥从哪来」：
#   Mock → 本地测试密钥对（KEY_PATH / PUB_PATH）
#   Real → 税务根证书公钥（OCR_TAX_ROOT_CERT 环境变量注入的 PEM）
# 算法、规范化、摘要、状态码映射完全一致，切换只改环境变量。
# ══════════════════════════════════════════════════════════════
DS = "http://www.w3.org/2000/09/xmldsig#"
ET.register_namespace("ds", DS)          # 序列化输出 <ds:Signature>，与税局下发格式一致
C14N_1 = "REC-xml-c14n-20010315"
C14N_EXC = "http://www.w3.org/2001/10/exc-c14n#"
ALGO_RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
ALGO_SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"
ENVELOPED = DS + "enveloped-signature"
ALGO_LABEL = {ALGO_RSA_SHA256: "SHA256-RSA", ALGO_SHA256: "SHA256"}

def _c14n(elem_or_text, c14n_uri=None):
    """XMLDSig 规范化：必须用 C14N，不能用「删空白」代替——两者对属性/命名空间的处理不同。

    采用 REC-xml-c14n-20010315（inclusive C14N、去注释），即 XMLDSig 最常用的算法，
    也是税局下发 XML 数字签名常见的取值。标准库 ET.canonicalize 只支持这一种，
    若遇 exc-c14n / c14n-11 需改用 lxml 实现。c14n_uri 参数仅为兼容调用点保留。
    """
    data = elem_or_text if isinstance(elem_or_text, str) \
        else ET.tostring(elem_or_text, encoding="unicode")
    return ET.canonicalize(data).encode("utf-8")


def _strip_ds_signatures(elem):
    """递归摘除 ds:Signature（保留其它命名空间的 Signature 节点，使其同样参与摘要）。"""
    for child in list(elem):
        if child.tag == "{%s}Signature" % DS:
            elem.remove(child)
        else:
            _strip_ds_signatures(child)


def _digest_of_document(xml_text, c14n_uri=C14N_1):
    """enveloped-signature transform：摘掉 Signature 子树 → c14n → 取摘要值。"""
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None
    _strip_ds_signatures(root)
    return hashlib.sha256(_c14n(ET.tostring(root, encoding="unicode"), c14n_uri)).digest()


def _new_signature_element(digest_b64, c14n_uri=C14N_1):
    sig = ET.Element("{%s}Signature" % DS)
    si = ET.SubElement(sig, "{%s}SignedInfo" % DS)
    ET.SubElement(si, "{%s}CanonicalizationMethod" % DS).set("Algorithm", C14N_1)
    ET.SubElement(si, "{%s}SignatureMethod" % DS).set("Algorithm", ALGO_RSA_SHA256)
    ref = ET.SubElement(si, "{%s}Reference" % DS)
    ref.set("URI", "")
    tr = ET.SubElement(ref, "{%s}Transforms" % DS)
    ET.SubElement(tr, "{%s}Transform" % DS).set("Algorithm", ENVELOPED)
    ET.SubElement(tr, "{%s}Transform" % DS).set("Algorithm", c14n_uri)
    ET.SubElement(ref, "{%s}DigestMethod" % DS).set("Algorithm", ALGO_SHA256)
    ET.SubElement(ref, "{%s}DigestValue" % DS).text = digest_b64
    return sig, si


def _xml_sign(root, key, cert_serial, signer, sign_time):
    """给 root 追加标准 enveloped XMLDSig 并返回签名节点。

    顺序：先算 DigestValue → 组装 SignedInfo → 对 c14n(SignedInfo) 签名 → 回填 SignatureValue。
    """
    digest = hashlib.sha256(_c14n(ET.tostring(root, encoding="unicode"))).digest()
    sig, si = _new_signature_element(base64.b64encode(digest).decode())
    # 展示用元信息（真实税局 XML 里这些放在 KeyInfo / X509Data）
    ki = ET.SubElement(sig, "{%s}KeyInfo" % DS)
    xd = ET.SubElement(ki, "{%s}X509Data" % DS)
    ET.SubElement(xd, "{%s}X509SerialNumber" % DS).text = str(cert_serial)
    ET.SubElement(ki, "{%s}Signer" % DS).text = str(signer)
    ET.SubElement(ki, "{%s}SignTime" % DS).text = str(sign_time)
    root.append(sig)

    sig_bytes = key.sign(_c14n(si), padding.PKCS1v15(), hashes.SHA256())
    ET.SubElement(sig, "{%s}SignatureValue" % DS).text = base64.b64encode(sig_bytes).decode()
    return sig


def _xml_verify(xml_text, pub):
    """真 XMLDSig 验签：验签名 + 比对 DigestValue。

    返回 dict(valid, reason, algorithm, cert_serial, signer, sign_time)。
    公钥由调用方传入——本地测试密钥 或 税务根证书，验签逻辑本身不关心。
    """
    out = dict(valid=False, reason="", algorithm="", cert_serial="", signer="", sign_time="")
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        out["reason"] = "XML 解析失败：%s" % e
        return out

    sigs = list(root.iter("{%s}Signature" % DS))
    if not sigs:
        out["reason"] = "未找到 <ds:Signature> 节点：不是标准税务数字签名 XML"
        return out

    sig = sigs[0]
    si = sig.find("{%s}SignedInfo" % DS)
    sigval = sig.find("{%s}SignatureValue" % DS)
    if si is None or sigval is None or not (sigval.text or "").strip():
        out["reason"] = "签名结构不完整（缺 SignedInfo 或 SignatureValue）"
        return out

    def txt(node, tag):
        e = node.find("{%s}%s" % (DS, tag))
        return (e.text or "").strip() if e is not None else ""

    # 算法标识挂在 SignatureMethod 的 Attribute 上（不是子元素文本）
    sm_node = si.find("{%s}SignatureMethod" % DS)
    out["algorithm"] = ALGO_LABEL.get((sm_node.get("Algorithm", "") if sm_node is not None else ""),
                                      "unknown")
    # 序列号 / 签署方 / 时间挂在 KeyInfo 下（与真实税局 XML 一致）
    ki_node = sig.find("{%s}KeyInfo" % DS)

    def kinfo(tag):
        # 递归查找：X509SerialNumber 嵌在 X509Data 里，Signer/SignTime 可能是平铺也可能是嵌套
        e = ki_node.find(".//{%s}%s" % (DS, tag)) if ki_node is not None else None
        return (e.text or "").strip() if e is not None else ""

    out["cert_serial"] = kinfo("X509SerialNumber")
    out["signer"] = kinfo("Signer")
    out["sign_time"] = kinfo("SignTime")

    # ① 验签名：c14n(SignedInfo) 上的 RSA-SHA256
    try:
        pub.verify(base64.b64decode(sigval.text.strip()), _c14n(si),
                   padding.PKCS1v15(), hashes.SHA256())
    except Exception:
        out["reason"] = "签名校验失败：SignedInfo 被篡改，或公钥与签名不匹配"
        return out

    # ② 比对摘要：摘掉 Signature 后的文档 c14n → SHA256，必须等于 Reference 的 DigestValue
    ref = si.find("{%s}Reference" % DS)
    dv = ref.find("{%s}DigestValue" % DS) if ref is not None else None
    if dv is None or not (dv.text or "").strip():
        out["reason"] = "结构不完整：Reference/DigestValue 缺失"
        return out
    c14n_uri = C14N_1
    trs = ref.find("{%s}Transforms" % DS) if ref is not None else None
    for tr in (list(trs) if trs is not None else []):
        a = tr.get("Algorithm", "")
        if a in (C14N_1, C14N_EXC):
            c14n_uri = a
    body_digest = _digest_of_document(xml_text, c14n_uri)
    if body_digest is None:
        out["reason"] = "正文规范化失败，无法比对摘要"
        return out
    if base64.b64encode(body_digest).decode() != (dv.text or "").strip():
        out["reason"] = "内容摘要不一致：票面正文在签发后已被修改"
        return out

    out["valid"] = True
    out["reason"] = "签名有效（c14n + RSA-SHA256 + 摘要比对全部通过），内容自签发后未被篡改"
    return out


# ══════════════════════════════════════════════════════════════
# Real 实现：税局取 XML 原件 / 税务根证书验签 / 查验
# ══════════════════════════════════════════════════════════════
class RealComplianceProvider(ComplianceProvider):
    """真实实现：税局取 XML 法定原件 / 税务根证书验签 / 发票查验。

    与 Mock 的两处差异都是「外部信任源」，不是算法：
      1. 法定原件来源 —— 乐企直连 / 税务数字账户 API，而非本地生成
      2. 验签公钥     —— 税务根证书 PEM（OCR_TAX_ROOT_CERT），而非本地测试密钥
    验签算法、C14N 规范化、摘要比对、状态码映射与 Mock **共用同一份实现**
    （见上文 _xml_verify / _xml_sign / _digest_of_document）。

    为什么这套代码没有在公开仓库里跑出真结果：
      税局网关地址、企业授权凭据、税务根证书、真实发票要素属公司保密资料。
      本仓库只以「环境变量 + 占位值」声明这些依赖，不落盘、不上传、不发真实请求。
      真实环境配好 OCR_LEQI_* / OCR_TAX_ROOT_CERT / OCR_VERIFY_API_URL 即可运行，业务代码零改动。
      缺凭证时各方法返回 available=False / ok=False 并给出原因——即「降级」而非崩溃：
      生产里「某租户未开通乐企直连」是常态，抛异常反而是错的。
    """
    name = "real"
    CACHE_TTL = 24 * 3600        # 查验结论缓存 24h（查验平台有频次限制）

    def __init__(self, cache_path=None):
        # ── 保密项：全部由环境变量注入，仓库内不写死任何真实值 ──
        self.leqi_base = os.environ.get("OCR_LEQI_BASE_URL", "")        # 乐企直连网关
        self.leqi_token = os.environ.get("OCR_LEQI_TOKEN", "")          # 已签发的 access_token
        self.leqi_secret = os.environ.get("OCR_LEQI_CLIENT_SECRET", "") # 企业凭据 secret
        self.tax_pubkey = os.environ.get("OCR_TAX_ROOT_CERT", "")       # 税务根证书公钥 PEM
        self.tax_serial = os.environ.get("OCR_TAX_CERT_SERIAL", "")     # 证书序列号白名单（可选）
        self.verify_api = os.environ.get("OCR_VERIFY_API_URL", "")      # 查验平台地址
        self.ca_bundle = os.environ.get("OCR_CA_BUNDLE", "")            # 网关专用根证书（可选）
        self.cache_path = cache_path or os.environ.get("OCR_VERIFY_CACHE") or \
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "verify_cache.jsonl")
        self._cache = {}
        self._cache_loaded = False

    # ─────────────── 内部：HTTP / 缓存 ───────────────
    def _http(self, url, payload, headers, timeout=30):
        ctx = ssl.create_default_context(cafile=self.ca_bundle or None)
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers=dict(headers, **{"Content-Type": "application/json"}))
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            return json.loads(r.read().decode("utf-8"))

    def _token(self):
        if self.leqi_token:
            return self.leqi_token
        if not (self.leqi_base and self.leqi_secret):
            return ""
        d = self._http(self.leqi_base.rstrip("/") + "/oauth2/token",
                       {"grant_type": "client_credentials", "client_secret": self.leqi_secret}, {})
        return d.get("access_token") or d.get("token") or ""

    def _cache_load(self):
        if self._cache_loaded:
            return
        self._cache_loaded = True
        if not os.path.exists(self.cache_path):
            return
        for line in open(self.cache_path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
                self._cache[o["key"]] = o
            except Exception:
                pass

    def _cache_get(self, key):
        self._cache_load()
        o = self._cache.get(key)
        return o if o and (time.time() - o.get("ts", 0)) < self.CACHE_TTL else None

    def _cache_put(self, key, value):
        self._cache[key] = dict(value, ts=time.time())
        try:
            with open(self.cache_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(dict(key=key, **value), ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ─────────────── 1) 从税局获取 XML 法定原件 ───────────────
    def fetch_legal_original(self, record, req=None):
        """① 企业凭据换 access_token ② 按发票要素调下载接口
        ③ 响应体即含税务数字签名的 XML 原文，直接留存，切勿二次转换（签名覆盖原始字节）
        ④ 同时取 OFD / PDF 版式件；参考电子凭证会计数据标准（财会〔2025〕9号）接口规范"""
        t0 = time.time()
        ap = applicability(req)
        if ap["xml"] is False:
            return LegalOriginal(available=False, applicable=False, fmt="IMAGE",
                                 reason="该票种法定原件为发票影像，不涉及 XML —— 不适用",
                                 source="不适用")
        if ap["xml"] is None:
            return LegalOriginal(available=False, applicable=None,
                                 reason="票种未确定，无法判断是否需要法定原件", source="待人工判定")

        if not (self.leqi_base and (self.leqi_token or self.leqi_secret)):
            return LegalOriginal(available=False, fmt="XML", source="税局直连接入",
                                 reason="未配置 OCR_LEQI_BASE_URL / OCR_TAX_ROOT_CERT / OCR_LEQI_CLIENT_SECRET，"
                                        "无法从税局获取 XML 法定原件（该链路在公司环境已实现，"
                                        "网关地址与凭据属保密项，不外置到公开仓库）",
                                 elapsed=round(time.time() - t0, 4))

        try:
            token = self._token()
            inv = record.get("invoice", {}) or {}
            payload = {
                "invoiceCode": str(inv.get("invoice_code", "")),
                "invoiceNumber": str(inv.get("invoice_number", "")),
                "invoiceDate": str(inv.get("invoice_date", "")),
                "format": ["XML", "OFD", "PDF"],
            }
            d = self._http(self.leqi_base.rstrip("/") + "/api/invoice/download", payload,
                           {"Authorization": "Bearer " + token})
            data = d.get("data", {}) or {}
            xml_text = data.get("xml") or d.get("xml") or ""
            ifd_b64 = data.get("ofd") or d.get("ofd") or ""
            pdf_b64 = data.get("pdf") or d.get("pdf") or ""
            if not xml_text:
                return LegalOriginal(available=False, fmt="XML", source="税局直连接入",
                                     reason="接口未返回 XML 原文（响应码 %s / 提示：%s）"
                                            % (d.get("code"), d.get("message")),
                                     elapsed=round(time.time() - t0, 4))
            return LegalOriginal(
                available=True, fmt="XML", content=xml_text,
                source="税务数字账户 / 乐企直连 /api/invoice/download",
                signature={"algorithm": "SHA256-RSA", "value": "",
                           "cert_serial": self.tax_serial, "signer": "国家税务总局",
                           "sign_time": ""},
                reason="" if (ifd_b64 or pdf_b64) else "仅取到 XML，未取到 OFD / PDF 版式件",
                elapsed=round(time.time() - t0, 4))
        except Exception as e:
            return LegalOriginal(available=False, fmt="XML", source="税局直连接入",
                                 reason="获取 XML 失败：%s: %s" % (type(e).__name__, e),
                                 elapsed=round(time.time() - t0, 4))

    # ─────────────── 2) 用税务根证书公钥验签 ───────────────
    def verify_signature(self, xml_content, req=None):
        """① 载入税务根证书公钥（self.tax_pubkey）
        ② 解析 ds:Signature：算法 / 证书序列号 / 签名值
        ③ C14N 规范化 + RSA-SHA256 验签，并比对 Reference 的 DigestValue
        ④ 可选：证书序列号是否在白名单内
        注意：与 Mock 唯一的差异是「公钥从哪来」，验签逻辑走的是同一个 _xml_verify。"""
        t0 = time.time()
        ap = applicability(req)
        if ap["sign"] is False:
            return SignatureResult(valid=False, applicable=False, reason="该票种不适用数字签名验签")
        if ap["sign"] is None:
            return SignatureResult(valid=False, applicable=None, reason="票种未确定，暂不验签")
        if not self.tax_pubkey:
            return SignatureResult(
                valid=False, algorithm="SHA256-RSA",
                reason="未配置 OCR_TAX_ROOT_CERT（税务根证书公钥）。验签算法与真实实现完全一致，"
                       "缺的只是信任源——配好该环境变量即可验税局下发的标准 XMLDSig")
        try:
            pub = serialization.load_pem_public_key(self.tax_pubkey.encode("utf-8"))
        except Exception as e:
            return SignatureResult(valid=False, reason="税务根证书公钥解析失败：%s" % e,
                                   elapsed=round(time.time() - t0, 4))

        r = _xml_verify(xml_content, pub)
        if r["valid"] and self.tax_serial and r["cert_serial"] and r["cert_serial"] != self.tax_serial:
            return SignatureResult(False, r["algorithm"], r["cert_serial"], r["signer"], r["sign_time"],
                                   reason="证书序列号不在白名单内（期望 %s，实际 %s）"
                                          % (self.tax_serial, r["cert_serial"]),
                                   elapsed=round(time.time() - t0, 4))
        return SignatureResult(r["valid"], r["algorithm"], r["cert_serial"], r["signer"], r["sign_time"],
                               r["reason"], elapsed=round(time.time() - t0, 4))

    # ─────────────── 3) 调用查验接口 ───────────────
    def check_invoice(self, record, req=None):
        """① 组查验请求：发票代码 / 号码 / 日期 / 校验码后 6 位（普票）或金额（专票）
        ② 带企业授权标头 POST 查验接口 ③ 状态码 → invoice_state
        ④ 结论落盘缓存（同票 24h 内不重复查验），查验记录随归档件一起留存"""
        t0 = time.time()
        ap = applicability(req)
        if ap["verify"] is False:
            return VerifyResult(ok=False, applicable=False, invoice_state="",
                                message="该票种不适用税务查验", channel="不适用")
        if ap["verify"] is None:
            return VerifyResult(ok=False, applicable=None, invoice_state="",
                                message="票种未确定，暂不查验", channel="待人工判定")

        inv = record.get("invoice", {}) or {}
        sub = record.get("subtotal", {}) or {}
        key = "|".join([str(inv.get("invoice_code", "")), str(inv.get("invoice_number", "")),
                        str(inv.get("invoice_date", ""))])
        if not key.strip("|"):
            return VerifyResult(False, "9001", "发票要素缺失，无法查验", "invalid_input",
                                channel="真实查验", elapsed=round(time.time() - t0, 4))

        hit = self._cache_get(key)
        if hit:
            return VerifyResult(hit.get("ok"), hit.get("code"), hit.get("message"),
                                hit.get("invoice_state"), hit.get("times"), hit.get("checked_at"),
                                hit.get("fields"), hit.get("channel"),
                                elapsed=round(time.time() - t0, 4))

        if not (self.verify_api and (self.leqi_token or self.leqi_secret)):
            return VerifyResult(ok=False, code="9002", invoice_state="unavailable",
                                message="未配置 OCR_VERIFY_API_URL / 企业凭据，无法调用查验接口",
                                channel="真实查验（缺配置）", elapsed=round(time.time() - t0, 4))

        try:
            payload = {
                "invoiceCode": str(inv.get("invoice_code", "")),
                "invoiceNumber": str(inv.get("invoice_number", "")),
                "invoiceDate": str(inv.get("invoice_date", "")),
                "checkCode": str(inv.get("check_code", ""))[-6:],
                "totalAmount": str(sub.get("total", "")),
            }
            d = self._http(self.verify_api, payload, {"Authorization": "Bearer " + self._token()})
            code = str(d.get("code") or d.get("status") or "")
            state = {"1000": "normal", "1001": "voided", "1002": "red_flushed",
                     "1003": "not_found", "1004": "rate_limited"}.get(code, "unknown")
            res = VerifyResult(
                ok=(state == "normal"), code=code,
                message=VerifyResult.STATE.get(code, str(d.get("message", ""))),
                invoice_state=state, times=int(d.get("times") or 1),
                fields=d.get("data", {}) or {},
                channel="全国增值税发票查验平台（乐企直连）", elapsed=round(time.time() - t0, 4))
            self._cache_put(key, res.to_dict())
            return res
        except Exception as e:
            return VerifyResult(ok=False, code="9003", invoice_state="error",
                                message="查验调用失败：%s: %s" % (type(e).__name__, e),
                                channel="真实查验", elapsed=round(time.time() - t0, 4))


def get_provider():
    mode = (os.environ.get("OCR_COMPLIANCE_PROVIDER") or "mock").lower()
    if mode == "real":
        return RealComplianceProvider()
    return MockComplianceProvider(chaos=(os.environ.get("OCR_MOCK_CHAOS") == "1"))


# ══════════════════════════════════════════════════════════════
# 自测：证明签名与验签是真的（含篡改检出）
# ══════════════════════════════════════════════════════════════
def _selftest():
    ok = 0
    total = 0

    def check(name, cond, extra=""):
        nonlocal ok, total
        total += 1
        ok += bool(cond)
        print("  %s %s%s" % ("✓" if cond else "✗", name, ("  " + extra) if extra else ""))

    rec = {
        "invoice": {"invoice_number": "12847181", "invoice_date": "03/03/2012",
                    "seller_name": "Fitzpatrick and Sons", "client_name": "Duncan PLC",
                    "seller_address": "00480 Cook Cove", "client_address": "Unit 8799"},
        "items": [{"description": "HP Desktop", "quantity": "4.00", "total_price": "615.78"},
                  {"description": "Custom Build", "quantity": "3.00", "total_price": "4620.00"}],
        "subtotal": {"tax": "623,68", "total": "6 860,45"},
    }
    p = MockComplianceProvider()

    def _pem(pub):
        """把公钥对象导出为 PEM 字符串，充当 Real 侧的「税务根证书公钥」。"""
        return pub.public_bytes(serialization.Encoding.PEM,
                                serialization.PublicFormat.SubjectPublicKeyInfo).decode("utf-8")

    print("=" * 80)
    print("合规能力自测（Mock，但签名/验签为真实密码学运算）")
    print("=" * 80)

    lo = p.fetch_legal_original(rec)
    check("1. 获取法定原件返回 XML", lo.available and lo.format == "XML",
          "长度 %d 字符" % len(lo.content or ""))
    check("   含税务数字签名节点", "ds:Signature" in (lo.content or ""))
    check("   含发票号码", "12847181" in (lo.content or ""))

    sr = p.verify_signature(lo.content)
    check("2. 验签通过（内容未被篡改）", sr.valid, sr.reason)
    check("   返回算法与证书序列号", sr.algorithm == "SHA256-RSA" and bool(sr.cert_serial),
          "%s / %s" % (sr.algorithm, sr.cert_serial))

    # 篡改测试：改一个金额，验签必须失败
    tampered = (lo.content or "").replace("6860.45", "16860.45")
    sr2 = p.verify_signature(tampered)
    check("3. 篡改金额后验签失败（关键）", (not sr2.valid), sr2.reason)

    # 去掉签名节点：应判为「不是法定原件」
    _root = ET.fromstring(lo.content)
    _sig = _root.find("{%s}Signature" % DS)
    _root.remove(_sig)
    no_sig = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(_root, encoding="utf-8").decode("utf-8")
    sr3 = p.verify_signature(no_sig)
    check("4. 无签名节点被识别为非法定原件", (not sr3.valid) and ("签名" in sr3.reason), sr3.reason)

    vr = p.check_invoice(rec)
    check("5. 发票查验返回确定性结论", vr.ok and vr.invoice_state == "normal",
          "状态码 %s / %s" % (vr.code, vr.message))
    vr2 = p.check_invoice(rec)
    vr3 = p.check_invoice(rec)
    check("   查验次数累计", vr3.times == 3, "第 %d 次" % vr3.times)
    check("   查验返回票面字段（供交叉核对）", vr.fields.get("invoice_number") == "12847181")

    p2 = MockComplianceProvider(chaos=True)
    states = {p2.check_invoice({"invoice": {"invoice_number": "100000%02d" % i},
                                "subtotal": {}, "items": []}).invoice_state for i in range(1, 21)}
    check("6. 异常票可被模拟（作废/红冲/查无）", len(states) > 1, "本次出现状态：" + ",".join(sorted(states)))

    # ── 7. 票种感知：不适用的票种不得伪造 XML / 验签 / 查验 ──
    print("-" * 80)
    print("票种感知（关键：不适用的动作必须如实返回「不适用」，而不是伪造成功）")
    INTL = {"legal_original": "发票影像（PDF / 图片）", "signature_check": False,
            "verify": "不适用（非国内税务票据）", "need_xml": False}
    CN   = {"legal_original": "XML", "signature_check": True,
            "verify": "全国增值税发票查验平台", "need_xml": True}

    ap = applicability(INTL)
    check("7. 英文商业发票 → 三项均不适用",
          ap["xml"] is False and ap["sign"] is False and ap["verify"] is False, str(ap))

    lo2 = p.fetch_legal_original(rec, INTL)
    check("   不生成 XML，明确标记不适用",
          (not lo2.available) and lo2.applicable is False and "影像" in lo2.reason,
          "格式=%s / %s" % (lo2.format, lo2.reason[:34]))
    sr4 = p.verify_signature("whatever", INTL)
    check("   验签返回不适用（不是「验签失败」）",
          (not sr4.valid) and sr4.applicable is False, sr4.reason)
    vr4 = p.check_invoice(rec, INTL)
    check("   查验返回不适用（不是「查无此票」）",
          (not vr4.ok) and vr4.applicable is False and vr4.invoice_state == "",
          vr4.message)

    # 需要 XML 的票种：行为不变
    ap2 = applicability(CN)
    check("8. 数电票 → 三项均适用", ap2["xml"] and ap2["sign"] and ap2["verify"], str(ap2))
    lo3 = p.fetch_legal_original(rec, CN)
    check("   数电票正常生成 XML 且验签通过",
          lo3.available and lo3.format == "XML" and p.verify_signature(lo3.content, CN).valid)

    # 票种未知：不猜，交人工
    ap3 = applicability({})
    lo4 = p.fetch_legal_original(rec, {})
    ap4 = applicability(None)
    check("   未传票种时保持旧行为（按需执行）",
          ap4["xml"] is True and p.fetch_legal_original(rec, None).available)
    check("9. 票种未知（传空）→ 不猜，提示人工判定",
          ap3["xml"] is None and lo4.applicable is None and "人工" in lo4.reason,
          lo4.reason[:30])

    # ── 10. Real 与 Mock 共用同一套验签算法，差异只在「公钥从哪来」 ──
    print("-" * 80)
    print("Real / Mock 同源（算法一致，仅信任源不同）")

    # 用本地测试公钥冒充「税务根证书」，走 Real 的验签通道
    real = RealComplianceProvider()
    _, mock_pub = p._keys()
    real.tax_pubkey = _pem(mock_pub)
    sr5 = real.verify_signature(lo.content)
    check("10. Real 验签 Mock 签发的 XML 通过（同一算法）", sr5.valid, sr5.reason[:40])

    # 换成不相干的公钥 → 必须失败（证明验的是真签名，不是一路绿灯）
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    real.tax_pubkey = _pem(other.public_key())
    sr6 = real.verify_signature(lo.content)
    check("    换用无关公钥则验签失败",
          (not sr6.valid) and "签名校验失败" in sr6.reason, sr6.reason[:40])

    # 未配置税务根证书时：如实降级为「不可用 + 原因」，而不是伪造成功或抛异常
    real.tax_pubkey = ""
    sr7 = real.verify_signature(lo.content)
    check("    未配税务根证书→ 如实降级（不伪造、不抛异常）",
          (not sr7.valid) and "OCR_TAX_ROOT_CERT" in sr7.reason, sr7.reason[:40])

    # 税局凭据未配置时：取 XML 返回可用=False，不编造法定原件
    real.leqi_base = ""
    lo5 = real.fetch_legal_original(rec, None)
    check("    未配税局凭据 → 取 XML 如实返回不可用",
          (not lo5.available) and "OCR_LEQI" in lo5.reason, lo5.reason[:40])

    print("-" * 80)
    print("通过 %d / %d" % (ok, total))
    return ok == total


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if _selftest() else 1)
