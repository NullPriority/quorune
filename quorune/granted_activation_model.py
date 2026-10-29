from __future__ import annotations

"""Closed granted-activation cost and output fragments, including fixed Crew."""

from dataclasses import dataclass
from typing import Any, Iterable, Mapping
from .ability_fragment_primitives import AbilityFragmentError
from .replacement.immutable import FrozenMap, thaw_value


_MANA_KEYS = frozenset({"W", "U", "B", "R", "G", "C", "GENERIC"})
RESTRICTED_MANA_GRANT_CAPABILITY = 'continuous.ability.restricted_mana_grant'


def normalize_granted_cost_choices(choices):
    """The single closed cost-choice boundary shared by model and compiler."""
    if not isinstance(choices, tuple):
        raise AbilityFragmentError('Granted-ability choices must be typed cost choices')
    normalized = []
    for choice in choices:
        if not isinstance(choice, Mapping) or set(choice) != {'kind','count','zone','card_type','another'}:
            raise AbilityFragmentError('Granted-ability choices have a closed schema')
        kind=choice.get('kind');card_type=choice.get('card_type')
        if (kind not in {'discard','sacrifice'} or choice.get('count') != 1
                or choice.get('another') is not False or choice.get('zone') != ('hand' if kind=='discard' else 'battlefield')
                or card_type not in {None,'artifact','creature','enchantment','land','permanent'}
                or kind=='discard' and card_type=='permanent' or kind=='sacrifice' and card_type is None):
            raise AbilityFragmentError('Granted-ability cost choice is unsupported')
        normalized.append(FrozenMap(choice))
    return tuple(normalized)


def _mana_pairs(
    value: Mapping[str, Any] | Iterable[tuple[str, int]],
) -> tuple[tuple[str, int], ...]:
    items = value.items() if isinstance(value, Mapping) else value
    normalized: dict[str, int] = {}
    for raw_key, raw_amount in items:
        key = str(raw_key).upper()
        if key not in _MANA_KEYS:
            raise AbilityFragmentError(
                f"Unsupported granted-ability mana key {raw_key!r}"
            )
        if type(raw_amount) is not int or raw_amount < 0:
            raise AbilityFragmentError(
                "Granted-ability mana amounts must be nonnegative integers"
            )
        if raw_amount:
            normalized[key] = raw_amount
    return tuple(sorted(normalized.items()))


def _nonempty_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AbilityFragmentError(f"{field} must be a nonempty string")
    return value.strip()


@dataclass(frozen=True, slots=True)
class GrantedActivatedAbilitySpec:
    """A closed executable activated ability granted in layer 6."""

    ability_id: str
    semantic_key: str
    cost_text: str
    effect_text: str
    mana: tuple[tuple[str, int], ...] = ()
    tap_source: bool = False
    sorcery_speed: bool = False
    mana_ability: bool = False
    fixed_mana_outputs: tuple[tuple[tuple[str, int], ...], ...] = ()
    untap_source: bool = False
    sacrifice_source: bool = False
    life_payment: int = 0
    choices: tuple[FrozenMap, ...] = ()
    mana_spend_restriction: str | None = None
    schema_version: int = 1
    crew_threshold: int | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3, 4}:
            raise AbilityFragmentError(
                "Unsupported granted activated-ability schema version"
            )
        if self.crew_threshold is not None and (self.schema_version!=3 or type(self.crew_threshold) is not int or self.crew_threshold<0):
            raise AbilityFragmentError('Granted Crew requires a versioned fixed threshold')
        if self.schema_version==3 and self.crew_threshold is None:raise AbilityFragmentError('Granted Crew schema requires its threshold')
        for field in ("ability_id", "semantic_key", "cost_text", "effect_text"):
            object.__setattr__(
                self,
                field,
                _nonempty_string(getattr(self, field), field=field),
            )
        for field in (
            "tap_source",
            "untap_source",
            "sacrifice_source",
            "sorcery_speed",
            "mana_ability",
        ):
            if type(getattr(self, field)) is not bool:
                raise AbilityFragmentError(f"{field} must be a boolean")
        if type(self.life_payment) is not int or self.life_payment < 0:
            raise AbilityFragmentError(
                "Granted-ability life payment must be a nonnegative integer"
            )
        object.__setattr__(self, "choices", normalize_granted_cost_choices(self.choices))
        from .mana_restriction_model import valid_mana_spend_restriction
        if self.schema_version == 4:
            if (not valid_mana_spend_restriction(self.mana_spend_restriction)
                    or self.mana_spend_restriction in {None, 'nonartifact_spell_prohibited', 'legendary_spell_uncounterable'}):
                raise AbilityFragmentError('Version-four grants need a closed spending restriction without additional results')
        elif self.mana_spend_restriction not in {
            None,
            "nonartifact_spell_prohibited",
        }:
            raise AbilityFragmentError(
                "Granted-ability mana restriction is unsupported"
            )
        if self.schema_version == 1 and any(
            (
                self.untap_source,
                self.sacrifice_source,
                self.life_payment,
                self.choices,
                self.mana_spend_restriction is not None,
            )
        ):
            raise AbilityFragmentError(
                "Extended granted-ability costs require schema version 2"
            )
        object.__setattr__(self, "mana", _mana_pairs(self.mana))
        if not isinstance(self.fixed_mana_outputs, tuple):
            raise AbilityFragmentError("fixed mana outputs must be a tuple")
        normalized_outputs = tuple(
            _mana_pairs(output) for output in self.fixed_mana_outputs
        )
        if any(not output for output in normalized_outputs):
            raise AbilityFragmentError("fixed mana outputs cannot be empty")
        if len(normalized_outputs) != len(set(normalized_outputs)):
            raise AbilityFragmentError("fixed mana outputs must be unique")
        if normalized_outputs and not self.mana_ability:
            raise AbilityFragmentError(
                "fixed mana outputs require a mana ability"
            )
        if self.mana_spend_restriction is not None and (
            not self.mana_ability or not normalized_outputs
        ):
            raise AbilityFragmentError(
                "Granted-ability mana restrictions require fixed mana output"
            )
        object.__setattr__(self, "fixed_mana_outputs", normalized_outputs)

    @property
    def capability_id(self) -> str | None:
        return RESTRICTED_MANA_GRANT_CAPABILITY if self.schema_version == 4 else None

    @property
    def mana_bundle(self) -> dict[str, int]:
        return dict(self.mana)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "ability_id": self.ability_id,
            "semantic_key": self.semantic_key,
            "cost_text": self.cost_text,
            "effect_text": self.effect_text,
            "mana": self.mana_bundle,
            "tap_source": self.tap_source,
            "sorcery_speed": self.sorcery_speed,
            "mana_ability": self.mana_ability,
            "fixed_mana_outputs": [
                dict(output) for output in self.fixed_mana_outputs
            ],
        }
        if self.schema_version in {2,3,4}:
            result.update(
                {
                    "untap_source": self.untap_source,
                    "sacrifice_source": self.sacrifice_source,
                    "life_payment": self.life_payment,
                    "choices": [thaw_value(choice) for choice in self.choices],
                    "mana_spend_restriction": self.mana_spend_restriction,
                }
            )
        if self.schema_version==3:result["crew_threshold"]=self.crew_threshold
        return result

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any]
    ) -> "GrantedActivatedAbilitySpec":
        expected = {
            "schema_version",
            "ability_id",
            "semantic_key",
            "cost_text",
            "effect_text",
            "mana",
            "tap_source",
            "sorcery_speed",
            "mana_ability",
            "fixed_mana_outputs",
        }
        extended = {
            *expected,
            "untap_source",
            "sacrifice_source",
            "life_payment",
            "choices",
            "mana_spend_restriction",
        }
        crew_extended={*extended,'crew_threshold'}
        if (
            not isinstance(value, Mapping)
            or set(value) not in {frozenset(expected), frozenset(extended),frozenset(crew_extended)}
            or (value.get("schema_version") == 1 and set(value) != expected)
            or (value.get("schema_version") == 2 and set(value) != extended)
            or (value.get("schema_version") == 3 and set(value) != crew_extended)
            or (value.get("schema_version") == 4 and set(value) != extended)
        ):
            raise AbilityFragmentError(
                "Granted activated-ability fragments have a closed schema"
            )
        if not isinstance(value["mana"], Mapping):
            raise AbilityFragmentError(
                "Granted activated-ability mana must be an object"
            )
        if not isinstance(value["fixed_mana_outputs"], list) or any(
            not isinstance(output, Mapping)
            for output in value["fixed_mana_outputs"]
        ):
            raise AbilityFragmentError(
                "Granted activated-ability fixed outputs must be an array of objects"
            )
        if value["schema_version"] in {2,3,4} and not isinstance(
            value["choices"], list
        ):
            raise AbilityFragmentError(
                "Granted activated-ability choices must be an array"
            )
        return cls(
            schema_version=value["schema_version"],
            ability_id=value["ability_id"],
            semantic_key=value["semantic_key"],
            cost_text=value["cost_text"],
            effect_text=value["effect_text"],
            mana=value["mana"],
            tap_source=value["tap_source"],
            sorcery_speed=value["sorcery_speed"],
            mana_ability=value["mana_ability"],
            fixed_mana_outputs=tuple(value["fixed_mana_outputs"]),
            untap_source=value.get("untap_source", False),
            sacrifice_source=value.get("sacrifice_source", False),
            life_payment=value.get("life_payment", 0),
            choices=tuple(value.get("choices", ())),
            mana_spend_restriction=value.get("mana_spend_restriction"),
            crew_threshold=value.get("crew_threshold"),
        )
