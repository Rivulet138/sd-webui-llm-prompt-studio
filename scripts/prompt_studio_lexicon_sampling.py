"""Small, stateless helpers for sampling the indexed static lexicon.

The UI layer owns prompt-specific policy (character-focused categories,
blocklists and recipe dimensions). This module only performs bounded,
independent random sampling against the database adapter.
"""

import random


def sample_terms(
    db,
    limit=8,
    exclude_terms=None,
    categories=None,
    per_category=2,
    max_categories=12,
    logger=None,
):
    """Return a fresh, category-balanced vocabulary slice for one call."""
    try:
        requested = max(0, min(int(limit or 0), 200))
    except (TypeError, ValueError):
        requested = 0
    if not requested:
        return []
    excluded = {
        str(item).strip().casefold()
        for item in (exclude_terms or [])
        if str(item).strip()
    }
    try:
        grouped = db.wildcard_samples_by_category(
            per_category=max(1, min(int(per_category or 1), 12)),
            exclude=excluded,
            categories=categories,
            limit=requested,
            max_categories=max(1, min(int(max_categories or 1), 50)),
        )
    except Exception:
        if logger is not None:
            logger.exception("静态词库随机抽样失败")
        return []
    entries = [
        (str(term).strip(), str(category))
        for category, terms in grouped.items()
        for term in terms
        if str(term).strip() and str(term).strip().casefold() not in excluded
    ]
    random.SystemRandom().shuffle(entries)
    return entries[:requested]


def sample_references(
    db,
    source,
    related_limit=8,
    sample_limit=8,
    categories=None,
    exclude_terms=None,
    blocklist=None,
    logger=None,
):
    """Combine related matches and independent samples without stateful use."""
    try:
        related_count = max(0, min(int(related_limit or 0), 30))
        sample_count = max(0, min(int(sample_limit or 0), 30))
    except (TypeError, ValueError):
        related_count, sample_count = 0, 0
    excluded = {
        str(item).strip().casefold()
        for item in (exclude_terms or [])
        if str(item).strip()
    }
    blocked = {
        str(item).strip().casefold()
        for item in (blocklist or [])
        if str(item).strip()
    }
    related = []
    try:
        category_list = list(categories) if categories else [None]
        for category in category_list:
            if len(related) >= related_count:
                break
            for term in db.wildcard_matches(str(source or ""), related_count, category=category):
                key = str(term or "").strip().casefold()
                if key and key not in blocked and key not in excluded and term not in related:
                    related.append((term, ""))
                    if len(related) >= related_count:
                        break
    except Exception:
        if logger is not None:
            logger.exception("静态词库相关匹配失败")
    sample_excluded = excluded | {str(item[0]).strip().casefold() for item in related}
    sampled = sample_terms(
        db,
        sample_count,
        exclude_terms=sample_excluded,
        categories=categories,
        per_category=2,
        max_categories=sample_count,
        logger=logger,
    )
    return related + sampled
