# View Spec

A view is a JSON document (validated by Pydantic on the backend and a matching TypeScript type on the frontend). One shared renderer draws it. Keep the format small; extend only when needed.

```json
{
  "schema_version": 1,
  "view": {"name": "Weekly Performance", "slug": "weekly-performance"},
  "revision": 7,
  "updated_at": "2026-11-21T12:00:00Z",
  "updated_by": "user@example.com",
  "layout": [
    {"element_id": "el_1", "x": 0, "y": 0, "w": 12, "h": 6}
  ],
  "elements": [
    {
      "id": "el_1",
      "type": "chart",
      "title": "Spend by Channel",
      "chart": {
        "kind": "bar",
        "x": "channel",
        "y": ["spend"],
        "series": null,
        "stacked": false,
        "format": {"spend": "currency"}
      },
      "format": {},
      "sql": "SELECT channel, SUM(spend) AS spend FROM `proj.dataset.table` GROUP BY channel",
      "cache_ttl_seconds": 3600
    }
  ]
}
```

Element types for the prototype: chart (kinds: bar, line, area, pie, scatter), table, kpi (single value or small group of values). Text/markdown blocks are a nice-to-have.

Notes:

- `layout` uses a 12-column grid: `x + w <= 12` (enforced by the backend). `y` and `h` are in row units.
- `chart.format` maps y columns to `currency`, `percent` or `number`. For table and kpi elements the same hints go in the element's top-level `format` (column -> hint).
- kpi renders the first result row: each column becomes one number.
- Decimals arrive from the API as strings and dates as ISO strings; the renderer coerces them.

Rules:

- sql is stored in the spec and only ever executed server-side after validation.
- The spec contains no credentials and no data.
- layout is a simple grid; do not build a drag-and-drop editor first. The LLM and the analyst can adjust layout through chat.
- Revisions are immutable once written.
