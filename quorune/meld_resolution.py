"""Resolve the original no-target meld and preserve actual exile incarnations."""


from .continuous_effect_model import ContinuousObjectIdentity
from .errors import GameRuleError
from .meld_entry import commit_meld_entry, prepare_meld_entry
from .meld_model import MELD_PAIR_FIELD, MeldPairSpec, _name_key, meld_pair_effects, validate_meld_pair_instruction
from .replacement.immutable import FrozenMap
from .rules.meld_pair_shapes import meld_pair_runtime_dependencies


MELD_EXILE_COMMIT = "meld_exile_commit"


def _require_meld_program(host, item, actor, spec):
    program = host.semantics.get(item.semantic_key) if item is not None else None
    if (item is None or item.controller != actor or item.kind not in {"activated_ability", "activated_ability_copy"}
            or item.targets or item.context.get("currently_resolving") is not True or program is None
            or not host.semantic_program_is_current_trusted(program) or not meld_pair_runtime_dependencies(program)
            or tuple(map(FrozenMap, program.effects)) != tuple(map(FrozenMap, meld_pair_effects(spec)))):
        raise GameRuleError("Meld needs its original resolving activation")


def _current_source(host, item, actor):
    source = host.state.cards.get(item.source_object_id)
    if (source is None or source.logical_object_id != item.context.get("source_logical_object_id")
            or source.zone != "battlefield" or source.owner != actor or source.controller != actor or source.phased_out):
        return None
    return source


def _eligible_partner(host, card, actor, spec):
    if (card.zone != "battlefield" or card.owner != actor or card.controller != actor or card.phased_out):
        return False
    data = host._effective_card_data(card)
    return (str(data.get("name") or "") and _name_key(str(data["name"])) == _name_key(spec.partner_name)
            and "creature" in host._type_parts(str(data.get("type_line") or ""))[0])


def meld_partner_candidate_refs(host, item, actor, effect):
    spec = MeldPairSpec.from_dict(effect[MELD_PAIR_FIELD])
    _require_meld_program(host, item, actor, spec)
    source = _current_source(host, item, actor)
    if source is None:
        return ()
    return tuple(sorted(card.ref for card in host.state.cards.values()
                        if card.object_id != source.object_id and _eligible_partner(host, card, actor, spec)))


def _commit_pair_exile(host, item, effect, *, actor, spec, reason):
    from .zone_transitions import ZoneTransitionOwner

    if MELD_EXILE_COMMIT in item.context:
        raise GameRuleError("Meld already committed its original exile instruction")
    source = _current_source(host, item, actor)
    selected = ContinuousObjectIdentity(**effect["partner"])
    partner = host.state.cards.get(selected.object_id)
    if (source is None or partner is None or source.object_id == partner.object_id
            or partner.logical_object_id != selected.logical_object_id or not _eligible_partner(host, partner, actor, spec)):
        return ()
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        ((source.object_id, "exile"), (partner.object_id, "exile")), reason=reason,
        replacement_selections=tuple(effect.get("_replacement_selections") or ()),
    )
    originals = (source.object_id, partner.object_id)
    exiled = {card.object_id: card for card in moved if card.zone == "exile" and card.is_card_object and not card.is_token}
    if len(moved) == 2 and set(exiled) == set(originals):
        item.context[MELD_EXILE_COMMIT] = [
            {"object_id": part, "logical_object_id": exiled[part].logical_object_id} for part in originals
        ]
    return tuple(card.ref for card in moved)


def _find_original_meld_pair(host, committed, actor, spec):
    parts = []
    for raw, name in zip(committed, (spec.source_name, spec.partner_name), strict=True):
        identity = ContinuousObjectIdentity(**raw)
        record = host.card_db.lookup(name, fuzzy=False)
        card = host.state.cards.get(identity.object_id)
        if (card is None or card.owner != actor or card.logical_object_id != identity.logical_object_id
                or card.zone != "exile" or card.is_token or not card.is_card_object or card.face_down
                or record.layout != "meld" or card.oracle_id != record.oracle_id):
            return ()
        parts.append(card)
    return tuple(parts)


def resolve_meld_instruction(host, item, effect, *, actor, reason):
    from .zone_transitions import ZoneTransitionOwner

    instruction = {key: value for key, value in effect.items() if key != "_runtime_source"}
    spec = validate_meld_pair_instruction(instruction)
    _require_meld_program(host, item, actor, spec)
    if effect["phase"] == "exile":
        return _commit_pair_exile(host, item, effect, actor=actor, spec=spec, reason=reason)
    committed = item.context.get(MELD_EXILE_COMMIT)
    if committed is None:
        return ()
    parts = _find_original_meld_pair(host, committed, actor, spec)
    if not parts:
        return ()
    result = host.card_db.lookup(spec.result_name, fuzzy=False)
    entry = prepare_meld_entry(host, *parts, result, actor=actor)
    return commit_meld_entry(ZoneTransitionOwner(host), entry, reason=reason,
                             replacement_selections=tuple(effect.get("_replacement_selections") or ()))
