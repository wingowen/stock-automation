#!/usr/bin/env python3
"""上下文构建模块 -- K线数据 + Skill知识 + 历史简报。

职责：
1. 子进程调用 fetch_kline.py 获取 K 线数据，解析 stdout
2. 读取 wyckoff-trading/ 下的 Markdown 知识库文件
3. 根据分析轮次选择性加载 skill 章节
4. 调用 workflow.py 获取历史简报（归档 + 读取）

不重复实现已有功能，通过子进程复用 fetch_kline.py 和 workflow.py。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from config import (
    BG_MONTHS,
    KLINE_FETCH_SCRIPT,
    KNOWLEDGE_FILE,
    PROMPTS_DIR,
    PROJECT_ROOT,
    SKILL_DIR,
    WYCKOFF_SKILL_DIR,
    WORKFLOW_SCRIPT,
)


def log(*a):
    print("[context_builder]", *a, file=sys.stderr, flush=True)


# ── K线数据获取 ──────────────────────────────────────────────
def fetch_kline_data(code: str, bg_months: int = BG_MONTHS, trade_date: str | None = None) -> dict:
    """子进程调用 fetch_kline.py，解析 stdout 输出。

    Returns:
        {
            "code": "002279",
            "name": "久其软件",
            "raw_output": "完整 stdout 文本",
            "week_data": "本周日线数据块",
            "bg_data": "背景数据块",
            "summary": "统计分析块",
            "recent30": "最近30日明细块",
        }
    """
    monday = ""
    if trade_date:
        from datetime import datetime, timedelta
        d = datetime.strptime(trade_date, "%Y-%m-%d").date()
        monday = (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")

    cmd = [sys.executable, str(KLINE_FETCH_SCRIPT), code, str(bg_months)]
    if monday:
        cmd.append(monday)

    log(f"拉取 K 线: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(PROJECT_ROOT),
        )
    except subprocess.TimeoutExpired:
        log("K 线拉取超时")
        return {"code": code, "name": "", "raw_output": "", "error": "timeout"}
    except Exception as e:
        log(f"K 线拉取失败: {e}")
        return {"code": code, "name": "", "raw_output": "", "error": str(e)}

    raw = result.stdout
    if not raw.strip():
        log(f"K 线输出为空，stderr: {result.stderr[:200]}")
        return {"code": code, "name": "", "raw_output": "", "error": "empty output"}

    # 提取股票名称
    name = ""
    for line in raw.split("\n"):
        if line.startswith("STOCK_NAME="):
            name = line.split("=", 1)[1].strip()
            break

    # 按分隔符切分数据块
    blocks = _split_blocks(raw)

    return {
        "code": code,
        "name": name,
        "raw_output": raw,
        "week_data": blocks.get("本周", ""),
        "bg_data": blocks.get("背景", ""),
        "summary": blocks.get("统计分析", ""),
        "recent30": blocks.get("最近30", ""),
    }


def _split_blocks(raw: str) -> dict[str, str]:
    """按 === 分隔符切分 fetch_kline 的输出块。"""
    blocks = {}
    current_key = ""
    current_lines: list[str] = []

    for line in raw.split("\n"):
        if line.startswith("=" * 30):
            if current_key:
                blocks[current_key] = "\n".join(current_lines).strip()
            current_key = ""
            current_lines = []
        elif current_key == "" and line.strip() and not line.startswith("="):
            # 识别块标题
            for keyword in ("本周", "背景", "统计分析", "最近30"):
                if keyword in line:
                    current_key = keyword
                    break
            if not current_key:
                current_lines.append(line) if current_lines else None
        else:
            current_lines.append(line)

    if current_key:
        blocks[current_key] = "\n".join(current_lines).strip()

    return blocks


# ── Skill 知识加载 ───────────────────────────────────────────
def load_skill_core() -> str:
    """加载 SKILL.md 核心框架（所有轮次的 system prompt 基础）。"""
    skill_md = WYCKOFF_SKILL_DIR / "SKILL.md"
    if not skill_md.exists():
        log(f"SKILL.md 不存在: {skill_md}")
        return ""
    return skill_md.read_text(encoding="utf-8")


def load_chapter(chapter: str) -> str:
    """加载指定章节内容。

    Args:
        chapter: 章节标识，如 "ch01", "ch02", "ch04"
    """
    ch_file = WYCKOFF_SKILL_DIR / "chapters" / f"{chapter}-accumulation.md"
    if not ch_file.exists():
        # 尝试模糊匹配
        ch_dir = WYCKOFF_SKILL_DIR / "chapters"
        if ch_dir.exists():
            candidates = list(ch_dir.glob(f"{chapter}*.md"))
            if candidates:
                ch_file = candidates[0]
            else:
                return ""
        else:
            return ""
    return ch_file.read_text(encoding="utf-8")


def load_chapters_for_round(round_num: int, background_phase: str = "") -> str:
    """根据轮次和背景判断结果，选择性加载章节。

    Args:
        round_num: 1-5
        background_phase: Round 1 的输出，如 "accumulation" / "distribution" / "spring"
    """
    chapters = []

    if round_num == 1:
        # Round 1: 只需核心框架（已在 system prompt 中）
        pass
    elif round_num == 2:
        # Round 2: 根据背景加载对应章节
        if "accumulation" in background_phase.lower() or "吸筹" in background_phase:
            chapters.append(load_chapter("ch02"))
        elif "distribution" in background_phase.lower() or "派发" in background_phase:
            chapters.append(load_chapter("ch03"))
        elif "spring" in background_phase.lower():
            chapters.append(load_chapter("ch04"))
        else:
            # 默认加载吸筹+派发
            chapters.append(load_chapter("ch02"))
            chapters.append(load_chapter("ch03"))
    elif round_num == 3:
        # Round 3: 三大原则 + 市场本质
        chapters.append(load_chapter("ch01"))
        chapters.append(load_chapter("ch05"))
    elif round_num in (4, 5):
        # Round 4-5: 综合分析
        chapters.append(load_chapter("ch05"))
        chapters.append(load_chapter("ch06"))

    return "\n\n---\n\n".join(c for c in chapters if c)


# ── 历史简报获取 ──────────────────────────────────────────────
def get_history_brief(code: str, trade_date: str) -> dict:
    """调用 workflow.py 获取历史简报信息。

    Returns:
        {
            "new_brief_path": "/abs/path/to/new/brief.md",
            "archived": "archive/path or NONE",
            "stock_name": "久其软件",
            "version": 1,
            "history_content": "旧简报全文 or '（无历史简报，首次分析）'",
        }
    """
    cmd = [sys.executable, str(WORKFLOW_SCRIPT), code, trade_date]

    log(f"准备简报: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT_ROOT),
        )
    except Exception as e:
        log(f"workflow.py 调用失败: {e}")
        return {
            "new_brief_path": "",
            "archived": "NONE",
            "stock_name": "",
            "version": 1,
            "history_content": "（工作流调用失败）",
        }

    stdout = result.stdout

    # 解析 workflow.py 的结构化输出
    new_brief = ""
    archived = "NONE"
    stock_name = ""
    version = 1
    history_content = "（无历史简报，首次分析）"

    in_history = False
    history_lines: list[str] = []

    for line in stdout.split("\n"):
        if line.startswith("NEW_BRIEF="):
            new_brief = line.split("=", 1)[1].strip()
        elif line.startswith("ARCHIVED="):
            archived = line.split("=", 1)[1].strip()
        elif line.startswith("STOCK_NAME="):
            stock_name = line.split("=", 1)[1].strip()
        elif line.startswith("VERSION="):
            try:
                version = int(line.split("=", 1)[1].strip().replace("v", ""))
            except ValueError:
                version = 1
        elif line.strip() == "HISTORY_BEGIN":
            in_history = True
        elif line.strip() == "HISTORY_END":
            in_history = False
        elif in_history:
            history_lines.append(line)

    if history_lines:
        history_content = "\n".join(history_lines).strip()

    return {
        "new_brief_path": new_brief,
        "archived": archived,
        "stock_name": stock_name,
        "version": version,
        "history_content": history_content,
    }


# ── K线数据压缩 ──────────────────────────────────────────────
def compress_kline_for_later_rounds(kline_data: dict, round1_result: dict) -> str:
    """为 Round 2-5 压缩 K 线数据，只保留摘要 + Round 1 结论。

    避免每轮都传完整 K 线明细，节省 token。
    """
    summary = kline_data.get("summary", "")
    bg = kline_data.get("bg_data", "")

    # 提取背景期高低点和均量（从 summary 中）
    bg_summary = ""
    for line in bg.split("\n"):
        if "高点" in line or "低点" in line or "日均量" in line:
            bg_summary += line + "\n"

    round1_json = json.dumps(round1_result, ensure_ascii=False, indent=2) if round1_result else ""

    return f"""【K线摘要】
{summary}

【背景期关键数据】
{bg_summary}

【Round 1 背景判断结论】
{round1_json}
"""


# ── Prompt 模板加载 ──────────────────────────────────────────
def load_prompt(round_name: str) -> str:
    """加载指定轮次的 prompt 模板。

    Args:
        round_name: 如 "round1_background", "round2_patterns"
    """
    prompt_file = PROMPTS_DIR / f"{round_name}.txt"
    if not prompt_file.exists():
        log(f"Prompt 模板不存在: {prompt_file}")
        return ""
    return prompt_file.read_text(encoding="utf-8")
