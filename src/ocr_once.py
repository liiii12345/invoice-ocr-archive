# -*- coding: utf-8 -*-
"""单张识别：把 run_ocr + 合规链路的结果以 JSON 打到 stdout。

    python src/ocr_once.py --file 发票.jpg --extractor rule --compliance 1

为什么单独一个入口：
  控制台后端（console_server.py）可能跑在一个没装 OCR 依赖的解释器上，
  这时它会 spawn 到 --python 指定的解释器来跑本文件，再读回 JSON。

输出约定：
  stdout 只有一行 JSON（run_ocr 内部的 [warn] 等一律重定向到 stderr，免得污染 JSON）。
"""
import os, sys, json, argparse, io, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="图片路径")
    ap.add_argument("--extractor", default="rule", choices=["rule", "llm", "hybrid"])
    ap.add_argument("--compliance", default="0", help="1 = 顺带跑合规链路（取原件/验签/查验）")
    a = ap.parse_args()

    from ocr_service import run_ocr, run_compliance

    out = {}
    # run_ocr 内部会 print [warn]，必须挪到 stderr，否则 stdout 不是合法 JSON
    with contextlib.redirect_stdout(sys.stderr):
        r = run_ocr(a.file, a.extractor)
    out = r if isinstance(r, dict) else {"ok": False, "error": "run_ocr 返回异常"}

    if a.compliance == "1" and out.get("ok"):
        try:
            req = (out.get("vtype") or {}).get("requirements")
            with contextlib.redirect_stdout(sys.stderr):
                out["compliance"] = run_compliance(
                    out.get("fields") or {}, out.get("items") or [], req)
        except Exception as e:  # noqa 合规失败不该让识别结果丢掉
            out["compliance"] = {"error": "%s: %s" % (type(e).__name__, e)}

    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
