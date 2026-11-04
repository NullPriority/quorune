"""Optional graveyard returns choose current owned cards after mandatory mill."""
import ast
from copy import deepcopy
from dataclasses import replace
import inspect
import json
from pathlib import Path
import tempfile
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.object_query import ObjectQueryResult
from quorune.replacement.immutable import FrozenMap
from quorune.rules.graveyard_return_choices import (
    CAPABILITY, MECHANIC, OPERATION, GraveyardReturnSelection, graveyard_return_candidates,
    graveyard_return_choice_node_capabilities,
)
from quorune.semantic_choices.context import SemanticChoiceContext, SnapshotSemanticChoiceQuery
from quorune.semantic_choices.graveyard_return import GraveyardReturnChoiceHandler
from quorune.semantic_choices.model import SemanticChoiceError


def row(ref, *, owner='A', controller=None, zone='graveyard', types=('creature',), incarnation=1, token=False):
    return ObjectQueryResult(object_id='object:'+ref,ref=ref,printed_name=ref,owner=owner,
        controller=controller or owner,zone=zone,types=types,token=token,
        logical_object_id='object:'+ref+'@'+str(incarnation))


def query(rows, refs):
    return SnapshotSemanticChoiceQuery(seat_order=tuple('ABCD'),active_order=tuple('ABCD'),
        object_rows=tuple(rows),materialized_choice_candidates=tuple(refs))


def context(rows, refs, *, actor='A'):
    return SemanticChoiceContext(actor=actor,stack_ref='S1',stack_controller=actor,
        stack_label='Graveyard return witness',source_ref=None,card_ref=None,
        semantic_program_id='fixture:graveyard-choice',semantic_program_version=1,query=query(rows,refs))


def instruction(types=('creature',), player='A'):
    return {'op':OPERATION,'player':player,'selection':GraveyardReturnSelection(types).to_dict()}


class GraveyardReturnChoiceTests(unittest.TestCase):
    def test_owned_card_materialization_uses_type_union_once_and_excludes_other_zones_and_noncards(self):
        rows=(row('old'),row('milled'),row('dual',types=('creature','land')),row('land',types=('land',)),
            row('artifact',types=('artifact',)),row('foreign',owner='B',controller='A'),
            row('hand',zone='hand'),row('exiled',zone='exile'),row('token',token=True),row('spell-copy'))
        cards={r.object_id:SimpleNamespace(is_card_object=not r.token and r.ref!='spell-copy') for r in rows}
        host=SimpleNamespace(state=SimpleNamespace(cards=cards),public_zone_move_object_rows=lambda actor:rows,
            public_zone_move_active_seats=lambda:tuple('ABCD'),public_zone_move_apnap_order=lambda:tuple('CDAB'))
        actual=graveyard_return_candidates(host,'A',instruction(('creature','land')))
        self.assertEqual({'old','milled','dual','land'},set(actual))
        self.assertEqual(1,actual.count('dual'))
        permanent=GraveyardReturnSelection(('artifact','battle','creature','enchantment','land','planeswalker'))
        self.assertIn('battle',permanent.query.types_any)
        self.assertIn('planeswalker',permanent.query.types_any)
        self.assertEqual('owner',permanent.public_set().relation_axis.value)

    def test_optional_prepare_seals_incarnation_and_returns_through_existing_owner(self):
        target=row('old');ctx=context((target,),('old',));handler=GraveyardReturnChoiceHandler()
        prepared=handler.prepare(instruction(),ctx)
        self.assertEqual((0,1,True),(prepared.request.choice.minimum,prepared.request.choice.maximum,prepared.request.choice.optional))
        result=handler.complete(SimpleNamespace(effect=prepared.continuation_effect),{'card':'old'},ctx.query)
        from quorune.semantic_runtime.intents import ReturnGraveyardCardToOwnerHandIntent
        self.assertIsInstance(result.intents[0],ReturnGraveyardCardToOwnerHandIntent)
        self.assertEqual(('A','old'),(result.intents[0].actor,result.intents[0].object_ref))
        self.assertEqual({'ref':'old','object_id':'object:old','zone_change_counter':1},dict(result.intents[0].target_snapshot))
        for response in ({},{'card':None},{'card':''}):
            self.assertFalse(handler.complete(SimpleNamespace(effect=prepared.continuation_effect),response,ctx.query).intents)

    def test_empty_set_auto_continues_and_malformed_instruction_or_identity_rejects(self):
        handler=GraveyardReturnChoiceHandler()
        prepared=handler.prepare(instruction(),context((),()))
        self.assertIsNone(prepared.request)
        self.assertIsNotNone(prepared.auto_continue)
        for change in ({'player':'B'},{'targets':['old']},{'selection':{'schema_version':True,'types_any':['creature']}},
            {'selection':{'schema_version':1,'types_any':'creature'}},
            {'selection':{'schema_version':1,'types_any':['creature','land','instant']}}):
            with self.subTest(change=change),self.assertRaises(SemanticChoiceError):
                handler.prepare({**instruction(),**change},context((),()))
        for identity in ('opaque','object:old@01','object:old@-1',True):
            with self.assertRaises(SemanticChoiceError):
                handler.prepare(instruction(),context((replace(row('old'),logical_object_id=identity),),('old',)))

    def test_stale_owner_zone_type_incarnation_and_withdrawn_candidates_reject(self):
        handler=GraveyardReturnChoiceHandler();original=row('old');ctx=context((original,),('old',))
        saved=SimpleNamespace(effect=handler.prepare(instruction(),ctx).continuation_effect)
        for changed in (replace(original,logical_object_id='object:old@2'),replace(original,owner='B'),
            replace(original,zone='exile'),replace(original,types=('instant',)),replace(original,token=True)):
            with self.subTest(changed=changed),self.assertRaises(SemanticChoiceError):
                handler.complete(saved,{'card':'old'},query((changed,),('old',)))
        for response in ({'card':'foreign'},{'card':True},{'card':['old']}):
            with self.assertRaises(SemanticChoiceError):handler.complete(saved,response,ctx.query)
        with self.assertRaises(SemanticChoiceError):handler.complete(saved,{'card':'old'},query((original,),()))
        for change in ({'_choice_actor':'B'},{'_legal_logical_ids':{}},{'_legal_refs':('old','old')},{'_stack_label':False}):
            with self.assertRaises(SemanticChoiceError):handler.complete(
                SimpleNamespace(effect=FrozenMap({**dict(saved.effect),**change})),{'card':None},ctx.query)

    def test_compiler_orders_mill_then_untargeted_choice_and_withdrawal_preserves_plain_mill(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from test_granted_skulk import record
        registry=load_default_capability_registry()
        for subject in ('creature','land','creature or land','permanent'):
            text='Mill three cards, then you may return a '+subject+' card from your graveyard to your hand.'
            ir=compile_oracle_card(record(text,'Instant'),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0]
            self.assertIsNone(node.target_schema)
            self.assertEqual(('mill',OPERATION),tuple(effect['op'] for effect in node.effects))
            self.assertEqual('$controller',node.effects[-1]['player'])
            self.assertIn(CAPABILITY,node.capability_dependencies)
        from quorune.compiler.graveyard_return_choice_templates import graveyard_return_choice_template
        for text in ('You may return up to one target creature card from your graveyard to your hand.',
            'You may return a creature card milled this way from your graveyard to your hand.',
            'You may return two creature cards from your graveyard to your hand.',
            'You may return a creature card from a graveyard to your hand.'):
            self.assertIsNone(graveyard_return_choice_template(text))
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text())
        next(r for r in raw['capabilities'] if r['id']==CAPABILITY).update(status='blocked',blockers=['choice owner withdrawn'])
        blocked=CapabilityRegistry(raw)
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        index=build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        blocked.mark_evidence_verified(validate_capability_evidence_index(index,registry=blocked))
        self.assertNotEqual('exact',compile_oracle_card(record('You may return a creature card from your graveyard to your hand.','Instant'),
            capability_registry=blocked,capability_profile='commander_review').status)
        self.assertEqual('exact',compile_oracle_card(record('Mill three cards.','Instant'),
            capability_registry=blocked,capability_profile='commander_review').status)

    def test_runtime_infers_and_validates_choice_independently(self):
        from quorune.card_programs.runtime_requirements import program_dynamic_runtime_requirements
        from quorune.semantics import SemanticProgram
        effect=instruction(player='$controller')
        program=SemanticProgram(key='fixture:graveyard-choice',label='Graveyard choice',effects=[effect],coverage=[MECHANIC])
        required,blockers=program_dynamic_runtime_requirements(program)
        self.assertIn(CAPABILITY,required)
        self.assertFalse(blockers)
        self.assertFalse(graveyard_return_choice_node_capabilities(effects=(effect,),
            target_schema={'zones':['graveyard']},mechanic_ids=(MECHANIC,)))
        program.effects=[{**effect,'_runtime_source':{}}]
        _required,blockers=program_dynamic_runtime_requirements(program)
        self.assertTrue(any('graveyard_return_choice' in blocker for blocker in blockers))

    def test_optional_and_incarnation_guard_implementation_mutants_are_killed(self):
        import quorune.semantic_choices.graveyard_return as implementation
        real_choice=implementation.ObjectChoice
        with patch.object(implementation,'ObjectChoice',side_effect=lambda **kwargs:
                real_choice(**{**kwargs,'minimum':1,'optional':False})):
            with self.assertRaises(AssertionError):self.test_optional_prepare_seals_incarnation_and_returns_through_existing_owner()
        tree=ast.parse(textwrap.dedent(inspect.getsource(GraveyardReturnChoiceHandler.complete)))
        class RemoveIncarnationGuard(ast.NodeTransformer):
            def visit_Compare(self,node):
                if ast.unparse(node)=='row.logical_object_id != identities[selected]':return ast.copy_location(ast.Constant(False),node)
                return self.generic_visit(node)
        tree=ast.fix_missing_locations(RemoveIncarnationGuard().visit(tree));namespace=dict(vars(implementation))
        exec(compile(tree,'<graveyard-incarnation-mutant>','exec'),namespace)
        with patch.object(GraveyardReturnChoiceHandler,'complete',namespace['complete']):
            original=row('old');ctx=context((original,),('old',));handler=GraveyardReturnChoiceHandler()
            saved=SimpleNamespace(effect=handler.prepare(instruction(),ctx).continuation_effect)
            with self.assertRaises(AssertionError):
                with self.assertRaises(SemanticChoiceError):
                    handler.complete(saved,{'card':'old'},query((replace(original,logical_object_id='object:old@2'),),('old',)))


class GraveyardReturnChoiceNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'graveyard-salvage.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/source-explore-cards.json',ROOT/'tests/fixtures/graveyard-salvage-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Graveyard choice',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    from test_crime import CrimeNativeTests as _crime
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _actions
    from casualty_fixture import CasualtyFixture as _casualty
    from test_explore_event_triggers import ExploreEventNativeTests as _explore_actions
    session=_crime.session
    start=_crime.start
    cast=_crime.cast
    priority=_crime.priority_to
    assert_closed=_crime.assert_closed
    add=_actions.add
    checkpoint=_actions.checkpoint
    resolve=_actions.resolve
    replay=_actions.replay
    casualty_action=_casualty.casualty_action
    cast_casualty=_casualty.cast_casualty
    resolve_top=_explore_actions.resolve_top

    def library(self,session,names,seat='A'):
        for oid in list(session.state.players[seat].zones['library']):
            session.engine.move_card(oid,'exile',reason='Prepared salvage library')
        return [self.add(session.engine,name,seat=seat,zone='library',ref='SALVAGE_'+seat+'_TOP_'+str(i))
            for i,name in enumerate(names)]

    def options(self,session):
        self.assertEqual('semantic.choice',session.state.pending_decision.kind)
        ctx=session.packet('pilot:A',full=True)['decision']['ctx']
        self.assertEqual(OPERATION,ctx['operation'])
        return {row['id'] for row in ctx['objects']}

    def choose(self,session,card=None,invalid=()):
        from quorune.record import authoritative_state_hash
        command={'action_id':'choose','card':card.ref if card is not None else None}
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:B',command).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        for ref in invalid:
            self.assertFalse(session.act('pilot:A',{'action_id':'choose','card':ref}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
        session=self.replay(session,load=True)
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        return session

    def test_actual_short_mill_can_return_preexisting_union_card_without_target_or_crime(self):
        session=self.session(646001)
        spell=self.add(session.engine,'Grapple with the Past',zone='hand',ref='SALVAGE_GRAPPLE')
        old=self.add(session.engine,'Dryad Arbor',zone='graveyard',ref='SALVAGE_OLD_DUAL')
        artifact=self.add(session.engine,'Sol Ring',zone='graveyard',ref='SALVAGE_WRONG_TYPE')
        foreign=self.add(session.engine,'Grizzly Bears',seat='B',zone='graveyard',ref='SALVAGE_FOREIGN')
        top,=self.library(session,['Forest'])
        for card in (spell,old,foreign):self.assert_closed(session,card)
        self.start(session);self.cast(session,spell)
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertEqual({old.ref,top.ref},self.options(session))
        self.assertEqual(0,len(session.state.players['A'].zones['library']))
        self.assertEqual('graveyard',session.state.cards[top.object_id].zone)
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        before=authoritative_state_hash(session.state);move=session.engine.move_card
        def fail_after_move(object_id,destination,**kwargs):
            result=move(object_id,destination,**kwargs)
            if object_id==old.object_id and destination=='hand':raise GameRuleError('injected after salvage movement')
            return result
        with patch.object(session.engine,'move_card',side_effect=fail_after_move):
            failed=session.act('pilot:A',{'action_id':'choose','card':old.ref})
        self.assertFalse(failed.ok);self.assertIn('injected after salvage movement',failed.summary)
        self.assertEqual(before,authoritative_state_hash(session.state))
        session=self.choose(session,old,(foreign.ref,artifact.ref))
        self.resolve(session)
        self.assertEqual('hand',session.state.cards[old.object_id].zone)
        self.assertEqual('graveyard',session.state.cards[top.object_id].zone)
        from quorune.rules.crime import crime_count_this_turn
        self.assertEqual(0,crime_count_this_turn(session.state,'A'))
        for viewer in 'ABC':self.assertTrue('CRIME_PRIVATE_D_HAND' not in json.dumps(session.packet('pilot:'+viewer,full=True)),viewer)
        self.replay(session,load=True)

    def test_actual_decline_and_empty_creature_set_keep_milling_and_permanent_scope(self):
        session=self.session(646002)
        source=self.add(session.engine,'Acolyte of Affliction',zone='hand',ref='SALVAGE_ACOLYTE')
        later=self.add(session.engine,'Corpse Churn',zone='hand',ref='SALVAGE_EMPTY_CHURN')
        artifact=self.add(session.engine,'Sol Ring',zone='graveyard',ref='SALVAGE_PERMANENT_ARTIFACT')
        land=self.add(session.engine,'Forest',zone='graveyard',ref='SALVAGE_PERMANENT_LAND')
        theft=self.add(session.engine,'Act of Treason',seat='B',zone='hand',ref='SALVAGE_SOURCE_CONTROL')
        self.add(session.engine,'Vedalken Orrery',seat='B',ref='SALVAGE_B_FLASH')
        milled=self.library(session,['Divination','Divination'])
        for card in (source,later,theft):self.assert_closed(session,card)
        self.start(session);session.state.players['B'].mana_pool.update(C=4,R=3);self.checkpoint(session)
        other_library=len(session.state.players['B'].zones['library'])
        self.cast(session,source);self.resolve_top(session)
        self.priority(session,'B');self.cast(session,theft,(source.ref,),actor='B');self.resolve_top(session)
        self.assertEqual('B',session.state.cards[source.object_id].controller)
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertEqual(['A'],session.state.pending_decision.actors)
        self.assertEqual(other_library,len(session.state.players['B'].zones['library']))
        self.assertEqual({artifact.ref,land.ref},self.options(session))
        session=self.choose(session,None,tuple(card.ref for card in milled));self.resolve(session)
        self.assertTrue(all(session.state.cards[card.object_id].zone=='graveyard' for card in milled))
        self.assertEqual('battlefield',session.state.cards[source.object_id].zone)
        self.cast(session,later);self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard',session.state.cards[later.object_id].zone)
        self.assertEqual(0,len(session.state.players['A'].zones['library']))
        self.assertEqual('graveyard',session.state.cards[artifact.object_id].zone)
        self.replay(session,load=True)

    def test_actual_casualty_copy_chooses_again_after_each_mill_and_respects_cost_owner(self):
        session=self.session(646003)
        spell=self.add(session.engine,'Dig Up the Body',zone='hand',ref='SALVAGE_CASUALTY')
        cost=self.add(session.engine,'Grizzly Bears',ref='SALVAGE_BORROWED_COST');cost.owner='B'
        cards=self.library(session,['Sol Ring','Grizzly Bears','Forest','Ornithopter'])
        self.assert_closed(session,spell);self.assert_closed(session,cost)
        self.start(session);self.cast_casualty(session,spell,cost)
        self.assertEqual('graveyard',session.state.cards[cost.object_id].zone)
        self.assertIn(cost.object_id,session.state.players['B'].zones['graveyard'])
        self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertEqual({cards[3].ref},self.options(session))
        session=self.choose(session,cards[3],(cost.ref,));self.assertEqual('semantic.choice',self.resolve(session).kind)
        self.assertEqual({cards[1].ref},self.options(session))
        session=self.choose(session,cards[1],(cost.ref,cards[3].ref));self.resolve(session)
        self.assertEqual(('hand','hand'),tuple(session.state.cards[cards[i].object_id].zone for i in (1,3)))
        self.assertEqual('graveyard',session.state.cards[spell.object_id].zone)
        self.assertEqual(0,len(session.state.players['A'].zones['library']))
        from quorune.rules.crime import crime_count_this_turn
        self.assertEqual(0,crime_count_this_turn(session.state,'A'))
        self.replay(session,load=True)
