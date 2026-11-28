from __future__ import annotations

from dataclasses import dataclass
import re
from .declaration_fragments import (
    DECLARATION_MANA_KEYS as _MANA_KEYS,
    DeclarationCostScope,
    DeclarationCostTemplate,
    DeclarationKind,
    ENCHANTMENT_COUNT_ATTACK_TAX_ID,
)
from .rules.source_references import SourceReferenceSpec
from .util import mana_cost_to_vector


_SELF_COST = re.compile(
    r"this creature can't (?P<kind>attack|block|attack or block) unless "
    r"(?:its controller pays|you pay) "
    r"(?P<cost>(?:\{[^{}]+\})+)\."
)
_SELF_COST_PREFIX = re.compile(
    r"this creature can't (?P<kind>attack|block|attack or block) unless "
    r"(?:its controller pays|you pay)"
)
_ATTACHED_COST = re.compile(
    r"enchanted creature can't (?P<kind>attack|block|attack or block) "
    r"unless its controller pays (?P<cost>(?:\{[^{}]+\})+)\."
)
_ATTACHED_COST_PREFIX = re.compile(
    r"enchanted creature can't (?P<kind>attack|block|attack or block) "
    r"unless its controller pays"
)
_ATTACK_TAX = re.compile(
    r"(?:(?P<untapped>as long as this creature is untapped), )?"
    r"creatures can't attack you"
    r"(?P<planeswalkers> or planeswalkers you control)? unless their "
    r"controller pays (?P<cost>(?:\{[^{}]+\})+) for each "
    r"(?P<object>creature they control that's attacking you|of those creatures)\."
)
_ENCHANTMENT_COUNT_ATTACK_TAX = (
    "creatures can't attack you or planeswalkers you control unless their controller "
    "pays {x} for each of those creatures, where x is the number of enchantments you control."
)
_ATTACK_TAX_PREFIX = re.compile(
    r"(?:as long as this creature is untapped, )?"
    r"creatures can't attack you(?: or planeswalkers you control)? unless "
    r"their controller pays"
)
_PLANESWALKER_ATTACK_TAX = re.compile(
    r"creatures can't attack planeswalkers you control unless their "
    r"controller pays (?P<cost>(?:\{[^{}]+\})+) for each creature they "
    r"control that's attacking a planeswalker you control\."
)
_BLOCK_TAX = re.compile(
    r"(?:(?P<attacking>as long as this creature is attacking), )?"
    r"creatures can't block unless their controller pays "
    r"(?P<cost>(?:\{[^{}]+\})+) for each of those creatures\."
)
_BLOCK_TAX_PREFIX = re.compile(
    r"(?:as long as this creature is attacking, )?"
    r"creatures can't block unless their controller pays"
)
_ABILITY_WORD_PREFIX = re.compile(
    r"^[a-z][a-z ']+ [—-] (?P<body>.+)$"
)
_BROADER_ATTACK_COST = re.compile(
    r"^(?:as long as .+, )?(?:each |non[a-z]+ |[a-z]+ )?creatures? "
    r".*can't attack(?: .+)? unless .*(?:pay|pays)\b.*$"
)
_BROADER_BLOCK_COST = re.compile(
    r"^(?:as long as .+, )?(?:each |non[a-z]+ |[a-z]+ )?creatures? "
    r".*can't block(?: .+)? unless .*(?:pay|pays)\b.*$"
)
_BROADER_SELF_ATTACK_COST = re.compile(
    r"^this creature can't attack(?: .+)? unless .*(?:pay|pays)\b.*$"
)
_BROADER_SELF_BLOCK_COST = re.compile(
    r"^this creature can't (?:attack or )?block(?: .+)? unless "
    r".*(?:pay|pays)\b.*$"
)


def normalized_oracle_line(text: str, *, card_name: str = "") -> str:
    """Normalize one Oracle line without erasing sentence boundaries."""

    line = " ".join(str(text).casefold().split())
    if not str(card_name).strip():
        return line
    source = SourceReferenceSpec(card_name)
    line = line.replace(source.normalized_names[0], "this creature")
    # Oracle commonly shortens a legendary permanent's self-reference to its
    # leading proper name (for example, a title is omitted). Restrict this
    # normalization to the beginning of the ability sentence so an unrelated
    # named object elsewhere in the text cannot be mistaken for the source.
    leading_name = (
        source.normalized_names[1]
        if len(source.normalized_names) == 2
        else ""
    )
    shortened_suffix = (
        line[len(leading_name) + 1 :]
        if line.startswith(f"{leading_name} ")
        else ""
    )
    if (
        len(leading_name) >= 3
        and leading_name not in {"the", "a", "an"}
        and shortened_suffix.startswith(
            ("can't ", "can ", "attacks ", "blocks ", "has ", "gets ")
        )
    ):
        line = "this creature " + shortened_suffix
    return line


def fixed_declaration_mana(
    cost_text: str,
) -> tuple[tuple[str, int], ...] | None:
    """Return an ordinary fixed mana vector, or ``None`` if unresolved."""

    requirements, complex_symbols = mana_cost_to_vector(cost_text)
    if complex_symbols:
        return None
    fixed = tuple(
        (key, int(requirements.get(key, 0)))
        for key in _MANA_KEYS
        if int(requirements.get(key, 0)) > 0
    )
    return fixed or None


@dataclass(frozen=True, slots=True)
class DeclarationCostParse:
    """Result that distinguishes unrelated text from unresolved cost text."""

    recognized: bool
    template: DeclarationCostTemplate | None = None
    reason: str | None = None
    declarations: tuple[DeclarationKind, ...] = ()
    scope: DeclarationCostScope | None = None

    @property
    def exact(self) -> bool:
        return self.template is not None and self.reason is None


@dataclass(frozen=True, slots=True)
class DeclarationCost:
    """One server-derived required cost for one declaration selection."""

    cost_id: str
    variable: str
    option: str
    payer: str
    mana: tuple[tuple[str, int], ...]
    source: str | None = None
    label: str = ""

    def __post_init__(self) -> None:
        if not self.cost_id:
            raise ValueError("Declaration cost id is required")
        if not self.variable or not self.option or not self.payer:
            raise ValueError(
                "Declaration cost selection and payer are required"
            )
        allowed = set(_MANA_KEYS)
        seen: set[str] = set()
        for raw_key, raw_amount in self.mana:
            key = str(raw_key).upper()
            amount = int(raw_amount)
            if key not in allowed or key in seen or amount <= 0:
                raise ValueError(
                    "Declaration mana costs must be unique positive "
                    "requirements"
                )
            seen.add(key)

    @property
    def selection(self) -> tuple[str, str]:
        return self.variable, self.option

    def mana_requirements(self) -> dict[str, int]:
        requirements = {key: 0 for key in _MANA_KEYS}
        for key, amount in self.mana:
            requirements[str(key).upper()] = int(amount)
        return requirements

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.cost_id,
            "variable": self.variable,
            "option": self.option,
            "payer": self.payer,
            "kind": "mana",
            "mana": dict(self.mana),
            "source": self.source,
            "label": self.label,
        }


def _parse_source_controller_attack_cost(line: str) -> DeclarationCostParse | None:
    if line == _ENCHANTMENT_COUNT_ATTACK_TAX:
        return DeclarationCostParse(
            True,
            DeclarationCostTemplate(
                template_id=ENCHANTMENT_COUNT_ATTACK_TAX_ID,
                declarations=("attack",),
                scope="source_controller",
                mana=(),
                printed_cost="{x}",
                includes_planeswalkers=True,
                mana_calculation="source_controller_enchantment_count",
            ),
            declarations=("attack",),
            scope="source_controller",
        )

    match = _ATTACK_TAX.fullmatch(line)
    if match:
        expected_object = (
            "of those creatures"
            if match.group("planeswalkers")
            else "creature they control that's attacking you"
        )
        if match.group("object") != expected_object:
            return DeclarationCostParse(
                True,
                reason="attack tax object binding is unresolved",
                declarations=("attack",),
                scope="source_controller",
            )
        mana = fixed_declaration_mana(match.group("cost"))
        if mana is None:
            return DeclarationCostParse(
                True,
                reason="attack tax uses non-fixed mana symbols",
                declarations=("attack",),
                scope="source_controller",
            )
        return DeclarationCostParse(
            True,
            DeclarationCostTemplate(
                template_id=(
                    "source-controller-fixed-mana-attack-tax-"
                    + (
                        "player-planeswalker"
                        if match.group("planeswalkers")
                        else "player"
                    )
                    + "-v1"
                ),
                declarations=("attack",),
                scope="source_controller",
                mana=mana,
                printed_cost=match.group("cost"),
                source_condition=(
                    "source_untapped" if match.group("untapped") else None
                ),
                includes_planeswalkers=bool(match.group("planeswalkers")),
            ),
            declarations=("attack",),
            scope="source_controller",
        )
    if _ATTACK_TAX_PREFIX.match(line):
        return DeclarationCostParse(
            True,
            reason="attack tax grammar is unresolved",
            declarations=("attack",),
            scope="source_controller",
        )

    match = _PLANESWALKER_ATTACK_TAX.fullmatch(line)
    if match:
        mana = fixed_declaration_mana(match.group("cost"))
        if mana is None:
            return DeclarationCostParse(
                True,
                reason="planeswalker attack tax uses non-fixed mana symbols",
                declarations=("attack",),
                scope="source_planeswalkers",
            )
        return DeclarationCostParse(
            True,
            DeclarationCostTemplate(
                template_id=(
                    "source-controller-fixed-mana-attack-tax-"
                    "planeswalker-v1"
                ),
                declarations=("attack",),
                scope="source_planeswalkers",
                mana=mana,
                printed_cost=match.group("cost"),
                includes_planeswalkers=True,
            ),
            declarations=("attack",),
            scope="source_planeswalkers",
        )
    return None


def parse_declaration_cost_line(
    text: str,
    *,
    card_name: str = "",
) -> DeclarationCostParse:
    """Parse the reviewed attack and block cost sentence families.

    Prefix matches are deliberately reported as unresolved. This keeps a new
    or mutated member of the family from silently losing a mandatory cost.
    """

    line = normalized_oracle_line(text, card_name=card_name)
    ability_word = _ABILITY_WORD_PREFIX.fullmatch(line)
    if ability_word:
        line = ability_word.group("body")
    match = _SELF_COST.fullmatch(line)
    if match:
        kinds = {
            "attack": ("attack",),
            "block": ("block",),
            "attack or block": ("attack", "block"),
        }[match.group("kind")]
        mana = fixed_declaration_mana(match.group("cost"))
        if mana is None:
            return DeclarationCostParse(
                True,
                reason="declaration cost uses non-fixed mana symbols",
                declarations=kinds,
                scope="self",
            )
        return DeclarationCostParse(
            True,
            DeclarationCostTemplate(
                template_id=(
                    "intrinsic-fixed-mana-"
                    + "-".join(kinds)
                    + "-cost-v1"
                ),
                declarations=kinds,
                scope="self",
                mana=mana,
                printed_cost=match.group("cost"),
            ),
            declarations=kinds,
            scope="self",
        )
    prefix = _SELF_COST_PREFIX.match(line)
    if prefix:
        kinds = {
            "attack": ("attack",),
            "block": ("block",),
            "attack or block": ("attack", "block"),
        }[prefix.group("kind")]
        return DeclarationCostParse(
            True,
            reason="intrinsic declaration cost grammar is unresolved",
            declarations=kinds,
            scope="self",
        )

    match = _ATTACHED_COST.fullmatch(line)
    if match:
        kinds = {
            "attack": ("attack",),
            "block": ("block",),
            "attack or block": ("attack", "block"),
        }[match.group("kind")]
        mana = fixed_declaration_mana(match.group("cost"))
        if mana is None:
            return DeclarationCostParse(
                True,
                reason="attached declaration cost uses non-fixed mana symbols",
                declarations=kinds,
                scope="attached",
            )
        return DeclarationCostParse(
            True,
            DeclarationCostTemplate(
                template_id=(
                    "attached-fixed-mana-"
                    + "-".join(kinds)
                    + "-cost-v1"
                ),
                declarations=kinds,
                scope="attached",
                mana=mana,
                printed_cost=match.group("cost"),
            ),
            declarations=kinds,
            scope="attached",
        )
    prefix = _ATTACHED_COST_PREFIX.match(line)
    if prefix:
        kinds = {
            "attack": ("attack",),
            "block": ("block",),
            "attack or block": ("attack", "block"),
        }[prefix.group("kind")]
        return DeclarationCostParse(
            True,
            reason="attached declaration cost grammar is unresolved",
            declarations=kinds,
            scope="attached",
        )

    if attack_tax := _parse_source_controller_attack_cost(line):
        return attack_tax

    match = _BLOCK_TAX.fullmatch(line)
    if match:
        mana = fixed_declaration_mana(match.group("cost"))
        if mana is None:
            return DeclarationCostParse(
                True,
                reason="block tax uses non-fixed mana symbols",
                declarations=("block",),
                scope="global",
            )
        return DeclarationCostParse(
            True,
            DeclarationCostTemplate(
                template_id="global-fixed-mana-block-tax-v1",
                declarations=("block",),
                scope="global",
                mana=mana,
                printed_cost=match.group("cost"),
                source_condition=(
                    "source_attacking" if match.group("attacking") else None
                ),
            ),
            declarations=("block",),
            scope="global",
        )
    if _BLOCK_TAX_PREFIX.match(line):
        return DeclarationCostParse(
            True,
            reason="block tax grammar is unresolved",
            declarations=("block",),
            scope="global",
        )
    if _BROADER_SELF_ATTACK_COST.fullmatch(line):
        return DeclarationCostParse(
            True,
            reason="intrinsic conditional attack cost grammar is unresolved",
            declarations=("attack",),
            scope="self",
        )
    if _BROADER_SELF_BLOCK_COST.fullmatch(line):
        declarations: tuple[DeclarationKind, ...] = (
            ("attack", "block")
            if "attack or block" in line
            else ("block",)
        )
        return DeclarationCostParse(
            True,
            reason="intrinsic conditional block cost grammar is unresolved",
            declarations=declarations,
            scope="self",
        )
    if _BROADER_ATTACK_COST.fullmatch(line):
        scope: DeclarationCostScope = (
            "source_planeswalkers"
            if "can't attack planeswalkers you control" in line
            and "can't attack you" not in line
            else "source_controller"
        )
        return DeclarationCostParse(
            True,
            reason="conditional attack tax grammar is unresolved",
            declarations=("attack",),
            scope=scope,
        )
    if _BROADER_BLOCK_COST.fullmatch(line):
        return DeclarationCostParse(
            True,
            reason="conditional block tax grammar is unresolved",
            declarations=("block",),
            scope="global",
        )
    return DeclarationCostParse(False)
