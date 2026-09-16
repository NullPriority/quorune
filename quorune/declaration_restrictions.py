from __future__ import annotations

from dataclasses import dataclass
import re

from .declaration_costs import (
    normalized_oracle_line,
    parse_declaration_cost_line,
)
from .declaration_fragments import (
    ComparedStat,
    DeclarationBattlefieldCondition,
    DeclarationCombatCondition,
    DeclarationCondition,
    DeclarationConditionPlayer,
    DeclarationKind,
    DeclarationObjectPredicate,
    DeclarationPlayerStateCondition,
    DeclarationRestrictionMode,
    DeclarationRestrictionScope,
    DeclarationRestrictionTemplate,
    DeclarationSharedSubtypeCondition,
    DeclarationSourceStatCondition,
    DeclarationTurnHistoryCondition,
    DeclarationTurnHistoryFact,
    PowerOperand,
    PowerOperator,
    StatComparison,
)
from .compiler.public_state_queries import fixed_public_state_condition


_COLORS = {
    "white": "W",
    "blue": "U",
    "black": "B",
    "red": "R",
    "green": "G",
}
_ABILITY_WORD_PREFIX = re.compile(
    r"^[a-z][a-z ']+ [—-] (?P<body>.+)$"
)
_SELF_PROHIBITION = re.compile(
    r"this creature can't (?P<kind>attack|block|attack or block)\."
)
_PUBLIC_STATE_PROHIBITION = re.compile(
    r"(?P<subject>this creature|enchanted creature|enchanted permanent) "
    r"can't (?P<kind>attack|block|attack or block) "
    r"(?P<link>unless|if|as long as) (?P<condition>.+)\."
)
_PUBLIC_STATE_EVASION = re.compile(
    r"(?P<subject>this creature|enchanted creature) can't be blocked "
    r"(?P<link>unless|if|as long as) (?P<condition>.+)\."
)
_SELF_SOURCE_CONTROLLER_ATTACK = re.compile(
    r"this creature can't attack you"
    r"(?P<planeswalkers> or planeswalkers you control)?\."
)
_SOURCE_CONTROLLER_BLOCK = re.compile(
    r"(?P<subject>enchanted creature|creatures with [a-z-]+) "
    r"can't block creatures you control\."
)
_SELF_ATTACK_ALONE_PUBLIC_STATE = re.compile(
    r"this creature can't attack alone unless (?P<condition>.+)\."
)
_SELF_SOURCE_STAT_PROHIBITION = re.compile(
    r"this creature can't (?P<kind>attack|block|attack or block) unless its "
    r"(?P<stat>power|toughness) is (?P<count>\d+) or "
    r"(?P<direction>greater|less)\."
)
_SELF_COLOR_POWER_BLOCK = re.compile(
    r"this creature can't block (?P<color>white|blue|black|red|green) "
    r"creatures with power (?P<count>\d+) or (?P<direction>greater|less)\."
)
_OPPONENT_KEYWORD_POWER_BLOCK = re.compile(
    r"creatures your opponents control without (?P<keywords>[a-z-]+) or "
    r"(?P<second>[a-z-]+) can't block creatures with power "
    r"(?P<count>\d+) or (?P<direction>greater|less)\."
)
_SOURCE_POWER_CONTROLLER_BLOCK = re.compile(
    r"creatures with power less than this creature's power can't block "
    r"creatures you control\."
)
_CONTROLLER_STAT_UNBLOCKABLE = re.compile(
    r"creatures you control with (?P<stat>power|toughness) "
    r"(?P<count>\d+) or less can't be blocked\."
)
_OPPONENT_COUNTERED_PROHIBITION = re.compile(
    r"creatures your opponents control with counters on them can't "
    r"(?P<kind>attack|block|attack or block)\."
)
_ATTACHED_PROHIBITION = re.compile(
    r"enchanted (?:creature|permanent) can't "
    r"(?P<kind>attack|block|attack or block)\."
)
_GLOBAL_PROHIBITION = re.compile(
    r"creatures can't (?P<kind>attack|block)\."
)
_SELF_NOT_ALONE = re.compile(
    r"this creature can't (?P<kind>attack|block|attack or block) alone\."
)
_GLOBAL_MAXIMUM = re.compile(
    r"no more than (?P<count>one|two|three|\d+) creatures? can "
    r"(?P<kind>attack|block) each combat\."
)
_GOADED_OPPONENT_BLOCK = re.compile(
    r"goaded creatures your opponents control can't block\."
)
_KEYWORDLESS_GLOBAL_ATTACK = re.compile(
    r"creatures without (?P<keywords>[a-z][a-z -]*"
    r"(?: or [a-z][a-z -]*)*) can't attack\."
)
_SOURCE_POWER_EVASION = re.compile(
    r"creatures with power less than this creature's power can't block it\."
)
_SELF_FIXED_POWER_BLOCK = re.compile(
    r"this creature can't block creatures with power (?P<count>\d+) "
    r"or (?P<direction>greater|less)\."
)
_SELF_COLOR_BLOCK = re.compile(
    r"this creature can't block (?P<color>white|blue|black|red|green) "
    r"creatures\."
)
_SELF_UNBLOCKABLE = re.compile(r"this creature can't be blocked\.")
_SELF_BLOCKED_BY_POWER = re.compile(
    r"this creature can't be blocked by creatures with "
    r"(?P<stat>power|toughness) (?P<count>\d+) or "
    r"(?P<direction>greater|less)\."
)
_SELF_BLOCKED_BY_COLOR = re.compile(
    r"this creature can't be blocked by "
    r"(?P<color>white|blue|black|red|green) creatures\."
)
_SELF_BLOCKED_BY_SUBTYPE = re.compile(
    r"this creature can't be blocked by (?P<subtype>[a-z][a-z'-]*)s\."
)
_SELF_BLOCKED_BY_FILTER = re.compile(
    r"this creature can't be blocked by (?P<filter>[^.]+)\."
)
_ATTACHED_BLOCKED_BY_FILTER = re.compile(
    r"enchanted creature can't be blocked by (?P<filter>[^.]+)\."
)
_SELF_BLOCKED_EXCEPT_FILTER = re.compile(
    r"this creature can't be blocked except by (?P<filter>[^.]+)\."
)
_ATTACHED_BLOCKED_EXCEPT_FILTER = re.compile(
    r"enchanted creature can't be blocked except by (?P<filter>[^.]+)\."
)
_ATTACHED_UNBLOCKABLE = re.compile(
    r"enchanted creature can't be blocked\."
)
_SELF_BLOCK_FILTER = re.compile(
    r"this creature can't block (?P<filter>[^.]+)\."
)
_ATTACHED_CAN_BLOCK_ONLY_FILTER = re.compile(
    r"enchanted creature can block only (?P<filter>[^.]+)\."
)
_GLOBAL_CAN_BLOCK_ONLY_FILTER = re.compile(
    r"(?P<subject>[^.]+) can block only (?P<opposing>[^.]+)\."
)
_SELF_BLOCK_SOURCE_POWER = re.compile(
    r"this creature can't block creatures with power greater than "
    r"this creature's power\."
)
_SELF_BLOCKED_BY_GREATER_POWER = re.compile(
    r"this creature can't be blocked by creatures with greater power\."
)
_SELF_BATTLEFIELD_CONDITION = re.compile(
    r"this creature can't (?P<kind>attack|block|attack or block) "
    r"(?P<link>unless|if) "
    r"(?:(?P<source>you) control|"
    r"(?P<defender>defending player) controls) "
    r"(?P<filter>[^.]+)\."
)
_SELF_PLAYER_STATE_CONDITION = re.compile(
    r"this creature can't (?P<kind>attack|block|attack or block) unless "
    r"(?P<player>defending player|you) "
    r"(?P<verb>is|are) (?P<state>poisoned|the monarch)\."
)
_SELF_CAST_SPELL_THIS_TURN = re.compile(
    r"this creature can't attack unless you've cast a "
    r"(?P<spell_kind>creature|noncreature) spell this turn\."
)
_SELF_OPPONENT_DAMAGED_THIS_TURN = re.compile(
    r"this creature can't attack unless an opponent has been dealt damage "
    r"this turn\."
)
_SELF_CONTROLLED_CREATURE_DIED_THIS_TURN = re.compile(
    r"this creature can't attack or block unless a creature died under your "
    r"control this turn\."
)
_SELF_ALREADY_ATTACKED_PLAYER_THIS_TURN = re.compile(
    r"this creature can't attack a player it has already attacked this turn\."
)
_OPPONENT_CAST_SPELL_THIS_TURN = re.compile(
    r"each opponent who cast a spell this turn can't attack with creatures\."
)
_SELF_MONARCH_BLOCKER_CONDITION = re.compile(
    r"this creature can't be blocked by creatures the monarch controls\."
)
_GLOBAL_MONARCH_SOURCE_CONTROLLER_ATTACK = re.compile(
    r"as long as you're the monarch, creatures with power "
    r"(?P<count>\d+) or less can't attack you\."
)
_SELF_CONDITIONAL_UNBLOCKABLE = re.compile(
    r"this creature can't be blocked as long as defending player controls "
    r"(?P<condition>[^.]+)\."
)
_SELF_CONDITIONAL_BLOCKER_FILTER = re.compile(
    r"this creature can't be blocked by (?P<blockers>[^.]+) as long as "
    r"defending player controls (?P<condition>[^.]+)\."
)
_SELF_OTHER_DECLARATIONS = re.compile(
    r"this creature can't (?P<kind>attack|block) unless at least "
    r"(?P<count>one|two|three|\d+) other creatures "
    r"(?P<verb>attack|block)\."
)
_SELF_MATCHING_COMPANION = re.compile(
    r"this creature can't (?P<kind>attack|block) unless "
    r"(?P<filter>a [a-z]+ or [a-z]+ creature|a creature with greater power) "
    r"also (?P<verb>attacks|blocks)\."
)
_SELF_ATTACKING_ALONE_EVASION = re.compile(
    r"this creature can't be blocked as long as it's attacking alone\."
)
_ATTACHED_ATTACKING_ALONE_EVASION = re.compile(
    r"enchanted creature can't be blocked as long as it's attacking alone\."
)
_SELF_NO_OTHER_CREATURE_EVASION = re.compile(
    r"this creature can't be blocked as long as you control no other creatures\."
)
_GLOBAL_SOURCE_CONTROLLER_ATTACK = re.compile(
    r"creatures(?P<filter> with power \d+ or (?:greater|less)| "
    r"with [a-z-]+| without [a-z-]+)? can't attack you"
    r"(?P<planeswalkers> or planeswalkers you control)?\."
)
_GLOBAL_SOURCE_CONTROLLER_ATTACK_BLOCK = re.compile(
    r"creatures with (?P<keyword>[a-z-]+) can't attack you or block "
    r"creatures you control\."
)
_ATTACHED_SOURCE_CONTROLLER_ATTACK = re.compile(
    r"enchanted creature can't attack you"
    r"(?P<planeswalkers> or planeswalkers you control)?\."
)
_SOURCE_CONTROLLER_ATTACK_MAXIMUM = re.compile(
    r"no more than (?P<count>one|two|three|\d+) creatures? can attack "
    r"you each combat\."
)
_SOURCE_ATTACK_MAXIMUM = re.compile(
    r"no more than (?P<count>one|two|three|\d+) creatures? can attack "
    r"this creature each combat\."
)
_SELF_SHARED_SUBTYPE_BLOCK_CONDITION = re.compile(
    r"this creature can't be blocked unless defending player controls "
    r"(?P<count>one|two|three|\d+) or more creatures that share a "
    r"creature type\."
)
_SELF_BLOCKED_BY_MORE_THAN = re.compile(
    r"this creature can't be blocked by more than (?P<count>one|two|three|\d+) "
    r"creatures?\."
)
_SELF_BLOCKED_EXCEPT_COUNT = re.compile(
    r"this creature can't be blocked except by "
    r"(?P<count>one|two|three|\d+) or more creatures\."
)
_SELF_CAN_BLOCK_ONLY_KEYWORD = re.compile(
    r"this creature can block only creatures with "
    r"(?P<keyword>[a-z][a-z -]*)\."
)
_SUBTYPE_BLOCK = re.compile(
    r"(?P<blocker>[a-z][a-z'-]*)s can't block "
    r"(?P<attacker>[a-z][a-z'-]*)s\."
)
_STATIC_RESTRICTION_PREFIX = re.compile(
    r"^(?:(?:"
    r"this creature|enchanted (?:creature|permanent)|"
    r"goaded creatures|creatures|non-[a-z'-]+ creatures"
    r")[^.]*\b(?:(?:can't|cannot) (?:attack|block|be blocked)"
    r"|can (?:attack|block) only)\b"
    r"|no more than [a-z0-9]+ creatures? can (?:attack|block)\b)"
)

_FILTER_KEYWORDS = {
    "defender",
    "flying",
    "horsemanship",
    "reach",
}
_IRREGULAR_SUBTYPE_PLURALS = {
    "elves": "Elf",
    "dwarves": "Dwarf",
    "mice": "Mouse",
    "oxen": "Ox",
}


def _declarations(kind: str) -> tuple[DeclarationKind, ...]:
    return {
        "attack": ("attack",),
        "block": ("block",),
        "attack or block": ("attack", "block"),
    }[kind]


def _number(value: str) -> int:
    return {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
    }.get(value, int(value) if value.isdigit() else 0)


def _singular_subtype(value: str) -> str:
    normalized = value.strip().casefold()
    if normalized in _IRREGULAR_SUBTYPE_PLURALS:
        return _IRREGULAR_SUBTYPE_PLURALS[normalized]
    words = normalized.split()
    if not words:
        return ""
    last = words[-1]
    if last.endswith("ies") and len(last) > 3:
        last = last[:-3] + "y"
    elif last.endswith("s") and not last.endswith("ss"):
        last = last[:-1]
    words[-1] = last
    return " ".join(words).title()


@dataclass(frozen=True, slots=True)
class DeclarationRestrictionParse:
    """Exact, unresolved, or unrelated static restriction text."""

    recognized: bool
    template: DeclarationRestrictionTemplate | None = None
    reason: str | None = None
    declarations: tuple[DeclarationKind, ...] = ()
    scope: DeclarationRestrictionScope | None = None

    @property
    def exact(self) -> bool:
        return self.template is not None and self.reason is None


def _matching_creature_filter(text: str) -> DeclarationObjectPredicate | None:
    """Return the creatures named by one exact public filter phrase."""

    phrase = " ".join(text.casefold().split())
    if phrase == "artifact creatures":
        return DeclarationObjectPredicate(types_any=("Artifact",))
    if phrase == "legendary creatures":
        return DeclarationObjectPredicate(supertypes_any=("Legendary",))
    if phrase == "creature tokens":
        return DeclarationObjectPredicate(token=True)
    match = re.fullmatch(
        r"(?P<colors>white|blue|black|red|green)"
        r"(?: and/or (?P<second>white|blue|black|red|green))? creatures",
        phrase,
    )
    if match:
        colors = [match.group("colors")]
        if match.group("second"):
            colors.append(match.group("second"))
        return DeclarationObjectPredicate(
            colors_any=tuple(_COLORS[color] for color in colors)
        )
    match = re.fullmatch(
        r"creatures with (?P<keywords>[a-z-]+(?: or [a-z-]+)*)",
        phrase,
    )
    if match:
        keywords = tuple(match.group("keywords").split(" or "))
        if all(keyword in _FILTER_KEYWORDS for keyword in keywords):
            return DeclarationObjectPredicate(
                keywords_any=tuple(keyword.title() for keyword in keywords)
            )
        return None
    match = re.fullmatch(r"non-(?P<subtype>[a-z'-]+) creatures", phrase)
    if match:
        return DeclarationObjectPredicate(
            subtypes_none=(match.group("subtype").title(),)
        )
    match = re.fullmatch(
        r"creatures with (?P<stat>power|toughness) (?P<count>\d+) "
        r"or (?P<direction>greater|less)",
        phrase,
    )
    if match:
        operator: PowerOperator = (
            "ge" if match.group("direction") == "greater" else "le"
        )
        return DeclarationObjectPredicate(
            stat=StatComparison(
                match.group("stat"),
                operator,
                "fixed",
                int(match.group("count")),
            )
        )
    # Bare subtype filters use plural subtype nouns ("Humans", "Oxen", or
    # "Eldrazi Scions").  Do not interpret arbitrary prose ending in a word
    # such as "controls" as a subtype phrase.
    if {"creature", "creatures"}.intersection(phrase.split()):
        return None
    atoms = phrase.split(" or ")
    if all(
        re.fullmatch(r"[a-z][a-z'-]*(?: [a-z][a-z'-]*)?", atom)
        and (
            atom.split()[-1].endswith("s")
            or atom.split()[-1] in _IRREGULAR_SUBTYPE_PLURALS
        )
        for atom in atoms
    ):
        subtypes = tuple(_singular_subtype(value) for value in atoms)
        if all(subtypes):
            return DeclarationObjectPredicate(subtypes_any=subtypes)
    return None


def _controlled_object_atom(text: str) -> DeclarationObjectPredicate | None:
    """Parse one exact permanent-description atom after ``controls``."""

    phrase = " ".join(text.casefold().split())
    phrase = re.sub(r"^(?:a|an) ", "", phrase)
    if phrase == "permanent":
        return DeclarationObjectPredicate()
    if phrase == "enchanted permanent":
        return DeclarationObjectPredicate(enchanted=True)

    match = re.fullmatch(r"(?P<power>\d+)/(?P<toughness>\d+) creature", phrase)
    if match:
        return DeclarationObjectPredicate(
            types_any=("Creature",),
            stat=StatComparison(
                "power", "eq", "fixed", int(match.group("power"))
            ),
            additional_stats=(
                StatComparison(
                    "toughness",
                    "eq",
                    "fixed",
                    int(match.group("toughness")),
                ),
            ),
        )

    match = re.fullmatch(
        r"(?P<tapped>untapped )?creature with "
        r"(?P<stat>power|toughness) (?P<count>\d+) or "
        r"(?P<direction>greater|less)",
        phrase,
    )
    if match:
        operator: PowerOperator = (
            "ge" if match.group("direction") == "greater" else "le"
        )
        return DeclarationObjectPredicate(
            types_any=("Creature",),
            stat=StatComparison(
                match.group("stat"),
                operator,
                "fixed",
                int(match.group("count")),
            ),
            tapped=False if match.group("tapped") else None,
        )

    match = re.fullmatch(
        r"creature with (?P<keyword>[a-z][a-z-]*)", phrase
    )
    if match and match.group("keyword") in _FILTER_KEYWORDS:
        return DeclarationObjectPredicate(
            types_any=("Creature",),
            keywords_any=(match.group("keyword").title(),),
        )

    match = re.fullmatch(
        r"(?P<tapped>untapped )?(?P<supertype>snow )?"
        r"(?P<type>artifact|creature|enchantment|land)",
        phrase,
    )
    if match:
        return DeclarationObjectPredicate(
            types_any=(match.group("type").title(),),
            supertypes_any=("Snow",) if match.group("supertype") else (),
            tapped=False if match.group("tapped") else None,
        )

    match = re.fullmatch(
        r"(?P<color>white|blue|black|red|green) permanent", phrase
    )
    if match:
        return DeclarationObjectPredicate(
            colors_any=(_COLORS[match.group("color")],)
        )

    # A remaining one- or two-word noun is a subtype. Longer prose and words
    # belonging to condition grammar are rejected rather than guessed.
    if re.fullmatch(r"[a-z][a-z'-]*(?: [a-z][a-z'-]*)?", phrase) and not {
        "more",
        "most",
        "player",
        "controls",
    }.intersection(phrase.split()):
        return DeclarationObjectPredicate(subtypes_any=(phrase.title(),))
    return None


def _battlefield_condition(
    player: DeclarationConditionPlayer,
    text: str,
) -> DeclarationBattlefieldCondition | None:
    """Compile one exact battlefield-count condition from Oracle prose."""

    phrase = " ".join(text.casefold().split())
    comparison = re.fullmatch(
        r"more (?P<type>creatures|lands) than "
        r"(?P<player>defending|attacking) player",
        phrase,
    )
    if comparison:
        return DeclarationBattlefieldCondition(
            player=player,
            predicates_any=(
                DeclarationObjectPredicate(
                    types_any=(comparison.group("type")[:-1].title(),)
                ),
            ),
            compare_player=(
                "defending_player"
                if comparison.group("player") == "defending"
                else "attacking_player"
            ),
        )

    minimum = re.fullmatch(
        r"(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|\d+) "
        r"or more (?P<type>creatures|lands)",
        phrase,
    )
    if minimum:
        return DeclarationBattlefieldCondition(
            player=player,
            predicates_any=(
                DeclarationObjectPredicate(
                    types_any=(minimum.group("type")[:-1].title(),)
                ),
            ),
            minimum=_number(minimum.group("count")),
        )

    exclude_source = phrase.startswith("another ")
    if exclude_source:
        phrase = phrase.removeprefix("another ")

    # Do not split the "or greater/less" portion of a stat comparison.
    if re.search(r"\b(?:power|toughness) \d+ or (?:greater|less)$", phrase):
        atoms = (phrase,)
    else:
        atoms = tuple(
            re.sub(r"^(?:a|an) ", "", atom.strip())
            for atom in phrase.split(" or ")
        )
    predicates = tuple(
        predicate
        for atom in atoms
        if (predicate := _controlled_object_atom(atom)) is not None
    )
    if len(predicates) != len(atoms):
        return None
    return DeclarationBattlefieldCondition(
        player=player,
        predicates_any=predicates,
        exclude_source=exclude_source,
    )


def _nonmatching_creature_filter(text: str) -> DeclarationObjectPredicate | None:
    """Return creatures outside an exact allowed-blocker union."""

    phrase = " ".join(text.casefold().split())
    parts = phrase.split(" and/or ")
    predicates: list[DeclarationObjectPredicate] = []
    if len(parts) > 1:
        for part in parts:
            predicate = _matching_creature_filter(part)
            if predicate is None:
                return None
            predicates.append(predicate)
    else:
        predicate = _matching_creature_filter(phrase)
        if predicate is None:
            return None
        predicates.append(predicate)

    types_any: list[str] = []
    types_none: list[str] = []
    supertypes_any: list[str] = []
    supertypes_none: list[str] = []
    subtypes_any: list[str] = []
    subtypes_none: list[str] = []
    colors_any: list[str] = []
    colors_none: list[str] = []
    keywords_any: list[str] = []
    keywords_none: list[str] = []
    token: bool | None = None
    for predicate in predicates:
        if predicate.types_any:
            types_none.extend(predicate.types_any)
        elif predicate.types_none:
            types_any.extend(predicate.types_none)
        elif predicate.supertypes_any:
            supertypes_none.extend(predicate.supertypes_any)
        elif predicate.supertypes_none:
            supertypes_any.extend(predicate.supertypes_none)
        elif predicate.subtypes_any:
            subtypes_none.extend(predicate.subtypes_any)
        elif predicate.subtypes_none:
            subtypes_any.extend(predicate.subtypes_none)
        elif predicate.colors_any:
            colors_none.extend(predicate.colors_any)
        elif predicate.colors_none:
            colors_any.extend(predicate.colors_none)
        elif predicate.keywords_any:
            keywords_none.extend(predicate.keywords_any)
        elif predicate.keywords_none:
            keywords_any.extend(predicate.keywords_none)
        elif predicate.token is True and len(predicates) == 1:
            token = False
        else:
            return None
    return DeclarationObjectPredicate(
        types_any=tuple(types_any),
        types_none=tuple(types_none),
        supertypes_any=tuple(supertypes_any),
        supertypes_none=tuple(supertypes_none),
        subtypes_any=tuple(subtypes_any),
        subtypes_none=tuple(subtypes_none),
        colors_any=tuple(colors_any),
        colors_none=tuple(colors_none),
        keywords_any=tuple(keywords_any),
        keywords_none=tuple(keywords_none),
        token=token,
    )


def _matching_companion_filter(text: str) -> DeclarationObjectPredicate | None:
    phrase = " ".join(text.casefold().split())
    if phrase == "a creature with greater power":
        return DeclarationObjectPredicate(
            stat=StatComparison("power", "gt", "source")
        )
    match = re.fullmatch(
        r"a (?P<first>white|blue|black|red|green) or "
        r"(?P<second>white|blue|black|red|green) creature",
        phrase,
    )
    if match:
        return DeclarationObjectPredicate(
            colors_any=(
                _COLORS[match.group("first")],
                _COLORS[match.group("second")],
            )
        )
    return None


def _global_attacker_filter(
    text: str | None,
) -> DeclarationObjectPredicate | None:
    if not text:
        return DeclarationObjectPredicate()
    phrase = " ".join(text.casefold().split())
    if phrase.startswith("without "):
        keyword = phrase.removeprefix("without ")
        if keyword in _FILTER_KEYWORDS:
            return DeclarationObjectPredicate(keywords_none=(keyword.title(),))
        return None
    if phrase.startswith("with "):
        return _matching_creature_filter(f"creatures {phrase}")
    return None


def parse_declaration_restriction_line(
    text: str,
    *,
    card_name: str = "",
) -> DeclarationRestrictionParse:
    """Parse reviewed static CR 508.1c/509.1b Oracle sentence families.

    The parser is deliberately whole-line and compiler-only.
    Static-looking mutations in a recognized family become material residuals;
    triggered, activated, and resolving one-shot text is left to its own
    semantic compiler instead of being mistaken for a battlefield restriction.
    Declaration costs are owned by ``declaration_costs`` and are not duplicated.
    """

    line = normalized_oracle_line(text, card_name=card_name)
    ability_word = _ABILITY_WORD_PREFIX.fullmatch(line)
    if ability_word:
        line = ability_word.group("body")

    if parse_declaration_cost_line(line).recognized:
        return DeclarationRestrictionParse(False)

    legacy_battlefield = _SELF_BATTLEFIELD_CONDITION.fullmatch(line)
    legacy_conditional_evasion = _SELF_CONDITIONAL_UNBLOCKABLE.fullmatch(line)
    legacy_public_condition = (
        legacy_battlefield is not None
        and _battlefield_condition(
            (
                "source_controller"
                if legacy_battlefield.group("source")
                else "defending_player"
            ),
            legacy_battlefield.group("filter"),
        )
        is not None
    ) or (
        legacy_conditional_evasion is not None
        and _battlefield_condition(
            "defending_player",
            legacy_conditional_evasion.group("condition"),
        )
        is not None
    ) or any(
        pattern.fullmatch(line) is not None
        for pattern in (
            _SELF_PLAYER_STATE_CONDITION,
            _SELF_CAST_SPELL_THIS_TURN,
            _SELF_OPPONENT_DAMAGED_THIS_TURN,
            _SELF_CONTROLLED_CREATURE_DIED_THIS_TURN,
            _SELF_ALREADY_ATTACKED_PLAYER_THIS_TURN,
            _SELF_CONDITIONAL_BLOCKER_FILTER,
            _SELF_ATTACKING_ALONE_EVASION,
            _ATTACHED_ATTACKING_ALONE_EVASION,
            _SELF_NO_OTHER_CREATURE_EVASION,
        )
    )
    match = (
        None
        if legacy_public_condition
        else _PUBLIC_STATE_PROHIBITION.fullmatch(line)
    )
    if match:
        condition = fixed_public_state_condition(
            match.group("condition"),
            source_name=card_name or "this creature",
        )
        if condition is not None:
            declarations = _declarations(match.group("kind"))
            attached = match.group("subject").startswith("enchanted ")
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id=(
                        f"{'attached' if attached else 'intrinsic'}-"
                        f"public-state-{'-'.join(declarations)}-"
                        f"{match.group('link').replace(' ', '-')}-v1"
                    ),
                    declarations=declarations,
                    scope="attached" if attached else "self",
                    condition=condition,
                    applies_when_condition=(match.group("link") != "unless"),
                ),
                declarations=declarations,
                scope="attached" if attached else "self",
            )

    match = (
        None
        if legacy_public_condition
        else _PUBLIC_STATE_EVASION.fullmatch(line)
    )
    if match:
        condition = fixed_public_state_condition(
            match.group("condition"),
            source_name=card_name or "this creature",
        )
        if condition is not None:
            attached = match.group("subject") == "enchanted creature"
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id=(
                        f"{'attached' if attached else 'intrinsic'}-"
                        f"public-state-evasion-"
                        f"{match.group('link').replace(' ', '-')}-v1"
                    ),
                    declarations=("block",),
                    scope="attached_option" if attached else "source_option",
                    condition=condition,
                    applies_when_condition=(match.group("link") != "unless"),
                ),
                declarations=("block",),
                scope="attached_option" if attached else "source_option",
            )

    match = _SELF_SOURCE_CONTROLLER_ATTACK.fullmatch(line)
    if match:
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-source-controller-attack-v1",
                declarations=("attack",),
                scope="self",
                option_relation="source_controller",
                includes_planeswalkers=match.group("planeswalkers") is not None,
            ),
            declarations=("attack",),
            scope="self",
        )

    match = _SOURCE_CONTROLLER_BLOCK.fullmatch(line)
    if match:
        attached = match.group("subject") == "enchanted creature"
        subject = (
            DeclarationObjectPredicate()
            if attached
            else _matching_creature_filter(match.group("subject"))
        )
        if subject is not None:
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id=(
                        "attached-source-controller-block-v1"
                        if attached
                        else "global-source-controller-block-v1"
                    ),
                    declarations=("block",),
                    scope="attached" if attached else "global",
                    subject=subject,
                    option_relation="source_controller",
                ),
                declarations=("block",),
                scope="attached" if attached else "global",
            )

    match = _SELF_ATTACK_ALONE_PUBLIC_STATE.fullmatch(line)
    if match:
        condition = fixed_public_state_condition(
            match.group("condition"),
            source_name=card_name or "this creature",
        )
        if condition is not None:
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id="intrinsic-public-state-attack-alone-unless-v1",
                    declarations=("attack",),
                    scope="self",
                    mode="minimum_total_selections",
                    count=2,
                    condition=condition,
                    applies_when_condition=False,
                ),
                declarations=("attack",),
                scope="self",
            )

    match = _SELF_SOURCE_STAT_PROHIBITION.fullmatch(line)
    if match:
        declarations = _declarations(match.group("kind"))
        operator: PowerOperator = (
            "ge" if match.group("direction") == "greater" else "le"
        )
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-source-stat-prohibition-unless-v1",
                declarations=declarations,
                scope="self",
                condition=DeclarationSourceStatCondition(
                    stat=match.group("stat"),
                    operator=operator,
                    value=int(match.group("count")),
                ),
                applies_when_condition=False,
            ),
            declarations=declarations,
            scope="self",
        )

    match = _SELF_COLOR_POWER_BLOCK.fullmatch(line)
    if match:
        operator: PowerOperator = (
            "ge" if match.group("direction") == "greater" else "le"
        )
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-color-power-block-prohibition-v1",
                declarations=("block",),
                scope="self",
                opposing=DeclarationObjectPredicate(
                    colors_any=(_COLORS[match.group("color")],),
                    stat=StatComparison(
                        "power", operator, "fixed", int(match.group("count"))
                    ),
                ),
            ),
            declarations=("block",),
            scope="self",
        )

    match = _OPPONENT_KEYWORD_POWER_BLOCK.fullmatch(line)
    if match:
        operator: PowerOperator = (
            "ge" if match.group("direction") == "greater" else "le"
        )
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="opponent-keywordless-power-block-prohibition-v1",
                declarations=("block",),
                scope="source_opponents",
                subject=DeclarationObjectPredicate(
                    keywords_none=(
                        match.group("keywords").title(),
                        match.group("second").title(),
                    )
                ),
                opposing=DeclarationObjectPredicate(
                    stat=StatComparison(
                        "power", operator, "fixed", int(match.group("count"))
                    )
                ),
            ),
            declarations=("block",),
            scope="source_opponents",
        )

    if _SOURCE_POWER_CONTROLLER_BLOCK.fullmatch(line):
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="source-power-controller-block-prohibition-v1",
                declarations=("block",),
                scope="global",
                subject=DeclarationObjectPredicate(
                    stat=StatComparison("power", "lt", "source")
                ),
                option_relation="source_controller",
            ),
            declarations=("block",),
            scope="global",
        )

    match = _CONTROLLER_STAT_UNBLOCKABLE.fullmatch(line)
    if match:
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="controller-stat-unblockable-v1",
                declarations=("block",),
                scope="global",
                opposing=DeclarationObjectPredicate(
                    stat=StatComparison(
                        match.group("stat"),
                        "le",
                        "fixed",
                        int(match.group("count")),
                    )
                ),
                option_relation="source_controller",
            ),
            declarations=("block",),
            scope="global",
        )

    match = _OPPONENT_COUNTERED_PROHIBITION.fullmatch(line)
    if match:
        declarations = _declarations(match.group("kind"))
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="opponent-countered-declaration-prohibition-v1",
                declarations=declarations,
                scope="source_opponents",
                subject=DeclarationObjectPredicate(has_any_counter=True),
            ),
            declarations=declarations,
            scope="source_opponents",
        )

    match = _SELF_CAST_SPELL_THIS_TURN.fullmatch(line)
    if match:
        spell_kind = match.group("spell_kind")
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id=(
                    f"intrinsic-cast-{spell_kind}-spell-this-turn-"
                    "attack-unless-v1"
                ),
                declarations=("attack",),
                scope="self",
                condition=DeclarationTurnHistoryCondition(
                    fact=(
                        "cast_creature_spell"
                        if spell_kind == "creature"
                        else "cast_noncreature_spell"
                    ),
                    player="source_controller",
                ),
                applies_when_condition=False,
            ),
            declarations=("attack",),
            scope="self",
        )

    if _SELF_OPPONENT_DAMAGED_THIS_TURN.fullmatch(line):
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-opponent-damaged-this-turn-attack-unless-v1",
                declarations=("attack",),
                scope="self",
                condition=DeclarationTurnHistoryCondition(
                    fact="opponent_dealt_damage",
                    player="source_controller",
                ),
                applies_when_condition=False,
            ),
            declarations=("attack",),
            scope="self",
        )

    if _SELF_CONTROLLED_CREATURE_DIED_THIS_TURN.fullmatch(line):
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id=(
                    "intrinsic-controlled-creature-died-this-turn-"
                    "attack-block-unless-v1"
                ),
                declarations=("attack", "block"),
                scope="self",
                condition=DeclarationTurnHistoryCondition(
                    fact="creature_died_under_control",
                    player="source_controller",
                ),
                applies_when_condition=False,
            ),
            declarations=("attack", "block"),
            scope="self",
        )

    if _SELF_ALREADY_ATTACKED_PLAYER_THIS_TURN.fullmatch(line):
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-already-attacked-player-this-turn-v1",
                declarations=("attack",),
                scope="self",
                condition=DeclarationTurnHistoryCondition(
                    fact="attacked_player",
                ),
            ),
            declarations=("attack",),
            scope="self",
        )

    if _OPPONENT_CAST_SPELL_THIS_TURN.fullmatch(line):
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="source-opponents-cast-spell-this-turn-attack-v1",
                declarations=("attack",),
                scope="source_opponents",
                condition=DeclarationTurnHistoryCondition(
                    fact="cast_spell",
                    player="attacking_player",
                ),
            ),
            declarations=("attack",),
            scope="source_opponents",
        )

    match = _SELF_OTHER_DECLARATIONS.fullmatch(line)
    if match and match.group("kind") == match.group("verb"):
        declarations = _declarations(match.group("kind"))
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-other-declarations-minimum-v1",
                declarations=declarations,
                scope="self",
                mode="minimum_total_selections",
                count=_number(match.group("count")) + 1,
            ),
            declarations=declarations,
            scope="self",
        )

    match = _SELF_MATCHING_COMPANION.fullmatch(line)
    expected_verb = (
        "attacks"
        if match is not None and match.group("kind") == "attack"
        else "blocks"
    )
    if match and match.group("verb") == expected_verb:
        matching = _matching_companion_filter(match.group("filter"))
        if matching is not None:
            declarations = _declarations(match.group("kind"))
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id="intrinsic-matching-companion-minimum-v1",
                    declarations=declarations,
                    scope="self",
                    mode="minimum_matching_selections",
                    count=1,
                    matching=matching,
                ),
                declarations=declarations,
                scope="self",
            )

    if _SELF_ATTACKING_ALONE_EVASION.fullmatch(line):
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-attacking-alone-evasion-v1",
                declarations=("block",),
                scope="source_option",
                condition=DeclarationCombatCondition("attacking_alone"),
            ),
            declarations=("block",),
            scope="source_option",
        )

    if _ATTACHED_ATTACKING_ALONE_EVASION.fullmatch(line):
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="attached-attacking-alone-evasion-v1",
                declarations=("block",),
                scope="attached_option",
                condition=DeclarationCombatCondition("attacking_alone"),
            ),
            declarations=("block",),
            scope="attached_option",
        )

    if _SELF_NO_OTHER_CREATURE_EVASION.fullmatch(line):
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-no-other-creature-evasion-v1",
                declarations=("block",),
                scope="source_option",
                condition=DeclarationBattlefieldCondition(
                    player="source_controller",
                    predicates_any=(
                        DeclarationObjectPredicate(types_any=("Creature",)),
                    ),
                    minimum=0,
                    maximum=0,
                    exclude_source=True,
                ),
            ),
            declarations=("block",),
            scope="source_option",
        )

    match = _SOURCE_CONTROLLER_ATTACK_MAXIMUM.fullmatch(line)
    if match:
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="source-controller-attack-maximum-v1",
                declarations=("attack",),
                scope="global",
                mode="maximum_option_uses",
                count=_number(match.group("count")),
                option_relation="source_controller",
            ),
            declarations=("attack",),
            scope="global",
        )

    match = _SOURCE_ATTACK_MAXIMUM.fullmatch(line)
    if match:
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="source-attack-maximum-v1",
                declarations=("attack",),
                scope="source_option",
                mode="maximum_option_uses",
                count=_number(match.group("count")),
            ),
            declarations=("attack",),
            scope="source_option",
        )

    match = _SELF_SHARED_SUBTYPE_BLOCK_CONDITION.fullmatch(line)
    if match:
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id=(
                    "intrinsic-defending-player-shared-subtype-"
                    "block-unless-v1"
                ),
                declarations=("block",),
                scope="source_option",
                condition=DeclarationSharedSubtypeCondition(
                    player="defending_player",
                    minimum=_number(match.group("count")),
                ),
                applies_when_condition=False,
            ),
            declarations=("block",),
            scope="source_option",
        )

    match = _SELF_PLAYER_STATE_CONDITION.fullmatch(line)
    if match:
        expected_verb = (
            "is" if match.group("player") == "defending player" else "are"
        )
        if match.group("verb") == expected_verb:
            declarations = _declarations(match.group("kind"))
            player: DeclarationConditionPlayer = (
                "defending_player"
                if match.group("player") == "defending player"
                else "source_controller"
            )
            state: Literal["monarch", "poisoned"] = (
                "monarch"
                if match.group("state") == "the monarch"
                else "poisoned"
            )
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id=(
                        f"intrinsic-{player.replace('_', '-')}-{state}-"
                        f"{'-'.join(declarations)}-unless-v1"
                    ),
                    declarations=declarations,
                    scope="self",
                    condition=DeclarationPlayerStateCondition(
                        player=player,
                        state=state,
                    ),
                    applies_when_condition=False,
                ),
                declarations=declarations,
                scope="self",
            )

    if _SELF_MONARCH_BLOCKER_CONDITION.fullmatch(line):
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-monarch-controller-evasion-v1",
                declarations=("block",),
                scope="source_option",
                condition=DeclarationPlayerStateCondition(
                    player="defending_player",
                    state="monarch",
                ),
            ),
            declarations=("block",),
            scope="source_option",
        )

    match = _GLOBAL_MONARCH_SOURCE_CONTROLLER_ATTACK.fullmatch(line)
    if match:
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="global-monarch-source-controller-attack-v1",
                declarations=("attack",),
                scope="global",
                subject=DeclarationObjectPredicate(
                    stat=StatComparison(
                        "power", "le", "fixed", int(match.group("count"))
                    )
                ),
                condition=DeclarationPlayerStateCondition(
                    player="source_controller",
                    state="monarch",
                ),
                option_relation="source_controller",
            ),
            declarations=("attack",),
            scope="global",
        )

    match = _GLOBAL_SOURCE_CONTROLLER_ATTACK_BLOCK.fullmatch(line)
    if match and match.group("keyword") in _FILTER_KEYWORDS:
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="global-source-controller-attack-block-v1",
                declarations=("attack", "block"),
                scope="global",
                subject=DeclarationObjectPredicate(
                    keywords_any=(match.group("keyword").title(),)
                ),
                option_relation="source_controller",
            ),
            declarations=("attack", "block"),
            scope="global",
        )

    match = _GLOBAL_SOURCE_CONTROLLER_ATTACK.fullmatch(line)
    if match:
        subject = _global_attacker_filter(match.group("filter"))
        if subject is not None:
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id="global-source-controller-attack-v1",
                    declarations=("attack",),
                    scope="global",
                    subject=subject,
                    option_relation="source_controller",
                    includes_planeswalkers=bool(match.group("planeswalkers")),
                ),
                declarations=("attack",),
                scope="global",
            )

    match = _ATTACHED_SOURCE_CONTROLLER_ATTACK.fullmatch(line)
    if match:
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="attached-source-controller-attack-v1",
                declarations=("attack",),
                scope="attached",
                option_relation="source_controller",
                includes_planeswalkers=bool(match.group("planeswalkers")),
            ),
            declarations=("attack",),
            scope="attached",
        )

    match = _SELF_BATTLEFIELD_CONDITION.fullmatch(line)
    if match:
        declarations = _declarations(match.group("kind"))
        player: DeclarationConditionPlayer = (
            "source_controller"
            if match.group("source")
            else "defending_player"
        )
        condition = _battlefield_condition(player, match.group("filter"))
        if condition is not None:
            role = (
                "controller"
                if player == "source_controller"
                else "defending-player"
            )
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id=(
                        f"intrinsic-{role}-battlefield-"
                        f"{'-'.join(declarations)}-{match.group('link')}-v1"
                    ),
                    declarations=declarations,
                    scope="self",
                    condition=condition,
                    applies_when_condition=(match.group("link") == "if"),
                ),
                declarations=declarations,
                scope="self",
            )

    match = _SELF_CONDITIONAL_BLOCKER_FILTER.fullmatch(line)
    if match:
        subject = _matching_creature_filter(match.group("blockers"))
        condition = _battlefield_condition(
            "defending_player", match.group("condition")
        )
        if subject is not None and condition is not None:
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id=(
                        "intrinsic-defending-player-conditional-"
                        "blocker-filter-v1"
                    ),
                    declarations=("block",),
                    scope="source_option",
                    subject=subject,
                    condition=condition,
                ),
                declarations=("block",),
                scope="source_option",
            )

    match = _SELF_CONDITIONAL_UNBLOCKABLE.fullmatch(line)
    if match:
        condition = _battlefield_condition(
            "defending_player", match.group("condition")
        )
        if condition is not None:
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id=(
                        "intrinsic-defending-player-conditional-"
                        "unblockable-v1"
                    ),
                    declarations=("block",),
                    scope="source_option",
                    condition=condition,
                ),
                declarations=("block",),
                scope="source_option",
            )

    match = _SELF_PROHIBITION.fullmatch(line)
    if match:
        declarations = _declarations(match.group("kind"))
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id=(
                    "intrinsic-" + "-".join(declarations) + "-prohibition-v1"
                ),
                declarations=declarations,
                scope="self",
            ),
            declarations=declarations,
            scope="self",
        )

    match = _ATTACHED_PROHIBITION.fullmatch(line)
    if match:
        declarations = _declarations(match.group("kind"))
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id=(
                    "attached-" + "-".join(declarations) + "-prohibition-v1"
                ),
                declarations=declarations,
                scope="attached",
            ),
            declarations=declarations,
            scope="attached",
        )

    match = _GLOBAL_PROHIBITION.fullmatch(line)
    if match:
        declarations = _declarations(match.group("kind"))
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id=(
                    "global-" + "-".join(declarations) + "-prohibition-v1"
                ),
                declarations=declarations,
                scope="global",
            ),
            declarations=declarations,
            scope="global",
        )

    match = _SELF_NOT_ALONE.fullmatch(line)
    if match:
        declarations = _declarations(match.group("kind"))
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id=(
                    "intrinsic-" + "-".join(declarations) + "-not-alone-v1"
                ),
                declarations=declarations,
                scope="self",
                mode="minimum_total_selections",
                count=2,
            ),
            declarations=declarations,
            scope="self",
        )

    match = _GLOBAL_MAXIMUM.fullmatch(line)
    if match:
        declarations = _declarations(match.group("kind"))
        count = _number(match.group("count"))
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id=(
                    f"global-maximum-{count}-{declarations[0]}-v1"
                ),
                declarations=declarations,
                scope="global",
                mode="maximum_total_selections",
                count=count,
            ),
            declarations=declarations,
            scope="global",
        )

    if _GOADED_OPPONENT_BLOCK.fullmatch(line):
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="opponent-goaded-creature-block-prohibition-v1",
                declarations=("block",),
                scope="source_opponents",
                subject=DeclarationObjectPredicate(goaded=True),
            ),
            declarations=("block",),
            scope="source_opponents",
        )

    match = _KEYWORDLESS_GLOBAL_ATTACK.fullmatch(line)
    if match:
        keywords = tuple(
            word.strip().title()
            for word in match.group("keywords").split(" or ")
        )
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="global-keywordless-attack-prohibition-v1",
                declarations=("attack",),
                scope="global",
                subject=DeclarationObjectPredicate(keywords_none=keywords),
            ),
            declarations=("attack",),
            scope="global",
        )

    if _SOURCE_POWER_EVASION.fullmatch(line):
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="source-power-evasion-v1",
                declarations=("block",),
                scope="source_option",
                subject=DeclarationObjectPredicate(
                    stat=StatComparison("power", "lt", "source")
                ),
            ),
            declarations=("block",),
            scope="source_option",
        )

    match = _SELF_FIXED_POWER_BLOCK.fullmatch(line)
    if match:
        operator: PowerOperator = (
            "ge" if match.group("direction") == "greater" else "le"
        )
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-fixed-power-block-prohibition-v1",
                declarations=("block",),
                scope="self",
                opposing=DeclarationObjectPredicate(
                    stat=StatComparison(
                        "power",
                        operator,
                        "fixed",
                        int(match.group("count")),
                    )
                ),
            ),
            declarations=("block",),
            scope="self",
        )

    if _SELF_BLOCK_SOURCE_POWER.fullmatch(line):
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-source-power-block-prohibition-v1",
                declarations=("block",),
                scope="self",
                opposing=DeclarationObjectPredicate(
                    stat=StatComparison("power", "gt", "source")
                ),
            ),
            declarations=("block",),
            scope="self",
        )

    if _SELF_UNBLOCKABLE.fullmatch(line):
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-unblockable-v1",
                declarations=("block",),
                scope="source_option",
            ),
            declarations=("block",),
            scope="source_option",
        )

    if _ATTACHED_UNBLOCKABLE.fullmatch(line):
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="attached-unblockable-v1",
                declarations=("block",),
                scope="attached_option",
            ),
            declarations=("block",),
            scope="attached_option",
        )

    if _SELF_BLOCKED_BY_GREATER_POWER.fullmatch(line):
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-source-stat-evasion-v1",
                declarations=("block",),
                scope="source_option",
                subject=DeclarationObjectPredicate(
                    stat=StatComparison("power", "gt", "source")
                ),
            ),
            declarations=("block",),
            scope="source_option",
        )

    match = _SELF_BLOCKED_BY_POWER.fullmatch(line)
    if match:
        operator = "ge" if match.group("direction") == "greater" else "le"
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-blocker-stat-evasion-v1",
                declarations=("block",),
                scope="source_option",
                subject=DeclarationObjectPredicate(
                    stat=StatComparison(
                        match.group("stat"),
                        operator,
                        "fixed",
                        int(match.group("count")),
                    )
                ),
            ),
            declarations=("block",),
            scope="source_option",
        )

    match = _SELF_BLOCKED_BY_COLOR.fullmatch(line)
    if match:
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-blocker-color-evasion-v1",
                declarations=("block",),
                scope="source_option",
                subject=DeclarationObjectPredicate(
                    colors_any=(_COLORS[match.group("color")],)
                ),
            ),
            declarations=("block",),
            scope="source_option",
        )

    match = _SELF_BLOCKED_BY_SUBTYPE.fullmatch(line)
    if match:
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-blocker-subtype-evasion-v1",
                declarations=("block",),
                scope="source_option",
                subject=DeclarationObjectPredicate(
                    subtypes_any=(match.group("subtype").title(),)
                ),
            ),
            declarations=("block",),
            scope="source_option",
        )

    match = _SELF_BLOCKED_BY_MORE_THAN.fullmatch(line)
    if match:
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-maximum-blockers-v1",
                declarations=("block",),
                scope="source_option",
                mode="maximum_option_uses",
                count=_number(match.group("count")),
            ),
            declarations=("block",),
            scope="source_option",
        )

    match = _SELF_BLOCKED_EXCEPT_COUNT.fullmatch(line)
    if match:
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-minimum-blockers-v1",
                declarations=("block",),
                scope="source_option",
                mode="minimum_option_uses",
                count=_number(match.group("count")),
            ),
            declarations=("block",),
            scope="source_option",
        )

    match = _SELF_BLOCKED_BY_FILTER.fullmatch(line)
    if match:
        predicate = _matching_creature_filter(match.group("filter"))
        if predicate is not None:
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id="intrinsic-blocker-filter-evasion-v1",
                    declarations=("block",),
                    scope="source_option",
                    subject=predicate,
                ),
                declarations=("block",),
                scope="source_option",
            )

    match = _ATTACHED_BLOCKED_BY_FILTER.fullmatch(line)
    if match:
        predicate = _matching_creature_filter(match.group("filter"))
        if predicate is not None:
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id="attached-blocker-filter-evasion-v1",
                    declarations=("block",),
                    scope="attached_option",
                    subject=predicate,
                ),
                declarations=("block",),
                scope="attached_option",
            )

    match = _SELF_BLOCKED_EXCEPT_FILTER.fullmatch(line)
    if match:
        predicate = _nonmatching_creature_filter(match.group("filter"))
        if predicate is not None:
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id="intrinsic-allowed-blocker-filter-v1",
                    declarations=("block",),
                    scope="source_option",
                    subject=predicate,
                ),
                declarations=("block",),
                scope="source_option",
            )

    match = _ATTACHED_BLOCKED_EXCEPT_FILTER.fullmatch(line)
    if match:
        predicate = _nonmatching_creature_filter(match.group("filter"))
        if predicate is not None:
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id="attached-allowed-blocker-filter-v1",
                    declarations=("block",),
                    scope="attached_option",
                    subject=predicate,
                ),
                declarations=("block",),
                scope="attached_option",
            )

    match = _SELF_CAN_BLOCK_ONLY_KEYWORD.fullmatch(line)
    if match:
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-block-only-keyword-v1",
                declarations=("block",),
                scope="self",
                opposing=DeclarationObjectPredicate(
                    keywords_none=(match.group("keyword").title(),)
                ),
            ),
            declarations=("block",),
            scope="self",
        )

    match = _ATTACHED_CAN_BLOCK_ONLY_FILTER.fullmatch(line)
    if match:
        predicate = _nonmatching_creature_filter(match.group("filter"))
        if predicate is not None:
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id="attached-block-only-filter-v1",
                    declarations=("block",),
                    scope="attached",
                    opposing=predicate,
                ),
                declarations=("block",),
                scope="attached",
            )

    match = _SELF_COLOR_BLOCK.fullmatch(line)
    if match:
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="intrinsic-color-block-prohibition-v1",
                declarations=("block",),
                scope="self",
                opposing=DeclarationObjectPredicate(
                    colors_any=(_COLORS[match.group("color")],)
                ),
            ),
            declarations=("block",),
            scope="self",
        )

    match = _SELF_BLOCK_FILTER.fullmatch(line)
    if match:
        predicate = _matching_creature_filter(match.group("filter"))
        if predicate is not None:
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id="intrinsic-block-filter-prohibition-v1",
                    declarations=("block",),
                    scope="self",
                    opposing=predicate,
                ),
                declarations=("block",),
                scope="self",
            )

    match = _GLOBAL_CAN_BLOCK_ONLY_FILTER.fullmatch(line)
    if match and not match.group("subject").startswith(
        ("this creature", "enchanted creature")
    ):
        subject = _matching_creature_filter(match.group("subject"))
        opposing = _nonmatching_creature_filter(match.group("opposing"))
        if subject is not None and opposing is not None:
            return DeclarationRestrictionParse(
                True,
                DeclarationRestrictionTemplate(
                    template_id="global-block-only-filter-v1",
                    declarations=("block",),
                    scope="global",
                    subject=subject,
                    opposing=opposing,
                ),
                declarations=("block",),
                scope="global",
            )

    match = _SUBTYPE_BLOCK.fullmatch(line)
    if match and match.group("blocker") != "creature":
        return DeclarationRestrictionParse(
            True,
            DeclarationRestrictionTemplate(
                template_id="subtype-pair-block-prohibition-v1",
                declarations=("block",),
                scope="global",
                subject=DeclarationObjectPredicate(
                    subtypes_any=(match.group("blocker").title(),)
                ),
                opposing=DeclarationObjectPredicate(
                    subtypes_any=(match.group("attacker").title(),)
                ),
            ),
            declarations=("block",),
            scope="global",
        )

    if _STATIC_RESTRICTION_PREFIX.match(line):
        declarations: list[DeclarationKind] = []
        if "attack" in line:
            declarations.append("attack")
        if "block" in line or "be blocked" in line:
            declarations.append("block")
        scope: DeclarationRestrictionScope = (
            "attached_option"
            if line.startswith("enchanted ") and "be blocked" in line
            else "source_option"
            if "be blocked" in line or line.endswith("block it.")
            else "self"
            if line.startswith("this creature")
            else "attached"
            if line.startswith("enchanted ")
            else "source_opponents"
            if line.startswith("goaded creatures your opponents")
            else "global"
        )
        return DeclarationRestrictionParse(
            True,
            reason="static declaration restriction grammar is unresolved",
            declarations=tuple(dict.fromkeys(declarations)),
            scope=scope,
        )

    return DeclarationRestrictionParse(False)
