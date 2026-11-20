"""Pinned CR107.3a/602.2/702.77: one hand price and X counters on one creature."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import json,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card,register_generated_programs
from quorune.semantics import SemanticRegistry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime,bind_semantic_program_runtime
from quorune.compiler.announced_reinforce_nodes import announced_reinforce_material
from quorune.compiler.activated_costs import parse_compiled_activated_abilities
from quorune.compiler.activated_ability_catalog import compile_activated_ability_catalog
from quorune.query_effect_amount_model import CastXAmountSpec
from quorune.activation_x import ANNOUNCED_ACTIVATION_X_SOURCE_DISCARD_CAPABILITY as PRICE_CAP
from scripts.build_test_database import build_fixture_database
from test_declared_effect_amounts import generic_spell
import test_target_recipient_results as recipients

NAMES=('Swell of Courage',"Wren's Run Hydra")
FIXTURE=ROOT/'tests/fixtures/announced-reinforce-cards.json'


class AnnouncedReinforceTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[c for c in base['cards'] if c['name']=='Generic Bound Plains'];base['rulings']=[]
        basic=folder/'basic.json';basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,FIXTURE],folder/'reinforce.sqlite3')
        cls.db=CardDatabase(folder/'reinforce.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Announced Reinforce',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def register(self,name):
        card=self.db.lookup(name,fuzzy=False);semantics=SemanticRegistry()
        register_generated_programs(self.db,semantics,[card],trust_level='provisional',
            capability_registry=self.registry,capability_profile='commander_review',
            promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        program=compile_best_available_card_program(self.db,card,semantic_registry=semantics,
            capability_registry=self.registry,capability_profile='commander_review')
        bound=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(bound['strict_capability_ready'],bound['blockers']);return card,semantics

    def offer(self,s,source):
        self.priority(s,'A')
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        return next(a for a in actions if a['id'].startswith('activate:'+source.ref+':')
            and a.get('choice_schema',{}).get('x'))

    def activate(self,s,source,x,target):
        action=self.offer(s,source);command={'action_id':action['id'],'x':x,'targets':[target.ref],'pay':'auto'}
        self.reject(s,'B',command);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual(x,s.state.stack[-1].x_value);return action

    def test_complete_originals_and_old_fixed_reinforce_keep_normal_source_closure(self):
        for name in (*NAMES,'Earthbrawn','Burrenton Bombardier','Mosquito Guard'):
            with self.subTest(name=name):
                card,semantics=self.register(name)
                self.assertEqual('exact',compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review').status)
                if name in NAMES:
                    ability=next(p for p in semantics.programs_for_oracle(card.oracle_id) if p.event=='activate' and p.active_zone=='hand')
                    self.assertEqual(CastXAmountSpec().to_dict(),ability.effects[0]['amount'])
                    self.assertEqual(['activation.catalog.pinned.v1'],[h['handler_id'] for h in ability.handlers])
                    self.assertEqual(1,ability.cost_schema['announced_x_symbols'])
                    self.assertTrue(ability.cost_schema['discard_source'])

    def test_catalog_retains_original_row_and_old_fixed_codec_has_no_new_x_binding(self):
        for name in NAMES:
            card=self.db.lookup(name,fuzzy=False);ability,=compile_activated_ability_catalog(card)['front']
            self.assertEqual(card.oracle_text.splitlines()[ability.line_index],ability.oracle_line)
            self.assertEqual(('hand',),ability.zones);self.assertFalse(ability.sorcery_speed)
            self.assertEqual(1,ability.announced_x_symbols);self.assertTrue(ability.discard_source)
        from quorune.counter_keyword_abilities import compile_fixed_counter_keyword_ability,FixedCounterKeywordAbilitySpec
        old=compile_fixed_counter_keyword_ability(material_line='Reinforce 2—{1}{G}',oracle_line='Reinforce 2—{1}{G}',
            line_index=0,mechanic='reinforce',printed_power=None)
        raw=old.to_dict();self.assertEqual(raw,FixedCounterKeywordAbilitySpec.from_dict(raw).to_dict())
        self.assertNotIn('announced_x_symbols',old.to_activated_ability().to_dict())

    def test_unsupported_reinforce_prices_counts_and_material_riders_remain_residual(self):
        for text in ('Reinforce X—{2}{G}','Reinforce X—{X}{X}{G}','Reinforce X—{X}{G/P}',
                'Reinforce X—{X}{S}','Reinforce 2—{X}{G}','Reinforce X—{X}{G}. Draw a card.'):
            self.assertIsNone(announced_reinforce_material(text))
            card=replace(generic_spell(text),keywords=('Reinforce',))
            self.assertTrue(compile_oracle_card(card,capability_registry=self.registry).material_residuals)

    def test_raw_hand_price_and_counter_amount_owners_cannot_be_removed(self):
        card,semantics=self.register(NAMES[0])
        ability=next(p for p in semantics.programs_for_oracle(card.oracle_id) if p.event=='activate')
        for missing in (PRICE_CAP,'activation.mana_cost.announced_x','quantity_expression.declared_effect_amount','counter.producer.fixed_effect'):
            with self.subTest(missing=missing):
                deps=tuple(c for c in ability.capability_dependencies if c!=missing)
                altered=replace(ability,coverage=(),capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                bound=bind_semantic_program_runtime(altered,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+missing,bound['blockers'])

    def paid_scene(self,name,x,seed,*,doubler=False,prevent=False,foreign_turn=False):
        s=self.session(seed);source=self.add(s.engine,name,zone='hand');bear=self.add(s.engine,'Grizzly Bears')
        land=self.add(s.engine,'Plains',seat='B');self.add(s.engine,'Heartstone');self.add(s.engine,'Training Grounds')
        if doubler:self.add(s.engine,'Doubling Season')
        if prevent:self.add(s.engine,'Solemnity')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='reinforce-private')
        color='W' if name==NAMES[0] else 'G';self.prepare(s,active='B' if foreign_turn else 'A',mana={'C':x,color:2})
        self.checkpoint(s);action=self.offer(s,source)
        for bad in (None,True,-1,9):self.reject(s,'A',{'action_id':action['id'],'x':bad,'targets':[bear.ref],'pay':'auto'})
        for targets in ([],[land.ref],[bear.ref,bear.ref]):
            self.reject(s,'A',{'action_id':action['id'],'x':x,'targets':targets,'pay':'auto'})
        self.activate(s,source,x,bear);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s));self.assertEqual(0 if prevent else x*(2 if doubler else 1),s.state.cards[bear.object_id].counters.get('+1/+1',0))
        self.reject(s,'A',{'action_id':action['id'],'x':0,'targets':[bear.ref],'pay':'auto'})
        self.assertEqual(0,len(s.engine._current_turn_history('spell_cast')));self.finish(s,private)
        return s

    def test_actual_both_zero_and_two_hand_activations_keep_any_turn_price_target_and_discard(self):
        for i,name in enumerate(NAMES):
            for x in (0,2):
                with self.subTest(name=name,x=x):self.paid_scene(name,x,712000+i*10+x,foreign_turn=True)

    def test_actual_counter_replacement_and_prevention_use_existing_transaction(self):
        self.paid_scene(NAMES[0],2,712030,doubler=True)
        self.paid_scene(NAMES[1],2,712031,prevent=True)

    def test_actual_spell_siblings_use_ordinary_temporary_stats_and_independent_cast_x(self):
        for i,name in enumerate(NAMES):
            with self.subTest(name=name):
                s=self.session(712040+i);source=self.add(s.engine,name,zone='hand')
                bear=self.add(s.engine,'Grizzly Bears');other=self.add(s.engine,'Grizzly Bears',seat='B',ref='reinforce-opponent-bear')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='reinforce-spell-private')
                self.prepare(s,mana={'C':3,'W':2} if i==0 else {'C':3,'G':1});self.checkpoint(s)
                self.priority(s,'A');offer=self.action(s,'A',source)
                command={'action_id':offer['id'],'targets':[],'pay':'auto'}
                if i:command['x']=3
                result=s.act('pilot:A',command)
                self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
                if i==0:
                    self.assertEqual(4,s.engine._numeric_stat(bear.object_id,'power'))
                    self.assertFalse(s.state.cards[bear.object_id].counters)
                    self.until(s,lambda game:game.state.active_player=='B' and game.state.step=='upkeep')
                    self.assertEqual(2,s.engine._numeric_stat(bear.object_id,'power'))
                else:
                    body=s.state.cards[source.object_id];self.assertEqual(3,body.counters.get('+1/+1'))
                    self.assertEqual(3,s.engine._numeric_stat(body.object_id,'power'));self.assertIn('reach',s.engine._combat_keywords(body))
                self.assertEqual(2,s.engine._numeric_stat(other.object_id,'power'));self.finish(s,private)

    def test_actual_bounced_target_or_exiled_discarded_source_keeps_locked_price(self):
        for exile_source in (False,True):
            with self.subTest(exile_source=exile_source):
                s=self.session(712050+exile_source);source=self.add(s.engine,NAMES[1],zone='hand')
                bear=self.add(s.engine,'Grizzly Bears',seat='D')
                response=self.add(s.engine,'Cremate' if exile_source else 'Unsummon',seat='B',zone='hand')
                self.add(s.engine,'Swamp',seat='B');self.add(s.engine,'Island',seat='B')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='reinforce-response-private')
                self.prepare(s,mana={'C':2,'G':2});self.checkpoint(s);self.activate(s,source,2,bear)
                self.cast(s,'B',response,[source.ref if exile_source else bear.ref]);self.resolve_top(s)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual('exile' if exile_source else 'graveyard',s.state.cards[source.object_id].zone)
                self.assertEqual('battlefield' if exile_source else 'hand',s.state.cards[bear.object_id].zone)
                self.assertEqual(2 if exile_source else 0,s.state.cards[bear.object_id].counters.get('+1/+1',0))
                self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()));self.finish(s,private)

    def test_fresh_zero_result_mutant_is_killed_after_positive_paid_counter_witness(self):
        from quorune.semantic_runtime import values as owner
        with patch.object(owner,'resolve_cast_x_amount',return_value=0):
            with self.assertRaises(AssertionError):self.paid_scene(NAMES[0],2,712060)

    def test_actual_counter_event_fault_rolls_back_result_and_retries_without_refunding_price(self):
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s=self.session(712070);source=self.add(s.engine,NAMES[0],zone='hand')
        bear=self.add(s.engine,'Grizzly Bears')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='reinforce-fault-private')
        self.prepare(s,mana={'C':2,'W':2});self.checkpoint(s);self.activate(s,source,2,bear)
        original=s.engine._log;observed=[];failed=None
        def fault(*args,**kwargs):
            result=original(*args,**kwargs)
            if len(args)>1 and args[1]=='counter.add':
                observed.append(s.state.cards[bear.object_id].counters.get('+1/+1',0))
                raise GameRuleError('Injected Reinforce counter occurrence failure')
            return result
        with patch.object(s.engine,'_log',side_effect=fault):
            for _ in range(8):
                principal=s.pending_principals()[0];before=authoritative_state_hash(s.state)
                result=s.act(principal,{'action_id':'pass'})
                if not result.ok:
                    self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
        self.assertEqual([2],observed);self.assertIsNotNone(failed)
        self.assertEqual(0,s.state.cards[bear.object_id].counters.get('+1/+1',0))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        s=self.replay(s,load=True);result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(2,s.state.cards[bear.object_id].counters.get('+1/+1'))
        self.finish(s,private)
