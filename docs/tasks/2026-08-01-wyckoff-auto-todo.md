# 任务清单：威科夫自动化分析系统（wyckoff-auto）

> 规格: [spec.md](../specs/2026-08-01-wyckoff-auto-spec.md)
> 计划: [plan.md](../plans/2026-08-01-wyckoff-auto-plan.md)

## Phase 1: 基础设施

- [ ] Task 1: 创建 watchlist.json + config.py
  - Acceptance: watchlist.json 可被解析；config.py 提供路径约定和环境变量读取
  - Verify: `python -c "from wyckoff_auto.config import *; print(WATCHLIST_PATH)"`
  - Files: `wyckoff-auto/watchlist.json`, `wyckoff-auto/config.py`

- [ ] Task 2: 创建 llm_client.py
  - Acceptance: 封装 call_agnes() 支持多轮 messages 数组；重试机制；失败返回 None
  - Verify: mock 测试 + `python wyckoff-auto/llm_client.py --test`（需 AGNES_API_KEY）
  - Files: `wyckoff-auto/llm_client.py`, `wyckoff-auto/tests/test_llm_client.py`

## Phase 2: 数据层

- [ ] Task 3: 创建 context_builder.py
  - Acceptance: 能获取 K线数据、加载 skill 知识、读取历史简报；章节选择逻辑正确
  - Verify: `python -c "from wyckoff_auto.context_builder import build_context; ..."` 对 002279 构建完整上下文
  - Files: `wyckoff-auto/context_builder.py`, `wyckoff-auto/tests/test_context_builder.py`

## Phase 3: 分析引擎

- [ ] Task 4: 创建 5 个 prompt 模板
  - Acceptance: 每个模板定义清晰的输入期望和 JSON 输出 schema
  - Verify: 人工审阅模板内容
  - Files: `wyckoff-auto/prompts/round1_background.txt` ~ `round5_action.txt`

- [ ] Task 5: 创建 analyzer.py 主引擎
  - Acceptance: 5 轮 LLM 调用循环；对话历史累积；单只股票失败降级
  - Verify: `python wyckoff-auto/analyzer.py --code 002279 --dry-run`（需 AGNES_API_KEY）
  - Files: `wyckoff-auto/analyzer.py`, `wyckoff-auto/tests/test_analyzer.py`

## Phase 4: 输出层

- [ ] Task 6: 创建 brief_writer.py
  - Acceptance: 5 轮 JSON -> Markdown 简报；复用归档机制；brief_parser.py 可解析
  - Verify: 生成简报后运行 `python -c "from scanner.brief_parser import parse_all_briefs; ..."` 提取触发点
  - Files: `wyckoff-auto/brief_writer.py`, `wyckoff-auto/tests/test_brief_writer.py`

- [ ] Task 7: 创建 knowledge_updater.py
  - Acceptance: LLM 辅助更新 KNOWLEDGE.md；输出格式与现有结构一致
  - Verify: 生成更新后的 KNOWLEDGE.md 结构校验
  - Files: `wyckoff-auto/knowledge_updater.py`

## Phase 5: 集成

- [ ] Task 8: 修改 scanner 集成 watchlist
  - Acceptance: price_scanner.py 支持 --watchlist 参数；从 watchlist 过滤股票
  - Verify: `python -m scanner.price_scanner --watchlist wyckoff-auto/watchlist.json` 正常运行
  - Files: `scanner/price_scanner.py`, `scanner/config.py`

- [ ] Task 9: 创建 GitHub Actions workflow
  - Acceptance: workflow 可手动触发；运行 analyzer + 提交简报
  - Verify: GitHub Actions 手动触发成功
  - Files: `.github/workflows/wyckoff-auto.yml`
