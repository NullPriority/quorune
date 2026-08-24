from __future__ import annotations

import re
from typing import Any, Mapping

from ..ability_fragments import (
    CombatKeywordTriggerKind,
    CombatKeywordTriggerSpec,
    SpellCastKeywordTriggerKind,
    SpellCastKeywordTriggerSpec,
    TOXIC_ABILITY_FRAGMENT_KIND,
    ToxicSpec,
    ability_fragment_to_dict,
    parse_protection_line,
)
from ..aura import parse_enchant_line
from ..cast_timing import CastTimingPermission, PRINTED_FLASH_MECHANIC
from ..renown import RENOWN_MECHANIC_ID, RenownSpec
from ..trigger_participation import WardSpec
from .ability_keyword_fragment_model import AbilityKeywordFragmentLowering


def _lower_fixed_generic_ward(
    material_line: str,
    mechanics: tuple[str, ...],
) -> AbilityKeywordFragmentLowering | None:
    if mechanics != ("ward",):
        return None
    match = re.fullmatch(
        r"Ward\s+\{(?P<generic>\d+)\}\.?",
        material_line.strip(),
        re.IGNORECASE,
    )
    if match is None:
        return AbilityKeywordFragmentLowering(
            residual_kind="unsupported_ward_cost",
            residual_reason=(
                "Ward cost is outside the closed fixed-generic grammar"
            ),
            residual_blockers=("fixed generic Ward cost",),
        )
    return AbilityKeywordFragmentLowering(
        handlers=(
            {
                "handler_id": "ability.trigger.ward.v1",
                "schema_version": 1,
                "event": "continuous",
                "fragment": ability_fragment_to_dict(
                    WardSpec(generic_cost=int(match.group("generic")))
                ),
            },
        )
    )


def _lower_toxic_fragment(
    material_line: str,
) -> AbilityKeywordFragmentLowering:
    matches = tuple(
        match
        for part in _keyword_parts(material_line)
        if (
            match := re.fullmatch(
                r"Toxic (?P<value>[1-9]\d*)",
                part,
                re.IGNORECASE,
            )
        )
        is not None
    )
    if len(matches) != 1:
        return AbilityKeywordFragmentLowering(
            residual_kind="unsupported_toxic_value",
            residual_reason="Toxic requires one printed positive integer value",
            residual_blockers=("positive integer Toxic value",),
        )
    return AbilityKeywordFragmentLowering(
        handlers=(
            {
                "handler_id": "ability.static.toxic.v1",
                "schema_version": 1,
                "event": "continuous",
                "fragment": ability_fragment_to_dict(
                    ToxicSpec(value=int(matches[0].group("value")))
                ),
            },
        )
    )


def _lower_cascade_fragment(
    material_line: str,
    mechanics: tuple[str, ...],
) -> AbilityKeywordFragmentLowering | None:
    if mechanics != ("cascade",):
        return None
    if material_line.strip().rstrip(".").casefold() != "cascade":
        return AbilityKeywordFragmentLowering(
            residual_kind="unsupported_cascade_variant",
            residual_reason=(
                "Cascade wording is outside the closed printed keyword grammar"
            ),
            residual_blockers=("ordinary printed Cascade",),
        )
    return AbilityKeywordFragmentLowering(
        handlers=(
            {
                "handler_id": "ability.trigger.cascade.v1",
                "schema_version": 1,
                "event": "spell.cast",
                "fragment": ability_fragment_to_dict(
                    SpellCastKeywordTriggerSpec(
                        kind=SpellCastKeywordTriggerKind.CASCADE,
                    )
                ),
            },
        )
    )


def _lower_storm_fragment(
    material_line: str,
    mechanics: tuple[str, ...],
) -> AbilityKeywordFragmentLowering | None:
    if mechanics != ("storm",):
        return None
    if material_line.strip().rstrip(".").casefold() != "storm":
        return AbilityKeywordFragmentLowering(
            residual_kind="unsupported_storm_variant",
            residual_reason=(
                "Storm wording is outside the closed printed keyword grammar"
            ),
            residual_blockers=("ordinary printed Storm",),
        )
    return AbilityKeywordFragmentLowering(
        handlers=(
            {
                "handler_id": "ability.trigger.storm.v1",
                "schema_version": 1,
                "event": "spell.cast",
                "fragment": ability_fragment_to_dict(
                    SpellCastKeywordTriggerSpec(
                        kind=SpellCastKeywordTriggerKind.STORM,
                    )
                ),
            },
        )
    )


def _lower_enchant_fragment(
    material_line: str,
) -> AbilityKeywordFragmentLowering:
    enchant_spec = parse_enchant_line(material_line)
    if enchant_spec is None:
        return AbilityKeywordFragmentLowering(
            residual_kind="unsupported_enchant_restriction",
            residual_reason=(
                "Enchant restriction is outside the closed typed grammar"
            ),
            residual_blockers=("typed Enchant restriction",),
        )
    return AbilityKeywordFragmentLowering(
        handlers=(
            {
                "handler_id": (
                    "ability.static.enchant.typed.v2"
                    if enchant_spec.schema_version == 2
                    else "ability.static.enchant.v1"
                ),
                "schema_version": 1,
                "event": "continuous",
                "fragment": ability_fragment_to_dict(enchant_spec),
            },
        )
    )


def lower_ability_keyword_fragments(
    material_line: str,
    mechanics: tuple[str, ...],
) -> AbilityKeywordFragmentLowering:
    """Lower closed keyword grammar to typed executable fragments."""

    if mechanics == (PRINTED_FLASH_MECHANIC,):
        return AbilityKeywordFragmentLowering(
            handlers=(
                {
                    "handler_id": "ability.static.flash.v1",
                    "schema_version": 1,
                    "event": "cast.permission",
                    "permission": CastTimingPermission().to_dict(),
                },
            )
        )

    if mechanics == ("enchant",):
        return _lower_enchant_fragment(material_line)
    if mechanics == (TOXIC_ABILITY_FRAGMENT_KIND,):
        return _lower_toxic_fragment(material_line)
    cascade = _lower_cascade_fragment(material_line, mechanics)
    if cascade is not None:
        return cascade
    storm = _lower_storm_fragment(material_line, mechanics)
    if storm is not None:
        return storm
    if mechanics == ("prowess",):
        matching_parts = tuple(
            part
            for part in _keyword_parts(material_line)
            if part.casefold() == "prowess"
        )
        if len(matching_parts) != 1:
            return AbilityKeywordFragmentLowering(
                residual_kind="unsupported_prowess_variant",
                residual_reason=(
                    "Prowess wording is outside the closed printed keyword grammar"
                ),
                residual_blockers=("ordinary printed Prowess",),
            )
        return AbilityKeywordFragmentLowering(
            handlers=(
                {
                    "handler_id": "ability.trigger.prowess.v1",
                    "schema_version": 1,
                    "event": "spell.cast",
                    "fragment": ability_fragment_to_dict(
                        SpellCastKeywordTriggerSpec(
                            kind=SpellCastKeywordTriggerKind.PROWESS,
                        )
                    ),
                },
            )
        )
    ward = _lower_fixed_generic_ward(material_line, mechanics)
    if ward is not None:
        return ward
    if mechanics == (RENOWN_MECHANIC_ID,):
        matches = tuple(
            match
            for part in _keyword_parts(material_line)
            if (
                match := re.fullmatch(
                    r"Renown (?P<amount>[1-9]\d*)",
                    part,
                    re.IGNORECASE,
                )
            )
            is not None
        )
        if len(matches) != 1:
            return AbilityKeywordFragmentLowering(
                residual_kind="unsupported_renown_value",
                residual_reason=(
                    "Renown requires one printed positive integer value"
                ),
                residual_blockers=("positive integer Renown value",),
            )
        return AbilityKeywordFragmentLowering(
            handlers=(
                RenownSpec(
                    amount=int(matches[0].group("amount"))
                ).handler_descriptor(),
            )
        )
    combat = _lower_combat_keyword_fragments(material_line, mechanics)
    if combat.residual_kind is not None:
        return combat
    handlers = list(combat.handlers)

    if "protection" in mechanics:
        protection_parts = tuple(
            part
            for part in _keyword_parts(material_line)
            if part.strip().casefold().startswith("protection from ")
        )
        parsed = tuple(
            parse_protection_line(part) for part in protection_parts
        )
        if (
            len(protection_parts) != mechanics.count("protection")
            or any(not specs for specs in parsed)
        ):
            return AbilityKeywordFragmentLowering(
                handlers=tuple(handlers),
                residual_kind="unsupported_protection_quality",
                residual_reason=(
                    "protection quality is outside the closed typed DEBT "
                    "grammar"
                ),
                residual_blockers=("typed protection quality",),
            )
        specs = tuple(
            spec
            for values in parsed
            for spec in (values or ())
        )
        handlers.extend(
            tuple(
                {
                    "handler_id": "ability.static.protection.v1",
                    "schema_version": 1,
                    "event": "continuous",
                    "fragment": ability_fragment_to_dict(spec),
                }
                for spec in specs
            )
        )
    return AbilityKeywordFragmentLowering(handlers=tuple(handlers))


def _keyword_parts(material_line: str) -> tuple[str, ...]:
    return tuple(
        part.strip()
        for part in re.split(r"[,;]", material_line.rstrip("."))
    )


def _lower_combat_keyword_fragments(
    material_line: str,
    mechanics: tuple[str, ...],
) -> AbilityKeywordFragmentLowering:
    handlers: list[Mapping[str, Any]] = []
    parts = _keyword_parts(material_line)
    flanking_parts = tuple(
        part for part in parts if part.casefold() == "flanking"
    )
    handlers.extend(
        {
            "handler_id": "ability.trigger.flanking.v1",
            "schema_version": 1,
            "event": "continuous",
            "fragment": ability_fragment_to_dict(
                CombatKeywordTriggerSpec(
                    kind=CombatKeywordTriggerKind.FLANKING,
                    amount=1,
                )
            ),
        }
        for _part in flanking_parts
    )

    bushido_matches = tuple(
        match
        for part in parts
        if (
            match := re.fullmatch(
                r"Bushido (?P<amount>[1-9]\d*)",
                part,
                re.IGNORECASE,
            )
        )
        is not None
    )
    handlers.extend(
        {
            "handler_id": "ability.trigger.bushido.v1",
            "schema_version": 1,
            "event": "continuous",
            "fragment": ability_fragment_to_dict(
                CombatKeywordTriggerSpec(
                    kind=CombatKeywordTriggerKind.BUSHIDO,
                    amount=int(match.group("amount")),
                )
            ),
        }
        for match in bushido_matches
    )
    ordinary_attack_keywords = (
        (
            "exalted",
            CombatKeywordTriggerKind.EXALTED,
            "ability.trigger.exalted.v1",
        ),
        (
            "battle cry",
            CombatKeywordTriggerKind.BATTLE_CRY,
            "ability.trigger.battle_cry.v1",
        ),
        (
            "melee",
            CombatKeywordTriggerKind.MELEE,
            "ability.trigger.melee.v1",
        ),
        (
            "mentor",
            CombatKeywordTriggerKind.MENTOR,
            "ability.trigger.mentor.v1",
        ),
        (
            "dethrone",
            CombatKeywordTriggerKind.DETHRONE,
            "ability.trigger.dethrone.v1",
        ),
        (
            "training",
            CombatKeywordTriggerKind.TRAINING,
            "ability.trigger.training.v1",
        ),
    )
    for mechanic, kind, handler_id in ordinary_attack_keywords:
        keyword = mechanic
        matching_parts = tuple(
            part for part in parts if part.casefold() == keyword
        )
        handlers.extend(
            {
                "handler_id": handler_id,
                "schema_version": 1,
                "event": "continuous",
                "fragment": ability_fragment_to_dict(
                    CombatKeywordTriggerSpec(kind=kind, amount=1)
                ),
            }
            for _part in matching_parts
        )
        if mechanics.count(mechanic) != len(matching_parts):
            return AbilityKeywordFragmentLowering(
                handlers=tuple(handlers),
                residual_kind=f"unsupported_{kind.value}_variant",
                residual_reason=(
                    f"{keyword.title()} wording is outside the closed "
                    "printed keyword grammar"
                ),
                residual_blockers=(f"ordinary printed {keyword.title()}",),
            )
    if mechanics.count("flanking") != len(flanking_parts):
        return AbilityKeywordFragmentLowering(
            handlers=tuple(handlers),
            residual_kind="unsupported_flanking_variant",
            residual_reason=(
                "Flanking wording is outside the closed printed keyword grammar"
            ),
            residual_blockers=("ordinary printed Flanking",),
        )
    if mechanics.count("bushido") != len(bushido_matches):
        return AbilityKeywordFragmentLowering(
            handlers=tuple(handlers),
            residual_kind="unsupported_bushido_value",
            residual_reason=(
                "Bushido requires one printed positive integer value"
            ),
            residual_blockers=("positive integer Bushido value",),
        )

    return AbilityKeywordFragmentLowering(handlers=tuple(handlers))


__all__ = [
    "AbilityKeywordFragmentLowering",
    "lower_ability_keyword_fragments",
]
