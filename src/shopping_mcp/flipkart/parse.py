from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlparse

SITE = "https://www.flipkart.com"


def resolve_image(url: str | None, width: int = 400, height: int = 400, quality: int = 70) -> str | None:
    if not url:
        return None
    return (
        url.replace("{@width}", str(width))
        .replace("{@height}", str(height))
        .replace("{@quality}", str(quality))
    )


def absolute_url(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path.split("#", 1)[0]
    if path.startswith("/"):
        return SITE + path
    return urljoin(SITE + "/", path)


def title_from_url(url: str | None) -> str | None:
    if not url:
        return None
    path = urlparse(url).path
    if "/p/" not in path:
        return None
    slug = path.split("/p/", 1)[0].strip("/")
    if not slug or slug == "p":
        return None
    return slug.replace("-", " ").strip() or None


def _clean_title(title: str | None) -> str | None:
    if not title:
        return None
    for sep in (" Rs.", " Price in India", " Online - "):
        if sep in title:
            title = title.split(sep, 1)[0]
    title = title.strip(" -")
    if title in {"EmergingElectronics Atlas PP", "Flipkart"}:
        return None
    return title or None


def _walk(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _walk(item)


def _collect_texts(obj: Any) -> list[str]:
    texts: list[str] = []
    for node in _walk(obj):
        for key in ("text", "title", "subtitle"):
            value = node.get(key)
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    texts.append(cleaned)
    return texts


def _snippet_texts(snippets: Any) -> list[str]:
    out: list[str] = []
    for node in _walk(snippets):
        if node.get("type") == "RichTextValue" and isinstance(node.get("text"), str):
            text = node["text"].strip()
            if text:
                out.append(text)
    merged: list[str] = []
    buf = ""
    for part in out:
        if part.startswith("₹") or part.startswith("Off") or part in {"Upto", "Upto "}:
            buf += part
            continue
        if buf:
            merged.append((buf + " " + part).strip())
            buf = ""
        else:
            merged.append(part)
    if buf:
        merged.append(buf.strip())
    return merged


def _price_block(pricing: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pricing:
        return None
    final = pricing.get("finalPrice") or {}
    mrp = pricing.get("mrp") or {}
    return {
        "selling_price": final.get("value"),
        "selling_price_text": final.get("decimalValue"),
        "currency": final.get("currency") or mrp.get("currency") or "INR",
        "mrp": mrp.get("value"),
        "discount_percent": pricing.get("totalDiscount"),
        "discount_amount": pricing.get("discountAmount"),
        "delivery_charge": (pricing.get("deliveryCharge") or {}).get("value"),
    }


def parse_product_card(card: dict[str, Any]) -> dict[str, Any]:
    info = (card.get("productInfo") or {}).get("value") or {}
    action = (card.get("productInfo") or {}).get("action") or {}
    params = action.get("params") or {}
    rating = info.get("rating") or {}
    media = info.get("media") or {}
    images = [
        resolve_image(img.get("url"))
        for img in (media.get("images") or [])
        if isinstance(img, dict)
    ]
    path = info.get("baseUrl") or action.get("url")
    return {
        "pid": info.get("id") or params.get("productId"),
        "listing_id": info.get("listingId") or (params.get("lids") or [None])[0],
        "item_id": info.get("itemId"),
        "brand": info.get("productBrand"),
        "title": (info.get("titles") or {}).get("title"),
        "subtitle": (info.get("titles") or {}).get("subtitle"),
        "url": absolute_url(path),
        "in_stock": (info.get("availability") or {}).get("displayState") == "IN_STOCK",
        "availability": (info.get("availability") or {}).get("displayState"),
        "price": _price_block(info.get("pricing")),
        "rating": rating.get("average"),
        "rating_count": rating.get("count"),
        "review_count": rating.get("reviewCount"),
        "key_specs": info.get("keySpecs") or [],
        "highlights": info.get("minKeySpecs") or [],
        "warranty": info.get("warrantySummary"),
        "category": (info.get("analyticsData") or {}).get("category"),
        "vertical": info.get("vertical"),
        "image": images[0] if images else None,
        "images": [img for img in images if img][:8],
        "badges": _snippet_texts(card.get("snippets")),
    }


def _flatten_filter_values(values: Any) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    if not isinstance(values, list):
        return options
    for value in values:
        if not isinstance(value, dict):
            continue
        resource = value.get("resource") or {}
        title = value.get("title") or value.get("displayValue")
        params = resource.get("params")
        if title and params:
            options.append(
                {
                    "title": title,
                    "params": params,
                    "count": value.get("count"),
                    "selected": bool(resource.get("selected")),
                }
            )
        if value.get("values"):
            options.extend(_flatten_filter_values(value["values"]))
    return options


def parse_filters(data: dict[str, Any]) -> list[dict[str, Any]]:
    facets = (((data.get("filters") or {}).get("facetResponse") or {}).get("facets")) or []
    out: list[dict[str, Any]] = []
    for facet in facets:
        if not isinstance(facet, dict):
            continue
        options = _flatten_filter_values(facet.get("values") or [])
        out.append(
            {
                "id": facet.get("id"),
                "title": facet.get("title") or facet.get("id"),
                "options": options[:40],
            }
        )
    return out


def parse_search(payload: dict[str, Any]) -> dict[str, Any]:
    response = payload.get("RESPONSE") or {}
    slots = response.get("slots") or []
    products: list[dict[str, Any]] = []
    filters: list[dict[str, Any]] = []
    sort_options: list[dict[str, Any]] = []
    pagination: dict[str, Any] = {}
    total = None
    query = None
    breadcrumbs: list[str] = []

    for slot in slots:
        widget = slot.get("widget") or {}
        wtype = widget.get("type")
        data = widget.get("data") or {}
        if wtype == "PRODUCT_SUMMARY":
            for card in data.get("products") or []:
                parsed = parse_product_card(card)
                if parsed.get("pid") or parsed.get("title"):
                    products.append(parsed)
        elif wtype == "FILTERS":
            filters = parse_filters(data)
        elif wtype == "FILTER_SORT_OPTIONS":
            total = data.get("totalProducts")
            query = data.get("query")
            breadcrumbs = [b.get("title") for b in (data.get("breadCrumbs") or []) if b.get("title")]
            for option in data.get("sortOptions") or []:
                value = option.get("value") or {}
                action = option.get("action") or {}
                params = action.get("params") or {}
                sort_options.append(
                    {
                        "title": value.get("title"),
                        "value": params.get("value"),
                        "selected": bool(value.get("selected")),
                    }
                )
        elif wtype == "PAGINATION_BAR":
            pagination = {
                "current_page": data.get("currentPage"),
                "total_pages": data.get("totalPages"),
            }

    return {
        "query": query,
        "total_products": total,
        "page": pagination.get("current_page") or 1,
        "total_pages": pagination.get("total_pages"),
        "breadcrumbs": breadcrumbs,
        "sort_options": sort_options,
        "products": products,
        "filters": [
            {
                "id": f["id"],
                "title": f["title"],
                "option_count": len(f["options"]),
                "top_options": f["options"][:8],
            }
            for f in filters
        ],
    }


def parse_autosuggest(payload: dict[str, Any]) -> dict[str, Any]:
    response = payload.get("RESPONSE") or {}
    suggestions: list[dict[str, Any]] = []
    for item in response.get("suggestions") or []:
        component = ((item.get("data") or {}).get("component") or {})
        value = component.get("value") or {}
        action = component.get("action") or {}
        params = action.get("params") or {}
        kind = value.get("type") or item.get("type")
        title = value.get("title") or value.get("backFill")
        if not title:
            continue
        entry: dict[str, Any] = {
            "type": kind,
            "title": title,
            "store": value.get("store"),
            "search_url": absolute_url(action.get("url") or action.get("originalUrl")),
            "query": params.get("query"),
        }
        if kind == "PRODUCT" or "PRODUCT" in str(kind):
            entry["pid"] = params.get("productId") or value.get("id")
        image = resolve_image(value.get("imageUrl"))
        if image:
            entry["image"] = image
        suggestions.append(entry)
    return {"query": response.get("query"), "suggestions": suggestions}


def _widget_name(slot: dict[str, Any]) -> str:
    widget = slot.get("widget") or {}
    return widget.get("widgetName") or widget.get("type") or ""


def _widget_data(slot: dict[str, Any]) -> dict[str, Any]:
    widget = slot.get("widget") or {}
    data = widget.get("data")
    return data if isinstance(data, dict) else {}


def _psi(payload: dict[str, Any]) -> dict[str, Any]:
    page_data = (payload.get("RESPONSE") or {}).get("pageData") or {}
    events = ((page_data.get("pageContext") or {}).get("fdpEventTracking") or {}).get("events") or {}
    psi = events.get("psi")
    return psi if isinstance(psi, dict) else {}


def _seo(payload: dict[str, Any]) -> dict[str, Any]:
    page_data = (payload.get("RESPONSE") or {}).get("pageData") or {}
    seo = (page_data.get("seoData") or {}).get("seo") or {}
    return seo if isinstance(seo, dict) else {}


def _highlights(data: dict[str, Any]) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for node in _walk(data):
        if "highlight-card_1" not in node and "highlight-card-one-label_0" not in node:
            continue
        card = (node.get("highlight-card_1") or {}).get("value") or {}
        title = ((card.get("label_0") or {}).get("value") or {}).get("text")
        subtitle = ((card.get("label_1") or {}).get("value") or {}).get("text")
        if not title:
            one = (node.get("highlight-card-one-label_0") or {}).get("value") or {}
            title = ((one.get("label_1") or {}).get("value") or {}).get("text")
        if not title:
            continue
        key = (title.strip(), (subtitle or "").strip())
        if key in seen:
            continue
        seen.add(key)
        item = {"title": key[0]}
        if key[1]:
            item["detail"] = key[1]
        cards.append(item)
    return cards


def _images(data: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for node in _walk(data):
        url = node.get("dynamicImageUrl") or node.get("url")
        if not isinstance(url, str):
            continue
        if "flixcart.com/image" not in url and "fkcdn.com/image" not in url:
            continue
        resolved = resolve_image(url)
        if resolved and resolved not in seen:
            seen.add(resolved)
            urls.append(resolved)
    return urls


def _reviews(data: dict[str, Any]) -> list[dict[str, Any]]:
    texts = _collect_texts(data)
    skip = {
        "ratings and reviews",
        "show all reviews",
        "verified buyers",
        "verified buyer",
        "very good",
        "+4",
    }
    reviews: list[dict[str, Any]] = []
    i = 0
    while i < len(texts) - 2:
        a, b, c = texts[i], texts[i + 1], texts[i + 2]
        if a.lower() == "verified buyer":
            reviews.append(
                {
                    "author": b,
                    "when": c if i + 2 < len(texts) else None,
                    "title": texts[i + 3] if i + 3 < len(texts) else None,
                }
            )
            i += 4
            continue
        i += 1
    if not reviews:
        titles = [t for t in texts if t.lower() not in skip and not t.lower().startswith("based on")]
        reviews = [{"title": t} for t in titles[:12]]
    return reviews[:12]


def _aspect_ratings(psi: dict[str, Any]) -> list[dict[str, Any]]:
    return (psi.get("pr") or {}).get("parameterRating") or []


def _offers(slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    offers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for slot in slots:
        widget = slot.get("widget") or {}
        data = widget.get("data") or {}
        if widget.get("type") == "ATLAS_NEP_V2" or widget.get("widgetName") == "ATLAS_NEP_V2":
            for offer in data.get("offers") or []:
                value = offer.get("value") or offer
                title = value.get("title") or value.get("offerTitle")
                if title and title not in seen:
                    seen.add(title)
                    offers.append(
                        {
                            "title": title,
                            "subtitle": value.get("subtitle") or value.get("header"),
                            "type": value.get("offerType") or value.get("type"),
                        }
                    )
                for summary in value.get("offerSummariesRC") or []:
                    sval = summary.get("value") or {}
                    ot = sval.get("offerTitle")
                    if ot and ot not in seen:
                        seen.add(ot)
                        offers.append({"title": ot, "type": sval.get("offerType")})
        for node in _walk(data):
            title = node.get("offerTitle") or node.get("contentTitle")
            if not isinstance(title, str):
                continue
            if title.lower() in {"wow deal", "bank offers"} or title in seen:
                if title.lower() == "bank offers" and title not in seen:
                    seen.add(title)
                    offers.append({"title": title, "amount": node.get("metaInfoLabelValue")})
                continue
    extra_texts = []
    for slot in slots:
        name = _widget_name(slot)
        if "PRICING" in name or "NEP" in name or "BENEFIT" in name:
            extra_texts.extend(_collect_texts(_widget_data(slot)))
    for text in extra_texts:
        low = text.lower()
        if any(token in low for token in ("offer", "emi", "off", "cashback", "coupon")):
            if text not in seen and len(text) < 160:
                seen.add(text)
                offers.append({"title": text})
    return offers[:20]


def _label_text(node: Any) -> str | None:
    if not isinstance(node, dict):
        return None
    value = (node.get("value") or node).get("text") if isinstance(node.get("value"), dict) else node.get("text")
    if isinstance(value, list):
        parts = [str(v).strip() for v in value if str(v).strip()]
        return ", ".join(parts) if parts else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _spec_pairs(data: dict[str, Any]) -> list[dict[str, str]]:
    pairs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for node in _walk(data):
        name = _label_text(node.get("label_0") or {})
        value = _label_text(node.get("label_1") or {}) or _label_text(node.get("label_2") or {})
        if not name or not value:
            continue
        if name.lower() in {"dummy", "specifications", "features"}:
            continue
        if name.lower() == value.lower():
            continue
        key = (name.lower(), value.lower())
        if key in seen:
            continue
        seen.add(key)
        pairs.append({"name": name, "value": value})
    return pairs[:80]


def _card_from_node(card: dict[str, Any], heading: str | None) -> dict[str, Any] | None:
    pid = None
    url = None
    title = None
    rating = None
    prices: list[str] = []
    image = None
    for inner in _walk(card):
        tracking = inner.get("tracking")
        if isinstance(tracking, dict) and tracking.get("productId"):
            pid = pid or tracking.get("productId")
            title = title or tracking.get("contentTitle") or tracking.get("title_primary")
        action = inner.get("action")
        if isinstance(action, dict):
            params = action.get("params") or {}
            pid = pid or params.get("productId") or (params.get("pids") or [None])[0]
            url = url or action.get("url") or action.get("originalUrl")
        if isinstance(inner.get("rating"), (int, float)):
            rating = inner["rating"]
        text = inner.get("text")
        if isinstance(text, str):
            stripped = text.strip()
            if stripped.startswith("₹"):
                prices.append(stripped)
            elif (
                title is None
                and len(stripped) > 20
                and "off" not in stripped.lower()
                and "bank" not in stripped.lower()
            ):
                title = stripped
        img = inner.get("dynamicImageUrl")
        if isinstance(img, str) and image is None:
            image = resolve_image(img)
    if not pid:
        return None
    abs_url = absolute_url(url) if url else absolute_url(f"/p/itm?pid={pid}")
    return {
        "pid": pid,
        "title": title or title_from_url(abs_url),
        "url": abs_url,
        "price_text": prices[0] if prices else None,
        "mrp_text": prices[1] if len(prices) > 1 else None,
        "rating": rating,
        "image": image,
        "section": heading,
    }


def _similar_from_slot(slot: dict[str, Any], heading: str | None = None) -> list[dict[str, Any]]:
    data = _widget_data(slot)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in _walk(data):
        for key, value in node.items():
            if "product-card" not in str(key).lower() or not isinstance(value, dict):
                continue
            card = value.get("value") if isinstance(value.get("value"), dict) else value
            parsed = _card_from_node(card, heading)
            if not parsed or parsed["pid"] in seen:
                continue
            seen.add(parsed["pid"])
            items.append(parsed)
    if items:
        return items
    for node in _walk(data):
        tracking = node.get("tracking")
        if not isinstance(tracking, dict) or not tracking.get("productId"):
            continue
        pid = tracking["productId"]
        if pid in seen:
            continue
        seen.add(pid)
        items.append(
            {
                "pid": pid,
                "title": tracking.get("contentTitle") or tracking.get("title_primary"),
                "url": None,
                "section": heading,
            }
        )
    return items


def _variants(data: dict[str, Any]) -> list[str]:
    skip = {"variant:", "variant", "out of stock", "1 left"}
    texts: list[str] = []
    seen: set[str] = set()
    for text in _collect_texts(data):
        low = text.lower().strip()
        if low in skip or text.startswith("₹") or text.startswith("↓") or "%" in text:
            continue
        if re.fullmatch(r"[\d,]+", text):
            continue
        if text in seen:
            continue
        seen.add(text)
        texts.append(text)
    return texts[:20]


def _fbt(data: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for product in data.get("products") or []:
        value = product.get("value") or {}
        tracking = product.get("tracking") or {}
        titles = value.get("titles") or {}
        images = ((value.get("media") or {}).get("images") or [])
        image = resolve_image(images[0].get("url") if images else None)
        out.append(
            {
                "pid": value.get("id") or tracking.get("productId"),
                "listing_id": value.get("listingId"),
                "title": titles.get("title") or tracking.get("contentTitle"),
                "subtitle": titles.get("subtitle"),
                "image": image,
            }
        )
    return out


def parse_product(payload: dict[str, Any]) -> dict[str, Any]:
    response = payload.get("RESPONSE") or {}
    slots = response.get("slots") or []
    page_data = response.get("pageData") or {}
    page_context = page_data.get("pageContext") or {}
    psi = _psi(payload)
    seo = _seo(payload)
    pricing = psi.get("ppd") or {}
    listing = psi.get("pls") or {}
    rating_info = psi.get("pr") or {}
    policy = psi.get("pi") or {}
    sla = (psi.get("sla") or [{}])[0] if psi.get("sla") else {}

    title = None
    brand = psi.get("bd")
    highlights: list[dict[str, str]] = []
    images: list[str] = []
    reviews: list[dict[str, Any]] = []
    specs: list[dict[str, str]] = []
    similar: list[dict[str, Any]] = []
    variants: list[str] = []
    fbt: list[dict[str, Any]] = []
    delivery_notes: list[str] = []
    seller = None
    qna_note = None

    for slot in slots:
        name = _widget_name(slot)
        data = _widget_data(slot)
        if name == "ATLAS_PRODUCT_TITLE":
            texts = _collect_texts(data)
            skip_title = {"visit store", "visit brand store"}
            if texts:
                maybe_brand = texts[0]
                if len(maybe_brand) <= 40 and maybe_brand.lower() not in skip_title:
                    brand = brand or maybe_brand
            for text in texts:
                if text.lower() in skip_title:
                    continue
                if len(text) > 24:
                    title = text
                    break
        elif name == "ATLAS_PRODUCT_HIGHLIGHTS":
            highlights = _highlights(data)
        elif "MULTIMEDIA" in name:
            for url in _images(data):
                if url not in images:
                    images.append(url)
        elif name == "ATLAS_RATING_AND_REVIEWS":
            reviews = _reviews(data)
        elif "RICH_PRODUCT_DETAILS" in name:
            specs.extend(_spec_pairs(data))
        elif name == "ATLAS_COMPOSED_SWATCH":
            variants = _variants(data)
        elif name in {"ATLAS_DELIVERY", "ATLAS_DELIVERY_V2"}:
            delivery_notes = [
                t
                for t in _collect_texts(data)
                if t.lower()
                not in {
                    "delivery details",
                    "delivery",
                    "select delivery location",
                    "see other sellers",
                }
            ]
            for text in delivery_notes:
                if text.lower().startswith("seller:"):
                    seller = text.split(":", 1)[-1].strip()
        elif "PMU" in name:
            heading = next(
                (
                    t
                    for t in _collect_texts(data)[:6]
                    if any(token in t.lower() for token in ("like", "similar", "compare"))
                ),
                "Similar products",
            )
            similar.extend(_similar_from_slot(slot, heading))
        elif name == "FREQUENTLY_BOUGHT_TOGETHER" or slot.get("widget", {}).get("type") == "FREQUENTLY_BOUGHT_TOGETHER":
            fbt = _fbt(data)
        elif name == "ATLAS_QNA":
            texts = _collect_texts(data)
            qna_note = next((t for t in texts if "question" in t.lower()), None)

    pid = page_context.get("productId") or psi.get("fsn")
    listing_id = page_context.get("listingId") or listing.get("listingId")
    title = _clean_title(title) or _clean_title(page_data.get("pageTitle")) or _clean_title(
        seo.get("ogTitle") or seo.get("title")
    )
    page_url = None
    og_url = seo.get("ogUrl") or seo.get("canonical")
    if pid:
        page_url = f"{SITE}/p/itm?pid={pid}"
        if listing_id:
            page_url += f"&lid={listing_id}"
    elif og_url:
        page_url = absolute_url(og_url)

    spec_seen: set[tuple[str, str]] = set()
    unique_specs: list[dict[str, str]] = []
    for spec in specs:
        key = (spec["name"].lower(), spec["value"].lower())
        if key in spec_seen:
            continue
        spec_seen.add(key)
        unique_specs.append(spec)

    similar_seen: set[str] = set()
    unique_similar: list[dict[str, Any]] = []
    for item in similar:
        if not item.get("pid") or item["pid"] == pid or item["pid"] in similar_seen:
            continue
        similar_seen.add(item["pid"])
        unique_similar.append(item)

    return {
        "pid": pid,
        "listing_id": listing_id,
        "brand": brand,
        "title": title,
        "url": page_url,
        "in_stock": listing.get("availabilityStatus") == "IN_STOCK" or listing.get("isAvailable"),
        "availability": listing.get("availabilityStatus"),
        "flipkart_assured": bool(listing.get("isFA") or listing.get("fa")),
        "seller_id": listing.get("sellerId"),
        "seller": seller,
        "price": {
            "selling_price": pricing.get("finalPrice") or pricing.get("fsp"),
            "mrp": pricing.get("mrp"),
            "special_price": bool(pricing.get("isSpecialPrice") or pricing.get("specialPrice")),
            "bank_offer_price": pricing.get("nepPrice") or None,
            "currency": "INR",
        },
        "rating": rating_info.get("rating"),
        "rating_count": rating_info.get("ratingsCount"),
        "review_count": rating_info.get("reviewsCount"),
        "aspect_ratings": _aspect_ratings(psi),
        "cod_available": policy.get("isCODAvailable") or policy.get("codavailable"),
        "return_policy": policy.get("returnPolicy"),
        "delivery_sla_days": sla.get("maxSLA"),
        "delivery": delivery_notes[:12],
        "variants": variants,
        "highlights": highlights,
        "key_specs": unique_specs,
        "offers": _offers(slots),
        "images": images[:12],
        "reviews": reviews,
        "qna": qna_note,
        "frequently_bought_together": fbt,
        "similar_products": unique_similar[:12],
    }


def looks_like_pid(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Z]{3}[A-Z0-9]{10,}", value.strip()))
