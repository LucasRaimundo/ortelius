import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.domain.entities import EntityType
from pipeline.wikidata.mapping import Alias, UnmappableItemError, facts_from_response, slugify

FIXTURES = Path(__file__).parent / "fixtures"


def load(qid: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{qid}.json").read_text())


def item_of(response: dict[str, Any]) -> dict[str, Any]:
    return next(iter(response["entities"].values()))


def statement(value: object, *, rank: str = "normal", datatype: str = "wikibase-item") -> dict[str, Any]:
    datavalue_type = "globecoordinate" if datatype == "globe-coordinate" else "wikibase-entityid"
    return {
        "mainsnak": {"snaktype": "value", "datatype": datatype, "datavalue": {"value": value, "type": datavalue_type}},
        "type": "statement",
        "rank": rank,
    }


def coordinate(longitude: float, latitude: float) -> dict[str, Any]:
    value = {"latitude": latitude, "longitude": longitude, "globe": "http://www.wikidata.org/entity/Q2"}
    return statement(value, datatype="globe-coordinate")


def test_maps_a_person_with_relations_to_other_items() -> None:
    facts = facts_from_response(load("Q1405"))

    assert (facts.qid, facts.revision, facts.name, facts.entity_type) == (
        "Q1405",
        2568559129,
        "Augusto",
        EntityType.PERSON,
    )
    assert facts.summary == "primeiro imperador romano (27 a.C.\u201314 d.C.)"
    assert facts.location is None
    assert [(r.relation_type.key, r.target_qid) for r in facts.relations] == [
        ("nasceu-em", "Q220"),
        ("morreu-em", "Q72288"),
        ("conjuge-de", "Q237618"),
        ("conjuge-de", "Q233444"),
        ("conjuge-de", "Q469701"),
    ]


def test_maps_an_event_with_location_as_longitude_latitude() -> None:
    facts = facts_from_response(load("Q131386"))

    assert facts.entity_type == EntityType.EVENT
    assert facts.location == (4.50027778, 47.53722222)
    assert facts.points_to_check == ()
    assert [(r.relation_type.key, r.target_qid) for r in facts.relations] == [
        ("ocorreu-em", "Q835966"),
        ("parte-de", "Q202161"),
    ]


def test_aliases_are_labels_in_other_languages_plus_portuguese_aliases_without_repeating_the_name() -> None:
    facts = facts_from_response(load("Q60220"))

    assert facts.aliases[:4] == (
        Alias("Arma virumque cano", "pt"),
        Alias("Aeneis", "pt"),
        Alias("Aeneid", "en"),
        Alias("Αἰνειάς", "grc"),
    )
    assert "Eneida" not in {alias.name for alias in facts.aliases}
    assert "Eneid" not in {alias.name for alias in facts.aliases}


def test_refuses_unknown_type_naming_what_was_found() -> None:
    response = load("Q60220")
    item_of(response)["claims"]["P31"] = [statement({"id": "Q999"})]

    with pytest.raises(UnmappableItemError, match=r"Q60220: tipo desconhecido \(P31: Q999\).*--tipo"):
        facts_from_response(response)


def test_forced_type_skips_inference() -> None:
    response = load("Q60220")
    item_of(response)["claims"]["P31"] = [statement({"id": "Q999"})]

    assert facts_from_response(response, forced_type=EntityType.CONCEPT).entity_type == EntityType.CONCEPT


def test_prefers_preferred_rank_and_ignores_deprecated_values() -> None:
    response = load("Q60220")
    item_of(response)["claims"]["P31"] = [
        statement({"id": "Q5"}, rank="deprecated"),
        statement({"id": "Q7725634"}),
        statement({"id": "Q515"}, rank="preferred"),
    ]

    facts = facts_from_response(response)

    assert facts.entity_type == EntityType.PLACE
    assert facts.points_to_check == ("Tipo escolhido: place. O item também é work no Wikidata.",)


def test_falls_back_to_english_name_and_asks_to_check_it() -> None:
    response = load("Q60220")
    del item_of(response)["labels"]["pt"]

    facts = facts_from_response(response)

    assert facts.name == "Aeneid"
    assert facts.points_to_check == ("Nome em inglês (Aeneid): o item não tem rótulo em português.",)


def test_refuses_item_without_portuguese_or_english_name() -> None:
    response = load("Q60220")
    labels = item_of(response)["labels"]
    del labels["pt"], labels["en"]

    with pytest.raises(UnmappableItemError, match="Q60220 não tem nome"):
        facts_from_response(response)


def test_uses_first_of_several_coordinates_and_asks_to_check_it() -> None:
    response = load("Q131386")
    item_of(response)["claims"]["P625"] = [coordinate(4.5, 47.5), coordinate(2.3, 48.8)]

    facts = facts_from_response(response)

    assert facts.location == (4.5, 47.5)
    assert facts.points_to_check == ("O Wikidata tem 2 coordenadas; foi usada a primeira. Confira o ponto no mapa.",)


def test_ignores_coordinates_on_other_celestial_bodies() -> None:
    response = load("Q131386")
    lunar = coordinate(1.0, 1.0)
    lunar["mainsnak"]["datavalue"]["value"]["globe"] = "http://www.wikidata.org/entity/Q405"
    item_of(response)["claims"]["P625"] = [lunar]

    assert facts_from_response(response).location is None


SHAPE_CHANGES: list[Callable[[dict[str, Any]], object]] = [
    lambda item: item.pop("lastrevid"),
    lambda item: item["claims"].__setitem__("P31", [{"rank": "normal"}]),
    lambda item: item.__setitem__("labels", None),
]


@pytest.mark.parametrize("mutate", SHAPE_CHANGES)
def test_unexpected_shape_becomes_a_readable_refusal(mutate: Callable[[dict[str, Any]], object]) -> None:
    response = copy.deepcopy(load("Q60220"))
    mutate(item_of(response))

    with pytest.raises(UnmappableItemError, match="formato inesperado"):
        facts_from_response(response)


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Batalha de Alésia", "batalha-de-alesia"),
        ("Júlio César", "julio-cesar"),
        ("  Ótávio — o Jovem! ", "otavio-o-jovem"),
    ],
)
def test_slug_drops_accents_and_punctuation(name: str, slug: str) -> None:
    assert slugify(name) == slug
