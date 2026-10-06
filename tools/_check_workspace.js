const fs = require("fs");
const { JSDOM } = require("jsdom");
const html = fs.readFileSync("demo_console.html", "utf8");
const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true, url: "http://localhost/" });
const w = dom.window, d = w.document;
let pass = 0, fail = 0;
const ok = (n, c, e) => { c ? (pass++, console.log("  ✓ " + n + (e ? "  " + e : ""))) : (fail++, console.log("  ✗ " + n + (e ? "  " + e : ""))); };
const ev = s => w.eval(s);

console.log("=== 识别 / 合规页（jsdom）===");

/* 导航 */
const btns = [...d.querySelectorAll(".seg button")].map(b => b.dataset.v);
ok("顶栏四个视图", JSON.stringify(btns) === '["work","doc","batch","comp"]', btns.join("/"));
ok("四个 shell 都在", ["shellWork", "shellDoc", "shellBatch", "shellComp"]
   .every(id => !!d.getElementById(id)));

/* 默认落在识别页 */
ev("W.online=true; switchView('work')");
ok("识别页可见", !d.getElementById("shellWork").classList.contains("hidden"));
ok("其余三页隐藏", ["shellDoc", "shellBatch", "shellComp"]
   .every(id => d.getElementById(id).classList.contains("hidden")));
let h = d.querySelector("#wBody").innerHTML;
ok("有拖拽区", h.indexOf('id="wDrop"') >= 0);
ok("有策略选择", h.indexOf('id="wExt"') >= 0);
ok("有跑合规开关", h.indexOf('id="wComp"') >= 0);
ok("有开始识别按钮", h.indexOf('id="wGo"') >= 0);
ok("在线时不显示离线提示", h.indexOf("需要本地后端") < 0);

ev("W.online=false; wRenderBody()");
h = d.querySelector("#wBody").innerHTML;
ok("离线时给出启动后端指引", h.indexOf("需要本地后端") >= 0 && h.indexOf("console_server.py") >= 0);
ok("离线时拖拽区仍可用（可先选文件）", h.indexOf('id="wDrop"') >= 0);

/* 导入文件 */
ev("W.online=true;");
ev(`
  var _mk=function(n,b){ return new File([new Uint8Array(b||10)], n, {type:"image/jpeg"}); };
  wAddFiles([_mk("a.jpg",100), _mk("b.png",200), _mk("c.txt",10), _mk("d.webp",300)]);
`);
ok("只收图片，过滤掉 .txt", ev("W.files.length") === 3, "（收了 " + ev("W.files.length") + " 个）");
ok("文件进入列表", d.querySelector("#wList").innerHTML.indexOf("a.jpg") >= 0);
ok("侧栏计数更新", d.querySelector("#wCnt").textContent === "3 个");
ev("W.files=[]; W.cur=null; wRenderSide(); wRenderBody();");

/* 单张结果详情 */
const fake = {
  ok: true, line_count: 104, avg_conf: 0.8115, elapsed: 8.99, extractor: "rule",
  img_size: [1654, 2339],
  vtype: { code: "intl-commercial", name: "英文商业发票（含 VAT）", short: "英文商发",
           confidence: 0.99, region: "INTL", evidence: ["gross worth", "invoice no"] },
  fields: { invoice_number: { value: "51109338", conf: 0.95 },
            total: { value: "6204.19", conf: 0.9, evidence: { crop: "data:image/png;base64,AAA" } } },
  items: [{ description: "Widget", quantity: "2", unit_price: "10", total_price: "20" }],
  lines: [{ text: "INVOICE", conf: 0.99 }, { text: "No. 51109338", conf: 0.95 }],
  compliance: {
    provider: "mock", applicability: { xml: false, sign: false, verify: false },
    legal_original: { available: false, format: "IMAGE", source: "不适用：非国内税务票据",
                      reason: "该票种法定原件为发票影像" },
    signature: { valid: false, applicable: false, reason: "不涉及 XML —— 不适用" },
    verify: { ok: false, applicable: false, code: "", message: "该票种不适用税务查验",
              invoice_state: "", times: 0, channel: "不适用" }
  }
};
ev("W.files=[{id:1,name:'x.jpg',size:12345,url:'blob:x',state:'ok',rec:" +
   JSON.stringify(fake) + ",err:''}]; W.cur=1; wRenderBody();");
h = d.querySelector("#wBody").innerHTML;
ok("详情：票种卡", h.indexOf("英文商业发票") >= 0 && h.indexOf("intl-commercial") >= 0);
ok("详情：字段表", h.indexOf("51109338") >= 0 && h.indexOf("6204.19") >= 0);
ok("详情：证据放大图", h.indexOf('class="ev"') >= 0);
ok("详情：明细行", h.indexOf("Widget") >= 0);
ok("详情：OCR 原文", h.indexOf("INVOICE") >= 0 && h.indexOf("ocrlines") >= 0);
ok("详情：合规三卡", h.indexOf("取法定原件") >= 0 && h.indexOf("XMLDSig 验签") >= 0 && h.indexOf("发票查验") >= 0);
ok("合规卡标「不适用」而非伪造通过", h.indexOf("不适用") >= 0 && h.indexOf("不适用：非国内税务票据") >= 0);
ok("判定依据可见", h.indexOf("gross worth") >= 0);

/* 失败态 */
ev("W.files=[{id:2,name:'bad.jpg',size:1,url:'blob:y',state:'fail',rec:{ok:false,error:'OCR 未检出'},err:'OCR 未检出'}]; W.cur=2; wRenderBody();");
h = d.querySelector("#wBody").innerHTML;
ok("失败张给出原因", h.indexOf("OCR 未检出") >= 0);

/* 合规页（cRender 是 async，要等它渲染完再看 DOM） */
(async function tail() {
  ev("W.files=[];W.cur=null; switchView('comp');");
  ok("合规页可见", !d.getElementById("shellComp").classList.contains("hidden"));
  await ev("cRender()");
  h = d.querySelector("#cBody").innerHTML;
  ok("合规页未连后端时给指引而非假数据", h.indexOf("需要本地后端") >= 0,
     "（含启动命令：" + (h.indexOf("console_server.py") >= 0 ? "是" : "否") + "）");
  ok("合规页标题与口径说明", h.indexOf("合规核验") >= 0 && h.indexOf("不适用") >= 0);
  await tail2();
})();

async function tail2() {
/* 合规三卡的适用性三态 */
ev("W.files=[{id:3,name:'z.jpg',size:1,url:'blob:z',state:'ok',rec:{ok:true,compliance:" +
   JSON.stringify(fake.compliance) + "},err:''}]; W.cur=3;");
const cc = ev("wComplianceCards({compliance:" + JSON.stringify(fake.compliance) + "})");
ok("三态：true→适用", ev("wComplianceCards({compliance:{applicability:{xml:true,sign:true,verify:true}}})").indexOf("适用") >= 0);
ok("三态：false→不适用", cc.indexOf("不适用") >= 0);
ok("三态：null→待人工判定",
   ev("wComplianceCards({compliance:{applicability:{xml:null}}})").indexOf("待人工判定") >= 0);

/* 字段表/明细表空态 */
ok("字段为空有提示", ev("wFieldsTable({fields:{}})").indexOf("没有抽到字段") >= 0);
ok("明细为空有提示", ev("wItemsTable([])").indexOf("没有明细行") >= 0);
ok("体积格式化", ev("wSize(2048)") === "2 KB" && ev("wSize(3145728)") === "3.0 MB",
   ev("wSize(2048)") + " / " + ev("wSize(3145728)"));

console.log("");
console.log(pass + " passed, " + fail + " failed");
process.exit(fail ? 1 : 0);
}
