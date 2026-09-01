"""URL detection and character counting."""
from x_auto.utils.text import (
    X_MAX_POST_CHARS,
    contains_url,
    count_cashtags,
    extract_first_url,
    find_cashtags,
    validate_post_body,
    x_char_count,
)


class TestContainsUrl:
    def test_https(self):
        assert contains_url("see https://example.com for details")

    def test_http(self):
        assert contains_url("visit http://example.com please")

    def test_www(self):
        assert contains_url("go to www.example.com today")

    def test_bare_domain_com(self):
        assert contains_url("check brand.com for the docs")

    def test_bare_domain_io(self):
        assert contains_url("upload at app.io today")

    def test_no_url_just_words(self):
        assert not contains_url("just some normal text without links")

    def test_empty(self):
        assert not contains_url("")

    def test_spelled_out_domain(self):
        # "brand dot com" should NOT flag.
        assert not contains_url("go to brand dot com for the deal")

    def test_url_in_middle_of_sentence(self):
        assert contains_url("see https://x.com/foo now")

    def test_case_insensitive(self):
        assert contains_url("HTTPS://EXAMPLE.COM")
        assert contains_url("Brand.COM")
        assert contains_url("WWW.Example.com")


class TestExtractFirstUrl:
    def test_https_first(self):
        assert extract_first_url("https://x.com here https://y.com") == "https://x.com"

    def test_www(self):
        assert extract_first_url("go to www.example.com now") == "www.example.com"

    def test_bare(self):
        assert extract_first_url("see brand.io today") == "brand.io"

    def test_none(self):
        assert extract_first_url("no url here") is None


class TestXCharCount:
    def test_plain(self):
        assert x_char_count("hello world") == 11

    def test_url_shorthand(self):
        # URL is shortened to 23 chars by t.co, so a long URL counts as 23.
        long = "https://example.com/" + "a" * 100
        # length of the original is 116, after t.co shortening it's 23.
        assert x_char_count(long) == 23

    def test_empty(self):
        assert x_char_count("") == 0


class TestCashtags:
    """Cashtag detection and the ``≤1 cashtag per post`` rule.

    X rejects any post with two or more cashtags (e.g. ``$BTC $ETH``)
    with a 403. We pre-flight this in the Publish tab and also tell
    the rephrase prompt not to generate more than one. Dollar
    amounts like ``$175k`` count as cashtags per X's parser, so the
    regex is alphanumeric.
    """

    def test_single_cashtag_detected(self):
        assert find_cashtags("markets looking bullish on $NVDA today") == ["$NVDA"]

    def test_multiple_cashtags_detected(self):
        text = "split across $NVDA and $MRVL markets"
        assert find_cashtags(text) == ["$NVDA", "$MRVL"]

    def test_dollar_amounts_count_as_cashtags(self):
        # ``$175k`` and ``$25k`` parse as cashtags per X, so the
        # rephrase prompt has to avoid them.
        text = "keeping the $175k weekly pool intact; $25k of that..."
        assert count_cashtags(text) == 2

    def test_no_cashtag(self):
        assert find_cashtags("just plain text without tickers") == []

    def test_word_boundary(self):
        # The regex is 1–5 alphanumeric chars. Single letter and
        # 5-letter tickers both qualify; the next char must be a
        # non-ticker (a word boundary).
        assert find_cashtags("price $a is up") == ["$a"]
        assert find_cashtags("price $abcde is up") == ["$abcde"]
        # Six letters in a row doesn't match (tickers are capped at 5).
        assert find_cashtags("price $abcdef is up") == []
        # Trailing punctuation is OK (``$a.`` is a cashtag then a period).
        assert find_cashtags("up $a. now") == ["$a"]


class TestValidatePostBody:
    """Pre-flight checks for the Publish tab so we don't pay for a 4xx."""

    def test_clean_body_passes(self):
        assert validate_post_body("just a normal tweet about $NVDA") == []

    def test_too_long(self):
        text = "a" * (X_MAX_POST_CHARS + 1)
        errs = validate_post_body(text)
        codes = [e.code for e in errs]
        assert "too_long" in codes

    def test_url_in_body(self):
        errs = validate_post_body("see https://x.com for the deal")
        assert any(e.code == "url_in_body" for e in errs)

    def test_url_in_reply_is_allowed(self):
        assert validate_post_body(
            "See https://x.com for the deal", role="reply", allow_url=True
        ) == []

    def test_two_cashtags_blocked(self):
        errs = validate_post_body("markets split between $NVDA and $MRVL today")
        cashtag_errs = [e for e in errs if e.code == "too_many_cashtags"]
        assert len(cashtag_errs) == 1
        assert "$NVDA" in cashtag_errs[0].message
        assert "$MRVL" in cashtag_errs[0].message
        # The hint tells the user how to fix it.
        assert "Pick" in cashtag_errs[0].hint or "drop" in cashtag_errs[0].hint.lower()

    def test_dollar_amounts_count_for_the_cashtag_rule(self):
        # The original report: ``$175k weekly pool`` + ``$25k of that``
        # + ``$NVDA`` -- 3 cashtags. Must be blocked.
        body = (
            "Atlas Markets extended USDC rewards 6 more weeks, keeping the "
            "$175k weekly pool intact. $25k of that gets split across "
            "$NVDA and MRVL markets."
        )
        errs = validate_post_body(body)
        assert any(e.code == "too_many_cashtags" for e in errs)

    def test_exactly_one_cashtag_is_fine(self):
        # Boundary: 1 cashtag is allowed.
        assert validate_post_body("bullish on $NVDA today") == []

    def test_empty_body(self):
        errs = validate_post_body("")
        assert any(e.code == "empty" for e in errs)
        # Don't return other errors for an empty body — they'd be noise.
        assert all(e.code == "empty" for e in errs)

    def test_role_label_appears_in_message(self):
        errs = validate_post_body("a" * 999, role="reply")
        assert any("Reply" in e.message for e in errs)
