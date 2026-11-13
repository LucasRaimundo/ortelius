"""Aplica cms/config.json ao Directus, alterando só o que difere. Idempotente.

Uso: uv run python -m cms.apply_config [--check]

Ambiente:
  DIRECTUS_URL     padrão http://localhost:8055
  DIRECTUS_TOKEN   token estático de admin; sem ele, faz login com ADMIN_EMAIL/ADMIN_PASSWORD de cms/.env
  DIRECTUS_DB_URL  conexão do usuário `directus`; sem ela, usa DB_PASSWORD de cms/.env no banco local
"""

import argparse
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
    method: Literal["POST", "PATCH", "SQL"]
    path: str = ""
    body: Json = field(default_factory=dict[str, Any])


@dataclass(frozen=True)
class DirectusState:
    settings: Json
    collections: dict[str, Json | None]
    fields: dict[Key, Json | None]
    relations: dict[Key, Json | None]


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
        return DirectusState(
            settings=cast(Json, self.request("GET", "/settings")["data"]),
            collections={item["collection"]: item.get("meta") for item in collections},
            fields={(item["collection"], item["field"]): item.get("meta") for item in fields},
            relations={(item["collection"], item["field"]): item.get("meta") for item in relations},
        )


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aplica cms/config.json ao Directus.")
    parser.add_argument("--check", action="store_true", help="mostra o que mudaria, sem aplicar")
    args = parser.parse_args(argv)

    dev_env = read_dev_env(DEV_ENV_PATH)
    config = cast(Json, json.loads(CONFIG_PATH.read_text()))
    try:
        client = DirectusClient.connect(
            os.environ.get("DIRECTUS_URL", "http://localhost:8055"), os.environ.get("DIRECTUS_TOKEN"), dev_env
        )
        operations = plan(config, client.read_state())
        if not operations:
            print("Directus já está de acordo com cms/config.json.")
            return 0
        for operation in operations:
            print(f"{'[check] ' if args.check else ''}{operation.method:5} {operation.description}")
            if args.check:
                continue
            if operation.method == "SQL":
                database_url = os.environ.get("DIRECTUS_DB_URL") or (
                    f"postgresql://directus:{dev_env.get('DB_PASSWORD', '')}@localhost:5432/ortelius"
                )
                create_file_relation(database_url, operation.body["collection"], operation.body["field"])
            else:
                client.request(operation.method, operation.path, operation.body)
        if not args.check:
            client.request("POST", "/utils/cache/clear")
    except ConfigError as error:
        print(f"erro: {error}", file=sys.stderr)
        return 1
    print(f"{len(operations)} alteração(ões) {'pendente(s)' if args.check else 'aplicada(s)'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
