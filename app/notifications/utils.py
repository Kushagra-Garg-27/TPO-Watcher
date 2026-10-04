import html
from typing import Any, Optional
from urllib.parse import urlparse


def escape_html(value: Any) -> str:
    """
    HTML-escape any value at the template insertion boundary using quote=True.
    Converts &, <, >, ", and ' to safe character entities.
    """
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def sanitize_url(url: Optional[str]) -> Optional[str]:
    """
    Validate that an external or upstream URL uses only http or https schemes.
    Rejects javascript:, data:, file:, vbscript:, and other unsafe schemes.
    Returns the HTML-escaped safe URL string if valid, or None if unsafe.
    """
    if not url:
        return None
    url_str = str(url).strip()
    try:
        parsed = urlparse(url_str)
        if parsed.scheme.lower() in ("http", "https"):
            return escape_html(url_str)
    except Exception:
        pass
    return None


def render_safe_link(url: Optional[str], link_text: str, style: str = "") -> str:
    """
    Renders an HTML <a> tag if the URL uses a safe http/https scheme.
    If the URL is unsafe or missing, renders only the escaped link text without a clickable link.
    """
    safe_url = sanitize_url(url)
    escaped_text = escape_html(link_text)
    if safe_url:
        style_attr = f' style="{escape_html(style)}"' if style else ""
        return f'<a href="{safe_url}"{style_attr}>{escaped_text}</a>'
    return escaped_text
