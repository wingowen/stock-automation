# 模型对比评估（model-eval）

独立于 `wyckoff-auto/` 的**模型对打测试框架**。给定一个 A 股标的范围，用可切换的
provider/model 跑现有的 5 轮威科夫分析（`wyckoff-auto/analyze_stock`），再用一套
**确定性评分 rubric** 对输出打分，输出可对比报告。以后换模型只需在配置里加一项，
无需改任何评估逻辑。

## 为什么独立又复用？

- **独立**：自己的目录、配置、CI、报告，不污染生产管线。
- **复用**：直接 `import wyckoff-auto.analyzer` 跑真实 5 轮分析（`dry_run=True` 不写生产简报），
  不复制分析逻辑，避免 DRY 腐化。`wyckoff-auto` 的路径全部绝对解析，跨目录 import 无 CWD 依赖。

## 目录结构

```
model-eval/
  eval_config.json     # 评估标的 + 对比模型 + 输出目录（改这里加模型）
  eval/
    rubric.py          # 5 轮必填字段结构 / 枚举 / 权重（对齐 prompts/*.txt）
    scorer.py          # 4 维度评分（纯函数，零依赖，可本地单测）
    runner.py          # 复用 analyze_stock 跑多模型
    report.py          # markdown 排行榜 + JSON
    cli.py             # 编排入口
  tests/
    test_scorer.py     # 评分器单测（无需网络）
  reports/             # 产出（markdown + json + raw），由 CI 提交
```

## 用法

```bash
# 本地仅跑评分器单测（不需要网络/API）
python -m pytest model-eval/tests

# CI / 真跑（需能访问对应 provider，推荐在 GitHub Actions 境外 runner 上跑）
python model-eval/eval/cli.py                 # 全部模型
python model-eval/eval/cli.py --models gemini-flash
python model-eval/eval/cli.py --trade-date 2026-08-01
```

真跑需要对应 provider 的 API Key 以环境变量存在（GitHub Secrets 注入）：
`GEMINI_API_KEY` / `AGNES_API_KEY` / `AGNES_BASE_URL` 等。runner 按 `provider` 自动选 env 名。

## 如何新增一个待对比模型

在 `eval_config.json` 的 `models` 里加一项：

```json
{
  "id": "my-model",
  "label": "My Model",
  "provider": "gemini",            // 或 "agnes"
  "model": "gemini-2.5-pro",
  "api_key_env": "MY_MODEL_KEY",   // 可选，默认 {PROVIDER}_API_KEY
  "base_url_env": "MY_MODEL_URL"   // 可选，默认 {PROVIDER}_BASE_URL
}
```

并在仓库 Secrets 配好对应的 key。重新跑 `cli.py` 即可加入对比。

## 评分 rubric（4 维度各 25%）

| 维度 | 含义 | 计算 |
|------|------|------|
| **结构合规 structural** | LLM 稳定性 / JSON 解析成功率 | 完成轮数 / 5 |
| **schema 完整度** | 必填字段存在且合法（类型/枚举/非空，含嵌套与列表） | 合法字段 / 必填字段总数 |
| **轮间一致性** | 跨轮逻辑自洽 | R4 `direction`==`prediction.direction`、R5 仓位枚举、R5 盈亏比可计算、R1/R3 枚举与布尔合法 等检查通过率 |
| **约束遵守** | 领域硬规则 | 主板禁推（扫描 688/30xxxx/8xxxxx）、关键价格为正、confidence 枚举合法 |

> 注：本评分是**确定性、可复现的底线质量**检查（结构正确 + 规则遵守）。
> 若要评估「分析深度的语义质量」，可后续接入 LLM-judge（作为第 5 维度，默认关闭），
> 当前版本聚焦规则层，避免引入 judge 模型的方差与成本。

## 如何据分切换默认模型

报告末尾给出「切换建议」（综合最优模型）。落地切换：把
`.github/workflows/wyckoff-auto.yml` 的 `LLM_PROVIDER` 与该模型的
`GEMINI_MODEL` / `AGNES_MODEL` 等 secret 对齐即可。`model-eval` 与 `wyckoff-auto`
共用同一套 provider 抽象（`wyckoff-auto/llm_client.py`），切换零代码改动。

## 报告存档

`reports/<date>.md` + `<date>.json` 由 CI 提交进仓库，形成**跨时间的可对比分数历史**，
便于观察某个模型升级/退化。原始 rounds 存于 `reports/raw/` 以供人工复核。
