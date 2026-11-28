"""Compile one grantor-only temporary grant and its source-spanned free activation."""

from ..granted_activation_model import GRANTOR_REDIRECTION_CAPABILITY
from ..rules.grantor_damage_redirection import (
    MECHANIC, QUOTED, grantor_redirect_activation_schema, grantor_redirect_effect,
    grantor_redirect_grant, grantor_redirect_grant_capabilities, grantor_redirect_spell_schema,
)
from ..rules.temporary_ability_grant import TEMPORARY_QUOTED_GRANT_MECHANIC
from .activated_costs import activated_ability_cost, parse_compiled_activation_line
from .attached_granted_ability_nodes import GRANTED_ACTIVATED_ABILITY_KIND, attached_granted_program_ability_id
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, SourceSpan, append_residual
from .temporary_quoted_ability_nodes import _temporary_grant_outer_node

_ORACLE = f'Until end of turn, target creature you control gains "{QUOTED}" Only you may activate this ability.'


def grantor_damage_redirection_nodes(*, record, face_id, node_id, line, material_line, span,
                                    printed_card_types, capability_registry, capability_profile, residuals):
    if not set(printed_card_types).intersection({"instant", "sorcery"}) or not (
        " ".join(material_line.casefold().split()) == _ORACLE.casefold()
    ):
        return None
    first_quote, last_quote = line.index('"'), line.rindex('"')
    quoted = line[first_quote + 1:last_quote]
    child_span = SourceSpan(span.start + first_quote + 1, span.start + last_quote, span.line)
    child_id = node_id + ":granted"
    ability_id = attached_granted_program_ability_id(
        kind=GRANTED_ACTIVATED_ABILITY_KIND, face_id=face_id, line=span.line, node_id=child_id,
    )
    grant = grantor_redirect_grant(ability_id, f"{record.oracle_id}:{ability_id}")
    target = grantor_redirect_spell_schema()
    mechanics = (TEMPORARY_QUOTED_GRANT_MECHANIC, "cr-611-continuous-effects", "cr-115-targets")
    outer = _temporary_grant_outer_node(
        node_id=node_id, line=line, span=span, paid=False, template="grantor-only-temporary-redirection-v1",
        cost=None, effects=(grant,), schema=target, mechanics=mechanics,
        dependencies=grantor_redirect_grant_capabilities(grant, target, None),
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
    abilities, _ = parse_compiled_activation_line(quoted, card_name="Granted creature", keywords=())
    ability, = abilities
    gate = explicit_capabilities_gate((GRANTOR_REDIRECTION_CAPABILITY, "target.revalidate_resolution"),
        capability_registry=capability_registry, capability_profile=capability_profile)
    ids = (append_residual(residuals, kind="dependency_contract", text=quoted, span=child_span,
        reason="Grantor-only redirection requires its free activation authority", blockers=gate.blockers),) if gate.blockers else ()
    child = OracleNode(node_id=child_id, kind=GRANTED_ACTIVATED_ABILITY_KIND, text=quoted, span=child_span,
        active_zone="battlefield", event="activate", lowerable=True, exact=not gate.blockers,
        template_id="grantor-only-finite-redirection-activation-v1", cost=activated_ability_cost(ability),
        effects=(grantor_redirect_effect(),), target_schema=grantor_redirect_activation_schema(), mechanics=(MECHANIC,),
        residual_ids=ids, capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None)
    return outer, child
