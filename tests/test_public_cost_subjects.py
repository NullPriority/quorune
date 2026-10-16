from __future__ import annotations

"""CR 601.2f/i: spell ordinals count committed casts, with closed type unions."""

import json
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


class PublicCostSubjectCompilerTests(unittest.TestCase):
    def test_unqualified_ordinals_and_type_disjunctions_keep_their_exact_predicates(self):
        registry = load_default_capability_registry()
        cases = (
            ('The second spell you cast each turn costs {1} less to cast.', 'second', ()),
            ('Dualcast — The second spell you cast each turn costs {2} less to cast.', 'second', ()),
            ('The first instant or sorcery spell you cast each turn costs {3} less to cast.', 'first', ('instant', 'sorcery')),
            ('Instant or sorcery spells you cast cost {1} less to cast.', 'any', ('instant', 'sorcery')),
        )
        for text, ordinal, types in cases:
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, 'Artifact'), capability_registry=registry,
                    capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                descriptor = ir.faces[0].nodes[0].handlers[0]
                modifier = descriptor['modifier']
                self.assertEqual(ordinal, modifier['ordinal'])
                self.assertEqual(list(types), modifier['predicates_any'][0]['types_any'])
                self.assertEqual([], modifier['predicates_any'][0]['types_all'])

    def test_relative_color_union_is_one_discount_and_open_subjects_remain_residual(self):
        registry = load_default_capability_registry()
        text = "Each spell you cast that's red or green costs {1} less to cast."
        ir = compile_oracle_card(record(text, 'Creature — Goblin'), capability_registry=registry,
            capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        modifier = ir.faces[0].nodes[0].handlers[0]['modifier']
        self.assertEqual(['G', 'R'], modifier['predicates_any'][0]['colors_any'])
        self.assertEqual({'GENERIC': -1}, modifier['mana_adjustment'])
        self.assertEqual(1, len(modifier['predicates_any']))
        for text in ('The third spell you cast each turn costs {1} less to cast.',
                     'The first kicked spell you cast each turn costs {1} less to cast.',
                     'The first red spell you cast each turn costs {1} less to cast.',
                     "Each spell you cast that's red or chosen costs {1} less to cast.",
                     'Instant or creature with flying spells you cast cost {1} less to cast.'):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, 'Artifact'), capability_registry=registry,
                    capability_profile='commander_review')
                self.assertNotEqual('exact', ir.status)
                self.assertTrue(ir.material_residuals)


class PublicCostSubjectRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = actions.BoundEffectProgramRuntimeTests.session
    add = actions.BoundEffectProgramRuntimeTests.add
    checkpoint = actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = actions.BoundEffectProgramRuntimeTests.resolve
    replay = actions.BoundEffectProgramRuntimeTests.replay
    prepare = zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast = zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = zone_actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name).resolve() / 'public-cost-subjects.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/public-cost-subject-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Public cost subject witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def requirements(self, session, card):
        current = session.state.cards[card.object_id]
        action = next(row for row in session.packet('pilot:' + current.owner, full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'cast:' + current.ref)
        return next(option['requirements'] for option in action['cost_options'] if option['id'] == 'normal')

    def test_actual_bell_ringer_counts_its_own_cast_and_only_discounts_the_second_spell(self):
        session = self.session(399001); engine = session.engine
        source = self.add(engine, 'Highspire Bell-Ringer', zone='hand', ref='original-second-spell-reducer')
        second = self.add(engine, 'Generic Ordinal Colorless', zone='hand', ref='second-any-spell')
        third = self.add(engine, 'Generic Ordinal Sorcery', zone='hand', ref='third-any-spell')
        private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='ordinal-private')
        self.assert_closed(session, source); self.prepare(session, mana={'C': 8, 'U': 3}); self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', {'action_id': 'cast:' + source.ref, 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, 'A', source); self.resolve(session)
        self.assertEqual(1, self.requirements(session, second)['GENERIC'])
        self.cast(session, 'A', second)
        session = self.replay(session, load=True); self.resolve(session)
        self.assertEqual(4, self.requirements(session, third)['GENERIC'])
        self.cast(session, 'A', third); self.resolve(session)
        self.assertEqual(1, session.state.players['A'].mana_pool['C'])
        self.assertEqual(1, session.state.players['A'].mana_pool['U'])
        for viewer in 'ABC':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_actual_melek_counts_only_the_casters_instant_or_sorcery_history(self):
        session = self.session(399002); engine = session.engine
        source = self.add(engine, 'Melek, Reforged Researcher', zone='hand', ref='original-typed-first-reducer')
        self.add(engine, 'Generic Ordinal Sorcery', zone='graveyard', ref='melek-starting-sorcery')
        self.add(engine, 'Generic Ordinal Instant', zone='graveyard', ref='melek-starting-instant')
        first = self.add(engine, 'Generic Ordinal Instant', zone='hand', ref='first-typed-spell')
        second = self.add(engine, 'Generic Ordinal Sorcery', zone='hand', ref='second-typed-spell')
        foreign = self.add(engine, 'Generic Ordinal Zero Instant', seat='B', zone='hand', ref='foreign-cast-not-counted')
        self.assert_closed(session, source); self.prepare(session, mana={'C': 12, 'U': 8, 'R': 1}); self.checkpoint(session)
        self.assertEqual(3, self.requirements(session, source)['GENERIC'])
        self.assertEqual(1, self.requirements(session, source)['U'])
        self.assertEqual(1, self.requirements(session, source)['R'])
        self.cast(session, 'A', source); self.resolve(session)
        self.assertEqual(16, sum(session.state.players['A'].mana_pool.values()))
        self.cast(session, 'B', foreign); self.resolve(session)
        self.assertEqual(1, self.requirements(session, first)['GENERIC'])
        self.cast(session, 'A', first); self.resolve(session)
        self.assertEqual(14, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(4, self.requirements(session, second)['GENERIC'])
        self.cast(session, 'A', second)
        session = self.replay(session, load=True); self.resolve(session)
        self.assertEqual(9, sum(session.state.players['A'].mana_pool.values()))
        self.assertEqual(0, session.state.players['A'].mana_pool['R'])
        self.assertEqual(42, session.state.players['A'].life)
        self.assertEqual(41, session.state.players['B'].life)
        self.replay(session, load=True)

    def test_actual_anarchomancer_applies_one_discount_to_a_multicolor_spell(self):
        session = self.session(399003); engine = session.engine
        source = self.add(engine, 'Goblin Anarchomancer', zone='hand', ref='original-color-union-reducer')
        both = self.add(engine, 'Generic Ordinal Red Green', zone='hand', ref='both-colors-one-discount')
        neither = self.add(engine, 'Generic Ordinal Sorcery', zone='hand', ref='blue-spell-no-discount')
        self.assert_closed(session, source); self.prepare(session, mana={'C': 12, 'U': 3, 'R': 4, 'G': 4}); self.checkpoint(session)
        self.cast(session, 'A', source); self.resolve(session)
        self.assertEqual(1, self.requirements(session, both)['GENERIC'])
        self.assertEqual(4, self.requirements(session, neither)['GENERIC'])
        self.cast(session, 'A', both); self.resolve(session)
        self.assertEqual(11, session.state.players['A'].mana_pool['C'])
        self.assertEqual(2, session.state.players['A'].mana_pool['R'])
        self.assertEqual(2, session.state.players['A'].mana_pool['G'])
        self.cast(session, 'A', neither)
        session = self.replay(session, load=True); self.resolve(session)
        self.assertEqual(7, session.state.players['A'].mana_pool['C'])
        self.assertEqual(42, session.state.players['A'].life)
        self.replay(session, load=True)
