"""The closed, tap-only mana output from a private artifact selection."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from .fixed_mana_abilities import FixedActivatedManaAbilitySpec, FixedManaMode
from .replacement.immutable import FrozenMap

ARTIFACT_HAND_MANA_KIND = "artifact_hand_mana"
ARTIFACT_HAND_MANA_CAPABILITY = "mana.production.artifact_hand_reveal"
ARTIFACT_HAND_MANA_HANDLER_ID = "ability.activated.mana.artifact-hand-reveal.v1"
ARTIFACT_HAND_MANA_STEP = "artifact_hand_reveal"
ARTIFACT_HAND_MANA_SELECTION = "artifact_hand_mana_selection"
ARTIFACT_HAND_MANA_REVEAL_FIELD = "reveal_cards"
ARTIFACT_HAND_MANA_RULES = ("601.2g", "601.2h", "605.1a", "605.3b", "701.20a", "701.20b")


def artifact_hand_mana_output() -> dict:
    return {"kind": ARTIFACT_HAND_MANA_KIND, "schema_version": 1}


def is_artifact_hand_mana(ability) -> bool:
    value = ability.dynamic_mana_output
    return isinstance(value, Mapping) and value.get("kind") == ARTIFACT_HAND_MANA_KIND


def validate_artifact_hand_mana_ability(ability) -> None:
    value = ability.dynamic_mana_output
    if (value != artifact_hand_mana_output() or type(value["schema_version"]) is not int
            or ability.mana_ability is not True or ability.tap_source is not True
            or ability.zones != ("battlefield",) or any(ability.mana.values())
            or any((ability.mana_cost_options, ability.complex_symbols, ability.choices, ability.uncompiled_costs,
                    ability.untap_source, ability.discard_source, ability.sacrifice_source, ability.exile_source,
                    ability.life_payment, ability.energy_payment, ability.random_discard_payment,
                    ability.loyalty_delta is not None, ability.source_counter_removal_cost, ability.waterbend,
                    ability.activation_limit, ability.activation_conditions, ability.sorcery_speed,
                    ability.fixed_mana_outputs, ability.color_set_mana_output, ability.fixed_mana_result,
                    ability.mana_spend_restriction, ability.target_schema is not None))):
        raise ValueError("Artifact hand mana requires its exact tap-only activation")


@dataclass(frozen=True, slots=True)
class ArtifactHandActivatedManaSpec:
    costs: FixedActivatedManaAbilitySpec

    def __post_init__(self):
        if not isinstance(self.costs, FixedActivatedManaAbilitySpec) or self.costs.modes != (FixedManaMode(green=1),):
            raise ValueError("Artifact hand mana requires its closed cost descriptor")
        self.to_activated_ability()

    def to_activated_ability(self):
        return replace(self.costs.to_activated_ability(), fixed_mana_outputs=(),
                       dynamic_mana_output=FrozenMap(artifact_hand_mana_output()))



@dataclass(frozen=True, slots=True)
class ArtifactHandManaStep:
    ability_id: str
    cards: tuple[str, ...]

    def __post_init__(self):
        if (type(self.ability_id) is not str or not self.ability_id or not isinstance(self.cards, (list, tuple))
                or any(type(ref) is not str or not ref for ref in self.cards)
                or len(set(self.cards)) != len(self.cards)):
            raise ValueError("An artifact mana step requires its ability and distinct selected cards")
        object.__setattr__(self, "cards", tuple(self.cards))

    @property
    def colorless(self):
        return 2 * len(self.cards)

    def to_dict(self):
        return {"ability_id": self.ability_id, "cards": list(self.cards)}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"ability_id", "cards"}:
            raise ValueError("An artifact mana step requires its ability and selected cards")
        return cls(value["ability_id"], value["cards"])
