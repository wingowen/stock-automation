# 威科夫交易系统 · SPEC（v1.0 已批准）

> ✅ **v1.0 已批准**（2026-07-19）— 全部 6 个 Q 确认默认。Phase A 完成。
> 对应 phase: **A**（[实施方案 §5](file:///Users/wingo.wen/Documents/WorkSpace/stock-automation/威科夫交易系统/实施方案.md)）
> 配套文档：[实施方案.md](file:///Users/wingo.wen/Documents/WorkSpace/stock-automation/威科夫交易系统/实施方案.md) · [威科夫操盘法-章节/](file:///Users/wingo.wen/Documents/WorkSpace/stock-automation/威科夫操盘法-章节/)

***

## 1. 一句话

把《威科夫操盘法》的 16+ 形态量化为可检测算法，每日扫描 A 股生成结构化信号，复用现有 `stockexpert-daily-brief` 看板展示，**最终落地为单人可执行的小仓位实盘策略**。

## 2. 解决什么问题

| 现在            | 目标                  |
| ------------- | ------------------- |
| 手工看日线图识别威科夫形态 | 自动检测 + 可视化          |
| 错过信号、识别主观     | 规则统一、可复盘            |
| LLM 直接判形态（幻觉） | Python 硬检测 + LLM 解释 |
| 没有回测、没有止损     | 历史验证 + 硬止损          |

## 3. 目标用户

`[ASSUMPTION]` **单人 = 你自己**

- 场景：每天 15:30 收盘后看 5-10 只候选股
- 时间：每天 30 分钟看信号
- 能力：会写 Python、能接受手工下单
- 资金：**\[ASSUMPTION]** 单账户 ≤ 50 万

## 4. 成功标准

**Phase G（模拟盘）完成时**：

- 检测器：16+ 形态全部实现 + 每个有 ≥ 3 个测试
- 信号频率：每只股每年 ≥ 2 次有效信号
- 回测指标（2018-2024，沪深 300）：胜率 ≥ 50%，盈亏比 ≥ 1.5，最大回撤 ≤ 15%，年化夏普 ≥ 1.0
- 模拟盘：连续 3 个月不亏损

**Phase H（实盘）6 个月**：

- 模拟盘 vs 实盘差距 ≤ 30%
- 最大回撤 ≤ 10%

## 5. 范围

### 5.1 v1 包含

- 16+ 威科夫形态检测（吸筹 + 派发 + 复合）
- 阶段机（A→E 状态机）
- 历史回测
- 看板集成
- 模拟盘跟踪
- 每日信号推送

### 5.2 v1 不包含

- ❌ 实时自动下单（Phase H 之前不接券商 API）
- ❌ 港股 / 美股
- ❌ 财务数据、基本面分析
- ❌ 多账户、多策略
- ❌ 分钟级 K 线（v1 仅日线）
- ❌ 机器学习/深度学习模型

## 6. 形态事件清单

> 详细定义见 [实施方案 §4](file:///Users/wingo.wen/Documents/WorkSpace/stock-automation/威科夫交易系统/实施方案.md)。共 16+ 形态：

**吸筹（Accumulation）**：PS、SC、AR、ST、Spring、Test、SOS、LPS、BU、JAC

**派发（Distribution）**：BC、UTAD、SOW、LPSY、Test

**复合（Composite）**：Shakeout、No-SC、Cause-Effect、Effort-Result

每个形态输出统一的 `Event` 对象（见 §7.2）。

## 7. 数据契约

### 7.1 输入（OHLCV）

| 字段                        | 类型    | 说明                  |
| ------------------------- | ----- | ------------------- |
| date                      | date  | 交易日                 |
| code                      | str   | 6 位股票代码（如 "600519"） |
| name                      | str   | 股票名称                |
| open / high / low / close | float | 复权价（前复权）            |
| volume                    | float | 成交量（手）              |
| amount                    | float | 成交额（元）              |

- 频率：`[ASSUMPTION]` **日线**
- 复权：`[ASSUMPTION]` **前复权**
- 来源：[Baostock](http://baostock.com/baostock/index.php/A%E8%82%A1K%E7%BA%BF%E6%95%B0%E6%8D%AE)
- 范围：`[ASSUMPTION]` 沪深 300 成分股（300 只），2026-08 后扩展到中证 500

### 7.2 输出（Event）

```json
{
  "date": "2026-07-18",
  "code": "600519",
  "name": "贵州茅台",
  "phase": "C",                    // 阶段机状态: A/B/C/D/E
  "events": ["Spring", "Test"],    // 同时检测到的形态
  "strength": 0.78,                // 0-1，检测器自评
  "rvol": 1.65,                    // 相对量能 = 当日量 / 20日均量
  "price": 1680.5,
  "context": {                     // 检测时的上下文
    "range_high": 1720.0,
    "range_low": 1640.0,
    "range_days": 18
  }
}
```

存储：`wyckoff/data/YYYY-MM/DD/events.json`

## 8. 技术栈

| 层     | 选型                          | 理由              | 文档                                               |
| ----- | --------------------------- | --------------- | ------------------------------------------------ |
| 数据    | **Baostock**                | 免费、免注册、A 股全量    | [官方](http://baostock.com/)                       |
| 回测    | **Backtrader**              | 事件驱动、社区大        | [GitHub](https://github.com/mementum/backtrader) |
| 检测    | **pandas + numpy**          | 向量化快、熟悉度高       | [pandas](https://pandas.pydata.org/)             |
| AI 解释 | **Agnes AI**                | 已有、零成本          | 现有项目使用                                           |
| 看板    | **stockexpert-daily-brief** | 已有 GitHub Pages | 复用                                               |
| 部署    | **GitHub Actions**          | 已有、每日 16:30     | 复用                                               |
| 测试    | **pytest**                  | Python 标准       | [官方](https://docs.pytest.org/)                   |

**回测框架对比结论**：Backtrader 适合事件驱动；VectorBT 仅做参数扫描辅助。

## 9. 架构

```
GitHub Actions (每日 16:30)
   ├─ 1. baostock_daily.py          拉取日线
   ├─ 2. wyckoff_detect.py          跑检测器（Python 硬规则）
   ├─ 3. phase_machine.py           阶段机 + 冲突仲裁
   ├─ 4. agnes_explain.py           LLM 解释（不检测）
   ├─ 5. write_kanban.py            合并到看板 JSON
   └─ 6. deploy-pages               推 GitHub Pages
```

**核心原则**（来自 smartchart 经验）：

- ❌ **永远不**让 LLM 直接做形态检测
- ✅ Python 硬规则先检测，LLM 只做叙事和解释

## 10. 约束（A 股特殊性）

1. **T+1**：当日买入次日才可卖
2. **涨跌停**：主板 ±10%，创业板/科创板 ±20%，ST 股 ±5%
3. **停牌**：回测中遇到停牌 K 线要跳过，不算信号
4. **退市**：从成分股中剔除历史数据
5. **ST/PT**：可选剔除，避免高风险

> 回测必须显式建模上述约束，否则理论盈利会被高估 30%+。

## 11. 风控铁律

| 规则     | 阈值                    | 来源         |
| ------ | --------------------- | ---------- |
| 单笔最大风险 | 总资金 **1%**（实盘起步 0.5%） | 实施方案 §7.4  |
| 同时持仓   | ≤ **5 只**             | 同上         |
| 止损     | 跌破入场 K 低点即出           | 同上         |
| 月度回撤   | 达 **10%** 强制停手 1 周    | 同上         |
| 调参     | 任何参数调整必须重测            | 实施方案 §11.2 |

## 12. 阶段路线图

> 详见 [实施方案 §5](file:///Users/wingo.wen/Documents/WorkSpace/stock-automation/威科夫交易系统/实施方案.md)。

| 阶段        | 目标             | 时长    |
| --------- | -------------- | ----- |
| A 写规格     | **本文件**        | 1-2 天 |
| B 第一个 TDD | Spring 检测器闭环   | 1-2 天 |
| C 完整检测器   | 16+ detector   | 1-2 周 |
| D 阶段机     | FSM + 冲突仲裁     | 3-5 天 |
| E 回测      | 沪深 300 5 年     | 1 周   |
| F 看板集成    | 接入 stockexpert | 1 周   |
| G 模拟盘     | 30+ 笔模拟        | 4-8 周 |
| H 极小仓位    | 0.5% 起步        | 3-6 月 |

***

## 13. Commands

```bash
# 运行所有测试
python -m pytest tests/ -v

# 运行指定检测器测试
python -m pytest tests/test_spring.py -v

# 运行 Spring 检测器可视化
python scripts/visualize_600519_spring.py

# 数据流水线：拉取并验证 5 年数据
python -m wyckoff.data.pipeline fetch --code 600519 --years 5

# 数据流水线：仅验证已缓存数据
python -m wyckoff.data.pipeline validate --code 600519

# 数据流水线：生成审计报告
python -m wyckoff.data.pipeline audit --code 600519

# 运行数据流水线测试
python -m pytest tests/test_pipeline.py -v
```

---

## 14. Code Style

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class Event:
    """统一事件输出"""
    date: date
    code: str
    name: str
    phase: str
    events: list[str]
    strength: float
    rvol: float
    price: float
    context: dict


class SpringDetector:
    """检测器类 — PascalCase"""

    def detect(self, df: pd.DataFrame) -> list[Event]:
        """检测方法 — snake_case"""
        ...
```

**命名规范**:
- 类名：`PascalCase`（如 `SpringDetector`, `AkShareSource`）
- 函数/方法：`snake_case`（如 `detect_spring`, `load_csv`）
- 文件名：`snake_case`（如 `spring.py`, `tencent_source.py`）
- 常量：`UPPER_CASE`（如 `PRICE_TOLERANCE = 0.01`）
- 类型提示：必须使用（所有函数签名含类型注解）
- 文档字符串：仅类和方法使用 docstring，逻辑行不写注释

---

## 15. Testing Strategy

### 框架
- **pytest**（Python 生态标准）
- 测试文件放在 `tests/` 目录，与 `wyckoff/` 模块结构对应

### 测试分层

| 层级 | 覆盖内容 | 示例 |
| --- | --- | --- |
| 单元测试 | 单个检测器逻辑、验证规则 | `test_spring.py` |
| 集成测试 | 流水线流程、多组件协作 | `test_pipeline.py` |
| 端到端 | 真实数据拉取和验证 | `test_pipeline.py::TestE2E` |

### 测试设计原则
- **TDD**: 先写测试，再写实现（已完成 Phase B Spring 检测器 TDD 闭环）
- **每个检测器 ≥ 3 个测试用例**：正常检测、边界情况、无信号
- **数据源测试**: 使用 Mock 模拟 API 返回，避免依赖外部接口
- **覆盖率目标**: 核心检测逻辑 ≥ 90%，流水线 ≥ 80%

---

## 16. Boundaries

### Always
- 形态检测必须用 Python 硬规则，**绝不**让 LLM 直接检测形态
- 每批次数据拉取后必须做交叉验证，验证通过才能合并
- 所有代码必须包含类型注解
- 修改检测器逻辑后必须运行对应测试
- 数据源必须处理 macOS 系统代理问题（`requests.Session.trust_env = False`）

### Ask First
- 数据库/数据契约 schema 变更
- 添加新的数据源（需验证 API 稳定性和可靠性）
- 修改容差阈值（PRICE_TOLERANCE、VOLUME_TOLERANCE）
- 改变冲突仲裁策略
- 添加新的 Python 依赖
- 修改阶段机状态转换规则

### Never
- 让 LLM 直接输出形态检测结果作为交易信号
- 跳过验证直接合并数据
- 提交包含数据缺口的合并文件
- 在实盘环境使用未经回测的策略
- 提交 secrets 或凭证到版本控制

---

## 17. ✅ 6 个 Q 的确认结果（已批准）

> 用户确认：**全同意默认**（2026-07-19）

| # | 问题 | 决定 | 影响 |
| --- | --- | --- | --- |
| Q1 | 目标用户 | **单人** | v1 不需要多账户/权限 |
| Q2 | 覆盖市场 | **仅 A 股** | v1 不开发港美股数据接入 |
| Q3 | 实时数据源 | **Baostock** | 日线回测免费、免注册 |
| Q4 | 模拟→实盘切换 | **连续 3 个月不亏损** | Phase H 起步时间不早于 2027-Q1 |
| Q5 | 最大可承受回撤 | **模拟 15% / 实盘 10%** | Phase E 退出标准 |
| Q6 | 实盘下单方式 | **人工执行** | v1 不接券商 API |

***

## 18. 不在 v1 范围但未来考虑

- 分钟级 K 线验证
- 多策略组合（威科夫 + 趋势 + 套利）
- Web 端交互式回放
- 微信/Telegram 信号推送（替代看板）
- 多账户支持
- 美股/港股扩展

***

## 19. 评审记录

| 日期         | 评审人      | 状态         | 备注               |
| ---------- | -------- | ---------- | ---------------- |
| 2026-07-19 | AI v0 草稿 | ✅ 已批准      | 第 17 节 6 个 Q 全部同意默认；Phase A 完成 |

***

*完成 Phase A 的标志：你 review 完本文档（重点看第 17 节），并对 6 个 Q 给出答案。*
