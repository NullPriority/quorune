"""Compile a temporary self-copy from an ordinary activation body."""

from ..rules.source_references import SourceReferenceSpec, source_self_permanent_pattern
from ..rules.temporary_self_copy import (
    linked_temporary_artifact_copy_effect, linked_temporary_artifact_copy_schema,
    is_temporary_graveyard_copy_activation, temporary_graveyard_copy_effect, temporary_graveyard_copy_target_schema,
    temporary_self_copy_effect, temporary_self_copy_target_schema, TemporaryPermanentCopySet,
)
from ..rules.temporary_copy_subject import TemporaryCopySubjectScope
from .any_number_target_sets import ANY_NUMBER_TARGET_MECHANIC
from . import regex_working_set as re


_TARGET_TYPES = "artifact, creature, enchantment, or land"


def temporary_self_copy_template(text, *, source_name):
    if "becomes a copy of target" not in text.casefold():
        return None
    source = SourceReferenceSpec(source_name).regex_pattern
    match = re.fullmatch(
        rf"(?:{source}|{source_self_permanent_pattern()}) becomes a copy of target "
        r"(?P<scope>.+?) until end of turn\.?",
        " ".join(text.split()),
        flags=re.IGNORECASE,
    )
    if match is None or match["scope"].casefold() != _TARGET_TYPES:
        return _linked_temporary_artifact_copy(text, source_pattern=source)
    return (
        "temporary-self-copy-v1", (temporary_self_copy_effect(),),
        temporary_self_copy_target_schema(),
        ("cr-115-targets",),
    )


def temporary_graveyard_self_copy_template(ability, *, text, public_timing):
    from .activated_costs import activated_ability_cost

    if (" ".join(text.split()).casefold() !=
            "this land becomes a copy of target permanent card in your graveyard until end of turn."
            or not is_temporary_graveyard_copy_activation(
                ability, activated_ability_cost(ability, public_timing=public_timing),
            )):
        return None
    return ("temporary-owned-graveyard-self-copy-v1", (temporary_graveyard_copy_effect(),),
            temporary_graveyard_copy_target_schema(), ("cr-115-targets",))


def _linked_temporary_artifact_copy(text, *, source_pattern):
    match = re.fullmatch(
        rf"Until end of turn, (?:{source_pattern}|{source_self_permanent_pattern()}) becomes a copy of target "
        r"creature you control of the chosen type, except it['’]s an artifact in addition to its other types\.?",
        " ".join(text.split()),
        flags=re.IGNORECASE,
    )
    if match is None:
        return None
    return (
        "linked-temporary-artifact-copy-v1", (linked_temporary_artifact_copy_effect(),),
        linked_temporary_artifact_copy_schema(), ("cr-115-targets",),
    )


_COPY_SETS = {
    "each other creature becomes a copy of target nonlegendary creature until end of turn.":
        TemporaryPermanentCopySet.ALL_OTHER_CREATURES,
    "choose target creature you control. each other creature you control becomes a copy of it "
    "until end of turn, except those creatures aren't legendary.":
        TemporaryPermanentCopySet.OTHER_CONTROLLED_CREATURES_NONLEGENDARY,
    "choose target creature you control. each other creature becomes a copy of that creature "
    "until end of turn, except it isn't legendary.":
        TemporaryPermanentCopySet.ALL_OTHER_CREATURES_NONLEGENDARY,
    "choose target artifact you control. each other artifact you control becomes a copy of that artifact until end of turn.":
        TemporaryPermanentCopySet.OTHER_CONTROLLED_ARTIFACTS,
}


def temporary_creature_copy_set_template(text):
    material = " ".join(text.split()).replace("\u2019", "'").casefold()
    scope = _COPY_SETS.get(material)
    if scope is None:
        return None
    return f"temporary-{scope.card_type}-copy-set-{scope.value}-v{scope.schema_version}", (scope.to_effect(),), scope.target_schema(), ("cr-115-targets",)


_COPY_SUBJECT_CHOICES = {
    "choose a nonlegendary creature on the battlefield. target creature becomes a copy of that creature until end of turn.":
        TemporaryCopySubjectScope.NONLEGENDARY_FOR_ONE_TARGET,
    "choose a creature on the battlefield. any number of target creatures you control each become a copy of that creature "
    "until end of turn.":
        TemporaryCopySubjectScope.CREATURE_FOR_CONTROLLED_TARGETS,
}


def temporary_copy_subject_template(text):
    scope = _COPY_SUBJECT_CHOICES.get(" ".join(text.split()).casefold())
    if scope is None:
        return None
    mechanics = ("cr-115-targets", ANY_NUMBER_TARGET_MECHANIC) if scope.multiple_receivers else ("cr-115-targets",)
    return f"temporary-copy-subject-{scope.value}-v1", (scope.choice_effect(),), scope.target_schema(), mechanics


def linked_exile_self_copy_template(ability, *, source_name, text, public_timing):
    if (ability.cost_text != "{2}" or ability.zones != ("battlefield",) or public_timing is not None
            or ability.sorcery_speed or ability.activation_conditions or ability.activation_limit is not None):
        return None
    source = SourceReferenceSpec(source_name).regex_pattern
    if re.fullmatch(
        rf"(?:{source}|{source_self_permanent_pattern()}) becomes a copy of target creature card exiled with it, "
        r"except it['’]s 0/0 and has this ability\.?",
        " ".join(text.split()), re.IGNORECASE,
    ) is None:
        return None
    from ..rules.linked_exile_copy import (
        LINKED_EXILE_COPY_PAIR, LINKED_EXILE_COPY_TEMPLATE, linked_exile_copy_effect, linked_exile_copy_target_schema,
    )

    return (
        LINKED_EXILE_COPY_TEMPLATE, (linked_exile_copy_effect(),),
        linked_exile_copy_target_schema(LINKED_EXILE_COPY_PAIR), ("cr-115-targets",),
    )


def announced_artifact_self_copy_template(ability, *, source_name, text, public_timing):
    from ..rules.announced_artifact_copy import (
        ANNOUNCED_ARTIFACT_COPY_TEMPLATE, announced_artifact_copy_effect,
        artifact_copy_target_schema, is_announced_artifact_copy_price,
    )
    from .activated_costs import activated_ability_cost

    if (ability.zones != ("battlefield",) or public_timing is not None or ability.sorcery_speed
            or ability.activation_conditions or not is_announced_artifact_copy_price(activated_ability_cost(ability))):
        return None
    source = SourceReferenceSpec(source_name).regex_pattern
    match = re.fullmatch(
        rf"(?:{source}|this land) becomes a copy of target nontoken artifact you control with mana value X\.?",
        " ".join(text.split()), re.IGNORECASE,
    )
    if match is None:
        return None
    return (ANNOUNCED_ARTIFACT_COPY_TEMPLATE, (announced_artifact_copy_effect(),),
            artifact_copy_target_schema(), ("cr-115-targets",))


def announced_graveyard_self_copy_template(ability, *, source_name, text, public_timing):
    if (ability.cost_text != "{X}" or ability.announced_x_symbols != 1
            or ability.zones != ("battlefield",) or public_timing is not None
            or ability.sorcery_speed or ability.activation_conditions or ability.activation_limit is not None):
        return None
    source = SourceReferenceSpec(source_name).regex_pattern
    match = re.fullmatch(
        rf"(?:{source}|{source_self_permanent_pattern()}) becomes a copy of target creature card in your "
        r"graveyard with mana value X, except its name is (?P<name>.+?), "
        r"it['’]s legendary in addition to its other types, and it has this ability\.?",
        " ".join(text.split()), re.IGNORECASE,
    )
    if match is None or match["name"].casefold() != source_name.casefold():
        return None
    from ..rules.announced_graveyard_copy import (
        ANNOUNCED_GRAVEYARD_COPY_TEMPLATE, announced_graveyard_copy_effect, owned_graveyard_creature_target_schema,
    )

    return (
        ANNOUNCED_GRAVEYARD_COPY_TEMPLATE, (announced_graveyard_copy_effect(source_name),),
        owned_graveyard_creature_target_schema(), ("cr-115-targets",),
    )
