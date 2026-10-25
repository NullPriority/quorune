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
from quorune.compiler.activated_ability_catalog import with_activated_ability_catalog
from scripts.build_test_database import build_fixture_database
import test_static_player_restrictions as controls
import test_ability_label_composition as journeys

NAMES=('Sai, Master Thopterist',"Gonti's Aether Heart")


class ActivationViewEquivalenceTests(unittest.TestCase):
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
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'activation-views.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/activation-view-equivalence-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Owned activation views',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def activation(self,s,source,*,costs=(),payment=None,seat='A',valid=True):
        self.priority(s,seat);row=next(r for r in self.actions(s,seat) if r['id'].startswith('activate:'+source.ref+':'))
        command={'action_id':row['id'],'pay':'auto' if payment is None else 'manual'}
        if costs:command['cost_cards']=[c.ref for c in costs]
        if payment is not None:command['payment']=payment
        if not valid:self.reject(s,command,seat=seat);return
        self.reject(s,command,seat='D');result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def test_originals_normally_bind_complete_owned_catalog_prices_and_results(self):
        from quorune.card_programs.activation_view_equivalence import shadowed_reviewed_activation_view_keys as shadow
        for i,name in enumerate(NAMES):
            s=self.session(824000+i);source=self.add(s.engine,name,zone='hand');bound=self.binding(s,source)
            self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
            current=s.engine.semantics.programs_for_oracle(source.oracle_id)
            old=tuple(with_activated_ability_catalog(self.db.lookup(name),SemanticRegistry().programs_for_oracle(source.oracle_id),reference_programs=current))
            keys=shadow(self.db.lookup(name),current,old,capability_registry=self.registry,capability_profile='commander_review');self.assertTrue(keys,name)
            for key in keys:
                p=s.engine.semantics.get(key);self.assertTrue(str(p.provenance.get('authored_by','')).startswith('oracle-ir-v'));self.assertIsNotNone(p.cost_schema);self.assertTrue(p.capability_closure['trusted'])

    def test_canonical_full_prices_results_source_and_target_guards_do_not_mutate_inputs(self):
        from quorune.card_programs.activation_view_equivalence import shadowed_reviewed_activation_view_keys as shadow
        for i,name in enumerate(NAMES):
            s=self.session(824010+i);source=self.add(s.engine,name);record=self.db.lookup(name);current=s.engine.semantics.programs_for_oracle(source.oracle_id)
            old=tuple(with_activated_ability_catalog(record,SemanticRegistry().programs_for_oracle(source.oracle_id),reference_programs=current))
            def check(g=current,r=old):return shadow(record,g,r,capability_registry=self.registry,capability_profile='commander_review')
            keys=check();self.assertTrue(keys);selected=next(p for p in old if p.key in keys);p=next(p for p in current if p.key==selected.key)
            before=json.dumps([v.to_dict() for v in old],sort_keys=True)
            wrong=deepcopy(p.cost_schema);wrong['energy_payment']=7 if name==NAMES[1] else 1
            self.assertNotIn(selected.key,check(r=[replace(selected,cost_schema=wrong)]))
            effects=deepcopy(selected.effects);effects.append({'op':'draw','player':'$controller','count':1})
            self.assertNotIn(selected.key,check(r=[replace(selected,effects=effects)]))
            changed=deepcopy(selected.handlers);changed[0]['ability']['uncompiled_costs']=['unsupported extra price']
            self.assertNotIn(selected.key,check(r=[replace(selected,handlers=changed)]))
            self.assertNotIn(selected.key,check(r=[replace(selected,event_condition={'field':'controller','op':'eq','value':'B'})]))
            self.assertNotIn(selected.key,check(r=[replace(selected,target_schema={'zones':['battlefield'],'count':1,'creature':True})]))
            self.assertFalse(check(r=[replace(selected,provenance={**selected.provenance,'source_rulings_hash':'0'*64})]))
            self.assertFalse(check(g=[replace(v,trust_level='provisional') for v in current]))
            self.assertFalse(shadow(record,current,old,capability_registry=None,capability_profile='commander_review'))
            self.assertNotIn(selected.key,check(g=[*current,*current]));self.assertEqual(before,json.dumps([v.to_dict() for v in old],sort_keys=True))

    def test_token_display_prefix_changes_no_owned_characteristic_or_other_operation(self):
        from quorune.card_programs.trigger_view_equivalence import _effects
        from quorune.characteristic_evaluation import type_parts
        s=self.session(824020);source=self.add(s.engine,NAMES[0]);old=next(p for p in SemanticRegistry().programs_for_oracle(source.oracle_id) if p.event=='spell.cast')
        current=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='spell.cast')
        before=deepcopy(current.effects);self.assertEqual(_effects(old),_effects(current));self.assertEqual(before,current.effects)
        line=current.effects[0]['characteristics']['type_line'];self.assertEqual(type_parts(line),type_parts(line.removeprefix('Token ')))
        changed=deepcopy(current.effects);changed[0]['characteristics']['power']='2';self.assertNotEqual(_effects(old),_effects(replace(current,effects=changed)))

    def test_real_sai_countered_artifact_cast_and_two_distinct_artifact_price_draw_replay(self):
        s=self.session(824030);source=self.add(s.engine,NAMES[0],zone='hand');artifact=self.add(s.engine,'Sol Ring',zone='hand');body=self.add(s.engine,'Ornithopter')
        counter=self.add(s.engine,'Counterspell',seat='B',zone='hand');bounce=self.add(s.engine,'Unsummon',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool['U']=2;self.prepare(s,mana={'C':4,'U':3});self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'U':1});self.assertIsNone(self.resolve(s));self.cast(s,artifact,payment={'C':1})
        stack_ref=next(item.ref for item in s.state.stack if item.kind=='spell' and item.card_object_id==artifact.object_id)
        self.cast(s,counter,[stack_ref],seat='B',payment={'U':2});self.assertIsNone(self.resolve(s));self.assertEqual('graveyard',s.state.cards[artifact.object_id].zone)
        thopters=[c for c in s.state.cards.values() if c.is_token and c.zone=='battlefield' and c.printed_name=='Thopter'];self.assertEqual(1,len(thopters));token=thopters[0]
        self.assertEqual(1,s.engine._numeric_stat(token.object_id,'power'));self.assertIn('flying',s.engine._combat_keywords(token))
        self.activation(s,source,costs=[token,token],payment={'C':1,'U':1},valid=False)
        self.activation(s,source,costs=[source,body],payment={'C':1,'U':1},valid=False)
        self.activation(s,source,costs=[token,body],payment={'C':1,'U':1});self.assertNotEqual('battlefield',s.state.cards[token.object_id].zone);self.assertEqual('graveyard',s.state.cards[body.object_id].zone)
        self.cast(s,bounce,[source.ref],payment={'U':1})
        while s.state.stack and s.state.cards[source.object_id].zone=='battlefield':
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('hand',s.state.cards[source.object_id].zone);before=set(s.state.players['A'].zones['hand']);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        drawn=set(s.state.players['A'].zones['hand'])-before;self.assertEqual(1,len(drawn));ref=s.state.cards[next(iter(drawn))].ref
        for seat in 'BCD':self.assertNotIn(ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.private_replay(s,private)

    def test_real_gonti_energy_exile_price_extra_turn_and_countered_ability_replay(self):
        for countered in (False,True):
            s=self.session(824040+countered);source=self.add(s.engine,NAMES[1],zone='hand');zero=self.add(s.engine,'Ornithopter',zone='hand');one=self.add(s.engine,'Sol Ring',zone='hand');three=self.add(s.engine,'Manalith',zone='hand')
            denial=self.add(s.engine,'Stifle',seat='B',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');s.state.players['B'].mana_pool['U']=1
            self.prepare(s,mana={'C':10});self.checkpoint(s);self.cast(s,source,payment={'C':6});self.assertIsNone(self.resolve(s));self.assertEqual(2,s.state.players['A'].energy)
            self.cast(s,zero,payment={});self.assertIsNone(self.resolve(s));self.cast(s,one,payment={'C':1});self.assertIsNone(self.resolve(s));self.assertEqual(6,s.state.players['A'].energy)
            self.priority(s,'A');ability=next(a for a in s.engine._activated_abilities(s.state.cards[source.object_id]) if a.energy_payment==8 and a.exile_source)
            command={'action_id':'activate:'+source.ref+':'+ability.ability_id,'pay':'auto'}
            self.assertNotIn(command['action_id'],{r['id'] for r in self.actions(s)});self.reject(s,command)
            self.cast(s,three,payment={'C':3});self.assertIsNone(self.resolve(s));self.assertEqual(8,s.state.players['A'].energy)
            self.activation(s,source);self.assertEqual(0,s.state.players['A'].energy);self.assertEqual('exile',s.state.cards[source.object_id].zone);self.assertFalse(s.state.extra_turns)
            activated=s.state.stack[-1].ref;s=self.replay(s,load=True)
            if countered:self.cast(s,denial,[activated],seat='B',payment={'U':1})
            self.assertIsNone(self.resolve(s));self.assertEqual(0,s.state.players['A'].energy);self.assertEqual('exile',s.state.cards[source.object_id].zone)
            self.assertEqual(0 if countered else 1,len(s.state.extra_turns))
            if not countered:
                scheduled=s.state.extra_turns[0].turn_id;self.assertEqual('A',s.state.extra_turns[0].player)
                self.advance(s,lambda x:x.state.current_turn is not None and x.state.current_turn.turn_id==scheduled)
                self.assertEqual('A',s.state.active_player);self.assertTrue(s.state.current_turn.extra)
            self.private_replay(s,private)

    def test_real_gonti_foreign_artifact_and_current_controller_energy_scope(self):
        s=self.session(824050);source=self.add(s.engine,NAMES[1]);zero=self.add(s.engine,'Ornithopter',seat='B',zone='hand');one=self.add(s.engine,'Sol Ring',seat='B',zone='hand');steal=self.add(s.engine,'Steal Artifact',seat='B',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,actor='B',active='B',mana={'C':3,'U':2});self.checkpoint(s);self.cast(s,zero,seat='B',payment={});self.assertIsNone(self.resolve(s));self.assertEqual((0,0),(s.state.players['A'].energy,s.state.players['B'].energy))
        self.cast(s,steal,[source.ref],seat='B',payment={'C':2,'U':2});self.assertIsNone(self.resolve(s));self.assertEqual(('A','B'),(s.state.cards[source.object_id].owner,s.state.cards[source.object_id].controller))
        self.cast(s,one,seat='B',payment={'C':1});self.assertIsNone(self.resolve(s));self.assertEqual((0,2),(s.state.players['A'].energy,s.state.players['B'].energy));self.private_replay(s,private)

    def test_fresh_omission_or_unchecked_price_and_result_changes_fail_closed(self):
        method='test_originals_normally_bind_complete_owned_catalog_prices_and_results'
        with patch('quorune.card_programs.activation_view_equivalence.shadowed_reviewed_activation_view_keys',return_value=set()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        method='test_canonical_full_prices_results_source_and_target_guards_do_not_mutate_inputs'
        with patch('quorune.card_programs.activation_view_equivalence._catalog_preserves_cost',return_value=True):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        with patch('quorune.card_programs.reviewed_overlay._canonical_effects',return_value=[]):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
