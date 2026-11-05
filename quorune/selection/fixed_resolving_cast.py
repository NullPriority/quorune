from __future__ import annotations

"""Shared immutable fixed-cost options for rules-authored resolving casts."""

import copy
from typing import Any, Mapping

from ..cast_timing import type_line_has_card_type
from ..rules.casting.model import CastProposalError
from ..rules.casting.proposal import _aura_spell_target_schema, _static_cast_rule_state
from ..rules.casting.face_queries import prospective_cast_card
from ..target_source import spell_targeting_source

PUBLIC_COST_OPTION_FIELDS = frozenset({'id', 'kind', 'requirements', 'choice_schema', 'label'})
_PUBLIC_COST_OPTION_FIELDS = PUBLIC_COST_OPTION_FIELDS


def fixed_resolving_cast_options(host, *, actor, card, cost_option, option_id):
    if cost_option.get('id') != option_id:
        raise CastProposalError('The resolving-cast alternative base changed')
    record = host.card_record(card)
    if record is None:
        return ()
    result: list[dict[str, Any]] = []
    for face in host._cast_faces(record, required_face=None):
        face_name = str(face.get('name') or '') if face else None
        type_line = str(face.get('type_line') or '') if face else record.type_line
        if type_line_has_card_type(type_line, 'land'):
            continue
        prohibition, _timing = _static_cast_rule_state(
            host, actor, prospective_cast_card(host, card, face_name),
            face=face, face_name=face_name, face_down=False,
        )
        if prohibition is not None:
            continue
        semantic_key = f"{record.oracle_id}:spell:{face_name or 'front'}"
        program = host.semantics.get(semantic_key)
        is_instant_or_sorcery = any((type_line_has_card_type(type_line, card_type) for card_type in ('instant', 'sorcery')))
        if program is not None and (not host.semantic_program_is_current_trusted(program)):
            continue
        if program is None and (is_instant_or_sorcery or not host._trusted_generic_spell(record)):
            continue
        try:
            aura_target_schema = _aura_spell_target_schema(type_line=type_line, enchant_spec=host._compiled_enchant_spec(card, face_name=face_name), reviewed_target_schema=getattr(program, 'target_schema', None))
        except CastProposalError:
            continue
        options = host._cast_cost_options(actor, card, program, hint=True, alternative_base=cost_option, cast_type_line=type_line, suppress_source_costs=False)
        for option in options:
            # The canonical cost owner starts solely from alternative_base.
            # Retain its additional-cost branches, including optional Squad.
            target_specification = dict(option['target_schema']) if isinstance(option.get('target_schema'), Mapping) else dict(aura_target_schema) if aura_target_schema is not None else program.target_schema if program is not None else None
            public_target_schema = None
            if target_specification is not None:
                public_target_schema = host._public_target_schema(actor, target_specification, acting_source=spell_targeting_source(host, prospective_cast_card(host, card, face_name)), source_ref=card.ref)
                if public_target_schema is None:
                    continue
            public_option = {key: copy.deepcopy(value) for key, value in option.items() if key in _PUBLIC_COST_OPTION_FIELDS}
            if face_name is not None:
                public_option['face'] = face_name
            if public_target_schema is not None:
                public_option['target_schema'] = public_target_schema
            result.append(public_option)
    return tuple(result)
