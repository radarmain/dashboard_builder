# DNS records (Cloudflare)

DNS lives in Cloudflare. We never change it from code: these are the records YOU add. All are
**DNS only (grey cloud, NOT proxied)**. They are the ONLY changes; nothing existing is edited or removed.

| # | Type  | Name (Cloudflare "Name")                 | Target                                                              | Proxy    | Status |
|---|-------|------------------------------------------|---------------------------------------------------------------------|----------|--------|
| 1 | CNAME | `_07bf555e2f63c9184c4e7790f13896e2`      | `_8004e51400d56322a83d3c29e104b48c.jkddzztszm.acm-validations.aws`  | DNS only | added; cert is ISSUED |
| 2 | CNAME | `builder`                                | `d2b1ocm7efgvua.cloudfront.net`                                     | DNS only | to add |
| 3 | CNAME | `*`                                      | `d1ovuelimg7ibs.cloudfront.net`                                     | DNS only | to add |

Notes:
- Record 1 is the ACM validation record for the `*.radar-analytics.com` certificate
  (`scripts/request_cert.py`). Keep it: ACM uses it to auto-renew.
- `builder.radar-analytics.com` -> Builder distribution; any other `<client>.radar-analytics.com`
  -> Viewer distribution. Explicit records (www, mail, amplify, requests, ...) win over `*`, so
  existing hostnames are unaffected.
- Distribution domains come from the `rdb-app` stack outputs (`BuilderUrl`, `ViewerUrl`).
- Do not enable the Cloudflare proxy (orange cloud): CloudFront terminates TLS with our ACM cert.
