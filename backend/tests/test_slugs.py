import pytest
from app.models import ClientCreate
from app.slugs import RESERVED_SLUGS
from pydantic import ValidationError


@pytest.mark.parametrize("slug", ["acme", "st-georges-2", "a" * 40, "x1"])
def test_good_client_slugs(slug: str) -> None:
    assert ClientCreate(name="n", slug=slug).slug == slug


@pytest.mark.parametrize(
    "slug",
    ["-acme", "acme-", "ac--me", "Acme", "ac_me", "a" * 41, "www", "builder", "mailo", "fs3"],
)
def test_bad_client_slugs(slug: str) -> None:
    with pytest.raises(ValidationError):
        ClientCreate(name="n", slug=slug)


def test_reserved_list_complete() -> None:
    assert len(RESERVED_SLUGS) == 52
    assert {"api", "ns1", "gh-mail", "preview", "notify"} <= RESERVED_SLUGS
