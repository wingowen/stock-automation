# Stock Automation - A股量化交易系统集合

> **项目定位**: 多项目仓库，包含多个独立的 A 股量化交易研究与自动化系统

---

## 📋 仓库说明

这是一个**多项目仓库（Multi-Project Repository）**，每个子目录都是一个完全独立的项目，包含自己的：
- 源代码
- 测试文件
- 文档
- 依赖管理（pyproject.toml）
- 虚拟环境

---

## 🗂️ 项目结构

```
stock-automation/
├── stockexpert-daily-brief/               # 项目 1: 每日简报系统
│   ├── README.md                          # 项目文档
│   ├── pyproject.toml                     # 项目依赖配置
│   ├── tests/                             # 项目测试（如有）
│   ├── .venv/                             # 项目虚拟环境（不入仓）
│   └── ...
│
├── wyckoff/                               # 项目 2: 威科夫交易系统
│   ├── README.md                          # 项目文档
│   ├── pyproject.toml                     # 项目依赖配置
│   ├── tests/                             # 项目测试
│   ├── .venv/                             # 项目虚拟环境（不入仓）
│   └── ...
│
├── .github/                               # GitHub Actions 配置
├── .gitignore                             # 全局 Git 忽略配置
└── README.md                              # 本文档（仓库说明）
```

---

## 🚀 快速开始

### 项目 1: 每日简报系统（stockexpert-daily-brief）

**功能**: 自动化 A 股市场分析看板，每日收盘后生成结构化研判报告

```bash
# 进入项目目录
cd stockexpert-daily-brief

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖（本项目仅使用标准库，无需额外安装）
# pip install -e .

# 配置环境变量
export AGNES_API_KEY="your-api-key"
export AGNES_BASE_URL="https://api.agnes-ai.com/v1"

# 运行
python web_brief.py
```

**详细文档**: [stockexpert-daily-brief/README.md](stockexpert-daily-brief/README.md)

---

### 项目 2: 威科夫交易系统（wyckoff）

**功能**: 基于《威科夫操盘法》的形态识别与策略回测系统

```bash
# 进入项目目录
cd wyckoff

# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 安装依赖
pip install -e .

# 运行测试
python -m pytest tests/ -v

# 使用
python -m wyckoff.data.pipeline fetch --code 600519 --years 5
```

**详细文档**: [wyckoff/README.md](wyckoff/README.md)

---

## 📚 项目列表

| 项目 | 功能 | 状态 | 文档 |
| --- | --- | --- | --- |
| [stockexpert-daily-brief](stockexpert-daily-brief/) | 每日简报系统 | 生产运行中 | [README](stockexpert-daily-brief/README.md) |
| [wyckoff](wyckoff/) | 威科夫交易系统 | 开发中 | [README](wyckoff/README.md) |

---

## 🛠️ 开发规范

### 多项目仓库原则

1. **项目独立性**: 每个项目完全独立，可以单独克隆、构建、运行
2. **虚拟环境独立**: 每个项目维护自己的虚拟环境（`.venv/`）
3. **依赖管理独立**: 每个项目有自己的 `pyproject.toml`
4. **文档独立**: 每个项目有完整的 `README.md`

### 全局配置

- `.gitignore`: 全局 Git 忽略规则（包括各项目的虚拟环境）
- `.github/`: GitHub Actions 工作流配置
- `README.md`: 仓库级说明文档（本文件）

---

## 🔒 安全与合规

### 数据安全

- ✅ 所有 secrets 通过 GitHub Actions Secrets 管理
- ✅ 不提交 API Key 或凭证到版本控制
- ✅ 数据源使用公开接口，无需账号密码

### 风险提示

> ⚠️ **重要提示**: 本仓库中所有项目仅供研究和学习使用，不构成投资建议。

---

## 🤝 贡献指南

### 添加新项目

1. 在根目录创建新项目目录
2. 创建项目的 `README.md`、`pyproject.toml`、`tests/` 等
3. 更新根 `README.md` 的项目列表
4. 提交 PR

### 提交规范

- feat: 新功能
- fix: 修复 Bug
- docs: 文档更新
- test: 测试相关
- refactor: 重构
- chore: 构建/工具相关

---

## 📞 联系方式

- **项目主页**: [GitHub Repository](https://github.com/your-username/stock-automation)
- **问题反馈**: [GitHub Issues](https://github.com/your-username/stock-automation/issues)

---

## 📄 许可证

本仓库中所有项目仅供个人学习和研究使用，未经授权不得用于商业用途。

---

*最后更新: 2026-07-21*