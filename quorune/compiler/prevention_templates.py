from __future__ import annotations

import re
from typing import Any, Mapping

from ..damage_source import REPRESENTED_DAMAGE_SOURCE_ZONES
from ..object_query import (
    ObjectQuerySpec,
    validate_chosen_damage_source_predicate,
)
from ..rules.source_references import SourceReferenceSpec
from ..rules.capabilities import capability_dependencies_for_node
from ..semantics import SemanticProgram
from .fixed_all_damage_prevention import (
    fixed_all_damage_prevention_scope_descriptor,
    fixed_all_damage_prevention_specs,
    fixed_all_damage_prevention_target_schema,
)


PreventionTemplate = tuple[
    str,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]
PreventionTriggerTemplate = tuple[
    str,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
    Mapping[str, Any],
]


_DYNAMIC_AMOUNT_TOKEN = chr(120)
_LIFE_CAPTURE = "".join(("li", "fe"))
_SUBJECT_PATTERN = (
    r"any target|target creature(?: you control)?|"
    r"target artifact creature|target legendary creature|"
    r"target player or planeswalker|you"
)
_SOURCE_QUALIFIER_PATTERN = (
    r"white|blue|black|red|green|black or red|artifact|land|creature|"
    r"legendary"
)
_COLOR_CODES = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}


def _target_schema(phrase: str) -> Mapping[str, Any] | None:
    normalized = phrase.casefold()
    if normalized == "you":
        return None
    if normalized == "any target":
        return {
            "zones": ["player", "battlefield"],
            "categories": ["player", "permanent"],
            "predicate": "damageable",
            "count": 1,
        }
    if normalized == "target player or planeswalker":
        return {
            "zones": ["player", "battlefield"],
            "categories": ["player", "permanent"],
            "predicate": "player_or_planeswalker",
            "count": 1,
        }
    if normalized in {
        "target creature",
        "target creature you control",
        "target artifact creature",
        "target legendary creature",
    }:
        schema: dict[str, Any] = {
            "zones": ["battlefield"],
            "categories": ["permanent"],
            "types_all": ["creature"],
            "count": 1,
        }
        if normalized == "target artifact creature":
            schema["types_all"] = ["artifact", "creature"]
        if normalized == "target creature you control":
            schema["controller"] = "you"
        if normalized == "target legendary creature":
            schema["supertypes_any"] = ["legendary"]
        return schema
    return None


def _amount_value(raw: str) -> int | str:
    return "$x" if raw.casefold() == _DYNAMIC_AMOUNT_TOKEN else int(raw)


def _shield(
    *,
    amount: int | str,
    subject: str,
    aftermath: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "op": "create_damage_prevention_shield",
        "source": "$source",
        "subject": "$controller" if subject == "you" else "$target.0",
        "mode": "amount",
        "amount": amount,
        "duration": "until_end_of_turn",
    }
    if aftermath:
        value["aftermath"] = aftermath
    return value


def _rules(
    target_schema: Mapping[str, Any] | None,
    *extra: str,
) -> tuple[str, ...]:
    return (
        "cr-615-prevention-effects",
        *extra,
        *(("cr-115-targets",) if target_schema is not None else ()),
    )


def _source_predicate(qualifier: str | None) -> ObjectQuerySpec:
    normalized = " ".join(str(qualifier or "").casefold().split())
    if not normalized:
        return validate_chosen_damage_source_predicate(
            ObjectQuerySpec(
                zones=REPRESENTED_DAMAGE_SOURCE_ZONES,
                known_to_actor=True,
            )
        )
    color_words = normalized.split(" or ")
    if all(word in _COLOR_CODES for word in color_words):
        return validate_chosen_damage_source_predicate(
            ObjectQuerySpec(
                zones=REPRESENTED_DAMAGE_SOURCE_ZONES,
                colors_any=tuple(_COLOR_CODES[word] for word in color_words),
                known_to_actor=True,
            )
        )
    return validate_chosen_damage_source_predicate(
        ObjectQuerySpec(
            zones=REPRESENTED_DAMAGE_SOURCE_ZONES,
            types_all=(
                (normalized,)
                if normalized in {"artifact", "land", "creature"}
                else ()
            ),
            supertypes_all=(
                ("legendary",) if normalized == "legendary" else ()
            ),
            known_to_actor=True,
        )
    )


def _chosen_source_effect(
    *,
    shield: Mapping[str, Any],
    qualifier: str | None,
) -> dict[str, Any]:
    return {
        "op": "choose_damage_source",
        "prompt": "Choose the source whose damage will be prevented.",
        "source_predicate": _source_predicate(qualifier).to_dict(),
        "shield": dict(shield),
    }


def _chosen_source_next_instance(
    normalized: str,
) -> PreventionTemplate | None:
    match = re.fullmatch(
        rf"the next time an? (?:(?P<qualifier>{_SOURCE_QUALIFIER_PATTERN}) )?"
        r"source of your choice would deal damage"
        rf"(?: to (?P<subject>{_SUBJECT_PATTERN}))? this turn, prevent "
        r"(?:that|the) damage\.?",
        normalized,
        re.IGNORECASE,
    )
    if not match:
        return None
    subject = str(match.group("subject") or "").casefold()
    target_schema = _target_schema(subject) if subject else None
    shield = {
        "op": "create_damage_prevention_shield",
        "source": "$source",
        "subject": (
            "$controller"
            if subject == "you"
            else ("$target.0" if subject else "*")
        ),
        "mode": "next_instance",
        "duration": "until_end_of_turn",
    }
    return (
        "damage-prevention-chosen-source-next-instance-v1",
        (
            _chosen_source_effect(
                shield=shield,
                qualifier=match.group("qualifier"),
            ),
        ),
        target_schema,
        _rules(target_schema),
    )


def _chosen_source_damage_aftermath(
    normalized: str,
) -> PreventionTemplate | None:
    match = re.fullmatch(
        r"the next time a source of your choice would deal damage to you "
        r"this turn, prevent that damage\. if damage is prevented this way, "
        r"(?P<source>[a-z0-9'’, -]+) deals that much damage to that source's "
        r"controller\.?",
        normalized,
        re.IGNORECASE,
    )
    if not match:
        return None
    shield = {
        "op": "create_damage_prevention_shield",
        "source": "$source",
        "subject": "$controller",
        "mode": "next_instance",
        "duration": "until_end_of_turn",
        "aftermath": [
            {
                "kind": "deal_damage",
                "source": "$source",
                "recipient": None,
                "recipient_kind": "prevented_source_controller",
                "per_prevented": 1,
                "fixed_amount": 0,
            }
        ],
    }
    return (
        "damage-prevention-source-controller-aftermath-v1",
        (_chosen_source_effect(shield=shield, qualifier=None),),
        None,
        _rules(None),
    )


def _chosen_source_triggered_damage_and_draw(
    normalized: str,
    *,
    card_name: str | None = None,
) -> PreventionTemplate | None:
    match = re.fullmatch(
        r"the next time a source of your choice would deal damage to you "
        r"this turn, prevent that damage\. when damage is prevented this way, "
        r"(?P<source>[a-z0-9'’, -]+) deals that much damage to that source's "
        r"controller and you draw that many cards?\.?",
        normalized,
        re.IGNORECASE,
    )
    if not match:
        return None
    if card_name is not None and not SourceReferenceSpec(card_name).matches(
        match.group("source")
    ):
        return None
    shield = {
        "op": "create_damage_prevention_shield",
        "source": "$source",
        "subject": "$controller",
        "mode": "next_instance",
        "duration": "until_end_of_turn",
        "triggered_ability": {
            "source": "$source",
            "label": "When damage is prevented this way",
            "target_schema": {},
            "results": [
                {
                    "kind": "deal_damage",
                    "source": "$source",
                    "recipient_kind": "prevented_source_controller",
                    "per_prevented": 1,
                    "fixed_amount": 0,
                },
                {
                    "kind": "draw_cards",
                    "player": "$controller",
                    "per_prevented": 1,
                    "fixed_amount": 0,
                    "private": True,
                },
            ],
        },
    }
    return (
        "damage-prevention-triggered-damage-draw-v1",
        (_chosen_source_effect(shield=shield, qualifier=None),),
        None,
        _rules(None, "cr-120-damage", "cr-121-drawing-a-card"),
    )


def _chosen_source_all_damage(
    normalized: str,
) -> PreventionTemplate | None:
    dealt_to = re.fullmatch(
        rf"prevent all damage that would be dealt to "
        rf"(?P<subject>{_SUBJECT_PATTERN}) this turn by an? "
        rf"(?:(?P<qualifier>{_SOURCE_QUALIFIER_PATTERN}) )?"
        r"source of your choice\.?",
        normalized,
        re.IGNORECASE,
    )
    source_deals = re.fullmatch(
        rf"prevent all damage an? "
        rf"(?:(?P<qualifier>{_SOURCE_QUALIFIER_PATTERN}) )?"
        r"source of your choice would deal"
        rf"(?: to (?P<subject>{_SUBJECT_PATTERN}))? this turn\.?",
        normalized,
        re.IGNORECASE,
    )
    match = dealt_to or source_deals
    if not match:
        return None
    subject = str(match.group("subject") or "").casefold()
    target_schema = _target_schema(subject) if subject else None
    shield = {
        "op": "create_damage_prevention_shield",
        "source": "$source",
        "subject": (
            "$controller"
            if subject == "you"
            else ("$target.0" if subject else "*")
        ),
        "mode": "all",
        "duration": "until_end_of_turn",
    }
    return (
        "damage-prevention-chosen-source-all-v1",
        (
            _chosen_source_effect(
                shield=shield,
                qualifier=match.group("qualifier"),
            ),
        ),
        target_schema,
        _rules(target_schema),
    )


def _chosen_source_fixed_life(normalized: str) -> PreventionTemplate | None:
    match = re.fullmatch(
        rf"prevent the next (?P<amount>\d+|x) damage that would be dealt to "
        rf"(?P<subject>{_SUBJECT_PATTERN}) this turn by a source of your choice\. "
        r"you gain (?P<life>\d+) life\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        subject = match.group("subject").casefold()
        target_schema = _target_schema(subject)
        shield = _shield(
            amount=_amount_value(match.group("amount")),
            subject=subject,
        )
        return (
            "damage-prevention-chosen-source-fixed-life-v2",
            (
                _chosen_source_effect(shield=shield, qualifier=None),
                {
                    "op": _LIFE_CAPTURE,
                    "player": "$controller",
                    "delta": int(match.group(_LIFE_CAPTURE)),
                    "source": "$source",
                    "cause": "spell_resolution",
                },
            ),
            target_schema,
            _rules(target_schema, "cr-119-life"),
        )
    return None


def _chosen_source_fixed_amount(
    normalized: str,
) -> PreventionTemplate | None:
    match = re.fullmatch(
        rf"prevent the next (?P<amount>\d+|x) damage that would be dealt to "
        rf"(?P<subject>{_SUBJECT_PATTERN}) this turn by an? "
        rf"(?:(?P<qualifier>{_SOURCE_QUALIFIER_PATTERN}) )?"
        r"source of your choice\.?",
        normalized,
        re.IGNORECASE,
    )
    if not match:
        return None
    subject = match.group("subject").casefold()
    target_schema = _target_schema(subject)
    return (
        "damage-prevention-chosen-source-fixed-v1",
        (
            _chosen_source_effect(
                shield=_shield(
                    amount=_amount_value(match.group("amount")),
                    subject=subject,
                ),
                qualifier=match.group("qualifier"),
            ),
        ),
        target_schema,
        _rules(target_schema),
    )


def _scaled_life_aftermath(normalized: str) -> PreventionTemplate | None:
    match = re.fullmatch(
        rf"prevent the next (?P<amount>\d+|x) damage that would be dealt to "
        rf"(?P<subject>{_SUBJECT_PATTERN}) this turn\. you gain life equal to "
        r"the damage prevented this way\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        subject = match.group("subject").casefold()
        target_schema = _target_schema(subject)
        return (
            "damage-prevention-life-aftermath-v1",
            (
                _shield(
                    amount=_amount_value(match.group("amount")),
                    subject=subject,
                    aftermath=[
                        {
                            "kind": "gain_life",
                            "player": "$controller",
                            "per_prevented": 1,
                            "fixed_amount": 0,
                        }
                    ],
                ),
            ),
            target_schema,
            _rules(target_schema, "cr-119-life"),
        )
    return None


def _counter_aftermath(normalized: str) -> PreventionTemplate | None:
    match = re.fullmatch(
        rf"prevent the next (?P<amount>\d+|x) damage that would be dealt to "
        rf"(?P<subject>target creature(?: you control)?) this turn\. for each "
        r"1 damage prevented this way, put a (?P<counter>\+\d+/\+\d+) counter "
        r"on that creature\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        subject = match.group("subject").casefold()
        return (
            "damage-prevention-counter-aftermath-v1",
            (
                _shield(
                    amount=_amount_value(match.group("amount")),
                    subject=subject,
                    aftermath=[
                        {
                            "kind": "place_counters",
                            "subject": "$target.0",
                            "counter_name": match.group("counter"),
                            "placing_player": "$controller",
                            "per_prevented": 1,
                            "fixed_amount": 0,
                        }
                    ],
                ),
            ),
            _target_schema(subject),
            _rules(_target_schema(subject), "cr-122-counters"),
        )
    return None


def _shared_color_creatures(normalized: str) -> PreventionTemplate | None:
    match = re.fullmatch(
        r"prevent the next (?P<amount>\d+|x) damage that would be dealt to "
        r"target creature and each other creature that shares a color with it "
        r"this turn\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        return (
            "damage-prevention-shared-color-creatures-v1",
            (
                {
                    "op": "create_damage_prevention_shield",
                    "source": "$source",
                    "selector": {
                        "kind": "shares_color_with",
                        "anchor": "$target.0",
                        "types_all": ["creature"],
                    },
                    "mode": "amount",
                    "amount": _amount_value(match.group("amount")),
                    "duration": "until_end_of_turn",
                },
            ),
            _target_schema("target creature"),
            ("cr-615-prevention-effects", "cr-115-targets"),
        )
    return None


def _ordinary_shield(normalized: str) -> PreventionTemplate | None:
    match = re.fullmatch(
        r"prevent the next (?P<amount>\d+|x) damage that would be dealt to "
        rf"(?P<subject>{_SUBJECT_PATTERN}) "
        r"this turn\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        subject = match.group("subject").casefold()
        target_schema = _target_schema(subject)
        return (
            "damage-prevention-fixed-shield-v1",
            (
                {
                    "op": "create_damage_prevention_shield",
                    "source": "$source",
                    "subject": (
                        "$controller" if subject == "you" else "$target.0"
                    ),
                    "mode": "amount",
                    "amount": _amount_value(match.group("amount")),
                    "duration": "until_end_of_turn",
                },
            ),
            target_schema,
            _rules(target_schema),
        )
    return None


def _fixed_combat_shield(normalized: str) -> PreventionTemplate | None:
    match = re.fullmatch(
        r"prevent the next (?P<amount>\d+|x) combat damage that would be dealt to "
        rf"(?P<subject>{_SUBJECT_PATTERN}) this turn\.?",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    subject = match.group("subject").casefold()
    target_schema = _target_schema(subject)
    return (
        "damage-prevention-fixed-combat-shield-v1",
        (
            {
                "op": "create_damage_prevention_shield",
                "source": "$source",
                "subject": (
                    "$controller" if subject == "you" else "$target.0"
                ),
                "mode": "amount",
                "amount": _amount_value(match.group("amount")),
                "duration": "until_end_of_turn",
                "damage_kind": "combat",
            },
        ),
        target_schema,
        _rules(target_schema),
    )


def _all_combat_damage(normalized: str) -> PreventionTemplate | None:
    if re.fullmatch(
        r"prevent all combat damage that would be dealt this turn\.?",
        normalized,
        re.IGNORECASE,
    ):
        return (
            "damage-prevention-all-combat-v1",
            (
                {
                    "op": "create_damage_prevention_shield",
                    "source": "$source",
                    "subject": "*",
                    "mode": "all",
                    "duration": "until_end_of_turn",
                    "damage_kind": "combat",
                },
            ),
            None,
            _rules(None),
        )
    if re.fullmatch(
        r"prevent all combat damage that would be dealt to players this turn\.?",
        normalized,
        re.IGNORECASE,
    ):
        return (
            "damage-prevention-all-combat-to-players-v1",
            (
                {
                    "op": "create_damage_prevention_shield",
                    "source": "$source",
                    "subject": "*",
                    "mode": "all",
                    "duration": "until_end_of_turn",
                    "damage_kind": "combat",
                    "recipient_kind": "player",
                },
            ),
            None,
            _rules(None),
        )
    source_match = re.fullmatch(
        r"prevent all combat damage (?:(?:that would be dealt by )?target "
        r"creature(?: would deal)?) this turn\.?",
        normalized,
        re.IGNORECASE,
    )
    if source_match is not None:
        target_schema = _target_schema("target creature")
        return (
            "damage-prevention-all-combat-by-target-v1",
            (
                {
                    "op": "create_damage_prevention_shield",
                    "source": "$source",
                    "subject": "*",
                    "chosen_source": "$target.0",
                    "mode": "all",
                    "duration": "until_end_of_turn",
                    "damage_kind": "combat",
                },
            ),
            target_schema,
            _rules(target_schema),
        )
    target_match = re.fullmatch(
        r"prevent all combat damage that would be dealt to target creature "
        r"this turn\.?",
        normalized,
        re.IGNORECASE,
    )
    if target_match is not None:
        target_schema = _target_schema("target creature")
        return (
            "damage-prevention-all-combat-to-target-v1",
            (
                {
                    "op": "create_damage_prevention_shield",
                    "source": "$source",
                    "subject": "$target.0",
                    "mode": "all",
                    "duration": "until_end_of_turn",
                    "damage_kind": "combat",
                },
            ),
            target_schema,
            _rules(target_schema),
        )
    self_match = re.fullmatch(
        r"prevent all combat damage that would be dealt to and dealt by this "
        r"(?:artifact|creature|permanent) this turn\.?",
        normalized,
        re.IGNORECASE,
    )
    if self_match is None:
        return None
    common = {
        "op": "create_damage_prevention_shield",
        "source": "$source",
        "mode": "all",
        "duration": "until_end_of_turn",
        "damage_kind": "combat",
    }
    return (
        "damage-prevention-all-combat-to-from-self-v1",
        (
            {**common, "subject": "$source"},
            {**common, "subject": "*", "chosen_source": "$source"},
        ),
        None,
        _rules(None),
    )


def _ordinary_all_damage(normalized: str) -> PreventionTemplate | None:
    match = re.fullmatch(
        rf"prevent all damage that would be dealt to "
        rf"(?P<subject>{_SUBJECT_PATTERN}) this turn\.?",
        normalized,
        re.IGNORECASE,
    )
    if not match:
        return None
    subject = match.group("subject").casefold()
    target_schema = _target_schema(subject)
    return (
        "damage-prevention-all-shield-v1",
        (
            {
                "op": "create_damage_prevention_shield",
                "source": "$source",
                "subject": (
                    "$controller" if subject == "you" else "$target.0"
                ),
                "mode": "all",
                "duration": "until_end_of_turn",
            },
        ),
        target_schema,
        _rules(target_schema),
    )


def _self_all_damage(normalized: str) -> PreventionTemplate | None:
    match = re.fullmatch(
        r"prevent all damage that would be dealt to this "
        r"(?:artifact|creature|permanent) this turn\.?",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return (
        "damage-prevention-all-shield-self-v1",
        (
            {
                "op": "create_damage_prevention_shield",
                "source": "$source",
                "subject": "$source",
                "mode": "all",
                "duration": "until_end_of_turn",
            },
        ),
        None,
        _rules(None),
    )


def _self_shield(normalized: str) -> PreventionTemplate | None:
    match = re.fullmatch(
        r"prevent the next (?P<amount>\d+|x) damage that would be dealt to "
        r"(?:it|this (?:artifact|creature|permanent)) this turn\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        return (
            "damage-prevention-fixed-shield-self-v1",
            (
                {
                    "op": "create_damage_prevention_shield",
                    "source": "$source",
                    "subject": "$source",
                    "mode": "amount",
                    "amount": _amount_value(match.group("amount")),
                    "duration": "until_end_of_turn",
                },
            ),
            None,
            ("cr-615-prevention-effects",),
        )
    return None


def _source_shield(normalized: str) -> PreventionTemplate | None:
    match = re.fullmatch(
        r"prevent the next (?P<amount>\d+|x) damage that would be dealt by "
        r"this (?:artifact|creature|permanent) this turn\.?",
        normalized,
        re.IGNORECASE,
    )
    if match:
        return (
            "damage-prevention-fixed-shield-source-v1",
            (
                {
                    "op": "create_damage_prevention_shield",
                    "source": "$source",
                    "subject": "*",
                    "chosen_source": "$source",
                    "mode": "amount",
                    "amount": _amount_value(match.group("amount")),
                    "duration": "until_end_of_turn",
                },
            ),
            None,
            ("cr-615-prevention-effects",),
        )
    return None


_PREVENTION_PRODUCTIONS = (
    _chosen_source_fixed_life,
    _chosen_source_fixed_amount,
    _chosen_source_damage_aftermath,
    _chosen_source_next_instance,
    _chosen_source_all_damage,
    _scaled_life_aftermath,
    _counter_aftermath,
    _shared_color_creatures,
    _fixed_combat_shield,
    _all_combat_damage,
    _ordinary_shield,
    _ordinary_all_damage,
    _self_all_damage,
    _self_shield,
    _source_shield,
)


def fixed_prevention_effect_template(
    text: str,
    *,
    card_name: str | None = None,
) -> PreventionTemplate | None:
    """Lower one closed finite CR 615 sentence through ordered productions."""

    normalized = " ".join(text.strip().split())
    triggered = _chosen_source_triggered_damage_and_draw(
        normalized,
        card_name=card_name,
    )
    if triggered is not None:
        return triggered
    for production in _PREVENTION_PRODUCTIONS:
        result = production(normalized)
        if result is not None:
            return result
    if card_name:
        specs = fixed_all_damage_prevention_specs(
            normalized,
            card_name=card_name,
        )
        if specs is not None and all(
            spec.duration == "until_end_of_turn"
            and not spec.source_controller_turn_only
            and not spec.source.attached_identity
            and not spec.recipient.attached_identity
            for spec in specs
        ):
            target_schema = fixed_all_damage_prevention_target_schema(specs)
            return (
                "damage-prevention-fixed-all-scopes-v1",
                tuple(
                    {
                        "op": "create_damage_prevention_shield",
                        "source": "$source",
                        "subject": "*",
                        "mode": "all",
                        "duration": "until_end_of_turn",
                        "damage_kind": spec.damage_kind,
                        "scope": fixed_all_damage_prevention_scope_descriptor(
                            spec
                        ),
                        **(
                            {"application_group_id": "$stack"}
                            if spec.application_group is not None
                            else {}
                        ),
                    }
                    for spec in specs
                ),
                target_schema,
                _rules(target_schema),
            )
    return None


def prevention_trigger_effect_template(
    text: str,
    *,
    card_name: str | None = None,
) -> PreventionTriggerTemplate | None:
    """Lower closed CR 615.13 battlefield trigger wording."""

    normalized = " ".join(text.strip().split())
    source_reference = (
        rf"(?:this (?:artifact|creature|enchantment|permanent)|"
        rf"{SourceReferenceSpec(card_name).regex_pattern})"
        if card_name
        else r"this (?:artifact|creature|enchantment|permanent)"
    )
    match = re.fullmatch(
        r"whenever damage that would be dealt to you is prevented, put that "
        rf"many (?P<counter>\+\d+/\+\d+) counters? on {source_reference}\.?",
        normalized,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return (
        "damage-prevented-self-counter-trigger-v1",
        (
            {
                "op": "counter",
                "card": "$source",
                "counter": match.group("counter"),
                "delta": "$context.prevented_amount",
                "source": "$source",
            },
        ),
        None,
        ("cr-615-prevention-effects", "cr-122-counters"),
        {
            "field": "affected_players",
            "op": "contains_any",
            "value": ["$source.controller"],
        },
    )


def is_closed_fixed_prevention_program(
    program: SemanticProgram,
) -> bool:
    """Recognize one compiler-owned fixed damage-prevention instruction."""

    if "cr-615-prevention-effects" not in program.coverage:
        return False
    required = set(
        capability_dependencies_for_node(
            effects=program.effects,
            target_schema=program.target_schema,
            mechanic_ids=program.coverage,
            cost_schema=program.cost_schema,
        )
    )
    return (
        "damage.prevention.persistent_amount" in required
        and required.issubset(program.capability_dependencies)
    )


__all__ = [
    "fixed_prevention_effect_template",
    "is_closed_fixed_prevention_program",
    "prevention_trigger_effect_template",
]
