"""Compile exact faction choice carriers and their independently typed branches."""

from dataclasses import replace

from ..ability_fragments import ability_fragment_to_dict, TriggerMultiplierSpec
from ..rules.faction_trigger_shapes import faction_attack_multiplier_dependencies
from .static_runtime_nodes import runtime_handler_node
from .trigger_participation_templates import static_trigger_multiplier_handler

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
_MARDU_BODY = (
    "if a creature attacking causes a triggered ability of a permanent you control to trigger, "
    "that ability triggers an additional time."
)
_MARDU = "• mardu — " + _MARDU_BODY
_JESKAI = (
    "• jeskai — at the beginning of your upkeep, create a 1/1 red goblin creature token. "
    "it gains lifelink and haste until end of turn."
)
_BRANCH = {_BROTHERHOOD: "Brotherhood", _ENCLAVE: "Enclave", _JESKAI: "Jeskai"}

_ENTRY = "as this enchantment enters, choose brotherhood or enclave."
_CLAN_ENTRY = "as this enchantment enters, choose mardu or jeskai."


def _clan_label(line):
    subject = " ".join(line.casefold().split()).removeprefix("• ")
    return subject.startswith(("mardu — ", "mardu - ", "jeskai — ", "jeskai - "))


def faction_face_ability_compiler(fallback, *, material_rows, card_types):
    """Bind faction branches only where their whole entry choice is present."""
    source = tuple(" ".join(row[0].casefold().split()) for row in material_rows)
    paired = "enchantment" in card_types and source in (
        (_ENTRY, _BROTHERHOOD, _ENCLAVE), (_CLAN_ENTRY, _MARDU, _JESKAI),
    )
    if not paired and not any(_clan_label(line) for line in source):
        from .jeskai_temur_faction import jeskai_temur_face_compiler

        return jeskai_temur_face_compiler(fallback, source)

    def compile_ability(**fields):
        line = fields["line"]
        normalized = " ".join(line.casefold().split())
        if paired and normalized in {*_BRANCH, _MARDU}:
            subset = ("node_id", "line", "span", "capability_registry", "capability_profile", "residuals")
            result = faction_trigger_node(**{key: fields[key] for key in subset})
            if result is not None:
                return result
        if _clan_label(line):
            residual = append_residual(
                fields["residuals"], kind="faction_branch", text=line, span=fields["span"],
                reason="A faction branch requires its exact entry choice and sibling",
                blockers=("faction entry scope",),
            )
            return OracleNode(
                node_id=fields["node_id"], kind="static_ability", text=line, span=fields["span"],
                active_zone="battlefield", event="unresolved", lowerable=False, exact=False,
                residual_ids=(residual,),
            )
        return fallback(**fields)

    return compile_ability


def faction_trigger_node(*, node_id, line, span, capability_registry, capability_profile, residuals):
    normalized = " ".join(line.casefold().split())
    if normalized == _MARDU:
        return _mardu_trigger_multiplier_node(node_id=node_id, line=line, span=span,
            capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals)
    choice = _BRANCH.get(normalized)
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



def _mardu_trigger_multiplier_node(*, node_id, line, span, capability_registry, capability_profile, residuals):
    compiled = static_trigger_multiplier_handler(_MARDU_BODY)
    if compiled is None:
        return None
    _, descriptor, _ = compiled
    leaf = TriggerMultiplierSpec.from_dict(descriptor["fragment"]["value"])
    guarded = replace(leaf, required_faction="Mardu")
    return runtime_handler_node(
        node_id=node_id, line=line, span=span,
        compiled=("mardu-attack-trigger-multiplier-v1",
                  {**descriptor, "fragment": ability_fragment_to_dict(guarded)},
                  faction_attack_multiplier_dependencies()),
        kind="static_ability", event="continuous",
        dependency_reason="Mardu attack participation requires its original faction choice",
        capability_registry=capability_registry, capability_profile=capability_profile, residuals=residuals,
    )
