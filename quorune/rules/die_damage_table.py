"""Resolve one closed die table through ordinary object choice and damage."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..announced_damage import ANNOUNCED_DAMAGE_EFFECT_FIELD, AllocatedDamageRecipient
from ..errors import GameRuleError
from ..fixed_damage_set import snapshot_fixed_damage_set
from ..fixed_damage_set_model import FixedDamageSetSpec, PermanentControllerRelation, PermanentDamageGroup
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap, thaw_value
from .effect_shape_walkers import nested_effects


CAPABILITY = "random.die.fixed_creature_damage_table"
TABLE_FIELD = "rolled_creature_damage_table"
OUTCOME_CONTEXT = "bound_creature_damage_table_roll"
SPARE_CHOICE_FIELD = "creature_damage_table_spare_choice"
SPARED_OUTCOME_FIELD = "creature_damage_table_spared_outcome"
SIDES = 20
DEPENDENCIES = (
    CAPABILITY, "damage.batch.fixed_set", "damage.amount.positive", "damage.source.stack_current_or_lki",
    "damage.result.creature_mark",
)
_CREATURES = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))
_ALL = FixedDamageSetSpec((PermanentDamageGroup(_CREATURES),))
_OPPONENTS = FixedDamageSetSpec((PermanentDamageGroup(_CREATURES, PermanentControllerRelation.OPPONENTS),))
_SELECTOR = {"zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"], "min": 0, "max": 1}


@dataclass(frozen=True, slots=True)
class CreatureDamageTableRoll:
    stack_ref: str
    instruction_pointer: int
    result: int

    def __post_init__(self):
        if (type(self.stack_ref) is not str or not self.stack_ref
                or type(self.instruction_pointer) is not int or self.instruction_pointer < 0
                or type(self.result) is not int):
            raise ValueError("The creature damage table needs its exact retained die result")

    def to_dict(self):
        return {
            "schema_version": 1, "stack_ref": self.stack_ref, "instruction_pointer": self.instruction_pointer,
            "result": self.result,
        }

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {
                "schema_version", "stack_ref", "instruction_pointer", "result",
            } or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("The creature damage table receipt has a closed schema")
        return cls(value["stack_ref"], value["instruction_pointer"], value["result"])

    @property
    def optional_spare(self):
        return 10 <= self.result <= 19


def creature_damage_table_effect():
    return {"op": "damage_fixed_set", "source": "$source", TABLE_FIELD: 1}


def die_damage_table_runtime_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if TABLE_FIELD in effect or SPARE_CHOICE_FIELD in effect or SPARED_OUTCOME_FIELD in effect)
    if not marked:
        return ()
    if (
        len(marked) != 1 or marked != tuple(program.effects) or program.handlers
        or type(marked[0].get(TABLE_FIELD)) is not int or dict(marked[0]) != creature_damage_table_effect()
        or (program.event, program.active_zone) != ("resolve", "stack")
        or program.target_schema is not None or program.cost_schema is not None or program.event_condition is not None
        or not program.ability_id.startswith("spell:")
    ):
        raise ValueError("The creature damage table requires its complete original untargeted spell")
    return DEPENDENCIES


def is_closed_die_damage_table_program(program):
    try:
        required = die_damage_table_runtime_dependencies(program)
    except (ValueError, TypeError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def _require_resolving_table(host, item):
    program = host.semantics.get(item.semantic_key)
    if (
        item.kind not in {"spell", "spell_copy"} or item.context.get("currently_resolving") is not True
        or item.controller not in host.active_seats or program is None
        or not host.semantic_program_is_current_trusted(program) or not is_closed_die_damage_table_program(program)
    ):
        raise GameRuleError("The creature damage table requires its trusted resolving spell")


def _group_damage(spec, amount):
    return {"op": "damage_fixed_set", "source": "$source", "amount": amount, **spec.to_dict()}


def bind_die_damage_table(host, item, effect, *, instruction_pointer):
    from .night_shift_dice import roll_adjusted_game_die

    _require_resolving_table(host, item)
    if type(effect.get(TABLE_FIELD)) is not int or dict(effect) != creature_damage_table_effect():
        raise GameRuleError("The resolving die table changed its printed damage rows")
    saved = item.context.get(OUTCOME_CONTEXT)
    if saved is None:
        rolled = roll_adjusted_game_die(host, item, sides=SIDES, instruction_pointer=instruction_pointer)
        receipt = CreatureDamageTableRoll(item.ref, instruction_pointer, rolled.single_result)
        item.context[OUTCOME_CONTEXT] = receipt.to_dict()
    else:
        receipt = CreatureDamageTableRoll.from_dict(saved)
        if (receipt.stack_ref, receipt.instruction_pointer) != (item.ref, instruction_pointer):
            raise GameRuleError("The die table tried to reuse an unrelated roll")

    if 1 <= receipt.result <= 9:
        return host._semantic_value(_group_damage(_ALL, 3), item)
    if receipt.result == 20:
        return host._semantic_value(_group_damage(_OPPONENTS, 6), item)
    if not receipt.optional_spare:
        return None

    candidates = snapshot_fixed_damage_set(host, actor=item.controller, spec=_ALL).recipients
    return {
        "op": "choose_objects", "player": item.controller, "prompt": "You may choose a creature to spare.",
        "selector": thaw_value(FrozenMap(_SELECTOR)), "minimum": 0, "maximum": 1,
        SPARE_CHOICE_FIELD: {
            "roll": receipt.to_dict(),
            "candidates": {row.ref: row.logical_object_id for row in candidates},
        },
    }


def die_damage_spare_followup(continuation, query, actor, selected, rows):
    effect = continuation.effect
    payload = effect.get(SPARE_CHOICE_FIELD)
    if SPARE_CHOICE_FIELD not in effect:
        return ()
    from ..semantic_choices.model import SemanticChoiceError

    stack = query.stack_object(continuation.stack_ref)
    try:
        if not isinstance(payload, Mapping) or set(payload) != {"roll", "candidates"}:
            raise ValueError("The creature damage table lost its offered choice")
        receipt = CreatureDamageTableRoll.from_dict(payload["roll"])
        candidates = payload["candidates"]
        if (
            receipt.stack_ref != continuation.stack_ref or not receipt.optional_spare or stack is None
            or stack.controller != actor or actor not in query.active_seats or effect.get("player") != actor
            or not isinstance(candidates, Mapping) or len(selected) > 1
        ):
            raise ValueError("Only the resolving controller may spare one current creature")
        spared = None
        if selected:
            row, = rows
            if (
                row.zone != "battlefield" or row.phased_out or "creature" not in row.types
                or not row.logical_object_id or candidates.get(row.ref) != row.logical_object_id
            ):
                raise ValueError("The spared creature is no longer the original eligible object")
            spared = row.logical_object_id
    except (ValueError, TypeError, KeyError) as exc:
        raise SemanticChoiceError(str(exc)) from exc
    return (FrozenMap({
        "op": "damage_fixed_set", "source": "$source",
        SPARED_OUTCOME_FIELD: {"roll": receipt.to_dict(), "spared": spared},
    }),)


def bind_die_damage_spared_outcome(host, item, effect):
    _require_resolving_table(host, item)
    payload = effect[SPARED_OUTCOME_FIELD]
    if (
        set(effect) != {"op", "source", SPARED_OUTCOME_FIELD}
        or effect["op"] != "damage_fixed_set" or effect["source"] != "$source"
        or not isinstance(payload, Mapping) or set(payload) != {"roll", "spared"}
        or (payload["spared"] is not None and (type(payload["spared"]) is not str or not payload["spared"]))
    ):
        raise GameRuleError("The creature damage table's spared result has invalid fields")
    receipt = CreatureDamageTableRoll.from_dict(payload["roll"])
    saved = CreatureDamageTableRoll.from_dict(item.context.get(OUTCOME_CONTEXT))
    if receipt != saved or receipt.stack_ref != item.ref or not receipt.optional_spare:
        raise GameRuleError("The creature damage table's spared result lost its retained roll")
    spared = payload["spared"]
    if spared is None:
        return host._semantic_value(_group_damage(_ALL, 3), item)
    chosen = snapshot_fixed_damage_set(host, actor=item.controller, spec=_ALL)
    recipients = [
        AllocatedDamageRecipient(row.ref, row.logical_object_id, 3).to_dict()
        for row in chosen.recipients if row.logical_object_id != spared
    ]
    if not recipients:
        return None
    return host._semantic_value({
        "op": "damage_fixed_set", "source": "$source",
        "amount": 3 * len(recipients), ANNOUNCED_DAMAGE_EFFECT_FIELD: recipients,
    }, item)
