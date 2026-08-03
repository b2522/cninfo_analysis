"""Core business rules and value objects for CNINFO announcement analysis."""

OPPORTUNITY_CATEGORIES = (
    "业绩增长",
    "并购重组和资产注入",
    "回购、增持和股权激励",
    "终止减持、未减持",
    "产品获批、重大技术突破",
    "产能投产和重大项目",
)

RISK_CATEGORIES = (
    "大股东减持、质押和股权变动",
    "重大合同违约或经营风险",
    "业绩大降",
)

SUPPORTED_CATEGORIES = OPPORTUNITY_CATEGORIES + RISK_CATEGORIES

RESULT_VIEW_CATEGORIES = {
    "opportunity": OPPORTUNITY_CATEGORIES,
    "risk": RISK_CATEGORIES,
}


def result_views_for_labels(labels: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    label_set = set(labels)
    return tuple(
        view
        for view, categories in RESULT_VIEW_CATEGORIES.items()
        if label_set.intersection(categories)
    )
