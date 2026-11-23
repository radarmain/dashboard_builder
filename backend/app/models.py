"""Pydantic models: API payloads, stored entities, and the view spec (docs/VIEW_SPEC.md)."""

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_serializer,
    model_validator,
)

from app.slugs import client_slug_error

Slug = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", min_length=1, max_length=63),
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
# Project/view slugs when RENAMING (creation keeps the looser 63 for existing data).
ChildSlug = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", min_length=1, max_length=40),
]


# ---------- entities ----------
class ClientCreate(BaseModel):
    name: Name
    slug: Slug  # stricter subdomain rules below

    @field_validator("slug")
    @classmethod
    def _subdomain_rules(cls, v: str) -> str:
        err = client_slug_error(v)
        if err:
            raise ValueError(err)
        return v


class Client(ClientCreate):
    created_at: str
    created_by: str


class ProjectCreate(BaseModel):
    name: Name
    slug: Slug


class Project(ProjectCreate):
    client_slug: str
    status: str = "active"
    created_at: str


Description = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


class ViewCreate(BaseModel):
    name: Name
    slug: Slug
    description: Description | None = None


class ViewPatch(BaseModel):
    """Metadata only (not in the revision spec); applies immediately. A new slug moves the view
    (renames.py) and keeps the old link working through an alias."""

    name: Name | None = None
    description: Description | None = None
    slug: ChildSlug | None = None


class ProjectPatch(BaseModel):
    name: Name | None = None
    slug: ChildSlug | None = None


class View(ViewCreate):
    client_slug: str
    project_slug: str
    draft_revision: int | None = None
    published_revision: int | None = None
    has_unpublished_changes: bool = False
    updated_at: str


class AccessGrant(BaseModel):
    user_sub: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    client_slug: Slug


# ---------- view spec ----------
class ChartDef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["bar", "line", "area", "pie", "scatter"]
    x: str
    y: list[str]
    series: str | None = None
    stacked: bool = False
    format: dict[str, str] = Field(default_factory=dict)
    outline: bool = False  # area: draw a stroke on top of the fill
    labels: bool = False  # show value labels on points/bars


class PivotDef(BaseModel):
    """Table pivot: one row per `row` value, one column per `column` value, cells = `value`."""

    model_config = ConfigDict(extra="forbid")
    row: str
    column: str
    value: str


class HeatmapDef(BaseModel):
    """Color table cells by value. diverging = red (low) to green (high); sequential = light to dark."""

    model_config = ConfigDict(extra="forbid")
    palette: Literal["diverging", "sequential"] = "sequential"
    min: float = 0.0
    max: float = 1.0


class ImportMeta(BaseModel):
    """Provenance of an element created by the Tableau import (a scaffold: analysts verify it)."""

    model_config = ConfigDict(extra="forbid")
    source: Literal["tableau"] = "tableau"
    sheet_name: str = ""
    status: Literal["unverified", "needs_review", "verified", "differs"] = "unverified"
    notes: list[str] = Field(default_factory=list)
    dry_run_bytes: int | None = None
    source_is_view: bool = False
    base_tables: list[str] = Field(default_factory=list)


class Element(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)
    id: str
    type: Literal["chart", "table", "kpi", "text"]
    title: str = ""
    chart: ChartDef | None = None
    pivot: PivotDef | None = None  # table only
    heatmap: HeatmapDef | None = None  # table only (needs pivot)
    sql: str = ""
    format: dict[str, str] = Field(
        default_factory=dict
    )  # column -> currency|percent|number (table/kpi)
    cache_ttl_seconds: int = Field(default=3600, ge=0, le=7 * 86400)
    import_meta: ImportMeta | None = Field(default=None, alias="import")  # Tableau import only
    review_status: Literal["reviewed"] | None = None  # analyst sign-off for imported elements

    @model_serializer(mode="wrap")
    def _drop_unset_import(self, handler: Any) -> dict[str, Any]:
        d = handler(self)
        for k in ("import", "review_status"):
            if d.get(k) is None:
                d.pop(k, None)
        return d

    @model_validator(mode="after")
    def _check(self) -> "Element":
        if self.type == "chart" and self.chart is None:
            raise ValueError("chart element requires 'chart'")
        if self.type in ("chart", "table", "kpi") and not self.sql.strip():
            raise ValueError("element requires 'sql'")
        return self


class LayoutItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    element_id: str
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=12)  # 12-column grid
    h: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def _fits(self) -> "LayoutItem":
        if self.x + self.w > 12:
            raise ValueError("layout item exceeds the 12-column grid")
        return self


class ViewMeta(BaseModel):
    name: str
    slug: str


class ViewSpec(BaseModel):
    """No credentials and no data in a spec. extra=forbid keeps stray payloads out."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    view: ViewMeta
    revision: int = 0  # server-assigned on save
    updated_at: str = ""  # server-assigned
    updated_by: str = ""  # server-assigned
    layout: list[LayoutItem] = Field(default_factory=list)
    elements: list[Element] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> "ViewSpec":
        ids = [e.id for e in self.elements]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate element ids")
        for item in self.layout:
            if item.element_id not in ids:
                raise ValueError(f"layout references unknown element {item.element_id}")
        return self


# ---------- project settings ----------
class ProjectSettingsIn(BaseModel):
    bq_project: str
    tables: list[str]  # "project.dataset.table"
    max_bytes_billed: int | None = Field(default=None, gt=0)


class TableauLink(BaseModel):
    workbook_id: Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F-]{8,64}$")]
    workbook_name: Name


class ProjectConfig(BaseModel):
    bq_project: str | None = None
    tables: list[str] = Field(default_factory=list)
    max_bytes_billed: int | None = None
    tableau_link: TableauLink | None = None


class QueryIn(BaseModel):
    sql: str
    refresh: bool = False
    gate: bool = False  # imported elements: dry-run first; views / large scans need `confirm`
    confirm: bool = False


class PublishIn(BaseModel):
    confirm_unreviewed: bool = False  # explicit OK to publish unverified imported elements
