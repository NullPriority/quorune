from __future__ import annotations

"""Fixed animation and base-setting through the existing characteristic owner."""

from dataclasses import replace
from . import regex_working_set as re
from typing import Any, Mapping

from ..attachment_references import AttachmentReferenceKind, AttachmentReferenceSpec
from ..continuous_effect_model import ContinuousEffectDuration
from ..creature_subtypes import canonical_creature_subtype
from ..object_predicate import ObjectQuerySpec
from ..resolution_characteristic_model import (
    CountedTargetLandAnimationSpec,
    ChosenCreatureTypeModifier, ChosenCreatureTypeResult,
    CHARACTERISTIC_OPERATION, CHOSEN_SUBTYPE_CHOICE_VERSION, PERMANENT_CHARACTERISTIC_MECHANIC,
    DERIVED_CHARACTERISTIC_INSTRUCTION_VERSION, RECIPIENT_MANA_VALUE,
    SUBTYPE_GRANT_INSTRUCTION_VERSION, DerivedResolutionCharacteristicsSpec,
    FixedResolutionCharacteristicsSpec, FixedResolutionSubtypeGrantSpec, IndefiniteSourceAnimationSpec,
    controlled_subtype_creature_query,
    INDEFINITE_SOURCE_ANIMATION_VERSION, INDEFINITE_SOURCE_ANIMATION_MECHANIC,
    INDEFINITE_ENCHANTMENT_FORM_VERSION, INDEFINITE_ENCHANTMENT_FORM_MECHANIC,
    FixedResolutionCardTypeAdditionSpec, TARGET_CARD_TYPE_ADDITION_VERSION, TARGET_CARD_TYPE_ADDITION_MECHANIC,
    TargetPlayerCreatureOverwriteSpec, TARGET_PLAYER_CREATURE_OVERWRITE_VERSION,
    target_player_creature_overwrite_query, target_player_creature_overwrite_schema,
    TargetCreatureAbilityLossSpec, TARGET_CREATURE_ABILITY_LOSS_VERSION, target_creature_ability_loss_schema,
    NEXT_TURN_TARGET_LAND_ANIMATION_MECHANICS, next_turn_target_land_animation_effects,
    next_turn_target_land_animation_schema,
)
from ..rules.source_references import SourceReferenceSpec
from .direct_target import direct_permanent_target_spec
from .fixed_resolution_characteristic_queries import fixed_resolution_characteristic_query_is_closed
from .fixed_target_effect_sequences import (
    FixedSourceCharacteristicsTemplate, _animation_description, _COLOR_NAMES, _keyword_list,
)
from .public_state_queries import fixed_characteristic_battlefield_query_subject


_BASE = re.compile(r"(?P<subject>.+?) (?:has|have) base power and toughness (?P<p>\d+)/(?P<t>\d+)(?P<tail>.*)",re.I)
_ANIMATE = re.compile(r"(?P<subject>.+?) becomes? (?:an? )?(?:(?P<p>\d+)/(?P<t>\d+) (?P<description>.+)|(?P<base_description>.+?) with base power and toughness (?P<bp>\d+)/(?P<bt>\d+))(?P<tail>.*)",re.I)
_MANA_VALUE_ANIMATION = re.compile(
    r"target noncreature artifact becomes an artifact creature with power and toughness each equal to its mana value",
    re.I,
)
_X_BASE_ANIMATION = re.compile(
    r"target creature becomes an? (?P<definition>.+?) with base power and toughness each equal to X(?: plus (?P<offset>1))?",
    re.I,
)
_PLAYER_SET_SCHEMA = {"zones":["player"],"categories":["player"],"player_relation":"any","count":1}
_CHOSEN_SUBTYPE = re.compile(
    r"(?P<subject>.+?) becomes (?:the|a) creature type of your choice until end of turn\.", re.I,
)
_CHOSEN_SUBTYPE_PREFIX = re.compile(
    r"Choose a creature type(?: other than (?P<excluded>[^.]+))?\. "
    r"(?P<subject>.+?) becomes that type until end of turn\.", re.I,
)


def _chosen_creature_type_result_template(text: str):
    normalized = " ".join(text.casefold().replace("’", "'").split())
    boost = re.fullmatch(
        r"creatures of the creature type of your choice get \+(?P<amount>[1-9][0-9]?)/\+(?P=amount)"
        r"(?P<trample> and gain trample)? until end of turn[.]?", normalized,
    )
    if boost is not None:
        result = ChosenCreatureTypeModifier(int(boost["amount"]), bool(boost["trample"]))
        mechanics = ("cr-611-continuous-effects", *(("trample",) if result.trample else ()))
        return "chosen-type-temporary-modifier-v1", (result.choice_effect(),), None, mechanics
    if normalized == "choose a creature type. draw a card for each permanent you control of that type.":
        result = ChosenCreatureTypeResult.CONTROLLED_PERMANENT_DRAW
        mechanics = ("cr-121-drawing-a-card",)
    elif normalized == (
        "choose a creature type. reveal cards from the top of your library until you reveal a creature card of that type. "
        "put that card onto the battlefield and shuffle the rest into your library."
    ):
        result = ChosenCreatureTypeResult.REVEAL_UNTIL_ENTRY
        mechanics = ()
    elif normalized == "choose a creature type. creatures that aren't of the chosen type get -3/-3 until end of turn.":
        result = ChosenCreatureTypeResult.NONMATCHING_CREATURE_STATS
        mechanics = ("cr-611-continuous-effects",)
    elif normalized == "choose a creature type. return all creatures that aren't of the chosen type to their owners' hands.":
        result = ChosenCreatureTypeResult.NONMATCHING_CREATURE_RETURN
        mechanics = ("fixed-public-zone-move",)
    else:
        return None
    return f"chosen-creature-type-{result.value}-v1", (result.choice_effect(),), None, mechanics


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
    subject = {
        AttachmentReferenceKind.ENCHANTED: "enchanted creature",
        AttachmentReferenceKind.EQUIPPED: "equipped creature",
    }.get(source_attachment_relation)
    if subject is None:
        return None
    match = re.fullmatch(
        rf"{subject} gets ([+-]\d+)/([+-]\d+) until end of turn\.", text.strip(), re.I,
    )
    if match is None:
        return None
    power, toughness = map(int, match.groups())
    if not (power or toughness):
        return None
    # A last-known attachment can identify a permanent that has since changed type.
    reference = AttachmentReferenceSpec(source_attachment_relation, "permanent", schema_version=2)
    effect = {"op": "modify_stats_until_end_of_turn", "card": reference.to_dict(), "power": power, "toughness": toughness}
    return "fixed-attached-stats-until-end-of-turn-v1", (effect,), None, ("cr-611-continuous-effects",)


def fixed_attached_keyword_effect_template(text: str, *, source_attachment_relation: AttachmentReferenceKind | None):
    material = text.strip()
    if source_attachment_relation is AttachmentReferenceKind.EQUIPPED:
        if material.casefold() != "equipped creature gains hexproof until end of turn.":
            return None
        source_kind, keywords = "artifact", ("Hexproof",)
    elif source_attachment_relation is AttachmentReferenceKind.ENCHANTED:
        match = re.fullmatch(r"Enchanted creature gains (.+?) until end of turn\.", material, re.I)
        if match is None or (keywords := _keyword_list(match[1])) is None:
            return None
        source_kind = "enchantment"
    else:
        return None
    template = FixedSourceCharacteristicsTemplate(source_kind=source_kind, keywords=keywords)
    reference = AttachmentReferenceSpec(source_attachment_relation, "permanent", schema_version=2)
    effect = {**template.effects[0], "card": reference.to_dict()}
    mechanics = ("cr-611-continuous-effects", *(keyword.casefold() for keyword in keywords))
    return "fixed-attached-keywords-until-end-of-turn-v1", (effect,), None, mechanics


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
    if normalized.casefold() == "all lands you control":
        normalized = normalized[len("all "):]
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


def _derived_target_animation(body: str, *, cast_x_available: bool):
    mana_value = _MANA_VALUE_ANIMATION.fullmatch(body)
    x_base = _X_BASE_ANIMATION.fullmatch(body) if cast_x_available else None
    if mana_value is not None:
        target = direct_permanent_target_spec("target noncreature artifact")
        settings = FixedResolutionCharacteristicsSpec(
            card_types=("Artifact", "Creature"), retain_types=True, retain_creature_subtypes=True,
        )
        spec = DerivedResolutionCharacteristicsSpec(settings, RECIPIENT_MANA_VALUE)
    elif x_base is not None:
        from ..query_effect_amount_model import CastXAmountSpec

        parsed = _animation_description(x_base["definition"])
        if parsed is None:
            return None
        colors, subtypes = parsed
        if not colors or not subtypes:
            return None
        target = direct_permanent_target_spec("target creature")
        settings = FixedResolutionCharacteristicsSpec(
            card_types=("Creature",), creature_subtypes=subtypes, colors=colors, retain_types=True,
        )
        spec = DerivedResolutionCharacteristicsSpec(settings, CastXAmountSpec(), int(x_base["offset"] or 0))
    else:
        return None
    assert target is not None
    effect = {"op": CHARACTERISTIC_OPERATION, "schema_version": DERIVED_CHARACTERISTIC_INSTRUCTION_VERSION,
              "characteristics": spec.to_dict(), "card": "$target.0"}
    return ("fixed-resolution-characteristics-derived-base-v1", (effect,), target.to_target_schema(),
            ("cr-611-continuous-effects", PERMANENT_CHARACTERISTIC_MECHANIC, "cr-115-targets"))


_INDEFINITE_SOURCE_ANIMATION = re.compile(
    r"(?P<subject>.+?) becomes a (?P<p>[0-9]{1,2})/(?P<t>[0-9]{1,2}) "
    r"(?P<description>[^.]+?) creature(?: with (?P<abilities>[^.]+))?", re.I,
)
_ADDITIVE_CARD_TYPE = re.compile(
    r"(?P<subject>(?:another )?target .+?) "
    r"(?:gets (?P<power>[+-]\d+)/(?P<toughness>[+-]\d+) and )?"
    r"becomes an? (?P<card_type>[A-Za-z]+) in addition to its other types",
    re.IGNORECASE,
)
_FIXED_PROTECTION_SUFFIX = re.compile(
    r"(?:^|,? and )protection from (?P<color>white|blue|black|red|green)$", re.I,
)


def _indefinite_source_animation(text, *, source_name, source_is_permanent, source_card_types):
    match = _INDEFINITE_SOURCE_ANIMATION.fullmatch(text.removesuffix("."))
    if match is None:
        return None
    selected = _subject(match["subject"], source_name=source_name,
        source_is_permanent=source_is_permanent, source_card_types=source_card_types)
    if selected != ({"card": "$source.zone_object"}, None):
        return None
    parsed = _animation_description(match["description"])
    if parsed is None or parsed[0] is not None or not parsed[1]:
        return None
    abilities = match["abilities"] or ""
    protection = _FIXED_PROTECTION_SUFFIX.search(abilities)
    color = _COLOR_NAMES[protection["color"].casefold()] if protection is not None else None
    if protection is not None:
        abilities = abilities[:protection.start()]
    keywords = _keyword_list(abilities, extended=True) if abilities else ()
    if keywords is None:
        return None
    settings = FixedResolutionCharacteristicsSpec(card_types=("Creature",), creature_subtypes=parsed[1],
        keywords=keywords, base_power=int(match["p"]), base_toughness=int(match["t"]))
    spec = IndefiniteSourceAnimationSpec(settings, color)
    effect = {"op": CHARACTERISTIC_OPERATION, "schema_version": INDEFINITE_SOURCE_ANIMATION_VERSION,
        "characteristics": spec.to_dict(), "card": "$source.zone_object"}
    return ("indefinite-source-animation-v1", (effect,), None,
        ("cr-611-continuous-effects", INDEFINITE_SOURCE_ANIMATION_MECHANIC))


def _indefinite_enchantment_form(text, *, source_name, source_is_permanent, source_card_types):
    match = re.fullmatch(r"(?P<subject>.+?) becomes an enchantment\.", text, re.I)
    if match is None:
        return None
    selected = _subject(match["subject"], source_name=source_name,
        source_is_permanent=source_is_permanent, source_card_types=source_card_types)
    if selected != ({"card": "$source.zone_object"}, None):
        return None
    effect = {"op": CHARACTERISTIC_OPERATION, "schema_version": INDEFINITE_ENCHANTMENT_FORM_VERSION,
        "characteristics": {"card_type": "Enchantment"}, "card": "$source.zone_object"}
    return ("indefinite-enchantment-form-v1", (effect,), None,
        ("cr-611-continuous-effects", INDEFINITE_ENCHANTMENT_FORM_MECHANIC))


_TARGET_PLAYER_CREATURE_FORMS = {
    "until end of turn, each creature target player controls loses all abilities and becomes a blue frog "
    "with base power and toughness 1/1.": "blue_frog",
    "until end of turn, creatures target player controls lose all abilities and have base power and toughness 0/2.": "zero_two",
}


def _target_player_creature_overwrite_template(text):
    form = _TARGET_PLAYER_CREATURE_FORMS.get(" ".join(text.casefold().split()))
    if form is None:
        return None
    effect = {
        "op": CHARACTERISTIC_OPERATION,
        "schema_version": TARGET_PLAYER_CREATURE_OVERWRITE_VERSION,
        "predicate": target_player_creature_overwrite_query().to_dict(),
        "characteristics": TargetPlayerCreatureOverwriteSpec(form).to_dict(),
    }
    return ("fixed-target-player-creature-overwrite-v1", (effect,), target_player_creature_overwrite_schema(),
            ("cr-611-continuous-effects", PERMANENT_CHARACTERISTIC_MECHANIC, "cr-115-targets"))


def _targeted_tap_then_ability_loss_template(text):
    normalized = " ".join(text.casefold().split())
    if normalized != "tap target creature an opponent controls. it loses all abilities until end of turn.":
        return None
    effects = (
        {"op": "tap", "card": "$target.0"},
        {"op": CHARACTERISTIC_OPERATION, "schema_version": TARGET_CREATURE_ABILITY_LOSS_VERSION,
         "card": "$target.0", "characteristics": TargetCreatureAbilityLossSpec().to_dict()},
    )
    return ("targeted-tap-then-ability-loss-v1", effects, target_creature_ability_loss_schema(),
            ("tap-and-untap", "cr-611-continuous-effects", PERMANENT_CHARACTERISTIC_MECHANIC, "cr-115-targets"))


def _untap_then_animate_land_template(text):
    normalized = " ".join(text.casefold().replace("’", "'").split())
    if normalized != (
        "untap target land you control. until your next turn, "
        "it becomes a 5/5 elemental creature with haste. it's still a land."
    ):
        return None
    return (
        "untap-retaining-land-animation-next-turn-v1", next_turn_target_land_animation_effects(),
        next_turn_target_land_animation_schema(),
        NEXT_TURN_TARGET_LAND_ANIMATION_MECHANICS,
    )


def fixed_resolution_characteristics_effect_template(
    text: str, *, source_name: str, source_is_permanent: bool | None,
    source_card_types: tuple[str, ...],
    source_attachment_relation: AttachmentReferenceKind | None = None,
    cast_x_available: bool = False,
) -> tuple[str, tuple[Mapping[str, Any], ...], Mapping[str, Any] | None, tuple[str, ...]] | None:
    """Consume one complete fixed end-of-turn instruction, never an open tail."""
    if source_is_permanent is True and " ".join(text.split()).casefold() == (
        "target land you control becomes an x/x elemental creature with trample and haste until end of turn, "
        "where x is the number of enchantments you control. it's still a land."
    ):
        target = direct_permanent_target_spec("target land you control")
        return ("counted-controlled-land-animation-v1", (CountedTargetLandAnimationSpec().effect(),),
                target.to_target_schema(), ("cr-611-continuous-effects", PERMANENT_CHARACTERISTIC_MECHANIC,
                                            "cr-115-targets", "trample", "haste"))
    if fixed_form := (_target_player_creature_overwrite_template(text) or _targeted_tap_then_ability_loss_template(text)
                      or _untap_then_animate_land_template(text)):
        return fixed_form
    other = re.fullmatch(
        r"All other creatures get (?P<p>[+-][0-9]+)/(?P<t>[+-][0-9]+) until end of turn\.?",
        text.strip(), re.I,
    )
    if other is not None:
        if source_is_permanent is not True:
            return None
        power, toughness = int(other["p"]), int(other["t"])
        if not (power or toughness):
            return None
        query = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",),
                                exclude_ref="$source.zone_object")
        effect = {"op": "modify_all_matching_permanents_until_end_of_turn",
                  "predicate": query.to_dict(), "power": power, "toughness": toughness}
        return ("source-excluded-public-fixed-stats-eot-v1", (effect,), None,
                ("cr-611-continuous-effects",))
    if result := _chosen_creature_type_result_template(text):
        return result
    chosen = _chosen_subtype_template(text, source_is_permanent=source_is_permanent,
                                     source_attachment_relation=source_attachment_relation)
    if chosen is not None:
        return chosen
    from .fixed_stat_multipliers import fixed_stat_multiplier_effect_template
    multiplier = fixed_stat_multiplier_effect_template(text, source_name=source_name,
        source_is_permanent=source_is_permanent, source_card_types=source_card_types,
        source_attachment_relation=source_attachment_relation)
    if multiplier is not None:
        return multiplier
    from .power_toughness_switch import power_toughness_switch_effect_template
    switch = power_toughness_switch_effect_template(text, source_name=source_name,
        source_is_permanent=source_is_permanent, source_card_types=source_card_types)
    if switch is not None:
        return switch
    normalized = text.strip()
    type_addition = _additive_card_type_template(normalized)
    if type_addition is not None:
        return type_addition
    if any(value in normalized.casefold() for value in ('"','where',' if ',' when ','copy','all creature types')):
        return None
    prefix = re.fullmatch(r"Until end of turn, (?P<body>.+)",normalized,re.I)
    if prefix is not None:body=prefix['body']
    else:
        suffix=re.fullmatch(r"(?P<body>.+?) until end of turn(?P<rider>\. (?:It's|They're) still (?:a land|lands|a planeswalker|planeswalkers)\.)?\.?",normalized,re.I)
        if suffix is None:
            return _indefinite_enchantment_form(normalized, source_name=source_name,
                source_is_permanent=source_is_permanent, source_card_types=source_card_types) or _indefinite_source_animation(
                    normalized, source_name=source_name, source_is_permanent=source_is_permanent,
                    source_card_types=source_card_types)
        body=suffix['body']+(suffix['rider'] or '')
    body=body.rstrip('.')
    derived = _derived_target_animation(body, cast_x_available=cast_x_available)
    if derived is not None:
        return derived
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
        artifact = bool(re.search(r"(?:^| )artifact creatures?$", description, re.I))
        description = re.sub(r"(?:^| )artifact creatures?$", "", description, flags=re.I)
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


def _additive_card_type_template(text):
    if not text.endswith("."):
        return None
    text = text.removesuffix(".")
    start = "Until end of turn, "
    end = " until end of turn"
    if text.casefold().startswith(start.casefold()):
        body = text[len(start):]
        duration = ContinuousEffectDuration.UNTIL_END_OF_TURN
    elif text.casefold().endswith(end):
        body = text[:-len(end)]
        duration = ContinuousEffectDuration.UNTIL_END_OF_TURN
    else:
        body = text
        duration = ContinuousEffectDuration.ZONE_OBJECT
    match = _ADDITIVE_CARD_TYPE.fullmatch(body)
    if match is None:
        return None
    target = direct_permanent_target_spec(match["subject"])
    if target is None:
        return None
    try:
        spec = FixedResolutionCardTypeAdditionSpec(
            match["card_type"].title(), duration, int(match["power"] or 0), int(match["toughness"] or 0),
        )
    except ValueError:
        return None
    if match["power"] is not None and not (spec.power or spec.toughness):
        return None
    effect = {"op": CHARACTERISTIC_OPERATION, "schema_version": TARGET_CARD_TYPE_ADDITION_VERSION,
              "card": "$target.0", "characteristics": spec.to_dict()}
    return ("fixed-target-card-type-addition-v1", (effect,), target.to_target_schema(),
            ("cr-611-continuous-effects", "cr-115-targets", TARGET_CARD_TYPE_ADDITION_MECHANIC))


_ADDITIVE_SUBTYPE = re.compile(
    r"(?P<subject>(?:another )?target .+?) (?:gains? (?P<before>.+?) and )?"
    r"becomes an? (?P<subtype>.+?) in addition to its other types"
    r"(?: and gains? (?P<after>.+))?",
    re.IGNORECASE,
)


_BASE_AND_ADDITIVE_SUBTYPE = re.compile(
    r"each (?P<required>.+?) creature you control has base power and toughness (?P<p>[0-9]+)/(?P<t>[0-9]+)"
    r" and becomes an? (?P<added>.+?) in addition to its other creature types",
    re.IGNORECASE,
)


def _base_and_additive_subtype_result(match):
    required = canonical_creature_subtype(match["required"])
    added = canonical_creature_subtype(match["added"])
    if required is None or added is None:
        return None
    spec = FixedResolutionSubtypeGrantSpec(
        added.title(), base_power_toughness=(int(match["p"]), int(match["t"])),
    )
    effect = {"op": CHARACTERISTIC_OPERATION, "schema_version": SUBTYPE_GRANT_INSTRUCTION_VERSION,
              "predicate": controlled_subtype_creature_query(required).to_dict(), "characteristics": spec.to_dict()}
    return ("fixed-base-and-additive-subtype-v1", (effect,), None,
            (PERMANENT_CHARACTERISTIC_MECHANIC, "cr-611-continuous-effects"))


def _additive_subtype_result(body: str):
    if match := _BASE_AND_ADDITIVE_SUBTYPE.fullmatch(body):
        return _base_and_additive_subtype_result(match)
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
