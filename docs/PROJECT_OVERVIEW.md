# Project Overview

## Background

Our product team built CPA, which visualizes performance media data and parses creative assets with AI for a RAG-based Q&A experience. Separately, our data team builds highly specific client dashboards in Tableau on top of BigQuery data. That process is slow, the data is not always accurate, and clients are leaving.

CPA will eventually replace Tableau through highly customizable, prompt-driven dashboards, but it is not ready yet. The Dashboard Builder (rdb) is a standalone prototype that delivers the "chat to build a dashboard" experience now, fully separate from CPA's ingestion pipeline (CPA copies data from BigQuery into its own RDS database; rdb does not).

## Goal

Let a DA go from "client + BigQuery tables" to a live client dashboard in one conversation, with fewer errors than the Tableau process.

## Core idea

- An analyst picks a BigQuery project and one or more tables for a client project.
- The analyst chats with the system to create charts, graphs, and tables. The LLM writes SQL plus a chart definition.
- The analyst can open any chart, see the SQL, and edit it directly.
- The result is saved as a JSON view spec (not static output), so data stays live from BigQuery at view time.
- Saving creates a draft the analyst can preview. Publishing makes it live for clients.
- Clients see dashboards at [client_slug].radar-analytics.com/[project_slug].

## Principles

- Spec, not code: views are JSON specs rendered by one shared React/Recharts runtime, not generated React codebases.
- Accuracy levers: table schemas and analyst-written column descriptions feed the LLM; all SQL is validated; query results can be reconciled by the analyst before publishing.
- No database where S3 works; DynamoDB only for the index and access mapping.
- Prototype speed over perfection.

## Non-goals (for now)

- Replacing CPA, RAG over creative assets, or sharing CPA's RDS.
- Two-person review before publishing.
- Exhaustive revision history (last 10 revisions per view is enough).
- Pixel-perfect Tableau migration.
- Automated Tableau import (possible later via the Tableau Metadata API).
