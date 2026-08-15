# 威科夫扫描 · Supabase 持久化 SPEC（v1.0）

> 状态：待用户评审
> 关联：[SPEC.md](file:///Users/wingo.wen/Documents/WorkSpace/stock-automation/wyckoff/docs/system/SPEC.md) · [wyckoff-signal-scan.yml](file:///Users/wingo.wen/Documents/WorkSpace/stock-automation/.github/workflows/wyckoff-signal-scan.yml)

---

## 1. 一句话

用 Supabase（免费 Postgres）为每日 LPS 全市场扫描提供**断点续扫 + 信号历史**持久化，使 GitHub Actions 超时/失败后重试只需补扫未完成的股票，且信号记录永久可查。

## 2. 解决什么问题

| 现在 | 目标 |
| --- | --- |
| 全市场扫描 ~64 分钟，30 分钟超时被强杀后**全部作废** | 断点续扫，重试只补未完成部分（分钟级） |
| 结果只写本地 JSON artifact，无信号日 artifact 为空 | DB 为主、artifact 为辅，历史可查 |
| 信号无历史记录，无法事后复盘 | `lps_signals` 永久保留，供回测/复盘查询 |

## 3. Tech Stack

| 层 | 选型 | 理由 |
| --- | --- | --- |
| 数据库 | **Supabase 免费版**（Postgres） | 已有账号，专用项目；500MB/500MAU 额度远超所需（预计 < 10MB） |
| 通信 | **requests 直连 PostgREST** | 零新依赖，符合 `notify.py` 轻量风格；supabase-py 传递依赖多、breaking change 风险高 |
| 认证 | **新式 API Key**（`sb_secret_` / `sb_publishable_`） | secret key 绕过 RLS，等效旧 service_role |
| 测试 | pytest + unittest.mock | 与现有 `wyckoff/tests/` 一致 |

## 4. Supabase 服务端配置

### 4.1 项目信息

| 配置项 | 值 | 存放位置 |
| --- | --- | --- |
| `SUPABASE_URL` | `https://ijhvdkqcxcidofrpgenf.supabase.co` | GitHub Secrets（非敏感，但统一管理） |
| `SUPABASE_SECRET_KEY` | `sb_secret_****`（**只进 GitHub Secrets，严禁入库**） | GitHub Secrets |
| `SUPABASE_PUBLISHABLE_KEY` | `sb_publishable_****` | 本功能**不使用**（publishable key 受 RLS 约束，服务端场景用不到） |
| `SUPABASE_JWKS_URL` | `.../auth/v1/.well-known/jwks.json` | 本功能**不使用**（JWKS 用于 JWT 验签场景，REST 服务端调用无需） |

> ⚠️ secret key 曾在对话中明文出现，上线前应到 **Dashboard → Settings → API Keys** 轮换一次。

### 4.2 认证方式（每个 REST 请求）

```
GET/POST {SUPABASE_URL}/rest/v1/<table>
apikey: <SUPABASE_SECRET_KEY>
Authorization: Bearer <SUPABASE_SECRET_KEY>
Content-Type: application/json
Prefer: resolution=merge-duplicates        # upsert 时
```

### 4.3 安全基线（建表 SQL 内置，见 §5）

- 三张表全部 `enable row level security` 且**不建任何 policy** → 匿名/publishable key 不可读写；secret key 绕过 RLS 不受影响。
- secret key 只经 GitHub Secrets → `env:` 注入，不进 `run:` 内插（同时修复上轮审查 O1）。

## 5. 数据模型（在 Supabase SQL Editor 执行一次）

```sql
-- 每次扫描一行（同一天重试产生多行，保留完整运行历史）
create table scan_runs (
  id bigint generated always as identity primary key,
  trade_date date not null,
  status text not null default 'running',   -- running / success / failed / degraded
  total_stocks int not null default 0,
  scanned_count int not null default 0,
  skipped_count int not null default 0,     -- 断点续扫跳过的数量
  signal_count int not null default 0,
  failed_count int not null default 0,      -- 拉数失败的股票数
  started_at timestamptz not null default now(),
  finished_at timestamptz
);

-- 断点续扫核心表：每股进度
create table scan_progress (
  trade_date date not null,
  code text not null,
  status text not null,                     -- done / failed（failed 重试时重扫）
  scanned_at timestamptz not null default now(),
  primary key (trade_date, code)
);

-- LPS 信号历史（永久保留）
create table lps_signals (
  trade_date date not null,
  code text not null,
  name text,
  close numeric, ma20 numeric,
  vol_ratio numeric, deviation_pct numeric,
  is_spring boolean not null default false,   -- 信号日前 30 交易日内出现过 Spring
  spring_date date,                           -- 最近一次 Spring 日期（无则 NULL）
  spring_strength numeric,                    -- Spring 强度 0-1（无则 NULL）
  created_at timestamptz not null default now(),
  primary key (trade_date, code)
);

-- 安全基线：RLS 全开、零 policy
alter table scan_runs     enable row level security;
alter table scan_progress enable row level security;
alter table lps_signals   enable row level security;

-- 运行历史保留 90 天（可选的手动清理，见 §6 purge）
create index on scan_progress (trade_date);
create index on scan_runs (trade_date);
```

**数据量估算**：`scan_progress` 3,200 行/天 × 保留 14 天 ≈ 4.5 万行；`lps_signals` 几十~几百行/天。总计 < 10MB，免费版 500MB 上限无压力。

## 6. 断点续扫逻辑（`mainboard_scanner.py` 改动）

1. **启动时**：`GET /rest/v1/scan_progress?select=code&trade_date=eq.<今日>&status=eq.done` → done 集合；循环中命中即跳过（`failed` 的重扫）。附带 `DELETE /rest/v1/scan_progress?trade_date=lt.<今日-14天>` 清理过期进度。
2. **增量写入**：进度/信号在内存攒批，**每 50 只或 60s** flush 一次 upsert（`Prefer: resolution=merge-duplicates`）。超时强杀最多丢 50 只进度。
3. **运行登记**：开始 insert `scan_runs(status=running)`；结束（含异常）PATCH 统计与最终状态。
4. 信号 JSON 照旧写本地（兜底），DB 为主。

### 容错语义

| 场景 | 行为 |
| --- | --- |
| flush 失败 | 2 次指数退避重试；仍失败则继续扫描不中断，run 标记 `degraded`，结束时 ntfy 告警 |
| 启动时 DB 不可达 | **fail fast 退出**（持久化是本功能核心，静默降级=白扫一小时） |
| 重试同一交易日 | `workflow_dispatch` 手动触发，断点续扫自动只补未完成股票 |

## 7. 模块设计

新增 `wyckoff/supabase_client.py`（与 `notify.py` 同级、同轻量风格）：

```python
class SupabaseClient:
    """读环境变量 SUPABASE_URL / SUPABASE_SECRET_KEY"""
    def insert_run(trade_date) -> int              # 返回 run_id
    def finish_run(run_id, stats, status)           # PATCH 收尾
    def load_done_codes(trade_date) -> set[str]     # 断点续扫
    def upsert_progress(rows) / upsert_signals(rows)
    def purge_old_progress(keep_days=14)
```

- 纯 `requests`，每请求 2 次指数退避重试（4xx 不重试）
- `run_scan()` 改为可注入 client 与数据源（默认值不变，向后兼容）；现有 `AkShareSource()` 写死的问题借此一并参数化

## 8. Workflow 改动（`wyckoff-signal-scan.yml`）

```yaml
permissions:
  contents: read        # 收紧（原 contents: write 无使用方）
timeout-minutes: 180    # 配合断点续扫
# scan 步骤 env 新增:
#   SUPABASE_URL / SUPABASE_SECRET_KEY ← secrets
#   DAYS / LIMIT / TRADE_DATE 经 env 传入（不再 ${{ }} 内插进 run:）
# upload 步骤新增:
#   if-no-files-found: ignore
```

不引入 workflow 自动 re-run（YAGNI）：失败 → ntfy 告警 → 人工 dispatch 重试，断点续扫保证重试成本分钟级。

## 9. Project Structure（新增/改动）

```
wyckoff/
├── supabase_client.py          # 新增
├── mainboard_scanner.py        # 改动：断点续扫 + 攒批 flush + run 登记
└── tests/
    └── test_supabase_client.py # 新增：mock requests.Session
.github/workflows/wyckoff-signal-scan.yml  # 改动：见 §8
```

## 10. Testing Strategy

| 层 | 内容 | 方式 |
| --- | --- | --- |
| 单元：client | payload 构造、upsert 幂等头、退避重试、4xx 不重试 | mock `requests.Session`，无真实 DB |
| 单元：断点续扫 | done 跳过 / failed 重扫 / flush 攒批 / degraded 标记 / DB 不可达 fail fast | mock client + 假数据源 |
| 集成冒烟 | 真实 Supabase，`--limit 5` 跑两遍 | 第二遍日志应显示"跳过 5 只"；SQL Editor 查行数 |
| CI | 单元测试随 push 跑，不依赖 secrets | 无 secrets 时照常通过 |

## 11. Boundaries

### Always
- secret key 只存 GitHub Secrets，经 `env:` 注入
- 三张表 RLS 全开、零 policy
- 进度/信号必须攒批增量写入（不允许只在结尾一次性写）
- `requests.Session.trust_env = False`（沿袭 [SPEC.md §16](file:///Users/wingo.wen/Documents/WorkSpace/stock-automation/wyckoff/docs/system/SPEC.md) macOS 代理约定）

### Ask First
- 表结构变更（本 SPEC §5 之外的列/索引）
- 免费额度告警阈值与清理策略调整（当前：progress 14 天、runs 90 天）
- 引入 supabase-py 或其他新依赖

### Never
- secret key / 任何凭证提交到版本控制
- 用 publishable key 做服务端写入
- DB 不可达时静默降级为"无持久化扫描"

## 12. Success Criteria

- 模拟超时：`--limit 300` 扫到一半 Ctrl-C，重新运行后日志显示跳过已完成部分，总耗时显著小于全量重扫
- 信号历史：`select * from lps_signals order by trade_date desc` 可查任意历史信号
- 无信号日：workflow 绿色，无 "No files were found" 告警（`if-no-files-found: ignore`）
- 单元测试全绿，且不依赖 secrets

## 13. Open Questions

（无——入库范围、SDK 选型、项目配置均已确认。）

## 14. 评审记录

| 日期 | 评审人 | 状态 | 备注 |
| --- | --- | --- | --- |
| 2026-08-15 | AI v1.0 草稿 | 待评审 | 方案 A（直连 REST）已确认；建议评审后轮换 secret key |
