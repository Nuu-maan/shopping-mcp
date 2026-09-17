from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from urllib.parse import parse_qs, urlparse

SITE = "https://www.amazon.in"

_CARD_SPLIT = re.compile(r'(?=<div role="listitem" data-asin=")')
_ASIN = re.compile(r'<div role="listitem" data-asin="([A-Z0-9]{10})"')
_INR = re.compile(r"[₹]?\s*([\d,]+(?:\.\d+)?)")


def parse_inr(text: str | None) -> int | None:
    if not text:
        return None
    match = _INR.search(text.replace("\xa0", " "))
    if not match:
        return None
    try:
        return int(float(match.group(1).replace(",", "")))
    except ValueError:
        return None


def _clean(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = unescape(re.sub(r"\s+", " ", text)).strip()
    return cleaned or None


def _first(pattern: str, html: str, flags: int = 0) -> str | None:
    match = re.search(pattern, html, flags)
    return _clean(match.group(1)) if match else None


def parse_search(html: str) -> dict[str, Any]:
    products: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chunk in _CARD_SPLIT.split(html)[1:]:
        asin_match = _ASIN.match(chunk)
        if not asin_match:
            continue
        asin = asin_match.group(1)
        if not asin or asin in seen:
            continue
        seen.add(asin)
        sponsored = "AdHolder" in chunk[:600] or "Sponsored Ad" in chunk[:2000]
        title = _first(r'<h2[^>]*aria-label="([^"]+)"', chunk)
        if title and title.lower().startswith("sponsored ad - "):
            title = title[15:]
        href = _first(rf'href="(/[^"]+/dp/{asin}[^"]*)"', chunk)
        url = SITE + href.split("?")[0] if href else f"{SITE}/dp/{asin}"
        price_text = _first(
            r'<span class="a-price"[^>]*>\s*<span class="a-offscreen">([^<]+)</span>',
            chunk,
        )
        mrp_text = _first(
            r'M\.R\.P:\s*</span>\s*<span class="a-price a-text-price"[^>]*>\s*<span class="a-offscreen">([^<]+)</span>',
            chunk,
        ) or _first(r"M\.R\.P:\s*([^<]+)", chunk)
        products.append(
            {
                "asin": asin,
                "sponsored": sponsored,
                "brand": _first(
                    r'<h2 class="a-size-mini[^"]*">\s*<span class="a-size-medium a-color-base">([^<]+)</span>',
                    chunk,
                ),
                "title": title,
                "url": url,
                "price": {
                    "selling_price": parse_inr(price_text),
                    "selling_price_text": price_text,
                    "mrp": parse_inr(mrp_text),
                    "mrp_text": mrp_text,
                    "currency": "INR",
                },
                "rating": _float(_first(r"([\d.]+) out of 5 stars", chunk)),
                "rating_count": _int(_first(r'aria-label="([\d,]+) ratings"', chunk)),
                "image": _first(
                    r'src="(https://m\.media-amazon\.com/images/I/[^"]+)"',
                    chunk,
                ),
            }
        )

    total = _first(r"of over ([\d,]+) results", html) or _first(
        r"([\d,]+) results for", html
    )
    page_range = re.search(r"([\d,]+)-([\d,]+) of", html)
    return {
        "marketplace": "amazon.in",
        "total_products_text": total,
        "page_start": _int(page_range.group(1)) if page_range else None,
        "page_end": _int(page_range.group(2)) if page_range else None,
        "products": products,
        "filters": parse_filters(html),
        "sort_options": [
            {"title": "Featured", "value": "relevance"},
            {"title": "Price: Low to High", "value": "price_asc"},
            {"title": "Price: High to Low", "value": "price_desc"},
            {"title": "Avg. Customer Review", "value": "rating"},
            {"title": "Newest Arrivals", "value": "newest"},
        ],
    }


def parse_filters(html: str) -> list[dict[str, Any]]:
    idx = html.find('id="s-refinements"')
    block = html[idx : idx + 90000] if idx >= 0 else html
    links = re.findall(
        r'href="(/s\?[^"]*)"[\s\S]{0,400}?<span class="a-size-base a-color-base(?: puis-bold-weight-text)?">([^<]+)</span>',
        block,
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for href, title in links:
        title = unescape(title).strip()
        query = parse_qs(urlparse(unescape(href)).query)
        rh = (query.get("rh") or [""])[0]
        if not rh:
            continue
        facet = rh.split(",")[-1]
        family = facet.split(":")[0]
        if family not in grouped:
            grouped[family] = []
            order.append(family)
        grouped[family].append({"title": title, "rh": facet})
    return [{"id": family, "options": grouped[family][:20]} for family in order]


def parse_product(html: str, requested: str | None = None) -> dict[str, Any]:
    asin = (
        _first(r'name="ASIN"\s+value="([A-Z0-9]{10})"', html)
        or _first(r'data-csa-c-asin="([A-Z0-9]{10})"', html)
        or _asin_from(requested)
    )
    title = _first(r'id="productTitle"[^>]*>\s*([^<]+)', html)
    brand = _first(
        r'id="bylineInfo"[^>]*>\s*Visit the ([^<]+) Store',
        html,
    ) or _first(
        r'<span class="a-size-base a-text-bold">Brand</span>[\s\S]{0,200}?<span class="a-size-base po-break-word">([^<]+)</span>',
        html,
    )
    selling_text = _first(
        r'id="apex-pricetopay-accessibility-label"[^>]*>\s*([^<]+)',
        html,
    )
    selling = parse_inr(selling_text)
    if selling is None:
        whole = _first(
            r'class="a-price-whole">([\d,]+)',
            html[html.find("corePriceDisplay") : html.find("corePriceDisplay") + 4000]
            if "corePriceDisplay" in html
            else html,
        )
        selling = parse_inr(whole)
    hidden = _first(r'name="priceValue"[^>]*value="([^"]+)"', html)
    if selling is None and hidden:
        try:
            selling = int(float(hidden))
        except ValueError:
            selling = None
    mrp_text = _first(r"M\.R\.P\.:?\s*₹?\s*([\d,]+\.?\d*)", html)
    availability = _first(
        r'id="availability"[\s\S]{0,600}?<span[^>]*>\s*([^<]+)',
        html,
    )
    in_stock = bool(availability and "in stock" in availability.lower())
    rating = _float(_first(r'id="acrPopover"[^>]*title="([\d.]+) out of 5 stars"', html))
    review_count = _int(_first(r'id="acrCustomerReviewText"[^>]*>\s*\(?([\d,]+)', html))
    seller = _first(
        r"Sold by[\s\S]{0,400}?<a[^>]*>\s*([^<]+)",
        html,
    )
    highlights = [
        t
        for t in (
            _clean(x)
            for x in re.findall(
                r'<span class="a-list-item">\s*([^<]+)',
                _block(html, "feature-bullets", 9000),
            )
        )
        if t and t.lower() != "about this item"
    ]
    specs = []
    overview = _block(html, "productOverview_feature_div", 8000)
    for name, value in re.findall(
        r'<span class="a-size-base a-text-bold">([^<]+)</span>[\s\S]{0,250}?<span class="a-size-base po-break-word">([^<]+)</span>',
        overview,
    ):
        specs.append({"name": _clean(name), "value": _clean(value)})
    images = []
    seen_img: set[str] = set()
    for url in re.findall(
        r'"(?:hiRes|large)":"(https://m\.media-amazon\.com/images/I/[^"]+)"',
        html,
    ):
        if url not in seen_img:
            seen_img.add(url)
            images.append(url)
    savings = _first(
        r'class="[^"]*savingsPercentage[^"]*">([^<]+)',
        html,
    )
    return {
        "marketplace": "amazon.in",
        "asin": asin,
        "brand": brand,
        "title": title,
        "url": f"{SITE}/dp/{asin}" if asin else requested,
        "in_stock": in_stock,
        "availability": availability,
        "seller": seller,
        "price": {
            "selling_price": selling,
            "mrp": parse_inr(mrp_text),
            "currency": "INR",
            "savings_percent_text": savings,
        },
        "rating": rating,
        "review_count": review_count,
        "delivery": parse_delivery(html),
        "variants": parse_variants(html, current_asin=asin),
        "reviews": parse_reviews(html),
        "highlights": highlights[:12],
        "key_specs": [s for s in specs if s.get("name") and s.get("value")],
        "images": images[:12],
        "requested": requested,
    }


def parse_delivery(html: str) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for match in re.finditer(
        r'data-csa-c-delivery-price="([^"]*)"[\s\S]{0,500}?data-csa-c-delivery-time="([^"]*)"(?:[\s\S]{0,400}?data-csa-c-delivery-cutoff="([^"]*)")?',
        html,
    ):
        price, when, cutoff = match.group(1), match.group(2), match.group(3)
        key = (price, when)
        if not when or key in seen:
            continue
        seen.add(key)
        item = {"when": unescape(when), "price": unescape(price) if price else None}
        if cutoff:
            item["cutoff"] = unescape(cutoff)
        options.append(item)
    return options


def parse_variants(html: str, current_asin: str | None = None) -> list[dict[str, Any]]:
    match = re.search(r'"dimensionValuesDisplayData"\s*:\s*(\{.*?\})', html)
    if not match:
        return []
    try:
        mapping = json.loads(match.group(1))
    except json.JSONDecodeError:
        return []
    variants = []
    for asin, labels in mapping.items():
        if not isinstance(labels, list):
            continue
        variants.append(
            {
                "asin": asin,
                "label": ", ".join(str(x) for x in labels),
                "selected": asin == current_asin,
                "url": f"{SITE}/dp/{asin}",
            }
        )
    return variants


def parse_reviews(html: str) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    for chunk in html.split('data-hook="review"')[1:9]:
        author = _first(r'class="a-profile-name">([^<]+)', chunk)
        rating = _float(_first(r"([\d.]+) out of 5 stars", chunk))
        title = _first(r'data-hook="reviewTitle"[^>]*>\s*([^<]+)', chunk)
        when = _first(r'data-hook="review-date"[^>]*>\s*([^<]+)', chunk)
        body = _first(
            r'data-hook="reviewRichContentContainer"[^>]*>\s*<p>\s*<span>\s*([^<]+)',
            chunk,
        )
        verified = "Verified Purchase" in chunk[:2500]
        if not (author or title or body):
            continue
        reviews.append(
            {
                "author": author,
                "rating": rating,
                "title": title,
                "body": body,
                "when": when,
                "verified_purchase": verified,
            }
        )
    return reviews


def _block(html: str, element_id: str, size: int) -> str:
    idx = html.find(f'id="{element_id}"')
    if idx < 0:
        return ""
    return html[idx : idx + size]


def _asin_from(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(?:/dp/|/gp/product/)?([A-Z0-9]{10})(?:[/?]|$)", value)
    return match.group(1) if match else None


def _int(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(text.replace(",", ""))
    except ValueError:
        return None


def _float(text: str | None) -> float | None:
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_suggestions(payload: dict[str, Any]) -> dict[str, Any]:
    suggestions: list[dict[str, Any]] = []
    for item in payload.get("suggestions") or []:
        kind = item.get("suggType") or item.get("type")
        if kind in {"KeywordSuggestion", "KEYWORD", "PAST_SEARCHES"} or item.get("type") in {
            "KEYWORD",
            "PAST_SEARCHES",
        }:
            value = item.get("value")
            if value:
                suggestions.append(
                    {
                        "type": item.get("type") or "KEYWORD",
                        "query": value,
                        "search_url": f"{SITE}/s?k={value}",
                    }
                )
        elif kind in {"WidgetSuggestion", "WIDGET"}:
            header = ((item.get("metadata") or {}).get("header"))
            for widget in item.get("widgetItems") or []:
                meta = widget.get("metadata") or {}
                suggestions.append(
                    {
                        "type": "WIDGET",
                        "title": meta.get("text"),
                        "subtitle": meta.get("secondaryText"),
                        "header": header,
                        "image": meta.get("image_url"),
                        "url": (SITE + meta["link_url"]) if meta.get("link_url") else None,
                    }
                )
    return {
        "marketplace": "amazon.in",
        "prefix": payload.get("prefix"),
        "suggestions": suggestions,
    }
