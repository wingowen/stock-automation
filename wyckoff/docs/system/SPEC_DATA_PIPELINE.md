# Spec: 多源数据获取与交叉验证流水线

> **版本**: v1.0
> **日期**: 2026-07-19
> **背景**: 600519 合并数据发现 72 天静默缺口，需要建立强制交叉验证机制

---

## ASSUMPTIONS I'M MAKING

1. **数据频率**: 日线级别 (OHLCV)，前复权价格
2. **5 年范围**: 从当前日期向前推 5 年（例如 2026-07-19 → 2021-07-19 ~ 2026-07-19）
3. **数据源**: 双源验证 — **AkShare(东财) 为主拉取源** + **腾讯财经(缓存) 为校验源**。由于腾讯历史 K 线 API 域名解析失败，TencentSource 降级为只读缓存模式，仅用于已缓存数据的验证，不支持在线拉取。后续网络恢复后可升级为在线模式。
4. **批次策略**: 按季度分批拉取（每批约 63 个交易日），每批独立验证
5. **容差阈值**: 价格差 ≤0.01 元、量差 ≤1 手（来自审计结论，两源差异仅小数取舍）
6. **冲突仲裁**: 两源冲突时标记为待审核，拒绝自动合并
7. **代理问题**: 代码必须在 `requests.Session` 中设 `trust_env=False`，避免 macOS 系统代理静默失败
8. **现有代码兼容**: 新架构需兼容 [wyckoff/data/tencent_source.py](file:///Users/wingo.wen/Documents/WorkSpace/stock-automation/wyckoff/data/tencent_source.py) 的 `load_csv()` 接口

→ Correct me now or I'll proceed with these.

---

## Objective

构建一个**强制交叉验证的 A 股数据获取流水线**，确保：

1. **完整性**: 指定标的 5 年日线数据无缺日（日期连续性强制检查）
2. **准确性**: 每批次拉取后立即双源逐行验证，合并后再全量验证
3. **可追溯**: 每个批次都有独立审计报告，合并后生成总体审计报告
4. **可扩展**: 数据源抽象为统一接口，便于新增第三源

**用户故事**:
- 量化研究员说："给我拉 600519 过去 5 年数据"
- 系统自动从双源分批拉取，每批验证通过才合并
- 返回完整数据 + 审计报告，报告明确指出任何数据缺口或不一致

**成功标准**:
- 5 年数据完整覆盖（无缺日），双源验证通过
- 每批次验证耗时 ≤ 30 秒，全量验证 ≤ 2 分钟
- 验证失败时自动拒绝合并，输出清晰的错误定位

---

## Tech Stack

| 类别 | 工具 | 版本 | 用途 |
| --- | --- | --- | --- |
| 语言 | Python | 3.9+ | 核心逻辑 |
| 数据处理 | pandas | 2.x | DataFrame 操作 |
| 数据源 1 | AkShare | 1.17+ | 东财接口（第二源基准） |
| 数据源 2 | 腾讯财经 API | — | gtimg.cn（第一源） |
| 测试框架 | unittest | 内置 | 单元/集成测试 |
| 日志 | logging | 内置 | 审计追踪 |

---

## Commands

```bash
# 拉取并验证单个标的 (5年)
python -m wyckoff.data.pipeline fetch --code 600519 --years 5

# 仅验证已缓存数据
python -m wyckoff.data.pipeline validate --code 600519

# 生成审计报告
python -m wyckoff.data.pipeline audit --code 600519

# 运行所有测试
python -m pytest tests/test_pipeline.py -v

# 运行特定测试
python -m pytest tests/test_pipeline.py::TestBatchValidation -v
```

---

## Project Structure

```
wyckoff/
├── data/
│   ├── __init__.py
│   ├── baostock_source.py      # Baostock 实现（备用）
│   ├── tencent_source.py       # 腾讯财经实现（现有）
│   ├── akshare_source.py       # AkShare 实现（新增）
│   ├── base.py                 # DataSource ABC（新增）
│   ├── validator.py            # 验证器（新增）
│   ├── pipeline.py             # 主流水线（新增）
│   └── cache/                  # 缓存目录
│       ├── {code}_batch_001.csv    # 批次数据
│       ├── {code}_batch_001_audit.json  # 批次审计
│       └── {code}_full.csv     # 合并后全量数据
├── detectors/
├── schemas.py
└── __init__.py

tests/
├── test_pipeline.py            # 流水线测试（新增）
├── test_validator.py           # 验证器测试（新增）
└── test_spring.py              # Spring 检测器测试（现有）
```

---

## Code Style

```python
from abc import ABC, abstractmethod
from datetime import date, timedelta
from typing import Optional
import pandas as pd


class DataSource(ABC):
    """数据源抽象基类（所有数据源必须实现）"""
    
    @abstractmethod
    def fetch(
        self,
        code: str,
        start_date: date,
        end_date: date,
        adjust: str = "qfq"
    ) -> pd.DataFrame:
        """拉取指定日期范围的数据
        
        Returns:
            DataFrame 必须包含列: date, code, open, high, low, close, volume
            date: datetime.date 类型
            OHLC: float
            volume: int（单位：手）
        """
        ...
    
    @abstractmethod
    def name(self) -> str:
        """返回数据源名称（用于日志和报告）"""
        ...


class BatchResult:
    """单批次拉取结果"""
    def __init__(self, source_name: str, df: pd.DataFrame, 
                 start_date: date, end_date: date):
        self.source_name = source_name
        self.df = df
        self.start_date = start_date
        self.end_date = end_date


class ValidationResult:
    """验证结果"""
    def __init__(self, passed: bool, discrepancies: list[dict]):
        self.passed = passed
        self.discrepancies = discrepancies
```

**命名规范**:
- 类名：`PascalCase`（如 `AkShareSource`）
- 函数名：`snake_case`（如 `fetch_daily`）
- 文件名：`snake_case`（如 `akshare_source.py`）
- 常量：`UPPER_CASE`（如 `PRICE_TOLERANCE = 0.01`）

---

## Testing Strategy

### 测试框架
- **unittest**（Python 内置，无需额外依赖）
- 测试文件放在 `tests/` 目录

### 测试分层

| 层级 | 覆盖内容 | 文件名 |
| --- | --- | --- |
| 单元测试 | DataSource 单源拉取、验证规则 | `test_validator.py` |
| 集成测试 | 批次验证、合并验证、流水线完整流程 | `test_pipeline.py` |
| 端到端 | 真实拉取 600519 某批次并验证 | `test_pipeline.py::TestE2E` |

### 覆盖率要求
- 核心验证逻辑覆盖率 ≥ 90%
- 流水线主流程覆盖率 ≥ 80%

### 测试用例设计

1. **单源拉取**: 拉取已知日期范围，验证返回列完整性和日期格式
2. **批次验证**: 两源返回相同数据，验证 passed=True；两源有差异，验证 passed=False
3. **日期连续性**: 故意制造缺日，验证被检测出来
4. **边界测试**: 价格差刚好等于容差、量差刚好等于容差、边界日期
5. **冲突仲裁**: 两源价格差异超过阈值，验证被标记为待审核

---

## Boundaries

### Always
- 每批次拉取后必须验证日期连续性（无缺日），否则拒绝合并
- 两源 OHLC 差异超过 0.01 元、量差异超过 1 手时，标记为冲突并拒绝自动合并
- 代码中必须处理 macOS 系统代理（`requests.Session.trust_env = False`）
- 所有验证结果必须写入审计文件，不可仅在内存中检查

### Ask First
- 修改容差阈值（PRICE_TOLERANCE、VOLUME_TOLERANCE）
- 添加新数据源（需确认数据源可靠性和 API 稳定性）
- 改变冲突仲裁策略（当前为"标记待审核"，如需改为"以多数源为准"需确认）
- 修改批次划分策略（当前为季度，如需改为月度/半年需确认）

### Never
- 跳过验证直接合并数据
- 在验证失败时自动降级为单源数据
- 忽略日期连续性检查
- 提交包含数据缺口的合并文件

---

## Success Criteria

### 功能层面
- [ ] `python -m wyckoff.data.pipeline fetch --code 600519 --years 5` 成功拉取 5 年数据
- [ ] 每批次验证自动运行，验证失败时拒绝合并并输出错误定位
- [ ] 合并后自动生成全量审计报告（日期范围、行数、验证结果、缺口列表）
- [ ] 支持单独运行验证（`validate` 命令）和生成报告（`audit` 命令）

### 质量层面
- [ ] 测试全部通过（`python -m pytest tests/test_pipeline.py -v`）
- [ ] 代码符合命名规范和类型提示要求
- [ ] 日志清晰可追溯（每批次拉取、验证、合并步骤都有日志）

### 性能层面
- [ ] 单批次验证 ≤ 30 秒
- [ ] 全量 5 年数据验证 ≤ 2 分钟

---

## Open Questions（已关闭）

> 以下问题已在 Plan 阶段（[plan.md](../tasks/plan.md)）做出技术决策。如需重新讨论，请提 Issue。

| # | 问题 | 决策 | 依据 |
|---|------|------|------|
| 1 | 5 年日期范围 | **向前推 5 年**（如 2026-07-19 → 2021-07-19 ~ 2026-07-19） | CLI `--years 5` 设计，见 SPEC §Assumptions #2 |
| 2 | 第三源预留 | **预留接口但不强制实现**。DataSource ABC 已支持扩展，当前只实现 AkShare + Tencent | plan.md §7.5 数据源角色分工 |
| 3 | 冲突处理方式 | **不自动尝试第三源仲裁**。两源冲突时标记为待审核，拒绝合并，必须人工确认后才能继续 | plan.md §7.3 仲裁规则 |
| 4 | 缓存策略 | **已验证通过的批次永久缓存**，文件名包含批次编号，避免重复拉取 | plan.md §7.4 缓存策略 |
| 5 | 并发拉取 | **串行**，每批次之间加 1-2 秒延迟，避免触发 API 限流 | plan.md §三 风险表 |
| 6 | 输出格式 | **JSON + Markdown 两种格式都生成**，JSON 供程序消费，Markdown 供人工审阅 | plan.md §7.8 审计报告 |

---

## 附录：容差阈值与仲裁规则

### 容差阈值（基于 600519 审计结论）

| 字段 | 容差 | 说明 |
| --- | --- | --- |
| open/high/low/close | ≤ 0.01 元 | 两源小数取舍差异上限 |
| volume | ≤ 1 手 | 量能单位差异上限 |

### 仲裁规则

```
1. 两源差异 ≤ 容差 → 通过，取第一源数据作为最终结果
2. 两源差异 > 容差 → 标记为冲突，拒绝合并
3. 任一源缺失某日期 → 标记为缺口，拒绝合并
4. 冲突/缺口必须人工确认后才能继续
```

### 批次划分策略

按季度划分批次，每批次独立拉取和验证：
- 批次大小：约 3 个月（60-65 个交易日）
- 批次编号：`batch_001`, `batch_002`, ...
- 批次重叠：相邻批次重叠 1 天（确保连续性）

### 日期连续性检查

```python
# 预期交易日 = 起始日到结束日之间的所有交易日
expected_dates = get_trading_dates(start_date, end_date)
actual_dates = sorted(df["date"].unique())
missing_dates = expected_dates - set(actual_dates)

if missing_dates:
    raise DataGapError(f"Missing {len(missing_dates)} trading days: {missing_dates}")
```

---

> **评审请求**: 请审阅以上 SPEC，确认后我将进入 Phase 2（Plan）阶段，生成技术实现计划和任务清单。
