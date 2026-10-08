import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Tags that almost never contain useful job content
_NOISE_TAGS = ["script", "style", "nav", "footer", "header", "aside", "form", "noscript", "iframe"]

# CSS class/id keywords that typically wrap the main job content
_CONTENT_KEYWORDS = re.compile(r"(job|posting|description|content|vacancy|offer|detail)", re.I)


def is_url(text: str) -> bool:
    """Return True if text looks like an HTTP/HTTPS URL."""
    try:
        p = urlparse(text.strip())
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def scrape_url(url: str) -> tuple[bool, str]:
    """
    Fetch and extract readable text from a job posting URL.

    Returns:
        (True, content)  on success
        (False, reason)  on failure — caller should ask user to paste manually
    """
    try:
        resp = requests.get(url.strip(), headers=_HEADERS, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        # Strip noise
        for tag in soup(_NOISE_TAGS):
            tag.decompose()

        # Prefer a focused content container if identifiable
        main = (
            soup.find("main")
            or soup.find("article")
            or soup.find(id=_CONTENT_KEYWORDS)
            or soup.find(class_=_CONTENT_KEYWORDS)
            or soup.body
        )

        raw_text = (main or soup).get_text(separator="\n", strip=True)

        # Collapse blank lines
        lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
        clean = "\n".join(lines)

        if len(clean) < 150:
            return False, "Page content is too short — the page may require JavaScript or login"

        return True, clean[:15_000]

    except requests.exceptions.HTTPError as exc:
        return False, f"HTTP {exc.response.status_code}"
    except requests.exceptions.ConnectionError:
        return False, "Could not connect to the server"
    except requests.exceptions.Timeout:
        return False, "Request timed out"
    except Exception as exc:
        return False, str(exc)
