# -*- coding: utf-8 -*-
"""向控制台注入「识别」与「合规」两个视图，并把顶栏导航扩成四个（幂等）。

必须在 tools/build_batch_ui.py 之后跑：它复用批量页的 esc / toast / probeServer / bLlm /
bEtaSec 等函数，并把两按钮的 segmented 换成四按钮。

    python tools/build_workspace.py
"""
import os, re

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "..", "demo_console.html")

MARK_CSS = "/*WORK_CSS*/"
MARK_CSS_END = "/*WORK_CSS_END*/"
MARK_DOM = "<!--WORK_DOM-->"
MARK_DOM_END = "<!--/WORK_DOM-->"
MARK_JS = "/*WORK_JS*/"
MARK_JS_END = "/*WORK_JS_END*/"
MARK_SEG = "<!--SEG4-->"


def _read(fn):
    with open(os.path.join(HERE, fn), encoding="utf-8") as f:
        return f.read()


WJS = _read("_workspace.js")

CSS = r"""
/*WORK_CSS*/
  /* ── 识别工作台 ── */
  .wpane,.cpane{padding:22px 28px 60px;max-width:1240px;display:flex;flex-direction:column;gap:16px}
  .drop{border:1.5px dashed var(--line);border-radius:12px;background:#fafbfc;
    padding:44px 20px;text-align:center;color:var(--ink-3);cursor:pointer;transition:.15s}
  .drop:hover,.drop.over{border-color:var(--accent);background:#f5f8ff;color:var(--ink-2)}
  .drop svg{width:38px;height:38px;margin-bottom:10px;opacity:.55}
  .drop .t{font-size:15px;font-weight:600;color:var(--ink)}
  .drop .s{font-size:12px;margin-top:6px}
  .wset{display:flex;align-items:center;gap:16px;flex-wrap:wrap}
  .wset .frow{grid-template-columns:auto auto;gap:8px;margin:0}
  .wset select{height:31px;border:1px solid var(--line);border-radius:6px;background:#fafbfc;
    padding:0 9px;font-size:12.5px;color:var(--ink);font-family:var(--sans);min-width:300px}
  .wset .spacer{flex:1}
  .witem{display:flex;align-items:center;gap:10px;padding:9px 16px;border-bottom:1px solid var(--line-2);
    cursor:pointer;border-left:2px solid transparent}
  .witem:hover{background:var(--hover)}
  .witem.active{background:var(--sel);border-left-color:var(--accent)}
  .witem img{width:42px;height:42px;object-fit:cover;border-radius:5px;border:1px solid var(--line);flex:none}
  .witem .nothumb{width:42px;height:42px;border-radius:5px;border:1px solid var(--line);flex:none;
    display:flex;align-items:center;justify-content:center;font-size:11px;color:var(--ink-3);background:#fafbfc}
  .witem .m{flex:1;min-width:0}
  .witem .n{font-size:12.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .witem .s{font-size:11px;color:var(--ink-3);margin-top:2px;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
  .witem .x{border:none;background:none;color:var(--ink-3);cursor:pointer;font-size:16px;line-height:1;padding:2px 4px}
  .witem .x:hover{color:#e0574f}
  .wview{display:grid;grid-template-columns:340px 1fr;gap:18px}
  @media (max-width:1100px){.wview{grid-template-columns:1fr}}
  .wprev{width:100%;border:1px solid var(--line);border-radius:8px;display:block}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:12px}
  .grid2 .cell{background:#fafbfc;border:1px solid var(--line-2);border-radius:6px;padding:7px 9px}
  .grid2 .k{font-size:10.5px;color:var(--ink-3)}
  .grid2 .v{font-size:12.5px;margin-top:2px;word-break:break-all}
  table.dt.fld td.k{font-size:11.5px;color:var(--ink-3);white-space:nowrap}
  table.dt.fld td.v{font-size:12.5px;max-width:340px}
  img.ev{height:40px;border:1px solid var(--line);border-radius:4px;display:block}
  .ocrlines{max-height:340px;overflow:auto;font-family:var(--mono);font-size:11.5px}
  .ocrlines .ln{display:flex;gap:10px;padding:3px 0;border-bottom:1px solid var(--line-2);align-items:baseline}
  .ocrlines i{color:var(--ink-3);font-style:normal;width:34px;text-align:right;flex:none}
  .ocrlines span{flex:1;word-break:break-all}
  .ocrlines b{color:var(--ink-3);font-weight:500;flex:none}
  /* ── 合规页 ── */
  .cgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
  .ccard{border:1px solid var(--line-2);border-radius:8px;padding:12px 14px;background:#fafbfc}
  .ccard .h{font-size:12.5px;font-weight:600;margin-bottom:9px;display:flex;align-items:center;gap:8px}
  .ccard .r{display:flex;justify-content:space-between;gap:12px;font-size:11.5px;padding:3px 0}
  .ccard .r span{color:var(--ink-3);flex:none}
  .ccard .r b{font-family:var(--mono);font-weight:500;text-align:right;word-break:break-all}
  .offline{padding:8px 4px}
  .offline .h{font-size:13.5px;font-weight:600;color:var(--ink);margin-bottom:6px}
  .offline .d{font-size:12.5px;color:var(--ink-2);margin:6px 0}
  textarea{width:100%;border:1px solid var(--line);border-radius:7px;background:#fafbfc;
    padding:10px 12px;font-family:var(--mono);font-size:12px;color:var(--ink);outline:none;resize:vertical}
  textarea:focus{border-color:var(--accent);background:#fff}
/*WORK_CSS_END*/
"""

DOM = """
<!--WORK_DOM-->
<div class="shell hidden" id="shellWork">
  <aside class="side">
    <div class="side-h"><span class="t">待识别</span><span class="c" id="wCnt">0</span></div>
    <div class="filters">
      <div class="row">
        <button class="btn" id="wPick" style="flex:1">选择文件</button>
        <button class="btn" id="wClear" style="flex:1">清空</button>
      </div>
      <div class="lbl" id="wConnBox" style="display:flex;align-items:center;gap:6px">
        后端 <span class="pill" id="wConn">检测中…</span>
      </div>
    </div>
    <div class="list" id="wList"></div>
  </aside>
  <main class="main"><div id="wBody"></div></main>
</div>
<div class="shell hidden" id="shellComp">
  <main class="main"><div id="cBody"></div></main>
</div>
<!--/WORK_DOM-->
"""

SEG = ('<!--SEG4--><div class="seg" style="margin-right:4px">'
       '<button class="on" data-v="work">识别</button>'
       '<button data-v="doc">档案</button>'
       '<button data-v="batch">批量</button>'
       '<button data-v="comp">合规</button>'
       '</div><!--/SEG4-->')


def build():
    with open(os.path.abspath(TARGET), encoding="utf-8") as f:
        html = f.read()

    js = MARK_JS + "\n" + WJS + "\n" + MARK_JS_END

    # ── CSS ──
    if MARK_CSS in html:
        html, n = re.subn(r"\n/\*WORK_CSS\*/[\s\S]*?\n/\*WORK_CSS_END\*/",
                          lambda m: CSS.rstrip("\n"), html, count=1)
        if not n:
            html, n = re.subn(r"\n/\*WORK_CSS\*/[\s\S]*?(?=\n</style>)",
                              lambda m: CSS.rstrip("\n"), html, count=1)
    else:
        html = html.replace("</style>", CSS + "</style>", 1)

    # ── DOM ──
    if MARK_DOM in html:
        html, n = re.subn(r"\n<!--WORK_DOM-->[\s\S]*?\n<!--/WORK_DOM-->",
                          lambda m: DOM.rstrip("\n"), html, count=1)
        if not n:
            html, n = re.subn(r"\n<!--WORK_DOM-->[\s\S]*?(?=\n<div class=\"shell\" id=\"shellDoc\">)",
                              lambda m: DOM.rstrip("\n"), html, count=1)
    else:
        html = html.replace('<div class="shell" id="shellDoc">', DOM + "\n" + '<div class="shell" id="shellDoc">', 1)

    # ── 顶栏导航：两按钮 → 四按钮 ──
    if MARK_SEG not in html:
        html, n = re.subn(r'<div class="seg"[^>]*>.*?</div>',
                          lambda m: SEG, html, count=1, flags=re.S)
        if not n:
            html = html.replace('<span class="envtag" id="envtag">', SEG + '<span class="envtag" id="envtag">', 1)
    else:
        html = re.sub(r"\n<!--SEG4-->[\s\S]*?<!--/SEG4-->", lambda m: SEG, html, count=1)

    # ── JS ──
    if MARK_JS in html:
        html, n = re.subn(r"\n/\*WORK_JS\*/[\s\S]*?\n/\*WORK_JS_END\*/",
                          lambda m: "\n" + js, html, count=1)
        if not n:
            html, n = re.subn(r"\n/\*WORK_JS\*/[\s\S]*?(?=\n</script>)",
                              lambda m: "\n" + js, html, count=1)
    else:
        html = html.replace("</script>", js + "\n</script>", 1)

    with open(os.path.abspath(TARGET), "w", encoding="utf-8") as f:
        f.write(html)
    return html


if __name__ == "__main__":
    before = os.path.getsize(os.path.abspath(TARGET))
    build()
    after = os.path.getsize(os.path.abspath(TARGET))
    print("workspace patched: %d -> %d bytes" % (before, after))
