import json
from typing import cast

import pytest

from app.infrastructure.database.models import Base
from cms.apply_config import CONFIG_PATH, ConfigError, DirectusState, Json, plan

CONFIG = cast(Json, json.loads(CONFIG_PATH.read_text()))
CONFIGURED_FIELDS = cast(dict[str, dict[str, Json]], CONFIG["fields"])
HIDDEN_FIELDS = frozenset(cast(list[str], CONFIG["hidden_fields"]))
HIDDEN_META: Json = {"hidden": True, "readonly": True}


def _with_directus_extras(meta: Json) -> Json:
    """O Directus devolve chaves que o config não gerencia; o plano precisa ignorá-las."""
    return {**meta, "id": 1, "group": None, "collapse": "open"}


def applied_state() -> DirectusState:
    fields: dict[tuple[str, str], Json | None] = {}
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            spec = CONFIGURED_FIELDS.get(table.name, {}).get(column.name)
            meta = spec["meta"] if spec else HIDDEN_META
            fields[(table.name, column.name)] = _with_directus_extras(meta)
    for collection, specs in CONFIGURED_FIELDS.items():
        for name, spec in specs.items():
            if spec.get("type") == "alias":
                fields[(collection, name)] = _with_directus_extras(spec["meta"])

    relations: dict[tuple[str, str], Json | None] = {
        (relation["collection"], relation["field"]): _with_directus_extras(relation["meta"])
        for relation in cast(list[Json], CONFIG["relations"])
    }
    for relation in cast(list[Json], CONFIG["file_relations"]):
        relations[(relation["collection"], relation["field"])] = {"one_field": None}

    return DirectusState(
        settings={**CONFIG["settings"], "project_color": "#6644FF"},
        collections={
            name: _with_directus_extras(spec["meta"])
            for name, spec in cast(dict[str, Json], CONFIG["collections"]).items()
        },
        fields=fields,
        relations=relations,
    )


def test_plan_is_empty_when_directus_already_matches_config() -> None:
    assert plan(CONFIG, applied_state()) == []


def test_plan_patches_only_the_field_that_drifted() -> None:
    state = applied_state()
    drifted = dict(cast(Json, state.fields[("entities", "status")]))
    drifted["interface"] = "input"
    state.fields[("entities", "status")] = drifted

    operations = plan(CONFIG, state)

    assert [(op.method, op.path) for op in operations] == [("PATCH", "/fields/entities/status")]


def test_plan_configures_collection_that_directus_only_introspected() -> None:
    state = applied_state()
    state.collections["sources"] = None

    assert [(op.method, op.path) for op in plan(CONFIG, state)] == [("PATCH", "/collections/sources")]


def test_plan_creates_missing_alias_field() -> None:
    state = applied_state()
    del state.fields[("entities", "citations")]

    operations = plan(CONFIG, state)

    assert [(op.method, op.path, op.body["type"]) for op in operations] == [("POST", "/fields/entities", "alias")]


def test_plan_refuses_to_configure_a_column_missing_from_the_database() -> None:
    state = applied_state()
    del state.fields[("entities", "summary")]

    with pytest.raises(ConfigError, match=r"entities\.summary"):
        plan(CONFIG, state)


def test_plan_registers_file_relation_only_when_missing() -> None:
    state = applied_state()
    del state.relations[("entities", "cover_media")]

    operations = plan(CONFIG, state)

    assert [(op.method, op.body) for op in operations] == [("SQL", {"collection": "entities", "field": "cover_media"})]


def test_every_domain_column_is_configured_for_the_panel() -> None:
    """Uma coluna nova sem configuração aparece crua no painel; o spike mostrou que isso o torna inutilizável."""
    unconfigured = sorted(
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if column.name not in HIDDEN_FIELDS and column.name not in CONFIGURED_FIELDS.get(table.name, {})
    )
    assert unconfigured == []


def test_every_configured_field_exists_in_the_models() -> None:
    columns = {(table.name, column.name) for table in Base.metadata.sorted_tables for column in table.columns}
    unknown = sorted(
        f"{collection}.{name}"
        for collection, specs in CONFIGURED_FIELDS.items()
        for name, spec in specs.items()
        if spec.get("type") != "alias" and (collection, name) not in columns
    )
    assert unknown == []
