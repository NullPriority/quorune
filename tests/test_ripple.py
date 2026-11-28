"""Independent Ripple numeric and captured-public-name boundaries."""
from dataclasses import FrozenInstanceError,replace
import unittest
from quorune.ripple_model import RippleSpec,RippleCastSnapshot


class RippleModelTests(unittest.TestCase):
    def test_fixed_count_and_captured_names_round_trip_without_current_card_or_mutation(self):
        for count in (0,4,1000):
            value=RippleSpec(count);self.assertEqual(value,RippleSpec.from_dict(value.to_dict()))
        snapshot=RippleCastSnapshot('A','S1',('Alias','Shared Name'))
        wire=snapshot.to_dict();copy=RippleCastSnapshot.from_dict(wire)
        self.assertEqual(snapshot,copy);wire['rules_names'].append('Later Name')
        self.assertEqual(('Alias','Shared Name'),snapshot.rules_names)
        self.assertTrue(snapshot.matches_names(('Other Alias','Shared Name')))
        self.assertFalse(snapshot.matches_names(('Later Name',)))
        self.assertFalse(snapshot.matches_names(()))
        self.assertFalse(RippleCastSnapshot('A','S2',()).matches_names(()))
        with self.assertRaises(FrozenInstanceError):snapshot.controller='B'

    def test_numeric_and_snapshot_codecs_reject_open_versions_unknown_names_and_mutable_data(self):
        for count in (True,-1,None,'4',4.0):
            with self.subTest(count=count),self.assertRaises(ValueError):RippleSpec(count)
        for version in (True,0,2):
            with self.subTest(version=version),self.assertRaises(ValueError):RippleSpec(4,schema_version=version)
        with self.assertRaises(ValueError):RippleSpec.from_dict({'schema_version':1,'count':4,'free':True})
        source=RippleCastSnapshot('A','S1',('Name',));wire=source.to_dict()
        for changes in ({'schema_version':True},{'rules_names':None},{'rules_names':['Name','Name']},
                {'rules_names':['Z','A']},{'controller':''},{'source_spell_ref':None},{'extra':None}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):RippleCastSnapshot.from_dict({**wire,**changes})
        with self.assertRaises(ValueError):RippleCastSnapshot('A','S1',['Name'])
        with self.assertRaises(ValueError):source.matches_names(None)

    def test_fresh_first_name_only_mutant_is_killed_by_multiple_public_names(self):
        from unittest.mock import patch
        result=unittest.TestResult()
        witness=type(self)('test_fixed_count_and_captured_names_round_trip_without_current_card_or_mutation')
        def wrong(source,names):
            return bool(source.rules_names and names and source.rules_names[0]==names[0])
        with patch.object(RippleCastSnapshot,'matches_names',wrong):witness.run(result)
        self.assertEqual([],result.errors);self.assertEqual(1,len(result.failures))


class RippleInstructionTests(unittest.TestCase):
    def test_closed_instruction_and_program_require_actual_source_cast_and_independent_trigger(self):
        from types import SimpleNamespace
        from quorune.rules.ripple import (ripple_instruction,ripple_event_condition,
            decode_ripple_instruction,ripple_program_dependencies)
        from quorune.compiler.spell_cast_predicates import FixedSpellCastSubject,FixedSpellCastController,FixedSpellCastQuality
        effect=ripple_instruction(RippleSpec(4));spec,snapshot=decode_ripple_instruction(effect)
        self.assertEqual(RippleSpec(4),spec);self.assertIsNone(snapshot)
        common=FixedSpellCastSubject(FixedSpellCastController.SOURCE,quality=FixedSpellCastQuality.ANY,source_spell=True)
        self.assertEqual(common.event_condition,ripple_event_condition())
        program=SimpleNamespace(effects=(effect,),target_schema=None,event='spell.cast',active_zone='stack',event_condition=common.event_condition)
        self.assertIn('keyword.ripple.fixed_count',ripple_program_dependencies(program))
        for field,value in (('event','spell.cast_or_copy'),('active_zone','battlefield'),('event_condition',None),('target_schema',{})):
            with self.subTest(field=field),self.assertRaises(ValueError):
                ripple_program_dependencies(SimpleNamespace(**{**vars(program),field:value}))
        for changes in ({'player':'B'},{'extra':None},{'cast_snapshot':None},{'ripple':{}}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):decode_ripple_instruction({**effect,**changes})
        with self.assertRaises(ValueError):decode_ripple_instruction(effect,allow_symbolic=False)

    def test_cast_snapshot_captures_public_rules_name_before_source_moves_or_changes(self):
        from types import SimpleNamespace
        from quorune.rules.ripple import capture_ripple_cast_snapshot
        source=SimpleNamespace(zone='stack',ref='CARD',controller='A',face_down=False,
            phased_out=False,annotations={},printed_name='Wrong Printed Name')
        data={'name':'Actual Cast Name','type_line':'Instant'}
        host=SimpleNamespace(card_record=lambda card:None,_effective_card_data=lambda card:data)
        event={'card':'CARD','controller':'A','stack':'S1'}
        snapshot=capture_ripple_cast_snapshot(host,source,event)
        data['name']='Later Name';source.zone='graveyard'
        self.assertEqual(('Actual Cast Name',),snapshot.rules_names)
        self.assertTrue(snapshot.matches_names(('Actual Cast Name',)));self.assertFalse(snapshot.matches_names(('Later Name',)))
        source.zone='stack'
        for changes in ({'card':'OTHER'},{'controller':'B'},{'stack':None}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):capture_ripple_cast_snapshot(host,source,{**event,**changes})


class RippleKeywordCompilerTests(unittest.TestCase):
    reminder=('When you cast this spell, you may reveal the top four cards of your library. '
        'You may cast spells with the same name as this spell from among those cards without paying their mana costs. '
        'Put the rest on the bottom of your library.')

    def test_numeric_keyword_and_complete_printed_reminder_match_without_ignoring_unknown_tail(self):
        from quorune.compiler.ripple_nodes import ripple_spec_from_line
        for text in ('Ripple 4','Ripple 4 ('+self.reminder+')','Ripple 0','Ripple 17.'):
            with self.subTest(text=text):self.assertIsNotNone(ripple_spec_from_line(text))
        for text in ('Ripple X','Ripple -1','Ripple 04','Ripple 4, ripple 4','Ripple 4. Draw a card.',
                'Ripple 3 ('+self.reminder+')','Ripple 4 ('+self.reminder.replace('may reveal','reveal')+')'):
            with self.subTest(text=text):self.assertIsNone(ripple_spec_from_line(text))

    def test_default_keyword_classifier_recognizes_numeric_ripple_and_keeps_unknown_keyword_closed(self):
        from quorune.compiler.keyword_templates import keyword_mechanics
        self.assertEqual(('ripple',),keyword_mechanics('Ripple 4',('Ripple',)))
        self.assertEqual(('ripple',),keyword_mechanics('Ripple 4',('Ripple','Flying')))
        self.assertIsNone(keyword_mechanics('Ripple 4',()))
        self.assertIsNone(keyword_mechanics('Rippled 4',('Ripple',)))

    def test_multiple_printed_instances_compile_to_separate_self_cast_triggers(self):
        from dataclasses import replace
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        record=replace(query_record('Ripple 4\nRipple 4\nDraw a card.'),type_line='Sorcery',keywords=('Ripple',),power=None,toughness=None)
        ir=compile_oracle_card(record,capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        nodes=[n for n in ir.faces[0].nodes if n.event=='spell.cast']
        self.assertEqual(2,len(nodes));self.assertEqual(2,len({n.node_id for n in nodes}))
        self.assertEqual(['stack','stack'],[n.active_zone for n in nodes])

    def test_source_spanned_node_stays_blocked_without_registered_runtime_and_uses_self_cast_condition(self):
        from quorune.compiler.ripple_nodes import ripple_keyword_node
        from quorune.compiler.ir_model import SourceSpan
        from quorune.rules.capabilities import load_default_capability_registry
        line='Ripple 4 ('+self.reminder+')';span=SourceSpan(10,10+len(line),3);residuals=[]
        node=ripple_keyword_node(node_id='n1',line=line,material_line='Ripple 4',span=span,
            mechanics=('ripple',),capability_registry=None,capability_profile='commander_review',residuals=residuals)
        self.assertEqual(span,node.span);self.assertEqual(line,node.text)
        self.assertEqual('spell.cast',node.event);self.assertEqual('stack',node.active_zone)
        self.assertTrue(node.lowerable);self.assertFalse(node.exact)
        self.assertEqual(4,node.effects[0]['ripple']['count']);self.assertTrue(residuals)


class RippleStackRulesNameTests(unittest.TestCase):
    def test_canonical_stack_names_distinguish_one_split_half_both_fused_names_and_face_down(self):
        from types import SimpleNamespace
        from quorune.carddb_characteristics import public_stack_rules_names,public_object_rules_names
        card=SimpleNamespace(zone='stack',face_down=False,phased_out=False)
        record=SimpleNamespace(name='Front // Back',layout='split',faces=({'name':'Front'},{'name':'Back'}))
        self.assertEqual(('Front',),public_stack_rules_names(card,{'name':'Front'},record))
        self.assertEqual(('Front','Back'),public_stack_rules_names(card,{'name':'Front // Back'},record))
        self.assertEqual(('Current Copy Name',),public_stack_rules_names(card,{'name':'Current Copy Name'},record))
        self.assertEqual((),public_object_rules_names(card,{'name':'Front'},record))
        card.face_down=True;self.assertEqual((),public_stack_rules_names(card,{'name':'Front'},record))
        card.face_down=False
        with self.assertRaises(ValueError):public_stack_rules_names(card,{},record)


class RippleRevealStateTests(unittest.TestCase):
    def state(self,stage='cast'):
        from quorune.rules.ripple_resolution_model import RippleResolutionState
        from quorune.rules.library_selection import LibrarySelectionObjectIdentity
        return RippleResolutionState(RippleSpec(4),RippleCastSnapshot('A','SPELL',('Same Name',)),'TRIGGER',
            (LibrarySelectionObjectIdentity('a','a@1','A1'),LibrarySelectionObjectIdentity('b','b@1','B1')),stage=stage)

    def test_reveal_state_round_trip_skips_cast_or_reincarnated_cards_without_fetching_them_back(self):
        from types import SimpleNamespace
        from quorune.rules.ripple_resolution_model import RippleResolutionState
        state=self.state();wire=state.effect()
        self.assertEqual(state,RippleResolutionState.from_effect(wire,actor='A'))
        rows={'A1':SimpleNamespace(object_id='a',logical_object_id='a@1',owner='A'),
            'B1':SimpleNamespace(object_id='b',logical_object_id='b@2',owner='A')}
        query=SimpleNamespace(object=lambda ref,**kw:rows.get(ref))
        self.assertEqual(('A1',),tuple(i.ref for i in state.remaining(query)))
        rows.pop('A1');self.assertEqual((),state.remaining(query))
        with self.assertRaises(ValueError):RippleResolutionState.from_effect(wire,actor='B')
        with self.assertRaises(ValueError):RippleResolutionState.from_effect({**wire,'extra':None},actor='A')

    def test_cast_and_bottom_intents_require_exact_controller_stack_stage_and_reveal_set(self):
        from quorune.rules.ripple_resolution_model import ResolveRippleCastIntent,RippleBottomIntent
        from quorune.replacement.immutable import FrozenMap
        state=self.state()
        intent=ResolveRippleCastIntent('A','TRIGGER',state,'0'*64,FrozenMap({'card':'A1'}))
        self.assertEqual(state,intent.state)
        from quorune.rules.ripple_resolution_model import validate_ripple_cast_identity,validate_ripple_bottom_identity
        self.assertEqual(intent.identity(),validate_ripple_cast_identity(intent.identity()))
        with self.assertRaises(ValueError):replace(intent,response=FrozenMap({'_mana_payment_id':'forged'}))
        with self.assertRaises(ValueError):validate_ripple_cast_identity({**intent.identity(),'extra':None})
        for changes in ({'actor':'B'},{'stack_ref':'OTHER'},{'options_fingerprint':'bad'},{'response':{}},
                {'state':self.state('bottom')}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):replace(intent,**changes)
        bottom=RippleBottomIntent('A','TRIGGER',self.state('bottom'),('B1','A1'))
        self.assertEqual(bottom.identity(),validate_ripple_bottom_identity(bottom.identity()))
        with self.assertRaises(ValueError):replace(bottom,order=({},'A1'))
        for changes in ({'order':('A1',)},{'order':('B1','B1')},{'actor':'B'},{'state':state}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):replace(bottom,**changes)

    def test_private_bottom_uses_current_revealed_set_after_shuffle_preserving_unseen_order_and_incarnations(self):
        from types import SimpleNamespace
        from quorune.rules.ripple_resolution_model import RippleBottomIntent
        from quorune.rules.revealed_library_bottom import execute_ripple_bottom,RevealedLibraryBottomOrder
        from quorune.library_order_model import LibraryArrivalOrder
        state=self.state('bottom');library=['a','hidden','b','other']
        cards={i.ref:SimpleNamespace(object_id=i.object_id,logical_object_id=i.logical_object_id,
            owner='A',zone='library',known_to=['A','B']) for i in state.looked}
        item=SimpleNamespace(ref='TRIGGER',controller='A',context={'currently_resolving':True,'ripple_cast_snapshot':state.source.to_dict()})
        events=[]
        host=SimpleNamespace(state=SimpleNamespace(stack=[item],players={'A':SimpleNamespace(zones={'library':library})}),
            _require_seat=lambda *a,**k:None,_resolve_object=lambda actor,ref,**k:cards[ref],
            _log=lambda *a,**k:events.append((a,k)))
        intent=RippleBottomIntent('A','TRIGGER',state,('B1','A1'));self.assertEqual(2,execute_ripple_bottom(host,intent))
        self.assertEqual(['b','a','hidden','other'],library)
        self.assertTrue(all(c.logical_object_id.endswith('@1') for c in cards.values()))
        self.assertTrue(all(c.known_to==['A'] for c in cards.values()))
        self.assertNotIn('order',events[0][0][3]);self.assertEqual(['A1','B1'],events[0][0][3]['objects'])
        order=RevealedLibraryBottomOrder('A','bottom',('a','b'));self.assertEqual(('library',),order.origin_zones)
        self.assertEqual(order,RevealedLibraryBottomOrder.from_dict(order.to_dict()))
        with self.assertRaises(ValueError):LibraryArrivalOrder.from_dict(order.to_dict())

    def test_bottom_rejects_changed_identity_before_any_library_placement(self):
        from types import SimpleNamespace
        from quorune.rules.ripple_resolution_model import RippleBottomIntent
        from quorune.rules.revealed_library_bottom import execute_ripple_bottom
        from quorune.errors import GameRuleError
        state=self.state('bottom');library=['a','b','hidden']
        cards={i.ref:SimpleNamespace(object_id=i.object_id,logical_object_id='wrong' if i.ref=='B1' else i.logical_object_id,
            owner='A',zone='library',known_to=['A']) for i in state.looked}
        item=SimpleNamespace(ref='TRIGGER',controller='A',context={'currently_resolving':True,'ripple_cast_snapshot':state.source.to_dict()})
        host=SimpleNamespace(state=SimpleNamespace(stack=[item],players={'A':SimpleNamespace(zones={'library':library})}),
            _require_seat=lambda *a,**k:None,_resolve_object=lambda actor,ref,**k:cards[ref],_log=lambda *a,**k:None)
        with self.assertRaises(GameRuleError):execute_ripple_bottom(host,RippleBottomIntent('A','TRIGGER',state,('B1','A1')))
        self.assertEqual(['a','b','hidden'],library)


class RippleChoiceFlowTests(unittest.TestCase):
    def source_effect(self,count=2):
        from quorune.rules.ripple import ripple_instruction
        return {**ripple_instruction(RippleSpec(count)),'player':'A',
            'cast_snapshot':RippleCastSnapshot('A','ORIGINAL',('Same Name',)).to_dict()}

    def query(self,options=(),omit=()):
        from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery
        from quorune.object_query import ObjectQueryResult
        from quorune.replacement.immutable import FrozenMap
        rows=tuple(ObjectQueryResult(i,ref,name,'A','A','library',logical_object_id=i+'@1')
            for i,ref,name in (('a','A1','Same Name'),('b','B1','Other Revealed'),('h','H1','Hidden Name')) if ref not in omit)
        return SnapshotSemanticChoiceQuery(('A','B'),('A','B'),object_rows=rows,
            libraries_by_seat=FrozenMap({'A':[ref for ref in ('H1','B1','A1') if ref not in omit]}),
            authorized_cast_option_rows=tuple(FrozenMap(row) for row in options))

    def context(self,query):
        from types import SimpleNamespace
        return SimpleNamespace(actor='A',stack_controller='A',stack_ref='TRIGGER',query=query)

    def continuation(self,effect):
        from types import SimpleNamespace
        return SimpleNamespace(effect=effect,stack_ref='TRIGGER')

    def revealed(self,query):
        from quorune.semantic_choices.ripple import complete_ripple
        return complete_ripple(self.continuation(self.source_effect()),{'choice':'reveal'},query).prepend_effects[0]

    def test_decline_does_not_reveal_and_accepted_reveal_is_the_actual_clamped_top_prefix(self):
        import json
        from quorune.semantic_choices.ripple import prepare_ripple,complete_ripple
        from quorune.semantic_runtime.intents import RevealLibraryCardsIntent
        query=self.query();effect=self.source_effect()
        preparation=prepare_ripple(effect,self.context(query))
        self.assertIsNotNone(preparation.request);self.assertEqual((),preparation.preparation_intents)
        self.assertNotIn('Hidden Name',json.dumps(dict(preparation.request.public_context)))
        declined=complete_ripple(self.continuation(preparation.continuation_effect),{'choice':'decline'},query)
        self.assertEqual((),declined.intents);self.assertEqual((),declined.prepend_effects)
        revealed=complete_ripple(self.continuation(effect),{'choice':'reveal'},query)
        self.assertEqual(1,len(revealed.intents));self.assertIsInstance(revealed.intents[0],RevealLibraryCardsIntent)
        self.assertTrue(revealed.intents[0].public);self.assertEqual(('A1','B1'),revealed.intents[0].refs_top_first)
        short=complete_ripple(self.continuation(self.source_effect(9)),{'choice':'reveal'},query)
        self.assertEqual(('A1','B1','H1'),short.intents[0].refs_top_first)
        empty=prepare_ripple(self.source_effect(0),self.context(query))
        self.assertIsNone(empty.request);self.assertIsNotNone(empty.auto_continue)

    def test_issued_cast_repeats_same_captured_reveal_set_and_rejects_changed_price_or_unrevealed_option(self):
        from quorune.semantic_choices.ripple import prepare_ripple,complete_ripple
        from quorune.semantic_choices.model import SemanticChoiceError
        from quorune.rules.ripple_resolution_model import ResolveRippleCastIntent
        option={'id':'issued:A1','card':'A1','kind':'mana','requirements':{}}
        query=self.query((option,));effect=self.revealed(query)
        preparation=prepare_ripple(effect,self.context(query))
        completed=complete_ripple(self.continuation(preparation.continuation_effect),
            {'choice':'cast','card':'A1','cost_option':'issued:A1'},query)
        self.assertIsInstance(completed.intents[0],ResolveRippleCastIntent);self.assertIsNotNone(completed.repeat_effect)
        self.assertEqual('ORIGINAL',completed.intents[0].state.source.source_spell_ref)
        with self.assertRaises(SemanticChoiceError):
            complete_ripple(self.continuation(preparation.continuation_effect),{'choice':'decline'},self.query())
        with self.assertRaises(SemanticChoiceError):
            prepare_ripple(effect,self.context(self.query(({**option,'card':'H1'},))))

    def test_finishing_casts_orders_only_original_surviving_identities_and_never_the_hidden_suffix(self):
        from quorune.semantic_choices.ripple import prepare_ripple,complete_ripple
        from quorune.rules.ripple_resolution_model import RippleBottomIntent
        from quorune.semantic_choices.model import SemanticChoiceError
        option={'id':'issued:A1','card':'A1','kind':'mana','requirements':{}}
        query=self.query((option,));effect=self.revealed(query);prepared=prepare_ripple(effect,self.context(query))
        finish=complete_ripple(self.continuation(prepared.continuation_effect),{'choice':'decline'},query)
        bottom=prepare_ripple(finish.prepend_effects[0],self.context(query));self.assertIsNotNone(bottom.request)
        complete=complete_ripple(self.continuation(bottom.continuation_effect),{'order':['B1','A1']},query)
        self.assertIsInstance(complete.intents[0],RippleBottomIntent);self.assertEqual(('B1','A1'),complete.intents[0].order)
        with self.assertRaises(SemanticChoiceError):
            complete_ripple(self.continuation(bottom.continuation_effect),{'order':['A1','H1']},query)
        moved=prepare_ripple(finish.prepend_effects[0],self.context(self.query(omit=('A1',))))
        self.assertIsNone(moved.request);self.assertEqual(('B1',),moved.preparation_intents[0].order)

    def test_no_legal_free_cast_moves_to_private_bottom_order_without_an_exile_or_hand_detour(self):
        from quorune.semantic_choices.ripple import prepare_ripple
        from quorune.rules.ripple_resolution_model import RippleResolutionState
        query=self.query();effect=self.revealed(query)
        prepared=prepare_ripple(effect,self.context(query))
        self.assertIsNone(prepared.request);self.assertEqual((),prepared.preparation_intents)
        state=RippleResolutionState.from_effect(prepared.auto_continue.prepend_effects[0],actor='A')
        self.assertEqual('bottom',state.stage);self.assertEqual(('A1','B1'),tuple(i.ref for i in state.looked))


class RippleCopiedControllerTests(unittest.TestCase):
    def test_copied_trigger_uses_current_controller_library_without_rewriting_original_cast_names(self):
        from types import SimpleNamespace
        from quorune.rules.ripple import ripple_instruction
        from quorune.rules.ripple_resolution_model import RippleResolutionState
        from quorune.semantic_choices.ripple import prepare_ripple,complete_ripple
        from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery
        from quorune.object_query import ObjectQueryResult
        from quorune.replacement.immutable import FrozenMap
        source=RippleCastSnapshot('A','ORIGINAL',('Original Spell Name',))
        effect={**ripple_instruction(RippleSpec(4)),'player':'B','cast_snapshot':source.to_dict()}
        row=ObjectQueryResult('b','B1','Original Spell Name','B','B','library',logical_object_id='b@1')
        query=SnapshotSemanticChoiceQuery(('A','B'),('A','B'),object_rows=(row,),libraries_by_seat=FrozenMap({'B':['B1']}))
        context=SimpleNamespace(actor='B',stack_controller='B',stack_ref='COPIED_TRIGGER',query=query)
        self.assertIsNotNone(prepare_ripple(effect,context).request)
        completed=complete_ripple(SimpleNamespace(effect=effect,stack_ref='COPIED_TRIGGER'),{'choice':'reveal'},query)
        self.assertEqual('B',completed.intents[0].player)
        state=RippleResolutionState.from_effect(completed.prepend_effects[0],actor='B')
        self.assertEqual('B',state.actor);self.assertEqual(source,state.source)
        self.assertEqual(('B1',),tuple(i.ref for i in state.remaining(query)))


class RippleLiveBridgeReplayTests(unittest.TestCase):
    def intents(self):
        from quorune.rules.library_selection import LibrarySelectionObjectIdentity
        from quorune.rules.ripple_resolution_model import RippleResolutionState,ResolveRippleCastIntent,RippleBottomIntent
        from quorune.replacement.immutable import FrozenMap
        state=RippleResolutionState(RippleSpec(4),RippleCastSnapshot('A','SOURCE',('Same',)),'TRIGGER',
            (LibrarySelectionObjectIdentity('b','b@1','B1'),),actor='B')
        cast=ResolveRippleCastIntent('B','TRIGGER',state,'0'*64,FrozenMap({'card':'B1','cost_option':'issued:B1'}))
        bottom_state=replace(state,stage='bottom')
        return cast,RippleBottomIntent('B','TRIGGER',bottom_state,('B1',))

    def test_cast_and_bottom_identities_and_cost_replacement_preserve_copied_controller(self):
        from quorune.semantic_choices.intent_replacement import semantic_intent_identity,validate_semantic_intent_identity,with_replacement_selections
        for intent in self.intents():
            kind,data=semantic_intent_identity(intent)
            self.assertEqual(data,validate_semantic_intent_identity(kind,data))
        cast=self.intents()[0]
        resumed=with_replacement_selections(cast,('replacement:one',))
        self.assertEqual(('replacement:one',),resumed.replacement_selections)
        self.assertEqual('B',resumed.actor);self.assertEqual('A',resumed.state.source.controller)

    def test_partial_completed_choice_round_trip_keeps_full_ripple_values_and_cast_response(self):
        import json
        from quorune.semantic_choices.model import SemanticChoiceContinuation,SemanticChoiceFrame,SemanticChoiceCompletion
        from quorune.semantic_choices.completed_choice import encode_remaining_completion,decode_remaining_completion
        from quorune.replacement.immutable import FrozenMap
        cast,bottom=self.intents()
        continuation=SemanticChoiceContinuation(handler_id='choice.public.objects-then.v1',handler_version=1,
            stack_ref='TRIGGER',effect=FrozenMap(cast.state.effect()),remaining=(),destination=None,note='Ripple',
            semantic_frame=SemanticChoiceFrame('fixture:ripple',1,'TRIGGER',0,'B'))
        completion=SemanticChoiceCompletion(intents=(cast,bottom))
        encoded=encode_remaining_completion(continuation,'B',{'choice':'cast'},completion,0)
        restored=decode_remaining_completion(json.loads(json.dumps(encoded)))
        self.assertEqual(completion.intents,restored[3].intents)
        self.assertEqual('B',restored[3].intents[0].state.actor)
        self.assertEqual('A',restored[3].intents[0].state.source.controller)

    def test_library_name_reader_uses_both_split_names_and_front_mdfc_name_outside_stack(self):
        from types import SimpleNamespace
        from quorune.carddb_characteristics import public_revealed_library_rules_names
        card=SimpleNamespace(zone='library')
        record=SimpleNamespace(name='Front // Back',layout='split',faces=({'name':'Front'},{'name':'Back'}))
        self.assertEqual(('Front','Back'),public_revealed_library_rules_names(card,record))
        record.layout='modal_dfc'
        self.assertEqual(('Front',),public_revealed_library_rules_names(card,record))
        card.zone='hand'
        with self.assertRaises(ValueError):public_revealed_library_rules_names(card,record)


from inherited_composition_fixture import InheritedNativeFixture


class RippleNativeFixture(InheritedNativeFixture):
    extra_ripple_fixtures = ()

    @classmethod
    def setUpClass(cls):
        import json,tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();root=Path(cls.temporary.name)
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[r for r in base['cards'] if r['name']=='Generic Bound Plains'];base['rulings']=[]
        path=root/'base.json';path.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([path,ROOT/'tests/fixtures/ripple-cards.json',
                                *cls.extra_ripple_fixtures],root/'ripple.sqlite3')
        cls.db=CardDatabase(root/'ripple.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Ripple',[DeckEntry('Sliver Hivelord',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])


class RippleNativeTests(RippleNativeFixture,unittest.TestCase):
    def test_all_five_complete_originals_bind_normally_and_keep_existing_spell_or_permanent_bodies(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        for name in ('Surging Aether','Surging Dementia','Surging Flame','Surging Might','Surging Sentinels'):
            card=self.db.lookup(name);ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            program=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
            binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
            self.assertTrue(binding['strict_capability_ready'],(name,binding['blockers']))

    def test_actual_paid_original_declines_reveal_without_changing_library_then_deals_printed_damage_and_replays(self):
        import json
        s=self.session(702600101);source=self.add(s.engine,'Surging Flame',zone='hand')
        secret=self.add(s.engine,'Counterspell',zone='library',ref='ripple-unrevealed-secret')
        self.assert_closed(s,source);self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s)
        before=list(s.state.players['A'].zones['library'])
        self.cast_for(s,'A',source,['B']);self.assertEqual(1,self.trigger_count(s,source))
        decision=self.resolve(s);self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        result=s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(before,s.state.players['A'].zones['library'])
        self.assertEqual(38,s.state.players['B'].life)
        for viewer in 'BCD':self.assertNotIn(secret.ref,json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.replay(s,load=True)

    def test_actual_reveal_casts_named_library_card_without_mana_orders_only_remainder_and_replays_nested_trigger(self):
        s=self.session(702600102);source=self.add(s.engine,'Surging Flame',zone='hand')
        remainder=[self.add(s.engine,name,zone='library',ref='ripple-remainder-'+str(i))
            for i,name in enumerate(('Wastes','Island','Grizzly Bears'))]
        copy=self.add(s.engine,'Surging Flame',zone='library',ref='ripple-free-copy')
        self.assert_closed(s,source);self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s)
        self.cast_for(s,'A',source,['B']);self.assertEqual('semantic.choice',self.resolve(s).kind)
        result=s.act('pilot:A',{'action_id':'choose','choice':'reveal'});self.assertTrue(result.ok,result.summary)
        packet=s.packet('pilot:A',full=True)['decision'];self.assertEqual('semantic.choice',packet['kind'])
        options=packet['ctx'].get('cast_options') or packet['ctx'].get('context',{}).get('cast_options') or ()
        self.assertTrue(any(o['card']==copy.ref for o in options),options)
        option=next(o for o in options if o['card']==copy.ref)
        result=s.act('pilot:A',{'action_id':'choose','choice':'cast',
            'cast':{'card':copy.ref,'cost_option':option['id'],'targets':['C'],'pay':'auto'}})
        self.assertTrue(result.ok,result.summary);self.assertEqual('stack',s.state.cards[copy.object_id].zone)
        self.assertFalse(any(s.state.players['A'].mana_pool.values()))
        decision=s.packet('pilot:A',full=True)['decision'];self.assertEqual('semantic.choice',decision['kind'])
        order=tuple(c.ref for c in remainder)
        result=s.act('pilot:A',{'action_id':'choose','order':list(order)});self.assertTrue(result.ok,result.summary)
        self.assertEqual([c.object_id for c in remainder],s.state.players['A'].zones['library'][:len(remainder)])
        decision=self.resolve(s);self.assertIsNotNone(decision);self.assertEqual('semantic.choice',decision.kind)
        result=s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual((38,38),(s.state.players['B'].life,s.state.players['C'].life))
        self.assertFalse(s.state.stack);self.replay(s,load=True)

    def test_actual_reveal_with_no_same_name_cast_orders_only_top_four_and_keeps_hidden_suffix_private(self):
        import json
        s=self.session(702600103);source=self.add(s.engine,'Surging Flame',zone='hand')
        cards=[self.add(s.engine,name,zone='library',ref='ripple-public-top-'+str(i))
            for i,name in enumerate(('Wastes','Island','Grizzly Bears','Forest'))]
        secret=self.add(s.engine,'Counterspell',zone='library',ref='ripple-hidden-suffix')
        library=s.state.players['A'].zones['library']
        library.remove(secret.object_id);library.insert(0,secret.object_id)
        self.assert_closed(s,source);self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s)
        original=list(s.state.players['A'].zones['library'])
        self.cast_for(s,'A',source,['B']);self.assertEqual('semantic.choice',self.resolve(s).kind)
        result=s.act('pilot:A',{'action_id':'choose','choice':'reveal'});self.assertTrue(result.ok,result.summary)
        decision=s.packet('pilot:A',full=True)['decision'];self.assertEqual('semantic.choice',decision['kind'])
        self.assertNotIn('cast_options',decision['ctx'])
        order=list(reversed(cards))
        from quorune.record import authoritative_state_hash
        before=authoritative_state_hash(s.state)
        wrong=s.act('pilot:A',{'action_id':'choose','order':[secret.ref]+[c.ref for c in order[1:]]})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:A',{'action_id':'choose','order':[c.ref for c in order]});self.assertTrue(result.ok,result.summary)
        self.assertEqual([c.object_id for c in order]+original[:-4],s.state.players['A'].zones['library'])
        for viewer in 'BCD':
            packet=json.dumps(s.packet('pilot:'+viewer,full=True))
            self.assertNotIn(secret.ref,packet)
        self.assertIsNone(self.resolve(s));self.assertEqual(38,s.state.players['B'].life)
        self.replay(s,load=True)

    def test_actual_empty_library_finishes_ripple_without_a_choice_or_draw_loss_and_replays(self):
        s=self.session(702600104);source=self.add(s.engine,'Surging Flame',zone='hand')
        for oid in tuple(s.state.players['A'].zones['library']):
            s.engine.move_card(oid,'exile',reason='Empty Ripple library fixture preparation',log=False)
        self.assert_closed(s,source);self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s)
        self.cast_for(s,'A',source,['B']);self.assertEqual(1,self.trigger_count(s,source))
        self.assertIsNone(self.resolve(s));self.assertFalse(s.state.game_over)
        self.assertEqual([],s.state.players['A'].zones['library'])
        self.assertEqual(38,s.state.players['B'].life);self.replay(s,load=True)

    def test_actual_short_library_reveals_all_without_choosing_a_smaller_count_and_replays(self):
        s=self.session(702600105);source=self.add(s.engine,'Surging Flame',zone='hand')
        for oid in tuple(s.state.players['A'].zones['library']):
            s.engine.move_card(oid,'exile',reason='Short Ripple library fixture preparation',log=False)
        card=self.add(s.engine,'Wastes',zone='library',ref='ripple-only-library-card')
        self.assert_closed(s,source);self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s)
        self.cast_for(s,'A',source,['B']);self.assertEqual('semantic.choice',self.resolve(s).kind)
        from quorune.record import authoritative_state_hash
        before=authoritative_state_hash(s.state)
        invalid=s.act('pilot:A',{'action_id':'choose','choice':'reveal','count':0})
        self.assertFalse(invalid.ok);self.assertEqual(before,authoritative_state_hash(s.state))
        result=s.act('pilot:A',{'action_id':'choose','choice':'reveal'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual([card.object_id],s.state.players['A'].zones['library'])
        self.assertEqual(38,s.state.players['B'].life);self.replay(s,load=True)


    def test_actual_mandatory_tax_still_blocks_or_charges_revealed_free_casts_and_replays(self):
        for spare in (0,1):
            with self.subTest(spare=spare):
                s=self.session(702600110+spare);source=self.add(s.engine,'Surging Flame',zone='hand')
                tax=self.add(s.engine,'Sphere of Resistance')
                child=self.add(s.engine,'Surging Flame',zone='library',ref='ripple-tax-child')
                self.assert_closed(s,source);self.assert_closed(s,tax)
                self.prepare(s,mana={'C':2+spare,'R':1});self.checkpoint(s)
                self.cast_for(s,'A',source,['B']);self.assertEqual('semantic.choice',self.resolve(s).kind)
                result=s.act('pilot:A',{'action_id':'choose','choice':'reveal'});self.assertTrue(result.ok,result.summary)
                ctx=s.packet('pilot:A',full=True)['decision']['ctx']
                options=ctx.get('cast_options') or ctx.get('context',{}).get('cast_options') or ()
                if spare:
                    option=next(o for o in options if o['card']==child.ref)
                    self.assertEqual(1,option['requirements']['GENERIC'])
                    result=s.act('pilot:A',{'action_id':'choose','choice':'cast',
                        'cast':{'card':child.ref,'cost_option':option['id'],'targets':['C']}})
                    self.assertTrue(result.ok,result.summary);self.assertEqual('stack',s.state.cards[child.object_id].zone)
                else:
                    self.assertFalse(options);self.assertEqual('library',s.state.cards[child.object_id].zone)
                self.assertFalse(any(s.state.players['A'].mana_pool.values()))
                decision=s.packet('pilot:A',full=True)['decision']
                schema=decision['legal_actions'][0]['choice_schema']
                result=s.act('pilot:A',{'action_id':'choose','order':schema['legal_refs']})
                self.assertTrue(result.ok,result.summary)
                if spare:
                    self.assertEqual('semantic.choice',self.resolve(s).kind)
                    result=s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
                self.assertIsNone(self.resolve(s))
                self.assertEqual((38,38 if spare else 40),(s.state.players['B'].life,s.state.players['C'].life))
                self.replay(s,load=True)

    def test_actual_drannith_prohibits_revealed_library_cast_but_keeps_paid_hand_spell_and_replays(self):
        s=self.session(702600120);source=self.add(s.engine,'Surging Flame',zone='hand')
        child=self.add(s.engine,'Surging Flame',zone='library',ref='ripple-prohibited-child')
        guard=self.add(s.engine,'Drannith Magistrate',seat='B')
        self.assert_closed(s,source);self.assert_closed(s,guard)
        self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s)
        self.cast_for(s,'A',source,['B']);self.assertEqual('semantic.choice',self.resolve(s).kind)
        result=s.act('pilot:A',{'action_id':'choose','choice':'reveal'});self.assertTrue(result.ok,result.summary)
        decision=s.packet('pilot:A',full=True)['decision'];ctx=decision['ctx']
        self.assertFalse(ctx.get('cast_options') or ctx.get('context',{}).get('cast_options'))
        schema=decision['legal_actions'][0]['choice_schema'];self.assertIn(child.ref,schema['legal_refs'])
        result=s.act('pilot:A',{'action_id':'choose','order':schema['legal_refs']});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual('library',s.state.cards[child.object_id].zone)
        self.assertEqual(38,s.state.players['B'].life);self.replay(s,load=True)

    def test_fresh_native_name_match_mutant_is_killed_after_real_library_cast_witness(self):
        from unittest.mock import patch
        from quorune.ripple_model import RippleCastSnapshot
        native=type(self)('test_actual_reveal_casts_named_library_card_without_mana_orders_only_remainder_and_replays_nested_trigger')
        result=unittest.TestResult()
        with patch.object(RippleCastSnapshot,'matches_names',return_value=False):
            native.run(result)
        self.assertEqual([],result.errors);self.assertEqual(1,len(result.failures))


    def test_actual_post_cast_fault_restores_library_spell_mana_trigger_and_pending_choice_then_retries(self):
        from unittest.mock import patch
        from quorune.record import authoritative_state_hash
        from quorune.errors import GameRuleError
        import quorune.rules.ripple_casting as owner
        s=self.session(702600140);source=self.add(s.engine,'Surging Flame',zone='hand')
        child=self.add(s.engine,'Surging Flame',zone='library',ref='ripple-rollback-child')
        self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s);self.cast_for(s,'A',source,['B'])
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        result=s.act('pilot:A',{'action_id':'choose','choice':'reveal'});self.assertTrue(result.ok,result.summary)
        ctx=s.packet('pilot:A',full=True)['decision']['ctx']
        options=ctx.get('cast_options') or ctx.get('context',{}).get('cast_options') or ()
        selected=next(o for o in options if o['card']==child.ref)
        command={'action_id':'choose','choice':'cast','cast':{'card':child.ref,'cost_option':selected['id'],'targets':['C']}}
        before=authoritative_state_hash(s.state);identity=child.logical_object_id;commit=owner.commit_resolving_cast;observed=[]
        def fault(*args,**kwargs):
            commit(*args,**kwargs);observed.append(s.state.cards[child.object_id].zone)
            raise GameRuleError('Injected post Ripple cast fault')
        with patch.object(owner,'commit_resolving_cast',side_effect=fault):
            result=s.act('pilot:A',command);self.assertFalse(result.ok,result.summary)
            self.assertIn('Injected post Ripple cast fault',result.summary)
        self.assertEqual(['stack'],observed);self.assertEqual(before,authoritative_state_hash(s.state))
        self.assertEqual(('library',identity),(s.state.cards[child.object_id].zone,s.state.cards[child.object_id].logical_object_id))
        s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        decision=s.packet('pilot:A',full=True)['decision'];schema=decision['legal_actions'][0]['choice_schema']
        result=s.act('pilot:A',{'action_id':'choose','order':schema['legal_refs']});self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        result=s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual((38,38),(s.state.players['B'].life,s.state.players['C'].life))
        self.replay(s,load=True)

    def test_actual_original_counter_leaves_ripple_while_countered_ripple_leaves_original_spell_and_replays(self):
        for counter_trigger in (False,True):
            with self.subTest(counter_trigger=counter_trigger):
                s=self.session(702600150+counter_trigger);source=self.add(s.engine,'Surging Flame',zone='hand')
                counter=self.add(s.engine,'Stifle' if counter_trigger else 'Counterspell',seat='B',zone='hand')
                s.state.players['B'].mana_pool.update({'U':1 if counter_trigger else 2})
                self.assert_closed(s,counter);self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s)
                self.cast_for(s,'A',source,['C'])
                item=next(i for i in s.state.stack if i.kind==('triggered_ability' if counter_trigger else 'spell') and (i.source_object_id if counter_trigger else i.card_object_id)==source.object_id)
                self.cast_for(s,'B',counter,[item.ref]);self.resolve_top(s)
                if counter_trigger:
                    self.assertEqual(0,self.trigger_count(s,source));self.assertIsNone(self.resolve(s))
                    self.assertEqual(38,s.state.players['C'].life)
                else:
                    self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                    self.assertEqual('semantic.choice',self.resolve(s).kind)
                    result=s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
                    self.assertIsNone(self.resolve(s));self.assertEqual(40,s.state.players['C'].life)
                self.replay(s,load=True)

    def test_canonical_opponent_trigger_copy_preserves_cast_names_and_uses_own_library_during_native_cast_and_replay(self):
        s=self.session(702600160);source=self.add(s.engine,'Surging Flame',zone='hand')
        child=self.add(s.engine,'Surging Flame',seat='B',zone='library',ref='ripple-opponent-child')
        self.prepare(s,mana={'C':1,'R':1});self.checkpoint(s);self.cast_for(s,'A',source,['C'])
        original=next(i for i in s.state.stack if i.kind=='triggered_ability' and i.source_object_id==source.object_id)
        facts=dict(original.context['ripple_cast_snapshot'])
        # Exercise the existing canonical copy port. An opponent's utility card
        # that can request this copy is outside this family and is not admitted.
        copied=s.engine._copy_stack_item(controller='B',target=original,targets=[],target_groups={},
            reason='Ripple copied-controller interaction boundary')
        self.checkpoint(s)
        self.assertEqual(facts,copied.context['ripple_cast_snapshot'])
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual(['B'],s.state.pending_decision.actors)
        result=s.act('pilot:B',{'action_id':'choose','choice':'reveal'});self.assertTrue(result.ok,result.summary)
        ctx=s.packet('pilot:B',full=True)['decision']['ctx'];options=ctx.get('cast_options') or ctx.get('context',{}).get('cast_options') or ()
        selected=next(o for o in options if o['card']==child.ref)
        result=s.act('pilot:B',{'action_id':'choose','choice':'cast','cast':{'card':child.ref,'cost_option':selected['id'],'targets':['D']}})
        self.assertTrue(result.ok,result.summary);self.assertEqual('B',s.state.cards[child.object_id].controller)
        decision=s.packet('pilot:B',full=True)['decision'];schema=decision['legal_actions'][0]['choice_schema']
        result=s.act('pilot:B',{'action_id':'choose','order':schema['legal_refs']});self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual(['B'],s.state.pending_decision.actors)
        result=s.act('pilot:B',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual(['A'],s.state.pending_decision.actors)
        result=s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual((38,38),(s.state.players['C'].life,s.state.players['D'].life))
        self.replay(s,load=True)



    def test_actual_lithoform_spell_copy_is_uncast_and_creates_no_additional_ripple_trigger(self):
        s=self.session(702600170);source=self.add(s.engine,'Surging Flame',zone='hand')
        copier=self.add(s.engine,'Lithoform Engine');self.assert_closed(s,copier)
        self.prepare(s,mana={'C':4,'R':1});self.checkpoint(s)
        self.cast_for(s,'A',source,['B'],reserve={'C':3})
        original=next(i for i in s.state.stack if i.kind=='spell' and i.card_object_id==source.object_id)
        self.assertEqual(1,self.trigger_count(s,source));self.priority(s,'A')
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a['id'].startswith('activate:'+copier.ref+':') and original.ref in str(a.get('target_schema')))
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[original.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        result=s.act('pilot:A',{'action_id':'choose'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,len([i for i in s.state.stack if i.kind=='spell_copy']))
        self.assertEqual(1,self.trigger_count(s,source))
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual(38,s.state.players['B'].life)
        result=s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertEqual(36,s.state.players['B'].life)
        self.assertEqual(1,len(s.engine._current_turn_history('spell_cast')));self.replay(s,load=True)

    def test_actual_other_four_printed_original_bodies_remain_paid_and_execute_after_ripple_decline(self):
        cases=(('Surging Aether',{'C':3,'U':1}),('Surging Dementia',{'C':1,'B':1}),
            ('Surging Might',{'C':2,'G':1}),('Surging Sentinels',{'C':2,'W':1}))
        for index,(name,mana) in enumerate(cases):
            with self.subTest(name=name):
                s=self.session(702600180+index);source=self.add(s.engine,name,zone='hand')
                bear=self.add(s.engine,'Grizzly Bears',seat='B')
                if name=='Surging Dementia':
                    for oid in tuple(s.state.players['C'].zones['hand']):
                        s.engine.move_card(oid,'exile',reason='One-card discard fixture preparation',log=False)
                    discarded=self.add(s.engine,'Wastes',seat='C',zone='hand')
                targets=[] if name=='Surging Sentinels' else ['C'] if name=='Surging Dementia' else [bear.ref]
                self.assert_closed(s,source);self.prepare(s,mana=mana);self.checkpoint(s)
                self.cast_for(s,'A',source,targets);self.assertEqual(1,self.trigger_count(s,source))
                self.assertFalse(any(s.state.players['A'].mana_pool.values()))
                self.assertEqual('semantic.choice',self.resolve(s).kind)
                result=s.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
                decision=self.resolve(s)
                if name=='Surging Dementia':
                    self.assertIsNotNone(decision);self.assertEqual('choice.apnap',decision.kind)
                    result=s.act('pilot:C',{'action_id':'choose','cards':[discarded.ref]});self.assertTrue(result.ok,result.summary)
                    self.assertIsNone(self.resolve(s))
                else:self.assertIsNone(decision)
                if name=='Surging Aether':self.assertEqual('hand',s.state.cards[bear.object_id].zone)
                elif name=='Surging Dementia':
                    self.assertEqual([],s.state.players['C'].zones['hand']);self.assertEqual('graveyard',s.state.cards[discarded.object_id].zone)
                elif name=='Surging Might':
                    self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
                    self.assertEqual(bear.object_id,s.state.cards[source.object_id].attached_to)
                    self.assertEqual((4,4),(s.engine._numeric_stat(bear.object_id,'power'),s.engine._numeric_stat(bear.object_id,'toughness')))
                else:
                    self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
                    self.assertIn('first strike',s.engine._combat_keywords(s.state.cards[source.object_id]))
                self.replay(s,load=True)


class RippleRegistrationTests(RippleNativeFixture,unittest.TestCase):
    def test_independent_runtime_minimum_rejects_removed_fine_owner_without_trusting_coverage(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(702600130);source=self.add(s.engine,'Surging Flame',zone='hand')
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='spell.cast')
        self.assertTrue(s.engine.semantic_program_is_current_trusted(program))
        cap='keyword.ripple.fixed_count';deps=tuple(c for c in program.capability_dependencies if c!=cap)
        changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        result=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,result['blockers'])

    def test_genuinely_verified_ripple_and_library_owner_withdrawals_block_all_originals_and_keep_vanilla(self):
        import json
        from copy import deepcopy
        from pathlib import Path
        from common import ROOT
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        document=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        for cap in ('keyword.ripple.fixed_count','library.order.fixed_controller'):
            value=deepcopy(raw);next(c for c in value['capabilities'] if c['id']==cap).update(status='blocked',blockers=['Withdrawn Ripple or canonical library order authority'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=document,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            for name in ('Surging Aether','Surging Dementia','Surging Flame','Surging Might','Surging Sentinels'):
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status,(cap,name))
                blockers=[b for r in ir.material_residuals for b in r.blockers]
                self.assertTrue(any('status:'+cap+':blocked' in b for b in blockers),blockers)
                self.assertFalse(any('evidence_index:unverified' in b for b in blockers),blockers)
            ir=compile_oracle_card(self.db.lookup('Grizzly Bears'),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
