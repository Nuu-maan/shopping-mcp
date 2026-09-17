from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from mcp.server.mcpserver import MCPServer

from shopping_mcp.amazon.client import AmazonClient
from shopping_mcp.amazon.parse import parse_product as parse_amazon_product
from shopping_mcp.amazon.parse import parse_search as parse_amazon_search
from shopping_mcp.amazon.parse import parse_suggestions as parse_amazon_suggestions
from shopping_mcp.flipkart.client import FlipkartClient, FlipkartError, product_uri
from shopping_mcp.flipkart.parse import looks_like_pid, parse_autosuggest, parse_product, parse_search

INSTRUCTIONS = """
You are using the shopping MCP (Flipkart, Amazon.in, and more stores later).
Never guess prices, stock, ratings, or specs. Always call tools and quote the JSON numbers.

Flipkart flow:
1. autosuggest — fix a messy query.
2. search_products — live Flipkart cards (pid, selling_price, MRP, rating, specs).
3. list_search_filters — refine with extra_facets.
4. get_product / compare_products — exact Flipkart details.

Amazon.in flow:
1. amazon_autosuggest — fix messy Amazon queries.
2. amazon_search — live Amazon cards (asin, selling_price, MRP, rating).
3. amazon_list_search_filters — refine with extra_rh (Amazon rh= params).
4. amazon_get_product — exact Amazon details by ASIN or /dp/ URL (optional pincode for delivery ETA).
5. amazon_check_delivery — FREE / fastest delivery for an ASIN at a PIN code.

Cross-store:
- compare_marketplaces — search Flipkart and Amazon.in in parallel for the same query.
- When recommending a buy, quote both marketplaces' selling_price from tool output.
- Flipkart ids are pid (e.g. COMGZW3FSJHF2SEU). Amazon ids are ASIN (e.g. B0CGCRRC1Z).
""".strip()


mcp = MCPServer("shopping", instructions=INSTRUCTIONS, log_level="WARNING")

_fk: FlipkartClient | None = None
_amz: AmazonClient | None = None


def _client() -> FlipkartClient:
    global _fk
    if _fk is None:
        _fk = FlipkartClient()
    return _fk


def _amazon() -> AmazonClient:
    global _amz
    if _amz is None:
        _amz = AmazonClient()
    return _amz


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
async def autosuggest(query: str) -> str:
    """Correct and complete a Flipkart search query.

    Use this first when the shopper's wording is messy (typos, missing brand/model).
    Returns suggested queries, category-scoped searches, and sometimes product matches.
    """
    payload = await _client().autosuggest(query)
    return _dump(parse_autosuggest(payload))


@mcp.tool()
async def search_products(
    query: str,
    page: int = 1,
    sort: str = "relevance",
    min_price: int | None = None,
    max_price: int | None = None,
    brands: list[str] | None = None,
    min_rating: int | None = None,
    f_assured: bool = False,
    extra_facets: list[str] | None = None,
) -> str:
    """Search Flipkart and return live product cards.

    Each card includes pid, url, selling price, MRP, discount, rating, stock,
    key specs, and badges (bank offer, only 1 left, exchange, etc).

    sort: relevance | popularity | price_asc | price_desc | newest
    min_rating: 4 means '4★ & above'. brands: e.g. ["ASUS","HP"].
    extra_facets: raw params from list_search_filters, like
    "facets.processor[]=Intel Core i7" or "facets.ssd_capacity[]=512 GB".
    """
    payload = await _client().search(
        query,
        page=page,
        sort=sort,
        min_price=min_price,
        max_price=max_price,
        brands=brands,
        min_rating=min_rating,
        f_assured=f_assured,
        extra_facets=extra_facets,
    )
    parsed = parse_search(payload)
    parsed["search_query"] = query
    return _dump(parsed)


@mcp.tool()
async def list_search_filters(query: str) -> str:
    """List Flipkart facets for a query so you can refine search_products.

    Returns filter ids (brand, price_range, processor, rating, availability, ...)
    and each option's `params` string. Pass those params as extra_facets on
    search_products. Facets change by category — always call this for the
    current query instead of guessing facet names.
    """
    payload = await _client().search(query)
    parsed = parse_search(payload)
    return _dump(
        {
            "query": parsed.get("query") or query,
            "total_products": parsed.get("total_products"),
            "sort_options": parsed.get("sort_options"),
            "filters": parsed.get("filters"),
        }
    )


@mcp.tool()
async def get_product(pid_or_url: str, listing_id: str | None = None) -> str:
    """Fetch one Flipkart product with live price, specs, offers, and reviews.

    Accepts a product URL from search results, or a pid such as COMGZW3FSJHF2SEU.
    Optional listing_id (LST...) selects a specific seller listing.

    Returns selling price, MRP, bank-offer price if present, stock, seller,
    highlights, specification pairs, review titles, similar products, and images.
    Review bodies and pincode-accurate delivery ETAs are not in this capture —
    titles/aspect ratings are. Quote the numeric selling_price from this payload.
    """
    uri = product_uri(pid_or_url, listing_id)
    payload = await _client().product(uri)
    parsed = parse_product(payload)
    if not parsed.get("pid") and looks_like_pid(pid_or_url):
        parsed["pid"] = pid_or_url.strip()
    parsed["requested"] = pid_or_url
    return _dump(parsed)


@mcp.tool()
async def compare_products(pid_or_urls: list[str]) -> str:
    """Fetch 2–5 Flipkart products in parallel for a side-by-side pick.

    Pass pids or product URLs from search_products. Returns the same live
    fields as get_product for each item. Use this instead of mixing remembered
    prices with a new search.
    """
    if not pid_or_urls:
        raise FlipkartError("compare_products needs at least one pid or url")
    items = pid_or_urls[:5]
    client = _client()

    async def one(item: str) -> dict[str, Any]:
        payload = await client.product(product_uri(item))
        parsed = parse_product(payload)
        parsed["requested"] = item
        return parsed

    settled = await asyncio.gather(*[one(item) for item in items], return_exceptions=True)
    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for item, result in zip(items, settled, strict=True):
        if isinstance(result, Exception):
            errors.append(f"{item}: {result}")
        else:
            results.append(result)
    return _dump({"products": results, "errors": errors})


@mcp.tool()
async def get_similar_products(pid_or_url: str) -> str:
    """Return Flipkart's similar / you-may-also-like products for a pid or url."""
    payload = await _client().product(product_uri(pid_or_url))
    parsed = parse_product(payload)
    return _dump(
        {
            "pid": parsed.get("pid"),
            "title": parsed.get("title"),
            "similar_products": parsed.get("similar_products"),
            "frequently_bought_together": parsed.get("frequently_bought_together"),
        }
    )


@mcp.tool()
async def amazon_autosuggest(query: str) -> str:
    """Correct and complete an Amazon.in search query.

    Use this when the shopper's wording is messy (typos like 'lonovo loq').
    Returns keyword suggestions to feed into amazon_search or compare_marketplaces.
    """
    payload = await _amazon().autosuggest(query)
    return _dump(parse_amazon_suggestions(payload))


@mcp.tool()
async def amazon_search(
    query: str,
    page: int = 1,
    sort: str = "relevance",
    min_price: int | None = None,
    max_price: int | None = None,
    extra_rh: list[str] | None = None,
    include_sponsored: bool = False,
) -> str:
    """Search Amazon.in and return live product cards.

    Each card includes asin, url, selling price, MRP, rating, and image.
    sort: relevance | price_asc | price_desc | rating | newest
    min_price / max_price are INR integers (Amazon encodes them as p_36 rupees*100).
    extra_rh: raw rh values from amazon_list_search_filters, e.g. ["p_123:391242"] for Lenovo.
    Sponsored cards are dropped unless include_sponsored is true.
    """
    html = await _amazon().search(
        query,
        page=page,
        sort=sort,
        min_price=min_price,
        max_price=max_price,
        extra_rh=extra_rh,
    )
    parsed = parse_amazon_search(html)
    if not include_sponsored:
        parsed["products"] = [p for p in parsed["products"] if not p.get("sponsored")]
    parsed["search_query"] = query
    return _dump(parsed)


@mcp.tool()
async def amazon_list_search_filters(query: str) -> str:
    """List Amazon.in refinements for a query.

    Returns rh strings (p_36 price, p_123 brand, RAM, screen size, …).
    Pass those as extra_rh on amazon_search. Facets change by category.
    """
    html = await _amazon().search(query)
    parsed = parse_amazon_search(html)
    return _dump(
        {
            "query": query,
            "total_products_text": parsed.get("total_products_text"),
            "sort_options": parsed.get("sort_options"),
            "filters": parsed.get("filters"),
        }
    )


@mcp.tool()
async def amazon_get_product(asin_or_url: str, pincode: str | None = None) -> str:
    """Fetch one Amazon.in product with live price, specs, delivery, variants, and reviews.

    Accepts an ASIN such as B0DW495WY3 or a /dp/ product URL from amazon_search.
    Optional pincode (6-digit Indian PIN) sets delivery location via Amazon Glow
    and refreshes ETA (FREE delivery date, fastest slot, cutoff).
    """
    html = await _amazon().product(asin_or_url, pincode=pincode)
    parsed = parse_amazon_product(html, requested=asin_or_url)
    if pincode:
        parsed["pincode"] = pincode
    return _dump(parsed)


@mcp.tool()
async def amazon_check_delivery(asin_or_url: str, pincode: str) -> str:
    """Check Amazon.in delivery ETA for an ASIN at a 6-digit pincode.

    Uses the Glow address-change call from the product-page HAR, then reloads
    the product HTML for FREE / fastest delivery times.
    """
    client = _amazon()
    html = await client.product(asin_or_url, pincode=pincode)
    parsed = parse_amazon_product(html, requested=asin_or_url)
    return _dump(
        {
            "marketplace": "amazon.in",
            "asin": parsed.get("asin"),
            "title": parsed.get("title"),
            "pincode": pincode,
            "in_stock": parsed.get("in_stock"),
            "availability": parsed.get("availability"),
            "delivery": parsed.get("delivery"),
            "url": parsed.get("url"),
        }
    )


@mcp.tool()
async def compare_marketplaces(
    query: str,
    min_price: int | None = None,
    max_price: int | None = None,
    limit: int = 5,
) -> str:
    """Search Flipkart and Amazon.in in parallel for the same query.

    Use this when the shopper wants the cheaper/better listing across stores.
    Returns the top organic cards from each marketplace with live selling_price.
    Follow up with get_product (Flipkart pid) and amazon_get_product (ASIN)
    on the shortlist before recommending a buy.
    """
    limit = max(1, min(limit, 10))

    async def flipkart() -> dict[str, Any]:
        payload = await _client().search(
            query, min_price=min_price, max_price=max_price
        )
        parsed = parse_search(payload)
        parsed["products"] = parsed.get("products") or []
        parsed["products"] = parsed["products"][:limit]
        parsed["marketplace"] = "flipkart"
        return parsed

    async def amazon() -> dict[str, Any]:
        html = await _amazon().search(
            query, min_price=min_price, max_price=max_price
        )
        parsed = parse_amazon_search(html)
        parsed["products"] = [p for p in parsed["products"] if not p.get("sponsored")][:limit]
        return parsed

    fk_result, amz_result = await asyncio.gather(flipkart(), amazon(), return_exceptions=True)
    out: dict[str, Any] = {"query": query}
    if isinstance(fk_result, Exception):
        out["flipkart_error"] = str(fk_result)
    else:
        out["flipkart"] = fk_result
    if isinstance(amz_result, Exception):
        out["amazon_error"] = str(amz_result)
    else:
        out["amazon"] = amz_result
    return _dump(out)


@mcp.prompt()
def shop(need: str) -> str:
    """How to shop across supported marketplaces with these tools."""
    return (
        "Shop for: "
        + need
        + "\n\nRules:\n"
        "- Never guess a price, rating, or spec. Call tools.\n"
        "- autosuggest / amazon_autosuggest if the query is messy, then search.\n"
        "- If results are mixed, list filters and search again.\n"
        "- get_product or amazon_get_product on the shortlist before recommending.\n"
        "- Quote selling_price and mrp from the tool JSON.\n"
        "- For Flipkart vs Amazon, call compare_marketplaces then fetch the shortlist.\n"
    )


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
