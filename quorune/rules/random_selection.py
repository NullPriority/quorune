from __future__ import annotations

"""Instruction-keyed sampling of distinct physical identities."""

import random


def _instruction_randomizer(*, seed, instruction_key, namespace, seat):
    return random.Random(f'{seed}|{instruction_key}|{namespace}|{seat}')


def roll_fixed_die(sides: int, *, seed, instruction_key: str, seat: str) -> int:
    if type(sides) is not int or sides < 2:
        raise ValueError("A fixed die requires at least two sides")
    return _instruction_randomizer(
        seed=seed, instruction_key=instruction_key, namespace="die-roll", seat=seat,
    ).randint(1, sides)


def flip_fixed_coin(*, seed, instruction_key: str, seat: str) -> bool:
    return bool(_instruction_randomizer(
        seed=seed, instruction_key=instruction_key, namespace="coin-flip", seat=seat,
    ).getrandbits(1))


def publish_resolution_die_roll(
    host, *, controller: str, stack_ref: str, instruction_pointer: int, sides: int, roll_ordinal: int = 1,
) -> int:
    """Publish a die face for one resolving instruction and its resumption step."""
    if (
        type(stack_ref) is not str or not stack_ref or type(controller) is not str or not controller
        or type(instruction_pointer) is not int or instruction_pointer < 0
        or type(roll_ordinal) is not int or roll_ordinal < 1
    ):
        raise ValueError("A resolution die roll requires its stack, controller and positive roll ordinal")
    key = f"{stack_ref}|{instruction_pointer}"
    if roll_ordinal != 1:
        key += f"|roll:{roll_ordinal}"
    face = roll_fixed_die(sides, seed=host.state.config.seed, instruction_key=key, seat=controller)
    host._log(
        controller, "die.roll", f"{controller} rolled {face} on a d{sides}.",
        {"player": controller, "sides": sides, "result": face, "stack": stack_ref},
        importance=2,
    )
    return face

def choose_random_integer(minimum: int, maximum: int, *, seed, instruction_key: str, seat: str) -> int:
    if type(minimum) is not int or type(maximum) is not int or minimum >= maximum:
        raise ValueError("A random integer interval needs two ordered integer bounds")
    return _instruction_randomizer(
        seed=seed, instruction_key=instruction_key, namespace="integer-selection", seat=seat,
    ).randint(minimum, maximum)


def sample_identity_rows(rows, count, *, seed, instruction_key, namespace, seat):
    if type(count) is not int or count < 0:
        raise ValueError('Random selection count must be a nonnegative integer')
    members = sorted(rows,key=lambda row:row['object_id'])
    identities = [row['object_id'] for row in members]
    if any(type(oid) is not str or not oid for oid in identities) or len(identities)!=len(set(identities)):
        raise ValueError('Random selection requires distinct physical identities')
    randomizer = _instruction_randomizer(
        seed=seed, instruction_key=instruction_key, namespace=namespace, seat=seat,
    )
    return tuple(randomizer.sample(members,min(count,len(members))))
