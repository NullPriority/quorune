from __future__ import annotations

"""Pinned CR701/121/608: all discard destinations precede following draws."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import json
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses


class WholeHandDiscardCompilerTests(unittest.TestCase):
    def test_whole_hand_discard_compiler_closes_complete_instructions_and_order(self):
        for text in ('Discard your hand.', 'Each player discards their hand, then draws seven cards.',
                     'Discard your hand, then draw two cards.', 'Target opponent discards their hand.'):
            with self.subTest(text=text):
                ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=load_default_capability_registry())
                self.assertEqual('exact',ir.status)
                node=ir.faces[0].nodes[0]
                self.assertEqual('discard_whole_hands',node.effects[0]['op'])
                self.assertIn('zone.discard.whole_hand',node.capability_dependencies)
                self.assertEqual((0,len(text)),(node.span.start,node.span.end))
                if 'then' in text:self.assertIn(node.effects[1]['op'],{'draw','draw_each_player'})

    def test_whole_hand_discard_grammar_and_player_codec_fail_closed(self):
        from quorune.compiler.whole_hand_discard_templates import whole_hand_discard_effect_template
        self.assertIsNone(whole_hand_discard_effect_template('You may discard your hand.'))
        for text in ('Each player on your team discards their hand.',
                     'Discard any number of cards from your hand.',
                     'Each player discards their hand, then draws that many cards.',
                     'Discard your hand, then draw two cards. You win the game.'):
            ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=load_default_capability_registry())
            self.assertNotEqual('exact',ir.status,text)
        from quorune.semantic_runtime.whole_hand_discard import WholeHandDiscardHandler
        from quorune.semantic_runtime.context import ReadOnlyHandlerContext
        context=ReadOnlyHandlerContext.from_sequences(actor='A',default_reason='Discard witness',seats='ABCD',active_seats='ABCD',apnap_order='BCDA')
        base={'op':'discard_whole_hands','actor':'A','players':'all'}
        for change in ({'actor':'B'},{'players':['B','C']},{'players':True},{'extra':0}):
            with self.assertRaises(ValueError):WholeHandDiscardHandler().lower({**base,**change},context)
        data=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        data['capabilities']=[r for r in data['capabilities'] if r['id']!='zone.discard.whole_hand']
        ir=compile_oracle_card(replace(query_record('Discard your hand.'),type_line='Sorcery'),capability_registry=CapabilityRegistry(data))
        self.assertNotEqual('exact',ir.status)

    def test_whole_hand_owner_unknown_membership_is_not_known_empty(self):
        from types import SimpleNamespace
        from quorune.whole_hand_discard import resolve_whole_hand_discard
        from quorune.whole_hand_discard_model import DiscardWholeHandsIntent
        from quorune.errors import GameRuleError
        host=SimpleNamespace(active_seats=['A'],state=SimpleNamespace(players={'A':SimpleNamespace(zones={'hand':['missing']})},cards={}))
        commit=unittest.mock.Mock()
        with self.assertRaises(GameRuleError):resolve_whole_hand_discard(host,DiscardWholeHandsIntent('A',('A',),'Missing hand'),commit_batch=commit)
        commit.assert_not_called()


class WholeHandDiscardRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'discard.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/whole-hand-discard-cards.json',
            ROOT/'tests/fixtures/batched-support-assurance-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Whole hand witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def test_original_batch_carriers_with_residual_siblings_reject_runtime_admission(self):
        from high_risk_interaction_support import _observed_piece_ids
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.compiler.unlock_frontier import analyze_card_unlocks
        from quorune.semantics import SemanticRegistry

        boundaries = (
            ('Rasputin Dreamweaver', 'counter.placement.quantity_replacement',
             ('residual.replacement.damage-prevention',)),
            ('Rotating Fireplace', 'mana.production.public_quantity',
             ('residual.replacement.replacement-applicability',
              'residual.replacement.self-replacement-and-prevention-ordering')),
            ('The Flame of Keld', 'zone.discard.whole_hand',
             ('residual.card_form.ordinary-saga-chapter-event-binding',)),
            ('Chandra Ablaze', 'zone.discard.whole_hand',
             ('residual.target_or_choice.conditional-effect',
              'residual.target_or_choice.target-predicate')),
        )
        for name, capability, residuals in boundaries:
            with self.subTest(card=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(record, capability_registry=self.registry,
                                         capability_profile='commander_review')
                program = compile_best_available_card_program(self.db, record,
                    semantic_registry=SemanticRegistry(), capability_registry=self.registry,
                    capability_profile='commander_review')
                row = analyze_card_unlocks(ir, program=program, program_error=None,
                    capabilities=self.registry, profile='commander_review')
                self.assertLessEqual({'capability.' + capability, *residuals}, _observed_piece_ids(row))
                self.assertEqual('residual', row['card_program_status'])
                self.assertIsNone(row['hard_construction_failure'])
                self.assertEqual('unresolved', program.trust_closure['trust_basis'])
                binding = bind_card_program_runtime(program, capability_registry=self.registry,
                                                    profile='commander_review')
                self.assertFalse(binding['strict_capability_ready'])
                self.assertFalse(binding['compatible_ready'])
                self.assertIn('trust_basis:unresolved', binding['blockers'])

    def test_actual_wits_end_revalidates_departed_player_without_discarding_survivors(self):
        session = self.session(285013)
        engine = session.engine
        spell = self.add(engine, "Wit's End", zone='hand')
        private = self.add(engine, 'Generic Bound Growth', seat='C', zone='hand', ref='private-surviving-hand')
        action = self.ready(session, spell, {'B':2, 'C':5})
        self.assertIn('B', action['target_schema']['legal_refs'])
        self.assertNotIn(private.ref, str(session.packet('pilot:B', full=True)))
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:C', {'action_id':action['id'], 'targets':['B'], 'pay':'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id':action['id'], 'targets':['B'], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(8):
            if engine.state.priority_player == 'B':
                break
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual('B', engine.state.priority_player)
        departed = session.act('pilot:B', {'action_id':'concede', 'choices':{'confirm_concede':True}})
        self.assertTrue(departed.ok, departed.summary)
        self.assertFalse(engine.state.players['B'].in_game)
        surviving_hands = {seat: tuple(engine.state.players[seat].zones['hand']) for seat in 'ACD'}
        self.resolve(session)
        self.assertEqual('graveyard', spell.zone)
        self.assertEqual(surviving_hands, {seat:tuple(engine.state.players[seat].zones['hand']) for seat in 'ACD'})
        self.assertEqual('hand', private.zone)
        self.replay(session, load=True)

    def test_actual_wheel_discards_all_hands_before_drawing_and_replays(self):
        session=self.session(285001);engine=session.engine
        spell=self.add(engine,'Wheel of Fortune',zone='hand')
        originals={seat:tuple(session.state.players[seat].zones['hand']) for seat in 'ABCD'}
        originals['A']=tuple(i for i in originals['A'] if i!=spell.object_id)
        private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='private-wheel-c')
        originals['C']=(*originals['C'],private.object_id)
        action=self.ready(session,spell,{'R':1,'C':2});self.checkpoint(session)
        self.assertNotIn(private.ref,str(session.packet('pilot:B',full=True)))
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:C',{'action_id':action['id'],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        original=engine.move_objects_simultaneously_intent
        batches=[]
        def observe(intent):
            self.assertEqual(set().union(*(set(ids) for ids in originals.values())),
                {next(c.object_id for c in engine.state.cards.values() if c.ref==ref) for ref in intent.object_refs})
            batches.append(intent)
            return original(intent)
        with patch.object(engine,'move_objects_simultaneously_intent',side_effect=observe):self.resolve(session)
        self.assertEqual(1,len(batches))
        for seat in 'ABCD':
            self.assertEqual(7,len(session.state.players[seat].zones['hand']))
            self.assertTrue(all(session.state.cards[i].zone=='graveyard' for i in originals[seat]))
        self.replay(session,load=True)

    def test_actual_wager_known_empty_hand_still_draws_after_discard(self):
        session=self.session(285002);engine=session.engine
        for object_id in tuple(session.state.players['A'].zones['hand']):engine.move_card(object_id,'library',log=False)
        spell=self.add(engine,'Dangerous Wager',zone='hand')
        action=self.ready(session,spell,{'R':1,'C':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(2,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)

    def test_whole_hand_discard_target_and_invalid_response_roll_back(self):
        session=self.session(285003);engine=session.engine
        spell=self.add(engine,'Generic Whole Hand Target',zone='hand')
        a_hand=tuple(engine.state.players['A'].zones['hand']);b_hand=tuple(engine.state.players['B'].zones['hand'])
        action=self.ready(session,spell,{});self.checkpoint(session);before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':action['id'],'targets':['A'],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual([],engine.state.players['B'].zones['hand'])
        self.assertTrue(all(engine.state.cards[i].zone=='graveyard' for i in b_hand))
        self.assertEqual(set(a_hand)-{spell.object_id},set(engine.state.players['A'].zones['hand']))
        self.replay(session,load=True)

    def test_whole_hand_discard_pending_replacement_save_load_and_replay(self):
        session=self.session(285004);engine=session.engine
        self.add(engine,'Dauthi Voidwalker');self.add(engine,'Dauthi Voidwalker',ref='second-voidwalker')
        for seat in 'ABCD':
            for object_id in tuple(engine.state.players[seat].zones['hand']):engine.move_card(object_id,'library',log=False)
        private=self.add(engine,'Generic Bound Growth',seat='B',zone='hand',ref='pending-private-b')
        spell=self.add(engine,'Wheel of Fortune',zone='hand')
        originals={s:tuple(engine.state.players[s].zones['hand']) for s in 'ABCD'}
        originals['A']=tuple(i for i in originals['A'] if i!=spell.object_id)
        action=self.ready(session,spell,{'R':1,'C':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        pending=self.resolve(session);self.assertEqual('replacement.order',pending.kind)
        self.assertNotIn(private.ref,str(session.packet('pilot:C',full=True)))
        self.assertTrue(all(engine.state.cards[i].zone=='hand' for ids in originals.values() for i in ids))
        resumed=self.replay(session,load=True)
        packet=resumed.packet(resumed.pending_principals()[0],full=True)['decision']
        choice=packet['ctx']['options'][0]['id']
        principal=resumed.pending_principals()[0];wrong='pilot:A' if principal!='pilot:A' else 'pilot:C'
        before=authoritative_state_hash(resumed.state)
        rejected=resumed.act(wrong,{'action_id':'choose','replacement':choice})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(resumed.state))
        rejected=resumed.act(principal,{'action_id':'choose','replacement':'invalid-replacement'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(resumed.state))
        for _ in range(64):
            decision=resumed.state.pending_decision
            if decision is None or decision.kind!='replacement.order':break
            principal=resumed.pending_principals()[0];packet=resumed.packet(principal,full=True)['decision']
            accepted=resumed.act(principal,{'action_id':'choose','replacement':packet['ctx']['options'][0]['id']})
            self.assertTrue(accepted.ok,accepted.summary)
        else:self.fail('Replacement journal did not finish')
        self.resolve(resumed)
        for seat in 'ABCD':
            self.assertEqual(7,len(resumed.state.players[seat].zones['hand']))
            self.assertTrue(all(resumed.state.cards[i].zone==('graveyard' if seat=='A' else 'exile') for i in originals[seat]))
        self.replay(resumed,load=True)

    def test_whole_hand_discard_omitted_batch_mutant_is_killed(self):
        from quorune import whole_hand_discard as owner
        with patch.object(owner,'resolve_whole_hand_discard',return_value=()):
            with self.assertRaises(AssertionError):self.test_actual_wheel_discards_all_hands_before_drawing_and_replays()

    def test_actual_peer_counts_graveyard_types_after_hand_discard(self):
        session=self.session(285005);engine=session.engine
        for object_id in tuple(engine.state.players['A'].zones['hand']):engine.move_card(object_id,'library',log=False)
        hand=self.add(engine,'Generic Bound Body',zone='hand')
        spell=self.add(engine,'Peer Past the Veil',zone='hand')
        action=self.ready(session,spell,{'R':1,'G':1,'C':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('graveyard',session.state.cards[hand.object_id].zone)
        self.assertEqual(1,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)

    def test_actual_change_of_fortune_counts_current_turn_discards(self):
        session=self.session(285006);engine=session.engine
        for object_id in tuple(engine.state.players['A'].zones['hand']):engine.move_card(object_id,'library',log=False)
        body=self.add(engine,'Generic Bound Body',zone='hand')
        spell=self.add(engine,'Change of Fortune',zone='hand')
        action=self.ready(session,spell,{'R':1,'C':3});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('graveyard',session.state.cards[body.object_id].zone)
        self.assertEqual(1,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)

    def test_actual_reveler_entry_discard_draw_is_trusted_and_replayed(self):
        session=self.session(285008);engine=session.engine
        spell=self.add(engine,'Bedlam Reveler',zone='hand')
        hand=tuple(i for i in engine.state.players['A'].zones['hand'] if i!=spell.object_id)
        action=self.ready(session,spell,{'R':2,'C':6});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('battlefield',session.state.cards[spell.object_id].zone)
        self.assertTrue(all(session.state.cards[i].zone=='graveyard' for i in hand))
        self.assertEqual(3,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)

    def test_actual_discard_history_counts_redirection_from_opponent_controller(self):
        session=self.session(285009);engine=session.engine
        self.add(engine,'Dauthi Voidwalker',seat='B')
        for object_id in tuple(engine.state.players['A'].zones['hand']):engine.move_card(object_id,'library',log=False)
        discarded=self.add(engine,'Generic Bound Body',zone='hand')
        spell=self.add(engine,'Change of Fortune',zone='hand')
        action=self.ready(session,spell,{'R':1,'C':3});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('exile',session.state.cards[discarded.object_id].zone)
        self.assertEqual(1,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)

    def test_whole_hand_pending_membership_change_rejects_before_draw(self):
        session=self.session(285007);engine=session.engine
        self.add(engine,'Dauthi Voidwalker');self.add(engine,'Dauthi Voidwalker',ref='pending-second-voidwalker')
        spell=self.add(engine,'Wheel of Fortune',zone='hand')
        action=self.ready(session,spell,{'R':1,'C':2})
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        pending=self.resolve(session);self.assertEqual('replacement.order',pending.kind)
        principal=session.pending_principals()[0];packet=session.packet(principal,full=True)['decision']
        # Owner diagnostic: introduce a new hand member behind a pending
        # replacement. A continuation must not resample a larger discard set.
        self.add(engine,'Generic Bound Growth',seat='B',zone='hand',ref='stale-new-hand-member')
        before=authoritative_state_hash(session.state)
        result=session.act(principal,{'action_id':'choose','replacement':packet['ctx']['options'][0]['id']})
        self.assertFalse(result.ok,result.summary)
        self.assertEqual(before,authoritative_state_hash(session.state))


if __name__=='__main__':unittest.main()
