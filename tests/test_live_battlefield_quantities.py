from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_public_query_effect_amounts import query_amount_record
from quorune.live_battlefield_quantities import LiveBattlefieldQuantitySpec, current_live_battlefield_count
from quorune.object_predicate import ObjectQuerySpec, PermanentStatePredicateSpec
from quorune.query_effect_amount_model import PublicQueryAmountSpec


class LiveBattlefieldQuantityCompilerTests(unittest.TestCase):
    def test_counted_token_definition_retains_trailing_keywords(self):
        text = 'Create a 1/1 white Bird creature token with flying for each creature you control with defender.'
        ir = compile_oracle_card(query_amount_record(text, suffix=365202001),
            capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        effect = ir.faces[0].nodes[0].effects[0]
        self.assertEqual(['Flying'], effect['characteristics']['keywords'])
        self.assertEqual('live_battlefield_quantity', effect['quantity']['quantity']['kind'])
        for variant in (text.replace('flying', 'unrepresented ability'), text.replace('defender', 'chosen quality')):
            self.assertNotEqual('exact', compile_oracle_card(query_amount_record(variant, suffix=365202002),
                capability_registry=load_default_capability_registry()).status)

    def test_live_quantity_missing_producer_and_runtime_shape_fail_closed(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id'] == 'quantity_expression.live_battlefield_count').update(
            status='blocked', blockers=['Current battlefield producer withdrawn'])
        card = query_amount_record('Draw a card for each attacking creature.', suffix=365200199)
        self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=CapabilityRegistry(raw)).status)
        with patch('quorune.compiler.public_state_queries.fixed_battlefield_query_subject', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(card,
                capability_registry=load_default_capability_registry()).status)

    def test_resolution_count_preserves_current_keyword_combat_and_source_scope(self):
        registry = load_default_capability_registry()
        cases = (
            ('Draw a card for each attacking creature.', 'any', False, (), True),
            ('You gain 2 life for each creature you control with flying.', 'source_controller', False, ('flying',), None),
            ('This creature gets +2/+0 until end of turn for each other attacking Goblin.', 'any', True, (), True),
        )
        for i, (text, relation, excluded, keywords, attacking) in enumerate(cases):
            with self.subTest(text=text):
                ir = compile_oracle_card(query_amount_record(text, suffix=365200100 + i),
                    capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                effects = ir.faces[0].nodes[0].effects
                def quantities(value):
                    if isinstance(value, dict):
                        if value.get('kind') == 'live_battlefield_quantity':
                            yield value
                        for child in value.values():
                            yield from quantities(child)
                    elif isinstance(value, (list, tuple)):
                        for child in value:
                            yield from quantities(child)
                quantity = next(quantities(effects))
                self.assertEqual(relation, quantity['controller_relation'])
                self.assertEqual(excluded, quantity['exclude_source'])
                self.assertEqual(list(keywords), quantity['query']['keywords_all'])
                if attacking is not None:
                    self.assertEqual(attacking, quantity['query']['state_predicate']['attacking'])


class LiveBattlefieldQuantityModelTests(unittest.TestCase):
    def test_live_count_codec_preserves_legacy_boundary_and_rejects_unknown_domains(self):
        from quorune.characteristic_fragments import CharacteristicQuantitySpec, CharacteristicQuantityScope
        query = ObjectQuerySpec(zones=('battlefield',), types_all=('creature',), keywords_all=('defender',))
        spec = LiveBattlefieldQuantitySpec('source_controller', query)
        amount = PublicQueryAmountSpec(spec).to_dict()
        self.assertEqual(amount, PublicQueryAmountSpec.from_dict(amount).to_dict())
        with self.assertRaises(ValueError):
            CharacteristicQuantitySpec(CharacteristicQuantityScope.CONTROLLER_ZONE, query)
        for altered in (replace(query, zones=('graveyard',)), replace(query, known_to_actor=True),
                replace(query, owner='A'), replace(query, state_predicate=PermanentStatePredicateSpec(modified=True))):
            with self.subTest(query=altered):
                with self.assertRaises(ValueError):
                    LiveBattlefieldQuantitySpec('source_controller', altered)
        for value in ({**spec.to_dict(), 'schema_version': True}, {**spec.to_dict(), 'extra': 1},
                {**spec.to_dict(), 'controller_relation': 'chosen'},
                {**spec.to_dict(), 'controller_relation': []}):
            with self.assertRaises(ValueError):
                LiveBattlefieldQuantitySpec.from_dict(value)

    def test_live_count_current_membership_controller_and_mutation_contrasts(self):
        from quorune.model import CardInstance
        def card(index, controller='A', keyword='Defender', phased=False):
            c = CardInstance(object_id=str(index), ref='C'+str(index), printed_name='Public body',
                oracle_id='test:live-body', owner=controller, controller=controller, zone='battlefield')
            c.phased_out = phased
            return c, {'type_line': 'Creature — Wall', 'keywords': [keyword], 'power': '0', 'toughness': '4'}
        pairs = [card(1), card(2), card(3, 'B'), card(4, phased=True)]
        data = {c.object_id: d for c, d in pairs}
        host = SimpleNamespace(state=SimpleNamespace(players={'A': object(), 'B': object()},
            cards={c.object_id: c for c, d in pairs}), _effective_card_data=lambda c: data[c.object_id],
            _type_parts=lambda _t: ({'creature'}, {'wall'}, set()))
        query = ObjectQuerySpec(zones=('battlefield',), types_all=('creature',), keywords_all=('defender',))
        spec = LiveBattlefieldQuantitySpec('source_controller', query)
        self.assertEqual(2, current_live_battlefield_count(host, 'A', spec))
        self.assertEqual(1, current_live_battlefield_count(host, 'A', replace(spec, exclude_source=True), source_ref='C1'))
        self.assertEqual(1, current_live_battlefield_count(host, 'A', replace(spec, controller_relation='source_opponents')))
        data['2']['keywords'] = []
        self.assertEqual(1, current_live_battlefield_count(host, 'A', spec))
        pairs[0][0].zone = 'graveyard'
        self.assertEqual(0, current_live_battlefield_count(host, 'A', spec))
        with self.assertRaises(ValueError):
            current_live_battlefield_count(host, 'A', replace(spec, exclude_source=True))
        with patch('quorune.live_battlefield_quantities.object_matches_query', return_value=True):
            self.assertNotEqual(0, current_live_battlefield_count(host, 'A', spec))


class LiveBattlefieldQuantityActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'live-counts.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/live-battlefield-quantity-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Live quantities', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _bound
    session = _bound.BoundEffectProgramRuntimeTests.session
    add = _bound.BoundEffectProgramRuntimeTests.add
    ready = _bound.BoundEffectProgramRuntimeTests.ready
    checkpoint = _bound.BoundEffectProgramRuntimeTests.checkpoint
    replay = _bound.BoundEffectProgramRuntimeTests.replay
    resolve = _bound.BoundEffectProgramRuntimeTests.resolve
    register_fixture_cards_on_add = True

    def attack(self, session, attackers):
        from quorune.model import CombatState
        engine = session.engine
        engine.permissions.invalidate_current()
        session.state.pending_decision = None
        session.state.active_player = 'A'
        session.state.started = True
        session.state.phase = 'combat'
        session.state.step = 'declare_attackers'
        session.state.phase_index = 5
        session.state.combat = CombatState()
        for card in attackers:
            card.acquired_control_turn_count = -1
        engine._issue_attackers()
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'a': 'attack', 'atk': {c.ref: 'B' for c in attackers}})
        self.assertTrue(accepted.ok, accepted.summary)

    def action(self, session, card, **fields):
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offer = next(row for row in actions if row.get('card') == card.ref)
        accepted = session.act('pilot:A', {'action_id': offer['id'], 'pay': 'auto', **fields})
        self.assertTrue(accepted.ok, accepted.summary)
        return offer

    def test_original_keep_watch_counts_actual_attackers_after_blink_and_replays_privately(self):
        from quorune.record import authoritative_state_hash
        session = self.session(365201001)
        engine = session.engine
        attackers = [self.add(engine, 'Generic Bound Body', ref='attacker-' + str(i)) for i in range(2)]
        self.add(engine, 'Generic Bound Body', ref='nonattacker')
        draw = self.add(engine, 'Keep Watch', zone='hand')
        blink = self.add(engine, 'Cloudshift', zone='hand')
        engine.state.players['A'].mana_pool.update({'U': 1, 'W': 1, 'C': 3})
        self.attack(session, attackers)
        self.action(session, draw)
        self.action(session, blink, targets=[attackers[1].ref])
        for _ in range(4):
            accepted = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(1, len(session.state.stack))
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:D', {'action_id': 'pass'})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        hand = set(session.state.players['A'].zones['hand'])
        self.resolve(session)
        drawn = set(session.state.players['A'].zones['hand']) - hand
        self.assertEqual(1, len(drawn))
        for seat in 'BCD':
            packet = str(session.packet('pilot:' + seat, full=True))
            for oid in drawn:
                self.assertNotIn(session.state.cards[oid].ref, packet)
        self.replay(session, load=True)

    def test_original_vent_sentinel_counts_current_defender_after_snakeform_response(self):
        session = self.session(365201002)
        engine = session.engine
        source = self.add(engine, 'Vent Sentinel')
        other = self.add(engine, 'Overgrown Battlement')
        self.add(engine, 'Vent Sentinel', seat='B', ref='opponent-defender')
        snake = self.add(engine, 'Snakeform', zone='hand')
        offer = self.ready(session, source, {'R': 1, 'G': 1, 'C': 4})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offer['id'], 'pay': 'auto', 'targets': ['B']})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.action(session, snake, targets=[other.ref])
        for _ in range(4):
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.assertEqual(1, len(session.state.stack))
        session = self.replay(session, load=True)
        life = session.state.players['B'].life
        self.resolve(session)
        self.assertEqual(life - 1, session.state.players['B'].life)
        self.assertEqual(40, session.state.players['C'].life)
        self.assertEqual(40, session.state.players['D'].life)
        self.replay(session)

    def test_original_piledriver_excludes_source_and_non_goblin_attackers(self):
        session = self.session(365201003)
        engine = session.engine
        source = self.add(engine, 'Goblin Piledriver')
        goblin = self.add(engine, 'Goblin Piledriver', ref='other-goblin')
        body = self.add(engine, 'Generic Bound Body')
        self.add(engine, 'Goblin Piledriver', seat='B', ref='opponent-goblin')
        self.attack(session, [source, goblin, body])
        self.assertEqual('trigger.order', session.state.pending_decision.kind)
        decision = session.packet('pilot:A', full=True)['decision']
        order = [row['id'] for row in decision['ctx']['triggers']]
        accepted = session.act('pilot:A', {'action_id': 'order', 'triggers': order})
        self.assertTrue(accepted.ok, accepted.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        for card in (source, goblin):
            self.assertEqual(3, session.engine._numeric_stat(card.object_id, 'power'))
        self.assertEqual(2, session.engine._numeric_stat(body.object_id, 'power'))
        self.replay(session)

    def test_original_battlement_advertises_and_commits_live_defender_mana_without_stack(self):
        from quorune.record import authoritative_state_hash
        session = self.session(365201004)
        engine = session.engine
        source = self.add(engine, 'Overgrown Battlement')
        self.add(engine, 'Vent Sentinel')
        self.add(engine, 'Overgrown Battlement', seat='B', ref='enemy-battlement')
        offer = self.ready(session, source, {})
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:B', {'action_id': offer['id']})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id': offer['id']})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(2, session.state.players['A'].mana_pool['G'])
        self.assertEqual([], session.state.stack)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.replay(session, load=True)

    def test_original_chaplain_counts_current_defenders_and_creates_flying_birds(self):
        session = self.session(365202003)
        engine = session.engine
        other = self.add(engine, 'Overgrown Battlement')
        self.add(engine, 'Vent Sentinel', seat='B', ref='enemy-chaplain-defender')
        source = self.add(engine, 'Wingmantle Chaplain', zone='hand')
        snake = self.add(engine, 'Snakeform', zone='hand')
        offer = self.ready(session, source, {'W': 1, 'G': 1, 'C': 6})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offer['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(4):
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertEqual(1, len(session.state.stack))
        self.action(session, snake, targets=[other.ref])
        for _ in range(4):
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        session = self.replay(session, load=True)
        self.resolve(session)
        tokens = [c for c in session.state.cards.values() if c.zone == 'battlefield' and c.is_token]
        self.assertEqual(1, len(tokens))
        bird = tokens[0]
        data = session.engine._effective_card_data(bird)
        self.assertEqual('A', bird.controller)
        self.assertIn('Bird', data['type_line'])
        self.assertEqual(['W'], data['colors'])
        self.assertIn('flying', session.engine._combat_keywords(bird))
        self.assertEqual((1, 1), (session.engine._numeric_stat(bird.object_id, 'power'),
            session.engine._numeric_stat(bird.object_id, 'toughness')))
        self.replay(session)
