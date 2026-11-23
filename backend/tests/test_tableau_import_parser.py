"""Tableau import parser: plan content + the credential sanitizer."""

import io
import json
import zipfile
from pathlib import Path

import pytest
from app.tableau_import.parser import ParseError, parse_workbook, plan_from_download

FIXTURE = Path(__file__).parent / "fixtures" / "tiny_workbook.twb"
SECRETS = [
    "svc-user@example.com",
    "HUNTER2-PASSWORD",
    "OAUTHTOKEN-ABC",
    "SERVEROAUTH-XYZ",
    "REFRESH-TOKEN-123",
    "bigquery.googleapis.com",
    "extract.hyper",
    "C:\\\\secret",
    "tableau_internal_user",
    "oauth",
    "password",
]


def plan() -> dict:
    return parse_workbook(FIXTURE.read_bytes())


def test_no_credentials_in_plan() -> None:
    text = json.dumps(plan())
    for s in SECRETS:
        assert s not in text, s


def test_datasource_tables_and_mode() -> None:
    ds = plan()["datasources"][0]
    assert ds["tables"] == ["demo-proj.client_data.occ"]
    assert ds["mode"] == "extract"
    assert ds["connection_classes"] == ["bigquery", "federated", "hyper"]


def test_calcs_and_parameters() -> None:
    p = plan()
    occ = next(c for c in p["calculated_fields"] if c["name"] == "Occupancy")
    assert occ["formula"] == "SUM([Occupied Sqft]) / SUM([Available Sqft])"
    win = next(c for c in p["calculated_fields"] if c["name"] == "Window Occ")
    assert win["kind"] == "table_calc"
    assert p["parameters"] == [
        {"name": "Top N", "datatype": "integer", "default": "10", "domain": "range"}
    ]


def test_sheet_types_and_status() -> None:
    sheets = {s["name"]: s for s in plan()["worksheets"]}
    trend = sheets["Trend"]
    assert trend["chart_type"] == "area" and trend["dual_axis"] is True
    assert trend["status"] == "supported"  # same measure on both axes
    assert sheets["Heatmap"]["chart_type"] == "heatmap"
    assert sheets["Total"]["chart_type"] == "kpi"
    rolling = sheets["Rolling"]
    assert rolling["status"] == "unsupported" and "table calculation" in rolling["status_reason"]


def test_filters_and_quick_filter_flag() -> None:
    trend = next(s for s in plan()["worksheets"] if s["name"] == "Trend")
    by = {f["field"]: f for f in trend["filters"]}
    assert by["Country MMS"]["values"] == ["MMS"] and by["Country MMS"]["interactive_in_tableau"]
    assert (by["date"]["min"], by["date"]["max"]) == ("2025-07-01", "2026-03-31")


def test_dashboard_grid_layout() -> None:
    d = plan()["dashboards"][0]
    assert d["layout"]["Total"] == {"x": 0, "y": 0, "w": 2, "h": 4}
    assert (
        d["layout"]["Trend"]["x"] == 2
        and d["layout"]["Trend"]["x"] + d["layout"]["Trend"]["w"] == 12
    )
    assert d["layout"]["Heatmap"]["y"] >= 4
    assert d["sheets"][0]["name"] == "Total" and d["sheets"][0]["x_pct"] == 0.8


def test_twbx_and_cleanup_and_bad_input(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("wb.twb", FIXTURE.read_bytes())
        z.writestr("Data/Extracts/x.hyper", b"secret-extract")
    assert plan_from_download(buf.getvalue())["worksheets"]
    with pytest.raises(ParseError):
        parse_workbook(b"<!DOCTYPE x [<!ENTITY a 'b'>]><workbook/>")
    with pytest.raises(ParseError) as e:
        plan_from_download(b"<workbook><oops password='p4ss'>")
    assert "p4ss" not in str(e.value)
