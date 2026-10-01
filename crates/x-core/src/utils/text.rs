//! Text utilities: URL detection, cashtag counting, post validation.
//!
//! Ported from `src/x_auto/utils/text.py`. The regexes and thresholds are
//! load-bearing, not cosmetic:
//!
//! * `contains_url` gates the app's central cost invariant. X charges
//!   $0.200 for a post containing an autolinked URL vs $0.015 plain, so
//!   the URL must stay in the reply.
//! * `count_cashtags` prevents a 403 round-trip: X rejects a post with
//!   two or more cashtags.
//! * `x_char_count` approximates X's weighted counting (every URL counts
//!   as 23 chars via t.co).

use regex::Regex;
use std::sync::OnceLock;

/// X's hard free-tier post limit.
pub const X_MAX_POST_CHARS: usize = 280;

/// X rejects more than one cashtag per post with a 403.
pub const X_MAX_CASHTAGS_PER_POST: usize = 1;

/// t.co shortens every URL to exactly 23 characters.
const TCO_URL_CHARS: usize = 23;

fn regex(cell: &'static OnceLock<Regex>, pattern: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(pattern).expect("valid regex literal"))
}

fn url_explicit() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    regex(&R, r"(?i)https?://\S+")
}

fn url_www() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    regex(&R, r"(?i)\bwww\.\S+\.[a-z]{2,}(?:/\S*)?")
}

/// Bare domains with a known TLD. Kept byte-for-byte equivalent to the
/// Python pattern's TLD alternation.
fn url_bare_domain() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    regex(
        &R,
        concat!(
            r"(?i)\b[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*",
            r"\.(?:com|io|ai|app|dev|co|net|org|me|info|biz|us|uk|ca|de|fr|jp|cn|tech|xyz|so|gg|tv|fm|am|to|sh|ly|gl|vc|im|nu|rs|re|asia|museum|cloud|online|store|site|blog|live|news|design|media|consulting|solutions|tools|systems|network|group|center|company|academy|education|energy|finance|legal|health|lab|space|world|today|life|cool|wtf|foo|bar|baz|local|global|earth|uno|bike|cool|games|app|page|link|click|review|press|wiki|plus|now|hub|fast|easy|pro|community|fund|email|chat|talk|hello|hi|hey|wow|fun|love|art|music|film|movie|video|podcast|stream|audio|cloud|data|api|sdk|dev|engine|server|host|cloud|saas|app)\b"
        ),
    )
}

/// A cashtag: `$` + 1–5 word chars. `\b` in Rust requires explicit
/// Unicode handling; `\w` is already ASCII-class restricted in the
/// `(?i)`-free pattern, and we assert the boundary manually in
/// `find_cashtags` via the regex's own `\b`.
fn cashtag() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    regex(&R, r"\$[0-9A-Za-z_]{1,5}\b")
}

fn url_patterns() -> [&'static Regex; 3] {
    [url_explicit(), url_www(), url_bare_domain()]
}

/// True if the text contains a URL X would autolink.
///
/// X's pricing keys off *presence*, not length, so this checks the raw
/// text. The 23-char t.co shortening is a display concern only.
pub fn contains_url(text: &str) -> bool {
    if text.is_empty() {
        return false;
    }
    url_patterns().iter().any(|p| p.is_match(text))
}

/// The first URL in the text, or `None`.
pub fn extract_first_url(text: &str) -> Option<String> {
    if text.is_empty() {
        return None;
    }
    for p in url_patterns() {
        if let Some(m) = p.find(text) {
            return Some(m.as_str().to_string());
        }
    }
    None
}

/// Approximate X's weighted character count: URLs count as 23 chars,
/// everything else as one char per code point.
pub fn x_char_count(text: &str) -> usize {
    if text.is_empty() {
        return 0;
    }
    let mut url_total = 0usize;
    for p in url_patterns() {
        for m in p.find_iter(text) {
            url_total += m.as_str().chars().count().saturating_sub(TCO_URL_CHARS);
        }
    }
    text.chars().count().saturating_sub(url_total)
}

/// Filesystem-safe slug from arbitrary text.
pub fn slugify(text: &str, max_len: usize) -> String {
    static NON_SAFE: OnceLock<Regex> = OnceLock::new();
    let re = regex(&NON_SAFE, r"[^A-Za-z0-9_-]+");
    let s: String = re.replace_all(text, "-").to_string();
    let s = s.trim_matches('-').to_lowercase();
    let out: String = s.chars().take(max_len).collect();
    if out.is_empty() {
        "untitled".to_string()
    } else {
        out
    }
}

/// All cashtags in order of appearance.
///
/// Dollar amounts like `$175` or `$25k` count as cashtags per X's parser,
/// hence the alphanumeric class rather than letters-only.
pub fn find_cashtags(text: &str) -> Vec<String> {
    if text.is_empty() {
        return Vec::new();
    }
    cashtag().find_iter(text).map(|m| m.as_str().to_string()).collect()
}

pub fn count_cashtags(text: &str) -> usize {
    find_cashtags(text).len()
}

/// A single pre-flight check failure for a post body.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct PostValidationError {
    pub code: String,
    pub message: String,
    pub hint: String,
}

/// Pre-flight checks for a post body before we send it to X.
///
/// Returns *all* failures, not just the first, so the user can fix
/// everything in one pass instead of one error per round-trip.
pub fn validate_post_body(text: &str, role: &str, allow_url: bool) -> Vec<PostValidationError> {
    let mut errs = Vec::new();
    let role_label = capitalize(role);

    if text.trim().is_empty() {
        errs.push(PostValidationError {
            code: "empty".into(),
            message: format!("{role_label} tweet is empty."),
            hint: "Type a draft before posting.".into(),
        });
        return errs; // further checks on "" would mislead
    }

    let char_len = x_char_count(text);
    if char_len > X_MAX_POST_CHARS {
        errs.push(PostValidationError {
            code: "too_long".into(),
            message: format!(
                "{role_label} tweet is {char_len} characters (X allows {X_MAX_POST_CHARS}; please trim)."
            ),
            hint: "Cut filler words; the AI prompt aims for 220-260.".into(),
        });
    }

    if contains_url(text) && !allow_url {
        errs.push(PostValidationError {
            code: "url_in_body".into(),
            message: format!(
                "{role_label} tweet contains a URL - would cost $0.200 instead of $0.015."
            ),
            hint: "Move the URL to the reply field below.".into(),
        });
    }

    let n = count_cashtags(text);
    if n > X_MAX_CASHTAGS_PER_POST {
        let tags = find_cashtags(text).join(", ");
        errs.push(PostValidationError {
            code: "too_many_cashtags".into(),
            message: format!(
                "{role_label} tweet has {n} cashtags ({tags}) - X allows at most {X_MAX_CASHTAGS_PER_POST}."
            ),
            hint: "Pick the most relevant ticker and drop the rest (or rephrase without a $ symbol for the others).".into(),
        });
    }

    errs
}

/// Python's `str.capitalize()` for the role label ("main" -> "Main").
fn capitalize(s: &str) -> String {
    let mut c = s.chars();
    match c.next() {
        Some(f) => f.to_uppercase().collect::<String>() + &c.as_str().to_lowercase(),
        None => String::new(),
    }
}

/// Strip em-dashes, en-dashes, double hyphens, and unwanted $AI tickers.
pub fn clean_humanized_text(text: &str, niche: &str) -> String {
    if text.is_empty() {
        return text.to_string();
    }
    let text = text.to_string();
    static DASH: OnceLock<Regex> = OnceLock::new();
    static DOUBLE: OnceLock<Regex> = OnceLock::new();
    static COMMA_RUN: OnceLock<Regex> = OnceLock::new();
    static COMMA_DOT: OnceLock<Regex> = OnceLock::new();
    static COMMA_COLON: OnceLock<Regex> = OnceLock::new();
    static SPACES: OnceLock<Regex> = OnceLock::new();
    static TRAILING_TICKER: OnceLock<Regex> = OnceLock::new();
    static AI_TICKER: OnceLock<Regex> = OnceLock::new();
    static AGI_TICKER: OnceLock<Regex> = OnceLock::new();

    let mut cleaned = regex(&DASH, r"\s*[\x{2014}\x{2013}]\s*")
        .replace_all(&text, ", ")
        .to_string();
    cleaned = regex(&DOUBLE, r"\s*--\s*").replace_all(&cleaned, ", ").to_string();
    cleaned = regex(&COMMA_RUN, r",\s*,").replace_all(&cleaned, ",").to_string();
    cleaned = regex(&COMMA_DOT, r",\s*\.").replace_all(&cleaned, ".").to_string();
    cleaned = regex(&COMMA_COLON, r",\s*:").replace_all(&cleaned, ":").to_string();

    // Strip leading punctuation introduced at a sentence start or line break.
    cleaned = strip_leading_punct(&cleaned);
    cleaned = regex(&SPACES, r"[ \t]+").replace_all(&cleaned, " ").to_string();

    let clean_niche = niche.trim().to_lowercase();
    let lower = cleaned.to_lowercase();
    if clean_niche == "ai" || lower.contains("$ai") || lower.contains("$agi") {
        cleaned = regex(&TRAILING_TICKER, r"(?i)\s+\$(?:AI|AGI)\s*$")
            .replace_all(&cleaned, "")
            .to_string();
        cleaned = regex(&AI_TICKER, r"(?i)\$AI\b").replace_all(&cleaned, "AI").to_string();
        cleaned = regex(&AGI_TICKER, r"(?i)\$AGI\b").replace_all(&cleaned, "AGI").to_string();
    }

    cleaned.trim().to_string()
}

/// Port of Python's `re.sub(r"(?:^|\n)[,\s]+", ...)` used to strip
/// punctuation left dangling at a sentence or line start after dash
/// removal. The original replaces a matched run with `"\n"` when the run
/// contained a newline and `""` otherwise, so `re` can't express it as a
/// plain `replace_all`; we walk the run instead.
///
/// Note the match requires at least one `[,\s]` character after the
/// line start. A line beginning with an ordinary character is untouched,
/// which is why the empty-run case falls through and emits the char.
fn strip_leading_punct(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let chars: Vec<char> = text.chars().collect();
    let mut i = 0usize;
    let mut at_line_start = true;

    while i < chars.len() {
        let ch = chars[i];
        if at_line_start {
            // Consume the maximal run of [,\s]+.
            let start = i;
            while i < chars.len() && (chars[i] == ',' || chars[i].is_whitespace()) {
                i += 1;
            }
            if i == start {
                // No run: this char is ordinary text, keep it.
                out.push(ch);
                at_line_start = ch == '\n';
                i += 1;
                continue;
            }
            // Collapse the run to a single newline, or drop it entirely.
            if chars[start..i].contains(&'\n') {
                out.push('\n');
            }
            at_line_start = true;
            continue;
        }
        out.push(ch);
        at_line_start = ch == '\n';
        i += 1;
    }
    out
}

/// Format a reply tweet, guaranteeing the project URL is attached and
/// the result fits within 280 weighted characters.
///
/// Never truncates the URL. If the combined text is too long, the copy
/// portion is trimmed instead.
pub fn format_cta_reply(cta_text: &str, project_url: &str, niche: &str) -> String {
    let clean_url = project_url.trim();
    // Remove the URL from the copy if it already leaked in.
    let copy_owned = if clean_url.is_empty() {
        cta_text.trim().to_string()
    } else {
        cta_text.replace(clean_url, "").trim().to_string()
    };
    let mut copy_text = clean_humanized_text(&copy_owned, niche);

    if clean_url.is_empty() {
        if x_char_count(&copy_text) > X_MAX_POST_CHARS {
            copy_text = truncate_to_last_space(&copy_text, X_MAX_POST_CHARS);
        }
        return copy_text;
    }

    // Weighted total = copy + 1 (space) + 23 (t.co URL).
    let max_copy_weighted = X_MAX_POST_CHARS - 24; // 256
    if x_char_count(&copy_text) > max_copy_weighted {
        copy_text = truncate_to_last_space(&copy_text, max_copy_weighted);
    }

    copy_text = copy_text.trim_end().to_string();
    if !copy_text.is_empty()
        && !copy_text.ends_with(['.', '!', '?', ':', '\u{2192}'])
    {
        copy_text.push(':');
    }

    if copy_text.is_empty() {
        clean_url.to_string()
    } else {
        format!("{copy_text} {clean_url}")
    }
}

/// Cut to `limit` chars, then back off to the last space (Python's
/// `s[:limit].rsplit(" ", 1)[0]`).
fn truncate_to_last_space(s: &str, limit: usize) -> String {
    let head: String = s.chars().take(limit).collect();
    match head.rfind(' ') {
        Some(idx) => head[..idx].to_string(),
        None => head,
    }
}

/// Remove the first URL found, then collapse whitespace. Defensive guard
/// for the no-URL-in-main-body cost invariant.
pub fn strip_url(text: &str) -> String {
    if !contains_url(text) {
        return text.to_string();
    }
    let url = extract_first_url(text).unwrap_or_default();
    let cleaned = text.replace(&url, "");
    static SPACES: OnceLock<Regex> = OnceLock::new();
    let cleaned = regex(&SPACES, r"[ \t]+").replace_all(&cleaned, " ");
    cleaned.trim().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_explicit_urls() {
        assert!(contains_url("visit https://example.com now"));
        assert!(contains_url("http://example.com"));
        assert_eq!(
            extract_first_url("see https://example.com/x?y=1 now").as_deref(),
            Some("https://example.com/x?y=1")
        );
    }

    #[test]
    fn detects_bare_domains_with_known_tld() {
        assert!(contains_url("check example.com for more"));
        assert!(contains_url("visit foo.io"));
        assert!(contains_url("go to www.example.com"));
    }

    #[test]
    fn ignores_prose_and_spelled_out_domains() {
        assert!(!contains_url("let's build an app"));
        assert!(!contains_url("no links here at all"));
        assert!(!contains_url("brand dot com"));
        assert!(!contains_url(""));
    }

    #[test]
    fn char_count_uses_tco_weighting() {
        // A 46-char URL counts as 23.
        let url = "https://example.com/abcdefghijklmnopqrstuvwxyz";
        assert_eq!(url.chars().count(), 46);
        assert_eq!(x_char_count(url), 23);
        assert_eq!(x_char_count("hello"), 5);
        assert_eq!(x_char_count(""), 0);
    }

    #[test]
    fn counts_cashtags_including_dollar_amounts() {
        assert_eq!(count_cashtags("buying $BTC now"), 1);
        assert_eq!(count_cashtags("$NVDA and $MRVL"), 2);
        assert_eq!(count_cashtags("made $25k"), 1);
        assert_eq!(count_cashtags("no tickers here"), 0);
    }

    #[test]
    fn validate_flags_url_in_main_body() {
        let errs = validate_post_body("look at https://a.com", "main", false);
        let codes: Vec<&str> = errs.iter().map(|e| e.code.as_str()).collect();
        assert!(codes.contains(&"url_in_body"));
    }

    #[test]
    fn validate_allows_url_in_reply() {
        let errs = validate_post_body("see https://a.com", "reply", true);
        assert!(errs.is_empty(), "unexpected errors: {errs:?}");
    }

    #[test]
    fn validate_flags_empty_and_returns_early() {
        let errs = validate_post_body("   ", "main", false);
        assert_eq!(errs.len(), 1);
        assert_eq!(errs[0].code, "empty");
    }

    #[test]
    fn validate_flags_too_long() {
        let long = "a".repeat(300);
        let errs = validate_post_body(&long, "main", false);
        assert!(errs.iter().any(|e| e.code == "too_long"));
    }

    #[test]
    fn validate_flags_multiple_cashtags() {
        let errs = validate_post_body("$BTC and $ETH", "main", false);
        assert!(errs.iter().any(|e| e.code == "too_many_cashtags"));
    }

    #[test]
    fn clean_removes_dashes_and_ai_tickers() {
        assert_eq!(clean_humanized_text("a \u{2014} b", "crypto"), "a, b");
        assert_eq!(clean_humanized_text("a -- b", "crypto"), "a, b");
        // Inline $AI becomes plain AI.
        assert_eq!(clean_humanized_text("the $AI era", "ai"), "the AI era");
        // A trailing " $AI" is removed entirely rather than converted,
        // matching the Python rule order (strip trailing, then inline).
        assert_eq!(clean_humanized_text("watching $AI", "ai"), "watching");
    }

    #[test]
    fn format_cta_guarantees_url_present() {
        let out = format_cta_reply("Check this out", "https://proj.com", "crypto");
        assert!(out.contains("https://proj.com"));
        assert!(out.ends_with("https://proj.com"));
        assert!(out.starts_with("Check this out:"));
    }

    #[test]
    fn format_cta_does_not_duplicate_url() {
        let out = format_cta_reply(
            "Check https://proj.com",
            "https://proj.com",
            "crypto",
        );
        assert_eq!(out.matches("https://proj.com").count(), 1);
    }

    #[test]
    fn format_cta_never_truncates_url() {
        let long_copy = "word ".repeat(200);
        let url = "https://example.com/very/long/path/for/testing/purposes";
        let out = format_cta_reply(&long_copy, url, "crypto");
        assert!(out.ends_with(url), "url must survive truncation");
        assert!(x_char_count(&out) <= X_MAX_POST_CHARS);
    }

    #[test]
    fn format_cta_without_url_returns_copy() {
        assert_eq!(
            format_cta_reply("Just words", "", "crypto"),
            "Just words"
        );
    }

    #[test]
    fn strip_url_removes_first_url_only() {
        // Removing the URL leaves a double space, which the [ \t]+
        // collapse then reduces to one.
        assert_eq!(strip_url("go https://a.com now"), "go now");
        assert_eq!(strip_url("no url"), "no url");
    }

    #[test]
    fn strip_leading_punct_keeps_line_breaks() {
        let input = "first\n, ,second line\n, third";
        let out = strip_leading_punct(input);
        assert!(out.contains('\n'));
        assert!(!out.contains(", ,"));
    }

    #[test]
    fn slugify_produces_safe_names() {
        assert_eq!(slugify("Hello World! 2026", 60), "hello-world-2026");
        assert_eq!(slugify("!!!", 60), "untitled");
    }
}