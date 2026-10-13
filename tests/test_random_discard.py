from __future__ import annotations

"""Pinned CR701.9b: random selection replaces the affected player's choice."""

from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.random_discard import RandomDiscardIntent, resolve_random_discard
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import BoundEffectProgramRuntimeTests, record
from test_resolution_public_conditions import IndependentDualSourceEventRuntimeTests


class RandomDiscardCompilerTests(unittest.TestCase):
    def test_fixed_random_discard_closes_target_controller_and_apnap_sets(self):
        for text in ('Target player discards two cards at random.', 'Target opponent discards a card at random.',
                     'Discard three cards at random.', 'Each player discards a card at random.'):
            ir=compile_oracle_card(record(text,'Sorcery'),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            self.assertEqual('discard_random_cards',ir.faces[0].nodes[0].effects[0]['op'])
            self.assertIn('zone.discard.fixed_random',ir.faces[0].nodes[0].capability_dependencies)
        for text in ('Discard X cards at random.', 'Target player discards a creature card at random.',
                     'Target player discards two cards at random unless they pay 2 life.'):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Sorcery'),capability_registry=load_default_capability_registry()).status,text)
        from quorune.rules.capabilities import CapabilityRegistry
        data=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for cap in ('zone.discard.fixed_random','zone.discard.typed_cause','zone.change.destination_replacement'):
            changed={**data,'capabilities':[{**r,'status':'blocked','blockers':['test:blocked']} if r['id']==cap else r for r in data['capabilities']]}
            ir=compile_oracle_card(record('Target player discards two cards at random.','Sorcery'),capability_registry=CapabilityRegistry(changed))
            self.assertNotEqual('exact',ir.status)

    def test_random_subset_is_deterministic_distinct_and_fails_on_unknown_membership(self):
        cards={f'card:{n}':SimpleNamespace(object_id=f'card:{n}',logical_object_id=f'epoch:{n}',ref=f'hand:{n}',zone='hand',owner='A') for n in range(7)}
        host=SimpleNamespace(active_seats=['A'],state=SimpleNamespace(config=SimpleNamespace(seed=7019),cards=cards,
            players={'A':SimpleNamespace(zones={'hand':list(cards)})}))
        intent=RandomDiscardIntent('A',('A',),3,'stack:one','Random discard witness')
        selected=[]
        resolve_random_discard(host,intent,commit_batch=lambda refs:selected.append(refs))
        resolve_random_discard(host,intent,commit_batch=lambda refs:selected.append(refs))
        self.assertEqual(selected[0],selected[1]);self.assertEqual(3,len(set(selected[0])))
        import random
        expected=tuple(row.ref for row in random.Random('7019|stack:one|random-discard|A').sample(
            sorted(cards.values(),key=lambda row:row.object_id),3))
        self.assertEqual(expected,selected[0])
        with patch('quorune.random_discard.random.Random') as randomizer:
            randomizer.return_value.sample.side_effect=lambda members,count:members[:count]
            mutant=[];resolve_random_discard(host,intent,commit_batch=lambda refs:mutant.append(refs))
        self.assertNotEqual(expected,mutant[0])
        with patch('quorune.compiler.resolution_effect_templates.random_discard_effect_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record('Target player discards two cards at random.','Sorcery'),capability_registry=load_default_capability_registry()).status)
        host.state.players['A'].zones['hand'].append('missing')
        commit=Mock()
        with self.assertRaises(GameRuleError):resolve_random_discard(host,intent,commit_batch=commit)
        commit.assert_not_called()
        for count in (True,0,-1,5):
            with self.assertRaises(ValueError):RandomDiscardIntent('A',('A',),count,'stack:one','Malformed count')


class RandomDiscardActionTests(unittest.TestCase):
    add=BoundEffectProgramRuntimeTests.add
    ready=BoundEffectProgramRuntimeTests.ready
    checkpoint=BoundEffectProgramRuntimeTests.checkpoint
    resolve=BoundEffectProgramRuntimeTests.resolve
    replay=BoundEffectProgramRuntimeTests.replay
    session=BoundEffectProgramRuntimeTests.session
    finish_combat=IndependentDualSourceEventRuntimeTests.finish_combat

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'random-discard.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/random-discard-cards.json',
            ROOT/'tests/fixtures/whole-hand-discard-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Random discard witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def test_original_hymn_trusted_target_rollback_private_survivors_and_replay(self):
        session=self.session(701900);engine=session.engine
        spell=self.add(engine,'Hymn to Tourach',zone='hand',ref='original-random-hymn')
        private=self.add(engine,'Generic Bound Growth',seat='B',zone='hand',ref='random-private-survivor')
        action=self.ready(session,spell,{'B':2});self.checkpoint(session)
        self.assertNotIn(private.ref,json.dumps(session.packet('pilot:C',full=True)))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C',{'action_id':action['id'],'targets':['B'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        original={seat:tuple(player.zones['hand']) for seat,player in session.state.players.items()}
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        resumed=self.replay(session,load=True)
        self.assertIsNone(self.resolve(resumed))
        hand=resumed.state.players['B'].zones['hand'];removed=set(original['B'])-set(hand)
        self.assertEqual(2,len(removed));self.assertTrue(all(resumed.state.cards[key].zone=='graveyard' for key in removed))
        for key in hand:self.assertNotIn(resumed.state.cards[key].ref,json.dumps(resumed.packet('pilot:C',full=True)))
        self.replay(resumed,load=True)

    def test_real_random_discard_batches_apnap_hands_and_clamps_empty_short_hands(self):
        for short in (False,True):
            session=self.session(701901+int(short));engine=session.engine
            spell=self.add(engine,'Generic Random Discard All',zone='hand',ref='random-all-spell')
            if short:
                for seat,keep in (('B',0),('C',1)):
                    for key in tuple(engine.state.players[seat].zones['hand'])[keep:]:engine.move_card(key,'outside',log=False)
            action=self.ready(session,spell,{'B':1});self.checkpoint(session)
            self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
            hands={seat:tuple(player.zones['hand']) for seat,player in session.state.players.items()}
            batches=[];commit=engine.move_objects_simultaneously_intent
            def observe(intent):batches.append(intent);return commit(intent)
            with patch.object(engine,'move_objects_simultaneously_intent',side_effect=observe):self.assertIsNone(self.resolve(session))
            self.assertEqual(1,len(batches))
            for seat in 'ABCD':
                self.assertEqual(max(0,len(hands[seat])-2),len(session.state.players[seat].zones['hand']))
            self.replay(session,load=True)

    def test_real_optional_random_discard_apply_decline_and_pending_reload(self):
        for index,choice in enumerate(('apply','decline')):
            session=self.session(701910+index);engine=session.engine
            spell=self.add(engine,'Generic Optional Random Discard',zone='hand',ref='optional-random-spell')
            action=self.ready(session,spell,{'B':1});self.checkpoint(session)
            self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
            count=len(session.state.players['A'].zones['hand'])
            self.assertEqual('semantic.choice',self.resolve(session).kind)
            resumed=self.replay(session,load=True)
            result=resumed.act('pilot:A',{'action_id':'choose','choice':choice})
            self.assertTrue(result.ok,result.summary);self.resolve(resumed)
            self.assertEqual(count-int(choice=='apply'),len(resumed.state.players['A'].zones['hand']))
            self.replay(resumed,load=True)

    def test_random_discard_replacement_pending_selection_reload_rollback_and_replay(self):
        session=self.session(701920);engine=session.engine
        self.add(engine,'Dauthi Voidwalker',ref='random-replace-first')
        self.add(engine,'Dauthi Voidwalker',ref='random-replace-second')
        spell=self.add(engine,'Hymn to Tourach',zone='hand',ref='random-replacement-hymn')
        hand=tuple(session.state.players['B'].zones['hand'])
        action=self.ready(session,spell,{'B':2});self.checkpoint(session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':['B'],'pay':'auto'}).ok)
        self.assertEqual('replacement.order',self.resolve(session).kind)
        stale=self.replay(session,load=True)
        stale.engine.move_card(hand[-1],'outside',log=False,reason='stale random-hand control')
        principal=stale.pending_principals()[0]
        options=stale.packet(principal,full=True)['decision']['ctx']['options']
        before_stale=authoritative_state_hash(stale.state)
        rejected=stale.act(principal,{'action_id':'choose','replacement':options[0]['id']})
        self.assertFalse(rejected.ok)
        self.assertIn('membership changed',rejected.summary)
        self.assertEqual(before_stale,authoritative_state_hash(stale.state))
        resumed=self.replay(session,load=True)
        before=authoritative_state_hash(resumed.state)
        self.assertFalse(resumed.act('pilot:C',{'action_id':'choose','replacement':'invalid'}).ok)
        self.assertEqual(before,authoritative_state_hash(resumed.state))
        for _ in range(16):
            pending=resumed.state.pending_decision
            if pending is None or pending.kind!='replacement.order':break
            principal=resumed.pending_principals()[0]
            options=resumed.packet(principal,full=True)['decision']['ctx']['options']
            result=resumed.act(principal,{'action_id':'choose','replacement':options[0]['id']})
            self.assertTrue(result.ok,result.summary)
        else:self.fail('Random discard replacements did not finish')
        self.resolve(resumed)
        self.assertEqual(2,sum(resumed.state.cards[key].zone=='exile' for key in hand))
        self.assertEqual(len(hand)-2,len(resumed.state.players['B'].zones['hand']))
        self.replay(resumed,load=True)

    def test_original_entry_and_combat_random_discard_keep_source_and_recipient(self):
        session=self.session(701930);engine=session.engine
        source=self.add(engine,'Spellgorger Barbarian',zone='hand',ref='original-discard-entry')
        action=self.ready(session,source,{'R':1,'C':3});self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand'])
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before-2,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)
        from quorune.model import CombatState
        for index,own_source in enumerate((True,False)):
            session=self.session(701931+index);engine=session.engine
            observer=self.add(engine,'Hypnotic Specter',ref='original-random-damage-source')
            other=self.add(engine,'Generic Bound Body',ref='other-random-damage-source')
            for card in (observer,other):card.acquired_control_turn_count=-1
            attacker=observer if own_source else other
            engine.permissions.invalidate_current();engine.state.pending_decision=None
            engine.state.priority_player=None;engine.state.priority_passes=[]
            engine.state.active_player='A';engine.state.phase='combat';engine.state.step='declare_attackers'
            engine.state.phase_index=5;engine.state.combat=CombatState();engine._issue_attackers()
            self.checkpoint(session);hands={seat:len(player.zones['hand']) for seat,player in session.state.players.items()}
            accepted=session.act('pilot:A',{'action_id':'attack','attackers':{attacker.ref:'B'}})
            self.assertTrue(accepted.ok,accepted.summary)
            resumed=self.finish_combat(session,pending_reload=True)
            self.assertEqual(hands['B']-int(own_source),len(resumed.state.players['B'].zones['hand']))
            for seat in 'ACD':self.assertEqual(hands[seat],len(resumed.state.players[seat].zones['hand']))
            self.replay(resumed,load=True)

    def test_original_goblin_lore_draws_before_random_discard_and_replays(self):
        session=self.session(701940);engine=session.engine
        spell=self.add(engine,'Goblin Lore',zone='hand',ref='original-goblin-lore')
        action=self.ready(session,spell,{'R':1,'C':1});self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand'])
        before_draws=sum(event.code=='card.draw' for event in session.state.events)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        self.assertEqual(before_draws+4,sum(event.code=='card.draw' for event in session.state.events))
        self.replay(session,load=True)

    def test_two_random_instructions_keep_distinct_persisted_sampling_keys(self):
        session=self.session(701941);engine=session.engine
        spell=self.add(engine,'Generic Two Random Discards',zone='hand',ref='two-random-instructions')
        action=self.ready(session,spell,{'B':1});self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand'])
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        from quorune.semantic_runtime import executor
        original=executor.execute_random_discard;keys=[]
        def observe(sink,intent):keys.append(intent.randomization_key);return original(sink,intent)
        with patch.object(executor,'execute_random_discard',side_effect=observe):self.assertIsNone(self.resolve(session))
        self.assertEqual(2,len(keys));self.assertNotEqual(keys[0],keys[1])
        self.assertEqual(before-3,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)
