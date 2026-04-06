"""
News aggregator for the Morning Brief.
Fetches market-relevant news from free sources.
Maps news to the fund's tracked market categories.
"""

import logging
import json
import os
import time
import hashlib
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus
from urllib.request import urlopen, Request
from urllib.error import URLError

logger = logging.getLogger("kalshi.news")

# Map Kalshi categories to search queries
CATEGORY_QUERIES = {
    "Crypto": ["bitcoin price today", "crypto market news"],
    "Economics": ["federal reserve news today", "us economy news", "inflation data"],
    "Sports": ["NBA scores today", "sports news"],
    "Financials": ["stock market today", "S&P 500 news"],
    "Elections": ["election news today"],
    "Other": ["prediction markets news"],
}

# Specific ticker patterns to queries
TICKER_QUERIES = {
    "KXFED": "federal reserve interest rate decision news",
    "KXBTC": "bitcoin BTC price today",
    "KXAAAGASM": "gas prices today national average AAA",
    "KXINX": "S&P 500 index today",
    "KXETH": "ethereum ETH price today",
    "KXNBA": "NBA basketball scores today",
    "KXNFL": "NFL football news",
    "KXMVE": "sports betting parlays today",
}

# Cache to avoid re-fetching
_news_cache: dict = {}
_cache_ttl = 600  # 10 minutes


class NewsItem:
    def __init__(self, title: str, source: str, url: str, snippet: str,
                 category: str, relevance: float, published: str = ""):
        self.title = title
        self.source = source
        self.url = url
        self.snippet = snippet
        self.category = category
        self.relevance = relevance
        self.published = published

    def to_dict(self):
        return {
            "title": self.title,
            "source": self.source,
            "url": self.url,
            "snippet": self.snippet,
            "category": self.category,
            "relevance": round(self.relevance, 2),
            "published": self.published,
        }


def fetch_news_for_markets(tracked_markets: list, max_per_category: int = 5) -> list[dict]:
    """
    Fetch news relevant to the fund's tracked markets.
    Uses DuckDuckGo instant answer API (free, no key required).

    Args:
        tracked_markets: list of market dicts with 'ticker', 'title', 'category'
        max_per_category: max news items per category

    Returns: list of NewsItem dicts sorted by relevance
    """
    # Determine which categories we're trading
    active_categories = set()
    active_tickers = set()
    for m in tracked_markets:
        cat = m.get("category", "")
        if cat:
            active_categories.add(cat)
        ticker = m.get("ticker", "")
        for prefix, query in TICKER_QUERIES.items():
            if ticker.startswith(prefix):
                active_tickers.add(prefix)

    all_news = []

    # Fetch by active categories
    for cat in active_categories:
        queries = CATEGORY_QUERIES.get(cat, [cat])
        for query in queries[:2]:  # max 2 queries per category
            items = _search_news(query, cat, max_results=max_per_category)
            all_news.extend(items)

    # Fetch by specific ticker patterns
    for prefix in active_tickers:
        query = TICKER_QUERIES.get(prefix, "")
        if query:
            items = _search_news(query, "Ticker", max_results=3)
            all_news.extend(items)

    # Deduplicate by title similarity
    seen_titles = set()
    unique_news = []
    for item in all_news:
        title_key = item["title"][:50].lower()
        if title_key not in seen_titles:
            seen_titles.add(title_key)
            unique_news.append(item)

    # Sort by relevance
    unique_news.sort(key=lambda x: x["relevance"], reverse=True)

    return unique_news[:20]  # top 20


def _search_news(query: str, category: str, max_results: int = 5) -> list[dict]:
    """Search for news using DuckDuckGo HTML lite search."""
    cache_key = f"{query}_{int(time.time() // _cache_ttl)}"
    if cache_key in _news_cache:
        return _news_cache[cache_key]

    results = []

    try:
        import re
        from html import unescape

        # Use DuckDuckGo lite (HTML) for actual search results
        url = f"https://lite.duckduckgo.com/lite/?q={quote_plus(query + ' news today')}"
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })

        with urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        # Parse results from DuckDuckGo lite HTML
        # Results are in <a> tags within <td> elements with class "result-link"
        # or in the general table structure

        # Extract links and snippets (DuckDuckGo lite uses single-quoted classes)
        link_pattern = r"""<a[^>]+href=["']([^"']+)["'][^>]*class=["'][^"']*result-link[^"']*["'][^>]*>(.*?)</a>"""
        snippet_pattern = r"""<td[^>]*class=["'][^"']*result-snippet[^"']*["'][^>]*>(.*?)</td>"""

        links = re.findall(link_pattern, html)
        snippets = re.findall(snippet_pattern, html, re.DOTALL)

        # Fallback: try more general link extraction
        if not links:
            # Look for any links with result text
            all_links = re.findall(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', html)
            # Filter to news-like domains
            news_domains = ['reuters', 'cnbc', 'bloomberg', 'wsj', 'nytimes', 'bbc',
                           'marketwatch', 'yahoo', 'cnn', 'coindesk', 'theblock',
                           'apnews', 'foxbusiness', 'fortune', 'ft.com', 'axios',
                           'politico', 'espn', 'sports']
            links = [(url, title) for url, title in all_links
                     if any(d in url.lower() for d in news_domains)]

        for i, (link_url, title) in enumerate(links[:max_results]):
            title = unescape(re.sub(r'<[^>]+>', '', title)).strip()
            snippet = ""
            if i < len(snippets):
                snippet = unescape(re.sub(r'<[^>]+>', '', snippets[i])).strip()

            # Extract actual URL from DuckDuckGo redirect wrapper
            from urllib.parse import unquote as url_unquote
            if 'uddg=' in link_url:
                uddg_match = re.search(r'uddg=([^&]+)', link_url)
                if uddg_match:
                    link_url = url_unquote(uddg_match.group(1))

            if not title or len(title) < 10:
                continue

            # Determine source from URL
            source = "Web"
            for domain in ['reuters', 'cnbc', 'bloomberg', 'wsj', 'bbc', 'coindesk',
                          'marketwatch', 'yahoo', 'cnn', 'apnews', 'espn']:
                if domain in link_url.lower():
                    source = domain.capitalize()
                    break

            results.append(NewsItem(
                title=title[:150],
                source=source,
                url=link_url,
                snippet=snippet[:400] if snippet else title,
                category=category,
                relevance=round(0.9 - i * 0.1, 2),
                published="today",
            ).to_dict())

    except Exception as e:
        logger.debug("DuckDuckGo lite search failed for '%s': %s", query, e)

    # If HTML search failed, try the JSON API as fallback
    if not results:
        try:
            url = f"https://api.duckduckgo.com/?q={quote_plus(query)}&format=json&no_html=1"
            req = Request(url, headers={"User-Agent": "KalshiQuant/1.0"})
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())

            if data.get("Abstract"):
                results.append(NewsItem(
                    title=data.get("Heading", query),
                    source=data.get("AbstractSource", "Wikipedia"),
                    url=data.get("AbstractURL", ""),
                    snippet=data["Abstract"][:400],
                    category=category,
                    relevance=0.6,
                ).to_dict())

            for topic in data.get("RelatedTopics", [])[:3]:
                if isinstance(topic, dict) and topic.get("Text"):
                    results.append(NewsItem(
                        title=topic["Text"][:150],
                        source="DuckDuckGo",
                        url=topic.get("FirstURL", ""),
                        snippet=topic["Text"][:400],
                        category=category,
                        relevance=0.5,
                    ).to_dict())
        except Exception:
            pass

    # Last resort: generate a useful context note
    if not results:
        results.append(NewsItem(
            title=f"Monitoring: {query}",
            source="KalshiQuant",
            url="",
            snippet=f"Active monitoring of {category} markets. No recent news articles found for '{query}'.",
            category=category,
            relevance=0.3,
        ).to_dict())

    _news_cache[cache_key] = results
    return results


def get_market_context(markets: list) -> list[dict]:
    """
    Generate contextual insights about the fund's markets.
    No API calls -- pure analysis of market data.
    """
    insights = []

    if not markets:
        return insights

    # Group by category
    by_cat = {}
    for m in markets:
        cat = m.get("category", "Other")
        by_cat.setdefault(cat, []).append(m)

    for cat, cat_markets in by_cat.items():
        # Volume analysis
        volumes = [m.get("volume", 0) for m in cat_markets]
        avg_vol = sum(volumes) / len(volumes) if volumes else 0

        # Price extremes
        prices = [m.get("price", 0.5) for m in cat_markets if m.get("price", 0) > 0]
        high_conviction = [m for m in cat_markets if m.get("price", 0.5) > 0.85 or m.get("price", 0.5) < 0.15]

        insights.append({
            "category": cat,
            "market_count": len(cat_markets),
            "avg_volume": round(avg_vol),
            "high_conviction_count": len(high_conviction),
            "summary": _generate_category_summary(cat, cat_markets),
        })

    return insights


def _generate_category_summary(category: str, markets: list) -> str:
    """Generate a one-line summary for a market category."""
    n = len(markets)
    if category == "Crypto":
        btc_markets = [m for m in markets if "BTC" in m.get("ticker", "")]
        if btc_markets:
            prices = [m.get("price", 0.5) for m in btc_markets]
            avg = sum(prices) / len(prices)
            direction = "bullish" if avg > 0.5 else "bearish"
            return f"{len(btc_markets)} BTC strike markets, avg probability {avg:.0%} ({direction} lean)"
    elif category == "Economics":
        fed_markets = [m for m in markets if "FED" in m.get("ticker", "")]
        gas_markets = [m for m in markets if "AAAG" in m.get("ticker", "")]
        parts = []
        if fed_markets:
            parts.append(f"{len(fed_markets)} Fed rate markets")
        if gas_markets:
            parts.append(f"{len(gas_markets)} gas price markets")
        return ", ".join(parts) if parts else f"{n} economics markets"
    elif category == "Sports":
        return f"{n} active sports markets"

    return f"{n} markets in {category}"
