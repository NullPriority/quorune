"""Pinned CR609.3/701.20 count boundaries before any library inspection."""

from copy import deepcopy
import unittest

from quorune.object_predicate import ObjectQuerySpec
from quorune.variable_library_selection_model import (
    ALL_LOOKED,fixed_selection_for_resolved_count,validate_variable_library_selection,
)


class VariableLibrarySelectionValueTests(unittest.TestCase):
    def effect(self,count=4):
        return {'op':'variable_library_selection','schema_version':1,'player':'A','look_count':count,
            'public_reveal':False,'selected_reveal':False,'selection_policy':'fixed_any',
            'minimum':1,'maximum':1,'predicate_groups':[],
            'remainder_destination':'library_bottom','remainder_order':'chosen'}

    def test_zero_has_no_inspection_and_positive_delegates_exact_fixed_shape(self):
        self.assertIsNone(fixed_selection_for_resolved_count(self.effect(0)))
        original=self.effect();before=deepcopy(original)
        fixed=fixed_selection_for_resolved_count(original)
        self.assertEqual(before,original)
        self.assertEqual('fixed_library_selection',fixed['op']);self.assertNotIn('schema_version',fixed)
        self.assertEqual(4,fixed['look_count']);self.assertEqual((1,1),(fixed['minimum'],fixed['maximum']))
        fixed['predicate_groups'].append({'mutated':True})
        self.assertEqual([],original['predicate_groups'])

    def test_fixed_requested_selection_does_only_as_much_as_the_inspection_allows(self):
        effect={**self.effect(1),'minimum':2,'maximum':2}
        fixed=fixed_selection_for_resolved_count(effect)
        self.assertEqual((1,1,1),(fixed['look_count'],fixed['minimum'],fixed['maximum']))

    def test_all_matching_maximum_follows_the_resolved_count(self):
        query=ObjectQuerySpec(zones=('library',),types_any=('creature',)).to_dict()
        effect={**self.effect(3),'public_reveal':True,'selection_policy':'all_matching',
            'minimum':0,'maximum':ALL_LOOKED,'predicate_groups':[[query]]}
        fixed=fixed_selection_for_resolved_count(effect)
        self.assertEqual((3,0,3),(fixed['look_count'],fixed['minimum'],fixed['maximum']))
        self.assertEqual([[query]],fixed['predicate_groups']);self.assertTrue(fixed['public_reveal'])

    def test_two_optional_slots_reduce_to_one_union_for_one_inspected_card(self):
        creature=ObjectQuerySpec(zones=('library',),types_any=('creature',)).to_dict()
        land=ObjectQuerySpec(zones=('library',),types_any=('land',)).to_dict()
        effect={**self.effect(1),'selection_policy':'optional_slots','minimum':0,'maximum':2,
            'selected_reveal':True,'predicate_groups':[[creature],[land]]}
        fixed=fixed_selection_for_resolved_count(effect)
        self.assertEqual(('up_to_matching',0,1),(fixed['selection_policy'],fixed['minimum'],fixed['maximum']))
        self.assertEqual([[creature,land]],fixed['predicate_groups'])
        self.assertTrue(fixed['selected_reveal'])
        fixed=fixed_selection_for_resolved_count({**effect,'look_count':2})
        self.assertEqual('optional_slots',fixed['selection_policy'])
        self.assertEqual([[creature],[land]],fixed['predicate_groups'])

    def test_unknown_authority_shapes_and_boolean_counts_reject_even_at_zero(self):
        for change in ({'look_count':True},{'look_count':-1},{'look_count':{}},{'maximum':False},
                       {'maximum':'arbitrary'},{'schema_version':True},{'player':' A'},
                       {'public_reveal':1},{'remainder_destination':'battlefield'},{'extra':0}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                fixed_selection_for_resolved_count({**self.effect(0),**change})

    def test_unresolved_count_requires_an_existing_typed_amount_owner(self):
        from quorune.query_effect_amount_model import CastXAmountSpec
        effect={**self.effect(),'look_count':CastXAmountSpec().to_dict()}
        validate_variable_library_selection(effect)
        with self.assertRaises(ValueError):fixed_selection_for_resolved_count(effect)

    def test_public_count_and_cast_x_compiler_keep_complete_selection_shape(self):
        from quorune.compiler.variable_library_selection_templates import variable_library_selection_effect_template
        public=variable_library_selection_effect_template(
            'Look at the top X cards of your library, where X is the number of artifacts you control. '
            'Put one of those cards into your hand and the rest on the bottom of your library in any order.',source_name='Source')
        self.assertIsNotNone(public)
        self.assertEqual('variable_library_selection',public[1][0]['op'])
        self.assertIsInstance(public[1][0]['look_count'],dict)
        self.assertEqual((1,1),(public[1][0]['minimum'],public[1][0]['maximum']))
        cast=variable_library_selection_effect_template(
            'Reveal the top X cards of your library. Put all creature cards revealed this way into your hand '
            'and the rest on the bottom of your library in any order.',source_name='Source',cast_x_available=True)
        self.assertIsNotNone(cast)
        self.assertEqual(ALL_LOOKED,cast[1][0]['maximum']);self.assertTrue(cast[1][0]['public_reveal'])

    def test_count_bridge_has_no_query_or_choice_and_retains_remaining_work(self):
        from types import SimpleNamespace
        from quorune.semantic_choices.variable_library_selection import VariableLibrarySelectionHandler
        class NoInspectionContext:
            actor='A';stack_controller='A'
            @property
            def query(self):raise AssertionError('Count bridge must not inspect cards')
        handler=VariableLibrarySelectionHandler();context=NoInspectionContext()
        zero=handler.prepare(self.effect(0),context)
        self.assertIsNone(zero.request);self.assertEqual((),zero.preparation_intents)
        self.assertEqual((),zero.auto_continue.prepend_effects)
        positive=handler.prepare(self.effect(2),context)
        self.assertIsNone(positive.request);self.assertEqual((),positive.preparation_intents)
        self.assertEqual(1,len(positive.auto_continue.prepend_effects))
        self.assertEqual('fixed_library_selection',positive.auto_continue.prepend_effects[0]['op'])
        with self.assertRaises(ValueError):handler.prepare(self.effect(2),SimpleNamespace(actor='B',stack_controller='A'))

    def test_compiler_rejects_other_libraries_unowned_x_and_incomplete_procedure(self):
        from quorune.compiler.variable_library_selection_templates import variable_library_selection_effect_template as compile
        base='Look at the top X cards of your library. Put one of those cards into your hand and the rest on the bottom of your library in any order.'
        self.assertIsNone(compile(base,source_name='Source'))
        for text in (base.replace('your library',"target opponent's library"),
                     base.replace('the top X cards','the top X plus one cards'),
                     base.replace(' and the rest on the bottom of your library in any order.','.'),
                     base+' Draw a card.'):
            with self.subTest(text=text):self.assertIsNone(compile(text,source_name='Source',cast_x_available=True))

    def test_compiler_and_dependency_withdrawal_fail_closed(self):
        import json
        from common import ROOT
        from dataclasses import replace
        from unittest.mock import patch
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        source=replace(query_record('Look at the top X cards of your library, where X is the number of artifacts you control. '
            'Put one of those cards into your hand and the rest on the bottom of your library in any order.'),
            type_line='Instant',power=None,toughness=None)
        registry=load_default_capability_registry()
        ir=compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,[(row.kind,row.reason,row.blockers) for row in ir.material_residuals])
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf8'))
        for dependency in ('library.select.variable_controller','library.select.fixed_controller'):
            changed=deepcopy(raw);cap=next(row for row in changed['capabilities'] if row['id']==dependency)
            cap['status']='blocked';cap['blockers']=['Withdrawn by focused mutation']
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=CapabilityRegistry(changed),
                capability_profile='commander_review').status)
        with patch('quorune.compiler.variable_library_selection_templates.variable_library_selection_effect_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review').status)


class VariableLibrarySelectionNativeTests(unittest.TestCase):
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
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'variable-inspection.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/variable-library-selection-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Variable inspection',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def assert_closed(self,card):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])

    def top_cards(self,engine):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        return tuple(H.add(self,engine,name,zone='library',ref='TOP'+str(index))
            for index,name in enumerate(('Generic Bound Body','Generic Bound Growth','Generic Bound Plains')))

    def test_original_machinate_locks_public_count_then_keeps_look_and_order_private(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        session=H.session(self,929001);engine=session.engine
        for _ in range(3):engine.create_token('A',name='Counter source',characteristics={'type_line':'Token Artifact'})
        top=self.top_cards(engine)
        source=H.add(self,engine,'Machinate',zone='hand');self.assert_closed(source)
        action=H.ready(self,session,source,{'U':2,'C':1});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
        self.assertNotIn('Generic Bound Body',str(session.packet('pilot:B',full=True)))
        session=H.replay(self,session,load=True);engine=session.engine
        response={'action_id':'choose','cards':{'hand':[top[0].ref],'bottom':[top[1].ref,top[2].ref]}}
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',response)
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',response)
        self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
        self.assertEqual('hand',engine.state.cards[top[0].object_id].zone)
        self.assertEqual(2,sum(engine.state.cards[card.object_id].zone=='library' for card in top))
        self.assertNotIn('Generic Bound Body',str(session.packet('pilot:B',full=True)))
        H.replay(self,session,load=True)

    def test_original_enshrined_memories_announced_zero_and_three_reveal_only_inspected_cards(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        for count in (0,3):
            with self.subTest(count=count):
                session=H.session(self,929010+count);engine=session.engine
                top=self.top_cards(engine);source=H.add(self,engine,'Enshrined Memories',zone='hand')
                self.assert_closed(source)
                action=H.ready(self,session,source,{'G':1,'C':3});H.checkpoint(self,session)
                original_library=list(engine.state.players['A'].zones['library'])
                accepted=session.act('pilot:A',{'action_id':action['id'],'x':count,'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);decision=H.resolve(self,session)
                if count==0:
                    self.assertIsNone(decision);self.assertEqual(original_library,engine.state.players['A'].zones['library'])
                    self.assertNotIn('Generic Bound Body',str(session.packet('pilot:B',full=True)))
                else:
                    self.assertIsNotNone(decision)
                    self.assertIn('Generic Bound Body',str(session.packet('pilot:B',full=True)))
                    session=H.replay(self,session,load=True);engine=session.engine
                    before=authoritative_state_hash(session.state)
                    rejected=session.act('pilot:A',{'action_id':'choose','cards':{'hand':[],
                        'bottom':[card.ref for card in top]}})
                    self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
                    accepted=session.act('pilot:A',{'action_id':'choose','cards':{'hand':[top[0].ref],
                        'bottom':[top[1].ref,top[2].ref]}})
                    self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
                    self.assertEqual('hand',engine.state.cards[top[0].object_id].zone)
                H.replay(self,session,load=True)

    def test_original_machinate_short_library_and_stale_top_reject_before_movement(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        for index,stale in enumerate((False,True)):
            with self.subTest(stale=stale):
                session=H.session(self,929020+index);engine=session.engine
                for oid in tuple(engine.state.players['A'].zones['library']):engine.move_card(oid,'outside',log=False)
                top=H.add(self,engine,'Generic Bound Body',zone='library',ref='SHORT_TOP')
                for _ in range(3):engine.create_token('A',name='Artifact count',characteristics={'type_line':'Token Artifact'})
                source=H.add(self,engine,'Machinate',zone='hand');self.assert_closed(source)
                action=H.ready(self,session,source,{'U':2,'C':1});H.checkpoint(self,session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
                session=H.replay(self,session,load=True);engine=session.engine
                if stale:
                    # Deliberate authoritative-state corruption exercises the
                    # continuation's immutable top binding, not a legal action.
                    engine.move_card(top.object_id,'graveyard',log=False)
                    before=authoritative_state_hash(engine.state)
                    rejected=session.act('pilot:A',{'action_id':'choose','cards':{'hand':[top.ref],'bottom':[]}})
                    self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
                    self.assertEqual('graveyard',engine.state.cards[top.object_id].zone)
                else:
                    accepted=session.act('pilot:A',{'action_id':'choose','cards':{'hand':[top.ref],'bottom':[]}})
                    self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
                    self.assertEqual('hand',engine.state.cards[top.object_id].zone)
                    self.assertEqual([],engine.state.players['A'].zones['library'])
                    H.replay(self,session,load=True)
