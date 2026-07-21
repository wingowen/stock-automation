"""可视化脚本: 600519 + Spring 检测结果

Phase B 退出标准的一部分: "PR 包含 1 张真实数据可视化"
"""
import sys
from pathlib import Path

# Add worktree to path
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")  # no display
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from wyckoff.data.tencent_source import load_600519
from wyckoff.detectors.spring import detect_spring

# 加载数据
df = load_600519()
df["date_dt"] = pd.to_datetime(df["date"])
events = detect_spring(df)

print(f"Data: {len(df)} rows, {df.iloc[0]['date']} to {df.iloc[-1]['date']}")
print(f"Detected {len(events)} Springs:")
for e in events:
    print(f"  {e.date}  price={e.price:.2f}  rvol={e.rvol:.2f}  strength={e.strength}")

# 画图
fig, ax = plt.subplots(figsize=(14, 6))
ax.plot(df["date_dt"], df["close"], color="#2c3e50", linewidth=1, label="Close")
ax.plot(df["date_dt"], df["high"], color="#95a5a6", linewidth=0.5, alpha=0.5, label="High")
ax.plot(df["date_dt"], df["low"], color="#95a5a6", linewidth=0.5, alpha=0.5, label="Low")

# 标记 Spring
for e in events:
    ax.scatter(e.date, e.price, marker="^", color="#e74c3c", s=150, zorder=5,
               label=f"Spring (strength={e.strength})" if e == events[0] else None)
    ax.annotate(
        f"Spring\nrvol={e.rvol:.1f}\n{str(e.strength)}",
        xy=(e.date, e.price),
        xytext=(0, 18),
        textcoords="offset points",
        ha="center",
        fontsize=8,
        color="#c0392b",
    )

# 标注 range 上下文 (以第一个 Spring 为例)
if events:
    e = events[0]
    ax.axhspan(e.range_low, e.range_high, alpha=0.1, color="blue",
               label=f"Range @ {e.date} (low={e.range_low:.0f}, high={e.range_high:.0f})")

ax.set_title(f"600519 贵州茅台 — Spring Detection (Phase B)\n"
             f"Data: {df.iloc[0]['date']} ~ {df.iloc[-1]['date']} ({len(df)} trading days)",
             fontsize=12)
ax.set_xlabel("Date")
ax.set_ylabel("Price (前复权)")
ax.legend(loc="upper left", fontsize=9)
ax.grid(True, alpha=0.3)
ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
fig.autofmt_xdate()

out_path = ROOT / "data" / "cache" / "600519_spring_v1.png"
fig.tight_layout()
fig.savefig(out_path, dpi=120)
print(f"\n✅ Saved: {out_path}")
