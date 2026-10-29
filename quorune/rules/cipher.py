"""Cipher encoding delegates physical movement to the canonical zone owner."""
from ..cipher_model import CipherEncoding, CIPHER_LINK_FIELD
from ..continuous_effect_model import ContinuousObjectIdentity, Layer
from ..errors import GameRuleError
from ..zone_transitions import ZoneTransitionOwner
from ..cipher_model import CIPHER_GRANT_HANDLER, CIPHER_TRIGGER_KEY, CIPHER_CAPABILITY


def current_cipher_encoding(state, card):
    raw = card.annotations.get(CIPHER_LINK_FIELD)
    if raw is None or card.annotations.get('cipher_link_ended') is True:
        return None
    try:
        link = CipherEncoding.from_dict(raw)
    except (ValueError, TypeError) as exc:
        raise GameRuleError(str(exc)) from exc
    creature = state.cards.get(link.creature.object_id)
    if (card.zone != 'exile' or card.ref != link.card_ref or card.logical_object_id != link.card.logical_object_id
            or creature is None or creature.zone != 'battlefield' or creature.ref != link.creature_ref
            or creature.logical_object_id != link.creature.logical_object_id):
        return None
    return link


def reconcile_cipher_links(host):
    for card in host.state.cards.values():
        raw = card.annotations.get(CIPHER_LINK_FIELD)
        if raw is None or card.zone != 'exile':
            continue
        link = CipherEncoding.from_dict(raw)
        creature = host.state.cards.get(link.creature.object_id)
        if creature is None or creature.zone != 'battlefield' or creature.logical_object_id != link.creature.logical_object_id:
            card.annotations['cipher_link_ended'] = True


def encode_cipher(host, intent):
    card = host.state.cards.get(intent.card.object_id)
    if (card is None or card.zone != 'stack' or card.logical_object_id != intent.card.logical_object_id
            or card.object_kind != 'card'):
        raise GameRuleError('Cipher encoding lost its original physical spell')
    if intent.creature is None:
        return False
    creature = host.state.cards.get(intent.creature.object_id)
    if (creature is None or creature.zone != 'battlefield' or creature.phased_out or creature.controller != intent.actor
            or creature.ref != intent.creature_ref or creature.logical_object_id != intent.creature.logical_object_id):
        raise GameRuleError('Cipher encoding creature is no longer eligible')
    types, _subtypes, _supertypes = host._type_parts(host._effective_card_data(creature, maximum_layer=Layer.TYPE)['type_line'])
    if 'creature' not in types:
        raise GameRuleError('Cipher can be encoded only on a current creature')
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(((card.object_id, 'exile'),),
        reason=intent.reason, replacement_selections=intent.replacement_selections)
    current = next((value for value in moved if value.object_id == card.object_id and value.zone == 'exile'), None)
    if current is None:
        return False
    link = CipherEncoding(ContinuousObjectIdentity(current.object_id, current.logical_object_id), intent.creature,
                          current.ref, creature.ref)
    current.annotations[CIPHER_LINK_FIELD] = link.to_dict()
    host._log(intent.actor, 'cipher.encoded', f'{current.ref} was encoded on {creature.ref}.',
        {'card': current.ref, 'creature': creature.ref}, changed_objects=[current.object_id, creature.object_id])
    return True


def cipher_granted_program(host, creature, granted):
    """Materialize a distinct ordinary program from its trusted exile grant."""
    from dataclasses import replace
    card = next((value for value in host.state.cards.values() if value.ref == granted.cipher_card_ref), None)
    link = current_cipher_encoding(host.state, card) if card is not None else None
    if link is None or link.creature.object_id != creature.object_id or link.creature.logical_object_id != creature.logical_object_id:
        return None
    parent = next((p for p in host.semantics.runtime_handler_programs_for_oracle(card.oracle_id,
        active_zone='exile', event='characteristics.evaluate') if host.semantic_program_is_current_trusted(p)
        and any(d.get('handler_id') == CIPHER_GRANT_HANDLER for d in p.handlers)), None)
    if parent is None:
        return None
    key = CIPHER_TRIGGER_KEY + ':' + card.ref
    program = replace(parent, key=key, label='Cipher', ability_id=granted.ability_id,
        event='damage.dealt.self', active_zone='battlefield', handlers=[], effects=[], target_schema=None,
        event_condition={'all': [{'field': 'combat', 'op': 'truthy', 'value': True},
            {'field': 'target_kind', 'op': 'eq', 'value': 'player'}]},
        provenance={**parent.provenance, 'cipher_grant': link.to_dict(), 'granted_only': True},
        coverage=['cipher_granted_trigger'], capability_dependencies=list(parent.capability_dependencies))
    return program


def cipher_program_is_current_trusted(host, program):
    if not program.provenance.get('cipher_grant'):
        return False
    try:
        link = CipherEncoding.from_dict(program.provenance['cipher_grant'])
    except (ValueError, TypeError):
        return False
    card = host.state.cards.get(link.card.object_id)
    return card is not None and any(host.semantic_program_is_current_trusted(parent)
        and any(d.get('handler_id') == CIPHER_GRANT_HANDLER for d in parent.handlers)
        for parent in host.semantics.runtime_handler_programs_for_oracle(card.oracle_id,
            active_zone='exile', event='characteristics.evaluate'))


def begin_cipher_copy_cast(host, item):
    from ..cipher_model import CIPHER_CAST_PRODUCER
    from ..stack_resolution import complete_stack_resolution
    raw = item.context.get('cipher_grant')
    try:
        link = CipherEncoding.from_dict(raw)
    except (ValueError, TypeError) as exc:
        raise GameRuleError('Cipher trigger lost its encoded-card identity') from exc
    card = host.state.cards.get(link.card.object_id)
    if card is None or card.zone != 'exile' or card.ref != link.card_ref or card.logical_object_id != link.card.logical_object_id:
        complete_stack_resolution(host, item=item, destination=None, prepared_replacement=None)
        host._grant_priority(host.state.active_player)
        return
    # The trigger is already locked to the creature's controller at damage.
    # A creature departure after triggering does not remove the encoded card
    # from the resolving ability's retained reference.
    copied = host.create_card_copy(item.controller, card.ref, zone='exile')
    options = host._one_shot_exile_cast_options(actor=item.controller, card=copied, maximum_mana_value=None)
    if not options:
        host.move_card(copied.object_id, 'outside', reason='Cipher copy has no legal cast', log=False)
        complete_stack_resolution(host, item=item, destination=None, prepared_replacement=None)
        host._grant_priority(host.state.active_player)
        return
    host._begin_one_shot_exile_cast_choice(item=item, card=copied, cleanup_cards=(),
        maximum_mana_value=None, producer=CIPHER_CAST_PRODUCER)
