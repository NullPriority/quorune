"""Pinned CR614.6/701.24: draw from committed hand-to-library arrivals."""
from dataclasses import asdict, FrozenInstanceError, replace
import json
import unittest
from quorune.discard_result_model import IntentResultPlayerDraws, PlayerDiscardCounts
from quorune.hand_library_redraw_model import (
    HandLibraryRedrawSpec as Spec, HandLibraryPlacement as Placement,
    HandLibrarySelection as Selection, HandLibraryScope as Scope,
    HandLibraryRedrawIntent, HandLibraryResultDraws, PlayerLibraryReturnCounts,
    HandLibraryArrivalOrder, decode_hand_library_redraw, committed_library_return_counts,
)
from quorune.compiler.hand_library_redraw_templates import hand_library_redraw_template
from quorune.library_order_model import LibraryArrivalOrder
from quorune.replacement.immutable import FrozenMap


def member(ref):
    return {'object_id':'object:'+ref,'logical_object_id':'logical:'+ref,'ref':ref}


class HandLibraryRedrawModelTests(unittest.TestCase):
    def test_six_independently_bounded_specs_have_closed_immutable_round_trip(self):
        cases=((Placement.SHUFFLE,Selection.ALL,Scope.CONTROLLER,0),
               (Placement.SHUFFLE,Selection.ALL,Scope.ALL_PLAYERS,0),
               (Placement.SHUFFLE,Selection.ANY,Scope.CONTROLLER,0),
               (Placement.BOTTOM,Selection.ALL,Scope.CONTROLLER,0),
               (Placement.BOTTOM,Selection.ALL,Scope.EVENT_PLAYER,0),
               (Placement.BOTTOM,Selection.ANY,Scope.CONTROLLER,1))
        for args in cases:
            spec=Spec(*args)
            self.assertEqual(spec,Spec.from_dict(json.loads(json.dumps(spec.to_dict()))))
            self.assertEqual(spec,decode_hand_library_redraw(spec.effect()))
            with self.assertRaises(FrozenInstanceError):spec.draw_offset=2
    def test_unknown_versions_combinations_offsets_and_recipient_forgery_fail_closed(self):
        spec=Spec(Placement.SHUFFLE,Selection.ALL,Scope.CONTROLLER)
        raw=spec.to_dict()
        for change in ({'schema_version':True},{'schema_version':2},{'draw_offset':True},
                       {'draw_offset':2},{'placement':'top'},{'selection':'up_to_two'},{'scope':'opponents'},{'extra':1}):
            with self.assertRaises((TypeError,ValueError)):Spec.from_dict({**raw,**change})
        for args in ((Placement.BOTTOM,Selection.ALL,Scope.ALL_PLAYERS,0),
                     (Placement.SHUFFLE,Selection.ANY,Scope.EVENT_PLAYER,0),
                     (Placement.SHUFFLE,Selection.ANY,Scope.CONTROLLER,1)):
            with self.assertRaises(ValueError):Spec(*args)
        with self.assertRaises(ValueError):decode_hand_library_redraw({**spec.effect(),'player':'$target.0'})
        with self.assertRaises(ValueError):decode_hand_library_redraw({**spec.effect(),'actor':'A','player':'B'},actor='A',player='B')
    def intent(self,*,placement=Placement.SHUFFLE,selection=Selection.ALL,scope=Scope.CONTROLLER,
               offset=0,players=('A',),hands=None,selected=None):
        hands={'A':[member('commander'),member('ordinary')]} if hands is None else hands
        selected={'A':['commander','ordinary']} if selected is None else selected
        return HandLibraryRedrawIntent('A',players,Spec(placement,selection,scope,offset),FrozenMap(hands),FrozenMap(selected),'test')
    def test_only_actual_arrivals_count_when_commander_library_move_is_replaced(self):
        intent=self.intent()
        result=committed_library_return_counts(intent,{'A':['previous','object:ordinary']})
        self.assertEqual((('A',1),),result.counts)
        self.assertEqual(1,HandLibraryResultDraws(0).resolved_effects(result)[0]['count'])
        self.assertEqual((('A',2),),committed_library_return_counts(intent,{'A':['object:ordinary','object:commander']}).counts)
        self.assertEqual((('A',0),),committed_library_return_counts(intent,{'A':[]}).counts)
    def test_selected_bottom_offset_counts_successes_and_keeps_private_permutation(self):
        intent=self.intent(placement=Placement.BOTTOM,selection=Selection.ANY,offset=1,selected={'A':['ordinary','commander']})
        self.assertEqual(('ordinary','commander'),tuple(intent.selected['A']))
        restored=HandLibraryRedrawIntent.from_identity(json.loads(json.dumps(intent.identity())))
        self.assertEqual(intent,restored)
        result=committed_library_return_counts(intent,{'A':['object:ordinary']})
        self.assertEqual(2,HandLibraryResultDraws(0,offset=1).resolved_effects(result)[0]['count'])
        empty=self.intent(placement=Placement.BOTTOM,selection=Selection.ANY,offset=1,selected={'A':[]})
        self.assertEqual(1,HandLibraryResultDraws(0,offset=1).resolved_effects(committed_library_return_counts(empty,{'A':[]}))[0]['count'])
    def test_multiplayer_committed_counts_and_draw_packets_retain_apnap_not_mapping_order(self):
        players=('C','D','A','B')
        hands={seat:[member(seat+str(i)) for i in range(n)] for seat,n in zip(players,(2,0,1,3))}
        selected={seat:[m['ref'] for m in members] for seat,members in hands.items()}
        intent=self.intent(scope=Scope.ALL_PLAYERS,players=players,hands=hands,selected=selected)
        result=committed_library_return_counts(intent,{seat:[m['object_id'] for m in members] for seat,members in hands.items()})
        effects=HandLibraryResultDraws(0).resolved_effects(result)
        self.assertEqual(players,tuple(e['player'] for e in effects))
        self.assertEqual((2,0,1,3),tuple(e['count'] for e in effects))
    def test_no_ten_or_thirteen_card_limit_and_empty_hands_are_real_sets(self):
        hands={'A':[member(str(i)) for i in range(17)]}
        intent=self.intent(hands=hands,selected={'A':[m['ref'] for m in hands['A']]})
        self.assertEqual(17,committed_library_return_counts(intent,{'A':[m['object_id'] for m in hands['A']]}).counts[0][1])
        empty=self.intent(hands={'A':[]},selected={'A':[]})
        self.assertEqual((('A',0),),committed_library_return_counts(empty,{'A':[]}).counts)
    def test_continuation_cannot_change_membership_or_accept_unknown_shapes(self):
        raw=self.intent().identity()
        for change in ({'schema_version':True},{'schema_version':2},{'players':'A'},{'unknown':True},
                       {'selected':{'A':['foreign']}},{'selected':{'A':['ordinary','ordinary']}},
                       {'selected':{'A':['ordinary']}}):
            with self.assertRaises((TypeError,ValueError)):HandLibraryRedrawIntent.from_identity({**raw,**change})
        duplicate={**raw,'hands':{'A':[member('ordinary'),member('ordinary')]}}
        with self.assertRaises(ValueError):HandLibraryRedrawIntent.from_identity(duplicate)
    def test_new_draw_result_does_not_reinterpret_old_discard_result_types_or_fields(self):
        self.assertEqual({'intent_index','reduction','offset'},set(asdict(IntentResultPlayerDraws(0,'own'))))
        with self.assertRaises(ValueError):IntentResultPlayerDraws(0,'own').resolved_effects(PlayerLibraryReturnCounts((('A',1),)))
        with self.assertRaises(ValueError):HandLibraryResultDraws(0).resolved_effects(PlayerDiscardCounts((('A',1),)))
        with self.assertRaises(ValueError):HandLibraryResultDraws(0,'greatest')
        with self.assertRaises(ValueError):PlayerLibraryReturnCounts((('A',True),))
    def test_new_hand_order_has_explicit_tag_and_old_order_stays_byte_shape_compatible(self):
        old=LibraryArrivalOrder('A','bottom',('a','b'))
        self.assertEqual({'chooser':'A','position':'bottom','members':['a','b']},old.to_dict())
        new=HandLibraryArrivalOrder('B','bottom',('second','first'))
        self.assertEqual(('hand',),new.origin_zones);self.assertEqual('hand_redraw_v1',new.to_dict()['scope'])
        self.assertEqual(('second','first'),new.members)
        with self.assertRaises(ValueError):HandLibraryArrivalOrder('A','top',('a',))


class HandLibraryRedrawCompilerTests(unittest.TestCase):
    def test_exact_whole_selected_and_event_player_clauses_keep_one_generic_instruction(self):
        clauses=(
          'Shuffle the cards from your hand into your library, then draw that many cards.',
          'Each player shuffles the cards from their hand into their library, then draws that many cards.',
          'Shuffle any number of cards from your hand into your library, then draw that many cards.',
          'Put the cards in your hand on the bottom of your library in any order, then draw that many cards.',
          'That player puts the cards in their hand on the bottom of their library in any order, then draws that many cards.',
          'Put any number of cards from your hand on the bottom of your library, then draw that many cards plus one.')
        for text in clauses:
            compiled=hand_library_redraw_template(text)
            self.assertIsNotNone(compiled);_,effects,schema,mechanics=compiled.compiled()
            self.assertEqual(compiled.redraw,decode_hand_library_redraw(effects[0]))
            self.assertIsNone(schema);self.assertEqual(1,len(effects));self.assertEqual(('hand-library-redraw',),mechanics)
    def test_nearby_sources_offsets_predicates_and_extra_results_are_not_silently_dropped(self):
        for text in (
          'Shuffle your hand and graveyard into your library, then draw seven cards.',
          'Discard all the cards in your hand, then draw that many cards.',
          'Shuffle any number of red cards from your hand into your library, then draw that many cards.',
          'Shuffle any number of cards from your hand into your library, then draw that many cards plus two.',
          'Put any number of cards from your hand on top of your library, then draw that many cards plus one.',
          'Each opponent shuffles the cards from their hand into their library, then draws that many cards.',
          'Shuffle the cards from your hand into your library, then draw that many cards. Gain 1 life.'):
            self.assertIsNone(hand_library_redraw_template(text))


from types import SimpleNamespace
from quorune.object_query import ObjectQueryResult
from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery
from quorune.semantic_choices.hand_library_redraw import HandLibraryRedrawHandler
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.semantic_choices.intent_replacement import (
    semantic_intent_identity, validate_semantic_intent_identity, with_replacement_selections,
)


def private_row(ref,seat='A'):
    return ObjectQueryResult(object_id='object:'+ref,logical_object_id='logical:'+ref,ref=ref,
        printed_name='Private '+ref,owner=seat,controller=seat,zone='hand',known_to_actor=True)


class HandLibraryRedrawHandlerTests(unittest.TestCase):
    def context(self,spec,*,actor='A',controller='A',rows=None,hands=None,order=('A','B')):
        rows=(private_row('one',actor),private_row('two',actor)) if rows is None else rows
        hands={actor:[member(row.ref) for row in rows]} if hands is None else hands
        query=SnapshotSemanticChoiceQuery(seat_order=('A','B'),active_order=('A','B'),apnap_order=order,
            object_rows=rows,discard_hand_inputs=FrozenMap(hands))
        context=SimpleNamespace(actor=actor,stack_controller=controller,stack_ref='stack',
            stack_label='Hand redraw',query=query)
        effect={**spec.effect(),'actor':controller,'player':actor}
        return context,effect
    def test_whole_all_player_shuffle_has_no_choice_and_retains_private_hands_in_apnap(self):
        spec=Spec(Placement.SHUFFLE,Selection.ALL,Scope.ALL_PLAYERS)
        hands={'A':[member('one')],'B':[member('secret-B'),member('other-B')]}
        ctx,effect=self.context(spec,rows=(private_row('one'),),hands=hands,order=('B','A'))
        preparation=HandLibraryRedrawHandler().prepare(effect,ctx)
        self.assertIsNone(preparation.request)
        intent=preparation.preparation_intents[0]
        self.assertEqual(('B','A'),intent.players)
        self.assertEqual(('secret-B','other-B'),tuple(intent.selected['B']))
        self.assertEqual(('B','A'),tuple(e['player'] for e in preparation.auto_continue.resolved_effects({0:PlayerLibraryReturnCounts((('B',2),('A',1)))})))
    def test_optional_private_choice_accepts_zero_and_does_not_include_another_hand(self):
        spec=Spec(Placement.SHUFFLE,Selection.ANY,Scope.CONTROLLER)
        ctx,effect=self.context(spec)
        handler=HandLibraryRedrawHandler();preparation=handler.prepare(effect,ctx)
        self.assertEqual('actor_private',preparation.request.choice.visibility)
        schema=preparation.request.choice.choice_schema()
        self.assertEqual((0,2),(schema['minimum'],schema['maximum']))
        self.assertNotIn('secret-B',str(preparation.request.payload()))
        continuation=SimpleNamespace(effect=preparation.continuation_effect,
            semantic_frame=SimpleNamespace(controller='A'))
        completion=handler.complete(continuation,{'cards':[]},ctx.query)
        automatic=handler.prepare(completion.prepend_effects[0],ctx)
        self.assertEqual((),tuple(automatic.preparation_intents[0].selected['A']))
        literal=json.loads(json.dumps(dict(completion.prepend_effects[0]),default=lambda value:dict(value) if isinstance(value,FrozenMap) else value))
        self.assertEqual((),tuple(handler.prepare(literal,ctx).preparation_intents[0].selected['A']))
    def test_event_player_orders_only_own_hand_while_stack_controller_remains_source_owner(self):
        spec=Spec(Placement.BOTTOM,Selection.ALL,Scope.EVENT_PLAYER)
        ctx,effect=self.context(spec,actor='B',controller='A')
        handler=HandLibraryRedrawHandler();preparation=handler.prepare(effect,ctx)
        self.assertEqual({'order':'bottom_to_top'},dict(preparation.request.choice.schema_extras))
        continuation=SimpleNamespace(effect=preparation.continuation_effect,
            semantic_frame=SimpleNamespace(controller='A'))
        completion=handler.complete(continuation,{'cards':['two','one']},ctx.query)
        automatic=handler.prepare(completion.prepend_effects[0],ctx)
        intent=automatic.preparation_intents[0]
        self.assertEqual(('A',('B',)),(intent.actor,intent.players))
        self.assertEqual(('two','one'),tuple(intent.selected['B']))
    def test_private_choice_rejects_duplicate_foreign_stale_and_incomplete_permutations(self):
        spec=Spec(Placement.BOTTOM,Selection.ALL,Scope.CONTROLLER)
        ctx,effect=self.context(spec);handler=HandLibraryRedrawHandler()
        preparation=handler.prepare(effect,ctx)
        continuation=SimpleNamespace(effect=preparation.continuation_effect,
            semantic_frame=SimpleNamespace(controller='A'))
        for refs in (['one'],['one','one'],['one','foreign']):
            with self.assertRaises(SemanticChoiceError):handler.complete(continuation,{'cards':refs},ctx.query)
        stale=SnapshotSemanticChoiceQuery(seat_order=('A','B'),active_order=('A','B'),apnap_order=('A','B'),
            object_rows=(private_row('one'),),discard_hand_inputs=ctx.query.discard_hand_inputs)
        with self.assertRaises(SemanticChoiceError):handler.complete(continuation,{'cards':['one','two']},stale)
    def test_new_preparation_identity_and_replacement_selections_round_trip_without_old_field_changes(self):
        spec=Spec(Placement.SHUFFLE,Selection.ALL,Scope.CONTROLLER)
        ctx,effect=self.context(spec)
        intent=HandLibraryRedrawHandler().prepare(effect,ctx).preparation_intents[0]
        kind,identity=semantic_intent_identity(intent)
        self.assertEqual('hand_library_redraw',kind)
        self.assertEqual(identity,validate_semantic_intent_identity(kind,json.loads(json.dumps(identity))))
        retained=with_replacement_selections(intent,('replace',))
        self.assertEqual(('replace',),retained.replacement_selections)
        self.assertEqual(identity,retained.identity())
        order=HandLibraryArrivalOrder('A','bottom',('one','two'))
        self.assertEqual(order,LibraryArrivalOrder.from_dict(order.to_dict()))


from pathlib import Path
import tempfile
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_public_condition_self_replacements as controls

FIXTURE=ROOT/'tests/fixtures/hand-library-redraw-cards.json'
CANDIDATES=('Whirlpool Rider','Whirlpool Drake','Whirlpool Warrior','Winds of Change','Credit Voucher',
            "Teferi's Puzzle Box",'Arjun, the Shifting Flame','Valakut Awakening // Valakut Stoneforge')
NEIGHBORS=('Molten Psyche','Diminishing Returns','Time Spiral')
RETENTION=('Brainstorm','Riverwise Augur','Cultivate')


class HandLibraryRedrawRegistrationTests(unittest.TestCase):
    def test_eight_complete_originals_close_normally_old_library_cards_retain_and_full_rider_neighbors_do_not(self):
        registry=load_default_capability_registry();semantics=SemanticRegistry()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'originals.sqlite3';build_fixture_database([FIXTURE],path)
            with CardDatabase(path) as db:
                for name in (*CANDIDATES,*NEIGHBORS,*RETENTION):
                    with self.subTest(name=name):
                        card=db.lookup(name,fuzzy=False)
                        register_generated_programs(db,semantics,(card,),trust_level='provisional',capability_registry=registry,
                            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                        program=compile_best_available_card_program(db,card,semantic_registry=semantics,capability_registry=registry,capability_profile='commander_review')
                        bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
                        self.assertEqual(name not in NEIGHBORS,bool(bound['strict_capability_ready'] and bound['trust_basis']=='capability_closed'),bound['blockers'])


class HandLibraryRedrawGameplayTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.PublicConditionSelfReplacementTests.session
    start=controls.PublicConditionSelfReplacementTests.start
    add=controls.PublicConditionSelfReplacementTests.add
    prepare=controls.PublicConditionSelfReplacementTests.prepare
    ready=controls.PublicConditionSelfReplacementTests.ready
    checkpoint=controls.PublicConditionSelfReplacementTests.checkpoint
    resolve=controls.PublicConditionSelfReplacementTests.resolve
    replay=controls.PublicConditionSelfReplacementTests.replay
    binding=controls.PublicConditionSelfReplacementTests.binding
    priority=controls.PublicConditionSelfReplacementTests.priority
    cast=controls.PublicConditionSelfReplacementTests.cast
    play_land=controls.PublicConditionSelfReplacementTests.play_land
    reject=controls.PublicConditionSelfReplacementTests.reject
    advance=controls.PublicConditionSelfReplacementTests.advance
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'hand-redraw.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Hand library redraw',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()
    def closed(self,s,card):
        bound=self.binding(s,card)
        self.assertTrue(bound['strict_capability_ready'],(card.printed_name,bound['blockers']))
        self.assertEqual('capability_closed',bound['trust_basis'])
    def hand(self,s,seat='A'):
        return [s.state.cards[oid] for oid in s.state.players[seat].zones['hand']]
    def add_hand(self,s,seat,count):
        return [self.add(s.engine,'Plains',seat=seat,zone='hand',ref='redraw-hand-'+seat+'-'+str(i)) for i in range(count)]
    def shuffles(self,s):return {seat:s.state.players[seat].stats.get('shuffle_count',0) for seat in 'ABCD'}
    def private_hand(self,s,seat='A'):
        for principal in (*('pilot:'+p for p in 'ABCD' if p!=seat),'spectator'):
            packet=str(s.packet(principal,full=True))
            for card in self.hand(s,seat):
                if not card.is_commander:
                    self.assertNotIn(card.object_id,packet);self.assertNotIn(card.ref,packet)
    def choose(self,s,refs,seat=None):
        decision=s.state.pending_decision;self.assertEqual('semantic.choice',decision.kind)
        seat=seat or decision.actors[0];command={'action_id':'choose','cards':list(refs)}
        self.reject(s,command,'D' if seat!='D' else 'C')
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
    def activate(self,s,card,seat='A'):
        self.closed(s,card);self.priority(s,seat)
        action=next(x for x in s.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
                    if x['id'].startswith('activate:'+card.ref+':'))
        command={'action_id':action['id'],'pay':'auto'};self.reject(s,command,'D' if seat!='D' else 'C')
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)

    def test_actual_winds_of_change_finishes_all_apnap_shuffles_before_individual_draws_and_keeps_hands_private(self):
        from quorune import engine as draw_entry
        s=self.start(931010);source=self.add(s.engine,'Winds of Change',seat='B',zone='hand');self.closed(s,source)
        for seat,count in (('A',2),('B',2),('C',1),('D',0)):self.add_hand(s,seat,count)
        secret=self.add(s.engine,'Generic Bound Growth',seat='A',zone='hand',ref='winds-private-A')
        self.prepare(s,actor='B',active='B',mana={k:100 for k in 'CWUBRG'});self.checkpoint(s)
        before=self.shuffles(s);self.cast(s,source,seat='B')
        original=draw_entry.begin_draw_sequence;calls=[]
        def observe(host,seat,count,**kwargs):
            self.assertEqual({p:before[p]+1 for p in 'ABCD'},self.shuffles(s))
            if count:calls.append((seat,count))
            return original(host,seat,count,**kwargs)
        with patch.object(draw_entry,'begin_draw_sequence',side_effect=observe):self.assertIsNone(self.resolve(s))
        self.assertEqual([('B',2),('C',1),('A',3)],calls)
        self.assertEqual({'A':3,'B':2,'C':1,'D':0},{p:len(self.hand(s,p)) for p in 'ABCD'})
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        for principal in ('pilot:B','pilot:C','pilot:D','spectator'):
            self.assertNotIn(secret.object_id,str(s.packet(principal,full=True)))
        self.replay(s,load=True)

    def test_actual_whirlpool_rider_resets_only_its_controller_hand(self):
        s=self.start(931020);source=self.add(s.engine,'Whirlpool Rider',zone='hand');self.closed(s,source)
        self.add_hand(s,'A',2);other=self.add_hand(s,'B',1)[0]
        old_identity=other.logical_object_id;before=self.shuffles(s)
        self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,len(self.hand(s)));self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertEqual(old_identity,s.state.cards[other.object_id].logical_object_id)
        self.assertEqual({**before,'A':before['A']+1},self.shuffles(s));self.replay(s,load=True)

    def test_actual_whirlpool_drake_enters_and_dies_each_use_their_current_hand_and_keep_flying(self):
        s=self.start(931030);source=self.add(s.engine,'Whirlpool Drake',zone='hand');self.closed(s,source)
        self.add_hand(s,'A',2);removal=self.add(s.engine,'Murder',seat='B',zone='hand')
        s.state.players['B'].mana_pool.update({'B':2,'C':1});self.ready(s);before=self.shuffles(s)
        self.cast(s,source);self.assertIsNone(self.resolve(s));self.assertEqual(before['A']+1,self.shuffles(s)['A'])
        before_draws=len(s.state.players['A'].draw_history)
        self.cast(s,removal,[source.ref],seat='B');self.assertIsNone(self.resolve(s))
        self.assertEqual(before['A']+2,self.shuffles(s)['A'])
        self.assertEqual(before_draws+2,len(s.state.players['A'].draw_history))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.replay(s,load=True)

    def test_actual_whirlpool_warrior_self_sacrifice_pays_red_and_resets_every_hand_without_graveyards(self):
        s=self.start(931040);source=self.add(s.engine,'Whirlpool Warrior',zone='hand');self.closed(s,source)
        for seat,count in (('A',2),('B',1),('C',0),('D',3)):self.add_hand(s,seat,count)
        grave=self.add(s.engine,'Shock',seat='C',zone='graveyard')
        mana={k:0 for k in 'CWUBRG'};mana.update(C=2,U=1,R=1)
        self.prepare(s,mana=mana);self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'U':1});self.assertIsNone(self.resolve(s));before=self.shuffles(s)
        self.activate(s,source);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual({p:before[p]+1 for p in 'ABCD'},self.shuffles(s))
        self.assertEqual('graveyard',s.state.cards[grave.object_id].zone)
        self.assertEqual({'A':2,'B':1,'C':0,'D':3},{p:len(self.hand(s,p)) for p in 'ABCD'});self.replay(s,load=True)

    def test_actual_credit_voucher_pays_mana_tap_sacrifice_and_zero_selection_still_shuffles(self):
        for selected_count in (0,2):
            with self.subTest(selected_count=selected_count):
                s=self.start(931050+selected_count);source=self.add(s.engine,'Credit Voucher',zone='hand');self.closed(s,source)
                self.add_hand(s,'A',3);mana={k:0 for k in 'CWUBRG'};mana['C']=4
                self.prepare(s,mana=mana);self.checkpoint(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
                before=self.shuffles(s);old={c.object_id:c.logical_object_id for c in self.hand(s)}
                self.activate(s,source);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
                decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind)
                self.private_hand(s)
                refs=[c.ref for c in self.hand(s)][:selected_count]
                retained={c.object_id:c.logical_object_id for c in self.hand(s) if c.ref not in refs}
                s=self.replay(s,load=True);self.choose(s,refs);self.assertIsNone(self.resolve(s))
                self.assertEqual(3,len(self.hand(s)));self.assertEqual(before['A']+1,self.shuffles(s)['A'])
                self.assertTrue(all(s.state.cards[oid].logical_object_id==identity for oid,identity in retained.items()))
                if not selected_count:self.assertEqual(old,{c.object_id:c.logical_object_id for c in self.hand(s)})
                self.replay(s,load=True)

    def test_actual_puzzle_box_uses_draw_step_player_after_their_normal_draw_and_keeps_bottom_order_private(self):
        s=self.start(931060);source=self.add(s.engine,"Teferi's Puzzle Box",zone='hand');self.closed(s,source)
        self.add_hand(s,'B',2);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        before=len(s.state.players['B'].draw_history)
        self.advance(s,lambda game:game.state.active_player=='B' and game.state.step=='draw')
        decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind);self.assertEqual(['B'],decision.actors)
        self.assertEqual(before+1,len(s.state.players['B'].draw_history));cards=list(reversed(self.hand(s,'B')))
        self.assertEqual(3,len(cards));ids=[c.object_id for c in cards];refs=[c.ref for c in cards]
        for principal in ('pilot:A','pilot:C','pilot:D','spectator'):
            for ref in refs:self.assertNotIn(ref,str(s.packet(principal,full=True)))
        s=self.replay(s,load=True);self.choose(s,refs,seat='B');self.assertIsNone(self.resolve(s))
        self.assertEqual(ids,s.state.players['B'].zones['library'][:3])
        self.assertEqual(before+4,len(s.state.players['B'].draw_history));self.assertEqual(3,len(self.hand(s,'B')));self.replay(s,load=True)

    def test_actual_arjun_response_spell_retriggers_and_both_hand_orders_resolve_before_their_spells(self):
        s=self.start(931070);source=self.add(s.engine,'Arjun, the Shifting Flame',zone='hand');self.closed(s,source)
        first=self.add(s.engine,'Shock',zone='hand',ref='arjun-first');second=self.add(s.engine,'Shock',zone='hand',ref='arjun-response')
        self.add_hand(s,'A',2);self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        self.cast(s,first,['B']);self.cast(s,second,['C'])
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual((40,40),(s.state.players['B'].life,s.state.players['C'].life))
        first_refs=[c.ref for c in self.hand(s)];self.assertEqual(2,len(first_refs))
        self.private_hand(s)
        s=self.replay(s,load=True);self.choose(s,list(reversed(first_refs)))
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual((40,38),(s.state.players['B'].life,s.state.players['C'].life))
        second_refs=[c.ref for c in self.hand(s)];self.assertTrue(set(first_refs).isdisjoint(second_refs))
        self.choose(s,second_refs);self.assertIsNone(self.resolve(s));self.assertEqual((38,38),(s.state.players['B'].life,s.state.players['C'].life))
        self.replay(s,load=True)
    def test_actual_commander_library_replacement_changes_draw_count_after_pending_reload(self):
        self.deck=DeckDefinition('Commander hand redraw',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])
        for redirect in (True,False):
            with self.subTest(redirect=redirect):
                s=self.start(931100+redirect);source=self.add(s.engine,'Winds of Change',zone='hand');self.closed(s,source)
                commander=next(c for c in s.state.cards.values() if c.owner=='A' and c.is_commander)
                player=s.state.players['A'];player.zones['command'].remove(commander.object_id);player.zones['hand'].append(commander.object_id)
                commander.zone='hand';commander.known_to=['A']
                register_generated_programs(self.db,s.engine.semantics,(self.db.lookup('Isamaru, Hound of Konda'),),trust_level='provisional',
                    capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,
                    promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                self.closed(s,commander);self.add_hand(s,'A',1)
                self.ready(s);before=self.shuffles(s);self.cast(s,source)
                self.assertEqual('replacement.order',self.resolve(s).kind);self.assertEqual(before,self.shuffles(s))
                self.assertEqual(2,len(self.hand(s)));s=self.replay(s,load=True)
                options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
                chosen=next(x for x in options if bool(x.get('decline')) is (not redirect))
                command={'action_id':'choose','replacement':chosen['id']};self.reject(s,command,'D')
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                for _ in range(6):
                    decision=self.resolve(s)
                    if decision is None:break
                    self.assertEqual('replacement.order',decision.kind);self.assertEqual(['A'],decision.actors)
                    options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
                    chosen=next(x for x in options if x.get('decline'))
                    s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','replacement':chosen['id']});self.assertTrue(result.ok,result.summary)
                else:self.fail('Commander draw boundary did not finish')
                self.assertEqual(1 if redirect else 2,len(self.hand(s)))
                if redirect:self.assertEqual('command',s.state.cards[commander.object_id].zone)
                self.assertEqual({p:before[p]+1 for p in 'ABCD'},self.shuffles(s));self.replay(s,load=True)
    def test_fresh_requested_count_mutation_is_killed_by_actual_commander_redirection(self):
        from contextlib import nullcontext
        from quorune.rules import hand_library_redraw as owned
        def mutant(intent,libraries):
            return PlayerLibraryReturnCounts(tuple((seat,len(intent.selected[seat])) for seat in intent.players))
        with patch.object(owned,'committed_library_return_counts',mutant),patch.object(self,'subTest',lambda **kwargs:nullcontext()):
            with self.assertRaises(AssertionError):self.test_actual_commander_library_replacement_changes_draw_count_after_pending_reload()
    def test_actual_fault_after_second_shuffle_rolls_back_all_hand_moves_rng_and_draws_then_retries(self):
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s=self.start(931110);source=self.add(s.engine,'Winds of Change',zone='hand');self.closed(s,source)
        for seat in 'ABCD':self.add_hand(s,seat,2)
        self.ready(s);before=self.shuffles(s);self.cast(s,source);original=ZoneTransitionOwner.shuffle_library;calls=[];failed=None
        def fault(owner,seat,**kwargs):
            value=original(owner,seat,**kwargs);calls.append(seat)
            if len(calls)==2:raise GameRuleError('Injected hand redraw shuffle failure')
            return value
        with patch.object(ZoneTransitionOwner,'shuffle_library',fault):
            for _ in range(8):
                principal=s.pending_principals()[0];state_before=authoritative_state_hash(s.state);result=s.act(principal,{'action_id':'pass'})
                if not result.ok:self.assertEqual(state_before,authoritative_state_hash(s.state));failed=principal;break
        self.assertEqual(['A','B'],calls);self.assertIsNotNone(failed);self.assertEqual(before,self.shuffles(s))
        self.assertEqual({p:2 for p in 'ABCD'},{p:len(self.hand(s,p)) for p in 'ABCD'})
        s=self.replay(s,load=True);result=s.act(failed,{'action_id':'pass'});self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual({p:before[p]+1 for p in 'ABCD'},self.shuffles(s));self.replay(s,load=True)
    def test_actual_registered_redraw_dependency_cannot_hide_in_removed_coverage(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.hand_library_redraw_model import HAND_LIBRARY_REDRAW_CAPABILITY as cap
        s=self.start(931120);source=self.add(s.engine,'Winds of Change',zone='hand');self.closed(s,source)
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(e.get('op')=='hand_library_redraw' for e in p.effects))
        deps=tuple(value for value in program.capability_dependencies if value!=cap)
        missing=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_actual_valakut_optional_zero_draws_one_and_its_land_face_enters_tapped_then_taps_for_red(self):
        s=self.start(931080);spell=self.add(s.engine,'Valakut Awakening // Valakut Stoneforge',zone='hand',ref='valakut-front');self.closed(s,spell)
        land=self.add(s.engine,'Valakut Awakening // Valakut Stoneforge',zone='hand',ref='valakut-back');self.closed(s,land)
        self.ready(s);self.cast(s,spell)
        self.assertEqual('semantic.choice',self.resolve(s).kind);before=len(s.state.players['A'].draw_history)
        self.choose(s,[]);self.assertIsNone(self.resolve(s));self.assertEqual(before+1,len(s.state.players['A'].draw_history))
        self.play_land(s,land);self.assertTrue(s.state.cards[land.object_id].tapped)
        self.advance(s,lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main')
        self.advance(s,lambda game:game.state.active_player=='A' and game.state.phase=='precombat_main')
        self.assertFalse(s.state.cards[land.object_id].tapped);before=s.state.players['A'].mana_pool.get('R',0)
        self.activate(s,s.state.cards[land.object_id])
        self.assertEqual(before+1,s.state.players['A'].mana_pool.get('R',0));self.assertTrue(s.state.cards[land.object_id].tapped)
        self.replay(s,load=True)

    def test_actual_seventeen_card_voucher_choice_and_draw_are_not_capped_at_ten_or_thirteen(self):
        s=self.start(931130);source=self.add(s.engine,'Credit Voucher',zone='hand');self.closed(s,source)
        self.add_hand(s,'A',17);mana={k:0 for k in 'CWUBRG'};mana['C']=4
        self.prepare(s,mana=mana);self.checkpoint(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        before=self.shuffles(s);self.activate(s,source);self.assertEqual('semantic.choice',self.resolve(s).kind)
        ctx=s.packet('pilot:A',full=True)['decision']['ctx'];schema=ctx['legal_actions'][0]['choice_schema']
        self.assertEqual(17,schema['maximum']);refs=[c.ref for c in self.hand(s)]
        self.private_hand(s)
        s=self.replay(s,load=True);self.choose(s,refs);self.assertIsNone(self.resolve(s))
        self.assertEqual(17,len(self.hand(s)));self.assertEqual(before['A']+1,self.shuffles(s)['A']);self.replay(s,load=True)

    def test_actual_draw_limit_owner_applies_after_hand_returns_without_changing_arrival_counts(self):
        s=self.start(931140);source=self.add(s.engine,'Winds of Change',seat='B',zone='hand');self.closed(s,source)
        limiter=self.add(s.engine,'Narset, Parter of Veils');mine=self.add(s.engine,'Howling Mine')
        self.closed(s,limiter);self.closed(s,mine)
        for seat in 'ABCD':self.add_hand(s,seat,2)
        self.add(s.engine,'Mountain',seat='B');self.ready(s)
        before_draw=len(s.state.players['B'].draw_history)
        self.advance(s,lambda game:game.state.active_player=='B' and game.state.phase=='precombat_main')
        self.assertEqual(before_draw+1,len(s.state.players['B'].draw_history))
        before=self.shuffles(s);self.cast(s,source,seat='B');self.assertIsNone(self.resolve(s))
        self.assertEqual({p:before[p]+1 for p in 'ABCD'},self.shuffles(s))
        self.assertEqual({'A':2,'B':0,'C':1,'D':1},{p:len(self.hand(s,p)) for p in 'ABCD'})
        self.assertEqual(before_draw+1,len(s.state.players['B'].draw_history));self.replay(s,load=True)

    def test_actual_two_puzzle_boxes_take_separate_current_private_hand_snapshots(self):
        s=self.start(931150)
        for i in range(2):
            source=self.add(s.engine,"Teferi's Puzzle Box",ref='double-box-'+str(i));self.closed(s,source)
        self.add_hand(s,'B',2);self.ready(s);before=len(s.state.players['B'].draw_history)
        self.advance(s,lambda game:game.state.active_player=='B' and game.state.step=='draw')
        if s.state.pending_decision.kind=='trigger.order':
            actor=s.state.pending_decision.actors[0];ctx=s.packet('pilot:'+actor,full=True)['decision']['ctx']
            result=s.act('pilot:'+actor,{'action_id':'order','triggers':[row['id'] for row in ctx['triggers']]});self.assertTrue(result.ok,result.summary)
        previous=None
        for _ in range(2):
            decision=self.resolve(s);self.assertEqual('semantic.choice',decision.kind);self.assertEqual(['B'],decision.actors)
            refs=[c.ref for c in self.hand(s,'B')];self.assertEqual(3,len(refs))
            self.private_hand(s,'B')
            if previous is not None:self.assertTrue(set(previous).isdisjoint(refs))
            previous=refs;s=self.replay(s,load=True);self.choose(s,list(reversed(refs)),seat='B')
        self.assertIsNone(self.resolve(s));self.assertEqual(before+7,len(s.state.players['B'].draw_history));self.replay(s,load=True)

    def test_actual_arjun_hand_trigger_finishes_after_its_creature_source_is_destroyed(self):
        s=self.start(931160);source=self.add(s.engine,'Arjun, the Shifting Flame',zone='hand');self.closed(s,source)
        spell=self.add(s.engine,'Shock',zone='hand');removal=self.add(s.engine,'Murder',seat='B',zone='hand');self.add_hand(s,'A',2)
        s.state.players['B'].mana_pool.update({'B':2,'C':1});self.ready(s);self.cast(s,source);self.assertIsNone(self.resolve(s))
        self.cast(s,spell,['B']);self.cast(s,removal,[source.ref],seat='B')
        self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        refs=[c.ref for c in self.hand(s)];s=self.replay(s,load=True);self.choose(s,refs);self.assertIsNone(self.resolve(s))
        self.assertEqual(2,len(self.hand(s)));self.assertEqual(38,s.state.players['B'].life);self.replay(s,load=True)

    def test_actual_valakut_selected_bottom_order_draws_committed_count_plus_one_and_keeps_the_rest(self):
        s=self.start(931170);source=self.add(s.engine,'Valakut Awakening // Valakut Stoneforge',zone='hand');self.closed(s,source)
        hand=self.add_hand(s,'A',3);self.ready(s);self.cast(s,source);self.assertEqual('semantic.choice',self.resolve(s).kind)
        selected=(hand[2],hand[0]);retained=hand[1];identity=retained.logical_object_id
        ids=[c.object_id for c in selected];refs=[c.ref for c in selected]
        self.private_hand(s)
        s=self.replay(s,load=True);self.choose(s,refs);self.assertIsNone(self.resolve(s))
        self.assertEqual(4,len(self.hand(s)));self.assertEqual(identity,s.state.cards[retained.object_id].logical_object_id)
        self.assertEqual(ids,s.state.players['A'].zones['library'][:2]);self.replay(s,load=True)

    def test_actual_puzzle_box_skips_its_bound_draw_player_after_their_offered_concession(self):
        s=self.start(931180);source=self.add(s.engine,"Teferi's Puzzle Box");self.closed(s,source)
        self.add_hand(s,'B',2);self.ready(s)
        self.advance(s,lambda game:game.state.active_player=='B' and game.state.step=='draw')
        self.priority(s,'B');actions=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        action=next(x for x in actions if x['id']=='concede');before=len(s.state.players['B'].draw_history)
        result=s.act('pilot:B',{'action_id':action['id'],'choices':{'confirm_concede':True}});self.assertTrue(result.ok,result.summary)
        self.assertFalse(s.state.players['B'].in_game);self.assertIsNone(self.resolve(s))
        self.assertEqual(before,len(s.state.players['B'].draw_history));self.assertFalse(s.state.stack);self.replay(s,load=True)
