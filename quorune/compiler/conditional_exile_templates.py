from __future__ import annotations

"""Source-spanned recognition for CR610.3, separate from linked triggers.

Recognition retains target predicates and emits the existing linked operation
with an explicit conditional source-departure lifecycle.
"""

from dataclasses import dataclass
from . import regex_working_set as re

from ..replacement.immutable import FrozenMap
from ..rules.source_references import SourceReferenceSpec, source_self_permanent_type
from .direct_target import direct_permanent_target_spec
from .fixed_homogeneous_target_sets import _singularize_subject
import hashlib
from ..linked_exile_return_model import (
    ANNOUNCED_OPPONENT_EXILE_VERSION, LinkedExileReturnSpec, LINKED_EXILE_RETURN_MECHANIC,
    CONDITIONAL_EXILE_CAST_BINDING, CONDITIONAL_EXILE_CAST_VERSION, conditional_exile_cast_target_schema,
    SAME_NAME_CONDITIONAL_EXILE_VERSION,
    SELECTED_CONDITIONAL_EXILE_VERSION, SELECTED_CONDITIONAL_EXILE_FIELD,
    SameNameConditionalExileScope, SelectedConditionalExileScope,
)
from ..replacement.immutable import thaw_value


_CAST_EXILED = re.compile(
    r"(?P<exile>Exile .+? leaves the battlefield)\. You may cast that card for as long as it remains exiled, "
    r"and mana of any type can be spent to cast that spell\.", re.I,
)
_UNTIL = re.compile(r'^Exile (?P<subject>.+?) until (?P<source>.+?) leaves the battlefield\.?$', re.IGNORECASE)
_ANNOUNCED_OPPONENTS = re.compile(
    r"For each opponent, exile up to one target nonland permanent that player controls "
    r"until (?P<source>.+?) leaves the battlefield\.?$", re.I,
)
_SAME_NAME = re.compile(
    r"target nonland permanent an opponent controls and all other nonland permanents "
    r"(?P<scope>your opponents control|that player controls) with the same name as that permanent", re.I,
)
_CARDINALITY = re.compile(r'(?P<count>up to (?:one|two|three|four|five|six)|two|three|four|five|six) (?P<other>other )?target (?P<quality>.+)', re.IGNORECASE)
_COUNTS = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6}
_NON_ANGEL_SELECTION = re.compile(
    r"You may exile any number of non-Angel creatures you control until (?P<source>.+?) leaves the battlefield\.",
    re.I,
)
_OTHER_NONTOKEN_SELECTION = re.compile(
    r"Exile any number of other nontoken creatures you control until (?P<source>.+?) leaves the battlefield\.",
    re.I,
)
_OTHER_NONLAND_SELECTION = re.compile(
    r"Exile any number of other nonland permanents you control until (?P<source>.+?) leaves the battlefield\. "
    r"Create a 1/1 white Soldier creature token for each permanent exiled this way\.",
    re.I,
)


@dataclass(frozen=True, slots=True)
class ConditionalSourceExileTemplate:
    target_schema: FrozenMap
    same_name_scope: SameNameConditionalExileScope | None = None
    announced_opponents: bool = False
    cast_exiled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.target_schema, FrozenMap):
            raise ValueError('Conditional exile target data must be immutable')
        if self.same_name_scope is not None and not isinstance(self.same_name_scope, SameNameConditionalExileScope):
            raise ValueError("Conditional exile requires a typed same-name scope")
        if type(self.cast_exiled) is not bool or self.cast_exiled and (
            self.announced_opponents or self.same_name_scope is not None
            or self.target_schema != FrozenMap(conditional_exile_cast_target_schema())
        ):
            raise ValueError("Conditional cast requires another target creature or artifact")
        if type(self.announced_opponents) is not bool or (self.announced_opponents and self.same_name_scope is not None):
            raise ValueError("Conditional exile requires one closed selection scope")

    def compiled(self):
        schema=thaw_value(self.target_schema)
        scope = self.same_name_scope
        version = 2
        template = "conditional-source-exile-v1"
        identity = repr(sorted(schema.items()))
        mechanics = (LINKED_EXILE_RETURN_MECHANIC, "cr-115-targets")
        if scope is not None:
            version = SAME_NAME_CONDITIONAL_EXILE_VERSION
            template = "conditional-source-same-name-exile-v1"
            identity += ":" + scope.value
        elif self.announced_opponents:
            from .any_number_target_sets import ANY_NUMBER_TARGET_MECHANIC
            from .per_player_target_sets import PER_PLAYER_TARGET_MECHANIC

            version = ANNOUNCED_OPPONENT_EXILE_VERSION
            template = "conditional-source-announced-opponent-exile-v1"
            identity += ":announced-opponents"
            mechanics += (ANY_NUMBER_TARGET_MECHANIC, PER_PLAYER_TARGET_MECHANIC)
        if self.cast_exiled:
            version = CONDITIONAL_EXILE_CAST_VERSION
            template = "conditional-source-exile-cast-incarnation-v1"
        spec = LinkedExileReturnSpec(schema_version=version, timing="source_departure", same_name_scope=scope,
                                     announced_opponents=self.announced_opponents).to_dict()
        binding = CONDITIONAL_EXILE_CAST_BINDING if self.cast_exiled else "blink:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return template, (
            {'op':'linked_exile_return','phase':'exile','cards':'$targets','binding_id':binding,'spec':spec},
            {'op':'linked_exile_return','phase':'return','binding_id':binding,'spec':spec},
        ), schema, mechanics


@dataclass(frozen=True, slots=True)
class SelectedConditionalExileTemplate:
    scope: SelectedConditionalExileScope

    def compiled(self):
        spec = LinkedExileReturnSpec(timing="source_departure", schema_version=SELECTED_CONDITIONAL_EXILE_VERSION,
                                     selected_departure_scope=self.scope)
        effect = {"op": "choose_objects", SELECTED_CONDITIONAL_EXILE_FIELD: spec.to_dict()}
        return "selected-source-departure-exile-v1", (effect,), None, (LINKED_EXILE_RETURN_MECHANIC,)


def saga_chapter_per_player_nonsaga_exile(text):
    if re.fullmatch(
        r"For each player, exile up to one target non-Saga, nonland permanent that player controls "
        r"until this Saga leaves the battlefield[.]", text, re.I,
    ) is None:
        return None
    from ..rules.linked_exile_return_shapes import announced_nonsaga_exile_target_schema
    from .any_number_target_sets import ANY_NUMBER_TARGET_MECHANIC
    from .per_player_target_sets import PER_PLAYER_TARGET_MECHANIC

    schema = announced_nonsaga_exile_target_schema()
    _template, effects, schema, mechanics = ConditionalSourceExileTemplate(FrozenMap(schema)).compiled()
    return "conditional-source-per-player-nonsaga-exile-v1", effects, schema, (
        *mechanics, ANY_NUMBER_TARGET_MECHANIC, PER_PLAYER_TARGET_MECHANIC,
    )


def conditional_source_exile_template(text: str, *, card_name: str,
                                     source_is_permanent: bool) -> ConditionalSourceExileTemplate | SelectedConditionalExileTemplate | None:
    if source_is_permanent is not True:
        return None
    for grammar, scope in (
        (_NON_ANGEL_SELECTION, SelectedConditionalExileScope.NON_ANGEL_CREATURES),
        (_OTHER_NONLAND_SELECTION, SelectedConditionalExileScope.OTHER_NONLAND_PERMANENTS_WITH_SOLDIERS),
        (_OTHER_NONTOKEN_SELECTION, SelectedConditionalExileScope.OTHER_NONTOKEN_CREATURES),
    ):
        selection = grammar.fullmatch(text.strip())
        if selection is None:
            continue
        source = selection["source"]
        own_pronoun = scope is SelectedConditionalExileScope.OTHER_NONTOKEN_CREATURES and source.casefold() == "it"
        if own_pronoun or source_self_permanent_type(source) is not None or SourceReferenceSpec(card_name).matches(source):
            return SelectedConditionalExileTemplate(scope)
    cast = _CAST_EXILED.fullmatch(text.strip())
    if cast is not None:
        exile = conditional_source_exile_template(cast["exile"], card_name=card_name, source_is_permanent=True)
        if (isinstance(exile, ConditionalSourceExileTemplate)
                and exile.target_schema == FrozenMap(conditional_exile_cast_target_schema())):
            return ConditionalSourceExileTemplate(exile.target_schema, cast_exiled=True)
        return None
    announced = _ANNOUNCED_OPPONENTS.fullmatch(text.strip())
    match = announced or _UNTIL.fullmatch(text.strip())
    if match is None:
        return None
    if source_self_permanent_type(match['source']) is None and not SourceReferenceSpec(card_name).matches(match['source']):
        return None
    if announced is not None:
        from ..rules.linked_exile_return_shapes import announced_opponent_exile_target_schema

        return ConditionalSourceExileTemplate(FrozenMap(announced_opponent_exile_target_schema()), announced_opponents=True)
    subject = match['subject']
    if same_name := _SAME_NAME.fullmatch(subject):
        from ..rules.linked_exile_return_shapes import same_name_conditional_exile_target_schema

        scope = (SameNameConditionalExileScope.OPPONENTS if same_name["scope"].casefold() == "your opponents control"
                 else SameNameConditionalExileScope.ANCHOR_CONTROLLER)
        return ConditionalSourceExileTemplate(FrozenMap(same_name_conditional_exile_target_schema()), scope)
    cardinality = _CARDINALITY.fullmatch(subject)
    minimum = maximum = 1
    if cardinality:
        count = cardinality['count'].casefold()
        maximum = _COUNTS[count.removeprefix('up to ')]
        minimum = 0 if count.startswith('up to ') else maximum
        subject = ('another target ' if cardinality['other'] else 'target ') + _singularize_subject(cardinality['quality'])
    target = direct_permanent_target_spec(subject)
    if target is None:
        return None
    schema = target.to_target_schema()
    schema.pop('count', None)
    schema['up_to' if minimum == 0 else 'count'] = maximum
    return ConditionalSourceExileTemplate(FrozenMap(schema))
