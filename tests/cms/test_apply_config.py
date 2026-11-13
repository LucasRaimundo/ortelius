import json
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import CheckConstraint

from app.infrastructure.database.models import SLUG_PATTERN, Base
from cms.apply_config import (
    CONFIG_PATH,
    MANAGED_FLOW_PREFIX,
    ConfigError,
    DirectusState,
    Json,
    flow_description,
    load_config,
    plan,
)

CONFIG = load_config()
POLICIES = cast(dict[str, Json], CONFIG["policies"])
ROLES = cast(dict[str, Json], CONFIG["roles"])
FLOWS = cast(dict[str, Json], CONFIG["flows"])
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
        policies={name: {"id": f"pol-{name}", **spec["meta"]} for name, spec in POLICIES.items()},
        permissions=[
            {
                "id": f"perm-{name}-{collection}-{action}",
                "policy": f"pol-{name}",
                "collection": collection,
                "action": action,
                "fields": ["*"],
                "permissions": None,
            }
            for name, spec in POLICIES.items()
            for collection, actions in cast(dict[str, list[str]], spec["permissions"]).items()
            for action in actions
        ],
        roles={
            name: {
                "id": f"role-{name}",
                **spec["meta"],
                "policies": [{"id": f"access-{policy}", "policy": f"pol-{policy}"} for policy in spec["policies"]],
            }
            for name, spec in ROLES.items()
        },
        flows={
            name: {"id": f"flow-{name}", "description": flow_description(spec), "status": "active"}
            for name, spec in FLOWS.items()
        },
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


def test_plan_grants_missing_permission_and_revokes_permission_removed_from_config() -> None:
    state = applied_state()
    state.permissions[:] = [item for item in state.permissions if item["collection"] != "sources"]
    state.permissions.append(
        {
            "id": "perm-extra",
            "policy": "pol-Editor",
            "collection": "directus_users",
            "action": "update",
            "fields": ["*"],
            "permissions": None,
        }
    )

    operations = plan(CONFIG, state)

    assert sorted((op.method, op.path, op.body.get("collection"), op.body.get("action")) for op in operations) == [
        ("DELETE", "/permissions/perm-extra", None, None),
        ("POST", "/permissions", "sources", "create"),
        ("POST", "/permissions", "sources", "delete"),
        ("POST", "/permissions", "sources", "read"),
        ("POST", "/permissions", "sources", "update"),
    ]


def test_plan_resets_permission_that_gained_a_row_filter() -> None:
    state = applied_state()
    restricted = next(item for item in state.permissions if item["collection"] == "entities")
    restricted["permissions"] = {"status": {"_eq": "draft"}}

    assert [(op.method, op.path) for op in plan(CONFIG, state)] == [("PATCH", f"/permissions/{restricted['id']}")]


def test_plan_defers_role_until_its_policy_exists() -> None:
    state = applied_state()
    state.policies.clear()
    state.permissions.clear()
    state.roles.clear()

    assert [(op.method, op.path) for op in plan(CONFIG, state)] == [("POST", "/policies")]


def test_plan_recreates_flow_whose_definition_changed() -> None:
    state = applied_state()
    name = next(iter(FLOWS))
    state.flows[name]["description"] = f"{MANAGED_FLOW_PREFIX} (000000000000). Não edite pelo painel."

    operations = plan(CONFIG, state)

    assert [(op.method, op.path) for op in operations] == [("DELETE", f"/flows/flow-{name}"), ("FLOW", "")]


def test_plan_removes_managed_flow_dropped_from_config_but_keeps_flows_created_in_the_panel() -> None:
    state = applied_state()
    state.flows["Flow antigo"] = {"id": "flow-old", "description": f"{MANAGED_FLOW_PREFIX} (abc). Não edite."}
    state.flows["Criado no painel"] = {"id": "flow-manual", "description": "feito à mão", "status": "active"}

    assert [(op.method, op.path) for op in plan(CONFIG, state)] == [("DELETE", "/flows/flow-old")]


def _write_config_with_flow(tmp_path: Path, flow: Json) -> Path:
    config = cast(Json, json.loads(CONFIG_PATH.read_text()))
    config["flows"] = {"Flow de teste": flow}
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    return path


def test_load_config_rejects_filter_flow_that_would_overwrite_the_saved_payload(tmp_path: Path) -> None:
    flow: Json = {"trigger": "event", "options": {"type": "filter", "return": "$last"}, "operations": []}

    with pytest.raises(ConfigError, match=r"\$trigger\.payload"):
        load_config(_write_config_with_flow(tmp_path, flow))


def test_load_config_rejects_branch_pointing_backwards(tmp_path: Path) -> None:
    operations: list[Json] = [
        {"key": "first", "options": {}},
        {"key": "second", "options": {}, "reject": "first"},
    ]
    options = {"type": "filter", "return": "$trigger.payload"}
    flow: Json = {"trigger": "event", "options": options, "operations": operations}

    with pytest.raises(ConfigError, match=r"second\.reject"):
        load_config(_write_config_with_flow(tmp_path, flow))


def test_panel_slug_fields_produce_what_the_database_accepts() -> None:
    """O painel converte o texto em slug com hífen; o CHECK e a validação do campo precisam aceitar esse formato."""
    slug_fields = [
        (collection, name, spec["meta"])
        for collection, specs in CONFIGURED_FIELDS.items()
        for name, spec in specs.items()
        if spec["meta"].get("options", {}).get("slug")
    ]
    checks = {
        table.name: [str(item.sqltext) for item in table.constraints if isinstance(item, CheckConstraint)]
        for table in Base.metadata.sorted_tables
    }

    mismatched = [
        f"{collection}.{name}"
        for collection, name, meta in slug_fields
        if f"{name} ~ '{SLUG_PATTERN}'" not in checks[collection]
        or meta.get("validation") != {"_and": [{name: {"_regex": SLUG_PATTERN}}]}
    ]

    assert slug_fields
    assert mismatched == []
