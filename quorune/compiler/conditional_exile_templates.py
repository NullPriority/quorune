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
    SAME_NAME_CONDITIONAL_EXILE_VERSION,
    SameNameConditionalExileScope,
)
from ..replacement.immutable import thaw_value


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


@dataclass(frozen=True, slots=True)
class ConditionalSourceExileTemplate:
    target_schema: FrozenMap
    same_name_scope: SameNameConditionalExileScope | None = None
    announced_opponents: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.target_schema, FrozenMap):
            raise ValueError('Conditional exile target data must be immutable')
        if self.same_name_scope is not None and not isinstance(self.same_name_scope, SameNameConditionalExileScope):
            raise ValueError("Conditional exile requires a typed same-name scope")
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
        spec = LinkedExileReturnSpec(schema_version=version, timing="source_departure", same_name_scope=scope,
                                     announced_opponents=self.announced_opponents).to_dict()
        binding = "blink:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        return template, (
            {'op':'linked_exile_return','phase':'exile','cards':'$targets','binding_id':binding,'spec':spec},
            {'op':'linked_exile_return','phase':'return','binding_id':binding,'spec':spec},
        ), schema, mechanics


def conditional_source_exile_template(text: str, *, card_name: str,
                                     source_is_permanent: bool) -> ConditionalSourceExileTemplate | None:
    if source_is_permanent is not True:
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
