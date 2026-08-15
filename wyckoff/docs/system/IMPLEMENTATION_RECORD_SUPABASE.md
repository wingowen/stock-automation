# Supabase 断点续扫实施记录（2026-08-15）

> 配套设计文档：[SPEC_SUPABASE_PERSISTENCE.md](SPEC_SUPABASE_PERSISTENCE.md)
> 本次实施：workflow 审查修复 + Supabase 持久化 + 断点续扫 + Spring 标注，commit `b5cc1cc` 及后续

## 1. 背景与动机

对 `.github/workflows/wyckoff-signal-scan.yml` 审查发现核心矛盾：

| 问题 | 严重度 | 说明 |
|---|---|---|
| timeout 30min 装不下全市场扫描 | Critical | ~3100 只 × (拉取+0.5s 限流+重试) ≈ 60-90min，定时触发必然超时，且结果在循环结束后才落盘 → 超时即全部作废 |
| 无信号日 artifact 恒为空 | Required | upload-artifact v4+ 默认排除隐藏文件，`.gitkeep` 不上传 |
| `contents: write` 过度授权 | Required | 全流程无 git 写操作 |
| secrets/inputs 内插进 shell | Consider | 改经 `env:` 传入 |

**决策**：引入免费 Supabase 做执行状态持久化——失败重试时跳过已完成股票（断点续扫），超时/中断不再作废；顺带获得信号历史库。

## 2. 方案设计要点（已确认）

- **三张表**：`scan_runs`（运行登记）/ `scan_progress`（每股进度，断点续扫核心）/ `lps_signals`（信号历史，永久保留）
- **断点续扫语义**：启动时加载当日 `status=done` 的股票集合直接跳过；`failed` 的重扫
- **攒批 flush**：50 只或 60s 阈值，超时强杀最多丢一批进度
- **容错分级**：
  - flush 失败 → 继续扫描，run 标记 `degraded`，结束 ntfy 告警，exit 1 让 CI 标红
  - 启动时 DB 不可达 → fail fast 直接退出（核心目的就是可靠持久化，静默降级更糟）
- **安全基线**：三表 RLS 全开 + 零 policy（secret key 绕过 RLS 正常读写，匿名 key 不可读写——已实测验证）

## 3. 实施内容

### 3.1 新增文件

| 文件 | 职责 |
|---|---|
| `wyckoff/supabase_client.py` | PostgREST 薄封装：指数退避重试（4xx 不重试）、分页读取（单页 1000 上限）、幂等 upsert（`resolution=merge-duplicates`）、过期清理（进度 14 天/运行 90 天） |
| `wyckoff/tests/test_supabase_client.py` | 15 个用例：认证头/分页/重试策略/幂等头/env 校验 |
| `wyckoff/tests/test_mainboard_scanner.py` | 12 个用例：断点跳过/failed 重扫/攒批/降级/fail fast/Spring 标注 |

### 3.2 mainboard_scanner.py 改造

- `run_scan()` 注入 `client`（可选持久化）与 `source`（可测数据源），默认 `TencentSource`
- 主循环：跳过 done → 扫描 → 进度/信号攒批 → 阈值触发 flush → 收尾 flush → `finish_run` 上报统计
- 信号 dict 新增 `is_spring` / `spring_date` / `spring_strength`
- ntfy 推送行格式：`000001 平安银行 价=... [Spring✓ 2026-08-05]`
- `main()`：非 dry-run 强制要求 Supabase env（fail fast）；`degraded` 时告警 + exit 1

### 3.3 Spring 标注（新增功能）

- 语义：**标注而非过滤**——LPS 照常全量输出，前 30 个交易日（`SPRING_WINDOW=30`）内出现过 Spring 事件的信号标 `is_spring=true`
- 实现：`_annotate_spring()` 复用 `wyckoff/detectors/spring.py::detect_spring`（跌破支撑→收回→放量），只对触发 LPS 的股票执行（每天几十只，不影响全市场扫速）
- 理论依据：威科夫吸筹结构中 Spring 先于 LPS，"Spring 后的 LPS" 是 Phase D 的高质量回踩确认

### 3.4 workflow 更新

`permissions: contents: read`、`timeout-minutes: 180`、三个 secrets 经 `env:` 传入、inputs 经 `INPUT_*` env 中转、`if-no-files-found: ignore`

### 3.5 DB 变更（需在 Supabase SQL Editor 手工执行）

建表 DDL 见 SPEC §5（本次已执行）；Spring 加列：

```sql
alter table lps_signals
  add column if not exists is_spring boolean not null default false,
  add column if not exists spring_date date,
  add column if not exists spring_strength numeric;
```

## 4. 过程中发现并修复的 3 个存量 bug

1. **`last_lps_date` NameError**（mainboard_scanner）：无 LPS 历史的股票走 debug 日志分支时变量未定义 → 被 except 捕获误记为"扫描失败"。对断点续扫是致命的：这批股票永远 failed、永远重扫。已修（无历史时占位 `"-"`）。
2. **东财接口封锁本地 IP**：akshare 的 `stock_zh_a_hist` 全挂（`RemoteDisconnected`），同参数 curl 200 → 排除 UA/TLS 因素后确认是对 requests 特征或 IP 的间歇性拒绝。**将默认数据源切至 `TencentSource`**——与项目"腾讯为主源"的既有约定一致。
3. **tencent_source 7 列 bug**：腾讯 qfq 数据在除权日行带第 7 列（分红信息 dict），原实现硬编码 6 列名 → 约 53% 股票解析崩溃。已修（`iloc[:, :6]` 截取）。

## 5. 本地验证记录（--limit 100，交易日 2026-08-14）

| 遍次 | 扫描 | 跳过 | 失败 | 信号 | 退出码 |
|---|---|---|---|---|---|
| 1（东财源，被中止） | — | — | — | — | run 留在 `running`（符合设计：被强杀的 run 保留现场） |
| 2（腾讯源，7 列 bug 未修） | 47 | 0 | 53 | 5 | 0 |
| 3（修复后重跑） | **53** | **47** | **0** | 9 | 0 |

第 3 遍完整演示断点续扫：47 只 done 跳过（不请求不 sleep），只补扫 53 只上轮 failed。

DB 核验：`scan_progress` 100 行全 done；`lps_signals` 14 条（5+9，含名称/收盘/MA20/量比）；`scan_runs` 3 条历史如实保留；**publishable key 查询返回 `[]`**（RLS 生效）。全套件（含 pipeline E2E）exit 0。

验证后已清空 08-14 测试数据（三表 DELETE），保证线上首跑是干净全量。

## 6. 部署状态

- [x] Supabase 建表 + RLS + 索引（用户 SQL Editor 执行）
- [x] Spring 三列 ALTER（用户执行）
- [x] GitHub Secrets：`SUPABASE_URL` / `SUPABASE_SECRET_KEY`（gh CLI 写入，2026-08-15）；`NTFY_TOPIC_URL` 远端已有
- [x] 代码提交：`b5cc1cc feat: LPS 扫描接入 Supabase 断点续扫与 Spring 标注`（8 文件 +1395 行）
- [x] `.gitignore` 补 `.env`（本地密钥文件，此前未被忽略）
- [ ] push + 线上全量首跑（~3100 只，预计 60-90min）
- [ ] **轮换 `sb_secret_*`**：该 key 曾在对话/文档中明文出现，上线后应到 Dashboard → Settings → API Keys 轮换，新值更新 GitHub Secrets 与本地 `.env` 即可，代码无需改动

## 7. 运维速查

```sql
-- 实时进度（线上跑时可反复执行）
select status, count(*) from scan_progress where trade_date = '2026-08-14' group by status;
-- 运行历史
select * from scan_runs order by id desc limit 5;
-- Spring 确认的信号
select * from lps_signals where trade_date = '2026-08-14' and is_spring;
```

- 线上失败/超时 → 重新 Run workflow（同交易日）即可，断点续扫只补未完成部分
- 中途强杀的 run 停留在 `running` 状态，属预期（保留现场），不影响续扫
- 数据源再遇封锁：`run_scan(source=...)` 已可注入任意 `DataSource` 实现
