from __future__ import annotations

"""Closed resolution-time costs for the existing payment owner."""

from dataclasses import dataclass
from typing import Any, Mapping

from .object_predicate import ObjectQuerySpec, object_query_capabilities
from .creature_subtypes import canonical_creature_subtype
from .counter_names import normalized_counter_name
from .replacement.immutable import FrozenMap, thaw_value
from .rules.blight_model import BLIGHT_CAPABILITY, BLIGHT_PAYMENT_VERSION, blight_creature_predicate
from .rules.controlled_return import CONTROLLED_RETURN_CAPABILITY, ControlledReturn
from .rules.hybrid_resolution_payment import (
    HYBRID_RESOLUTION_PAYMENT_CAPABILITY, HYBRID_RESOLUTION_PAYMENT_KIND,
    HYBRID_RESOLUTION_PAYMENT_VERSION, hybrid_resolution_mana_options,
)
from .rules.mandatory_sacrifice_reanimation import (
    MANDATORY_ARTIFACT_REANIMATION_VERSION, MANDATORY_CREATURE_SACRIFICE_VERSION,
    MANDATORY_REANIMATION_PAYMENT_VERSIONS, MANDATORY_SACRIFICE_REANIMATION_CAPABILITY,
)


FIXED_EFFECT_PAYMENT_MECHANIC = "fixed-optional-mana-payment"
FIXED_EFFECT_PAYMENT_CAPABILITY = "effect.choice.optional_fixed_mana_payment"
MANA_DISCARD_PAYMENT_CAPABILITY = 'effect.choice.optional_mana_discard_payment'
ANOTHER_SACRIFICE_PAYMENT_CAPABILITY = 'cost.sacrifice.another_fixed_resolution'
SOURCE_SACRIFICE_PAYMENT_CAPABILITY = 'cost.sacrifice.fixed_source_resolution'
MANDATORY_DISCARD_PAYMENT_CAPABILITY = "cost.discard.mandatory_fixed_resolution"
MANDATORY_DISCARD_PAYMENT_VERSION = 8
MANDATORY_ARTIFACT_SACRIFICE_CAPABILITY = "cost.sacrifice.mandatory_fixed_artifact_resolution"
MANDATORY_ARTIFACT_SACRIFICE_PAYMENT_VERSION = 16
_MANDATORY_SACRIFICES = {
    MANDATORY_ARTIFACT_SACRIFICE_PAYMENT_VERSION: ("artifact", MANDATORY_ARTIFACT_SACRIFICE_CAPABILITY),
    MANDATORY_ARTIFACT_REANIMATION_VERSION: ("artifact", MANDATORY_ARTIFACT_SACRIFICE_CAPABILITY),
    MANDATORY_CREATURE_SACRIFICE_VERSION: ("creature", MANDATORY_SACRIFICE_REANIMATION_CAPABILITY),
}
EQUIPPED_DEATH_LIFE_PRICE_VERSION = 14
OPPONENT_SOURCE_POWER_MANA_PRICE_VERSION = 15
RETURN_PAYMENT_KIND = "return_owner_hand"
RETURN_PAYMENT_VERSION = 11
_MANA_KEYS = frozenset({"GENERIC", "W", "U", "B", "R", "G", "C"})


def creature_or_vehicle_sacrifice_predicate(controller: str) -> ObjectQuerySpec:
    return ObjectQuerySpec(
        zones=("battlefield",), controller=controller,
        any_of=(
            ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)),
            ObjectQuerySpec(zones=("battlefield",), subtypes_all=("vehicle",)),
        ),
    )


def _validate_fixed_tap_predicate(predicate: ObjectQuerySpec | None) -> None:
    if not isinstance(predicate,ObjectQuerySpec):
        raise ValueError('Tap payments require a closed current-object predicate')
    allowed=ObjectQuerySpec(zones=('battlefield',),controller=predicate.controller,
        tapped=False,known_to_actor=True,exclude_ref=predicate.exclude_ref,
        types_all=predicate.types_all,subtypes_all=predicate.subtypes_all,
        excluded_subtypes=predicate.excluded_subtypes)
    if (predicate!=allowed or type(allowed.controller) is not str or not allowed.controller
            or not set(allowed.types_all)<={'artifact','battle','creature','enchantment','land','planeswalker'}
            or not (allowed.types_all or allowed.subtypes_all)
            or any(canonical_creature_subtype(value)!=value and value not in {
                'food','gate','forest','island','mountain','plains','swamp'} for value in allowed.subtypes_all)
            or allowed.excluded_subtypes not in ((),('human',))
            or allowed.excluded_subtypes and allowed.types_all!=('creature',)):
        raise ValueError('Tap payment predicate is outside the closed controlled untapped family')


def _validate_calculated_payment(spec) -> None:
    if spec.schema_version == EQUIPPED_DEATH_LIFE_PRICE_VERSION:
        if (spec.kind != "life" or type(spec.amount) is not int or spec.amount < 0
                or any(value is not None for value in
                       (spec.requirements, spec.predicate, spec.counter_name, spec.source_ref))
                or spec.hybrid_symbols != ()):
            raise ValueError("An equipped death quotes exactly one nonnegative life price")
        return
    requirements = spec.requirements
    if (spec.kind != "mana" or type(spec.amount) is not int or spec.amount != 1
            or not isinstance(requirements, Mapping) or set(requirements) != _MANA_KEYS
            or any(type(value) is not int or value < 0 for value in requirements.values())
            or any(requirements[key] for key in _MANA_KEYS - {"GENERIC"})
            or any(value is not None for value in (spec.predicate, spec.counter_name, spec.source_ref))
            or spec.hybrid_symbols != ()):
        raise ValueError("An opponent's source-power payment quotes one nonnegative generic mana price")
    object.__setattr__(spec, "requirements", FrozenMap(requirements))


@dataclass(frozen=True, slots=True)
class FixedEffectPaymentSpec:
    kind: str
    amount: int = 1
    requirements: FrozenMap | None = None
    predicate: ObjectQuerySpec | None = None
    schema_version: int = 2
    counter_name: str | None = None
    source_ref: str | None = None
    hybrid_symbols: tuple[str, ...] = ()

    @property
    def mandatory(self) -> bool:
        return type(self.schema_version) is int and self.schema_version in (
            MANDATORY_DISCARD_PAYMENT_VERSION, *_MANDATORY_SACRIFICES,
        )

    @property
    def mandatory_sacrifice_type(self):
        if self.schema_version in _MANDATORY_SACRIFICES and self.kind == "sacrifice":
            return _MANDATORY_SACRIFICES[self.schema_version][0]
        return None

    def __post_init__(self) -> None:
        if type(self.schema_version) is int and self.schema_version in {
            EQUIPPED_DEATH_LIFE_PRICE_VERSION, OPPONENT_SOURCE_POWER_MANA_PRICE_VERSION,
        }:
            _validate_calculated_payment(self)
            return
        if type(self.schema_version) is int and self.schema_version == 13:
            from .rules.fractional_life_payment import validate_resolved_fractional_payment_spec
            validate_resolved_fractional_payment_spec(self)
            return
        if type(self.schema_version) is int and self.schema_version == 12:
            from .rules.repeated_object_payment import validate_repeated_object_payment
            validate_repeated_object_payment(self)
            return
        hybrid = self.kind == HYBRID_RESOLUTION_PAYMENT_KIND
        if not hybrid and self.hybrid_symbols != ():
            raise ValueError("Only hybrid payments carry hybrid symbols")
        if self.kind == "blight":
            self._validate_blight()
            return
        if self.mandatory:
            if self.schema_version in _MANDATORY_SACRIFICES:
                self._validate_mandatory_sacrifice(_MANDATORY_SACRIFICES[self.schema_version][0])
            else:
                self._validate_mandatory_discard()
            return
        if self.kind == RETURN_PAYMENT_KIND:
            if (type(self.schema_version) is not int or self.schema_version != RETURN_PAYMENT_VERSION
                    or type(self.amount) is not int or self.amount != 1
                    or any(value is not None for value in (self.requirements, self.counter_name, self.source_ref))):
                raise ValueError("A return payment requires one controlled permanent")
            ControlledReturn(self.predicate, 1, 1)
            return
        another=self.kind=='sacrifice' and type(self.schema_version) is int and self.schema_version==6
        version=HYBRID_RESOLUTION_PAYMENT_VERSION if hybrid else 7 if self.kind=='source_sacrifice' else 6 if another else 5 if self.kind=='mana_discard' else 4 if self.kind=='remove_source_counter' else 3 if self.kind in {'exile','tap'} else 2
        if type(self.schema_version) is not int or self.schema_version != version:
            raise ValueError("Fixed effect payment version does not match its cost kind")
        if self.kind not in {"mana", "life", "discard", "sacrifice", "energy", "exile", "tap", "remove_source_counter", "mana_discard", "source_sacrifice", HYBRID_RESOLUTION_PAYMENT_KIND}:
            raise ValueError("Fixed effect payment kind is unsupported")
        if type(self.amount) is not int or self.amount <= 0:
            raise ValueError("Fixed effect payment amount must be positive")
        if self.kind=='source_sacrifice':
            if (self.amount!=1 or self.counter_name is not None or self.predicate is not None or self.requirements is not None
                    or self.source_ref is not None and (type(self.source_ref) is not str or not self.source_ref)):
                raise ValueError('Source sacrifice requires one bound source without another cost or predicate')
            return
        if self.kind=='mana_discard':
            if self.amount!=1 or self.counter_name is not None or self.source_ref is not None:
                raise ValueError('Compound mana/discard cost requires exactly one ordinary hand card')
            mana=FixedEffectPaymentSpec('mana',requirements=self.requirements)
            FixedEffectPaymentSpec('discard',predicate=self.predicate)
            object.__setattr__(self,'requirements',mana.requirements)
            return
        if self.kind=='remove_source_counter':
            if (type(self.counter_name) is not str or not self.counter_name
                    or normalized_counter_name(self.counter_name)!=self.counter_name
                    or self.source_ref is not None and (type(self.source_ref) is not str or not self.source_ref)):
                raise ValueError('Source counter payments require a canonical named counter and optional current source')
        elif self.counter_name is not None or self.source_ref is not None:
            raise ValueError('Only source counter payments carry counter and source fields')
        if self.kind == "mana" or hybrid:
            if not isinstance(self.requirements, Mapping) or set(self.requirements) != _MANA_KEYS or any(
                type(value) is not int or value < 0 for value in self.requirements.values()
            ) or (not hybrid and not any(self.requirements.values())) or self.amount != 1 or self.predicate is not None:
                raise ValueError("Hybrid mana payment requirements are malformed" if hybrid else
                    "Fixed mana payments require one positive ordinary vector")
            object.__setattr__(self, "requirements", FrozenMap(self.requirements))
            if hybrid:
                hybrid_resolution_mana_options(self.requirements, self.hybrid_symbols)
                object.__setattr__(self, "hybrid_symbols", tuple(self.hybrid_symbols))
        elif self.requirements is not None:
            raise ValueError("Nonmana payments cannot carry mana requirements")
        if self.kind == 'sacrifice' and isinstance(self.predicate, ObjectQuerySpec) and self.predicate.any_of:
            self._validate_sacrifice_union()
            return
        if self.kind == 'tap':
            _validate_fixed_tap_predicate(self.predicate)
        elif self.kind in {"discard", "sacrifice", "exile"}:
            zone = {'discard':'hand','sacrifice':'battlefield','exile':'graveyard'}[self.kind]
            if (self.kind != 'exile' and self.amount not in ({1,2}if self.kind=='discard'else {1})) or not isinstance(self.predicate, ObjectQuerySpec) or self.predicate.zones != (zone,):
                raise ValueError("Zone payments require one closed current-object predicate")
            allowed = ObjectQuerySpec(zones=(zone,), owner=self.predicate.owner,
                controller=self.predicate.controller, types_all=self.predicate.types_all,
                types_any=self.predicate.types_any, colors_all=self.predicate.colors_all,
                colors_any=self.predicate.colors_any, excluded_types=self.predicate.excluded_types,
                subtypes_all=self.predicate.subtypes_all, subtypes_any=self.predicate.subtypes_any,
                supertypes_all=self.predicate.supertypes_all,token=self.predicate.token,
                known_to_actor=self.predicate.known_to_actor,
                exclude_ref=self.predicate.exclude_ref if another else None)
            if self.predicate != allowed or not set((*allowed.types_all, *allowed.types_any)) <= {
                "artifact", "battle", "creature", "enchantment", "instant", "land", "planeswalker", "sorcery"
            } or any(color not in "WUBRG" for color in (*allowed.colors_all, *allowed.colors_any)):
                raise ValueError("Zone payment predicate is outside the closed family")
            if not set(allowed.excluded_types)<={'artifact','creature','land'} or not set(allowed.supertypes_all)<={'basic','legendary','snow'} or any(
                value not in {'aura','clue','desert','food','forest','island','mountain','plains','room','swamp','treasure'} and canonical_creature_subtype(value)!=value
                for value in (*allowed.subtypes_all,*allowed.subtypes_any)
            ):
                raise ValueError('Zone payment quality is not a canonical represented characteristic')
            if self.kind=='discard'and (allowed.controller is not None or allowed.subtypes_all or allowed.subtypes_any or allowed.supertypes_all or allowed.excluded_types or allowed.token is not None):
                raise ValueError('Discard costs use only the closed owned-card quality')
            if self.kind == 'exile' and (type(allowed.owner) is not str or not allowed.owner
                    or allowed.controller is not None or allowed.token is not False or allowed.known_to_actor is not True):
                raise ValueError('Graveyard exile costs require known owned cards without controller restrictions')
        elif self.predicate is not None:
            raise ValueError("Resource payments cannot carry object predicates")

    def _validate_sacrifice_union(self) -> None:
        controller = self.predicate.controller
        if (self.schema_version != 2 or self.amount != 1 or type(controller) is not str or not controller
                or self.predicate != creature_or_vehicle_sacrifice_predicate(controller)):
            raise ValueError("A sacrifice union requires one controlled creature or Vehicle")

    def _validate_blight(self) -> None:
        predicate = self.predicate
        if (
            type(self.schema_version) is not int or self.schema_version != BLIGHT_PAYMENT_VERSION
            or type(self.amount) is not int or self.amount <= 0
            or any(value is not None for value in (self.requirements, self.counter_name, self.source_ref))
            or not isinstance(predicate, ObjectQuerySpec)
            or type(predicate.controller) is not str or not predicate.controller
            or predicate != blight_creature_predicate(predicate.controller)
        ):
            raise ValueError("A Blight payment requires one controlled creature and a fixed counter amount")

    def _validate_mandatory_discard(self) -> None:
        if (
            self.kind != "discard" or type(self.amount) is not int or self.amount != 1
            or any(value is not None for value in (self.requirements, self.counter_name, self.source_ref))
            or not isinstance(self.predicate, ObjectQuerySpec)
            or type(self.predicate.owner) is not str or not self.predicate.owner
            or self.predicate != ObjectQuerySpec(
                zones=("hand",), owner=self.predicate.owner, known_to_actor=True,
            )
        ):
            raise ValueError("Mandatory discard payment requires one unqualified owned hand card")

    def _validate_mandatory_sacrifice(self, permanent_type) -> None:
        predicate = self.predicate
        if (
            self.kind != "sacrifice" or type(self.amount) is not int or self.amount != 1
            or any(value is not None for value in (self.requirements, self.counter_name, self.source_ref))
            or not isinstance(predicate, ObjectQuerySpec)
            or type(predicate.controller) is not str or not predicate.controller
            or predicate != mandatory_sacrifice_predicate(predicate.controller, permanent_type)
        ):
            raise ValueError(f"Mandatory {permanent_type} sacrifice requires one controlled {permanent_type}")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version":self.schema_version, "kind":self.kind, "amount":self.amount,
                "requirements":thaw_value(self.requirements) if self.requirements is not None else None,
                "predicate":self.predicate.to_dict() if self.predicate is not None else None,
                **({'hybrid_symbols':list(self.hybrid_symbols)} if self.kind==HYBRID_RESOLUTION_PAYMENT_KIND else {}),
                **({'counter_name':self.counter_name,'source_ref':self.source_ref} if self.kind=='remove_source_counter' else {}),
                **({'source_ref':self.source_ref} if self.kind=='source_sacrifice' else {})}

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], *, allow_mandatory: bool = False,
    ) -> "FixedEffectPaymentSpec":
        fields={"schema_version", "kind", "amount", "requirements", "predicate"}
        if isinstance(value,Mapping) and value.get('kind')=='remove_source_counter':fields|={'counter_name','source_ref'}
        if isinstance(value,Mapping) and value.get('kind')=='source_sacrifice':fields|={'source_ref'}
        if isinstance(value,Mapping) and value.get('kind')==HYBRID_RESOLUTION_PAYMENT_KIND:fields|={'hybrid_symbols'}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ValueError("Fixed effect payment fields are malformed")
        if value["schema_version"] == OPPONENT_SOURCE_POWER_MANA_PRICE_VERSION:
            raise ValueError("A source-power price requires its original opponent spell occurrence")
        if value["schema_version"] in (MANDATORY_DISCARD_PAYMENT_VERSION, *_MANDATORY_SACRIFICES) and not allow_mandatory:
            raise ValueError("Mandatory object payment is outside this payment's resolution scope")
        predicate = thaw_value(value["predicate"])
        return cls(kind=value["kind"], amount=value["amount"], schema_version=value["schema_version"],
            counter_name=value.get('counter_name'),source_ref=value.get('source_ref'),
            hybrid_symbols=value.get('hybrid_symbols',()),
            requirements=FrozenMap(value["requirements"]) if isinstance(value["requirements"],Mapping) else value["requirements"],
            predicate=ObjectQuerySpec.from_dict(predicate) if isinstance(predicate, Mapping) else predicate)

    @property
    def hybrid_mana_options(self):
        return hybrid_resolution_mana_options(self.requirements, self.hybrid_symbols)

    @property
    def capabilities(self) -> tuple[str, ...]:
        if self.kind == 'resolved_fractional_life':
            from .fractional_life_payment_model import FRACTIONAL_LIFE_PAYMENT_CAPABILITY
            return (FIXED_EFFECT_PAYMENT_CAPABILITY, FRACTIONAL_LIFE_PAYMENT_CAPABILITY)
        costs = {"mana":(), "life":("casting.additional_cost.fixed_life_payment",),
                 'energy':('cost.energy.fixed_resolution_payment',),
                 "discard":("choice.affected_player.fixed_discard", "zone.change.destination_replacement"),
                 "sacrifice":("choice.affected_player.fixed_sacrifice", "zone.change.destination_replacement"),
                 'exile':('cost.exile.owned_graveyard_resolution','zone.change.destination_replacement'),
                 'tap':('cost.tap.controlled_untapped_resolution',),
                 'remove_source_counter':('cost.counter.source_fixed_resolution',)}
        costs['source_sacrifice']=(SOURCE_SACRIFICE_PAYMENT_CAPABILITY,'choice.affected_player.fixed_sacrifice','zone.change.destination_replacement')
        costs['mana_discard']=(MANA_DISCARD_PAYMENT_CAPABILITY,'choice.affected_player.fixed_discard','zone.change.destination_replacement')
        costs["blight"] = (BLIGHT_CAPABILITY,)
        costs[HYBRID_RESOLUTION_PAYMENT_KIND]=(HYBRID_RESOLUTION_PAYMENT_CAPABILITY,)
        costs[RETURN_PAYMENT_KIND] = (CONTROLLED_RETURN_CAPABILITY, "zone.change.destination_replacement")
        return (FIXED_EFFECT_PAYMENT_CAPABILITY, *costs[self.kind], *object_query_capabilities(self.predicate),
            *((MANDATORY_DISCARD_PAYMENT_CAPABILITY,) if self.mandatory and self.kind == "discard" else ()),
            *((_MANDATORY_SACRIFICES[self.schema_version][1],) if self.mandatory_sacrifice_type else ()),
            *((ANOTHER_SACRIFICE_PAYMENT_CAPABILITY,) if self.kind=='sacrifice' and self.schema_version==6 else ()),
            *(('cost.object.repeated_resolution',) if self.schema_version==12 else ()))


EQUIPPED_DEATH_LIFE_DRAW_FIELD = "equipped_death_life_draw"
_EQUIPPED_CREATURE_DEATH = {"field": "attachments", "op": "contains_any", "value": ["$source.ref"]}
_EQUIPPED_DEATH_LIFE_DRAW_DEPENDENCIES = (
    FIXED_EFFECT_PAYMENT_CAPABILITY,
    "attachment.reference.current_or_lki",
    "casting.additional_cost.fixed_life_payment",
    "trigger.event.normalized_zone_change",
    "zone.draw.library_to_hand",
)


def equipped_death_life_draw_effect():
    return {
        "op": "offer_optional_mana_payment",
        "schema_version": 2,
        "player": "$controller",
        EQUIPPED_DEATH_LIFE_DRAW_FIELD: 1,
    }


def equipped_death_life_draw_dependencies(effect):
    if not isinstance(effect, Mapping) or EQUIPPED_DEATH_LIFE_DRAW_FIELD not in effect:
        return ()
    if (type(effect.get("schema_version")) is not int
            or type(effect[EQUIPPED_DEATH_LIFE_DRAW_FIELD]) is not int
            or thaw_value(effect) != equipped_death_life_draw_effect()):
        raise ValueError("Equipped death life draw requires the exact controller payment and same-size draw")
    return _EQUIPPED_DEATH_LIFE_DRAW_DEPENDENCIES


def equipped_death_life_draw_program_dependencies(program):
    effects = [effect for effect in program.effects if EQUIPPED_DEATH_LIFE_DRAW_FIELD in effect]
    if not effects:
        return ()
    if (len(program.effects) != 1 or program.target_schema is not None or program.cost_schema is not None
            or program.handlers or program.active_zone != "battlefield" or program.event != "creature.dies"
            or thaw_value(program.event_condition) != _EQUIPPED_CREATURE_DEATH):
        raise ValueError("Equipped death life draw must use the original creature death and attachment occurrence")
    return equipped_death_life_draw_dependencies(effects[0])


def bind_equipped_death_life_draw(item, raw):
    equipped_death_life_draw_dependencies(raw)
    context = item.context
    power = context.get("power")
    if (context.get("event") != "creature.dies" or type(power) is not int
            or not context.get("card_object_identity") or not context.get("attachments")):
        raise ValueError("The normalized equipped-creature death is missing its recorded last power")
    amount = max(0, power)
    return {
        "op": "offer_optional_mana_payment",
        "schema_version": 2,
        "player": item.controller,
        "payment": FixedEffectPaymentSpec(
            "life", amount=amount, schema_version=EQUIPPED_DEATH_LIFE_PRICE_VERSION,
        ).to_dict(),
        "effects": [{"op": "draw", "player": item.controller, "count": amount, "private": True}] if amount else [],
    }


MANDATORY_SOURCE_COUNTER_FOLLOWUP_VERSION = 16
MANDATORY_SOURCE_COUNTER_FOLLOWUP_MECHANIC = "mandatory-source-counter-followup"


def uses_mandatory_source_counter_followup(value: Any) -> bool:
    return (isinstance(value, Mapping) and value.get("op") == "offer_optional_mana_payment"
            and value.get("schema_version") == MANDATORY_SOURCE_COUNTER_FOLLOWUP_VERSION)


def mandatory_source_counter_followup_parts(value, *, resolved: bool = False):
    if (
        not uses_mandatory_source_counter_followup(value)
        or set(value) != {"op", "schema_version", "player", "payment", "effects"}
        or type(value["schema_version"]) is not int
        or type(value["player"]) is not str or not value["player"]
        or not resolved and value["player"] != "$controller"
    ):
        raise ValueError("Mandatory counter payment requires its closed resolution wrapper")
    spec = FixedEffectPaymentSpec.from_dict(value["payment"])
    if (spec.kind, spec.counter_name, spec.amount) != ("remove_source_counter", "+1/+1", 1):
        raise ValueError("Mandatory counter payment requires one +1/+1 counter from the source")
    if not resolved and spec.source_ref != "$source.zone_object":
        raise ValueError("Mandatory counter payment requires the original source incarnation")
    effects = value["effects"]
    if (not isinstance(effects, (tuple, list)) or len(effects) != 1 or not isinstance(effects[0], Mapping)
            or effects[0].get("op") not in {"damage", "create_token"}):
        raise ValueError("Mandatory counter payment requires its fixed damage or token result")
    return spec, effects[0]


def mandatory_sacrifice_predicate(controller, permanent_type):
    return ObjectQuerySpec(zones=("battlefield",), controller=controller, types_any=(permanent_type,),
                           known_to_actor=True)


def mandatory_artifact_sacrifice_predicate(controller):
    return mandatory_sacrifice_predicate(controller, "artifact")


def mandatory_artifact_sacrifice_result_parts(value, *, resolved=False):
    if (set(value) != {"op", "schema_version", "player", "payment", "effects"}
            or value["op"] != "offer_optional_mana_payment"
            or type(value["schema_version"]) is not int or value["schema_version"] != 2
            or type(value["player"]) is not str or not value["player"]
            or not resolved and value["player"] != "$controller"):
        raise ValueError("Mandatory artifact sacrifice requires the fixed controller result")
    payment = FixedEffectPaymentSpec.from_dict(value["payment"], allow_mandatory=True)
    if (payment.schema_version != MANDATORY_ARTIFACT_SACRIFICE_PAYMENT_VERSION
            or payment.predicate.controller != value["player"]):
        raise ValueError("Mandatory artifact sacrifice requires its current controller")
    effects = value["effects"]
    if not isinstance(effects, (list, tuple)) or len(effects) != 1 or not isinstance(effects[0], Mapping):
        raise ValueError("Mandatory artifact sacrifice requires its single source counter result")
    child = effects[0]
    expected = {"op": "place_counters", "card": "$source.zone_object", "counter": "+1/+1",
                "amount": 2, "source": "$source"}
    if resolved and set(child) == set(expected):
        for field in ("card", "source"):
            bound = child[field]
            if bound is not None and (type(bound) is not str or not bound or bound.startswith("$")):
                raise ValueError("Artifact sacrifice result lost its bound counter recipient")
            expected[field] = bound
    if type(child.get("amount")) is not int or FrozenMap(child) != FrozenMap(expected):
        raise ValueError("Mandatory artifact sacrifice requires two counters on the original source")
    return payment, child


def fixed_payment_payload_dependencies(value: Mapping[str, Any]) -> tuple[str, ...]:
    if not isinstance(value, Mapping) or value.get("op") != "offer_optional_mana_payment":
        return ()
    from .rules.opponent_event_mana_payment import opponent_event_mana_payment_capabilities, uses_opponent_event_mana_payment
    from .rules.attached_copy_payment import attached_copy_payment_capabilities, uses_attached_copy_payment

    if uses_attached_copy_payment(value):
        return attached_copy_payment_capabilities(value)
    if uses_opponent_event_mana_payment(value):
        return opponent_event_mana_payment_capabilities(value)
    if required := equipped_death_life_draw_dependencies(value):
        return required
    if uses_mandatory_source_counter_followup(value):
        payment, _ = mandatory_source_counter_followup_parts(value)
        return payment.capabilities
    if value.get('schema_version') == 13:
        from .rules.optional_sacrifice_stats import optional_sacrifice_stat_node_capabilities
        required = optional_sacrifice_stat_node_capabilities(effects=(value,), target_schema=None)
        if not required:raise ValueError('Resolution sacrifice stats require their complete price and result')
        return required
    if value.get('schema_version') == 12:
        from .rules.recover import recover_node_dependencies
        return recover_node_dependencies(value)
    from .rules.scheduled_life_return import is_scheduled_life_return, scheduled_life_return_payload_dependencies
    if is_scheduled_life_return(value):
        return scheduled_life_return_payload_dependencies(value)
    from .rules.targeted_life_decline import is_targeted_life_decline_effect, targeted_life_decline_payload_dependencies
    if is_targeted_life_decline_effect(value):
        return targeted_life_decline_payload_dependencies(value)
    descriptor = value.get("payment")
    if not isinstance(descriptor, Mapping):
        return ()
    if descriptor.get("schema_version") == EQUIPPED_DEATH_LIFE_PRICE_VERSION:
        raise ValueError("An equipped-death life quote may only be generated at resolution")
    from .rules.entry_player_counter_payment import (
        entry_player_counter_payment_capabilities, is_entry_player_counter_payment, uses_entry_player_counter_payment,
    )
    if uses_entry_player_counter_payment(value):
        if not is_entry_player_counter_payment(value):
            raise ValueError("Land entry counter payment requires its fixed scoped descriptor")
        return entry_player_counter_payment_capabilities()
    if descriptor.get("kind") == "blight":
        payment = FixedEffectPaymentSpec.from_dict(descriptor)
        if (type(value.get("schema_version")) is not int or value["schema_version"] not in {2, 4}
                or value.get("player") != "$controller" or payment.predicate.controller != "$controller"):
            raise ValueError("Blight payment requires its closed controller resolution scope")
        return payment.capabilities
    if descriptor.get("kind") == HYBRID_RESOLUTION_PAYMENT_KIND:
        if type(value.get("schema_version")) is not int or value["schema_version"] != 2 or "cost" in value:
            raise ValueError("Hybrid prices require their version-two optional payment wrapper")
        return FixedEffectPaymentSpec.from_dict(descriptor).capabilities
    if descriptor.get("kind") == RETURN_PAYMENT_KIND:
        payment = FixedEffectPaymentSpec.from_dict(descriptor)
        if (type(value.get("schema_version")) is not int or value["schema_version"] != 2
                or value.get("player") != "$controller" or payment.predicate.controller != "$controller"
                or "cost" in value or "declined_effects" in value):
            raise ValueError("Return payment requires its closed controller resolution scope")
        return payment.capabilities
    if descriptor.get("schema_version") == MANDATORY_ARTIFACT_SACRIFICE_PAYMENT_VERSION:
        payment, _ = mandatory_artifact_sacrifice_result_parts(value)
        return (*payment.capabilities, "counter.producer.fixed_effect")
    if descriptor.get("schema_version") in MANDATORY_REANIMATION_PAYMENT_VERSIONS:
        from .rules.mandatory_sacrifice_reanimation import mandatory_sacrifice_reanimation_payload_dependencies

        return mandatory_sacrifice_reanimation_payload_dependencies(value)
    mandatory = descriptor.get("schema_version") == MANDATORY_DISCARD_PAYMENT_VERSION
    if not mandatory and descriptor.get("kind") != "mana_discard":
        return ()
    if mandatory and (type(value.get("schema_version")) is not int or value["schema_version"] != 2):
        raise ValueError("Mandatory discard requires a fixed resolution payment")
    payment = FixedEffectPaymentSpec.from_dict(descriptor, allow_mandatory=mandatory)
    if payment.predicate.owner != value.get("player"):
        label = "Mandatory discard" if mandatory else "Combined payment"
        raise ValueError(f"{label} requires its own payer hand")
    return payment.capabilities
