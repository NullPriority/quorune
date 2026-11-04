from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest

from quorune.emblems import commit_emblem_creation, create_emblem, prepare_emblem_creation
from quorune.errors import GameRuleError


class EmblemCreationHostFixture:
    def __init__(self):
        self.seats = ['A', 'B', 'C', 'D']
        self.active = set(self.seats)
        self.state = SimpleNamespace(cards={}, players={
            seat: SimpleNamespace(zones={'command': []}, stats={}) for seat in self.seats})
        self.references = 0
        self.timestamps = 100
        self.events = []

    def _require_seat(self, seat, *, in_game):
        if seat not in self.seats or in_game and seat not in self.active:
            raise GameRuleError('Unknown or inactive emblem owner')

    def _next_ref(self, prefix):
        self.references += 1
        return prefix + str(self.references)

    def _stable_runtime_id(self, kind, value):
        return kind + ':' + value

    def _next_zone_timestamp(self):
        self.timestamps += 1
        return self.timestamps

    def _log(self, *args, **kwargs):
        self.events.append(deepcopy((args, kwargs)))

    def snapshot(self):
        return deepcopy({
            'cards': {key: card.to_dict() for key, card in self.state.cards.items()},
            'players': {seat: {'zones': player.zones, 'stats': player.stats}
                        for seat, player in self.state.players.items()},
            'references': self.references, 'timestamps': self.timestamps, 'events': self.events,
        })


class EmblemCreationTests(unittest.TestCase):
    def test_preparation_is_immutable_and_allocates_nothing(self):
        host = EmblemCreationHostFixture()
        before = host.snapshot()
        prepared = prepare_emblem_creation(host, 'A', abilities=['  One display ability.  '])
        self.assertEqual(('One display ability.',), prepared.abilities)
        self.assertEqual(before, host.snapshot())
        with self.assertRaises(FrozenInstanceError):
            prepared.owner = 'B'

    def test_emblem_ownership_public_command_object_and_journal(self):
        host = EmblemCreationHostFixture()
        ref = create_emblem(host, 'C', abilities=['One ability.', 'Another ability.'],
            display_label='Rules emblem', semantic_key='test:emblem', reason='creation witness')
        self.assertEqual('E1', ref)
        emblem, = host.state.cards.values()
        self.assertEqual('emblem', emblem.object_kind)
        self.assertFalse(emblem.is_card_object)
        self.assertFalse(emblem.is_token)
        self.assertEqual(('C', 'C', 'command'), (emblem.owner, emblem.controller, emblem.zone))
        self.assertEqual([emblem.object_id], host.state.players['C'].zones['command'])
        self.assertEqual(host.seats, emblem.known_to)
        self.assertEqual(host.seats, emblem.revealed_to)
        self.assertEqual({'type_line': '', 'oracle_text': 'One ability.\nAnother ability.',
                          'colors': [], 'keywords': []}, emblem.annotations['object_characteristics'])
        self.assertTrue(host.state.players['C'].stats['emblem_objects_v1'])
        args, keywords = host.events[0]
        self.assertEqual(('C', 'emblem.create', 'C created Rules emblem.'), args[:3])
        self.assertEqual('test:emblem', args[3]['semantic_key'])
        self.assertEqual([emblem.object_id], keywords['changed_objects'])
        self.assertEqual(['C'], keywords['changed_players'])

    def test_invalid_or_stale_owner_and_empty_abilities_fail_before_allocation(self):
        for owner, abilities in (('unknown', ['One ability.']), ('A', []), ('A', ['']),
                                 ('A', [' ']), ('A', ['One ability.', ' '])):
            with self.subTest(owner=owner, abilities=abilities):
                host = EmblemCreationHostFixture()
                before = host.snapshot()
                with self.assertRaises(GameRuleError):
                    create_emblem(host, owner, abilities=abilities)
                self.assertEqual(before, host.snapshot())
        host = EmblemCreationHostFixture()
        prepared = prepare_emblem_creation(host, 'A', abilities=['One ability.'])
        host.active.remove('A')
        before = host.snapshot()
        with self.assertRaises(GameRuleError):
            commit_emblem_creation(host, prepared)
        self.assertEqual(before, host.snapshot())

    def test_each_emblem_has_a_distinct_identity_and_multiple_sources_persist(self):
        host = EmblemCreationHostFixture()
        first = create_emblem(host, 'A', abilities=['One ability.'])
        second = create_emblem(host, 'A', abilities=['One ability.'])
        self.assertNotEqual(first, second)
        self.assertEqual(2, len(host.state.players['A'].zones['command']))
        self.assertEqual(2, len({card.logical_object_id for card in host.state.cards.values()}))
        self.assertEqual([101, 102], [card.zone_timestamp for card in host.state.cards.values()])

    def test_commit_omission_is_killed_by_object_and_journal_witness(self):
        from unittest.mock import patch
        with patch('quorune.emblems.commit_emblem_creation', return_value='E-omitted'):
            with self.assertRaises(AssertionError):
                self.test_emblem_ownership_public_command_object_and_journal()


class EmblemCapabilityClosureTests(unittest.TestCase):
    def test_ring_requires_creation_owner_while_unrelated_draw_remains_available(self):
        import json
        from pathlib import Path
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from test_granted_skulk import record
        root = Path(__file__).resolve().parents[1]
        registry = load_default_capability_registry()
        ring = record('The Ring tempts you.', 'Instant')
        self.assertEqual('exact', compile_oracle_card(ring, capability_registry=registry).status)
        raw = json.loads((root / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id'] == 'object.emblem.creation').update(
            status='blocked', blockers=['omitted immutable emblem creation owner'])
        changed = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(root / 'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(root).values()))
        changed.mark_evidence_verified(validate_capability_evidence_index(index, registry=changed))
        self.assertNotEqual('exact', compile_oracle_card(ring, capability_registry=changed).status)
        self.assertEqual('exact', compile_oracle_card(record('Draw a card.', 'Instant'), capability_registry=changed).status)
