# User Flow

Hierarchy: Client > Project > View.

1. User lands on builder.radar-analytics.com.
2. Authenticates via AWS Cognito. The app detects their group: admin, analyst, or client. (Clients do not use the Builder; they use the Viewer.)
3. Home screen: list of existing clients, plus "Add client".
4. Add client: user enters client name; slug is auto-suggested and editable (lowercase, hyphenated, unique). On create, they land in that client's portal.
5. Client portal: list of existing projects (dashboards), plus "Create project".
6. Create project: user enters project name and slug. They land on the project page. Viewer path will be [client_slug].radar-analytics.com/[project_slug].
7. If the project is new or has no connection, show Project Settings first.
8. Project Settings:
   - Select a BigQuery project to connect.
   - Then multi-select tables from that project to connect.
   - On save, snapshot each table's schema (from INFORMATION_SCHEMA) and let the user add or edit column descriptions. Descriptions are fed to the LLM.
9. User switches to the Builder tab inside the project and clicks "Add view".
10. Add view: name and slug. They land in the view editor.
11. View editor: mostly empty canvas with a prompt box at the bottom. The user prompts the system to create a chart, graph, or table.
12. Each element on the canvas can be clicked to open a panel showing the SQL that renders it. The user can edit the SQL directly and re-run it. Edited SQL goes through the same validation as generated SQL.
13. Save: stores a draft revision of the view spec. The analyst can preview the draft at any time.
14. Publish: promotes the current draft to the live version. Clients see only the published version.
15. Revisions: the user can browse the last 10 revisions of a view and restore one as the new draft.

View statuses: draft (has unpublished changes), published (live matches latest save), and a view may be both (published version exists plus a newer draft).

Client access: client users (Cognito group client) are restricted to one or more specific client slugs and can only read published views for those clients. Shared links without login are not in scope; clients log in.
