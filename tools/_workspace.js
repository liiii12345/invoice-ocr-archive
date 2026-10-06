
/* ═══════════════ 识别工作台 + 合规页 ═══════════════
   顶栏四个视图：work（识别）/ doc（档案）/ batch（批量）/ comp（合规）。
   识别页是主入口——用户第一件事就是把发票拖进来。                        */

const W = {
  files: [],        /* {id, file, name, size, url, state:'wait'|'run'|'ok'|'fail', rec, err} */
  cur: null, running: false, abort: false,
  ext: "rule", comp: false, online: false, nextId: 1
};
const $w = s => document.querySelector(s);

/* ── 页头小工具 ── */
function wPill(kind, txt){
  return '<span class="pill ' + kind + '">' + esc(txt) + '</span>';
}
function wSize(b){
  if (b < 1024) return b + " B";
  if (b < 1024 * 1024) return (b / 1024).toFixed(0) + " KB";
  return (b / 1024 / 1024).toFixed(1) + " MB";
}
function wOffline(){
  /* 后端不在时，这些功能就是做不了——如实说，不要给假结果 */
  return '<div class="card"><div class="card-b"><div class="offline">' +
    '<div class="h">需要本地后端才能真跑识别</div>' +
    '<div class="d">单文件 HTML 里没有 Python 运行时，OCR 必须在本机执行。启动方式：</div>' +
    '<pre class="code">python tools/console_server.py --python &lt;装了OCR依赖的解释器&gt;</pre>' +
    '<div class="d">启动后本页会自动连上（默认 <span class="mono">http://127.0.0.1:8770</span>）。' +
    '连不上时下面的功能区保持只读，不会给你编造识别结果。</div>' +
    '</div></div></div>';
}

/* ══════════════ 识别页 ══════════════ */
function wDropHtml(){
  if (W.files.length) return "";
  return `
  <div class="drop" id="wDrop">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
      <path d="M12 16V4m0 0L8 8m4-4 4 4"/><path d="M3 16v3a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-3"/>
    </svg>
    <div class="t">把发票图片拖到这里</div>
    <div class="s">单张或多张都行 · jpg / png / webp / bmp / tif · 单张 ≤ 30MB</div>
    <button class="btn primary" id="wDropBtn" style="margin-top:14px">选择文件</button>
    <div class="s" style="margin-top:12px">图片只发到本机 127.0.0.1 的后端，不会上传到任何外部服务</div>
  </div>`;
}

function wFormHtml(){
  return `
  <div class="card">
    <div class="card-h"><span class="t">识别设置</span>
      <span class="h">策略影响速度与准确率；合规链路会顺带跑「取原件 → 验签 → 查验」</span></div>
    <div class="card-b">
      <div class="wset">
        <div class="frow"><label>抽取策略</label>
          <select id="wExt">
            <option value="rule">rule（规则抽取，快，约 5-8 秒/张）</option>
            <option value="hybrid">hybrid（规则 + 大模型融合）</option>
            <option value="llm">llm（大模型抽取，慢但稳）</option>
          </select></div>
        <label class="chk"><input type="checkbox" id="wComp">跑合规链路</label>
        <span class="spacer"></span>
        <button class="btn" id="wAdd">+ 添加更多</button>
        <button class="btn primary" id="wGo">开始识别</button>
        <button class="btn" id="wStop" style="display:none">中止</button>
      </div>
    </div>
  </div>`;
}

function wFileList(){
  if (!W.files.length) return "";
  return W.files.map((f, i) => {
    const st = f.state === "ok" ? wPill("ok", "已识别")
      : f.state === "fail" ? wPill("bad", "失败")
      : f.state === "run" ? wPill("warn", "识别中")
      : wPill("", "待识别");
    const vt = f.rec && f.rec.vtype ? esc(f.rec.vtype.short || f.rec.vtype.name || "") : "";
    return '<div class="witem' + (W.cur === f.id ? " active" : "") + '" data-i="' + i + '">' +
      (f.url ? '<img src="' + f.url + '" alt="">' : '<div class="nothumb">图</div>') +
      '<div class="m"><div class="n">' + esc(f.name) + '</div>' +
      '<div class="s">' + wSize(f.size) + ' · ' + vt + ' ' + st + '</div></div>' +
      '<button class="x" data-x="' + i + '" title="移除">×</button></div>';
  }).join("");
}

function wRenderSide(){
  const c = $w("#wCnt"); if (c) c.textContent = W.files.length + " 个";
  const l = $w("#wList"); if (!l) return;
  if (!W.files.length) {
    l.innerHTML = '<div class="item"><div class="sub"><span class="ty">还没有文件</span></div></div>';
    return;
  }
  l.innerHTML = wFileList();
  l.querySelectorAll(".witem").forEach(el => el.onclick = e => {
    if (e.target.dataset.x !== undefined) return;
    W.cur = W.files[+el.dataset.i].id; wRenderBody();
  });
  l.querySelectorAll(".x").forEach(b => b.onclick = e => {
    e.stopPropagation();
    const i = +b.dataset.x;
    if (W.files[i].url) URL.revokeObjectURL(W.files[i].url);
    W.files.splice(i, 1); wRenderSide(); wRenderBody();
  });
}

function wAddFiles(list){
  const ok = [];
  for (const f of list) {
    const ext = (f.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
    if (![".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"].includes(ext)) continue;
    if (f.size > 30 * 1024 * 1024) continue;
    /* 预览图地址不一定拿得到（非浏览器环境、或策略限制），拿不到就用占位块，别整条挂掉 */
    let url = "";
    try { url = URL.createObjectURL(f); } catch (e) { url = ""; }
    ok.push({ id: W.nextId++, file: f, name: f.name, size: f.size,
              url: url, state: "wait", rec: null, err: "" });
  }
  if (!ok.length) { toast("没有可用图片（仅 jpg/png/webp/bmp/tif，单张 ≤30MB）"); return; }
  W.files = W.files.concat(ok);
  if (!W.cur) W.cur = ok[0].id;
  wRenderSide(); wRenderBody();
  toast("已加入 " + ok.length + " 个文件");
}

async function wRunOne(f, i, n, t0){
  const fd = new FormData();
  fd.append("file", f.file, f.name);
  fd.append("extractor", W.ext);
  fd.append("compliance", W.comp ? "1" : "0");
  const cfg = bLlm();
  if (cfg.apiKey) fd.append("apiKey", cfg.apiKey);
  if (cfg.baseUrl) fd.append("baseUrl", cfg.baseUrl);
  if (cfg.model) fd.append("model", cfg.model);
  const r = await fetch(LIVE_ORIGIN + "/api/ocr", { method: "POST", body: fd });
  if (!r.ok) throw new Error("HTTP " + r.status);
  const j = await r.json();
  f.state = j.ok ? "ok" : "fail";
  f.rec = j;
  f.err = j.ok ? "" : (j.error || j.message || "识别失败");
  wRenderSide(); wRenderBody();
  const done = W.files.filter(x => x.state === "ok" || x.state === "fail").length;
  const eta = bEtaSec(done, n, t0);
  const p = $w("#wProgTx");
  if (p) p.textContent = done + " / " + n + (eta ? " · 预计剩余 " + bEtaText(eta) : "");
  const bar = $w("#wProg");
  if (bar) bar.style.width = Math.round(done / n * 100) + "%";
}

async function wRun(){
  if (W.running || !W.files.length) return;
  const online = await probeServer();
  if (!online) { wRenderBody(); toast("后端未启动，无法识别"); return; }
  W.running = true; W.abort = false;
  wRenderBody();
  const todo = W.files.filter(f => f.state !== "ok");
  const t0 = Date.now();
  const go = $w("#wGo"), stop = $w("#wStop");
  if (go) go.style.display = "none";
  if (stop) stop.style.display = "";
  if (stop) stop.onclick = () => { W.abort = true; W.running = false; wRenderBody(); };
  for (let k = 0; k < todo.length; k++) {
    if (W.abort) break;
    const f = todo[k];
    f.state = "run"; wRenderSide();
    try { await wRunOne(f, k, todo.length, t0); }
    catch (e) { f.state = "fail"; f.err = e.message || String(e); wRenderSide(); wRenderBody(); }
  }
  W.running = false;
  const okN = W.files.filter(f => f.state === "ok").length;
  const badN = W.files.filter(f => f.state === "fail").length;
  toast("完成：成功 " + okN + " / 失败 " + badN);
  wRenderBody();
}

/* ── 单张结果详情 ── */
function wFieldsTable(rec){
  const f = rec.fields || {};
  const keys = Object.keys(f);
  if (!keys.length) return '<div class="hint">没有抽到字段。</div>';
  return '<div class="dtwrap"><table class="dt fld"><thead><tr>' +
    '<th>字段</th><th>抽取结果</th><th>置信</th><th>证据</th></tr></thead><tbody>' +
    keys.map(k => {
      const v = f[k] || {};
      const val = v.value != null ? String(v.value) : "";
      const conf = v.conf != null ? v.conf : (v.score != null ? v.score : null);
      const ev = v.evidence || {};
      const crop = ev.crop ? '<img class="ev" src="' + ev.crop + '" alt="证据">' : '<span class="muted">—</span>';
      return '<tr><td class="k">' + esc(k) + '</td>' +
        '<td class="v">' + (val ? esc(val).replace(/\n/g, "<br>") : '<span class="muted">—</span>') + '</td>' +
        '<td>' + (conf != null ? Number(conf).toFixed(2) : "—") + '</td>' +
        '<td>' + crop + '</td></tr>';
    }).join("") + '</tbody></table></div>';
}

function wItemsTable(items){
  if (!items || !items.length) return '<div class="hint">没有明细行。</div>';
  return '<div class="dtwrap"><table class="dt"><thead><tr>' +
    '<th>#</th><th>说明</th><th>数量</th><th>单价</th><th>金额</th></tr></thead><tbody>' +
    items.map((it, i) => '<tr><td>' + (i + 1) + '</td><td>' + esc(it.description || "—") + '</td>' +
      '<td>' + esc(it.quantity || "—") + '</td><td>' + esc(it.unit_price || "—") + '</td>' +
      '<td>' + esc(it.total_price || "—") + '</td></tr>').join("") +
    '</tbody></table></div>';
}

function wComplianceCards(rec){
  const c = rec.compliance;
  if (!c) return "";
  if (c.error) return '<div class="hint">合规链路出错：' + esc(c.error) + '</div>';
  const ap = c.applicability || {};
  const st = v => v === true ? wPill("ok", "适用")
    : v === false ? wPill("", "不适用") : wPill("warn", "待人工判定");
  const lo = c.legal_original || {}, sg = c.signature || {}, vf = c.verify || {};
  const card = (title, flag, rows) =>
    '<div class="ccard"><div class="h">' + esc(title) + ' ' + st(flag) + '</div>' +
    rows.map(r => '<div class="r"><span>' + esc(r[0]) + '</span><b>' + esc(r[1]) + '</b></div>').join("") +
    '</div>';
  return '<div class="sec-h" style="margin-top:22px">合规链路<span class="pill" style="margin-left:8px">provider: ' +
    esc(c.provider || "—") + '</span></div>' +
    '<div class="cgrid">' +
    card("取法定原件", ap.xml, [
      ["是否取到", lo.available ? "是" : "否"],
      ["格式", lo.format || "—"],
      ["来源", lo.source || "—"],
      ["说明", lo.reason || "—"],
    ]) +
    card("XMLDSig 验签", ap.sign, [
      ["结论", sg.valid ? "有效" : (sg.applicable === false ? "不适用" : "无效")],
      ["算法", sg.algorithm || "—"],
      ["证书序列号", sg.cert_serial || "—"],
      ["签名方", sg.signer || "—"],
      ["说明", sg.reason || "—"],
    ]) +
    card("发票查验", ap.verify, [
      ["结论", vf.ok ? "正常" : (vf.applicable === false ? "不适用" : "异常")],
      ["状态码", vf.code || "—"],
      ["票据状态", vf.invoice_state || "—"],
      ["已查验次数", String(vf.times != null ? vf.times : "—")],
      ["渠道", vf.channel || "—"],
      ["说明", vf.message || "—"],
    ]) +
    '</div>';
}

function wDetail(f){
  const rec = f.rec || {};
  if (!rec.ok) {
    return '<div class="card"><div class="card-b"><div class="offline">' +
      '<div class="h">这张没能识别</div><div class="d">' + esc(f.err || rec.error || "未知原因") + '</div>' +
      (rec.stderr ? '<pre class="code">' + esc(rec.stderr.slice(-800)) + '</pre>' : '') +
      '</div></div></div>';
  }
  const v = rec.vtype || {};
  const meta = [
    ["票种", (v.name || "未知") + (v.code ? "（" + v.code + "）" : "")],
    ["置信", v.confidence != null ? Number(v.confidence).toFixed(2) : "—"],
    ["OCR 行数", String(rec.line_count || 0)],
    ["平均置信", rec.avg_conf != null ? Number(rec.avg_conf).toFixed(3) : "—"],
    ["耗时", (rec.elapsed != null ? rec.elapsed : "—") + "s"],
    ["策略", rec.extractor || W.ext],
  ];
  let h = '<div class="card"><div class="card-h"><span class="t">' + esc(f.name) + '</span>' +
    '<span class="h">' + wSize(f.size) + ' · ' + (rec.img_size ? rec.img_size.join("×") : "") + '</span></div>' +
    '<div class="card-b"><div class="wview"><div class="wleft">' +
    '<img class="wprev" src="' + f.url + '" alt="">' +
    '<div class="grid2">' + meta.map(m => '<div class="cell"><div class="k">' +
      esc(m[0]) + '</div><div class="v">' + esc(m[1]) + '</div></div>').join("") + '</div>' +
    (v.evidence && v.evidence.length ? '<div class="hint">判定依据：' +
      esc(v.evidence.join("、")) + '</div>' : '') +
    '</div><div class="wright">' +
    '<div class="sec-h">抽取字段</div>' + wFieldsTable(rec) +
    '<div class="sec-h" style="margin-top:20px">明细行（' + ((rec.items || []).length) + '）</div>' +
    wItemsTable(rec.items) +
    wComplianceCards(rec) +
    '</div></div></div></div>';
  const lines = rec.lines || [];
  if (lines.length) {
    h += '<div class="card"><div class="card-h"><span class="t">OCR 原文</span>' +
      '<span class="h">' + lines.length + ' 行 · 按版面顺序</span></div>' +
      '<div class="card-b"><div class="ocrlines">' +
      lines.map((l, i) => '<div class="ln"><i>' + (i + 1) + '</i><span>' + esc(l.text) +
        '</span><b>' + (l.conf != null ? Number(l.conf).toFixed(2) : "") + '</b></div>').join("") +
      '</div></div></div>';
  }
  return h;
}

function wRenderBody(){
  const body = $w("#wBody"); if (!body) return;
  const f = W.files.find(x => x.id === W.cur);
  let h = '<div class="wpane">';
  h += '<div class="bp-head"><div><div class="t">识别</div>' +
    '<div class="s">导入发票图片 → 抽取字段 → 核对证据 → 跑合规链路</div></div></div>';
  /* 后端不在就说清楚：这时候「导入」依旧可用，但「识别」跑不了，不能让人白等 */
  h += W.online ? "" : wOffline();
  h += wDropHtml();
  h += wFormHtml();
  if (W.files.length) {
    const done = W.files.filter(x => x.state === "ok" || x.state === "fail").length;
    h += '<div class="card"><div class="card-h"><span class="t">进度</span>' +
      '<span class="h" id="wProgTx">' + (W.running ? done + " / " + W.files.filter(x => x.state !== "ok").length : done + " / " + W.files.length + " 已处理") + '</span></div>' +
      '<div class="card-b"><div class="prog"><i id="wProg" style="width:' +
      Math.round(done / W.files.length * 100) + '%"></i></div></div></div>';
  }
  if (f) h += wDetail(f);
  h += "</div>";
  body.innerHTML = h;

  const drop = $w("#wDrop");
  const btn = $w("#wDropBtn");
  const pick = () => {
    const inp = document.createElement("input");
    inp.type = "file"; inp.multiple = true; inp.accept = ".jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff";
    inp.onchange = () => { wAddFiles(Array.from(inp.files || [])); };
    inp.click();
  };
  if (btn) btn.onclick = pick;
  if (drop) {
    drop.onclick = pick;
    drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = e => {
      e.preventDefault(); drop.classList.remove("over");
      wAddFiles(Array.from((e.dataTransfer || {}).files || []));
    };
  }
  const pk = $w("#wPick"); if (pk) pk.onclick = pick;
  const ad = $w("#wAdd"); if (ad) ad.onclick = pick;
  const cl = $w("#wClear"); if (cl) cl.onclick = () => {
    W.files.forEach(x => x.url && URL.revokeObjectURL(x.url));
    W.files = []; W.cur = null; wRenderSide(); wRenderBody();
  };
  const ex = $w("#wExt");
  if (ex) { ex.value = W.ext; ex.onchange = () => { W.ext = ex.value; }; }
  const cp = $w("#wComp");
  if (cp) { cp.checked = W.comp; cp.onchange = () => { W.comp = cp.checked; }; }
  const go = $w("#wGo");
  if (go) {
    go.disabled = !W.files.length;
    go.onclick = wRun;
    if (!W.files.length) go.title = "先导入图片";
  }
  wRenderSide();
}

/* ══════════════ 合规页 ══════════════ */
async function cRender(){
  const body = $w("#cBody"); if (!body) return;
  const online = await probeServer();
  let h = '<div class="cpane"><div class="bp-head"><div><div class="t">合规核验</div>' +
    '<div class="s">法定原件 / XMLDSig 验签 / 发票查验 —— 按票种判断是否适用，不适用就不伪造结果</div></div></div>';
  if (!online) { h += wOffline() + "</div>"; body.innerHTML = h; return; }

  let cap = {};
  try {
    const r = await fetch(LIVE_ORIGIN + "/api/capabilities", { cache: "no-store" });
    cap = await r.json();
  } catch (e) { cap = {}; }

  h += '<div class="card"><div class="card-h"><span class="t">运行环境</span>' +
    '<span class="h">' + (cap.provider === "real" ? "税务真实链路" : "Mock（算法一致，仅信任源不同）") + '</span></div>' +
    '<div class="card-b"><div class="cgrid">' +
    '<div class="ccard"><div class="h">合规提供方</div>' +
    '<div class="r"><span>OCR_COMPLIANCE_PROVIDER</span><b>' + esc(cap.provider || "—") + '</b></div>' +
    '<div class="r"><span>验签</span><b>' + (cap.abilities && cap.abilities.compliance_sign ? "可用" : "不可用") + '</b></div>' +
    '<div class="r"><span>查验</span><b>' + (cap.abilities && cap.abilities.compliance_verify ? "可用" : "不可用") + '</b></div></div>' +
    '<div class="ccard"><div class="h">OCR 运行时</div>' +
    '<div class="r"><span>解释器</span><b class="mono" style="font-size:11px">' + esc((cap.python || "").split("\\").pop() || "—") + '</b></div>' +
    '<div class="r"><span>进程内推理</span><b>' + (cap.has_ocr ? "是（快）" : "否（每次 spawn，慢几秒）") + '</b></div>' +
    '<div class="r"><span>LLM 抽取</span><b>' + (cap.llm_ready ? "已配置 " + esc(cap.llm_model || "") : "未配置 Key") + '</b></div></div>' +
    '</div></div></div>';

  /* 独立验签 */
  h += '<div class="card"><div class="card-h"><span class="t">XMLDSig 验签</span>' +
    '<span class="h">粘一段带 ds:Signature 的 XML 进来，用当前 provider 的公钥验</span></div>' +
    '<div class="card-b"><textarea id="cXml" rows="7" placeholder="粘贴 XML 原文…"></textarea>' +
    '<div style="margin-top:10px;display:flex;gap:8px;align-items:center">' +
    '<button class="btn primary" id="cVBtn">验签</button>' +
    '<button class="btn" id="cVClr">清空</button><span class="spacer"></span>' +
    '<span class="lbl" id="cVStat"></span></div>' +
    '<div id="cVOut"></div></div></div>';

  /* 票种判定 */
  h += '<div class="card"><div class="card-h"><span class="t">票种判定</span>' +
    '<span class="h">只给 OCR 文本就能判票种，并推导该票种要不要做合规动作</span></div>' +
    '<div class="card-b"><textarea id="cTxt" rows="5" placeholder="粘贴 OCR 文本…"></textarea>' +
    '<div style="margin-top:10px;display:flex;gap:8px;align-items:center">' +
    '<button class="btn primary" id="cTBtn">判定</button>' +
    '<button class="btn" id="cTClr">清空</button><span class="spacer"></span>' +
    '<span class="lbl" id="cTStat"></span></div>' +
    '<div id="cTOut"></div></div></div>';

  /* 说明 */
  h += '<div class="card"><div class="card-h"><span class="t">口径</span></div><div class="card-b">' +
    '<div class="hint">票种决定「要不要做」：英文商业发票这类非国内税务票据，取 XML / 验签 / 查验' +
    '都会如实返回<b>不适用</b>，而不是伪造一份通过。Mock 与 Real 共用同一份 XMLDSig 实现' +
    '（C14N + RSA-SHA256 + 摘要比对），差别只在公钥来源。</div></div></div>';

  h += "</div>";
  body.innerHTML = h;

  const vb = $w("#cVBtn");
  if (vb) vb.onclick = async () => {
    const xml = ($w("#cXml") || {}).value || "";
    const st = $w("#cVStat"), out = $w("#cVOut");
    if (!xml.trim()) { st.textContent = "先粘 XML"; return; }
    st.textContent = "验签中…";
    try {
      const r = await fetch(LIVE_ORIGIN + "/api/verify_xml", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ xml: xml })
      });
      const j = await r.json();
      st.textContent = "";
      if (!j.ok) { out.innerHTML = '<div class="hint">失败：' + esc(j.message || "") + '</div>'; return; }
      const good = !!j.valid;
      out.innerHTML = '<div class="cgrid" style="margin-top:12px"><div class="ccard">' +
        '<div class="h">结论 ' + wPill(good ? "ok" : "bad", good ? "签名有效" : "签名无效") + '</div>' +
        [["算法", j.algorithm], ["证书序列号", j.cert_serial], ["签名方", j.signer],
         ["签名时间", j.sign_time], ["provider", j.provider], ["耗时", j.elapsed + "s"]]
          .map(x => '<div class="r"><span>' + esc(x[0]) + '</span><b>' + esc(x[1] || "—") + '</b></div>').join("") +
        '</div></div>' + (j.reason ? '<div class="hint">' + esc(j.reason) + '</div>' : '');
    } catch (e) { st.textContent = "请求失败"; }
  };
  const vc = $w("#cVClr");
  if (vc) vc.onclick = () => { $w("#cXml").value = ""; $w("#cVOut").innerHTML = ""; $w("#cVStat").textContent = ""; };

  const tb = $w("#cTBtn");
  if (tb) tb.onclick = async () => {
    const txt = ($w("#cTxt") || {}).value || "";
    const st = $w("#cTStat"), out = $w("#cTOut");
    if (!txt.trim()) { st.textContent = "先粘文本"; return; }
    st.textContent = "判定中…";
    try {
      const r = await fetch(LIVE_ORIGIN + "/api/vtype", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: txt })
      });
      const j = await r.json();
      st.textContent = "";
      if (!j.ok) { out.innerHTML = '<div class="hint">失败：' + esc(j.message || "") + '</div>'; return; }
      const v = j.vtype || {};
      const req = v.requirements || {};
      out.innerHTML = '<div class="cgrid" style="margin-top:12px">' +
        '<div class="ccard"><div class="h">票种</div>' +
        [["代码", v.code], ["名称", v.name], ["简称", v.short], ["置信", v.confidence],
         ["地区", v.region]].map(x => '<div class="r"><span>' + esc(x[0]) +
          '</span><b>' + esc(x[1] != null ? String(x[1]) : "—") + '</b></div>').join("") + '</div>' +
        '<div class="ccard"><div class="h">合规要求</div>' +
        [["法定原件", req.legal_original], ["验签", String(req.signature_check)],
         ["查验", req.verify], ["需要 XML", String(req.need_xml)]]
          .map(x => '<div class="r"><span>' + esc(x[0]) + '</span><b>' + esc(x[1] != null ? String(x[1]) : "—") +
            '</b></div>').join("") + '</div></div>' +
        (v.evidence && v.evidence.length ? '<div class="hint">判定依据：' + esc(v.evidence.join("、")) + '</div>' : '');
    } catch (e) { st.textContent = "请求失败"; }
  };
  const tc = $w("#cTClr");
  if (tc) tc.onclick = () => { $w("#cTxt").value = ""; $w("#cTOut").innerHTML = ""; $w("#cTStat").textContent = ""; };
}

/* ══════════════ 四视图切换（覆盖 optblock 的两视图版本）═════════════ */
function switchView(v) {
  BState.view = v;
  document.querySelectorAll(".seg button").forEach(b => b.classList.toggle("on", b.dataset.v === v));
  const map = { doc: "shellDoc", batch: "shellBatch", work: "shellWork", comp: "shellComp" };
  Object.keys(map).forEach(k => {
    const el = document.getElementById(map[k]);
    if (el) el.classList.toggle("hidden", v !== k);
  });
  const q = document.querySelector(".topbar .search");
  if (q) q.classList.toggle("hide", v !== "doc");
  if (v === "batch") { bRenderSide(); bRenderBody(); }
  if (v === "work") wRenderBody();
  if (v === "comp") cRender();
}
document.querySelectorAll(".seg button").forEach(b => b.onclick = () => switchView(b.dataset.v));

/* 进来看一眼后端在不在，不在就把「识别」页的说明换成启动指引 */
(async function wBoot() {
  const on = await probeServer();
  W.online = on;
  const tag = $w("#wConn");
  if (tag) {
    tag.textContent = on ? "后端已连接" : "后端未启动";
    tag.className = "pill " + (on ? "ok" : "warn");
  }
  switchView("work");
})();
