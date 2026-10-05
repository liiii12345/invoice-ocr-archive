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

# 3) 打开交互控制台（浏览器直接打开，无需起服务）
open demo_console.html
```

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
python tools/console_server.py            # 默认 8770，仅听 127.0.0.1
python tools/console_server.py --port 8770 --root .
```

它做的事很薄：`/api/run` 收到表单参数 → 拼 `src/batch_run.py` 命令行 → `subprocess` 执行 →
把 stdout 逐行以 SSE `log` 事件推给前端 → 结束推 `done` 带 `summary`。
安全上 `--out` 被锁在仓库目录内（可用 `--outside` 显式放开），抽取策略与并发数走白名单，
单任务 30 分钟超时。

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
│   ├── invoice_extract.py            规则抽取器（版面坐标推理）
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
│   ├── console_server.py             可选本地后端，只听 127.0.0.1，让页签能真跑 batch_run.py
│   └── _check_batch_ui.js            页签回归测试（jsdom，30 条断言，`npm i jsdom` 后 `node tools/_check_batch_ui.js`）
├── docs/                             设计说明、真实性与边界说明、接入指南、页签示意图、可优化点清单
├── requirements.txt
└── LICENSE
```

### 控制台是怎么构建出来的

`demo_console.html` 是**构建产物**，别手改它。源码在 `tools/build_console.py`（文档档案视图）与
`tools/build_batch_ui.py`（批量测试视图）两个注入器里，都幂等——反复跑只会覆盖自己那一段：

```bash
python tools/build_console.py            # 重建基础控制台
python tools/build_batch_ui.py           # 本地版：带你机器上的数据集预设，跑「实跑」用
python tools/build_batch_ui.py --public  # 公开版：只留仓库内置合成样例，可推 GitHub
```

本地版会把你自己磁盘上的数据集目录写进预设里（控制台一开就能选），公开版必须走 `--public`
——否则真实路径会跟着产物一起出仓库。

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

## License

MIT —— 见 [LICENSE](./LICENSE)。
