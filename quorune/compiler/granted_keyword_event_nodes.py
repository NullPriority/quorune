"""Grant existing exact keyword event programs through typed ability shells."""
from __future__ import annotations

from dataclasses import replace
from . import regex_working_set as re

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..death_return import DeathReturnSpec
from .conditional_granted_ability_templates import (
    conditional_quoted_ability_handler, conditional_quoted_ability_text,
)
from .continuous_templates import (
    attached_quoted_ability_handler, attached_quoted_ability_text,
    fixed_power_toughness_anthem_handler, fixed_query_quoted_ability_handler, fixed_query_quoted_ability_text,
)
from .keyword_event_effect_nodes import fixed_keyword_event_effect_node, fixed_keyword_event_effect_spec
from .oracle_source_text import without_ability_word_label


GRANTED_KEYWORD_EVENT_CAPABILITY = 'continuous.ability.fixed_keyword_event_grant'
_KEYWORD = re.compile(r'\b(?:Afflict|Annihilator|Firebending|Mobilize|Soulshift) [1-9]\d*\b'
                      r'|\b(?:Ingest|Exploit|Provoke|Persist|Undying)\b', re.I)
_NONHUMAN_UNDYING_ANTHEM = re.compile(
    r'(Other non-Human creatures you control) get \+1/\+1 and have undying\.', re.I,
)


def _evolve_grant_shell(material_line):
    if not material_line.casefold().rstrip(".").endswith("have evolve"):
        return None
    text = without_ability_word_label(material_line)
    if text is None or not re.fullmatch(
        r"(?:Other creatures|Creature tokens) you control have evolve\.?", text, re.I,
    ):
        return None
    quoted = text.rstrip(".")[:-len("evolve")] + '"Evolve".'
    if fixed_query_quoted_ability_text(quoted) != "Evolve":
        return None

    def compile_shell(fragment, capabilities):
        return fixed_query_quoted_ability_handler(quoted, fragment=fragment, fragment_capabilities=capabilities)

    return "Evolve", "evolve", compile_shell


def _grant_shell(material_line, source_name):
    if re.fullmatch(r"Equipped creature has myriad\.?", material_line.strip(), re.I):
        def compile_equipped_myriad(fragment, capabilities):
            return attached_quoted_ability_handler(
                'Equipped creature has "Myriad".', source_name=source_name,
                fragment=fragment, fragment_capabilities=capabilities,
            )
        return "Myriad", "myriad", compile_equipped_myriad

    evolve = _evolve_grant_shell(material_line)
    if evolve is not None:
        return evolve
    if '"' in material_line:
        return None
    matches = tuple(_KEYWORD.finditer(material_line))
    if len(matches) != 1:
        return None
    match = matches[0]
    if re.search(r'\b(?:has|have)\b', material_line[:match.start()], re.I) is None:
        return None
    keyword = match.group()
    spec = (DeathReturnSpec.for_keyword(keyword) if keyword.casefold() in {'persist', 'undying'}
            else fixed_keyword_event_effect_spec(keyword))
    if spec is None:
        return None
    synthetic = material_line[:match.start()] + '"' + keyword + '"' + material_line[match.end():]
    if attached_quoted_ability_text(synthetic, source_name=source_name) == keyword:
        compile_shell = lambda fragment, caps: attached_quoted_ability_handler(synthetic,
            source_name=source_name, fragment=fragment, fragment_capabilities=caps)
    elif fixed_query_quoted_ability_text(synthetic) == keyword:
        compile_shell = lambda fragment, caps: fixed_query_quoted_ability_handler(synthetic,
            fragment=fragment, fragment_capabilities=caps)
    elif conditional_quoted_ability_text(synthetic, source_name=source_name) == keyword:
        compile_shell = lambda fragment, caps: conditional_quoted_ability_handler(synthetic,
            source_name=source_name, fragment=fragment, fragment_capabilities=caps)
    else:
        return None
    return keyword, spec, compile_shell


def _keyword_inner_node(spec, **values):
    if spec == "evolve":
        from .dependency_gate import explicit_capabilities_gate
        from .keyword_nodes import evolve_keyword_node

        gate = explicit_capabilities_gate(("counter.producer.evolve", "counter.producer.fixed_effect"),
            capability_registry=values["capability_registry"], capability_profile=values["capability_profile"])
        return evolve_keyword_node(
            node_id=values["node_id"], line=values["line"], material_line=values["material_line"],
            span=values["span"], mechanics=("evolve",), gate=gate, residual_ids=(),
        )
    if isinstance(spec, DeathReturnSpec):
        from .dependency_gate import explicit_capabilities_gate
        from .keyword_nodes import death_return_keyword_node
        gate = explicit_capabilities_gate((spec.capability_id,),
            capability_registry=values['capability_registry'], capability_profile=values['capability_profile'])
        node = death_return_keyword_node(node_id=values['node_id'], line=values['line'],
            material_line=values['material_line'], span=values['span'], mechanics=(spec.keyword,),
            gate=gate, residual_ids=())
        # These keyword definitions observe every permanent's graveyard arrival.
        return replace(node, event='permanent.graveyard.self') if node is not None else None
    if spec == "myriad":
        from .myriad_nodes import fixed_myriad_keyword_node

        lower, keyword = fixed_myriad_keyword_node, "myriad"
    else:
        lower, keyword = fixed_keyword_event_effect_node, spec.keyword
    node = lower(node_id=values['node_id'], line=values['line'],
        material_line=values['material_line'], span=values['span'], mechanics=(keyword,),
        capability_registry=values['capability_registry'], capability_profile=values['capability_profile'],
        residuals=values['residuals'])
    if node is None:
        return None
    # A granted-only program is discovered through its current grant fragment.
    # Its receiver does not own the printed source's static-component marker.
    return replace(node, runtime_coverage=tuple(value for value in node.runtime_coverage
                                             if value != CURRENT_ABILITY_FRAGMENT_COVERAGE))


def compile_granted_keyword_event_nodes(*, record, face_id, node_id, line, material_line, span,
        source_name, trusted_mechanics, capability_registry, capability_profile, residuals,
        effect_template, trigger_effect_template, material_line_for):
    compound = _NONHUMAN_UNDYING_ANTHEM.fullmatch(material_line)
    anthem = None
    if compound is not None:
        subject = compound[1]
        anthem = fixed_power_toughness_anthem_handler(f"{subject} get +1/+1.")
        if anthem is None:
            return None
        shell = _grant_shell(f"{subject} have undying.", source_name)
    else:
        shell = _grant_shell(material_line, source_name)
    if shell is None:
        return None
    keyword, spec, compile_shell = shell

    def bind_shell(fragment, fragment_capabilities):
        compiled = compile_shell(fragment, fragment_capabilities)
        if compiled is None:
            return None
        _template_id, descriptor, capabilities = compiled
        required = (*capabilities, GRANTED_KEYWORD_EVENT_CAPABILITY)
        if anthem is None:
            return ('continuous-fixed-keyword-event-grant-v1', descriptor, tuple(sorted(set(required))))
        from ..rules.nonhuman_undying_anthem import (
            NONHUMAN_UNDYING_ANTHEM_DEPENDENCIES, NONHUMAN_UNDYING_ANTHEM_TEMPLATE,
        )

        if descriptor['condition'] != anthem[1]['condition']:
            return None
        return (NONHUMAN_UNDYING_ANTHEM_TEMPLATE, (descriptor, anthem[1]),
                tuple(sorted({*required, *NONHUMAN_UNDYING_ANTHEM_DEPENDENCIES})))

    from .attached_granted_ability_nodes import _compile_granted_ability_pair
    pair = _compile_granted_ability_pair(record=record, face_id=face_id, node_id=node_id,
        line=line, material_line=material_line, span=span, quoted=keyword, compile_shell=bind_shell,
        runtime_coverage='fixed_keyword_event_grant',
        dependency_reason='A fixed keyword grant requires its ordinary inner program and current grant owner',
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, residuals=residuals,
        compile_inner=lambda **values: _keyword_inner_node(spec, **values),
        effect_template=effect_template, trigger_effect_template=trigger_effect_template,
        material_line_for=material_line_for)
    if pair is not None and anthem is not None:
        outer, granted = pair
        outer = replace(outer, runtime_coverage=(*outer.runtime_coverage, CURRENT_ABILITY_FRAGMENT_COVERAGE))
        return outer, granted
    return pair
