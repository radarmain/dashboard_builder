"""Client slugs are subdomains (<slug>.radar-analytics.com). ONE shared reserved list lives here;
the Builder fetches it from GET /api/slug-rules and the portal/viewer mirror it for host routing."""

import json
import re
from pathlib import Path

# The ONE reserved list: reserved_slugs.json (the portal frontend imports the same file).
RESERVED_SLUGS: frozenset[str] = frozenset(
    json.loads((Path(__file__).parent / "reserved_slugs.json").read_text())
)
CLIENT_SLUG_MAX = 40
CLIENT_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")  # no leading/trailing/double hyphen


def client_slug_error(slug: str) -> str | None:
    """None if valid, else a short human-readable reason."""
    if len(slug) > CLIENT_SLUG_MAX:
        return f"Client slug must be at most {CLIENT_SLUG_MAX} characters."
    if not CLIENT_SLUG_RE.match(slug):
        return "Client slug: lowercase letters, digits, hyphens; no leading or trailing hyphen."
    if slug in RESERVED_SLUGS:
        return f'"{slug}" is reserved (it is used by the site or by email/DNS). Pick another.'
    return None
