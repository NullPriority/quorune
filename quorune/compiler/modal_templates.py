from __future__ import annotations

"""Closed compiler owners for fixed nonrepeating modal programs."""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import re
from typing import Any, Callable, Mapping, Sequence

from ..util import stable_json


FIXED_CHOOSE_ONE_MODAL_MECHANIC = "fixed-choose-one-modal-spell"
FIXED_CHOOSE_ONE_MODAL_CAPABILITY = "choice.modal.fixed_one"
FIXED_NONREPEATING_MODAL_MECHANIC = "fixed-nonrepeating-modal"
FIXED_NONREPEATING_MODAL_CAPABILITY = "choice.modal.fixed_nonrepeating"

CompiledEffectTemplate = tuple[
    str | None,
    tuple[Mapping[str, Any], ...],
    Mapping[str, Any] | None,
    tuple[str, ...],
]
EffectCompiler = Callable[[str], CompiledEffectTemplate]
MaterialRow = tuple[str, str, Any]

_HEADER = "Choose one —"
_NONREPEATING_HEADERS = {
    _HEADER: (1, 1),
    "Choose one or both —": (1, 2),
    "Choose one or more —": (1, None),
    "Choose two —": (2, 2),
}
_BULLET_PREFIX = "• "
_NAMED_MODE = re.compile(
    r"^(?P<label>[A-Z][A-Za-z' ]*[A-Za-z']) — (?P<body>.+)$"
)


@dataclass(frozen=True, slots=True)
class FixedChooseOneModalSpellTemplate:
    """One mandatory choice among two or three independently closed modes."""

    component_template_ids: tuple[str, ...]
    _target_schema: Mapping[str, Any]
    mechanic_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            len(self.component_template_ids) not in {2, 3}
            or any(not value for value in self.component_template_ids)
            or self.mechanic_ids[:1] != (FIXED_CHOOSE_ONE_MODAL_MECHANIC,)
        ):
            raise ValueError("Fixed Choose one modal template is malformed")
        schema = deepcopy(dict(self._target_schema))
        if schema.get("mode_count") != 1:
            raise ValueError("Fixed Choose one modal template requires one mode")
        modes = schema.get("modes")
        expected = tuple(
            f"mode_{index}"
            for index in range(1, len(self.component_template_ids) + 1)
        )
        if not isinstance(modes, Mapping) or tuple(modes) != expected:
            raise ValueError("Fixed Choose one modal definitions are malformed")
        object.__setattr__(self, "_target_schema", schema)

    @property
    def template_id(self) -> str:
        digest = hashlib.sha256(
            stable_json(
                {
                    "components": self.component_template_ids,
                    "target_schema": self._target_schema,
                }
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"fixed-choose-one-modal-{digest}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return ()

    @property
    def target_schema(self) -> Mapping[str, Any]:
        return deepcopy(self._target_schema)

    @property
    def mechanics(self) -> tuple[str, ...]:
        return self.mechanic_ids

    def compiled(self) -> CompiledEffectTemplate:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


@dataclass(frozen=True, slots=True)
class FixedNonrepeatingModalTemplate:
    """One bounded ordered selection among independently closed modes."""

    component_template_ids: tuple[str, ...]
    minimum_modes: int
    maximum_modes: int
    context_prefix: str
    _target_schema: Mapping[str, Any]
    mechanic_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        mode_total = len(self.component_template_ids)
        supported_bounds = {
            (1, 1),
            (1, mode_total),
            (2, 2),
        }
        if (
            mode_total not in {2, 3, 4, 5}
            or any(not value for value in self.component_template_ids)
            or not 1 <= self.minimum_modes <= self.maximum_modes <= mode_total
            or (self.minimum_modes, self.maximum_modes) not in supported_bounds
            or self.mechanic_ids[:1]
            != (FIXED_NONREPEATING_MODAL_MECHANIC,)
        ):
            raise ValueError("Fixed nonrepeating modal template is malformed")
        schema = deepcopy(dict(self._target_schema))
        if (
            set(schema) != {"mode_count", "min_modes", "max_modes", "modes"}
            or schema.get("mode_count") != self.minimum_modes
            or schema.get("min_modes") != self.minimum_modes
            or schema.get("max_modes") != self.maximum_modes
        ):
            raise ValueError("Fixed modal selection bounds are malformed")
        modes = schema.get("modes")
        expected = tuple(
            f"mode_{index}" for index in range(1, mode_total + 1)
        )
        if not isinstance(modes, Mapping) or tuple(modes) != expected:
            raise ValueError("Fixed modal definitions are malformed")
        object.__setattr__(self, "_target_schema", schema)

    @property
    def template_id(self) -> str:
        digest = hashlib.sha256(
            stable_json(
                {
                    "components": self.component_template_ids,
                    "minimum_modes": self.minimum_modes,
                    "maximum_modes": self.maximum_modes,
                    "target_schema": self._target_schema,
                }
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"fixed-nonrepeating-modal-{digest}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return ()

    @property
    def target_schema(self) -> Mapping[str, Any]:
        return deepcopy(self._target_schema)

    @property
    def mechanics(self) -> tuple[str, ...]:
        return self.mechanic_ids

    def compiled(self) -> CompiledEffectTemplate:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


def _mode_body(material_line: str) -> str | None:
    if not material_line.startswith(_BULLET_PREFIX):
        return None
    body = material_line[len(_BULLET_PREFIX) :]
    named = _NAMED_MODE.fullmatch(body)
    return named.group("body") if named is not None else body


def fixed_choose_one_modal_spell_template(
    material_rows: Sequence[MaterialRow],
    *,
    compile_effect: EffectCompiler,
) -> FixedChooseOneModalSpellTemplate | None:
    """Lower only a complete ordinary two- or three-mode spell face."""

    if (
        len(material_rows) not in {3, 4}
        or material_rows[0][1] != _HEADER
    ):
        return None
    bodies = tuple(_mode_body(row[1]) for row in material_rows[1:])
    if any(body is None or not body.strip() for body in bodies):
        return None
    compiled = tuple(compile_effect(str(body)) for body in bodies)
    if any(
        template_id is None
        or not effects
        or not mechanics
        or target_schema is not None
        and "modes" in target_schema
        for template_id, effects, target_schema, mechanics in compiled
    ):
        return None

    modes: dict[str, dict[str, Any]] = {}
    mechanic_ids = [FIXED_CHOOSE_ONE_MODAL_MECHANIC]
    for index, (_template, effects, target_schema, mechanics) in enumerate(
        compiled,
        1,
    ):
        definition = deepcopy(dict(target_schema or {}))
        if target_schema is None:
            definition["groups"] = []
        definition["effects"] = [deepcopy(dict(effect)) for effect in effects]
        definition["mechanics"] = list(mechanics)
        modes[f"mode_{index}"] = definition
        mechanic_ids.extend(mechanics)
    return FixedChooseOneModalSpellTemplate(
        component_template_ids=tuple(
            str(template_id)
            for template_id, _effects, _schema, _mechanics in compiled
        ),
        _target_schema={"mode_count": 1, "modes": modes},
        mechanic_ids=tuple(dict.fromkeys(mechanic_ids)),
    )


def fixed_nonrepeating_modal_template(
    material_rows: Sequence[MaterialRow],
    *,
    compile_effect: EffectCompiler,
) -> FixedNonrepeatingModalTemplate | None:
    """Lower one complete fixed modal block without repeatable selections."""

    if len(material_rows) not in {3, 4, 5, 6}:
        return None
    header_line = material_rows[0][1]
    header = next(
        (
            value
            for value in _NONREPEATING_HEADERS
            if header_line.casefold().endswith(value.casefold())
        ),
        None,
    )
    if header is None:
        return None
    context_prefix = header_line[: -len(header)]
    if context_prefix and not context_prefix.endswith(" "):
        return None
    bodies = tuple(_mode_body(row[1]) for row in material_rows[1:])
    if any(body is None or not body.strip() for body in bodies):
        return None
    minimum_modes, fixed_maximum = _NONREPEATING_HEADERS[header]
    maximum_modes = len(bodies) if fixed_maximum is None else fixed_maximum
    if maximum_modes > len(bodies):
        return None
    compiled = tuple(compile_effect(str(body)) for body in bodies)
    if any(
        template_id is None
        or not effects
        or not mechanics
        or target_schema is not None
        and ("groups" in target_schema or "modes" in target_schema)
        for template_id, effects, target_schema, mechanics in compiled
    ):
        return None

    modes: dict[str, dict[str, Any]] = {}
    mechanic_ids = [FIXED_NONREPEATING_MODAL_MECHANIC]
    for index, (_template, effects, target_schema, mechanics) in enumerate(
        compiled,
        1,
    ):
        mode_id = f"mode_{index}"
        groups: list[dict[str, Any]] = []
        if target_schema is not None:
            group = deepcopy(dict(target_schema))
            group["id"] = f"{mode_id}_target_1"
            groups.append(group)
        modes[mode_id] = {
            "groups": groups,
            "effects": [deepcopy(dict(effect)) for effect in effects],
            "mechanics": list(mechanics),
        }
        mechanic_ids.extend(mechanics)
    return FixedNonrepeatingModalTemplate(
        component_template_ids=tuple(
            str(template_id)
            for template_id, _effects, _schema, _mechanics in compiled
        ),
        minimum_modes=minimum_modes,
        maximum_modes=maximum_modes,
        context_prefix=context_prefix,
        _target_schema={
            "mode_count": minimum_modes,
            "min_modes": minimum_modes,
            "max_modes": maximum_modes,
            "modes": modes,
        },
        mechanic_ids=tuple(dict.fromkeys(mechanic_ids)),
    )


__all__ = [
    "FIXED_CHOOSE_ONE_MODAL_CAPABILITY",
    "FIXED_CHOOSE_ONE_MODAL_MECHANIC",
    "FIXED_NONREPEATING_MODAL_CAPABILITY",
    "FIXED_NONREPEATING_MODAL_MECHANIC",
    "FixedChooseOneModalSpellTemplate",
    "FixedNonrepeatingModalTemplate",
    "fixed_choose_one_modal_spell_template",
    "fixed_nonrepeating_modal_template",
]
