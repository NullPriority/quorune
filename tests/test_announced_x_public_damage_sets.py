"""Pinned CR107.3/120.4/120.8/608.2h/707.10: one paid X, one current public set."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.damage_capability_shapes import fixed_damage_node_capabilities
from quorune.rules.announced_x_damage_sets import announced_x_damage_set_dependencies
from quorune.query_effect_amount_model import CastXAmountSpec
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from test_declared_effect_amounts import generic_spell
import test_target_recipient_results as recipients

FIXTURE = ROOT/'tests/fixtures/announced-x-public-damage-set-cards.json'
NAMES = ('Windstorm','Earthquake','Hurricane','Squall Line','Savage Twister','Fault Line','Rolling Earthquake')


class AnnouncedXDamageSetTests(InheritedNativeFixture, unittest.TestCase):
    reject = recipients.TargetRecipientNativeTests.reject
    finish = recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); folder = Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[c for c in base['cards'] if c['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=folder/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,FIXTURE],folder/'x-damage.sqlite3')
        cls.db=CardDatabase(folder/'x-damage.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Announced X damage',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def normal_program(self,name):
        card=self.db.lookup(name,fuzzy=False);semantics=SemanticRegistry()
        register_generated_programs(self.db,semantics,[card],trust_level='provisional',
            capability_registry=self.registry,capability_profile='commander_review',
            promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,
            capability_registry=self.registry,capability_profile='commander_review')
        return card,semantics,program,bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')

    def cast_x(self,s,source,x,*,reserve=None):
        self.priority(s,'A');offer=self.action(s,'A',s.state.cards[source.object_id])
        command={'action_id':offer['id'],'x':x,'pay':'auto','targets':[],'reserve':reserve or {}}
        self.reject(s,'B',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(x,s.state.stack[-1].x_value)

    def test_complete_originals_close_normally_and_material_split_second_sibling_stays_unready(self):
        for name in (*NAMES,'Lightning Bolt','Pyroclasm'):
            with self.subTest(name=name):
                card,semantics,program,bound=self.normal_program(name)
                self.assertTrue(bound['strict_capability_ready'],bound['blockers'])
                self.assertEqual('exact',compile_oracle_card(card,capability_registry=self.registry).status)
                if name in NAMES:
                    spell=next(p for p in semantics.programs_for_oracle(card.oracle_id) if p.event=='resolve')
                    self.assertEqual('damage-fixed-simultaneous-set-v1',spell.provenance['template_id'])
                    self.assertEqual(CastXAmountSpec().to_dict(),spell.effects[0]['amount'])
        card,_,_,bound=self.normal_program('Molten Disaster')
        self.assertFalse(bound['strict_capability_ready'])
        ir=compile_oracle_card(card,capability_registry=self.registry)
        self.assertTrue(any('split second' in r.text.lower() for r in ir.material_residuals))

    def test_declining_reference_recognizer_cannot_consume_fixed_set_mechanics(self):
        card=self.db.lookup('Earthquake',fuzzy=False)
        effect=deepcopy(compile_oracle_card(card,capability_registry=self.registry).faces[0].nodes[0].effects[0])
        effect['amount']=2
        expected=fixed_damage_node_capabilities(effects=(effect,),target_schema=None,mechanic_ids=('cr-120-damage',))
        self.assertTrue(expected)
        self.assertEqual(expected,fixed_damage_node_capabilities(effects=(effect,),target_schema=None,
            mechanic_ids=(word for word in ('cr-120-damage',))))

    def test_raw_payload_owners_cannot_be_dropped_even_without_coverage(self):
        card,semantics,_,_=self.normal_program('Earthquake')
        spell=next(p for p in semantics.programs_for_oracle(card.oracle_id) if p.event=='resolve')
        for missing in ('quantity_expression.declared_effect_amount','damage.amount.positive',
                'damage.batch.fixed_set','damage.source.stack_current_or_lki','damage.result.player_life',
                'damage.result.multitype_permanent','target.permanent.characteristic_predicate'):
            with self.subTest(missing=missing):
                deps=tuple(c for c in spell.capability_dependencies if c!=missing)
                altered=replace(spell,coverage=(),capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(altered,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict'])
                self.assertIn('capability:undeclared_runtime_dependency:'+missing,bound['blockers'])

    def test_malformed_amount_groups_source_and_unknown_fields_fail_closed(self):
        card=self.db.lookup('Windstorm',fuzzy=False)
        good=deepcopy(compile_oracle_card(card,capability_registry=self.registry).faces[0].nodes[0].effects[0])
        self.assertTrue(announced_x_damage_set_dependencies(good))
        bad=[]
        for patch_value in ({'coefficient':True},{'coefficient':-1},{'schema_version':99},{'extra':1}):
            row=deepcopy(good);row['amount'].update(patch_value);bad.append(row)
        for change in ({'source':'$target.0'},{'groups':[]},{'unknown':1}):bad.append({**deepcopy(good),**change})
        row=deepcopy(good);row['groups'][0]['query']['zones']=['hand'];bad.append(row)
        for value in bad:
            with self.subTest(value=value),self.assertRaises(ValueError):announced_x_damage_set_dependencies(value)
        self.assertEqual((),announced_x_damage_set_dependencies({**good,'amount':2}))

    def test_unsupported_quantities_filters_and_extra_material_remain_residual(self):
        for text in ('This spell deals X plus two damage to each creature.',
                'This spell deals X damage to each creature with an unrepresented ability.',
                'This spell deals X damage to each creature. Invent an unrepresented sibling.'):
            self.assertTrue(compile_oracle_card(generic_spell(text),capability_registry=self.registry).material_residuals)

    def test_actual_all_seven_spells_pay_x_and_select_current_public_groups(self):
        for index,name in enumerate(NAMES):
            with self.subTest(name=name):
                s=self.session(711000+index);source=self.add(s.engine,name,zone='hand')
                ground=self.add(s.engine,'Grizzly Bears',seat='B');flyer=self.add(s.engine,'Storm Crow',seat='C')
                horse=self.add(s.engine,'Wu Elite Cavalry',seat='D');self.add(s.engine,'Witchbane Orb',seat='B')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='x-damage-private')
                self.prepare(s,mana={'C':1,'G':2,'R':2});self.checkpoint(s)
                self.priority(s,'A');offer=self.action(s,'A',source)
                for bad in (-1,True,99):self.reject(s,'A',{'action_id':offer['id'],'x':bad,'targets':[],'pay':'auto'})
                self.reject(s,'A',{'action_id':offer['id'],'x':1,'targets':[ground.ref],'pay':'auto'})
                self.cast_x(s,source,1);s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                expected=(0,1,0) if name in ('Windstorm','Hurricane','Squall Line') else (1,0,1) if name in ('Earthquake','Fault Line') else (1,1,0) if name=='Rolling Earthquake' else (1,1,1)
                self.assertEqual(expected,tuple(s.state.cards[c.object_id].marked_damage for c in (ground,flyer,horse)))
                life=40 if name in ('Windstorm','Savage Twister') else 39
                self.assertEqual([life]*4,[s.state.players[seat].life for seat in 'ABCD'])
                batches=[e for e in s.state.events if e.code=='effect.damage.fixed_set']
                self.assertEqual(1,len(batches));self.assertEqual(sum(expected)+(0 if life==40 else 4),batches[0].details['recipient_count'])
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.finish(s,private)

    def test_actual_x_zero_is_paid_without_damage_events(self):
        s=self.session(711010);source=self.add(s.engine,'Squall Line',zone='hand')
        self.add(s.engine,'Storm Crow',seat='B');self.add(s.engine,'Furnace of Rath')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='zero-private')
        self.prepare(s,mana={'G':2});self.checkpoint(s);self.cast_x(s,source,0)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.assertIsNone(self.resolve(s));self.assertEqual([40]*4,[s.state.players[p].life for p in 'ABCD'])
        batches=[e for e in s.state.events if e.code=='effect.damage.fixed_set']
        self.assertTrue(batches);self.assertTrue(all(not e.details['damage_events'] for e in batches))
        self.assertFalse(s.engine._current_turn_history('damage'));self.finish(s,private)

    def test_actual_flying_gain_and_loss_in_response_change_resolution_membership(self):
        for lose in (False,True):
            with self.subTest(lose=lose):
                s=self.session(711020+lose);source=self.add(s.engine,'Windstorm',zone='hand')
                target=self.add(s.engine,'Air Elemental' if lose else 'Grizzly Bears',seat='B')
                response=self.add(s.engine,'Humble' if lose else 'Flight',seat='B',zone='hand')
                if not lose:self.add(s.engine,'Vedalken Orrery',seat='B')
                self.add(s.engine,'Island',seat='B');self.add(s.engine,'Plains',seat='B')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='membership-private')
                self.prepare(s,mana={'C':1,'G':1});self.checkpoint(s);self.cast_x(s,source,1)
                self.cast(s,'B',response,[target.ref]);self.resolve_top(s);s=self.replay(s,load=True)
                self.assertIsNone(self.resolve(s));self.assertEqual(0 if lose else 1,s.state.cards[target.object_id].marked_damage)
                self.finish(s,private)

    def test_actual_countered_original_keeps_cost_and_copy_inherits_x(self):
        for copy_spell in (False,True):
            with self.subTest(copy_spell=copy_spell):
                s=self.session(711030+copy_spell);source=self.add(s.engine,'Hurricane',zone='hand')
                response=self.add(s.engine,'Fury Storm' if copy_spell else 'Counterspell',seat='B',zone='hand')
                for i in range(4):self.add(s.engine,'Mountain' if copy_spell else 'Island',seat='B',ref='x-response-payer-'+str(i))
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='copy-private')
                self.prepare(s,mana={'C':2,'G':1});self.checkpoint(s);self.cast_x(s,source,2)
                original=s.state.stack[-1];self.cast(s,'B',response,[original.ref]);self.assertIsNone(self.resolve(s))
                self.assertEqual([36 if copy_spell else 40]*4,[s.state.players[p].life for p in 'ABCD'])
                self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.finish(s,private)

    def test_actual_replacement_choices_follow_apnap_and_reload_before_one_batch_commit(self):
        s=self.session(711040);source=self.add(s.engine,'Hurricane',zone='hand')
        self.add(s.engine,'Furnace of Rath',ref='x-first-furnace')
        self.add(s.engine,'Furnace of Rath',seat='D',ref='x-second-furnace')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='apnap-private')
        self.prepare(s,mana={'C':1,'G':1});self.checkpoint(s);self.cast_x(s,source,1)
        self.assertEqual('replacement.order',self.resolve(s).kind)
        for seat in 'ABCD':
            self.assertEqual([40]*4,[s.state.players[p].life for p in 'ABCD'])
            s=self.replay(s,load=True);self.assertEqual(['pilot:'+seat],s.pending_principals())
            options=s.packet('pilot:'+seat,full=True)['decision']['ctx']['options']
            command={'action_id':'choose','choices':{'replacement':options[0]['id']}}
            self.reject(s,'D' if seat!='D' else 'A',command)
            result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual([36]*4,[s.state.players[p].life for p in 'ABCD'])
        batches=[e for e in s.state.events if e.code=='effect.damage.fixed_set']
        self.assertEqual(1,len(batches));self.assertEqual(4,len(batches[0].details['damage_events']));self.finish(s,private)

    def test_actual_prevention_response_and_replacement_order_keep_paid_x(self):
        s=self.session(711050);source=self.add(s.engine,'Hurricane',zone='hand')
        furnace=self.add(s.engine,'Furnace of Rath');shield=self.add(s.engine,'Mending Hands',seat='B',zone='hand')
        self.add(s.engine,'Plains',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='prevention-private')
        self.prepare(s,mana={'C':3,'G':1});self.checkpoint(s);self.cast_x(s,source,3)
        self.cast(s,'B',shield,['B']);self.assertEqual('replacement.order',self.resolve(s).kind)
        self.assertEqual([40]*4,[s.state.players[p].life for p in 'ABCD']);s=self.replay(s,load=True)
        options=s.packet('pilot:B',full=True)['decision']['ctx']['options']
        option=next(o['id'] for o in options if ':'+furnace.ref+':' in o['id'])
        command={'action_id':'choose','choices':{'replacement':option}};self.reject(s,'C',command)
        result=s.act('pilot:B',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual([34,38,34,34],[s.state.players[p].life for p in 'ABCD'])
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.finish(s,private)

    def test_actual_damage_event_fault_rolls_back_entire_set_then_retries(self):
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s=self.session(711060);source=self.add(s.engine,'Earthquake',zone='hand')
        bear=self.add(s.engine,'Grizzly Bears',seat='B')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='rollback-private')
        self.prepare(s,mana={'C':2,'R':1});self.checkpoint(s);self.cast_x(s,source,2)
        original=s.engine._dispatch_semantic_event;observed=[];failed=None
        def fault(event,context,**kwargs):
            result=original(event,context,**kwargs)
            if event=='damage.dealt':
                observed.append(([s.state.players[p].life for p in 'ABCD'],s.state.cards[bear.object_id].marked_damage))
                raise GameRuleError('Injected X damage-set event failure')
            return result
        with patch.object(s.engine,'_dispatch_semantic_event',side_effect=fault):
            for _ in range(8):
                self.assertEqual('priority',s.state.pending_decision.kind)
                principal=s.pending_principals()[0];before=authoritative_state_hash(s.state)
                result=s.act(principal,{'action_id':'pass'})
                if not result.ok:
                    self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
        self.assertEqual([([38]*4,2)],observed);self.assertIsNotNone(failed)
        self.assertEqual([40]*4,[s.state.players[p].life for p in 'ABCD'])
        self.assertEqual(0,s.state.cards[bear.object_id].marked_damage)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        s=self.replay(s,load=True);result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual([38]*4,[s.state.players[p].life for p in 'ABCD'])
        self.assertEqual('graveyard',s.state.cards[bear.object_id].zone);self.finish(s,private)

    def test_actual_lethal_player_and_creature_packets_finish_before_state_based_actions(self):
        s=self.session(711070);source=self.add(s.engine,'Earthquake',zone='hand')
        bear=self.add(s.engine,'Grizzly Bears',seat='B')
        self.prepare(s,mana={'C':2,'R':1})
        for seat in 'ABCD':s.state.players[seat].life=2
        self.checkpoint(s);self.cast_x(s,source,2)
        original=s.engine._dispatch_semantic_event;observed=[]
        def observe(event,context,**kwargs):
            if event=='damage.dealt':observed.append(([s.state.players[p].life for p in 'ABCD'],
                s.state.cards[bear.object_id].zone,s.state.cards[bear.object_id].marked_damage))
            return original(event,context,**kwargs)
        with patch.object(s.engine,'_dispatch_semantic_event',side_effect=observe):self.assertIsNone(self.resolve(s))
        self.assertEqual([([0]*4,'battlefield',2)]*5,observed)
        self.assertTrue(all(not s.state.players[p].in_game for p in 'ABCD'));self.replay(s,load=True)

    def test_fresh_zero_amount_mutant_is_killed_after_positive_paid_batch_witness(self):
        from quorune.semantic_runtime import values as owner
        with patch.object(owner,'resolve_cast_x_amount',return_value=0):
            with self.assertRaises(AssertionError):
                self.test_actual_lethal_player_and_creature_packets_finish_before_state_based_actions()
