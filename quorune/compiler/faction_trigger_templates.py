"""Compile only the printed Brotherhood and Enclave occurrence clauses."""

from ..rules.faction_trigger_model import faction_branch_capabilities, faction_condition, faction_effects, faction_event
from .ir_model import OracleNode, append_residual
from .dependency_gate import explicit_capabilities_gate


_BROTHERHOOD = (
    "• brotherhood — at the beginning of your upkeep, each opponent draws a card. "
    "you draw a card for each card drawn this way."
)
_ENCLAVE = (
    "• enclave — whenever a player attacks you with one or more creatures, "
    "that player gets twice that many rad counters."
)
_BRANCH = {_BROTHERHOOD: "Brotherhood", _ENCLAVE: "Enclave"}

_ENTRY = "as this enchantment enters, choose brotherhood or enclave."


def faction_face_ability_compiler(fallback, *, material_rows, card_types):
    """Bind the grants only where the original faction choice is present."""
    if "enchantment" not in card_types or len(material_rows) != 3:
        return fallback
    source = tuple(" ".join(row[0].casefold().split()) for row in material_rows)
    if source != (_ENTRY, _BROTHERHOOD, _ENCLAVE):
        return fallback

    def compile_ability(**fields):
        if " ".join(fields["line"].casefold().split()) in _BRANCH:
            subset = ("node_id", "line", "span", "capability_registry", "capability_profile", "residuals")
            result = faction_trigger_node(**{key: fields[key] for key in subset})
            if result is not None:
                return result
        return fallback(**fields)

    return compile_ability


def faction_trigger_node(*, node_id, line, span, capability_registry, capability_profile, residuals):
    choice = _BRANCH.get(" ".join(line.casefold().split()))
    if choice is None:
        return None
    gate = explicit_capabilities_gate(
        faction_branch_capabilities(choice),
        capability_registry=capability_registry, capability_profile=capability_profile,
    )
    residual = ()
    if gate.blockers:
        residual = (append_residual(
            residuals, kind="dependency_contract", text=line, span=span,
            reason="Faction trigger requires its entry choice and exact occurrence result",
            blockers=gate.blockers,
        ),)
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span,
        active_zone="battlefield", event=faction_event(choice), event_condition=faction_condition(choice),
        lowerable=True, exact=not residual, template_id=f"entry-faction-{choice.casefold()}-v1",
        effects=faction_effects(choice), target_schema=None,
        mechanics=("cr-603-handling-triggered-abilities",),
        residual_ids=residual, capability_dependencies=gate.capabilities,
        capability_closure=closure.reachable if closure is not None else (),
        capability_profile=closure.profile if closure is not None else None,
        capability_fingerprint=closure.fingerprint if closure is not None else None,
    )
