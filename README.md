# 智能市场舆情分析

按需触发的市场研究助手：填写侧重点 → GitHub Actions 拉公开新闻和行情 → AI（或规则回退）写出带出处的板块前瞻。前端部署在 GitHub Pages。

本系统是研究辅助，不是投资建议。密钥只放在 Actions Secrets，不进网页。

## 怎么用

1. 仓库 Settings → Secrets and variables → Actions 确认已有：
   - `AI_API_KEY`（SSSAiCode 的 `sk-` 密钥）
   - `AI_BASE_URL` = `https://node-hk.sssaicode.com/api/v1`（也可不填，代码默认就是这个）
2. Settings → Pages → Source 选 **GitHub Actions**。
3. 打开 [Analyze market](https://github.com/ha0c9/market_analysis/actions/workflows/analyze.yml) → **Run workflow**，侧重点例如 `存储相关`。
4. 跑完后打开 GitHub Pages（`https://ha0c9.github.io/market_analysis/`）查看报告。

页面上的「在 GitHub 启动分析」会跳到第 3 步那个 Actions 页。真正的一键调度（不离开 Pages）还没做，密钥仍然不能进浏览器。

## 本地跑

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 填 AI_API_KEY；不填则用规则规划/草稿报告
python -m src.analyze --focus "存储相关" --lookback-hours 36
bash scripts/build_site.sh
python -m http.server 8000 --directory _site
```

```bash
python -m unittest discover -s tests -v
```

## 当前进度

- [x] 第 0 期：需求、可行性、实施计划
- [x] 第 1 期：Pages 前端 + 示例报告
- [x] 第 2 期：公开源 + 行情校准 + AI 管线（无 Key 时规则回退）
- [ ] 第 3 期：Pages 一键触发（不跳转到 Actions）
- [ ] 第 4 期：微博 / X 官方 API 插件
- [ ] 第 5 期：质量与成本打磨

计划全文：[docs/feasibility-and-plan.md](docs/feasibility-and-plan.md)
