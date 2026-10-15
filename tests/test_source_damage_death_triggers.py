from __future__ import annotations

import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_public_query_effect_amounts import query_amount_record


class SourceDamageDeathCompilerTests(unittest.TestCase):
    def test_source_damage_death_requires_history_producer_and_closed_subject(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        text = 'Whenever a creature dealt damage by this creature this turn dies, put a +1/+1 counter on this creature.'
        record = query_amount_record(text, suffix=365207002, type_line='Creature — Vampire')
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id'] == 'trigger.event.source_damage_death_history').update(
            status='blocked', blockers=['History binding withdrawn'])
        self.assertNotEqual('exact', compile_oracle_card(record, capability_registry=CapabilityRegistry(raw)).status)
        from dataclasses import replace
        for changed in (text.replace('this turn', 'this game'), text.replace('this creature', 'another named source', 1)):
            self.assertNotEqual('exact', compile_oracle_card(replace(record, oracle_text=changed),
                capability_registry=load_default_capability_registry()).status)

    def test_source_damage_history_is_part_of_one_normalized_death_subscription(self):
        text = 'Whenever a creature dealt damage by this creature this turn dies, put a +1/+1 counter on this creature.'
        ir = compile_oracle_card(query_amount_record(text, suffix=365207001, type_line='Creature — Vampire'),
            capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual('creature.dies', node.event)
        self.assertIn('source_dealt_damage_to_dead_incarnation_this_turn', str(node.event_condition))
        self.assertEqual('$source.zone_object', node.effects[0]['card'])


class SourceDamageDeathModelTests(unittest.TestCase):
    def test_history_requires_both_incarnations_positive_damage_and_current_turn(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from quorune.model import TurnHistory, TurnHistoryEvent
        from quorune.source_damage_death import source_damage_death_matches
        source = SimpleNamespace(logical_object_id='source@1')
        history = TurnHistory(turn_sequence=2, events=[TurnHistoryEvent('permanent_damaged',
            actor='B', object_incarnation='source@1', target='victim', target_kind='creature',
            target_object_incarnation='victim@3', amount=1)])
        host = SimpleNamespace(state=SimpleNamespace(turn_history=history, turn_sequence=2))
        context = {'card_object_identity': 'victim@3'}
        self.assertTrue(source_damage_death_matches(host, source, context))
        self.assertFalse(source_damage_death_matches(host, SimpleNamespace(logical_object_id='other@1'), context))
        self.assertFalse(source_damage_death_matches(host, source, {'card_object_identity': 'victim@5'}))
        from dataclasses import replace
        history.events[0] = replace(history.events[0], amount=0)
        self.assertFalse(source_damage_death_matches(host, source, context))
        with patch('quorune.source_damage_death.current_turn_history_events', return_value=(
                TurnHistoryEvent('permanent_damaged', object_incarnation='source@1', target_object_incarnation='victim@3', amount=1),)):
            with self.assertRaises(AssertionError):
                self.assertFalse(source_damage_death_matches(host, source, context))
        host.state.turn_sequence = 3
        from quorune.errors import GameRuleError
        with self.assertRaises(GameRuleError):
            source_damage_death_matches(host, source, context)
        with self.assertRaises(GameRuleError):
            source_damage_death_matches(host, source, {})


class SourceDamageDeathActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'source-damage-death.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/source-damage-death-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Damage-death', [DeckEntry('Generic Bound Commander', 1, 'commander'),
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
    attack = _live.LiveBattlefieldQuantityActionTests.attack
    action = _live.LiveBattlefieldQuantityActionTests.action
    register_fixture_cards_on_add = True

    def test_original_blood_cultist_ping_then_sacrifice_counts_only_matching_source(self):
        from quorune.record import authoritative_state_hash
        session = self.session(365207010)
        engine = session.engine
        source = self.add(engine, 'Blood Cultist')
        other = self.add(engine, 'Blood Cultist', ref='same-name-other-cultist')
        enemy = self.add(engine, 'Blood Cultist', seat='B', ref='enemy-cultist')
        victim = self.add(engine, 'Generic Bound Body')
        feeder = self.add(engine, 'Carrion Feeder')
        offered = self.ready(session, source, {})
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:B', {'action_id': offered['id'], 'targets': [victim.ref]})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'targets': [victim.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(1, session.state.cards[victim.object_id].marked_damage)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered = next(a for a in actions if a['id'].startswith('activate:' + feeder.ref + ':'))
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'cost_cards': [victim.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(2, len(session.state.stack))
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual(1, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.assertEqual(0, session.state.cards[other.object_id].counters.get('+1/+1', 0))
        self.assertEqual(0, session.state.cards[enemy.object_id].counters.get('+1/+1', 0))
        self.assertEqual(1, session.state.cards[feeder.object_id].counters.get('+1/+1', 0))
        self.assertEqual('graveyard', session.state.cards[victim.object_id].zone)
        self.replay(session)

    def test_original_rot_wolf_combat_death_draw_is_private_and_replays_from_actions(self):
        session = self.session(365207020)
        engine = session.engine
        source = self.add(engine, 'Rot Wolf', zone='hand')
        blocker = self.add(engine, 'Wall of Omens', seat='B')
        self.add(engine, 'Rot Wolf', ref='undamaged-observer-wolf')
        growth = self.add(engine, 'Giant Growth', zone='hand')
        self.add(engine, 'Forest')
        offered = self.ready(session, source, {'G': 1, 'C': 2})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'}).ok)
        self.resolve(session)
        self.attack(session, [session.state.cards[source.object_id]])
        self.action(session, growth, targets=[source.ref])
        self.resolve(session)
        for _ in range(8):
            if session.state.pending_decision.kind == 'combat.blockers':break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.assertTrue(session.act('pilot:B', {'a': 'block', 'blk': {blocker.ref: source.ref}}).ok)
        for _ in range(16):
            decision = session.state.pending_decision
            if decision.kind == 'combat.damage':
                response = {'a': 'assign_damage', 'assignments': [
                    {'source': source.ref, 'target': blocker.ref, 'amount': 5}]}
            else:
                response = {'action_id': 'pass'}
            accepted = session.act(session.pending_principals()[0], response)
            self.assertTrue(accepted.ok, accepted.summary)
            if session.state.stack:break
        self.assertEqual('graveyard', session.state.cards[blocker.object_id].zone)
        self.assertEqual(1, len(session.state.stack))
        self.assertEqual('semantic.choice', self.resolve(session).kind)
        hand = set(session.state.players['A'].zones['hand'])
        session = self.replay(session, load=True)
        accepted = session.act('pilot:A', {'action_id': 'choose', 'choice': 'draw'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        drawn = set(session.state.players['A'].zones['hand']) - hand
        self.assertEqual(1, len(drawn))
        for seat in 'BCD':
            packet = str(session.packet('pilot:' + seat, full=True))
            for oid in drawn:self.assertNotIn(session.state.cards[oid].ref, packet)
        self.replay(session)

    def test_original_cultist_victim_or_source_blink_does_not_reuse_damage_history(self):
        for blink_victim in (True, False):
            session = self.session(365207030 + blink_victim)
            engine = session.engine
            source = self.add(engine, 'Blood Cultist')
            victim = self.add(engine, 'Generic Bound Body')
            feeder = self.add(engine, 'Carrion Feeder')
            shift = self.add(engine, 'Cloudshift', zone='hand')
            offered = self.ready(session, source, {'W': 1})
            self.checkpoint(session)
            self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'targets': [victim.ref]}).ok)
            self.resolve(session)
            self.action(session, shift, targets=[victim.ref if blink_victim else source.ref])
            self.resolve(session)
            actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            offered = next(a for a in actions if a['id'].startswith('activate:' + feeder.ref + ':'))
            self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'cost_cards': [victim.ref]}).ok)
            self.assertEqual(1, len(session.state.stack))
            self.resolve(session)
            self.assertEqual(0, session.state.cards[source.object_id].counters.get('+1/+1', 0))
            self.assertEqual(1, session.state.cards[feeder.object_id].counters.get('+1/+1', 0))
            self.replay(session)

    def test_original_two_cultists_observe_one_death_and_order_independent_results(self):
        session = self.session(365207040)
        engine = session.engine
        sources = [self.add(engine, 'Blood Cultist', ref='damage-observer-' + str(i)) for i in range(2)]
        victim = self.add(engine, 'Generic Bound Body')
        feeder = self.add(engine, 'Carrion Feeder')
        offered = self.ready(session, sources[0], {})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'targets': [victim.ref]}).ok)
        self.resolve(session)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered = next(a for a in actions if a['id'].startswith('activate:' + sources[1].ref + ':'))
        self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'targets': [victim.ref]}).ok)
        self.resolve(session)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered = next(a for a in actions if a['id'].startswith('activate:' + feeder.ref + ':'))
        self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'cost_cards': [victim.ref]}).ok)
        self.assertEqual('trigger.order', session.state.pending_decision.kind)
        refs = [row['id'] for row in session.packet('pilot:A', full=True)['decision']['ctx']['triggers']]
        self.assertEqual(2, len(refs))
        self.assertTrue(session.act('pilot:A', {'action_id': 'order', 'triggers': list(reversed(refs))}).ok)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual([1, 1], [session.state.cards[c.object_id].counters.get('+1/+1', 0) for c in sources])
        self.assertEqual(1, session.state.cards[feeder.object_id].counters.get('+1/+1', 0))
        self.replay(session)

    def test_original_cultist_simultaneous_death_uses_lki_but_cannot_counter_departed_source(self):
        session = self.session(365207050)
        engine = session.engine
        source = self.add(engine, 'Blood Cultist')
        victim = self.add(engine, 'Thriving Rhino')
        pyro = self.add(engine, 'Pyroclasm', zone='hand')
        offered = self.ready(session, source, {'R': 1, 'C': 1})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'targets': [victim.ref]}).ok)
        self.resolve(session)
        self.action(session, pyro)
        for _ in range(4):
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[victim.object_id].zone)
        self.assertEqual(1, len(session.state.stack))
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual(0, session.state.cards[source.object_id].counters.get('+1/+1', 0))
        self.replay(session)
