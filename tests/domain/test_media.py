import pytest

from app.domain.entities import ImageLicense
from app.domain.media import LicenseInfo, license_info, rendition_for, rendition_widths


@pytest.mark.parametrize(
    ("original", "widths"),
    [(3000, [480, 960, 1600]), (1600, [480, 960, 1600]), (1000, [480, 960, 1000]), (480, [480]), (300, [300])],
)
def test_sizes_never_exceed_the_original(original: int, widths: list[int]) -> None:
    assert rendition_widths(original) == widths


@pytest.mark.parametrize(("actual", "requested"), [(300, 480), (480, 480), (1000, 1600), (1600, 1600)])
def test_each_size_maps_to_the_smallest_request_that_produces_it(actual: int, requested: int) -> None:
    assert rendition_for(actual) == requested


@pytest.mark.parametrize(
    ("license_", "version", "expected"),
    [
        (ImageLicense.PUBLIC_DOMAIN, None, LicenseInfo("Domínio público", None)),
        (ImageLicense.CC0, None, LicenseInfo("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/")),
        (ImageLicense.CC_BY, "3.0", LicenseInfo("CC BY 3.0", "https://creativecommons.org/licenses/by/3.0/")),
        (
            ImageLicense.CC_BY_SA,
            "2.5",
            LicenseInfo("CC BY-SA 2.5", "https://creativecommons.org/licenses/by-sa/2.5/"),
        ),
    ],
)
def test_license_label_and_link(license_: ImageLicense, version: str | None, expected: LicenseInfo) -> None:
    assert license_info(license_, version) == expected
