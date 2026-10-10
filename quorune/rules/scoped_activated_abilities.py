from __future__ import annotations

"""Read-only off-battlefield parent applicability for pinned activation catalogs."""

from ..ability_fragments import StaticComponentScopeSpec
from ..semantic_runtime.ability_fragments import fragments_from_descriptors
from ..card_program_faces import program_matches_face
from .source_speed_designations import current_source_designations


def nonbattlefield_program_is_applicable(host,card,program):
    if card.zone=='battlefield':return True
    record=host.card_record(card)
    for parent in host.semantics.runtime_handler_programs_for_oracle(card.oracle_id,active_zone='all',event='characteristics.evaluate'):
        if not host.semantic_program_is_current_trusted(parent) or not program_matches_face(record,parent,card):continue
        for scope in fragments_from_descriptors(parent.handlers):
            if isinstance(scope,StaticComponentScopeSpec) and program.key in scope.child_semantic_keys and scope.applicability is not None:
                if not scope.applicability.applies(current_source_designations(host.state,card)):return False
    return True
