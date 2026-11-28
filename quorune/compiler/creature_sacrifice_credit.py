"""Compose a selected creature-sacrifice price with an independently closed result."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..characteristic_evaluation import type_parts
from ..rules.casting.optional_creature_payments import (
    sacrifice_credit_cost_schema,
    sacrifice_credit_dependencies,
)
from .ir_model import OracleFaceIR, SourceSpan, append_residual


_TEMPLATE_ID = "optional-creature-sacrifice-credit-v1"
_PRICE_TEXT = (
    "as an additional cost to cast this spell, you may sacrifice any number of creatures. "
    "this spell costs {2} less to cast for each creature sacrificed this way."
)
_CASTING_MECHANIC = "cr-601-casting-spells"


@dataclass(frozen=True, slots=True)
class CreatureSacrificeCreditTemplate:
    template_id: str = _TEMPLATE_ID

    @property
    def cost_schema(self):
        return sacrifice_credit_cost_schema()


def creature_sacrifice_credit_template(text):
    if " ".join(text.split()).casefold() == _PRICE_TEXT:
        return CreatureSacrificeCreditTemplate()
    return None


def closed_sacrifice_credit_spell(program, *, body_is_closed):
    if program.event != "resolve" or program.active_zone != "stack" or not program.ability_id.startswith("spell:"):
        return False
    try:
        required = sacrifice_credit_dependencies(program.cost_schema)
    except (TypeError, ValueError):
        return False
    prefix = _TEMPLATE_ID + "+"
    template = str(program.provenance.get("template_id") or "")
    if not required or not set(required).issubset(program.capability_dependencies) or not template.startswith(prefix):
        return False
    body = replace(
        program, cost_schema=None,
        provenance={**program.provenance, "template_id": template[len(prefix):]},
        coverage=[value for value in program.coverage if value != _CASTING_MECHANIC],
    )
    return body_is_closed(body)


def compile_sacrifice_credit_with_token(
    *, record, face_id, face_name, type_line, keywords, oracle_text, material_rows,
    effect_template, compile_inner, trigger_node, grant_effect_templates,
    trusted_mechanics, capability_registry, capability_profile, residuals,
):
    """Preserve separately source-pinned intrinsic abilities of the created token."""
    if len(material_rows) != 2 or capability_registry is None:
        return None
    price = creature_sacrifice_credit_template(material_rows[0][1])
    line, material, body_span = material_rows[1]
    if price is None or '"' not in material:
        return None
    from .typed_token_ability_nodes import typed_token_ability_nodes

    def compile_token_body(shell, **context):
        # Removing a terminal quoted ability must keep the creation
        # instruction a complete sentence with its own implicit subject.
        if material.endswith('."') and not shell.endswith("."):
            shell += "."
        return effect_template(shell, **context)

    typed = typed_token_ability_nodes(
        record=record, face_id=face_id, node_id=f"{face_id}:cost", line=line,
        material_line=material, span=body_span, source_name=face_name or record.name,
        effect_template=compile_token_body, keywords=keywords,
        printed_card_types=tuple(sorted(type_parts(type_line)[0])), source_attachment_relation=None,
        trusted_mechanics=trusted_mechanics, capability_registry=capability_registry,
        capability_profile=capability_profile, compile_inner=compile_inner,
        trigger_node=trigger_node, grant_effect_templates=grant_effect_templates,
    )
    if typed is None:
        return None
    outer, *children = typed
    if outer.kind != "spell_ability" or not children:
        return None
    cost_span = material_rows[0][2]
    span = SourceSpan(cost_span.start, body_span.end, cost_span.line)
    text = oracle_text[span.start:span.end]
    required = tuple(sorted(set(outer.capability_dependencies) | set(sacrifice_credit_dependencies(price.cost_schema))))
    closure = capability_registry.closure(required, profile=capability_profile)
    if closure.blockers or not closure.trusted:
        marked = (append_residual(
            residuals, kind="dependency_contract", text=text, span=span,
            reason="Selected sacrifice credit and the closed token result require trusted rules dependencies",
            blockers=closure.blockers,
        ),)
    else:
        marked = ()
    outer = replace(
        outer, text=text, span=span, exact=outer.exact and not marked,
        cost=price.cost_schema, template_id=f"{price.template_id}+{outer.template_id}",
        mechanics=tuple(dict.fromkeys((_CASTING_MECHANIC, *outer.mechanics))),
        residual_ids=(*outer.residual_ids, *marked), capability_dependencies=required,
        capability_closure=closure.reachable, capability_profile=closure.profile,
        capability_fingerprint=closure.fingerprint,
    )
    return OracleFaceIR(
        face_id=face_id, face_name=face_name, oracle_text=oracle_text,
        nodes=(outer, *children), residuals=tuple(residuals),
    )
