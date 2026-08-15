#!/usr/bin/env bash
# A股K线数据获取 - 统一环境探测脚本
#
# 环境策略：本地开发全项目共用根目录统一 venv（uv 管理），本 skill 不再单独建环境。
#   统一 venv 路径：<repo_root>/.venv（用 `uv venv .venv` 创建）
#   依赖：pandas + requests（skill 所需），由根目录统一安装管理
#
# 解释器选择（按优先级）：
#   1. 根目录统一 venv（.venv）已存在且含 pandas/requests → 直接使用
#   2. 不存在但机器装有 uv → 用 uv 在根目录创建统一 venv 并装依赖
#   3. 无 uv（如 CI / 最小环境，使用 setup-python 的 python）→ 回退到 PATH 上
#      已具备 pandas/requests 的 python3，不新建任何环境
#
# 输出：把选定的 python 绝对路径写入 .python_path 文件（供调用方读取）
#       同时打印到 stdout，格式：PYTHON=<path>
#
# 用法：
#   bash setup_env.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PYTHON_PATH_FILE="$SCRIPT_DIR/.python_path"
UNIFIED_PY="$REPO_ROOT/.venv/bin/python"

echo "[setup_env] 优先使用项目根目录统一 venv: $UNIFIED_PY"

# 层级 1/2：统一 venv 不存在时，尝试用 uv 创建
if [ ! -x "$UNIFIED_PY" ]; then
    if command -v uv >/dev/null 2>&1; then
        echo "[setup_env] 未找到统一 venv，用 uv 在根目录创建..."
        (cd "$REPO_ROOT" && uv venv .venv && uv pip install --python .venv/bin/python pandas requests)
    else
        echo "[setup_env] 未找到统一 venv 且未安装 uv，将回退到系统 python（仅 CI / 最小环境）。"
    fi
fi

# 层级 1/2：统一 venv 可用
if [ -x "$UNIFIED_PY" ] && "$UNIFIED_PY" -c "import pandas, requests" 2>/dev/null; then
    echo "$UNIFIED_PY" > "$PYTHON_PATH_FILE"
    echo "[setup_env] OK: 使用统一 venv $UNIFIED_PY"
    echo "PYTHON=$UNIFIED_PY"
    exit 0
fi

# 层级 3：回退到 PATH 上已具备依赖的 python3（CI 的 setup-python 场景）
echo "[setup_env] 统一 venv 不可用，探测 PATH 上满足依赖的 python3..."
for py in python3 python; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c "import pandas, requests" 2>/dev/null; then
        PY="$(command -v "$py")"
        echo "$PY" > "$PYTHON_PATH_FILE"
        echo "[setup_env] OK: 使用系统 python $PY"
        echo "PYTHON=$PY"
        exit 0
    fi
done

echo "[setup_env] ERROR: 没有可用环境。请先安装 uv 并在仓库根目录运行："
echo "  uv venv .venv && uv pip install --python .venv/bin/python pandas requests"
exit 1
