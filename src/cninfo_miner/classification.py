"""Deterministic high-recall title screening and evidence checks."""

from collections.abc import Iterable
import re
from typing import Any

from .domain import OPPORTUNITY_CATEGORIES, RISK_CATEGORIES

_PERFORMANCE_GENERAL = ("业绩预告", "业绩快报", "年度报告", "半年度报告", "季度报告", "净利润")
_TERMINATION_REDUCTION_TITLE_MARKERS = ("提前终止减持", "终止减持", "未减持")
_TERMINATION_REDUCTION_EVIDENCE_MARKERS = (
    "提前终止减持计划",
    "提前终止本次减持计划",
    "终止减持计划",
    "终止本次减持计划",
    "未减持",
)
_ROUTINE_REPLEDGE_CONNECTORS = ("及质押", "和质押")
_SALE_REPURCHASE_TITLE_MARKERS = ("出售已回购股份", "出售回购股份")
_PLEDGE_RISK_ESCALATION_MARKERS = (
    "被动平仓",
    "强制平仓",
    "违约处置",
    "司法冻结",
    "轮候冻结",
    "控制权变更",
    "可能导致控制权",
)
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "业绩增长": _PERFORMANCE_GENERAL + ("预增", "扭亏为盈", "业绩增长"),
    "并购重组和资产注入": ("资产重组", "重大资产", "资产注入", "吸收合并", "发行股份购买资产", "收购", "购买资产"),
    "回购、增持和股权激励": ("股份回购", "回购股份", "回购", "增持", "股权激励", "限制性股票", "员工持股"),
    "终止减持、未减持": ("终止减持", "未减持", "减持计划期限届满", "减持计划终止", "提前终止减持"),
    "产品获批、重大技术突破": ("获批", "注册证", "临床试验", "技术突破", "研发成果", "认证", "取得许可", "批准上市"),
    "产能投产和重大项目": ("投产", "试生产", "产能", "重大项目", "项目建设", "项目竣工", "项目开工", "生产基地", "产线"),
    "大股东减持、质押和股权变动": ("股东减持", "减持计划", "股份质押", "股份变动", "权益变动", "解除质押", "控股股东", "实际控制人", "出售已回购股份", "出售回购股份"),
    "重大合同违约或经营风险": ("合同违约", "经营风险", "无法履约", "债务逾期", "流动性", "终止合同", "退市风险", "重大风险提示"),
    "业绩大降": _PERFORMANCE_GENERAL + ("预减", "首亏", "续亏", "由盈转亏", "亏损", "业绩下滑"),
}
_COMBINED_RECALL: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "并购重组和资产注入": (("资产", "重组", "收购", "合并"), ("进展", "提示", "说明", "完成", "终止", "变更")),
    "产品获批、重大技术突破": (("产品", "技术", "研发", "药品", "器械"), ("进展", "提示", "说明", "完成", "获批")),
    "产能投产和重大项目": (("项目", "基地", "产线", "产能"), ("进展", "提示", "说明", "完成", "投产", "竣工", "开工")),
    "大股东减持、质押和股权变动": (("股东", "股份", "质押", "权益"), ("进展", "提示", "说明", "变动", "减持", "解除")),
}


def is_routine_release_and_repledge_title(title: str) -> bool:
    """Identify routine release-and-repledge notices without escalation signals."""
    return (
        "解除质押" in title
        and any(connector in title for connector in _ROUTINE_REPLEDGE_CONNECTORS)
        and not any(marker in title for marker in _PLEDGE_RISK_ESCALATION_MARKERS)
    )


def is_sale_repurchase_title(title: str) -> bool:
    return any(marker in title for marker in _SALE_REPURCHASE_TITLE_MARKERS)


def _excluded_categories(title: str) -> set[str]:
    excluded: set[str] = set()
    if "激励对象名单" in title or is_sale_repurchase_title(title):
        excluded.add("回购、增持和股权激励")
    if "购买土地使用权" in title and "进展" in title:
        excluded.add("产能投产和重大项目")
    if is_routine_release_and_repledge_title(title):
        excluded.add("大股东减持、质押和股权变动")
    return excluded


def screen_categories(title: str) -> tuple[str, ...]:
    """Return high-recall candidate labels for a title; final classification requires PDF evidence."""
    matched = []
    excluded = _excluded_categories(title)
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if category in excluded:
            continue
        if any(keyword in title for keyword in keywords):
            matched.append(category)
            continue
        topic_words, event_words = _COMBINED_RECALL.get(category, ((), ()))
        if topic_words and any(topic in title for topic in topic_words) and any(event in title for event in event_words):
            matched.append(category)
    return tuple(matched)


def is_termination_reduction_title(title: str) -> bool:
    return any(marker in title for marker in _TERMINATION_REDUCTION_TITLE_MARKERS)


def termination_reduction_evidence(title: str, text: str) -> str:
    """Return a direct PDF phrase only for an explicit termination or no-sale title."""
    if not is_termination_reduction_title(title):
        return ""
    return next((marker for marker in _TERMINATION_REDUCTION_EVIDENCE_MARKERS if marker in text), "")


def sale_repurchase_evidence(title: str, text: str) -> dict[str, Any] | None:
    """Confirm sale of repurchased shares only when the PDF states the sale."""
    if not is_sale_repurchase_title(title):
        return None
    normalized = re.sub(r"\s+", "", text)
    evidence = _match_sentence(normalized, r"出售(?:已)?回购股份")
    if not evidence:
        return None
    return {
        "label": "大股东减持、质押和股权变动",
        "summary": "出售已回购股份（相当于减持）。",
        "evidence": evidence,
        "confidence": "high",
        "stage": "出售回购股份",
        "metrics": {},
    }


def _match_repurchase_amount_range(text: str) -> str | None:
    match = re.search(
        r"(?:回购股份资金(?:总额)?|回购资金总额)[^。；]{0,120}?"
        r"(?:不低于|不少于)(?:人民币)?([\d,，.]+)万元.*?"
        r"(?:不超过|不高于)(?:人民币)?([\d,，.]+)万元",
        text,
    )
    if not match:
        return None
    lower, upper = (value.replace("，", ",") for value in match.groups())
    return f"{lower}万元至{upper}万元"


def _match_repurchase_price(text: str) -> str | None:
    match = re.search(
        r"回购(?:股份)?(?:的)?价格[^。；]{0,80}?(?:不超过|不高于)(?:人民币)?([\d,，.]+)元/股",
        text,
    )
    return f"{match.group(1).replace('，', ',')}元/股" if match else None


def _match_sentence(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    if not match:
        return ""
    start = max(text.rfind("。", 0, match.start()), text.rfind("；", 0, match.start())) + 1
    end_candidates = [index for index in (text.find("。", match.end()), text.find("；", match.end())) if index >= 0]
    end = min(end_candidates) + 1 if end_candidates else min(len(text), match.end() + 240)
    return text[start:end]


def _match_repurchase_total_amount(text: str) -> str | None:
    match = re.search(
        r"(?:累计回购总金额|累计(?:已)?支付(?:的)?(?:回购)?(?:资金)?(?:总)?金额|已支付(?:的)?(?:回购)?(?:资金)?(?:总)?金额)"
        r"[^。；]{0,80}?(?:为|达|共计)?(?:人民币)?([\d,，.]+)(元|万元|亿元)",
        text,
    )
    if not match:
        return None
    return f"{match.group(1).replace('，', ',')}{match.group(2)}"


def _match_repurchase_trade_prices(text: str) -> tuple[str | None, str | None]:
    range_match = re.search(
        r"成交价格(?:区间)?(?:为|在)?([\d,，.]+)元/股(?:至|到|—|-|~)([\d,，.]+)元/股",
        text,
    )
    if range_match:
        low, high = (value.replace("，", ",") for value in range_match.groups())
        return f"{high}元/股", f"{low}元/股"
    high_match = re.search(r"最高成交价(?:为)?([\d,，.]+)元/股", text)
    low_match = re.search(r"最低成交价(?:为)?([\d,，.]+)元/股", text)
    high = f"{high_match.group(1).replace('，', ',')}元/股" if high_match else None
    low = f"{low_match.group(1).replace('，', ',')}元/股" if low_match else None
    return high, low


def repurchase_evidence(title: str, text: str) -> dict[str, Any] | None:
    """Return fixed-order PDF facts for a repurchase plan or progress notice."""
    if "回购" not in title or is_sale_repurchase_title(title):
        return None
    normalized = re.sub(r"\s+", "", text)
    amount_range = _match_repurchase_amount_range(normalized)
    price_cap = _match_repurchase_price(normalized)
    plan_amount = amount_range or "本公告未披露"
    plan_price = price_cap or "本公告未披露"
    if "进展" in title:
        date_match = re.search(r"截至\d{4}年\d{1,2}月\d{1,2}日", normalized)
        shares_match = re.search(r"累计回购(?:公司)?股份([\d,，]+)股", normalized)
        if not date_match or not shares_match:
            return None
        date_text = date_match.group(0)
        shares = shares_match.group(1).replace("，", ",")
        ratio_match = re.search(r"占(?:公司)?(?:当前)?总股本(?:的)?([\d.]+%)", normalized)
        ratio = ratio_match.group(1) if ratio_match else None
        total_amount = _match_repurchase_total_amount(normalized)
        high_price, low_price = _match_repurchase_trade_prices(normalized)
        progress_parts = [
            date_text,
            f"累计回购{shares}股",
            f"占总股本{ratio}" if ratio else "累计回购比例本公告未披露",
            f"累计回购总金额{total_amount or '本公告未披露'}",
            f"最高成交价{high_price or '本公告未披露'}",
            f"最低成交价{low_price or '本公告未披露'}",
        ]
        evidence = _match_sentence(normalized, r"截至\d{4}年\d{1,2}月\d{1,2}日[^。；]*累计回购(?:公司)?股份") or normalized
        metrics = {"累计回购股份": f"{shares}股", "截至日期": date_text}
        if amount_range:
            metrics["计划回购金额"] = amount_range
        if price_cap:
            metrics["回购价格上限"] = price_cap
        if ratio:
            metrics["累计回购比例"] = ratio
        if total_amount:
            metrics["累计回购总金额"] = total_amount
        if high_price:
            metrics["最高成交价"] = high_price
        if low_price:
            metrics["最低成交价"] = low_price
        return {
            "label": "回购、增持和股权激励",
            "summary": f"回购计划：资金总额{plan_amount}，回购价格上限{plan_price}；回购进展：{'，'.join(progress_parts)}。",
            "evidence": evidence,
            "confidence": "high",
            "stage": "回购进展",
            "metrics": metrics,
        }
    if amount_range:
        evidence = _match_sentence(normalized, r"(?:回购股份资金(?:总额)?|回购资金总额)[^。；]*") or normalized
        metrics = {"计划回购金额": amount_range}
        if price_cap:
            metrics["回购价格上限"] = price_cap
        return {
            "label": "回购、增持和股权激励",
            "summary": f"回购计划：资金总额{plan_amount}，回购价格上限{plan_price}；回购进展：本公告未披露。",
            "evidence": evidence,
            "confidence": "high",
            "stage": "回购方案",
            "metrics": metrics,
        }
    return None


def increase_holding_evidence(title: str, text: str) -> dict[str, Any] | None:
    """Return deterministic facts for an actual share-increase progress notice."""
    if "增持" not in title:
        return None
    normalized = re.sub(r"\s+", "", text)
    shares_match = re.search(r"(?:累计|已)增持(?:公司)?股份([\d,，]+)股", normalized)
    if not shares_match:
        return None
    shares = shares_match.group(1).replace("，", ",")
    ratio_match = re.search(r"占(?:公司)?(?:当前)?总股本(?:的)?([\d.]+%)", normalized)
    ratio = ratio_match.group(1) if ratio_match else None
    average_match = re.search(r"(?:增持均价|成交均价)(?:为)?([\d,，.]+)元/股", normalized)
    price_range_match = re.search(
        r"(?:增持|成交)价格区间(?:为|在)?([\d,，.]+)元/股(?:至|到|—|-|~)([\d,，.]+)元/股",
        normalized,
    )
    if average_match:
        price_text = f"成交均价{average_match.group(1).replace('，', ',')}元/股"
    elif price_range_match:
        price_text = (
            f"成交价格区间{price_range_match.group(1).replace('，', ',')}元/股"
            f"至{price_range_match.group(2).replace('，', ',')}元/股"
        )
    else:
        price_text = "成交价格本公告未披露"
    summary_parts = [f"累计增持{shares}股"]
    summary_parts.append(f"占总股本{ratio}" if ratio else "增持比例本公告未披露")
    summary_parts.append(price_text)
    evidence = _match_sentence(normalized, r"(?:累计|已)增持(?:公司)?股份[\d,，]+股") or normalized
    metrics = {"累计增持股份": f"{shares}股"}
    if ratio:
        metrics["累计增持比例"] = ratio
    if average_match:
        metrics["增持均价"] = f"{average_match.group(1).replace('，', ',')}元/股"
    elif price_range_match:
        metrics["增持价格区间"] = f"{price_range_match.group(1).replace('，', ',')}元/股至{price_range_match.group(2).replace('，', ',')}元/股"
    return {
        "label": "回购、增持和股权激励",
        "summary": f"增持进展：{'，'.join(summary_parts)}。",
        "evidence": evidence,
        "confidence": "high",
        "stage": "增持进展",
        "metrics": metrics,
    }


def has_evidence(claim: str, text: str) -> bool:
    """Require the reported claim to be present in the original PDF text."""
    return bool(claim.strip()) and claim in text


def keyword_families() -> Iterable[tuple[str, tuple[str, ...]]]:
    return _CATEGORY_KEYWORDS.items()


def all_categories() -> tuple[str, ...]:
    return OPPORTUNITY_CATEGORIES + RISK_CATEGORIES


