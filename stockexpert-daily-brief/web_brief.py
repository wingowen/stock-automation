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

# A股粗略交易日历：周一到周五，排除少数法定节假日（此处仅做基础过滤，
# 生产环境可接入交易所日历 API。非交易日则跳过生成）。
# 简单节假日集合（YYYY-MM-DD），按需补充。
HOLIDAYS = set()

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
    "breadth_up": null, "breadth_down": null,
    "breadth_reasoning": "..."
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
"""


# ---------- 工具 ----------
def log(*a):
    print("[web_brief]", *a, file=sys.stderr, flush=True)


def http_get(url: str, timeout: int = 15) -> str | None:
    """简单 GET，失败返回 None（不抛异常，符合"缺失即标注"）。"""
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:  # noqa: BLE001
        log(f"GET 失败 {url}: {e}")
        return None


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

    # 2) 板块涨幅（东财行业板块，m:90+t:2 为板块，字段含 f3 涨幅 f62 主力净流）
    sector_url = "https://push2.eastmoney.com/api/qt/clist/get?fs=m:90+t:2&fields=f12,f14,f3,f62&pn=1&pz=20"
    stxt = http_get(sector_url)
    if stxt:
        try:
            j = json.loads(stxt)
            data = j.get("data") if isinstance(j.get("data"), dict) else {}
            items = data.get("diff") or []
            lines = []
            if isinstance(items, list):
                for it in items[:15]:
                    if isinstance(it, dict):
                        lines.append(f"{it.get('f14')} 涨幅={it.get('f3')}% 主力净流={it.get('f62')}")
            if lines:
                parts.append("【行业板块涨幅 TOP】\n" + "\n".join(lines))
                ds.append({"type": "web", "url": sector_url, "fetched_at": now})
            else:
                parts.append("【行业板块】接口返回空，未获取")
        except Exception as e:  # noqa: BLE001
            log(f"板块解析失败: {e}")
            parts.append("【行业板块】解析失败，未获取")
    else:
        parts.append("【行业板块】未获取")

    # 3) 盘中快讯（东财快讯列表接口）
    news_api = "https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=10&page_index=1"
    ntxt = http_get(news_api)
    if ntxt:
        ds.append({"type": "web", "url": news_api, "fetched_at": now})
        parts.append("【盘中快讯】已抓取东财公开接口（见数据源）")
    else:
        parts.append("【盘中快讯】未获取")

    # 4) 博查实时搜索：A股当日收评 + 题材热点（免费额度，CI 友好）
    for q in (f"{trade_date.isoformat()} A股 收评 大盘 题材", f"{trade_date.isoformat()} A股 热点板块 涨停 复盘"):
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
            payload = assemble(trade_date, model_json, ds, idx_struct)
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


def assemble(trade_date: dt.date, mj: dict, ds: list[dict], idx_struct: list[dict] | None = None) -> dict:
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
                "market_cap": c.get("market_cap"),
                "price": c.get("price"),
                "change_pct": c.get("change_pct"),
                "ma5": c.get("ma5"),
                "quote_source": c.get("source", "web:用户提供的收评"),
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
        "data_quality": mj.get("data_quality", {"overall": "partial", "missing": ["模型未提供完整溯源"]}),
        "methods_used": [METHOD],
    }
    return payload


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
            "strength": "neutral",
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
