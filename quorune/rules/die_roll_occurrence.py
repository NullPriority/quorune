from __future__ import annotations

"""Plan retained dice samples without changing the original instruction key."""

from dataclasses import dataclass

from .random_selection import roll_fixed_die


@dataclass(frozen=True, slots=True)
class FixedDieRollOccurrence:
    seat: str
    instruction_key: str
    sides: int
    samples: tuple[int, ...]
    ignored_indices: tuple[int, ...]
    results: tuple[int, ...]

    def __post_init__(self) -> None:
        if (type(self.seat) is not str or not self.seat
                or type(self.instruction_key) is not str or not self.instruction_key
                or type(self.sides) is not int or self.sides < 2
                or not isinstance(self.samples, tuple) or not self.samples
                or any(type(value) is not int or not 1 <= value <= self.sides for value in self.samples)
                or not isinstance(self.ignored_indices, tuple)
                or any(type(index) is not int or not 0 <= index < len(self.samples) for index in self.ignored_indices)
                or self.ignored_indices != tuple(sorted(set(self.ignored_indices)))
                or not isinstance(self.results, tuple) or not self.results
                or len(self.results) != len(self.samples) - len(self.ignored_indices)
                or any(type(value) is not int for value in self.results)):
            raise ValueError("A die roll requires exact sample, retention, and result identities")

    @property
    def retained_natural_results(self) -> tuple[int, ...]:
        return tuple(value for index, value in enumerate(self.samples) if index not in self.ignored_indices)

    @property
    def ignored_natural_results(self) -> tuple[int, ...]:
        return tuple(self.samples[index] for index in self.ignored_indices)

    @property
    def single_result(self) -> int:
        if len(self.results) != 1:
            raise ValueError("This instruction requires one retained die result")
        return self.results[0]


def plan_fixed_die_roll(sides: int, *, seed, instruction_key: str, seat: str,
                        rolls: int = 1, extra_rolls: int = 0,
                        ignore_lowest: int = 0) -> FixedDieRollOccurrence:
    """Sample the original die at its legacy key, then add keyed samples when required."""
    if (type(rolls) is not int or rolls < 1 or type(extra_rolls) is not int or extra_rolls < 0
            or type(ignore_lowest) is not int or not 0 <= ignore_lowest < rolls + extra_rolls):
        raise ValueError("A planned die instruction needs at least one retained sample")
    keys = (instruction_key, *(f"{instruction_key}|die:{index + 1}" for index in range(1, rolls + extra_rolls)))
    samples = tuple(roll_fixed_die(sides, seed=seed, instruction_key=key, seat=seat) for key in keys)
    by_value = sorted(range(len(samples)), key=lambda index: (samples[index], index))
    ignored = tuple(sorted(by_value[:ignore_lowest]))
    results = tuple(value for index, value in enumerate(samples) if index not in ignored)
    return FixedDieRollOccurrence(seat, instruction_key, sides, samples, ignored, results)


def roll_game_die(host, item, *, sides: int, instruction_pointer: int) -> FixedDieRollOccurrence:
    """Resolve and announce one die through the current game’s instruction owner."""
    if type(instruction_pointer) is not int or instruction_pointer < 0:
        raise ValueError("A game die requires a nonnegative resolving instruction pointer")
    actor = item.controller
    occurrence = plan_fixed_die_roll(
        sides, seed=host.state.config.seed, instruction_key=f"{item.ref}|{instruction_pointer}", seat=actor,
    )
    result = occurrence.single_result
    host._log(
        actor, "die.roll", f"{actor} rolled {result} on a d{sides}.",
        {"player": actor, "sides": sides, "result": result, "stack": item.ref},
        importance=2,
    )
    return occurrence
