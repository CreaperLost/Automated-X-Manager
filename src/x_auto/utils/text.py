"""Text utilities: URL detection, character counting, slug helpers.

URL detection matches X's autolinker behavior:
  - https?://, http://
  - www. (followed by a domain)
  - bare domains with common TLDs

Spelled-out domains ("brand dot com") are NOT detected.

Character counting follows X's rules: URLs in a tweet are shortened
to 23 characters via t.co, regardless of the original length. So when
we ask "would this draft trigger the URL-surcharge?", we count the
*raw* text — X's pricing is based on whether a URL is present, not on
its length.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Order matters: try the most specific first.
URL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"https?://[^\s]+", re.IGNORECASE),
    re.compile(r"\bwww\.[^\s]+\.[a-z]{2,}(?:/[^\s]*)?", re.IGNORECASE),
    re.compile(
        r"\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*"
        r"\.(?:com|io|ai|app|dev|co|net|org|me|info|biz|us|uk|ca|de|fr|jp|cn|tech|xyz|so|gg|tv|fm|am|to|sh|ly|gl|vc|im|nu|rs|re|asia|museum|cloud|online|store|site|blog|live|news|design|media|consulting|solutions|tools|systems|network|group|center|company|academy|education|energy|finance|legal|health|lab|space|world|today|life|cool|wtf|foo|bar|baz|local|global|earth|uno|bike|cool|games|app|page|link|click|review|press|wiki|plus|now|hub|fast|easy|pro|community|fund|email|chat|talk|hello|hi|hey|wow|fun|love|art|music|film|movie|video|podcast|stream|audio|cloud|data|api|sdk|dev|engine|server|host|cloud|saas|app)\b",
        re.IGNORECASE,
    ),
)

# A X-style cashtag: a literal ``$`` followed by 1–5 ticker characters
# (letters or digits). X enforces "at most one cashtag per post" and
# returns 403 ``not-authorized-for-resource`` when more than one is
# present. Note: dollar amounts like ``$175`` or ``$25k`` count as
# cashtags per X's parser, so the regex is alphanumeric (not just
# letters). Word boundary keeps ``$a@b`` from matching the ``$a``
# prefix; the post body never has email-like substrings in practice
# but the boundary is defensive.
CASHTAG_PATTERN: re.Pattern[str] = re.compile(r"\$[\w]{1,5}\b")

# Max characters X allows in a free-tier post. The rephrase prompt
# asks the model to stay at 220–260, leaving room for the user to
# edit, but the hard limit is 280.
X_MAX_POST_CHARS = 280

# Max cashtags X allows per post. Anything more returns 403.
X_MAX_CASHTAGS_PER_POST = 1

# A short whitelist of TLDs that should NOT trigger URL detection even if
# they match the bare-domain regex. (e.g. ".app" is a TLD but a sentence
# like "let's build an app" should not flag as a URL.)
URL_TLD_ALLOWLIST: frozenset[str] = frozenset(
    {
        "com", "io", "ai", "app", "dev", "co", "net", "org", "me", "info", "biz",
        "us", "uk", "ca", "de", "fr", "jp", "cn", "tech", "xyz", "so", "gg",
        "tv", "fm", "am", "to", "sh", "ly", "gl", "vc", "im", "nu", "rs", "re",
        "asia", "museum", "cloud", "online", "store", "site", "blog", "live",
        "news", "design", "media", "tools", "systems", "network", "group",
        "center", "company", "academy", "education", "energy", "finance", "legal",
        "health", "lab", "space", "world", "today", "life", "cool", "wtf", "foo",
        "bar", "baz", "local", "global", "earth", "uno", "bike", "games", "page",
        "link", "click", "review", "press", "wiki", "plus", "now", "hub", "fast",
        "easy", "pro", "community", "fund", "email", "chat", "talk", "hello",
        "fun", "love", "art", "music", "film", "movie", "video", "podcast",
        "stream", "audio", "data", "api", "sdk", "engine", "server", "host",
        "saas",
    }
)

# A word list for the bare-domain regex. We DO require these TLDs.
TLD_LIST = sorted(URL_TLD_ALLOWLIST)


def contains_url(text: str) -> bool:
    """Return True if the text contains a URL X would autolink.

    X charges $0.200 for any post that contains an autolinked URL.
    The check is the raw text — X's pricing is based on presence, not
    length. The 23-char t.co shortening is a display thing only.
    """
    if not text:
        return False
    for pattern in URL_PATTERNS:
        if pattern.search(text):
            return True
    return False


def extract_first_url(text: str) -> str | None:
    """Return the first URL found in the text, or None.

    Used by the Create tab to extract a project link from the AI's
    reply payload.
    """
    if not text:
        return None
    for pattern in URL_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(0)
    return None


def x_char_count(text: str) -> int:
    """Approximate X's character count for a tweet.

    URLs are counted as 23 characters (the t.co shortened length);
    everything else is counted as 1 character per code point.

    This is an approximation of X's "weighted" character counting
    used for the 280-character limit. We do not need exact
    conformance for v1 because the prompt tells the model to stay
    under 280 anyway.
    """
    if not text:
        return 0
    url_total = 0
    for pattern in URL_PATTERNS:
        for m in pattern.finditer(text):
            url_total += max(0, len(m.group(0)) - 23)
    return len(text) - url_total


def slugify(text: str, max_len: int = 60) -> str:
    """Make a filesystem-safe slug from arbitrary text."""
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-").lower()
    return s[:max_len] or "untitled"


# ---- Cashtag utilities ------------------------------------------------------

def find_cashtags(text: str) -> list[str]:
    """Return all cashtags in ``text`` in order of appearance.

    A cashtag is ``$`` followed by 1–5 ticker characters (letters or
    digits). Dollar amounts like ``$175`` or ``$25k`` count as
    cashtags per X's parser, so the regex is alphanumeric.

    X caps a post at one cashtag. Two or more returns 403
    ``not-authorized-for-resource`` and the post is rejected without
    a bill — but it's a round-trip the user shouldn't have to take.
    """
    if not text:
        return []
    return CASHTAG_PATTERN.findall(text)


def count_cashtags(text: str) -> int:
    """Return how many cashtags ``text`` carries."""
    return len(find_cashtags(text))


# ---- Post validation --------------------------------------------------------

@dataclass(frozen=True)
class PostValidationError:
    """A single pre-flight check failure for a post body.

    ``message`` is what we show the user; ``hint`` is the suggested
    fix (also user-facing). Multiple errors can apply to a single
    body, so the validator returns a list, not the first error.
    """
    code: str
    message: str
    hint: str


def validate_post_body(
    text: str,
    *,
    role: str = "main",
    allow_url: bool = False,
) -> list[PostValidationError]:
    """Pre-flight checks for a post body before we send it to X.

    Catches the failures X is strictest about, so the user doesn't
    pay for a 4xx round-trip:

    * Empty body
    * Over X_MAX_POST_CHARS (280)
    * Contains a URL when ``allow_url`` is false (X charges $0.200 vs
      $0.015 for plain — the app's whole point is to put the URL in the
      reply instead)
    * More than one cashtag (X returns 403 and rejects the post)
    """
    errs: list[PostValidationError] = []
    if not text or not text.strip():
        errs.append(PostValidationError(
            "empty",
            f"{role.capitalize()} tweet is empty.",
            "Type a draft before posting.",
        ))
        return errs  # any further checks would be misleading on ""

    char_len = x_char_count(text)
    if char_len > X_MAX_POST_CHARS:
        errs.append(PostValidationError(
            "too_long",
            f"{role.capitalize()} tweet is {char_len} characters "
            f"(X allows {X_MAX_POST_CHARS}; please trim).",
            "Cut filler words; the AI prompt aims for 220–260.",
        ))

    if contains_url(text) and not allow_url:
        errs.append(PostValidationError(
            "url_in_body",
            f"{role.capitalize()} tweet contains a URL — would cost "
            "$0.200 instead of $0.015.",
            "Move the URL to the reply field below.",
        ))

    n_cashtags = count_cashtags(text)
    if n_cashtags > X_MAX_CASHTAGS_PER_POST:
        cashtags = find_cashtags(text)
        errs.append(PostValidationError(
            "too_many_cashtags",
            f"{role.capitalize()} tweet has {n_cashtags} cashtags "
            f"({', '.join(cashtags)}) — X allows at most "
            f"{X_MAX_CASHTAGS_PER_POST}.",
            "Pick the most relevant ticker and drop the rest "
            "(or rephrase without a $ symbol for the others).",
        ))

    return errs
