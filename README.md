# shopping-mcp

MCP server that looks up **live listings** on Indian storefronts so an agent can quote real prices, stock, and specs instead of guessing.

Currently wired to **Flipkart** and **Amazon.in**. The layout is store-agnostic so more marketplaces can be added the same way.

## What it does

| Tool | Store | Returns |
| --- | --- | --- |
| `autosuggest` | Flipkart | Query completions |
| `search_products` | Flipkart | pid, URL, selling price, MRP, rating, stock, key specs |
| `list_search_filters` | Flipkart | Facets (`extra_facets`) for the current query |
| `get_product` | Flipkart | Price, offers, highlights, specs, similar items |
| `compare_products` | Flipkart | Live details for 2–5 pids/URLs |
| `get_similar_products` | Flipkart | Similar / frequently bought together |
| `amazon_autosuggest` | Amazon.in | Keyword completions (typo-tolerant) |
| `amazon_search` | Amazon.in | ASIN, URL, selling price, MRP, rating |
| `amazon_list_search_filters` | Amazon.in | `rh` refinements (brand, price, RAM, …) |
| `amazon_get_product` | Amazon.in | Price, stock, variants, reviews, delivery ETA |
| `amazon_check_delivery` | Amazon.in | FREE / fastest delivery at a 6-digit PIN |
| `compare_marketplaces` | both | Parallel Flipkart + Amazon search |

Typical flow: suggest → search → filter if noisy → `get_product` / `amazon_get_product` on the shortlist. Cross-store: `compare_marketplaces`, then fetch the winning pid and ASIN.

## Install

```bash
git clone https://github.com/Nuu-maan/shopping-mcp.git
cd shopping-mcp
uv sync
```

Run over stdio:

```bash
uv run shopping-mcp
```

## Connect an agent

**Grok** (`~/.grok/config.toml` or a project `.grok/config.toml`):

```toml
[mcp_servers.shopping]
command = "uv"
args = ["run", "--directory", "/absolute/path/to/shopping-mcp", "shopping-mcp"]
enabled = true
```

```bash
grok mcp add shopping -- uv run --directory /absolute/path/to/shopping-mcp shopping-mcp
```

**Claude Desktop** / other stdio MCP hosts:

```json
{
  "mcpServers": {
    "shopping": {
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/shopping-mcp", "shopping-mcp"]
    }
  }
}
```

Restart the host after adding the server.

## Layout

```
src/shopping_mcp/
  server.py
  flipkart/
  amazon/
```

Flipkart talks to `2.rome.api.flipkart.com` (`/api/4/page/fetch`, `/api/4/discover/autosuggest`). Amazon uses public `amazon.in` search HTML, `/suggestions`, `/dp/{asin}`, and Glow `address-change` for PIN codes.

No store login is required for the supported tools.

## Limits

These are not implemented (no captured request to implement from):

- Flipkart pincode / delivery date
- Flipkart full review bodies, Q&A, other sellers
- Amazon “see all reviews” pagination
- Amazon other-seller / New & Used listing

Store HTML and private APIs change. Amazon may serve a bot check or captcha; the client retries the interstitial and errors on captcha instead of inventing data.

## License

MIT
