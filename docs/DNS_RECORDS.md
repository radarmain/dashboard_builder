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

## SES sending identity: `notify.radar-analytics.com` (Cognito invite emails)

Created by `scripts/setup_ses_identity.py` in **us-east-2** (Easy DKIM, 2048-bit, no custom MAIL FROM).
Three DKIM CNAMEs, all **DNS only (grey cloud)**. These are the ONLY Cloudflare changes for this.
In Cloudflare's "Name" box type the short name (the zone `radar-analytics.com` is appended).

| # | Type  | Name (Cloudflare)                                          | Target                                              | Proxy    | Status |
|---|-------|------------------------------------------------------------|-----------------------------------------------------|----------|--------|
| 4 | CNAME | `ouodcgew4hgz6ikcjdkoagjfhruwmolt._domainkey.notify`       | `ouodcgew4hgz6ikcjdkoagjfhruwmolt.dkim.amazonses.com` | DNS only | to add |
| 5 | CNAME | `kjtoeyqoiegol7bsx76muug5xoefjb64._domainkey.notify`       | `kjtoeyqoiegol7bsx76muug5xoefjb64.dkim.amazonses.com` | DNS only | to add |
| 6 | CNAME | `vbe6fcvkcxyocdulzuioaveupgdpgk3q._domainkey.notify`       | `vbe6fcvkcxyocdulzuioaveupgdpgk3q.dkim.amazonses.com` | DNS only | to add |

Full names: `<token>._domainkey.notify.radar-analytics.com`. `notify` is in the reserved-slug list, so
the `*` wildcard (Viewer) no longer applies to it; records under `notify.` are explicit.
