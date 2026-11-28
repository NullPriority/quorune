"""Close the exile-cast copy and its independently executable granted trigger."""

from dataclasses import replace

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE, canonical_ability_fragments
from ..rules.stack_spell_copy_shapes import permanent_spell_copy_node_dependencies
from ..rules.exile_spell_copy_grant import EXILE_CAST_COPY_CONDITION, copy_cleanup_effect
from ..stack_spell_copy_model import PermanentSpellCopySpec
from . import regex_working_set as re


_QUOTE = "At the beginning of the end step, sacrifice this permanent."
_SHELL = "Whenever you cast a spell from exile, copy it. You may choose new targets for the copy."
_COMPLETE = re.compile(
    re.escape(_SHELL + ' If it\'s a permanent spell, the copy gains haste and "' + _QUOTE + '"')
    + r"(?: \(A copy of a permanent spell becomes a token\.\))?", re.IGNORECASE,
)


def exile_spell_copy_grant_nodes(*, record, face_id, node_id, line, material_line, span, source_name,
                                 keywords, printed_card_types, source_attachment_relation,
                                 trusted_mechanics, capability_registry, capability_profile,
                                 compile_inner, trigger_node, grant_effect_templates, effect_template):
    if capability_registry is None or not _COMPLETE.fullmatch(material_line.replace("’", "'")):
        return None
    from .typed_token_ability_nodes import _outer_nodes, _token_fragments_and_children

    outer_nodes = _outer_nodes(
        node_id=node_id, line=line, shell=_SHELL, span=span, source_name=source_name,
        printed_card_types=printed_card_types, keywords=keywords,
        source_attachment_relation=source_attachment_relation, effect_template=effect_template,
        compile_inner=compile_inner, trigger_node=trigger_node, grant_effect_templates=grant_effect_templates,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile,
    )
    if outer_nodes is None or len(outer_nodes) != 1:
        return None
    parent, = outer_nodes
    if (parent.event != "spell.cast" or parent.event_condition != EXILE_CAST_COPY_CONDITION
            or parent.active_zone != "battlefield" or parent.target_schema is not None or parent.cost is not None):
        return None
    inner = _token_fragments_and_children(
        quoted_abilities=(_QUOTE,), characteristics={"type_line":"Token Creature","keywords":[]},
        oracle_id=record.oracle_id, face_id=face_id, node_id=node_id, span=span,
        compile_inner=compile_inner, trigger_node=trigger_node, grant_effect_templates=grant_effect_templates,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, copy_recipe=True,
    )
    if inner is None:
        return None
    fragments, children, _ = inner
    if len(fragments) != 1 or len(children) != 1 or tuple(children[0].effects) != (copy_cleanup_effect(),):
        return None
    grant, = canonical_ability_fragments(fragments)
    spec = PermanentSpellCopySpec("exile_cast_grant", 2, grant)
    required = permanent_spell_copy_node_dependencies(spec.effects[0], None)
    closure = capability_registry.closure((*parent.capability_dependencies, *required), profile=capability_profile)
    if not required or not closure.trusted or closure.blockers:
        return None
    decorated = replace(parent, effects=spec.effects, template_id="exile-cast-spell-copy-grant-v1",
                        runtime_coverage=tuple(dict.fromkeys((*parent.runtime_coverage, CURRENT_ABILITY_FRAGMENT_COVERAGE))),
                        capability_dependencies=closure.requested, capability_closure=closure.reachable,
                        capability_profile=closure.profile, capability_fingerprint=closure.fingerprint)
    return (decorated, *children)

def typed_token_or_exile_copy_nodes(**arguments):
    from .typed_token_ability_nodes import typed_token_ability_nodes

    return exile_spell_copy_grant_nodes(**arguments) or typed_token_ability_nodes(**arguments)
