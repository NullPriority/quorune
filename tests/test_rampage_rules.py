from __future__ import annotations

import unittest
from dataclasses import replace
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_public_query_effect_amounts import query_amount_record


class RampageCompilerTests(unittest.TestCase):
    def test_rampage_dependency_omission_and_unknown_amount_fail_closed(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from unittest.mock import patch
        card = replace(query_amount_record('Rampage 2', suffix=365206002, type_line='Creature — Giant'), keywords=('Rampage',))
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id'] == 'combat.trigger.rampage').update(status='blocked', blockers=['Rampage withdrawn'])
        self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=CapabilityRegistry(raw)).status)
        for text in ('Rampage X', 'Rampage 0', 'Rampage 2 or 3'):
            self.assertNotEqual('exact', compile_oracle_card(replace(card, oracle_text=text),
                capability_registry=load_default_capability_registry()).status)
        with patch('quorune.compiler.rampage_fragments.rampage_fragment_lowering',
                return_value=__import__('quorune.compiler.ability_keyword_fragment_model', fromlist=['AbilityKeywordFragmentLowering']).AbilityKeywordFragmentLowering(
                    residual_kind='missing', residual_reason='Removed producer', residual_blockers=('missing',))):
            self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=load_default_capability_registry()).status)

    def test_fixed_rampage_is_typed_and_preserves_multiple_instances(self):
        record = replace(query_amount_record('Rampage 2; rampage 1', suffix=365206001,
            type_line='Creature — Giant'), keywords=('Rampage',))
        ir = compile_oracle_card(record, capability_registry=load_default_capability_registry())
        self.assertEqual('exact', ir.status, ir.material_residuals)
        values = [h['fragment']['value'] for h in ir.faces[0].nodes[0].handlers]
        self.assertEqual([2, 1], [v['amount'] for v in values])
        self.assertTrue(all(v['kind'] == 'rampage' for v in values))


class RampageModelTests(unittest.TestCase):
    def test_rampage_count_mutation_and_malformed_amount_are_rejected(self):
        from quorune.ability_fragments import CombatKeywordTriggerKind, CombatKeywordTriggerSpec
        for amount in (0, True, -1):
            with self.assertRaises(ValueError):
                CombatKeywordTriggerSpec(CombatKeywordTriggerKind.RAMPAGE, amount)

    def test_rampage_derives_per_instance_only_when_attacking_and_samples_current_blockers(self):
        from types import SimpleNamespace
        from quorune.ability_fragments import CombatKeywordTriggerKind, CombatKeywordTriggerSpec
        from quorune.block_transitions import (BlockTransitionAssignment, BlockTransitionEvent,
            BlockTransitionParticipant, derive_block_keyword_trigger_occurrences)
        from quorune.rampage import rampage_resolution_bonus
        specs = (CombatKeywordTriggerSpec(CombatKeywordTriggerKind.RAMPAGE, 1),
            CombatKeywordTriggerSpec(CombatKeywordTriggerKind.RAMPAGE, 2))
        attacker = BlockTransitionParticipant('a', 'a@0', 'Aref', 'A', specs)
        blockers = tuple(BlockTransitionParticipant('b'+str(i), 'b'+str(i)+'@0', 'Bref'+str(i), 'B', specs if i == 0 else ())
            for i in range(3))
        event = BlockTransitionEvent.create(turn_sequence=1, priority_epoch=1, active_player='A',
            participants=(attacker, *blockers), assignments=tuple(BlockTransitionAssignment('a', b.object_id) for b in blockers))
        occurrences = derive_block_keyword_trigger_occurrences(event)
        self.assertEqual(2, len(occurrences))
        self.assertTrue(all(o.source.object_id == 'a' for o in occurrences))
        rows = {'a': SimpleNamespace(zone='battlefield', phased_out=False, logical_object_id='a@0', object_id='a')}
        rows.update({b.object_id: SimpleNamespace(zone='battlefield', phased_out=False, blocking='a') for b in blockers})
        host = SimpleNamespace(state=SimpleNamespace(cards=rows, combat=SimpleNamespace(blockers={'a': ['b0', 'b1', 'b2']})))
        self.assertEqual([2, 4], [rampage_resolution_bonus(host, o) for o in occurrences])
        from unittest.mock import patch
        with patch('quorune.rampage.max', side_effect=lambda *_args: 3, create=True):
            with self.assertRaises(AssertionError):
                self.assertEqual(2, rampage_resolution_bonus(host, occurrences[0]))
        rows['b2'].zone = 'exile'
        self.assertEqual([1, 2], [rampage_resolution_bonus(host, o) for o in occurrences])
        rows['b1'].phased_out = True
        self.assertEqual([0, 0], [rampage_resolution_bonus(host, o) for o in occurrences])
        rows['a'].logical_object_id = 'a@2'
        self.assertEqual(0, rampage_resolution_bonus(host, occurrences[0]))


class RampageActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'rampage.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/rampage-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Rampage', [DeckEntry('Generic Bound Commander', 1, 'commander'),
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

    def blocked(self, session, source, blockers):
        self.attack(session, [source])
        for _ in range(8):
            if session.state.pending_decision.kind == 'combat.blockers':
                break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        self.assertEqual('combat.blockers', session.state.pending_decision.kind)
        blocked = session.act('pilot:B', {'a': 'block', 'blk': {b.ref: source.ref for b in blockers}})
        self.assertTrue(blocked.ok, blocked.summary)
        self.assertEqual(1, len(session.state.stack))

    def test_original_craw_giant_samples_blockers_at_resolution_and_retains_bonus_after(self):
        from quorune.record import authoritative_state_hash
        session = self.session(365206010)
        engine = session.engine
        source = self.add(engine, 'Craw Giant', zone='hand')
        blockers = [self.add(engine, 'Generic Bound Body', seat='B', ref='rampage-blocker-' + str(i)) for i in range(3)]
        blink = self.add(engine, 'Cloudshift', seat='B', zone='hand')
        after_blink = self.add(engine, 'Cloudshift', seat='B', zone='hand', ref='rampage-after-blink')
        self.add(engine, 'Generic Bound Plains', seat='B')
        self.add(engine, 'Generic Bound Plains', seat='B', ref='rampage-second-plains')
        offered = self.ready(session, source, {'G': 4, 'C': 3})
        self.checkpoint(session)
        self.assertTrue(session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'}).ok)
        self.resolve(session)
        self.blocked(session, session.state.cards[source.object_id], blockers)
        for seat in 'ABCD':
            packet = str(session.packet('pilot:' + seat, full=True))
            for other in 'ABCD':
                if other == seat:continue
                for oid in session.state.players[other].zones['hand']:
                    self.assertNotIn(session.state.cards[oid].ref, packet)
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:C', {'action_id': 'pass'})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        for _ in range(4):
            if session.state.priority_player == 'B':break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        actions = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        offered = next(a for a in actions if a.get('card') == blink.ref)
        self.assertTrue(session.act('pilot:B', {'action_id': offered['id'], 'targets': [blockers[2].ref], 'pay': 'auto'}).ok)
        for _ in range(4):
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual(8, session.engine._numeric_stat(source.object_id, 'power'))
        self.assertEqual(2, len(session.state.combat.blockers[source.object_id]))
        for _ in range(4):
            if session.state.priority_player == 'B':break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        actions = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        offered = next(a for a in actions if a.get('card') == after_blink.ref)
        self.assertTrue(session.act('pilot:B', {'action_id': offered['id'], 'targets': [blockers[1].ref], 'pay': 'auto'}).ok)
        self.resolve(session)
        self.assertEqual(8, session.engine._numeric_stat(source.object_id, 'power'))
        self.assertEqual(1, len(session.state.combat.blockers[source.object_id]))
        for _ in range(16):
            decision = session.state.pending_decision
            if decision.kind == 'combat.damage':
                accepted = session.act('pilot:A', {'a': 'assign_damage', 'assignments': [
                    {'source': source.ref, 'target': blockers[0].ref, 'amount': 6},
                    {'source': source.ref, 'target': 'B', 'amount': 2}]})
            else:
                accepted = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(accepted.ok, accepted.summary)
            if session.state.cards[blockers[0].object_id].zone == 'graveyard':break
        self.assertEqual('graveyard', session.state.cards[blockers[0].object_id].zone)
        self.assertEqual(38, session.state.players['B'].life)
        self.assertEqual(2, session.state.cards[source.object_id].marked_damage)
        self.replay(session)

    def test_original_horrible_hordes_one_blocker_has_zero_bonus_and_source_blink_is_stale(self):
        for blink_source in (False, True):
            session = self.session(365206020 + blink_source)
            engine = session.engine
            source = self.add(engine, 'Horrible Hordes')
            blocker = self.add(engine, 'Generic Bound Body', seat='B')
            blink = self.add(engine, 'Cloudshift', zone='hand')
            self.add(engine, 'Generic Bound Plains')
            self.blocked(session, source, [blocker])
            if blink_source:
                self.action(session, blink, targets=[source.ref])
                for _ in range(4):
                    self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
            session = self.replay(session, load=True)
            self.resolve(session)
            self.assertEqual(2, session.engine._numeric_stat(source.object_id, 'power'))
            self.replay(session)

    def test_original_rampage_removed_before_blocks_produces_no_occurrence(self):
        session = self.session(365206030)
        engine = session.engine
        source = self.add(engine, 'Horrible Hordes')
        blocker = self.add(engine, 'Generic Bound Body', seat='B')
        snake = self.add(engine, 'Snakeform', zone='hand')
        self.add(engine, 'Generic Bound Plains')
        self.add(engine, 'Generic Bound Plains', ref='rampage-mana-two')
        self.add(engine, 'Generic Bound Plains', ref='rampage-mana-three')
        engine.state.players['A'].mana_pool.update({'G': 1, 'C': 2})
        self.attack(session, [source])
        self.action(session, snake, targets=[source.ref])
        self.resolve(session)
        for _ in range(8):
            if session.state.pending_decision.kind == 'combat.blockers':break
            self.assertTrue(session.act(session.pending_principals()[0], {'action_id': 'pass'}).ok)
        blocked = session.act('pilot:B', {'a': 'block', 'blk': {blocker.ref: source.ref}})
        self.assertTrue(blocked.ok, blocked.summary)
        self.assertEqual([], session.state.stack)
        self.assertEqual(1, engine._numeric_stat(source.object_id, 'power'))
        self.replay(session)
