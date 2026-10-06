# -*- coding: utf-8 -*-
"""可选本地后端：让控制台「批量测试」页签从样例模式切到实跑模式。

    python tools/console_server.py --port 8770
    python tools/console_server.py --scan D:/data/invoices --scan E:/more   # 让数据集下拉自动列出

数据源：
  GET /                  控制台页面
  GET /api/health        {"ok":true}  ← 控制台用它探测
  GET /api/datasets      内置样例 + --scan 扫到的本机数据集（?refresh=1 强制重扫）
  GET /api/run?...       SSE：参数 → 调用 src/batch_run.py，逐行转发 stdout，结束推 summary
  GET /api/stop?task=    中止正在跑的任务（杀进程树）
  GET /api/results?out=  读 out/results.csv，返回行（供结果表 / 字段对照 / 档案视图跳转）
  GET /api/summary?out=  读 out/summary.json

安全约束（仅限本机回环）：
  · 只监听 127.0.0.1
  · --out 必须落在项目目录内（--outside 可放开）；--input/--truth/--base 放开读，
    因为真实数据集通常在仓库外
  · 抽取策略白名单；--workers 限 1..8
  · 单任务超时 1800s
  · LLM Key 只在请求里以环境变量注入子进程，**不写任何文件、不进日志**
"""
import os, sys, json, argparse, subprocess, time, urllib.parse, csv as _csv, re as _re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
# OCR 依赖（rapidocr 等）往往装在专用 venv 里，主解释器不一定有，可用 --python 指定
PYTHON = os.environ.get("OCR_PYTHON") or sys.executable
MAX_SEC = 1800
ALLOW_OUTSIDE = False
MAX_ROWS = 2000          # /api/results 最多返回多少行
SCAN_MAX_DEPTH = 3       # 数据集扫描最深几层
SCAN_MAX_ITEMS = 60      # 数据集下拉最多列多少条
MAX_UPLOAD_MB = 30       # 单文件上限
UPLOAD_DIR = os.path.join(ROOT, "uploads")

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
UPLOAD_EXT = IMG_EXT | {".pdf"}


def _has_ocr():
    """当前解释器是否装齐 OCR 依赖；没有就得 spawn 到 --python 指向的解释器。"""
    try:
        import importlib
        for m in ("numpy", "PIL", "rapidocr_onnxruntime"):
            importlib.import_module(m)
        return True
    except Exception:  # noqa
        return False


HAS_OCR = _has_ocr()

# 仓库内置样例（公开仓库里不落任何本机路径；本机数据集靠 --scan 或 OCR_SCAN_ROOTS 探测）
BUILTIN_DATASETS = [
    {"id": "sample", "label": "合成样例 3 张（仓库内置）", "short": "sample",
     "files": 3, "src": "data/samples", "truth": "", "builtin": True},
]

SCAN_ROOTS = []          # 由 --scan / OCR_SCAN_ROOTS 填充
_RUNNING = {}            # task_id -> subprocess.Popen，供 /api/stop 使用
_DS_CACHE = {"ts": 0.0, "items": None}


def safe_abs(p):
    """路径必须落在项目目录内（Windows 大小写不敏感，统一 lower 比较）。"""
    if not p:
        return None
    ap = os.path.abspath(os.path.join(ROOT, p if not os.path.isabs(p) else p))
    rp = os.path.abspath(ROOT).lower()
    if ap.lower().startswith(rp):
        return ap
    if ALLOW_OUTSIDE:
        return ap
    return None


def _looks_like_truth(csv_path):
    """真值表判定：首行包含 name/file/发票 之类列名。"""
    try:
        with open(csv_path, encoding="utf-8-sig", errors="ignore") as f:
            head = f.readline().lower()
    except Exception:  # noqa
        return False
    return ("name" in head) or ("file" in head) or ("发票" in head) or ("invoice" in head)


def scan_datasets(roots, max_depth=SCAN_MAX_DEPTH, min_files=1):
    """扫本机图片目录，自动配对同名真值 csv。

    只数文件、不读内容，1489 张的量级也是毫秒级；扫到的目录按文件数降序，
    截断到 SCAN_MAX_ITEMS 条，避免下拉被几千个子目录塞满。
    """
    found = []
    for root in roots:
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            depth = dirpath[len(root):].count(os.sep)
            if depth >= max_depth:
                dirnames[:] = []
            imgs = [f for f in filenames if os.path.splitext(f)[1].lower() in IMG_EXT]
            if len(imgs) < min_files:
                continue
            # 真值表配对（顺序即优先级，绝不「就近乱配」——配错比不配更糟）：
            #   ① 同目录内的 csv（且列名像真值表）
            #   ② 父目录下与本目录同名的 csv，如 batch1_2/ ↔ batch1_2.csv
            truth = ""
            for f in filenames:
                if f.lower().endswith(".csv"):
                    c = os.path.join(dirpath, f)
                    if _looks_like_truth(c):
                        truth = c
                        break
            if not truth:
                parent = os.path.dirname(dirpath)
                same = os.path.join(parent, os.path.basename(dirpath) + ".csv")
                if os.path.isfile(same) and _looks_like_truth(same):
                    truth = same
            rel = os.path.relpath(dirpath, root)
            base = os.path.basename(root.rstrip("\\/")) or root
            label = ("扫描 · " + rel) if rel != "." else ("扫描 · " + base)
            found.append({
                "id": "scan_" + str(abs(hash(dirpath)) % 10**8),
                "label": "%s（%d 张%s）" % (label, len(imgs), "，含真值" if truth else ""),
                "short": os.path.basename(dirpath) or base,
                "files": len(imgs),
                "src": dirpath,
                "truth": truth,
                "scan": True,
            })
    found.sort(key=lambda d: -d["files"])
    return found[:SCAN_MAX_ITEMS]


def all_datasets(force=False):
    if not force and _DS_CACHE["items"] is not None:
        return _DS_CACHE["items"]
    items = list(BUILTIN_DATASETS)
    if SCAN_ROOTS:
        try:
            items += scan_datasets(SCAN_ROOTS)
        except Exception as e:  # noqa  扫描失败不该让整个后端 500
            sys.stderr.write("scan failed: %r\n" % (e,))
    _DS_CACHE["ts"] = time.time()
    _DS_CACHE["items"] = items
    return items


def read_results(out):
    """读 results.csv（utf-8-sig，写出来的就是 -sig，读必须带 -sig 否则首列名带 BOM）。"""
    fp = os.path.join(safe_abs(out) or os.path.abspath(out), "results.csv")
    if not os.path.isfile(fp):
        return None, "no results.csv"
    try:
        with open(fp, encoding="utf-8-sig", errors="replace", newline="") as f:
            rows = list(_csv.DictReader(f))
    except Exception as e:  # noqa
        return None, repr(e)
    return rows[:MAX_ROWS], None


def parse_multipart(body, boundary):
    """极简 multipart/form-data 解析：只取普通字段与文件。

    Python 3.13 已经没有 cgi 模块了，为这一个接口引入第三方又太重，
    就按 boundary 切段自己解——上传只走本机回环，够用且可控。
    """
    out = {}
    delim = b"--" + boundary.encode("utf-8")
    for part in body.split(delim):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if b"\r\n\r\n" not in part:
            continue
        head, data = part.split(b"\r\n\r\n", 1)
        head = head.decode("utf-8", "ignore")
        m = _re.search(r'name="([^"]*)"', head)
        if not m:
            continue
        name = m.group(1)
        fm = _re.search(r'filename="([^"]*)"', head)
        if fm:
            out[name] = {"filename": fm.group(1), "data": data}
        else:
            out[name] = data.decode("utf-8", "ignore")
    return out


def _llm_env(api_key=None, base_url=None, model=None):
    env = os.environ.copy()
    if api_key:
        env["OCR_LLM_API_KEY"] = api_key
        env["DASHSCOPE_API_KEY"] = api_key
    if base_url:
        env["OCR_LLM_BASE_URL"] = base_url
    if model:
        env["OCR_LLM_MODEL"] = model
    return env


def run_ocr_file(path, extractor="rule", with_compliance=False,
                 api_key=None, base_url=None, model=None):
    """识别一张图。有 OCR 依赖就进程内跑，没有就 spawn 到 --python 的解释器。"""
    if HAS_OCR:
        import io as _io, contextlib
        if os.path.join(ROOT, "src") not in sys.path:
            sys.path.insert(0, os.path.join(ROOT, "src"))
        from ocr_service import run_ocr, run_compliance
        noise = _io.StringIO()
        try:
            with contextlib.redirect_stdout(noise):
                r = run_ocr(path, extractor)
                if with_compliance and isinstance(r, dict) and r.get("ok"):
                    req = (r.get("vtype") or {}).get("requirements")
                    r["compliance"] = run_compliance(
                        r.get("fields") or {}, r.get("items") or [], req)
        except Exception as e:  # noqa
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        r["_stdout"] = noise.getvalue()[-2000:]
        return r

    cmd = [PYTHON, "-u", os.path.join("src", "ocr_once.py"),
           "--file", path, "--extractor", extractor]
    if with_compliance:
        cmd += ["--compliance", "1"]
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                           env=_llm_env(api_key, base_url, model),
                           encoding="utf-8", errors="replace", timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:  # noqa
        return {"ok": False, "error": repr(e)}
    if p.returncode != 0:
        return {"ok": False, "error": "exit %d" % p.returncode,
                "stderr": (p.stderr or "")[-1200:]}
    try:
        return json.loads(p.stdout)
    except Exception:  # noqa
        return {"ok": False, "error": "返回不是 JSON", "stderr": (p.stderr or "")[-1200:],
                "stdout_head": (p.stdout or "")[:400]}


def provider_info():
    return {
        "provider": os.environ.get("OCR_COMPLIANCE_PROVIDER", "mock"),
        "python": PYTHON,
        "has_ocr": HAS_OCR,
        "spawn": not HAS_OCR,
        "llm_ready": bool(os.environ.get("OCR_LLM_API_KEY")
                          or os.environ.get("DASHSCOPE_API_KEY")
                          or os.environ.get("OPENAI_API_KEY")),
        "llm_model": os.environ.get("OCR_LLM_MODEL") or "qwen-flash",
        "llm_base_url": os.environ.get("OCR_LLM_BASE_URL") or "",
        "scan_roots": SCAN_ROOTS,
    }


def capabilities():
    """告诉前端「哪些能力现在真能用」——不能用的就别在界面上装作能用。"""
    pi = provider_info()
    return {"ok": True, "server": True, **pi, "abilities": {
        "ocr": True,                                   # 有后端就能识别（可能走 spawn）
        "ocr_inprocess": pi["has_ocr"],
        "vtype": True,
        "batch": True,
        "extract_llm": pi["llm_ready"],
        "compliance_xml": pi["provider"] != "",
        "compliance_sign": True,                       # Mock 模式下用的是真 XMLDSig 验签
        "compliance_verify": True,
    }}


def run_batch(qs, send_event, task_id=""):
    """构造命令行并执行；逐行转发 stdout。"""
    inputs = qs.get("input") or []
    if not inputs:
        send_event("error", {"message": "缺少 --input"})
        return
    cmd = [PYTHON, "-u", os.path.join("src", "batch_run.py")]
    for src in inputs:
        cmd += ["--input", src]
    for key, flag in (("limit", "--limit"), ("offset", "--offset"), ("workers", "--workers")):
        if qs.get(key):
            cmd += [flag, qs[key][0]]
    for key, flag in (("truth", "--truth"), ("base", "--base")):
        if qs.get(key):
            cmd += [flag, qs[key][0]]
    if qs.get("extractor") in ("rule", "llm", "hybrid"):
        cmd += ["--extractor", qs.get("extractor")[0]]
    if qs.get("resume"):
        cmd += ["--resume"]
    if qs.get("detail"):
        cmd += ["--detail"]
    out = qs.get("out") or ["batch_out"]
    outdir = os.path.abspath(os.path.join(ROOT, out[0]))
    os.makedirs(outdir, exist_ok=True)
    cmd += ["--out", outdir]

    # LLM 凭据：只在本次子进程的环境变量里，不落盘、不进日志、不回显
    env = os.environ.copy()
    masked = {}
    if qs.get("apiKey") and qs["apiKey"][0]:
        env["OCR_LLM_API_KEY"] = qs["apiKey"][0]
        env["DASHSCOPE_API_KEY"] = qs["apiKey"][0]
        masked["OCR_LLM_API_KEY"] = "已注入（%d 位，不明文回显）" % len(qs["apiKey"][0])
    if qs.get("baseUrl") and qs["baseUrl"][0]:
        env["OCR_LLM_BASE_URL"] = qs["baseUrl"][0]
        masked["OCR_LLM_BASE_URL"] = qs["baseUrl"][0]
    if qs.get("model") and qs["model"][0]:
        env["OCR_LLM_MODEL"] = qs["model"][0]
        masked["OCR_LLM_MODEL"] = qs["model"][0]

    # 命令行回显里绝不能出现 Key
    shown = " ".join(cmd)
    send_event("start", {"cmd": shown, "out": out[0], "task": task_id, "env": masked})
    t0 = time.time()
    try:
        # bufsize=1 是行缓冲：默认 8KB 块缓冲会把进度行全积压在父进程里，
        # 表现为「进度条半天不动、一结束全刷出来」，中止时还会整段丢日志。
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, bufsize=1,
                             stderr=subprocess.STDOUT, text=True, env=env,
                             encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:  # noqa
        send_event("error", {"message": repr(e)})
        return
    if task_id:
        _RUNNING[task_id] = p
    try:
        for line in p.stdout:
            line = line.rstrip("\n")
            if line:
                send_event("log", {"line": line})
        rc = p.wait(timeout=max(1, MAX_SEC - int(time.time() - t0)))
    except subprocess.TimeoutExpired:
        p.kill()
        send_event("error", {"message": "timeout"})
        return
    finally:
        _RUNNING.pop(task_id, None)
    if rc != 0:
        send_event("error", {"message": "exit code %d（也可能是被中止）" % rc})
        return
    sp = os.path.join(outdir, "summary.json")
    summary = {}
    if os.path.isfile(sp):
        try:
            with open(sp, encoding="utf-8") as f:
                summary = json.load(f)
        except Exception:  # noqa
            summary = {}
    summary.setdefault("elapsed", round(time.time() - t0, 1))
    send_event("done", {"summary": summary, "out": out[0]})


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ── helpers ──
    def _body(self, code, obj, ctype="application/json; charset=utf-8"):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def _sse_init(self):
        # SSE 必须让 handler 保持在请求内流式输出：一旦 do_GET 返回，
        # BaseHTTPRequestHandler.finish() 会关闭 wfile，子线程写入全丢。
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.close_connection = True
        self.end_headers()

    # ── routes ──
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        qs = urllib.parse.parse_qs(u.query)

        if path == "/api/health":
            return self._body(200, {"ok": True, "root": ROOT, "py": PYTHON,
                                    "scan_roots": SCAN_ROOTS})

        if path == "/api/provider":
            return self._body(200, {"ok": True, **provider_info()})

        if path == "/api/capabilities":
            return self._body(200, capabilities())

        if path == "/api/datasets":
            return self._body(200, {"ok": True,
                                    "datasets": all_datasets(force=bool(qs.get("refresh")))})

        if path == "/api/stop":
            tid = (qs.get("task") or [""])[0]
            p = _RUNNING.get(tid)
            if p is None:
                return self._body(404, {"ok": False, "message": "没有在跑的任务 " + tid})
            try:
                p.terminate()
            except Exception:  # noqa
                try:
                    p.kill()
                except Exception:  # noqa
                    pass
            _RUNNING.pop(tid, None)
            return self._body(200, {"ok": True, "stopped": tid})

        if path == "/api/results":
            out = (qs.get("out") or ["batch_out"])[0]
            rows, err = read_results(out)
            if err:
                return self._body(404, {"ok": False, "message": err})
            return self._body(200, {"ok": True, "out": out, "rows": rows})

        if path == "/api/run":
            # 参数校验 + 路径白名单
            # 读路径（--input/--truth/--base）放开：真实数据集常在仓库外；
            # 写路径（--out）必须落在项目目录内。
            inputs = qs.get("input") or []
            bad = []
            if not inputs:
                bad.append("input")
            for k in ("truth", "base"):
                if qs.get(k) and not os.path.isfile(qs[k][0]):
                    bad.append(k)
            if qs.get("out") and not ALLOW_OUTSIDE and not safe_abs(qs["out"][0]):
                bad.append("out")
            w = qs.get("workers", ["1"])[0]
            try:
                w = int(w)
                if w < 1 or w > 8:
                    bad.append("workers")
            except ValueError:
                bad.append("workers")
            if qs.get("extractor", [""])[0] not in ("", "rule", "llm", "hybrid"):
                bad.append("extractor")
            if bad:
                return self._body(400, {"ok": False, "rejected": bad})

            self._sse_init()

            def send_event(kind, payload):
                try:
                    self.wfile.write(("event: %s\ndata: %s\n\n" %
                                      (kind, json.dumps({"k": kind, "d": payload},
                                                        ensure_ascii=False))).encode("utf-8"))
                    self.wfile.flush()
                except Exception:  # noqa
                    pass

            task_id = (qs.get("task") or [""])[0] or ("t%d" % int(time.time() * 1000))
            run_batch(qs, send_event, task_id)   # 同步流式：handler 一直持有连接
            return

        if path == "/api/summary":
            out = (qs.get("out") or ["batch_out"])[0]
            fp = os.path.join(safe_abs(out) or os.path.abspath(out), "summary.json")
            if not os.path.isfile(fp):
                return self._body(404, {"ok": False, "message": "no summary.json"})
            with open(fp, encoding="utf-8") as f:
                return self._body(200, json.load(f))

        if path in ("/", "/index.html", "/demo_console.html"):
            fp = os.path.join(ROOT, "demo_console.html")
            if not os.path.isfile(fp):
                return self._body(404, {"ok": False, "message": "no console"})
            data = open(fp, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return

        return self._body(404, {"ok": False, "message": "not found"})

    # ── POST：上传识别 / 票种判定 / 独立验签 ──
    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return self._body(400, {"ok": False, "message": "空请求体"})
        if n > (MAX_UPLOAD_MB + 8) * 1024 * 1024:
            return self._body(413, {"ok": False, "message": "超过 %d MB" % MAX_UPLOAD_MB})
        body = self.rfile.read(n)

        ctype = self.headers.get("Content-Type") or ""

        # ── 上传识别 ──
        if path == "/api/ocr":
            m = _re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", ctype)
            if not m:
                return self._body(400, {"ok": False, "message": "需要 multipart/form-data"})
            boundary = (m.group(1) or m.group(2) or "").strip()
            fields = parse_multipart(body, boundary)
            f = fields.get("file")
            if not isinstance(f, dict) or not f.get("data"):
                return self._body(400, {"ok": False, "message": "缺少 file"})
            extractor = str(fields.get("extractor") or "rule")
            if extractor not in ("rule", "llm", "hybrid"):
                extractor = "rule"
            with_compliance = str(fields.get("compliance") or "0") in ("1", "true", "on")

            orig = os.path.basename(f.get("filename") or "upload")
            ext = os.path.splitext(orig)[1].lower()
            if ext not in UPLOAD_EXT:
                return self._body(400, {"ok": False,
                                        "message": "不支持的格式 %s（支持 %s）"
                                                   % (ext, "/".join(sorted(UPLOAD_EXT)))})
            if len(f["data"]) > MAX_UPLOAD_MB * 1024 * 1024:
                return self._body(413, {"ok": False, "message": "文件超过 %d MB" % MAX_UPLOAD_MB})

            # 落盘名去干净：只留扩展名，避免路径穿越和中文名踩坑
            os.makedirs(UPLOAD_DIR, exist_ok=True)
            safe = "u%s%s" % (int(time.time() * 1000), ext)
            fp = os.path.join(UPLOAD_DIR, safe)
            with open(fp, "wb") as fh:
                fh.write(f["data"])

            t0 = time.time()
            r = run_ocr_file(fp, extractor, with_compliance,
                             fields.get("apiKey"), fields.get("baseUrl"), fields.get("model"))
            if isinstance(r, dict):
                r["_file"] = {"name": orig, "saved": safe, "bytes": len(f["data"])}
                r["_server_elapsed"] = round(time.time() - t0, 2)
            return self._body(200, r if isinstance(r, dict)
                              else {"ok": False, "error": str(r)})

        # ── 票种判定（不给图，只给文本）──
        if path == "/api/vtype":
            try:
                j = json.loads(body.decode("utf-8", "ignore"))
            except Exception:  # noqa
                return self._body(400, {"ok": False, "message": "body 不是 JSON"})
            text = str(j.get("text") or "")
            if not text.strip():
                return self._body(400, {"ok": False, "message": "text 为空"})
            try:
                if os.path.join(ROOT, "src") not in sys.path:
                    sys.path.insert(0, os.path.join(ROOT, "src"))
                from vtype_classify import classify
                return self._body(200, {"ok": True, "vtype": classify(text)})
            except Exception as e:  # noqa
                return self._body(500, {"ok": False, "message": "%s: %s" % (type(e).__name__, e)})

        # ── 独立验签：直接粘一段 XML 进来 ──
        if path == "/api/verify_xml":
            try:
                j = json.loads(body.decode("utf-8", "ignore"))
            except Exception:  # noqa
                return self._body(400, {"ok": False, "message": "body 不是 JSON"})
            xml = str(j.get("xml") or "")
            if not xml.strip():
                return self._body(400, {"ok": False, "message": "xml 为空"})
            try:
                if os.path.join(ROOT, "src") not in sys.path:
                    sys.path.insert(0, os.path.join(ROOT, "src"))
                from compliance_provider import get_provider
                p = get_provider()
                res = p.verify_signature(xml, j.get("req"))
                return self._body(200, {"ok": True, "provider": p.name, **res.to_dict()})
            except Exception as e:  # noqa
                return self._body(500, {"ok": False, "message": "%s: %s" % (type(e).__name__, e)})

        return self._body(404, {"ok": False, "message": "not found"})


def main():
    global ALLOW_OUTSIDE, SCAN_ROOTS
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--outside", action="store_true", help="允许 --out 写到项目目录外")
    ap.add_argument("--scan", action="append", default=[],
                    help="数据集扫描根目录，可重复传；也可用环境变量 OCR_SCAN_ROOTS（分号分隔）")
    ap.add_argument("--python", default=None,
                    help="跑 batch_run.py 用的解释器；默认 OCR_PYTHON 环境变量，再退回当前解释器")
    a = ap.parse_args()
    global PYTHON
    if a.python:
        PYTHON = a.python
    ALLOW_OUTSIDE = a.outside
    roots = list(a.scan)
    for r in os.environ.get("OCR_SCAN_ROOTS", "").split(";"):
        if r.strip():
            roots.append(r.strip())
    SCAN_ROOTS = roots
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print("console server → http://127.0.0.1:%d  (root=%s)" % (a.port, ROOT))
    if SCAN_ROOTS:
        print("扫描根目录：%s" % "; ".join(SCAN_ROOTS))
    else:
        print("未配置扫描根目录：数据集下拉只有内置样例。"
              "用 --scan <目录> 或 OCR_SCAN_ROOTS 让本机数据集自动出现。")
    print("Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
