from __future__ import annotations

"""Fixed animation and base-setting through the existing characteristic owner."""

from dataclasses import replace
import re
from typing import Any, Mapping

from ..attachment_references import AttachmentReferenceKind, AttachmentReferenceSpec
from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec
from ..resolution_characteristic_model import (
    CHARACTERISTIC_OPERATION, CHOSEN_SUBTYPE_CHOICE_VERSION, PERMANENT_CHARACTERISTIC_MECHANIC,
    SUBTYPE_GRANT_INSTRUCTION_VERSION, FixedResolutionCharacteristicsSpec, FixedResolutionSubtypeGrantSpec,
)
from ..rules.source_references import SourceReferenceSpec
from .direct_target import direct_permanent_target_spec
from .fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
from .fixed_target_effect_sequences import _animation_description, _keyword_list
from .public_state_queries import fixed_characteristic_battlefield_query_subject


_BASE = re.compile(r"(?P<subject>.+?) (?:has|have) base power and toughness (?P<p>\d+)/(?P<t>\d+)(?P<tail>.*)",re.I)
_ANIMATE = re.compile(r"(?P<subject>.+?) becomes? (?:an? )?(?:(?P<p>\d+)/(?P<t>\d+) (?P<description>.+)|(?P<base_description>.+?) with base power and toughness (?P<bp>\d+)/(?P<bt>\d+))(?P<tail>.*)",re.I)
_PLAYER_SET_SCHEMA = {"zones":["player"],"categories":["player"],"player_relation":"any","count":1}
_CHOSEN_SUBTYPE = re.compile(
    r"(?P<subject>.+?) becomes (?:the|a) creature type of your choice until end of turn\.", re.I,
)
_CHOSEN_SUBTYPE_PREFIX = re.compile(
    r"Choose a creature type(?: other than (?P<excluded>[^.]+))?\. "
    r"(?P<subject>.+?) becomes that type until end of turn\.", re.I,
)


def _chosen_subtype_template(text, *, source_is_permanent, source_attachment_relation):
    normalized = text.strip().removeprefix("• ")
    prefix = _CHOSEN_SUBTYPE_PREFIX.fullmatch(normalized)
    match = prefix or _CHOSEN_SUBTYPE.fullmatch(normalized)
    if match is None:
        return None
    excluded = canonical_creature_subtype(prefix["excluded"]) if prefix and prefix["excluded"] else None
    if prefix and prefix["excluded"] and excluded is None:
        return None
    subject = match["subject"].casefold()
    schema = None
    mechanics = ("cr-611-continuous-effects",)
    if subject in {"this creature", "this permanent"} and source_is_permanent is True:
        selection = {"card": "$source.zone_object"}
    elif subject == "enchanted creature" and source_attachment_relation is AttachmentReferenceKind.ENCHANTED:
        reference = AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED, "permanent", schema_version=2)
        selection = {"card": reference.to_dict()}
    elif subject in {"target creature", "another target creature"}:
        spec = direct_permanent_target_spec(subject)
        if spec is None:
            return None
        schema = spec.to_target_schema()
        selection = {"card": "$target.0"}
        mechanics = (*mechanics, "cr-115-targets")
    elif prefix and subject in {"each creature", "each creature you control"}:
        selection = {"predicate": ObjectQuerySpec(zones=("battlefield",), types_all=("creature",),
            controller="$controller" if subject.endswith("you control") else None).to_dict()}
    else:
        return None
    effect = {"op": "choose_creature_type", "schema_version": CHOSEN_SUBTYPE_CHOICE_VERSION,
              "excluded": excluded, **selection}
    return "chosen-creature-type-setting-v1", (effect,), schema, mechanics


def fixed_attached_stat_effect_template(text: str, *, source_attachment_relation: AttachmentReferenceKind | None):
    if source_attachment_relation is not AttachmentReferenceKind.ENCHANTED:
        return None
    match = re.fullmatch(
        r"Enchanted creature gets ([+-]\d+)/([+-]\d+) until end of turn\.", text.strip(), re.I,
    )
    if match is None:
        return None
    power, toughness = map(int, match.groups())
    if not (power or toughness):
        return None
    # The last enchanted object can remain on the battlefield as a noncreature.
    reference = AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED, "permanent", schema_version=2)
    effect = {"op": "modify_stats_until_end_of_turn", "card": reference.to_dict(), "power": power, "toughness": toughness}
    return "fixed-attached-stats-until-end-of-turn-v1", (effect,), None, ("cr-611-continuous-effects",)


def _subject(text: str, *, source_name: str, source_is_permanent: bool | None,
             source_card_types: tuple[str, ...]) -> tuple[dict[str, Any], Mapping[str, Any] | None] | None:
    normalized = " ".join(text.strip().split())
    if normalized.casefold().startswith(("target ","another target ")):
        spec = direct_permanent_target_spec(normalized)
        return ({"card":"$target.0"},spec.to_target_schema()) if spec is not None else None
    source = re.fullmatch(r"this (?P<kind>artifact|creature|enchantment|land|permanent|planeswalker)",normalized,re.I)
    if source is not None or SourceReferenceSpec(source_name).matches(normalized):
        if source_is_permanent is not True:return None
        if source is not None and source['kind'].casefold() != 'permanent' and source['kind'].casefold() not in source_card_types:return None
        return {"card":"$source.zone_object"},None
    if normalized.casefold() == "creatures target player controls":
        return {"predicate":ObjectQuerySpec(zones=("battlefield",),controller="$target.0",types_all=("creature",)).to_dict()},dict(_PLAYER_SET_SCHEMA)
    parsed = fixed_characteristic_battlefield_query_subject(normalized)
    if parsed is None:return None
    relation,query,excluded = parsed
    if relation == 'source_controller':query=replace(query,controller="$controller")
    elif relation == 'source_opponents':query=replace(query,excluded_controllers=("$controller",))
    elif relation != 'any':return None
    if excluded:query=replace(query,exclude_ref="$source")
    if query.keywords_none or not fixed_resolution_characteristic_query_is_closed(query,target_schema=None):return None
    return {"predicate":query.to_dict()},None


def fixed_resolution_characteristics_effect_template(
    text: str, *, source_name: str, source_is_permanent: bool | None,
    source_card_types: tuple[str, ...],
    source_attachment_relation: AttachmentReferenceKind | None = None,
) -> tuple[str, tuple[Mapping[str, Any], ...], Mapping[str, Any] | None, tuple[str, ...]] | None:
    """Consume one complete fixed end-of-turn instruction, never an open tail."""
    chosen = _chosen_subtype_template(text, source_is_permanent=source_is_permanent,
                                     source_attachment_relation=source_attachment_relation)
    if chosen is not None:
        return chosen
    from .power_toughness_switch import power_toughness_switch_effect_template
    switch = power_toughness_switch_effect_template(text, source_name=source_name,
        source_is_permanent=source_is_permanent, source_card_types=source_card_types)
    if switch is not None:
        return switch
    normalized = text.strip()
    if any(value in normalized.casefold() for value in ('"','where',' if ',' when ','copy','all creature types')):
        return None
    prefix = re.fullmatch(r"Until end of turn, (?P<body>.+)",normalized,re.I)
    if prefix is not None:body=prefix['body']
    else:
        suffix=re.fullmatch(r"(?P<body>.+?) until end of turn(?P<rider>\. (?:It's|They're) still (?:a land|lands|a planeswalker|planeswalkers)\.)?\.?",normalized,re.I)
        if suffix is None:return None
        body=suffix['body']+(suffix['rider'] or '')
    body=body.rstrip('.')
    subtype = _additive_subtype_result(body)
    if subtype is not None:
        return subtype
    retains=False
    if re.search(r"(?:\. (?:It's|They're) still (?:a land|lands|a planeswalker|planeswalkers)| that's still a (?:land|planeswalker))$",body,re.I):
        retains=True
        body=re.sub(r"(?:\. (?:It's|They're) still (?:a land|lands|a planeswalker|planeswalkers)| that's still a (?:land|planeswalker))$",'',body,flags=re.I)
    if re.search(r" in addition to (?:its|their) other types$",body,re.I):
        retains=True
        body=re.sub(r" in addition to (?:its|their) other types$",'',body,flags=re.I)
    removes=False
    remove_prefix=re.fullmatch(r"(?P<subject>.+?) loses all abilities and (?P<body>.+)",body,re.I)
    if remove_prefix is not None:
        removes=True;body=remove_prefix['subject']+' '+remove_prefix['body']
    removal_tail=re.search(r", loses all abilities, and gains (?P<keywords>.+)$",body,re.I)
    if removal_tail is not None:
        removes=True;body=body[:removal_tail.start()]+' and gains '+removal_tail['keywords']
    base=_BASE.fullmatch(body)
    animation=_ANIMATE.fullmatch(body) if base is None else None
    if base is None and animation is None:return None
    match=base or animation
    assert match is not None
    selected=_subject(match['subject'],source_name=source_name,source_is_permanent=source_is_permanent,source_card_types=source_card_types)
    if selected is None:return None
    selection,schema=selected
    keywords=()
    if base is not None:
        creature_subject = (
            selection.get("card") == "$source.zone_object"
            and "creature" in source_card_types
        ) or (
            schema is not None and schema.get("types_any") == ["creature"]
        ) or (
            "predicate" in selection
            and "creature" in selection["predicate"]["types_all"]
        )
        if not creature_subject:
            return None
        tail=base['tail'].strip()
        if tail:
            keyword_match=re.fullmatch(r"and gains? (?P<keywords>.+)",tail,re.I)
            if keyword_match is None:return None
            keywords=_keyword_list(keyword_match['keywords'],extended=True)
            if keywords is None:return None
        if retains:return None
        spec=FixedResolutionCharacteristicsSpec(remove_all_abilities=removes,keywords=keywords,base_power=int(base['p']),base_toughness=int(base['t']))
    else:
        assert animation is not None
        description=(animation['description'] or animation['base_description']).strip()
        tail=animation['tail'].strip()
        description=(description+' '+tail).strip()
        keyword_tail=re.search(r" (?:with|and gains?) (?P<keywords>.+)$",description,re.I)
        if keyword_tail is not None:
            keywords=_keyword_list(keyword_tail['keywords'],extended=True)
            if keywords is None:return None
            description=description[:keyword_tail.start()]
        is_creature=bool(re.search(r"(?:^| )creatures?$",description,re.I))
        artifact=bool(re.search(r"(?:^| )artifact creature$",description,re.I))
        description=re.sub(r"(?:^| )artifact creature$",'',description,flags=re.I)
        description=re.sub(r"(?:^| )creatures?$",'',description,flags=re.I)
        parsed=_animation_description(description)
        if parsed is None:return None
        colors,subtypes=parsed
        if not is_creature and not subtypes:
            return None
        spec=FixedResolutionCharacteristicsSpec(card_types=(('Artifact','Creature') if artifact else ('Creature',)),creature_subtypes=subtypes or None,
            colors=colors,retain_types=retains or artifact,retain_creature_subtypes=retains,
            remove_all_abilities=removes,keywords=keywords,base_power=int(animation['p'] or animation['bp']),base_toughness=int(animation['t'] or animation['bt']))
    mechanics=('cr-611-continuous-effects',PERMANENT_CHARACTERISTIC_MECHANIC,*(keyword.casefold() for keyword in keywords),*(('cr-115-targets',) if schema is not None else ()))
    return ('fixed-resolution-characteristics-v2',({'op':CHARACTERISTIC_OPERATION,'schema_version':2,'characteristics':spec.to_dict(),**selection},),schema,mechanics)


_ADDITIVE_SUBTYPE = re.compile(
    r"(?P<subject>(?:another )?target .+?) (?:gains? (?P<before>.+?) and )?"
    r"becomes an? (?P<subtype>.+?) in addition to its other types"
    r"(?: and gains? (?P<after>.+))?",
    re.IGNORECASE,
)


def _additive_subtype_result(body: str):
    from ..creature_subtypes import canonical_creature_subtype

    match = _ADDITIVE_SUBTYPE.fullmatch(body)
    if match is None or (match["before"] is not None and match["after"] is not None):
        return None
    target = direct_permanent_target_spec(match["subject"])
    subtype = canonical_creature_subtype(match["subtype"])
    if target is None or subtype is None:
        return None
    schema = target.to_target_schema()
    if schema.get("types_any") != ["creature"] and "creature" not in schema.get("types_all", ()):
        return None
    raw_keywords = match["before"] or match["after"]
    keywords = _keyword_list(raw_keywords, extended=True) if raw_keywords is not None else ()
    if keywords is None:
        return None
    try:
        spec = FixedResolutionSubtypeGrantSpec(subtype.title(), keywords)
    except ValueError:
        return None
    effect = {"op": CHARACTERISTIC_OPERATION, "schema_version": SUBTYPE_GRANT_INSTRUCTION_VERSION, "card": "$target.0",
              "characteristics": spec.to_dict()}
    return (
        "fixed-resolution-subtype-grant-v12", (effect,), schema,
        (PERMANENT_CHARACTERISTIC_MECHANIC, "cr-611-continuous-effects", "cr-115-targets",
         *(keyword.casefold() for keyword in keywords)),
    )
