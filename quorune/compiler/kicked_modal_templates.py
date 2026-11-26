"""Compile the complete any-number-when-kicked modal instruction."""

from dataclasses import dataclass
import hashlib

from ..kicked_modal import KICKED_MODAL_FIELD, KickedModalSpec
from ..util import stable_json
from .modal_templates import FixedNonrepeatingModalTemplate, fixed_nonrepeating_modal_template


@dataclass(frozen=True, slots=True)
class FixedKickedModalTemplate:
    body: FixedNonrepeatingModalTemplate
    policy: KickedModalSpec

    @property
    def cost_schema(self):
        return {KICKED_MODAL_FIELD: self.policy.to_dict()}

    def compiled(self):
        template, effects, schema, mechanics = self.body.compiled()
        digest = hashlib.sha256(stable_json({"body": template, "policy": self.policy.to_dict()}).encode()).hexdigest()[:16]
        return f"fixed-kicked-modal-{digest}-v1", effects, schema, mechanics


def _kicked_modal_branch(text, compile_effect):
    if text.casefold() == "target player gains x life, where x is the greatest power among creatures they control.":
        from ..rules.greatest_power_life import GREATEST_POWER_LIFE_SCHEMA, greatest_power_life_effect

        return ("target-player-greatest-power-life-v1", (greatest_power_life_effect(),),
                GREATEST_POWER_LIFE_SCHEMA, ("cr-115-targets", "cr-119-life"))
    return compile_effect(text)


def fixed_kicked_modal_template(material_rows, *, compile_effect):
    if (not 3 <= len(material_rows) <= 6
            or material_rows[0][1].casefold() != "choose one. if this spell was kicked, choose any number instead."):
        return None
    first = material_rows[0]
    ordinary = ((first[0], "Choose one —", first[2]), *material_rows[1:])
    body = fixed_nonrepeating_modal_template(
        ordinary, compile_effect=lambda text: _kicked_modal_branch(text, compile_effect),
    )
    if body is None or body.context_prefix:
        return None
    policy = KickedModalSpec(tuple(body.target_schema["modes"]))
    policy.validate_schema(body.target_schema)
    return FixedKickedModalTemplate(body, policy)
