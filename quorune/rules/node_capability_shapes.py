from __future__ import annotations

"""Strict CardProgram node shapes with reviewed capability ownership."""

from typing import Any, Iterable, Mapping, Sequence

from .casting_additional_costs import (
    AdditionalCostError,
    FixedCounterPlacementAdditionalCost,
    FixedSacrificeAdditionalCost,
    FixedZoneChangeAdditionalCost,
)
from .casting_additional_cost_groups import (
    fixed_alternative_additional_cost_node_capabilities,
    fixed_life_payment_additional_cost_node_capabilities,
    fixed_zone_change_additional_cost_capability,
)
from ..attachment_references import (
    AttachmentReferenceError,
    AttachmentReferenceSpec,
)
from ..compiler.counter_placement_templates import (
    fixed_counter_set_spec_is_closed,
)
from ..compiler.direct_target import DirectPermanentTargetSpec
from ..compiler.fixed_target_effect_sequences import (
    FIXED_TARGET_CHARACTERISTIC_KEYWORDS,
)
from ..compiler.fixed_source_effect_sequences import (
    FIXED_SOURCE_SEQUENCE_MECHANIC,
    SOURCE_ZONE_OBJECT,
)
from ..keyword_counters import keyword_counter_mechanic
from .temporary_declaration_restrictions import (
    TEMPORARY_DECLARATION_RESTRICTION_KINDS,
    temporary_declaration_restriction,
)
from ..zone_object_keyword_model import ZONE_OBJECT_KEYWORDS
from .amass_capability_shapes import fixed_amass_node_capabilities
from .cumulative_upkeep_capability_shapes import (
    fixed_life_cumulative_upkeep_node_capabilities,
    fixed_mana_cumulative_upkeep_node_capabilities,
)
from .permanent_predicate_capability_shapes import (
    direct_permanent_target_schema_is_closed,
    direct_target_predicate_capabilities,
    fixed_counter_target_schema_is_closed,
    fixed_counter_target_set_state_capabilities,
    public_state_query_capabilities,
)
from .tap_state_capability_shapes import targeted_tap_state_node_capabilities
from ..affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
    PermanentControllerRelation as AffectedControllerRelation,
)
from ..fixed_damage_set_model import (
    FixedDamageSetError,
    FixedDamageSetSpec,
    PermanentControllerRelation,
    PermanentDamageGroup,
    PlayerDamageGroup,
)

_EXILE_MECHANIC = "exile"


def fixed_counter_additional_cost_node_capabilities(
    *, cost_schema: Mapping[str, Any] | None
) -> tuple[str, ...]:
    """Recognize exactly one mandatory fixed counter casting cost."""

    if not isinstance(cost_schema, Mapping) or set(cost_schema) != {
        "additional_costs"
    }:
        return ()
    raw_costs = cost_schema.get("additional_costs")
    if not isinstance(raw_costs, list) or len(raw_costs) != 1:
        return ()
    try:
        FixedCounterPlacementAdditionalCost.from_descriptor(raw_costs[0])
    except (AdditionalCostError, TypeError):
        return ()
    return ("casting.additional_cost.fixed_counter_placement",)


def fixed_sacrifice_additional_cost_node_capabilities(
    *, cost_schema: Mapping[str, Any] | None
) -> tuple[str, ...]:
    """Recognize exactly one mandatory fixed sacrifice casting cost."""

    if not isinstance(cost_schema, Mapping) or set(cost_schema) != {
        "additional_costs"
    }:
        return ()
    raw_costs = cost_schema.get("additional_costs")
    if not isinstance(raw_costs, list) or len(raw_costs) != 1:
        return ()
    try:
        FixedSacrificeAdditionalCost.from_descriptor(raw_costs[0])
    except (AdditionalCostError, TypeError):
        return ()
    return ("casting.additional_cost.fixed_sacrifice",)


def fixed_zone_change_additional_cost_node_capabilities(
    *, cost_schema: Mapping[str, Any] | None
) -> tuple[str, ...]:
    """Recognize one typed single-object zone-change casting cost."""

    if not isinstance(cost_schema, Mapping) or set(cost_schema) != {
        "additional_costs"
    }:
        return ()
    raw_costs = cost_schema.get("additional_costs")
    if not isinstance(raw_costs, list) or len(raw_costs) != 1:
        return ()
    try:
        cost = FixedZoneChangeAdditionalCost.from_descriptor(raw_costs[0])
    except (AdditionalCostError, TypeError):
        return ()
    return (fixed_zone_change_additional_cost_capability(cost),)


_FIXED_DAMAGE_TARGET_SCHEMAS: dict[str, Mapping[str, Any]] = {
    "any_target": {
        "zones": ["player", "battlefield"],
        "categories": ["player", "permanent"],
        "predicate": "damageable",
        "count": 1,
    },
    "creature": {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "types_any": ["creature"],
        "count": 1,
    },
    "creature_or_planeswalker": {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "types_any": ["creature", "planeswalker"],
        "count": 1,
    },
    "player_or_planeswalker": {
        "zones": ["player", "battlefield"],
        "categories": ["player", "permanent"],
        "predicate": "player_or_planeswalker",
        "count": 1,
    },
    "opponent_or_planeswalker": {
        "zones": ["player", "battlefield"],
        "categories": ["player", "permanent"],
        "predicate": "player_or_planeswalker",
        "count": 1,
        "player_relation": "opponent",
    },
    "player": {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
    },
    "opponent": {
        "zones": ["player"],
        "categories": ["player"],
        "count": 1,
        "player_relation": "opponent",
    },
}
_PLAYER_DAMAGE_DOMAINS = frozenset(
    {
        "any_target",
        "player_or_planeswalker",
        "opponent_or_planeswalker",
        "player",
        "opponent",
    }
)
_PERMANENT_DAMAGE_DOMAINS = frozenset(
    {
        "any_target",
        "creature",
        "creature_or_planeswalker",
        "player_or_planeswalker",
        "opponent_or_planeswalker",
    }
)

_DRAW_TARGET_SCHEMAS: tuple[Mapping[str, Any], ...] = (
    {
        "zones": ["player"],
        "categories": ["player"],
        "player_relation": "any",
        "count": 1,
    },
    {
        "zones": ["player"],
        "categories": ["player"],
        "player_relation": "opponent",
        "count": 1,
    },
)

_TARGETED_RETURN_TO_HAND_SCHEMAS: tuple[Mapping[str, Any], ...] = (
    *tuple(
        {
            "zones": ["battlefield"],
            "categories": ["permanent"],
            **({"types_any": list(kinds)} if kinds else {}),
            "count": 1,
        }
        for kinds in (
            ("artifact",),
            ("creature",),
            ("enchantment",),
            ("land",),
            (),
            ("artifact", "enchantment"),
            ("creature", "planeswalker"),
        )
    ),
    {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "types_none": ["land"],
        "count": 1,
    },
)
_COUNTER_STACK_BASE = {
    "zones": ["stack"],
    "categories": ["spell"],
    "source_exclusion": True,
    "count": 1,
}
_TARGETED_EXPLORE_SCHEMA = {
    "zones": ["battlefield"],
    "categories": ["permanent"],
    "types_any": ["creature"],
    "controller_relation": "you",
    "count": 1,
}
_FIXED_TARGET_SEQUENCE_MECHANIC = "fixed-target-effect-sequence"
_FIXED_TARGET_SEQUENCE_KEYWORDS = frozenset(
    value.title() for value in FIXED_TARGET_CHARACTERISTIC_KEYWORDS
)
_FIXED_TARGET_ZONE_OBJECT_KEYWORDS = frozenset(
    value.title() for value in ZONE_OBJECT_KEYWORDS
)
_TARGETED_COUNTER_SCHEMAS: tuple[Mapping[str, Any], ...] = (
    _COUNTER_STACK_BASE,
    {**_COUNTER_STACK_BASE, "types_none": ["creature"]},
    *tuple(
        {**_COUNTER_STACK_BASE, "types_any": list(types)}
        for types in (
            ("creature",),
            ("creature", "planeswalker"),
            ("instant", "sorcery"),
            ("sorcery",),
            ("instant",),
            ("artifact", "enchantment"),
            ("artifact",),
            ("creature", "enchantment"),
            ("artifact", "creature"),
        )
    ),
    *tuple(
        {**_COUNTER_STACK_BASE, "colors_any": list(colors)}
        for colors in (("U",), ("R",), ("G",), ("R", "G"))
    ),
    {**_COUNTER_STACK_BASE, "predicate": "nonblue_spell"},
    {**_COUNTER_STACK_BASE, "colorless": True},
    {
        "zones": ["stack"],
        "categories": ["ability"],
        "source_exclusion": True,
        "predicate": "activated_ability",
        "count": 1,
    },
    {
        "zones": ["stack"],
        "categories": ["ability"],
        "source_exclusion": True,
        "predicate": "triggered_ability",
        "count": 1,
    },
    {
        "zones": ["stack"],
        "categories": ["ability"],
        "source_exclusion": True,
        "count": 1,
    },
    {
        "zones": ["stack"],
        "categories": ["spell", "ability"],
        "source_exclusion": True,
        "count": 1,
    },
)


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def fixed_damage_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for the closed fixed-damage node vocabulary."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "cr-120-damage" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if (
        set(effect) == {"op", "source", "amount", "groups"}
        and effect.get("op") == "damage_fixed_set"
        and effect.get("source") == "$source"
        and _positive_int(effect.get("amount"))
    ):
        try:
            spec = FixedDamageSetSpec.from_dict(
                {"groups": effect.get("groups")}
            )
        except FixedDamageSetError:
            return ()
        targeted = any(
            isinstance(group, PermanentDamageGroup)
            and group.controller_relation
            is PermanentControllerRelation.TARGET_PLAYER
            for group in spec.groups
        )
        expected_target = (
            {
                "zones": ["player"],
                "categories": ["player"],
                "player_relation": "opponent",
                "count": 1,
            }
            if targeted
            else None
        )
        if target_schema != expected_target or (
            targeted and "cr-115-targets" not in mechanics
        ):
            return ()
        dependencies = {
            "damage.amount.positive",
            "damage.batch.fixed_set",
        }
        if any(isinstance(group, PlayerDamageGroup) for group in spec.groups):
            dependencies.add("damage.result.player_life")
        if any(
            isinstance(group, PermanentDamageGroup) for group in spec.groups
        ):
            dependencies.add("damage.result.multitype_permanent")
        if targeted:
            dependencies.add("target.revalidate_resolution")
        return tuple(sorted(dependencies))
    if (
        target_schema is None
        and set(effect) == {"op", "source", "amount"}
        and effect.get("op") == "damage_each_opponent"
        and effect.get("source") == "$source"
        and _positive_int(effect.get("amount"))
    ):
        return (
            "damage.amount.positive",
            "damage.result.player_life",
        )
    if (
        "cr-115-targets" not in mechanics
        or set(effect) != {"op", "source", "target", "amount"}
        or effect.get("op") != "damage"
        or effect.get("source") != "$source"
        or effect.get("target") != "$target.0"
        or not _positive_int(effect.get("amount"))
    ):
        return ()
    schema = dict(target_schema or {})
    domain = next(
        (
            name
            for name, expected in _FIXED_DAMAGE_TARGET_SCHEMAS.items()
            if schema == expected
        ),
        None,
    )
    direct_target = None
    if domain is None and direct_permanent_target_schema_is_closed(schema):
        direct_target = DirectPermanentTargetSpec.from_target_schema(schema)
        if direct_target.combat_state is None:
            direct_target = None
    if domain is None and direct_target is None:
        return ()
    dependencies = {"damage.amount.positive"}
    if domain in _PLAYER_DAMAGE_DOMAINS:
        dependencies.add("damage.result.player_life")
    if domain in _PERMANENT_DAMAGE_DOMAINS or direct_target is not None:
        dependencies.add("damage.result.multitype_permanent")
    if direct_target is not None:
        dependencies.update(direct_target_predicate_capabilities(schema))
        dependencies.add("target.revalidate_resolution")
    else:
        dependencies.add(
            "target.public.player_or_damageable_permanent"
            if domain == "any_target"
            else "target.revalidate_resolution"
        )
    return tuple(sorted(dependencies))


def fixed_draw_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return the draw capability only for the closed fixed-count grammar."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "cr-121-drawing-a-card" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    operation = effect.get("op")
    if operation == "draw_with_actions":
        expected_actions = [
            {"action": "reveal", "public": True},
            {
                "action": "discard_unless_type",
                "card_type": "land",
            },
        ]
        if (
            target_schema is None
            and set(effect)
            == {
                "op",
                "player",
                "count",
                "private",
                "post_draw_actions",
            }
            and effect.get("player") == "$controller"
            and effect.get("count") == 1
            and type(effect.get("count")) is int
            and effect.get("private") is True
            and effect.get("post_draw_actions") == expected_actions
        ):
            return ("zone.draw.specifically_drawn_card_actions",)
        return ()
    if (
        target_schema is None
        and operation == "draw_each_player"
        and set(effect) == {"op", "count"}
        and _positive_int(effect.get("count"))
    ):
        return ("zone.draw.library_to_hand",)
    if operation not in {"draw", "offer_draw"}:
        return ()
    expected_fields = (
        {"op", "player", "count", "private"}
        if operation == "draw"
        else {"op", "player", "drawer", "count", "private"}
    )
    if (
        set(effect) != expected_fields
        or not _positive_int(effect.get("count"))
        or effect.get("private") is not True
    ):
        return ()
    player = effect.get("player")
    drawer = effect.get("drawer", player)
    if player == "$controller" and drawer == "$controller":
        return (
            ("zone.draw.library_to_hand",)
            if target_schema is None
            else ()
        )
    if (
        (
            (operation == "draw" and player == "$target.0")
            or (operation == "offer_draw" and player == "$controller")
        )
        and drawer == "$target.0"
        and dict(target_schema or {}) in _DRAW_TARGET_SCHEMAS
        and "cr-115-targets" in mechanics
    ):
        return (
            "target.revalidate_resolution",
            "zone.draw.library_to_hand",
        )
    return ()


def fixed_scry_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return the Scry capability only for one fixed controller instruction."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "scry" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if (
        target_schema is None
        and set(effect) == {"op", "player", "count"}
        and effect.get("op") == "scry"
        and effect.get("player") == "$controller"
        and _positive_int(effect.get("count"))
    ):
        return ("library.scry.fixed_controller",)
    return ()


def single_explore_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for one permanent exploring once."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "explore" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "player", "card"}
        or effect.get("op") != "explore"
    ):
        return ()
    if (
        target_schema is None
        and effect.get("player") == "$source.controller"
        and effect.get("card") == "$source"
    ):
        return ("keyword_action.explore.single",)
    if (
        "cr-115-targets" in mechanics
        and dict(target_schema or {}) == _TARGETED_EXPLORE_SCHEMA
        and effect.get("player") == "$target.controller.0"
        and effect.get("card") == "$target.0"
    ):
        return (
            "keyword_action.explore.single",
            "target.revalidate_resolution",
        )
    return ()


def single_proliferate_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return the capability for one unmodified Proliferate instruction."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        "proliferate" not in mechanics
        or target_schema is not None
        or len(effects) != 1
        or dict(effects[0]) != {"op": "proliferate"}
    ):
        return ()
    return ("counter.producer.proliferate",)


def fixed_self_counter_keyword_action_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership for one exact fixed self-counter keyword action."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        target_schema is not None
        or len(effects) != 1
        or "cr-122-counters" not in mechanics
    ):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "action", "amount", "source"}
        or effect.get("op") != "fixed_self_counter_keyword_action"
        or effect.get("source") != "$source"
        or not _positive_int(effect.get("amount"))
    ):
        return ()
    action = effect.get("action")
    if action == "adapt" and "adapt" in mechanics:
        return ("keyword_action.adapt.fixed",)
    if action == "monstrosity" and "monstrosity" in mechanics:
        return ("keyword_action.monstrosity.fixed",)
    if action == "renown" and "renown" in mechanics:
        return ("counter.producer.renown",)
    return ()


def fixed_bolster_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership for one exact fixed positive Bolster action."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        target_schema is not None
        or len(effects) != 1
        or not {"bolster", "cr-122-counters"}.issubset(mechanics)
    ):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "player", "amount"}
        or effect.get("op") != "fixed_bolster"
        or effect.get("player") != "$controller"
        or not _positive_int(effect.get("amount"))
    ):
        return ()
    return ("counter.producer.bolster",)


def targeted_destruction_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for the closed direct destruction grammar."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        not {"destroy", "cr-115-targets"}.issubset(mechanics)
        or len(effects) != 1
        or not direct_permanent_target_schema_is_closed(target_schema)
    ):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "card"}
        or effect.get("op") != "destroy"
        or effect.get("card") != "$target.0"
    ):
        return ()
    assert target_schema is not None
    return (
        "permanent.destroy.effect",
        *direct_target_predicate_capabilities(target_schema),
        "target.revalidate_resolution",
    )


def self_regeneration_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership only for one self-zone-object regeneration action."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if mechanics != {"regenerate"} or target_schema is not None:
        return ()
    if len(effects) != 1:
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "card"}
        or effect.get("op") != "regenerate"
        or effect.get("card") != SOURCE_ZONE_OBJECT
    ):
        return ()
    return ("permanent.regeneration.self_activation",)


def mass_destruction_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for the closed fixed-set destruction grammar."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if not {"destroy", "destroy-fixed-set"}.issubset(mechanics) or len(effects) != 1:
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "source", "set"}
        or effect.get("op") != "destroy_all"
        or effect.get("source") != "$source"
    ):
        return ()
    try:
        spec = AffectedPermanentSetSpec.from_dict(effect["set"])
    except (AffectedPermanentSetError, KeyError, TypeError):
        return ()
    targeted = spec.controller_relation is AffectedControllerRelation.TARGET_PLAYER
    schema = dict(target_schema or {})
    valid_player_schemas = (
        {
            "zones": ["player"],
            "categories": ["player"],
            "count": 1,
            "player_relation": "any",
        },
        {
            "zones": ["player"],
            "categories": ["player"],
            "count": 1,
            "player_relation": "opponent",
        },
    )
    if targeted:
        if "cr-115-targets" not in mechanics or schema not in valid_player_schemas:
            return ()
        if spec.target_controller != "$target.0":
            return ()
        return (
            "permanent.destroy.fixed_set",
            "target.revalidate_resolution",
        )
    if target_schema is not None or "cr-115-targets" in mechanics:
        return ()
    return ("permanent.destroy.fixed_set",)


def targeted_return_to_hand_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for the closed direct battlefield grammar."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    direct_target = (
        DirectPermanentTargetSpec.from_target_schema(target_schema)
        if direct_permanent_target_schema_is_closed(target_schema)
        else None
    )
    if (
        not {"return-to-owner-hand", "cr-115-targets"}.issubset(mechanics)
        or len(effects) != 1
        or (
            dict(target_schema or {}) not in _TARGETED_RETURN_TO_HAND_SCHEMAS
            and not (
                direct_target is not None
                and direct_target.combat_state is not None
            )
        )
    ):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "card"}
        or effect.get("op") != "bounce"
        or effect.get("card") != "$target.0"
    ):
        return ()
    return (
        "permanent.return.owner_hand",
        *(
            direct_target_predicate_capabilities(target_schema)  # type: ignore[arg-type]
            if direct_target is not None
            else ()
        ),
        "target.revalidate_resolution",
    )


def targeted_exile_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for the closed direct permanent exile."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        not {_EXILE_MECHANIC, "cr-115-targets"}.issubset(mechanics)
        or len(effects) != 1
        or not direct_permanent_target_schema_is_closed(target_schema)
    ):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "card"}
        or effect.get("op") != "exile_permanent"
        or effect.get("card") != "$target.0"
    ):
        return ()
    assert target_schema is not None
    return (
        "permanent.exile.effect",
        *direct_target_predicate_capabilities(target_schema),
        "target.revalidate_resolution",
    )


def targeted_counter_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for the closed direct stack-counter grammar."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        not {"counter", "cr-115-targets"}.issubset(mechanics)
        or len(effects) != 1
        or dict(target_schema or {}) not in _TARGETED_COUNTER_SCHEMAS
    ):
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "stack"}
        or effect.get("op") != "counter_stack_target"
        or effect.get("stack") != "$target.0"
    ):
        return ()
    return (
        "stack.counter.effect",
        "target.revalidate_resolution",
    )


def fixed_counter_placement_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for one closed fixed counter placement."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "cr-122-counters" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "card", "counter", "amount", "source"}
        or effect.get("op") != "place_counters"
        or type(effect.get("counter")) is not str
        or not effect.get("counter")
        or type(effect.get("amount")) is not int
        or effect.get("amount", 0) <= 0
        or effect.get("source") != "$source"
    ):
        return ()
    counter_mechanic = keyword_counter_mechanic(effect.get("counter"))
    if counter_mechanic is not None and counter_mechanic not in mechanics:
        return ()
    characteristic_capabilities = (
        ("counter.characteristic.keyword",)
        if counter_mechanic is not None
        else ()
    )
    if target_schema is None and effect.get("card") in ("$source", SOURCE_ZONE_OBJECT):
        return ("counter.producer.fixed_effect", *characteristic_capabilities)
    if target_schema is None and isinstance(effect.get("card"), Mapping):
        try:
            AttachmentReferenceSpec.from_dict(effect["card"])
        except (AttachmentReferenceError, TypeError):
            return ()
        return (
            "counter.producer.fixed_attached_effect",
            *characteristic_capabilities,
        )
    if (
        "cr-115-targets" in mechanics
        and effect.get("card") == "$target.0"
        and fixed_counter_target_schema_is_closed(target_schema)
    ):
        assert target_schema is not None
        target_capabilities = direct_target_predicate_capabilities(target_schema)
        return (
            "counter.producer.fixed_effect",
            *characteristic_capabilities,
            *target_capabilities,
            "target.revalidate_resolution",
        )
    return ()


def fixed_counter_placement_batch_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership for one closed fixed multi-kind counter batch."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "cr-122-counters" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "card", "placements", "source"}
        or effect.get("op") != "place_counter_batch"
        or effect.get("source") != "$source"
    ):
        return ()
    placements = effect.get("placements")
    if not isinstance(placements, (list, tuple)) or not 2 <= len(
        placements
    ) <= 3:
        return ()
    names: set[str] = set()
    keyword_counter = False
    for placement in placements:
        if not isinstance(placement, Mapping) or set(placement) != {
            "counter",
            "amount",
        }:
            return ()
        counter_name = placement.get("counter")
        amount = placement.get("amount")
        if (
            type(counter_name) is not str
            or not counter_name
            or counter_name != " ".join(counter_name.casefold().split())
            or counter_name in names
            or type(amount) is not int
            or amount <= 0
        ):
            return ()
        names.add(counter_name)
        counter_mechanic = keyword_counter_mechanic(counter_name)
        if counter_mechanic is not None:
            if counter_mechanic not in mechanics:
                return ()
            keyword_counter = True
    result = (
        "counter.producer.fixed_multikind_effect",
        *(("counter.characteristic.keyword",) if keyword_counter else ()),
    )
    if target_schema is None and effect.get("card") == "$source":
        return result
    if (
        "cr-115-targets" in mechanics
        and effect.get("card") == "$target.0"
        and fixed_counter_target_schema_is_closed(target_schema)
    ):
        assert target_schema is not None
        return (
            *result,
            *direct_target_predicate_capabilities(target_schema),
            "target.revalidate_resolution",
        )
    return ()


def fixed_target_effect_sequence_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership for one closed target-threaded counter sequence."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if any(
        effect.get("op") == "grant_zone_object_keyword"
        for effect in effects
    ):
        if not {
            _FIXED_TARGET_SEQUENCE_MECHANIC,
            "cr-115-targets",
            "cr-122-counters",
            "cr-611-continuous-effects",
        }.issubset(mechanics) or len(effects) != 2:
            return ()
        counter, grant = effects
        if (
            set(counter) != {"op", "card", "counter", "amount", "source"}
            or counter.get("op") != "place_counters"
            or counter.get("card") != "$target.0"
            or type(counter.get("counter")) is not str
            or not counter.get("counter")
            or type(counter.get("amount")) is not int
            or counter.get("amount", 0) <= 0
            or counter.get("source") != "$source"
            or not fixed_counter_target_schema_is_closed(target_schema)
        ):
            return ()
        keyword = grant.get("keyword")
        keyword_mechanic = keyword_counter_mechanic(keyword)
        if (
            set(grant) != {"op", "card", "keyword"}
            or grant.get("op") != "grant_zone_object_keyword"
            or grant.get("card") != "$target.0"
            or keyword not in _FIXED_TARGET_ZONE_OBJECT_KEYWORDS
            or keyword_mechanic is None
            or keyword_mechanic not in mechanics
        ):
            return ()
        counter_mechanic = keyword_counter_mechanic(counter.get("counter"))
        if counter_mechanic is not None and counter_mechanic not in mechanics:
            return ()
        return (
            "continuous.resolution.fixed_keyword_zone_object",
            *(("counter.characteristic.keyword",) if counter_mechanic else ()),
            "counter.producer.fixed_effect",
            "resolution.effect_sequence.fixed_target",
            *direct_target_predicate_capabilities(target_schema),
            "target.revalidate_resolution",
        )
    if not {
        _FIXED_TARGET_SEQUENCE_MECHANIC,
        "cr-115-targets",
        "cr-122-counters",
        "cr-611-continuous-effects",
    }.issubset(mechanics) or not 2 <= len(effects) <= 4:
        return ()
    if not fixed_counter_target_schema_is_closed(target_schema):
        return ()
    assert target_schema is not None
    counter_count = 0
    keyword_counter = False
    characteristic_count = 0
    granted_keywords: set[str] = set()
    for effect in effects:
        operation = effect.get("op")
        if operation == "place_counters":
            if (
                set(effect) != {"op", "card", "counter", "amount", "source"}
                or effect.get("card") != "$target.0"
                or type(effect.get("counter")) is not str
                or not effect.get("counter")
                or type(effect.get("amount")) is not int
                or effect.get("amount", 0) <= 0
                or effect.get("source") != "$source"
            ):
                return ()
            counter_mechanic = keyword_counter_mechanic(effect.get("counter"))
            if counter_mechanic is not None:
                if counter_mechanic not in mechanics:
                    return ()
                keyword_counter = True
            counter_count += 1
            continue
        if operation == "modify_stats_until_end_of_turn":
            if (
                set(effect) != {"op", "card", "power", "toughness"}
                or effect.get("card") != "$target.0"
                or type(effect.get("power")) is not int
                or type(effect.get("toughness")) is not int
                or (effect.get("power") == 0 and effect.get("toughness") == 0)
            ):
                return ()
            characteristic_count += 1
            continue
        if operation == "grant_keyword_until_end_of_turn":
            keyword = effect.get("keyword")
            if (
                set(effect) != {"op", "card", "keyword"}
                or effect.get("card") != "$target.0"
                or keyword not in _FIXED_TARGET_SEQUENCE_KEYWORDS
                or keyword in granted_keywords
            ):
                return ()
            granted_keywords.add(keyword)
            characteristic_count += 1
            continue
        return ()
    if counter_count != 1 or characteristic_count < 1:
        return ()
    return (
        "continuous.resolution.fixed_characteristics_until_end_of_turn",
        *(("counter.characteristic.keyword",) if keyword_counter else ()),
        "counter.producer.fixed_effect",
        "resolution.effect_sequence.fixed_target",
        *direct_target_predicate_capabilities(target_schema),
        "target.revalidate_resolution",
    )


def fixed_source_effect_sequence_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership for one closed source-threaded counter sequence."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if (
        target_schema is not None
        or not {
            FIXED_SOURCE_SEQUENCE_MECHANIC,
            "cr-122-counters",
            "cr-611-continuous-effects",
        }.issubset(mechanics)
        or not 2 <= len(effects) <= 3
    ):
        return ()
    counter = effects[0]
    if (
        set(counter) != {"op", "card", "counter", "amount", "source"}
        or counter.get("op") != "place_counters"
        or counter.get("card") != SOURCE_ZONE_OBJECT
        or type(counter.get("counter")) is not str
        or not counter.get("counter")
        or type(counter.get("amount")) is not int
        or counter.get("amount", 0) <= 0
        or counter.get("source") != "$source"
    ):
        return ()
    counter_mechanic = keyword_counter_mechanic(counter.get("counter"))
    if counter_mechanic is not None and counter_mechanic not in mechanics:
        return ()
    grants: set[str] = set()
    for effect in effects[1:]:
        operation = effect.get("op")
        if operation == "modify_stats_until_end_of_turn":
            if (
                set(effect) != {"op", "card", "power", "toughness"}
                or effect.get("card") != SOURCE_ZONE_OBJECT
                or type(effect.get("power")) is not int
                or type(effect.get("toughness")) is not int
                or (
                    effect.get("power") == 0
                    and effect.get("toughness") == 0
                )
            ):
                return ()
            continue
        if operation == "grant_keyword_until_end_of_turn":
            keyword = effect.get("keyword")
            if (
                set(effect) != {"op", "card", "keyword"}
                or effect.get("card") != SOURCE_ZONE_OBJECT
                or keyword not in _FIXED_TARGET_SEQUENCE_KEYWORDS
                or keyword in grants
            ):
                return ()
            keyword_mechanic = keyword_counter_mechanic(keyword)
            if keyword_mechanic is not None and keyword_mechanic not in mechanics:
                return ()
            grants.add(keyword)
            continue
        return ()
    return (
        "continuous.resolution.fixed_characteristics_until_end_of_turn",
        *(("counter.characteristic.keyword",) if counter_mechanic else ()),
        "counter.producer.fixed_effect",
        "resolution.effect_sequence.fixed_source",
    )


def fixed_target_characteristics_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership for one closed targeted fixed characteristic effect."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if not {
        "cr-115-targets",
        "cr-611-continuous-effects",
    }.issubset(mechanics) or not 1 <= len(effects) <= 3:
        return ()
    schema = dict(target_schema or {})
    if not direct_permanent_target_schema_is_closed(schema):
        return ()
    target_spec = DirectPermanentTargetSpec.from_target_schema(schema)
    if target_spec.types_any != ("creature",):
        return ()
    granted_keywords: set[str] = set()
    for effect in effects:
        operation = effect.get("op")
        if operation == "modify_stats_until_end_of_turn":
            if (
                set(effect) != {"op", "card", "power", "toughness"}
                or effect.get("card") != "$target.0"
                or type(effect.get("power")) is not int
                or type(effect.get("toughness")) is not int
                or (effect.get("power") == 0 and effect.get("toughness") == 0)
            ):
                return ()
            continue
        if operation == "grant_keyword_until_end_of_turn":
            keyword = effect.get("keyword")
            if (
                set(effect) != {"op", "card", "keyword"}
                or effect.get("card") != "$target.0"
                or keyword not in _FIXED_TARGET_SEQUENCE_KEYWORDS
                or keyword in granted_keywords
            ):
                return ()
            granted_keywords.add(keyword)
            continue
        return ()
    return (
        "continuous.resolution.fixed_characteristics_until_end_of_turn",
        *direct_target_predicate_capabilities(schema),
        "target.revalidate_resolution",
    )


def temporary_declaration_restriction_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return ownership for one closed target declaration restriction."""

    if len(effects) != 1:
        return ()
    effect = effects[0]
    kind = effect.get("restriction")
    if (
        set(effect) != {"op", "card", "restriction"}
        or effect.get("op")
        != "grant_declaration_restriction_until_end_of_turn"
        or effect.get("card") != "$target.0"
        or kind not in TEMPORARY_DECLARATION_RESTRICTION_KINDS
    ):
        return ()
    schema = dict(target_schema or {})
    if schema != {
        "zones": ["battlefield"],
        "categories": ["permanent"],
        "types_any": ["creature"],
        "count": 1,
    }:
        return ()
    required_mechanics = {
        "cr-115-targets",
        "cr-611-continuous-effects",
        *temporary_declaration_restriction(str(kind)).mechanics,
    }
    mechanics = {str(value).casefold() for value in mechanic_ids}
    if not required_mechanics.issubset(mechanics):
        return ()
    return (
        "combat.declaration.typed_components",
        "continuous.resolution.declaration_rules_until_end_of_turn",
        "target.revalidate_resolution",
    )


def fixed_counter_placement_set_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for one closed affected-set placement."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "cr-122-counters" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    if (
        set(effect) != {"op", "source", "set", "counter", "amount"}
        or effect.get("op") != "place_counters_on_set"
        or effect.get("source") != "$source"
        or type(effect.get("counter")) is not str
        or not str(effect.get("counter") or "").strip()
        or type(effect.get("amount")) is not int
        or effect.get("amount", 0) <= 0
    ):
        return ()
    counter_mechanic = keyword_counter_mechanic(effect.get("counter"))
    if counter_mechanic is not None and counter_mechanic not in mechanics:
        return ()
    characteristic_capabilities = (
        ("counter.characteristic.keyword",)
        if counter_mechanic is not None
        else ()
    )
    try:
        spec = AffectedPermanentSetSpec.from_dict(effect.get("set"))
    except (AffectedPermanentSetError, TypeError):
        return ()
    if not fixed_counter_set_spec_is_closed(spec):
        return ()
    state_capabilities = public_state_query_capabilities(
        spec.query.state_predicate
    )
    if spec.controller_relation is AffectedControllerRelation.TARGET_PLAYER:
        if (
            "cr-115-targets" not in mechanics
            or dict(target_schema or {})
            not in {
                "any": {
                    "zones": ["player"],
                    "categories": ["player"],
                    "count": 1,
                    "player_relation": "any",
                },
                "opponent": {
                    "zones": ["player"],
                    "categories": ["player"],
                    "count": 1,
                    "player_relation": "opponent",
                },
            }.values()
        ):
            return ()
        return (
            "counter.producer.fixed_permanent_set_effect",
            *characteristic_capabilities,
            *state_capabilities,
            "target.revalidate_resolution",
        )
    if target_schema is not None:
        return ()
    return (
        "counter.producer.fixed_permanent_set_effect",
        *characteristic_capabilities,
        *state_capabilities,
    )


def fixed_counter_placement_target_set_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for one closed optional permanent target set."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    support = "support" in mechanics
    if not {"cr-122-counters", "cr-115-targets"}.issubset(mechanics):
        return ()
    if len(effects) != 1:
        return ()
    effect = effects[0]
    expected_effect_fields = {
        "op",
        "cards",
        "maximum_targets",
        "counter",
        "amount",
        "source",
    }
    maximum = effect.get("maximum_targets")
    if (
        set(effect) != expected_effect_fields
        or effect.get("op") != "place_counters_on_targets"
        or effect.get("cards") != "$targets"
        or effect.get("source") != "$source"
        or type(maximum) is not int
        or maximum <= 0
        or type(effect.get("counter")) is not str
        or not str(effect.get("counter") or "").strip()
        or type(effect.get("amount")) is not int
        or effect.get("amount", 0) <= 0
    ):
        return ()
    counter_mechanic = keyword_counter_mechanic(effect.get("counter"))
    if counter_mechanic is not None and counter_mechanic not in mechanics:
        return ()
    characteristic_capabilities = (
        ("counter.characteristic.keyword",)
        if counter_mechanic is not None
        else ()
    )
    schema = dict(target_schema or {})
    allowed_schema_fields = {
        "zones",
        "categories",
        "types_any",
        "types_none",
        "controller_relation",
        "source_exclusion",
        "support_source_context",
        "state_predicate",
        "up_to",
    }
    if (
        set(schema) - allowed_schema_fields
        or schema.get("zones") != ["battlefield"]
        or schema.get("categories") != ["permanent"]
        or type(schema.get("up_to")) is not int
        or schema.get("up_to") != maximum
        or schema.get("controller_relation", "any")
        not in {"any", "you", "opponent"}
    ):
        return ()
    types_any = schema.get("types_any", ())
    if "types_any" in schema:
        if not isinstance(types_any, (list, tuple)) or tuple(types_any) not in {
            ("artifact",),
            ("battle",),
            ("creature",),
            ("enchantment",),
            ("land",),
            ("planeswalker",),
        }:
            return ()
    types_none = schema.get("types_none", ())
    if "types_none" in schema:
        if (
            not isinstance(types_none, (list, tuple))
            or tuple(types_none) != ("creature",)
            or tuple(types_any) != ("artifact",)
        ):
            return ()
    if "source_exclusion" in schema and schema["source_exclusion"] is not True:
        return ()
    state_capabilities = fixed_counter_target_set_state_capabilities(
        schema,
        types_any=tuple(types_any),
    )
    if state_capabilities is None:
        return ()
    if support:
        source_context = schema.get("support_source_context")
        if (
            str(effect.get("counter")).casefold() != "+1/+1"
            or effect.get("amount") != 1
            or tuple(types_any) != ("creature",)
            or "types_none" in schema
            or "controller_relation" in schema
            or bool(state_capabilities)
            or source_context not in {"permanent", "spell"}
            or (
                source_context == "permanent"
                and schema.get("source_exclusion") is not True
            )
            or (
                source_context == "spell"
                and "source_exclusion" in schema
            )
        ):
            return ()
        return (
            "counter.producer.support",
        )
    if "source_exclusion" in schema:
        return ()
    if "support_source_context" in schema:
        return ()
    return (
        "counter.producer.fixed_permanent_target_set_effect",
        *characteristic_capabilities,
        *state_capabilities,
        "target.revalidate_resolution",
    )


def fixed_player_counter_placement_node_capabilities(
    *,
    effects: Sequence[Mapping[str, Any]],
    target_schema: Mapping[str, Any] | None,
    mechanic_ids: Iterable[str],
) -> tuple[str, ...]:
    """Return capabilities only for one closed player-counter placement."""

    mechanics = {str(value).casefold() for value in mechanic_ids}
    if "cr-122-counters" not in mechanics or len(effects) != 1:
        return ()
    effect = effects[0]
    subject = effect.get("subjects")
    base_fields = {"op", "subjects", "counter", "amount", "source"}
    expected_fields = (
        base_fields | {"target"} if subject == "target" else base_fields
    )
    if (
        set(effect) != expected_fields
        or effect.get("op") != "place_player_counters"
        or subject
        not in {"controller", "target", "each-player", "each-opponent"}
        or type(effect.get("counter")) is not str
        or not str(effect.get("counter") or "").strip()
        or type(effect.get("amount")) is not int
        or effect.get("amount", 0) <= 0
        or effect.get("source") != "$source"
    ):
        return ()
    if subject != "target":
        return (
            ("counter.producer.fixed_player_effect",)
            if target_schema is None
            else ()
        )
    valid_schemas = (
        {
            "zones": ["player"],
            "categories": ["player"],
            "count": 1,
        },
        {
            "zones": ["player"],
            "categories": ["player"],
            "count": 1,
            "player_relation": "opponent",
        },
    )
    if (
        effect.get("target") != "$target.0"
        or "cr-115-targets" not in mechanics
        or dict(target_schema or {}) not in valid_schemas
    ):
        return ()
    return (
        "counter.producer.fixed_player_effect",
        "target.revalidate_resolution",
    )


__all__ = [
    "direct_target_predicate_capabilities",
    "fixed_alternative_additional_cost_node_capabilities",
    "fixed_counter_additional_cost_node_capabilities",
    "fixed_life_payment_additional_cost_node_capabilities",
    "fixed_sacrifice_additional_cost_node_capabilities",
    "fixed_zone_change_additional_cost_node_capabilities",
    "fixed_damage_node_capabilities",
    "mass_destruction_node_capabilities",
    "fixed_draw_node_capabilities",
    "fixed_counter_placement_node_capabilities",
    "fixed_counter_placement_batch_node_capabilities",
    "fixed_counter_target_schema_is_closed",
    "fixed_target_effect_sequence_node_capabilities",
    "fixed_source_effect_sequence_node_capabilities",
    "fixed_target_characteristics_node_capabilities",
    "temporary_declaration_restriction_node_capabilities",
    "fixed_counter_placement_set_node_capabilities",
    "fixed_counter_placement_target_set_node_capabilities",
    "fixed_player_counter_placement_node_capabilities",
    "fixed_life_cumulative_upkeep_node_capabilities",
    "fixed_mana_cumulative_upkeep_node_capabilities",
    "single_explore_node_capabilities",
    "single_proliferate_node_capabilities",
    "self_regeneration_node_capabilities",
    "fixed_self_counter_keyword_action_node_capabilities",
    "fixed_bolster_node_capabilities",
    "fixed_amass_node_capabilities",
    "targeted_destruction_node_capabilities",
    "targeted_exile_node_capabilities",
    "targeted_return_to_hand_node_capabilities",
    "targeted_tap_state_node_capabilities",
    "targeted_counter_node_capabilities",
]
