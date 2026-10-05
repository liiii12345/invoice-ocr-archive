# -*- coding: utf-8 -*-
"""
本地 OCR 服务 —— 免费 · 零资质 · 数据不出本机
引擎：RapidOCR（ONNXRuntime，CPU 可跑，无需 GPU / 无需 API Key）

启动：
    python ocr_service.py                # 默认 http://127.0.0.1:8765
    python ocr_service.py --port 9000

接口：
    GET  /health                         健康检查
    POST /ocr   {"file": "batch1-0002.jpg"}   按文件名识别数据集图片
    POST /ocr   {"image_base64": "..."}       识别上传的图片
返回：
    {"ok":true, "lines":[{"text","box","conf"}], "text":"...", "avg_conf":0.81, "elapsed":2.9}
"""
import sys, os, io, json, time, base64, argparse, glob, re
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from invoice_extract import extract          # 规则抽取器（版面坐标推理）
from llm_extract import hybrid_extract, llm_extract, LLMConfig, FIELD_KEYS
from vtype_classify import classify as classify_vtype
from compliance_provider import get_provider, MockComplianceProvider, applicability

_compliance = None
def compliance():
    global _compliance
    if _compliance is None:
        _compliance = get_provider()
        print("[init] 合规能力提供方: %s" % _compliance.name, flush=True)
    return _compliance


def build_record(fields, items):
    """把抽取结果整理成合规能力需要的 record 结构"""
    def g(k):
        v = (fields.get(k) or {})
        return v.get("value", "") if isinstance(v, dict) else ""
    return {
        "invoice": {"invoice_number": g("invoice_number"), "invoice_date": g("invoice_date"),
                    "seller_name": g("seller_name"), "seller_address": g("seller_address"),
                    "client_name": g("client_name"), "client_address": g("client_address")},
        "items": [{"description": it.get("description", ""), "quantity": it.get("quantity", ""),
                   "total_price": it.get("total_price", "")} for it in (items or [])],
        "subtotal": {"tax": g("tax"), "total": g("total")},
    }


def run_compliance(fields, items, req=None):
    """跑完整合规链路：① 获取法定原件 ② 验签 ③ 发票查验

    req = 票种推导出的合规要求（vtype_classify 产出的 requirements）。
    传入后：不适用的动作直接返回「不适用」，不伪造 XML / 验签通过 / 查验正常。
    """
    p = compliance()
    rec = build_record(fields, items)
    out = {"provider": p.name, "record": rec, "applicability": applicability(req)}
    try:
        lo = p.fetch_legal_original(rec, req)
        d = lo.to_dict(); d["content_len"] = len(lo.content or "")
        d["content_preview"] = (lo.content or "")[:4000]
        d.pop("content", None)
        out["legal_original"] = d
        if lo.available:
            out["signature"] = p.verify_signature(lo.content, req).to_dict()
        else:
            # 原件取不到时，把「不适用 / 待人工」原样透传，不要伪装成「验签失败」
            out["signature"] = {"valid": False, "applicable": lo.applicable,
                                "algorithm": "", "cert_serial": "", "signer": "",
                                "sign_time": "", "reason": lo.reason,
                                "checked_at": "", "elapsed": 0.0}
    except NotImplementedError as e:
        out["legal_original"] = {"available": False, "reason": "未接入：%s" % e}
        out["signature"] = {"valid": False, "reason": "未接入：%s" % e}
    try:
        out["verify"] = p.check_invoice(rec, req).to_dict()
    except NotImplementedError as e:
        out["verify"] = {"ok": False, "message": "未接入：%s" % e}
    return out

sys.stdout.reconfigure(encoding="utf-8")

DATASET_DIRS = [
    r"D:/BaiduNetdiskDownload/考公资料大全/archive/batch_1/batch1_1",
    r"D:/BaiduNetdiskDownload/考公资料大全/archive/batch_1/batch1_2",
    r"D:/BaiduNetdiskDownload/考公资料大全/archive/batch_1/batch1_3",
]

_INDEX = {}

def build_index():
    for d in DATASET_DIRS:
        for p in glob.glob(os.path.join(d, "*.jpg")):
            _INDEX.setdefault(os.path.basename(p), p)
    return len(_INDEX)

_ENGINE = None
def engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        print("[init] 加载 RapidOCR 模型 …", flush=True)
        _ENGINE = RapidOCR()
        print("[init] 就绪", flush=True)
    return _ENGINE


def _wrap_rule(fields_raw, items_raw, items_ev):
    """把纯规则结果包装成统一结构（与混合模式同形）"""
    fields = {k: {"value": v["value"], "evidence": v.get("evidence"),
                  "source": "rule", "status": "rule_only",
                  "llm_value": "", "rule_value": v["value"]}
              for k, v in fields_raw.items()}
    items = [{"description": i["description"], "quantity": i["quantity"],
              "total_price": i["total_price"], "evidence": None, "status": "rule_only",
              "alt_quantity": "", "alt_total_price": ""} for i in items_raw]
    return fields, items, {"mode": "rule", "items_evidence": items_ev}


def run_extract(lines, img_w, extractor="rule"):
    """按 extractor 选择抽取方式，返回统一结构"""
    if extractor in ("llm", "hybrid"):
        cfg = LLMConfig()
        if not cfg.ready():
            r = extract(lines, img_w=img_w)
            f, it, m = _wrap_rule({k: v for k, v in r.items() if not k.startswith("_")},
                                  r.get("_items", []), r.get("_items_evidence"))
            m.update({"mode": "rule(fallback)", "llm_error": "未配置 LLM Key/地址", "requested": extractor})
            return f, it, m
        if extractor == "hybrid":
            return hybrid_extract(lines, img_w, cfg)
        # extractor == "llm"：纯 LLM，失败降级规则
        try:
            lf, li, meta = llm_extract(lines, cfg)
            items = [{"description": i["description"], "quantity": i["quantity"],
                      "total_price": i["total_price"], "evidence": i.get("evidence"),
                      "status": "llm_only", "alt_quantity": "", "alt_total_price": ""} for i in li]
            fields = {k: {"value": v["value"], "evidence": v.get("evidence"),
                          "source": "llm", "status": "llm_only",
                          "llm_value": v["value"], "rule_value": ""} for k, v in lf.items()}
            meta["mode"] = "llm"
            return fields, items, meta
        except Exception as e:
            r = extract(lines, img_w=img_w)
            f, it, m = _wrap_rule({k: v for k, v in r.items() if not k.startswith("_")},
                                  r.get("_items", []), r.get("_items_evidence"))
            m.update({"mode": "rule(fallback)", "llm_error": "%s: %s" % (type(e).__name__, str(e)[:160])})
            return f, it, m
    r = extract(lines, img_w=img_w)
    return _wrap_rule({k: v for k, v in r.items() if not k.startswith("_")},
                      r.get("_items", []), r.get("_items_evidence"))


def _infer_robust(arr, im, img_w):
    """推理，失败时逐级降采样重试。返回 (result, note)。

    真实场景里 OCR 失败的两个主因：① 图太大导致内存不足（MemoryError）；
    ② 非常规尺寸/通道。策略：按 100% → 75% → 50% 宽逐级重试，
    并如实记录「已降采样」，避免整份材料因单张图失败被静默跳过。
    """
    import numpy as np
    from PIL import Image
    last = None
    tries = []
    for scale in (1.0, 0.75, 0.5):
        if scale == 1.0:
            src = arr
        else:
            w = max(320, int(img_w * scale))
            h = max(1, int(im.height * (w / float(im.width))))
            src = np.array(im.resize((w, h), Image.LANCZOS))
        try:
            res, _ = engine()(src)
            note = "" if scale == 1.0 else "已降采样至 %d%% 重试成功（原图推理失败）" % int(scale * 100)
            return res, note
        except Exception as e:
            last = e
            tries.append("%d%%→%s(%s)" % (int(scale * 100), type(e).__name__, str(e)[:40]))
            if scale == 1.0:
                print("[warn] 原图推理失败，尝试降采样：%s" % type(e).__name__, flush=True)
    raise RuntimeError("OCR 推理失败，已尝试 100%%/75%%/50%% 三档：%s" % " | ".join(tries))


def run_ocr(source, extractor="rule"):
    """source: 图片路径 或 numpy 数组。返回 OCR 行 + 抽取字段（含证据坐标与放大图）"""
    import numpy as np
    from PIL import Image
    if isinstance(source, str):
        im = Image.open(source).convert("RGB")
        img_w, img_h = im.size
        arr = np.array(im)
    else:
        arr = source
        img_h, img_w = arr.shape[0], arr.shape[1]
        im = Image.fromarray(arr)

    t0 = time.time()
    try:
        res, wnote = _infer_robust(arr, im, img_w)
        if wnote:
            print("[warn] %s | %s" % (wnote, os.path.basename(source) if isinstance(source, str) else "upload"), flush=True)
    except Exception as e:
        # 单张失败不应拖垮整批：返回结构化错误，交由前端记入「失败」并给出原因
        return {"ok": False, "error": str(e), "elapsed": round(time.time() - t0, 3),
                "img_size": [img_w, img_h], "lines": [], "fields": {}, "items": []}
    lines = []
    for it in (res or []):
        box, text, score = it[0], str(it[1]), float(it[2])
        lines.append({
            "text": text,
            "conf": round(score, 4),
            # 坐标框：四点多边形，可直接用于图上描边
            "box": [[int(round(pt[0])), int(round(pt[1]))] for pt in box],
        })
    avg = round(sum(l["conf"] for l in lines) / len(lines), 4) if lines else 0.0
    ordered = sorted(lines, key=lambda l: (l["box"][0][1] // 12, l["box"][0][0]))
    ocr_done = time.time() - t0

    # 票种判定：基于 OCR 文本 + 版面特征的真实内容分类（不是文件名哈希）
    full_text = " ".join(l["text"] for l in ordered)
    vtype = classify_vtype(full_text, lines)

    # 抽取（规则 / LLM / 混合）
    try:
        fields, items, meta = run_extract(lines, img_w, extractor)
    except Exception as e:
        import traceback
        fields, items = {}, []
        meta = {"mode": "failed", "error": str(e), "trace": traceback.format_exc()[-400:]}

    # 为每个字段生成「证据放大图」：从原图裁出该字段所在区域并放大，便于肉眼核对
    for v in fields.values():
        ev = v.get("evidence") if isinstance(v, dict) else None
        if not ev or not ev.get("box"):
            continue
        x1, y1, x2, y2 = ev["box"]
        pad = 16
        crop = im.crop((max(0, x1 - pad), max(0, y1 - pad),
                        min(img_w, x2 + pad), min(img_h, y2 + pad)))
        if crop.width < 560:
            sc = 560.0 / crop.width
            crop = crop.resize((560, max(1, int(crop.height * sc))), Image.LANCZOS)
        buf = io.BytesIO(); crop.save(buf, "PNG")
        ev["crop"] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    return {
        "ok": True,
        "lines": lines,
        "text": " ".join(l["text"] for l in ordered),
        "avg_conf": avg,
        "line_count": len(lines),
        "elapsed": round(time.time() - t0, 3),
        "ocr_elapsed": round(ocr_done, 3),
        "img_size": [img_w, img_h],
        "extractor": extractor,
        "vtype": vtype,
        "extract_meta": meta,
        "fields": fields,
        "items": items,
        "items_evidence": (meta or {}).get("items_evidence"),
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send({"ok": True})

    def do_GET(self):
        # 同源托管前端：打开 http://127.0.0.1:8765 即可用，避免 file:// 跨源访问 localhost 的不稳定
        if urlparse(self.path).path in ("/", "/index.html"):
            idx = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
            if os.path.exists(idx):
                with open(idx, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                return self.wfile.write(body)
            return self._send({"ok": False, "error": "index.html 不存在"}, 404)
        if urlparse(self.path).path == "/health":
            cfg = LLMConfig()
            self._send({"ok": True, "engine": "RapidOCR (ONNXRuntime)",
                        "dataset_indexed": len(_INDEX), "model_loaded": _ENGINE is not None,
                        "extractors": ["rule", "llm", "hybrid"],
                        "llm_ready": cfg.ready(), "llm_model": cfg.describe(),
                        "compliance_provider": compliance().name,
                        "endpoints": ["/ocr", "/compliance/run", "/compliance/verify-xml"]})
        else:
            self._send({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/ocr", "/compliance/run", "/compliance/verify-xml"):
            return self._send({"ok": False, "error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception as e:
            return self._send({"ok": False, "error": "bad request: %s" % e}, 400)

        # ── 合规能力：获取法定原件 + 验签 + 查验 ──
        if path == "/compliance/verify-xml":
            try:
                sr = compliance().verify_signature(req.get("xml", ""), req.get("vtype"))
                return self._send({"ok": True, "signature": sr.to_dict()})
            except Exception as e:
                return self._send({"ok": False, "error": str(e)}, 500)
        if path == "/compliance/run":
            try:
                if req.get("record"):
                    p = compliance()
                    rec = req["record"]
                    vq = req.get("vtype")
                    lo = p.fetch_legal_original(rec, vq)
                    d = lo.to_dict(); d["content_len"] = len(lo.content or "")
                    d["content_preview"] = (lo.content or "")[:4000]; d.pop("content", None)
                    out = {"provider": p.name, "applicability": applicability(vq),
                           "legal_original": d,
                           "signature": (p.verify_signature(lo.content, vq).to_dict() if lo.available
                                         else {"valid": False, "applicable": lo.applicable,
                                               "algorithm": "", "cert_serial": "", "signer": "",
                                               "sign_time": "", "reason": lo.reason,
                                               "checked_at": "", "elapsed": 0.0}),
                           "verify": p.check_invoice(rec, vq).to_dict()}
                elif req.get("file"):
                    pth = _INDEX.get(os.path.basename(req["file"]))
                    if not pth:
                        return self._send({"ok": False, "error": "文件不在索引中"}, 404)
                    r = run_ocr(pth, req.get("extractor", "rule"))
                    vq = req.get("vtype") or ((r.get("vtype") or {}).get("requirements"))
                    out = run_compliance(r["fields"], r["items"], vq)
                else:
                    return self._send({"ok": False, "error": "需要 record 或 file"}, 400)
                return self._send({"ok": True, **out})
            except Exception as e:
                import traceback
                return self._send({"ok": False, "error": str(e),
                                   "trace": traceback.format_exc()[-400:]}, 500)

        try:
            if req.get("image_base64"):
                import numpy as np
                from PIL import Image
                raw = req["image_base64"].split(",")[-1]
                img = Image.open(io.BytesIO(base64.b64decode(raw))).convert("RGB")
                out = run_ocr(np.array(img), req.get("extractor", "rule"))
                out["source"] = "upload"
            elif req.get("file"):
                p = _INDEX.get(os.path.basename(req["file"]))
                if not p:
                    return self._send({"ok": False, "error": "文件不在数据集索引中：%s" % req["file"]}, 404)
                out = run_ocr(p, req.get("extractor", "rule"))
                out["source"] = p
            else:
                return self._send({"ok": False, "error": "需要 file 或 image_base64"}, 400)
            self._send(out)
        except Exception as e:
            import traceback
            self._send({"ok": False, "error": str(e), "trace": traceback.format_exc()[-400:]}, 500)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args()
    n = build_index()
    print("[init] 数据集索引 %d 张图片" % n, flush=True)
    print("[run ] http://%s:%d   （GET /health · POST /ocr · POST /compliance/run）" % (a.host, a.port), flush=True)
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()
