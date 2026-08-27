# 智能市场舆情分析系统：需求分析、可行性与实施计划

> 仓库当前几乎为空（仅有 README）。本文是第一份产品与技术方案，用于对齐范围后再开工。  
> 结论先说：**可以做成一个可用的「按需、低成本、有依据」的市场分析助手；不能做成免登录、全网微博/X 实时舆情平台。**  
> 行情（指数 / 板块 / 相关标的涨跌）应作为默认校准层接入；它在网页上公开可见，但程序化拉取仍有延迟、限速和接口变更风险。  
> **AI API Key 绝不能放进 GitHub Pages 或前端代码。** 正确位置是本仓库的 GitHub Actions Secrets（加密、不进 git、访客不可见）。

---

## 1. 需求还原

把原始描述拆成可验收的能力，并标出原文里不完整的地方。

### 1.1 用户目标

用户没有自有服务器，希望：

1. **前端**部署在 GitHub Pages。
2. **主动触发**：点按钮才开始分析，而不是 7×24 常驻爬虫。
3. **分析前可输入侧重点**（例：`存储相关`）。不填则做当日/近 24–48 小时的泛市场扫描。
4. **后端用 AI 先规划再取数**：根据侧重点决定看哪些板块、哪些信源、哪些关键词、哪些博主，而不是固定爬全网。
5. **信源**：
   - 微博、X 上有分量博主的前瞻，以及部分用户的指标性发言；
   - 国内外对市场影响力较大的财经信息，强调**时效性**；
   - **当前市场行情**（指数、相关板块、规划出的标的），用来校准「舆情热」是否已被价格兑现。
6. **输出**：详细的**板块热度前瞻**、**分析依据**，以及原文在「以及」处中断、未写完的第三块。本文按完整产品补全为：来源与时间戳、置信度、反向证据、风险与失效条件、**行情校准（涨跌、相对大盘、是否已计价）**。
7. **密钥**：用户有 AI API Key，先配一个，后续可加多个；系统要**效用最大化 + 成本控制**。Key **不放在网站里**，只放 GitHub Actions Secrets（见 §12）。
8. **鉴权**：默认只用公共信息；若某信源必须登录/付费，需明确「要什么、怎么拿」。

### 1.2 建议的产品边界（第一版就写死）

| 做 | 不做（至少第一期不做） |
| --- | --- |
| 个人研究助手：一次点击 → 一份带出处的报告 | 券商级实时舆情监控台 |
| 公开 RSS / 官方或半官方新闻 API / 用户自备的社交 API | 绕过登录、验证码、WAF 去爬微博/X |
| 公开行情快照（指数 / 板块 / 少量相关标的）校准舆情 | 逐笔成交、Level-2、自建行情源、荐股与目标价 |
| 板块热度、叙事方向、证据链、时效窗口、价讯是否背离 | 买卖点 |
| 主动触发、结果可回看 | 全网 7×24 流式采集 |
| AI 规划取数范围，避免无脑全量灌模型 | 把原始 HTML/全量帖子/全市场行情直接丢给大模型 |

免责声明应写在页面底部：本系统是信息聚合与研究辅助，不是投资建议。

---

## 2. 可行性结论

**总体：有条件可行。** 核心链路（前端 → 规划 → 拉公开财经信息与行情快照 → 压缩 → AI 对照价格综合 → 报告）可以在无自有服务器的前提下落地。社交舆情是最大的成本和合规瓶颈，必须做成**可选插件**。行情作为默认校准层，比社交更便宜、也更能压住「只根据标题喊涨」的幻觉。

### 2.1 按模块打分

| 模块 | 可行性 | 说明 |
| --- | --- | --- |
| GitHub Pages 前端 | 高 | 静态站点完全匹配「点按钮、填侧重点、看报告」。 |
| 无自有服务器的后端 | 中高 | Pages **不能跑后端、不能藏 API Key**。必须另接免费/低成本计算（见 §3）。 |
| AI 规划分析任务 | 高 | 用便宜模型根据侧重点产出「关键词 / 板块 / 信源 / 时间窗」即可。 |
| 国内外财经资讯（公开） | 高 | RSS + 免费新闻 API 足够支撑第一期；中文快讯稳定性低于英文主流媒体。 |
| 当前市场行情（校准用） | 高 | 网页报价公开可见。A 股常用新浪/腾讯/东财网页 JSON（无 key、限速、非官方契约）；美股/全球可用免费 API 或延迟行情。用于校准，不追求交易所官方实时。 |
| 微博有分量博主 | 低（无账号） / 中（有官方授权） | 开放平台要应用审核 + OAuth；搜索/时间线能力受限；商业舆情接口面向企业。页面爬取会触发风控，且违反平台约定，**本项目不做**。 |
| X 有分量博主 | 低（免费） / 中（付费按次） | 2026 年起新开发者默认 **pay-per-use**，无可用免费读额度。读一条帖约 **$0.005**（以 [X Developer Console](https://console.x.com) 当时标价为准）。200 条帖 ≈ $1，成本会很快超过 AI 本身。 |
| 时效性（分钟级） | 低 | 无常驻进程、无付费火线。主动点击场景下，**小时级 / 当日**是合理目标。 |
| 成本可控的 AI | 高 | 分层模型 + 先过滤再综合 + 缓存，可以把单次分析压到很小的 token 开销。 |
| 结果质量（板块前瞻） | 中高 | 有新闻 + 行情校准时，能区分「在讲」和「已涨」。缺少社交信号时仍偏叙事，但价格是硬约束，幻觉空间更小。 |

### 2.2 必须接受的硬约束

1. **GitHub Pages 只托管静态文件。** 浏览器里不能放 AI Key、X Token、微博 Token。任何密钥进前端，等于公开。
2. **「爬取」公开网页 ≠ 合法稳定。** 微博、X、雪球、部分中文财经站点有登录墙、WAF 和反爬。本项目默认走 **RSS、官方 API、站点自己提供给网页的公开 JSON（需限速、可降级）**。不实现模拟登录、cookie 池、验证码破解。
3. **社交数据默认缺席博主时间线。** 没有 X / 微博凭证时，系统仍应能出报告。微博**公开热搜快照**可以无 token 拉取，但海外 Actions 可能被拒，失败则标注「未接入微博热搜」。
4. **Cloudflare 免费 Workers 的 CPU 限额很紧**（约 10ms CPU / 请求量级，I/O 等待不计入）。一次「多源拉取 + 两次 LLM」更适合：付费 Workers / Workflows，或 **GitHub Actions**（本仓库已有，零额外账号）。
5. **公开仓库 + GitHub Actions**：Workflow secrets 不会进 Pages，但 fork PR 读不到 secrets。密钥只配在本仓库 Settings → Secrets。

### 2.3 推荐策略：先做「新闻 + 行情校准」，社交作为插件

第一期用公开财经信息 + 公开行情快照，回答「存储相关最近在讲什么、价格是否已经兑现、哪些子板块热而价弱/价强而讯少」。  
微博/X 博主前瞻仍作后续插件。行情比社交更适合做默认层：公开、便宜、对校准最有效。

---

## 3. 无服务器架构

目标：用户零台 VPS，密钥不进浏览器，点一次按钮能拿到一份报告。

### 3.1 推荐架构（本仓库默认按此实施）

```text
用户浏览器 (GitHub Pages)
    │  POST /api/analyze  { focus, lookbackHours }
    │  轮询 GET /api/jobs/:id
    ▼
GitHub Actions  (真正的分析进程)
    │  1. 便宜模型：规划关键词 / 板块 / 标的代码 / 信源
    │  2. 并行拉取 RSS / 新闻 API / 行情快照 /（可选）社交 API
    │  3. 去重、时效过滤、相关度打分，只留 Top N 文本；行情压成一张小表
    │  4. 中等模型：对照「舆情 vs 价格」综合成结构化报告
    │  5. 把 JSON 报告写到 docs/reports/ 或 gh-pages
    ▼
Pages 展示最新报告（板块热度、依据、来源链接、时间）
```

触发方式有两档，可并存：

| 档 | 怎么点 | 特点 |
| --- | --- | --- |
| A. 仓库内 `workflow_dispatch` | GitHub → Actions → Run workflow，填侧重点 | **零额外平台**。按钮不在 Pages 上，但完全免费、密钥最安全。适合第一期。 |
| B. Pages 上的「开始分析」 | 前端调一个极薄的网关（Cloudflare Worker 或 GitHub App），网关再用 GitHub API 触发 workflow | 体验最好。需要一个免费 Cloudflare 账号，或一个 GitHub App。**网关里只放触发分析的 token，不放 AI Key。** |

分析进程放在 GitHub Actions 的原因：

- 用户已经在用 GitHub，不必再买服务器。
- 公开仓库有较充足的分钟额度；一次分析 1–3 分钟可接受。
- 跑完把报告提交回 `gh-pages` / `docs/reports`，Pages 自动更新。
- LLM 调用是网络 I/O，Actions 比免费 Workers 更不容易被 CPU 限额掐断。

### 3.2 备选：Cloudflare Pages + Worker（若用户愿意离开「纯 GitHub Pages」）

前后端同域、无 CORS、可用 KV 存任务状态。前端仍是静态的，只是托管方换成 Cloudflare。用户明确要求 Pages 时，把它当备选，不作为第一期默认。

### 3.3 明确不采用

- 把 AI Key 写进前端 / `config.js` / GitHub Pages 仓库明文 / 浏览器 localStorage。
- 用浏览器直连 OpenAI / X / 微博（CORS 和密钥双杀）。
- 自建常驻爬虫、代理池、账号农场。
- 把全市场逐笔行情灌进大模型。

---

## 4. 数据源清单：公共默认可跑，鉴权按需打开

原则：**适配器可插拔**。规划器只选择「当前已配置且健康」的源。某个源失败时跳过并在报告里写明，不让整次任务失败。

### 4.1 默认（无需登录）

| 源 | 用途 | 时效 | 备注 |
| --- | --- | --- | --- |
| 国际媒体 RSS（Reuters / CNBC / BBC Business / MarketWatch 等公开 feed） | 全球宏观与公司新闻 | 分钟–小时 | 无 key；条目多为标题+摘要，足够做叙事。 |
| Google News RSS（按规划出的关键词检索） | 补「侧重点」召回 | 小时 | 无 key；噪声大，必须做相关度过滤。 |
| 中文权威媒体公开 RSS（如人民网、中新网、经济观察网、凤凰财经等经验证 feed） | 国内政策与财经 | 小时 | 官方 RSS 比「网页接口」稳。 |
| SEC EDGAR 近期 filings RSS / 美联储等官方 feed | 监管与宏观 | 小时–日 | 高质量、低噪声。 |
| Reddit 公开 JSON（如 `r/stocks`、`r/wallstreetbets` 的 `.json`） | 英文零售情绪替代 | 小时 | 无 key 有较严限速；**不能替代微博/X**。仅作可选增强。 |
| 巨潮资讯网公告检索（公开页面/接口，需限速） | A 股公司公告 | 小时–日 | 比社交媒体更「硬」。 |
| **公开行情快照**（见 §4.4） | 校准舆情是否已被价格兑现 | 交易时段接近实时；休市为收盘价 | 网页可见 ≠ 官方实时 API。默认无 key；限速、可降级。 |

中文「7×24 快讯」（财联社、东财、同花顺、金十、华尔街见闻）多数**没有稳定官方 RSS**。社区方案是打网页端公开 JSON 或自建 RSS 桥。这些接口会改、会限流、可能与站点条款冲突。第一期：

- **不作为阻塞依赖**；
- 若接入，必须限速、缓存、失败降级；
- 不登录、不绕 WAF、不维护 cookie。

### 4.2 建议配置（免费 key，显著提升质量）

| 源 | 要什么 | 怎么取 | 费用量级 |
| --- | --- | --- | --- |
| AI（必配，至少一个） | `OPENAI_API_KEY` 或 `ANTHROPIC_API_KEY` 或 `DEEPSEEK_API_KEY` 或 `GEMINI_API_KEY` | 对应控制台创建 key，放入 GitHub Actions Secret | 按 token；见 §6 |
| Finnhub | `FINNHUB_API_KEY` | [finnhub.io](https://finnhub.io/) 注册免费档 | 免费档有新闻/情绪类接口，适合美股 |
| NewsAPI 或 Marketaux | 对应 API key | [newsapi.org](https://newsapi.org/) / [marketaux.com](https://marketaux.com/) | 免费档约几十到 100 次/天，够「按需点击」 |
| Alpha Vantage News & Sentiment | `ALPHAVANTAGE_API_KEY` | [alphavantage.co](https://www.alphavantage.co/support/#api-key) | 免费档日调用很少，当备份 |

### 4.3 可选（社交，必须用户明确开通）

#### X（Twitter）

| 项 | 内容 |
| --- | --- |
| 要什么 | X Developer 账号；pay-per-use **credits**；App 的 Bearer Token 或 OAuth 2.0 凭证 |
| 怎么取 | 1. 打开 [developer.x.com](https://developer.x.com) / [console.x.com](https://console.x.com) 注册开发者  
2. 创建 Project + App  
3. 购买 credits，并设**消费上限**（强烈建议）  
4. 把 `X_BEARER_TOKEN` 配进 GitHub Secrets |
| 费用 | 新开发者已无免费读额度。第三方整理的标价约为 **读帖 $0.005/次**（以控制台为准）。规划器必须限制：每次任务最多拉 N 个博主 × M 条（建议默认 N≤10、M≤10，上限约 $0.5/次）。 |
| 用法 | **白名单博主**（用户维护一份 `config/x-kols.yml`），拉 `user timeline` + 关键词搜索。禁止全站扫描。 |
| 不做 | 非官方抓取、Nitter 镜像、cookie 登录。 |

#### 微博

| 项 | 内容 |
| --- | --- |
| 公开热搜（已接入） | 网页自己用的 JSON：`https://weibo.com/ajax/statuses/hot_band`（含 `category`、`onboard_time`），失败再试 `https://weibo.com/ajax/side/hotSearch`。浏览器 UA + Referer 即可，**不必登录**。`s.weibo.com/top/summary` 仍是登录墙，不用。没有单独的「财经榜」接口；从总榜按 `category=财经` 以及 A股/股市等市场词、侧重点关键词过滤。 |
| 时效 | 热搜是**当下快照**。每条尽量带上榜时间 `onboard_time`；超过 `weibo_max_age_hours`（默认 18 小时）的丢掉。拉取失败不沿用上一份报告里的旧榜。GitHub-hosted runner 在海外，可能 403/418，失败则 `dataCoverage.weibo=false`，任务继续。 |
| 用法 | 当作盘中情绪胶带，不与新闻分数混排；综合模型不得把热搜 URL 当新闻出处。娱乐话题不进报告。 |
| 博主时间线（仍未做） | 开放平台账号；已审核应用；OAuth2 `WEIBO_ACCESS_TOKEN` + `config/weibo-kols.yml`。官方个人应用通常能读指定用户公开微博；全网搜索/商业热搜往往要企业资质。 |
| 不做 | 手机号扫码会话、cookie 注入、验证码打码、多账号轮换、第三方热搜聚合站当权威源。 |

社交源未配置时的产品文案：

> 微博仅为公开热搜快照（非博主时间线），以上榜时间衡量时效。未接入 X。结论主要来自公开财经资讯、公告与行情快照。若要纳入指定博主前瞻，请按文档配置 token 和 KOL 名单。

### 4.4 市场行情（默认开启，用来校准）

**「公开」分两层，不要混为一谈：**

| 含义 | 是否成立 | 对本项目的影响 |
| --- | --- | --- |
| 任何人打开财经网站能看到报价 | **是** | 用报价校准舆情合理，也不依赖登录 |
| 交易所官方、可转售、无延迟的程序化 API 免费开放 | **否** | 我们不承诺 Level-1 官方实时，只用延迟或网页公开接口做研究校准 |
| 网页背后的 JSON（新浪 / 腾讯 / 东财等）无 key 可调 | **大体是**，但是未文档化契约 | 限速、失败换源、报告里标注数据源与时间戳；接口改版就降级 |

校准要回答的问题（比「再爬一堆价格」更重要）：

1. 舆情在热的板块，**今天/本周是不是已经涨了？**（热度已被计价 → 下调「前瞻」确定性）
2. 舆情很热但价格跌或横盘？**价讯背离**，在报告里单独标出。
3. 相对大盘：板块强于沪深300 / 标普，还是只是跟着 beta 走。
4. 规划出的几只代表标的：涨跌幅、成交是否放大（只取 Planner 给出的少量代码，不扫全市场）。

默认拉取的紧凑快照（一次任务大约几十个代码，成本可忽略）：

- **基准：** 上证综指、沪深300、创业板指、恒生、纳斯达克100 或标普500、VIX（若可得）、美元指数、COMEX 金、WTI 或布油（宏观背景）。
- **板块：** Planner 映射到的东财/申万行业或对应 ETF（例：存储 → 半导体/存储芯片板块指数或 SOXX/SMH）。
- **代表标的：** Planner 输出的 `tickers[]`，建议硬顶 15–20 只。
- **每个代码只留：** 现价、涨跌幅（日/5日/20日若一次请求拿得到）、成交额、相对基准超额、报价时间、是否延迟。

数据源优先级（失败自动换下一个）：

| 市场 | 默认（无 key） | 有免费 key 时更好 | 不做 |
| --- | --- | --- | --- |
| A 股 / 港股 | 腾讯 `qt.gtimg.cn`、新浪 `hq.sinajs.cn`、东财 push2/quote（网页公开 JSON） | 无必要 | 通达信 TCP 长连接、模拟交易终端登录 |
| 美股 / 全球指数 | Stooq 日线、Yahoo 公开图表接口（非正式、易变） | Finnhub quote、Alpha Vantage（日限额很小） | 付费 Polygon 实时 |
| 板块涨跌 | 东财行业/概念板块列表接口（限速） | — | 全市场选股器扫一遍 |

接入原则与新闻源相同：限速、缓存到「同一交易日同一小时」、单源失败不致命、**只把一张小表而不是 K 线全文送给 Synthesizer**。

---

## 5. AI 工作流（效用优先、成本受控）

一次点击对应一次 **Job**，固定四步，不允许「把爬到的原文全部塞进最贵模型」。

```text
① Planner（便宜模型）
    输入：用户侧重点 + 当前时刻 + 已启用信源
    输出：JSON
      - sectors[]          关注板块
      - keywords[]         检索词（中英）
      - tickers[]          相关代码（硬顶 15–20）
      - benchmarks[]       对照指数
      - sources[]          启用哪些适配器
      - lookbackHours      默认 24–48
      - maxItemsPerSource  硬顶

② Fetchers（无模型）
    并行：新闻 / 公告 / 行情快照 /（可选）社交
    限时、限条；失败记 warning

③ Distiller（规则 + 可选便宜模型）
    文本：去重、时效、相关度，Top K（40–80）条短摘要
    行情：压成一张表（现价、涨跌、相对基准、报价时间、是否延迟）
    规则打标：pricedIn | divergence | confirming | insufficientData

④ Synthesizer（中等模型，一次调用）
    只看 Distiller 文本 + 行情小表 + 规划 JSON
    产出结构化报告（见 §5.1）
    热度前瞻必须对照价格，禁止只根据标题喊「要涨」
```

模型分工建议（用户先配一个 key 时，用同一家的两个档；只有一个模型就把 Planner 提示压得很短）：

| 步骤 | 模型档 | 为什么 |
| --- | --- | --- |
| Planner | 便宜（GPT-4.1-mini / Haiku / DeepSeek-chat / Gemini Flash） | 输出短 JSON，贵模型浪费 |
| Distiller | 默认纯规则；条目 > 120 时才用便宜模型做一次分批打分 | 大部分过滤不需要 LLM |
| Synthesizer | 中等（4.1 / Sonnet / DeepSeek-reasoner 等） | 报告质量主要在这里，但输入已被压到几千 token |
| 不用 | 长上下文把原始网页全文灌进 Opus / o 系列 | 成本高、时延高、边际收益低 |

硬预算（写入配置，可调）：

- 单次 Job 输入 token 上限（建议 Synthesizer ≤ 8k tokens）。
- 单次 Job 费用软上限（建议默认 $0.05 AI + $0.50 社交；超了截断源而不是换更大模型）。
- 同侧重点 + 同 UTC 小时的结果缓存命中则直接返回，不重复花 token。
- 并发：同时只允许 1 个 Job，防止按钮连点烧钱。

### 5.1 报告结构（补全「以及」之后的产品定义）

```json
{
  "generatedAt": "ISO-8601",
  "focus": "存储相关",
  "timeWindow": { "from": "...", "to": "..." },
  "dataCoverage": {
    "news": true,
    "quotes": true,
    "filings": true,
    "x": false,
    "weibo": false
  },
  "marketSnapshot": {
    "asOf": "ISO-8601",
    "delayed": true,
    "source": "tencent|sina|eastmoney|finnhub",
    "benchmarks": [{ "symbol": "000300.SH", "name": "沪深300", "changePct": 0.0 }],
    "sectors": [{ "name": "半导体", "changePct": 0.0, "vsBenchmarkPct": 0.0 }],
    "tickers": [{ "symbol": "603986.SH", "name": "兆易创新", "changePct": 0.0, "changePct5d": 0.0 }]
  },
  "sectorOutlook": [
    {
      "sector": "存储芯片 / NAND",
      "heat": 1,
      "heatScore": 0.86,
      "priceAction": "up | down | mixed | flat | unknown",
      "calibration": "confirming | pricedIn | divergence | insufficientData",
      "direction": "up | down | mixed | unclear",
      "narrative": "一句话前瞻（必须提到价格是否已反应）",
      "evidence": [
        {
          "claim": "要点",
          "sourceTitle": "...",
          "url": "...",
          "publishedAt": "...",
          "weight": "primary | supporting"
        }
      ],
      "counterEvidence": [],
      "confidence": 0.0,
      "invalidatedIf": "何种新信息会推翻该判断"
    }
  ],
  "crossSectorNotes": "板块间传导（如存储涨价 → 手机/服务器）",
  "limitations": ["未接入微博/X", "仅过去 36 小时"],
  "stats": { "fetched": 0, "used": 0, "model": "...", "estCostUsd": 0 }
}
```

页面展示优先级：板块热度表（旁注行情校准标签）→ 大盘/板块快照 → 每块的前瞻与依据（可点开原文）→ 反向证据 → 覆盖缺口与时效。

---

## 6. 成本粗算（按需点击，不是常驻）

假设每天点 3 次、每次 lookback 36 小时、Synthesizer 输入约 6k tokens、输出 2k tokens。

| 项目 | 量级 | 说明 |
| --- | --- | --- |
| Planner | 可忽略 | 几百 token 的便宜模型 |
| Synthesizer | 视供应商，大约每次 $0.01–$0.08 | DeepSeek / Flash 会接近下限；Sonnet / GPT 中档会接近上限 |
| GitHub Actions | 每次 2–5 分钟 | 公开仓库通常可忽略 |
| RSS / 公开行情快照 | $0 | 每次几十个代码，可忽略 |
| Finnhub / NewsAPI 免费档 | $0（有日限额） | 按需点击不易打满 |
| X 读帖（若开启） | 100 条 ≈ $0.50 | **默认关闭**；打开必须有条数帽 |
| 微博官方 API | 平台限流为主 | 个人应用通常不按条计费，但额度小 |

成本控制的关键不是换更便宜的报告模型，而是 **永远不要把未过滤的社交/新闻原文送进综合模型**，以及 **社交默认关闭**。

---

## 7. 系统模块与仓库结构

```text
market_analysis/
  docs/
    feasibility-and-plan.md    ← 本文
    reports/                   ← 生成的 JSON/Markdown 报告（可被 Pages 读取）
  web/                         ← GitHub Pages 前端（Vite + 静态托管）
  src/
    planner/                   ← 侧重点 → 检索计划
    ingest/                    ← 各信源适配器（rss, quotes, newsapi, finnhub, x, weibo, reddit）
    distill/                   ← 去重、时效、相关度、截断
    synthesize/                ← 报告生成
    schema/                    ← 规划与报告的 JSON Schema
  config/
    sources.yml                ← 默认 RSS 列表、行情代码帽、超时、条数帽
    x-kols.yml                 ← 可选
    weibo-kols.yml             ← 可选
    budgets.yml                ← token / 社交条数 / 并发
  .github/workflows/
    analyze.yml                ← 手动或 API 触发的分析任务
    pages.yml                  ← 构建并发布前端
```

技术选型（第一期保持少依赖）：

- 前端：Vite + 原生 TS 或轻量 React；无后端框架。
- 分析运行时：Node 或 Python 均可。建议 **Python 3.12 + httpx**：RSS 解析、YAML、JSON Schema 更省事。
- 配置：YAML + 环境变量。密钥只走 GitHub Secrets。
- 测试：适配器用 fixture HTML/JSON，不打真实外网；Planner/Synthesizer 用 snapshot 测 schema。

---

## 8. 分阶段实施计划

每期都要能独立演示。不把微博/X 作为第一期验收项。

### 第 0 期 — 对齐（本文，已完成）

- 写清可行/不可行、密钥清单、架构、报告 schema。
- 不写爬虫、不接真实付费 API。

### 第 1 期 — 可点的前端 + 假报告

**目标**：Pages 上能填侧重点、点按钮（本地 mock 或读仓库内示例 JSON）、看到板块热度/依据/来源。

- Vite 单页：输入框、时间窗、开始按钮、报告视图、覆盖缺口提示。
- 用 `docs/reports/sample.json` 驱动 UI。
- GitHub Actions 发布 Pages。
- 页脚免责声明。

**验收**：打开 Pages，不配任何 key，能完整看完一份示例报告。

### 第 2 期 — 真实分析管线（公开源 + 行情校准 + 一个 AI Key）

**目标**：`workflow_dispatch` 填 `focus=存储相关`，几分钟后 Pages 上出现新报告，且热度带「确认 / 已计价 / 背离」标签。

- 实现 Planner → RSS/Google News/Finnhub（若有 key）+ **行情快照** → Distiller → Synthesizer。
- Secret：至少一个 `AI_API_KEY`（加 `AI_BASE_URL` 以便兼容 OpenAI 接口的中转/DeepSeek）。行情默认无 key。
- 预算与缓存（文本按小时，行情按交易日+小时）。
- 报告写入 `docs/reports/latest.json` 并带历史 `docs/reports/{timestamp}.json`。
- 失败时写出 `errors` 而不是空页面。行情失败时仍出新闻报告，并标 `quotes: false`。

**验收**：只配 AI Key，对「存储相关」跑一次；每条板块既有新闻出处，也有对应指数/标的涨跌（或明确写「行情未取到」）。

### 第 3 期 — Pages 上一键触发

**目标**：按钮真正开跑，而不是去 Actions 页面点。

- 增加 Cloudflare Worker 或 GitHub App 作为触发器（只允许本仓库、只允许本 Pages Origin、有简单口令或 GitHub OIDC）。
- 前端轮询 `latest.json` 的 `generatedAt`，或轮询 workflow run 状态。
- 防连点、展示「规划中 / 拉取中 / 生成中」。

**验收**：在 Pages 输入侧重点 → 等待 → 报告更新。密钥仍不出现在前端仓库。

### 第 4 期 — 社交插件

**目标**：配置了 token 和 KOL 名单后，报告 `dataCoverage.x/weibo=true`，博主前瞻进入证据链。

- X：白名单 timeline + 条数帽 + 费用日志。
- 微博：官方 API 拉指定 uid；搜不到就降级。
- 规划器根据「是否有社交凭证」决定是否把社交列入 sources。

**验收**：无 token 时行为与第 2 期相同；有 token 时证据中出现社交条目，且单次不超过预算。

### 第 5 期 — 质量与成本打磨

- 板块映射表（中英别名：存储 / memory / NAND / HBM / 国产替代）。
- 相关度阈值与「标题党」降权。
- 历史报告对比（热度变化，而不是每次从零讲故事）。
- 用量仪表：每次 Job 的 token、美元估算、源失败率。

---

## 9. 风险与对策

| 风险 | 对策 |
| --- | --- |
| 中文快讯接口改版/封 IP | 多源、限速、缓存；单源失败不致命 |
| 公开行情接口改版或延迟 | 腾讯/新浪/东财互为备份；报告标注 `delayed` 与 `asOf`；失败则只出新闻 |
| 把延迟行情当成交易所官方实时 | UI 写明「公开网页行情，可能延迟，仅供研究校准」 |
| X 标价上调 | 社交默认关；硬条数帽；消费上限设在 X 控制台 |
| 微博应用审核失败 | 长期以新闻+公告为主；不阻塞主路径 |
| 模型幻觉板块热度 | 强制证据 URL；无证据的板块不得进入 Top 列表；展示反向证据 |
| 公开仓库被他人刷分析 | 第 3 期触发器要口令/登录；Actions 仅 `workflow_dispatch` + 本仓库 |
| 把系统当成荐股工具 | UI 与报告明确「研究辅助 / 非投资建议」 |
| GitHub Actions 对部分国内站点超时 | 超时短、重试少、优先 RSS 与国际源 |

---

## 10. 用户需要准备的东西（按优先级）

**现在就可以做（第 2 期）：**

1. 一个 AI API Key（OpenAI / Anthropic / DeepSeek / Gemini 任一）。**不要提交到仓库，不要写进 Pages。** Key 放 GitHub Actions **Secrets**；Base URL 和模型名放 **Variables**（逐步说明见 §12）：
   - Secret：`AI_API_KEY`
   - Variable：`AI_BASE_URL`（选供应商的 **OpenAI / Chat Completions** 地址，见 §12.4）
   - Variable：`AI_MODEL_PLANNER`、`AI_MODEL_SYNTHESIZER`（见 §12.5）
2. 打开 GitHub Pages（后续 workflow 会用 `docs/` 或 `gh-pages`）。
3. 行情：**默认不用你再申请 key**。

**强烈建议（提升新闻覆盖与美股报价质量）：**

4. `FINNHUB_API_KEY`（新闻 + 更稳的全球报价）
5. `NEWSAPI_KEY` 或 `MARKETAUX_API_KEY`

**社交（第 4 期，按需）：**

6. X：Developer credits + `X_BEARER_TOKEN` + `config/x-kols.yml`  
7. 微博：开放平台应用 + `WEIBO_ACCESS_TOKEN` + `config/weibo-kols.yml`

**第 3 期一键触发（三选一）：**

8. Cloudflare 免费账号（Worker 里放 `GITHUB_TRIGGER_TOKEN`，权限仅 `actions:write`，**仍然不要放 AI Key**），或  
9. GitHub App，或  
10. 继续用 Actions 页面手动 Run（零额外账号）

不需要：云服务器、域名（Pages 默认 `https://<user>.github.io/market_analysis/` 即可）、代理池、微博/X 爬虫账号、行情付费终端。

---

## 11. 建议的下一步

若认可本文边界，下一份 PR 做 **第 1 期**：前端骨架 + 报告 schema（含行情校准字段）+ 示例 `sample.json` + Pages workflow。  
第 2 期接真实 AI、RSS 与公开行情。微博/X 等到密钥和 KOL 名单齐了再开适配器。

---

## 12. AI API Key 放哪里？（不是网站里）

**短答案：放在 GitHub 仓库的 Actions Secrets 里，加密存储。不放在 GitHub Pages 网站上，也不提交进 git。**

访客打开你的 Pages 页面时，**看不到**这个 Key。仓库如果是公开的，别人也**看不到** Secrets 的值。分析任务跑起来时，GitHub 才把 Key 注入到 Actions 进程，用来调用 AI；调用结束即丢弃。

```text
你（仓库管理员）
    │  在网页里粘贴一次 Key
    ▼
GitHub 加密 Secrets          ← 不进 git，不进 Pages，浏览器拿不到
    │  仅在 Actions 运行时注入环境变量
    ▼
分析脚本调用 AI 厂商 API     ← Key 出现在服务器内存，不写进报告 JSON
    ▼
报告 JSON / 前端页面         ← 只有分析结果，没有 Key
```

### 12.1 正确做法

1. 打开仓库：`https://github.com/ha0c9/market_analysis/settings/secrets/actions`  
   （Settings → Secrets and variables → Actions）
2. 点 **New repository secret**。
3. Name 填 `AI_API_KEY`，Value 贴你的 key，保存。
4. 需要时同样添加 `AI_BASE_URL`（OpenAI 格式的地址）、`AI_MODEL_PLANNER` 等。
5. 只有你（以及被授予 admin 的协作者）能**覆盖或删除** Secret；GitHub **不会再次显示**已保存的明文。

本地开发另建一份 **未提交** 的 `.env`（`.gitignore` 已忽略），不要把 `.env` 推上去。

### 12.2 绝对不要放的地方

| 位置 | 为什么不行 |
| --- | --- |
| `web/` 前端代码、`config.js`、`.env` 提交进仓库 | 公开仓库人人能 clone；Pages 源码对访客可见 |
| 浏览器 localStorage / 页面输入框长期保存 | 等于把扣款凭证交给每个打开网站的人（或 XSS） |
| 报告 JSON、README、Issue、PR 评论 | 进 git 历史后很难彻底抹掉 |
| Cloudflare Worker（若只做「触发分析」） | 触发器用 GitHub token 即可；AI Key 应留在 Actions |

### 12.3 谁能用到这笔钱

- **不能：** 打开 Pages 的路人、clone 公开仓库的人、fork 出来的 PR（fork 默认读不到你的 Secrets，这是 GitHub 的保护）。
- **能：** 你在本仓库点 Run workflow（或第 3 期那条受控触发器）。所以第 3 期必须给触发器加口令/登录，防止外人替你烧 AI 额度。

若 Key 曾经不小心提交过：去 AI 控制台**立刻作废并换新**，不要以为「再删掉文件」就安全（git 历史里还在）。

### 12.4 供应商让你选 Anthropic / OpenAI / Responses 时选哪个？

这三项不是三家公司，而是**同一种 HTTPS 调用的三种 JSON 协议**。本系统按 **OpenAI Chat Completions** 来接，兼容面最广（官方 OpenAI、DeepSeek、多数国内中转都走这条）。

| 控制台里的名字 | 实际协议 | 典型路径 | 本项目 |
| --- | --- | --- | --- |
| **OpenAI**（有的写成 Chat Completions / 对话补全） | Chat Completions | `/v1/chat/completions` | **选这个** |
| Responses | OpenAI 较新的 Agent 接口 | `/v1/responses` | 不要选。我们不做官方内置网页搜索那套 Agent |
| Anthropic | Claude 原生 Messages | `/v1/messages` | 不要选。请求头和 JSON 形状都不同 |

`AI_BASE_URL` 应填 **OpenAI 格式对应的 Base URL**，从控制台复制后原样粘贴。程序**不会**自动补 `/v1`。例如：

- OpenAI 官方：`https://api.openai.com/v1`
- DeepSeek：`https://api.deepseek.com` 或 `https://api.deepseek.com/v1`（以供应商文档为准）
- 中转/聚合：复制控制台里标注为 OpenAI 兼容的那一条

不要把 Anthropic 或 Responses 那条 URL 填进来，否则协议对不上。  
若控制台还让你选「模型」，那是给网页 Playground 用的；仓库 Secrets 里的模型名才用 `AI_MODEL_*`，与这里的协议选择无关。

#### 若供应商是 SSSAiCode / SSSAiAPI

后台三种格式请选 **OpenAI**。GitHub 这样填（名字是 `AI_` 开头，不是 `AL_`）：

| 位置 | Name | 第一版填什么 |
| --- | --- | --- |
| **Secret** | `AI_API_KEY` | 中转站密钥（`sk-` 开头） |
| **Variable** | `AI_BASE_URL` | `https://node-hk.sssaiapi.com/api/v1`（OpenAI 那条，带 `/v1`） |
| **Variable** | `AI_MODEL_PLANNER` | 后台模型 ID，例如 `deepseek-v4-flash` |
| **Variable** | `AI_MODEL_SYNTHESIZER` | 后台模型 ID，例如 `grok-4.6` |

`AI_API_KEY` 必须放 Secrets。Base URL 和模型名不是密钥：放 Variables 后日志不会被掩成 `***`。若同一段值还留在 Secrets 里，GitHub 仍会在整份日志里掩掉它，所以要从 Secrets **删掉**这三项。

核对方法：后台 OpenAI 格式的地址应能对上 `{你填的 BASE}/chat/completions`。若控制台复制出来的是整段 `.../chat/completions`，**删掉末尾的 `/chat/completions`**，其余原样保留。

模型 ID **必须和后台「模型」页显示的字符串完全一致**。若列表里不是 `deepseek-v4-flash`，以控制台为准整段复制。


### 12.5 `AI_MODEL_PLANNER` 和 `AI_MODEL_SYNTHESIZER` 怎么填？

名字是 **`AI_MODEL_PLANNER` / `AI_MODEL_SYNTHESIZER`**（`AI` 是 A + I），不是 `AL_MODEL_*`。建议放在 **Actions Variables**（不是 Secrets），这样 Analyze 日志里能看到实际模型名。值是模型 ID 字符串，不是 URL，也不是 API Key。

| Variable | 干什么 | 示例 |
| --- | --- | --- |
| `AI_MODEL_PLANNER` | 规划器：把「存储相关」变成关键词、板块、行情代码 | 后台列表里的 DeepSeek Flash ID |
| `AI_MODEL_SYNTHESIZER` | 写报告：只看压缩后的新闻+行情小表 | 后台列表里的 Grok ID |

不填时：规划器默认 `deepseek-v4-flash`；综合模型复用规划器，再没有则同样默认 `deepseek-v4-flash`。程序**不会**再 `GET /models` 自动挑 GPT。仍建议显式填上。若这三项还留在 Secrets 里，日志仍会被掩掉。

