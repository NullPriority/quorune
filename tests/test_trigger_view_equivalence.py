from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_static_player_restrictions as controls
import test_ability_label_composition as journeys

NAMES=('Spine of Ish Sah','Marionette Apprentice','Bastion of Remembrance','Simulacrum Synthesizer')


class TriggerViewEquivalenceTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.StaticPlayerRestrictionRuntimeTests.session
    add=controls.StaticPlayerRestrictionRuntimeTests.add
    prepare=controls.StaticPlayerRestrictionRuntimeTests.prepare
    checkpoint=controls.StaticPlayerRestrictionRuntimeTests.checkpoint
    resolve=controls.StaticPlayerRestrictionRuntimeTests.resolve
    replay=controls.StaticPlayerRestrictionRuntimeTests.replay
    private_replay=controls.StaticPlayerRestrictionRuntimeTests.private_replay
    priority=controls.StaticPlayerRestrictionRuntimeTests.priority
    actions=controls.StaticPlayerRestrictionRuntimeTests.actions
    cast=controls.StaticPlayerRestrictionRuntimeTests.cast
    reject=controls.StaticPlayerRestrictionRuntimeTests.reject
    binding=controls.StaticPlayerRestrictionRuntimeTests.binding
    advance=journeys.AbilityLabelCompositionTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'trigger-views.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/trigger-view-equivalence-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Current trigger views',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def tokens(self,s,name):return [c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller=='A' and c.printed_name==name]

    def choose(self,s,command,*,seat='A'):
        self.reject(s,command,seat='D');result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def sacrifice_spell(self,s,spell,price,*,seat='A'):
        self.priority(s,seat);row=next(r for r in self.actions(s,seat) if r['id']=='cast:'+spell.ref)
        command={'action_id':row['id'],'sacrifice_cards':[price.ref],'pay':'manual','payment':{'B':1}}
        self.choose(s,command,seat=seat)

    def test_four_originals_normally_close_with_one_current_owner_for_each_trigger(self):
        from quorune.card_programs.trigger_view_equivalence import shadowed_reviewed_trigger_view_keys as shadow
        for i,name in enumerate(NAMES):
            s=self.session(823000+i);source=self.add(s.engine,name,zone='hand');b=self.binding(s,source)
            self.assertTrue(b['strict_capability_ready'],(name,b['blockers']))
            current=s.engine.semantics.programs_for_oracle(source.oracle_id);reviewed=SemanticRegistry().programs_for_oracle(source.oracle_id)
            keys=shadow(self.db.lookup(name),current,reviewed,capability_registry=self.registry,capability_profile='commander_review')
            self.assertTrue(keys,name);self.assertFalse(keys & set(s.engine.semantics.keys()))

    def test_closed_normalizations_preserve_conditions_results_targets_and_input_payloads(self):
        from quorune.card_programs.trigger_view_equivalence import shadowed_reviewed_trigger_view_keys as shadow,_condition,_effects
        for i,name in enumerate(NAMES):
            s=self.session(823010+i);source=self.add(s.engine,name);record=self.db.lookup(name)
            current=s.engine.semantics.programs_for_oracle(source.oracle_id);old=SemanticRegistry().programs_for_oracle(source.oracle_id)
            def check(g=current,r=old,card=record):return shadow(card,g,r,capability_registry=self.registry,capability_profile='commander_review')
            baseline=check();self.assertTrue(baseline);previous=json.dumps([p.to_dict() for p in old],sort_keys=True)
            selected=next(p for p in old if p.key in baseline)
            for field,value in (('event_condition',{'field':'controller','op':'eq','value':'B'}),('event','spell.cast')):
                changed=replace(selected,**{field:value});self.assertNotIn(selected.key,check(r=[changed]))
            effects=deepcopy(selected.effects);effects.append({'op':'draw','player':'$controller','count':1});self.assertNotIn(selected.key,check(r=[replace(selected,effects=effects)]))
            stale=replace(selected,provenance={**selected.provenance,'source_rulings_hash':'0'*64});self.assertFalse(check(r=[stale]))
            self.assertFalse(check(g=[replace(p,trust_level='provisional') for p in current]));self.assertFalse(check(card=replace(record,layout='transform')))
            self.assertFalse(shadow(record,current,old,capability_registry=None,capability_profile='commander_review'))
            changed=replace(selected,cost_schema={'cost_text':'{1}'});self.assertNotIn(selected.key,check(r=[changed]))
            self.assertNotIn(selected.key,check(g=[*current,*current]))
            self.assertEqual(previous,json.dumps([p.to_dict() for p in old],sort_keys=True))
        leaf={'field':'previous_controller','op':'eq','value':'$source.controller'}
        self.assertEqual('controller',_condition(leaf,'creature.dies')['field']);self.assertEqual(leaf,_condition(leaf,'permanent.graveyard'))
        s=self.session(823019);source=self.add(s.engine,NAMES[0]);record=self.db.lookup(NAMES[0]);g=s.engine.semantics.programs_for_oracle(source.oracle_id);old=SemanticRegistry().programs_for_oracle(source.oracle_id)
        entry=next(p for p in old if p.event=='permanent.enter.self');bad=deepcopy(entry.target_schema);bad['groups'][0]['min']=0
        self.assertFalse(shadow(record,g,[replace(entry,target_schema=bad)],capability_registry=self.registry,capability_profile='commander_review'))
        missing=replace(entry,effects=[{'op':'damage_each_opponent','amount':1}])
        explicit=replace(entry,effects=[{'op':'damage_each_opponent','amount':1,'source':'$source'}])
        self.assertNotEqual(_effects(missing),_effects(explicit))

    def test_real_spine_entry_destroy_and_foreign_controller_return_to_owner_replay(self):
        s=self.session(823020);source=self.add(s.engine,NAMES[0],zone='hand');victim=self.add(s.engine,'Sol Ring',seat='B',ref='spine_victim_ring')
        self.add(s.engine,'Sol Ring',ref='spine_own_ring');self.add(s.engine,'Plains')
        for i in range(4):self.add(s.engine,'Island',seat='B',ref='spine_b_island'+str(i))
        steal=self.add(s.engine,'Steal Artifact',seat='B',zone='hand');destroy=self.add(s.engine,'Disenchant',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':7});self.checkpoint(s);self.cast(s,source,payment={'C':7});pending=self.resolve(s);self.assertEqual('semantic.target',pending.kind)
        self.reject(s,{'action_id':'choose','targets':['D']});self.choose(s,{'action_id':'choose','targets':[victim.ref]});s=self.replay(s,load=True);self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[victim.object_id].zone)
        self.advance(s,lambda x:x.state.active_player=='B' and x.state.phase=='precombat_main' and x.state.pending_decision.kind=='priority')
        self.cast(s,steal,[source.ref],seat='B');self.assertIsNone(self.resolve(s));self.assertEqual(('A','B'),(s.state.cards[source.object_id].owner,s.state.cards[source.object_id].controller))
        self.cast(s,destroy,[source.ref]);self.assertIsNone(self.resolve(s));self.assertEqual(('hand','A'),(s.state.cards[source.object_id].zone,s.state.cards[source.object_id].owner));self.private_replay(s,private)

    def test_real_spine_trigger_fizzles_when_its_only_target_leaves(self):
        s=self.session(823030);source=self.add(s.engine,NAMES[0],zone='hand');target=self.add(s.engine,'Fugitive Wizard',seat='B');bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool['U']=1;self.prepare(s,mana={'C':7});self.checkpoint(s);self.cast(s,source,payment={'C':7});self.assertEqual('semantic.target',self.resolve(s).kind)
        self.choose(s,{'action_id':'choose','targets':[target.ref]});self.cast(s,bounce,[target.ref],seat='B',payment={'U':1});self.assertIsNone(self.resolve(s));self.assertEqual('hand',s.state.cards[target.object_id].zone);self.assertEqual('battlefield',s.state.cards[source.object_id].zone);self.private_replay(s,private)

    def test_real_marionette_fabricate_type_union_self_exclusion_and_locked_trigger(self):
        s=self.session(823040);source=self.add(s.engine,NAMES[1],zone='hand');enemy=self.add(s.engine,'Ornithopter',seat='B')
        foreign=self.add(s.engine,'Village Rites',seat='B',zone='hand',ref='marionette_b_rites');first=self.add(s.engine,'Village Rites',zone='hand',ref='marionette_a_rites1');second=self.add(s.engine,'Village Rites',zone='hand',ref='marionette_a_rites2');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool['B']=1;self.prepare(s,mana={'C':1,'B':3});self.checkpoint(s);self.cast(s,source,payment={'C':1,'B':1});pending=self.resolve(s);self.assertEqual('semantic.choice',pending.kind)
        self.choose(s,{'action_id':'choose','choice':'token'});self.assertIsNone(self.resolve(s));servo=self.tokens(s,'Servo');self.assertEqual(1,len(servo))
        self.sacrifice_spell(s,foreign,enemy,seat='B');self.assertIsNone(self.resolve(s));self.assertEqual([40]*4,[s.state.players[x].life for x in 'ABCD'])
        self.sacrifice_spell(s,first,servo[0]);self.sacrifice_spell(s,second,source);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual([40,39,39,39],[s.state.players[x].life for x in 'ABCD']);self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.private_replay(s,private)

    def test_real_bastion_uses_last_known_controller_instead_of_owner(self):
        s=self.session(823050);source=self.add(s.engine,NAMES[2],zone='hand');body=self.add(s.engine,'Generic Bound Body')
        own=self.add(s.engine,'Village Rites',zone='hand',ref='bastion_a_rites');foreign=self.add(s.engine,'Village Rites',seat='B',zone='hand',ref='bastion_b_rites');control=self.add(s.engine,'Control Magic',seat='B',zone='hand')
        for i in range(4):self.add(s.engine,'Island',seat='B',ref='bastion_b_island'+str(i))
        self.add(s.engine,'Swamp',seat='B');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':2,'B':2});self.checkpoint(s);self.cast(s,source,payment={'C':2,'B':1});self.assertIsNone(self.resolve(s));soldiers=self.tokens(s,'Human Soldier');self.assertEqual(1,len(soldiers))
        self.sacrifice_spell(s,own,soldiers[0]);self.assertIsNone(self.resolve(s));self.assertEqual([41,39,39,39],[s.state.players[x].life for x in 'ABCD'])
        self.advance(s,lambda x:x.state.active_player=='B' and x.state.phase=='precombat_main' and x.state.pending_decision.kind=='priority')
        self.cast(s,control,[body.ref],seat='B');self.assertIsNone(self.resolve(s));self.assertEqual(('A','B'),(s.state.cards[body.object_id].owner,s.state.cards[body.object_id].controller))
        self.priority(s,'B');row=next(r for r in self.actions(s,'B') if r['id']=='cast:'+foreign.ref)
        self.choose(s,{'action_id':row['id'],'sacrifice_cards':[body.ref],'pay':'auto'},seat='B');self.assertIsNone(self.resolve(s));self.assertEqual([41,39,39,39],[s.state.players[x].life for x in 'ABCD']);self.private_replay(s,private)

    def test_real_synthesizer_private_scry_threshold_and_live_construct_definition(self):
        s=self.session(823060);source=self.add(s.engine,NAMES[3],zone='hand');small=self.add(s.engine,'Sol Ring',zone='hand');large=self.add(s.engine,'Manalith',zone='hand');destroy=self.add(s.engine,'Disenchant',zone='hand')
        secret=self.add(s.engine,'Fugitive Wizard',zone='library',ref='synth_scry_top');library=s.state.players['A'].zones['library'];library.remove(secret.object_id);library.append(secret.object_id);secret.known_to=[];secret.revealed_to=[]
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');self.prepare(s,mana={'C':7,'U':1,'W':1});self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'U':1});pending=self.resolve(s);self.assertEqual('semantic.choice',pending.kind)
        schema=s.state.pending_decision.payload_by_actor['A']['legal_actions'][0]['choice_schema'];refs=schema['legal_refs'];self.assertEqual(2,len(refs));self.assertIn(secret.ref,refs)
        for seat in 'BCD':self.assertNotIn(secret.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        s=self.replay(s,load=True);self.choose(s,{'action_id':'choose','cards':{'top':[secret.ref],'bottom':[r for r in refs if r!=secret.ref]}});self.assertIsNone(self.resolve(s));self.assertFalse(self.tokens(s,'Construct'))
        self.cast(s,small,payment={'C':1});self.assertIsNone(self.resolve(s));self.assertFalse(self.tokens(s,'Construct'))
        self.cast(s,large,payment={'C':3});self.assertIsNone(self.resolve(s));tokens=self.tokens(s,'Construct');self.assertEqual(1,len(tokens));self.assertEqual(4,s.engine._numeric_stat(tokens[0].object_id,'power'))
        self.cast(s,destroy,[large.ref],payment={'C':1,'W':1});self.assertIsNone(self.resolve(s));self.assertEqual(3,s.engine._numeric_stat(tokens[0].object_id,'power'));self.private_replay(s,private)

    def test_real_synthesizer_does_not_observe_opponent_artifact_entry(self):
        s=self.session(823070);self.add(s.engine,NAMES[3]);spell=self.add(s.engine,'Manalith',seat='B',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,actor='B',active='B',mana={'C':3});self.checkpoint(s);self.cast(s,spell,seat='B',payment={'C':3});self.assertIsNone(self.resolve(s));self.assertFalse(self.tokens(s,'Construct'));self.private_replay(s,private)

    def test_fresh_omission_and_widened_target_or_event_conditions_are_killed(self):
        method='test_four_originals_normally_close_with_one_current_owner_for_each_trigger'
        with patch('quorune.card_programs.trigger_view_equivalence.shadowed_reviewed_trigger_view_keys',return_value=set()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_closed_normalizations_preserve_conditions_results_targets_and_input_payloads'
        with patch('quorune.card_programs.trigger_view_equivalence._targets_equal',return_value=True):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        with patch('quorune.card_programs.trigger_view_equivalence._condition',return_value=None):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
