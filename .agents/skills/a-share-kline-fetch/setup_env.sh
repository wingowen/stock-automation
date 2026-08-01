#!/usr/bin/env bash
# A股K线数据获取 - 环境探测/创建脚本
#
# 策略（按优先级回退）：
#   1. 探测系统已有 python 是否已安装 pandas + requests
#   2. 探测常见 conda 环境（base / 项目相关 env）
#   3. 上述均失败 → 在 skill 目录下创建最小 venv（.venv），仅装 pandas + requests
#
# 输出：把可用的 python 解释器绝对路径写入 .python_path 文件（供 fetch_kline.py 读取）
#       同时打印到 stdout，格式：PYTHON=<path>
#
# 用法：
#   bash setup_env.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_PATH_FILE="$SCRIPT_DIR/.python_path"

# ── 工具函数 ────────────────────────────────────────────────
check_deps() {
    local py="$1"
    [ -x "$py" ] || return 1
    "$py" -c "import pandas, requests" 2>/dev/null
}

# ── 候选 Python 列表 ─────────────────────────────────────────
CANDIDATES=(
    "$(command -v python3 2>/dev/null || true)"
    "/Users/wingo.wen/anaconda3/bin/python"
    "/opt/homebrew/bin/python3"
    "/usr/bin/python3"
    "/Users/wingo.wen/anaconda3/envs/stock_data_collection/bin/python"
    "/Users/wingo.wen/anaconda3/envs/trading_env/bin/python"
)

echo "[setup_env] 探测可用 Python 解释器..."
for py in "${CANDIDATES[@]}"; do
    [ -z "$py" ] && continue
    if [ -x "$py" ] && check_deps "$py"; then
        echo "$py" > "$PYTHON_PATH_FILE"
        echo "[setup_env] OK: 使用 $py"
        echo "PYTHON=$py"
        exit 0
    fi
    echo "[setup_env]   - $py 不满足（缺失 pandas/requests 或不存在）"
done

# ── 回退：创建本地最小 venv ─────────────────────────────────
echo "[setup_env] 未找到满足依赖的 Python，开始创建本地 venv..."
VENV_DIR="$SCRIPT_DIR/.venv"

# 选择一个能用的 python3 用于创建 venv
BASE_PY=""
for py in "${CANDIDATES[@]}"; do
    [ -z "$py" ] && continue
    if [ -x "$py" ]; then
        BASE_PY="$py"
        break
    fi
done
[ -z "$BASE_PY" ] && { echo "[setup_env] FATAL: 系统中找不到任何 python3"; exit 1; }

"$BASE_PY" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet pandas requests

echo "$VENV_DIR/bin/python" > "$PYTHON_PATH_FILE"
echo "[setup_env] OK: 已创建 venv $VENV_DIR"
echo "PYTHON=$VENV_DIR/bin/python"
