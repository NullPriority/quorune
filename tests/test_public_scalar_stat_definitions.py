from __future__ import annotations

"""CR 604.3/613.4a: public scalar arithmetic defines current all-zone stats."""

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.characteristic_fragments import CharacteristicQuantitySpec, CharacteristicQuantityScope, CharacteristicQuantityReduction
from quorune.deck import DeckDefinition, DeckEntry
from quorune.dynamic_characteristics import query_characteristic_count
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_bound_effect_programs as actions
import test_self_public_zone_casting as zone_actions


class PublicScalarStatCompilerTests(unittest.TestCase):
    def test_definitions_preserve_complete_nested_public_arithmetic(self):
        registry = load_default_capability_registry()
        for phrase in ('the number of sorcery cards in all graveyards', 'the number of instant cards in all graveyards',
                       'twice the number of cards in your hand', '1 plus twice the number of age counters on it'):
            text = "This creature's power and toughness are each equal to " + phrase + '.'
            with self.subTest(phrase=phrase):
                ir = compile_oracle_card(record(text, 'Creature — Human'), capability_registry=registry,
                    capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(text, node.text)
                self.assertIn('continuous.characteristics.query_power_toughness_definition', node.capability_dependencies)

    def test_hand_arithmetic_reads_only_cardinality_and_counter_arithmetic_remains_closed(self):
        class NoHiddenReads(dict):
            def __getitem__(self, key):
                raise AssertionError('Hand arithmetic read a hidden card')

        host = SimpleNamespace(state=SimpleNamespace(players={'A': SimpleNamespace(zones={'hand': ['secret-1', 'secret-2']})}, cards=NoHiddenReads()))
        source = SimpleNamespace(controller='A', counters={'age': 2})
        hand = CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE,
            query=ObjectQuerySpec(zones=('hand',)), schema_version=2, multiplier=2, offset=1)
        self.assertEqual(5, query_characteristic_count(host, source, hand))
        self.assertEqual(hand, CharacteristicQuantitySpec.from_dict(json.loads(json.dumps(hand.to_dict()))))
        age = CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.SOURCE_COUNTER,
            counter_name='age', schema_version=2, multiplier=2, offset=1)
        self.assertEqual(5, query_characteristic_count(host, source, age))
        self.assertEqual(1, query_characteristic_count(host, SimpleNamespace(counters={}), age))
        for query in (ObjectQuerySpec(zones=('hand',), types_all=('creature',)),
                      ObjectQuerySpec(zones=('hand',), colors_all=('R',)),
                      ObjectQuerySpec(zones=('hand',), token=False)):
            with self.assertRaises(ValueError):
                replace(hand, query=query)
        for altered in ({'reduction': CharacteristicQuantityReduction.TOTAL_MANA_VALUE}, {'multiplier': True}, {'offset': -1}):
            with self.assertRaises(ValueError):
                replace(age, **altered)
        with self.assertRaises(ValueError):
            replace(hand, reduction=CharacteristicQuantityReduction.DISTINCT_COLORS)


class PublicScalarStatRuntimeTests(unittest.TestCase):
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
        path = Path(cls.temporary.name).resolve() / 'public-scalar-stats.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/public-scalar-stat-cards.json', ROOT / 'tests/fixtures/self-public-zone-cards.json',
            ROOT / 'tests/fixtures/event-player-subject-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Public scalar stat witness', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def stats(self, session, card):
        return tuple(session.engine._numeric_stat(card.object_id, name) for name in ('power', 'toughness'))

    def test_actual_magnivore_counts_all_players_sorceries_and_ignores_instants(self):
        session = self.session(398001); engine = session.engine
        source = self.add(engine, 'Magnivore', zone='hand', ref='original-graveyard-definition')
        self.add(engine, 'Generic Stat Sorcery', zone='graveyard', ref='own-grave-sorcery')
        self.add(engine, 'Generic Stat Sorcery', seat='C', zone='graveyard', ref='foreign-grave-sorcery')
        self.add(engine, 'Generic Stat Instant', seat='B', zone='graveyard', ref='foreign-grave-instant')
        sorcery = self.add(engine, 'Generic Stat Sorcery', zone='hand', ref='new-counted-sorcery')
        instant = self.add(engine, 'Generic Stat Instant', zone='hand', ref='new-uncounted-instant')
        private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='stat-private')
        self.assert_closed(session, source); self.prepare(session, mana={'R': 2, 'C': 2}); self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B', {'action_id': 'cast:' + source.ref, 'pay': 'auto'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, 'A', source); self.resolve(session)
        self.assertEqual((2, 2), self.stats(session, source))
        self.cast(session, 'A', sorcery)
        session = self.replay(session, load=True); self.resolve(session)
        self.assertEqual((3, 3), self.stats(session, source))
        self.cast(session, 'A', instant); self.resolve(session)
        self.assertEqual((3, 3), self.stats(session, source))
        self.assertEqual(42, session.state.players['A'].life)
        for viewer in 'ABC':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.replay(session, load=True)

    def test_actual_masumaro_tracks_private_draws_current_controller_and_new_zone(self):
        session = self.session(398002); engine = session.engine
        source = self.add(engine, 'Masumaro, First to Live', zone='hand', ref='original-hand-definition')
        draw = self.add(engine, 'Generic Stat Draw', zone='hand', ref='hand-count-draw')
        control = self.add(engine, 'Generic Event Subject Control', seat='D', zone='hand', ref='hand-count-control')
        self.add(engine, 'Generic Bound Plains', seat='D', ref='hand-count-control-payment')
        kill = self.add(engine, 'Generic Public Zone Destroy', zone='hand', ref='hand-count-departure')
        for i in range(3):
            self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='other-hand-' + str(i))
        self.assert_closed(session, source); self.prepare(session, mana={'G': 6, 'C': 3}); self.checkpoint(session)
        self.cast(session, 'A', source); self.resolve(session)
        self.assertEqual((2 * len(session.state.players['A'].zones['hand']),) * 2, self.stats(session, source))
        before_hand = set(session.state.players['A'].zones['hand'])
        self.cast(session, 'A', draw)
        session = self.replay(session, load=True); self.resolve(session)
        self.assertEqual((2 * len(session.state.players['A'].zones['hand']),) * 2, self.stats(session, source))
        new = set(session.state.players['A'].zones['hand']) - before_hand
        self.assertEqual(2, len(new))
        for oid in new:
            reference = session.state.cards[oid].ref
            for viewer in 'BCD':
                self.assertNotIn(reference, json.dumps(session.packet('pilot:' + viewer, full=True)))
        self.cast(session, 'D', control, [source.ref]); self.resolve(session)
        self.assertEqual('D', session.state.cards[source.object_id].controller)
        self.assertEqual((2 * len(session.state.players['D'].zones['hand']),) * 2, self.stats(session, source))
        self.cast(session, 'A', kill, [source.ref]); self.resolve(session)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        data = session.engine._effective_card_data(session.state.cards[source.object_id])
        self.assertEqual((str(2 * len(session.state.players['A'].zones['hand'])),) * 2, (data['power'], data['toughness']))
        self.replay(session, load=True)

    def test_actual_mwonvuli_uses_nested_counter_arithmetic_before_later_modifiers(self):
        session = self.session(398003); engine = session.engine
        source = self.add(engine, 'Mwonvuli Ooze', zone='hand', ref='original-counter-definition')
        age = self.add(engine, 'Generic Stat Age', zone='hand', ref='definition-age-counter')
        growth = self.add(engine, 'Generic Bound Growth', zone='hand', ref='definition-later-modifier')
        self.assert_closed(session, source); self.prepare(session, mana={'G': 2}); self.checkpoint(session)
        self.cast(session, 'A', source); self.resolve(session)
        self.assertEqual((1, 1), self.stats(session, source))
        self.cast(session, 'A', growth, [source.ref]); self.resolve(session)
        self.assertEqual((3, 3), self.stats(session, source))
        self.cast(session, 'A', age, [source.ref]); self.resolve(session)
        self.assertEqual((5, 5), self.stats(session, source))
        self.replay(session, load=True)
