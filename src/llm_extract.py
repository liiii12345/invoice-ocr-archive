# -*- coding: utf-8 -*-
"""
LLM 字段抽取器 —— 与厂商无关（任何 OpenAI 兼容接口）

设计要点：
1. 输入是 OCR 的「文本行 + 坐标 + 逐行置信度」，不是图片（OCR 与理解分离，便于替换任一环）。
2. **让 LLM 回传 source_lines（字段来源的 OCR 行号）**，据此反查坐标框 →
   图上描边 / 字段证据放大图 依然可用。这是「LLM 抽取」能保留可解释性的关键。
3. LLM 主抽 + 规则抽取器兜底 + 两者交叉校验（agree / conflict / 单侧命中）。

配置（环境变量，均可不设）：
  OCR_LLM_BASE_URL   默认 https://dashscope.aliyuncs.com/compatible-mode/v1   （阿里百炼/通义）
  OCR_LLM_API_KEY    默认依次取 DASHSCOPE_API_KEY / OPENAI_API_KEY / ARK_API_KEY
  OCR_LLM_MODEL      默认 qwen-flash

  本地 Ollama（完全免费、无需 Key）：
    OCR_LLM_BASE_URL=http://127.0.0.1:11434/v1
    OCR_LLM_API_KEY=ollama
    OCR_LLM_MODEL=qwen2.5:7b
"""
import os, re, json, time, urllib.request, urllib.error

from invoice_extract import extract as rule_extract, parse_amount, _norm

FIELD_KEYS = ["invoice_number", "invoice_date", "due_date", "seller_name", "seller_address",
              "client_name", "client_address", "tax", "discount", "total",
              "bank_name", "account_number"]

FIELD_DESC = """- invoice_number 发票号码（数字）
- invoice_date 开票日期（保持票面原样）
- due_date 到期日期
- seller_name 卖方名称
- seller_address 卖方地址（多行用 \\n 连接）
- client_name 买方名称
- client_address 买方地址（多行用 \\n 连接）
- tax 税额
- discount 折扣
- total 价税合计（本次应付总额）
- bank_name 收款银行
- account_number 银行账号"""

SYSTEM_PROMPT = (
    "你是发票字段抽取引擎。你只输出严格 JSON，不输出任何解释、不加 markdown 代码块。"
    "你会根据 OCR 文本行的序号来标注每个字段的来源行，以便系统在原件上定位。"
)

USER_TMPL = """下面是一张发票经过 OCR 识别得到的文本行。每行格式为：
行号 | (x,y)坐标 | 文本

请抽取字段并输出严格 JSON。规则：
1. 只输出 JSON 对象本身，不要任何额外文字或代码块围栏。
2. 每个字段输出 {{"value": <字符串>, "source_lines": [行号, ...]}}。
   source_lines 必须是「该字段值实际来自」的 OCR 行号（可多行），用于在原件影像上定位。找不到就填 []。
3. 抽不到的字段 value 填 ""，source_lines 填 []。不要猜测、不要编造。
4. 金额统一输出为「纯数字字符串，两位小数，无千分位、无货币符号」，例如 6860.45。
   注意票面可能是欧洲写法：6 860,45 表示 6860.45；1.234,56 表示 1234.56。
5. 数量同样输出为两位小数字符串，例如 4.00。
6. 明细 items 按票面出现顺序输出，total_price 为该行的含税金额。

字段定义：
{field_desc}

输出结构（务必严格遵循）：
{{"fields": {{"invoice_number": {{"value": "", "source_lines": []}}, ... }},
  "items": [{{"description": "", "quantity": "", "total_price": "", "source_lines": []}}]}}

OCR 文本行（共 {n} 行）：
{lines}
"""


class LLMConfig:
    def __init__(self, base_url=None, api_key=None, model=None, timeout=120):
        self.base_url = (base_url or os.environ.get("OCR_LLM_BASE_URL")
                         or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
        self.api_key = (api_key or os.environ.get("OCR_LLM_API_KEY")
                        or os.environ.get("DASHSCOPE_API_KEY")
                        or os.environ.get("OPENAI_API_KEY")
                        or os.environ.get("ARK_API_KEY") or "")
        self.model = model or os.environ.get("OCR_LLM_MODEL") or "qwen-flash"
        self.timeout = timeout

    def ready(self):
        # 本地 Ollama 之类可能不需要真实 key
        return bool(self.base_url and (self.api_key or "127.0.0.1" in self.base_url or "localhost" in self.base_url))

    def describe(self):
        host = re.sub(r"^https?://", "", self.base_url).split("/")[0]
        return "%s @ %s" % (self.model, host)


def _post_json(url, payload, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def chat(messages, cfg=None):
    cfg = cfg or LLMConfig()
    payload = {"model": cfg.model, "messages": messages,
               "temperature": 0, "max_tokens": 2000}
    headers = {"Content-Type": "application/json"}
    if cfg.api_key:
        headers["Authorization"] = "Bearer " + cfg.api_key
    d = _post_json(cfg.base_url + "/chat/completions", payload, headers, cfg.timeout)
    return d["choices"][0]["message"]["content"], d.get("usage") or {}


def build_prompt(lines, max_chars=140):
    """把 OCR 行压成紧凑的编号列表，控制 token"""
    out = []
    for i, l in enumerate(lines):
        box = l["box"]
        x, y = int(box[0][0]), int(box[0][1])
        t = str(l["text"]).replace("\n", " ")[:max_chars]
        out.append("%d | (%d,%d) | %s" % (i, x, y, t))
    return "\n".join(out)


def _parse_json_loose(s):
    s = s.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    try:
        return json.loads(s)
    except Exception:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        return json.loads(s[i:j + 1])
    raise ValueError("LLM 未返回可解析的 JSON")


def _ev_from_lines(lines, idxs):
    picked = []
    for k in idxs or []:
        try:
            k = int(k)
        except Exception:
            continue
        if 0 <= k < len(lines):
            picked.append(lines[k])
    if not picked:
        return None
    xs = [p["box"][0][0] for p in picked]; ys = [p["box"][0][1] for p in picked]
    x2s = [p["box"][2][0] for p in picked]; y2s = [p["box"][2][1] for p in picked]
    return {
        "lines": [{"text": p["text"], "conf": p["conf"], "box": p["box"]} for p in picked],
        "conf": round(min(float(p["conf"]) for p in picked), 4),
        "box": [min(xs), min(ys), max(x2s), max(y2s)],
    }


def llm_extract(lines, cfg=None):
    """返回 (fields, items, meta)；fields[k] = {'value','evidence'}"""
    cfg = cfg or LLMConfig()
    prompt = USER_TMPL.format(field_desc=FIELD_DESC, n=len(lines), lines=build_prompt(lines))
    t0 = time.time()
    content, usage = chat([{"role": "system", "content": SYSTEM_PROMPT},
                           {"role": "user", "content": prompt}], cfg)
    dt = time.time() - t0
    raw = _parse_json_loose(content)

    fields = {}
    for k in FIELD_KEYS:
        cell = (raw.get("fields") or {}).get(k) or {}
        v = cell.get("value", "")
        v = "" if v is None else str(v).strip()
        # 金额类字段做一次归一化，避免 LLM 漏了格式要求
        if k in ("tax", "discount", "total") and v:
            n = parse_amount(v)
            if n is not None:
                v = "%.2f" % n
        fields[k] = {"value": v, "evidence": _ev_from_lines(lines, cell.get("source_lines"))}

    items = []
    for it in (raw.get("items") or []):
        q = str(it.get("quantity", "") or "").strip()
        p = str(it.get("total_price", "") or "").strip()
        if q:
            n = parse_amount(q);  q = ("%.2f" % n) if n is not None else q
        if p:
            n = parse_amount(p);  p = ("%.2f" % n) if n is not None else p
        items.append({"description": str(it.get("description", "") or "").strip(),
                      "quantity": q, "total_price": p,
                      "evidence": _ev_from_lines(lines, it.get("source_lines"))})

    return fields, items, {"model": cfg.model, "elapsed": round(dt, 2),
                           "usage": usage, "raw_len": len(content)}


def _same(a, b):
    """两个字段值是否一致（数字按数值，文本按去符号小写）"""
    a, b = str(a or "").strip(), str(b or "").strip()
    if not a and not b:
        return True
    if not a or not b:
        return False
    na, nb = parse_amount(a), parse_amount(b)
    if na is not None and nb is not None:
        return abs(na - nb) < 0.02
    return _norm(a) == _norm(b)


def hybrid_extract(lines, img_w, cfg=None):
    """LLM 主抽 + 规则兜底 + 交叉校验。LLM 失败时自动降级为纯规则。"""
    cfg = cfg or LLMConfig()
    rule = rule_extract(lines, img_w=img_w)
    rule_fields = {k: v for k, v in rule.items() if not k.startswith("_")}
    rule_items = rule.get("_items", [])

    llm_fields, llm_items, meta, llm_err = None, None, {}, None
    if cfg.ready():
        try:
            llm_fields, llm_items, meta = llm_extract(lines, cfg)
        except Exception as e:
            llm_err = "%s: %s" % (type(e).__name__, str(e)[:200])

    if llm_fields is None:
        # 降级：纯规则
        fields = {k: {"value": v["value"], "evidence": v.get("evidence"),
                      "source": "rule", "status": "llm_failed"} for k, v in rule_fields.items()}
        items = [{"description": i["description"], "quantity": i["quantity"],
                  "total_price": i["total_price"], "evidence": None} for i in rule_items]
        return fields, items, {"mode": "rule(fallback)", "llm_error": llm_err,
                               "rule_evidence": rule.get("_items_evidence")}

    # 交叉校验：逐字段比对
    fields, conflicts = {}, []
    for k in FIELD_KEYS:
        lv = (llm_fields.get(k) or {}).get("value", "")
        rv = (rule_fields.get(k) or {}).get("value", "")
        if lv and rv:
            if _same(lv, rv):
                status, src = "agree", "both"
            else:
                status, src = "conflict", "llm"       # 冲突时以 LLM 为准，但强制标记复核
                conflicts.append(k)
        elif lv:
            status, src = "llm_only", "llm"
        elif rv:
            status, src = "rule_only", "rule"
        else:
            status, src = "empty", "none"
        # 证据优先用 LLM 的；LLM 没给来源行时回落到规则证据
        ev = (llm_fields.get(k) or {}).get("evidence") or (rule_fields.get(k) or {}).get("evidence")
        fields[k] = {"value": lv if lv else rv, "evidence": ev, "source": src,
                     "status": status, "llm_value": lv, "rule_value": rv}

    # 明细：逐项交叉校验（LLM 在表格列对齐上常出错，必须逐项比对并暴露分歧）
    items, n_conf = [], 0
    n = max(len(llm_items or []), len(rule_items))
    for i in range(n):
        a = (llm_items or [])[i] if i < len(llm_items or []) else None
        b = rule_items[i] if i < len(rule_items) else None
        if a and b:
            ok = _same(a["total_price"], b["total_price"]) and _same(a["quantity"], b["quantity"])
            status = "agree" if ok else "conflict"
            if not ok:
                n_conf += 1
        elif a:
            status = "llm_only"
        else:
            status = "rule_only"
        base = a or b
        items.append({
            "description": base["description"],
            "quantity": base["quantity"],
            "total_price": base["total_price"],
            "evidence": (a or {}).get("evidence"),
            "status": status,
            # 冲突时把另一侧的值一并带上，供人工对照选择
            "alt_quantity": (b or {}).get("quantity") if status == "conflict" else "",
            "alt_total_price": (b or {}).get("total_price") if status == "conflict" else "",
        })
    items_conflict = n_conf > 0 or len(llm_items or []) != len(rule_items)

    meta.update({"mode": "hybrid", "conflicts": conflicts,
                 "items_conflict": items_conflict, "items_conflict_count": n_conf,
                 "items_len_llm": len(llm_items or []), "items_len_rule": len(rule_items)})
    return fields, items, meta
