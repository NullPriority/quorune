from __future__ import annotations

"""Closed public occurrence grammar for fixed typed event-effect triggers."""

from . import regex_working_set as re
from .public_event_binding_spec import FixedPublicEventBindingSpec, public_event_binding as _spec
from typing import Any, Callable, Mapping

from ..creature_subtypes import canonical_creature_subtype
from ..dungeon_progress import (
    DUNGEON_COMPLETED_EVENT, DUNGEON_COMPLETION_TRIGGER_VARIANT, DUNGEON_VENTURE_CAPABILITY,
)
from ..life_change import FIRST_LIFE_GAIN_CAPABILITY, FIRST_LIFE_GAIN_EVENT
from ..face_up_effect_model import (
    FACE_DOWN_ENTRY_CAPABILITY, FACE_DOWN_ENTRY_FIELD,
    TURN_FACE_UP_ACTOR_CAPABILITY, TURN_FACE_UP_ACTOR_FIELD, TURN_FACE_UP_EVENT,
)
from ..class_levels import CLASS_MECHANIC_ID
from ..rules.source_references import SourceReferenceSpec
from .fixed_entry_return_requirements import (
    fixed_entry_return_requirement_spec,
)


def validate_public_trigger_metadata(binding):
    """Validate the public metadata carried by a fixed trigger binding."""
    if binding.public_mechanic is not None:
        if binding.spell_subject is not None:
            raise ValueError("Public event triggers cannot carry a parallel spell subject")
        if not binding.public_mechanic or not binding.public_template_id:
            raise ValueError("Public event triggers require typed owner identities")
        if binding.public_active_zone not in {None, "battlefield", "hand", "graveyard"}:
            raise ValueError("Public event triggers require a closed active zone")
    elif any(value is not None for value in (binding.public_condition, binding.public_active_zone, binding.public_template_id)):
        raise ValueError("Only public event triggers accept public predicate metadata")
    if not isinstance(binding.public_capabilities, tuple) or any(
        type(value) is not str or not value for value in binding.public_capabilities
    ):
        raise ValueError("Public event trigger capabilities must be nonempty strings")
    if len(set(binding.public_capabilities)) != len(binding.public_capabilities):
        raise ValueError("Public event trigger capabilities must be unique")
    if binding.public_mechanic is None and binding.public_capabilities:
        raise ValueError("Only public event triggers accept explicit capabilities")


_PUBLIC_DAMAGE_TRIGGER = re.compile(
    r"^Whenever (?P<subject>"
    r"a creature you control|an artifact creature you control|"
    r"a [A-Z][A-Za-z'-]* you control|"
    r"[A-Z][A-Za-z', -]+) "
    r"(?P<event>deals combat damage to a player|"
    r"deals combat damage to an opponent|deals damage to a player|"
    r"deals damage to an opponent), (?P<body>.+)$",
)

ANYWHERE_GRAVEYARD_VARIANT = 'public_anywhere_graveyard_card'
ANYWHERE_GRAVEYARD_CAPABILITY = 'trigger.event.normalized_anywhere_graveyard'
_ANYWHERE_GRAVEYARD = re.compile(
    r'^(?:When|Whenever) a(?:n)? (?P<quality>(?:(?:white|blue|black|red|green|colorless|'
    r'nonland|noncreature|artifact|battle|creature|enchantment|instant|kindred|land|planeswalker|sorcery) )?)'
    r'card is put into (?P<owner>your|an opponent\'s|a) graveyard from anywhere, (?P<body>.+)$', re.I,
)


def _anywhere_graveyard_spec(material_line):
    match = _ANYWHERE_GRAVEYARD.fullmatch(material_line)
    if match is None:
        return None
    quality = match['quality'].strip().casefold()
    conditions = [{'field': 'token', 'op': 'eq', 'value': False}]
    colors = {'white': 'W', 'blue': 'U', 'black': 'B', 'red': 'R', 'green': 'G'}
    if quality in colors:
        conditions.append({'field': 'colors', 'op': 'contains_any', 'value': [colors[quality]]})
    elif quality == 'colorless':
        conditions.append({'field': 'colors', 'op': 'eq', 'value': []})
    elif quality:
        condition = {'field': 'types', 'op': 'contains_any', 'value': [quality.removeprefix('non')]}
        conditions.append({'not': condition} if quality.startswith('non') else condition)
    if match['owner'].casefold() != 'a':
        conditions.append({'field': 'owner', 'op': 'eq' if match['owner'].casefold() == 'your' else 'ne',
                           'value': '$source.controller'})
    return _spec('card.enter_graveyard', ANYWHERE_GRAVEYARD_VARIANT, match['body'],
        'fixed-counter-public-anywhere-graveyard-trigger-v1', 'trigger-event-normalized-zone-change',
        condition={'all': conditions}, capabilities=(ANYWHERE_GRAVEYARD_CAPABILITY,))
_PUBLIC_ATTACK_TRIGGER = re.compile(
    r"^Whenever (?P<subject>this creature|this Vehicle|a creature|"
    r"a creature you control|a creature with flying) "
    r"attacks(?P<alone> alone)?(?: (?P<recipient>you(?: or a planeswalker "
    r"you control)?))?, (?P<body>.+)$",
    re.IGNORECASE,
)
_NAMED_SOURCE_ATTACK = re.compile(
    r"^(?:When|Whenever) (?P<source>.+?) attacks, (?P<body>.+)$",
    re.IGNORECASE,
)
_EACH_OPPONENT_TOP_EXILE_ATTACK_BODY = (
    "each opponent exiles the top card of their library."
)
_OPPONENT_LIBRARY_EXILE_PLAY_TRIGGER = re.compile(
    r"^Whenever a spell or ability you control causes one or more cards "
    r"to be exiled from an opponent['’]s library, (?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_BLOCK_TRIGGER = re.compile(
    r"^(?:When|Whenever) (?P<subject>this creature|a creature(?: you control(?: with defender)?)?) "
    r"(?P<event>blocks|becomes blocked)"
    r"(?P<blocked> a creature with flying| by a creature)?, (?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_CYCLE_TRIGGER = re.compile(
    r"^(?:(?P<source>When you cycle this card)|"
    r"(?P<another>Whenever you cycle another card)|"
    r"Whenever a player cycles a card), "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_FACE_UP_TRIGGER = re.compile(
    r"^Whenever (?:a permanent|this creature or another permanent) is turned face up, (?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_FACE_UP_TRIGGER = re.compile(
    r"^(?:When|Whenever) this "
    r"(?:artifact|creature|enchantment|Equipment|land|permanent) "
    r"is turned face up, (?P<body>.+)$",
    re.IGNORECASE,
)
_ANY_PLAYER_CARD_DRAW_DAMAGE = re.compile(
    r"Whenever a player draws a card, (?P<body>this enchantment deals 1 damage to that player\.)", re.I,
)
_OPPONENT_CARD_DRAW_TRIGGER = re.compile(
    r"^Whenever an opponent draws a card, (?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_SECOND_DRAW_TRIGGER = re.compile(
    r"^Whenever (?P<scope>an opponent|a player) draws their second card each turn, (?P<body>.+)$",
    re.IGNORECASE,
)
_HEROIC_TRIGGER = re.compile(
    r"^Whenever you cast a spell that targets (?P<subject>.+?), "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_MAGECRAFT_TRIGGER = re.compile(
    r"^Whenever you cast or copy an instant or sorcery spell, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_CONSTELLATION_TRIGGER = re.compile(
    r"^Whenever (?P<subject>.+?) enters, (?P<body>.+)$",
    re.IGNORECASE,
)
_BATTALION_TRIGGER = re.compile(
    r"^Whenever (?P<subject>.+?) and at least two other creatures attack, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_CLASS_LEVEL_TRIGGER = re.compile(
    r"^When this Class becomes level (?P<level>[23]), (?P<body>.+)$",
    re.IGNORECASE,
)
_ATTACHMENT_ATTACK_TRIGGER = re.compile(
    r"^(?:When|Whenever) (?P<relation>enchanted|equipped) creature attacks"
    r"(?P<alone> alone)?, (?P<body>.+)$",
    re.IGNORECASE,
)
_ATTACHMENT_DAMAGE_TRIGGER = re.compile(
    r"^Whenever (?P<relation>enchanted|equipped) creature deals "
    r"(?P<combat>combat )?damage(?: to (?P<recipient>a player|an opponent))?, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_ATTACHMENT_DEATH_TRIGGER = re.compile(
    r"^(?:When enchanted|Whenever equipped) creature dies, (?P<body>.+)$",
    re.IGNORECASE,
)
_CONTROLLED_ENCHANTMENT_GRAVEYARD_TRIGGER = re.compile(
    r"^Whenever an enchantment you control is put into a graveyard from the "
    r"battlefield, (?P<body>.+)$",
    re.IGNORECASE,
)
_SMALL_CREATURE_ENTRY_TRIGGER = re.compile(
    r"^Whenever another creature you control with power (?P<power>[1-9][0-9]*) "
    r"or less enters, (?P<body>.+)$",
    re.IGNORECASE,
)
_COLORLESS_CREATURE_ENTRY_TRIGGER = re.compile(
    r"^Whenever another colorless creature you control enters, (?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_OR_CONTROLLED_DEATH_TRIGGER = re.compile(
    r"^Whenever this creature or another creature or artifact you control "
    r"dies, (?P<body>.+)$",
    re.IGNORECASE,
)
_CREATURE_CARD_LEAVES_GRAVEYARD_TRIGGER = re.compile(
    r"^Whenever one or more creature cards leave your graveyard, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)


def _all_conditions(
    *conditions: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    values = [dict(value) for value in conditions if value is not None]
    if not values:
        return None
    return values[0] if len(values) == 1 else {"all": values}


def _entry_return_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    requirement = fixed_entry_return_requirement_spec(material_line)
    if requirement is None or ", " not in material_line:
        return None
    condition: Mapping[str, Any]
    if requirement.event_predicate is None:
        condition = {"field": "card", "op": "eq", "value": "$source.ref"}
    else:
        query = requirement.event_predicate
        values: list[Mapping[str, Any]] = []
        if query.controller == "$actor":
            values.append(
                {
                    "field": "controller",
                    "op": "eq",
                    "value": "$source.controller",
                }
            )
        if query.types_all:
            values.append(
                {
                    "field": "types",
                    "op": "contains_any",
                    "value": list(query.types_all),
                }
            )
        if query.subtypes_all:
            values.append(
                {
                    "field": "subtypes",
                    "op": "contains_any",
                    "value": list(query.subtypes_all),
                }
            )
        if query.exclude_ref == "$source":
            values.append(
                {"field": "card", "op": "ne", "value": "$source.ref"}
            )
        condition = values[0] if len(values) == 1 else {"all": values}
    return _spec(
        "permanent.enter",
        "fixed_entry_return_requirement",
        material_line.split(", ", 1)[1],
        "fixed-counter-entry-return-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=condition,
    )


def _class_level_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    matched = _CLASS_LEVEL_TRIGGER.fullmatch(material_line)
    if matched is None:
        return None
    level = int(matched.group("level"))
    return _spec(
        "permanent.class_level_changed.self",
        "class_level_changed",
        matched.group("body"),
        "class-level-change-trigger-v1",
        CLASS_MECHANIC_ID,
        condition={"field": "level", "op": "eq", "value": level},
    )


def _source_subject(subject: str, *, card_name: str | None) -> bool:
    normalized = " ".join(subject.casefold().split())
    return normalized == "this creature" or bool(
        card_name and SourceReferenceSpec(card_name).matches(subject)
    )


_LINKED_PLAYER_CAST_MILL_VARIANT = "linked_player_cast_mill"
_LINKED_PLAYER_CAST_MILL = re.compile(
    r"Whenever the chosen player casts a spell, (?P<body>that player mills a card\.)",
    re.I,
)


def linked_player_cast_mill_result(binding, body):
    if binding.variant != _LINKED_PLAYER_CAST_MILL_VARIANT or body != binding.body:
        return None
    from ..entry_designations import LINKED_PLAYER_CAST_MILL_TEMPLATE, linked_player_cast_mill_effect
    from .mill_templates import EVENT_MILL_RECIPIENT_MECHANIC, MILL_MECHANIC_ID

    return (
        (LINKED_PLAYER_CAST_MILL_TEMPLATE, (linked_player_cast_mill_effect(),), None,
         (MILL_MECHANIC_ID, EVENT_MILL_RECIPIENT_MECHANIC)), True,
    )


def _public_spell_action_spec(
    material_line: str,
    *,
    card_name: str | None,
) -> FixedPublicEventBindingSpec | None:
    from .spell_cast_predicates import source_power_cast_binding_spec

    if source_power := source_power_cast_binding_spec(material_line, card_name=card_name):
        return source_power
    linked = _LINKED_PLAYER_CAST_MILL.fullmatch(material_line)
    if linked is not None:
        from ..entry_designations import ENTRY_DESIGNATION_CAPABILITY, LINKED_PLAYER_CAST_MILL_TEMPLATE, linked_player_cast_condition

        return _spec(
            "spell.cast", _LINKED_PLAYER_CAST_MILL_VARIANT, linked["body"], LINKED_PLAYER_CAST_MILL_TEMPLATE,
            "trigger-event-normalized-spell-cast", condition=linked_player_cast_condition(),
            capabilities=(ENTRY_DESIGNATION_CAPABILITY, "trigger.event.normalized_spell_cast"),
        )
    heroic = _HEROIC_TRIGGER.fullmatch(material_line)
    if heroic is not None and _source_subject(
        heroic.group("subject"),
        card_name=card_name,
    ):
        return _spec(
            "spell.cast",
            "heroic_source_targeted",
            heroic.group("body"),
            "fixed-counter-heroic-spell-cast-trigger-v1",
            "trigger-event-normalized-spell-cast",
            condition=_all_conditions(
                {
                    "field": "controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
                {
                    "field": "targets",
                    "op": "contains_any",
                    "value": ["$source.ref"],
                },
            ),
        )
    magecraft = _MAGECRAFT_TRIGGER.fullmatch(material_line)
    if magecraft is not None:
        return _spec(
            "spell.cast_or_copy",
            "magecraft_instant_or_sorcery",
            magecraft.group("body"),
            "fixed-counter-magecraft-spell-action-trigger-v1",
            "trigger-event-normalized-spell-action",
            condition=_all_conditions(
                {
                    "field": "controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
                {
                    "field": "types",
                    "op": "contains_any",
                    "value": ["instant", "sorcery"],
                },
            ),
        )
    return None


def _source_or_controlled_toughness_greater_than_power_entry_spec(material_line, *, card_name):
    match = re.fullmatch(
        r"Whenever (?P<source>.+?) or another creature you control with toughness greater than its power enters, "
        r"(?P<body>.+)",
        material_line, re.IGNORECASE,
    )
    if match is None or not _source_subject(match["source"], card_name=card_name):
        return None
    return _spec(
        "permanent.enter", "source_or_controlled_toughness_greater_than_power_entry", match["body"],
        "fixed-counter-permanent-entry-trigger-v1", "trigger-event-normalized-zone-change",
        condition={"any": [
            {"field": "card", "op": "eq", "value": "$source.ref"},
            {"all": [
                {"field": "controller", "op": "eq", "value": "$source.controller"},
                {"field": "types", "op": "contains_any", "value": ["creature"]},
                {"field": "power", "op": "ne", "value": None},
                {"field": "toughness", "op": "gt", "value": "$context.power"},
            ]},
        ]},
    )


def _constellation_spec(
    material_line: str,
    *,
    card_name: str | None,
) -> FixedPublicEventBindingSpec | None:
    match = _CONSTELLATION_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    subject = " ".join(match.group("subject").casefold().split())
    ordinary = subject == "an enchantment you control"
    source_or_other = False
    suffix = " or another enchantment you control"
    if subject.endswith(suffix):
        source_subject = subject[: -len(suffix)]
        source_or_other = source_subject in {
            "this creature",
            "this enchantment",
        } or bool(
            card_name and SourceReferenceSpec(card_name).matches(source_subject)
        )
    if not ordinary and not source_or_other:
        return None
    controlled_enchantment = _all_conditions(
        {
            "field": "controller",
            "op": "eq",
            "value": "$source.controller",
        },
        {
            "field": "types",
            "op": "contains_any",
            "value": ["enchantment"],
        },
    )
    return _spec(
        "permanent.enter",
        (
            "constellation_source_or_controlled_enchantment"
            if source_or_other
            else "constellation_controlled_enchantment"
        ),
        match.group("body"),
        "fixed-counter-constellation-entry-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=(
            {
                "any": [
                    {
                        "field": "card",
                        "op": "eq",
                        "value": "$source.ref",
                    },
                    controlled_enchantment,
                ]
            }
            if source_or_other
            else controlled_enchantment
        ),
    )


def _battalion_spec(
    material_line: str,
    *,
    card_name: str | None,
) -> FixedPublicEventBindingSpec | None:
    match = _BATTALION_TRIGGER.fullmatch(material_line)
    if match is None or not _source_subject(
        match.group("subject"),
        card_name=card_name,
    ):
        return None
    return _spec(
        "creature.attacks",
        "battalion_source_and_two_others_attack",
        match.group("body"),
        "fixed-counter-battalion-attack-trigger-v1",
        "trigger-event-normalized-public-action",
        condition=_all_conditions(
            {"field": "card", "op": "eq", "value": "$source.ref"},
            {"field": "attacker_count", "op": "gte", "value": 3},
        ),
    )


def _artifact_graveyard_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    match = re.fullmatch(
        r"Whenever an artifact(?P<controller> an opponent controls)? is put into "
        r"(?P<owner>an opponent's|a) graveyard from the battlefield, "
        r"(?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if match is None:
        return None
    if match.group("controller"):
        if match.group("owner").casefold() != "a":
            return None
        return _spec(
            "artifact.graveyard", "opponent_controlled_artifact_graveyard", match.group("body"),
            "fixed-counter-public-zone-trigger-v1", "trigger-event-normalized-zone-change",
            condition={"field": "previous_controller", "op": "ne", "value": "$source.controller"},
        )
    opponent = match.group("owner").casefold().startswith("an opponent")
    return _spec(
        "artifact.graveyard",
        "artifact_opponent_graveyard" if opponent else "artifact_any_graveyard",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=(
            {"field": "owner", "op": "ne", "value": "$source.controller"}
            if opponent
            else None
        ),
    )


_CREATURE_GRAVEYARD_OWNER = re.compile(
    r"(?:When|Whenever) a (?P<nontoken>nontoken )?creature is put into "
    r"(?P<owner>your|an opponent's) graveyard from the battlefield, (?P<body>.+)",
    re.IGNORECASE,
)


def creature_graveyard_owner_spec(
    material_line: str, *, active_zone: str = "battlefield",
) -> FixedPublicEventBindingSpec | None:
    match = _CREATURE_GRAVEYARD_OWNER.fullmatch(material_line)
    if match is None:
        return None
    owner = {
        "field": "owner",
        "op": "eq" if match["owner"].casefold() == "your" else "ne",
        "value": "$source.owner" if active_zone == "graveyard" else "$source.controller",
    }
    nontoken = {"field": "token", "op": "eq", "value": False} if match["nontoken"] else None
    return _spec(
        "creature.dies", "creature_graveyard_owner", match["body"],
        "fixed-counter-public-zone-trigger-v1", "trigger-event-normalized-zone-change",
        condition=_all_conditions(owner, nontoken), active_zone=active_zone,
    )


def _land_entry_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    self_or_other = re.fullmatch(
        r"Whenever this land or another land you control enters, (?P<body>.+)",
        material_line, re.IGNORECASE,
    )
    if self_or_other is not None:
        from ..rules.distinct_land_entry import source_or_controlled_land_entry

        return _spec(
            "permanent.enter", "source_or_controlled_land_entry", self_or_other["body"],
            "fixed-counter-public-zone-trigger-v1", "trigger-event-normalized-zone-change",
            condition=source_or_controlled_land_entry(),
        )
    putting = re.fullmatch(
        r"Whenever a player puts a (?P<subtype>Forest|Swamp) onto the battlefield, (?P<body>.+)",
        material_line, re.IGNORECASE,
    )
    if putting is not None:
        from ..rules.entry_player_counter_payment import entry_player_counter_payment_condition

        subtype = putting["subtype"].casefold()
        return _spec(
            "land.enter", "putting_player_" + subtype, putting["body"],
            "fixed-counter-public-zone-trigger-v1", "trigger-event-normalized-zone-change",
            condition=entry_player_counter_payment_condition(subtype),
        )
    match = re.fullmatch(
        r"Whenever a land(?: (?P<relation>an opponent controls))? enters, "
        r"(?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if match is None:
        return None
    opponent = match.group("relation") is not None
    return _spec(
        "land.enter",
        "opponent_land" if opponent else "any_land",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=(
            {"field": "controller", "op": "ne", "value": "$source.controller"}
            if opponent
            else {"field": "types", "op": "contains_any", "value": ["land"]}
        ),
    )


def _power_entry_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    match = re.fullmatch(
        r"Whenever (?P<include>this creature or )?"
        r"(?P<article>another|a) creature you control with power "
        r"(?P<power>[1-9][0-9]*) or greater (?P<event>enters|dies), "
        r"(?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if match is None:
        return None
    event = match.group("event").casefold()
    exclude_source = (
        match.group("article").casefold() == "another"
        and match.group("include") is None
    )
    return _spec(
        "creature.dies" if event == "dies" else "creature.enter",
        f"controlled_creature_power_gte_{match.group('power')}_{event}",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=_all_conditions(
            {"field": "controller", "op": "eq", "value": "$source.controller"},
            {"field": "power", "op": "gte", "value": int(match.group("power"))},
            {"field": "card", "op": "ne", "value": "$source.ref"}
            if exclude_source
            else None,
        ),
    )


def _controlled_artifact_departure_return_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    from ..rules.artifact_graveyard_departure import (
        CAPABILITY, EVENT, VARIANT, departure_artifact_return_condition,
    )

    match = re.fullmatch(
        r"Whenever this creature dies or another artifact you control is put into a graveyard from the battlefield, "
        r"(?P<body>return to your hand target artifact card in your graveyard with lesser mana value[.])",
        material_line,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return _spec(
        EVENT, VARIANT, match["body"], "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change", condition=departure_artifact_return_condition(),
        capabilities=(CAPABILITY,),
    )


def _source_or_artifact_death_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    match = re.fullmatch(
        r"Whenever this creature or another artifact creature dies, "
        r"(?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return _spec(
        "creature.dies",
        "source_or_artifact_creature_dies",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition={
            "any": [
                {"field": "card", "op": "eq", "value": "$source.ref"},
                {"field": "types", "op": "contains_any", "value": ["artifact"]},
            ]
        },
    )


def _subtype_graveyard_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    match = re.fullmatch(
        r"Whenever (?P<article>another|a) (?P<token>nontoken )?"
        r"(?P<subtype>[A-Z][A-Za-z'-]*) "
        r"(?:(?P<control>you control) )?"
        r"(?P<verb>dies|is put into (?:your|a) graveyard from the battlefield), "
        r"(?P<body>.+)",
        material_line,
    )
    if match is None:
        return None
    subtype = canonical_creature_subtype(match.group("subtype"))
    if subtype is None:
        return None
    dies = match.group("verb").casefold() == "dies"
    return _spec(
        "creature.dies" if dies else "permanent.graveyard",
        f"subtype_{subtype}_{'dies' if dies else 'graveyard'}",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=_all_conditions(
            {"field": "subtypes", "op": "contains_any", "value": [subtype]},
            {
                "field": "previous_controller",
                "op": "eq",
                "value": "$source.controller",
            }
            if match.group("control") is not None
            else None,
            {"field": "owner", "op": "eq", "value": "$source.controller"}
            if "your graveyard" in match.group("verb").casefold()
            else None,
            {"field": "token", "op": "falsy", "value": True}
            if match.group("token") is not None
            else None,
            {"field": "card", "op": "ne", "value": "$source.ref"}
            if match.group("article").casefold() == "another"
            else None,
        ),
    )


_NAMED_SOURCE_GRAVEYARD = re.compile(
    r"When (?s:.*?) is put into a graveyard from the battlefield, .+", re.IGNORECASE
)


def _named_source_graveyard_spec(
    material_line: str,
    *,
    card_name: str | None,
) -> FixedPublicEventBindingSpec | None:
    if not card_name or _NAMED_SOURCE_GRAVEYARD.fullmatch(material_line) is None:
        return None
    match = re.fullmatch(
        rf"When {re.escape(card_name)} is put into a graveyard from the "
        r"battlefield, (?P<body>.+)",
        material_line,
        re.IGNORECASE,
    )
    if match is None:
        return None
    return _spec(
        "permanent.graveyard.self",
        "named_source_graveyard",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
    )


def _attachment_attack_or_damage_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    attack = _ATTACHMENT_ATTACK_TRIGGER.fullmatch(material_line)
    if attack is not None:
        return _spec(
            "creature.attacks",
            f"{attack['relation'].casefold()}_creature_attacks",
            attack.group("body"),
            "fixed-counter-public-attack-trigger-v1",
            "trigger-event-normalized-public-action",
            condition=_all_conditions(
                {"field": "source_attachment_target_ref", "op": "eq", "value": "$context.card"},
                {"field": "attacking_alone", "op": "truthy", "value": True} if attack["alone"] else None,
            ),
            capabilities=("attachment.reference.current_or_lki",),
        )
    damage = _ATTACHMENT_DAMAGE_TRIGGER.fullmatch(material_line)
    if damage is not None:
        recipient = (damage.group("recipient") or "").casefold()
        relation = damage.group("relation").casefold()
        combat = damage.group("combat") is not None
        return _spec(
            "damage.dealt",
            f"{relation}_creature_{'combat_' if combat else ''}"
            f"damage{('_' + recipient.replace(' ', '_')) if recipient else ''}",
            damage.group("body"),
            "fixed-counter-public-damage-trigger-v1",
            "trigger-event-normalized-damage",
            condition=_all_conditions(
                {
                    "field": "source_attachment_target_ref",
                    "op": "eq",
                    "value": "$context.source",
                },
                {"field": "target_kind", "op": "eq", "value": "player"}
                if recipient
                else None,
                {"field": "combat", "op": "truthy", "value": True}
                if combat
                else None,
                {
                    "field": "target",
                    "op": "ne",
                    "value": "$source.controller",
                }
                if recipient == "an opponent"
                else None,
            ),
            capabilities=("attachment.reference.current_or_lki",),
        )
    return None


def _attachment_death_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    from .enchanted_departure_return_templates import enchanted_departure_return_binding

    if linked := enchanted_departure_return_binding(material_line):
        return linked
    graveyard=re.fullmatch(r'(?:When|Whenever) enchanted (?P<type>artifact|creature|enchantment|land|permanent) is put into a graveyard(?: from the battlefield)?, (?P<body>.+)',material_line,re.I)
    if graveyard is None:
        graveyard = re.fullmatch(r'(?:When|Whenever) enchanted (?P<type>land) dies, (?P<body>.+)', material_line, re.I)
    if graveyard is not None:
        kind=graveyard['type'].casefold()
        return _spec('permanent.graveyard','enchanted_permanent_graveyard',graveyard['body'],
            'fixed-counter-public-zone-trigger-v1','trigger-event-normalized-zone-change',
            condition=_all_conditions({'field':'attachments','op':'contains_any','value':['$source.ref']},
                None if kind=='permanent' else {'field':'types','op':'contains_any','value':[kind]}),
            capabilities=('attachment.reference.current_or_lki',))
    death = _ATTACHMENT_DEATH_TRIGGER.fullmatch(material_line)
    if death is None:
        return None
    relation = (
        "enchanted"
        if material_line.casefold().startswith("when enchanted")
        else "equipped"
    )
    return _spec(
        "creature.dies",
        f"{relation}_creature_dies",
        death.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition={
            "field": "attachments",
            "op": "contains_any",
            "value": ["$source.ref"],
        },
        capabilities=("attachment.reference.current_or_lki",),
    )


def _controlled_zone_departure_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    controlled_enchantment = _CONTROLLED_ENCHANTMENT_GRAVEYARD_TRIGGER.fullmatch(
        material_line
    )
    if controlled_enchantment is not None:
        return _spec(
            "permanent.graveyard",
            "controlled_enchantment_graveyard",
            controlled_enchantment.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=_all_conditions(
                {"field": "types", "op": "contains_any", "value": ["enchantment"]},
                {
                    "field": "previous_controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
            ),
        )
    source_or_other = _SOURCE_OR_CONTROLLED_DEATH_TRIGGER.fullmatch(material_line)
    if source_or_other is None:
        return None
    return _spec(
        "permanent.graveyard",
        "source_or_controlled_creature_or_artifact_dies",
        source_or_other.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=_all_conditions(
            {
                "field": "previous_controller",
                "op": "eq",
                "value": "$source.controller",
            },
            {
                "field": "types",
                "op": "contains_any",
                "value": ["creature", "artifact"],
            },
        ),
    )


def _controlled_entry_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    small = _SMALL_CREATURE_ENTRY_TRIGGER.fullmatch(material_line)
    if small is not None:
        return _spec(
            "creature.enter",
            "another_controlled_creature_power_at_most",
            small.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=_all_conditions(
                {"field": "controller", "op": "eq", "value": "$source.controller"},
                {"field": "card", "op": "ne", "value": "$source.ref"},
                {"field": "power", "op": "lte", "value": int(small.group("power"))},
            ),
        )
    colorless = _COLORLESS_CREATURE_ENTRY_TRIGGER.fullmatch(material_line)
    if colorless is not None:
        return _spec(
            "creature.enter",
            "another_controlled_colorless_creature_enters",
            colorless.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=_all_conditions(
                {"field": "controller", "op": "eq", "value": "$source.controller"},
                {"field": "card", "op": "ne", "value": "$source.ref"},
                {"field": "colors", "op": "eq", "value": []},
            ),
        )
    return None


def _graveyard_departure_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    graveyard_leave = _CREATURE_CARD_LEAVES_GRAVEYARD_TRIGGER.fullmatch(
        material_line
    )
    if graveyard_leave is None:
        return None
    return _spec(
        "card.leave_graveyard",
        "one_or_more_controller_creature_cards_leave_graveyard",
        graveyard_leave.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=_all_conditions(
            {"field": "owner", "op": "eq", "value": "$source.controller"},
            {"field": "types", "op": "contains_any", "value": ["creature"]},
        ),
    )


def _attachment_event_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    return _attachment_attack_or_damage_spec(
        material_line
    ) or _attachment_death_spec(material_line)


def _other_nontoken_creature_entry_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    from ..token_copy_recipient_model import OTHER_NONTOKEN_CREATURE_ENTRY, other_nontoken_creature_entry_condition

    match = re.fullmatch(r"Whenever another nontoken creature enters, (?P<body>.+)", material_line, re.I)
    if match is None:
        return None
    return _spec(
        "creature.enter", OTHER_NONTOKEN_CREATURE_ENTRY, match["body"],
        "fixed-counter-public-zone-trigger-v1", "trigger-event-normalized-zone-change",
        condition=other_nontoken_creature_entry_condition(),
    )


def _closed_public_zone_event_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    return (
        _controlled_zone_departure_spec(material_line)
        or _controlled_entry_spec(material_line)
        or _other_nontoken_creature_entry_spec(material_line)
        or _graveyard_departure_spec(material_line)
    )


def _public_zone_spec(
    material_line: str,
    *,
    card_name: str | None,
) -> FixedPublicEventBindingSpec | None:
    entry_return = _entry_return_spec(material_line)
    if entry_return is not None:
        return entry_return
    from .qualified_entry_effects import qualified_entry_result_binding

    closed = qualified_entry_result_binding(material_line, spec=_spec) or _closed_public_zone_event_spec(material_line)
    if closed is not None:
        return closed
    parsers: tuple[Callable[[str], FixedPublicEventBindingSpec | None], ...] = (
        _artifact_graveyard_spec,
        creature_graveyard_owner_spec,
        _land_entry_spec,
        _power_entry_spec,
        _controlled_artifact_departure_return_spec,
        _source_or_artifact_death_spec,
        _subtype_graveyard_spec,
    )
    for parser in parsers:
        spec = parser(material_line)
        if spec is not None:
            return spec
    return _named_source_graveyard_spec(material_line, card_name=card_name)


def source_damage_incarnation_condition() -> Mapping[str, Any]:
    """CR201.5/400.7: damage must come from this source incarnation."""
    return {'field':'source_logical_object_id','op':'eq','value':'$source.logical_object_id'}


def _damage_subject_condition(
    subject: str,
    *,
    card_name: str | None,
) -> Mapping[str, Any] | None:
    controller = {"field": "source_controller", "op": "eq", "value": "$source.controller"}
    if subject in {"a creature you control", "an artifact creature you control"}:
        return _all_conditions(
            controller,
            {"field": "source_types", "op": "contains_any", "value": ["artifact"]}
            if subject.startswith("an artifact")
            else None,
        )
    if subject.startswith("a ") and subject.endswith(" you control"):
        subtype = canonical_creature_subtype(
            subject.removeprefix("a ").removesuffix(" you control")
        )
        if subtype is None:
            return None
        return _all_conditions(
            controller,
            {"field": "source_subtypes", "op": "contains_any", "value": [subtype]},
        )
    if card_name and subject == " ".join(
        card_name.split(",", 1)[0].casefold().split()
    ):
        return _all_conditions({'field':'source','op':'eq','value':'$source.ref'},
            source_damage_incarnation_condition())
    return None


def _public_damage_spec(
    material_line: str,
    *,
    card_name: str | None,
) -> FixedPublicEventBindingSpec | None:
    from .controlled_subtype_damage_batch import controlled_subtype_damage_binding_parts

    if grouped := controlled_subtype_damage_binding_parts(material_line):
        event, variant, body, template, mechanic, condition = grouped
        return _spec(event, variant, body, template, mechanic, condition=condition)
    from .subtype_damage_controller import subtype_damage_binding_parts

    if subtype := subtype_damage_binding_parts(material_line):
        variant, body, condition = subtype
        return _spec("damage.dealt", variant, body, "fixed-counter-public-damage-trigger-v1",
                     "trigger-event-normalized-damage", condition=condition)
    dealer = re.fullmatch(
        r"Whenever a creature deals combat damage to one of your opponents, (?P<body>.+)",
        material_line, re.I,
    )
    if dealer is not None:
        from ..rules.event_optional_draw_shapes import DAMAGE_DEALER_DRAW_VARIANT, damage_dealer_draw_condition

        return _spec(
            "damage.dealt", DAMAGE_DEALER_DRAW_VARIANT, dealer["body"],
            "fixed-counter-public-damage-trigger-v1", "trigger-event-normalized-damage",
            condition=damage_dealer_draw_condition(),
        )
    match = _PUBLIC_DAMAGE_TRIGGER.fullmatch(material_line)
    if match is None:
        from .damage_permanent_event_bindings import damage_permanent_binding_spec
        return damage_permanent_binding_spec(material_line,card_name=card_name)
    subject = " ".join(match.group("subject").casefold().split())
    subject_condition = _damage_subject_condition(subject, card_name=card_name)
    if subject_condition is None:
        return None
    event = match.group("event").casefold()
    return _spec(
        "damage.dealt",
        "public_" + event.replace(" ", "_"),
        match.group("body"),
        "fixed-counter-public-damage-trigger-v1",
        "trigger-event-normalized-damage",
        condition=_all_conditions(
            subject_condition,
            {"field": "target_kind", "op": "eq", "value": "player"},
            {"field": "combat", "op": "truthy", "value": True}
            if "combat damage" in event
            else None,
            {"field": "target", "op": "ne", "value": "$source.controller"}
            if event.endswith("opponent")
            else None,
        ),
    )


def attack_food_stats_body_matches(body: str) -> bool:
    normalized = " ".join(body.casefold().split()).replace(
        "a food entered the battlefield under your control",
        "a food entered under your control",
    )
    return normalized == (
        "attacking creatures get +1/+1 until end of turn. if a food entered under your control this turn, "
        "untap those creatures and they get an additional +2/+2 until end of turn."
    )


def _public_attack_spec(material_line: str, *, card_name: str | None = None) -> FixedPublicEventBindingSpec | None:
    from .affected_player_sacrifice_templates import attack_sacrifice_stat_binding_spec

    if sacrifice := attack_sacrifice_stat_binding_spec(material_line, card_name=card_name):
        return sacrifice
    from ..qualified_attack_model import (
        QUALIFIED_ATTACK_CAPABILITY, QUALIFIED_ATTACK_VARIANT,
        defended_player_condition, defended_player_or_planeswalker_condition, source_attacks_battle_condition,
    )

    battle = re.fullmatch(r"(?:When|Whenever) this creature attacks a battle, (?P<body>.+)", material_line, re.I)
    if battle is not None:
        return _spec("creature.attacks", QUALIFIED_ATTACK_VARIANT, battle["body"],
            "fixed-counter-public-attack-trigger-v1", "trigger-event-normalized-public-action",
            condition=source_attacks_battle_condition(), capabilities=(QUALIFIED_ATTACK_CAPABILITY,))
    saddled = re.fullmatch(r"(?:When|Whenever) (?P<source>.+?) attacks while saddled, (?P<body>.+)",
        material_line, re.I)
    if saddled is not None and _source_subject(saddled["source"], card_name=card_name):
        from ..saddle import SADDLED_ATTACK_CAPABILITY, saddled_attack_condition
        return _spec("creature.attacks", "source_attacks", saddled["body"],
            "fixed-counter-public-attack-trigger-v1", "trigger-event-normalized-public-action",
            condition=saddled_attack_condition(), capabilities=(SADDLED_ATTACK_CAPABILITY,))
    named_source_attack = _NAMED_SOURCE_ATTACK.fullmatch(material_line)
    if (
        named_source_attack is not None
        and _source_subject(named_source_attack["source"], card_name=card_name)
        and (
            " ".join(named_source_attack["body"].casefold().split()) == _EACH_OPPONENT_TOP_EXILE_ATTACK_BODY
            or attack_food_stats_body_matches(named_source_attack["body"])
        )
    ):
        return _spec(
            "creature.attacks",
            "source_attacks",
            named_source_attack["body"],
            "fixed-counter-public-attack-trigger-v1",
            "trigger-event-normalized-public-action",
            condition={"field": "card", "op": "eq", "value": "$source.ref"},
        )
    match = _PUBLIC_ATTACK_TRIGGER.fullmatch(material_line)
    if match is None:
        from .qualified_attack_subjects import qualified_attack_binding_spec
        return qualified_attack_binding_spec(material_line)
    subject = " ".join(match.group("subject").casefold().split())
    recipient = match["recipient"]
    if subject == "this creature" and (match["alone"] is None or recipient is not None):
        return None
    if recipient and recipient.casefold() == "you":
        if subject != "a creature" or match["alone"] is not None:
            return None
        scope = defended_player_condition()
    else:
        scope = defended_player_or_planeswalker_condition() if recipient else None
    return _spec(
        "creature.attacks",
        "source_attacks" if subject == "this creature" else "public_attack_" + subject.replace(" ", "_"),
        match.group("body"),
        "fixed-counter-public-attack-trigger-v1",
        "trigger-event-normalized-public-action",
        condition=_all_conditions(
            {"field": "card", "op": "eq", "value": "$source.ref"}
            if subject in {"this creature", "this vehicle"}
            else None,
            {"field": "controller", "op": "eq", "value": "$source.controller"}
            if subject == "a creature you control"
            else None,
            {"field": "keywords", "op": "contains_any", "value": ["flying"]}
            if subject == "a creature with flying"
            else None,
            {"field": "attacking_alone", "op": "truthy", "value": True}
            if match.group("alone") is not None
            else None,
            scope,
        ),
        capabilities=(QUALIFIED_ATTACK_CAPABILITY,) if scope is not None else (),
    )


def _public_block_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    match = _PUBLIC_BLOCK_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    subject = " ".join(match.group("subject").casefold().split())
    event = match.group("event").casefold()
    blocked_text = str(match.group("blocked") or "").casefold()
    if (subject == "a creature you control" and (event != "becomes blocked" or blocked_text)
        or subject == "a creature" and (event != "blocks" or blocked_text)):
        return None
    blocked_flying = blocked_text == " a creature with flying"
    blocked_by_creature = blocked_text == " by a creature"
    if blocked_flying and (subject != "this creature" or event != "blocks"):
        return None
    if blocked_by_creature and (
        subject != "this creature" or event != "becomes blocked"
    ):
        return None
    if blocked_by_creature and re.search(r'\bthat creature\b',match.group('body'),re.I):
        from .qualified_block_pairs import qualified_block_pair_spec
        return qualified_block_pair_spec(material_line)
    return _spec(
        "creature.blocks" if event == "blocks" else "creature.becomes_blocked",
        (
            "this_creature_blocks_flying"
            if blocked_flying
            else "this_creature_becomes_blocked_by_creature"
            if blocked_by_creature
            else f"{subject.replace(' ', '_')}_{event.replace(' ', '_')}"
        ),
        match.group("body"),
        "fixed-counter-public-block-trigger-v1",
        "trigger-event-normalized-public-action",
        condition=_all_conditions(
            {"field": "card", "op": "eq", "value": "$source.ref"}
            if subject == "this creature"
            else None,
            {"field": "controller", "op": "eq", "value": "$source.controller"}
            if "you control" in subject
            else None,
            {"field": "keywords", "op": "contains_any", "value": ["defender"]}
            if "defender" in subject
            else None,
            {
                "field": "blocked_attacker_keywords",
                "op": "contains_any",
                "value": ["flying"],
            }
            if blocked_flying
            else None,
        ),
    )


def _face_transition_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    entry = re.fullmatch(r"Whenever a face-down (?P<kind>creature|permanent) you control enters, (?P<body>.+)",
                         material_line, re.IGNORECASE)
    if entry is not None:
        conditions = [
            {"field": FACE_DOWN_ENTRY_FIELD, "op": "eq", "value": True},
            {"field": "controller", "op": "eq", "value": "$source.controller"},
        ]
        if entry["kind"].casefold() == "creature":
            conditions.append({"field": "types", "op": "contains_any", "value": ["creature"]})
        return _spec(
            "permanent.enter", "controlled_face_down_permanent_enters", entry["body"],
            "fixed-counter-face-down-entry-v1", "trigger-event-normalized-zone-change",
            condition={"all": conditions}, capabilities=(FACE_DOWN_ENTRY_CAPABILITY,),
        )
    turned = re.fullmatch(
        r"Whenever (?:(?P<active>you turn a permanent face up)|"
        r"(?:a permanent you control|this (?:creature|enchantment) or another permanent you control) is turned face up), "
        r"(?P<body>.+)", material_line, re.IGNORECASE,
    )
    if turned is None:
        return None
    active = turned["active"] is not None
    return _spec(
        TURN_FACE_UP_EVENT, "controller_turns_up_permanent" if active else "controlled_permanent_turned_face_up",
        turned["body"], "fixed-counter-public-face-up-trigger-v1", "trigger-event-normalized-public-action",
        condition={"field": TURN_FACE_UP_ACTOR_FIELD if active else "controller",
                   "op": "eq", "value": "$source.controller"},
        capabilities=(TURN_FACE_UP_ACTOR_CAPABILITY,) if active else (),
    )


def _public_misc_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    transition = _face_transition_spec(material_line)
    if transition is not None:
        return transition
    dungeon = re.fullmatch(r"Whenever you complete a dungeon, (?P<body>.+)", material_line, re.IGNORECASE)
    if dungeon is not None:
        return _spec(
            DUNGEON_COMPLETED_EVENT, DUNGEON_COMPLETION_TRIGGER_VARIANT, dungeon["body"],
            "fixed-counter-dungeon-completion-trigger-v1", "cr-603-handling-triggered-abilities",
            condition={"field": "player", "op": "eq", "value": "$source.controller"},
            capabilities=(DUNGEON_VENTURE_CAPABILITY,),
        )
    cycle = _PUBLIC_CYCLE_TRIGGER.fullmatch(material_line)
    if cycle is not None:
        source_self = cycle.group("source") is not None
        another = cycle.group("another") is not None
        return _spec(
            "card.cycled.self" if source_self else "card.cycled",
            (
                "source_cycles"
                if source_self
                else "controller_cycles_another"
                if another
                else "player_cycles"
            ),
            cycle.group("body"),
            "fixed-counter-public-cycle-trigger-v1",
            "trigger-event-normalized-public-action",
            condition=(
                {
                    "all": [
                        {
                            "field": "player",
                            "op": "eq",
                            "value": "$source.controller",
                        },
                        {
                            "field": "card",
                            "op": "ne",
                            "value": "$source.ref",
                        },
                    ]
                }
                if another
                else None
            ),
            active_zone="hand" if source_self else "battlefield",
        )
    source_face_up = _SOURCE_FACE_UP_TRIGGER.fullmatch(material_line)
    if source_face_up is not None:
        return _spec(
            "permanent.turned_face_up",
            "source_turned_face_up",
            source_face_up.group("body"),
            "fixed-counter-public-face-up-trigger-v1",
            "trigger-event-normalized-public-action",
            condition={
                "field": "card",
                "op": "eq",
                "value": "$source.ref",
            },
        )
    face_up = _PUBLIC_FACE_UP_TRIGGER.fullmatch(material_line)
    if face_up is not None:
        return _spec(
            "permanent.turned_face_up",
            "permanent_turned_face_up",
            face_up.group("body"),
            "fixed-counter-public-face-up-trigger-v1",
            "trigger-event-normalized-public-action",
        )
    second_draw = _PUBLIC_SECOND_DRAW_TRIGGER.fullmatch(material_line)
    if second_draw is not None:
        opponent = second_draw["scope"].casefold() == "an opponent"
        condition = (
            {"field": "player", "op": "ne", "value": "$source.controller"}
            if opponent else {"field": "draw_ordinal", "op": "eq", "value": 2}
        )
        return _spec(
            "card.second_draw", "opponent_second_draw" if opponent else "any_player_second_draw",
            second_draw["body"], "fixed-counter-public-second-draw-trigger-v1",
            "trigger-event-normalized-card-draw", condition=condition,
        )
    any_draw_damage = _ANY_PLAYER_CARD_DRAW_DAMAGE.fullmatch(material_line)
    if any_draw_damage is not None:
        return _spec(
            "card.drawn", "any_player_card_draw_damage", any_draw_damage["body"],
            "fixed-counter-any-player-card-draw-damage-v1", "trigger-event-normalized-card-draw",
        )
    opponent_draw = _OPPONENT_CARD_DRAW_TRIGGER.fullmatch(material_line)
    if opponent_draw is not None:
        body = re.sub(
            r"^(this creature deals [1-9]\d* damage to )them(?=\.?$)",
            r"\g<1>that player", opponent_draw.group("body"), flags=re.IGNORECASE,
        )
        return _spec(
            "card.drawn",
            "opponent_card_draw",
            body,
            "fixed-counter-opponent-card-draw-trigger-v1",
            "trigger-event-normalized-card-draw",
            condition={
                "field": "player",
                "op": "ne",
                "value": "$source.controller",
            },
        )
    return None


def _source_any_damage_spec(material_line, *, card_name):
    """Fixed source identity and committed amount, independent of recipient kind."""
    match=re.fullmatch(r'Whenever (?P<source>.+?) (?P<verb>deals|is dealt) (?P<combat>combat )?damage, (?P<body>.+)',material_line,re.I)
    if match is None or not _source_subject(match['source'],card_name=card_name):return None
    receiving=match['verb'].casefold()=='is dealt'
    condition=_all_conditions(
        {'field':'target','op':'eq','value':'$source.ref'} if receiving else None,
        {'field':'combat','op':'eq','value':True} if match['combat'] else None)
    from ..received_damage_model import RECEIVED_DAMAGE_EVENT, RECEIVED_COMBAT_DAMAGE_EVENT, RECEIVED_DAMAGE_MECHANIC
    template = ('fixed-counter-source-received-combat-damage-trigger-v1' if match['combat'] else 'fixed-counter-source-received-damage-trigger-v1') if receiving else 'fixed-counter-public-damage-trigger-v1'
    return _spec((RECEIVED_COMBAT_DAMAGE_EVENT if match['combat'] else RECEIVED_DAMAGE_EVENT) if receiving else 'damage.dealt.self',
        ('source_received_combat_damage' if match['combat'] else 'source_dealt_damage') if receiving else 'source_any_combat_damage' if match['combat'] else 'source_any_damage',
        match['body'],template,RECEIVED_DAMAGE_MECHANIC if receiving else 'trigger-event-normalized-damage',condition=condition)


def _transform_into_spec(material_line, *, card_name):
    if " transforms into " not in material_line.casefold():
        return None
    match = re.fullmatch(
        r"(?:When|Whenever) (?P<subject>.+?) (?P<entry>enters or )?"
        r"transforms into (?P<result>[^,]+?)(?P<death> or dies)?, (?P<body>.+)",
        material_line, re.I,
    )
    if match is None:
        return None
    from ..rules.transform_capability_shapes import PERMANENT_TRANSFORM_CAPABILITY_ID
    from ..transform_timestamp_model import TRANSFORM_EVENT

    result = match["result"]
    companion = match["entry"] or match["death"]
    mechanics = "trigger-event-normalized-public-action"
    if match["subject"].casefold() == "this permanent" or _source_subject(match["subject"], card_name=card_name):
        if (match["entry"] and match["death"]
                or re.match(r"a(?:n)?\s|any\s", result, re.I)
                or re.search(r"\bor\b", result, re.I)):
            return None
        source = {"field": "card", "op": "eq", "value": "$source.ref"}
        condition = _all_conditions(
            source, {"field": "transformed_name", "op": "eq", "value": result},
        )
        if companion:
            from ..rules.event_subscriptions import FixedEventSubscription, fixed_event_subscription_condition

            other_event = "permanent.enter" if match["entry"] else "creature.dies"
            return _spec(
                TRANSFORM_EVENT, "source_transform_pair", match["body"],
                "fixed-counter-source-transform-pair-trigger-v1", mechanics,
                condition=fixed_event_subscription_condition((
                    FixedEventSubscription(TRANSFORM_EVENT, condition),
                    FixedEventSubscription(other_event, source),
                )),
                capabilities=(
                    PERMANENT_TRANSFORM_CAPABILITY_ID,
                    "trigger.event.normalized_zone_change",
                    "trigger.subscription.fixed_multi_event",
                ),
            )
        return _spec(
            TRANSFORM_EVENT, "source_transforms_into", match["body"],
            "fixed-counter-source-transforms-into-trigger-v1", mechanics,
            condition=condition, capabilities=(PERMANENT_TRANSFORM_CAPABILITY_ID,),
        )
    if companion or match["subject"].casefold() != "a permanent you control":
        return None
    subject = re.fullmatch(r"an? (?P<quality>.+)", result, re.I)
    if subject is None:
        return None
    excluded = re.fullmatch(r"non-(.+) creature", subject["quality"], re.I)
    subtype = canonical_creature_subtype(excluded[1] if excluded else subject["quality"])
    if subtype is None:
        return None
    subtype_condition = {"field": "subtypes", "op": "contains_any", "value": [subtype]}
    condition = _all_conditions(
        {"field": "controller", "op": "eq", "value": "$source.controller"},
        {"field": "types", "op": "contains_any", "value": ["creature"]} if excluded else None,
        {"not": subtype_condition} if excluded else subtype_condition,
    )
    return _spec(
        TRANSFORM_EVENT, "controlled_permanent_transforms_into", match["body"],
        "fixed-counter-controlled-transforms-into-trigger-v1", mechanics,
        condition=condition, capabilities=(PERMANENT_TRANSFORM_CAPABILITY_ID,),
    )


_FIRST_LIFE_GAIN = re.compile(
    r'(?:When|Whenever) you gain life for the first time (each turn|during each of your turns), (?P<body>.+)', re.I,
)


def _first_life_gain_spec(material_line):
    match = _FIRST_LIFE_GAIN.fullmatch(material_line)
    if match is None:
        return None
    condition = {'field': 'player', 'op': 'eq', 'value': '$source.controller'}
    if match[1].casefold() != 'each turn':
        condition = {'all': [condition, {'field': 'active_player', 'op': 'eq', 'value': '$source.controller'}]}
    return _spec(FIRST_LIFE_GAIN_EVENT, 'controller_first_life_gain', match['body'],
        'fixed-counter-controller-first-life-gain-trigger-v1', 'trigger-event-normalized-life-gain',
        condition=condition, capabilities=(FIRST_LIFE_GAIN_CAPABILITY,))


def _opponent_life_gain_paid_graveyard_return_spec(material_line):
    from ..rules.graveyard_self_return import (
        GRAVEYARD_SELF_RETURN_CAPABILITY, graveyard_opponent_life_gain_condition,
    )
    match = re.fullmatch(
        r"Whenever an opponent gains life, "
        r"(?P<body>you may pay \{R\}\. If you do, return this card from your graveyard to your hand\.)",
        material_line, re.I,
    )
    if match is None:
        return None
    return _spec(
        "life.gained", "opponent_life_gain_paid_graveyard_return", match["body"],
        "fixed-opponent-life-gain-paid-graveyard-return-v1", "trigger-event-normalized-life-gain",
        condition=graveyard_opponent_life_gain_condition(),
        capabilities=(GRAVEYARD_SELF_RETURN_CAPABILITY,), active_zone="graveyard",
    )


def _opponent_life_gain_self_counters_spec(material_line):
    match = re.fullmatch(
        r"Whenever an opponent gains life, "
        r"(?P<body>put that many \+1/\+1 counters on this creature\.)",
        material_line, re.I,
    )
    if match is None:
        return None
    return _spec(
        "life.gained", "opponent_life_gain_self_counters", match["body"],
        "fixed-counter-opponent-life-gain-self-counters-v1", "trigger-event-normalized-life-gain",
        condition={"field": "player", "op": "ne", "value": "$source.controller"},
    )


def _opponent_first_own_turn_life_loss_spec(material_line, *, card_name):
    from ..rules.first_own_turn_life_loss import CAPABILITY, EVENT, MECHANIC, TEMPLATE, VARIANT, first_own_turn_loss_condition

    match = re.fullmatch(
        r"Whenever an opponent loses life for the first time during each of their turns, "
        r"(?P<body>put a \+1/\+1 counter on (?P<source>.+?) and draw a card\.)",
        material_line, re.I,
    )
    if match is None or not _source_subject(match["source"], card_name=card_name):
        return None
    return _spec(
        EVENT, VARIANT, match["body"], TEMPLATE, MECHANIC,
        condition=first_own_turn_loss_condition(),
        capabilities=(CAPABILITY, "counter.producer.fixed_effect", "zone.draw.library_to_hand"),
    )


def _opponent_life_loss_spec(material_line):
    from ..life_change import LIFE_LOSS_CAPABILITY, LIFE_LOSS_EVENT
    from ..rules.event_outcome_shapes import opponent_life_loss_condition

    match = re.fullmatch(
        r"(?:When|Whenever) an opponent loses life, "
        r"(?P<body>you gain that much life|that player mills that many cards)\.?", material_line, re.I,
    )
    if match is None:
        return None
    return _spec(LIFE_LOSS_EVENT, "opponent_life_loss", match["body"],
        "fixed-counter-opponent-life-loss-result-v1", "trigger-event-normalized-life-loss",
        condition=opponent_life_loss_condition(), capabilities=(LIFE_LOSS_CAPABILITY,))


def _opponent_library_exile_play_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    match = _OPPONENT_LIBRARY_EXILE_PLAY_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    from ..opponent_library_exile_model import (
        OPPONENT_LIBRARY_EXILE_ACTOR_FIELD,
        OPPONENT_LIBRARY_EXILE_EVENT,
    )
    from ..resolving_library_exile import OPPONENT_LIBRARY_EXILE_EVENT_CAPABILITY
    from .opponent_library_exile_templates import OPPONENT_LIBRARY_EXILE_PLAY_VARIANT

    return _spec(
        OPPONENT_LIBRARY_EXILE_EVENT,
        OPPONENT_LIBRARY_EXILE_PLAY_VARIANT,
        match["body"],
        "controlled-opponent-library-exile-play-trigger-v1",
        "trigger-event-resolving-opponent-library-exile",
        condition={
            "field": OPPONENT_LIBRARY_EXILE_ACTOR_FIELD,
            "op": "eq",
            "value": "$source.controller",
        },
        capabilities=(OPPONENT_LIBRARY_EXILE_EVENT_CAPABILITY,),
    )


def fixed_public_event_binding_spec(
    material_line: str,
    *,
    card_name: str | None = None,
) -> FixedPublicEventBindingSpec | None:
    from .blight_templates import opponent_blight_entry_spec

    from .opposing_owner_entry_life import opposing_owner_entry_binding

    owned_entry = opposing_owner_entry_binding(material_line, spec=_spec)
    if owned_entry is not None:
        return owned_entry
    exile_play = _opponent_library_exile_play_spec(material_line)
    if exile_play is not None:
        return exile_play
    from .source_counter_cast_binding import source_counter_cast_binding_spec
    source_counter=source_counter_cast_binding_spec(material_line,card_name=card_name)
    if source_counter is not None:return source_counter
    from .designation_event_bindings import designation_event_binding_spec
    designation = designation_event_binding_spec(material_line, card_name=card_name)
    if designation is not None:return designation
    from .received_damage_bindings import received_permanent_binding_spec
    received = received_permanent_binding_spec(material_line, card_name=card_name)
    if received is not None:return received
    """Parse one bounded public occurrence without compiling its effect body."""

    exploit=re.fullmatch(r'(?:When|Whenever) (?P<source>.+?) exploits a creature, (?P<body>.+)',material_line,re.I)
    if exploit is not None and _source_subject(exploit['source'],card_name=card_name):
        return _spec('creature.exploits.self','source_exploits',exploit['body'],
            'fixed-source-exploit-result-v1','fixed-exploit-result',capabilities=('trigger.keyword.exploit.creature_sacrifice',))

    return (
        _opponent_first_own_turn_life_loss_spec(material_line, card_name=card_name)
        or _first_life_gain_spec(material_line)
        or _opponent_life_gain_paid_graveyard_return_spec(material_line)
        or _opponent_life_gain_self_counters_spec(material_line)
        or _opponent_life_loss_spec(material_line)
        or _anywhere_graveyard_spec(material_line)
        or _source_any_damage_spec(material_line,card_name=card_name)
        or _transform_into_spec(material_line, card_name=card_name)
        or _class_level_spec(material_line)
        or _public_spell_action_spec(material_line, card_name=card_name)
        or _attachment_event_spec(material_line)
        or opponent_blight_entry_spec(material_line, card_name=card_name)
        or _source_or_controlled_toughness_greater_than_power_entry_spec(material_line, card_name=card_name)
        or _constellation_spec(material_line, card_name=card_name)
        or _battalion_spec(material_line, card_name=card_name)
        or _public_zone_spec(material_line, card_name=card_name)
        or _public_damage_spec(material_line, card_name=card_name)
        or _public_attack_spec(material_line, card_name=card_name)
        or _public_block_spec(material_line)
        or _public_misc_spec(material_line)
    )


__all__ = ["FixedPublicEventBindingSpec", "fixed_public_event_binding_spec"]
