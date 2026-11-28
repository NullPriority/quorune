"""One noncopiable enchantment-only result applied as a graveyard card enters."""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ..continuous_effect_model import (
    ContinuousEffect, ContinuousEffectDuration, ContinuousEffectOrigin, ContinuousObjectIdentity,
    ContinuousOperation, Layer,
)
from ..continuous_effect_state import commit_continuous_effect
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..type_subtype_vocabulary import CARD_SUPERTYPES, correlated_subtypes


ENTRY_ENCHANTMENT_FIELD = "resolution_entry_enchantment"


@dataclass(frozen=True, slots=True)
class EntryEnchantmentSetting:
    source_ref: str

    def __post_init__(self):
        if type(self.source_ref) is not str or not self.source_ref or self.source_ref.startswith("$"):
            raise ValueError("An enchantment-only entry requires its exact resolving source")

    def to_dict(self):
        return {"schema_version": 1, "source_ref": self.source_ref}

    @classmethod
    def from_dict(cls, raw):
        if (not isinstance(raw, Mapping) or set(raw) != {"schema_version", "source_ref"}
                or type(raw["schema_version"]) is not int or raw["schema_version"] != 1):
            raise ValueError("An enchantment-only entry has a closed result shape")
        return cls(raw["source_ref"])


def enchantment_only_entry_characteristics(characteristics):
    text = str(characteristics.get("type_line") or "").replace(" – ", " — ")
    left, _, suffix = text.partition(" — ")
    supertypes = [word for word in left.split() if word.casefold() in CARD_SUPERTYPES]
    retained = correlated_subtypes(suffix.split(), ("enchantment",))
    line = " ".join((*supertypes, "Enchantment"))
    if retained:
        line += " — " + " ".join(word for word in suffix.split() if word in retained)
    return {**characteristics, "type_line": line}


def with_resolution_entry_enchantment(plan, setting):
    if not isinstance(setting, EntryEnchantmentSetting) or plan.origin != "graveyard" or not plan.card.is_card_object:
        raise ValueError("Only an original physical graveyard card may enter as enchantment only")
    if plan.destination != "battlefield":
        return plan
    event = plan.prepared_replacement.event
    if ENTRY_ENCHANTMENT_FIELD in event.payload:
        raise ValueError("An enchantment entry result is already bound")
    payload = FrozenMap({**event.payload, ENTRY_ENCHANTMENT_FIELD: setting.to_dict()})
    return replace(plan, prepared_replacement=replace(plan.prepared_replacement, event=replace(event, payload=payload)))


def commit_resolution_entry_enchantment(host, card, event):
    if ENTRY_ENCHANTMENT_FIELD not in event.payload:
        return
    setting = EntryEnchantmentSetting.from_dict(event.payload[ENTRY_ENCHANTMENT_FIELD])
    if card.zone != "battlefield":
        raise ValueError("An enchantment entry result requires the resulting battlefield incarnation")
    operation = ContinuousOperation("set_types", ("Enchantment",), field="card_types")
    effect = ContinuousEffect(
        effect_id=f"entry-enchantment:{setting.source_ref}:{card.logical_object_id}", source_id=setting.source_ref,
        layer=Layer.TYPE, sublayer="4", timestamp=host._next_zone_timestamp(), operations=(operation,),
        origin=ContinuousEffectOrigin.RESOLUTION, duration=ContinuousEffectDuration.ZONE_OBJECT,
        applies=ObjectQuerySpec(zones=("battlefield",)),
        locked_objects=(ContinuousObjectIdentity(card.object_id, card.logical_object_id),),
    )
    commit_continuous_effect(host.state, effect)

def plan_resolution_entry_characteristics(plan, subtype, enchantment):
    from .entry_subtype_grants import with_resolution_entry_subtype
    from ..errors import GameRuleError

    if subtype is not None:
        plan = with_resolution_entry_subtype(plan, subtype)
    if enchantment is not None:
        try:
            plan = with_resolution_entry_enchantment(plan, enchantment)
        except (ValueError, TypeError) as exc:
            raise GameRuleError(str(exc)) from exc
    return plan
