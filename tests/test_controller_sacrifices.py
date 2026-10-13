from __future__ import annotations

"""Pinned CR701.21a and 608.2c: the resolving controller sacrifices its permanent."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as bound_witnesses
from test_bound_effect_programs import record


class ControllerSacrificeCompilerTests(unittest.TestCase):
    def test_mandatory_controller_sacrifice_uses_affected_player_choice(self):
        cap=load_default_capability_registry()
        for text in ('Sacrifice a creature.', 'You sacrifice a permanent.', 'Sacrifice two lands.'):
            ir=compile_oracle_card(record(text,'Sorcery'),capability_registry=cap)
            self.assertEqual('exact',ir.status,ir.material_residuals)
            effect=ir.faces[0].nodes[0].effects[0]
            self.assertEqual(['$controller'],effect['players'])
            self.assertEqual('sacrifice',effect['then'])
            self.assertIsNone(ir.faces[0].nodes[0].target_schema)
        for text in ('Sacrifice another creature.', 'Sacrifice any number of creatures.',
                     'You sacrifices a creature.', 'Target player sacrifice a creature.',
                     'Sacrifice a creature. If you do, draw cards equal to its power.'):
            self.assertNotEqual('exact',compile_oracle_card(record(text,'Sorcery'),capability_registry=cap).status,text)
        text='At the beginning of your upkeep, sacrifice a Homunculus.'
        ir=compile_oracle_card(record(text,'Creature'),capability_registry=cap)
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertEqual(['homunculus'],ir.faces[0].nodes[0].effects[0]['predicate']['subtypes_all'])
        from quorune.compiler.affected_player_sacrifice_templates import fixed_affected_player_sacrifice_effect_template
        from quorune.rules.affected_player_sacrifice_capability_shapes import fixed_affected_player_sacrifice_node_capabilities
        template=fixed_affected_player_sacrifice_effect_template('Sacrifice a Homunculus.')
        effect=template.effects[0]
        for change in ({'count':True},{'players':['$context.player']},{'then':'destroy'}):
            self.assertEqual((),fixed_affected_player_sacrifice_node_capabilities(
                effects=({**effect,**change},),target_schema=None,mechanic_ids=template.mechanics))
        for predicate_change in ({'types_all':[]},{'owner':'A'},{'keywords_all':['flying']}):
            changed={**effect,'predicate':{**effect['predicate'],**predicate_change}}
            self.assertEqual((),fixed_affected_player_sacrifice_node_capabilities(
                effects=(changed,),target_schema=None,mechanic_ids=template.mechanics))
        with patch('quorune.compiler.resolution_effect_templates.fixed_affected_player_sacrifice_effect_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(record('Sacrifice a creature.','Sorcery'),capability_registry=cap).status)


class ControllerSacrificeActionTests(unittest.TestCase):
    add=bound_witnesses.BoundEffectProgramRuntimeTests.add
    ready=bound_witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=bound_witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=bound_witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=bound_witnesses.BoundEffectProgramRuntimeTests.replay
    session=bound_witnesses.BoundEffectProgramRuntimeTests.session

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'controller-sacrifice.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/controller-sacrifice-cards.json',ROOT/'tests/fixtures/whole-hand-discard-cards.json',
            ROOT/'tests/fixtures/conditional-exile-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Controller sacrifice witness',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def complete_sacrifice(self,session,ref):
        self.assertEqual('choice.apnap',self.resolve(session).kind)
        packet=session.packet('pilot:A',full=True)['decision']['ctx']
        self.assertIn(ref,str(packet))
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',{'action_id':'choose','cards':[ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        resumed=self.replay(session,load=True)
        accepted=resumed.act('pilot:A',{'action_id':'choose','cards':[ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(resumed)
        self.replay(resumed,load=True)
        return resumed

    def test_original_entry_sacrifice_can_choose_the_source_or_another_controlled_creature(self):
        for index,self_choice in enumerate((True,False)):
            session=self.session(701210+index);engine=session.engine
            source=self.add(engine,'Accursed Centaur',zone='hand',ref='original-controller-sacrifice')
            other=self.add(engine,'Generic Bound Body',ref='own-sacrifice-contrast')
            enemy=self.add(engine,'Generic Bound Body',seat='B',ref='other-controller-sacrifice')
            action=self.ready(session,source,{'B':1});self.checkpoint(session)
            self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
            chosen=source if self_choice else other
            self.assertEqual('choice.apnap',self.resolve(session).kind)
            before=authoritative_state_hash(session.state)
            self.assertFalse(session.act('pilot:A',{'action_id':'choose','cards':[enemy.ref]}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
            resumed=self.complete_sacrifice(session,chosen.ref)
            self.assertEqual('graveyard',resumed.state.cards[chosen.object_id].zone)
            self.assertEqual('battlefield',resumed.state.cards[enemy.object_id].zone)
            survivor=other if self_choice else source
            self.assertEqual('battlefield',resumed.state.cards[survivor.object_id].zone)

    def test_original_lotus_field_hexproof_does_not_prevent_controller_sacrifice(self):
        # CR 702.11b restricts opposing targets; CR 701.21 sacrifice is not targeting.
        session=self.session(701216);engine=session.engine
        field=self.add(engine,'Lotus Field',zone='hand',ref='hexproof-sacrifice-field')
        spell=self.add(engine,'Perilous Research',zone='hand',ref='controller-land-sacrifice')
        self.ready(session,field,{'U':1,'C':1});self.checkpoint(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions if row.get('card')==field.ref)
        result=session.act('pilot:A',{'action_id':action['id']})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('choice.apnap',self.resolve(session).kind)
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','cards':[field.ref]}).ok)
        self.resolve(session)
        self.assertEqual('graveyard',session.state.cards[field.object_id].zone)
        self.replay(session,load=True)

        session=self.session(701217);engine=session.engine
        field=self.add(engine,'Lotus Field',ref='hexproof-research-field')
        spell=self.add(engine,'Perilous Research',zone='hand',ref='hexproof-research')
        action=self.ready(session,spell,{'U':1,'C':1});self.checkpoint(session)
        self.assertIn('hexproof',engine._combat_keywords(field))
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        hand_size=len(session.state.players['A'].zones['hand'])
        resumed=self.complete_sacrifice(session,field.ref)
        self.assertEqual('graveyard',resumed.state.cards[field.object_id].zone)
        self.assertEqual(hand_size+2,len(resumed.state.players['A'].zones['hand']))

    def test_subtype_sacrifice_uses_current_type_after_response_and_rejects_stale_choice(self):
        session=self.session(701220);engine=session.engine
        self.add(engine,'Puppet Conjurer',ref='subtype-observer')
        body=self.add(engine,'Generic Sacrifice Homunculus',ref='original-subtype-subject')
        other=self.add(engine,'Generic Bound Body',ref='become-subtype-subject')
        response=self.add(engine,'Generic Sacrifice Type Change',zone='hand',ref='type-change-response')
        action=self.ready(session,response,{'U':1})
        engine.state.active_player='A';engine.state.phase='beginning';engine.state.step='upkeep';engine.state.phase_index=1
        engine.permissions.invalidate_current();engine.state.pending_decision=None;engine.state.priority_player=None;engine.state.priority_passes=[]
        engine._enter_step()
        # The trigger is queued by real step entry; replay begins before the response.
        engine.state.players['A'].mana_pool['U']=1
        engine.permissions.invalidate_current();engine.state.pending_decision=None;engine._grant_priority('A');engine.pump()
        self.checkpoint(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row.get('card')==response.ref)
        accepted=session.act('pilot:A',{'action_id':offered['id'],'targets':[other.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('choice.apnap',self.resolve(session).kind)
        packet=session.packet('pilot:A',full=True)['decision']['ctx']
        self.assertIn(other.ref,str(packet))
        stale=self.replay(session,load=True)
        stale.engine.move_card(body.object_id,'outside',log=False)
        before=authoritative_state_hash(stale.state)
        self.assertFalse(stale.act('pilot:A',{'action_id':'choose','cards':[body.ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(stale.state))
        resumed=self.complete_sacrifice(session,other.ref)
        self.assertEqual('graveyard',resumed.state.cards[other.object_id].zone)

    def test_original_upkeep_sacrifice_is_current_controller_scoped_and_replays(self):
        session=self.session(701214);engine=session.engine
        spell=self.add(engine,'Smothering Abomination',zone='hand',ref='original-upkeep-sacrifice')
        body=self.add(engine,'Generic Bound Body',ref='upkeep-sacrifice-choice')
        action=self.ready(session,spell,{'B':2,'C':2});self.checkpoint(session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.resolve(session)
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.priority_player=None;engine.state.priority_passes=[]
        engine.state.active_player='A';engine.state.phase='beginning';engine.state.step='untap';engine.state.phase_index=0
        engine._grant_priority('A');engine.pump()
        self.checkpoint(session);before=len(session.state.players['A'].zones['hand'])
        for _ in range(8):
            if session.state.step=='upkeep':break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(result.ok,result.summary)
        self.assertEqual('upkeep',session.state.step)
        resumed=self.complete_sacrifice(session,body.ref)
        self.assertEqual('graveyard',resumed.state.cards[body.object_id].zone)
        self.assertEqual(before+1,len(resumed.state.players['A'].zones['hand']))

    def test_original_perilous_research_draws_before_controller_sacrifice_choice(self):
        session=self.session(701215);engine=session.engine
        spell=self.add(engine,'Perilous Research',zone='hand',ref='original-research-sacrifice')
        body=self.add(engine,'Generic Bound Body',ref='research-sacrifice-choice')
        action=self.ready(session,spell,{'U':1,'C':1});self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand'])
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual('choice.apnap',self.resolve(session).kind)
        self.assertEqual(before+1,len(session.state.players['A'].zones['hand']))
        resumed=self.complete_sacrifice(session,body.ref)
        self.assertEqual('graveyard',resumed.state.cards[body.object_id].zone)
        self.assertEqual(before+1,len(resumed.state.players['A'].zones['hand']))

    def test_original_puppet_upkeep_uses_current_creature_subtype_and_empty_sets(self):
        for index,eligible in enumerate((True,False)):
            session=self.session(701216+index);engine=session.engine
            source=self.add(engine,'Puppet Conjurer',ref='original-puppet-sacrifice')
            own=self.add(engine,'Generic Sacrifice Homunculus' if eligible else 'Generic Bound Body',ref='own-subtype-choice')
            enemy=self.add(engine,'Generic Sacrifice Homunculus',seat='B',ref='other-subtype-choice')
            engine.state.active_player='A';engine.state.phase='beginning';engine.state.step='untap';engine.state.phase_index=0
            engine._grant_priority('A');engine.pump();self.checkpoint(session)
            for _ in range(8):
                if session.state.step=='upkeep':break
                result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
            self.assertEqual('upkeep',session.state.step)
            if eligible:
                resumed=self.complete_sacrifice(session,own.ref)
                self.assertEqual('graveyard',resumed.state.cards[own.object_id].zone)
            else:
                self.assertIsNone(self.resolve(session));resumed=session;self.replay(resumed,load=True)
                self.assertEqual('battlefield',resumed.state.cards[own.object_id].zone)
            self.assertEqual('battlefield',resumed.state.cards[enemy.object_id].zone)

    def test_original_controller_sacrifice_keeps_choice_across_destination_replacement(self):
        session=self.session(701224);engine=session.engine
        first=self.add(engine,'Dauthi Voidwalker',seat='B',ref='controller-replacement-first')
        second=self.add(engine,'Dauthi Voidwalker',seat='B',ref='controller-replacement-second')
        body=self.add(engine,'Generic Bound Body',ref='replacement-sacrifice-subject')
        spell=self.add(engine,'Perilous Research',zone='hand',ref='replacement-research')
        action=self.ready(session,spell,{'U':1,'C':1});self.checkpoint(session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual('choice.apnap',self.resolve(session).kind)
        accepted=session.act('pilot:A',{'action_id':'choose','cards':[body.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertEqual('battlefield',session.state.cards[body.object_id].zone)
        resumed=self.replay(session,load=True)
        principal=resumed.pending_principals()[0]
        options=resumed.packet(principal,full=True)['decision']['ctx']['options']
        before=authoritative_state_hash(resumed.state)
        self.assertFalse(resumed.act('pilot:D',{'action_id':'choose','replacement':options[0]['id']}).ok)
        self.assertEqual(before,authoritative_state_hash(resumed.state))
        for _ in range(8):
            pending=resumed.state.pending_decision
            if pending is None or pending.kind!='replacement.order':break
            principal=resumed.pending_principals()[0]
            options=resumed.packet(principal,full=True)['decision']['ctx']['options']
            result=resumed.act(principal,{'action_id':'choose','replacement':options[0]['id']})
            self.assertTrue(result.ok,result.summary)
        else:self.fail('Controller sacrifice replacement did not finish')
        self.resolve(resumed)
        self.assertEqual('exile',resumed.state.cards[body.object_id].zone)
        self.assertEqual('battlefield',resumed.state.cards[first.object_id].zone)
        self.assertEqual('battlefield',resumed.state.cards[second.object_id].zone)
        self.replay(resumed,load=True)
