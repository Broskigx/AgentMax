"""
WebSearchAgent -- autonomous web search and page reading with SSRF protection.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import quote_plus, urlparse

import httpx
import structlog
from bs4 import BeautifulSoup

from core.agents.base_agent import ActionResult, AgentCapability, BaseAgent

log = structlog.get_logger(__name__)

# Private/reserved CIDR blocks that must never be fetched (SSRF protection).
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fc00::/7"),  # IPv6 ULA
]

_ALLOWED_SCHEMES = {"http", "https"}
_REQUEST_HEADERS = {
    "User-Agent": "AgentMax/1.0 (+https://AgentMax.app)",
    "Accept": "text/html,application/xhtml+xml",
}


def _is_safe_url(url: str) -> tuple[bool, str]:
    """
    Return (True, "") if the URL is safe to fetch.
    Return (False, reason) if it targets a private/reserved host.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return False, "Malformed URL"

    if parsed.scheme not in _ALLOWED_SCHEMES:
        return False, f"Disallowed scheme: {parsed.scheme!r}"

    hostname = parsed.hostname or ""
    if not hostname:
        return False, "Missing hostname"

    # Resolve hostname to IP and check against blocked networks
    try:
        ip_str = socket.getaddrinfo(hostname, None)[0][4][0]
        ip = ipaddress.ip_address(ip_str)
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                return False, f"Target resolves to private IP {ip} (SSRF blocked)"
    except Exception:
        pass  # DNS failure -- let the request fail naturally with a timeout

    return True, ""


class WebSearchAgent(BaseAgent):
    """
    Provides internet search and web page reading.
    All outbound URLs are validated against SSRF blocklists before fetching.
    """

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def capabilities(self) -> list[AgentCapability]:
        return [
            AgentCapability("search", "Search the web using DuckDuckGo"),
            AgentCapability("read_page", "Extract text content from a URL"),
        ]

    async def search(self, step: dict) -> ActionResult:
        query = step.get("query", "")
        if not query:
            return ActionResult(success=False, error="No query provided")

        await self.log_terminal(f"Searching: {query}", "info")
        # URL-encode the query so spaces and reserved chars (& # ? =) don't
        # break the request or silently truncate the search terms.
        search_url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"

        try:
            async with httpx.AsyncClient(
                headers=_REQUEST_HEADERS,
                follow_redirects=True,
                timeout=10.0,
            ) as client:
                resp = await client.get(search_url)
                resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            for div in soup.find_all("div", class_="result")[:5]:
                title_tag = div.find("a", class_="result__a")
                snippet_tag = div.find("a", class_="result__snippet")
                if title_tag:
                    results.append(
                        {
                            "title": title_tag.get_text(strip=True),
                            "url": title_tag.get("href", ""),
                            "snippet": snippet_tag.get_text(strip=True) if snippet_tag else "",
                        }
                    )

            await self.log_terminal(f"Found {len(results)} results", "stdout")
            return ActionResult(success=True, data={"results": results})
        except Exception as exc:
            await self.log_terminal(f"Search error: {exc}", "stderr")
            return ActionResult(success=False, error=str(exc))

    async def read_page(self, step: dict) -> ActionResult:
        url = step.get("url", "")
        if not url:
            return ActionResult(success=False, error="No URL provided")

        safe, reason = _is_safe_url(url)
        if not safe:
            log.warning("web_search.ssrf_blocked", url=url, reason=reason)
            await self.ctx.audit.log_event("web.ssrf_blocked", {"url": url, "reason": reason})
            return ActionResult(success=False, error=f"URL blocked: {reason}")

        await self.log_terminal(f"Reading: {url}", "info")

        try:
            async with httpx.AsyncClient(
                headers=_REQUEST_HEADERS,
                follow_redirects=True,
                timeout=10.0,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()

            text = soup.get_text(separator=" ", strip=True)
            summary = text[:3000] + "..." if len(text) > 3000 else text

            await self.log_terminal(f"Page read ({len(text):,} chars)", "stdout")
            return ActionResult(success=True, data={"content": summary, "url": url})
        except Exception as exc:
            await self.log_terminal(f"Page read error: {exc}", "stderr")
            return ActionResult(success=False, error=str(exc))
