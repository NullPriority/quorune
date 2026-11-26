from __future__ import annotations

"""Closed activation payment for one Forage resource choice."""

from dataclasses import dataclass

from ..additional_cost_vocabulary import EXILE_ONE_FROM_GRAVEYARD_COST, SACRIFICE_ONE_COST
from ..errors import GameRuleError
from ..object_predicate import ObjectQuerySpec
from .casting_additional_costs import FixedZoneChangeAdditionalCost, fixed_zone_change_cost_candidates


FORAGE_ACTIVATION_CAPABILITY = "activation.cost.forage"
FORAGE_COST_KIND = "forage"
_FOOD_PAYMENT = FixedZoneChangeAdditionalCost(
    operation=SACRIFICE_ONE_COST, choice_field="sacrifice_cards",
    predicate=ObjectQuerySpec(zones=("battlefield",), controller="$actor", types_all=("artifact",),
                             subtypes_all=("food",), known_to_actor=True),
)
_GRAVEYARD_PAYMENT = FixedZoneChangeAdditionalCost(
    operation=EXILE_ONE_FROM_GRAVEYARD_COST, choice_field="exile_cards", count=3,
    predicate=ObjectQuerySpec(zones=("graveyard",), owner="$actor", known_to_actor=True),
)


def has_forage_cost(ability):
    return any(choice.kind == FORAGE_COST_KIND for choice in ability.choices)


def validate_forage_activation(ability):
    from .forage_mana import forage_mana_text

    mana = ability.mana_ability and ability.tap_source and not any(ability.mana.values()) and forage_mana_text(
        ability.cost_text, ability.effect_text)
    if (
        len(ability.choices) != 1 or ability.choices[0].kind != FORAGE_COST_KIND
        or ability.zones != ("battlefield",) or ability.mana_ability != mana
        or ability.tap_source != mana or ability.untap_source or ability.sacrifice_source
        or ability.exile_source or ability.discard_source or ability.life_payment or ability.energy_payment
        or ability.mana_cost_options or ability.complex_symbols or ability.uncompiled_costs
        or ability.announced_x_symbols is not None or ability.loyalty_delta is not None
        or ability.source_counter_removal_cost is not None or ability.waterbend is not None
    ):
        raise ValueError("Forage requires one isolated battlefield choice and a fixed ordinary mana price")


def _forage_branches(host, actor):
    food = fixed_zone_change_cost_candidates(host, actor=actor, cost=_FOOD_PAYMENT)
    graveyard = fixed_zone_change_cost_candidates(host, actor=actor, cost=_GRAVEYARD_PAYMENT)
    card_refs = {
        host.state.cards[object_id].ref for object_id in host.state.players[actor].zones["graveyard"]
        if host.state.cards[object_id].is_card_object
    }
    graveyard = tuple(ref for ref in graveyard if ref in card_refs)
    return tuple((cost, refs) for cost, refs in ((_FOOD_PAYMENT, food), (_GRAVEYARD_PAYMENT, graveyard))
                 if len(refs) >= cost.count)


def forage_candidates(host, actor):
    return tuple(ref for _cost, refs in _forage_branches(host, actor) for ref in refs)


def forage_choice_schema(host, actor):
    branches = _forage_branches(host, actor)
    if not branches:
        return None
    counts = [cost.count for cost, _ in branches]
    labels = {
        1: "Forage by sacrificing one Food you control",
        3: "Forage by exiling three cards from your graveyard",
    }
    return {"cost_cards": {
        "type": "array",
        "label": labels[counts[0]] if len(counts) == 1 else "Forage by sacrificing one Food or exiling three cards from your graveyard",
        "legal_refs": [ref for _cost, refs in branches for ref in refs],
        "minimum": min(counts), "maximum": max(counts), "allowed_counts": counts,
    }}


@dataclass(frozen=True, slots=True)
class ForagePayment:
    cost: FixedZoneChangeAdditionalCost
    refs: tuple[str, ...]
    identities: tuple[str, ...]
    preserved_food: str | None

    def pay(self, host, actor, response):
        from .activation_costs import pay_fixed_zone_change_cost

        current = choose_forage_payment(host, actor, response)
        if current != self:
            raise GameRuleError("A selected Forage payment object changed before payment")
        return pay_fixed_zone_change_cost(host, actor=actor, cost=self.cost, refs=self.refs,
                                          response=response, reason="Forage activation cost")


def choose_forage_payment(host, actor, response):
    if "cost_cards" in response and "cost_objects" in response and response["cost_cards"] != response["cost_objects"]:
        raise GameRuleError("Forage payment selections disagree")
    refs = response.get("cost_cards", response.get("cost_objects"))
    if (not isinstance(refs, (list, tuple)) or any(type(ref) is not str for ref in refs)
            or len(refs) != len(set(refs))):
        raise GameRuleError("Choose one Food or three distinct cards from your graveyard to forage")
    for cost, eligible in _forage_branches(host, actor):
        if len(refs) != cost.count or any(ref not in eligible for ref in refs):
            continue
        food = cost.operation == SACRIFICE_ONE_COST
        cards = tuple(host._resolve_object(
            actor, ref, zones={cost.origin_zone}, controlled_only=food, owned_only=not food,
        ) for ref in refs)
        return ForagePayment(cost, tuple(refs), tuple(card.logical_object_id for card in cards),
                             cards[0].object_id if food else None)
    raise GameRuleError("Forage requires either one Food you control or three cards from your graveyard")


def pay_forage_activation_costs(host, proposal, source, response):
    from .mana_pool_payment import MANA_POOL_PAYMENT_FIELD
    from ..mana_restrictions import ability_mana_spend_context
    from .action_proposals import thaw_json

    actor = proposal.seat
    payment = choose_forage_payment(host, actor, response)
    requirements = dict(thaw_json(proposal.requirements))
    spent, activations = {}, []
    if sum(requirements.values()) or MANA_POOL_PAYMENT_FIELD in response:
        type_line = str(host._effective_card_data(source).get("type_line") or "")
        protection = {"preserve_source_id": payment.preserved_food} if payment.preserved_food is not None else {}
        mana_payment = host._pay_for_cost(
            actor, requirements, response, **protection,
            spend_context=ability_mana_spend_context(type_line, source_zone=source.zone, source_type_line=type_line),
        )
        spent = mana_payment.spent
        activations = mana_payment.activations
    return payment.pay(host, actor, response), activations, spent, None


def forage_activation_runtime_dependencies(program):
    from ..abilities import ActivatedAbility, CostChoice
    from dataclasses import replace
    from ..replacement.immutable import thaw_value
    from ..semantic_runtime.activated_abilities import ACTIVATED_ABILITY_CATALOG_HANDLER_ID

    cost = program.cost_schema or {}
    choices = tuple(cost.get("choices", ()))
    catalog = tuple(handler["ability"] for handler in program.handlers
                    if handler.get("handler_id") == ACTIVATED_ABILITY_CATALOG_HANDLER_ID)
    touches_forage = any(choice.get("kind", choice.get("k")) == FORAGE_COST_KIND for choice in choices)
    touches_forage |= any(choice.get("kind") == FORAGE_COST_KIND
                          for value in catalog for choice in value.get("choices", ()))
    if not touches_forage:
        return ()
    expected = CostChoice(kind=FORAGE_COST_KIND)
    if ((program.event, program.active_zone) != ("activate", "battlefield") or len(catalog) != 1
            or tuple(thaw_value(value) for value in choices) != (expected.compact(),)
            or cost.get("text") != catalog[0]["cost_text"]
            or thaw_value(cost.get("mana")) != thaw_value(catalog[0]["mana"])):
        raise ValueError("A Forage activation requires its matching catalog and public cost schema")
    ability = ActivatedAbility.from_dict(thaw_value(catalog[0]))
    validate_forage_activation(ability)
    if ability.mana_ability:
        from .forage_mana import CAPABILITY, HANDLER_ID
        from ..semantic_runtime.mana_abilities import ForageActivatedManaHandler

        handlers = tuple(row for row in program.handlers if row.get("handler_id") == HANDLER_ID)
        if program.effects or program.target_schema is not None or len(handlers) != 1 or len(program.handlers) != 2:
            raise ValueError("Forage mana requires its matching typed fixed-output carrier")
        spec = ForageActivatedManaHandler().validate(handlers[0])
        if ability != replace(spec.to_activated_ability(), builtin_semantic_key=program.key):
            raise ValueError("Forage mana changed its declared cost or color combination")
        return (FORAGE_ACTIVATION_CAPABILITY, CAPABILITY)
    return (FORAGE_ACTIVATION_CAPABILITY,)
