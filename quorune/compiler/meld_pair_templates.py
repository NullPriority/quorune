"""Lower one exact owned-and-controlled pair Meld activation."""

from . import regex_working_set as re
from .dependency_gate import DependencyGate, explicit_capabilities_gate
from ..meld_model import MELD_MECHANIC, MELD_TEMPLATE, MeldPairSpec, meld_pair_effects
from ..rules.meld_pair_shapes import meld_pair_activation_node_capabilities


_MELD = re.compile(
    r"If you both own and control this land and a creature named (?P<partner>.+?), "
    r"exile them, then meld them into (?P<result>.+?)\.", re.IGNORECASE,
)


def meld_pair_activation_template(ability, text, *, source_name, public_timing):
    match = _MELD.fullmatch(" ".join(text.split()))
    if (match is None or public_timing is not None or ability.mana_ability or not ability.tap_source
            or not ability.compiled_cost or ability.zones != ("battlefield",) or ability.target_schema is not None):
        return None
    pair = MeldPairSpec.from_names(source_name, match["partner"], match["result"])
    if pair is None:
        return None
    return MELD_TEMPLATE, meld_pair_effects(pair), None, (MELD_MECHANIC,)


def meld_pair_activation_gate(**scope):
    if MELD_MECHANIC not in scope["mechanics"]:
        return None
    required = meld_pair_activation_node_capabilities(scope["effects"], scope["target_schema"], scope["cost_schema"])
    if scope["active_zone"] != "battlefield" or not required:
        return DependencyGate(blockers=(f"mechanic:{MELD_MECHANIC}",))
    return explicit_capabilities_gate(
        required, capability_registry=scope["capability_registry"], capability_profile=scope["capability_profile"],
    )
