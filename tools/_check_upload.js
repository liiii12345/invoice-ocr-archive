/* 端到端：jsdom 里的前端代码真的向本地后端上传一张图并拿到识别结果。
   只验证「前端上传链路」（FormData 组装 → /api/ocr → 结果回填），
   不替代浏览器里的手点，但能保证按钮点下去不会是空转。

   用法：先起后端
     OCR_PYTHON=<装了OCR依赖的解释器> python tools/console_server.py --port 8770
   再跑： node tools/_check_upload.js <一张发票图片路径>
*/
const fs = require("fs");
const { JSDOM } = require("jsdom");
const { File } = require("node:buffer");

const IMG = process.argv[2] || "";
const html = fs.readFileSync("demo_console.html", "utf8");
// file:// → LIVE_ORIGIN 会被设成 http://127.0.0.1:8770，正好对上默认端口
const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true, url: "file:///x/" });
const w = dom.window, d = w.document;

let pass = 0, fail = 0;
const ok = (n, c, e) => { c ? (pass++, console.log("  ✓ " + n + (e ? "  " + e : ""))) : (fail++, console.log("  ✗ " + n + (e ? "  " + e : ""))); };

// jsdom 不带 fetch / FormData / File，借 Node 的给前端用
w.fetch = fetch;
w.FormData = FormData;
w.File = File;

(async function main() {
  console.log("=== 前端上传链路（jsdom → 本地后端）===");

  const alive = await fetch("http://127.0.0.1:8770/api/health").then(r => r.ok).catch(() => false);
  if (!alive) {
    console.log("  ! 后端未启动（http://127.0.0.1:8770），跳过");
    console.log("    OCR_PYTHON=<解释器> python tools/console_server.py --port 8770");
    process.exit(0);
  }
  ok("后端在线", true);

  if (!IMG || !fs.existsSync(IMG)) {
    console.log("  ! 未给图片路径，只测了连通性。用法：node tools/_check_upload.js <图片>");
    process.exit(0);
  }

  const buf = fs.readFileSync(IMG);
  const f = new File([buf], "upload_test.jpg", { type: "image/jpeg" });
  w.eval(`
    W.online = true; W.ext = "rule"; W.comp = true;
    W.files = []; W.cur = null;
  `);
  w.__f = f;
  w.eval(`
    W.files.push({id: W.nextId++, file: window.__f, name: window.__f.name,
                  size: window.__f.size, url: "", state: "wait", rec: null, err: ""});
    W.cur = W.files[0].id;
  `);
  ok("文件进入前端队列", w.eval("W.files.length") === 1);

  const t0 = Date.now();
  await w.eval("wRunOne(W.files[0], 0, 1, Date.now())");
  const sec = ((Date.now() - t0) / 1000).toFixed(1);

  const st = w.eval("W.files[0].state");
  const rec = w.eval("JSON.stringify(W.files[0].rec || {})");
  const j = JSON.parse(rec);
  ok("上传后状态为已识别", st === "ok", "（" + st + "，" + sec + "s）");
  ok("拿到票种", !!(j.vtype && j.vtype.code), j.vtype && (j.vtype.short || j.vtype.name));
  ok("拿到字段", Object.keys(j.fields || {}).length > 0,
     Object.keys(j.fields || {}).slice(0, 4).join(","));
  ok("拿到 OCR 行", (j.lines || []).length > 0, (j.lines || []).length + " 行");
  ok("拿到合规结论（含适用性）", !!(j.compliance && j.compliance.applicability),
     JSON.stringify(j.compliance && j.compliance.applicability));

  // 结果真的渲染进 DOM
  w.eval("wRenderBody()");
  const h = d.querySelector("#wBody").innerHTML;
  ok("详情渲染出字段值", h.indexOf("dtwrap") >= 0 || h.indexOf("OCR 未检出") >= 0);
  ok("详情渲染出合规三卡", h.indexOf("发票查验") >= 0);
  ok("侧栏列表显示已识别", d.querySelector("#wList").innerHTML.indexOf("已识别") >= 0);

  console.log("");
  console.log(pass + " passed, " + fail + " failed");
  process.exit(fail ? 1 : 0);
})();
