#!/usr/bin/env python3
"""威科夫分析简报管理工作流

职责：
1. 检查当月是否有同股旧简报 → 有则归档到 archive/{code}/ 并读取历史内容
2. 生成新简报模板文件（含历史上下文引用）
3. 输出新简报路径 + 历史简报内容（供 agent 写入分析时参考）

用法：
    python workflow.py <code> [analysis_date YYYY-MM-DD] [stock_name]

    code: 6位股票代码
    analysis_date: 分析日期，默认今天
    stock_name: 股票名称（可选，若提供则写入简报标题；否则标题只显示代码）

输出（stdout）：
    NEW_BRIEF=<新简报文件绝对路径>
    ARCHIVED=<归档的旧简报路径，若无则为 NONE>
    STOCK_NAME=<股票名称（可能为空）>
    HISTORY_BEGIN
    ...旧简报内容（供 agent 读取参考）...
    HISTORY_END

目录结构：
    analysis-brief/
    ├── KNOWLEDGE.md              # 顶层迭代知识文档
    ├── archive/                  # 历史归档
    │   └── <code>/
    │       └── <code>_<date>_v<n>.md
    └── <YYYY-MM>/                # 当月最新简报
        └── <code>_<name>_<date>.md   # 提供 stock_name 时；否则 <code>_<date>.md
"""
from __future__ import annotations

import sys
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

# ── 路径约定 ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[3]  # stock-automation/
BRIEF_ROOT = PROJECT_ROOT / "analysis-brief"
ARCHIVE_ROOT = BRIEF_ROOT / "archive"
KNOWLEDGE_FILE = BRIEF_ROOT / "KNOWLEDGE.md"


def find_existing_brief(code: str, year_month: str) -> Path | None:
    """在指定月份目录下查找同股旧简报（匹配前缀 <code>_）"""
    month_dir = BRIEF_ROOT / year_month
    if not month_dir.exists():
        return None
    candidates = sorted(month_dir.glob(f"{code}_*.md"))
    return candidates[-1] if candidates else None


def next_archive_version(code: str, base_name: str) -> Path:
    """为归档文件生成带版本号的路径：archive/<code>/<base>_v<n>.md"""
    code_dir = ARCHIVE_ROOT / code
    code_dir.mkdir(parents=True, exist_ok=True)
    stem = base_name.rsplit(".", 1)[0]  # 去掉 .md
    n = 1
    while True:
        candidate = code_dir / f"{stem}_v{n}.md"
        if not candidate.exists():
            return candidate
        n += 1


BRIEF_TEMPLATE = """# {code}{name_part} 威科夫分析简报

> 分析日期：{today}  |  版本：v{version}  |  数据范围：见下
> 历史简报：{history_ref}
> 知识库：[KNOWLEDGE.md](../KNOWLEDGE.md)

## 〇、历史上下文

{history_summary}

## 一、数据摘要

_（由 a-share-kline-fetch/fetch_kline.py 输出填充）_

- 本周（{week_range}）：
- 背景期：
- 关键统计：涨跌 __% / 振幅 __% / 量比 __

## 二、看盘五步法分析

### 第①步 背景判断

_（牛/熊/震荡区；处于吸筹/派发的哪个阶段）_

### 第②步 价量形态

_（关键K线描述：SC/AR/ST/SOS/JOC/UT/SOW 等）_

### 第③步 形态性质

_（努力-结果关系；停止行为；吸收行为等）_

### 第④步 结论/预测

| 维度 | 结论 |
|---|---|
| 大背景 | |
| 短期 | |
| 关键价位 | 阻力：__  支撑：__ |
| 当前阶段 | |

### 第⑤步 措施和行动

| 情景 | 条件 | 行动 |
|---|---|---|
| 进场 | | |
| 放弃 | | |
| 跟进 | | |

## 三、本次预判（用于后续迭代验证）

| 项目 | 预判 |
|---|---|
| 方向 | |
| 关键价位 | |
| 时间窗口 | |
| 触发条件 | |

## 四、迭代知识更新清单

本次分析对 [KNOWLEDGE.md](../KNOWLEDGE.md) 的更新（完成后由 agent 填写实际更新内容）：

- [ ] 模式总结：
- [ ] 个股跨期迭代：
- [ ] 规则修正：
- [ ] 失败案例库：

## 五、风险提示

1.
2.
3.
"""


def render_template(code: str, today_str: str, version: int,
                    history_ref: str, history_summary: str,
                    week_range: str, stock_name: str = "") -> str:
    name_part = f" {stock_name}" if stock_name else ""
    return BRIEF_TEMPLATE.format(
        code=code,
        name_part=name_part,
        today=today_str,
        version=version,
        history_ref=history_ref,
        history_summary=history_summary,
        week_range=week_range,
    )


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    code = sys.argv[1].strip()
    today_str = sys.argv[2] if len(sys.argv) >= 3 else date.today().strftime("%Y-%m-%d")
    stock_name = sys.argv[3] if len(sys.argv) >= 4 else ""
    today = datetime.strptime(today_str, "%Y-%m-%d").date()
    year_month = f"{today.year:04d}-{today.month:02d}"

    # 本周范围（周一~周五）
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    week_range = f"{monday} ~ {friday}"

    # 1. 查找当月旧简报
    existing = find_existing_brief(code, year_month)

    archive_path_str = "NONE"
    history_content = "（无历史简报，首次分析）"
    history_ref = "无（首次分析）"
    version = 1

    if existing:
        # 2. 归档旧简报
        archive_path = next_archive_version(code, existing.name)
        shutil.move(str(existing), str(archive_path))
        archive_path_str = str(archive_path.relative_to(PROJECT_ROOT))
        history_content = archive_path.read_text(encoding="utf-8")
        history_ref = f"[{archive_path_str}](../{archive_path_str})"
        # 版本号从归档文件名提取
        try:
            version = int(archive_path.stem.rsplit("_v", 1)[1]) + 1
        except (IndexError, ValueError):
            version = 2
        print(f"[workflow] 已归档旧简报到 {archive_path_str}", file=sys.stderr)

    # 3. 生成新简报模板
    month_dir = BRIEF_ROOT / year_month
    month_dir.mkdir(parents=True, exist_ok=True)

    name_segment = f"_{stock_name}" if stock_name else ""
    new_brief_name = f"{code}{name_segment}_{today_str}.md"
    new_brief_path = month_dir / new_brief_name

    # 历史上下文摘要（取前几行作为引用）
    if existing:
        history_summary = f"参考上一版简报（{archive_path_str}）的核心预判：\n\n> _（agent 应根据 HISTORY 内容提取关键预判填入此处）_"
    else:
        history_summary = "_首次分析，无历史上下文_"

    template = render_template(
        code=code, today_str=today_str, version=version,
        history_ref=history_ref, history_summary=history_summary,
        week_range=week_range, stock_name=stock_name,
    )
    new_brief_path.write_text(template, encoding="utf-8")

    # 4. 输出结果
    rel_path = new_brief_path.relative_to(PROJECT_ROOT)
    print(f"NEW_BRIEF={new_brief_path}")
    print(f"ARCHIVED={archive_path_str}")
    print(f"STOCK_NAME={stock_name}")
    print(f"REL_PATH={rel_path}")
    print(f"VERSION=v{version}")
    print("HISTORY_BEGIN")
    print(history_content)
    print("HISTORY_END")
    print(f"\n[workflow] 新简报模板已生成：{rel_path}", file=sys.stderr)
    print(f"[workflow] 请 agent：1) 填入分析内容  2) 更新 KNOWLEDGE.md", file=sys.stderr)


if __name__ == "__main__":
    main()
