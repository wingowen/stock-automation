# StockExpert 每日看板自动化（stockexpert-daily-brief）

把 StockExpert 的「轻量看板模式」搬到 GitHub Actions：每天收盘后自动抓取公开 Web 财经信息，
调用 **Agnes AI**（OpenAI 兼容、免费）做综合研判，生成符合 `dashboard-contract` 的 `看板.json`，
并部署到 **GitHub Pages** —— 你每天打开一个网页就是最新 A股简报，零成本、不依赖任何本地运行。

## 架构

```
GitHub Actions (每个交易日 16:30 北京时间 / UTC 08:35, 周一~周五)
   ├─ web_brief.py
   │    ├─ 抓公开 Web（东财指数/板块接口）         → Web 信息汇聚
   │    ├─ 调 Agnes AI（AGNES_API_KEY 等 secret）  → 综合研判（右侧交易体系）
   │    └─ 生成 dashboard/data/YYYY-MM/DD/看板.json + manifest.json
   ├─ git commit 看板数据
   └─ 部署 dashboard/ 到 GitHub Pages
```

网页（`dashboard/index.html`）纯静态、自带 `manifest.json`，无需后端。

## 目录

```
stockexpert-daily-brief/
├── web_brief.py              # 主脚本：Web 汇聚 + Agnes 研判 → 看板.json + manifest.json
├── requirements.txt
├── .gitignore
├── dashboard/
│   ├── index.html            # 看板前端（日期列表 + 当天详情）
│   ├── manifest.json         # 由脚本生成，前端枚举用
│   ├── .nojekyll
│   └── data/YYYY-MM/YYYY-MM-DD/看板.json
└── .github/workflows/daily-brief.yml
```

## 本地运行

```bash
cd stockexpert-daily-brief
export AGNES_API_KEY="你的 key"
export AGNES_BASE_URL="https://api.agnes-ai.com/v1"   # 以官网为准
export AGNES_MODEL="agnes-text"                        # 以官网模型目录为准
python web_brief.py --dry-run        # 仅打印，不落盘
python web_brief.py                   # 生成当天看板
python web_brief.py --trade-date 2026-07-16
```

本地预览网页：

```bash
cd dashboard && python -m http.server 8000
# 打开 http://127.0.0.1:8000/
```

## 在 GitHub 上启用

1. **Secrets**：仓库 `Settings → Secrets and variables → Actions → New repository secret`：
   - `AGNES_API_KEY`：你的 Agnes AI Key（必填）
   - `AGNES_BASE_URL`：网关地址（如 `https://api.agnes-ai.com/v1`）
   - `AGNES_MODEL`：文本模型名（如 `agnes-text`）
2. **Pages**：`Settings → Pages → Build and deployment → Source: GitHub Actions`。
3. Workflow 默认每个交易日 UTC 08:35 自动跑；也可在 Actions 页手动 `Run workflow`。

## 约束（沿用 StockExpert 原则）

- 所有判断字段带 `source` + `reasoning`；行情数字缺失填 `null`，绝不编造。
- 不做打板、不追涨停；中军只选「未涨停、好位置、趋势不破」的容量票。
- 仅供研究参考，不构成投资建议。

## 备注

- `web_brief.py` 只用 Python 标准库（urllib/json），无第三方依赖。
- 交易日过滤为简化版（周末+内置节假日集合），生产可接交易所日历 API。
- Agnes 调用失败时自动写入「数据不可用」降级看板，不中断流水线。
