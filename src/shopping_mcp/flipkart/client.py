from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

import httpx

ROME_FETCH = "https://2.rome.api.flipkart.com/api/4/page/fetch"
ROME_SUGGEST = "https://2.rome.api.flipkart.com/api/4/discover/autosuggest"
WWW_FETCH = "https://www.flipkart.com/api/4/page/fetch"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)
X_USER_AGENT = f"{USER_AGENT} FKUA/website/42/website/Desktop"

HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
    "Content-Type": "application/json",
    "Origin": "https://www.flipkart.com",
    "Referer": "https://www.flipkart.com/",
    "User-Agent": USER_AGENT,
    "X-User-Agent": X_USER_AGENT,
}

SORT_VALUES = {
    "relevance": "relevance",
    "popularity": "popularity",
    "price_asc": "price_asc",
    "price_low_to_high": "price_asc",
    "price_desc": "price_desc",
    "price_high_to_low": "price_desc",
    "newest": "recency_desc",
    "recency_desc": "recency_desc",
}


class FlipkartError(RuntimeError):
    pass


class FlipkartClient:
    def __init__(self, timeout: float = 25.0) -> None:
        self._client = httpx.AsyncClient(
            headers=HEADERS,
            timeout=timeout,
            follow_redirects=True,
            http2=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for candidate in (url, WWW_FETCH if url != WWW_FETCH else ROME_FETCH):
            try:
                response = await self._client.post(candidate, json=body)
                response.raise_for_status()
                data = response.json()
                status = data.get("STATUS_CODE")
                if status not in (None, 200):
                    raise FlipkartError(f"Flipkart STATUS_CODE={status}")
                return data
            except Exception as exc:
                last_error = exc
                continue
        raise FlipkartError(f"Flipkart request failed: {last_error}") from last_error

    async def fetch_page(
        self,
        page_uri: str,
        page_type: str,
        page_number: int = 1,
    ) -> dict[str, Any]:
        if not page_uri.startswith("/"):
            page_uri = "/" + page_uri
        body = {
            "pageUri": page_uri,
            "pageContext": {
                "fetchSeoData": True,
                "paginatedFetch": False,
                "pageNumber": page_number,
            },
            "requestContext": {"type": page_type},
        }
        return await self._post_json(ROME_FETCH, body)

    async def search(
        self,
        query: str,
        *,
        page: int = 1,
        sort: str | None = None,
        min_price: int | None = None,
        max_price: int | None = None,
        brands: list[str] | None = None,
        min_rating: int | None = None,
        f_assured: bool = False,
        extra_facets: list[str] | None = None,
    ) -> dict[str, Any]:
        uri = build_search_uri(
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
        return await self.fetch_page(uri, "BROWSE_PAGE", page_number=page)

    async def autosuggest(self, query: str, rows: int = 10) -> dict[str, Any]:
        body = {
            "query": query,
            "marketPlaceId": "FLIPKART",
            "types": ["QUERY", "QUERY_STORE", "PRODUCT", "RICH", "PARTITION"],
            "rows": rows,
            "zeroPrefixHistory": False,
        }
        return await self._post_json(ROME_SUGGEST, body)

    async def product(self, page_uri: str) -> dict[str, Any]:
        return await self.fetch_page(page_uri, "PRODUCT_PAGE", page_number=1)


def build_search_uri(
    query: str,
    *,
    page: int = 1,
    sort: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    brands: list[str] | None = None,
    min_rating: int | None = None,
    f_assured: bool = False,
    extra_facets: list[str] | None = None,
) -> str:
    params: list[tuple[str, str]] = [
        ("q", query),
        ("otracker", "search"),
        ("otracker1", "search"),
        ("marketplace", "FLIPKART"),
        ("as-show", "on"),
        ("as", "off"),
    ]
    if page > 1:
        params.append(("page", str(page)))
    if sort:
        key = SORT_VALUES.get(sort.lower().replace(" ", "_"), sort)
        params.append(("sort", key))

    facets: list[str] = []
    if min_price is not None or max_price is not None:
        lo = str(min_price) if min_price is not None else "Min"
        hi = str(max_price) if max_price is not None else "Max"
        facets.append(f"facets.price_range.from={lo},facets.price_range.to={hi}")
    for brand in brands or []:
        facets.append(f"facets.brand[]={brand}")
    if min_rating:
        facets.append(f"facets.rating[]={min_rating}★ & above")
    if f_assured:
        facets.append("facets.fulfilled_by[]=F-Assured")
    for extra in extra_facets or []:
        facets.append(extra)

    query_string = urlencode(params, quote_via=quote)
    for facet in facets:
        query_string += "&p[]=" + quote(facet, safe="")
    return "/search?" + query_string


def product_uri(pid_or_url: str, listing_id: str | None = None) -> str:
    raw = pid_or_url.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw.split("flipkart.com", 1)[-1] or raw
    if raw.startswith("/"):
        return raw
    uri = f"/p/itm?pid={quote(raw)}&marketplace=FLIPKART"
    if listing_id:
        uri += f"&lid={quote(listing_id)}"
    return uri
