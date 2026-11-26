"""Revalidate and pay for an ability granted by an exact Equipment instance."""

from collections.abc import Mapping

from ...attachments import detach_object
from ...errors import GameRuleError
from ...granted_activation_model import (
    GRANTING_ATTACHMENT_PAYMENT_CAPABILITY, GRANTING_ATTACHMENT_PAYMENT_FIELD,
    GRANTING_ATTACHMENT_REFLEXIVE_CAPABILITY,
    GrantedActivatedAbilitySpec, GrantingAttachmentPayment, GrantingAttachmentPaymentKind,
)
from ...zone_trigger_events import ZoneTransitionKind
from ..activation_zone_change_costs import counted_activation_replacement_selections


def _payment_fragments(value):
    if isinstance(value, Mapping):
        fragment = value.get('value')
        kind = value.get('kind')
        if (kind in ('granted_activated', 'granted_triggered') and isinstance(fragment, Mapping)
                and GRANTING_ATTACHMENT_PAYMENT_FIELD in fragment):
            yield kind, fragment
        for child in value.values():
            yield from _payment_fragments(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _payment_fragments(child)


def granting_attachment_runtime_dependencies(program):
    """Close both the pinned Equipment grant and its separate activation body."""
    cost = program.cost_schema
    child = isinstance(cost, Mapping) and GRANTING_ATTACHMENT_PAYMENT_FIELD in cost
    if child:
        payment = GrantingAttachmentPayment.from_dict(cost[GRANTING_ATTACHMENT_PAYMENT_FIELD])
        if (payment.granting_object is not None or program.event != 'activate' or program.active_zone != 'battlefield'
                or program.provenance.get('granted_only') is not True or cost.get('tap_source') is not True
                or any(cost.get(field) for field in ('complex_symbols', 'uncompiled_costs', 'choices', 'untap_source',
                                                    'sacrifice_source', 'discard_source', 'exile_source', 'life_payment',
                                                    'energy_payment', 'source_counter_removal_cost', 'mana_cost_options'))):
            raise ValueError('Granting Equipment payment requires one exact granted activation body')
    grant_capabilities = set()
    for handler in program.handlers:
        fragments = tuple(_payment_fragments(handler))
        if not fragments:
            continue
        from ...semantic_runtime.attached_continuous import ATTACHED_FIXED_CHARACTERISTICS_HANDLER_ID

        modifier = handler.get('modifier')
        approved = tuple(_payment_fragments(modifier.get('add_ability_fragments', ()))) if isinstance(modifier, Mapping) else ()
        if (handler.get('handler_id') != ATTACHED_FIXED_CHARACTERISTICS_HANDLER_ID
                or program.event != 'characteristics.evaluate' or program.active_zone != 'battlefield'
                or fragments != approved):
            raise ValueError('Granting Equipment payment requires an attached characteristic grant')
        from ...ability_fragments import GrantedTriggeredAbilitySpec
        for kind, fragment in fragments:
            if kind == 'granted_activated':
                spec = GrantedActivatedAbilitySpec.from_dict(fragment)
                capability = GRANTING_ATTACHMENT_PAYMENT_CAPABILITY
            else:
                spec = GrantedTriggeredAbilitySpec.from_dict(fragment)
                capability = GRANTING_ATTACHMENT_REFLEXIVE_CAPABILITY
            if spec.granting_attachment_payment.granting_object is not None:
                raise ValueError('An attachment grant definition cannot preselect its Equipment')
            grant_capabilities.add(capability)
    if any(_payment_fragments(program.effects)):
        raise ValueError('Granting Equipment payment is unsupported in resolution-time fragments')
    if child:
        grant_capabilities.add(GRANTING_ATTACHMENT_PAYMENT_CAPABILITY)
    return tuple(sorted(grant_capabilities))


def payable_granting_attachment(host, actor, source, payment: GrantingAttachmentPayment):
    identity = payment.granting_object
    equipment = host.state.cards.get(identity.object_id) if identity is not None else None
    if (equipment is None or equipment.zone != 'battlefield' or equipment.phased_out
            or equipment.logical_object_id != identity.logical_object_id
            or equipment.attached_to != source.object_id or equipment.object_id not in source.attachments):
        return None
    if payment.kind is GrantingAttachmentPaymentKind.SACRIFICE:
        from ...sacrifice_prohibition_model import sacrifice_is_prohibited

        if equipment.controller != actor or sacrifice_is_prohibited(host.state, equipment):
            return None
    _types, subtypes, _supertypes = host._type_parts(str(host._effective_card_data(equipment).get('type_line') or ''))
    return equipment if 'equipment' in subtypes else None


def pay_granting_attachment(host, *, actor, source, payment, response):
    equipment = payable_granting_attachment(host, actor, source, payment)
    if equipment is None:
        raise GameRuleError('The granting Equipment payment is no longer payable')
    if payment.kind is GrantingAttachmentPaymentKind.SACRIFICE:
        selections = counted_activation_replacement_selections(response, (equipment.ref,))
        host._move_cards_simultaneously(
            ((equipment.object_id, 'graveyard'),), reason='activated ability cost',
            replacement_selections=selections,
            transition_kinds={equipment.object_id: ZoneTransitionKind.SACRIFICE},
        )
    else:
        from ...unattachment_triggers import dispatch_unattachment, prepare_equipment_unattachment
        former = prepare_equipment_unattachment(host, equipment)
        detach_object(host.state.cards, equipment, players=host.state.players)
        host._log(actor, 'attachment.unattach', f'{equipment.ref} became unattached.',
                  {'attachment': equipment.ref, 'reason': 'activated ability cost'},
                  importance=2, changed_objects=[equipment.object_id, source.object_id])
        dispatch_unattachment(host, former)
    return equipment.object_id
