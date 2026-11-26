from __future__ import annotations

"""Closed public-action and result predicates for typed event triggers."""

from . import regex_working_set as re
from .day_night_nodes import day_night_counter_binding
from typing import Any, Mapping

from ..attack_batch_model import DIRECT_PLAYER_ATTACK_CAPABILITY, DIRECT_PLAYER_ATTACK_EVENT, direct_player_attack_condition
from ..creature_subtypes import canonical_creature_subtype
from ..initiative_model import TAKE_INITIATIVE_CAPABILITY
from ..rules.event_subscriptions import (
    FixedEventSubscription, LAND_PLAY_EVENT, fixed_event_subscription_condition,
    OTHER_LAND_PLAY_SACRIFICE_CAPABILITY, OTHER_LAND_PLAY_SACRIFICE_TEMPLATE,
    PHYSICAL_CARD_PLAY_TEMPLATE, PHYSICAL_EXILE_PLAY_TREASURE_TEMPLATE, physical_card_play_event_condition,
    other_land_play_condition,
)
from ..type_subtype_vocabulary import ARTIFACT_SUBTYPES
from ..source_sacrifice_model import SOURCE_UNATTACHMENT_VARIANT, UNATTACHMENT_EVENT, UNATTACHMENT_TRIGGER_CAPABILITY
from .fixed_public_event_trigger_bindings import (
    FixedPublicEventBindingSpec,
    _all_conditions,
    _source_subject,
    _spec,
)


_PUBLIC_ATTACK_BATCH_TRIGGER = re.compile(
    r"^Whenever you attack(?: with (?P<count>two|three) or more creatures)?, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_CONTROLLER_CYCLE_TRIGGER = re.compile(
    r"^Whenever you cycle a card, (?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_LAND_PLAY_TRIGGER = re.compile(
    r"Whenever (?P<actor>you|a player|an opponent) (?P<play>play|plays) a land"
    r"(?P<land_origin> from exile)?"
    r"(?: or (?P<cast>cast|casts) a spell(?P<spell_origin> from exile|"
    r"(?P<shared_nonhand> from anywhere other than your hand))?)?, (?P<body>.+)",
    re.IGNORECASE,
)
_CONTROLLED_FOOD_SACRIFICE_GROUP = re.compile(
    r"Whenever you sacrifice one or more Foods, (?P<body>.+)", re.IGNORECASE,
)
_OTHER_LAND_PLAY_SACRIFICE = re.compile(
    r"When(?:ever)? you play another land, (?P<body>sacrifice this land\.)", re.IGNORECASE,
)
_PUBLIC_SACRIFICE_TRIGGER = re.compile(
    r"^(?:When|Whenever) (?P<actor>you|an opponent|a player) "
    r"(?P<verb>sacrifice|sacrifices) "
    r"(?P<object>[^,]+?)(?P<turn> during your turn)?, (?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_SACRIFICE_TRIGGER = re.compile(
    r"^When you sacrifice this (?P<object>Aura|artifact|creature), "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_OR_OTHER_SACRIFICE_TRIGGER = re.compile(
    r"^(?:When|Whenever) you sacrifice (?P<source>.+?) or another "
    r"(?P<type>artifact|creature|permanent), (?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_DISCARD_TRIGGER = re.compile(
    r"^Whenever (?P<actor>you|an opponent|a player) "
    r"(?P<verb>discard|discards) "
    r"(?P<object>a card|a land card|a creature card|a nonland card|a noncreature, nonland card), "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_GRAVEYARD_DEPARTURE_TRIGGER = re.compile(
    r"^Whenever one or more (?P<cards>cards|creature cards|artifact cards|"
    r"artifact and/or creature cards) leave your graveyard"
    r"(?P<turn> during your turn)?, (?P<body>.+)$",
    re.IGNORECASE,
)
_CONTROLLED_TOKEN_ENTRY_TRIGGER = re.compile(
    r"^Whenever (?P<quantity>a token|one or more tokens) you control "
    r"(?P<verb>enters|enter), (?P<body>.+)$",
    re.IGNORECASE,
)
_CONTROLLED_TOKEN_LEAVE_TRIGGER = re.compile(
    r"^Whenever a token you control leaves the battlefield, (?P<body>.+)$",
    re.IGNORECASE,
)
_CONTROLLED_CREATURE_LEAVE_TRIGGER = re.compile(
    r"^Whenever another creature you control leaves the battlefield, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_CONTROLLED_CREATURE_OR_ARTIFACT_GRAVEYARD_TRIGGER = re.compile(
    r"^Whenever another (?P<order>creature or artifact|artifact or creature) "
    r"you control is put into a graveyard from the battlefield, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_NONTOKEN_GRAVEYARD_TRIGGER = re.compile(
    r"^Whenever a nontoken (?P<kind>creature is put into your graveyard|"
    r"artifact you control is put into a graveyard) from the battlefield, "
    r"(?P<body>.+)$",
    re.IGNORECASE,
)
_CONTROLLED_NONTOKEN_DRAGON_ENTRY_TRIGGER = re.compile(
    r"^Whenever another nontoken Dragon you control enters, (?P<body>.+)$",
    re.IGNORECASE,
)
_LAND_ENTRY_DURING_TURN_TRIGGER = re.compile(
    r"^Whenever a land enters during your turn, (?P<body>.+)$",
    re.IGNORECASE,
)
_SOURCE_LAND_UNTAPPED_ENTRY_TRIGGER = re.compile(
    r"^When this land enters untapped, (?P<body>.+)$",
    re.IGNORECASE,
)
_PUBLIC_DAMAGE_BATCH_TRIGGER = re.compile(
    r"^Whenever one or more (?P<kind>creatures|artifact creatures) you "
    r"control deal combat damage to a player, (?P<body>.+)$",
    re.IGNORECASE,
)
QUALIFIED_DAMAGE_BATCH_VARIANT = 'one_or_more_qualified_damage_player'
QUALIFIED_DAMAGE_BATCH_CAPABILITY = 'trigger.event.qualified_damage_batch'
QUALIFIED_DAMAGE_SINGULAR_VARIANT = 'singular_qualified_damage_player'
QUALIFIED_DAMAGE_SINGULAR_CAPABILITY = 'trigger.event.qualified_damage_singular'
_QUALIFIED_DAMAGE_BATCH = re.compile(
    r'^Whenever one or more (?P<subject>.+?) you control deal(?P<combat> combat)? damage '
    r'to (?P<recipient>a player|an opponent), (?P<body>.+)$', re.I,
)
_QUALIFIED_DAMAGE_SINGULAR = re.compile(
    r'^Whenever (?:a|an) (?P<subject>.+?) you control deals(?P<combat> combat)? damage '
    r'to (?P<recipient>a player|an opponent), (?P<body>.+)$', re.I,
)
_SOURCE_EXTENDED_DAMAGE_TRIGGER = re.compile(
    r"^Whenever this creature deals combat damage to a player or "
    r"(?P<recipient>planeswalker|battle), (?P<body>.+)$",
    re.IGNORECASE,
)
_OPPONENT_NONCOMBAT_DAMAGE_TRIGGER = re.compile(
    r"^Whenever an opponent is dealt noncombat damage, (?P<body>.+)$",
    re.IGNORECASE,
)


def _actor_condition(
    actor: str,
    *,
    field: str,
) -> Mapping[str, Any] | None:
    relation = " ".join(actor.casefold().split())
    if relation == "you":
        return {
            "field": field,
            "op": "eq",
            "value": "$source.controller",
        }
    if relation == "an opponent":
        return {
            "field": field,
            "op": "ne",
            "value": "$source.controller",
        }
    if relation == "a player":
        return None
    raise ValueError("Public action actors require a closed controller relation")


def _public_land_play_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    sacrifice = _OTHER_LAND_PLAY_SACRIFICE.fullmatch(material_line)
    if sacrifice is not None:
        return _spec(
            LAND_PLAY_EVENT, "other_land_play_source_sacrifice", sacrifice["body"],
            OTHER_LAND_PLAY_SACRIFICE_TEMPLATE, "trigger-event-normalized-public-action",
            condition=other_land_play_condition(), capabilities=(OTHER_LAND_PLAY_SACRIFICE_CAPABILITY,),
        )
    normalized = material_line.casefold().rstrip(".")
    if normalized == "whenever you play a card from exile, create a treasure token":
        return _spec(
            LAND_PLAY_EVENT, "independent_dual_event", "Create a Treasure token.", PHYSICAL_EXILE_PLAY_TREASURE_TEMPLATE,
            "trigger-event-normalized-public-action", condition=physical_card_play_event_condition(from_exile=True),
            capabilities=("trigger.event.normalized_spell_cast", "trigger.subscription.fixed_multi_event"),
        )
    if normalized == "whenever you play a card, draw a card":
        return _spec(
            LAND_PLAY_EVENT, "independent_dual_event", "Draw a card.", PHYSICAL_CARD_PLAY_TEMPLATE,
            "trigger-event-normalized-public-action", condition=physical_card_play_event_condition(),
            capabilities=("trigger.event.normalized_spell_cast", "trigger.subscription.fixed_multi_event"),
        )
    match = _PUBLIC_LAND_PLAY_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    actor = match["actor"].casefold()
    controller = actor == "you"
    if controller != (match["play"].casefold() == "play"):
        return None
    if match["shared_nonhand"] is not None:
        if not controller or match["land_origin"] is not None:
            return None
        land_origin = {"field": "from", "op": "ne", "value": "hand"}
    elif match["land_origin"] is not None:
        land_origin = {"field": "from", "op": "eq", "value": "exile"}
    else:
        land_origin = None
    condition = _all_conditions(
        _actor_condition(actor, field="player"),
        land_origin,
    )
    if match["cast"] is None:
        return _spec(
            LAND_PLAY_EVENT, "public_land_play", match["body"], "fixed-land-play-trigger-v1",
            "trigger-event-normalized-public-action", condition=condition,
        )
    if controller != (match["cast"].casefold() == "cast"):
        return None
    from .spell_cast_predicates import fixed_spell_cast_binding_spec

    origin = match["spell_origin"] or ""
    cast = fixed_spell_cast_binding_spec(
        f"Whenever {actor} {match['cast']} a spell{origin}, {match['body']}",
    )
    if cast is None:
        return None
    return _spec(
        LAND_PLAY_EVENT, "independent_dual_event", match["body"], "fixed-land-play-or-cast-trigger-v1",
        "trigger-event-normalized-public-action",
        condition=fixed_event_subscription_condition((
            FixedEventSubscription(LAND_PLAY_EVENT, condition),
            FixedEventSubscription("spell.cast", cast.subject.event_condition),
        )),
        capabilities=("trigger.event.normalized_spell_cast", "trigger.subscription.fixed_multi_event"),
    )


def _sacrifice_types(field: str, *values: str) -> dict[str, Any]:
    return {"field": field, "op": "contains_any", "value": list(values)}


def _sacrifice_subject(subject: str) -> tuple[str, tuple[Mapping[str, Any], ...]] | None:
    token = {"field": "token", "op": "truthy", "value": True}
    if subject in {"clue", "food"}:
        return subject, (_sacrifice_types("subtypes", subject),)
    if subject == "blood token":
        return "blood_token", (_sacrifice_types("subtypes", "blood"), token)
    if subject == "token":
        return subject, (token,)
    if subject in {"artifact or creature", "creature or artifact"}:
        return "artifact_or_creature", (_sacrifice_types("types", "artifact", "creature"),)
    if subject in {"artifact", "creature", "enchantment", "land"}:
        return subject, (_sacrifice_types("types", subject),)
    if subject == "permanent":
        return subject, ()
    if subject == "nontoken creature":
        return "qualified_permanent", (_sacrifice_types("types", "creature"),
            {"field": "token", "op": "eq", "value": False})
    if subject == "nontoken permanent":
        return "qualified_permanent", ({"field": "token", "op": "eq", "value": False},)
    if subject == "noncreature token":
        return "qualified_permanent", (token, {"not": _sacrifice_types("types", "creature")})
    token_only = subject.endswith(" token")
    subtype_text = subject.removesuffix(" token")
    subtypes = subtype_text.replace(" or a ", " or ").replace(" or an ", " or ").split(" or ")
    if (
        1 <= len(subtypes) <= 2 and len(set(subtypes)) == len(subtypes)
        and set(subtypes) <= ARTIFACT_SUBTYPES and (len(subtypes) == 1 or not token_only)
    ):
        return "qualified_permanent", (
            _sacrifice_types("types", "artifact"),
            _sacrifice_types("subtypes", *subtypes),
            *((token,) if token_only else ()),
        )
    creature_type = canonical_creature_subtype(subtype_text)
    if creature_type is not None:
        return "qualified_permanent", (
            _sacrifice_types("subtypes", creature_type),
            *((token,) if token_only else ()),
        )
    return None


def _public_sacrifice_spec(
    material_line: str, *, card_name: str | None,
) -> FixedPublicEventBindingSpec | None:
    source = _SOURCE_SACRIFICE_TRIGGER.fullmatch(material_line)
    if source is not None:
        kind = source.group("object").casefold()
        return _spec(
            "permanent.sacrificed.self",
            f"source_{kind}_sacrificed",
            source.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=(
                {"field": "types", "op": "contains_any", "value": [kind]}
                if kind != "aura"
                else {
                    "field": "subtypes",
                    "op": "contains_any",
                    "value": ["aura"],
                }
            ),
        )
    source_or_other = _SOURCE_OR_OTHER_SACRIFICE_TRIGGER.fullmatch(material_line)
    if source_or_other is not None and (
        source_or_other["source"].casefold() in {"this artifact", "this permanent"}
        or _source_subject(source_or_other["source"], card_name=card_name)
    ):
        kind = source_or_other["type"].casefold()
        subject = (
            {"any": [
                {"field": "card", "op": "eq", "value": "$source.ref"},
                _sacrifice_types("types", kind),
            ]}
            if kind != "permanent"
            else None
        )
        return _spec(
            "permanent.sacrificed", "controller_sacrifices_source_or_another", source_or_other["body"],
            "fixed-counter-public-zone-trigger-v1", "trigger-event-normalized-zone-change",
            condition=_all_conditions(_actor_condition("you", field="previous_controller"), subject),
        )
    group = _CONTROLLED_FOOD_SACRIFICE_GROUP.fullmatch(material_line)
    if group is not None:
        from ..rules.event_subscriptions import (
            CONTROLLER_FOOD_SACRIFICE_GROUP_CAPABILITY, CONTROLLER_FOOD_SACRIFICE_GROUP_VARIANT,
            controller_food_sacrifice_group_condition,
        )

        return _spec(
            "permanent.sacrificed", CONTROLLER_FOOD_SACRIFICE_GROUP_VARIANT, group["body"],
            "fixed-counter-public-zone-trigger-v1", "trigger-event-normalized-zone-change",
            condition=controller_food_sacrifice_group_condition(),
            capabilities=(CONTROLLER_FOOD_SACRIFICE_GROUP_CAPABILITY,),
        )
    match = _PUBLIC_SACRIFICE_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    actor = " ".join(match.group("actor").casefold().split())
    if (actor == "you") != (match["verb"].casefold() == "sacrifice"):
        return None
    raw_object = " ".join(match.group("object").casefold().split())
    exclude_source = raw_object.startswith("another ")
    if not exclude_source and not raw_object.startswith(("a ", "an ")):
        return None
    subject = raw_object.removeprefix("a ").removeprefix("an ")
    subject = subject.removeprefix("another ")
    predicate = _sacrifice_subject(subject)
    if predicate is None:
        return None
    subject_variant, subject_terms = predicate
    actor_variant = {
        "you": "controller",
        "an opponent": "opponent",
        "a player": "player",
    }[actor]
    return _spec(
        "permanent.sacrificed",
        f"{actor_variant}_sacrifices_{subject_variant}",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=_all_conditions(
            _actor_condition(actor, field="previous_controller"),
            {"field": "card", "op": "ne", "value": "$source.ref"} if exclude_source else None,
            *subject_terms,
            {"field": "source_controller_is_active_player", "op": "eq", "value": True} if match["turn"] else None,
        ),
    )


def _public_discard_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    from .cycle_or_discard_bindings import cycle_or_discard_binding_spec
    combined = cycle_or_discard_binding_spec(material_line)
    if combined is not None:
        return combined
    match = _PUBLIC_DISCARD_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    actor = " ".join(match.group("actor").casefold().split())
    subject = " ".join(match.group("object").casefold().split())
    subject = subject.removeprefix("a ").removeprefix("an ")
    conditions: list[Mapping[str, Any] | None] = [
        _actor_condition(actor, field="previous_controller"),
    ]
    if subject in {"land card", "creature card"}:
        conditions.append(
            {
                "field": "types",
                "op": "contains_any",
                "value": [subject.removesuffix(" card")],
            }
        )
    elif subject in {"nonland card", "noncreature, nonland card"}:
        conditions.append(
            {
                "not": {
                    "field": "types",
                    "op": "contains_any",
                    "value": ["creature", "land"] if subject == "noncreature, nonland card" else ["land"],
                }
            }
        )
    elif subject != "card":
        return None
    actor_variant = {
        "you": "controller",
        "an opponent": "opponent",
        "a player": "player",
    }[actor]
    return _spec(
        "card.discarded",
        f"{actor_variant}_discards_{subject.removesuffix(' card').replace(', ', '_')}",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=_all_conditions(*conditions),
    )


def _graveyard_departure_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    match = _PUBLIC_GRAVEYARD_DEPARTURE_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    card_kind = " ".join(match.group("cards").casefold().split())
    type_condition: Mapping[str, Any] | None = None
    if card_kind == "creature cards":
        type_condition = {
            "field": "types",
            "op": "contains_any",
            "value": ["creature"],
        }
    elif card_kind == "artifact cards":
        type_condition = {
            "field": "types",
            "op": "contains_any",
            "value": ["artifact"],
        }
    elif card_kind == "artifact and/or creature cards":
        type_condition = {
            "field": "types",
            "op": "contains_any",
            "value": ["artifact", "creature"],
        }
    during_turn = match.group("turn") is not None
    variant_kind = card_kind.replace(" and/or ", "_or_").replace(" ", "_")
    return _spec(
        "card.leave_graveyard",
        "one_or_more_controller_"
        f"{variant_kind}_leave_graveyard"
        f"{'_during_turn' if during_turn else ''}",
        match.group("body"),
        "fixed-counter-public-zone-trigger-v1",
        "trigger-event-normalized-zone-change",
        condition=_all_conditions(
            {"field": "owner", "op": "eq", "value": "$source.controller"},
            type_condition,
            {
                "field": "source_controller_is_active_player",
                "op": "eq",
                "value": True,
            }
            if during_turn
            else None,
        ),
    )


def _public_token_zone_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    token_entry = _CONTROLLED_TOKEN_ENTRY_TRIGGER.fullmatch(material_line)
    if token_entry is not None:
        singular = token_entry.group("quantity").casefold() == "a token"
        if token_entry.group("verb").casefold() != ("enters" if singular else "enter"):
            return None
        return _spec(
            "permanent.enter",
            "controlled_token_enters" if singular else "one_or_more_controlled_tokens_enter",
            token_entry.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=_all_conditions(
                {
                    "field": "controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
                {"field": "token", "op": "truthy", "value": True},
            ),
        )
    token_leave = _CONTROLLED_TOKEN_LEAVE_TRIGGER.fullmatch(material_line)
    if token_leave is not None:
        return _spec(
            "permanent.leave",
            "controlled_token_leaves",
            token_leave.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=_all_conditions(
                {
                    "field": "previous_controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
                {"field": "token", "op": "truthy", "value": True},
            ),
        )
    return None


def _public_controlled_departure_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    creature_leave = _CONTROLLED_CREATURE_LEAVE_TRIGGER.fullmatch(material_line)
    if creature_leave is not None:
        return _spec(
            "permanent.leave",
            "another_controlled_creature_leaves",
            creature_leave.group("body"),
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
                    "value": ["creature"],
                },
                {"field": "card", "op": "ne", "value": "$source.ref"},
            ),
        )
    graveyard = _CONTROLLED_CREATURE_OR_ARTIFACT_GRAVEYARD_TRIGGER.fullmatch(
        material_line
    )
    if graveyard is not None:
        return _spec(
            "permanent.graveyard",
            "another_controlled_creature_or_artifact_graveyard",
            graveyard.group("body"),
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
                {"field": "card", "op": "ne", "value": "$source.ref"},
            ),
        )
    nontoken = _NONTOKEN_GRAVEYARD_TRIGGER.fullmatch(material_line)
    if nontoken is not None:
        creature = nontoken.group("kind").casefold().startswith("creature")
        return _spec(
            "creature.dies" if creature else "artifact.graveyard",
            (
                "nontoken_creature_enters_controller_graveyard"
                if creature
                else "controlled_nontoken_artifact_graveyard"
            ),
            nontoken.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=_all_conditions(
                {"field": "owner", "op": "eq", "value": "$source.controller"}
                if creature
                else {
                    "field": "previous_controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
                {"field": "token", "op": "falsy", "value": True},
            ),
        )
    return None


def _public_entry_zone_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    dragon = _CONTROLLED_NONTOKEN_DRAGON_ENTRY_TRIGGER.fullmatch(material_line)
    if dragon is not None:
        return _spec(
            "permanent.enter",
            "another_controlled_nontoken_dragon_enters",
            dragon.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition=_all_conditions(
                {
                    "field": "controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
                {
                    "field": "subtypes",
                    "op": "contains_any",
                    "value": ["dragon"],
                },
                {"field": "token", "op": "falsy", "value": True},
                {"field": "card", "op": "ne", "value": "$source.ref"},
            ),
        )
    during_turn = _LAND_ENTRY_DURING_TURN_TRIGGER.fullmatch(material_line)
    if during_turn is not None:
        return _spec(
            "land.enter",
            "land_enters_during_controller_turn",
            during_turn.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition={
                "field": "source_controller_is_active_player",
                "op": "eq",
                "value": True,
            },
        )
    source_land = _SOURCE_LAND_UNTAPPED_ENTRY_TRIGGER.fullmatch(material_line)
    if source_land is not None:
        return _spec(
            "permanent.enter.self",
            "source_land_enters_untapped",
            source_land.group("body"),
            "fixed-counter-public-zone-trigger-v1",
            "trigger-event-normalized-zone-change",
            condition={"field": "tapped", "op": "falsy", "value": True},
        )
    return None


def _public_zone_spec(
    material_line: str, *, card_name: str | None,
) -> FixedPublicEventBindingSpec | None:
    from .affected_player_sacrifice_templates import entry_sacrifice_power_draw_binding_spec

    return (
        entry_sacrifice_power_draw_binding_spec(material_line)
        or _public_token_zone_spec(material_line)
        or _public_controlled_departure_spec(material_line)
        or _public_entry_zone_spec(material_line)
        or _graveyard_departure_spec(material_line)
        or _public_sacrifice_spec(material_line, card_name=card_name)
        or _public_discard_spec(material_line)
    )


def _qualified_damage_match_spec(match, *, singular: bool):
    from .public_state_queries import fixed_characteristic_battlefield_query_subject
    from ..object_predicate import ObjectQuerySpec
    subject = match['subject']
    if singular:
        if subject.endswith('creature'):
            subject += 's'
        else:
            from ..creature_subtypes import canonical_creature_subtype
            terms = tuple(part.strip() for part in re.split(r',?\s+or\s+|,\s*', subject, flags=re.I))
            if not terms or any(canonical_creature_subtype(term) is None for term in terms):
                return None
            subject = ' or '.join(canonical_creature_subtype(term) for term in terms) + ' creatures'
    parsed = fixed_characteristic_battlefield_query_subject(subject + ' you control')
    if parsed is None or parsed[0] != 'source_controller' or parsed[2]:
        return None
    query = parsed[1]
    state=query.state_predicate
    if state is not None and not singular:return None
    if state is not None and (state.entered_this_turn or state.counter_name is not None
            or state.minimum_total_counter_count is not None):return None
    projected = ObjectQuerySpec(zones=('battlefield',), types_all=query.types_all,
        types_any=query.types_any, excluded_types=query.excluded_types,
        subtypes_all=query.subtypes_all, subtypes_any=query.subtypes_any,
        excluded_subtypes=query.excluded_subtypes, colors_all=query.colors_all,
        colors_any=query.colors_any, colorless=query.colorless, supertypes_all=query.supertypes_all,
        state_predicate=state)
    if query != projected:
        return None
    conditions = [
        {'field':'source_controller','op':'eq','value':'$source.controller'},
        {'field':'source_zone','op':'eq','value':'battlefield'},
        {'field':'target_kind','op':'eq','value':'player'},
    ]
    if state is not None:
        from ..permanent_event_state_model import PERMANENT_EVENT_STATE_PREFIX
        conditions.append({'field':'source_'+PERMANENT_EVENT_STATE_PREFIX+'version','op':'eq','value':1})
        conditions.extend({'field':'source_'+PERMANENT_EVENT_STATE_PREFIX+name,'op':'eq','value':getattr(state,name)}
            for name in ('tapped','attacking','blocking','enchanted','equipped','modified','monstrous')
            if getattr(state,name) is not None)
    if match['combat']:
        conditions.append({'field':'combat','op':'truthy','value':True})
    if match['recipient'].casefold() == 'an opponent':
        conditions.append({'field':'player','op':'ne','value':'$source.controller'})
    from .public_event_query_conditions import public_event_query_conditions
    conditions.extend(public_event_query_conditions(query, prefix='source_'))
    return _spec('damage.dealt', QUALIFIED_DAMAGE_SINGULAR_VARIANT if singular else QUALIFIED_DAMAGE_BATCH_VARIANT, match['body'],
        'fixed-counter-public-damage-trigger-v1', 'trigger-event-normalized-damage',
        condition=_all_conditions(*conditions), capabilities=(QUALIFIED_DAMAGE_SINGULAR_CAPABILITY if singular else QUALIFIED_DAMAGE_BATCH_CAPABILITY,
            *(('trigger.event.state_qualified_damage',) if state is not None else ())))


def _qualified_damage_batch_spec(text: str):
    match = _QUALIFIED_DAMAGE_BATCH.fullmatch(text)
    return _qualified_damage_match_spec(match, singular=False) if match is not None else None


def _qualified_damage_singular_spec(text: str):
    match = _QUALIFIED_DAMAGE_SINGULAR.fullmatch(text)
    return _qualified_damage_match_spec(match, singular=True) if match is not None else None


def _point_combat_spec(material_line):
    from ..rules.combat_point_counters import (
        INBOUND_POINT_VARIANT, OUTBOUND_POINT_VARIANT,
        point_combat_condition, point_combat_effects, point_combat_node_capabilities,
    )

    incoming = re.fullmatch(
        r"Whenever a creature deals combat damage to you, (?P<body>remove a point counter from this artifact[.])",
        material_line, re.I,
    )
    outgoing = re.fullmatch(
        r"Whenever a creature you control deals combat damage to an opponent, "
        r"(?P<body>put a point counter on this artifact[.] Then if it has ten or more point counters on it, "
        r"remove them all and that player loses the game[.])", material_line, re.I,
    )
    match = incoming or outgoing
    if match is None:
        return None
    outbound = outgoing is not None
    capabilities = point_combat_node_capabilities(
        effects=point_combat_effects(outbound=outbound), target_schema=None,
        mechanic_ids=("cr-122-counters",),
    )
    return _spec("damage.dealt", OUTBOUND_POINT_VARIANT if outbound else INBOUND_POINT_VARIANT,
                 match["body"], "fixed-combat-point-occurrence-v1", "trigger-event-normalized-damage",
                 condition=point_combat_condition(outbound=outbound), capabilities=capabilities)


def _public_damage_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    countered_batch = re.fullmatch(
        r"Whenever one or more creatures you control with counters on them deal combat damage to a player, "
        r"(?P<body>you may draw a card and proliferate[.]?)", material_line, re.IGNORECASE,
    )
    if countered_batch is not None:
        from ..permanent_event_state_model import (
            COUNTERED_COMBAT_DRAW_PROLIFERATE_CAPABILITY, any_counter_combat_damage_condition,
        )

        return _spec(
            "damage.dealt", "one_or_more_controlled_creatures_combat_damage_player", countered_batch["body"],
            "countered-combat-damage-draw-proliferate-v1", "trigger-event-normalized-damage",
            condition=any_counter_combat_damage_condition(),
            capabilities=(COUNTERED_COMBAT_DRAW_PROLIFERATE_CAPABILITY,),
        )
    points = _point_combat_spec(material_line)
    if points is not None:
        return points
    countered = re.fullmatch(
        r"Whenever a creature you control with a \+1/\+1 counter on it deals combat damage to a player, "
        r"(?P<body>you may draw a card[.]?)", material_line, re.IGNORECASE,
    )
    if countered is not None:
        from ..permanent_event_state_model import (
            COUNTERED_DAMAGE_SOURCE_CAPABILITY, COUNTERED_DAMAGE_SOURCE_VARIANT,
            countered_creature_combat_damage_condition,
        )

        return _spec("damage.dealt", COUNTERED_DAMAGE_SOURCE_VARIANT, countered["body"],
                     "countered-creature-combat-damage-draw-v1", "trigger-event-normalized-damage",
                     condition=countered_creature_combat_damage_condition(),
                     capabilities=(COUNTERED_DAMAGE_SOURCE_CAPABILITY,))
    batch = _PUBLIC_DAMAGE_BATCH_TRIGGER.fullmatch(material_line)
    if batch is not None:
        kind = batch.group("kind").casefold()
        return _spec(
            "damage.dealt",
            (
                "one_or_more_controlled_artifact_creatures_combat_damage_player"
                if kind.startswith("artifact")
                else "one_or_more_controlled_creatures_combat_damage_player"
            ),
            batch.group("body"),
            "fixed-counter-public-damage-trigger-v1",
            "trigger-event-normalized-damage",
            condition=_all_conditions(
                {
                    "field": "source_controller",
                    "op": "eq",
                    "value": "$source.controller",
                },
                {
                    "field": "source_types",
                    "op": "contains_any",
                    "value": ["creature"],
                },
                {
                    "field": "source_types",
                    "op": "contains_any",
                    "value": ["artifact"],
                }
                if kind.startswith("artifact")
                else None,
                {"field": "target_kind", "op": "eq", "value": "player"},
                {"field": "combat", "op": "truthy", "value": True},
            ),
        )
    qualified = _qualified_damage_singular_spec(material_line) or _qualified_damage_batch_spec(material_line)
    if qualified is not None:
        return qualified
    extended = _SOURCE_EXTENDED_DAMAGE_TRIGGER.fullmatch(material_line)
    if extended is not None:
        recipient = extended.group("recipient").casefold()
        return _spec(
            "damage.dealt",
            f"source_combat_damage_player_or_{recipient}",
            extended.group("body"),
            "fixed-counter-public-damage-trigger-v1",
            "trigger-event-normalized-damage",
            condition=_all_conditions(
                {"field": "source", "op": "eq", "value": "$source.ref"},
                {"field": "combat", "op": "truthy", "value": True},
                {
                    "any": [
                        {
                            "field": "target_kind",
                            "op": "eq",
                            "value": "player",
                        },
                        {
                            "all": [
                                {
                                    "field": "target_kind",
                                    "op": "eq",
                                    "value": "permanent",
                                },
                                {
                                    "field": "target_types",
                                    "op": "contains_any",
                                    "value": [recipient],
                                },
                            ]
                        },
                    ]
                },
            ),
        )
    opponent = _OPPONENT_NONCOMBAT_DAMAGE_TRIGGER.fullmatch(material_line)
    if opponent is None:
        return None
    return _spec(
        "damage.dealt",
        "opponent_dealt_noncombat_damage",
        opponent.group("body"),
        "fixed-counter-public-damage-trigger-v1",
        "trigger-event-normalized-damage",
        condition=_all_conditions(
            {"field": "target_kind", "op": "eq", "value": "player"},
            {
                "field": "target",
                "op": "ne",
                "value": "$source.controller",
            },
            {"field": "combat", "op": "falsy", "value": True},
        ),
    )


def _public_attack_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    match = _PUBLIC_ATTACK_BATCH_TRIGGER.fullmatch(material_line)
    if match is None:
        from .qualified_attack_batches import qualified_attack_batch_binding_spec
        return qualified_attack_batch_binding_spec(material_line)
    minimum = {"two": 2, "three": 3}.get(
        str(match.group("count") or "").casefold()
    )
    return _spec(
        "creature.attacks",
        (
            f"controller_attack_batch_at_least_{minimum}"
            if minimum is not None
            else "controller_attack_batch"
        ),
        match.group("body"),
        "fixed-counter-public-attack-trigger-v1",
        "trigger-event-normalized-public-action",
        condition=_all_conditions(
            {
                "field": "controller",
                "op": "eq",
                "value": "$source.controller",
            },
            {
                "field": "attacker_count",
                "op": "gte",
                "value": minimum,
            }
            if minimum is not None
            else None,
        ),
    )


def _player_or_walker_attacked_spec(text):
    from ..rules.attacking_opponent_library_exile import (
        ATTACKED_PLAYER_OR_WALKER_EVENT, ATTACKED_PLAYER_OR_WALKER_VARIANT,
        attack_top_exile_condition,
    )

    from .opponent_library_exile_templates import ATTACK_EXILE_BODY, ATTACK_EXILE_HEADER

    header, separator, body = text.partition(", ")
    if (not separator or header.casefold() != ATTACK_EXILE_HEADER
            or " ".join(body.casefold().replace("’", "'").split()) != ATTACK_EXILE_BODY):
        return None
    return _spec(
        ATTACKED_PLAYER_OR_WALKER_EVENT, ATTACKED_PLAYER_OR_WALKER_VARIANT, body,
        "fixed-player-or-planeswalker-attacked-v1", "trigger-event-normalized-public-action",
        condition=attack_top_exile_condition(),
    )


def direct_player_attack_binding_spec(text: str) -> FixedPublicEventBindingSpec | None:
    from ..attack_batch_model import ATTACHED_PLAYER_ATTACK_VARIANT
    from ..rules.attack_batches import ATTACKING_OPPONENT_TOKEN_CAPABILITY
    from .attack_event_results import attached_player_attack_token_body_is_closed

    match = re.fullmatch(
        r"Whenever (?P<head>you attack a player|you attack the player who has the initiative|"
        r"(?:an opponent|a player) attacks you|enchanted player is attacked)"
        r"(?P<count> with two or more creatures)?, (?P<body>.+)",
        text, re.IGNORECASE,
    )
    if match is None:
        return None
    head = match["head"].casefold()
    if head.startswith("you attack"):
        if match["count"]:
            return None
        variant = ("controller_attacks_initiative_holder" if head.endswith("initiative")
                   else "controller_attacks_player")
    elif head == "enchanted player is attacked":
        if match["count"] or not attached_player_attack_token_body_is_closed(match["body"]):
            return None
        variant = ATTACHED_PLAYER_ATTACK_VARIANT
    else:
        variant = ("opponent_attacks_controller_with_two" if match["count"]
                   else "opponent_attacks_controller")
    capabilities = (DIRECT_PLAYER_ATTACK_CAPABILITY,)
    if variant == "controller_attacks_initiative_holder":
        capabilities += (TAKE_INITIATIVE_CAPABILITY,)
    if variant == ATTACHED_PLAYER_ATTACK_VARIANT:
        capabilities += (ATTACKING_OPPONENT_TOKEN_CAPABILITY,)
    body = match["body"]
    if (variant == "opponent_attacks_controller"
            and body.casefold() == "choose target creature attacking you. put a stun counter on that creature."):
        body = "Put a stun counter on target creature that's attacking you."
    return _spec(
        DIRECT_PLAYER_ATTACK_EVENT, variant, body, "fixed-counter-direct-player-attack-v1",
        "trigger-event-normalized-public-action", condition=direct_player_attack_condition(variant),
        capabilities=capabilities,
    )


def _public_cycle_spec(
    material_line: str,
) -> FixedPublicEventBindingSpec | None:
    match = _PUBLIC_CONTROLLER_CYCLE_TRIGGER.fullmatch(material_line)
    if match is None:
        return None
    return _spec(
        "card.cycled",
        "controller_cycles",
        match.group("body"),
        "fixed-counter-public-cycle-trigger-v1",
        "trigger-event-normalized-public-action",
        condition={
            "field": "player",
            "op": "eq",
            "value": "$source.controller",
        },
    )


def _creature_activation_draw_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    from ..rules.creature_ability_activation import (
        CREATURE_ABILITY_ACTIVATED, CREATURE_ABILITY_ACTIVATION_CAPABILITY, creature_ability_activator_condition,
    )

    match = re.fullmatch(r"Whenever you activate an ability of a creature, (?P<body>draw a card\.)", material_line, re.I)
    if match is None:
        return None
    return _spec(
        CREATURE_ABILITY_ACTIVATED, "controller_activates_creature_ability", match["body"],
        "fixed-creature-activation-draw-v1", "trigger-event-normalized-public-action",
        condition=creature_ability_activator_condition(), capabilities=(CREATURE_ABILITY_ACTIVATION_CAPABILITY,),
    )


def _public_ninjutsu_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    from ..combat_entry_activations import (
        NINJUTSU_ACTIVATED_CAPABILITY, NINJUTSU_ACTIVATED_EVENT, ninjutsu_activation_controller_condition,
    )

    match = re.fullmatch(r"Whenever you activate a ninjutsu ability, (?P<body>.+)", material_line, re.I)
    if match is None:
        return None
    return _spec(
        NINJUTSU_ACTIVATED_EVENT, "controller_activates_ninjutsu", match["body"],
        "fixed-controller-ninjutsu-activation-trigger-v1", "trigger-event-normalized-public-action",
        condition=ninjutsu_activation_controller_condition(),
        capabilities=(NINJUTSU_ACTIVATED_CAPABILITY,),
    )


def fixed_ninjutsu_observer_effect(compiled):
    """Add the explicit hand-size offer to the exact private observer result."""
    from ..rules.library_selection_capability_shapes import (
        PRIVATE_ONE_HAND_ORDERED_BOTTOM_VERSION, private_one_hand_ordered_bottom_shape,
    )

    template, effects, target_schema, mechanics = compiled
    if (template is None or len(effects) != 1 or target_schema is not None
            or not private_one_hand_ordered_bottom_shape(effects[0])):
        return None, (), None, ()
    effect = {**effects[0], "schema_version": PRIVATE_ONE_HAND_ORDERED_BOTTOM_VERSION}
    return template, (effect,), None, mechanics


def _equipment_unattachment_spec(material_line):
    match = re.fullmatch(
        r"Whenever this Equipment becomes unattached from a permanent, (?P<body>sacrifice that permanent\.)",
        material_line, re.IGNORECASE,
    )
    if match is None:
        return None
    return _spec(UNATTACHMENT_EVENT, SOURCE_UNATTACHMENT_VARIANT, match["body"],
        "fixed-source-equipment-unattached-v1", "trigger-event-normalized-public-action",
        condition={"field": "card", "op": "eq", "value": "$source.ref"},
        capabilities=(UNATTACHMENT_TRIGGER_CAPABILITY,))


def fixed_public_action_event_binding_spec(
    material_line: str,
    *,
    card_name: str | None = None,
) -> FixedPublicEventBindingSpec | None:
    """Parse one bounded action/result occurrence without compiling its body."""

    return (
        direct_player_attack_binding_spec(material_line)
        or _player_or_walker_attacked_spec(material_line)
        or day_night_counter_binding(material_line, card_name=card_name)
        or _equipment_unattachment_spec(material_line)
        or _public_attack_spec(material_line)
        or _public_land_play_spec(material_line)
        or _controller_life_loss_draw_spec(material_line)
        or _source_mana_tap_damage_spec(material_line)
        or _land_mana_tap_damage_spec(material_line)
        or _mana_tapped_land_untap_spec(material_line)
        or _public_expend_spec(material_line)
        or _public_ring_spec(material_line)
        or _public_waterbend_spec(material_line)
        or _public_library_action_spec(material_line)
        or _public_damage_spec(material_line)
        or _public_zone_spec(material_line, card_name=card_name)
        or _public_cycle_spec(material_line)
        or _creature_activation_draw_spec(material_line)
        or _public_ninjutsu_spec(material_line)
    )


def _controller_life_loss_draw_spec(material_line):
    from ..life_change import LIFE_LOSS_CAPABILITY, LIFE_LOSS_EVENT
    from ..rules.event_outcome_shapes import CONTROLLER_LIFE_LOSS_DRAW_VARIANT, controller_life_loss_draw_condition

    match = re.fullmatch(r"Whenever you lose life, (?P<body>draw that many cards\.)", material_line, re.I)
    if match is None:
        return None
    return _spec(
        LIFE_LOSS_EVENT, CONTROLLER_LIFE_LOSS_DRAW_VARIANT, match["body"],
        "fixed-counter-controller-life-loss-draw-v1", "trigger-event-normalized-life-loss",
        condition=controller_life_loss_draw_condition(), capabilities=(LIFE_LOSS_CAPABILITY,),
    )


def _source_mana_tap_damage_spec(material_line):
    from ..triggered_mana_model import (
        SOURCE_MANA_TAP_CAPABILITY, SOURCE_MANA_TAP_EVENT, SOURCE_MANA_TAP_VARIANT,
        source_mana_tap_condition,
    )

    match = re.fullmatch(
        r"Whenever you tap this creature for mana, (?P<body>it deals 1 damage to each opponent\.)",
        material_line, re.I,
    )
    if match is None:
        return None
    return _spec(
        SOURCE_MANA_TAP_EVENT, SOURCE_MANA_TAP_VARIANT, match["body"],
        "fixed-counter-source-mana-tap-trigger-v1", "trigger-event-normalized-public-action",
        condition=source_mana_tap_condition(), capabilities=(SOURCE_MANA_TAP_CAPABILITY,),
    )


def _land_mana_tap_damage_spec(material_line):
    from ..triggered_mana_model import LAND_MANA_TAP_EVENT, LAND_MANA_TAP_VARIANT, land_mana_tap_condition

    match = re.fullmatch(
        r"Whenever a player taps a (?P<nonbasic>nonbasic )?land for mana, "
        r"(?P<body>this enchantment deals 1 damage to that player\.)", material_line, re.I,
    )
    if match is None:
        return None
    return _spec(
        LAND_MANA_TAP_EVENT, LAND_MANA_TAP_VARIANT, match["body"],
        "fixed-counter-land-mana-tap-trigger-v1", "trigger-event-normalized-public-action",
        condition=land_mana_tap_condition(nonbasic=match["nonbasic"] is not None),
    )


def _mana_tapped_land_untap_spec(material_line):
    from ..rules.tap_state_capability_shapes import MANA_TAPPED_LAND_UNTAP_VARIANT, mana_tapped_land_untap_condition
    from ..triggered_mana_model import LAND_MANA_TAP_EVENT

    match = re.fullmatch(
        r"Whenever an opponent taps a land for mana, "
        r"(?P<body>that land doesn['’]t untap during its controller['’]s next untap step\.)", material_line, re.I,
    )
    if match is None:
        return None
    return _spec(
        LAND_MANA_TAP_EVENT, MANA_TAPPED_LAND_UNTAP_VARIANT, match["body"],
        "fixed-counter-mana-tapped-land-untap-v1", "trigger-event-normalized-public-action",
        condition=mana_tapped_land_untap_condition(),
    )


def _public_waterbend_spec(material_line):
    match=re.fullmatch(r'Whenever (?P<actor>you|a player|an opponent) (?P<verb>waterbend|waterbends), (?P<body>.+)',material_line,re.I)
    if match is None or (match['actor'].casefold()=='you')!=(match['verb'].casefold()=='waterbend'):return None
    from ..waterbend import WATERBEND_CAPABILITY,WATERBEND_EVENT
    return _spec(WATERBEND_EVENT,'public_waterbend',match['body'],'fixed-waterbend-trigger-v1',
        'trigger-event-normalized-public-action',condition=_actor_condition(match['actor'],field='player'),capabilities=(WATERBEND_CAPABILITY,))


def _public_expend_spec(material_line):
    match=re.fullmatch(r'Whenever you expend (?P<threshold>[1-9]\d*), (?P<body>.+)',material_line,re.I)
    if match is None:return None
    from ..expend_model import EXPEND_CAPABILITY,SPELL_MANA_PAYMENT_EVENT
    threshold=int(match['threshold'])
    return _spec(SPELL_MANA_PAYMENT_EVENT,'player_expend',match['body'],
        'fixed-expend-trigger-v1','cr-603-handling-triggered-abilities',
        condition={'all':[{'field':'player','op':'eq','value':'$source.controller'},
            {'field':'mana_before','op':'lt','value':threshold},
            {'field':'mana_after','op':'gte','value':threshold}]},capabilities=(EXPEND_CAPABILITY,))


def _public_ring_spec(material_line):
    from ..ring_model import RING_TEMPTED_EVENT, RING_TEMPTATION_CAPABILITY
    for phrase, variant, chosen in (
        ('the Ring tempts you', 'public_ring_temptation', False),
        ('you choose a creature as your Ring-bearer', 'public_ring_bearer_choice', True),
    ):
        match = re.fullmatch(r'Whenever ' + re.escape(phrase) + r', (?P<body>.+)', material_line, re.I)
        if match is None:
            continue
        return _spec(RING_TEMPTED_EVENT, variant, match['body'],
            'fixed-ring-action-trigger-v1', 'trigger-event-normalized-public-action',
            condition=_all_conditions(_actor_condition('you', field='player'),
                {'field': 'creature_chosen', 'op': 'eq', 'value': True} if chosen else None),
            capabilities=(RING_TEMPTATION_CAPABILITY,))
    return None


def _public_library_action_spec(material_line: str) -> FixedPublicEventBindingSpec | None:
    match = re.fullmatch(
        r"Whenever (?P<actor>you|a player|an opponent) "
        r"(?P<action>scry|scries|surveil|surveils), (?P<body>.+)", material_line, re.I,
    )
    if match is None:
        return None
    actor = match["actor"].casefold()
    action = match["action"].casefold()
    if (actor == "you") != (action in {"scry", "surveil"}):
        return None
    event = "player.scried" if action in {"scry", "scries"} else "player.surveilled"
    return _spec(
        event, "public_library_action", match["body"], "fixed-library-action-trigger-v1",
        "trigger-event-normalized-public-action", condition=_actor_condition(actor, field="player"),
    )


__all__ = ["fixed_public_action_event_binding_spec"]
