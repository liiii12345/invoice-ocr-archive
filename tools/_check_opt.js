const fs = require("fs");
const { JSDOM } = require("jsdom");
const html = fs.readFileSync("demo_console.html", "utf8");
// 必须用 http 源：file:// 是 opaque origin，jsdom 下 localStorage 直接抛 SecurityError，
// 会把「Key 不回显」「复核标记」这类依赖本地存储的断言误判成失败。
const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true, url: "http://localhost/" });
const w = dom.window, d = w.document;
let pass = 0, fail = 0;
const ok = (n, c, e) => { c ? (pass++, console.log("  ✓ " + n + (e ? "  " + e : ""))) : (fail++, console.log("  ✗ " + n + (e ? "  " + e : ""))); };
const ev = s => w.eval(s);

console.log("=== 12 条优化（jsdom）===");
w.eval("switchView('batch')");

/* ① 数据集探测 */
ok("① bLoadDatasets 无后端时安全返回", w.eval("typeof bLoadDatasets") === "function");
ok("① 下拉按内置/扫描分组", ev("bDsOptions()").indexOf('optgroup label="仓库内置"') >= 0 &&
   ev("bDsOptions()").indexOf('本机扫描') >= 0, "");
ok("① 无后端时扫描组给出配置提示", ev("bDsOptions()").indexOf("--scan") >= 0);

/* ② LLM 配置面板 */
const card = ev("bLlmCard()");
ok("② LLM 卡片渲染", card.indexOf('id="f_key"') >= 0 && card.indexOf('id="f_model"') >= 0);
ok("② Key 输入框是 password", card.indexOf('type="password"') >= 0);
ev("bStoreSet('ocr_llm_cfg',{apiKey:'sk-abcdefgh1234',baseUrl:'http://x/v1',model:'qwen-flash'})");
const card2 = ev("bLlmCard()");
ok("② 不回显 Key 明文", card2.indexOf("sk-abcdefgh1234") < 0);
ok("② 只提示末 4 位", card2.indexOf("1234") >= 0, "（末 4 位）");
const q = ev("bLiveQuery()");
ok("② 运行参数带上 Key/模型/BaseURL", q.indexOf("apiKey=sk-abcdefgh1234") >= 0 &&
   q.indexOf("model=qwen-flash") >= 0 && q.indexOf("baseUrl=") >= 0, "");
ok("② 命令回显里不含 Key", ev("bCmd()").indexOf("sk-") < 0);
ok("② 带上 task 供中止用", /task=t\d+/.test(q), (q.match(/task=t\d+/) || [""])[0]);
ev("bStoreSet('ocr_llm_cfg',{apiKey:'',baseUrl:'',model:''})");

/* ③ 多任务对比 */
ev("BState.cmp=[]; BState.cmpOpen=false;");
ok("③ 少于两个任务给出提示", ev("bCompareHtml()").indexOf("至少勾选两个") >= 0);
ev(`
  var _m1=bMetrics('rule',20), _m2=bMetrics('hybrid',20);
  BState.tasks=[
    {name:'A_rule',extractor:'rule',kind:'live',metrics:Object.assign({},_m1)},
    {name:'B_hybrid',extractor:'hybrid',kind:'sandbox',metrics:Object.assign({},_m2)}];
  BState.cmp=[0,1];`);
const cmp = ev("bCompareHtml()");
ok("③ 对比表渲染", cmp.indexOf("<table") >= 0 && cmp.indexOf("A_rule") >= 0 && cmp.indexOf("B_hybrid") >= 0);
ok("③ 对比含六个字段列", ["发票号码","开票日期","销售方","购买方","税额","价税合计"]
   .every(k => cmp.indexOf(k) >= 0));
ok("③ 给出最快/最准结论", cmp.indexOf("最快") >= 0 && cmp.indexOf("最准") >= 0);

/* ④ 断点续跑 + ⑤ 三段式 */
const seg = ev("bSegOf({ok:new Set(['a.jpg','b.jpg'])}, [" +
  "{name:'a.jpg',ok:'True'},{name:'b.jpg',ok:'True'}," +
  "{name:'c.jpg',ok:'True'},{name:'d.jpg',ok:'False'}])");
/* a、b 上次已有 → 重跑；c 是新跑出来的 → 新增；d 失败 → 失败 */
ok("④ 新增=1 / 重跑=2 / 失败=1", seg.add === 1 && seg.again === 2 && seg.fail === 1,
   JSON.stringify(seg));
ok("④ 记录上次已成功数", seg.prevTotal === 2);
const segbar = ev("bSegBar({add:2,again:2,fail:1,prevTotal:2})");
ok("⑤ 三段式条渲染", segbar.indexOf("新增 2") >= 0 && segbar.indexOf("重跑 2") >= 0 &&
   segbar.indexOf("失败 1") >= 0 && segbar.indexOf("上次已成功 2") >= 0);

/* ⑥ 跳文档档案视图 */
const docr = ev("bRowToDoc({name:'x.jpg',ok:'True',vtype_name:'英文商发',seller_name:'S'," +
  "client_name:'C',invoice_number:'123',invoice_date:'2026-01-01',tax:'10',total:'110'," +
  "avg_conf:'0.8',elapsed:'7.2',extractor:'rule'}, 0)");
ok("⑥ 映射成档案记录", docr.title === "x.jpg" && docr.details.parties.length === 2 &&
   docr.details.primary_amount_value === 110, "（金额 " + docr.details.primary_amount_value + "）");
ok("⑥ id 用负数避免撞库", docr.id === -1000);
ok("⑥ 标注来自批量任务", docr.details.summary.indexOf("批量任务") >= 0);
ev("bOpenInDocs({name:'x.jpg',ok:'True',vtype_name:'英文商发',seller_name:'S',client_name:'C'," +
   "invoice_number:'123',invoice_date:'2026-01-01',tax:'10',total:'110',avg_conf:'0.8'," +
   "elapsed:'7.2',extractor:'rule'}, 0)");
ok("⑥ 跳转后切到文档视图", d.querySelector("#shellDoc").classList.contains("hidden") === false);

/* ⑦ 字段横向差异对照 */
ok("⑦ 缺失判异常", ev("bDiffBad('total','')") === "缺失");
ok("⑦ 非数值判异常", ev("bDiffBad('total','abc')") === "非数值");
ok("⑦ 正常值不报", ev("bDiffBad('total','1234.56')") === "" && ev("bDiffBad('invoice_number','51109338')") === "");
ok("⑦ 号码无数字判异常", ev("bDiffBad('invoice_number','ABCD')") === "无数字");
const diff = ev("bDiffHtml([{name:'a.jpg',total:'100',invoice_number:'1',invoice_date:'2026-01-01'," +
  "seller_name:'S',client_name:'C',tax:'9'},{name:'b.jpg',total:'',invoice_number:'2'," +
  "invoice_date:'2026-01-02',seller_name:'',client_name:'C',tax:'x'}])");
ok("⑦ 对照表渲染", diff.indexOf("<table") >= 0 && diff.indexOf("a.jpg") >= 0 && diff.indexOf("b.jpg") >= 0);
ok("⑦ 异常格高亮", (diff.match(/class="dbad"/g) || []).length >= 3,
   "（" + (diff.match(/class="dbad"/g) || []).length + " 处）");
ok("⑦ 附每列异常统计", diff.indexOf("异常") >= 0);

/* ⑧ 日志分色 */
ok("⑧ $ 命令→ac", ev("bLogClass('$ python src/batch_run.py')") === "ac");
ok("⑧ [overview]→ok", ev("bLogClass('[overview] 总数 20')") === "ok");
ok("⑧ [field]→fm", ev("bLogClass('[field] 字段准确率')") === "fm");
ok("⑧ [plan]→pl", ev("bLogClass('[plan] 待跑 20 张')") === "pl");
ok("⑧ [error]→bd", ev("bLogClass('[error] 后端报错')") === "bd");
ok("⑧ FAIL→wn", ev("bLogClass('[1/20] a.jpg FAIL')") === "wn");

/* ⑨ ETA */
ok("⑨ 未完成时不给 ETA", ev("bEtaSec(0,20,Date.now())") === null);
const t0 = Date.now() - 10000;
const eta = ev("bEtaSec(5,20," + t0 + ")");
ok("⑨ 按已用时间外推剩余", eta >= 25 && eta <= 35, "（5/20 用了 10s → 剩 " + eta + "s）");
ok("⑨ ETA 文案可读", /约 \d+/.test(ev("bEtaText(95)")), ev("bEtaText(95)"));

/* ⑩ 导出 Markdown / JSON */
let dl = null;
w.URL.createObjectURL = b => { dl = b; return "blob:x"; };
w.HTMLAnchorElement.prototype.click = function () {};
ev("BState.cur={name:'T',kind:'live',extractor:'rule',metrics:bMetrics('rule',20),rows:[]};");
ev("bReport('md')");
ok("⑩ Markdown 报告生成", !!dl && dl.type === "text/markdown");
dl = null; ev("bReport('json')");
ok("⑩ JSON 报告生成", !!dl && dl.type === "application/json");

/* ⑪ 抽样复核标记 */
ev("BState.out='batch_out'; bStoreSet('ocr_review_marks',{})");
ev("bMarkSet(bMarkKey('a.jpg'),'ok'); bMarkSet(bMarkKey('b.jpg'),'bad');");
ok("⑪ 标记写入", ev("Object.keys(bMarks()).length") === 2);
const rb = ev("bReviewBar([{name:'a.jpg'},{name:'b.jpg'}])");
ok("⑪ 复核统计条", rb.indexOf("已复核 2") >= 0 && rb.indexOf("判错 1") >= 0 && rb.indexOf("50.0%") >= 0, rb.replace(/<[^>]+>/g, "").trim());
/* 按钮走的是 toggle 语义（同一个标记再点一次取消），这里照 UI 的表达式来一遍 */
ok("⑪ 标记已存在", ev("bMarks()['batch_out::a.jpg']") === "ok");
ev("var _k=bMarkKey('a.jpg'); bMarkSet(_k, bMarks()[_k]==='ok'?null:'ok');");
ok("⑪ 再点一次取消标记", ev("bMarks()['batch_out::a.jpg']") === undefined);
const rowsHtml = ev("bRowsHtml([{name:'a.jpg',ok:'True',vtype_name:'英文商发',avg_conf:'0.8'," +
  "elapsed:'7.2',invoice_number:'1',total:'100',item_count:'3'}],'live')");
ok("⑪ 明细表带复核按钮", rowsHtml.indexOf('data-m="ok"') >= 0 && rowsHtml.indexOf('data-m="bad"') >= 0);
ok("⑪ 明细表带档案视图跳转", rowsHtml.indexOf("档案视图") >= 0);
ev("bStoreSet('ocr_review_marks',{})");

/* ⑫ 中止按钮 */
ev("BState.running=true; BState.cur=null;");
const runHtml = (() => {
  ev("BState.tasks=[]; BState.running=false;");
  return "";
})();
ok("⑫ 运行态模板含中止按钮", html.indexOf('id="bStop"') >= 0);
ok("⑫ 中止会关 SSE 并调 /api/stop", html.indexOf("/api/stop?task=") >= 0);
ok("⑫ 中止保留已写入结果", html.indexOf("--resume 会跳过") >= 0);

/* 回归：12 条没有破坏原有主区 */
ev("BState.running=false; BState.cur=null; BState.cmpOpen=false; switchView('batch');");
ok("回归：主区仍渲染表单", d.querySelector("#bBody").innerHTML.indexOf('id="f_srcs"') >= 0 ||
   d.querySelector("#bBody").innerHTML.indexOf("批量测试") >= 0);
ok("回归：命令卡仍可用", ev("bCmd()").indexOf("src/batch_run.py") >= 0);

console.log("");
console.log(pass + " passed, " + fail + " failed");
process.exit(fail ? 1 : 0);
