"""Compile resolved attack requirements for one represented creature."""
from dataclasses import dataclass
from . import regex_working_set as re
from ..attachment_references import AttachmentReferenceKind, AttachmentReferenceSpec
from ..temporary_attack_requirement_model import TemporaryAttackRequirementSpec


@dataclass(frozen=True, slots=True)
class TemporaryAttackRequirementTemplate:
    schema_version: int = 1

    def compiled(self):
        if self.schema_version == 5:
            from ..rules.temporary_attack_requirements import live_opponent_attack_effect

            return ("live-opponent-attack-requirements-v1", (live_opponent_attack_effect(timed=False),),
                    None, ("cr-611-continuous-effects", "cr-508-declare-attackers-step"))
        spec = TemporaryAttackRequirementSpec(schema_version=self.schema_version)
        target_schema = spec.target_schema()
        mechanics = ("cr-611-continuous-effects", "cr-508-declare-attackers-step")
        if target_schema is None:
            card = AttachmentReferenceSpec(AttachmentReferenceKind.ENCHANTED, "creature", schema_version=2).to_dict()
            template = "temporary-enchanted-creature-attack-this-turn-v1"
        else:
            card = "$target.0"
            mechanics = (*mechanics, "cr-115-targets")
            template = {
                1: "temporary-target-attack-each-combat-this-turn-v1",
                2: "temporary-opponent-creature-attack-this-turn-v1",
                4: "temporary-opponent-creature-attack-controller-this-turn-v1",
            }[self.schema_version]
        return (template, ({"op": "grant_declaration_restriction_until_end_of_turn",
            "card": card, "restriction": spec.to_dict()},), target_schema, mechanics)


def temporary_attack_requirement_template(text, *, source_attachment_relation=None):
    if text.casefold() == ("creatures your opponents control attack each combat if able "
                           "and attack a player other than you if able this turn."):
        return TemporaryAttackRequirementTemplate(5)
    if re.fullmatch(r"Target creature an opponent controls attacks you this turn if able\.?", text, re.I):
        return TemporaryAttackRequirementTemplate(4)
    target = re.fullmatch(r"Target creature(?P<opponent> an opponent controls)? attacks this turn if able\.?", text, re.I)
    if target is not None:
        return TemporaryAttackRequirementTemplate(2 if target["opponent"] else 1)
    if (source_attachment_relation is AttachmentReferenceKind.ENCHANTED
            and re.fullmatch(r"Enchanted creature attacks this turn if able\.?", text, re.I)):
        return TemporaryAttackRequirementTemplate(3)
    return None
