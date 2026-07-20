# Tasks: 多源数据获取与交叉验证流水线

> **版本**: v1.0
> **日期**: 2026-07-19
> **关联 Plan**: [plan.md](plan.md)

---

## 任务列表

### Task 1: 实现 DataSource ABC 抽象基类

- [ ] **Task**: 创建 `wyckoff/data/base.py`，定义统一数据源接口
- **Acceptance**: 
  - `DataSource` 抽象基类包含 `fetch()` 和 `name()` 抽象方法
  - `fetch()` 方法签名：`(code: str, start_date: date, end_date: date, adjust: str = "qfq") -> pd.DataFrame`
  - 返回 DataFrame 必须包含列: `date, code, open, high, low, close, volume`
  - 包含 `validate_response()` 方法验证返回数据格式
  - 定义自定义异常 `DataFormatError`, `DataGapError`, `FetchError`
- **Verify**: 
  - 文件存在且可导入：`python -c "from wyckoff.data.base import DataSource"`
  - 测试通过：`python -m pytest tests/test_base.py -v`
- **Files**: 
  - `wyckoff/data/base.py` (新建)
  - `tests/test_base.py` (新建)

---

### Task 2: 实现 AkShareSource 数据源

- [ ] **Task**: 创建 `wyckoff/data/akshare_source.py`，实现 AkShare 数据源
- **Acceptance**: 
  - 继承 `DataSource` ABC，实现 `fetch()` 和 `name()` 方法
  - `fetch()` 返回前复权日线数据（OHLCV）
  - 处理 macOS 系统代理问题（`requests.Session.trust_env = False`）
  - 实现重试机制（最多 3 次）
  - 返回 DataFrame 列名统一为：`date, code, open, high, low, close, volume`
- **Verify**: 
  - 拉取 600519 某批次数据成功：`python -c "from wyckoff.data.akshare_source import AkShareSource; src = AkShareSource(); df = src.fetch('600519', date(2024,1,1), date(2024,1,31)); print(df.head())"`
  - 返回列完整：`date, code, open, high, low, close, volume`
  - 日期类型为 `datetime.date`
- **Files**: 
  - `wyckoff/data/akshare_source.py` (新建)

---

### Task 3: 重构 TencentSource 数据源（只读缓存模式）

- [ ] **Task**: 重构 `wyckoff/data/tencent_source.py`，使其符合 DataSource 接口
- **Acceptance**: 
  - 新增 `TencentSource` 类，继承 `DataSource` ABC
  - `fetch()` 方法：**仅支持从缓存加载**，不支持在线拉取（因腾讯 API 域名解析失败）
  - 当请求日期范围超出缓存范围时，抛出 `CacheMissError`
  - 实现 `name()` 方法返回 "Tencent"
  - **保持原有 `load_csv()` 函数不变**（兼容性要求）
  - 返回 DataFrame 列名统一为：`date, code, open, high, low, close, volume`
- **Verify**: 
  - 原有 `load_csv()` 接口兼容：`python -c "from wyckoff.data.tencent_source import load_csv; df = load_csv('600519'); print(df.head())"`
  - 新接口可用：`python -c "from wyckoff.data.tencent_source import TencentSource; src = TencentSource(); print(src.name())"`
- **Files**: 
  - `wyckoff/data/tencent_source.py` (修改)

---

### Task 4: 实现 BatchValidator 验证器

- [ ] **Task**: 创建 `wyckoff/data/validator.py`，实现批次验证逻辑
- **Acceptance**: 
  - `BatchValidator` 类接受多个 `DataSource` 实例
  - `validate_batch()` 方法执行完整验证流程：
    1. 从所有源拉取批次数据（TencentSource 可能抛出 CacheMissError）
    2. 检查日期连续性（使用 A 股交易日历，**一次性拉取并缓存**）
    3. 逐行对比所有源数据
    4. 返回 `ValidationResult`（包含 passed 和 discrepancies）
  - 容差阈值：价格 ≤0.01 元、量 ≤1 手
  - `MergeValidator` 类实现全量合并后的验证
  - `TradingDates` 工具类：使用 `ak.tool_trade_date_hist_sina()` 获取交易日历，缓存有效期 30 天
  - 批次重叠去重：合并时保留后一批次的数据
- **Verify**: 
  - 单元测试通过：`python -m pytest tests/test_validator.py -v`
  - 真实数据验证：使用 600519 某批次测试验证逻辑
- **Files**: 
  - `wyckoff/data/validator.py` (新建)
  - `tests/test_validator.py` (新建)

---

### Task 5: 实现主流水线 pipeline.py

- [ ] **Task**: 创建 `wyckoff/data/pipeline.py`，实现完整流水线和 CLI
- **Acceptance**: 
  - `DataPipeline` 类包含核心流程：
    - `fetch(code, years)`: 拉取并验证指定标的的 N 年数据
    - `validate(code)`: 验证已缓存数据
    - `audit(code)`: 生成审计报告
  - CLI 支持三个子命令：`fetch`, `validate`, `audit`
  - 按季度划分批次，每批独立验证
  - 验证通过后合并数据，生成全量 CSV 和审计报告（JSON + Markdown）
  - 验证失败时拒绝合并，输出详细错误信息
- **Verify**: 
  - CLI 帮助信息正确：`python -m wyckoff.data.pipeline --help`
  - 完整流程测试：`python -m wyckoff.data.pipeline fetch --code 600519 --years 1`
  - 生成的全量数据和审计报告文件存在
- **Files**: 
  - `wyckoff/data/pipeline.py` (新建)

---

### Task 6: 实现端到端测试

- [ ] **Task**: 创建 `tests/test_pipeline.py`，实现流水线端到端测试
- **Acceptance**: 
  - `TestBatchValidation`: 测试批次验证逻辑（通过/失败场景）
  - `TestDateContinuity`: 测试日期连续性检查（缺日检测）
  - `TestMergeValidation`: 测试全量合并验证
  - `TestE2E`: 真实拉取 600519 某批次并验证（使用 AkShare 双源）
- **Verify**: 
  - 所有测试通过：`python -m pytest tests/test_pipeline.py -v`
  - E2E 测试成功验证真实数据
- **Files**: 
  - `tests/test_pipeline.py` (新建)

---

### Task 7: 补全 600519 数据缺口

- [ ] **Task**: 使用新流水线补全 600519 的 72 天数据缺口
- **Acceptance**: 
  - 成功拉取 2021-06-02 ~ 2021-09-09 的 71 天数据
  - 成功拉取 2022-06-30 的 1 天数据
  - 双源验证通过（无冲突、无缺口）
  - 合并后全量数据包含完整的 2020-07-01 ~ 2022-08-15 区间
- **Verify**: 
  - 合并后数据行数：518 行（与 AkShare 一致）
  - 审计报告显示无缺口、无冲突
  - Spring 检测器在完整数据上运行正常
- **Files**: 
  - `data/cache/600519_full.csv` (更新)
  - `data/cache/600519_full_audit.md` (新建)

---

## 任务依赖图

```
Task 1: base.py
     │
     ├──→ Task 2: akshare_source.py
     │
     └──→ Task 3: tencent_source.py (重构)
             │
             ├──→ Task 4: validator.py
             │       │
             │       └──→ Task 5: pipeline.py
             │               │
             │               └──→ Task 6: test_pipeline.py
             │                       │
             │                       └──→ Task 7: 补全 600519 缺口
```

---

## 验证检查点汇总

| 检查点 | 任务完成后 | 验证命令 |
| --- | --- | --- |
| DataSource 接口 | Task 1 | `python -m pytest tests/test_base.py -v` |
| AkShare 拉取 | Task 2 | 手动测试单源拉取 |
| Tencent 重构 | Task 3 | `python -c "from wyckoff.data.tencent_source import load_csv, TencentSource"` |
| 验证器逻辑 | Task 4 | `python -m pytest tests/test_validator.py -v` |
| 流水线 CLI | Task 5 | `python -m wyckoff.data.pipeline --help` |
| 端到端测试 | Task 6 | `python -m pytest tests/test_pipeline.py -v` |
| 数据补全 | Task 7 | 检查合并后数据行数和审计报告 |

---

> **评审请求**: 请审阅以上任务清单，确认后我将开始按顺序执行任务。
