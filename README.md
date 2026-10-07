# Invoice OCR & Compliance Archive

面向企业侧票据的 **OCR 识别 → 票种判定 → 税务合规核验 → 结构化抽取 → 归档检索** 一条链路，  
单进程可跑、无外部服务依赖、无真实企业凭据也能验证。

- **不依赖任何密钥就能跑通**：`python src/compliance_provider.py` 跑完整合规自测（23 项全绿）。
- **验签是真密码学，不是一路绿灯**：标准 XMLDSig（C14N 1.0 + RSA-SHA256 + 摘要比对），  
  换一颗无关公钥立即验不过，改一个金额必被摘要检出。
- **控制台双击即开**：`demo_console.html` 是单文件交互控制台，数据全部内嵌，离线可用。                                                                                        

---

## 快速开始

```bash
# 1) 只跑合规链路自测（唯一依赖 cryptography）
pip install cryptography
python src/compliance_provider.py

# 2) 跑完整 OCR + 抽取（额外需要本地推理与图像库）
pip install -r requirements.txt
python src/ocr_service.py --host 127.0.0.1 --port 8765

# 3) 起本地后端 —— 想让控制台「真能识别」就靠它（只听 127.0.0.1）
python tools/console_server.py
# OCR 依赖装在别的解释器时指定它（不指定也能跑，只是每次多 spawn 一次进程）
python tools/console_server.py --python ../venv/Scripts/python.exe
# 想让数据集下拉自动列出本机目录
python tools/console_server.py --scan D:/你的发票目录

# 4) 打开控制台，顶栏「识别」页把发票拖进去
open demo_console.html        # 或直接访问 http://127.0.0.1:8770
```

> 单文件 HTML 里没有 Python 运行时，OCR 必须在本机跑。
> 后端没起时控制台仍然能打开，但「识别 / 合规」会明说「需要本地后端」，**不会给你编造识别结果**。

自测输出末尾会打一行 `通过 23 / 23`；其中关键的三条：

```
✓ Real 用「税务根证书公钥」验签 Mock 签发的 XML   → 通过（同一份 _xml_verify）
      换一颗无关公钥                              → 失败（SignedInfo 不匹配）
      篡改正文金额                                → 失败（内容摘要不一致）
```

---

## 系统链路

```
 原始文件（图片 / PDF）
        │
        ▼
 ① OCR 识别 ─────────────── 本地 RapidOCR（ONNX）／云端多模态模型双通道，失败自动降采样重试
        │  正文 + 版面坐标
        ▼
 ② 票种判定 ─────────────── vtype_classify，输出「适用 / 不适用 / 待人工」三态
        │
        ├─── 适用 ──► ③a 取法定原件（XML / OFD / PDF）
        │                 ③b 税务数字签名验签（XMLDSig）
        │                 ③c 税务局查验（含 24h 缓存）
        │             （三者由 ComplianceProvider 统一接口，可 Mock / Real 切换）
        ▼
 ④ 结构化抽取 ───────────── 规则抽取（版面坐标推理）／LLM 抽取／hybrid 融合
        │  统一字段字典（FIELD_KEYS）+ 逐字段裁决
        ▼
 ⑤ 归档与检索 ───────────── SQLite 索引，内容 sha256 去重，全文检索
        │
        ▼
 ⑥ 交互控制台 ───────────── demo_console.html（概览 / 抽取字段 / OCR 原文 / 预览 / 风险印章）
```



---

## 控制台：四个页签

`demo_console.html` 顶栏四个入口，按「拿到一张发票后你会做什么」的顺序排：

| 页签 | 干什么 | 依赖后端 |
| :- | :- | :- |
| **识别** | 拖入/选择单张或多张图片 → 逐张识别 → 看字段、证据放大图、OCR 原文、合规结论 | 是 |
| **档案** | **你自己跑出来的**识别结果的检索与详情：概览 / 抽取字段 / OCR 原文 / 预览 / 验签 / 查重 | 是（未接后端时显示合成样例，并显式标注来源） |
| **批量** | 一整批图片跑批，出汇总表、字段准确率、耗时分布、多任务对比 | 部分（离线为样例） |
| **合规** | 运行环境体检、XMLDSig 独立验签、票种判定，以及「适不适用」的判断口径 | 是 |

### 档案页的数据是哪来的

不是写死在页面里的演示数据。链路是：「识别」页每识别成功一张 → 结果追加落盘到 `out/results.jsonl`
→ 「档案」页启动时 `GET /api/archive` 拉回来展示。**所以档案里每一条，都是你自己跑出来的发票。**

侧栏顶部会明示当前数据来源，三态互斥，不会把合成样例冒充成真实结果：

| 侧栏徽标 | 含义 | 什么时候出现 |
| :- | :- | :- |
| `真实识别结果 · N 条` | 来自 `out/results.jsonl` 的真实识别结果 | 后端在线且已跑过识别 |
| `后端在线 · 暂无识别记录` | 已连上后端，但还没识别过任何发票 | 首次启动后端 |
| `未接本地后端 · 当前为内置合成样例` | 本地后端没开（如直接 `file://` 打开页面） | 未执行 `console_server.py` |

归档只落结构化结果（字段 / 明细 / 票数），**不存原图、不存任何凭据或密钥**。

### 识别页怎么用

1. 把发票图片**拖进虚线框**（或点「选择文件」），单张多张都行，非图片会被过滤掉；**也支持直接拖入数电票 XML / OFD 原件**（免图片二传，精度更高，且自动走原生解析 + 税务数字签名验签）
2. 选抽取策略：`rule` 快（约 5-8 秒/张）/ `hybrid` 稳 / `llm` 慢但准；勾「跑合规链路」会顺带跑取原件 → 验签 → 查验
3. 点「开始识别」→ 逐张上传，进度条带 ETA，可随时中止
4. 结果分三块看：
   - **左**：原图（数电票原件显示「数电票原件（原生解析）」占位块，不依赖图像）+ 票种/来源/置信/行数/耗时
   - **右**：字段表（值 + 置信 + **证据放大图**，就是从原图裁出来放大那一块，用来肉眼核对）+ 明细行 + 合规三卡；
     数电票原件额外显示**「数电票原件验签（XMLDSig）」卡**，所有识别结果都额外显示**「重复报销核查」卡**（本机 SQLite 跨库比对）
   - **下**：OCR 原文逐行（按版面顺序，带置信度；数电票原件无 OCR 原文，改为展示结构化 XML 解析结果）

多张时左侧文件列表点一下就切到那张；失败的张会直接显示原因，不静默吞掉。

图片只发到本机 `127.0.0.1`，不经过任何外部服务；落盘在 `uploads/`（已在 `.gitignore` 里）。

### 合规页在验什么

不是「一路绿灯」，而是先判断**该不该做**：

- 票种判定给出 `requirements`（要不要 XML、要不要验签、要不要查验）
- 英文商业发票这类非国内税务票据，三项会如实返回**不适用**，而不是伪造一份「验签通过」
- Mock 与 Real 共用同一份 XMLDSig 实现（C14N + RSA-SHA256 + 摘要比对），差别只在公钥来源

---

## 批量测试：丢一整批图片进去，直接出汇总表和准确率

`src/batch_run.py` 是批量入口——逐张跑「OCR → 票种判定 → 抽取 → 合规链路」，
最后落一张 `results.csv` + 一份 `summary.json`（含字段级准确率）。

```bash
# 摸底 20 张：目录/glob 都行，--truth 带真值就顺带算准确率
python src/batch_run.py \
    --input "D:/data/batch_1/batch1_1/*.jpg" \
    --limit 20 --out out/ --truth D:/data/batch_1/batch1_1.csv

# 断点续跑：上次成功的自动跳过
python src/batch_run.py --input D:/data/batch_1 --resume --out out/

# 换抽取策略 / 多进程
python src/batch_run.py --input list.txt --extractor hybrid --workers 4 --out out/
```

**输入四种写法**（`--input` 可重复传，自动去重）：

| 写法 | 说明 |
| :- | :- |
| `--input 目录` | 递归扫 jpg/jpeg/png/webp/bmp |
| `--input "glob 模式"` | 用引号包住，如 `"D:/data/**/*.jpg"` |
| `--input list.txt` | 每行一个路径，`#` 开头是注释 |
| `--input truth.csv` | 第一列文件名，配合 `--base` 拼绝对路径 |

**输出**（都在 `--out` 目录下）：

| 文件 | 内容 |
| :- | :- |
| `results.csv` | 一文件一行：文件名 / 成败 / 票种 **code + 中文名** / 平均置信度 / OCR 行数 / 耗时 / 六个字段值 / 明细条数 / 失败原因 |
| `summary.json` | 总览：成功失败数、票种分布、字段非空率、字段准确率、明细一致率、耗时 avg/p50/p90/max、吞吐张每分钟 |
| `failed.txt` | 失败清单（带原因） |
| `results_detail.json` | 加 `--detail`：每张的 OCR 全文、字段证据坐标、完整记录 |

准确率口径与 `src/eval_extract.py` 完全一致（数字对得上）：
文本字段归一化后全等且非空；金额字段 `parse_amount` 后差值 < 0.02；明细要条数一致且同序 quantity / total_price 都对。

实测（英文商业发票 20 张，`--extractor rule`）：20/20 成功，
字段准确率 5/6 = 1.0（total 0.95），明细全对 19/20，平均 7.6s/张、约 7.8 张/分钟。

### 批量测试页签（图形界面）

命令行能用之后，同一套参数被搬进了控制台——`demo_console.html` 顶栏切到**「批量测试」**就是工作台。

![批量测试](/docs/batch-shot.svg)

- **左**：数据集下拉、新建任务、历史任务列表（存 localStorage，可重跑）
- **中·表单**：输入源（可多选 chips）、抽取策略、并发数、`--limit / --offset`、
  `--truth / --base / --out`、`--resume / --detail`
- **中·命令卡**：表单改动实时拼出等价命令，一键复制——**表单和 CLI 是同一套参数**，不是另一套演示逻辑
- **中·预计**：按策略系数 × 数据集 × `Math.pow(workers, 0.55)` 现算耗时与吞吐，方便先估再跑
- **结果区**：KPI 卡（总量/成功/失败/耗时/吞吐/准确率）、字段准确率六条横条、
  耗时分布柱状图、当前数据集下 rule / hybrid / llm 三策略对比

| 模式 | 怎么触发 | 结果从哪来 |
| :- | :- | :- |
| 实跑（默认探测） | 控制台自动探 `127.0.0.1:8770`，探到就走 SSE 真跑 | `src/batch_run.py` 的 `summary.json` 原样映射回来 |
| 离线样例（探不到就降级） | 直接开 HTML 点运行 | 按参数推算的沙箱指标，日志末行明说「样例 · 离线沙箱」 |

两种模式在界面右上角用 pill 标注，**不会把样例数字冒充成真实运行结果**。

想真跑，起一个只监听回环的本地后端（可选，`tools/console_server.py`）：

```bash
python tools/console_server.py
python tools/console_server.py --scan D:/data/invoices --scan E:/more   # 数据集下拉自动列出本机目录
python tools/console_server.py --python ../venv/Scripts/python.exe       # OCR 依赖装在别的解释器时
```

它做的事很薄：`/api/run` 收到表单参数 → 拼 `src/batch_run.py` 命令行 → `subprocess` 执行 →
把 stdout 逐行以 SSE `log` 事件推给前端 → 结束推 `done` 带 `summary`。
安全上 `--out` 被锁在仓库目录内（可用 `--outside` 显式放开），抽取策略与并发数走白名单，
单任务 30 分钟超时；LLM Key 只在请求里以环境变量注入子进程，**不写文件、不进日志、不回显**。

#### 页签上另外 12 件事

| # | 功能 | 落在哪 |
| :- | :- | :- |
| 1 | **真实数据集自动探测** | `--scan` 的目录会被扫出来（含同名真值 csv 自动配对），下拉里另起一组「本机扫描」 |
| 2 | **LLM Key 配置面板** | 选 llm/hybrid 才展开；Key 存 localStorage、只随本次运行注入环境变量，不明文回显 |
| 3 | **多任务对比** | 侧栏勾选 ≥2 个任务 → 出对比表，并直接说「谁最快 / 谁最准」 |
| 4 | **断点续跑可视化** | 跑之前先读一次 `results.csv`，跑完按「新增 / 重跑 / 失败」三段统计 |
| 5 | 三段式进度条 | 同上，落在结果区顶部的「断点续跑」卡 |
| 6 | **批量结果跳档案视图** | 明细行点「档案视图 ›」→ 映射成一条文档记录并选中，不用一张张点 |
| 7 | **字段横向差异对照** | 行=文件、列=六字段，缺失/非数值/日期格式可疑的格子标橙，附每列异常数 |
| 8 | 日志分色 | `$` / `[plan]` / `[field]` / `[overview]` / `[warn]` / `FAIL` / `[error]` 各一色，失败项可点 |
| 9 | 进度 ETA | 按已完成的平均用时外推「预计剩余 X 分 Y 秒」 |
| 10 | 导出 Markdown / JSON | 报告里带参数、命令、指标与抽样复核结论，可直接贴周报 |
| 11 | 抽样复核标记 | 明细行 ✓/✗ 打标，统计「已复核 n 张 · 判错 k · 错误率 x%」，导出时带上 |
| 12 | 运行中止 | 关 SSE + 调 `/api/stop`；已跑完的张已落盘，下次 `--resume` 会跳过 |

底层为此改了两处，否则上面几条是假的：

- `src/batch_run.py` 单进程路径原来写成列表推导（`raws = [run_one(p) for p in files]`），
  等于**先把所有张跑完再统一打印和写 CSV**——进度条不动、ETA 没意义、中止时一行都不剩。
  现在改成跑一张就 `_emit()` 一张（写盘 + flush + 打进度）。
- `sys.stdout.reconfigure(encoding="utf-8")` 会把缓冲重置回块缓冲，连 `python -u` 都被吃掉，
  所以重设编码时显式带上了 `line_buffering=True`。

---

## 目录结构

```
.
├── demo_console.html                 单文件交互控制台（数据内嵌，离线打开即用）
├── data/
│   └── synthetic_demo_data.json      控制台演示数据（全合成样例）
├── src/
│   ├── batch_run.py                  ★ 批量测试入口：一整批图片 → results.csv + summary.json
│   ├── compliance_provider.py        ★ 合规能力：法定原件 / 验签 / 查验，Mock 与 Real 同接口
│   ├── ocr_service.py                本地服务入口（HTTP + 批处理）
│   ├── ocr_once.py                   ★ 单张识别入口：图片 → JSON（后端 spawn 用）
│   ├── invoice_extract.py            规则抽取器（版面坐标推理）
│   ├── dedup.py                       ★ 跨文档查重引擎（SQLite 指纹，堵重复报销）
│   ├── einvoice_xml.py                ★ 数电票 XML 原生解析（结构化字段，复用 _xml_verify）
│   ├── ofd_parser.py                  ★ OFD 解包抽取内嵌结构化发票 XML
│   ├── table_struct.py                ★ 明细行自适应列检测 + 角色启发（替代写死阈值）
│   ├── llm_extract.py                LLM 抽取器 + hybrid 融合
│   ├── vtype_classify.py             票种判定与「适用性」三态
│   ├── extract_samples.py            样例导出（PIL）
│   ├── eval_extract.py               规则抽取评测
│   └── eval_llm.py                   LLM 抽取评测
├── tools/
│   ├── make_demo_data.py             生成合成演示数据（PIL 现画预览图）
│   ├── build_console.py              把合成数据注入控制台
│   ├── build_batch_ui.py             ★ 把批量参数注入控制台，产出「批量测试」页签（幂等，可重复跑）
│   ├── _runblock.js                  「运行」逻辑源（实跑 SSE / 离线沙箱两条分支）
│   ├── console_server.py             ★ 本地后端：上传识别 / 批量 / 数据集扫描 / 合规（只听回环）
│   ├── build_workspace.py            ★ 注入「识别」「合规」两个视图 + 四段导航（幂等）
│   ├── _workspace.js                 识别页与合规页的逻辑源
│   ├── _check_batch_ui.js            批量页签回归测试（jsdom，30 条断言）
│   ├── _check_opt.js                 12 条优化的回归测试（jsdom，50 条断言）
│   ├── _check_workspace.js           识别/合规页回归测试（jsdom，32 条断言）
│   └── _check_upload.js              端到端：jsdom 前端真的向后端上传一张图（10 条）
│
│   跑测试：npm i jsdom
│           node tools/_check_batch_ui.js && node tools/_check_opt.js
│           node tools/_check_workspace.js
│           node tools/_check_upload.js <一张发票图片>   # 需先起后端
├── docs/                             设计说明、真实性与边界说明、接入指南、页签示意图、可优化点清单
├── requirements.txt
└── LICENSE
```

### 控制台是怎么构建出来的

`demo_console.html` 是**构建产物**，别手改它。三个注入器按顺序跑，每个只改自己那一段，都幂等：

```bash
python tools/build_console.py            # ① 基础控制台（文档档案视图 + 合成演示数据）
python tools/build_batch_ui.py --public  # ② 批量测试视图 + 顶栏切换
python tools/build_workspace.py          # ③ 识别 + 合规两个视图，并把导航扩成四个
```

顺序不能乱：③ 复用 ② 的 `esc / toast / probeServer / bLlm / bEtaSec`，并接管它的 `switchView`。

注入器一律用 `/*XXX*/ … /*XXX_END*/` 首尾标记做锚点。**替换串只能 `rstrip("\n")` 去掉尾换行，
不能 `strip()`**——去掉前导换行会让上一段的结尾标记和本段开头粘在同一行，
每次构建吃掉一个换行，产物 md5 一直漂移；而 `re.sub` 匹配不上时**不报错**，
得用 `re.subn` 拿返回值 n 判断，否则新代码会被整段静默丢掉。

---

## 合规链路：算法一致，只有信任源不同

`ComplianceProvider` 两个实现（`MockComplianceProvider` / `RealComplianceProvider`）共用同一份标准 XMLDSig 实现：

| 环节 | 做法                                                                      |
| :- | :---------------------------------------------------------------------- |
| 摘要 | SHA256 over `c14n(摘掉 ds:Signature 的 XML)`，enveloped-signature transform |
| 签名 | RSA-SHA256（`rsa-sha256`）over `c14n(SignedInfo)`                         |
| 验签 | 先验签名，再比对 `Reference/DigestValue`，**两关都过才判有效**                           |
| 节点 | `<ds:Signature>`，标准命名空间（序列化走 `ds` 前缀）                                   |

切换实现只改一个环境变量，**业务代码零改动**：

```bash
export OCR_COMPLIANCE_PROVIDER=mock     # 默认，本地即可跑通全部验签
export OCR_COMPLIANCE_PROVIDER=real     # 接税局真实链路
```

`RealComplianceProvider` 覆盖 OAuth2 换 token → `/api/invoice/download` 取 XML/OFD/PDF →  
共用 `verify_signature` → 查验结论 24h 缓存 + 状态码映射  
（`1000` 正常 / `1001` 作废 / `1002` 红冲 / `1003` 查无 / `1004` 超限）。

**缺保密凭据时不抛异常、不伪造成功**，而是如实返回原因，例如：

```
未配置 OCR_TAX_ROOT_CERT（税务根证书公钥）。验签算法与真实实现完全一致，
仅信任源不同：真实环境用税务根证书公钥，本地测试用自签公钥。
```

---

## 涉密参数（一律走环境变量，仓库内不落盘）

| 变量                                                                | 含义                     |
| :---------------------------------------------------------------- | :--------------------- |
| `OCR_COMPLIANCE_PROVIDER`                                         | `mock` / `real`，切换合规实现 |
| `OCR_LEQI_BASE_URL` / `OCR_LEQI_TOKEN` / `OCR_LEQI_CLIENT_SECRET` | 税局侧凭据                  |
| `OCR_TAX_ROOT_CERT` / `OCR_TAX_CERT_SERIAL`                       | 税务根证书公钥、证书序列号白名单       |
| `OCR_VERIFY_API_URL` / `OCR_CA_BUNDLE` / `OCR_VERIFY_CACHE`       | 查验接口、CA Bundle、缓存文件    |
| `OCR_LLM_*`                                                       | LLM 抽取的模型与凭据           |

真实发票影像、开票方信息、企业凭据**均不包含在本仓库内**；仓库内的演示数据是合成样例。

---

## 演示数据为什么是合成的

`data/synthetic_demo_data.json` 与 `demo_console.html` 里的数据由 `tools/make_demo_data.py` 生成：

- 票面「销售方 / 购买方 / 发票代码」全部是占位值（示例科技有限公司 / 示例采购中心 / `SYNxxxx`）；
- 预览图由 PIL 现画成占位文档版式，带「合成样例 / SAMPLE」水印，**不是任何真实票据的扫描件**；
- schema 与真实 ingest 输出完全一致，替换成真实数据只需改数据来源，控制台不需要动。

重新生成：

```bash
pip install Pillow          # 仅生成预览图需要
python tools/make_demo_data.py
python tools/build_console.py <本地demo_console.html> demo_console.html
```

---

## 已知边界

- **国密 SM2**：税局部分链路用 SM2，需 `gmssl` 一类国密库；`cryptography` 不覆盖，仓库内未实现。
- **C14N**：标准库 `xml.etree.ElementTree.canonicalize` 只支持 C14N 1.0（inclusive）；  
  若税局下发用 exc-c14n 或 c14n-11，需换 `lxml` 实现。
- **查验限流**：税务局查验有频次限制，必须走缓存（本仓库 `OCR_VERIFY_CACHE`，TTL 24h）。
- **复刻版不冒充真实结果**：本仓库不发真实税局请求、不落企业凭据，任何界面也不会把合成数据显示成真实查验结论。

---

## 竞品对比与完善路线（摘要）

完整版见 [docs/竞品对比与完善路线.md](./docs/竞品对比与完善路线.md)。

**定位**：这是「发票 OCR + 税务合规核验」的**脱敏复刻版 / 作品集项目**——
对标分两层：底层和 PaddleOCR / RapidOCR / Tesseract / Surya / invoice2data 比识别抽取；
系统层和合思（易快报）/ 每刻 / 汇联易及百度·阿里·腾讯云发票 API 比合规闭环。

**差异化强项（本项目独有或少见）**：

- 本地化、**数据不出本机**、验签可自测可审计；
- 税务数字签名验签是**真 XMLDSig**（C14N + RSA-SHA256 + 摘要比对），Mock/Real 共用 `_xml_verify`；
- **票种驱动适用性判定**：对非国内票据如实返回「不适用」，不伪造结果。

**与商业平台的可见差距（大多纯本地就能补）**：

| 差距 | 性质 | 完善路线位置 | 状态 |
| :- | :- | :- | :- |
| 表格结构还原靠坐标近似，不如 PP-Structure | 本地可做 | 路线 #3 | ✅ 已做自适应列检测（2026-10-07） |
| 缺数电票 OFD/XML 原生解析 | 本地可做 | 路线 #2 | ✅ 已实现（2026-10-07） |
| 无跨文档查重 | 本地可做 | 路线 #1 | ✅ 已实现（2026-10-07） |
| 票种仅 9 类，未覆盖全票种 | 本地可做 | 路线 #4 | 待定 |
| 国密 SM2 验签（cryptography 不支持） | 本地可做 | 路线 #5 | 待定 |
| 无带数字签名的合规核验报告（单套制） | 本地可做 | 路线 #6 | 待定 |
| 真实税局查验直连 | ⚙️ 需配保密凭据（Real Provider） | 路线同真实系统，凭据不外带 | 已实现（凭据不外带） |

> 凡涉及税局真实交互的项，公开仓库一律保持**可插拔 Real Provider + 环境变量隔离**：
> 不落盘、不发真实请求、不把合成结果显示成真实查验结论。完善 ≠ 假装接入。

---

## License

MIT —— 见 [LICENSE](./LICENSE)。
