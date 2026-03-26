from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx
import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
SKILLPACKS_DIR = BASE_DIR / "skillpacks"
RUNTIME_STATE_PATH = BASE_DIR / "runtime" / "skill_state.json"
UTC = timezone.utc

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

STOPWORDS = {
    "amazon",
    "alibaba",
    "best",
    "new",
    "with",
    "from",
    "your",
    "this",
    "that",
    "these",
    "those",
    "the",
    "and",
    "for",
    "are",
    "our",
    "you",
    "into",
    "pack",
    "set",
    "piece",
    "pieces",
    "kit",
    "sale",
    "gift",
    "gifts",
    "hot",
    "high",
    "quality",
    "portable",
    "premium",
    "durable",
    "available",
    "products",
    "product",
    "offer",
    "offers",
    "count",
    "size",
    "sizes",
    "colors",
    "color",
    "style",
    "styles",
    "made",
    "more",
    "most",
    "great",
    "out",
    "use",
    "using",
    "in",
    "on",
    "of",
    "to",
    "by",
    "or",
    "at",
    "up",
    "per",
    "oz",
    "pcs",
    "moq",
}

CATEGORY_CATALOG: list[dict[str, Any]] = [
    {
        "id": "home-kitchen",
        "label": "家居厨房",
        "amazon_url": "https://www.amazon.com/Best-Sellers-Home-Kitchen/zgbs/home-garden",
        "amazon_search_alias": "garden",
        "alibaba_term": "kitchen-gadgets",
        "seed_keywords": ["home organization", "kitchen helper", "clean lines", "space-saving"],
        "focus_terms": ["kitchen", "home", "storage", "organizer", "cookware", "gadget"],
    },
    {
        "id": "beauty-personal-care",
        "label": "美妆个护",
        "amazon_url": "https://www.amazon.com/Best-Sellers-Beauty-Personal-Care/zgbs/beauty",
        "amazon_search_alias": "beauty-intl-ship",
        "alibaba_term": "beauty-products",
        "seed_keywords": ["skin-friendly", "beauty routine", "soft touch", "premium care"],
        "focus_terms": ["beauty", "skin", "care", "makeup", "serum", "facial", "cosmetic"],
    },
    {
        "id": "electronics",
        "label": "3C 数码",
        "amazon_url": "https://www.amazon.com/Best-Sellers-Electronics/zgbs/electronics",
        "amazon_search_alias": "electronics",
        "alibaba_term": "wireless-earbuds",
        "seed_keywords": ["smart device", "clean tech", "minimal setup", "modern texture"],
        "focus_terms": ["wireless", "bluetooth", "charger", "device", "usb", "smart", "earbuds"],
    },
    {
        "id": "sports-outdoors",
        "label": "运动户外",
        "amazon_url": "https://www.amazon.com/Best-Sellers-Sports-Outdoors/zgbs/sporting-goods",
        "amazon_search_alias": "sporting",
        "alibaba_term": "sports-bottle",
        "seed_keywords": ["active lifestyle", "outdoor ready", "leak-proof", "performance gear"],
        "focus_terms": ["sports", "fitness", "outdoor", "bottle", "gym", "hydration", "training"],
    },
    {
        "id": "pet-supplies",
        "label": "宠物用品",
        "amazon_url": "https://www.amazon.com/Best-Sellers-Pet-Supplies/zgbs/pet-supplies",
        "amazon_search_alias": "pet-supplies",
        "alibaba_term": "pet-supplies",
        "seed_keywords": ["pet friendly", "safe material", "daily care", "easy clean"],
        "focus_terms": ["pet", "dog", "cat", "collar", "leash", "feeding", "toy"],
    },
    {
        "id": "office-products",
        "label": "办公文具",
        "amazon_url": "https://www.amazon.com/Best-Sellers-Office-Products/zgbs/office-products",
        "amazon_search_alias": "office-products",
        "alibaba_term": "office-supplies",
        "seed_keywords": ["workspace upgrade", "business ready", "tidy desk", "efficient setup"],
        "focus_terms": ["office", "desk", "paper", "notebook", "organizer", "workspace"],
    },
    {
        "id": "baby-products",
        "label": "母婴用品",
        "amazon_url": "https://www.amazon.com/Best-Sellers-Baby/zgbs/baby-products",
        "amazon_search_alias": "baby-products",
        "alibaba_term": "baby-products",
        "seed_keywords": ["baby safe", "soft material", "family trust", "gentle touch"],
        "focus_terms": ["baby", "feeding", "silicone", "toddler", "nursery", "infant"],
    },
]

CREATIVE_MODES: list[dict[str, str]] = [
    {
        "id": "hero",
        "label": "高端主图",
        "brief": "突出单品主体、干净构图、强转化感，适合作为上架主图或封面图。",
    },
    {
        "id": "infographic",
        "label": "卖点信息图",
        "brief": "加入 3-5 组短文案标签、结构指向线或模块化卖点区，用于详情页首屏。",
    },
    {
        "id": "lifestyle",
        "label": "场景图",
        "brief": "把产品放入真实使用场景，强调氛围、材质和受众代入感。",
    },
]

SEARCH_TERM_MAPPINGS: dict[str, list[str]] = {
    "烟酰胺": ["niacinamide serum", "niacinamide", "brightening serum"],
    "niacinamide": ["niacinamide serum", "niacinamide", "brightening serum"],
    "维c": ["vitamin c serum", "vitamin c", "brightening serum"],
    "vc": ["vitamin c serum", "vitamin c"],
    "玻尿酸": ["hyaluronic acid serum", "hyaluronic acid", "hydrating serum"],
    "hyaluronic": ["hyaluronic acid serum", "hydrating serum"],
    "视黄醇": ["retinol serum", "retinol", "anti aging serum"],
    "retinol": ["retinol serum", "retinol", "anti aging serum"],
    "精华": ["serum", "essence", "facial serum"],
    "原液": ["serum", "essence"],
    "面霜": ["face cream", "moisturizer", "facial cream"],
    "乳液": ["lotion", "emulsion", "moisturizer"],
    "爽肤水": ["toner", "facial toner"],
    "防晒": ["sunscreen", "sun cream"],
    "洗面奶": ["facial cleanser", "cleanser"],
    "洁面": ["facial cleanser", "cleanser"],
    "面膜": ["face mask", "sheet mask"],
    "护肤": ["skin care", "facial care"],
    "美白": ["brightening", "whitening"],
    "保湿": ["hydrating", "moisturizing"],
    "修护": ["repair", "barrier repair"],
    "补水": ["hydrating", "moisturizing"],
    "祛痘": ["acne treatment", "blemish control"],
    "洗发": ["shampoo", "hair care"],
    "护发": ["hair care", "conditioner"],
    "口红": ["lipstick", "lip color"],
    "香水": ["perfume", "fragrance"],
    "耳机": ["wireless earbuds", "bluetooth earbuds"],
    "充电器": ["charger", "fast charger"],
    "无线": ["wireless", "magnetic"],
    "磁吸": ["magnetic", "magsafe"],
    "保温杯": ["insulated tumbler", "stainless steel tumbler"],
    "水杯": ["water bottle", "tumbler"],
}

CATEGORY_QUERY_HINTS: dict[str, dict[str, list[str]]] = {
    "home-kitchen": {
        "fallback_queries": ["kitchen gadget", "home organizer"],
        "fallback_slugs": ["kitchen-gadgets", "home-organization"],
    },
    "beauty-personal-care": {
        "fallback_queries": ["skin care serum", "facial serum", "beauty product"],
        "fallback_slugs": ["facial-serum", "skin-care-serum", "beauty-products"],
    },
    "electronics": {
        "fallback_queries": ["wireless charger", "smart device accessory"],
        "fallback_slugs": ["wireless-charger", "consumer-electronics"],
    },
    "sports-outdoors": {
        "fallback_queries": ["sports bottle", "fitness accessory"],
        "fallback_slugs": ["sports-bottle", "fitness-equipment"],
    },
    "pet-supplies": {
        "fallback_queries": ["pet accessory", "dog supplies"],
        "fallback_slugs": ["pet-supplies", "dog-supplies"],
    },
    "office-products": {
        "fallback_queries": ["office supplies", "desk organizer"],
        "fallback_slugs": ["office-supplies", "desk-organizer"],
    },
    "baby-products": {
        "fallback_queries": ["baby feeding set", "baby care"],
        "fallback_slugs": ["baby-products", "baby-feeding"],
    },
}

_state_lock = threading.Lock()


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return _now_utc().isoformat()


def _format_local_time(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _load_runtime_state() -> dict[str, Any]:
    if not RUNTIME_STATE_PATH.exists():
        return {}
    try:
        data = _read_json(RUNTIME_STATE_PATH)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_runtime_state(data: dict[str, Any]) -> None:
    with _state_lock:
        _write_json(RUNTIME_STATE_PATH, data)


def list_categories() -> list[dict[str, Any]]:
    return [{k: v for k, v in category.items() if k not in {"focus_terms"}} for category in CATEGORY_CATALOG]


def list_creative_modes() -> list[dict[str, str]]:
    return list(CREATIVE_MODES)


def _skillpack_dirs() -> list[Path]:
    if not SKILLPACKS_DIR.exists():
        return []
    return sorted(path for path in SKILLPACKS_DIR.iterdir() if path.is_dir())


def list_skillpacks() -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    for folder in _skillpack_dirs():
        manifest_path = folder / "skill.json"
        if not manifest_path.exists():
            continue
        manifest = _read_json(manifest_path)
        if not isinstance(manifest, dict):
            continue
        skills.append(manifest)
    return skills


def get_skillpack(skill_id: str) -> dict[str, Any]:
    for skill in list_skillpacks():
        if skill.get("id") == skill_id:
            return skill
    raise ValueError(f"Unknown skill_id: {skill_id}")


def get_category(category_id: str) -> dict[str, Any]:
    for category in CATEGORY_CATALOG:
        if category["id"] == category_id:
            return category
    raise ValueError(f"Unknown category_id: {category_id}")


def get_creative_mode(creative_mode_id: str) -> dict[str, str]:
    for mode in CREATIVE_MODES:
        if mode["id"] == creative_mode_id:
            return mode
    raise ValueError(f"Unknown creative_mode: {creative_mode_id}")


def _runtime_key(skill_id: str, category_id: str, search_signature: str = "") -> str:
    suffix = search_signature or "default"
    return f"{skill_id}:{category_id}:{suffix}"


def _extract_tokens(text: str) -> list[str]:
    return [
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9\-\+]{2,}", text or "")
        if token and token.lower() not in STOPWORDS and not token.isdigit()
    ]


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item.strip())
    return result


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _split_search_phrases(text: str) -> list[str]:
    raw = _normalize_whitespace(text)
    if not raw:
        return []
    parts = re.split(r"[,;/|，；、\n]+", raw)
    return _dedupe_keep_order(part for part in parts if _normalize_whitespace(part))


def _extract_mapped_phrases(text: str) -> list[str]:
    raw = _normalize_whitespace(text)
    if not raw:
        return []
    raw_lc = raw.lower()
    phrases: list[str] = []
    for needle, mapped_values in SEARCH_TERM_MAPPINGS.items():
        if needle.lower() in raw_lc:
            phrases.extend(mapped_values)
    return _dedupe_keep_order(phrases)


def _slugify_query(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def _build_search_context(
    category: dict[str, Any],
    *,
    search_keywords: str = "",
    product_name: str = "",
    selling_points: str = "",
    manual_prompt: str = "",
) -> dict[str, Any]:
    hints = CATEGORY_QUERY_HINTS.get(category["id"], {})
    raw_keyword_text = " ".join(
        value.strip() for value in [search_keywords, product_name, selling_points, manual_prompt] if (value or "").strip()
    )

    explicit_phrases = _split_search_phrases(search_keywords)
    mapped_phrases = _extract_mapped_phrases(raw_keyword_text)
    explicit_tokens = _extract_tokens(search_keywords)
    product_tokens = _extract_tokens(product_name)
    selling_tokens = _extract_tokens(selling_points)
    prompt_tokens = _extract_tokens(manual_prompt)
    primary_terms = _dedupe_keep_order([*explicit_tokens, *product_tokens, *_extract_tokens(" ".join(mapped_phrases))])[:10]
    has_explicit_search = bool(explicit_phrases or explicit_tokens)

    phrase_candidates: list[str] = []
    phrase_candidates.extend(explicit_phrases)
    if re.search(r"[A-Za-z]", product_name or ""):
        phrase_candidates.append(_normalize_whitespace(product_name))
    phrase_candidates.extend(mapped_phrases)
    if product_tokens:
        phrase_candidates.append(" ".join(product_tokens[:4]))
    if explicit_tokens:
        phrase_candidates.append(" ".join(explicit_tokens[:6]))
    combined_tokens = _dedupe_keep_order([*product_tokens, *selling_tokens, *prompt_tokens])
    if combined_tokens:
        phrase_candidates.append(" ".join(combined_tokens[:6]))
    if not has_explicit_search:
        phrase_candidates.extend(hints.get("fallback_queries", []))

    phrase_candidates = _dedupe_keep_order(_normalize_whitespace(value) for value in phrase_candidates if value)
    if not phrase_candidates:
        phrase_candidates = [" ".join(category.get("focus_terms", [])[:3])]

    query_terms = _dedupe_keep_order(
        [
            *explicit_tokens,
            *_extract_tokens(" ".join(mapped_phrases)),
            *product_tokens,
            *selling_tokens,
            *prompt_tokens,
            *([] if has_explicit_search else category.get("focus_terms", [])),
        ]
    )[:14]

    amazon_queries = _dedupe_keep_order(
        [
            *explicit_phrases,
            *phrase_candidates,
            *[
                f"{phrase_candidates[0]} {' '.join(selling_tokens[:3])}".strip()
                if phrase_candidates and selling_tokens
                else ""
            ],
        ]
    )[:6]

    alibaba_slugs = _dedupe_keep_order(
        [
            *[_slugify_query(query) for query in phrase_candidates if _slugify_query(query)],
            *(hints.get("fallback_slugs", []) if not has_explicit_search else []),
            category.get("alibaba_term", ""),
        ]
    )

    signature_raw = "|".join([phrase_candidates[0], *query_terms[:8]]) or category["id"]
    search_signature = hashlib.sha1(signature_raw.encode("utf-8")).hexdigest()[:12]

    return {
        "display_query": phrase_candidates[0],
        "primary_terms": primary_terms,
        "query_terms": query_terms,
        "amazon_queries": amazon_queries,
        "alibaba_slugs": alibaba_slugs[:5],
        "search_signature": search_signature,
    }


def _extract_keywords(titles: list[str], seed_keywords: list[str], limit: int = 10) -> list[str]:
    token_counter: Counter[str] = Counter()
    phrase_counter: Counter[str] = Counter()

    for title in titles:
        tokens = _extract_tokens(title)
        token_counter.update(tokens)
        for first, second in zip(tokens, tokens[1:]):
            if first == second:
                continue
            phrase = f"{first} {second}"
            if first in STOPWORDS or second in STOPWORDS:
                continue
            phrase_counter[phrase] += 1

    ordered: list[str] = []
    ordered.extend(seed_keywords)
    ordered.extend([phrase for phrase, _ in phrase_counter.most_common(limit * 2)])
    ordered.extend([token for token, _ in token_counter.most_common(limit * 3)])
    return _dedupe_keep_order(ordered)[:limit]


def _normalize_url(url: str, base_url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    if value.startswith("//"):
        return f"https:{value}"
    return urljoin(base_url, value)


def _normalize_result_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return value
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _decode_duckduckgo_result_url(url: str) -> str:
    value = (url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if "duckduckgo.com" not in parsed.netloc:
        return value
    target = parse_qs(parsed.query).get("uddg", [""])[0]
    return unquote(target or "").strip()


def _is_amazon_product_url(url: str) -> bool:
    parsed = urlparse((url or "").strip())
    if "amazon." not in parsed.netloc.lower():
        return False
    path = parsed.path.lower()
    return "/dp/" in path or "/gp/product/" in path


def _get_soup(url: str, timeout_sec: int) -> BeautifulSoup:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(
                timeout=float(timeout_sec),
                headers=DEFAULT_HEADERS,
                follow_redirects=True,
                trust_env=False,
            ) as client:
                resp = client.get(url)
                resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.0 + attempt * 0.5)
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Failed to fetch page: {url}")


def _get_soup_requests(
    url: str,
    timeout_sec: int,
    *,
    trust_env: bool = False,
    attempts: int = 3,
) -> BeautifulSoup:
    last_error: Exception | None = None
    total_attempts = max(attempts, 1)
    for attempt in range(total_attempts):
        session = requests.Session()
        session.trust_env = trust_env
        try:
            resp = session.get(url, headers=DEFAULT_HEADERS, timeout=float(timeout_sec), allow_redirects=True)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            last_error = exc
            if attempt < total_attempts - 1:
                time.sleep(1.0 + attempt * 0.5)
        finally:
            session.close()
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Failed to fetch page via requests: {url}")


def _get_text_requests(
    url: str,
    timeout_sec: int,
    *,
    trust_env: bool = False,
    attempts: int = 2,
) -> str:
    last_error: Exception | None = None
    total_attempts = max(attempts, 1)
    for attempt in range(total_attempts):
        session = requests.Session()
        session.trust_env = trust_env
        try:
            resp = session.get(url, headers=DEFAULT_HEADERS, timeout=float(timeout_sec), allow_redirects=True)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            last_error = exc
            if attempt < total_attempts - 1:
                time.sleep(1.0 + attempt * 0.5)
        finally:
            session.close()
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Failed to fetch text via requests: {url}")


def _extract_amazon_product_details(
    page_url: str,
    *,
    fallback_title: str,
    strict_terms: list[str],
    timeout_sec: int,
) -> dict[str, Any] | None:
    try:
        soup = _get_soup_requests(page_url, timeout_sec, trust_env=True, attempts=2)
    except Exception:
        return None

    title_node = soup.select_one("#productTitle")
    image_node = soup.select_one("#landingImage") or soup.select_one("#imgBlkFront")
    price_node = soup.select_one(".a-price .a-offscreen") or soup.select_one("#corePriceDisplay_desktop_feature_div .a-offscreen")
    rating_node = soup.select_one("#acrPopover") or soup.select_one("span.a-icon-alt")
    review_node = soup.select_one("#acrCustomerReviewText")

    title = title_node.get_text(" ", strip=True) if title_node else fallback_title
    if strict_terms and not _has_term_match(title, strict_terms):
        return None

    return {
        "title": title,
        "url": page_url,
        "image_url": _normalize_url(image_node.get("src", "") if image_node else "", page_url),
        "price": price_node.get_text(" ", strip=True) if price_node else "",
        "rating": (
            rating_node.get("title", "").strip()
            if rating_node and rating_node.has_attr("title")
            else rating_node.get_text(" ", strip=True)
            if rating_node
            else ""
        ),
        "review_count": review_node.get_text(" ", strip=True) if review_node else "",
        "source": "Amazon via DuckDuckGo",
    }


def _extract_duckduckgo_markdown_candidates(
    markdown_text: str,
    *,
    query_terms: list[str],
    focus_terms: list[str],
    strict_terms: list[str],
) -> list[tuple[int, str, str]]:
    candidates: list[tuple[int, str, str]] = []
    for title, href in re.findall(r"\d+\.\[(.*?)\]\((.*?)\)", markdown_text or ""):
        clean_title = _normalize_whitespace(title)
        target_url = _decode_duckduckgo_result_url(href)
        if not clean_title or not target_url or not _is_amazon_product_url(target_url):
            continue
        if strict_terms and not _has_term_match(clean_title, strict_terms):
            continue
        score = _score_title(clean_title, query_terms, focus_terms) + 12
        candidates.append((score, _normalize_result_url(target_url), clean_title))
    return candidates


def _fetch_amazon_duckduckgo_market(
    category: dict[str, Any],
    search_context: dict[str, Any],
    *,
    max_items: int,
    timeout_sec: int,
) -> dict[str, Any] | None:
    focus_terms = category.get("focus_terms", [])
    query_terms = search_context.get("query_terms", [])
    strict_terms = search_context.get("primary_terms", [])
    source_url = ""
    ranked: list[tuple[int, dict[str, Any]]] = []
    seen_urls: set[str] = set()

    for query in search_context.get("amazon_queries", []):
        ddg_query = quote_plus(f"site:amazon.com {query}")
        page_url = f"https://lite.duckduckgo.com/lite/?q={ddg_query}"
        if not source_url:
            source_url = page_url

        candidates: list[tuple[int, str, str]] = []
        try:
            soup = _get_soup_requests(page_url, min(timeout_sec, 8), trust_env=True, attempts=1)
            for anchor in soup.select("a[href]"):
                title = anchor.get_text(" ", strip=True)
                target_url = _decode_duckduckgo_result_url(anchor.get("href", ""))
                if not title or not target_url or not _is_amazon_product_url(target_url):
                    continue
                if strict_terms and not _has_term_match(title, strict_terms):
                    continue
                normalized_url = _normalize_result_url(target_url)
                if not normalized_url:
                    continue
                score = _score_title(title, query_terms, focus_terms) + 12
                candidates.append((score, normalized_url, title))
        except Exception:
            pass

        if not candidates:
            try:
                jina_url = f"https://r.jina.ai/http://{page_url.replace('https://', '', 1)}"
                markdown_text = _get_text_requests(jina_url, min(timeout_sec, 10), trust_env=True, attempts=1)
                candidates.extend(
                    _extract_duckduckgo_markdown_candidates(
                        markdown_text,
                        query_terms=query_terms,
                        focus_terms=focus_terms,
                        strict_terms=strict_terms,
                    )
                )
            except Exception:
                pass

        for score, candidate_url, candidate_title in sorted(candidates, key=lambda item: -item[0])[: max_items * 2]:
            if not candidate_url or candidate_url in seen_urls:
                continue
            seen_urls.add(candidate_url)
            details = _extract_amazon_product_details(
                candidate_url,
                fallback_title=candidate_title,
                strict_terms=strict_terms,
                timeout_sec=min(timeout_sec, 8),
            )
            if not details:
                continue
            ranked.append((_score_title(details["title"], query_terms, focus_terms) + 12, details))
            if len(ranked) >= max_items * 2:
                break

        if len(ranked) >= max_items:
            break

    if not ranked:
        return None

    ranked.sort(key=lambda pair: -pair[0])
    sample_products = [item for _, item in ranked[:max_items]]
    titles = [item["title"] for item in sample_products]
    return {
        "source_label": "Amazon via DuckDuckGo",
        "source_url": source_url,
        "source_page_title": "DuckDuckGo Lite Amazon Results",
        "source_note": (
            f"Amazon 搜索页不可用时，改用 DuckDuckGo 实时索引的 Amazon 商品页继续按关键词 "
            f"{search_context.get('display_query', '')} 抓取同款参考。"
        ),
        "source_query": search_context.get("display_query", ""),
        "fetched_at": _iso_now(),
        "platform": "amazon",
        "fallback": False,
        "sample_products": sample_products,
        "keywords": _extract_keywords(titles, category.get("seed_keywords", [])),
    }


def _score_title(title: str, query_terms: list[str], focus_terms: list[str]) -> int:
    title_lc = (title or "").lower()
    score = 0
    for term in query_terms:
        term_lc = term.lower()
        if term_lc and term_lc in title_lc:
            score += 8 if len(term_lc) >= 6 else 5
    for term in focus_terms:
        if term.lower() in title_lc:
            score += 2
    return score


def _has_term_match(title: str, terms: list[str]) -> bool:
    title_lc = (title or "").lower()
    return any(term.lower() in title_lc for term in terms if term)


def _extract_amazon_search_cards(
    soup: BeautifulSoup,
    page_url: str,
    *,
    query_terms: list[str],
    focus_terms: list[str],
    strict_terms: list[str],
) -> list[tuple[int, dict[str, Any]]]:
    ranked: list[tuple[int, dict[str, Any]]] = []
    for rank, card in enumerate(soup.select("div[data-component-type='s-search-result']"), start=1):
        title_node = card.select_one("h2 a span") or card.select_one("h2 span")
        link_node = card.select_one("h2 a[href]") or card.select_one("a[href*='/dp/']")
        image_node = card.select_one("img.s-image") or card.select_one("img")
        price_node = card.select_one(".a-price .a-offscreen") or card.select_one("span.a-offscreen")
        rating_node = card.select_one(".a-icon-alt")
        review_node = (
            card.select_one("span.a-size-base.s-underline-text")
            or card.select_one("a[href*='/dp/'] span.a-size-base")
            or card.select_one("span[aria-label*='ratings']")
        )

        title = title_node.get_text(" ", strip=True) if title_node else ""
        if not title:
            continue
        if strict_terms and not _has_term_match(title, strict_terms):
            continue
        asin = (card.get("data-asin") or "").strip()
        fallback_url = f"https://www.amazon.com/dp/{asin}" if asin else ""

        item = {
            "rank": rank,
            "title": title,
            "url": _normalize_url(link_node.get("href", "") if link_node else fallback_url, page_url),
            "image_url": _normalize_url(image_node.get("src", "") if image_node else "", page_url),
            "price": price_node.get_text(" ", strip=True) if price_node else "",
            "rating": rating_node.get_text(" ", strip=True) if rating_node else "",
            "review_count": review_node.get_text(" ", strip=True) if review_node else "",
            "source": "Amazon Search",
        }
        ranked.append((_score_title(title, query_terms, focus_terms), item))
    return ranked


def _fetch_amazon_bestsellers_market(
    category: dict[str, Any],
    search_context: dict[str, Any],
    *,
    max_items: int,
    timeout_sec: int,
) -> dict[str, Any]:
    url = category["amazon_url"]
    soup = _get_soup_requests(url, min(timeout_sec, 8), trust_env=True, attempts=2)
    page_title = soup.title.get_text(" ", strip=True) if soup.title else "Amazon Best Sellers"

    ranked: list[tuple[int, dict[str, Any]]] = []
    for rank, card in enumerate(soup.select("div.p13n-sc-uncoverable-faceout"), start=1):
        title_node = card.select_one("div[class*='line-clamp']")
        image_node = card.select_one("img.p13n-product-image") or card.select_one("img")
        link_node = card.select_one("a.a-link-normal.aok-block[href]") or card.select_one("a[href]")
        price_node = card.select_one("span._cDEzb_p13n-sc-price_3mJ9Z") or card.select_one("span.p13n-sc-price")
        rating_node = card.select_one(".a-icon-alt")
        review_node = card.select_one("a[aria-label*='ratings'] .a-size-small") or card.select_one(".a-size-small")

        title = (
            title_node.get_text(" ", strip=True)
            if title_node
            else (image_node.get("alt", "").strip() if image_node else "")
        )
        if not title:
            continue

        item = {
            "rank": rank,
            "title": title,
            "url": _normalize_url(link_node.get("href", "") if link_node else "", url),
            "image_url": _normalize_url(image_node.get("src", "") if image_node else "", url),
            "price": price_node.get_text(" ", strip=True) if price_node else "",
            "rating": rating_node.get_text(" ", strip=True) if rating_node else "",
            "review_count": review_node.get_text(" ", strip=True) if review_node else "",
            "source": "Amazon Best Sellers",
        }
        ranked.append((_score_title(title, search_context.get("query_terms", []), category.get("focus_terms", [])), item))

    ranked.sort(key=lambda pair: (-pair[0], pair[1]["rank"]))
    sample_products = [item for _, item in ranked[:max_items]]
    titles = [item["title"] for item in sample_products]

    return {
        "source_label": "Amazon Best Sellers",
        "source_url": url,
        "source_page_title": page_title,
        "source_note": f"关键词搜索结果不足，回退到 Amazon Best Sellers，并按商品关键词 {search_context.get('display_query', '')} 重新排序。",
        "source_query": search_context.get("display_query", ""),
        "fetched_at": _iso_now(),
        "platform": "amazon",
        "fallback": False,
        "sample_products": sample_products,
        "keywords": _extract_keywords(titles, category.get("seed_keywords", [])),
    }


def _fetch_amazon_market(
    category: dict[str, Any],
    search_context: dict[str, Any],
    *,
    max_items: int,
    timeout_sec: int,
) -> dict[str, Any]:
    focus_terms = category.get("focus_terms", [])
    amazon_timeout_sec = max(4, min(timeout_sec, 6))
    seen_urls: set[str] = set()
    ranked: list[tuple[int, dict[str, Any]]] = []
    source_url = ""
    source_page_title = "Amazon Search"
    department_alias = (category.get("amazon_search_alias") or "").strip()

    search_urls: list[str] = []
    for query in search_context.get("amazon_queries", []):
        encoded_query = quote_plus(query)
        search_urls.append(f"https://www.amazon.com/s?k={encoded_query}")
        if department_alias:
            search_urls.append(f"https://www.amazon.com/s?k={encoded_query}&i={quote_plus(department_alias)}")

    for page_url in _dedupe_keep_order(search_urls):
        try:
            soup = _get_soup_requests(page_url, amazon_timeout_sec, trust_env=True, attempts=2)
        except Exception:
            continue

        page_title = soup.title.get_text(" ", strip=True) if soup.title else "Amazon Search"
        query_ranked = _extract_amazon_search_cards(
            soup,
            page_url,
            query_terms=search_context.get("query_terms", []),
            focus_terms=focus_terms,
            strict_terms=search_context.get("primary_terms", []),
        )
        if not query_ranked:
            continue

        if not source_url:
            source_url = page_url
            source_page_title = page_title

        for score, item in query_ranked:
            item_url = item.get("url", "")
            if item_url and item_url in seen_urls:
                continue
            if item_url:
                seen_urls.add(item_url)
            ranked.append((score, item))

        if len(ranked) >= max_items * 4:
            break

    if not ranked:
        try:
            ddg_market = _fetch_amazon_duckduckgo_market(
                category,
                search_context,
                max_items=max_items,
                timeout_sec=timeout_sec,
            )
            if ddg_market and ddg_market.get("sample_products"):
                return ddg_market
        except Exception:
            pass
        try:
            cross_market = _fetch_alibaba_market(
                category,
                search_context,
                max_items=max_items,
                timeout_sec=timeout_sec,
            )
            if cross_market.get("sample_products"):
                cross_market["source_label"] = "Alibaba Showroom (Amazon Fallback)"
                cross_market["platform"] = "amazon"
                cross_market["source_note"] = (
                    f"Amazon 关键词搜索暂不可用，已改用相同关键词 {search_context.get('display_query', '')} "
                    f"抓取 Alibaba Showroom 相似商品，再按 Amazon 视觉规则生成。 {cross_market.get('source_note', '')}"
                ).strip()
                return cross_market
        except Exception:
            pass
        return _fetch_amazon_bestsellers_market(category, search_context, max_items=max_items, timeout_sec=timeout_sec)

    ranked.sort(key=lambda pair: (-pair[0], pair[1]["rank"]))
    sample_products = [item for _, item in ranked[:max_items]]
    titles = [item["title"] for item in sample_products]

    return {
        "source_label": "Amazon Search",
        "source_url": source_url,
        "source_page_title": source_page_title,
        "source_note": f"优先根据商品关键词搜索 Amazon 公共搜索页：{search_context.get('display_query', '')}",
        "source_query": search_context.get("display_query", ""),
        "fetched_at": _iso_now(),
        "platform": "amazon",
        "fallback": False,
        "sample_products": sample_products,
        "keywords": _extract_keywords(titles, category.get("seed_keywords", [])),
    }


def _extract_alibaba_update_note(full_text: str) -> str:
    match = re.search(r"Update:(\d{4}-\d{2}-\d{2})", full_text or "")
    if not match:
        return ""
    return f"Alibaba Showroom 页面更新时间：{match.group(1)}"


def _extract_alibaba_showroom_cards(
    soup: BeautifulSoup,
    page_url: str,
    *,
    query_terms: list[str],
    focus_terms: list[str],
    strict_terms: list[str],
) -> list[tuple[int, dict[str, Any]]]:
    ranked: list[tuple[int, dict[str, Any]]] = []
    for rank, card in enumerate(soup.select("div.traffic-card-gallery"), start=1):
        title_node = card.select_one("a.product-title[href*='product-detail']")
        image_node = card.select_one("a.product-image img")
        if not title_node or not image_node:
            continue

        price_node = card.select_one("div.il-text-lg.il-font-bold")
        moq_node = next(
            (
                node
                for node in card.select("div")
                if node.get_text(" ", strip=True).startswith("MOQ:")
            ),
            None,
        )

        title = title_node.get_text(" ", strip=True)
        if strict_terms and not _has_term_match(title, strict_terms):
            continue
        item = {
            "rank": rank,
            "title": title,
            "url": _normalize_url(title_node.get("href", ""), page_url),
            "image_url": _normalize_url(image_node.get("src", ""), page_url),
            "price": price_node.get_text(" ", strip=True) if price_node else "",
            "rating": "",
            "review_count": moq_node.get_text(" ", strip=True) if moq_node else "",
            "source": "Alibaba Showroom",
        }
        ranked.append((_score_title(title, query_terms, focus_terms), item))
    return ranked


def _fetch_alibaba_market(
    category: dict[str, Any],
    search_context: dict[str, Any],
    *,
    max_items: int,
    timeout_sec: int,
) -> dict[str, Any]:
    focus_terms = category.get("focus_terms", [])
    seen_urls: set[str] = set()
    ranked: list[tuple[int, dict[str, Any]]] = []
    source_url = ""
    source_page_title = "Alibaba Showroom"
    update_note = ""

    for slug in search_context.get("alibaba_slugs", []):
        page_url = f"https://www.alibaba.com/showroom/{slug}.html"
        try:
            soup = _get_soup(page_url, timeout_sec)
        except Exception:
            continue

        page_title = soup.title.get_text(" ", strip=True) if soup.title else "Alibaba Showroom"
        page_text = soup.get_text(" ", strip=True)
        current_note = _extract_alibaba_update_note(page_text)
        query_ranked = _extract_alibaba_showroom_cards(
            soup,
            page_url,
            query_terms=search_context.get("query_terms", []),
            focus_terms=focus_terms,
            strict_terms=search_context.get("primary_terms", []),
        )
        if not query_ranked:
            continue

        if not source_url:
            source_url = page_url
            source_page_title = page_title
            update_note = current_note

        for score, item in query_ranked:
            item_url = item.get("url", "")
            if item_url and item_url in seen_urls:
                continue
            if item_url:
                seen_urls.add(item_url)
            ranked.append((score, item))

        if len(ranked) >= max_items * 4:
            break

    if not ranked:
        fallback_url = f"https://www.alibaba.com/showroom/{category['alibaba_term']}.html"
        soup = _get_soup(fallback_url, timeout_sec)
        page_text = soup.get_text(" ", strip=True)
        update_note = _extract_alibaba_update_note(page_text)
        source_url = fallback_url
        source_page_title = soup.title.get_text(" ", strip=True) if soup.title else "Alibaba Showroom"
        ranked = _extract_alibaba_showroom_cards(
            soup,
            fallback_url,
            query_terms=search_context.get("query_terms", []),
            focus_terms=focus_terms,
            strict_terms=search_context.get("primary_terms", []),
        )
        source_prefix = f"关键词搜索结果不足，回退到 Alibaba Showroom 类目页，并按商品关键词 {search_context.get('display_query', '')} 重新排序。"
    else:
        source_prefix = f"优先根据商品关键词搜索 Alibaba Showroom：{search_context.get('display_query', '')}"

    ranked.sort(key=lambda pair: (-pair[0], pair[1]["rank"]))
    sample_products = [item for _, item in ranked[:max_items]]
    titles = [item["title"] for item in sample_products]
    source_note = source_prefix
    if update_note:
        source_note = f"{source_note} {update_note}"

    return {
        "source_label": "Alibaba Showroom",
        "source_url": source_url,
        "source_page_title": source_page_title,
        "source_note": source_note,
        "source_query": search_context.get("display_query", ""),
        "fetched_at": _iso_now(),
        "platform": "alibaba",
        "fallback": False,
        "sample_products": sample_products,
        "keywords": _extract_keywords(titles, category.get("seed_keywords", [])),
    }


def refresh_market_insight(
    skill: dict[str, Any],
    category: dict[str, Any],
    search_context: dict[str, Any],
    *,
    force_refresh: bool = False,
    ttl_sec: int = 1800,
    timeout_sec: int = 20,
    max_items: int = 6,
) -> dict[str, Any]:
    state = _load_runtime_state()
    key = _runtime_key(skill["id"], category["id"], search_context.get("search_signature", ""))
    cached = state.get(key)

    if not force_refresh and isinstance(cached, dict):
        fetched_at_raw = str(cached.get("fetched_at") or "")
        try:
            fetched_at = datetime.fromisoformat(fetched_at_raw)
        except ValueError:
            fetched_at = None
        if fetched_at is not None and (_now_utc() - fetched_at) <= timedelta(seconds=max(ttl_sec, 1)):
            insight = cached.get("market_insight") or {}
            if isinstance(insight, dict):
                insight["cache_hit"] = True
                return insight

    if skill.get("platform") == "amazon":
        market_insight = _fetch_amazon_market(
            category,
            search_context,
            max_items=max_items,
            timeout_sec=timeout_sec,
        )
    elif skill.get("platform") == "alibaba":
        market_insight = _fetch_alibaba_market(
            category,
            search_context,
            max_items=max_items,
            timeout_sec=timeout_sec,
        )
    else:
        raise ValueError(f"Unsupported platform: {skill.get('platform')}")

    market_insight["cache_hit"] = False
    state[key] = {
        "skill_id": skill["id"],
        "category_id": category["id"],
        "search_signature": search_context.get("search_signature", ""),
        "fetched_at": market_insight["fetched_at"],
        "market_insight": market_insight,
    }
    _save_runtime_state(state)
    return market_insight


def _fallback_market_insight(
    skill: dict[str, Any],
    category: dict[str, Any],
    search_context: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    keywords = _dedupe_keep_order(
        [
            *search_context.get("query_terms", []),
            *skill.get("default_keywords", []),
            *category.get("seed_keywords", []),
        ]
    )[:10]
    source_url = category["amazon_url"] if skill.get("platform") == "amazon" else f"https://www.alibaba.com/showroom/{category['alibaba_term']}.html"
    return {
        "source_label": skill.get("site_name", ""),
        "source_url": source_url,
        "source_page_title": skill.get("site_name", ""),
        "source_note": f"实时抓取失败，已回退到商品关键词 + 类目内置词包：{reason}",
        "source_query": search_context.get("display_query", ""),
        "fetched_at": _iso_now(),
        "platform": skill.get("platform"),
        "fallback": True,
        "cache_hit": False,
        "sample_products": [],
        "keywords": keywords,
    }


def _compose_reference_block(sample_products: list[dict[str, Any]]) -> str:
    if not sample_products:
        return "暂无实时样图，优先吸收类目热词与平台风格。"
    lines: list[str] = []
    for index, item in enumerate(sample_products[:4], start=1):
        meta_parts = [item.get("price", "").strip(), item.get("rating", "").strip(), item.get("review_count", "").strip()]
        meta = " | ".join(part for part in meta_parts if part)
        line = f"{index}. {item.get('title', '').strip()}"
        if meta:
            line = f"{line}（{meta}）"
        lines.append(line)
    return "\n".join(lines)


def compose_prompt(
    skill: dict[str, Any],
    category: dict[str, Any],
    creative_mode: dict[str, str],
    market_insight: dict[str, Any],
    *,
    product_name: str = "",
    selling_points: str = "",
    style_notes: str = "",
    manual_prompt: str = "",
) -> str:
    keywords = ", ".join(market_insight.get("keywords") or skill.get("default_keywords") or category.get("seed_keywords") or [])
    reference_block = _compose_reference_block(market_insight.get("sample_products") or [])
    platform_rules = "\n".join(f"- {rule}" for rule in skill.get("platform_rules", []))
    negative_rules = "\n".join(f"- {rule}" for rule in skill.get("negative_constraints", []))
    creative_rule = skill.get("creative_overrides", {}).get(creative_mode["id"], "")
    product_label = product_name.strip() or category["label"]
    market_time = _format_local_time(str(market_insight.get("fetched_at") or ""))

    prompt_parts = [
        "你是一名资深跨境电商视觉总监，请结合我上传的商品原图，为海外电商生成一张高转化图片。",
        f"目标平台：{skill.get('display_name', skill.get('name', ''))}",
        f"平台定位：{skill.get('positioning', '')}",
        f"商品品类：{category['label']}",
        f"商品名称：{product_label}",
        f"创意类型：{creative_mode['label']}（{creative_mode['brief']}）",
        f"市场热词：{keywords or 'premium, modern, clean'}",
        f"本次商品检索词：{market_insight.get('source_query', product_label)}",
        f"实时市场参考：抓取自 {market_insight.get('source_label', '')}，时间 {market_time}",
        market_insight.get("source_note", ""),
        "参考热卖样图标题：",
        reference_block,
        "必须满足以下平台要求：",
        platform_rules,
    ]

    if creative_rule:
        prompt_parts.extend(["当前创意重点：", creative_rule])

    if selling_points.strip():
        prompt_parts.extend(["产品卖点：", selling_points.strip()])

    if style_notes.strip():
        prompt_parts.extend(["补充风格要求：", style_notes.strip()])

    if manual_prompt.strip():
        prompt_parts.extend(["用户补充提示：", manual_prompt.strip()])

    prompt_parts.extend(
        [
            "输出要求：",
            "- 保留上传商品的核心造型、材质和结构真实性，不要凭空替换商品主体。",
            "- 画面要高级、干净、国际化，适合直接用于商品详情页、主图或广告落地页。",
            "- 如果是卖点信息图，使用精炼的英文短标签，控制在 3-5 组，排版整洁，不遮挡产品主体。",
            "- 适度参考热卖款的光影、构图、卖点表达与配色节奏，但不要照搬其他品牌标识。",
            "- 强化商业摄影质感：真实高光、阴影层次、细节锐利、主体聚焦、背景纯净或轻场景。",
            "避免事项：",
            negative_rules,
        ]
    )
    return "\n".join(part for part in prompt_parts if part)


def compose_skill_bundle(
    *,
    skill_id: str,
    category_id: str,
    creative_mode_id: str,
    search_keywords: str = "",
    product_name: str = "",
    selling_points: str = "",
    style_notes: str = "",
    manual_prompt: str = "",
    force_refresh: bool = False,
    ttl_sec: int = 1800,
    timeout_sec: int = 20,
    max_items: int = 6,
) -> dict[str, Any]:
    skill = get_skillpack(skill_id)
    category = get_category(category_id or skill.get("default_category_id", ""))
    creative_mode = get_creative_mode(creative_mode_id)
    search_context = _build_search_context(
        category,
        search_keywords=search_keywords,
        product_name=product_name,
        selling_points=selling_points,
        manual_prompt=manual_prompt,
    )
    warnings: list[str] = []

    try:
        market_insight = refresh_market_insight(
            skill,
            category,
            search_context,
            force_refresh=force_refresh,
            ttl_sec=ttl_sec,
            timeout_sec=timeout_sec,
            max_items=max_items,
        )
    except Exception as exc:
        warnings.append(f"实时抓取失败，已使用内置热词兜底：{type(exc).__name__}: {exc}")
        market_insight = _fallback_market_insight(skill, category, search_context, str(exc))

    prompt_preview = compose_prompt(
        skill,
        category,
        creative_mode,
        market_insight,
        product_name=product_name,
        selling_points=selling_points,
        style_notes=style_notes,
        manual_prompt=manual_prompt,
    )

    return {
        "skill": {
            "id": skill.get("id"),
            "name": skill.get("name"),
            "display_name": skill.get("display_name"),
            "platform": skill.get("platform"),
            "site_name": skill.get("site_name"),
            "description": skill.get("description"),
        },
        "category": {k: v for k, v in category.items() if k not in {"focus_terms"}},
        "creative_mode": creative_mode,
        "search_context": search_context,
        "market_insight": market_insight,
        "prompt_preview": prompt_preview,
        "warnings": warnings,
    }
