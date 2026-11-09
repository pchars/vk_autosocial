import re
from typing import Optional
from urllib.parse import urlencode, urlsplit

OAUTH_REDIRECT = "https://oauth.vk.ru/blank.html"
REDIRECT_HOSTS = {"oauth.vk.ru", "oauth.vk.com"}
# Standalone app that still accepts implicit flow without the messages scope.
OAUTH_APP_ID = 4083558
OAUTH_SCOPE = "photos,wall,groups,stories,offline"
TOKEN_RE = re.compile(r"access_token=([A-Za-z0-9._\-]+)")
RAW_TOKEN_RE = re.compile(r"^vk1\.a\.[A-Za-z0-9._\-]{20,}$")


def oauth_url(api_version: str = "5.199") -> str:
    params = {
        "client_id": OAUTH_APP_ID,
        "display": "page",
        "redirect_uri": OAUTH_REDIRECT,
        "scope": OAUTH_SCOPE,
        "response_type": "token",
        "v": api_version,
    }
    return "https://oauth.vk.ru/authorize?" + urlencode(params)


def extract_token(text: str) -> Optional[str]:
    """Accept the VK redirect address from the browser or a bare vk1.a. token."""
    text = (text or "").strip()
    match = TOKEN_RE.search(text)
    if match:
        address = urlsplit(text)
        if address.scheme != "https" or address.hostname not in REDIRECT_HOSTS or address.path != "/blank.html":
            return None
        return match.group(1)
    return text if RAW_TOKEN_RE.match(text) else None
