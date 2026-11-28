from __future__ import annotations

from . import regex_working_set as re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from ..attachment_references import (
    AttachmentReferenceKind,
    AttachmentReferenceSpec,
)
from ..rules.source_references import source_self_permanent_type
from ..next_player_untap_model import (
    NEXT_PLAYER_UNTAP_INSTRUCTION_VERSION, NEXT_PLAYER_UNTAP_OPERATION,
    NextPlayerUntapScope, next_player_untap_target_schema,
)
from .direct_target import (
    DirectPermanentTargetSpec,
    compiled_direct_target,
    direct_permanent_target_spec,
    direct_target_effect,
)
from .fixed_source_effect_sequences import SOURCE_ZONE_OBJECT


_TAP_STATE_SOURCE_TYPES = frozenset(
    {
        "artifact",
        "battle",
        "creature",
        "enchantment",
        "land",
        "permanent",
        "planeswalker",
    }
)


_NEXT_PLAYER_UNTAP = re.compile(
    r"(?P<subject>lands target player|creatures and lands target opponent)"
    r" controls don['’]t untap during their next untap step\.?", re.I,
)


COMBAT_PREVENTION_ATTACKERS_UNTAP_TEMPLATE = "combat-prevention-current-attackers-next-untap-v1"
_COMBAT_PREVENTION_CLAUSE = "Prevent all combat damage that would be dealt this turn."
_ATTACKERS_UNTAP_CLAUSE = "Each attacking creature doesn't untap during its controller's next untap step."


def fixed_combat_prevention_attackers_untap_template(text: str):
    normalized = " ".join(text.replace("’", "'").split()).casefold()
    if normalized != f"{_COMBAT_PREVENTION_CLAUSE} {_ATTACKERS_UNTAP_CLAUSE}".casefold():
        return None
    from .prevention_templates import fixed_prevention_effect_template
    from ..tap_state import attacking_creatures_next_untap_effect

    _, fog, _, mechanics = fixed_prevention_effect_template(_COMBAT_PREVENTION_CLAUSE)
    return (COMBAT_PREVENTION_ATTACKERS_UNTAP_TEMPLATE,
            (*fog, attacking_creatures_next_untap_effect()), None, (*mechanics, "tap-and-untap"))


def destroy_creatures_and_restrict_controller_lands_template(text: str):
    normalized = " ".join(text.replace("’", "'").split()).casefold()
    if normalized != "destroy all creatures. lands you control don't untap during your next untap step.":
        return None
    from .destruction_templates import mass_destruction_effect_template

    _, destruction, _, mechanics = mass_destruction_effect_template("Destroy all creatures.").compiled()
    restriction = {
        "op": NEXT_PLAYER_UNTAP_OPERATION, "schema_version": NEXT_PLAYER_UNTAP_INSTRUCTION_VERSION,
        "player": "$controller", "scope": NextPlayerUntapScope.LANDS.value,
    }
    return ("destroy-creatures-and-controller-land-untap-v1",
            (*destruction, restriction), None, (*mechanics, "tap-and-untap"))


def restrict_controller_lands_after_fixed_effect_template(text: str):
    destruction = destroy_creatures_and_restrict_controller_lands_template(text)
    if destruction is not None:
        return destruction
    normalized = " ".join(text.replace("’", "'").split()).casefold()
    primary = "gain control of target artifact, creature, or enchantment."
    if normalized != primary + " lands you control don't untap during your next untap step.":
        return None
    from .fixed_control_templates import fixed_control_effect_template

    _, control, schema, mechanics = fixed_control_effect_template(primary).compiled()
    restriction = {
        "op": NEXT_PLAYER_UNTAP_OPERATION, "schema_version": NEXT_PLAYER_UNTAP_INSTRUCTION_VERSION,
        "player": "$controller", "scope": NextPlayerUntapScope.LANDS.value,
    }
    return ("control-and-next-own-land-untap-v1",
            (*control, restriction), schema, (*mechanics, "tap-and-untap"))


def target_combat_prevention_two_untaps_template(text: str):
    normalized = " ".join(text.replace("’", "'").split()).casefold()
    if normalized != (
        "tap target creature. prevent all combat damage that would be dealt by that creature this turn. "
        "it doesn't untap during its controller's next two untap steps."
    ):
        return None
    from ..rules.prevention_component_shapes import targeted_outgoing_combat_shield
    from ..tap_state import two_controller_untaps_effect

    target_schema = direct_permanent_target_spec("target creature").to_target_schema()
    return ("target-combat-prevention-two-untaps-v1",
            ({"op": "tap", "card": "$target.0"}, targeted_outgoing_combat_shield(), two_controller_untaps_effect()),
            target_schema, ("cr-615-prevention-effects", "cr-115-targets", "tap-and-untap"))


def next_player_untap_effect_template(text: str):
    match = _NEXT_PLAYER_UNTAP.fullmatch(" ".join(text.strip().split()))
    if match is None:
        return None
    scope = (NextPlayerUntapScope.LANDS if match["subject"].casefold() == "lands target player"
        else NextPlayerUntapScope.CREATURES_AND_LANDS)
    effect = {"op": NEXT_PLAYER_UNTAP_OPERATION, "schema_version": NEXT_PLAYER_UNTAP_INSTRUCTION_VERSION,
        "player": "$target.0", "scope": scope.value}
    return "fixed-next-player-untap-v1", (effect,), next_player_untap_target_schema(scope), ("tap-and-untap", "cr-115-targets")


class TapStateAction(str, Enum):
    """Closed action vocabulary for one permanent tap-state instruction."""

    TAP = "tap"
    UNTAP = "untap"
    PROHIBIT_NEXT_UNTAP = "prohibit_next_untap"


@dataclass(frozen=True, slots=True)
class TargetedTapStateEffectTemplate:
    """Typed lowering for one mandatory direct-target instruction."""

    action: TapStateAction
    target_spec: DirectPermanentTargetSpec

    def __post_init__(self) -> None:
        if not isinstance(self.action, TapStateAction):
            raise ValueError("Tap-state action is unsupported")
        if not isinstance(self.target_spec, DirectPermanentTargetSpec):
            raise ValueError("Tap-state target is unsupported")

    @property
    def template_id(self) -> str:
        return f"{self.action.value}-target-{self.target_spec.slug}-v3"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return direct_target_effect(
            self.action.value,
            reference_field="card",
        )

    @property
    def target_schema(self) -> Mapping[str, Any]:
        return self.target_spec.to_target_schema()

    @property
    def mechanics(self) -> tuple[str, ...]:
        return ("tap-and-untap", "cr-115-targets")

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any],
        tuple[str, ...],
    ]:
        return compiled_direct_target(
            template_id=self.template_id,
            effects=self.effects,
            target_schema=self.target_schema,
            mechanics=self.mechanics,
        )


@dataclass(frozen=True, slots=True)
class TapThenProhibitNextUntapTemplate:
    target_spec: DirectPermanentTargetSpec

    @property
    def effects(self):
        return ({'op': 'tap', 'card': '$target.0'}, {'op': 'prohibit_next_untap', 'card': '$target.0'})

    @property
    def target_schema(self):
        return self.target_spec.to_target_schema()

    def compiled(self):
        return ('tap-target-next-controller-untap-v1', self.effects, self.target_schema,
                ('tap-and-untap', 'cr-115-targets'))


@dataclass(frozen=True, slots=True)
class SourceTapStateEffectTemplate:
    """Typed lowering for the current physical source incarnation."""

    action: TapStateAction
    source_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.action, TapStateAction):
            raise ValueError("Tap-state action is unsupported")
        if self.source_type not in _TAP_STATE_SOURCE_TYPES:
            raise ValueError("Tap-state source type is unsupported")

    @property
    def template_id(self) -> str:
        return f"{self.action.value}-this-{self.source_type}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return ({"op": self.action.value, "card": SOURCE_ZONE_OBJECT},)

    @property
    def target_schema(self) -> None:
        return None

    @property
    def mechanics(self) -> tuple[str, ...]:
        return ("tap-and-untap",)

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


@dataclass(frozen=True, slots=True)
class AttachedTapStateEffectTemplate:
    """Typed lowering for one source-relative attached permanent."""

    action: TapStateAction
    permanent_type: str
    relation: AttachmentReferenceKind = AttachmentReferenceKind.ENCHANTED

    def __post_init__(self) -> None:
        if not isinstance(self.action, TapStateAction):
            raise ValueError("Tap-state action is unsupported")
        if self.permanent_type not in {
            "artifact",
            "creature",
            "land",
            "permanent",
        }:
            raise ValueError("Attached tap-state type is unsupported")
        if self.relation is AttachmentReferenceKind.EQUIPPED:
            if (self.action, self.permanent_type) == (TapStateAction.UNTAP, "creature"):
                return
        elif self.relation is AttachmentReferenceKind.ENCHANTED:
            return
        raise ValueError("Attached tap-state relation is unsupported")

    @property
    def template_id(self) -> str:
        if self.relation is AttachmentReferenceKind.EQUIPPED:
            return "untap-equipped-creature-v1"
        return f"{self.action.value}-enchanted-{self.permanent_type}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": self.action.value,
                "card": AttachmentReferenceSpec(
                    relation=self.relation,
                    required_card_type=self.permanent_type,
                    schema_version=2 if self.relation is AttachmentReferenceKind.EQUIPPED else 1,
                ).to_dict(),
            },
        )

    @property
    def target_schema(self) -> None:
        return None

    @property
    def mechanics(self) -> tuple[str, ...]:
        return ("tap-and-untap",)

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        None,
        tuple[str, ...],
    ]:
        return (
            self.template_id,
            self.effects,
            self.target_schema,
            self.mechanics,
        )


@dataclass(frozen=True, slots=True)
class OptionalTapStateEffectTemplate:
    """Typed lowering for ``may tap or untap`` with an explicit decline."""

    target_spec: DirectPermanentTargetSpec

    def __post_init__(self) -> None:
        if not isinstance(self.target_spec, DirectPermanentTargetSpec):
            raise ValueError("Optional tap-state target is unsupported")

    @property
    def template_id(self) -> str:
        return f"choose-tap-or-untap-target-{self.target_spec.slug}-v1"

    @property
    def effects(self) -> tuple[Mapping[str, Any], ...]:
        return (
            {
                "op": "choose_option",
                "player": "$controller",
                "prompt": "Tap, untap, or leave the target unchanged.",
                "options": [
                    {"id": "tap", "label": "Tap"},
                    {"id": "untap", "label": "Untap"},
                    {"id": "decline", "label": "Leave unchanged"},
                ],
                "then_by_choice": {
                    "tap": [{"op": "tap", "card": "$target.0"}],
                    "untap": [{"op": "untap", "card": "$target.0"}],
                    "decline": [],
                },
            },
        )

    @property
    def target_schema(self) -> Mapping[str, Any]:
        return self.target_spec.to_target_schema()

    @property
    def mechanics(self) -> tuple[str, ...]:
        return ("tap-and-untap", "cr-115-targets")

    def compiled(
        self,
    ) -> tuple[
        str,
        tuple[Mapping[str, Any], ...],
        Mapping[str, Any],
        tuple[str, ...],
    ]:
        return compiled_direct_target(
            template_id=self.template_id,
            effects=self.effects,
            target_schema=self.target_schema,
            mechanics=self.mechanics,
        )


@dataclass(frozen=True, slots=True)
class OptionalEquippedCreatureTapStateTemplate:
    """One untargeted optional tap-state instruction from Equipment."""

    def compiled(self) -> tuple[
        str, tuple[Mapping[str, Any], ...], None, tuple[str, ...]
    ]:
        from ..rules.tap_state_capability_shapes import optional_equipped_creature_tap_state_effect

        return (
            "choose-tap-or-untap-equipped-creature-v1",
            (optional_equipped_creature_tap_state_effect(),),
            None,
            ("tap-and-untap",),
        )


TapStateEffectTemplate = (
    OptionalEquippedCreatureTapStateTemplate
    | TargetedTapStateEffectTemplate
    | SourceTapStateEffectTemplate
    | AttachedTapStateEffectTemplate
    | OptionalTapStateEffectTemplate
    | TapThenProhibitNextUntapTemplate
)


def targeted_tap_state_effect_template(
    text: str,
    *,
    source_is_permanent: bool | None = None,
    source_card_types: Sequence[str] = (),
    source_attachment_relation: AttachmentReferenceKind | None = None,
) -> TapStateEffectTemplate | None:
    """Recognize one closed single-object tap-state instruction."""

    normalized = " ".join(text.strip().split())
    delayed = re.fullmatch(
        r"Tap (?P<subject>(?:another )?target [^.]+)\. (?P<reference>It|That (?:creature|permanent|land)) doesn['’]t untap during its controller['’]s next untap step\.?",
        normalized, re.IGNORECASE)
    if delayed is not None:
        target_spec = direct_permanent_target_spec(delayed['subject'])
        if (target_spec is not None and delayed["reference"].casefold() == "that land"
                and tuple(target_spec.to_target_schema().get("types_any", ())) != ("land",)):
            return None
        return TapThenProhibitNextUntapTemplate(target_spec) if target_spec is not None else None
    next_untap = re.fullmatch(
        r"(?P<subject>(?:another )?target [^.]+) doesn['’]t untap during its controller['’]s next untap step\.?",
        normalized, re.IGNORECASE)
    if next_untap is not None:
        target_spec = direct_permanent_target_spec(next_untap['subject'])
        return TargetedTapStateEffectTemplate(TapStateAction.PROHIBIT_NEXT_UNTAP, target_spec) if target_spec is not None else None
    optional = re.fullmatch(
        r"you may tap or untap (?P<subject>(?:another )?target [^.]+)\.?",
        normalized,
        re.IGNORECASE,
    )
    if optional is not None:
        target_spec = direct_permanent_target_spec(optional.group("subject"))
        return (
            OptionalTapStateEffectTemplate(target_spec)
            if target_spec is not None
            else None
        )

    if (source_attachment_relation is AttachmentReferenceKind.EQUIPPED
            and re.fullmatch(r"you may tap or untap equipped creature\.?", normalized, re.I)):
        return OptionalEquippedCreatureTapStateTemplate()
    if (source_attachment_relation is AttachmentReferenceKind.EQUIPPED
            and re.fullmatch(r"untap equipped creature\.?", normalized, re.I)):
        return AttachedTapStateEffectTemplate(
            action=TapStateAction.UNTAP, permanent_type="creature", relation=source_attachment_relation,
        )
    attached = re.fullmatch(
        r"(?P<action>tap|untap) enchanted "
        r"(?P<kind>artifact|creature|land|permanent)\.?",
        normalized,
        re.IGNORECASE,
    )
    if attached is not None:
        if source_attachment_relation is not AttachmentReferenceKind.ENCHANTED:
            return None
        return AttachedTapStateEffectTemplate(
            action=TapStateAction(attached.group("action").casefold()),
            permanent_type=attached.group("kind").casefold(),
        )

    source = re.fullmatch(
        r"(?P<action>tap|untap) (?P<subject>this [A-Za-z]+)\.?",
        normalized,
        re.IGNORECASE,
    )
    if source is not None:
        subject_type = source.group("subject").split(maxsplit=1)[1].casefold()
        source_type = source_self_permanent_type(source.group("subject"))
        card_types = {str(value).casefold() for value in source_card_types}
        if (
            source_is_permanent is not True
            or source_type is None
            or subject_type not in _TAP_STATE_SOURCE_TYPES
            or (source_type != "permanent" and source_type not in card_types)
        ):
            return None
        return SourceTapStateEffectTemplate(
            action=TapStateAction(source.group("action").casefold()),
            source_type=subject_type,
        )

    targeted = re.fullmatch(
        r"(?P<action>tap|untap) "
        r"(?P<subject>(?:another )?target [^.]+)\.?",
        normalized,
        re.IGNORECASE,
    )
    if targeted is None:
        return None
    target_spec = direct_permanent_target_spec(targeted.group("subject"))
    if target_spec is None:
        return None
    return TargetedTapStateEffectTemplate(
        action=TapStateAction(targeted.group("action").casefold()),
        target_spec=target_spec,
    )


__all__ = [
    "AttachedTapStateEffectTemplate",
    "OptionalTapStateEffectTemplate",
    "SourceTapStateEffectTemplate",
    "TapStateAction",
    "TapStateEffectTemplate",
    "TargetedTapStateEffectTemplate",
    "targeted_tap_state_effect_template",
]
