"""Source-spanned live grants of differently named battlefield creature abilities."""

from . import regex_working_set as re
from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..rules.activation.battlefield_grants import (
    BATTLEFIELD_CREATURE_ACTIVATION_GRANT_CAPABILITY, battlefield_activation_grant_descriptor,
)
from ..rules.source_references import SourceReferenceSpec
from .static_runtime_nodes import runtime_handler_node


def battlefield_activation_grant_node(*, source_name, node_id, line, material_line, span,
                                      capability_registry, capability_profile, residuals):
    if "has all activated abilities of creatures you control" not in material_line.casefold():
        return None
    source = SourceReferenceSpec(source_name)
    pattern = (rf"(?:{source.regex_pattern}|this creature) has all activated abilities of creatures you control "
               r"that don['’]t have the same name as this creature\.")
    if re.fullmatch(pattern, material_line, re.I) is None:
        return None
    return runtime_handler_node(node_id=node_id, line=line, span=span,
        compiled=('different-name-battlefield-creature-activation-grant-v1', battlefield_activation_grant_descriptor(),
                  BATTLEFIELD_CREATURE_ACTIVATION_GRANT_CAPABILITY),
        kind='static_ability', event='characteristics.evaluate', runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,),
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
        dependency_reason='Live creature ability grants require bounded Layer 6 dependency and rebinding authority.')
