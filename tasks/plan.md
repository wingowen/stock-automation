# Plan: 多源数据获取与交叉验证流水线

> **版本**: v1.0
> **日期**: 2026-07-19
> **关联 SPEC**: [SPEC_DATA_PIPELINE.md](../威科夫交易系统/SPEC_DATA_PIPELINE.md)

---

## 一、核心组件与依赖关系

```
┌─────────────────────────────────────────────────────────────┐
│                     pipeline.py (主入口)                     │
│                       ┌─────────────────┐                   │
│                       │ CLI 命令解析      │                   │
│                       └────────┬────────┘                   │
│                                │                           │
│          ┌─────────────────────┼─────────────────────┐      │
│          ▼                     ▼                     ▼      │
│   ┌───────────┐      ┌───────────────┐      ┌─────────────┐ │
│   │ fetch 命令 │      │ validate 命令 │      │ audit 命令  │ │
│   └─────┬─────┘      └───────┬───────┘      └──────┬──────┘ │
│         │                    │                     │         │
└─────────┼────────────────────┼─────────────────────┼─────────┘
          │                    │                     │
          ▼                    ▼                     ▼
┌───────────────────┐  ┌───────────────┐  ┌─────────────────┐
│ 批次拉取引擎      │  │ MergeValidator│  │ 审计报告生成器   │
│ (BatchFetcher)    │  │ (全量验证)     │  │ (AuditReporter) │
└─────────┬─────────┘  └───────┬───────┘  └────────┬────────┘
          │                    │                     │
          ▼                    ▼                     │
┌───────────────────────────────────────┐            │
│        BatchValidator                 │            │
│    (每批次双源交叉验证)                │            │
└─────────────────┬─────────────────────┘            │
                  │                                  │
        ┌─────────┴─────────┐                        │
        ▼                   ▼                        │
┌─────────────┐     ┌───────────────┐                │
│ DataSource  │     │ 日期连续性检查 │                │
│   ABC 基类  │     │ (TradingDates)│                │
└──────┬──────┘     └───────┬───────┘                │
       │                    │                        │
  ┌────┴────┐               │                        │
  ▼         ▼               │                        │
┌───────────────┐   ┌───────┴───────┐               │
│AkShareSource  │   │交易日期计算    │               │
│TencentSource  │   │(A股交易日历)  │               │
└───────────────┘   └───────────────┘               │
                                                    │
                                                    ▼
                                        ┌───────────────────┐
                                        │   cache/ (存储)    │
                                        │ - batch CSV        │
                                        │ - batch audit JSON │
                                        │ - full CSV         │
                                        │ - full audit MD    │
                                        └───────────────────┘
```

---

## 二、实现顺序

| 阶段 | 组件 | 依赖 | 理由 |
| --- | --- | --- | --- |
| **Phase 1** | `base.py` (DataSource ABC) | 无 | 定义统一接口，后续所有数据源都依赖它 |
| **Phase 2** | `akshare_source.py` | base.py | AkShare 是基准源，先确保能稳定拉取 |
| **Phase 3** | `tencent_source.py` (重构) | base.py | 重构为符合 DataSource 接口，兼容现有功能 |
| **Phase 4** | `validator.py` (验证器) | base.py | 核心验证逻辑，依赖 DataSource 返回格式 |
| **Phase 5** | `pipeline.py` (主流水线) | base.py, validator.py | 整合所有组件，提供 CLI 命令 |
| **Phase 6** | 测试文件 | 以上全部 | TDD 闭环，确保每个组件都有测试覆盖 |

---

## 三、风险与缓解策略

| 风险 | 影响 | 缓解策略 |
| --- | --- | --- |
| AkShare 接口不稳定 | 拉取失败 | 实现重试机制（最多 3 次），失败时记录详细日志 |
| 腾讯 API 限流 | 批次拉取被拒 | 每批次之间加 1-2 秒延迟，避免触发限流 |
| macOS 代理问题 | 静默失败 | 在所有 requests.Session 中强制 `trust_env=False` |
| 日期连续性误报 | 误判为缺口 | 使用 A 股交易日历（排除周末和节假日），而非简单日期范围 |
| 大数据量内存压力 | 5 年数据 ~2500 行 | 按批次处理，每批约 60 行，避免一次性加载全量 |
| 容差阈值争议 | 验证结果不一致 | 容差作为配置参数，可通过 CLI 覆盖 |

---

## 四、并行 vs 串行

```
串行（必须按顺序）:
  base.py → akshare_source.py → tencent_source.py → validator.py → pipeline.py

并行（可同时进行）:
  测试文件编写（与对应组件开发同步）
  文档完善（README、使用说明）
```

---

## 五、验证检查点

| 检查点 | 位置 | 验证方式 |
| --- | --- | --- |
| DataSource 接口定义完成 | Phase 1 结束 | `python -m pytest tests/test_base.py -v` |
| AkShareSource 拉取成功 | Phase 2 结束 | 拉取 600519 某批次，验证返回列完整性 |
| TencentSource 重构完成 | Phase 3 结束 | `python -m pytest tests/test_tencent.py -v` + 原有 `load_csv()` 接口兼容 |
| BatchValidator 通过 | Phase 4 结束 | 运行 600519 某批次双源验证，确认通过/失败逻辑正确 |
| 流水线完整流程 | Phase 5 结束 | `python -m wyckoff.data.pipeline fetch --code 600519 --years 1`（先跑 1 年测试） |
| 全部测试通过 | Phase 6 结束 | `python -m pytest tests/ -v` |

---

## 六、资源估算

| 任务 | 预计时间 | 涉及文件 |
| --- | --- | --- |
| base.py | 30 分钟 | `wyckoff/data/base.py` |
| akshare_source.py | 1 小时 | `wyckoff/data/akshare_source.py` |
| tencent_source.py 重构 | 1 小时 | `wyckoff/data/tencent_source.py` |
| validator.py | 2 小时 | `wyckoff/data/validator.py` |
| pipeline.py | 2 小时 | `wyckoff/data/pipeline.py` |
| 测试文件 | 2 小时 | `tests/test_base.py`, `tests/test_validator.py`, `tests/test_pipeline.py` |
| 合计 | ~9 小时 | 约 10 个文件 |

---

## 七、技术决策记录

### 7.1 DataSource 接口设计

**决策**: 使用 ABC 抽象基类，强制所有数据源实现统一接口

**理由**:
- 便于新增数据源（如 Baostock），只需实现 `fetch()` 和 `name()` 方法
- 验证器可以对任意数据源组合进行验证，无需关心具体实现
- 符合"开闭原则"：对扩展开放，对修改关闭

### 7.2 批次划分

**决策**: 按季度划分，每批约 3 个月

**理由**:
- 季度约 60-65 个交易日，数据量适中
- 每批验证独立，失败时只需重跑该批次
- 相邻批次重叠 1 天，确保合并后日期连续

### 7.3 日期连续性检查

**决策**: 使用 A 股交易日历计算预期交易日

**理由**:
- A 股有周末、节假日、调休等复杂情况
- 简单日期范围无法正确判断是否缺日
- 使用 `akshare.trade_date_hist_em()` 获取交易日历

### 7.4 缓存策略

**决策**: 已验证通过的批次永久缓存，文件名包含批次编号

**理由**:
- 避免重复拉取相同数据，提高效率
- 批次文件独立，便于排查问题
- 缓存文件格式统一（CSV），便于手动检查

### 7.5 数据源角色分工

**决策**: AkShare 作为主拉取源，Tencent 作为只读校验源

**理由**:
- 腾讯历史 K 线 API（web.ifzq.gtimg.cn）域名解析失败，无法在线拉取
- AkShare（东财接口）稳定可用
- TencentSource 保留缓存验证能力，用于与已有数据做交叉验证
- 后续网络恢复后，可升级 TencentSource 为在线模式

**数据流**:
```
AkShareSource (主拉取) ──→ 批次数据 ──→ 验证
                                              │
TencentSource (只读缓存) ─────────────────────┘
```

### 7.6 交易日历

**决策**: 使用 `ak.tool_trade_date_hist_sina()` 获取 A 股交易日历，一次性拉取并缓存

**理由**:
- 避免验证器对数据源的循环依赖
- 交易日历变化缓慢，缓存有效期可设为 30 天
- Sina 接口比 AkShare 其他接口更稳定

### 7.7 批次重叠去重

**决策**: 合并时保留后一批次的数据（后一批次更新更完整）

**理由**:
- 相邻批次重叠 1 天（确保连续性）
- 后一批次的重叠日数据更完整（包含更多后续数据用于复权计算）
- 简单可靠，无需复杂的平均值计算

### 7.8 审计报告

**理由**:
- JSON 便于程序消费（如 CI 集成）
- Markdown 便于人工审阅和版本控制
- 两种格式内容一致，互相补充

---

## 八、关键代码片段（设计稿）

### 8.1 DataSource ABC

```python
# wyckoff/data/base.py
class DataSource(ABC):
    @abstractmethod
    def fetch(self, code: str, start_date: date, end_date: date, 
              adjust: str = "qfq") -> pd.DataFrame: ...
    
    @abstractmethod
    def name(self) -> str: ...
    
    def validate_response(self, df: pd.DataFrame) -> None:
        """验证返回数据格式是否符合契约"""
        required_cols = ["date", "code", "open", "high", "low", "close", "volume"]
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            raise DataFormatError(f"Missing columns: {missing}")
```

### 8.2 BatchValidator

```python
# wyckoff/data/validator.py
class BatchValidator:
    PRICE_TOLERANCE = 0.01
    VOLUME_TOLERANCE = 1  # 单位：手（1 手 = 100 股）
    
    def __init__(self, sources: list[DataSource]):
        self.sources = sources
    
    def validate_batch(self, code: str, start_date: date, end_date: date) -> ValidationResult:
        # 1. 从所有源拉取数据
        results = [src.fetch(code, start_date, end_date) for src in self.sources]
        # 2. 检查日期连续性
        self._check_date_continuity(results[0], start_date, end_date)
        # 3. 逐行对比所有源
        discrepancies = self._compare_sources(results)
        # 4. 返回验证结果
        return ValidationResult(passed=not discrepancies, discrepancies=discrepancies)
```

### 8.3 Pipeline CLI

```python
# wyckoff/data/pipeline.py
def main():
    parser = argparse.ArgumentParser(description="多源数据获取与验证流水线")
    subparsers = parser.add_subparsers(dest="command")
    
    # fetch 命令
    fetch_parser = subparsers.add_parser("fetch")
    fetch_parser.add_argument("--code", required=True)
    fetch_parser.add_argument("--years", type=int, default=5)
    
    # validate 命令
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--code", required=True)
    
    # audit 命令
    audit_parser = subparsers.add_parser("audit")
    audit_parser.add_argument("--code", required=True)
    
    args = parser.parse_args()
    
    if args.command == "fetch":
        pipeline = DataPipeline()
        pipeline.fetch(args.code, years=args.years)
    elif args.command == "validate":
        pipeline = DataPipeline()
        pipeline.validate(args.code)
    elif args.command == "audit":
        pipeline = DataPipeline()
        pipeline.audit(args.code)
```

---

> **评审请求**: 请审阅以上实现计划，确认后我将进入 Phase 3（Tasks）阶段，生成具体任务清单。
