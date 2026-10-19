"""Pinned CR 702.182a /207.2d: Hero attachment and decorative Equip labels."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_fixed_copy_type_exceptions as controls
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


EXPECTED = {"Monk's Fist", "Paladin's Arms", "Bard's Bow", "Samurai's Katana",
            "Warrior's Sword", "Dragoon's Lance", "Machinist's Arsenal",
            "Thief's Knife", "Black Mage's Rod", "Sage's Nouliths",
            "White Mage's Staff", "Red Mage's Rapier"}


class JobSelectModelTests(unittest.TestCase):
    def test_hero_profile_source_span_and_flavored_equip_keep_closed_values(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.rules.attachment_actions import fixed_equip_ability_spec
        text = 'Job select\nNamed Tool — Equip {2}'
        card = replace(query_record(text), type_line='Artifact — Equipment',
            keywords=('Job select', 'Equip'))
        ir = compile_oracle_card(card, capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status)
        effect = ir.faces[0].nodes[0].effects[0]
        self.assertEqual('create_attached_token', effect['op'])
        self.assertEqual('Hero', effect['name'])
        self.assertEqual(('1', '1', []), tuple(effect['characteristics'][key] for key in ('power', 'toughness', 'colors')))
        self.assertEqual('Named Tool — Equip {2}', ir.faces[0].nodes[-1].text)
        spec = fixed_equip_ability_spec('Named Tool — Equip {2}')
        self.assertIsNotNone(spec)
        self.assertEqual('{2}', spec.cost_text)
        for line in ('Named Tool — Equip {X}', 'Named Tool — Equip {2} and draw a card.',
                     'Named Tool — Equip {2}. Activate only during combat.'):
            self.assertIsNone(fixed_equip_ability_spec(line))

    def test_keyword_dependencies_and_unknown_keyword_tails_fail_closed(self):
        from copy import deepcopy
        from test_qualified_zone_event_queries import query_record
        from quorune.rules.capabilities import CapabilityRegistry
        card = replace(query_record('Job select'), type_line='Artifact — Equipment', keywords=('Job select',))
        caps = load_default_capability_registry()
        self.assertEqual('exact', compile_oracle_card(card, capability_registry=caps).status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for capability in ('trigger.keyword.job_select', 'attachment.action.fixed_source', 'token.creation.fixed_definition'):
            changed = deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id'] == capability).update(status='blocked', blockers=['omitted Job select authority'])
            self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=CapabilityRegistry(changed)).status)
        for text in ('Job select twice', 'Job select {2}', 'Job select. Draw a card.'):
            self.assertNotEqual('exact', compile_oracle_card(replace(card, oracle_text=text), capability_registry=caps).status)


class JobSelectRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.FixedCopyTypeExceptionRuntimeTests.session
    add = controls.FixedCopyTypeExceptionRuntimeTests.add
    prepare = controls.FixedCopyTypeExceptionRuntimeTests.prepare
    cast = controls.FixedCopyTypeExceptionRuntimeTests.cast
    checkpoint = controls.FixedCopyTypeExceptionRuntimeTests.checkpoint
    resolve = controls.FixedCopyTypeExceptionRuntimeTests.resolve
    replay = controls.FixedCopyTypeExceptionRuntimeTests.replay
    assert_closed = controls.FixedCopyTypeExceptionRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'job-select.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/job-select-cards.json',
            ROOT / 'tests/fixtures/ascend-city-blessing-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Job select', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_original_card_admission_and_unsupported_siblings_remain_distinct(self):
        fixture = json.loads((ROOT / 'tests/fixtures/job-select-cards.json').read_text(encoding='utf-8'))
        semantics = SemanticRegistry()
        for row in fixture['cards']:
            if row['name'] == 'Disenchant':
                continue
            card = self.db.lookup(row['name'])
            program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                capability_registry=self.registry, capability_profile='commander_review')
            ready = bind_card_program_runtime(program, capability_registry=self.registry,
                profile='commander_review')['strict_capability_ready']
            with self.subTest(card=card.name):
                self.assertEqual(card.name in EXPECTED, ready)

    def fixture(self, seed, name):
        session = self.session(seed)
        source = self.add(session.engine, name, zone='hand', ref='job-equipment')
        body = self.add(session.engine, 'Generic Bound Body', ref='job-other-body')
        private = {seat: self.add(session.engine, 'Generic Bound Body', seat=seat,
            zone='hand', ref='job-private-' + seat) for seat in 'ABCD'}
        self.prepare(session, mana={'C':20, 'W':4, 'U':4})
        session.state.players['B'].mana_pool.update({'W':2, 'C':2})
        self.checkpoint(session)
        return session, source, body, private

    def resolve_spell(self, session, source):
        for _ in range(16):
            if session.state.cards[source.object_id].zone == 'battlefield':
                return
            principal = session.pending_principals()[0]
            session.packet(principal, full=True)
            result = session.act(principal, {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Equipment did not resolve')

    def test_native_hero_and_flavored_equip_actions_resume_with_private_rollback(self):
        session, source, body, private = self.fixture(702_182_001, "Paladin's Arms")
        self.cast(session, 'A', source)
        self.resolve_spell(session, source)
        self.assertEqual(1, len(session.state.stack))
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        hero = next(card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield')
        self.assertEqual('A', hero.controller)
        self.assertEqual('Hero', hero.printed_name)
        self.assertEqual(hero.object_id, session.state.cards[source.object_id].attached_to)
        self.assertEqual([], session.engine._effective_card_data(hero)['colors'])
        self.assertEqual((3, 2), tuple(session.engine._numeric_stat(hero.object_id, stat) for stat in ('power', 'toughness')))
        self.assertIn('knight', session.engine._type_parts(session.engine._effective_card_data(hero)['type_line'])[1])
        for viewer in 'ABCD':
            packet = json.dumps(session.packet('pilot:' + viewer, full=True))
            self.assertIn(private[viewer].ref, packet)
            for owner in 'ABCD':
                if owner != viewer:
                    self.assertNotIn(private[owner].ref, packet)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        action = next(row for row in actions if row['id'].startswith('activate:' + session.state.cards[source.object_id].ref + ':'))
        command = {'action_id':action['id'], 'targets':[body.ref], 'pay':'auto'}
        unchanged = authoritative_state_hash(session.state)
        rejected = session.act('pilot:C', command)
        self.assertFalse(rejected.ok)
        self.assertEqual(unchanged, authoritative_state_hash(session.state))
        mana = sum(session.state.players['A'].mana_pool.values())
        accepted = session.act('pilot:A', command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(mana - 4, sum(session.state.players['A'].mana_pool.values()))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(body.object_id, session.state.cards[source.object_id].attached_to)
        self.assertEqual((1, 1), tuple(session.engine._numeric_stat(hero.object_id, stat) for stat in ('power', 'toughness')))
        self.assertNotIn('knight', session.engine._type_parts(session.engine._effective_card_data(hero)['type_line'])[1])
        self.replay(session, load=True)

    def test_departed_equipment_still_creates_unattached_one_one_hero(self):
        session, source, body, private = self.fixture(702_182_002, "Monk's Fist")
        removal = self.add(session.engine, 'Disenchant', seat='B', zone='hand', ref='job-remove-source')
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.resolve_spell(session, source)
        self.cast(session, 'B', removal, [session.state.cards[source.object_id].ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        hero = next(card for card in session.state.cards.values() if card.is_token and card.zone == 'battlefield')
        self.assertEqual('A', hero.controller)
        self.assertEqual('Hero', hero.printed_name)
        self.assertEqual((1, 1), tuple(session.engine._numeric_stat(hero.object_id, stat) for stat in ('power', 'toughness')))
        self.assertIsNone(session.state.cards[source.object_id].attached_to)
        self.replay(session, load=True)

    def test_token_profile_and_attachment_omissions_fail_native_consequences(self):
        import quorune.compiler.fixed_attachment_keyword_nodes as compiler
        method = 'test_native_hero_and_flavored_equip_actions_resume_with_private_rollback'
        with patch.dict(compiler._SPECS, {'job select':('trigger.keyword.job_select', 'Create a 2/2 red Rebel creature token.')}), self.assertRaises(AssertionError):
            getattr(type(self)(method), method)()
        with patch('quorune.effect_runtime.zones_and_attachments._apply_attach', return_value=None), self.assertRaises(AssertionError):
            getattr(type(self)(method), method)()
