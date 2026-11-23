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
- kpi renders the first result row: each column becomes one number. `format: {"col": "percent"}` shows fractions as percentages (0.82 -> 82%).
- `chart.outline` (bool, area): heavier stroke on top of the fill. `chart.labels` (bool): value labels on points/bars. Together they cover Tableau's dual-axis "line + area from one measure".
- table `pivot` `{row, column, value}` (all result columns, long format in): one row per `row` value, one column per `column` value, cells = `value`, formatted with `format[value]`.
- table `heatmap` `{palette: "diverging"|"sequential", min, max}` (needs `pivot`): colors cells by value scaled between min and max. diverging = red (min) to green (max), e.g. 0 to 1 for occupancy; sequential = light to dark.
- `cache_ttl_seconds` is 21600 on elements created by the Tableau importer.
- Optional `import` (Tableau importer only, schema_version stays 1): `{source: "tableau", sheet_name, status: "unverified"|"needs_review"|"verified"|"differs", notes: string[], dry_run_bytes, source_is_view, base_tables: string[]}`. `status` describes how well the draft is known to match Tableau; it is informational. Never sent to portal/viewer clients.
- Optional `review_status: "reviewed"` (set by the analyst in the element panel). An element is "unreviewed" when `import.status` is `unverified`/`needs_review`/`differs` and `review_status` is not `reviewed`; publishing a view with unreviewed elements needs an explicit confirm (audit-logged).
- Decimals arrive from the API as strings and dates as ISO strings; the renderer coerces them.

Rules:

- sql is stored in the spec and only ever executed server-side after validation.
- The spec contains no credentials and no data.
- layout is a simple grid; do not build a drag-and-drop editor first. The LLM and the analyst can adjust layout through chat.
- Revisions are immutable once written.
