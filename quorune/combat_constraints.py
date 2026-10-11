from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Iterable, Literal, Mapping, Sequence

DeclarationSelection = str | tuple[str,...]


def selected_options(value):
    return value if isinstance(value, tuple) else (value,) if isinstance(value, str) else ()


RequirementKind = Literal[
    "choose",
    "choose_option",
    "choose_option_in",
    "option_used",
]
RestrictionKind = Literal[
    "minimum_variable_selections",
    "minimum_option_uses",
    "maximum_option_uses",
    "minimum_total_selections",
    "maximum_total_selections",
]


class DeclarationConstraintError(ValueError):
    """A submitted combat declaration is outside its issued problem."""


class DeclarationSearchLimitError(RuntimeError):
    """Exact requirement maximization exceeded its deterministic limit."""


@dataclass(frozen=True, slots=True)
class DeclarationRequirement:
    requirement_id: str
    kind: RequirementKind
    variable: str | None = None
    option: str | None = None
    options: tuple[str, ...] = ()
    label: str = ""

    def satisfied_by(self, declaration: Mapping[str, DeclarationSelection]) -> bool:
        if self.kind == "choose":
            return self.variable in declaration
        if self.kind == "choose_option":
            return self.option in selected_options(declaration.get(str(self.variable)))
        if self.kind == "choose_option_in":
            return any(option in self.options for option in selected_options(declaration.get(str(self.variable))))
        if self.kind == "option_used":
            return any(self.option in selected_options(value) for value in declaration.values())
        raise DeclarationConstraintError(
            f"Unknown declaration requirement {self.kind!r}"
        )

    def to_dict(self) -> dict[str, str | list[str] | None]:
        return {
            "id": self.requirement_id,
            "kind": self.kind,
            "variable": self.variable,
            "option": self.option,
            "options": list(self.options),
            "label": self.label,
        }


@dataclass(frozen=True, slots=True)
class DeclarationRestriction:
    restriction_id: str
    kind: RestrictionKind
    option: str | None = None
    count: int = 0
    when_used: bool = False
    trigger_variable: str | None = None
    variables: tuple[str, ...] = ()
    label: str = ""

    def __post_init__(self) -> None:
        if not self.restriction_id:
            raise ValueError("Declaration restriction id is required")
        if self.count < 0:
            raise ValueError("Declaration restriction count cannot be negative")
        option_kinds = {"minimum_option_uses", "maximum_option_uses"}
        total_kinds = {
            "minimum_total_selections",
            "maximum_total_selections",
        }
        if self.kind in option_kinds and not self.option:
            raise ValueError(f"{self.kind} requires an option")
        if self.kind in total_kinds and self.option is not None:
            raise ValueError(f"{self.kind} does not accept an option")
        if (
            self.kind == "minimum_variable_selections"
            and self.option is not None
        ):
            raise ValueError(
                "minimum_variable_selections does not accept an option"
            )
        if len(set(self.variables)) != len(self.variables):
            raise ValueError("Declaration restriction variables must be unique")
        if self.kind != "minimum_variable_selections" and self.variables:
            raise ValueError(f"{self.kind} does not accept variables")

    def error(self, declaration: Mapping[str, DeclarationSelection]) -> str | None:
        if (
            self.trigger_variable is not None
            and self.trigger_variable not in declaration
        ):
            return None
        uses = sum(
            1
            for selected in declaration.values()
            if self.option is not None and self.option in selected_options(selected)
        )
        total = len(declaration)
        variable_uses = sum(
            1 for variable in self.variables if variable in declaration
        )
        measured = (
            variable_uses
            if self.kind == "minimum_variable_selections"
            else uses
            if self.kind in {"minimum_option_uses", "maximum_option_uses"}
            else total
        )
        if self.when_used and measured == 0:
            return None
        if self.kind == "minimum_option_uses" and measured < self.count:
            return self.label or (
                f"{self.option} requires at least {self.count} selections"
            )
        if (
            self.kind == "minimum_variable_selections"
            and measured < self.count
        ):
            return self.label or (
                f"The declaration requires at least {self.count} matching "
                "selections"
            )
        if self.kind == "maximum_option_uses" and measured > self.count:
            return self.label or (
                f"{self.option} allows at most {self.count} selections"
            )
        if self.kind == "minimum_total_selections" and measured < self.count:
            return self.label or (
                f"The declaration requires at least {self.count} selections"
            )
        if self.kind == "maximum_total_selections" and measured > self.count:
            return self.label or (
                f"The declaration allows at most {self.count} selections"
            )
        return None

    def to_dict(self) -> dict[str, str | int | bool | list[str] | None]:
        result: dict[str, str | int | bool | list[str] | None] = {
            "id": self.restriction_id,
            "kind": self.kind,
            "option": self.option,
            "count": self.count,
            "when_used": self.when_used,
            "trigger_variable": self.trigger_variable,
            "label": self.label,
        }
        if self.kind == "minimum_variable_selections":
            result["variables"] = list(self.variables)
        return result


@dataclass(frozen=True, slots=True)
class DeclarationEvaluation:
    satisfied: tuple[str, ...]
    unmet: tuple[str, ...]
    maximum: int
    restriction_errors: tuple[str, ...]

    @property
    def legal(self) -> bool:
        return not self.restriction_errors and len(self.satisfied) == self.maximum


@dataclass(frozen=True, slots=True)
class DeclarationProblem:
    """A finite combat declaration problem under CR 508.1d or 509.1c.

    Each variable may be omitted or select distinct options up to its declared
    capacity, which defaults to one.
    Restrictions are inviolable. Among declarations that obey them, a legal
    declaration must satisfy the greatest possible number of requirements.
    Costed options do not contribute to the free maximum unless the submitted
    declaration elects that exact option.
    """

    domains: Mapping[str, Sequence[str]]
    requirements: tuple[DeclarationRequirement, ...] = ()
    restrictions: tuple[DeclarationRestriction, ...] = ()
    costed_options: frozenset[tuple[str, str]] = frozenset()
    max_search_states: int = 200_000
    capacities: Mapping[str, int] | None = None

    def __post_init__(self) -> None:
        if self.max_search_states < 1:
            raise ValueError("max_search_states must be positive")
        if self.capacities is not None and any(
            variable not in self.domains or type(capacity) is not int or capacity < 1
            for variable,capacity in self.capacities.items()):
            raise ValueError('Declaration capacities require positive exact counts for eligible objects')
        if len({item.requirement_id for item in self.requirements}) != len(
            self.requirements
        ):
            raise ValueError("Declaration requirement ids must be unique")
        if len({item.restriction_id for item in self.restrictions}) != len(
            self.restrictions
        ):
            raise ValueError("Declaration restriction ids must be unique")
        for variable, option in self.costed_options:
            if (
                variable not in self.domains
                or option not in self.domains[variable]
            ):
                raise ValueError(
                    "Every costed declaration option must belong to its domain"
                )

    def canonical_declaration(
        self,
        declaration: Mapping[str, DeclarationSelection],
    ) -> dict[str, DeclarationSelection]:
        if not isinstance(declaration, Mapping):
            raise DeclarationConstraintError(
                "Combat declaration must be a mapping"
            )
        canonical: dict[str, DeclarationSelection] = {}
        for raw_variable, raw_option in declaration.items():
            variable = str(raw_variable)
            if variable not in self.domains:
                raise DeclarationConstraintError(
                    f"{variable} is not an eligible declaration object"
                )
            options = tuple(raw_option) if isinstance(raw_option,(list,tuple)) else (raw_option,)
            if (not options or any(type(option) is not str or option not in self.domains[variable] for option in options)
                    or len(options)!=len(set(options))):
                raise DeclarationConstraintError(
                    f"Distinct eligible options are required for {variable}"
                )
            capacity=(self.capacities or {}).get(variable,1)
            if len(options)>capacity:
                raise DeclarationConstraintError(f'{variable} can select at most {capacity} options')
            canonical[variable] = options[0] if len(options)==1 else tuple(sorted(options))
        return canonical

    def restriction_errors(
        self,
        declaration: Mapping[str, DeclarationSelection],
    ) -> tuple[str, ...]:
        return tuple(
            error
            for restriction in self.restrictions
            if (error := restriction.error(declaration)) is not None
        )

    def satisfied_requirement_ids(
        self,
        declaration: Mapping[str, DeclarationSelection],
    ) -> tuple[str, ...]:
        return tuple(
            requirement.requirement_id
            for requirement in self.requirements
            if requirement.satisfied_by(declaration)
        )

    def maximum_satisfied_requirements(
        self,
        *,
        enabled_costed_options: Iterable[tuple[str, str]] = (),
    ) -> int:
        if not self.requirements:
            return 0
        enabled = frozenset(
            (str(variable), str(option))
            for variable, option in enabled_costed_options
        )
        if not enabled.issubset(self.costed_options):
            raise DeclarationConstraintError(
                "Only represented declaration costs may be enabled"
            )
        variables = sorted(self.domains)
        domains = {
            variable: tuple(
                option
                for option in dict.fromkeys(self.domains[variable])
                if (variable, option) not in self.costed_options
                or (variable, option) in enabled
            )
            for variable in variables
        }
        best = 0
        states = 0
        declaration: dict[str, DeclarationSelection] = {}

        def search(index: int) -> bool:
            nonlocal best, states
            states += 1
            if states > self.max_search_states:
                raise DeclarationSearchLimitError(
                    "Exact combat requirement maximization exceeded "
                    f"{self.max_search_states} states"
                )
            if index == len(variables):
                if self.restriction_errors(declaration):
                    return False
                best = max(
                    best,
                    len(self.satisfied_requirement_ids(declaration)),
                )
                return best == len(self.requirements)

            variable = variables[index]
            # Requirement-satisfying branches occur before omission. This is
            # deterministic and usually proves the theoretical maximum early.
            capacity=min((self.capacities or {}).get(variable,1),len(domains[variable]))
            for count in range(1,capacity+1):
                for options in combinations(domains[variable],count):
                    declaration[variable] = options[0] if count==1 else options
                    if search(index + 1):
                        return True
            declaration.pop(variable, None)
            return search(index + 1)

        search(0)
        return best

    def evaluate(
        self,
        declaration: Mapping[str, DeclarationSelection],
    ) -> DeclarationEvaluation:
        canonical = self.canonical_declaration(declaration)
        satisfied = self.satisfied_requirement_ids(canonical)
        satisfied_set = set(satisfied)
        enabled_costed_options = frozenset(
            (variable,option)
            for variable,value in canonical.items()
            for option in selected_options(value)
            if (variable,option) in self.costed_options
        )
        return DeclarationEvaluation(
            satisfied=satisfied,
            unmet=tuple(
                item.requirement_id
                for item in self.requirements
                if item.requirement_id not in satisfied_set
            ),
            maximum=self.maximum_satisfied_requirements(
                enabled_costed_options=enabled_costed_options,
            ),
            restriction_errors=self.restriction_errors(canonical),
        )

    def projection(self) -> dict[str, object]:
        result = {
            "domains": {
                variable: list(dict.fromkeys(self.domains[variable]))
                for variable in sorted(self.domains)
            },
            "requirements": [item.to_dict() for item in self.requirements],
            "restrictions": [item.to_dict() for item in self.restrictions],
            "maximum_requirements": self.maximum_satisfied_requirements(),
            "costed_options": [
                {"variable": variable, "option": option}
                for variable, option in sorted(self.costed_options)
            ],
        }
        if self.capacities and any(value>1 for value in self.capacities.values()):
            result['capacities']={variable:self.capacities.get(variable,1) for variable in sorted(self.domains)}
        return result
