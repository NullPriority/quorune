from __future__ import annotations

"""Enumerate and commit one currently legal hand spell under a fixed free price."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..bounded_hand_cast_model import (
    BOUNDED_HAND_CAST_MECHANIC, BoundedHandCastSpec, LEGENDARY_WIZARD_HAND_CAST_CONDITION,
    LEGENDARY_WIZARD_HAND_CAST_VERSION, bounded_hand_cast_payload_dependencies, is_legendary_wizard_hand_cast_effect,
    DAMAGED_PLAYER_HAND_CAST_VERSION, SOURCE_COMBAT_DAMAGE_PLAYER_CONDITION, is_damaged_player_hand_cast_effect,
    ANNOUNCED_X_HAND_CAST_VERSION,
    ANNOUNCED_X_HAND_CAST_VERSION, has_announced_x_hand_cast_amount,
)
from ..characteristic_evaluation import type_parts
from ..discover_model import discover_options_fingerprint
from ..errors import GameRuleError, StateInvariantError
from ..public_quantity_reductions import public_mana_value
from ..replacement.immutable import FrozenMap, thaw_value
from .casting.face_queries import offered_cast_methods, prospective_cast_card
from .casting.characteristic_methods import characteristic_cast_view
from .casting.proposal import build_cast_offer
from .effect_shape_walkers import nested_effects
from .resolving_cast_payments import commit_resolving_cast


def legendary_wizard_hand_cast_runtime_dependencies(program):
    effects = tuple(program.effects)
    marked = tuple(effect for effect in nested_effects((*effects, *program.handlers))
                   if is_legendary_wizard_hand_cast_effect(effect))
    if not marked:
        return ()
    if (len(marked) != 1 or effects != marked or program.handlers or program.target_schema is not None
            or program.cost_schema is not None or program.trigger_limit is not None
            or not {BOUNDED_HAND_CAST_MECHANIC, CURRENT_ABILITY_FRAGMENT_COVERAGE} <= set(program.coverage)
            or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or FrozenMap(program.event_condition or {}) != LEGENDARY_WIZARD_HAND_CAST_CONDITION):
        raise ValueError("Legendary Wizard hand cast requires its exact own-turn combat trigger")
    return bounded_hand_cast_payload_dependencies(effects[0])


def damaged_player_hand_cast_runtime_dependencies(program):
    effects = tuple(program.effects)
    marked = tuple(effect for effect in nested_effects((*effects, *program.handlers))
                   if is_damaged_player_hand_cast_effect(effect))
    if not marked:
        return ()
    if (len(marked) != 1 or effects != marked or program.handlers or program.target_schema is not None
            or program.cost_schema is not None or program.trigger_limit is not None
            or not {BOUNDED_HAND_CAST_MECHANIC, CURRENT_ABILITY_FRAGMENT_COVERAGE} <= set(program.coverage)
            or (program.event, program.active_zone) != ("damage.dealt.self", "battlefield")
            or FrozenMap(program.event_condition or {}) != SOURCE_COMBAT_DAMAGE_PLAYER_CONDITION):
        raise ValueError("The inspected hand cast requires the original source's combat damage to a player")
    return bounded_hand_cast_payload_dependencies(effects[0])


def _live_item(host, actor, stack_ref):
    item = next((row for row in host.state.stack if row.ref == stack_ref), None)
    if (item is None or item.controller != actor or actor not in host.active_seats
            or not item.context.get('currently_resolving')):
        raise GameRuleError('The hand free cast lost its original resolution')
    return item


def _permission(effect, actor):
    instruction = {key: value for key, value in effect.items() if key not in {
        "_options_fingerprint", "_inspected_hand_identities",
    }}
    return BoundedHandCastSpec.from_effect(instruction, actor=actor)


def _require_damage_recipient_hand(host, item, spec):
    owner = spec.hand_owner
    program = host.semantics.get(item.semantic_key)
    try:
        required = damaged_player_hand_cast_runtime_dependencies(program) if program is not None else ()
    except (ValueError, TypeError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    if (not required or not set(required) <= set(program.capability_dependencies)
            or not host.semantic_program_is_current_trusted(program)
            or item.kind != "triggered_ability" or owner not in host.state.players
            or item.context.get("target") != owner or item.context.get("target_kind") != "player"
            or item.context.get("combat") is not True):
        raise GameRuleError("The inspected hand cast lost its original damage recipient")
    return owner


def damaged_player_hand_inspection_owner(host, *, item, actor, effect):
    if not is_damaged_player_hand_cast_effect(effect):
        return None
    if item is None:
        raise GameRuleError("The private hand look requires its resolving stack item")
    _live_item(host, actor, item.ref)
    try:
        spec = _permission(effect, actor)
    except (ValueError, TypeError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    owner = _require_damage_recipient_hand(host, item, spec)
    return owner if owner in host.active_seats else None


def _permits_proposed_spell(spec, effective):
    if spec.schema_version == DAMAGED_PLAYER_HAND_CAST_VERSION:
        return True
    if spec.schema_version == 2:
        types, _, _ = type_parts(effective["type_line"])
        return bool(types.intersection(spec.shared_card_types))
    if spec.schema_version == LEGENDARY_WIZARD_HAND_CAST_VERSION:
        types, _, _ = type_parts(effective["type_line"])
        if types.isdisjoint({"instant", "sorcery"}):
            return False
    return spec.mana_value_limit.permits(public_mana_value(effective))


def _require_announced_x_cast(host, item, spec):
    from .announced_x_damage_hand_cast import is_closed_announced_x_damage_hand_cast_program

    program = host.semantics.get(item.semantic_key) if item is not None else None
    if (item is None or item.kind not in {"spell", "spell_copy"} or len(item.targets) != 1
            or item.x_value is not None and type(item.x_value) is not int
            or spec.mana_value_limit.maximum != (item.x_value or 0)
            or program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_announced_x_damage_hand_cast_program(program)):
        raise GameRuleError("The hand cast must retain the original resolving spell and its announced X")


def _require_announced_x_hand_cast(host, item, spec):
    program = host.semantics.get(item.semantic_key)
    x = item.x_value
    if (program is None or (program.event, program.active_zone) != ("resolve", "stack")
            or item.kind not in {"spell", "spell_copy"} or not host.semantic_program_is_current_trusted(program)
            or not any(has_announced_x_hand_cast_amount(effect) for effect in nested_effects(program.effects))
            or type(x) is not int or x < 0 or spec.mana_value_limit.maximum != x):
        raise GameRuleError("The hand cast ceiling changed from its resolving spell's announced X")


def _option_pairs(host, actor, spec, *, item=None):
    pairs = []
    owner = actor
    if spec.schema_version == ANNOUNCED_X_HAND_CAST_VERSION:
        _require_announced_x_cast(host, item, spec)
    if spec.schema_version == ANNOUNCED_X_HAND_CAST_VERSION:
        _require_announced_x_hand_cast(host, item, spec)
    if spec.schema_version == DAMAGED_PLAYER_HAND_CAST_VERSION:
        owner = _require_damage_recipient_hand(host, item, spec)
        if owner not in host.active_seats:
            return ()
    player = host.state.players[owner]
    for object_id in player.zones['hand']:
        card = host.state.cards[object_id]
        if card.zone != 'hand' or card.owner != owner:
            raise GameRuleError('The hand free cast contains an invalid owned hand object')
        for method, face in offered_cast_methods(host, card):
            if method is not None and (method != "fuse" or owner != actor):
                continue
            result = build_cast_offer(host, actor, card, cast_method=method, face_name=face,
                                      resolving_payment='free', resolving_origin='hand')
            if result.offer is None:
                continue
            view, _record = characteristic_cast_view(host, card, host.card_record(card), method)
            view = prospective_cast_card(host, view, face)
            if not _permits_proposed_spell(spec, host._effective_card_data(view)):
                continue
            offer = result.offer.to_dict(include_fingerprint=False)
            for price in offer['cost_options']:
                canonical = {**price, 'face': offer.get('face'), 'cast_method': method}
                fingerprint = discover_options_fingerprint(({'logical_object_id': card.logical_object_id,
                                                             'price': canonical},))
                public = {**canonical, 'id': f'bounded-hand:{card.ref}:{fingerprint}', 'card': card.ref,
                          'label': f"{card.printed_name} — {price.get('label', offer['label'])}"}
                pairs.append((public, canonical, card))
    return tuple(pairs)


def bounded_hand_cast_options(host, *, item, actor, effect):
    if item is None:
        raise GameRuleError('A hand free cast requires its resolving stack item')
    _live_item(host, actor, item.ref)
    try:
        spec = _permission(effect, actor)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    return tuple(public for public, _, _ in _option_pairs(host, actor, spec, item=item))


def execute_bounded_hand_cast(host, intent):
    item = _live_item(host, intent.actor, intent.stack_ref)
    pairs = _option_pairs(host, intent.actor, intent.spec, item=item)
    if discover_options_fingerprint(tuple(public for public, _, _ in pairs)) != intent.options_fingerprint:
        raise GameRuleError('The hand free cast choices changed before completion')
    if intent.response is None:
        return
    response = thaw_value(intent.response)
    option_id = response.get('cost_option')
    matches = [(option, card) for public, option, card in pairs
               if option_id in (None, public['id']) and response.get('card', card.ref) == card.ref
               and response.get('face') in (None, option['face'])
               and response.get('cast_method') in (None, option['cast_method'])]
    if len(matches) != 1 or response.get('from', 'hand') != 'hand':
        raise GameRuleError('Select one currently issued hand free cast option')
    option, card = matches[0]
    original = card.logical_object_id
    response.update(card=card.ref, cost_option=option['id'], auto_pay=True)
    response['from'] = 'hand'
    for field in ('face', 'cast_method'):
        if option[field] is not None:
            response[field] = option[field]
    commit_resolving_cast(host, actor=intent.actor, response=response, origin='hand',
        payment_id=f'{item.ref}:bounded-hand:{original}', replacement_selections=intent.replacement_selections,
        authorized_hand_incarnation=original if card.owner != intent.actor else None)
    current = host.state.cards[card.object_id]
    if (current.zone != 'stack' or current.controller != intent.actor or current.logical_object_id == original
            or not any(row.kind == 'spell' and row.card_object_id == current.object_id for row in host.state.stack)):
        raise StateInvariantError('The selected hand card created no spell')
