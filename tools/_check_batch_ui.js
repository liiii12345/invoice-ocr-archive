const fs = require("fs");
const { JSDOM } = require("jsdom");
const html = fs.readFileSync("demo_console.html", "utf8");
const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true, url: "file:///x/" });
const w = dom.window, d = w.document;
let pass = 0, fail = 0;
const ok = (name, cond, extra) => {
  if (cond) { pass++; console.log("  ✓ " + name + (extra ? "  " + extra : "")); }
  else { fail++; console.log("  ✗ " + name + (extra ? "  " + extra : "")); }
};

console.log("=== 批量测试工作台（jsdom）===");
ok("BATCH 数据注入", w.eval("typeof B_PRESETS") === "object" && w.eval("B_PRESETS.length") > 0,
   "(" + w.eval("B_PRESETS.map(function(p){return p.id;}).join(',')") + ")");
ok("seg 控件存在", !!d.querySelector(".seg button[data-v=batch]"));
ok("shellBatch 存在", !!d.querySelector("#shellBatch"));
ok("shellDoc 存在", !!d.querySelector("#shellDoc"));

w.eval("switchView('batch')");
const body = d.querySelector("#bBody");
const h = body.innerHTML;
ok("切到 batch 后 shellDoc 隐藏", d.querySelector("#shellDoc").classList.contains("hidden"));
ok("切到 batch 后 shellBatch 可见", !d.querySelector("#shellBatch").classList.contains("hidden"));
ok("表单渲染（输入源）", h.includes("id=\"f_srcs\""));
ok("表单渲染（策略）", h.includes("id=\"f_ext\""));
ok("命令行卡渲染", h.includes("id=\"bCmd\""));
ok("预计文案渲染", h.includes("id=\"bEst\""));
ok("侧栏任务列表渲染", !!d.querySelector("#bTaskList"));
ok("数据集下拉有选项", d.querySelectorAll("#bDs option").length >= 1,
   "(" + d.querySelectorAll("#bDs option").length + ")");

// 命令随参数变化
const c1 = w.eval("bCmd()");
ok("命令含 src/batch_run.py", c1.includes("src/batch_run.py"));
ok("命令带 --input", c1.includes("--input"));
w.eval("BState.extractor='hybrid'; BState.workers=4; bRenderCmd();");
const c2 = w.eval("bCmd()");
ok("改策略后命令同步", c2.includes("--extractor hybrid") && c2.includes("--workers 4"));
ok("bEst 有预计", (d.querySelector("#bEst") || { textContent: "" }).textContent.includes("张"));

// 指标模型（先重置并发，保证 workers=1 的基准）
w.eval("BState.workers=1");
const m = w.eval("bMetrics('rule', 20)");
ok("指标：total=20", m.total === 20);
ok("指标：耗时量级正确(≈150s)", m.elapsed > 120 && m.elapsed < 190, "(" + m.elapsed + "s)");
ok("指标：吞吐≈7.8", Math.abs(m.throughput - 7.8) < .2, "(" + m.throughput + ")");
const ml = w.eval("bMetrics('llm', 20)");
ok("llm 比 rule 慢", ml.elapsed > m.elapsed * 2, "(" + ml.elapsed + "s)");
const mh = w.eval("bMetrics('hybrid', 20)");
ok("hybrid 融合最优", mh.avgAcc >= m.avgAcc && mh.avgAcc >= ml.avgAcc,
   "rule=" + m.avgAcc + " llm=" + ml.avgAcc + " hybrid=" + mh.avgAcc);

// 结果区渲染
w.eval("var _m=bMetrics('rule',20); BState.cur={kind:'sandbox',name:'t',extractor:'rule',total:_m.total,elapsed:_m.elapsed,metrics:_m}; bRenderBody();");
const h2 = d.querySelector("#bBody").innerHTML;
ok("结果区 KPI", h2.includes("class=\"kpi\""));
ok("结果区字段准确率 6 条", (h2.match(/bar-track/g) || []).length >= 6);
ok("结果区耗时分布柱状", h2.includes("class=\"hist\""));
ok("结果区策略对比", h2.includes("三策略对比"));

// 实跑字段映射
const fake = w.eval("bFromSummary({total:20,ok:19,failed:1,itemsFullMatch:'19/20',throughput:'7.8 张/分钟',field_accuracy_vs_truth:{invoice_number:1,total:.95},vtype_distribution:{x:20},elapsed:{wall:154,avg:7.6,p50:7.2,p90:9.4,max:14.7}})");
ok("实跑映射：total", fake.total === 20);
ok("实跑映射：itemsFull 解析 19/20", Math.abs(fake.itemsFull - .95) < .001, "(" + fake.itemsFull + ")");
ok("实跑映射：吞吐解析", fake.throughput === 7.8);
const acc = w.eval("bNormAcc(" + JSON.stringify(fake.accRaw) + ")");
ok("实跑字段映射中文键", acc["发票号码"] === 1 && Math.abs(acc["价税合计"] - .95) < .001,
   JSON.stringify(acc));
const h3 = (() => {
  const cur = {
    kind: "live", name: "live", extractor: "rule",
    total: fake.total, elapsed: fake.elapsed,
    metrics: {
      total: fake.total, ok: 19, fail: 1, elapsed: fake.elapsed,
      itemsFull: fake.itemsFull, avgConf: 0, accRaw: fake.accRaw,
      vtk: fake.vtk, el: { p50: 7.2, p90: 9.4, max: 14.7 }, hist: null
    }
  };
  w.eval("BState.cur=" + JSON.stringify(cur) + "; bRenderBody();");
  return d.querySelector("#bBody").innerHTML;
})();
ok("实跑结果区渲染（无 hist 走统计条）", !h3.includes("class=\"hist\"") && h3.includes("耗时统计"));

console.log("");
console.log(pass + " passed, " + fail + " failed");
process.exit(fail ? 1 : 0);
