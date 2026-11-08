from __future__ import annotations

"""Lower triggered mana before ordinary stack-trigger composition."""

import re
from collections import Counter

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..fixed_mana_abilities import FixedManaMode
from ..triggered_mana_model import (
    FIXED_TRIGGERED_MANA_EVENT,
    FIXED_TRIGGERED_MANA_FIELD,
    MANA_LAND_SUBTYPES,
    FixedTriggeredManaSpec,
    TriggeredManaChoice,
)
from .dependency_gate import explicit_capabilities_gate
from .ir_model import OracleNode, append_residual


_SUBJECT = r"permanent|land|creature|plains|island|swamp|mountain|forest"
_OUTPUT = r"(?:an additional )?(?P<output>(?:\{[WUBRGC]\})+)\."
_OWN_TAP = re.compile(
    rf"(?:When|Whenever) you tap (?:a|an) (?P<subject>{_SUBJECT}) for (?:mana|\{{(?P<color>[WUBRGC])\}}), add {_OUTPUT}", re.I,
)
_PASSIVE_TAP = re.compile(
    rf"(?:When|Whenever) (?:(?P<attached>enchanted land)|(?:a|an) (?P<subject>{_SUBJECT})) "
    rf"is tapped for mana, its controller adds {_OUTPUT}", re.I,
)
_ANY_PLAYER_TAP = re.compile(
    rf"(?:When|Whenever) a player taps (?:a|an) (?P<subject>{_SUBJECT}) for mana, that player adds {_OUTPUT}", re.I,
)
_LAND_TYPE = "(?:" + "|".join(sorted(MANA_LAND_SUBTYPES)) + ")"
_COPIED_OUTPUT = re.compile(
    rf"(?:When|Whenever) (?P<actor>you tap|a player taps) (?:a|an) "
    rf"(?P<lands>basic land|land|{_LAND_TYPE}(?:, {_LAND_TYPE})*(?:,? or {_LAND_TYPE})?) for mana, "
    r"(?P<recipient>add|that player adds) one mana of any type that land produced\.", re.I,
)
_COLOR_OUTPUT = r"(?:an additional )?(?P<amount>one mana of any color|two mana in any combination of colors)\."
_ATTACHED_COLOR = re.compile(
    rf"(?:When|Whenever) enchanted land is tapped for mana, its controller adds {_COLOR_OUTPUT}", re.I,
)
_MONARCH_COLOR = re.compile(
    rf"(?:When|Whenever) you tap a land for mana while you['’]re the monarch, add {_COLOR_OUTPUT}", re.I,
)


def _fixed_spec(material_line):
    match = _OWN_TAP.fullmatch(material_line)
    if match is not None:
        scope, subject, color = "controller", match["subject"].casefold(), match["color"]
    elif match := _PASSIVE_TAP.fullmatch(material_line):
        scope = "attached" if match["attached"] else "any"
        subject, color = (match["subject"] or "land").casefold(), None
    elif match := _ANY_PLAYER_TAP.fullmatch(material_line):
        scope, subject, color = "any", match["subject"].casefold(), None
    else:
        return None
    output = FixedManaMode.from_bundle(Counter(re.findall(r"\{([WUBRGC])\}", match["output"].upper())))
    return FixedTriggeredManaSpec(scope, subject, output, color.upper() if color else None)


def _choice_spec(material_line):
    copied = _COPIED_OUTPUT.fullmatch(material_line)
    if copied is not None:
        controlled = copied["actor"].casefold() == "you tap"
        if controlled != (copied["recipient"].casefold() == "add"):
            return None
        lands = copied["lands"].casefold()
        subtypes = tuple(sorted(set(re.findall(_LAND_TYPE, lands))))
        return FixedTriggeredManaSpec("controller" if controlled else "any", "land", TriggeredManaChoice("produced_type", 1),
                                     basic_only=lands == "basic land", subtypes_any=subtypes)
    monarch = _MONARCH_COLOR.fullmatch(material_line)
    color = monarch or _ATTACHED_COLOR.fullmatch(material_line)
    if color is None:
        return None
    amount = 2 if color["amount"].casefold().startswith("two ") else 1
    return FixedTriggeredManaSpec("controller" if monarch else "attached", "land", TriggeredManaChoice("any_color", amount),
                                 requires_monarch=monarch is not None)


def fixed_triggered_mana_node(*, node_id, line, material_line, span, capability_registry, capability_profile, residuals):
    spec = _fixed_spec(material_line) or _choice_spec(material_line)
    if spec is None:
        return None
    gate = explicit_capabilities_gate((spec.capability,), capability_registry=capability_registry,
                                     capability_profile=capability_profile)
    residual_ids = (append_residual(residuals, kind="dependency_contract", text=line, span=span,
        reason="Fixed triggered mana requires its production and funding owner", blockers=gate.blockers),) if gate.blockers else ()
    closure = gate.closure
    return OracleNode(
        node_id=node_id, kind="triggered_ability", text=line, span=span, active_zone="battlefield",
        event=FIXED_TRIGGERED_MANA_EVENT, event_condition={FIXED_TRIGGERED_MANA_FIELD: spec.to_dict()},
        lowerable=True, exact=not residual_ids, template_id=("chosen-triggered-mana-v2" if isinstance(spec.output, TriggeredManaChoice)
                                                           else "fixed-triggered-mana-v1"),
        runtime_coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,), residual_ids=residual_ids,
        capability_dependencies=gate.capabilities, capability_closure=closure.reachable if closure else (),
        capability_profile=closure.profile if closure else None, capability_fingerprint=closure.fingerprint if closure else None,
    )
