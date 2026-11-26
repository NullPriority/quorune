from __future__ import annotations

"""Closed granted-activation cost and output fragments, including fixed Crew."""

from dataclasses import dataclass, replace
from enum import StrEnum
import re
from typing import TYPE_CHECKING, Any, Iterable, Mapping
from .ability_fragment_primitives import AbilityFragmentError
from .replacement.immutable import FrozenMap, thaw_value
from .util import ORDINARY_FIXED_MANA_PATTERN, mana_cost_to_vector

if TYPE_CHECKING:
    from .continuous_effect_model import ContinuousObjectIdentity


_MANA_KEYS = frozenset({"W", "U", "B", "R", "G", "C", "GENERIC"})
RESTRICTED_MANA_GRANT_CAPABILITY = 'continuous.ability.restricted_mana_grant'
GRANTING_ATTACHMENT_PAYMENT_CAPABILITY = 'activation.granting_attachment.fixed_payment'
GRANTING_ATTACHMENT_PAYMENT_FIELD = 'granting_attachment_payment'
GRANTING_ATTACHMENT_SACRIFICE_REF = '$granting_attachment.zone_object'
GRANTING_ATTACHMENT_REFLEXIVE_CAPABILITY = 'trigger.attachment.granting_source_reflexive_sacrifice'
GRANTED_SOURCE_COUNTER_CAPABILITY = 'activation.source_counter_removal.fixed'
GRANTED_SPORE_COUNTER_COST = FrozenMap({'schema_version': 1, 'counter_name': 'spore', 'amount': 2})


class GrantingAttachmentPaymentKind(StrEnum):
    UNATTACH = 'unattach'
    SACRIFICE = 'sacrifice'


@dataclass(frozen=True, slots=True)
class GrantingAttachmentPayment:
    kind: GrantingAttachmentPaymentKind
    granting_object: ContinuousObjectIdentity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, GrantingAttachmentPaymentKind):
            raise AbilityFragmentError('Granting Equipment payment requires a typed operation')
        if self.granting_object is not None:
            from .continuous_effect_model import ContinuousObjectIdentity
            if not isinstance(self.granting_object, ContinuousObjectIdentity):
                raise AbilityFragmentError('Granting Equipment payment requires an exact incarnation')

    def bind(self, object_id: str, logical_object_id: str) -> GrantingAttachmentPayment:
        from .continuous_effect_model import ContinuousObjectIdentity

        if self.granting_object is not None:
            raise AbilityFragmentError('Granting Equipment payment is already bound')
        return replace(self, granting_object=ContinuousObjectIdentity(object_id, logical_object_id))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {'kind': self.kind.value}
        if self.granting_object is not None:
            result['granting_object'] = self.granting_object.to_dict()
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> GrantingAttachmentPayment:
        from .continuous_effect_model import ContinuousObjectIdentity

        if not isinstance(value, Mapping) or set(value) not in ({'kind'}, {'kind', 'granting_object'}):
            raise AbilityFragmentError('Granting Equipment payment has a closed schema')
        identity = ContinuousObjectIdentity.from_dict(value['granting_object']) if 'granting_object' in value else None
        return cls(GrantingAttachmentPaymentKind(value['kind']), identity)


def validate_bound_granting_attachment_payment(ability) -> None:
    payment = ability.granting_attachment_payment
    if (
        not isinstance(payment, GrantingAttachmentPayment) or payment.granting_object is None
        or ability.zones != ('battlefield',) or not ability.tap_source or not ability.builtin_semantic_key
        or ability.mana_ability or ability.sorcery_speed or ability.untap_source or ability.sacrifice_source
        or ability.discard_source or ability.exile_source or ability.life_payment or ability.energy_payment
        or ability.choices or ability.mana_cost_options or ability.source_counter_removal_cost is not None
        or ability.loyalty_delta is not None or ability.waterbend is not None
    ):
        raise AbilityFragmentError('Granting Equipment payment requires a bound fixed tap activation')


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


GRANTED_UNEARTH_VERSION = 8
GRAVEYARD_UNEARTH_GRANT_CAPABILITY = "continuous.ability.graveyard_unearth_grant"
GRANTED_NINJUTSU_VERSION = 9
HAND_NINJUTSU_GRANT_CAPABILITY = "continuous.ability.hand_ninjutsu_grant"
_INCARNATION_BOUND_GRANT_VERSIONS = (GRANTED_UNEARTH_VERSION, GRANTED_NINJUTSU_VERSION)


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
    granting_attachment_payment: GrantingAttachmentPayment | None = None
    source_counter_removal_cost: Mapping[str, Any] | None = None
    announced_x_symbols: int | None = None
    grant_incarnation: str | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3, 4, 5, 6, 7, *_INCARNATION_BOUND_GRANT_VERSIONS}:
            raise AbilityFragmentError(
                "Unsupported granted activated-ability schema version"
            )
        if self.crew_threshold is not None and (self.schema_version!=3 or type(self.crew_threshold) is not int or self.crew_threshold<0):
            raise AbilityFragmentError('Granted Crew requires a versioned fixed threshold')
        if self.schema_version==3 and self.crew_threshold is None:raise AbilityFragmentError('Granted Crew schema requires its threshold')
        if self.schema_version == 5:
            if (not isinstance(self.granting_attachment_payment, GrantingAttachmentPayment) or not self.tap_source
                    or self.mana_ability or self.untap_source or self.sacrifice_source or self.life_payment
                    or self.choices or self.mana_spend_restriction is not None or self.sorcery_speed):
                raise AbilityFragmentError('Granting Equipment payment requires a fixed tap activation')
        elif self.granting_attachment_payment is not None:
            raise AbilityFragmentError('Granting Equipment payment requires schema version 5')
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
        price = self.source_counter_removal_cost
        if self.schema_version == 6:
            if (not isinstance(price, Mapping) or price != GRANTED_SPORE_COUNTER_COST
                    or type(price["schema_version"]) is not int or type(price["amount"]) is not int
                    or any((self.mana, self.tap_source, self.untap_source, self.sacrifice_source,
                            self.life_payment, self.choices, self.mana_ability, self.sorcery_speed,
                            self.mana_spend_restriction, self.fixed_mana_outputs))):
                raise AbilityFragmentError("Granted source-counter cost requires exactly two spore counters from its source")
            object.__setattr__(self, "source_counter_removal_cost", GRANTED_SPORE_COUNTER_COST)
        elif price is not None:
            raise AbilityFragmentError("Granted source-counter costs require schema version 6")
        if self.schema_version == 7:
            if (type(self.announced_x_symbols) is not int or self.announced_x_symbols != 1
                    or self.cost_text != "{X}"
                    or any((self.mana, self.tap_source, self.untap_source, self.sacrifice_source,
                            self.life_payment, self.choices, self.mana_ability, self.sorcery_speed,
                            self.mana_spend_restriction, self.fixed_mana_outputs))):
                raise AbilityFragmentError("Announced-X grants require one ordinary X price")
        elif self.announced_x_symbols is not None:
            raise AbilityFragmentError("Announced-X grants require schema version 7")

        if self.schema_version in _INCARNATION_BOUND_GRANT_VERSIONS:
            self._validate_off_battlefield_grant()
        elif self.grant_incarnation is not None:
            raise AbilityFragmentError("A source incarnation requires a versioned off-battlefield grant")

    def _validate_off_battlefield_grant(self) -> None:
        sorcery_speed = self.schema_version == GRANTED_UNEARTH_VERSION
        if (
            self.sorcery_speed is not sorcery_speed
            or any((self.tap_source, self.untap_source, self.sacrifice_source, self.life_payment,
                    self.choices, self.mana_ability, self.mana_spend_restriction,
                    self.fixed_mana_outputs, self.crew_threshold))
            or not self.ability_id.startswith("ability:granted:")
            or not self.semantic_key.endswith(":" + self.ability_id)
        ):
            raise AbilityFragmentError("Off-battlefield grants require the keyword's fixed activation timing")
        if self.grant_incarnation is not None:
            value = self.grant_incarnation
            if type(value) is not str:
                raise AbilityFragmentError("Grant incarnation must be text")
            object_id, separator, incarnation = value.rpartition("@")
            if not object_id or not separator or not incarnation.isascii() or not incarnation.isdecimal():
                raise AbilityFragmentError("Off-battlefield grants require a source incarnation")
        if re.fullmatch(ORDINARY_FIXED_MANA_PATTERN, self.cost_text) is None:
            raise AbilityFragmentError("Off-battlefield grants require a fixed ordinary mana cost")
        expected, complex_symbols = mana_cost_to_vector(self.cost_text)
        if complex_symbols or {key: amount for key, amount in expected.items() if amount} != self.mana_bundle:
            raise AbilityFragmentError("Off-battlefield grant mana vector must match its cost")

    @property
    def activation_zones(self) -> tuple[str, ...]:
        if self.schema_version == GRANTED_NINJUTSU_VERSION:
            return ("hand",)
        return ("graveyard",) if self.schema_version == GRANTED_UNEARTH_VERSION else ("battlefield",)

    @property
    def capability_id(self) -> str | None:
        if self.schema_version == GRANTED_UNEARTH_VERSION:
            return GRAVEYARD_UNEARTH_GRANT_CAPABILITY
        if self.schema_version == GRANTED_NINJUTSU_VERSION:
            return HAND_NINJUTSU_GRANT_CAPABILITY
        if self.schema_version == 7:
            return "activation.mana_cost.announced_x"
        if self.schema_version == 6:
            return GRANTED_SOURCE_COUNTER_CAPABILITY
        if self.schema_version == 5:
            return GRANTING_ATTACHMENT_PAYMENT_CAPABILITY
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
        if self.schema_version in {2,3,4,5}:
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
        if self.granting_attachment_payment is not None:
            result[GRANTING_ATTACHMENT_PAYMENT_FIELD] = self.granting_attachment_payment.to_dict()
        if self.source_counter_removal_cost is not None:
            result["source_counter_removal_cost"] = thaw_value(self.source_counter_removal_cost)
        if self.announced_x_symbols is not None:
            result["announced_x_symbols"] = self.announced_x_symbols
        if self.schema_version in _INCARNATION_BOUND_GRANT_VERSIONS:
            result["grant_incarnation"] = self.grant_incarnation
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
        attachment_extended = {*extended, GRANTING_ATTACHMENT_PAYMENT_FIELD}
        source_counter_fields = {*expected, "source_counter_removal_cost"}
        announced_x_fields = {*expected, "announced_x_symbols"}
        incarnation_bound_fields = {*expected, "grant_incarnation"}
        if (
            not isinstance(value, Mapping)
            or type(value.get("schema_version")) is not int
            or set(value) not in {frozenset(expected), frozenset(extended), frozenset(crew_extended),
                                  frozenset(attachment_extended), frozenset(source_counter_fields), frozenset(announced_x_fields),
                                  frozenset(incarnation_bound_fields)}
            or (value.get("schema_version") == 1 and set(value) != expected)
            or (value.get("schema_version") == 2 and set(value) != extended)
            or (value.get("schema_version") == 3 and set(value) != crew_extended)
            or (value.get("schema_version") == 4 and set(value) != extended)
            or (value.get("schema_version") == 5 and set(value) != attachment_extended)
            or (value.get("schema_version") == 6 and set(value) != source_counter_fields)
            or (value.get("schema_version") == 7 and set(value) != announced_x_fields)
            or (value.get("schema_version") in _INCARNATION_BOUND_GRANT_VERSIONS
                and set(value) != incarnation_bound_fields)
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
            granting_attachment_payment=(GrantingAttachmentPayment.from_dict(value[GRANTING_ATTACHMENT_PAYMENT_FIELD])
                                         if GRANTING_ATTACHMENT_PAYMENT_FIELD in value else None),
            source_counter_removal_cost=value.get("source_counter_removal_cost"),
            announced_x_symbols=value.get("announced_x_symbols"),
            grant_incarnation=value.get("grant_incarnation"),
        )


LINKED_EXILE_ACTIVATION_FRAGMENT = "linked_exile_activated_grant"


@dataclass(frozen=True, slots=True)
class LinkedExileActivatedGrant:
    source_object_id: str
    source_logical_object_id: str
    pair: str
    schema_version: int = 1

    def __post_init__(self):
        from .continuous_effect_model import ContinuousObjectIdentity

        ContinuousObjectIdentity(self.source_object_id, self.source_logical_object_id)
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.pair) is not str or not self.pair or self.pair.strip() != self.pair):
            raise ValueError("Linked exile activation requires a version-one canonical pair identity")

    def to_dict(self) -> dict:
        return {"schema_version": 1, "source_object_id": self.source_object_id,
                "source_logical_object_id": self.source_logical_object_id, "pair": self.pair}

    @classmethod
    def from_dict(cls, value: Mapping):
        if not isinstance(value, Mapping) or set(value) != {
            "schema_version", "source_object_id", "source_logical_object_id", "pair",
        }:
            raise ValueError("A linked activation grant fragment has a closed shape")
        return cls(**value)
