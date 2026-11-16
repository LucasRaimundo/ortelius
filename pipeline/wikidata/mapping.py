"""Converte um item do Wikidata (resposta de `wbgetentities`) nos fatos de um rascunho do Atlas."""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from app.domain.entities import EntityType

ALIAS_LANGUAGES = ("pt", "la", "en", "grc", "el", "it", "es", "fr", "de")

TYPE_BY_INSTANCE: Mapping[str, EntityType] = {
    "Q5": EntityType.PERSON,
    "Q515": EntityType.PLACE,
    "Q1549591": EntityType.PLACE,
    "Q1637706": EntityType.PLACE,
    "Q5119": EntityType.PLACE,
    "Q3957": EntityType.PLACE,
    "Q532": EntityType.PLACE,
    "Q839954": EntityType.PLACE,
    "Q15661340": EntityType.PLACE,
    "Q148837": EntityType.PLACE,
    "Q133442": EntityType.PLACE,
    "Q48349": EntityType.STATE,
    "Q3024240": EntityType.STATE,
    "Q6256": EntityType.STATE,
    "Q7275": EntityType.STATE,
    "Q417175": EntityType.STATE,
    "Q178561": EntityType.EVENT,
    "Q188055": EntityType.EVENT,
    "Q198": EntityType.EVENT,
    "Q1261499": EntityType.EVENT,
    "Q831663": EntityType.EVENT,
    "Q350604": EntityType.EVENT,
    "Q8465": EntityType.EVENT,
    "Q10931": EntityType.EVENT,
    "Q7725634": EntityType.WORK,
    "Q37484": EntityType.WORK,
    "Q47461344": EntityType.WORK,
    "Q571": EntityType.WORK,
    "Q838948": EntityType.WORK,
    "Q3305213": EntityType.WORK,
    "Q860861": EntityType.WORK,
    "Q28171280": EntityType.CIVILIZATION,
    "Q8432": EntityType.CIVILIZATION,
    "Q11042": EntityType.CULTURE,
    "Q41710": EntityType.CULTURE,
    "Q151885": EntityType.CONCEPT,
}


@dataclass(frozen=True)
class RelationType:
    key: str
    label: str
    inverse_label: str | None


RELATION_BY_PROPERTY: Mapping[str, RelationType] = {
    "P36": RelationType("teve-como-capital", "teve como capital", "foi capital de"),
    "P50": RelationType("teve-como-autor", "teve como autor", "é autor de"),
    "P19": RelationType("nasceu-em", "nasceu em", "local de nascimento de"),
    "P20": RelationType("morreu-em", "morreu em", "local de morte de"),
    "P607": RelationType("participou-de", "participou de", "teve a participação de"),
    "P276": RelationType("ocorreu-em", "ocorreu em", "foi palco de"),
    "P361": RelationType("parte-de", "faz parte de", "inclui"),
    "P840": RelationType("ambientada-em", "é ambientada em", "é cenário de"),
    "P737": RelationType("influenciado-por", "foi influenciado por", "influenciou"),
    "P26": RelationType("conjuge-de", "foi cônjuge de", None),
}


class UnmappableItemError(Exception):
    """O item não vira rascunho sem decisão humana; a mensagem diz o que falta."""


@dataclass(frozen=True)
class Alias:
    name: str
    language_code: str


@dataclass(frozen=True)
class RelationFact:
    property_id: str
    target_qid: str
    relation_type: RelationType


@dataclass(frozen=True)
class ItemFacts:
    qid: str
    revision: int
    name: str
    entity_type: EntityType
    summary: str | None
    location: tuple[float, float] | None
    aliases: tuple[Alias, ...]
    relations: tuple[RelationFact, ...]
    points_to_check: tuple[str, ...]


def facts_from_response(response: Mapping[str, Any], *, forced_type: EntityType | None = None) -> ItemFacts:
    entities: object = response.get("entities")
    if not isinstance(entities, Mapping) or len(cast(Mapping[str, Any], entities)) != 1:
        raise UnmappableItemError("resposta do Wikidata sem exatamente um item")
    item = cast(Mapping[str, Any], next(iter(cast(Mapping[str, Any], entities).values())))
    try:
        return _facts_from_item(item, forced_type)
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise UnmappableItemError(f"formato inesperado no item do Wikidata: {error!r}") from error


def _facts_from_item(item: Mapping[str, Any], forced_type: EntityType | None) -> ItemFacts:
    qid: str = item["id"]
    points: list[str] = []
    name = _text(item["labels"], "pt")
    if name is None:
        name = _text(item["labels"], "en")
        if name is None:
            raise UnmappableItemError(f"{qid} não tem nome em português nem em inglês")
        points.append(f"Nome em inglês ({name}): o item não tem rótulo em português.")
    entity_type = forced_type or _entity_type(qid, item["claims"], points)
    location = _location(item["claims"], points)
    return ItemFacts(
        qid=qid,
        revision=int(item["lastrevid"]),
        name=name,
        entity_type=entity_type,
        summary=_text(item["descriptions"], "pt"),
        location=location,
        aliases=_aliases(item, name),
        relations=_relations(item["claims"]),
        points_to_check=tuple(points),
    )


def _text(values: Mapping[str, Any], language: str) -> str | None:
    value = values.get(language)
    return value["value"].strip() or None if value else None


def _statements(claims: Mapping[str, Any], property_id: str) -> list[Mapping[str, Any]]:
    """Valores com dado e rank válido, os preferidos primeiro."""
    usable = [
        statement
        for statement in claims.get(property_id, [])
        if statement["rank"] != "deprecated" and statement["mainsnak"]["snaktype"] == "value"
    ]
    return sorted(usable, key=lambda statement: statement["rank"] != "preferred")


def _item_ids(claims: Mapping[str, Any], property_id: str) -> list[str]:
    return [statement["mainsnak"]["datavalue"]["value"]["id"] for statement in _statements(claims, property_id)]


def _entity_type(qid: str, claims: Mapping[str, Any], points: list[str]) -> EntityType:
    instances = _item_ids(claims, "P31")
    types = list(dict.fromkeys(TYPE_BY_INSTANCE[i] for i in instances if i in TYPE_BY_INSTANCE))
    if not types:
        found = ", ".join(instances) or "nenhum"
        raise UnmappableItemError(f"{qid}: tipo desconhecido (P31: {found}); informe o tipo com --tipo")
    if len(types) > 1:
        points.append(f"Tipo escolhido: {types[0]}. O item também é {', '.join(types[1:])} no Wikidata.")
    return types[0]


def _location(claims: Mapping[str, Any], points: list[str]) -> tuple[float, float] | None:
    coordinates = [
        statement["mainsnak"]["datavalue"]["value"]
        for statement in _statements(claims, "P625")
        if statement["mainsnak"]["datavalue"]["value"].get("globe") == "http://www.wikidata.org/entity/Q2"
    ]
    if not coordinates:
        return None
    if len(coordinates) > 1:
        points.append(f"O Wikidata tem {len(coordinates)} coordenadas; foi usada a primeira. Confira o ponto no mapa.")
    return float(coordinates[0]["longitude"]), float(coordinates[0]["latitude"])


def _aliases(item: Mapping[str, Any], name: str) -> tuple[Alias, ...]:
    """Rótulo de cada idioma e aliases só em português.

    Os aliases dos outros idiomas trazem ruído que prejudica a busca: em Augusto (Q1405) chegam a 60, incluindo
    "César" e "Gaius Julius Caesar", que fariam a busca por Júlio César devolver Augusto.
    """
    seen = {name.casefold()}
    aliases: list[Alias] = []
    for language in ALIAS_LANGUAGES:
        label = item["labels"].get(language)
        candidates = ([label] if label else []) + (item["aliases"].get(language, []) if language == "pt" else [])
        for candidate in candidates:
            text = candidate["value"].strip()
            if text and text.casefold() not in seen:
                seen.add(text.casefold())
                aliases.append(Alias(text, language))
    return tuple(aliases)


def _relations(claims: Mapping[str, Any]) -> tuple[RelationFact, ...]:
    return tuple(
        RelationFact(property_id, target, relation_type)
        for property_id, relation_type in RELATION_BY_PROPERTY.items()
        for target in dict.fromkeys(_item_ids(claims, property_id))
    )


def slugify(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
