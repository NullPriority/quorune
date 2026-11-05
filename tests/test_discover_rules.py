"""Pinned Discover eligibility and resumable procedure contracts."""

import unittest


class DiscoverValueTests(unittest.TestCase):
    def test_resolving_cast_identity_rejects_internal_payment_fields_and_preserves_choices(self):
        from quorune.discover_model import DiscoverProgress,DiscoverCastIntent
        from quorune.rules.library_selection import LibrarySelectionObjectIdentity as Identity
        from quorune.replacement.immutable import FrozenMap
        from quorune.semantic_choices.intent_replacement import (
            semantic_intent_identity,validate_semantic_intent_identity,with_replacement_selections)
        progress=DiscoverProgress(4,(Identity('one','one@1','P1'),),'P1')
        intent=DiscoverCastIntent('A','STACK',progress,'CURRENT_OPTIONS',FrozenMap({'targets':['TARGET']}))
        kind,identity=semantic_intent_identity(intent)
        self.assertEqual('discover_cast',kind)
        self.assertEqual(identity,validate_semantic_intent_identity(kind,identity))
        self.assertEqual(('RULE',),with_replacement_selections(intent,('RULE',)).replacement_selections)
        for key in ('_mana_payment_id','_mana_replacement_selections','_random_discard_cost_refs'):
            with self.assertRaises(ValueError):
                DiscoverCastIntent('A','STACK',progress,'CURRENT_OPTIONS',FrozenMap({key:'FORGED'}))
        with self.assertRaises(ValueError):validate_semantic_intent_identity(kind,{**identity,'extra':True})

    def test_activated_discover_uses_closed_capability_gating_without_trusting_the_mechanic_globally(self):
        from dataclasses import replace
        from unittest.mock import patch
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.compiler.activated_mana_nodes import _CAPABILITY_SHAPED_EFFECT_OPERATIONS
        source=replace(query_record('{2}, {T}, Sacrifice this creature: Discover 3. Activate only as a sorcery.'),
            power='2',toughness='2')
        registry=load_default_capability_registry()
        ir=compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,[(row.reason,row.blockers) for row in ir.material_residuals])
        with patch('quorune.compiler.activated_mana_nodes._CAPABILITY_SHAPED_EFFECT_OPERATIONS',
                _CAPABILITY_SHAPED_EFFECT_OPERATIONS-{'discover'}):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=registry,
                capability_profile='commander_review').status)

    def test_cleanup_identity_is_closed_and_replacement_selection_preserves_the_progress(self):
        from quorune.discover_model import DiscoverProgress,DiscoverCleanupIntent
        from quorune.rules.library_selection import LibrarySelectionObjectIdentity as Identity
        from quorune.semantic_choices.intent_replacement import (
            semantic_intent_identity,validate_semantic_intent_identity,with_replacement_selections)
        progress=DiscoverProgress(0,(Identity('one','one@1','P1'),),None)
        intent=DiscoverCleanupIntent('A','STACK',progress)
        kind,identity=semantic_intent_identity(intent)
        self.assertEqual('discover_cleanup',kind)
        self.assertEqual(identity,validate_semantic_intent_identity(kind,identity))
        selected=with_replacement_selections(intent,('RULE',))
        self.assertEqual(('RULE',),selected.replacement_selections)
        self.assertEqual(progress,selected.progress)
        with self.assertRaises(ValueError):validate_semantic_intent_identity(kind,{**identity,'extra':True})

    def test_inclusive_card_and_resulting_spell_limits_are_independent(self):
        from quorune.discover_model import discover_card_qualifies,discover_spell_qualifies
        self.assertTrue(discover_card_qualifies(4,types=('creature',),mana_value=4))
        self.assertFalse(discover_card_qualifies(4,types=('creature',),mana_value=5))
        self.assertFalse(discover_card_qualifies(4,types=('land',),mana_value=0))
        self.assertTrue(discover_spell_qualifies(4,mana_value=4))
        self.assertFalse(discover_spell_qualifies(4,mana_value=7))
        self.assertTrue(discover_card_qualifies(0,types=('instant',),mana_value=0))
        for value in (True,-1,'4',float('nan')):
            with self.assertRaises(ValueError):discover_spell_qualifies(4,mana_value=value)

    def test_progress_pins_distinct_exile_incarnations_and_candidate_membership(self):
        from quorune.discover_model import DiscoverProgress
        from quorune.rules.library_selection import LibrarySelectionObjectIdentity as Identity
        first=Identity('one','one@1','P1');second=Identity('two','two@1','P2')
        progress=DiscoverProgress(4,(first,second),'P2')
        self.assertEqual((first,),progress.cleanup_cards)
        self.assertEqual(progress,DiscoverProgress.from_dict(progress.to_dict()))
        for exiled,candidate in (((first,first),None),((first,), 'P2')):
            with self.assertRaises(ValueError):DiscoverProgress(4,exiled,candidate)
        for amount in (True,-1,4.0,'4'):
            with self.assertRaises(ValueError):DiscoverProgress(amount,(),None)

    def test_progress_wire_shape_is_closed_and_does_not_alias_mutable_input(self):
        from quorune.discover_model import DiscoverProgress
        value={'schema_version':1,'amount':0,'exiled':[
            {'object_id':'one','logical_object_id':'one@1','ref':'P1'}],'candidate_ref':'P1'}
        progress=DiscoverProgress.from_dict(value)
        value['exiled'][0]['ref']='CHANGED'
        self.assertEqual('P1',progress.candidate_ref)
        self.assertEqual('P1',progress.exiled[0].ref)
        for changed in ({**progress.to_dict(),'extra':True},
                        {**progress.to_dict(),'schema_version':True}):
            with self.assertRaises(ValueError):DiscoverProgress.from_dict(changed)

    def test_compiler_and_capability_withdrawal_preserve_material_discover_boundaries(self):
        from copy import deepcopy
        from dataclasses import replace
        import json
        from unittest.mock import patch
        from common import ROOT
        from test_qualified_zone_event_queries import query_record
        from quorune.compiler.discover_templates import discover_effect_template
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        source=replace(query_record('Discover 3.'),type_line='Sorcery',power=None,toughness=None)
        registry=load_default_capability_registry()
        self.assertEqual('exact',compile_oracle_card(source,capability_registry=registry,
            capability_profile='commander_review').status)
        for text in ('Discover X.','Discover 3 twice.','Discover 3, then exile the discovered card.'):
            self.assertIsNone(discover_effect_template(text))
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf8'))
        for dependency in ('library.discover.controller','casting.permission.one_shot_exile_without_mana'):
            changed=deepcopy(raw);cap=next(row for row in changed['capabilities'] if row['id']==dependency)
            cap['status']='blocked';cap['blockers']=['Withdrawn for focused mutation']
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=CapabilityRegistry(changed),
                capability_profile='commander_review').status)
        with patch('quorune.compiler.discover_templates.discover_effect_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=registry,
                capability_profile='commander_review').status)
        conditional=replace(source,oracle_text='When this creature enters, if you cast it from exile, discover 3.',
            type_line='Creature — Human',power='3',toughness='2')
        self.assertNotEqual('exact',compile_oracle_card(conditional,capability_registry=registry,
            capability_profile='commander_review').status)


class VariableDiscoverValueTests(unittest.TestCase):
    def test_amount_capabilities_and_optional_source_binding_withdrawal_fail_closed(self):
        from copy import deepcopy
        from dataclasses import replace
        import json
        from unittest.mock import patch
        from common import ROOT
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from quorune.query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY
        from quorune.scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY
        source=replace(query_record('When this creature dies, you may discover X, where X is its toughness.'),power='0',toughness='3')
        registry=load_default_capability_registry()
        self.assertEqual('exact',compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review').status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf8'))
        for dependency in (DECLARED_AMOUNT_CAPABILITY,SCALAR_AMOUNT_CAPABILITY):
            changed=deepcopy(raw);cap=next(row for row in changed['capabilities'] if row['id']==dependency)
            cap['status']='blocked';cap['blockers']=['Withdrawn for variable Discover mutation']
            ir=compile_oracle_card(source,capability_registry=CapabilityRegistry(changed),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status)
            self.assertTrue(any(dependency in blocker for row in ir.material_residuals for blocker in row.blockers))
        with patch('quorune.compiler.scalar_effect_amounts.source_scalar_effect_amount_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=registry,
                capability_profile='commander_review').status)

    def test_declared_and_source_last_known_amounts_require_owned_producers(self):
        from dataclasses import replace
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        registry=load_default_capability_registry()
        cast=replace(query_record('Discover X.'),type_line='Sorcery',mana_cost='{X}{R}',power=None,toughness=None,
            colors=('R',),color_identity=('R',))
        death=replace(query_record('When this creature dies, you may discover X, where X is its toughness.'),power='0',toughness='3')
        for source in (cast,death):
            ir=compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,[(row.reason,row.blockers) for row in ir.material_residuals])
        for source in (replace(cast,mana_cost='{R}'),replace(death,oracle_text=
                'When this creature dies, discover X, where X is an arbitrary private characteristic.')):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=registry,
                capability_profile='commander_review').status)

    def test_unresolved_discover_amount_has_a_closed_type_and_cannot_reach_the_scan(self):
        from quorune.discover_model import validate_discover_effect
        from quorune.scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin
        amount=ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE,characteristic='toughness').to_dict()
        effect={'op':'discover','schema_version':1,'player':'$controller','amount':amount}
        validate_discover_effect(effect)
        with self.assertRaises(ValueError):validate_discover_effect({**effect,'player':'A'},resolved=True)
        for value in (True,-1,2.0,'3',{'kind':'unowned_amount'},{**amount,'coefficient':-1},{**amount,'extra':True}):
            with self.assertRaises(ValueError):validate_discover_effect({**effect,'amount':value})


class DiscoverStepTests(unittest.TestCase):
    def context(self):
        from types import SimpleNamespace
        from quorune.object_query import ObjectQueryResult
        rows={}
        for ref,kind,mana in (('LAND','land',0),('HIGH','creature',4),('EQUAL','creature',3),('UNSEEN','instant',1)):
            rows[ref]=ObjectQueryResult(ref,ref,ref,'A','A','library',types=(kind,),mana_value=mana,
                logical_object_id=ref+'@0')
        class Query:
            library=['LAND','HIGH','EQUAL','UNSEEN']
            active_seats=('A','B')
            def object(self,ref,*,zones=None):
                row=rows.get(ref)
                return row if row is not None and (zones is None or row.zone in zones) else None
            def library_refs(self,actor,*,top_first):return tuple(self.library)
        return SimpleNamespace(actor='A',stack_controller='A',stack_ref='STACK',stack_label='Discover',query=Query()),rows

    def test_scan_exiles_one_card_per_existing_zone_intent_and_stops_at_equality(self):
        from dataclasses import replace
        from quorune.semantic_choices.discover import DiscoverScanHandler
        from quorune.semantic_runtime.zone_move_intent import ZoneMoveIntent
        context,rows=self.context()
        effect={'op':'discover','schema_version':1,'player':'A','amount':3}
        for ref in ('LAND','HIGH','EQUAL'):
            preparation=DiscoverScanHandler(effect['op']).prepare(effect,context)
            self.assertIsNone(preparation.request);self.assertEqual(1,len(preparation.preparation_intents))
            intent=preparation.preparation_intents[0]
            self.assertIsInstance(intent,ZoneMoveIntent)
            self.assertEqual((ref,'exile',('library',),0),
                (intent.object_ref,intent.destination,intent.expected_zones,intent.expected_zone_change_counter))
            self.assertEqual(ref,context.query.library.pop(0))
            rows[ref]=replace(rows[ref],zone='exile',logical_object_id=ref+'@1')
            effect=dict(preparation.auto_continue.prepend_effects[0])
        preparation=DiscoverScanHandler(effect['op']).prepare(effect,context)
        self.assertFalse(preparation.preparation_intents)
        choice=dict(preparation.auto_continue.prepend_effects[0])
        self.assertEqual('discover_cast_choice',choice['op'])
        self.assertEqual('EQUAL',choice['progress']['candidate_ref'])
        self.assertEqual(['LAND','HIGH','EQUAL'],[card['ref'] for card in choice['progress']['exiled']])
        self.assertEqual(['UNSEEN'],context.query.library)

    def test_changed_exile_incarnation_and_unknown_progress_fields_reject_before_next_move(self):
        from dataclasses import replace
        from quorune.semantic_choices.discover import DiscoverScanHandler
        context,rows=self.context()
        initial={'op':'discover','schema_version':1,'player':'A','amount':3}
        preparation=DiscoverScanHandler('discover').prepare(initial,context)
        effect=dict(preparation.auto_continue.prepend_effects[0])
        context.query.library.pop(0)
        rows['LAND']=replace(rows['LAND'],zone='exile',logical_object_id='LAND@3')
        with self.assertRaises(ValueError):DiscoverScanHandler('discover_step').prepare(effect,context)
        with self.assertRaises(ValueError):DiscoverScanHandler('discover').prepare({**initial,'extra':True},context)


class DiscoverNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'discover.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/discover-cards.json',ROOT/'tests/fixtures/cascade-cards.json',
            ROOT/'tests/fixtures/state-based-destination-cards.json',
            ROOT/'tests/fixtures/conditional-exile-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Discover',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def closed(self,card):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])

    def test_original_dinosaur_egg_uses_last_known_toughness_zero_and_optional_decline(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        for index,mode in enumerate(('modified','negative','decline')):
            with self.subTest(mode=mode):
                session=H.session(self,949100+index);engine=session.engine
                egg=H.add(self,engine,'Dinosaur Egg',ref='ORIGINAL_EGG');self.closed(egg)
                if mode!='negative':egg.counters['+1/+1']=2
                names=(('Hill Giant','Endless One','Generic Bound Growth') if mode=='negative' else
                    ('Trumpeting Carnosaur','Hill Giant','Generic Bound Growth'))
                cards=tuple(H.add(self,engine,name,zone='library',ref='EGG_TOP'+str(i)) for i,name in enumerate(names))
                library=engine.state.players['A'].zones['library']
                for card in cards:library.remove(card.object_id)
                library.extend(card.object_id for card in reversed(cards))
                original_library=list(library)
                killer=H.add(self,engine,'Grasp of Darkness' if mode=='negative' else 'Murder',zone='hand')
                self.closed(killer)
                action=H.ready(self,session,killer,{'B':2} if mode=='negative' else {'B':2,'C':1})
                H.checkpoint(self,session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[egg.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
                self.assertEqual('graveyard',engine.state.cards[egg.object_id].zone)
                self.assertEqual({},engine.state.cards[egg.object_id].counters)
                accepted=session.act('pilot:A',{'action_id':'choose','choice':'decline' if mode=='decline' else 'apply'})
                self.assertTrue(accepted.ok,accepted.summary);decision=H.resolve(self,session)
                if mode=='decline':
                    self.assertIsNone(decision);self.assertEqual(original_library,engine.state.players['A'].zones['library'])
                    self.assertFalse(any(event.code=='library.discover' for event in engine.state.events))
                else:
                    self.assertIsNotNone(decision)
                    self.assertEqual(0 if mode=='negative' else 5,decision.continuation['effect']['progress']['amount'])
                    self.assertEqual(('exile','exile','library'),tuple(engine.state.cards[card.object_id].zone for card in cards))
                    self.assertNotIn('Generic Bound Growth',str(session.packet('pilot:B',full=True)))
                    session=H.replay(self,session,load=True);engine=session.engine
                    before=authoritative_state_hash(engine.state)
                    rejected=session.act('pilot:B',{'action_id':'choose','choice':'decline'})
                    self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
                    accepted=session.act('pilot:A',{'action_id':'choose','choice':'decline'})
                    self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
                    self.assertEqual('hand',engine.state.cards[cards[1].object_id].zone)
                    self.assertEqual('library',engine.state.cards[cards[0].object_id].zone)
                    discoveries=[event for event in engine.state.events if event.code=='library.discover']
                    self.assertEqual(1,len(discoveries));self.assertEqual(2,discoveries[0].details['exiled_count'])
                H.replay(self,session,load=True)

    def test_cast_x_and_public_amounts_keep_their_distinct_values_through_casting_and_costs(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        for index,mode in enumerate(('cast_x','public')):
            with self.subTest(mode=mode):
                session=H.session(self,949110+index);engine=session.engine
                artifacts=tuple(H.add(self,engine,'Generic Bound Mentor',ref='AMOUNT_ARTIFACT'+str(i)) for i in range(2))
                target=H.add(self,engine,'Generic Bound Body',seat='B',ref='AMOUNT_TARGET')
                names=(('Hill Giant','Generic Bound Body') if mode=='cast_x' else
                       ('Centaur Courser','Generic Discover Double Cost'))
                cards=tuple(H.add(self,engine,name,zone='library',ref='AMOUNT_TOP'+str(i)) for i,name in enumerate(names))
                library=engine.state.players['A'].zones['library']
                for card in cards:library.remove(card.object_id)
                library.extend(card.object_id for card in reversed(cards))
                source=H.add(self,engine,'Generic Discover Cast X' if mode=='cast_x' else
                    'Generic Discover Public Quantity',zone='hand');self.closed(source)
                action=H.ready(self,session,source,{'R':1,'C':3} if mode=='cast_x' else {'R':1});H.checkpoint(self,session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto',**({'x':3} if mode=='cast_x' else {})})
                self.assertTrue(accepted.ok,accepted.summary);decision=H.resolve(self,session)
                self.assertIsNotNone(decision)
                self.assertEqual(3 if mode=='cast_x' else 2,decision.continuation['effect']['progress']['amount'])
                self.assertTrue(all(engine.state.cards[card.object_id].zone=='exile' for card in cards))
                session=H.replay(self,session,load=True);engine=session.engine
                if mode=='cast_x':
                    accepted=session.act('pilot:A',{'action_id':'choose','choice':'decline'})
                    self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
                    self.assertEqual('hand',engine.state.cards[cards[1].object_id].zone)
                else:
                    options=session.packet('pilot:A',full=True)['decision']['ctx']['cast_options']
                    accepted=session.act('pilot:A',{'action_id':'choose','choice':'cast','cast':{
                        'cost_option':options[0]['id'],'targets':[target.ref],
                        'sacrifice_cards':[artifact.ref for artifact in artifacts]}})
                    self.assertTrue(accepted.ok,accepted.summary)
                    self.assertTrue(all(engine.state.cards[artifact.object_id].zone=='graveyard' for artifact in artifacts))
                    event=next(event for event in engine.state.events if event.code=='library.discover')
                    self.assertEqual(2,event.details['amount'])
                    H.resolve(self,session);self.assertEqual('graveyard',engine.state.cards[target.object_id].zone)
                H.replay(self,session,load=True)

    def test_original_hidden_nursery_pays_sacrifices_and_discovers_after_the_source_leaves(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,949060);engine=session.engine
        candidate=H.add(self,engine,'Hill Giant',zone='library',ref='LAND_DISCOVERY')
        source=H.add(self,engine,'Hidden Nursery');self.closed(source)
        H.ready(self,session,source,{'G':1,'C':4})
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions if row['id'].startswith('activate:'+source.ref+':')
            and 'Discover' in row.get('label',''))
        H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',engine.state.cards[source.object_id].zone)
        self.assertEqual(0,sum(engine.state.players['A'].mana_pool.values()))
        self.assertIsNotNone(H.resolve(self,session))
        self.assertEqual('exile',engine.state.cards[candidate.object_id].zone)
        session=H.replay(self,session,load=True);engine=session.engine
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'decline'})
        self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
        self.assertEqual('hand',engine.state.cards[candidate.object_id].zone)
        self.assertEqual('graveyard',engine.state.cards[source.object_id].zone)
        H.replay(self,session,load=True)

    def test_discovered_spell_additional_cost_replacement_preserves_parent_permission_and_pays_once(self):
        self.assert_discovered_cost_replacement('Cascade Trial',1,949070)

    def test_two_discovered_spell_cost_objects_wait_and_resume_their_distinct_replacements(self):
        self.assert_discovered_cost_replacement('Generic Discover Double Cost',2,949071)

    def test_paid_conditional_source_return_waits_for_owner_aura_choice_before_parent_followup(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.conditional_exile_model import ConditionalExileBinding,ExiledObjectIdentity
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.record import authoritative_state_hash
        session=H.session(self,949080);engine=session.engine
        cost=H.add(self,engine,'Generic Bound Body',ref='RETURN_COST')
        held=H.add(self,engine,'Generic Conditional Aura',seat='C',zone='exile',ref='RETURN_AURA')
        attachment=H.add(self,engine,'Generic Bound Body',seat='B',ref='RETURN_SUBJECT')
        binding=ConditionalExileBinding('existing-discover-cost-hold',
            ContinuousObjectIdentity(cost.object_id,cost.logical_object_id),
            (ExiledObjectIdentity(held.object_id,held.ref,held.logical_object_id),))
        ZoneTransitionOwner(engine).remember_conditional_exile(binding)
        candidate=H.add(self,engine,'Generic Conditional Cast Sacrifice',zone='library',ref='RETURN_SPELL');self.closed(candidate)
        source=H.add(self,engine,'Generic Discover Followup',zone='hand');self.closed(source)
        action=H.ready(self,session,source,{'R':1});H.checkpoint(self,session)
        before_hand=len(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
        options=session.packet('pilot:A',full=True)['decision']['ctx']['cast_options']
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'cast',
            'cast':{'cost_option':options[0]['id'],'sacrifice_cards':[cost.ref]}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('aura.entry',session.state.pending_decision.kind)
        self.assertEqual(['pilot:C'],list(session.pending_principals()))
        self.assertEqual(('graveyard','exile','stack','stack'),tuple(engine.state.cards[card.object_id].zone
            for card in (cost,held,candidate,source)))
        self.assertEqual(before_hand-1,len(engine.state.players['A'].zones['hand']))
        session=H.replay(self,session,load=True);engine=session.engine
        before=authoritative_state_hash(engine.state)
        rejected=session.act('pilot:A',{'action_id':'choose','aura_target':attachment.ref})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        rejected=session.act('pilot:C',{'action_id':'choose','aura_target':cost.ref})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:C',{'action_id':'choose','aura_target':attachment.ref})
        self.assertTrue(accepted.ok,accepted.summary)
        returned=engine.state.cards[held.object_id]
        self.assertEqual(('battlefield','C',attachment.object_id),(returned.zone,returned.controller,returned.attached_to))
        self.assertEqual('graveyard',engine.state.cards[source.object_id].zone)
        self.assertEqual('stack',engine.state.cards[candidate.object_id].zone)
        self.assertEqual(before_hand,len(engine.state.players['A'].zones['hand']))
        H.resolve(self,session)
        self.assertEqual(before_hand+1,len(engine.state.players['A'].zones['hand']))
        self.assertEqual(1,sum(event.code=='cost.sacrifice' and event.details.get('object')==cost.ref for event in engine.state.events))
        H.replay(self,session,load=True)

    def assert_discovered_cost_replacement(self,candidate_name,cost_count,seed):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=H.session(self,seed);engine=session.engine
        candidate=H.add(self,engine,candidate_name,zone='library',ref='COST_CANDIDATE');self.closed(candidate)
        artifacts=tuple(H.add(self,engine,'Generic Bound Mentor',ref='COST_ARTIFACT'+str(index)) for index in range(cost_count))
        target=H.add(self,engine,'Generic Bound Body',seat='B',ref='COST_TARGET')
        # Same reviewed replacement controls as the current SBA/casting tests;
        # Dauthi whole-card strict admission is not the claim under test.
        H.add(self,engine,'Dauthi Voidwalker',seat='B',ref='COST_REPLACE_ONE')
        H.add(self,engine,'Dauthi Voidwalker',seat='B',ref='COST_REPLACE_TWO')
        source=H.add(self,engine,'Generic Discover Followup',zone='hand');self.closed(source)
        action=H.ready(self,session,source,{'R':1});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
        options=session.packet('pilot:A',full=True)['decision']['ctx']['cast_options']
        self.assertTrue(options)
        self.assertTrue(all(artifact.ref in options[0]['choice_schema']['sacrifice_cards']['legal_refs'] for artifact in artifacts))
        before_hand=list(engine.state.players['A'].zones['hand'])
        before_payment=authoritative_state_hash(engine.state)
        rejected=session.act('pilot:A',{'action_id':'choose','choice':'cast',
            'cast':{'_mana_payment_id':'FORGED','cost_option':options[0]['id'],
                'targets':[target.ref],'sacrifice_cards':[artifact.ref for artifact in artifacts]}})
        self.assertFalse(rejected.ok);self.assertEqual(before_payment,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'cast',
            'cast':{'cost_option':options[0]['id'],'targets':[target.ref],'sacrifice_cards':[artifact.ref for artifact in artifacts]}})
        if not accepted.ok:self.assertEqual(before_payment,authoritative_state_hash(engine.state))
        self.assertTrue(accepted.ok,accepted.summary)
        for index in range(cost_count):
            self.assertEqual('replacement.order',session.state.pending_decision.kind)
            self.assertEqual('exile',engine.state.cards[candidate.object_id].zone)
            self.assertTrue(all(engine.state.cards[artifact.object_id].zone=='battlefield' for artifact in artifacts))
            self.assertFalse(any(item.card_object_id==candidate.object_id for item in session.state.stack))
            self.assertEqual(before_hand,engine.state.players['A'].zones['hand'])
            session=H.replay(self,session,load=True);engine=session.engine
            before=authoritative_state_hash(engine.state)
            selected=session.packet('pilot:A',full=True)['decision']['ctx']['options'][index%2]['id']
            self.assertEqual(before,authoritative_state_hash(engine.state),'Replacement projection changed authoritative state')
            rejected=session.act('pilot:B',{'action_id':'choose','replacement':selected})
            self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
            accepted=session.act('pilot:A',{'action_id':'choose','replacement':selected})
            self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(all(engine.state.cards[artifact.object_id].zone=='exile' for artifact in artifacts))
        self.assertEqual('stack',engine.state.cards[candidate.object_id].zone)
        self.assertEqual(len(before_hand)+1,len(engine.state.players['A'].zones['hand']))
        for artifact in artifacts:
            self.assertEqual(1,sum(event.code=='cost.sacrifice' and event.details.get('object')==artifact.ref for event in engine.state.events))
        # The same opponent replacements also apply to each A-owned spell's
        # eventual graveyard destination, independently of its paid cost.
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertEqual('stack',engine.state.cards[source.object_id].zone)
        selected=session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
        accepted=session.act('pilot:A',{'action_id':'choose','replacement':selected})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('exile',engine.state.cards[source.object_id].zone)
        self.assertIsNotNone(H.resolve(self,session))
        self.assertEqual('graveyard',engine.state.cards[target.object_id].zone)
        selected=session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
        accepted=session.act('pilot:A',{'action_id':'choose','replacement':selected})
        self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
        self.assertEqual('exile',engine.state.cards[candidate.object_id].zone)
        H.replay(self,session,load=True)

    def test_original_daring_discovery_exiles_until_inclusive_limit_then_casts_or_puts_in_hand(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        for index,choice in enumerate(('cast','decline')):
            with self.subTest(choice=choice):
                session=H.session(self,949001+index);engine=session.engine
                names=('Forest','Trumpeting Carnosaur','Hill Giant','Generic Bound Growth')
                cards=tuple(H.add(self,engine,name,zone='library',ref='DISCOVER'+str(i)) for i,name in enumerate(names))
                library=engine.state.players['A'].zones['library']
                for card in cards:library.remove(card.object_id)
                library.extend(card.object_id for card in reversed(cards))
                target=H.add(self,engine,'Generic Bound Body',seat='B',ref='BLOCKER')
                source=H.add(self,engine,'Daring Discovery',zone='hand');self.closed(source)
                action=H.ready(self,session,source,{'R':2,'C':4});H.checkpoint(self,session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
                self.assertEqual(['exile','exile','exile','library'],
                    [engine.state.cards[card.object_id].zone for card in cards])
                self.assertIn('Hill Giant',str(session.packet('pilot:B',full=True)))
                self.assertNotIn('Generic Bound Growth',str(session.packet('pilot:B',full=True)))
                session=H.replay(self,session,load=True);engine=session.engine
                response={'action_id':'choose','choice':choice}
                before=authoritative_state_hash(engine.state)
                rejected=session.act('pilot:B',response)
                self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
                accepted=session.act('pilot:A',response)
                self.assertTrue(accepted.ok,accepted.summary)
                if choice=='cast':
                    self.assertTrue(any(item.card_object_id==cards[2].object_id and item.kind=='spell' for item in session.state.stack))
                H.resolve(self,session)
                self.assertEqual('battlefield' if choice=='cast' else 'hand',engine.state.cards[cards[2].object_id].zone)
                self.assertTrue(all(engine.state.cards[card.object_id].zone=='library' for card in (cards[0],cards[1],cards[3])))
                self.assertEqual(1,sum(event.code=='library.discover' for event in engine.state.events))
                self.assertNotIn('Generic Bound Growth',str(session.packet('pilot:B',full=True)))
                H.replay(self,session,load=True)

    def test_original_daring_discovery_empty_library_completes_without_a_choice(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,949010);engine=session.engine
        for oid in tuple(engine.state.players['A'].zones['library']):engine.move_card(oid,'outside',log=False)
        target=H.add(self,engine,'Generic Bound Body',seat='B',ref='BLOCKER')
        source=H.add(self,engine,'Daring Discovery',zone='hand')
        self.closed(source)
        action=H.ready(self,session,source,{'R':2,'C':4});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(H.resolve(self,session))
        self.assertEqual(1,sum(event.code=='library.discover' for event in engine.state.events))
        H.replay(self,session,load=True)

    def test_nested_cast_uses_front_card_types_and_finishes_followup_before_the_new_spell_resolves(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,949020);engine=session.engine
        names=('Forest','Cascade Creature // Cascade Refuge','Generic Bound Growth')
        cards=tuple(H.add(self,engine,name,zone='library',ref='NESTED'+str(i)) for i,name in enumerate(names))
        library=engine.state.players['A'].zones['library']
        for card in cards:library.remove(card.object_id)
        library.extend(card.object_id for card in reversed(cards))
        source=H.add(self,engine,'Generic Discover Followup',zone='hand');self.closed(source)
        action=H.ready(self,session,source,{'R':1});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
        self.assertEqual(('exile','exile','library'),tuple(engine.state.cards[card.object_id].zone for card in cards))
        session=H.replay(self,session,load=True);engine=session.engine
        options=session.packet('pilot:A',full=True)['decision']['ctx']['cast_options']
        self.assertEqual(['Cascade Creature'],[option.get('face') for option in options])
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'cast',
            'cast':{'face':'Cascade Creature','cost_option':options[0]['id']}})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',engine.state.cards[source.object_id].zone)
        self.assertEqual('hand',engine.state.cards[cards[2].object_id].zone)
        self.assertTrue(any(item.kind=='spell' and item.card_object_id==cards[1].object_id for item in session.state.stack))
        H.resolve(self,session)
        self.assertEqual('battlefield',engine.state.cards[cards[1].object_id].zone)
        self.assertEqual('Cascade Creature',engine.state.cards[cards[1].object_id].active_face)
        H.replay(self,session,load=True)

    def test_original_discovery_uses_combined_split_value_then_offers_distinct_legal_faces(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=H.session(self,949030);engine=session.engine
        cards=tuple(H.add(self,engine,name,zone='library',ref='SPLIT'+str(i))
            for i,name in enumerate(('Boom // Bust','Dead // Gone','Generic Bound Growth')))
        library=engine.state.players['A'].zones['library']
        for card in cards:library.remove(card.object_id)
        library.extend(card.object_id for card in reversed(cards))
        target=H.add(self,engine,'Generic Bound Body',seat='B',ref='BLOCKER')
        source=H.add(self,engine,'Daring Discovery',zone='hand');self.closed(source)
        action=H.ready(self,session,source,{'R':2,'C':4});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
        self.assertEqual(['exile','exile','library'],[engine.state.cards[card.object_id].zone for card in cards])
        session=H.replay(self,session,load=True);engine=session.engine
        options=session.packet('pilot:A',full=True)['decision']['ctx']['cast_options']
        self.assertEqual({'Dead','Gone'},{option.get('face') for option in options})
        self.assertEqual(len(options),len({option['id'] for option in options}))
        gone=next(option for option in options if option['face']=='Gone')
        before=authoritative_state_hash(engine.state)
        rejected=session.act('pilot:A',{'action_id':'choose','choice':'cast',
            'cast':{'face':'Dead // Gone','cost_option':gone['id'],'targets':[target.ref]}})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'cast',
            'cast':{'face':'Gone','cost_option':gone['id'],'targets':[target.ref]}})
        self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
        self.assertEqual('hand',engine.state.cards[target.object_id].zone)
        self.assertEqual('graveyard',engine.state.cards[cards[1].object_id].zone)
        self.assertEqual('library',engine.state.cards[cards[0].object_id].zone)
        H.replay(self,session,load=True)

    def test_original_discovery_moves_an_uncastable_card_to_hand_and_free_x_cast_announces_zero(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        for index,name in enumerate(('Generic Bound Growth','Endless One')):
            with self.subTest(candidate=name):
                session=H.session(self,949040+index);engine=session.engine
                card=H.add(self,engine,name,zone='library',ref='CANDIDATE')
                target=H.add(self,engine,'Generic Bound Body',seat='B',ref='BLOCKER')
                source=H.add(self,engine,'Daring Discovery',zone='hand');self.closed(source)
                action=H.ready(self,session,source,{'R':2,'C':4});H.checkpoint(self,session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);decision=H.resolve(self,session)
                if name=='Generic Bound Growth':
                    self.assertIsNone(decision)
                    self.assertEqual('hand',engine.state.cards[card.object_id].zone)
                else:
                    self.assertIsNotNone(decision);self.closed(card)
                    session=H.replay(self,session,load=True);engine=session.engine
                    before=authoritative_state_hash(engine.state)
                    rejected=session.act('pilot:A',{'action_id':'choose','choice':'cast','cast':{'x':1}})
                    self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
                    accepted=session.act('pilot:A',{'action_id':'choose','choice':'cast','cast':{'x':0}})
                    self.assertTrue(accepted.ok,accepted.summary)
                    spell=next(item for item in session.state.stack if item.card_object_id==card.object_id)
                    self.assertEqual(0,spell.x_value);H.resolve(self,session)
                    self.assertEqual('graveyard',engine.state.cards[card.object_id].zone)
                H.replay(self,session,load=True)

    def test_cleanup_commander_replacement_suspends_the_whole_bottom_batch_then_resumes_the_tail(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=H.session(self,949050);engine=session.engine
        commander=next(card for card in engine.state.cards.values() if card.owner=='A' and card.is_commander)
        engine.state.players['A'].zones[commander.zone].remove(commander.object_id)
        commander.zone='library';commander.zone_change_counter+=1
        commander.known_to=['A'];commander.revealed_to=['A']
        engine.state.players['A'].zones['library'].append(commander.object_id)
        other=H.add(self,engine,'Generic Bound Body',zone='library',ref='CLEANUP_OTHER')
        candidate=H.add(self,engine,'Endless One',zone='library',ref='CLEANUP_ZERO')
        tail=H.add(self,engine,'Generic Bound Growth',zone='library',ref='CLEANUP_TAIL')
        cards=(commander,other,candidate,tail);library=engine.state.players['A'].zones['library']
        for card in cards:library.remove(card.object_id)
        library.extend(card.object_id for card in reversed(cards))
        source=H.add(self,engine,'Generic Discover Zero',zone='hand');self.closed(source)
        action=H.ready(self,session,source,{'R':1});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'decline'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertEqual(('exile','exile','library'),tuple(engine.state.cards[card.object_id].zone
            for card in (commander,other,tail)))
        session=H.replay(self,session,load=True);engine=session.engine
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        selected=next(option['id'] for option in options if not option['id'].startswith('decline:'))
        before=authoritative_state_hash(engine.state)
        rejected=session.act('pilot:B',{'action_id':'choose','replacement':selected})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:A',{'action_id':'choose','replacement':selected})
        self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
        self.assertEqual(('command','library','hand','hand'),tuple(engine.state.cards[card.object_id].zone for card in cards))
        self.assertEqual(1,sum(event.code=='library.discover' for event in engine.state.events))
        H.replay(self,session,load=True)
