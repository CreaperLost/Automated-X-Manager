---
name: X
description: Use when building applications that need to read or publish posts, search X's public conversation, manage user relationships, access real-time data streams, or analyze trends. Agents should reach for this skill when users request API integration, data retrieval, authentication setup, or troubleshooting X API issues.
metadata:
    mintlify-proj: x
    version: "1.0"
---

# X API Skill

## Product summary

The X API provides programmatic access to X's public conversation through modern REST endpoints. Agents use it to read posts, publish content, search archives, manage users, access real-time streams, and analyze trends. The API uses pay-per-usage pricing with no subscriptions. Key resources: Bearer Token for app-only requests, OAuth 1.0a or 2.0 for user-context requests, official Python and TypeScript SDKs, and the Developer Console at https://console.x.com. Primary documentation: https://docs.x.com/x-api/introduction

## When to use

Reach for this skill when:
- A user needs to integrate X data into an application (search posts, look up users, retrieve timelines)
- Building real-time features (filtered stream, webhooks, activity subscriptions)
- Publishing or managing posts, likes, reposts, or direct messages
- Analyzing engagement metrics or trends
- Setting up authentication for X API requests
- Troubleshooting API errors (401, 403, 429, rate limits)
- Choosing between authentication methods or endpoint options
- Implementing pagination or handling large result sets
- Migrating from v1.1 API to v2

## Quick reference

### Authentication methods

| Method | Use case | Credentials |
|:-------|:---------|:------------|
| **Bearer Token (OAuth 2.0 App-Only)** | Read public data, no user context needed | API Key + Secret → Bearer Token |
| **OAuth 1.0a User Context** | Act on behalf of a user (post, like, follow) | API Key, Secret, Access Token, Access Token Secret |
| **OAuth 2.0 Authorization Code (PKCE)** | User-facing apps, multi-user scenarios | Client ID, Client Secret, redirect URI |
| **Basic Auth** | Enterprise APIs only | Username + password (HTTP Basic) |

### Common endpoints

| Task | Endpoint | Method |
|:-----|:---------|:--------|
| Look up user by username | `GET /2/users/by/username/:username` | Bearer Token |
| Get user's posts | `GET /2/users/:id/tweets` | Bearer Token |
| Search recent posts (7 days) | `GET /2/tweets/search/recent` | Bearer Token |
| Search full archive | `GET /2/tweets/search/all` | Bearer Token (Enterprise) |
| Create post | `POST /2/tweets` | OAuth 1.0a or 2.0 User |
| Get filtered stream | `GET /2/tweets/search/stream` | Bearer Token |
| Add stream rule | `POST /2/tweets/search/stream/rules` | Bearer Token |
| Get post by ID | `GET /2/tweets/:id` | Bearer Token |
| Like a post | `POST /2/users/:id/likes` | OAuth 1.0a or 2.0 User |

### Rate limit headers

Every response includes:
- `x-rate-limit-limit` — Max requests in 15-minute window
- `x-rate-limit-remaining` — Requests left
- `x-rate-limit-reset` — Unix timestamp when window resets

### Query parameters

| Parameter | Purpose | Example |
|:----------|:--------|:--------|
| `fields` | Request specific fields on primary object | `tweet.fields=created_at,public_metrics` |
| `expansions` | Include related objects (author, media, etc.) | `expansions=author_id,attachments.media_keys` |
| `max_results` | Limit results per page | `max_results=100` |
| `pagination_token` or `next_token` | Navigate pages | Use value from `meta.next_token` |
| `query` | Search string with operators | `query=from:xdevelopers lang:en` |

## Decision guidance

### When to use Bearer Token vs. OAuth 1.0a vs. OAuth 2.0

| Scenario | Use | Why |
|:---------|:----|:----|
| Reading public data only, no user context | **Bearer Token** | Simplest, no user authentication needed |
| App needs to post/like on behalf of authenticated user | **OAuth 1.0a or 2.0 User** | Requires user authorization |
| Building user-facing web app with sign-in | **OAuth 2.0 PKCE** | Better security, multi-device support |
| Legacy integration or specific requirement | **OAuth 1.0a** | Backward compatible, but more complex |
| Enterprise API (DMs, account activity) | **Basic Auth** | Required for some enterprise endpoints |

### When to use search/recent vs. search/all vs. filtered stream

| Need | Use | Limits |
|:-----|:----|:--------|
| Search posts from last 7 days | `/2/tweets/search/recent` | 450 req/15min (app), 300 req/15min (user) |
| Search entire archive (back to 2006) | `/2/tweets/search/all` | 300 req/15min, 1 req/sec (Enterprise only) |
| Real-time post delivery as published | `/2/tweets/search/stream` | 50 req/15min, 1 connection, 1000 rules |
| Historical analysis, one-time queries | `/2/tweets/search/recent` | Sufficient for most use cases |

### When to use fields vs. expansions

| Situation | Use |
|:----------|:----|
| Need additional fields on the primary object (post text, metrics) | `tweet.fields=created_at,public_metrics` |
| Need data from related objects (author name, media URLs) | `expansions=author_id,attachments.media_keys` + `user.fields=username` |
| Want minimal response to reduce bandwidth | Omit both; use defaults |
| Building analytics dashboard | Combine both for rich data |

## Workflow

### 1. Set up authentication

1. Go to https://console.x.com and sign in with X account
2. Create a new app or select existing one
3. Navigate to "Keys and Access Tokens" tab
4. Copy Bearer Token (for app-only requests) or generate Access Tokens (for user context)
5. Store credentials securely in environment variables (never hardcode)
6. Test with a simple request: `curl "https://api.x.com/2/users/by/username/xdevelopers" -H "Authorization: Bearer $BEARER_TOKEN"`

### 2. Choose your approach

- **Quick testing**: Use cURL or Postman
- **Production code**: Use official SDK (Python or TypeScript)
- **CLI tool**: Use xurl for one-off requests with built-in OAuth

### 3. Build your request

1. Identify the endpoint you need (search, lookup, manage, stream)
2. Determine required parameters (query, user ID, post ID)
3. Add `fields` parameter to request specific fields
4. Add `expansions` parameter to include related objects
5. Set `max_results` for pagination control

### 4. Handle pagination

1. Check response `meta.next_token`
2. If present, make next request with `pagination_token=<next_token>`
3. Repeat until `next_token` is absent
4. For polling use cases, use `since_id` or `until_id` instead

### 5. Handle errors and rate limits

1. Check HTTP status code
2. If 429: Read `x-rate-limit-reset` header, wait, retry
3. If 401: Verify Bearer Token or OAuth credentials
4. If 403: Check app permissions in Developer Console
5. If 400: Validate query syntax and required parameters

### 6. Verify and monitor

1. Check response structure: `data` field contains results
2. Monitor `x-rate-limit-remaining` to avoid hitting limits
3. Log errors with full response for debugging
4. Test with small `max_results` before production deployment

## Common gotchas

- **Hardcoded credentials**: Never commit API keys to version control. Use environment variables or secret managers.
- **Forgetting Bearer Token prefix**: Authorization header must be `Bearer YOUR_TOKEN`, not just the token.
- **Requesting fields without expansions**: If you want author name, you need both `expansions=author_id` and `user.fields=username`.
- **Hitting rate limits silently**: Always check `x-rate-limit-remaining` proactively; don't wait for 429 errors.
- **Pagination token expiration**: Tokens expire after ~30 minutes; don't store them long-term.
- **Search limited to 7 days**: `/2/tweets/search/recent` only returns posts from last 7 days. Use `/2/tweets/search/all` for older data (Enterprise only).
- **Filtered stream rules not persisting**: Rules are per-connection; reconnecting requires re-adding rules.
- **Post character limits**: Posts are limited to 280 characters by default; use `text_processing_version=v2_tweeted_text` for accurate counting.
- **User context required for mutations**: Creating posts, liking, following require OAuth user tokens, not Bearer Token.
- **Mutable data in search results**: User handles and post metrics can change; search returns data at query-time, not creation-time.

## Verification checklist

Before submitting work:

- [ ] Authentication credentials are stored securely (environment variables, not hardcoded)
- [ ] Bearer Token or OAuth tokens are valid and not expired
- [ ] Endpoint URL is correct and uses `https://api.x.com/2/`
- [ ] Required parameters are included (query, user ID, post ID as needed)
- [ ] Authorization header format is correct: `Authorization: Bearer TOKEN`
- [ ] Response parsing handles both `data` and `errors` fields
- [ ] Rate limit headers are checked before making next request
- [ ] Pagination logic handles `next_token` correctly
- [ ] Error handling covers 401, 403, 429, and 400 status codes
- [ ] Test with small `max_results` before production deployment
- [ ] Credentials are not logged or exposed in error messages

## Resources

- **Comprehensive navigation**: https://docs.x.com/llms.txt — Full page-by-page listing for agent navigation
- **Getting started**: https://docs.x.com/make-your-first-request — Step-by-step guide with cURL examples
- **Authentication guide**: https://docs.x.com/fundamentals/authentication/overview — All auth methods explained
- **Rate limits reference**: https://docs.x.com/x-api/fundamentals/rate-limits — Per-endpoint limits and handling strategies

---

> For additional documentation and navigation, see: https://docs.x.com/llms.txt
