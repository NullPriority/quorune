"""Targeted search primitive contracts; no runtime admission or native proof claim."""
import unittest
from dataclasses import FrozenInstanceError
from quorune.rules.targeted_library_exile import TargetLibraryExileSpec
from quorune.compiler.targeted_library_exile_templates import targeted_library_exile_spec


class TargetLibraryExileContractTests(unittest.TestCase):
    def test_searcher_and_library_owner_are_separate_explicit_roles_without_changing_old_versions(self):
        spec=TargetLibraryExileSpec(3)
        effect=spec.effect(searching_player='A',library_player='B')
        self.assertEqual(('A','B'),(effect['searching_player'],effect['library_player']))
        self.assertEqual({'minimum':3,'maximum':3},effect['count'])
        self.assertEqual(('library','exile',False,True),(effect['zone'],effect['destination'],effect['optional'],effect['shuffle_after']))
        self.assertEqual(spec,TargetLibraryExileSpec.from_effect(effect,searching_player='A',library_player='B'))
        with self.assertRaises(ValueError):TargetLibraryExileSpec.from_effect(effect,searching_player='B',library_player='A')
        with self.assertRaises(FrozenInstanceError):spec.count=1

    def test_exact_numeric_and_boolean_codecs_reject_partial_or_legacy_search_payloads(self):
        for count in (0,-1,True,'3',3.0):
            with self.subTest(count=count),self.assertRaises(ValueError):TargetLibraryExileSpec(count)
        effect=TargetLibraryExileSpec(1).effect()
        for changes in ({'schema_version':True},{'schema_version':8},{'optional':0},{'reveal':0},
                {'shuffle_after':1},{'count':{'minimum':True,'maximum':1}},{'selector':{'types':['creature']}},
                {'destination':'hand'},{'extra':True}):
            with self.subTest(changes=changes),self.assertRaises((ValueError,TypeError)):
                TargetLibraryExileSpec.from_effect({**effect,**changes})

    def test_compiler_consumes_fixed_one_or_three_card_clause_and_every_material_rider(self):
        for text,count in (("Search target player's library for a card and exile it. Then that player shuffles.",1),
                ("Search target player's library for three cards and exile them. Then that player shuffles.",3)):
            with self.subTest(text=text):self.assertEqual(TargetLibraryExileSpec(count),targeted_library_exile_spec(text))
        for text in ("Search target player's library for a creature card and exile it. Then that player shuffles.",
                "Search target player's library for up to three cards and exile them. Then that player shuffles.",
                "Search that player's library for a card and exile it, then the player shuffles.",
                "Search target player's library for three card and exile it. Then that player shuffles.",
                "Search target player's library for a card and exile it. Then that player shuffles. Draw a card."):
            with self.subTest(text=text):self.assertIsNone(targeted_library_exile_spec(text))


class TargetLibrarySearcherConstraintTests(unittest.TestCase):
    def host(self):
        from types import SimpleNamespace
        return SimpleNamespace(state=SimpleNamespace(players={
            'A':SimpleNamespace(zones={'library':['A0','A1','A2'],'graveyard':['AG']}),
            'B':SimpleNamespace(zones={'library':['B0','B1','B2','B3'],'graveyard':['BG']})}))

    def test_searchers_top_restriction_slices_other_library_without_substituting_owner_permissions(self):
        from unittest.mock import patch
        from quorune.rules.library_search_rules import library_search_object_ids,LibrarySearchConstraint
        h=self.host()
        with patch('quorune.rules.library_search_rules.library_search_constraint',side_effect=lambda host,actor:
                LibrarySearchConstraint(top_count=2) if actor=='A' else LibrarySearchConstraint()) as rule:
            self.assertEqual(('B2','B3'),library_search_object_ids(h,'B','library',searching_player='A'))
            rule.assert_called_once_with(h,'A')
        self.assertEqual(['B0','B1','B2','B3'],h.state.players['B'].zones['library'])

    def test_search_prohibition_belongs_to_searcher_even_when_target_library_owner_is_allowed(self):
        from unittest.mock import patch
        from quorune.rules.library_search_rules import library_search_object_ids,LibrarySearchConstraint
        h=self.host()
        with patch('quorune.rules.library_search_rules.library_search_constraint',side_effect=lambda host,actor:
                LibrarySearchConstraint(prohibited=actor=='A')):
            self.assertEqual((),library_search_object_ids(h,'B','library',searching_player='A'))
            self.assertEqual(('B0','B1','B2','B3'),library_search_object_ids(h,'B','library'))
            self.assertEqual(('BG',),library_search_object_ids(h,'B','graveyard'))

    def test_explicit_missing_or_unknown_searcher_cannot_drop_search_permissions(self):
        from quorune.rules.library_search_rules import library_search_object_ids
        for actor in ('','UNKNOWN',True):
            with self.subTest(actor=actor),self.assertRaises(ValueError):
                library_search_object_ids(self.host(),'B','library',searching_player=actor)


    def test_fresh_owner_as_searcher_mutant_is_killed_by_independent_private_window(self):
        from unittest.mock import patch
        from quorune.rules import library_search_rules as owner
        from quorune.rules.targeted_library_exile import target_library_entries
        h,effect=TargetLibraryIdentityTests().fixture();original=owner.library_search_object_ids
        constraint=lambda host,actor:owner.LibrarySearchConstraint(top_count=2) if actor=='A' else owner.LibrarySearchConstraint()
        def witness():
            self.assertEqual(('B2','B3'),tuple(c.ref for c in target_library_entries(h,'A',effect)))
        with patch.object(owner,'library_search_constraint',side_effect=constraint):
            witness()
            with patch.object(owner,'library_search_object_ids',side_effect=lambda host,player,zone,**kw:original(host,player,zone)),self.assertRaises(AssertionError):
                witness()


class TargetLibraryIdentityTests(unittest.TestCase):
    def fixture(self):
        from types import SimpleNamespace
        h=TargetLibrarySearcherConstraintTests().host();h.state.active_seats=lambda:['A','B']
        h.state.cards={key:SimpleNamespace(object_id=key,logical_object_id=key+'@1',ref=key,
            owner=actor,zone='library',printed_name=key+' private') for actor in 'AB'
            for key in h.state.players[actor].zones['library']}
        return h,TargetLibraryExileSpec(1).effect(searching_player='A',library_player='B')

    def test_pinned_identities_use_target_owner_and_reject_same_reference_after_reentry(self):
        from unittest.mock import patch
        from quorune.rules.library_search_rules import LibrarySearchConstraint
        from quorune.rules.targeted_library_exile import bind_targeted_library_exile,targeted_library_exile_cards
        h,effect=self.fixture()
        with patch('quorune.rules.library_search_rules.library_search_constraint',return_value=LibrarySearchConstraint()):
            bound=bind_targeted_library_exile(h,'A',effect)
            self.assertEqual(['B0','B1','B2','B3'],[c.ref for c in targeted_library_exile_cards(h,'A',bound)])
            with self.assertRaises(ValueError):targeted_library_exile_cards(h,'A',bound,['A0'])
            h.state.cards['B0'].logical_object_id='B0@2'
            with self.assertRaisesRegex(ValueError,'incarnation'):targeted_library_exile_cards(h,'A',bound,['B0'])

    def test_search_window_refresh_cannot_expand_private_access_after_a_restriction_change(self):
        from unittest.mock import patch
        from quorune.rules.library_search_rules import LibrarySearchConstraint
        from quorune.rules.targeted_library_exile import bind_targeted_library_exile,targeted_library_exile_cards
        h,effect=self.fixture()
        with patch('quorune.rules.library_search_rules.library_search_constraint',return_value=LibrarySearchConstraint(top_count=2)):
            bound=bind_targeted_library_exile(h,'A',effect)
            self.assertEqual(['B2','B3'],[c.ref for c in targeted_library_exile_cards(h,'A',bound)])
        with patch('quorune.rules.library_search_rules.library_search_constraint',return_value=LibrarySearchConstraint()):
            with self.assertRaisesRegex(ValueError,'window'):targeted_library_exile_cards(h,'A',bound)

    def test_replayed_role_or_pending_identity_data_cannot_be_used_as_a_fresh_search(self):
        from unittest.mock import patch
        from quorune.rules.library_search_rules import LibrarySearchConstraint
        from quorune.rules.targeted_library_exile import bind_targeted_library_exile,targeted_library_exile_cards
        h,effect=self.fixture()
        with patch('quorune.rules.library_search_rules.library_search_constraint',return_value=LibrarySearchConstraint()):
            bound=bind_targeted_library_exile(h,'A',effect)
            with self.assertRaises(ValueError):bind_targeted_library_exile(h,'A',bound)
            with self.assertRaises(ValueError):targeted_library_exile_cards(h,'B',bound)
            with self.assertRaises(ValueError):targeted_library_exile_cards(h,'A',bound,['B0','B0'])
            h.state.active_seats=lambda:['A']
            with self.assertRaises(ValueError):targeted_library_exile_cards(h,'A',bound)


    def test_unqualified_cardinality_clamps_short_library_but_never_allows_optional_failure(self):
        from unittest.mock import patch
        from quorune.rules.library_search_rules import LibrarySearchConstraint
        from quorune.rules.targeted_library_exile import bind_targeted_library_exile,targeted_library_exile_cards
        h,_effect=self.fixture();h.state.players['B'].zones['library']=['B2','B3']
        effect=TargetLibraryExileSpec(3).effect(searching_player='A',library_player='B')
        with patch('quorune.rules.library_search_rules.library_search_constraint',return_value=LibrarySearchConstraint()):
            bound=bind_targeted_library_exile(h,'A',effect)
            self.assertEqual(['B2','B3'],[c.ref for c in targeted_library_exile_cards(h,'A',bound,['B2','B3'])])
            for refs in ([],['B2'],'B2B3'):
                with self.subTest(refs=refs),self.assertRaises(ValueError):targeted_library_exile_cards(h,'A',bound,refs)
            h.state.players['B'].zones['library']=[]
            empty=bind_targeted_library_exile(h,'A',effect)
            self.assertEqual((),targeted_library_exile_cards(h,'A',empty,[]))


from quorune.selection.searching import HiddenSearchOwnerMixin


class TargetedSearchHarness(HiddenSearchOwnerMixin):
    def __init__(self):
        from types import SimpleNamespace
        from quorune.model import StackItem
        from unittest.mock import Mock
        h,_effect=TargetLibraryIdentityTests().fixture()
        self.state=h.state;self.state.revision=0
        for card in self.state.cards.values():card.face_down=False
        self.item=StackItem(stack_id='search',ref='SEARCH',kind='spell',controller='A',label='Target search')
        self.state.stack=[self.item];self.logs=[];self.shuffles=[];self.continued=[]
        self.permissions=SimpleNamespace(issue=Mock(side_effect=self.issue))
        self.decision=None

    def issue(self,**kwargs):
        from types import SimpleNamespace
        self.decision=SimpleNamespace(**kwargs,decision_id='D1',created_revision=0,responses={})
        return self.decision

    def _require_seat(self,seat,**kwargs):
        if seat not in self.state.active_seats():raise ValueError('Inactive seat')

    def card_record(self,key):return object()
    def _land_entry_life_amount(self,record):return 0
    def _stack_source_ref(self,item):return None
    def _semantic_frame(self,item,*,instruction_pointer,locals):
        return {'instruction_pointer':instruction_pointer,'stack_object':item.ref,'locals':locals}
    def _validate_semantic_frame(self,frame,item):
        if frame['stack_object']!=item.ref:raise ValueError('Wrong frame')
    def shuffle_library(self,seat,**kwargs):self.shuffles.append(seat)
    def _log(self,*args,**kwargs):self.logs.append((args,kwargs))
    def _continue_resolution(self,**kwargs):self.continued.append(kwargs)

    def begin(self,count=3):
        effect=TargetLibraryExileSpec(count).effect(searching_player='A',library_player='B')
        self._begin_semantic_search(item=self.item,effect=effect,remaining=(),
            destination='graveyard',note='complete',instruction_pointer=0)
        return self.decision


class TargetLibraryLifecycleTests(unittest.TestCase):
    def permissions(self,constraint=None):
        from contextlib import ExitStack
        from unittest.mock import patch
        from quorune.rules.library_search_rules import LibrarySearchConstraint
        value=constraint or LibrarySearchConstraint()
        stack=ExitStack()
        stack.enter_context(patch('quorune.rules.library_search_rules.library_search_constraint',return_value=value))
        stack.enter_context(patch('quorune.selection.searching.library_search_constraint',return_value=value))
        return stack

    def test_only_searcher_gets_target_names_and_window_with_mandatory_available_cardinality(self):
        from quorune.rules.library_search_rules import LibrarySearchConstraint
        h=TargetedSearchHarness()
        with self.permissions(LibrarySearchConstraint(top_count=2)):
            decision=h.begin(3)
        self.assertEqual(['A'],decision.actors);self.assertEqual({'A'},set(decision.payload_by_actor))
        ctx=decision.payload_by_actor['A']
        self.assertEqual(['B2','B3'],[row['id'] for row in ctx['search_cards']])
        self.assertEqual(['B2','B3'],[row['id'] for row in ctx['search_window']])
        schema=ctx['legal_actions'][0]['choice_schema']
        self.assertEqual((2,2,False),(schema['minimum'],schema['maximum'],schema['rules_may_fail_to_find']))
        self.assertEqual([],h.shuffles)

    def test_completion_delegates_single_batch_to_canonical_owner_then_shuffles_library_owner(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        h=TargetedSearchHarness()
        with self.permissions():
            decision=h.begin(3);decision.responses={'A':{'search_cards':['B0','B2','B3']}}
            moved=[SimpleNamespace(object_id=key,ref=key,zone='exile',face_down=False) for key in ('B0','B2','B3')]
            with patch('quorune.zone_transitions.ZoneTransitionOwner.move_cards_simultaneously',return_value=moved) as commit:
                h._complete_semantic_search(decision)
            commit.assert_called_once_with((('B0','exile'),('B2','exile'),('B3','exile')),
                reason='Target search search',log=False,replacement_selections=())
        self.assertEqual(['B'],h.shuffles);self.assertEqual(1,len(h.continued))
        self.assertEqual(['B0','B1','B2','B3'],h.state.players['B'].zones['library'])

    def test_prohibited_search_skips_private_choice_and_still_shuffles_target_owner(self):
        from quorune.rules.library_search_rules import LibrarySearchConstraint
        h=TargetedSearchHarness()
        with self.permissions(LibrarySearchConstraint(prohibited=True)):h.begin()
        self.assertIsNone(h.decision);self.assertEqual(['B'],h.shuffles)
        self.assertEqual(1,len(h.continued))

    def test_target_concession_during_choice_finishes_without_foreign_reference_or_shuffle(self):
        from unittest.mock import patch
        h=TargetedSearchHarness()
        with self.permissions():
            decision=h.begin();decision.responses={'A':{'search_cards':['B0','B1','B2']}}
            h.state.active_seats=lambda:['A']
            with patch('quorune.zone_transitions.ZoneTransitionOwner.move_cards_simultaneously') as commit:
                h._complete_semantic_search(decision);commit.assert_not_called()
        self.assertEqual([],h.shuffles);self.assertEqual(1,len(h.continued))

    def test_old_search_versions_cannot_smuggle_target_library_roles(self):
        from quorune.errors import GameRuleError
        for version in (1,2,3,4,5,6,7,8):
            effect={**TargetLibraryExileSpec(1).effect(),'schema_version':version}
            with self.subTest(version=version),self.assertRaises(GameRuleError):
                HiddenSearchOwnerMixin._validate_counted_search_effect(effect)


    def test_replacement_resume_preserves_actor_three_card_batch_and_owner_shuffle(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        h=TargetedSearchHarness()
        with self.permissions():
            decision=h.begin(3);decision.responses={'A':{'search_cards':['B0','B1','B2']}}
            context=h._semantic_search_completion_context(decision)
            effect={**context.effect,'_search_completion':{'actor':'A','choice_id':'D1','selected_refs':['B0','B1','B2']},
                '_replacement_selections':['chosen-replacement']}
            moved=[SimpleNamespace(object_id=key,ref=key,zone='exile',face_down=False) for key in ('B0','B1','B2')]
            with patch('quorune.zone_transitions.ZoneTransitionOwner.move_cards_simultaneously',return_value=moved) as commit:
                h._resume_semantic_search(item=h.item,effect=effect,remaining=(),destination='graveyard',note='done',instruction_pointer=0)
            commit.assert_called_once_with((('B0','exile'),('B1','exile'),('B2','exile')),
                reason='Target search search',log=False,replacement_selections=['chosen-replacement'])
        self.assertEqual(['B'],h.shuffles);self.assertEqual(1,len(h.continued))

    def test_resumed_actor_or_short_mandatory_selection_reject_before_movement(self):
        from unittest.mock import patch
        from quorune.errors import GameRuleError
        for actor,refs in (('B',['B0','B1','B2']),('A',['B0','B1'])):
            h=TargetedSearchHarness()
            with self.permissions():
                decision=h.begin(3);decision.responses={'A':{'search_cards':['B0','B1','B2']}}
                context=h._semantic_search_completion_context(decision)
                effect={**context.effect,'_search_completion':{'actor':actor,'choice_id':'D1','selected_refs':refs}}
                with patch('quorune.zone_transitions.ZoneTransitionOwner.move_cards_simultaneously') as commit:
                    with self.subTest(actor=actor,refs=refs),self.assertRaises(GameRuleError):
                        h._resume_semantic_search(item=h.item,effect=effect,remaining=(),destination='graveyard',note='done',instruction_pointer=0)
                    commit.assert_not_called()
            self.assertEqual([],h.shuffles)

    def test_hidden_replacement_results_are_not_published_as_face_up_exile_identities(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        h=TargetedSearchHarness()
        with self.permissions():
            decision=h.begin(1);decision.responses={'A':{'search_cards':['B0']}}
            moved=[SimpleNamespace(object_id='B0',ref='B0',zone='hand',face_down=False)]
            with patch('quorune.zone_transitions.ZoneTransitionOwner.move_cards_simultaneously',return_value=moved):
                h._complete_semantic_search(decision)
        public=next(args[3] for args,kwargs in h.logs if args[1]=='library.search')
        self.assertEqual([],public['objects']);self.assertNotIn('object',public)
        private=next((args[3],kwargs) for args,kwargs in h.logs if args[1]=='library.search.private')
        self.assertEqual(['B0'],private[0]['objects']);self.assertEqual(['A','analyst'],private[1]['visibility'])


class TargetLibraryCompilerBoundaryTests(unittest.TestCase):
    def test_default_compiler_lowers_whole_source_span_and_requires_fine_owner(self):
        from dataclasses import replace
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        text="Search target player's library for a card and exile it. Then that player shuffles."
        record=replace(query_record(text),type_line='Sorcery',power=None,toughness=None)
        ir=compile_oracle_card(record,capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        node,=ir.faces[0].nodes
        self.assertEqual('targeted-fixed-library-exile-v1',node.template_id)
        self.assertTrue(node.lowerable);self.assertEqual(text,node.text)
        self.assertEqual(9,node.effects[0]['schema_version'])
        self.assertEqual('$target.0',node.effects[0]['library_player'])
        self.assertEqual(TargetLibraryExileSpec(1).target_schema(),node.target_schema)
        self.assertIn('library.search.targeted_fixed_exile',node.capability_dependencies)

    def test_raw_minimum_is_independent_of_annotation_and_keeps_legacy_scope_closed(self):
        from quorune.card_programs.descriptor_requirements import _owned_operation_payload_dependencies
        from quorune.rules.targeted_library_exile import TARGET_LIBRARY_EXILE_CAPABILITY
        effect=TargetLibraryExileSpec(3).effect()
        self.assertIn(TARGET_LIBRARY_EXILE_CAPABILITY,_owned_operation_payload_dependencies(effect))
        with self.assertRaises(ValueError):_owned_operation_payload_dependencies({**effect,'schema_version':8})
        with self.assertRaises(ValueError):_owned_operation_payload_dependencies({**effect,'searching_player':'$target'})

    def test_node_minimum_requires_one_complete_instruction_and_targeted_player_schema(self):
        from quorune.rules.targeted_library_exile import targeted_library_exile_node_capabilities,TARGET_LIBRARY_EXILE_CAPABILITY
        effect=TargetLibraryExileSpec(1).effect();schema=TargetLibraryExileSpec(1).target_schema()
        self.assertIn(TARGET_LIBRARY_EXILE_CAPABILITY,targeted_library_exile_node_capabilities(effects=(effect,),target_schema=schema))
        self.assertEqual((),targeted_library_exile_node_capabilities(effects=(effect,),target_schema=None))
        self.assertEqual((),targeted_library_exile_node_capabilities(effects=(effect,effect),target_schema=schema))


from inherited_composition_fixture import InheritedNativeFixture


class TargetLibraryNativeFixture(InheritedNativeFixture):
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
        build_fixture_database([path,ROOT/'tests/fixtures/targeted-library-exile-cards.json'],root/'target-exile.sqlite3')
        cls.db=CardDatabase(root/'target-exile.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Target library exile',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])


class TargetLibraryNativeTests(TargetLibraryNativeFixture,unittest.TestCase):
    def rejected(self,s,seat,response):
        from quorune.record import authoritative_state_hash
        before=authoritative_state_hash(s.state)
        self.assertFalse(s.act('pilot:'+seat,{'action_id':'choose',**response}).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))

    def options(self,s):
        self.assertEqual('semantic.search',s.state.pending_decision.kind)
        return s.packet('pilot:A',full=True)['decision']['ctx']['search_cards']

    def select(self,s,refs):
        result=s.act('pilot:A',{'action_id':'choose','search_cards':list(refs)})
        self.assertTrue(result.ok,result.summary)

    def test_three_originals_have_whole_default_admission_and_require_independent_fine_owner(self):
        from dataclasses import replace
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        cap='library.search.targeted_fixed_exile'
        for i,name in enumerate(('Extract',"Jester's Cap",'Bitter Ordeal')):
            record=self.db.lookup(name)
            ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            s=self.session(517441000+i);card=self.add(s.engine,name,zone='hand');self.assert_closed(s,card)
            programs=[p for p in s.engine.semantics.programs_for_oracle(card.oracle_id) if cap in p.capability_dependencies]
            self.assertTrue(programs,name)
            for program in programs:
                deps=tuple(d for d in program.capability_dependencies if d!=cap)
                changed=replace(program,capability_dependencies=deps,coverage=(),
                    capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
                binding=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(binding['strict'],(name,binding))

    def test_actual_paid_extract_searcher_sees_target_library_only_then_public_exile_owner_shuffle_private_load_replay(self):
        import json
        from unittest.mock import patch
        s=self.session(517441010);source=self.add(s.engine,'Extract',zone='hand')
        chosen=self.add(s.engine,'Sol Ring',seat='B',zone='library',ref='target-exile-selected')
        hidden=self.add(s.engine,'Grizzly Bears',seat='B',zone='library',ref='target-exile-unselected')
        own=self.add(s.engine,'Island',seat='A',zone='library',ref='searcher-library-secret')
        for card in (chosen,hidden,own):card.known_to=[];card.revealed_to=[]
        self.assert_closed(s,source);self.prepare(s,mana={'U':1});self.checkpoint(s)
        own_order=list(s.state.players['A'].zones['library'])
        self.cast_for(s,'A',source,['B']);self.assertEqual('semantic.search',self.resolve(s).kind)
        refs={row['id'] for row in self.options(s)}
        self.assertIn(chosen.ref,refs);self.assertIn(hidden.ref,refs);self.assertNotIn(own.ref,refs)
        for seat in 'BCD':
            packet=json.dumps(s.packet('pilot:'+seat,full=True))
            self.assertNotIn('target-exile-selected',packet);self.assertNotIn('target-exile-unselected',packet)
        self.rejected(s,'B',{'search_cards':[chosen.ref]});self.rejected(s,'A',{'search_cards':[]})
        self.rejected(s,'A',{'search_cards':[own.ref]})
        s=self.replay(s,load=True)
        with patch.object(s.engine,'shuffle_library',wraps=s.engine.shuffle_library) as shuffle:
            self.select(s,[chosen.ref]);self.assertIsNone(self.resolve(s))
            self.assertEqual(['B'],[call.args[0] for call in shuffle.call_args_list])
        self.assertEqual('exile',s.state.cards[chosen.object_id].zone)
        self.assertEqual('library',s.state.cards[hidden.object_id].zone)
        self.assertEqual(own_order,s.state.players['A'].zones['library'])
        self.assertFalse(any(s.state.players['A'].mana_pool.values()))
        for seat in 'ABCD':
            packet=json.dumps(s.packet('pilot:'+seat,full=True))
            self.assertIn('target-exile-selected',packet)
            if seat!='B':self.assertNotIn('target-exile-unselected',packet)
        self.replay(s,load=True)

    def test_actual_paid_jesters_cap_sacrifices_source_and_requires_three_simultaneous_exiles_before_owner_shuffle(self):
        from unittest.mock import patch
        from quorune.zone_transitions import ZoneTransitionOwner
        s=self.session(517441020);source=self.add(s.engine,"Jester's Cap",zone='hand')
        cards=[self.add(s.engine,name,seat='B',zone='library',ref='cap-choice-'+str(i))
            for i,name in enumerate(('Sol Ring','Grizzly Bears','Island'))]
        self.assert_closed(s,source);self.prepare(s,mana={'C':6});self.checkpoint(s)
        self.cast_for(s,'A',source,reserve={'C':2});self.assertIsNone(self.resolve(s))
        self.priority(s,'A')
        rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        row,=[r for r in rows if r['id'].startswith('activate:'+source.ref+':')]
        result=s.act('pilot:A',{'action_id':row['id'],'targets':['B'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertFalse(any(s.state.players['A'].mana_pool.values()))
        self.assertEqual('semantic.search',self.resolve(s).kind)
        self.rejected(s,'A',{'search_cards':[cards[0].ref,cards[1].ref]})
        s=self.replay(s,load=True);batches=[];original=ZoneTransitionOwner.move_cards_simultaneously
        def capture(owner,moves,**kwargs):
            rows=tuple(moves);batches.append(rows);return original(owner,rows,**kwargs)
        with patch.object(ZoneTransitionOwner,'move_cards_simultaneously',capture),patch.object(s.engine,'shuffle_library',wraps=s.engine.shuffle_library) as shuffle:
            self.select(s,[c.ref for c in cards]);self.assertIsNone(self.resolve(s))
            self.assertEqual(['B'],[call.args[0] for call in shuffle.call_args_list])
        expected={(c.object_id,'exile') for c in cards}
        self.assertEqual(1,sum(set(rows)==expected for rows in batches))
        self.assertTrue(all(s.state.cards[c.object_id].zone=='exile' for c in cards))
        self.replay(s,load=True)

    def test_actual_paid_bitter_ordeal_uses_gravestorm_copy_and_original_independent_target_searches_with_replay(self):
        s=self.session(517441030);source=self.add(s.engine,'Bitter Ordeal',zone='hand')
        wrath=self.add(s.engine,'Wrath of God',zone='hand');bear=self.add(s.engine,'Grizzly Bears',seat='D')
        chosen={seat:self.add(s.engine,'Sol Ring',seat=seat,zone='library',ref='ordeal-'+seat) for seat in 'BC'}
        for card in (source,wrath):self.assert_closed(s,card)
        self.prepare(s,mana={'C':4,'W':2,'B':1});self.checkpoint(s)
        self.cast_for(s,'A',wrath,reserve={'C':2,'B':1});self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[bear.object_id].zone)
        self.cast_for(s,'A',source,['B']);self.assertEqual(1,self.trigger_count(s,source))
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        result=s.act('pilot:A',{'action_id':'choose','targets':['C']});self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.search',self.resolve(s).kind)
        self.assertIn(chosen['C'].ref,{r['id'] for r in self.options(s)})
        self.select(s,[chosen['C'].ref]);self.assertEqual('semantic.search',self.resolve(s).kind)
        self.assertIn(chosen['B'].ref,{r['id'] for r in self.options(s)})
        s=self.replay(s,load=True);self.select(s,[chosen['B'].ref]);self.assertIsNone(self.resolve(s))
        self.assertTrue(all(s.state.cards[c.object_id].zone=='exile' for c in chosen.values()))
        self.replay(s,load=True)


class TargetLibraryInteractionTests(TargetLibraryNativeFixture,unittest.TestCase):
    options=TargetLibraryNativeTests.options
    rejected=TargetLibraryNativeTests.rejected
    select=TargetLibraryNativeTests.select

    def restricted_window(self,seed,*,provider='B'):
        s=self.session(seed);source=self.add(s.engine,'Extract',zone='hand')
        censor=self.add(s.engine,'Aven Mindcensor',seat=provider,zone='hand' if provider=='B' else 'battlefield')
        cards=[self.add(s.engine,name,seat='B',zone='library',ref='restricted-target-'+str(i))
            for i,name in enumerate(('Sol Ring','Grizzly Bears','Island','Wastes','Island'))]
        for card in cards:card.known_to=[];card.revealed_to=[]
        for card in (source,censor):self.assert_closed(s,card)
        s.state.players['B'].mana_pool.update({'C':2,'W':1})
        self.prepare(s,mana={'U':1});self.checkpoint(s);self.cast_for(s,'A',source,['B'])
        if provider=='B':self.cast_for(s,'B',censor);self.resolve_top(s)
        self.assertEqual('semantic.search',self.resolve(s).kind)
        expected={c.ref for c in cards[-4:]} if provider=='B' else {s.state.cards[oid].ref for oid in s.state.players['B'].zones['library']}
        self.assertEqual(expected,{row['id'] for row in self.options(s)})
        return s,cards

    def test_actual_flashed_mindcensor_applies_searcher_permissions_to_target_library_and_own_provider_does_not_restrict(self):
        import json
        for provider in 'BA':
            with self.subTest(provider=provider):
                s,cards=self.restricted_window(517441100+ord(provider),provider=provider)
                if provider=='B':self.rejected(s,'A',{'search_cards':[cards[0].ref]})
                for viewer in 'BCD':
                    packet=json.dumps(s.packet('pilot:'+viewer,full=True))
                    self.assertTrue(all(card.ref not in packet for card in cards))
                s=self.replay(s,load=True);self.select(s,[cards[-1].ref]);self.assertIsNone(self.resolve(s))
                self.assertEqual('exile',s.state.cards[cards[-1].object_id].zone);self.replay(s,load=True)

    def test_fresh_native_owner_permission_mutant_is_killed_after_positive_paid_extract(self):
        from unittest.mock import patch
        from quorune.rules import library_search_rules as owner
        self.restricted_window(517441180)
        original=owner.library_search_object_ids
        def wrong(host,player,zone,**kwargs):return original(host,player,zone)
        with patch.object(owner,'library_search_object_ids',wrong),self.assertRaises(AssertionError):
            self.restricted_window(517441180)

    def test_actual_mindlock_prohibition_issues_no_search_choice_and_still_shuffles_only_target_library_with_replay(self):
        from unittest.mock import patch
        s=self.session(517441200);source=self.add(s.engine,'Extract',zone='hand')
        orb=self.add(s.engine,'Mindlock Orb');self.assert_closed(s,orb);self.assert_closed(s,source)
        self.prepare(s,mana={'U':1});self.checkpoint(s);own=list(s.state.players['A'].zones['library']);target=list(s.state.players['B'].zones['library'])
        self.cast_for(s,'A',source,['B'])
        with patch.object(s.engine,'shuffle_library',wraps=s.engine.shuffle_library) as shuffle:
            self.assertIsNone(self.resolve(s));self.assertEqual(['B'],[call.args[0] for call in shuffle.call_args_list])
        self.assertEqual(own,s.state.players['A'].zones['library']);self.assertEqual(set(target),set(s.state.players['B'].zones['library']))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.replay(s,load=True)

    def test_actual_cap_short_and_empty_library_use_all_available_cards_without_optional_failure_and_shuffle_owner(self):
        from unittest.mock import patch
        for count in (0,2):
            with self.subTest(count=count):
                s=self.session(517441210+count);source=self.add(s.engine,"Jester's Cap",zone='hand')
                for oid in tuple(s.state.players['B'].zones['library']):s.engine.move_card(oid,'exile',reason='Short library fixture',log=False)
                cards=[self.add(s.engine,'Island',seat='B',zone='library',ref='short-cap-'+str(i)) for i in range(count)]
                self.prepare(s,mana={'C':6});self.checkpoint(s);self.cast_for(s,'A',source,reserve={'C':2});self.assertIsNone(self.resolve(s))
                self.priority(s,'A');rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                row,=[r for r in rows if r['id'].startswith('activate:'+source.ref+':')]
                result=s.act('pilot:A',{'action_id':row['id'],'targets':['B'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
                with patch.object(s.engine,'shuffle_library',wraps=s.engine.shuffle_library) as shuffle:
                    decision=self.resolve(s)
                    if count:
                        self.assertEqual('semantic.search',decision.kind)
                        self.rejected(s,'A',{'search_cards':[cards[0].ref]})
                        s=self.replay(s,load=True)
                    else:
                        if decision is not None:
                            self.assertEqual('semantic.search',decision.kind);self.assertEqual([],self.options(s));self.select(s,[]);self.assertIsNone(self.resolve(s))
                    if not count:self.assertEqual(['B'],[call.args[0] for call in shuffle.call_args_list])
                if count:
                    with patch.object(s.engine,'shuffle_library',wraps=s.engine.shuffle_library) as shuffle:
                        self.select(s,[c.ref for c in cards]);self.assertIsNone(self.resolve(s))
                        self.assertEqual(['B'],[call.args[0] for call in shuffle.call_args_list])
                self.assertFalse(s.state.players['B'].zones['library'])
                self.assertTrue(all(s.state.cards[c.object_id].zone=='exile' for c in cards));self.replay(s,load=True)

    def test_actual_post_shuffle_fault_restores_full_choice_movement_knowledge_rng_then_loads_and_retries_once(self):
        from unittest.mock import patch
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s=self.session(517441230);source=self.add(s.engine,'Extract',zone='hand')
        chosen=self.add(s.engine,'Sol Ring',seat='B',zone='library',ref='rollback-search-card')
        chosen.known_to=[];chosen.revealed_to=[]
        self.prepare(s,mana={'U':1});self.checkpoint(s);self.cast_for(s,'A',source,['B']);self.assertEqual('semantic.search',self.resolve(s).kind)
        before=authoritative_state_hash(s.state);original=s.engine.shuffle_library;seen=[]
        def fault(*args,**kwargs):
            original(*args,**kwargs);seen.append(args[0]);raise GameRuleError('Injected post-shuffle targeted exile fault')
        with patch.object(s.engine,'shuffle_library',side_effect=fault):
            result=s.act('pilot:A',{'action_id':'choose','search_cards':[chosen.ref]})
        self.assertFalse(result.ok);self.assertIn('Injected post-shuffle',result.summary);self.assertEqual(['B'],seen)
        self.assertEqual(before,authoritative_state_hash(s.state));self.assertEqual('library',s.state.cards[chosen.object_id].zone)
        s=self.replay(s,load=True);self.select(s,[chosen.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('exile',s.state.cards[chosen.object_id].zone);self.replay(s,load=True)

    def test_canonical_target_departure_during_actual_private_choice_skips_foreign_movement_and_shuffle_with_exact_replay(self):
        from unittest.mock import patch
        s=self.session(517441240);source=self.add(s.engine,'Extract',zone='hand')
        chosen=self.add(s.engine,'Sol Ring',seat='B',zone='library',ref='departed-search-card')
        self.prepare(s,mana={'U':1});self.checkpoint(s);self.cast_for(s,'A',source,['B']);self.assertEqual('semantic.search',self.resolve(s).kind)
        with patch.object(s.engine,'shuffle_library',wraps=s.engine.shuffle_library) as shuffle:
            s.engine._eliminate_players(['B'],reason='conceded')
            self.checkpoint(s)  # Canonical owner interaction during another actor's private choice.
            if s.state.pending_decision is not None and s.state.pending_decision.kind=='semantic.search':self.select(s,[chosen.ref])
            self.assertIsNone(self.resolve(s));self.assertEqual([],shuffle.call_args_list)
        self.assertNotIn('B',s.state.active_seats());self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.replay(s,load=True)

    def test_actual_countered_extract_and_departed_target_preserve_paid_price_without_search_or_shuffle(self):
        from unittest.mock import patch
        for mode in ('counter','depart'):
            with self.subTest(mode=mode):
                s=self.session(517441250+len(mode));source=self.add(s.engine,'Extract',zone='hand')
                counter=self.add(s.engine,'Counterspell',seat='B',zone='hand');s.state.players['B'].mana_pool.update({'U':2})
                self.prepare(s,mana={'U':1});self.checkpoint(s);self.cast_for(s,'A',source,['B'])
                if mode=='counter':self.cast_for(s,'B',counter,[s.state.stack[-1].ref])
                else:
                    self.priority(s,'B');result=s.act('pilot:B',{'action_id':'concede','choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
                with patch.object(s.engine,'shuffle_library',wraps=s.engine.shuffle_library) as shuffle:
                    self.assertIsNone(self.resolve(s));self.assertEqual([],shuffle.call_args_list)
                self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                self.assertFalse(any(s.state.players['A'].mana_pool.values()));self.replay(s,load=True)


class TargetLibraryWithdrawalTests(TargetLibraryNativeFixture,unittest.TestCase):
    def test_genuine_verified_five_owner_withdrawals_block_all_three_originals_and_preserve_unrelated_controls(self):
        import json,tempfile
        from pathlib import Path
        from copy import deepcopy
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        from scripts.build_test_database import build_fixture_database
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        doc=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known=set(discover_tests(ROOT).values())
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'old-search.sqlite3'
            build_fixture_database([ROOT/'tests/fixtures/library-search-rules-cards.json'],path)
            with CardDatabase(path) as db:old_search=db.lookup('Demonic Tutor')
        for cap in ('library.search.targeted_fixed_exile','library.search.fixed_counted',
                'library.search.restriction.static','zone.change.destination_replacement','target.revalidate_resolution'):
            value=deepcopy(raw);next(r for r in value['capabilities'] if r['id']==cap).update(
                status='blocked',blockers=['Target-library fine or shared owner genuinely withdrawn'])
            registry=CapabilityRegistry(value)
            index=build_index(registry_value=value,declaration_source=doc,discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertTrue(registry.evidence_fingerprint)
            for name in ('Extract',"Jester's Cap",'Bitter Ordeal'):
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
                self.assertNotEqual('exact',ir.status,(cap,name))
                blockers={b for residual in ir.material_residuals for b in residual.blockers}
                self.assertIn('capability:status:'+cap+':blocked',blockers)
                self.assertFalse(any('unverified' in b for b in blockers))
            for name in ('Grizzly Bears','Sol Ring'):
                self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=registry,
                    capability_profile='commander_review').status,(cap,name))
            if cap=='library.search.targeted_fixed_exile':
                self.assertEqual('exact',compile_oracle_card(old_search,capability_registry=registry,
                    capability_profile='commander_review').status,(cap,'existing self-library search'))


class HiddenLibraryExplanationTests(unittest.TestCase):
    def host(self,*,known=False):
        from types import SimpleNamespace
        from quorune.model import PlayerState
        card=SimpleNamespace(object_id='hidden-top',ref='hidden-library-land',zone='library',owner='B',
            known_to=['B'] if known else [],revealed_to=[])
        player = PlayerState(seat="B", name="B")
        player.zones["library"].append(card.object_id)
        host=SimpleNamespace(state=SimpleNamespace(players={'B':player},cards={card.object_id:card},active_player='A'),
            card_record=lambda card:card,_land_play_faces=lambda record:['land'],
            _compiled_land_play_permission=lambda seat,card:False)
        return host,card

    def test_unknown_top_land_has_no_explanation_or_identity_without_current_visibility_grant(self):
        from unittest.mock import patch
        from quorune.rules.action_explanations import projected_action_explanations
        host,card=self.host()
        with patch('quorune.semantic_runtime.action_permissions.library_top_visibility',return_value=None):
            self.assertEqual({},projected_action_explanations(host,'B'))
        self.assertEqual([],card.known_to)

    def test_known_top_and_current_library_visibility_keep_explanations_without_granting_an_action(self):
        from unittest.mock import patch
        from quorune.rules.action_explanations import projected_action_explanations
        for known,permission in ((True,None),(False,{'scope':'top'})):
            with self.subTest(known=known,permission=permission):
                host,card=self.host(known=known)
                with patch('quorune.semantic_runtime.action_permissions.library_top_visibility',return_value=permission):
                    row=projected_action_explanations(host,'B')[card.ref]
                self.assertEqual('unavailable',row['status'])
                self.assertEqual('unsupported_face_or_zone_permission',row['reason'])
