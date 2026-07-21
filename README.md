# Stock Automation - A股量化交易系统

> **项目定位**: 基于 Python 的 A 股量化交易研究与自动化系统，包含每日看板生成和威科夫操盘法形态识别两大核心模块。

---

## 📋 项目概述

本项目旨在构建一个完整的 A 股量化交易研究与自动化系统，主要包含两个独立但互补的功能模块：

1. **每日简报系统** (`stockexpert-daily-brief`): 自动化 A 股市场分析看板，每日收盘后生成结构化研判报告
2. **威科夫交易系统** (`wyckoff`): 基于经典威科夫操盘法的形态识别与策略回测系统

### 核心特点

- ✅ **零成本运行**: 使用免费数据源 + GitHub Actions + GitHub Pages
- ✅ **结构化研判**: 所有判断字段包含 `source` + `reasoning`，数据可追溯
- ✅ **TDD 开发**: 测试驱动开发，核心逻辑覆盖率 ≥ 90%
- ✅ **模块化设计**: 两个独立模块，可单独部署和运行
- ✅ **符合规范**: 遵循 spec-driven-development 开发流程

---

## 🗂️ 项目结构

```
stock-automation/
├── stockexpert-daily-brief/               # 每日简报系统（独立模块）
│   ├── README.md                          # 模块说明文档
│   ├── web_brief.py                       # 主脚本：Web 汇聚 + AI 研判
│   ├── dashboard/                         # GitHub Pages 前端
│   └── docs/                              # 模块文档
│
├── wyckoff/                               # 威科夫交易系统（独立模块）
│   ├── README.md                          # 模块说明文档
│   ├── data/                              # 数据获取与验证
│   ├── detectors/                         # 形态检测器
│   ├── scripts/                           # 实用脚本
│   └── docs/                              # 系统文档
│       ├── system/                        # 技术规范
│       └── tasks/                         # 任务管理
│
├── tests/                                 # 威科夫系统测试
├── .github/                               # GitHub Actions 配置
├── pyproject.toml                         # Python 项目配置
└── README.md                              # 本文档
```

**注意**: `tests/` 保留在顶层是 Python 标准做法，服务于威科夫系统。

---

## 🚀 快速开始

### 环境要求

- Python 3.11+
- Git
- GitHub 账号（用于 GitHub Actions 和 Pages）

### 安装步骤

```bash
# 克隆项目
git clone https://github.com/your-username/stock-automation.git
cd stock-automation

# 安装威科夫系统依赖
pip install -e .
```

---

## 📚 功能模块说明

### 1. 每日简报系统 (`stockexpert-daily-brief`)

**功能**: 自动化 A 股市场分析看板，每日收盘后生成结构化研判报告

**核心特性**:
- 自动抓取公开 Web 财经数据（指数、题材、板块、新闻）
- 调用 Agnes AI 做综合研判（右侧交易体系）
- 生成符合 `dashboard-contract` 的 `看板.json`
- 部署到 GitHub Pages，零成本、无本地依赖

**详细文档**: [stockexpert-daily-brief/README.md](stockexpert-daily-brief/README.md)

---

### 2. 威科夫交易系统 (`wyckoff`)

**功能**: 基于《威科夫操盘法》的形态识别与策略回测系统

**核心特性**:
- 16+ 威科夫形态检测器（吸筹、派发、复合形态）
- 多源数据获取与交叉验证（AkShare + 腾讯财经）
- TDD 开发流程，核心逻辑覆盖率 ≥ 90%
- 数据流水线强制验证，拒绝合并冲突数据

**当前进度**:
- ✅ Phase A: 规格编写完成
- ✅ Phase B: Spring 检测器 TDD 闭环
- ✅ 数据流水线：双源交叉验证完成
- 🚧 Phase C: 完整检测器开发中

**详细文档**: [wyckoff/README.md](wyckoff/README.md)

---

## 🛠️ 开发规范

### 代码风格

- **Python**: 遵循 PEP 8，使用类型注解
- **命名**: 函数 `snake_case`，类 `PascalCase`，常量 `UPPER_CASE`
- **文档**: 关键函数使用 docstring，逻辑行不写注释
- **测试**: TDD 开发，每个检测器 ≥ 3 个测试用例

### 提交规范

- feat: 新功能
- fix: 修复 Bug
- docs: 文档更新
- test: 测试相关
- refactor: 重构
- chore: 构建/工具相关

---

## 📖 文档索引

### 每日简报系统

- **模块说明**: [stockexpert-daily-brief/README.md](stockexpert-daily-brief/README.md)
- **技术规范**: [stockexpert-daily-brief/SPEC.md](stockexpert-daily-brief/SPEC.md)

### 威科夫交易系统

- **模块说明**: [wyckoff/README.md](wyckoff/README.md)
- **系统规范**: [wyckoff/docs/system/SPEC.md](wyckoff/docs/system/SPEC.md)
- **数据流水线**: [wyckoff/docs/system/SPEC_DATA_PIPELINE.md](wyckoff/docs/system/SPEC_DATA_PIPELINE.md)
- **实施方案**: [wyckoff/docs/system/实施方案.md](wyckoff/docs/system/实施方案.md)

### 任务管理

- **实现计划**: [wyckoff/docs/tasks/plan.md](wyckoff/docs/tasks/plan.md)
- **任务清单**: [wyckoff/docs/tasks/todo.md](wyckoff/docs/tasks/todo.md)

---

## 🧪 测试与质量

### 运行测试

```bash
# 运行所有测试（威科夫系统）
python -m pytest tests/ -v

# 运行指定测试
python -m pytest tests/test_spring.py -v
python -m pytest tests/test_pipeline.py -v

# 生成覆盖率报告
python -m pytest tests/ --cov=wyckoff --cov-report=html
```

---

## 🔒 安全与合规

### 数据安全

- ✅ 所有 secrets 通过 GitHub Actions Secrets 管理
- ✅ 不提交 API Key 或凭证到版本控制
- ✅ 数据源使用公开接口，无需账号密码

### 风险提示

> ⚠️ **重要提示**: 本系统仅供研究和学习使用，不构成投资建议。
>
> - 每日简报系统：仅为研究参考，不构成投资建议
> - 威科夫交易系统：模拟盘验证通过后才能考虑实盘，且需严格风控
> - 实盘前必须完成完整回测和模拟盘测试

---

## 🤝 贡献指南

### 贡献流程

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'feat: add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

---

## 📞 联系方式

- **项目主页**: [GitHub Repository](https://github.com/your-username/stock-automation)
- **问题反馈**: [GitHub Issues](https://github.com/your-username/stock-automation/issues)

---

## 📄 许可证

本项目仅供个人学习和研究使用，未经授权不得用于商业用途。

---

*最后更新: 2026-07-21*