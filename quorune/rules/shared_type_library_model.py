"""Closed comparison over two already-authorized creature-type snapshots."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..shared_creature_type_model import SharedCreatureTypeObject


@dataclass(frozen=True, slots=True)
class SharedCreatureTypeComparison:
    source: SharedCreatureTypeObject
    candidate: SharedCreatureTypeObject
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Shared creature-type comparison version is unsupported")
        if not isinstance(self.source, SharedCreatureTypeObject) or not isinstance(
            self.candidate, SharedCreatureTypeObject
        ):
            raise ValueError("Shared creature-type comparison requires two typed snapshots")

    @property
    def matches(self) -> bool:
        return bool(set(self.source.creature_types).intersection(self.candidate.creature_types))

    def to_dict(self):
        return {"schema_version": 1, "source": self.source.to_dict(),
                "candidate": self.candidate.to_dict()}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version", "source", "candidate"
        }:
            raise ValueError("Shared creature-type comparison fields are invalid")
        return cls(SharedCreatureTypeObject.from_dict(value["source"]),
                   SharedCreatureTypeObject.from_dict(value["candidate"]),
                   value["schema_version"])


SHARED_TYPE_LIBRARY_OPERATION = "look_top_shared_type_reveal"
SHARED_TYPE_LIBRARY_CAPABILITY = "library.top.shared_type_optional_reveal"
SHARED_TYPE_LIBRARY_MECHANIC = "private-top-shared-type-reveal"
SHARED_TYPE_PLAY_OPERATION = "play_revealed_shared_type_library_card"
SHARED_TYPE_PLAY_CAPABILITY = "library.top.revealed_shared_type.resolving_play"
SHARED_TYPE_PLAY_MECHANIC = "private-top-shared-type-play"



@dataclass(frozen=True, slots=True)
class SharedTypeLibraryResult:
    effects: tuple
    mechanics: tuple[str, ...]
    schema_version: int = 1

    def __post_init__(self):
        from ..replacement.immutable import FrozenMap
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Shared-type library result version is unsupported")
        if (not isinstance(self.effects, (list, tuple)) or not 1 <= len(self.effects) <= 3
                or not isinstance(self.mechanics, (list, tuple))
                or any(type(v) is not str or not v for v in self.mechanics)
                or len(set(self.mechanics)) != len(self.mechanics)):
            raise ValueError("Shared-type library result needs a bounded closed instruction list")
        frozen = tuple(FrozenMap(e) for e in self.effects)
        allowed = {"damage", "damage_fixed_set", "place_counters", "modify_all_matching_permanents_until_end_of_turn",
                   "apply_source_characteristics_until_end_of_turn", "choose_cards_apnap",
                   "discard_hand", "discard_whole_hands", "draw", "draw_each_player", "create_token", "life", "lose_life", "lose_life_each_opponent", "mill"}
        if (len(frozen) == 1 and frozen[0] == {"op": SHARED_TYPE_PLAY_OPERATION}
                and tuple(self.mechanics) == (SHARED_TYPE_PLAY_MECHANIC,)):
            object.__setattr__(self, "effects", frozen)
            object.__setattr__(self, "mechanics", (SHARED_TYPE_PLAY_MECHANIC,))
            return
        if any(e.get("op") not in allowed for e in frozen):
            raise ValueError("Shared-type library result contains an unrepresented instruction")
        for e in frozen:
            if not shared_type_result_capabilities(e, self.mechanics):
                raise ValueError("Shared-type library child lacks an independently closed owner")
        object.__setattr__(self, "effects", frozen)
        object.__setattr__(self, "mechanics", tuple(sorted(self.mechanics)))

    @property
    def capability_dependencies(self):
        sequence = {"resolution.effect_program.closed_components"} if len(self.effects) > 1 else set()
        from .capabilities import capability_dependencies_for_node
        children = {cap for effect in self.effects for cap in shared_type_result_capabilities(effect, self.mechanics)}
        children.update(capability_dependencies_for_node(
            effects=self.to_dict()["effects"], target_schema=None, mechanic_ids=self.mechanics))
        return tuple(sorted({SHARED_TYPE_LIBRARY_CAPABILITY, *sequence, *children}))

    def to_dict(self):
        from ..replacement.immutable import thaw_value
        return {"schema_version": 1, "effects": [thaw_value(e) for e in self.effects],
                "mechanics": list(self.mechanics)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "effects", "mechanics"}:
            raise ValueError("Shared-type library result fields are invalid")
        return cls(value["effects"], value["mechanics"], value["schema_version"])

    def effect(self, *, player="$controller"):
        return {"op": SHARED_TYPE_LIBRARY_OPERATION, "player": player, "result": self.to_dict()}


def shared_type_result_capabilities(effect, mechanics):
    """Reuse the child's canonical strict owner, including its list-shaped wire."""
    from ..replacement.immutable import thaw_value
    from .fixed_effect_clause_shapes import closed_effect_component_capabilities
    from .node_capability_shapes import fixed_draw_node_capabilities, fixed_source_characteristics_node_capabilities
    from .damage_capability_shapes import fixed_damage_node_capabilities
    from .fixed_controller_effect_shapes import fixed_life_node_capabilities
    from .fixed_resolution_characteristic_shapes import fixed_resolution_characteristic_set_node_capabilities
    from .token_creation_capability_shapes import fixed_token_creation_node_capabilities, token_creation_component_mechanics
    literal = thaw_value(effect)
    flags = set(mechanics)
    if literal == {"op": SHARED_TYPE_PLAY_OPERATION} and flags == {SHARED_TYPE_PLAY_MECHANIC}:
        return (SHARED_TYPE_PLAY_CAPABILITY,)
    for resolver in (fixed_draw_node_capabilities, fixed_source_characteristics_node_capabilities,
            fixed_damage_node_capabilities, fixed_life_node_capabilities,
            fixed_resolution_characteristic_set_node_capabilities):
        found = resolver(effects=(literal,), target_schema=None, mechanic_ids=flags)
        if found:
            return found
    if literal.get("op") == "create_token":
        found = fixed_token_creation_node_capabilities(effects=(literal,), target_schema=None,
            mechanic_ids=token_creation_component_mechanics((literal,), flags))
        if found:
            return found
    return closed_effect_component_capabilities((literal,), target_schema=None, mechanics=flags)
