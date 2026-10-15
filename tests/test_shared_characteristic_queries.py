from __future__ import annotations

import unittest
from quorune.compiler.query_characteristic_templates import query_characteristic_quantity


class SharedCharacteristicQueryCompilerTests(unittest.TestCase):
    def test_static_expansion_does_not_shadow_live_resolution_quantities(self):
        from quorune.compiler.live_battlefield_quantities import resolution_quantity
        from quorune.live_battlefield_quantity_model import LiveBattlefieldQuantitySpec
        for text in ('attacking creatures', 'other attacking Goblin', 'creatures you control with flying'):
            self.assertIsInstance(resolution_quantity(text, source_name='Generic'), LiveBattlefieldQuantitySpec)

    def test_shared_query_producer_mutation_and_unknown_terms_fail_closed(self):
        from unittest.mock import patch
        from quorune.compiler.shared_characteristic_queries import shared_characteristic_quantity
        self.assertIsNone(shared_characteristic_quantity('other imagined creatures you control'))
        with patch('quorune.compiler.public_state_queries.fixed_battlefield_query_subject', return_value=None):
            self.assertIsNone(query_characteristic_quantity('other modified creature you control', source_name='Generic'))

    def test_shared_characteristic_queries_preserve_union_exclusion_and_late_layer_boundary(self):
        cases = (
            ('Soldiers and Warriors you control', 'subtypes_any', ('soldier', 'warrior'), False),
            ('non-Wall creatures you control', 'excluded_subtypes', ('wall',), False),
            ('other creature you control with a +1/+1 counter on it', 'types_all', ('creature',), True),
            ('other modified creature you control', 'types_all', ('creature',), True),
        )
        for text, field, expected, other in cases:
            with self.subTest(text=text):
                value = query_characteristic_quantity(text, source_name='Generic')
                self.assertIsNotNone(value)
                self.assertEqual(expected, getattr(value.query, field))
                self.assertEqual(other, value.exclude_source)
        for text in ('creatures you control with flying', 'creatures you control without flying',
                'creatures you control with power 4 or greater', 'chosen creatures you control', 'creature'):
            self.assertIsNone(query_characteristic_quantity(text, source_name='Generic'))


class SharedCharacteristicQueryActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'shared-queries.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/shared-characteristic-query-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Shared queries', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _bound
    import test_live_battlefield_quantities as _live
    session = _bound.BoundEffectProgramRuntimeTests.session
    add = _bound.BoundEffectProgramRuntimeTests.add
    ready = _bound.BoundEffectProgramRuntimeTests.ready
    checkpoint = _bound.BoundEffectProgramRuntimeTests.checkpoint
    replay = _bound.BoundEffectProgramRuntimeTests.replay
    resolve = _bound.BoundEffectProgramRuntimeTests.resolve
    action = _live.LiveBattlefieldQuantityActionTests.action
    register_fixture_cards_on_add = True

    def test_original_avatar_and_sentinels_use_other_current_counter_creatures(self):
        session = self.session(365204001)
        engine = session.engine
        source = self.add(engine, 'High Sentinels of Arashin')
        body = self.add(engine, 'Generic Bound Body')
        enemy = self.add(engine, 'Generic Bound Body', seat='B', ref='enemy-counted')
        enemy.counters['+1/+1'] = 1
        spell = self.add(engine, 'Avatar of the Resolute', zone='hand')
        offer = self.ready(session, source, {'G': 5, 'W': 1})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offer['id'], 'targets': [body.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        engine = session.engine
        self.assertEqual(1, session.state.cards[body.object_id].counters['+1/+1'])
        self.assertEqual(4, engine._numeric_stat(source.object_id, 'power'))
        self.action(session, spell)
        self.resolve(session)
        self.assertEqual(1, session.state.cards[spell.object_id].counters.get('+1/+1', 0))
        self.assertEqual(5, engine._numeric_stat(source.object_id, 'power'))
        self.assertEqual(4, engine._numeric_stat(spell.object_id, 'power'))
        self.replay(session)

    def test_original_upriser_counts_controller_aura_and_counter_modifications_once(self):
        session = self.session(365204002)
        engine = session.engine
        source = self.add(engine, 'Upriser Renegade')
        body = self.add(engine, 'Generic Bound Body')
        unmodified = self.add(engine, 'Generic Bound Body', ref='second-body')
        aura = self.add(engine, 'Pacifism', zone='hand')
        foreign = self.add(engine, 'Eel Umbra', seat='B', zone='hand', ref='foreign-modifying-aura')
        source.counters['+1/+1'] = 1
        engine.state.players['B'].mana_pool.update({'U': 1, 'C': 1})
        offer = self.ready(session, aura, {'W': 1, 'C': 1})
        self.assertEqual(2, engine._numeric_stat(source.object_id, 'power'))
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offer['id'], 'targets': [body.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(4, engine._numeric_stat(source.object_id, 'power'))
        self.assertEqual(2, engine._numeric_stat(unmodified.object_id, 'power'))
        # An opponent-controlled Aura enchants our other creature but does not
        # make it modified for our controller-relative quantity.
        while session.state.priority_player != 'B':
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        actions = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        offered = next(row for row in actions if row.get('card') == foreign.ref)
        accepted = session.act('pilot:B', {'action_id': offered['id'], 'targets': [unmodified.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(4, engine._numeric_stat(source.object_id, 'power'))
        self.assertEqual(3, engine._numeric_stat(unmodified.object_id, 'power'))
        self.replay(session, load=True)

    def test_original_keldon_warlord_excludes_walls_and_opponents_at_current_layer(self):
        session = self.session(365204003)
        engine = session.engine
        source = self.add(engine, 'Keldon Warlord')
        body = self.add(engine, 'Generic Bound Body')
        wall = self.add(engine, 'Wall of Omens')
        self.add(engine, 'Generic Bound Body', seat='B', ref='enemy-nonwall')
        snake = self.add(engine, 'Snakeform', zone='hand')
        offer = self.ready(session, snake, {'G': 1, 'C': 2})
        self.assertEqual(2, engine._numeric_stat(source.object_id, 'power'))
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': offer['id'], 'targets': [wall.ref], 'pay': 'auto'}).ok)
        self.resolve(session)
        self.assertEqual(3, engine._numeric_stat(source.object_id, 'power'))
        self.assertEqual(2, engine._numeric_stat(body.object_id, 'power'))
        self.replay(session)
