#!/usr/bin/env python3
"""StockExpert 每日看板自动化 —— Web 汇聚 + Agnes AI 研判。

独立脚本，不依赖 CodeBuddy / ZCode 运行时。设计目标：
- 在 GitHub Actions（或本地）每日收盘后运行；
- 抓取公开 Web 财经信息（指数、题材、板块、新闻）；
- 调用 Agnes AI（OpenAI 兼容 API）做综合研判；
- 产出符合 dashboard-contract 的 看板.json，并生成 manifest.json 供前端枚举。

所有判断字段必须带 source + reasoning（全局硬约束）。行情数字缺失填 null，
绝不编造。Agnes 只做"研判/综合"，原始数字来自 Web。

环境变量（GitHub Actions secrets）：
  AGNES_API_KEY    Agnes AI 的 API Key（必填）
  AGNES_BASE_URL   Agnes API 网关，默认 https://api.agnes-ai.com/v1
  AGNES_MODEL      文本模型名，默认 agnes-text（以官网模型目录为准）
  BOCHA_API_KEY    博查 Bocha AI 搜索 Key（选填，用于抓实时 Web 资讯；免费额度）
  BRIEF_ROOT       输出根目录，默认脚本同级目录
  TRADE_DATE       指定交易日（YYYY-MM-DD），默认取最近一个 A股交易日

用法：
  python web_brief.py            # 生成当天看板
  python web_brief.py --dry-run  # 仅打印，不落盘
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# ---------- 配置 ----------
DEFAULT_BASE_URL = "https://api.agnes-ai.com/v1"
DEFAULT_MODEL = "agnes-text"
SCHEMA_VERSION = "1.0"
METHOD = "right-side-core"

# A股粗略交易日历：周一到周五，排除法定节假日。
# 生产环境建议接入交易所日历 API；此处内置 2026 年官方休市日
# （来源：沪深北交易所 2025-12-22 公告，证券时报等转载核对一致）。
# 注意：2027 年及以后需按当年公告补充，否则对应节假日仍会触发生成。
HOLIDAYS = {
    # 元旦
    "2026-01-01", "2026-01-02", "2026-01-03",
    # 春节
    "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
    # 清明节
    "2026-04-04", "2026-04-05", "2026-04-06",
    # 劳动节
    "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
    # 端午节
    "2026-06-19", "2026-06-20", "2026-06-21",
    # 中秋节
    "2026-09-25", "2026-09-26", "2026-09-27",
    # 国庆节
    "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05",
    "2026-10-06", "2026-10-07",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; StockExpertBrief/1.0)",
}

SYSTEM_PROMPT = """你是 A 股右侧短线研究助手。基于用户提供的当日 Web 财经信息（指数、题材、板块、新闻），
按"大盘强度 → 题材四阶段(启动/趋势中/高潮/退潮) → 中军(大容量、好位置、趋势不破) → 3日观察区间"漏斗做研判。

硬性规则：
1. 所有数字必须来自用户提供的 Web 资料，禁止编造任何价格/涨跌幅/市值；资料缺失就标"未获取"。
2. 每个判断必须给出 source（引用资料里的 URL 或"web:用户提供的收评"）和 reasoning（2-5 句因果链）。
3. 不做打板、不追涨停；中军候选只选"未涨停、位置好、趋势不破"的容量票。
4. 输出严格为 JSON，结构见下方 schema，不要任何额外文字。

输出 JSON schema：
{
  "market_regime": {
    "strength": "strong|neutral|weak",
    "strength_reasoning": "...",
    "indices": [{"name":"上证指数","change_pct": -1.85, "price": null, "source":"web:...", "fetched_at":"..."}],
    "breadth_up": 1200, "breadth_down": 3800,
    "breadth_reasoning": "从收评/搜索资料提取的涨跌家数；资料无则填 null 并说明",
    "limit_up": 45, "limit_down": 8,
    "limit_reasoning": "涨停/跌停家数（来自搜索资料）；无则 null"
  },
  "themes": [
    {"name":"题材名","stage":"launching|trending|climax|fading",
     "stage_reasoning":"...","strength":"high|mid|low","strength_reasoning":"...",
     "methods":["right-side-core"],"catalyst":"...","source":"web:..."}
  ],
  "midcaps": [
    {"code":"300251","name":"光线传媒","theme_id":"影视传媒",
     "price": null,"change_pct": 6.46,
     "position_eval":"good|ok|high|avoid","position_reasoning":"...",
     "trend_status":"intact|broken|watch","trend_reasoning":"...",
     "suggested_zone":{"low":12.5,"high":14.0,"reasoning":"..."},
     "source":"web:..."}
  ],
  "data_quality": {"overall":"partial|complete", "missing":["..."]}
}

注意：
- midcaps 的 code 必填（6 位数字，可带 sh/sz/bj 前缀），用于后续自动反查实时行情。
- breadth_up/down、limit_up/down 必须从资料中的具体数字提取，严禁编造；资料无则填 null。
"""


# ---------- 工具 ----------
def log(*a):
    print("[web_brief]", *a, file=sys.stderr, flush=True)


def http_get(url: str, timeout: int = 15) -> str | None:
    """简单 GET，失败返回 None（不抛异常，符合"缺失即标注"）。

    解码策略：腾讯 qt.gtimg.cn、新浪板块等接口返回 GBK 编码，先试 utf-8 再回退
    gbk，避免中文乱码导致指数/板块数字被写成 null（修复 2026-07-17 指数全 null 问题）。
    """
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except Exception as e:  # noqa: BLE001
        log(f"GET 失败 {url}: {e}")
        return None
    # 优先 utf-8（JSON 类接口），失败回退 gbk（腾讯/新浪类接口）
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def is_trade_day(d: dt.date) -> bool:
    if d.weekday() >= 5:  # 周六日
        return False
    if d.strftime("%Y-%m-%d") in HOLIDAYS:
        return False
    return True


def latest_trade_day(from_date: dt.date | None = None) -> dt.date:
    d = from_date or dt.date.today()
    while not is_trade_day(d):
        d -= dt.timedelta(days=1)
    return d


def call_agnes(system: str, user: str) -> str | None:
    """调用 Agnes（OpenAI 兼容）。返回模型文本，失败返回 None。

    增加单次重试与更长超时：免费 API 常冷启动慢。
    """
    api_key = os.environ.get("AGNES_API_KEY")
    if not api_key:
        log("AGNES_API_KEY 未设置，无法调用模型")
        return None
    base_url = os.environ.get("AGNES_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("AGNES_MODEL", DEFAULT_MODEL)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    url = f"{base_url}/chat/completions"
    last_err = None
    for attempt in (1, 2):
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": HEADERS["User-Agent"],
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                resp = json.loads(r.read().decode("utf-8"))
            return resp["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"Agnes 调用失败(第{attempt}次): {e}")
    return None


def call_bocha(query: str, count: int = 8) -> list[dict] | None:
    """调用博查 Bocha AI Web 搜索，返回 [{title,url,snippet}] 或 None。

    免费额度搜索接口，用于在 CI 环境抓取实时 A股收评/题材资讯，
    弥补东财 push2 接口在 CI 常返回空的问题。失败降级返回 None。
    """
    api_key = os.environ.get("BOCHA_API_KEY")
    if not api_key:
        log("BOCHA_API_KEY 未设置，跳过博查搜索")
        return None
    url = "https://api.bochaai.com/v1/web-search"
    payload = {"query": query, "count": count, "freshness": "noLimit", "summary": True}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read().decode("utf-8"))
        items = (resp.get("data") or {}).get("webPages", {}).get("value") or []
        out = []
        for it in items[:count]:
            out.append({
                "title": it.get("name", ""),
                "url": it.get("url", ""),
                "snippet": (it.get("snippet") or it.get("summary") or ""),
            })
        log(f"博查搜索 '{query[:20]}...' 命中 {len(out)} 条")
        return out
    except Exception as e:  # noqa: BLE001
        log(f"博查搜索失败: {e}")
        return None


def _parse_tencent_quote(seg: str) -> dict | None:
    """解析腾讯 qt.gtimg.cn 单条行情。

    格式示例：v_sz000001="1~平安银行~000001~12.34~12.50~12.40~..."
    按 ~ 切分后关键下标（腾讯标准）：
      [1]=名称 [2]=代码 [3]=当前价 [4]=昨收 [5]=今开
      [31]=涨跌额 [32]=涨跌幅(%) [33]=振幅(%) [34]=换手(%) [37]=最高 [38]=最低
    返回 {name,code,price,change_pct} 或 None。
    """
    if "=" not in seg:
        return None
    rhs = seg.split("=", 1)[1].strip().strip('"')
    parts = rhs.split("~")
    if len(parts) < 33:
        return None
    try:
        name = parts[1]
        code = parts[2]
        price = float(parts[3])
        change_pct = float(parts[32]) if parts[32] not in ("", "-") else None
    except (ValueError, IndexError):
        return None
    return {"name": name, "code": code, "price": price, "change_pct": change_pct}


def build_web_context(trade_date: dt.date) -> tuple[str, list[dict], list[dict]]:
    """抓取公开 Web 财经信息，拼接成给模型的上下文。

    这里用公开可访问的财经页面做轻量抓取。若某源失败，标注缺失，不阻塞。
    返回 (context_text, data_sources, structured_indices)
      - structured_indices: 结构化指数硬数字（name/code/price/change_pct/source），
        直接回填看板，不依赖模型回读，避免行情数字丢失。
    """
    ds: list[dict] = []
    parts: list[str] = []
    idx_struct: list[dict] = []
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()

    # 1) 指数/市场 —— 腾讯行情接口（CI 友好）优先，东财 push2 备用
    tencent_url = "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
    ttxt = http_get(tencent_url)
    if ttxt:
        for seg in ttxt.split(";"):
            seg = seg.strip()
            if not seg:
                continue
            rec = _parse_tencent_quote(seg)
            if not rec:
                continue
            idx_struct.append({
                "name": rec["name"], "code": rec["code"],
                "price": rec["price"], "change_pct": rec["change_pct"],
                "source": f"web:{tencent_url}", "fetched_at": now,
            })
            parts.append(f"【指数·腾讯】{rec['name']}({rec['code']}) 现价={rec['price']} 涨跌幅={rec['change_pct']}%")
        if idx_struct:
            ds.append({"type": "web", "url": tencent_url, "fetched_at": now})
    # 若腾讯没拿到，再试东财
    if not idx_struct:
        em_url = "https://push2.eastmoney.com/api/qt/clist/get?fs=m:1+t:2,m:0+t:6,m:0+t:80,m:1+t:23&fields=f12,f14,f2,f3,f104,f105"
        txt = http_get(em_url)
        if txt:
            try:
                j = json.loads(txt)
                data = j.get("data") if isinstance(j.get("data"), dict) else {}
                items = data.get("diff") or []
                if isinstance(items, list):
                    for it in items[:12]:
                        if isinstance(it, dict):
                            try:
                                price = float(it["f2"]) if it.get("f2") not in (None, "-", "") else None
                                change_pct = float(it["f3"]) if it.get("f3") not in (None, "-", "") else None
                            except (ValueError, TypeError):
                                price, change_pct = None, None
                            idx_struct.append({
                                "name": it.get("f14"), "code": it.get("f12"),
                                "price": price, "change_pct": change_pct,
                                "source": f"web:{em_url}", "fetched_at": now,
                            })
                            parts.append(f"【指数·东财】{it.get('f14')}({it.get('f12')}) 现价={price} 涨跌幅={change_pct}%")
                if idx_struct:
                    ds.append({"type": "web", "url": em_url, "fetched_at": now})
                else:
                    parts.append("【指数/市场】接口返回空，未获取")
            except Exception as e:  # noqa: BLE001
                log(f"东财解析失败: {e}")
                parts.append("【指数/市场】解析失败，未获取")
        else:
            parts.append("【指数/市场】未获取（网络/接口失败）")

    # 2) 行业板块名录（新浪行业板块接口，CI 友好；返回板块名+成分股数，不含涨幅）
    sector_url = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
    stxt = http_get(sector_url)
    sector_names: list[str] = []
    if stxt:
        try:
            # 形如： var S_Finance_bankuai_sinaindustry = {"new_blhy":"new_blhy,玻璃行业,19,0,...", ...}
            body = stxt.split("{", 1)[-1].rstrip("}; \n")
            for seg in body.split(","):
                # 每段 key:"key,名称,公司数,..." 取名称（第2个逗号字段前）
                if ":" in seg:
                    val = seg.split(":", 1)[1].strip('"')
                    cols = val.split(",")
                    if len(cols) >= 2:
                        sector_names.append(cols[1])
            if sector_names:
                # 最多展示 30 个板块名，给模型题材归类参考
                shown = "、".join(sector_names[:30])
                parts.append(f"【行业板块名录·新浪】共 {len(sector_names)} 个行业板块，示例：{shown}")
                ds.append({"type": "web", "url": sector_url, "fetched_at": now})
            else:
                parts.append("【行业板块】解析为空，未获取")
        except Exception as e:  # noqa: BLE001
            log(f"新浪板块解析失败: {e}")
            parts.append("【行业板块】解析失败，未获取")
    else:
        parts.append("【行业板块】未获取（网络失败）")

    # 3) 盘中快讯（东财快讯列表接口）
    news_api = "https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1"
    ntxt = http_get(news_api)
    if ntxt:
        ds.append({"type": "web", "url": news_api, "fetched_at": now})
        parts.append("【盘中快讯】已抓取东财公开接口（见数据源）")
    else:
        parts.append("【盘中快讯】未获取")

    # 4) 博查实时搜索：A股当日收评 + 题材热点 + 涨跌家数/涨停 + 板块涨幅（免费额度，CI 友好）
    for q in (
        f"{trade_date.isoformat()} A股 收评 大盘 题材",
        f"{trade_date.isoformat()} A股 热点板块 涨停 复盘",
        f"{trade_date.isoformat()} A股 上涨 下跌 家数 涨停 跌停 数量",
        f"{trade_date.isoformat()} A股 行业板块 涨幅 排行",
    ):
        results = call_bocha(q, count=6)
        if results:
            block = [f"【Web资讯·博查】{q}"]
            for r in results:
                snippet = (r.get("snippet") or "").strip().replace("\n", " ")
                block.append(f"- {r.get('title')}\n  {snippet}\n  来源: {r.get('url')}")
                ds.append({"type": "web", "url": r.get("url", ""), "fetched_at": now})
            parts.append("\n".join(block))

    context = (
        f"交易日：{trade_date.isoformat()}\n"
        "以下为自动抓取的公开 Web 财经信息（可能不完整，缺失处请标'未获取'）：\n\n"
        + "\n\n".join(parts)
        + "\n\n请基于以上信息完成研判，严格按 schema 输出 JSON。"
    )
    return context, ds, idx_struct


def generate_brief(trade_date: dt.date, dry_run: bool) -> int:
    log(f"生成看板：{trade_date.isoformat()} dry_run={dry_run}")
    context, ds, idx_struct = build_web_context(trade_date)
    raw = call_agnes(SYSTEM_PROMPT, context)

    root = Path(os.environ.get("BRIEF_ROOT", Path(__file__).resolve().parent))
    data_dir = root / "dashboard" / "data" / trade_date.strftime("%Y-%m") / trade_date.strftime("%Y-%m-%d")
    data_dir.mkdir(parents=True, exist_ok=True)

    if not raw:
        log("模型未返回，写入降级看板（标注数据不可用），但保留 Web 上下文原文")
        # 模型挂了也不丢抓取结果：落盘原始上下文，便于人工查看当天资讯
        (data_dir / "web_context.txt").write_text(context, encoding="utf-8")
        payload = fallback_payload(trade_date, ds, idx_struct)
    else:
        try:
            model_json = json.loads(raw)
            # 用模型给的个股 code 反查腾讯实时行情，回填硬数字
            mc_codes = [c.get("code") for c in model_json.get("midcaps", []) if c.get("code")]
            stock_quotes = _fetch_tencent_stocks(mc_codes) if mc_codes else {}
            if mc_codes:
                log(f"反查个股行情 {len(stock_quotes)}/{len(mc_codes)} 成功")
            payload = assemble(trade_date, model_json, ds, idx_struct, stock_quotes)
        except Exception as e:  # noqa: BLE001
            log(f"模型输出解析失败: {e}，写入降级看板")
            payload = fallback_payload(trade_date, ds)

    if dry_run:
        log("DRY-RUN：不落盘，打印 JSON 预览：")
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:3000])
        return 0

    out = data_dir / "看板.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"已写入 {out}")

    # 重新生成 manifest
    write_manifest(root)
    log("完成")
    return 0


def _norm_code(code: str) -> str:
    """规范化 6 位代码为腾讯 qt 前缀格式（sh/sz/bj）。未知市场默认 sh。"""
    c = str(code).strip().lower()
    c = c.lstrip("shszbj.").replace(".", "")
    if len(c) != 6 or not c.isdigit():
        return ""
    if c.startswith(("60", "68", "90", "58", "56", "11", "113")):
        prefix = "sh"
    elif c.startswith(("00", "30", "20", "39", "15", "12", "123")):
        prefix = "sz"
    elif c.startswith(("8", "4", "92")):
        prefix = "bj"
    else:
        prefix = "sh"
    return f"{prefix}{c}"


def _fetch_tencent_stocks(codes: list[str]) -> dict[str, dict]:
    """批量查腾讯个股行情，返回 {raw_code: {price,change_pct,market_cap,source}}。

    腾讯 qt.gtimg.cn 在 CI 网络可用。字段下标（个股）：
      [3]=现价 [33]=涨跌幅(%) [45]=总市值(亿元) [19]=流通市值? 实际 [44]流通市值 [45]总市值
    失败返回空 dict（不阻塞）。
    """
    norm = {c: _norm_code(c) for c in codes if _norm_code(c)}
    if not norm:
        return {}
    q = ",".join(norm.values())
    txt = http_get(f"https://qt.gtimg.cn/q={q}")
    out: dict[str, dict] = {}
    if not txt:
        return out
    for seg in txt.split(";"):
        seg = seg.strip()
        if "=" not in seg:
            continue
        rhs = seg.split("=", 1)[1].strip().strip('"')
        parts = rhs.split("~")
        if len(parts) < 46:
            continue
        tcode = parts[2]  # 腾讯返回的纯数字代码
        # 反查原始 code
        orig = next((k for k, v in norm.items() if v.endswith(tcode)), tcode)
        try:
            price = float(parts[3])
            change_pct = float(parts[33]) if parts[33] not in ("", "-") else None
            mcap = float(parts[45]) if parts[45] not in ("", "-") else None
        except (ValueError, IndexError):
            continue
        out[orig] = {
            "price": price,
            "change_pct": change_pct,
            "market_cap": mcap,  # 亿元
            "source": "web:https://qt.gtimg.cn",
        }
    return out


def assemble(trade_date: dt.date, mj: dict, ds: list[dict], idx_struct: list[dict] | None = None, stock_quotes: dict[str, dict] | None = None) -> dict:
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()
    mr = mj.get("market_regime", {})
    # 优先用结构化抓取的硬数字（price/change_pct 来自真实接口），
    # 模型的 indices 仅作兜底/补充；避免行情数字因模型未回读而丢失。
    if idx_struct:
        indices = [
            {
                "name": ix.get("name"),
                "code": ix.get("code"),
                "price": ix.get("price"),
                "change_pct": ix.get("change_pct"),
                "source": ix.get("source", "web:eastmoney"),
                "fetched_at": ix.get("fetched_at", now),
            }
            for ix in idx_struct
        ]
    else:
        indices = []
        for ix in mr.get("indices", []) or []:
            indices.append({
                "name": ix.get("name"),
                "code": ix.get("code"),
                "price": ix.get("price"),
                "change_pct": ix.get("change_pct"),
                "source": ix.get("source", "web:eastmoney"),
                "fetched_at": ix.get("fetched_at", now),
            })
    payload = {
        "schema_version": SCHEMA_VERSION,
        "trade_date": trade_date.isoformat(),
        "generated_at": now,
        "is_demo": False,
        "market_regime": {
            "strength": mr.get("strength", "neutral"),
            "strength_source": "ai-synthesis",
            "strength_reasoning": mr.get("strength_reasoning", "未获取"),
            "indices": indices,
            "breadth_up": mr.get("breadth_up"),
            "breadth_down": mr.get("breadth_down"),
            "breadth_reasoning": mr.get("breadth_reasoning", ""),
            "limit_up": mr.get("limit_up"),
            "limit_down": mr.get("limit_down"),
        },
        "themes": [
            {
                "id": f"{t.get('name','theme')}-{trade_date.isoformat()}",
                "name": t.get("name"),
                "stage": t.get("stage", "fading"),
                "stage_source": "ai-synthesis",
                "stage_reasoning": t.get("stage_reasoning", "未获取"),
                "strength": t.get("strength", "low"),
                "strength_reasoning": t.get("strength_reasoning", "未获取"),
                "methods": t.get("methods", [METHOD]),
                "signals": [],
                "related_midcap_codes": [c.get("code") for c in mj.get("midcaps", []) if c.get("theme_id") == t.get("name")],
                "catalyst": t.get("catalyst", ""),
                "source": t.get("source", "web:用户提供的收评"),
                "notes": "",
            }
            for t in mj.get("themes", []) or []
        ],
        "midcaps": [
            {
                "code": c.get("code"),
                "name": c.get("name"),
                "theme_id": c.get("theme_id"),
                # 行情硬数字优先用腾讯反查结果，模型给的仅作缺失兜底
                "market_cap": (stock_quotes or {}).get(c.get("code"), {}).get("market_cap", c.get("market_cap")),
                "price": (stock_quotes or {}).get(c.get("code"), {}).get("price", c.get("price")),
                "change_pct": (stock_quotes or {}).get(c.get("code"), {}).get("change_pct", c.get("change_pct")),
                "ma5": c.get("ma5"),
                "quote_source": (stock_quotes or {}).get(c.get("code"), {}).get("source", c.get("source", "web:用户提供的收评")),
                "position_eval": c.get("position_eval", "ok"),
                "position_reasoning": c.get("position_reasoning", "未获取"),
                "trend_status": c.get("trend_status", "watch"),
                "trend_reasoning": c.get("trend_reasoning", "未获取"),
                "suggested_zone": c.get("suggested_zone", {"low": None, "high": None, "reasoning": "未获取"}),
                "hold_horizon_days": 3,
                "methods": [METHOD],
                "source": c.get("source", "web:用户提供的收评"),
                "notes": "",
            }
            for c in mj.get("midcaps", []) or []
        ],
        "data_sources": ds,
        # 动态数据质量：基于真实抓取结果标注缺失，不写死
        "data_quality": _build_data_quality(idx_struct, stock_quotes, mj, mr),
        "methods_used": [METHOD],
    }
    # 校验关键枚举与观察区间，非法值回退安全默认并记入数据质量
    issues = _validate_and_sanitize(payload)
    if issues:
        payload["data_quality"]["missing"].extend(issues)
        payload["data_quality"]["overall"] = "partial"
    return payload


def _build_data_quality(idx_struct, stock_quotes, mj, mr) -> dict:
    missing = []
    if not idx_struct:
        missing.append("指数行情未获取（腾讯/东财接口失败）")
    if mr.get("breadth_up") is None or mr.get("breadth_down") is None:
        missing.append("涨跌家数未获取（资料无具体数字）")
    if not (stock_quotes or {}):
        missing.append("个股中军实时行情未获取（无 code 或接口失败）")
    # 模型自报的缺失（如板块涨幅等文字类）
    model_mq = (mj.get("data_quality") or {}).get("missing") or []
    for m in model_mq:
        if m and m not in missing:
            missing.append(m)
    overall = "complete" if not missing else "partial"
    return {"overall": overall, "missing": missing}


# ---------- 校验与清洗 ----------
_ALLOWED_STRENGTH = {"strong", "neutral", "weak", "unknown"}
_ALLOWED_STAGE = {"launching", "trending", "climax", "fading"}
_ALLOWED_POS = {"good", "ok", "high", "avoid"}
_ALLOWED_TREND = {"intact", "broken", "watch"}


def _validate_and_sanitize(payload: dict) -> list[str]:
    """校验关键枚举与观察区间，非法值回退到安全默认，返回问题清单。

    目的：防止模型/上游异常输出污染看板（如 stage 拼错、区间 low>high 等），
    所有问题追加到 data_quality.missing 便于审计。
    """
    issues: list[str] = []
    mr = payload.get("market_regime") or {}
    s = mr.get("strength")
    if s not in _ALLOWED_STRENGTH:
        mr["strength"] = "unknown"
        issues.append(f"market_regime.strength 非法值({s})已置为 unknown")

    for t in payload.get("themes") or []:
        if t.get("stage") not in _ALLOWED_STAGE:
            issues.append(f"题材[{t.get('name')}] stage 非法({t.get('stage')})已置 fading")
            t["stage"] = "fading"

    for c in payload.get("midcaps") or []:
        if c.get("position_eval") not in _ALLOWED_POS:
            issues.append(f"中军[{c.get('code')}] position_eval 非法({c.get('position_eval')})已置 ok")
            c["position_eval"] = "ok"
        if c.get("trend_status") not in _ALLOWED_TREND:
            issues.append(f"中军[{c.get('code')}] trend_status 非法({c.get('trend_status')})已置 watch")
            c["trend_status"] = "watch"
        z = c.get("suggested_zone") or {}
        lo, hi = z.get("low"), z.get("high")
        if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo > hi:
            issues.append(f"中军[{c.get('code')}] 观察区间 low({lo})>high({hi}) 矛盾已置空")
            z["low"], z["high"] = None, None
    return issues


def fallback_payload(trade_date: dt.date, ds: list[dict], idx_struct: list[dict] | None = None) -> dict:
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat()
    indices = [
        {
            "name": ix.get("name"),
            "code": ix.get("code"),
            "price": ix.get("price"),
            "change_pct": ix.get("change_pct"),
            "source": ix.get("source", "web:eastmoney"),
            "fetched_at": ix.get("fetched_at", now),
        }
        for ix in (idx_struct or [])
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "trade_date": trade_date.isoformat(),
        "generated_at": now,
        "is_demo": False,
        "market_regime": {
            "strength": "unknown",
            "strength_source": "ai-synthesis",
            "strength_reasoning": "模型调用失败，研判不可用；指数硬数字（如有）仍来自行情接口。",
            "indices": indices,
            "breadth_up": None,
            "breadth_down": None,
            "breadth_reasoning": "未获取",
        },
        "themes": [],
        "midcaps": [],
        "data_sources": ds,
        "data_quality": {"overall": "unavailable", "missing": ["Agnes 模型调用失败，全天数据不可用"]},
        "methods_used": [METHOD],
    }


def write_manifest(root: Path) -> None:
    """扫描 dashboard/data/*/*/看板.json，生成 manifest.json（前端静态枚举用）。"""
    data_root = root / "dashboard" / "data"
    days = []
    if data_root.exists():
        for month_dir in sorted(data_root.iterdir(), reverse=True):
            if not month_dir.is_dir():
                continue
            for day_dir in sorted(month_dir.iterdir(), reverse=True):
                jf = day_dir / "看板.json"
                if jf.exists():
                    try:
                        with jf.open(encoding="utf-8") as fh:
                            p = json.load(fh)
                        rel = jf.relative_to(root / "dashboard").as_posix()
                        days.append({
                            "trade_date": p.get("trade_date"),
                            "path": rel,  # 相对于 dashboard/ 的路径
                            "regime": (p.get("market_regime") or {}).get("strength", ""),
                            "theme_count": len(p.get("themes") or []),
                            "is_demo": bool(p.get("is_demo", False)),
                        })
                    except Exception as e:  # noqa: BLE001
                        log(f"manifest 解析失败 {jf}: {e}")
    manifest = {"generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(), "days": days}
    (root / "dashboard" / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    log(f"manifest 写入，共 {len(days)} 天")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="仅打印不落盘")
    ap.add_argument("--trade-date", help="指定交易日 YYYY-MM-DD")
    args = ap.parse_args()

    if args.trade_date:
        try:
            td = dt.datetime.strptime(args.trade_date, "%Y-%m-%d").date()
        except ValueError:
            log("trade_date 格式错误")
            return 2
        if not is_trade_day(td):
            log(f"{td} 非交易日，跳过")
            return 0
    else:
        td = latest_trade_day()
    return generate_brief(td, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
