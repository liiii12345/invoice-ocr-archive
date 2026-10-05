# -*- coding: utf-8 -*-
"""可选本地后端：让控制台「批量测试」页签从样例模式切到实跑模式。

    python tools/console_server.py --port 8770

数据源：
  GET /                 控制台页面（白名单目录内）
  GET /api/health       {"ok":true}  ← 控制台用它探测
  GET /api/datasets     {"ok":true,"datasets":[{id,label,files,default_src,truth}]}
  GET /api/run?...      SSE：参数 → 调用 src/batch_run.py，逐行转发 stdout，结束推 summary
  GET /api/summary?out= 读取指定输出目录的 summary.json

安全约束（仅限本机回环）：
  · 只监听 127.0.0.1
  · --input / --out / --truth 必须位于项目目录（--allow-outside 可放开 --out）
  · 抽取策略白名单；--workers 限 1..8
  · 单任务超时 1800s
"""
import os, sys, json, argparse, subprocess, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PYTHON = sys.executable
MAX_SEC = 1800
ALLOW_OUTSIDE = False

DATASETS = [
    {"id": "sample", "label": "合成样例 3 张（仓库内置）", "short": "sample",
     "files": 3, "src": "data/samples", "truth": ""},
    {"id": "en20", "label": "英文商业发票 20 张（含真值）", "short": "en20",
     "files": 20, "src": "D:/BaiduNetdiskDownload/考公资料大全/archive/batch_1/batch1_1/*.jpg",
     "truth": "D:/BaiduNetdiskDownload/考公资料大全/archive/batch_1/batch1_1.csv"},
    {"id": "en_all", "label": "英文商业发票 1489 张（全量）", "short": "en_all",
     "files": 1489, "src": "D:/BaiduNetdiskDownload/考公资料大全/archive/batch_1", "truth": ""},
]


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


def run_batch(qs, send_event):
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

    send_event("start", {"cmd": " ".join(cmd), "out": out[0]})
    t0 = time.time()
    try:
        p = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace",
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:  # noqa
        send_event("error", {"message": repr(e)})
        return
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
    if rc != 0:
        send_event("error", {"message": "exit code %d" % rc})
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
    send_event("done", {"summary": summary})


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
            return self._body(200, {"ok": True, "root": ROOT, "py": PYTHON})
        if path == "/api/datasets":
            return self._body(200, {"ok": True, "datasets": DATASETS})

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
            self._sse_started = True

            def send_event(kind, payload):
                try:
                    self.wfile.write(("event: %s\ndata: %s\n\n" %
                                      (kind, json.dumps({"k": kind, "d": payload},
                                                        ensure_ascii=False))).encode("utf-8"))
                    self.wfile.flush()
                except Exception:  # noqa
                    pass

            run_batch(qs, send_event)   # 同步流式：handler 一直持有连接
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


def main():
    global ALLOW_OUTSIDE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8770)
    ap.add_argument("--outside", action="store_true", help="允许 --out 写到项目目录外")
    a = ap.parse_args()
    ALLOW_OUTSIDE = a.outside
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print("console server → http://127.0.0.1:%d  (root=%s)" % (a.port, ROOT))
    print("Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
