from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlencode

import httpx

SITE = "https://www.amazon.in"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
    "User-Agent": USER_AGENT,
    "Referer": f"{SITE}/",
    "sec-ch-ua": '"Chromium";v="152", "Not?A_Brand";v="24", "Google Chrome";v="152"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
}

SORT_VALUES = {
    "relevance": "relevanceblender",
    "featured": "relevanceblender",
    "price_asc": "price-asc-rank",
    "price_low_to_high": "price-asc-rank",
    "price_desc": "price-desc-rank",
    "price_high_to_low": "price-desc-rank",
    "rating": "review-rank",
    "review": "review-rank",
    "newest": "date-desc-rank",
    "date-desc-rank": "date-desc-rank",
    "price-asc-rank": "price-asc-rank",
    "price-desc-rank": "price-desc-rank",
    "review-rank": "review-rank",
    "relevanceblender": "relevanceblender",
}


class AmazonError(RuntimeError):
    pass


class AmazonClient:
    def __init__(self, timeout: float = 25.0) -> None:
        self._client = httpx.AsyncClient(
            headers=HEADERS,
            timeout=timeout,
            follow_redirects=True,
            http2=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _pass_interstitial(self, html: str, referer: str) -> None:
        i_match = re.search(r"var i = (\d+);", html)
        n_match = re.search(r'var j = i \+ Number\("(\d+)" \+ "(\d+)"\)', html)
        bm_match = re.search(r'"bm-verify"\s*:\s*"([^"]+)"', html)
        if not (i_match and n_match and bm_match):
            raise AmazonError("Amazon bot-check page could not be solved")
        proof = int(i_match.group(1)) + int(n_match.group(1) + n_match.group(2))
        response = await self._client.post(
            f"{SITE}/_sec/verify?provider=interstitial",
            json={"bm-verify": bm_match.group(1), "pow": proof},
            headers={"Content-Type": "application/json", "Referer": referer},
        )
        if response.status_code >= 400:
            raise AmazonError(f"Amazon bot-check failed ({response.status_code})")

    async def get_html(self, url: str) -> str:
        response = await self._client.get(url)
        html = response.text
        if "bm-verify" in html and "/_sec/verify" in html:
            await self._pass_interstitial(html, str(response.url))
            response = await self._client.get(url)
            html = response.text
        if "validateCaptcha" in html or "opfcaptcha" in html.lower():
            raise AmazonError("Amazon served a captcha; retry in a bit")
        return html

    async def search(
        self,
        query: str,
        *,
        page: int = 1,
        sort: str | None = None,
        min_price: int | None = None,
        max_price: int | None = None,
        extra_rh: list[str] | None = None,
    ) -> str:
        return await self.get_html(
            build_search_url(
                query,
                page=page,
                sort=sort,
                min_price=min_price,
                max_price=max_price,
                extra_rh=extra_rh,
            )
        )

    async def set_pincode(self, pincode: str) -> dict[str, Any]:
        pin = re.sub(r"\D", "", pincode)[:6]
        if len(pin) != 6:
            raise AmazonError("Pincode must be 6 digits")
        response = await self._client.post(
            f"{SITE}/gp/delivery/ajax/address-change.html",
            data={
                "locationType": "LOCATION_INPUT",
                "zipCode": pin,
                "deviceType": "web",
                "storeContext": "generic",
                "pageType": "Detail",
                "actionSource": "glow",
            },
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": SITE,
                "Referer": f"{SITE}/",
            },
        )
        if response.status_code >= 400:
            raise AmazonError(f"Amazon pincode update failed ({response.status_code})")
        try:
            data = response.json()
        except Exception as exc:
            raise AmazonError("Amazon pincode update returned non-JSON") from exc
        if not data.get("isValidAddress") and not data.get("successful"):
            raise AmazonError(f"Amazon rejected pincode {pin}")
        return data

    async def product(self, asin_or_url: str, pincode: str | None = None) -> str:
        url = product_url(asin_or_url)
        html = await self.get_html(url)
        if pincode:
            await self.set_pincode(pincode)
            html = await self.get_html(url)
        return html

    async def autosuggest(self, prefix: str, limit: int = 11) -> dict[str, Any]:
        params = [
            ("limit", str(limit)),
            ("prefix", prefix),
            ("suggestion-type", "WIDGET"),
            ("suggestion-type", "KEYWORD"),
            ("page-type", "Gateway"),
            ("alias", "aps"),
            ("site-variant", "desktop"),
            ("version", "3"),
            ("event", "onkeypress" if prefix else "onfocus"),
            ("wc", ""),
            ("lop", "en_IN"),
            ("fb", "1"),
            ("mid", "A21TJRUUN4KGV"),
            ("plain-mid", "44571"),
            ("client-info", "search-ui"),
            ("ni", "1"),
        ]
        response = await self._client.get(
            f"{SITE}/suggestions",
            params=params,
            headers={
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{SITE}/",
            },
        )
        if response.status_code >= 400:
            raise AmazonError(f"Amazon suggestions failed ({response.status_code})")
        try:
            return response.json()
        except Exception as exc:
            raise AmazonError("Amazon suggestions returned non-JSON") from exc


def build_search_url(
    query: str,
    *,
    page: int = 1,
    sort: str | None = None,
    min_price: int | None = None,
    max_price: int | None = None,
    extra_rh: list[str] | None = None,
) -> str:
    params: list[tuple[str, str]] = [("k", query)]
    if page > 1:
        params.append(("page", str(page)))
    if sort:
        params.append(("s", SORT_VALUES.get(sort.lower().replace(" ", "_"), sort)))
    rh_parts: list[str] = []
    if min_price is not None or max_price is not None:
        lo = "" if min_price is None else str(min_price * 100)
        hi = "" if max_price is None else str(max_price * 100)
        rh_parts.append(f"p_36:{lo}-{hi}")
    for extra in extra_rh or []:
        rh_parts.append(extra)
    if rh_parts:
        params.append(("rh", ",".join(rh_parts)))
    return f"{SITE}/s?" + urlencode(params, quote_via=quote)


def product_url(asin_or_url: str) -> str:
    raw = asin_or_url.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw.split("?")[0]
    if raw.startswith("/"):
        return SITE + raw.split("?")[0]
    match = re.search(r"(?:/dp/|/gp/product/)([A-Z0-9]{10})", raw, re.I)
    if match:
        return f"{SITE}/dp/{match.group(1)}"
    if re.fullmatch(r"[A-Z0-9]{10}", raw, re.I):
        return f"{SITE}/dp/{raw.upper()}"
    raise AmazonError(f"Not an Amazon ASIN or product URL: {asin_or_url}")
