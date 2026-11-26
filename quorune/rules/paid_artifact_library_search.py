"""Freeze a selected artifact's battlefield mana value for one library result."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..activation_cost_choice import CostChoice
from ..errors import GameRuleError
from ..library_search_model import PAID_ARTIFACT_SEARCH_VERSION
from ..object_predicate import ObjectQuerySpec
from ..public_quantity_reductions import public_mana_value
from ..replacement.immutable import FrozenMap, thaw_value
from ..scalar_effect_amount_model import PaidSacrificeManaValueSnapshot


PAID_ARTIFACT_SEARCH_CAPABILITY = "library.search.paid_artifact_mana_value_plus_one"
PAID_ARTIFACT_SEARCH_MECHANIC = "paid-artifact-mana-value-library-search"
PAID_ARTIFACT_SEARCH_TEMPLATE = "paid-artifact-mana-value-library-search-v1"
PAID_ARTIFACT_SEARCH_CONTEXT = "paid_artifact_search"
_SEARCH_WORDING = (
    "search your library for an artifact card with mana value equal to 1 plus the sacrificed "
    "artifact's mana value, put that card onto the battlefield, then shuffle."
)
_REQUIRED = (PAID_ARTIFACT_SEARCH_CAPABILITY, "library.search.fixed_counted", "activation.selected_zone_change.fixed")
_ARTIFACT_COST = CostChoice("sacrifice_one", 1, "battlefield", another=True, predicate=FrozenMap(
    ObjectQuerySpec(zones=("battlefield",), controller="$actor", types_any=("artifact",), known_to_actor=True).to_dict()
))


def is_paid_artifact_search(effect):
    return effect.get("op") == "search" and effect.get("schema_version") == PAID_ARTIFACT_SEARCH_VERSION


def paid_artifact_search_effect(*, bound=None):
    selector = {"types": ["artifact"]}
    if bound is not None:
        selector["mana_value"] = {"equal": bound}
    return {
        "op": "search", "schema_version": PAID_ARTIFACT_SEARCH_VERSION, "zone": "library",
        "selector": selector, "destination": "battlefield", "count": {"minimum": 1, "maximum": 1},
        "reveal": False, "shuffle_after": True,
    }


def validate_paid_artifact_search_effect(effect, *, materialized=False):
    if materialized:
        selector = effect.get("selector")
        condition = selector.get("mana_value") if isinstance(selector, Mapping) else None
        bound = condition.get("equal") if isinstance(condition, Mapping) else None
        if type(bound) is not int or bound <= 0:
            raise ValueError("Artifact search requires an exact integer mana value after payment")
    else:
        bound = None
    if type(effect.get("schema_version")) is not int or thaw_value(effect) != paid_artifact_search_effect(bound=bound):
        raise ValueError("Artifact search requires its fixed private library and battlefield result")


def _fixed_artifact_price(cost):
    if not isinstance(cost, Mapping):
        return False
    fixed = {
        "tap_source": True, "untap_source": False, "sacrifice_source": False,
        "discard_source": False, "exile_source": False, "loyalty_delta": None, "source_counter_removal_cost": None,
        "life_payment": 0, "energy_payment": 0, "complex_symbols": [], "uncompiled_costs": [],
        "choices": [_ARTIFACT_COST.compact()],
    }
    if set(cost) != set(fixed) | {"text", "mana"} or not isinstance(cost["mana"], Mapping):
        return False
    if any(type(value) is not int or value < 0 for value in cost["mana"].values()):
        return False
    return (set(cost["mana"]) == {"GENERIC", *"WUBRGC"}
            and {key: value for key, value in cost["mana"].items() if value} == {"GENERIC": 2}
            and all(type(cost[key]) is type(value) for key, value in fixed.items() if type(value) in (bool, int))
            and all(thaw_value(cost[key]) == thaw_value(value) for key, value in fixed.items()))


def _ability_matches_paid_artifact_search(ability):
    return (
        ability.zones == ("battlefield",) and ability.sorcery_speed and not ability.mana_ability
        and ability.choices == (_ARTIFACT_COST,) and ability.target_schema is None
        and ability.activation_limit is None and not ability.activation_conditions
    )


def paid_artifact_library_search_template(ability, text, *, public_timing, cost):
    if (public_timing is not None or text.casefold().replace("’", "'") != _SEARCH_WORDING
            or not _ability_matches_paid_artifact_search(ability) or not _fixed_artifact_price(cost)):
        return None
    return PAID_ARTIFACT_SEARCH_TEMPLATE, (paid_artifact_search_effect(),), None, (PAID_ARTIFACT_SEARCH_MECHANIC,)


def paid_artifact_search_node_capabilities(*, effects, target_schema, mechanics, active_zone, cost_schema):
    if (PAID_ARTIFACT_SEARCH_MECHANIC not in mechanics or len(effects) != 1 or target_schema is not None
            or active_zone != "battlefield" or not _fixed_artifact_price(cost_schema)):
        return ()
    validate_paid_artifact_search_effect(effects[0])
    return _REQUIRED


def paid_artifact_search_runtime_dependencies(program):
    from .effect_shape_walkers import nested_effects

    if PAID_ARTIFACT_SEARCH_MECHANIC not in program.coverage and not any(
        is_paid_artifact_search(effect) for effect in nested_effects((*program.effects, *program.handlers))
    ):
        return ()
    required = paid_artifact_search_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanics=program.coverage,
        active_zone=program.active_zone, cost_schema=program.cost_schema,
    )
    if (not required or program.event != "activate" or program.event_condition is not None
            or len(program.handlers) != 1):
        raise ValueError("Paid artifact search requires the complete sorcery-speed nonmana activation")
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler

    ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    if ability.builtin_semantic_key != program.key or not _ability_matches_paid_artifact_search(ability):
        raise ValueError("Paid artifact search requires its original activation catalog")
    return required


@dataclass(frozen=True)
class PaidArtifactSearchReceipt:
    ability_key: str
    source_logical_object_id: str
    payer: str
    paid_object_id: str
    snapshot: PaidSacrificeManaValueSnapshot

    def __post_init__(self):
        fields = (self.ability_key, self.source_logical_object_id, self.payer, self.paid_object_id)
        if (any(type(value) is not str or not value for value in fields)
                or not isinstance(self.snapshot, PaidSacrificeManaValueSnapshot)
                or not self.snapshot.logical_object_id.startswith(self.paid_object_id + "@")):
            raise ValueError("Paid artifact search receipt lost its original identities")

    def to_dict(self):
        return {
            "ability_key": self.ability_key, "source_logical_object_id": self.source_logical_object_id,
            "payer": self.payer, "paid_object_id": self.paid_object_id, "snapshot": self.snapshot.to_dict(),
        }

    @classmethod
    def from_dict(cls, value):
        fields = {"ability_key", "source_logical_object_id", "payer", "paid_object_id", "snapshot"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ValueError("Paid artifact search has no exact sacrifice receipt")
        return cls(**{**value, "snapshot": PaidSacrificeManaValueSnapshot.from_dict(value["snapshot"])})


def capture_paid_artifact_search_receipt(host, proposal, source, ability, response, program):
    from .activation_costs import activation_choice_candidates

    required = paid_artifact_search_runtime_dependencies(program)
    if not required or not set(required) <= set(program.capability_dependencies):
        raise ValueError("Artifact search lacks its trusted payment and result owner")
    if not _ability_matches_paid_artifact_search(ability) or "cost_cards" in response and "cost_objects" in response:
        raise ValueError("Paid artifact activation selection is unavailable or ambiguous")
    refs = response.get("cost_cards", response.get("cost_objects", ()))
    if (not isinstance(refs, (list, tuple)) or len(refs) != 1 or type(refs[0]) is not str
            or refs[0] not in activation_choice_candidates(host, proposal.seat, source, _ARTIFACT_COST, ability=ability)):
        raise ValueError("Pay with exactly one other legal artifact you control")
    card = host._resolve_object(proposal.seat, refs[0], zones={"battlefield"})
    snapshot = PaidSacrificeManaValueSnapshot(card.logical_object_id, public_mana_value(host._effective_card_data(card)))
    receipt = PaidArtifactSearchReceipt(program.key, source.logical_object_id, proposal.seat, card.object_id, snapshot)
    return PAID_ARTIFACT_SEARCH_CONTEXT, receipt.to_dict()


def validate_paid_artifact_search_payment(paid_objects, context):
    if context is not None and context[0] == PAID_ARTIFACT_SEARCH_CONTEXT:
        receipt = PaidArtifactSearchReceipt.from_dict(context[1])
        if tuple(paid_objects) != (receipt.paid_object_id,):
            raise GameRuleError("Paid artifact search did not commit its exact selected sacrifice")


def is_closed_paid_artifact_search_program(program):
    try:
        required = paid_artifact_search_runtime_dependencies(program)
    except (ValueError, TypeError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def materialize_paid_artifact_search(host, item, effect, *, already_materialized=False):
    try:
        receipt = PaidArtifactSearchReceipt.from_dict(item.context.get(PAID_ARTIFACT_SEARCH_CONTEXT))
        program = host.semantics.get(item.semantic_key)
        if (program is None or not host.semantic_program_is_current_trusted(program)
                or not paid_artifact_search_runtime_dependencies(program)
                or item.context.get("currently_resolving") is not True
                or item.context.get("source_logical_object_id") != receipt.source_logical_object_id
                or item.semantic_key != receipt.ability_key):
            raise ValueError("Paid artifact search lost its original resolving activation")
        if item.kind == "activated_ability_copy":
            if not item.context.get("copied_from_stack"):
                raise ValueError("Paid artifact search copy lost its original payment")
        elif item.kind != "activated_ability" or item.controller != receipt.payer:
            raise ValueError("Paid artifact search lost its original payer")
        value = receipt.snapshot.mana_value + 1
        bounded = paid_artifact_search_effect(bound=value)
        if already_materialized:
            if thaw_value(effect) != bounded:
                raise ValueError("Resumed artifact search differs from the committed sacrifice")
        else:
            validate_paid_artifact_search_effect(effect)
        return bounded
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
