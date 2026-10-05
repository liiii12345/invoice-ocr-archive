#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把【合成样例数据】注入 demo_console.html，产出公开仓库版控制台。

本地那份 demo_console.html 里内嵌的是真实 ingest 结果（真实影像 base64 + 真实金额），
公开仓库必须换成 tools/make_demo_data.py 生成的合成样例。本脚本只做一件事：
替换 `const DATA = {...};` 这一行，其余 DOM / 样式 / 脚本一律不动。

用法：
    python tools/build_console.py <本地demo_console.html> <输出路径>
"""
import os, re, sys, json

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC_JSON = os.path.join(ROOT, "data", "synthetic_demo_data.json")


def build(src_html, out_html):
    with open(SRC_JSON, encoding="utf-8") as f:
        data = json.load(f)
    with open(src_html, encoding="utf-8") as f:
        html = f.read()

    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # 防御：数据里出现 </ 会提前闭合 <script>，需转义
    body = body.replace("</", "<\\/")

    new_line = "const DATA = %s;" % body
    pattern = re.compile(r"^const DATA = .*?;\s*$", re.MULTILINE)
    if not pattern.search(html):
        raise SystemExit("未找到 const DATA = ... 行，注入失败")

    html2, n = pattern.subn(lambda m: new_line, html, count=1)
    if n != 1:
        raise SystemExit("注入异常，替换次数 = %d" % n)

    # 控制台顶部数据来源说明改成合成口径
    html2 = html2.replace(
        "数据内嵌自 archive_demo_data.json", "数据内嵌自 data/synthetic_demo_data.json（合成样例）")
    html2 = html2.replace("archive_demo_data.json", "synthetic_demo_data.json")

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html2)
    print("built:", out_html, os.path.getsize(out_html), "bytes")


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "..", "demo_console.html")
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "demo_console.html")
    build(os.path.abspath(src), os.path.abspath(out))
