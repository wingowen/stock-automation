# Spec: 威科夫信号二次过滤 Action（扫描后二次过滤）

> 版本：v0.2（设计态，未实现；v0.2 为评审修订：内联阈值、输入容错、CI 修正）
> 日期：2026-08-16
> 关联：`.github/workflows/wyckoff-signal-scan.yml`（一次扫描）
> 关联指标定义源：`daily_stock_analysis/docs/wyckoff-secondary-filter-spec.md`（编写时在仓库内**不可达**，H/I 量化口径已按检测层既有阈值与威科夫语义内联至 §4.5，待回测校准）
> 状态：仅 spec，待评审与实现

---

## 0. 假设（实现前请确认，错误或我按此推进）

1. **集成形态**：在 `wyckoff-signal-scan.yml` 同一 workflow 内新增 `filter` job，`needs: [scan]`，消费扫描上传的 artifact（或读 Supabase `lps_signals`）。不新建独立 workflow。
2. **过滤范围（v0.1）**：硬过滤 `H2/H3/H4` + 评分 `I1/I2/I4` + 部分 `I7`。**不含** `H1/I3`（phase 阶段分类，需新模块）与 `I6`（跟随确认需 T+1~5 未来数据，实时每日流不可用）。
3. **指标口径来源**：design-spec 文档不可达；H/I 的量化分桶与硬过滤阈值**内联于本 spec §4.5**，依据检测层既有阈值（SOS 量比>1.5 / LPS 量比<0.8 / 偏离≤2% / spring_strength∈[0,1]）与威科夫语义自定，全部为**推荐默认值，投产前需回测校准**。
4. **原始数据再取**：scan 仅输出聚合信号，不含原始量价序列。二次过滤对**当日信号子集**（数量小）经 `wyckoff.data` 重新拉取近 120 日 OHLCV，自算缺失字段（MA20_VOL / amount 近似 / demand_ratio / ATR / drawdown / big_trend）。重拉失败与旧文件缺字段的降级规则见 **§5.5**。
5. **成交额近似**：腾讯源不返回 `amount`，流动性 `avg_amount_20` 用 `close × volume × 100` 近似（项目 volume 统一单位为**手**，见 `wyckoff/data/base.py normalize_ohlcv`），并在报告显式标注"近似"。
6. **权重**：v0.1 仅 4 个可用维度（I1/I2/I4/I7），综合分权重在其上**重归一化到 1.0**（见 §4），不在可用维度上强加 design-spec 全量权重。
7. **产出与推送**：写 `wyckoff/filtered_results/filtered_<date>.json` + 上传 artifact + ntfy 推送 A/B 档。v0.1 不写 Supabase（留 `lps_filtered_signals` 表待 v0.2）。**推送决策：每日共两条**——scan 概要推送（现状保留，不改 scanner）+ filter 详情推送（A/B 档）；合并为一条留待后续版本（见 §12）。filter 的 CI env 不传 Supabase secrets（v0.1 不读不写）。

---

## 1. Objective

一次扫描（`wyckoff.mainboard_scanner`）在全主板盘后产出 SOS→LPS 组合信号，数量可能较大、质量参差。
本 spec 在扫描**之后**新增一个"二次过滤" action，对满足信号的标的做：

- **硬过滤（一票否决）**：H2 事件存在 / H3 量能底线 / H4 主板+流动性；
- **加权评分（软排序）**：I1 量价确认 / I2 供求偏度 / I4 事件质量 / 部分 I7 背景趋势；
- **分档与数量控制**：A 档（强）→ 交易优先池，B 档（观察）→ 观察池，C 档剔除；A 档超 `MAX_POOL`(15) 按综合分降序截断。

目标：把"初筛信号池"精简为 **8–20 只高质量标的**，通过 ntfy 推送 A/B 档，降低盯盘与误信号成本。

成功画面：每日 19:00（北京时间）触发扫描 job（全市场约 60–90 分钟），**扫描 job 结束后数分钟内**（约 20:00–20:35）收到第二条"威科夫 LPS 二次过滤"推送，仅含经评分后的 A/B 档标的（含综合分与各维度分），而非全部原始信号（第一条为 scan 概要推送，现状保留）。

---

## 2. Tech Stack

- 语言：Python 3.11（与扫描一致）
- 运行时依赖：复用现有 `pandas`（计算）、`requests`/akshare（数据源）；**不新增运行时依赖**（`send_ntfy` 为纯 urllib）。pytest / pytest-cov 为**开发依赖**，仅测试步骤使用
- 数据源：`wyckoff.data.TencentSource`（项目约定腾讯为主源，与扫描一致；volume 单位为手）
- 持久化/推送：复用 `wyckoff.notify.send_ntfy`；Supabase v0.1 不读不写（CI env 不传其 secrets）
- CI：GitHub Actions，`actions/download-artifact@v6` + `actions/upload-artifact@v6`

---

## 3. Commands

```bash
# 本地运行二次过滤（依赖 wyckoff/scan_results/scan_<date>.json 已存在）
python -m wyckoff.secondary_filter --trade-date 2026-08-16
python -m wyckoff.secondary_filter                      # 自动取 scan_results 下最新一份
python -m wyckoff.secondary_filter --dry-run            # 仅打印，不写文件不推送
python -m wyckoff.secondary_filter --limit 10           # 限处理前 10 只【信号】（注意：与 scan 的 --limit 限【股票】数同名不同义，help 需写明）

# 测试
python -m pytest wyckoff/tests/test_secondary_filter.py -v

# CI（在 wyckoff-signal-scan.yml 的 filter job 内）
python -m wyckoff.secondary_filter ${TRADE_DATE:+--trade-date "$TRADE_DATE"}
```

---

## 4. 综合评分与分档（v0.1 重归一化）

原 design-spec 全量权重：`I1 0.20 / I2 0.18 / I4 0.15 / I7 0.10 / (H1 0.15 / I5 0.12 / I6 0.10 本版缺失)`。
v0.1 可用维度之和 = 0.20+0.18+0.15+0.10 = 0.63，重归一化：

| 维度 | design 权重 | v0.1 归一化权重 | 主数据来源（scan 已给 + 自算） |
|------|------------|----------------|-------------------------------|
| I1 量价确认 | 0.20 | **0.317** | SOS/LPS 事件棒量能比 = bar_vol / MA20_VOL（自算） |
| I2 供求偏度 | 0.18 | **0.286** | demand_ratio_10 = Σ涨量/Σ跌量（近10日，自算） |
| I4 事件质量 | 0.15 | **0.238** | is_spring + spring_strength + deviation_pct + penetration/recovery（scan 给 + 自算） |
| I7 背景趋势（部分） | 0.10 | **0.159** | drawdown_from_high + MA60/120 状态（自算） |

```
composite = 0.317·I1 + 0.286·I2 + 0.238·I4 + 0.159·I7     # 各 s_i ∈ [0,10]
```

分档（原 design-spec §6.2）：**A ≥ 7.5 / B 6.0–7.5 / C < 6.0**。
数量控制（原 design-spec §6.3）：取 A 档全集；A > `MAX_POOL`(15) 按 composite 降序取 Top-15；B 默认不进主池（`INCLUDE_B=false`，仅报告）；目标区间 `TARGET_MIN=8 / TARGET_MAX=20`。

### 4.5 阈值与分桶明细（v0.1 推荐默认值）

> 原 design-spec 文档不可达。以下数值依据**检测层既有阈值**（[mainboard_scanner.py](../../../wyckoff/mainboard_scanner.py)：SOS 量比>1.5、LPS 量比<0.8、|偏离|≤2%；[spring.py](../../../wyckoff/detectors/spring.py)：strength∈[0,1] = 0.5×刺穿深度 + 0.5×放量强度）与威科夫语义自定，**全部为推荐默认值，投产前需回测校准**。

**硬过滤（一票否决）**

| 闸门 | 规则 | 依据 |
|------|------|------|
| H2 事件存在 | 恒真（scan 仅输出 SOS→LPS 组合触发） | [mainboard_scanner.py](../../../wyckoff/mainboard_scanner.py) L386 |
| H3a SOS 放量底线 | SOS 棒量 / MA20_VOL ≥ **1.2** | 检测层已要求 SOS 对前 5 日均量 >1.5，此处换 20 日口径给底线 |
| H3b LPS 回踩缩量 | LPS 棒量 / MA20_VOL ≤ **1.5** | 检测层已要求对前 5 日均量 <0.8；20 日口径适度放宽 |
| H4 主板+流动性 | 主板由扫描层保证；`avg_amount_20` ≥ **5000 万元**（amount ≈ close × volume × 100，volume 单位为手） | 流动性底线，近似口径需标注 |

**I1 量价确认（输入：SOS 棒量能比 = SOS 棒量 / MA20_VOL）**

| 比值 | 得分 |
|------|------|
| ≥ 2.0 | 9–10 |
| 1.5 – 2.0 | 7–8 |
| 1.2 – 1.5 | 6 |
| 1.0 – 1.2 | 5 |
| < 1.0 | 0–4（区间内线性内插） |

**I2 供求偏度（输入：demand_ratio_10 = 近 10 日 Σ涨日量 / Σ跌日量）**

| 比值 | 得分 |
|------|------|
| ≥ 1.8 | 9–10 |
| 1.5 – 1.8 | 8 |
| 1.2 – 1.5 | 7 |
| 1.0 – 1.2 | 6 |
| 0.8 – 1.0 | 4–5 |
| < 0.8 | 0–3 |

**I4 事件质量（基础分 + Spring 加成，上限截断 10）**

| 构成 | 规则 |
|------|------|
| 基础分（deviation_pct，越小越好） | ≤1% → 7；1–2% → 6；2–3% → 5；>3% → 4（检测层要求 ≤2%，>2% 属边缘信号） |
| Spring 加成 | `+3 × spring_strength`（strength∈[0,1]） |
| 回收速度 | 有 Spring 且 recovery_bars ≤ 3 → +1 |
| 无 Spring | 仅基础分；penetration 以 deviation 代理，recovery 缺失**不扣分** |

**I7 背景趋势（趋势状态 0–5 + 回撤位置 0–5）**

| 构成 | 规则 |
|------|------|
| big_trend 状态 | MA60 上行（今值 > 5 日前值）且 close ≥ MA60 → 5；MA60 走平（±0.5%）→ 4；MA60 下行但 close ≥ MA120 → 3；MA60 下行且 close < MA120 → 1 |
| drawdown_from_high（120 日，绝对值） | 30–60% → 5（深度回调后低位，典型吸筹背景）；15–30% → 4；60–75% → 3；<15% → 2（高位结构）；>75% → 2 |

> 边界情形（比值恰等于分桶端点、MA60 走平区间判定等）在实现中以"**含下界、不含上界**"统一约定，并在单测中固化为边界用例。

---

## 5. 字段映射（scan 输出 → 指标）

scan 输出 `scan_<date>.json` 的 `signals[]` 每元素已含：

| scan 字段 | 含义 | 直接服务的指标 |
|-----------|------|----------------|
| `code` / `name` | 标的 | H4（主板已在扫描层保证） |
| `signal_date` | 触发日（=LPS 日） | 全部 |
| `close` / `ma20` | 价与 MA20 | H2/I4（收盘相对区间位置）、I7 |
| `vol_ratio` | LPS 棒量比（当日量/前5日均量） | H3/I1（近似；精确用 bar_vol/MA20_VOL） |
| `deviation_pct` | \|close-ma20\|/ma20×100 | H2（收盘收回支撑上方）、I4 |
| `sos_date` | SOS 日 | H2 事件存在、I1（SOS 棒量能） |
| `is_spring` / `spring_date` / `spring_strength` | Spring 标注 | H2 事件质量、I4 |
| `days_since_lps` | 信息性字段（当日信号恒为 0） | 不参与指标，透传到输出 |

**需自算（对信号子集重拉 OHLCV）：**

| 字段 | 用途 | 算法 |
|------|------|------|
| `ma20_vol` | H3/I1/I2 分母 | `volume.rolling(20).mean()` |
| `amount`（≈close×volume×100，手→股） | H4 流动性 | 近似，标注 |
| `avg_amount_20` | H4 | `amount.rolling(20).mean()` |
| `event_bar_vol_ratio` | H3/I1 | LPS 棒量 / ma20_vol；另算 SOS 棒量 / ma20_vol |
| `demand_ratio_10` | I2 | 近10日 Σ涨量 / Σ跌量 |
| `penetration_atr` | I4 | Spring 刺穿深度（ATR 倍数），无 Spring 时用 deviation 代理 |
| `recovery_bars` | I4 | Spring 跌破到收回棒数（有 Spring 时） |
| `drawdown_from_high` | I7 | (close − max(close[-120:])) / max |
| `big_trend` | I7 | MA60/MA120 方向与状态 |

> **H2 在 v0.1 恒为真**：scan 只输出 SOS→LPS 组合触发（裸 LPS 不输出），故"事件存在"必满足；保留 H2 作为闸门仅为前向兼容 design-spec 与未来接入 upthrust 路径。

### 5.5 输入容错与降级（必须实现，单测覆盖）

**A. 旧 scan JSON 缺字段**（如 `scan_2026-08-14.json`，2026-08-15 前生成，无 `sos_date` / `is_spring` 等 Spring 扩展字段）：

| 缺失字段 | 降级规则 |
|----------|----------|
| `is_spring` / `spring_date` / `spring_strength` | 按"无 Spring"处理，I4 走基础分（§4.5） |
| `sos_date` | 无法定位 SOS 棒 → I1 记**中性 5 分**（非 0 分），H3a 跳过、H3b 仍查 |
| 任意缺失 | 输出顶层 `input_degraded: true`；每标的 `missing_fields: [...]`；日志 warning |

**B. 重拉 OHLCV 失败**（对信号子集逐只 fetch）：

1. 重试 1 次（间隔与扫描层一致 `SLEEP_BETWEEN=0.5s`）；
2. 仍失败 → 该标的**仅用 scan 已有字段评分**：自算维度缺失按**中性 5 分**计入，H3/H4 涉及自算输入的闸门跳过（视作通过），标 `data_degraded: true`；**不整只剔除、不整体报错**；
3. 降级标的数 > 30% → 输出顶层 `degraded: true`，ntfy 推送附"数据降级 N/M"警示。

---

## 6. Project Structure

```
.github/workflows/wyckoff-signal-scan.yml   # 改造：末尾追加 filter job（见 §9）
wyckoff/
  secondary_filter.py                        # 新增：二次过滤主模块（本 spec 范围）
  mainboard_scanner.py                       # 已有：一次扫描（不改其输出契约）
  data/                                       # 已有：TencentSource 等（复用）
  notify.py                                   # 已有：send_ntfy（复用）
  scan_results/                              # 已有：scan_<date>.json（filter 输入）
  filtered_results/                          # 新增：filtered_<date>.json（filter 输出）
  tests/test_secondary_filter.py             # 新增：单测
```

---

## 7. Code Style

复用扫描模块风格：模块顶部 docstring 说明职责；函数带 type hints + docstring；私有函数 `_` 前缀；常量大写；错误返回 `Optional` 或抛模块级异常（如 `FilterError`，仿 `SupabaseError` 风格但不依赖 supabase 模块），不静默吞。

```python
# wyckoff/secondary_filter.py（骨架示例）
DEFAULT_MAX_POOL = 15
GRADE_A = 7.5
GRADE_B = 6.0
W_I1, W_I2, W_I4, W_I7 = 0.317, 0.286, 0.238, 0.159  # 归一化权重

def score_signal(sig: dict, df: pd.DataFrame) -> dict:
    """对单个信号计算硬过滤 + 四维评分，返回 {hard_filters, scores, composite, grade, reasons}。"""
    feats = _derive_features(df, sig)            # ma20_vol/amount/demand/atr/drawdown/big_trend
    hard = _hard_filter(sig, feats)             # H2/H3/H4
    if not all(hard.values()):
        return {"hard_filters": hard, "composite": 0.0, "grade": "C",
                "scores": {}, "reasons": ["硬过滤未通过"]}
    s_i = {
        "I1": _score_i1(feats),
        "I2": _score_i2(feats),
        "I4": _score_i4(sig, feats),
        "I7": _score_i7(feats),
    }
    composite = round(W_I1*s_i["I1"] + W_I2*s_i["I2"] + W_I4*s_i["I4"] + W_I7*s_i["I7"], 2)
    grade = "A" if composite >= GRADE_A else "B" if composite >= GRADE_B else "C"
    return {"hard_filters": hard, "scores": s_i, "composite": composite, "grade": grade, "reasons": []}
```

---

## 8. Testing Strategy

- 框架：pytest（复用现有 `wyckoff/tests/`）
- 位置：`wyckoff/tests/test_secondary_filter.py`
- 覆盖（须全绿）：
  1. **字段映射**：构造含 `is_spring/vol_ratio/deviation/sos_date` 的合成信号 + 合成 OHLCV，校验自算字段（ma20_vol/demand_ratio_10/drawdown/amount×100 单位换算）正确。
  2. **硬过滤**：H3a SOS 放量不足 / H3b LPS 缩量不足 → 剔除；H4 流动性不足（amount 近似，5000 万阈值）→ 剔除；H2 恒真。
  3. **评分分桶**：I1/I2/I4/I7 按 §4.5 表逐桶与边界值验证（含下界、不含上界；如 I1 ≥2.0→9–10，1.0–1.2→5）。
  4. **综合分与分档**：权重和=1.0，composite∈[0,10]，A/B/C 阈值正确。
  5. **数量控制**：A 档 >15 时 Top-15 截断；输出含截断标记。
  6. **空信号**：scan_results 无 `scan_<date>.json` 或 signals 为空 → 退出 0、不推送、不报错。
  7. **无前视**：单测断言 `score_signal` 仅读取 `signal_date` 及之前的数据（不引用未来 bar），杜绝实时泄漏。
  8. **输入容错（§5.5）**：缺 `sos_date` → I1 中性 5 分 + `input_degraded`；缺 Spring 字段 → I4 基础分；重拉失败 → 降级评分 + `data_degraded`，不剔除不报错；降级 >30% → 顶层 `degraded`。
- 覆盖率：核心评分/过滤逻辑 ≥ 85%（本地以 pytest-cov 测得——开发依赖，CI 不装不跑覆盖率）。

---

## 9. 集成到 wyckoff-signal-scan.yml（新增 filter job）

在现有 `scan` job 之后追加（同一 workflow，`needs: [scan]`）：

```yaml
  filter:
    needs: [scan]
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - name: 检出代码
        uses: actions/checkout@v5

      - name: 设置 Python 环境
        uses: actions/setup-python@v6
        with:
          python-version: '3.11'
          cache: 'pip'

      - name: 安装依赖
        run: |
          pip install --upgrade pip
          pip install "pandas>=2.2,<3" "akshare>=1.16" "requests>=2.31"

      - name: 运行单测（快速失败，仅合成数据不联网）
        run: |
          pip install pytest
          python -m pytest wyckoff/tests/test_secondary_filter.py -q

      - name: 下载扫描结果
        uses: actions/download-artifact@v6
        with:
          name: wyckoff-scan-results-${{ github.run_number }}
          path: wyckoff/scan_results
          # 注：download-artifact 无 if-no-files-found 输入；scan 侧 upload 已保证
          # 无信号日也会落一份 scan_<date>.json（断点续扫模式下同样写盘），artifact 恒存在

      - name: 运行二次过滤
        id: filter
        env:
          NTFY_TOPIC_URL: ${{ secrets.NTFY_TOPIC_URL }}
          INPUT_TRADE_DATE: ${{ github.event.inputs.trade_date }}
        run: |
          cd ${{ github.workspace }}
          TRADE_DATE="${INPUT_TRADE_DATE:-}"
          python -m wyckoff.secondary_filter ${TRADE_DATE:+--trade-date "$TRADE_DATE"}

      - name: 上传过滤结果
        uses: actions/upload-artifact@v6
        if: always()
        with:
          name: wyckoff-filtered-results-${{ github.run_number }}
          path: wyckoff/filtered_results
          retention-days: 30
          if-no-files-found: ignore
```

> 说明：`permissions: contents: read` 已够（artifact 上传/下载与 ntfy 走 secret，不需 contents 写）。`scan` job 的 `if-no-files-found: ignore` 保证无信号日 artifact 可能为空，filter job 通过"空信号→退出 0"兜底。**scan 失败时 filter 被 skip**（`needs` 默认 `success()` 行为：不运行、灰色、不标红）——这是可接受的：扫描本身失败时无数据可过滤，经 ntfy 的 scan 失败通知（现状）已可感知。env 不传 Supabase secrets（v0.1 不读不写，减少暴露面）。

---

## 10. Boundaries

- **Always**：跑测试后再提交；复用 `wyckoff.data`/`wyckoff.notify` 不改其契约；输入校验（trade_date 格式、signals 字段缺失与重拉失败按 §5.5 降级）；无信号日安静退出 0。
- **Ask first**：改动 `mainboard_scanner` 输出契约；改动 scan job 现有步骤（含其 ntfy 推送行为）；新增 Python 运行时依赖；调整 §4.5 阈值（须附回测依据，不得拍脑袋改）；改动 Supabase 表结构（v0.2 写 `lps_filtered_signals` 前需确认）；改 CI 触发时间。
- **Never**：提交 secret；在评分里引用 `signal_date` 之后的行情（前视泄漏）；为过历史最优反复调参（阈值先用 §4.5 默认值，样本外固定）。

---

## 11. Success Criteria（可验收）

1. `wyckoff-signal-scan.yml` 扫描成功后自动跑 `filter` job，`needs` 依赖生效；**scan 失败 → filter 被 skip**（灰色不标红）；**无信号/空 artifact → filter 运行并安静退出 0**，均不产生红色失败。
2. 有信号日产出 `wyckoff/filtered_results/filtered_<date>.json`，结构含 `composite / grade / scores / hard_filters / reasons`（及 §5.5 的降级标记），且 A 档 ≤ 15（超则截断并标注）。
3. ntfy 推送仅含 A/B 档（C 档剔除），每条带综合分与维度分；每日共两条推送（scan 概要 + filter 详情，§0.7 决策）。
4. CI filter job 内嵌单测步骤，`pytest wyckoff/tests/test_secondary_filter.py` 全绿；核心逻辑覆盖率 ≥ 85%（本地 pytest-cov 测得）。
5. 单测证明评分无前视（仅用触发日及之前数据）。
6. 全量信号 N 只时，filter job 新增耗时可控（仅重拉 N 只信号原始数据，timeout 30min 内）。
7. 输入容错符合 §5.5：旧 JSON 缺字段与个别重拉失败均降级评分不中断；降级 >30% 时推送附警示。

---

## 12. Open Questions（实现前/迭代待定）

1. **phase 分类器（H1/I3）**：v0.2 是否基于 `wyckoff` 现有 Phase 判定（若有）或新建？当前 scan 无 phase 输出。
2. **I6 跟随确认**：实时流不可用（需 T+1~5）；是否做离线 T+5 验证脚本（仿 daily_stock_analysis verify）作为 v0.2 研究项？
3. **Supabase 持久化**：v0.2 是否新增 `lps_filtered_signals` 表 + `SupabaseClient.upsert_filtered_signals`？
4. **权重**：归一化方案（§4）是否接受，还是保留 design-spec 全量权重并将缺失维度按中性 5 分计入？
5. **amount 近似**：腾讯源若后续返回真实 `amount`，是否切换精确值（不影响接口）？
6. **双推送合并**：v0.2 是否将 scan 概要并入 filter 推送头部（改 scanner 推送行为，需 Ask first）？
7. **依赖锁定**：filter job 已加版本范围约束；scan job 的裸版本安装（`pip install pandas akshare requests`）是否同步锁定（独立跟进项，不属本 spec 范围）？
8. **阈值校准**：§4.5 全部默认值需经历史回测校准（尤其 I7 的 drawdown 分桶与 H3a 的 1.2 底线），校准前不得投产实盘决策。

---

⚠️ 本 spec 为方法设计文档，数值为推荐默认值，需经回测与评审确认后方可投产；不构成投资建议。
