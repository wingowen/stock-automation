# 威科夫交易系统 (Wyckoff Trading System)

> **基于《威科夫操盘法》的 A 股量化交易系统** - 形态识别、策略回测与实盘跟踪

[![License](https://img.shields.io/badge/license-个人学习使用-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-brightgreen.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-pytest-green.svg)](https://docs.pytest.org/)
[![Coverage](https://img.shields.io/badge/coverage-≥90%25-brightgreen.svg)](https://pytest-cov.readthedocs.io/)

---

## 📋 目录

- [系统概述](#系统概述)
- [核心特性](#核心特性)
- [项目结构](#项目结构)
- [快速开始](#快速开始)
- [开发进度](#开发进度)
- [测试指南](#测试指南)
- [数据流水线](#数据流水线)
- [形态检测器](#形态检测器)
- [回测系统](#回测系统)
- [开发指南](#开发指南)
- [相关文档](#相关文档)
- [风险提示](#风险提示)

---

## 系统概述

本项目旨在将《威科夫操盘法》中的经典交易策略系统化、量化化，构建一个**从形态识别到实盘跟踪的完整交易系统**。

### 设计理念

- **Python 硬检测 + LLM 解释层**: 永不让 LLM 直接检测形态，避免幻觉
- **TDD 开发流程**: 测试驱动开发，确保核心逻辑可靠性
- **多源数据验证**: AkShare + 腾讯财经双源交叉验证，拒绝合并冲突数据
- **分阶段实施**: Phase A → H，从规格编写到极小仓位实盘

### 目标用户

- 场景：每天 15:30 收盘后看 5-10 只候选股
- 时间：每天 30 分钟看信号
- 能力：会写 Python、能接受手工下单
- 资金：单账户 ≤ 50 万（实盘起步 0.5% 风险）

---

## 核心特性

### 形态识别

- ✅ **16+ 威科夫形态检测器**
  - 吸筹形态：PS、SC、AR、ST、Spring、Test、SOS、LPS、BU、JAC
  - 派发形态：BC、UTAD、SOW、LPSY、Test
  - 复合形态：Shakeout、No-SC、Cause-Effect、Effort-Result
- ✅ **阶段机**：A → B → C → D → E 状态机
- ✅ **信号强度自评**：0-1 分值，包含上下文信息

### 数据系统

- ✅ **多源数据获取**：AkShare（主）+ 腾讯财经（备用）
- ✅ **强制交叉验证**：价格容差 ≤0.01 元，量容差 ≤1 手
- ✅ **日期连续性检查**：使用 A 股交易日历，拒绝缺口数据
- ✅ **审计报告生成**：JSON + Markdown 双格式

### 回测与跟踪

- ✅ **历史回测**：Backtrader 事件驱动框架
- ✅ **模拟盘跟踪**：30+ 笔模拟交易验证
- ✅ **看板集成**：接入现有 `stockexpert-daily-brief` 看板

### 质量保障

- ✅ **TDD 开发**：每个检测器 ≥ 3 个测试用例
- ✅ **覆盖率目标**：核心逻辑 ≥ 90%，流水线 ≥ 80%
- ✅ **符合规范**：遵循 spec-driven-development 流程

---

## 项目结构

威科夫交易系统涉及多个目录，形成完整的开发体系：

```
stock-automation/
├── wyckoff/                               # 核心代码（Python 包）
│   ├── data/                              # 数据获取与验证
│   │   ├── base.py                        # 数据源抽象基类
│   │   ├── akshare_source.py              # AkShare 实现
│   │   ├── tencent_source.py              # 腾讯财经实现
│   │   ├── pipeline.py                    # 数据流水线主程序
│   │   └── validator.py                   # 双源交叉验证
│   ├── detectors/                         # 形态检测器
│   │   └── spring.py                      # Spring 形态检测器
│   ├── schemas.py                         # 数据结构定义
│   └── __init__.py
│
├── 威科夫交易系统/                         # 系统文档（本目录）
│   ├── README.md                          # 模块说明（本文件）
│   ├── SPEC.md                            # 系统技术规范
│   ├── SPEC_DATA_PIPELINE.md              # 数据流水线规范
│   └── 实施方案.md                         # 分阶段实施方案
│
├── tests/                                 # 测试文件（威科夫系统）
│   ├── test_base.py                       # 数据源接口测试
│   ├── test_pipeline.py                   # 流水线集成测试
│   ├── test_validator.py                  # 验证器测试
│   ├── test_spring.py                     # Spring 检测器测试
│   └── helpers.py                         # 测试辅助函数
│
├── scripts/                               # 脚本工具（威科夫系统）
│   ├── fetch_5year_data.py                # 数据拉取脚本
│   └── visualize_600519_spring.py         # Spring 可视化脚本
│
└── data/                                  # 数据缓存（全局）
    └── cache/
        ├── .gitkeep                       # 保持目录结构
        └── 600519_full_audit.md           # 数据审计报告
```

---

## 快速开始

### 环境准备

```bash
# 克隆项目
git clone https://github.com/your-username/stock-automation.git
cd stock-automation

# 安装依赖
pip install -e .

# 验证安装
python -c "from wyckoff.detectors.spring import detect_spring; print('OK')"
```

### 运行测试

```bash
# 运行所有测试
python -m pytest tests/ -v

# 运行指定测试
python -m pytest tests/test_spring.py -v
python -m pytest tests/test_pipeline.py -v

# 生成覆盖率报告
python -m pytest tests/ --cov=wyckoff --cov-report=html
```

### 数据流水线

```bash
# 拉取并验证 5 年数据
python -m wyckoff.data.pipeline fetch --code 600519 --years 5

# 仅验证已缓存数据
python -m wyckoff.data.pipeline validate --code 600519

# 生成审计报告
python -m wyckoff.data.pipeline audit --code 600519
```

### 形态检测示例

```python
from wyckoff.detectors.spring import detect_spring
from wyckoff.data.tencent_source import load_600519

# 加载历史数据
df = load_600519()

# 检测 Spring 形态
events = detect_spring(df)

# 输出结果
for event in events:
    print(f"{event.date}: Spring detected, strength={event.strength:.2f}")
```

---

## 开发进度

### 已完成阶段

| 阶段 | 目标 | 状态 | 完成日期 |
| --- | --- | --- | --- |
| **Phase A** | 规格编写 | ✅ 已完成 | 2026-07-19 |
| **Phase B** | Spring 检测器 TDD 闭环 | ✅ 已完成 | 2026-07-19 |
| **Phase C** | 完整检测器（16+） | 🚧 开发中 | - |
| **Phase D** | 阶段机（FSM + 冲突仲裁） | ⏳ 待开始 | - |
| **Phase E** | 历史回测（沪深 300，5 年） | ⏳ 待开始 | - |
| **Phase F** | 看板集成 | ⏳ 待开始 | - |
| **Phase G** | 模拟盘跟踪（30+ 笔） | ⏳ 待开始 | - |
| **Phase H** | 极小仓位实盘（0.5% 起步） | ⏳ 待开始 | - |

### Phase B 成果

- ✅ Spring 检测器实现（`wyckoff/detectors/spring.py`）
- ✅ 单元测试通过（`tests/test_spring.py`）
- ✅ 可视化脚本（`scripts/visualize_600519_spring.py`）
- ✅ 数据流水线完整实现（7/7 任务完成）

---

## 测试指南

### 测试结构

```
tests/
├── test_base.py                # DataSource ABC 测试
├── test_pipeline.py            # 流水线集成测试
│   ├── TestBatchValidation     # 批次验证测试
│   ├── TestDateContinuity      # 日期连续性测试
│   ├── TestMergeValidation     # 合并验证测试
│   └── TestE2E                 # 端到端测试
├── test_validator.py           # 验证器测试
└── test_spring.py              # Spring 检测器测试
```

### 测试设计原则

1. **TDD 开发**: 先写测试，再写实现
2. **每个检测器 ≥ 3 个测试用例**: 正常检测、边界情况、无信号
3. **数据源测试**: 使用 Mock 模拟 API 返回，避免依赖外部接口
4. **覆盖率目标**: 核心检测逻辑 ≥ 90%，流水线 ≥ 80%

### 运行测试

```bash
# 运行所有测试
python -m pytest tests/ -v

# 运行指定测试
python -m pytest tests/test_spring.py -v

# 生成覆盖率报告
python -m pytest tests/ --cov=wyckoff --cov-report=html

# 打开覆盖率报告
open htmlcov/index.html
```

---

## 数据流水线

### 核心特性

- **多源数据获取**: AkShare（主）+ 腾讯财经（备用）
- **强制交叉验证**: 价格 ≤0.01 元、量 ≤1 手
- **日期连续性检查**: 使用 A 股交易日历
- **审计报告生成**: JSON + Markdown 双格式

### 数据契约

```python
# 输入（OHLCV）
{
    "date": date,
    "code": str,
    "name": str,
    "open": float,  # 前复权
    "high": float,
    "low": float,
    "close": float,
    "volume": float,  # 单位：手
    "amount": float
}

# 输出（Event）
{
    "date": "2026-07-18",
    "code": "600519",
    "name": "贵州茅台",
    "phase": "C",
    "events": ["Spring", "Test"],
    "strength": 0.78,
    "rvol": 1.65,
    "price": 1680.5,
    "context": {...}
}
```

### 使用示例

```bash
# 拉取 5 年数据
python -m wyckoff.data.pipeline fetch --code 600519 --years 5

# 输出：
# - data/cache/600519_full.csv（合并后全量数据）
# - data/cache/600519_full_audit.md（审计报告）
# - data/cache/600519_batch_*.csv（批次数据）
```

**详细文档**: [SPEC_DATA_PIPELINE.md](SPEC_DATA_PIPELINE.md)

---

## 形态检测器

### 已实现检测器

#### Spring 检测器 (`wyckoff/detectors/spring.py`)

**定义**: 价格在支撑位附近短暂跌破后快速回升，显示买方承接力强

**检测逻辑**:
1. 识别支撑位（近期低点）
2. 检测价格跌破支撑位后快速回升
3. 评估相对量能（rvol）和强度
4. 输出 Event 对象

**使用示例**:
```python
from wyckoff.detectors.spring import detect_spring

events = detect_spring(df)
for event in events:
    print(f"{event.date}: Spring, strength={event.strength:.2f}")
```

**可视化**:
```bash
python scripts/visualize_600519_spring.py
# 输出：600519_spring_signals.png
```

### 待实现检测器

- ⏳ **PS (Preliminary Support)** - 初步支撑
- ⏳ **SC (Selling Climax)** - 卖出高潮
- ⏳ **AR (Automatic Rally)** - 自动反弹
- ⏳ **ST (Secondary Test)** - 二次测试
- ⏳ **SOS (Sign of Strength)** - 力量信号
- ⏳ **LPS (Last Point Support)** - 最后支撑点
- ⏳ ... 等 10+ 形态

---

## 回测系统

### 回测框架

- **Backtrader**: 事件驱动，适合复杂策略
- **VectorBT**: 参数扫描辅助工具

### 回测指标目标（Phase E）

- ✅ 胜率 ≥ 50%
- ✅ 盈亏比 ≥ 1.5
- ✅ 最大回撤 ≤ 15%
- ✅ 年化夏普 ≥ 1.0

### A 股特殊约束

1. **T+1**: 当日买入次日才可卖
2. **涨跌停**: 主板 ±10%，创业板/科创板 ±20%，ST 股 ±5%
3. **停牌**: 回测中跳过停牌 K 线
4. **退市**: 从成分股中剔除历史数据

---

## 开发指南

### 代码风格

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

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
- 类名：`PascalCase`（如 `SpringDetector`）
- 函数/方法：`snake_case`（如 `detect_spring`）
- 文件名：`snake_case`（如 `spring.py`）
- 常量：`UPPER_CASE`（如 `PRICE_TOLERANCE = 0.01`）

### TDD 开发流程

1. **写测试**：先写测试用例（`tests/test_xxx.py`）
2. **写实现**：实现最小代码通过测试
3. **重构**：优化代码质量
4. **验证**：确保测试全部通过

### 提交规范

- feat: 新功能（如 `feat: 实现 PS 检测器`）
- fix: 修复 Bug
- test: 测试相关
- docs: 文档更新
- refactor: 重构

---

## 相关文档

### 技术规范

- **系统规范**: [SPEC.md](SPEC.md)
- **数据流水线**: [SPEC_DATA_PIPELINE.md](SPEC_DATA_PIPELINE.md)
- **实施方案**: [实施方案.md](实施方案.md)

### 任务管理

- **实现计划**: [../tasks/plan.md](../tasks/plan.md)
- **任务清单**: [../tasks/todo.md](../tasks/todo.md)

### 外部资源

- **威科夫操盘法书籍**: `威科夫操盘法-章节/`（被 .gitignore 忽略）
- **参考项目**: [YoungCan-Wang/WyckoffTradingAgent](https://github.com/YoungCan-Wang/WyckoffTradingAgent)

---

## 风险提示

> ⚠️ **重要提示**: 本系统仅供研究和学习使用，不构成投资建议。
>
> - **模拟盘验证**: Phase G 必须完成 30+ 笔模拟交易
> - **极小仓位起步**: Phase H 实盘从 0.5% 风险起步
> - **严格风控**: 单笔最大风险 1%，同时持仓 ≤ 5 只
> - **止损铁律**: 跌破入场 K 低点即出
> - **月度回撤**: 达 10% 强制停手 1 周

---

## 许可证

本项目仅供个人学习和研究使用，未经授权不得用于商业用途。

---

## 致谢

- **经典理论**: 《威科夫操盘法》
- **数据源**: AkShare、腾讯财经、Baostock
- **回测框架**: Backtrader、VectorBT
- **参考项目**: YoungCan-Wang/WyckoffTradingAgent、cyrainfall/cyx_skills

---

*最后更新: 2026-07-21*