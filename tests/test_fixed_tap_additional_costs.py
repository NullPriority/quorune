from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from copy import deepcopy
import json,tempfile,unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.rules.tap_additional_cost import FixedTapAdditionalCost,TAP_ADDITIONAL_COST_CAPABILITY
from quorune.rules.tap_additional_cost import PAYMENT_COMPOSITION_CAPABILITY
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs import bind_card_program_runtime
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from test_granted_skulk import record
import test_fixed_reveal_additional_costs as reveal


CLAUSE='As an additional cost to cast this spell, tap four untapped creatures you control.'


class FixedTapCostCompilerTests(unittest.TestCase):
    def test_existing_payment_keywords_compose_with_the_complete_price_and_keep_source_spans(self):
        for keyword in ('Convoke','Improvise','Delve'):
            with self.subTest(keyword=keyword):
                text=keyword+'\n'+CLAUSE+'\nDraw three cards.'
                ir=compile_oracle_card(replace(record(text,'Sorcery'),keywords=(keyword,)),capability_registry=load_default_capability_registry())
                self.assertEqual('exact',ir.status,ir.material_residuals)
                self.assertEqual(2,len(ir.faces[0].nodes))
                for node in ir.faces[0].nodes:self.assertEqual(node.text,text[node.span.start:node.span.end])

    def test_fixed_single_union_and_alternative_tap_prices_have_complete_spans(self):
        for clause,types,count in ((CLAUSE,('creature',),4),
                ('As an additional cost to cast this spell, tap two untapped creatures and/or lands you control.',('creature','land'),2),
                ('As an additional cost to cast this spell, tap four untapped artifacts, creatures, and/or lands you control.',('artifact','creature','land'),4),
                ('As an additional cost to cast this spell, tap an untapped artifact you control or pay {1}.',('artifact',),1)):
            with self.subTest(clause=clause):
                text=clause+'\nDraw three cards.'
                ir=compile_oracle_card(record(text,'Sorcery'),capability_registry=load_default_capability_registry())
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node,=ir.faces[0].nodes;self.assertEqual(text,node.text)
                self.assertIn(TAP_ADDITIONAL_COST_CAPABILITY,node.capability_dependencies)
                raw=node.cost['additional_costs'][0];raw=raw['options'][0] if raw['kind']=='alternative_additional_cost' else raw
                self.assertEqual((types,count),(tuple(raw['permanent_types']),raw['count']))

    def test_fixed_tap_descriptor_and_unknown_variable_or_optional_prices_fail_closed(self):
        raw=FixedTapAdditionalCost(4,('creature',)).to_descriptor()
        self.assertEqual(raw,FixedTapAdditionalCost.from_descriptor(raw).to_descriptor())
        for change in ({'schema_version':True},{'count':True},{'count':0},{'permanent_types':['sorcery']},
                {'permanent_types':['land','creature']},{'kind':'tap_source'},{'extra':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):FixedTapAdditionalCost.from_descriptor({**raw,**change})
        for clause in (CLAUSE.replace('four','X'),CLAUSE.replace('tap four','you may tap any number of'),
                CLAUSE.replace('untapped creatures','tapped creatures'),CLAUSE.replace('you control','an opponent controls'),
                CLAUSE.replace('creatures','creatures with power 2 or greater')):
            self.assertNotEqual('exact',compile_oracle_card(record(clause+'\nDraw three cards.','Sorcery'),capability_registry=load_default_capability_registry()).status)

    def test_composition_closes_each_original_span_and_preserves_payment_boundaries(self):
        for text in (CLAUSE+'\nConvoke\nDraw three cards.',CLAUSE+'\nDraw three cards.\nConvoke',
                'Convoke\nConvoke\n'+CLAUSE+'\nDraw three cards.'):
            ir=compile_oracle_card(replace(record(text,'Sorcery'),keywords=('Convoke',)),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            for node in ir.faces[0].nodes:self.assertEqual(node.text,text[node.span.start:node.span.end])
        text='Convoke\nImprovise\n'+CLAUSE+'\nDraw three cards.'
        self.assertNotEqual('exact',compile_oracle_card(replace(record(text,'Sorcery'),keywords=('Convoke','Improvise')),
            capability_registry=load_default_capability_registry()).status)
        for missing in (TAP_ADDITIONAL_COST_CAPABILITY,PAYMENT_COMPOSITION_CAPABILITY,'casting.payment.convoke'):
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(c for c in raw['capabilities'] if c['id']==missing).update(status='blocked',blockers=['Fixed tap payment producer omitted'])
            text='Convoke\n'+CLAUSE+'\nDraw three cards.'
            self.assertNotEqual('exact',compile_oracle_card(replace(record(text,'Sorcery'),keywords=('Convoke',)),capability_registry=CapabilityRegistry(raw)).status)


class FixedTapCostRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=reveal.FixedRevealCostRuntimeTests.session
    add=reveal.FixedRevealCostRuntimeTests.add
    checkpoint=reveal.FixedRevealCostRuntimeTests.checkpoint
    resolve=reveal.FixedRevealCostRuntimeTests.resolve
    replay=reveal.FixedRevealCostRuntimeTests.replay
    fund=reveal.FixedRevealCostRuntimeTests.fund
    reject=reveal.FixedRevealCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();p=Path(cls.temp.name)/'fixed-tap-cost.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/fixed-tap-additional-cost-cards.json'],p)
        cls.db=CardDatabase(p);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Fixed tap price witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def offered(self,session,source,actor='A'):
        actions=session.packet('pilot:'+actor,full=True)['decision']['ctx']['legal']['actions']
        result=next((a for a in actions if a.get('card')==source.ref),None)
        self.assertIsNotNone(result,'The supported priced spell must be offered when its complete payment is available')
        return result

    def test_actual_shared_discovery_pays_new_creatures_then_draws_three_and_replays(self):
        session=self.session(601150);source=self.add(session.engine,'Shared Discovery',zone='hand',ref='shared-source')
        paid=[self.add(session.engine,'Generic Shrouded Tap Body' if i==0 else 'Generic Bound Body',ref='shared-body-'+str(i)) for i in range(4)]
        paid[0].owner='B'
        for c in paid:c.acquired_control_turn_count=session.state.players['A'].turns_begun
        self.fund(session,{'U':1});offer=self.offered(session,source);option,=offer['cost_options']
        self.assertEqual((4,4),(option['choice_schema']['tap_cost_cards']['minimum'],option['choice_schema']['tap_cost_cards']['maximum']))
        base={'action_id':offer['id'],'cost_option':option['id'],'tap_cost_cards':[c.ref for c in paid],'pay':'auto'}
        self.reject(session,'B',**base);self.reject(session,**{**base,'tap_cost_cards':[paid[0].ref]*4})
        session=self.replay(session,load=True);hand_before=set(session.state.players['A'].zones['hand']);before=len(hand_before)
        result=session.act('pilot:A',base);self.assertTrue(result.ok,result.summary)
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in paid));self.assertEqual('stack',session.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(session));self.assertEqual(before+2,len(session.state.players['A'].zones['hand']))
        new_refs=[session.state.cards[oid].ref for oid in set(session.state.players['A'].zones['hand'])-hand_before]
        for viewer in 'BCD':
            packet=json.dumps(session.packet('pilot:'+viewer,full=True))
            for ref in new_refs:self.assertNotIn(ref,packet)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone);self.replay(session,load=True)

    def test_joint_affordability_keeps_blue_mana_source_out_of_the_additional_tap_selection(self):
        for bodies in (3,4):
            with self.subTest(bodies=bodies):
                session=self.session(601151);source=self.add(session.engine,'Shared Discovery',zone='hand',ref='joint-source')
                bird=self.add(session.engine,'Birds of Paradise',ref='aaa-blue-bird')
                bird.acquired_control_turn_count=max(0,session.state.players['A'].turns_begun-1)
                paid=[self.add(session.engine,'Generic Bound Body',ref='zzz-tap-body-'+str(i)) for i in range(bodies)]
                self.fund(session,{})
                actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                offer=next((a for a in actions if a.get('card')==source.ref),None)
                if bodies==3:
                    self.assertIsNone(offer,'Four tap-cost creatures cannot also supply blue mana by tapping one twice')
                    self.reject(session,action_id='cast:'+source.ref,tap_cost_cards=[bird.ref]+[c.ref for c in paid],pay='auto')
                    continue
                self.assertIsNotNone(offer,'A legal four-body selection must remain available even though the first sorted subset consumes the blue source')
                option,=offer['cost_options']
                self.assertIn('tap_cost_cards',option.get('choice_schema',{}))
                result=session.act('pilot:A',{'action_id':offer['id'],'cost_option':option['id'],'tap_cost_cards':[c.ref for c in paid],'pay':'auto'})
                self.assertTrue(result.ok,result.summary);self.assertTrue(session.state.cards[bird.object_id].tapped)
                self.assertTrue(all(session.state.cards[c.object_id].tapped for c in paid));self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_native_convoke_improvise_and_delve_keep_additional_taps_disjoint_and_replay(self):
        for mode in ('Convoke','Improvise','Delve','Convoke Delve'):
            with self.subTest(mode=mode):
                session=self.session(601152);source=self.add(session.engine,'Generic Fixed Tap '+mode,zone='hand',ref='composed-source')
                creature='Generic Bound Mentor' if mode=='Improvise' else 'Merfolk of the Pearl Trident' if 'Convoke' in mode else 'Birds of Paradise'
                paid=[self.add(session.engine,creature,ref='cost-'+str(i)) for i in range(2)]
                payer=self.add(session.engine,creature,ref='pay-third') if mode!='Delve' else None
                for card in paid+([payer] if payer else []):card.acquired_control_turn_count=session.state.players['A'].turns_begun
                grave=[self.add(session.engine,'Generic Bound Plains',zone='graveyard',ref='delve-'+str(i)) for i in range(2)] if 'Delve' in mode else []
                pool={'C':1} if mode=='Convoke' else {'U':1,'C':1} if mode=='Improvise' else {'U':1} if mode=='Delve' else {}
                self.fund(session,pool);offer=self.offered(session,source);option,=offer['cost_options']
                response={'action_id':offer['id'],'cost_option':option['id'],'tap_cost_cards':[c.ref for c in paid],'pay':'auto'}
                if 'Convoke' in mode:response['convoke_cards']=[payer.ref]
                if mode=='Improvise':response['improvise_cards']=[payer.ref]
                if grave:response['delve_cards']=[c.ref for c in grave]
                if payer:
                    field='improvise_cards' if mode=='Improvise' else 'convoke_cards'
                    self.reject(session,**{**response,field:[paid[0].ref]})
                result=session.act('pilot:A',response);self.assertTrue(result.ok,result.summary)
                self.assertTrue(all(session.state.cards[c.object_id].tapped for c in paid+([payer] if payer else [])))
                self.assertTrue(all(session.state.cards[c.object_id].zone=='exile' for c in grave))
                self.assertIsNone(self.resolve(session));self.replay(session,load=True)

    def test_all_five_originals_have_complete_independent_admission(self):
        for name in ('Shared Discovery','Guardian of the Great Door','Fear of Exposure',"Warlord's Elite",'Disruption Protocol'):
            with self.subTest(name=name):
                program=compile_best_available_card_program(self.db,self.db.lookup(name),semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry,capability_profile='commander_review')
                self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])

    def test_actual_mixed_type_union_prices_enter_original_creatures_once(self):
        for name,count in (('Guardian of the Great Door',4),('Fear of Exposure',2),("Warlord's Elite",2)):
            with self.subTest(name=name):
                session=self.session(601153);source=self.add(session.engine,name,zone='hand',ref='mixed-source')
                mentor=self.add(session.engine,'Generic Bound Mentor',ref='mixed-artifact-creature')
                lands=[self.add(session.engine,'Generic Bound Plains',ref='mixed-land-'+str(i)) for i in range(2)]
                ring=self.add(session.engine,'Sol Ring',ref='mixed-artifact')
                paid=[mentor,*lands,ring] if count==4 else [mentor,lands[0]]
                self.fund(session,{'W':4,'G':4,'C':8});offer=self.offered(session,source);option,=offer['cost_options']
                refs=[c.ref for c in paid]
                self.reject(session,action_id=offer['id'],cost_option=option['id'],tap_cost_cards=refs[:-1],pay='auto')
                result=session.act('pilot:A',{'action_id':offer['id'],'cost_option':option['id'],'tap_cost_cards':refs,'pay':'auto'})
                self.assertTrue(result.ok,result.summary);self.assertTrue(all(session.state.cards[c.object_id].tapped for c in paid))
                if name=='Fear of Exposure':self.assertFalse(session.state.cards[ring.object_id].tapped)
                self.assertIsNone(self.resolve(session));original=self.db.lookup(name)
                self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
                self.assertEqual((int(original.power),int(original.toughness)),tuple(session.engine._numeric_stat(source.object_id,k) for k in ('power','toughness')))
                self.assertTrue({k.casefold() for k in original.keywords}.issubset(session.engine._combat_keywords(session.state.cards[source.object_id])))
                self.replay(session,load=True)

    def test_actual_protocol_chooses_artifact_tap_or_added_mana_then_counters_target_spell(self):
        for tap in (True,False):
            with self.subTest(tap=tap):
                session=self.session(601154);source=self.add(session.engine,'Disruption Protocol',zone='hand',ref='protocol-source')
                artifact=self.add(session.engine,'Generic Bound Mentor',ref='protocol-artifact')
                target=self.add(session.engine,'Generic Bound Draw Loss',seat='B',zone='hand',ref='protocol-target-spell')
                session.state.players['A'].mana_pool.update({'U':2,'C':1});session.state.players['B'].mana_pool.update({'B':1})
                self.fund(session,{},actor='B')
                result=session.act('pilot:B',{'action_id':'cast:'+target.ref,'targets':['A'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
                target_ref=session.state.stack[-1].ref
                for _ in range(4):
                    if session.pending_principals()==['pilot:A'] or tuple(session.pending_principals())==('pilot:A',):break
                    result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
                session=self.replay(session,load=True);offer=self.offered(session,source)
                self.assertEqual(2,len(offer['cost_options']))
                option=next(o for o in offer['cost_options'] if o['label'].startswith('Tap')==tap)
                response={'action_id':offer['id'],'cost_option':option['id'],'targets':[target_ref],'pay':'auto'}
                if tap:response['tap_cost_cards']=[artifact.ref]
                result=session.act('pilot:A',response);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
                self.assertEqual(tap,session.state.cards[artifact.object_id].tapped)
                self.assertEqual(1 if tap else 0,session.state.players['A'].mana_pool['C'])
                self.assertEqual('graveyard',session.state.cards[target.object_id].zone);self.assertEqual(40,session.state.players['A'].life)
                self.replay(session,load=True)

    def test_wrong_controller_type_phase_and_tap_state_do_not_supply_cost_objects(self):
        session=self.session(601155);source=self.add(session.engine,'Shared Discovery',zone='hand',ref='wrong-cost-source')
        paid=[self.add(session.engine,'Generic Bound Body',ref='legal-cost-'+str(i)) for i in range(4)]
        foreign=self.add(session.engine,'Generic Bound Body',seat='B',ref='foreign-cost')
        foreign.owner='A'
        tapped=self.add(session.engine,'Generic Bound Body',ref='already-tapped-cost');tapped.tapped=True
        phased=self.add(session.engine,'Generic Bound Body',ref='phased-cost');phased.phased_out=True
        wrong=self.add(session.engine,'Sol Ring',ref='wrong-type-cost')
        self.fund(session,{'U':1});offer=self.offered(session,source);option,=offer['cost_options']
        legal=set(option['choice_schema']['tap_cost_cards']['legal_refs']);self.assertEqual({c.ref for c in paid},legal)
        for invalid in (foreign.ref,tapped.ref,phased.ref,wrong.ref):
            self.reject(session,action_id=offer['id'],cost_option=option['id'],tap_cost_cards=[invalid]+[c.ref for c in paid[1:]],pay='auto')

    def test_changed_cost_incarnation_during_mana_payment_rejects_atomically(self):
        session=self.session(601156);source=self.add(session.engine,'Shared Discovery',zone='hand',ref='during-mana-source')
        paid=[self.add(session.engine,'Generic Bound Body',ref='during-mana-'+str(i)) for i in range(4)]
        self.fund(session,{'U':1});offer=self.offered(session,source);option,=offer['cost_options']
        original=session.engine._pay_for_cost
        def pay_then_change(*args,**kwargs):
            result=original(*args,**kwargs)
            session.engine.move_card(paid[0].object_id,'hand',reason='Controlled mana-phase incarnation boundary',log=False,semantic_events=False)
            session.engine.move_card(paid[0].object_id,'battlefield',reason='Controlled mana-phase incarnation return',log=False,semantic_events=False)
            return result
        with patch.object(session.engine,'_pay_for_cost',side_effect=pay_then_change):
            self.reject(session,action_id=offer['id'],cost_option=option['id'],tap_cost_cards=[c.ref for c in paid],pay='auto')
        self.assertEqual(1,session.state.players['A'].mana_pool['U']);self.assertFalse(any(session.state.cards[c.object_id].tapped for c in paid))

    def test_actual_tap_commit_legal_selection_search_and_post_mana_omission_mutants_are_killed(self):
        with patch('quorune.rules.casting.commit._commit_tap_costs',return_value=[]):
            with self.assertRaises(AssertionError):self.test_actual_shared_discovery_pays_new_creatures_then_draws_three_and_replays()
        with patch('quorune.rules.casting.additional_tap_costs._payable_selections',side_effect=lambda host,seat,option,cost,candidates,mechanics,response,spend_context:iter((candidates[:cost.count],))):
            witness=type(self)('test_joint_affordability_keeps_blue_mana_source_out_of_the_additional_tap_selection')
            with self.assertRaises(AssertionError):witness.test_joint_affordability_keeps_blue_mana_source_out_of_the_additional_tap_selection()
        with patch('quorune.rules.casting.additional_tap_costs.validate_fixed_tap_payment',return_value=()):
            with self.assertRaises(AssertionError):self.test_changed_cost_incarnation_during_mana_payment_rejects_atomically()

    def test_free_base_cost_keeps_mandatory_taps_and_the_payment_snapshot_stays_closed(self):
        from quorune.rules.casting.costs import build_cast_cost_options
        from quorune.rules.casting.proposal import _spell_semantic_key
        from quorune.rules.casting.additional_tap_costs import validate_fixed_tap_payment
        session=self.session(601157);source=self.add(session.engine,'Shared Discovery',zone='hand',ref='free-tap-source')
        paid=[self.add(session.engine,'Generic Bound Body',ref='free-tap-'+str(i)) for i in range(4)]
        self.fund(session,{})
        program=session.engine.semantics.get(_spell_semantic_key(self.db.lookup('Shared Discovery'),None))
        options=build_cast_cost_options(session.engine,'A',source,program,hint=True,force_without_mana_cost=True)
        self.assertEqual(1,len(options));raw=options[0].to_dict();self.assertFalse(any(raw['requirements'].values()))
        self.assertEqual(4,len(validate_fixed_tap_payment(session.engine,'A',source,json.loads(json.dumps(raw)))))
        for change in ({'schema_version':True},{'unknown':1},{'objects':[raw['fixed_additional_tap_payment']['objects'][0]]*4}):
            mutated=deepcopy(raw);mutated['fixed_additional_tap_payment'].update(change)
            with self.subTest(change=tuple(change)),self.assertRaises(ValueError):validate_fixed_tap_payment(session.engine,'A',source,mutated)
        session.engine.move_card(paid[0].object_id,'hand',reason='Free-base missing tap resource boundary')
        self.assertFalse(build_cast_cost_options(session.engine,'A',source,program,hint=True,force_without_mana_cost=True))

    def test_countering_actual_shared_discovery_keeps_paid_taps_and_mana_without_a_draw(self):
        session=self.session(601158);source=self.add(session.engine,'Shared Discovery',zone='hand',ref='countered-tap-source')
        paid=[self.add(session.engine,'Generic Bound Body',ref='countered-tap-'+str(i)) for i in range(4)]
        counter=self.add(session.engine,'Counterspell',seat='B',zone='hand',ref='counter-tap-spell')
        session.state.players['B'].mana_pool.update({'U':2});self.fund(session,{'U':1})
        before=len(session.state.players['A'].zones['hand']);offer=self.offered(session,source);option,=offer['cost_options']
        result=session.act('pilot:A',{'action_id':offer['id'],'cost_option':option['id'],'tap_cost_cards':[c.ref for c in paid],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);stackref=session.state.stack[-1].ref
        session=self.replay(session,load=True)
        result=session.act('pilot:A',{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        result=session.act('pilot:B',{'action_id':'cast:'+counter.ref,'targets':[stackref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].tapped for c in paid));self.assertFalse(session.state.players['A'].mana_pool['U'])
        self.assertEqual(before-1,len(session.state.players['A'].zones['hand']));self.replay(session,load=True)

    def test_payment_composition_production_omission_is_killed_by_native_composed_casts(self):
        witness=type(self)('test_native_convoke_improvise_and_delve_keep_additional_taps_disjoint_and_replay')
        with patch('quorune.compiler.additional_cost_payment_faces.compile_additional_cost_payment_face',return_value=None):
            with self.assertRaises(AssertionError):witness.test_native_convoke_improvise_and_delve_keep_additional_taps_disjoint_and_replay()
