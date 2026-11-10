from __future__ import annotations

"""Price one ordered declaration through the ordinary spell-cost pipeline."""

from collections.abc import Mapping
from dataclasses import dataclass

from ...card_programs.admission import program_has_complete_card_program_admission
from ...effect_runtime.hand_inspection import commit_selected_hand_reveal, validate_entry_hand_reveal
from ...replacement.immutable import thaw_value
from ...semantic_runtime.cast_costs import default_cast_cost_component_registry
from ...targets import available_modes
from ..casting_additional_costs import FixedZoneChangeAdditionalCost, fixed_zone_change_additional_cost
from ..modal_selection import canonical_modes
from ..splice import SPLICE_HANDLER, SPLICE_INTERNAL_SELECTION, SPLICE_REQUEST, SPLICE_SELECTION, SpliceSpec
from .model import CastProposalError
from .spell_text import SpellTextBody, compose_cast_text


@dataclass(frozen=True, slots=True)
class SpliceSource:
    card: object
    spec: SpliceSpec
    body: SpellTextBody

    def identity(self, seat):
        return splice_source_identity(self.card, seat)


def splice_source_identity(card, seat):
    return {"object_id": card.object_id, "logical_object_id": card.logical_object_id,
        "ref": card.ref, "player": seat, "oracle_id": card.oracle_id, "name": card.printed_name}


def _printed_splice_source(host, card):
    specs = []
    for program in host.semantics.runtime_handler_programs_for_oracle(
        card.oracle_id, active_zone="hand", event="cast.cost",
    ):
        if not (host.semantic_program_is_current_trusted(program)
            and program_has_complete_card_program_admission(program)):
            continue
        for descriptor in program.handlers:
            if descriptor.get("handler_id") != SPLICE_HANDLER:
                continue
            spec, = default_cast_cost_component_registry().lower(descriptor, None)
            if not set(spec.price_capabilities()).issubset(program.capability_dependencies):
                continue
            specs.append(spec)
    if len(specs) != 1:
        return None
    body = host.semantics.get(f"{card.oracle_id}:spell:front")
    if (body is None or not host.semantic_program_is_current_trusted(body) or body.requires_arbiter
        or body.cost_schema or available_modes(body.target_schema or {}) or not body.effects):
        return None
    return SpliceSource(card, specs[0], SpellTextBody(body.effects, body.target_schema))


def available_splice_sources(host, seat, spell, program, *, type_line=None):
    if program is None or program.requires_arbiter or not host.semantic_program_is_current_trusted(program):
        return {}
    characteristics = type_line if type_line is not None else host._effective_card_data(spell).get("type_line", "")
    types, subtypes, _ = host._type_parts(str(characteristics))
    if not {"instant", "sorcery"}.intersection(types):
        return {}
    sources = {}
    for object_id in host.state.players[seat].zones["hand"]:
        card = host.state.cards[object_id]
        if card.object_id == spell.object_id or card.zone != "hand" or card.owner != seat:
            continue
        source = _printed_splice_source(host, card)
        if source is not None and source.spec.matches(types, subtypes):
            sources[card.ref] = source
    return sources


def splice_offer_fields(host, seat, card, program, *, type_line, enabled):
    if not enabled:
        return {}
    sources = available_splice_sources(host, seat, card, program, type_line=type_line)
    if not sources:
        return {}
    return {"splice_candidates": list(sources), "choice_schema": {
        SPLICE_REQUEST: {"type": "boolean", "label": "Choose cards to splice", "required": False},
    }}


def _splice_nonmana_prices(option, mandatory, sources):
    prices = [source.spec.nonmana_price for source in sources if source.spec.nonmana_price is not None]
    selected = option.get("_selected_additional_costs")
    existing = (*(mandatory if selected is None else selected), *option.get("_additional_option_costs", ()))
    fields = [price.choice_field for descriptor in existing
        if (price := fixed_zone_change_additional_cost(descriptor)) is not None]
    fields.extend(price.choice_field for price in prices if isinstance(price, FixedZoneChangeAdditionalCost))
    if len(fields) != len(set(fields)):
        return None
    return [*option.get("_additional_option_costs", ()), *(price.to_descriptor() for price in prices)]


def apply_splice_declaration(host, seat, card, program, option, mandatory, submission, *, type_line, hint):
    pinned = SPLICE_INTERNAL_SELECTION in submission
    if (not pinned and not hint) or (pinned and SPLICE_SELECTION in submission):
        return None
    declared = submission.get(SPLICE_INTERNAL_SELECTION if pinned else SPLICE_SELECTION)
    if not isinstance(declared, (list, tuple)) or not declared:
        return None
    if pinned and not all(isinstance(identity, Mapping) for identity in declared):
        return None
    refs = tuple(identity.get("ref") for identity in declared) if pinned else declared
    if any(type(ref) is not str or not ref for ref in refs) or len(set(refs)) != len(refs):
        return None
    candidates = available_splice_sources(host, seat, card, program, type_line=type_line)
    if any(ref not in candidates for ref in refs):
        return None
    sources = tuple(candidates[ref] for ref in refs)
    identities = [source.identity(seat) for source in sources]
    if pinned and list(declared) != identities:
        return None
    nonmana = _splice_nonmana_prices(option, mandatory, sources)
    if nonmana is None:
        return None
    schema = option.get("target_schema", program.target_schema)
    try:
        modes = canonical_modes(schema or {}, submission.get("modes") or (), require_modes=True)
        if modes and not available_modes(schema or {}):
            return None
        if "effects" in option and available_modes(schema or {}):
            schema = {**schema, "modes": {
                mode: {key: value for key, value in definition.items() if key != "effects"}
                for mode, definition in schema["modes"].items()
            }}
        main = SpellTextBody(tuple(option.get("effects", program.effects)), schema, modes)
        composed = compose_cast_text((main, *(source.body for source in sources)))
    except (TypeError, ValueError):
        return None
    requirements = host._mana_vector(option["requirements"])
    for source in sources:
        if source.spec.mana is not None:
            for symbol, count in source.spec.mana.items():
                requirements[symbol] += count
    return {**option, **composed.seal(), "requirements": requirements, "_additional_option_costs": nonmana,
        "target_schema": thaw_value(composed.target_schema) or {"groups": []}, "splice_modes": list(modes),
        SPLICE_SELECTION: identities}


def announce_splice_sources(host, proposal, source, selected_option) -> tuple[dict[str, str], ...]:
    """Reveal the validated selections and return their public card identities."""
    selected = selected_option.get(SPLICE_SELECTION)
    if selected is None:
        return ()
    if not isinstance(selected, (list, tuple)) or not selected or any(
        not isinstance(identity, Mapping) or identity.get("player") != proposal.seat for identity in selected
    ):
        raise CastProposalError("Splice announcement is malformed", reason="stale_splice_source")
    cards = tuple(validate_entry_hand_reveal(host, identity) for identity in selected)
    ids = {card.object_id for card in cards}
    if len(ids) != len(cards) or source.object_id in ids:
        raise CastProposalError("Splice source is no longer distinct from the spell", reason="stale_splice_source")
    for identity in selected:
        commit_selected_hand_reveal(host, identity, source_ref=source.ref,
            summary="A card was revealed for Splice.")
    return tuple({"id": card.ref, "name": card.printed_name} for card in cards)
