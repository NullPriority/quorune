from __future__ import annotations

"""Instruction-keyed sampling of distinct physical identities."""

import random


def sample_identity_rows(rows, count, *, seed, instruction_key, namespace, seat):
    if type(count) is not int or count < 0:
        raise ValueError('Random selection count must be a nonnegative integer')
    members = sorted(rows,key=lambda row:row['object_id'])
    identities = [row['object_id'] for row in members]
    if any(type(oid) is not str or not oid for oid in identities) or len(identities)!=len(set(identities)):
        raise ValueError('Random selection requires distinct physical identities')
    randomizer = random.Random(f'{seed}|{instruction_key}|{namespace}|{seat}')
    return tuple(randomizer.sample(members,min(count,len(members))))
