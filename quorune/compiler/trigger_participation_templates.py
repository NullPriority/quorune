from __future__ import annotations

import re
from typing import Any, Mapping

from ..ability_fragments import ability_fragment_to_dict
from ..trigger_participation import (
    TriggerMultiplierPredicate,
    TriggerMultiplierSpec,
)
from ..trigger_participation_query_model import (
    DUNGEON_ROOM_MULTIPLIER_CAPABILITY, DUNGEON_ROOM_MULTIPLIER_HANDLER, OWNED_DUNGEON_ROOM,
)
from ..rules.source_references import SourceReferenceSpec
from .creature_subtypes import canonical_creature_subtype
from .oracle_source_text import without_ability_word_label


_PANHARMONICON_PATTERN = re.compile(
    r"If an artifact or creature entering causes a triggered ability of a "
    r"permanent you control to trigger, that ability triggers an additional "
    r"time\.?",
    re.IGNORECASE,
)
_CHOSEN_TYPE_PATTERN = re.compile(
    r"If a triggered ability of another creature you control of the chosen "
    r"type triggers, it triggers an additional time\.?",
    re.IGNORECASE,
)


def static_trigger_multiplier_handler(
    text: str, *, source_name: str | None = None,
) -> tuple[str, Mapping[str, Any], str] | None:
    """Compile the closed represented CR 603.2d static wording families."""

    normalized = " ".join(str(text).strip().split())
    if _PANHARMONICON_PATTERN.fullmatch(normalized):
        spec = TriggerMultiplierSpec(
            predicate=TriggerMultiplierPredicate.ARTIFACT_OR_CREATURE_ENTERS,
            exclude_self=False,
        )
        template_id = "static-trigger-multiplier-artifact-creature-enters-v1"
    elif _CHOSEN_TYPE_PATTERN.fullmatch(normalized):
        spec = TriggerMultiplierSpec(
            predicate=(
                TriggerMultiplierPredicate.ANOTHER_CREATURE_OF_CHOSEN_TYPE
            ),
            exclude_self=True,
        )
        template_id = "static-trigger-multiplier-chosen-creature-type-v1"
    else:
        selected=_typed_participation(normalized,source_name)
        if selected is None:return None
        rule,subtype=selected
        spec=TriggerMultiplierSpec(TriggerMultiplierPredicate.TYPED_SOURCE_EVENT,schema_version=2,rule=rule,subtype=subtype)
        template_id='static-trigger-participation-typed-source-event-v2'
    handler_id = "ability.static.trigger-multiplier.v1"
    if spec.schema_version == 2:
        handler_id = {
            "trigger.participation.prohibition": "ability.static.trigger-prohibition.v2",
            DUNGEON_ROOM_MULTIPLIER_CAPABILITY: DUNGEON_ROOM_MULTIPLIER_HANDLER,
        }.get(spec.capability_id, "ability.static.trigger-participation.v2")
    return (
        template_id,
        {
            "handler_id": handler_id,
            "schema_version": 1,
            "event": "continuous",
            "fragment": ability_fragment_to_dict(spec),
        },
        spec.capability_id,
    )


def _typed_participation(text,source_name):
    text=without_ability_word_label(text)
    if text is None:return None
    text=text.rstrip('.')
    fixed={
        "Room abilities of dungeons you own trigger an additional time": OWNED_DUNGEON_ROOM,
        "If a permanent entering causes a triggered ability of a permanent you control to trigger, that ability triggers an additional time":'permanent_enters',
        "If a permanent entering the battlefield causes a triggered ability of a permanent you control to trigger, that ability triggers an additional time":'permanent_enters',
        "If a land entering causes a triggered ability of a permanent you control to trigger, that ability triggers an additional time":'land_enters',
        "If a creature dying causes a triggered ability of a permanent you control to trigger, that ability triggers an additional time":'creature_dies',
        "If a creature attacking causes a triggered ability of a permanent you control to trigger, that ability triggers an additional time":'creature_attacks',
        "If a creature you control attacking causes a triggered ability of a permanent you control to trigger, that ability triggers an additional time":'controlled_creature_attacks',
        "If a creature you control dealing combat damage to a player causes a triggered ability of a permanent you control to trigger, that ability triggers an additional time":'controlled_combat_damage_player',
        "If you casting or copying an instant or sorcery spell causes a triggered ability of a permanent you control to trigger, that ability triggers an additional time":'cast_or_copy_instant_sorcery',
        "If a legendary permanent or an artifact entering or leaving the battlefield causes a triggered ability of a permanent you control to trigger, that ability triggers an additional time":'legendary_or_artifact_enters_leaves',
        "If a triggered ability of a legendary creature you control triggers, that ability triggers an additional time":'legendary_creature_source',
        "If a triggered ability of a Ninja creature you control triggers, that ability triggers an additional time":'ninja_creature_source',
        "If a triggered ability of a Shaman or another Wizard you control triggers, that ability triggers an additional time":'shaman_or_another_wizard',
        "If a triggered ability of a colorless spell you control or another colorless permanent you control triggers, that ability triggers an additional time":'colorless_spell_or_another_permanent',
        "Creatures entering don't cause abilities to trigger":'prevent_creature_enters',
        "Artifacts and creatures entering don't cause abilities to trigger":'prevent_artifact_creature_enters',
        "Creatures entering or dying don't cause abilities to trigger":'prevent_creature_enters_dies',
        "Permanents entering don't cause abilities of permanents your opponents control to trigger":'prevent_opponent_permanent_enters',
    }
    for surface,rule in fixed.items():
        if text.casefold()==surface.casefold():return rule,None
    source=re.fullmatch(r'If a triggered ability of (?P<another>another |an? )(?P<subtype>[A-Za-z]+) you control triggers, (?:that ability|it) triggers an additional time',text,re.I)
    if source is not None:
        subtype=canonical_creature_subtype(source['subtype'])
        if subtype:return ('another_source_subtype' if source['another'].casefold()=='another ' else 'source_subtype'),subtype
    entering=re.fullmatch(r'If a (?P<subtype>[A-Za-z]+) you control entering(?: the battlefield)? causes a triggered ability of a permanent you control to trigger, that ability triggers an additional time',text,re.I)
    if entering is not None:
        subtype=canonical_creature_subtype(entering['subtype'])
        if subtype:return 'entering_subtype',subtype
    either=re.fullmatch(r'If a land or (?P<subtype>[A-Za-z]+) you control entering(?: the battlefield)? causes a triggered ability of a permanent you control to trigger, that ability triggers an additional time',text,re.I)
    if either is not None:
        subtype=canonical_creature_subtype(either['subtype'])
        if subtype:return 'controlled_entering_land_or_subtype',subtype
    attached=re.fullmatch(r'As long as (?P<first>.+?) is equipped, if a triggered ability of (?P<second>.+?) or an Equipment attached to it triggers, that ability triggers an additional time',text,re.I)
    if attached is not None and source_name:
        reference=SourceReferenceSpec(source_name)
        if reference.matches(attached['first']) and reference.matches(attached['second']):return 'equipped_source_or_attached_equipment',None
    return None


__all__ = ["static_trigger_multiplier_handler"]
