"""Lower one source-spanned Gift extra turn and promised protection spell."""

from ..characteristic_evaluation import type_parts
from ..gift_card_model import GIFT_EXTRA_TURN_COST_FIELD
from ..rules.gift_extra_turn_spells import (
    DEPENDENCIES, GIFT_EXTRA_TURN_PRICE, GIFT_EXTRA_TURN_TEMPLATE,
    flying_bird_creation_effect, gift_extra_turn_protection_effects,
)
from ..rules.spell_self_exile import SELF_EXILE
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleFaceIR, OracleNode, SourceSpan, append_residual
from .spell_self_exile import terminal_self_exile_body
from .token_templates import fixed_token_creation_effect_template


_PROMISED_PROTECTION = (
    "if the gift was promised, all permanents you control phase out, and until your next turn, "
    "your life total can't change and you gain protection from everything."
)


def _matches_gift_protection_text(material_rows, source_name):
    gift, results, terminal = (row[1] for row in material_rows)
    if " ".join(gift.casefold().split()).removesuffix(".") != "gift an extra turn":
        return False
    tokens, separator, condition = results.partition(". ")
    if not separator or " ".join(condition.casefold().replace("’", "'").split()) != _PROMISED_PROTECTION:
        return False
    token_template = fixed_token_creation_effect_template(tokens + ".")
    if (token_template is None or token_template.target_schema is not None
            or token_template.effect != flying_bird_creation_effect()):
        return False
    return terminal_self_exile_body(results + " " + terminal, source_name=source_name) is not None


def compile_gift_extra_turn_face(*, record, face_id, face_name, oracle_text, material_rows,
                                capability_registry, capability_profile, residuals):
    types, _, _ = type_parts(record.type_line)
    if record.layout != "normal" or record.faces or not {"instant", "sorcery"}.intersection(types):
        return None
    if len(material_rows) != 3 or not _matches_gift_protection_text(material_rows, face_name):
        return None
    gate = explicit_capabilities_gate(
        DEPENDENCIES, capability_registry=capability_registry, capability_profile=capability_profile,
    )
    first, _, last = material_rows
    span = SourceSpan(first[2].start, last[2].end, first[2].line)
    blocked = ((append_residual(
        residuals, kind="dependency_contract", text=oracle_text, span=span,
        reason="Gift an extra turn requires trusted casting, turn, token and protection owners", blockers=gate.blockers,
    ),) if gate.blockers else ())
    node = OracleNode(
        node_id=f"{face_id}:n1", kind="spell_ability", text=oracle_text, span=span,
        active_zone="stack", event="resolve", lowerable=True, exact=not blocked,
        template_id=GIFT_EXTRA_TURN_TEMPLATE, effects=gift_extra_turn_protection_effects(),
        mechanics=(SELF_EXILE.mechanic,),
        cost={GIFT_EXTRA_TURN_COST_FIELD: dict(GIFT_EXTRA_TURN_PRICE)},
        residual_ids=blocked, capability_dependencies=gate.capabilities,
        capability_closure=gate.closure.reachable if gate.closure else (),
        capability_profile=gate.closure.profile if gate.closure else None,
        capability_fingerprint=gate.closure.fingerprint if gate.closure else None,
    )
    return OracleFaceIR(face_id=face_id, face_name=face_name, oracle_text=oracle_text, nodes=(node,), residuals=tuple(residuals))
