"""URL fetching and text extraction with SSRF protection."""
import ipaddress
import logging
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Blocked by default: localhost, private, link-local, loopback
_PRIVATE_NETWORKS = (
    ipaddress.IPv4Network("127.0.0.0/8"),   # loopback
    ipaddress.IPv4Network("10.0.0.0/8"),    # private
    ipaddress.IPv4Network("172.16.0.0/12"), # private
    ipaddress.IPv4Network("192.168.0.0/16"), # private
    ipaddress.IPv4Network("169.254.0.0/16"), # link-local
    ipaddress.IPv4Network("0.0.0.0/8"),      # current network
    ipaddress.IPv6Network("::1/128"),       # loopback
    ipaddress.IPv6Network("fe80::/10"),      # link-local
    ipaddress.IPv6Network("fc00::/7"),       # unique local
)


def _is_blocked_host(host: str) -> bool:
    """Check if host is in a blocked range (SSRF protection)."""
    if not host:
        return True
    host = host.lower().strip()
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        for network in _PRIVATE_NETWORKS:
            if ip in network:
                return True
    except ValueError:
        pass  # hostname, not IP - allow by default (public DNS)
    return False


def validate_url(url: str) -> None:
    """Raise ValueError if URL is not allowed (SSRF check)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme must be http or https, got: {parsed.scheme}")
    if _is_blocked_host(parsed.hostname):
        raise ValueError(f"URL host is not allowed: {parsed.hostname}")


async def fetch_url_text(url: str) -> tuple[str, str]:
    """Fetch URL and extract title + plain text. Raises on SSRF or fetch errors."""
    validate_url(url)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        html = response.text

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title:
        title = url

    text = soup.get_text(separator=" ", strip=True)
    return title, text
