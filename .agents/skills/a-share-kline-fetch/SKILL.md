---
name: "a-share-kline-fetch"
description: "Fetch A-share daily kline data (Tencent API, qfq), format for Wyckoff analysis, and manage analysis briefings with iteration. Invoke when user asks to analyze a stock's trend/week using wyckoff-trading skill, needs A-share OHLCV data, or wants to generate/iterate analysis briefings."
---

# A股K线数据获取 + 威科夫分析简报管理

获取 A 股前复权日线数据，输出威科夫操盘法分析所需的标准格式（含振幅、收位、量比等维度），并管理分析简报的迭代归档与知识沉淀。配合 `wyckoff-trading` skill 使用：本 skill 提供"数据 + 格式 + 简报工作流"，`wyckoff-trading` 提供"分析框架"。

## 何时调用

- 用户要求分析某只 A 股"本周/近期/某段时间"走势（结合 `wyckoff-trading` skill）
- 用户要求获取 A 股 K 线数据（含价量关系维度）
- 用户提到 6 位股票代码（如 002279、600519）并要分析走势

## 文件清单

| 文件 | 作用 |
|---|---|
| `setup_env.sh` | 环境探测：优先校验仓库根目录统一 venv（uv 管理），把 python 路径写入 `.python_path`；缺失时用 uv 自动创建；无 uv（CI / 最小环境）时回退到 PATH 上已装依赖的 python3 |
| `fetch_kline.py` | 数据拉取脚本：腾讯 K 线 + 格式化输出威科夫分析所需数据 |
| `workflow.py` | 简报工作流：旧简报归档 + 新简报模板生成 + 历史上下文读取 |
| `.python_path` | `setup_env.sh` 运行后生成，记录统一 venv 的 python 绝对路径（供调用方读取） |

## 环境约定

- **环境策略**：全项目统一使用仓库根目录 `.venv`（`uv venv .venv` 创建、`uv pip install` 装依赖），本 skill **不单独建环境**
- **依赖**：`pandas` + `requests`（随根目录统一 venv 安装，无 akshare 重型依赖）
- **Python 版本**：≥ 3.10（用了 `Int64` nullable 类型）；统一 venv 使用 Python 3.11+（满足 wyckoff 等子模块要求）
- **数据源**：腾讯财经 K 线接口 `https://web.ifzq.gtimg.cn/appstock/app/fqkline/get`
- **复权方式**：前复权（qfq）
- **代理处理**：脚本内 `session.trust_env = False`，规避 macOS 系统代理影响
- **交易所识别**：6 开头 = 沪市（sh），其他 = 深市（sz）；不支持北交所

## 调用流程

### 第一步：准备环境（首次或环境变更时）

```bash
# 全项目统一 venv（仓库根目录，uv 管理）：
cd <repo_root> && uv venv .venv && uv pip install --python .venv/bin/python pandas requests
# 再运行本 skill 的环境探测，生成 .python_path：
bash .agents/skills/a-share-kline-fetch/setup_env.sh
```

输出示例：
```
[setup_env] 优先使用项目根目录统一 venv: /path/to/repo/.venv/bin/python
[setup_env] OK: 使用统一 venv /path/to/repo/.venv/bin/python
PYTHON=/path/to/repo/.venv/bin/python
```

成功后会在 skill 目录生成 `.python_path` 文件，后续可直接用其中路径运行 `fetch_kline.py`。

### 第二步：拉取并格式化数据

调用方（agent 或用户）按以下方式获取 python 路径并运行脚本：

```bash
SKILL_DIR=.agents/skills/a-share-kline-fetch
PY=$(cat $SKILL_DIR/.python_path 2>/dev/null || echo python3)
$PY $SKILL_DIR/fetch_kline.py <code> [背景月数] [本周一日期YYYY-MM-DD]
```

### 用法示例

```bash
# 默认：6个月背景 + 自动识别本周（基于今天日期）
python fetch_kline.py 002279

# 3个月背景 + 指定本周一日期
python fetch_kline.py 600519 3 2026-07-27

# 仅指定代码，其他默认
python fetch_kline.py 000001
```

## 输出格式说明

脚本输出 4 个数据块（stdout）：

1. **本周日线数据**：日期、OHLCV、涨跌、振幅、收位（0=最低，1=最高）
2. **近 N 月背景数据**：用于识别震荡区/趋势背景
3. **统计分析**：本周涨跌幅、振幅、均量、量比、背景期高低点
4. **最近30个交易日明细**：完整价量数据

### 关键维度释义

| 维度 | 计算方式 | 威科夫含义 |
|---|---|---|
| 振幅 | (高-低)/开 | 大振幅 + 高量 = SC/BC 候选 |
| 收位 | (收-低)/(高-低) | 收位接近1 = 需求主导；接近0 = 供应主导 |
| 量比 | 本周日均量/背景日均量 | >2 = 异常放量；<0.7 = 缩量 |
| 涨跌% | (收-开)/开 | 趋势方向初步判断 |

## 调用方工作流（agent 视角）

当用户要求"分析 XXXXXX 本周走势"时，执行以下 5 步闭环：

### 第一步：准备环境（首次或环境变更时）

```bash
bash .agents/skills/a-share-kline-fetch/setup_env.sh
```

### 第二步：拉取并格式化数据

```bash
SKILL_DIR=.agents/skills/a-share-kline-fetch
PY=$(cat $SKILL_DIR/.python_path 2>/dev/null || echo python3)
$PY $SKILL_DIR/fetch_kline.py <code> [背景月数] [本周一日期YYYY-MM-DD]
```

### 第三步：准备简报（读取历史 + 归档 + 生成模板）

```bash
# 可选：从 fetch_kline.py 的输出中提取 STOCK_NAME=xxx 传给 workflow.py，简报标题会带名称
$PY $SKILL_DIR/workflow.py <code> [分析日期YYYY-MM-DD] [股票名称]
```

输出包含：
- `NEW_BRIEF=<新简报路径>`：agent 应把分析内容写入此文件
- `ARCHIVED=<归档路径|NONE>`：若有归档，说明基于历史迭代
- `STOCK_NAME=<名称>`：透传的股票名称（可能为空）
- `HISTORY_BEGIN ... HISTORY_END`：旧简报全文，供 agent 提取关键预判做对照

**获取股票名称**：`fetch_kline.py` 的输出第二行含 `STOCK_NAME=久其软件`（从腾讯接口 qt 字段提取），agent 可用 grep 提取后传给 workflow.py。

### 第四步：写入分析内容

agent 把 `fetch_kline.py` 的数据 + `wyckoff-trading` skill 的看盘五步法分析，填入 `NEW_BRIEF` 指向的模板文件。**必须填写"本次预判"章节**（方向/关键价位/时间窗口/触发条件），用于下次迭代验证。

### 第五步：更新迭代知识库

打开 `analysis-brief/KNOWLEDGE.md`，按 4 个维度更新：

| 维度 | 何时更新 | 更新方式 |
|---|---|---|
| 模式总结 | 识别到跨个股共性形态 | 在表格末尾追加条目 |
| 个股跨期迭代 | 同股有历史简报时 | 对照上次预判 vs 实际走势，记录偏差 |
| 规则修正 | 发现 A 股本地化调整 | 记录原规则 + 修正 + 理由 |
| 失败案例库 | 预判被市场证伪 | 不掩饰，记录根因 + 防范措施 |

**重要**：每次分析后必须检查这 4 个维度，有新发现则更新，无则保持不变。

## 数据缓存与复用

- 当前版本不持久化数据，每次运行都重新拉取（保证数据最新）
- 如需缓存，可手动重定向输出：`python fetch_kline.py 002279 > /tmp/002279_$(date +%Y%m%d).txt`
- 项目内 `wyckoff/data/tencent_source.py` 提供了带缓存回退的版本（需安装 akshare），可作为升级路径

## 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| `[ERROR] 无数据返回` | 腾讯接口限流或代码错 | 检查代码前缀；稍后重试 |
| `ModuleNotFoundError: pandas` | 统一 venv 缺少依赖 | 根目录运行 `uv pip install --python .venv/bin/python pandas requests` |
| `.python_path` 不存在 | 未运行 setup_env.sh | 先运行 `bash setup_env.sh` |
| 拉取的数据少于预期 | 腾讯单次最多 640 条 | 缩短背景月数，或修改脚本分批拉取 |

## 范围与限制

- 仅 A 股（沪深），不含港股/美股/期货
- 仅日线，不含分钟线
- 不做技术指标计算（MACD/KDJ 等），只输出威科夫分析所需的原始价量维度
- 不做交易决策——决策由 `wyckoff-trading` skill 框架完成

## 简报目录结构

```
analysis-brief/
├── KNOWLEDGE.md              # 顶层迭代知识文档（4维度：模式/个股/规则/失败案例）
├── archive/                  # 历史归档（可追溯）
│   └── <code>/
│       └── <code>_<date>_v<n>.md
└── <YYYY-MM>/                # 按年月分目录（当月最新简报）
    └── <code>_<date>.md
```

**迭代机制**：
1. 同股同月再次分析时，`workflow.py` 自动把旧简报移到 `archive/<code>/` 并加版本号
2. 新简报引用历史版本，agent 基于历史预判做对照分析
3. 每次分析后更新 `KNOWLEDGE.md`，形成跨期跨股的迭代知识沉淀
