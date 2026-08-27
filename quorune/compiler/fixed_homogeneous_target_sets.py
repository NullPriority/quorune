from __future__ import annotations

"""Closed Oracle grammar for bounded homogeneous target sets."""

from dataclasses import dataclass
import re
from typing import Any, Callable, Mapping

from .destruction_templates import targeted_destruction_effect_template
from .exile_templates import targeted_exile_effect_template
from .public_zone_move_templates import public_graveyard_card_exile_template
from .return_to_hand_templates import (
    targeted_own_graveyard_return_to_hand_effect_template,
    targeted_return_to_hand_effect_template,
)
from .tap_state_templates import targeted_tap_state_effect_template


FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC = "fixed-homogeneous-target-set"
FIXED_HOMOGENEOUS_TARGET_SET_CAPABILITY = (
    "resolution.effect.fixed_homogeneous_target_set"
)

_COUNT_VALUES = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
}
_MULTI_TARGET = re.compile(
    r"(?P<verb>destroy|exile|tap|untap|return) "
    r"(?P<count>up to (?:one|two|three|four|five|six)|one or two|"
    r"two|three|four|five|six)(?P<other> other)? target "
    r"(?P<subject>[^.]+)\.?",
    re.IGNORECASE,
)
_OPERATION_LIFT = {
    "destroy": "destroy_targets",
    "exile_permanent": "exile_permanent_targets",
    "bounce": "return_permanent_targets_to_owner_hand",
    "return_graveyard_card_to_owner_hand": (
        "return_graveyard_targets_to_owner_hand"
    ),
    "exile_public_graveyard_card": "exile_public_graveyard_targets",
    "tap": "tap_targets",
    "untap": "untap_targets",
}


def _singularize_subject(value: str) -> str:
    replacements = (
        (r"\btheir owners' hands\b", "its owner's hand"),
        (r"\btheir owner's hands\b", "its owner's hand"),
        (r"\band/or\b", "or"),
        (r"\bfrom a single graveyard\b", "from a graveyard"),
        (r"\bfrom graveyards\b", "from a graveyard"),
        (r"\bcreatures\b", "creature"),
        (r"\bplaneswalkers\b", "planeswalker"),
        (r"\bartifacts\b", "artifact"),
        (r"\benchantments\b", "enchantment"),
        (r"\bpermanents\b", "permanent"),
        (r"\blands\b", "land"),
        (r"\bForests\b", "Forest"),
        (r"\bcards\b", "card"),
        (r"\btheir owners'\b", "its owner's"),
        (r"\btheir owner's\b", "its owner's"),
        (r"\btheir owner\b", "its owner"),
        (r"\btheir\b", "its"),
    )
    result = value
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


def _target_bounds(value: str) -> tuple[int, int, str]:
    normalized = value.casefold()
    if normalized.startswith("up to "):
        maximum = _COUNT_VALUES[normalized.removeprefix("up to ")]
        return 0, maximum, f"up-to-{maximum}"
    if normalized == "one or two":
        return 1, 2, "one-or-two"
    count = _COUNT_VALUES[normalized]
    return count, count, f"exactly-{count}"


def _compiled_singular(
    verb: str,
    text: str,
) -> tuple[
    str,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any],
    tuple[str, ...],
] | None:
    compilers: tuple[Callable[[str], Any], ...]
    if verb == "destroy":
        compilers = (targeted_destruction_effect_template,)
    elif verb == "exile":
        compilers = (
            targeted_exile_effect_template,
            public_graveyard_card_exile_template,
        )
    elif verb == "return":
        compilers = (
            targeted_return_to_hand_effect_template,
            targeted_own_graveyard_return_to_hand_effect_template,
        )
    elif verb in {"tap", "untap"}:
        compilers = (targeted_tap_state_effect_template,)
    else:
        return None
    for compiler in compilers:
        template = compiler(text)
        if template is None:
            continue
        compiled = template.compiled()
        if compiled[0] is None or compiled[2] is None:
            return None
        return (
            compiled[0],
            tuple(compiled[1]),
            compiled[2],
            tuple(compiled[3]),
        )
    return None


@dataclass(frozen=True, slots=True)
class FixedHomogeneousTargetSetTemplate:
    singular_template_id: str
    singular_effect: Mapping[str, Any]
    singular_target_schema: Mapping[str, Any]
    minimum_targets: int
    maximum_targets: int
    count_slug: str
    same_owner: bool
    mechanic_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        operation = self.singular_effect.get("op")
        reference_fields = set(self.singular_effect) - {"op"}
        if (
            operation not in _OPERATION_LIFT
            or reference_fields != {"card"}
            or self.singular_effect.get("card") != "$target.0"
            or not isinstance(self.singular_target_schema, Mapping)
            or type(self.minimum_targets) is not int
            or type(self.maximum_targets) is not int
            or not 0 <= self.minimum_targets <= self.maximum_targets <= 6
            or self.maximum_targets < 1
            or type(self.same_owner) is not bool
            or (
                self.same_owner
                and operation != "exile_public_graveyard_card"
            )
        ):
            raise ValueError("Fixed homogeneous target set is unsupported")

    @property
    def template_id(self) -> str:
        owner = "-same-owner" if self.same_owner else ""
        return (
            f"{self.singular_template_id}-fixed-{self.count_slug}"
            f"-target-set{owner}-v1"
        )

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": _OPERATION_LIFT[str(self.singular_effect["op"])],
                "cards": "$targets",
                "maximum_targets": self.maximum_targets,
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any]:
        schema = dict(self.singular_target_schema)
        if schema.pop("count", None) != 1:
            raise ValueError(
                "Fixed target sets must lift one canonical direct target"
            )
        if self.minimum_targets == 0:
            schema["up_to"] = self.maximum_targets
        elif self.minimum_targets == self.maximum_targets:
            schema["count"] = self.maximum_targets
        else:
            schema["min"] = self.minimum_targets
            schema["max"] = self.maximum_targets
        if self.same_owner:
            schema["same_owner"] = True
        return schema

    def compiled(self):
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            tuple(
                dict.fromkeys(
                    (
                        *self.mechanic_ids,
                        FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC,
                        "cr-115-targets",
                    )
                )
            ),
        )


def fixed_homogeneous_target_set_effect_template(
    text: str,
) -> FixedHomogeneousTargetSetTemplate | None:
    """Lower one bounded set of interchangeable public targets."""

    normalized = " ".join(text.strip().split())
    match = _MULTI_TARGET.fullmatch(normalized)
    if match is None:
        return None
    verb = match.group("verb").casefold()
    subject = match.group("subject")
    singular_target = "another target" if match.group("other") else "target"
    singular = (
        f"{verb} {singular_target} {_singularize_subject(subject)}."
    )
    compiled = _compiled_singular(verb, singular)
    if compiled is None:
        return None
    template_id, effects, target_schema, mechanic_ids = compiled
    if len(effects) != 1:
        return None
    minimum, maximum, count_slug = _target_bounds(match.group("count"))
    try:
        return FixedHomogeneousTargetSetTemplate(
            singular_template_id=template_id,
            singular_effect=effects[0],
            singular_target_schema=target_schema,
            minimum_targets=minimum,
            maximum_targets=maximum,
            count_slug=count_slug,
            same_owner="from a single graveyard" in subject.casefold(),
            mechanic_ids=mechanic_ids,
        )
    except ValueError:
        return None


__all__ = [
    "FIXED_HOMOGENEOUS_TARGET_SET_CAPABILITY",
    "FIXED_HOMOGENEOUS_TARGET_SET_MECHANIC",
    "FixedHomogeneousTargetSetTemplate",
    "fixed_homogeneous_target_set_effect_template",
]
