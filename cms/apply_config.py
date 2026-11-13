"""Aplica cms/config.json ao Directus, alterando só o que difere. Idempotente.

Uso: uv run python -m cms.apply_config [--check]

Ambiente:
  DIRECTUS_URL     padrão http://localhost:8055
  DIRECTUS_TOKEN   token estático de admin; sem ele, faz login com ADMIN_EMAIL/ADMIN_PASSWORD de cms/.env
  DIRECTUS_DB_URL  conexão do usuário `directus`; sem ela, usa DB_PASSWORD de cms/.env no banco local
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import psycopg

CMS_DIR = Path(__file__).parent
CONFIG_PATH = CMS_DIR / "config.json"
DEV_ENV_PATH = CMS_DIR / ".env"

type Json = dict[str, Any]
type Key = tuple[str, str]


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Operation:
    description: str
    method: Literal["POST", "PATCH", "DELETE", "SQL", "FLOW"]
    path: str = ""
    body: Json = field(default_factory=dict[str, Any])


@dataclass(frozen=True)
class DirectusState:
    settings: Json
    collections: dict[str, Json | None]
    fields: dict[Key, Json | None]
    relations: dict[Key, Json | None]
    policies: dict[str, Json] = field(default_factory=dict[str, Json])
    permissions: list[Json] = field(default_factory=list[Json])
    roles: dict[str, Json] = field(default_factory=dict[str, Json])
    flows: dict[str, Json] = field(default_factory=dict[str, Json])


MANAGED_FLOW_PREFIX = "Gerenciado por cms/config.json"


def load_config(path: Path = CONFIG_PATH) -> Json:
    config = cast(Json, json.loads(path.read_text()))
    for name, flow in cast(dict[str, Json], config["flows"]).items():
        options = cast(Json, flow["options"])
        # Num Flow de filtro, o valor devolvido substitui o que está sendo salvo. Qualquer retorno diferente do
        # payload original apaga o que o editor digitou (verificado no Directus 12.6.1).
        if options.get("type") == "filter" and options.get("return") != "$trigger.payload":
            raise ConfigError(f'flow \'{name}\': flows de filtro precisam de "return": "$trigger.payload"')
        operations = cast(list[Json], flow["operations"])
        for index, operation in enumerate(operations):
            later_keys = {later["key"] for later in operations[index + 1 :]}
            for branch in ("resolve", "reject"):
                target = operation.get(branch)
                if target is not None and target not in later_keys:
                    origin = f"{operation['key']}.{branch}"
                    raise ConfigError(f"flow '{name}': '{origin}' precisa apontar para uma operação seguinte")
            operation_options = cast(Json, operation["options"])
            if "code_file" in operation_options:
                operation_options["code"] = (path.parent / operation_options.pop("code_file")).read_text()
    return config


def flow_description(spec: Json) -> str:
    fingerprint = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:12]
    return f"{MANAGED_FLOW_PREFIX} ({fingerprint}). Não edite pelo painel."


def differs(current: object, desired: object) -> bool:
    """Compara só as chaves declaradas: o Directus guarda metadados extras que não gerenciamos."""
    if isinstance(desired, Mapping):
        if not isinstance(current, Mapping):
            return True
        current_map = cast(Mapping[str, object], current)
        desired_map = cast(Mapping[str, object], desired)
        return any(differs(current_map.get(key), value) for key, value in desired_map.items())
    return current != desired


def plan(config: Json, state: DirectusState) -> list[Operation]:
    operations: list[Operation] = []

    if differs(state.settings, config["settings"]):
        operations.append(Operation("configurações do projeto", "PATCH", "/settings", config["settings"]))

    for name, spec in cast(dict[str, Json], config["collections"]).items():
        if name not in state.collections:
            raise ConfigError(f"coleção '{name}' não existe no banco; as migrations foram aplicadas?")
        if differs(state.collections[name], spec["meta"]):
            operations.append(Operation(f"coleção {name}", "PATCH", f"/collections/{name}", {"meta": spec["meta"]}))

    for collection, fields in _fields_with_hidden_defaults(config, state).items():
        for name, spec in fields.items():
            key = (collection, name)
            if key not in state.fields:
                if spec.get("type") != "alias":
                    raise ConfigError(f"campo '{collection}.{name}' não existe no banco; migrations aplicadas?")
                body = {"field": name, "type": "alias", "meta": spec["meta"]}
                operations.append(Operation(f"campo {collection}.{name} (novo)", "POST", f"/fields/{collection}", body))
            elif differs(state.fields[key], spec["meta"]):
                path = f"/fields/{collection}/{name}"
                operations.append(Operation(f"campo {collection}.{name}", "PATCH", path, {"meta": spec["meta"]}))

    for relation in cast(list[Json], config["relations"]):
        key = (relation["collection"], relation["field"])
        if key not in state.relations:
            raise ConfigError(f"relação {key[0]}.{key[1]} não existe; a FK foi criada na migration?")
        if differs(state.relations[key], relation["meta"]):
            path = f"/relations/{key[0]}/{key[1]}"
            operations.append(Operation(f"relação {key[0]}.{key[1]}", "PATCH", path, {"meta": relation["meta"]}))

    for relation in cast(list[Json], config["file_relations"]):
        key = (relation["collection"], relation["field"])
        if key not in state.relations:
            operations.append(Operation(f"relação de arquivo {key[0]}.{key[1]}", "SQL", body=dict(relation)))

    return operations + _plan_access(config, state) + _plan_flows(config, state)


def _plan_access(config: Json, state: DirectusState) -> list[Operation]:
    operations: list[Operation] = []
    for name, spec in cast(dict[str, Json], config["policies"]).items():
        current = state.policies.get(name)
        if current is None:
            operations.append(Operation(f"política {name} (nova)", "POST", "/policies", {"name": name, **spec["meta"]}))
            continue
        if differs(current, spec["meta"]):
            operations.append(Operation(f"política {name}", "PATCH", f"/policies/{current['id']}", spec["meta"]))
        operations += _plan_permissions(name, current["id"], spec["permissions"], state.permissions)

    for name, spec in cast(dict[str, Json], config["roles"]).items():
        wanted_names = cast(list[str], spec["policies"])
        if any(policy not in state.policies for policy in wanted_names):
            continue  # a política é criada nesta execução; o papel entra na passada seguinte
        wanted = [cast(str, state.policies[policy]["id"]) for policy in wanted_names]
        current = state.roles.get(name)
        if current is None:
            body = {"name": name, **spec["meta"], "policies": [{"policy": policy_id} for policy_id in wanted]}
            operations.append(Operation(f"papel {name} (novo)", "POST", "/roles", body))
            continue
        if differs(current, spec["meta"]):
            operations.append(Operation(f"papel {name}", "PATCH", f"/roles/{current['id']}", spec["meta"]))
        attached = {access["policy"]: access["id"] for access in cast(list[Json], current.get("policies") or [])}
        to_add = [policy_id for policy_id in wanted if policy_id not in attached]
        to_remove = [access_id for policy_id, access_id in attached.items() if policy_id not in wanted]
        if to_add or to_remove:
            body = {"policies": {"create": [{"policy": p} for p in to_add], "update": [], "delete": to_remove}}
            operations.append(Operation(f"papel {name}: políticas", "PATCH", f"/roles/{current['id']}", body))
    return operations


def _plan_permissions(
    policy_name: str, policy_id: str, desired: dict[str, list[str]], permissions: list[Json]
) -> list[Operation]:
    wanted = {(collection, action) for collection, actions in desired.items() for action in actions}
    current = {(item["collection"], item["action"]): item for item in permissions if item["policy"] == policy_id}
    operations: list[Operation] = []
    for collection, action in sorted(wanted - current.keys()):
        body = {"policy": policy_id, "collection": collection, "action": action, "fields": ["*"]}
        operations.append(Operation(f"permissão {policy_name}: {action} em {collection}", "POST", "/permissions", body))
    for (collection, action), item in sorted(current.items()):
        description = f"permissão {policy_name}: {action} em {collection}"
        if (collection, action) not in wanted:
            operations.append(Operation(f"{description} (remover)", "DELETE", f"/permissions/{item['id']}"))
        elif item.get("fields") != ["*"] or item.get("permissions") not in (None, {}):
            body = {"fields": ["*"], "permissions": None}
            operations.append(Operation(description, "PATCH", f"/permissions/{item['id']}", body))
    return operations


def _plan_flows(config: Json, state: DirectusState) -> list[Operation]:
    """Flows não são editados no lugar: quando o conteúdo muda, o Flow é apagado e recriado."""
    desired = cast(dict[str, Json], config["flows"])
    operations: list[Operation] = []
    for name, spec in desired.items():
        description = flow_description(spec)
        current = state.flows.get(name)
        if current is not None and current.get("description") == description and current.get("status") == "active":
            continue
        if current is not None:
            operations.append(Operation(f"flow {name} (substituir)", "DELETE", f"/flows/{current['id']}"))
        operations.append(Operation(f"flow {name}", "FLOW", body={"name": name, **spec, "description": description}))
    for name, current in state.flows.items():
        if name not in desired and str(current.get("description") or "").startswith(MANAGED_FLOW_PREFIX):
            operations.append(Operation(f"flow {name} (remover)", "DELETE", f"/flows/{current['id']}"))
    return operations


def _fields_with_hidden_defaults(config: Json, state: DirectusState) -> dict[str, dict[str, Json]]:
    hidden_meta = {"hidden": True, "readonly": True}
    fields = cast(dict[str, dict[str, Json]], config["fields"])
    hidden_names = cast(list[str], config["hidden_fields"])
    return {
        collection: {
            **{name: {"meta": hidden_meta} for name in hidden_names if (collection, name) in state.fields},
            **specs,
        }
        for collection, specs in fields.items()
    }


class DirectusClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    @classmethod
    def connect(cls, base_url: str, token: str | None, dev_env: dict[str, str]) -> "DirectusClient":
        if token:
            return cls(base_url, token)
        credentials = {"email": dev_env.get("ADMIN_EMAIL"), "password": dev_env.get("ADMIN_PASSWORD")}
        if not all(credentials.values()):
            raise ConfigError("defina DIRECTUS_TOKEN ou ADMIN_EMAIL/ADMIN_PASSWORD em cms/.env")
        login = cls(base_url, "").request("POST", "/auth/login", credentials)
        return cls(base_url, login["data"]["access_token"])

    def request(self, method: str, path: str, body: Json | None = None) -> Json:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{self._base_url}{path}", data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if self._token:
            request.add_header("Authorization", f"Bearer {self._token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise ConfigError(f"{method} {path} → HTTP {error.code}: {detail}") from error
        except urllib.error.URLError as error:
            raise ConfigError(f"Directus inacessível em {self._base_url}: {error.reason}") from error
        return cast(Json, json.loads(raw)) if raw else {}

    def read_state(self) -> DirectusState:
        collections = cast(list[Json], self.request("GET", "/collections")["data"])
        fields = cast(list[Json], self.request("GET", "/fields")["data"])
        relations = cast(list[Json], self.request("GET", "/relations")["data"])
        policies = self._list("/policies", "id,name,icon,description,app_access,admin_access")
        roles = self._list("/roles", "id,name,icon,description,policies.id,policies.policy")
        flows = self._list("/flows", "id,name,description,status")
        return DirectusState(
            settings=cast(Json, self.request("GET", "/settings")["data"]),
            collections={item["collection"]: item.get("meta") for item in collections},
            fields={(item["collection"], item["field"]): item.get("meta") for item in fields},
            relations={(item["collection"], item["field"]): item.get("meta") for item in relations},
            policies={item["name"]: item for item in policies},
            permissions=self._list("/permissions", "id,policy,collection,action,fields,permissions"),
            roles={item["name"]: item for item in roles},
            flows={item["name"]: item for item in flows},
        )

    def _list(self, path: str, fields: str) -> list[Json]:
        return cast(list[Json], self.request("GET", f"{path}?limit=-1&fields={fields}")["data"])

    def create_flow(self, spec: Json) -> None:
        # Nasce inativo e sem a impressão digital: se a criação falhar no meio, a próxima execução o substitui.
        flow_body = {key: spec[key] for key in ("name", "trigger", "accountability", "options")}
        flow = self.request("POST", "/flows", {**flow_body, "status": "inactive", "description": "Em criação"})
        flow_id = flow["data"]["id"]
        # Criadas de trás para frente: resolve e reject só apontam para operações seguintes (validado no load).
        # Sem destino explícito, resolve segue para a próxima da lista.
        operations = cast(list[Json], spec["operations"])
        ids: dict[str, str] = {}
        for index in reversed(range(len(operations))):
            operation = operations[index]
            next_key = operations[index + 1]["key"] if index + 1 < len(operations) else None
            resolve_key = operation.get("resolve", next_key)
            reject_key = operation.get("reject")
            body = {
                "flow": flow_id,
                **{key: operation[key] for key in ("key", "name", "type", "options")},
                "position_x": 19 + 18 * index,
                "position_y": 1,
                "resolve": ids[resolve_key] if resolve_key else None,
                "reject": ids[reject_key] if reject_key else None,
            }
            ids[operation["key"]] = self.request("POST", "/operations", body)["data"]["id"]
        activation = {"operation": ids[operations[0]["key"]], "status": "active", "description": spec["description"]}
        self.request("PATCH", f"/flows/{flow_id}", activation)


def create_file_relation(database_url: str, collection: str, field_name: str) -> None:
    # Pela API, o Directus sempre tenta criar uma FK para directus_files, que o banco recusa (migration 0002) e
    # que o plan.md proíbe: o domínio não referencia tabelas do CMS. A relação existe só como metadado.
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "INSERT INTO directus.directus_relations (many_collection, many_field, one_collection)"
            " VALUES (%s, %s, 'directus_files')",
            (collection, field_name),
        )


def read_dev_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    pairs = (line.split("=", 1) for line in path.read_text().splitlines() if "=" in line and not line.startswith("#"))
    return {key.strip(): value.strip() for key, value in pairs}


MAX_PASSES = 3


def execute(client: DirectusClient, operation: Operation, dev_env: dict[str, str]) -> None:
    match operation.method:
        case "SQL":
            database_url = os.environ.get("DIRECTUS_DB_URL") or (
                f"postgresql://directus:{dev_env.get('DB_PASSWORD', '')}@localhost:5432/ortelius"
            )
            create_file_relation(database_url, operation.body["collection"], operation.body["field"])
        case "FLOW":
            client.create_flow(operation.body)
        case "DELETE":
            client.request("DELETE", operation.path)
        case _:
            client.request(operation.method, operation.path, operation.body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aplica cms/config.json ao Directus.")
    parser.add_argument("--check", action="store_true", help="mostra o que mudaria, sem aplicar")
    args = parser.parse_args(argv)

    dev_env = read_dev_env(DEV_ENV_PATH)
    try:
        config = load_config()
        client = DirectusClient.connect(
            os.environ.get("DIRECTUS_URL", "http://localhost:8055"), os.environ.get("DIRECTUS_TOKEN"), dev_env
        )
        if args.check:
            operations = plan(config, client.read_state())
            for operation in operations:
                print(f"[check] {operation.method:6} {operation.description}")
            print(f"{len(operations)} alteração(ões) pendente(s).", end="")
            print(" Itens que dependem de outros novos (ex.: papel de uma política nova) aparecem após aplicar.")
            return 0

        # Algumas operações dependem de identificadores criados na passada anterior (papel → política).
        applied = 0
        for _ in range(MAX_PASSES):
            operations = plan(config, client.read_state())
            if not operations:
                break
            for operation in operations:
                print(f"{operation.method:6} {operation.description}")
                execute(client, operation, dev_env)
            applied += len(operations)
            client.request("POST", "/utils/cache/clear")
        else:
            raise ConfigError(f"configuração não convergiu após {MAX_PASSES} passadas")
    except ConfigError as error:
        print(f"erro: {error}", file=sys.stderr)
        return 1
    if applied == 0:
        print("Directus já está de acordo com cms/config.json.")
    else:
        print(f"{applied} alteração(ões) aplicada(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
