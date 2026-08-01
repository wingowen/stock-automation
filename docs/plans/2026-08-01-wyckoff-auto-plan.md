# 实施计划：威科夫自动化分析系统（wyckoff-auto）

> 规格: [2026-08-01-wyckoff-auto-spec.md](../specs/2026-08-01-wyckoff-auto-spec.md)

## 组件依赖图

```
                    watchlist.json
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
     analyzer.py    scanner(修改)   brief_writer.py
          │                             ▲
          ├── context_builder.py ───────┤
          │    ├── fetch_kline.py (子进程)
          │    ├── wyckoff-trading/ (读MD)
          │    └── workflow.py (历史简报)
          │
          ├── llm_client.py (5轮调用)
          │
          └── knowledge_updater.py
```

## 实施顺序

### Phase 1: 基础设施（无外部依赖，可独立验证）

1. **watchlist.json + config.py**
   - 创建观察名单文件和配置模块
   - 验证：JSON 解析、路径约定正确

2. **llm_client.py**
   - 从 web_brief.py 提取 call_agnes()，封装为可复用模块
   - 支持多轮对话（messages 数组累积）
   - 验证：mock 测试 + 真实 API 单轮调用

### Phase 2: 数据层（依赖 Phase 1）

3. **context_builder.py**
   - K线数据获取：子进程调用 fetch_kline.py，解析 stdout
   - Skill 知识加载：读取 wyckoff-trading/ 下的 Markdown 文件
   - 章节选择逻辑：根据 Round 1 背景判断结果选择加载章节
   - 历史简报获取：复用 workflow.py 的归档和读取逻辑
   - 验证：单只股票的上下文构建完整

### Phase 3: 分析引擎（依赖 Phase 1 + 2）

4. **prompts/ 模板文件**
   - 5 个 prompt 模板，每个对应看盘五步法的一步
   - 定义每轮的 JSON 输出 schema

5. **analyzer.py 主引擎**
   - 5 轮调用循环
   - 对话历史管理
   - 单只股票失败降级
   - 验证：--dry-run --code XXX 单只完整运行

### Phase 4: 输出层（依赖 Phase 3）

6. **brief_writer.py**
   - 5 轮 JSON -> Markdown 简报映射
   - 复用 workflow.py 的归档机制
   - 验证：生成的简报被 brief_parser.py 正确解析

7. **knowledge_updater.py**
   - LLM 辅助的知识库更新
   - 验证：输出格式与 KNOWLEDGE.md 现有结构一致

### Phase 5: 集成（依赖 Phase 4）

8. **scanner 集成**
   - price_scanner.py 增加 --watchlist 参数
   - 从 watchlist 过滤要扫描的简报
   - 验证：scanner 使用 watchlist 正常运行

9. **GitHub Actions workflow**
   - wyckoff-auto.yml
   - 验证：手动触发成功

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| 5 轮 API 调用超时/限流 | 每轮独立重试，失败轮标注"不可用"，后续轮次可继续 |
| Token 超限（skill 知识 + K线 + 历史简报） | Round 2-5 压缩 K线数据为摘要；选择性加载章节而非全量 |
| LLM 输出 JSON 格式不稳定 | response_format: json_object + 解析失败重试 + 降级标注 |
| 简报格式不兼容 scanner 解析 | brief_writer 严格遵循模板格式，集成测试验证 brief_parser 解析 |
| 多只股票串行调用太慢 | 先串行实现（简单可靠），后续可并行化 |

## 并行机会

- Phase 1 的 task 1 和 task 2 可并行
- Phase 4 的 task 6 和 task 7 可并行
- Phase 5 的 task 8 和 task 9 可并行
