# 智能市场舆情分析

按需触发的市场研究助手：在 GitHub Pages 上填写分析侧重点，由 AI 规划取数范围，聚合公开财经信息与**当前行情快照**（可选微博 / X 官方 API），生成带出处、并对照价格校准过的板块热度前瞻。

**没有自有服务器。** 前端用 GitHub Pages；分析跑在 GitHub Actions。

**AI API Key 不放在网站里。** 放到仓库 Settings → Secrets and variables → Actions，访客和 git 都看不到。说明见 [计划文档 §12](docs/feasibility-and-plan.md#12-ai-api-key-放哪里不是网站里)。

## 可行性（摘要）

| 能力 | 结论 |
| --- | --- |
| Pages 前端 + 点按钮出报告 | 可行 |
| 无 VPS 的后端 | 可行（Actions；可选 Cloudflare 做一键触发） |
| 公开财经资讯 + **公开行情校准** | 可行，作为第一期主路径。网页报价公开可见，但不是交易所官方免费实时 API |
| 免费、免登录的微博 / X 全量舆情 | **不可行** |
| 配置官方 token 后的白名单博主 | 有条件可行（X 按次计费，必须设条数帽） |

完整论证、密钥清单、架构与分阶段计划见 **[docs/feasibility-and-plan.md](docs/feasibility-and-plan.md)**。

本系统是研究辅助，不是投资建议。

## 当前进度

- [x] 第 0 期：需求、可行性、实施计划
- [ ] 第 1 期：Pages 前端 + 示例报告
- [ ] 第 2 期：公开源 + 行情校准 + 一个 AI Key 的真实分析管线
- [ ] 第 3 期：Pages 一键触发
- [ ] 第 4 期：微博 / X 官方 API 插件
- [ ] 第 5 期：质量与成本打磨

## 你需要先准备的（第 2 期才会用到）

1. 至少一个 AI API Key → **GitHub Actions Secret `AI_API_KEY`**，不是网页配置项
2. `AI_BASE_URL`：SSSAiCode 选 **OpenAI** 格式，填 `https://node-hk.sssaicode.com/api/v1`（不要用 Anthropic 那条不带 `/v1` 的）
3. `AI_MODEL_PLANNER` / `AI_MODEL_SYNTHESIZER`：**不必填**（规划用便宜模型、写报告用好一点的模型；空着则共用默认模型）
4. 行情：默认不用再申请 key
5. （可选）Finnhub、NewsAPI 等免费新闻 Key
6. （可选）X Bearer Token + 博主名单；微博开放平台 token + uid 名单

获取方式、行情校准说明、密钥逐步配置见 [docs/feasibility-and-plan.md](docs/feasibility-and-plan.md) 第 4、10、12 节。
