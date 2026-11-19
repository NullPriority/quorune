"""CR401.4/609.3: mandatory putback uses the current hand after drawing."""
from dataclasses import asdict,FrozenInstanceError
import json,unittest
from quorune.fixed_hand_putback_model import (
    FixedHandPutbackSpec as Spec, PutbackPlacement as Placement,
    FixedHandPutbackOrder,decode_fixed_hand_putback,
)
from quorune.library_order_model import LibraryArrivalOrder,FixedHandTopOrderSpec
from quorune.compiler.fixed_hand_putback_templates import fixed_draw_hand_putback_template


class FixedPutbackModelTests(unittest.TestCase):
    def test_four_closed_count_placement_pairs_are_immutable_and_round_trip(self):
        for count,placement in ((1,Placement.BOTTOM),(2,Placement.BOTTOM),(1,Placement.SHUFFLE),(2,Placement.EITHER)):
            spec=Spec(count,placement)
            self.assertEqual(spec,Spec.from_dict(json.loads(json.dumps(spec.to_dict()))))
            self.assertEqual(spec,decode_fixed_hand_putback(spec.effect()))
            with self.assertRaises(FrozenInstanceError):spec.count=3
    def test_unknown_schema_quantities_and_cross_product_extensions_reject(self):
        raw=Spec(1,Placement.BOTTOM).to_dict()
        for change in ({'schema_version':True},{'schema_version':2},{'count':True},{'count':0},
                       {'count':3},{'placement':'top'},{'extra':True}):
            with self.assertRaises((ValueError,TypeError)):Spec.from_dict({**raw,**change})
        for count,placement in ((2,Placement.SHUFFLE),(1,Placement.EITHER)):
            with self.assertRaises(ValueError):Spec(count,placement)
        with self.assertRaises(ValueError):decode_fixed_hand_putback({**Spec(1,Placement.BOTTOM).effect(),'player':'$target.0'})
    def test_mandatory_effect_puts_back_as_much_as_possible_without_requiring_a_cost(self):
        spec=Spec(2,Placement.BOTTOM)
        self.assertEqual((0,1,2,2),tuple(spec.required(n) for n in (0,1,2,17)))
        for invalid in (-1,True,1.0):
            with self.assertRaises(ValueError):spec.required(invalid)
    def test_dream_pair_uses_one_edge_and_cannot_split_or_choose_for_fixed_placement(self):
        either=Spec(2,Placement.EITHER)
        self.assertEqual('top',either.resolved_placement('top'))
        self.assertEqual('bottom',either.resolved_placement('bottom'))
        for invalid in (None,'shuffle',['top','bottom']):
            with self.assertRaises((ValueError,TypeError)):either.resolved_placement(invalid)
        with self.assertRaises(ValueError):Spec(1,Placement.BOTTOM).resolved_placement('top')
    def test_new_private_order_tag_preserves_old_order_and_hand_top_field_shapes(self):
        self.assertEqual({'count':2},asdict(FixedHandTopOrderSpec(2)))
        old=LibraryArrivalOrder('A','bottom',('a','b'))
        self.assertEqual({'chooser':'A','position':'bottom','members':['a','b']},old.to_dict())
        for edge in ('top','bottom'):
            new=FixedHandPutbackOrder('A',edge,('second','first'))
            self.assertEqual(('hand',),new.origin_zones)
            self.assertEqual('fixed_hand_putback_v1',new.to_dict()['scope'])
            self.assertEqual(('second','first'),new.members)


class FixedPutbackCompilerTests(unittest.TestCase):
    def test_four_whole_clauses_emit_ordinary_draw_before_owned_putback(self):
        clauses=(
          ('Draw two cards, then put two cards from your hand on the bottom of your library.',2,2,'bottom'),
          ('Draw three cards, then put a card from your hand on the bottom of your library.',3,1,'bottom'),
          ('Draw two cards, then shuffle a card from your hand into your library.',2,1,'shuffle'),
          ('Draw three cards, then put two cards from your hand both on top of your library or both on the bottom of your library.',3,2,'either'))
        for text,draw,count,placement in clauses:
            template=fixed_draw_hand_putback_template(text);self.assertIsNotNone(template)
            _,effects,schema,mechanics=template.compiled()
            self.assertEqual(('draw','fixed_hand_putback'),tuple(e['op'] for e in effects))
            self.assertEqual(draw,effects[0]['count']);self.assertEqual(count,effects[1]['putback']['count'])
            self.assertEqual(placement,effects[1]['putback']['placement']);self.assertIsNone(schema)
            self.assertEqual(('fixed-draw-hand-putback',),mechanics)
    def test_other_recipients_optional_counts_filters_and_unrepresented_riders_stay_residual(self):
        for text in (
          'Draw three cards, then put two cards from your hand on top of your library in any order.',
          'Draw two cards, then shuffle up to one card from your hand into your library.',
          'Draw three cards, then put a creature card from your hand on the bottom of your library.',
          'Target player draws two cards, then shuffles a card from their hand into their library.',
          'Draw two cards, then shuffle a card from your hand into your library. Gain 1 life.',
          'Draw four cards, then put two cards from your hand on the bottom of your library.'):
            self.assertIsNone(fixed_draw_hand_putback_template(text))


from types import SimpleNamespace
from quorune.object_query import ObjectQueryResult
from quorune.replacement.immutable import FrozenMap
from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery
from quorune.semantic_choices.fixed_hand_putback import FixedHandPutbackHandler
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.semantic_choices.completed_choice import _data,_restore
from quorune.rules.fixed_hand_putback import fixed_putback_node_capabilities,fixed_putback_payload_dependencies


def hand_row(ref):
    return ObjectQueryResult(object_id='object:'+ref,logical_object_id='logical:'+ref,ref=ref,
        printed_name='Private '+ref,owner='A',controller='A',zone='hand',known_to_actor=True)


class FixedPutbackHandlerTests(unittest.TestCase):
    def context(self,refs=('old','drawn','other')):
        query=SnapshotSemanticChoiceQuery(seat_order=('A','B'),active_order=('A','B'),apnap_order=('A','B'),
                                         object_rows=tuple(hand_row(ref) for ref in refs))
        return SimpleNamespace(actor='A',stack_controller='A',stack_ref='stack',stack_label='Putback',query=query)
    def effect(self,count=1,placement=Placement.BOTTOM):
        return {**Spec(count,placement).effect(),'player':'A'}
    def test_private_exact_selection_uses_all_current_hand_and_rejects_foreign_duplicate_stale_refs(self):
        handler=FixedHandPutbackHandler();ctx=self.context();prep=handler.prepare(self.effect(),ctx)
        self.assertEqual('actor_private',prep.request.choice.visibility)
        self.assertEqual({'old','drawn','other'},set(prep.request.choice.legal_refs))
        cont=SimpleNamespace(effect=prep.continuation_effect,semantic_frame=SimpleNamespace(controller='A'))
        for refs in ([],['foreign'],['old','old']):
            with self.assertRaises(SemanticChoiceError):handler.complete(cont,{'cards':refs},ctx.query)
        stale=self.context(('old','drawn')).query
        with self.assertRaises(SemanticChoiceError):handler.complete(cont,{'cards':['old']},stale)
        done=handler.complete(cont,{'cards':['old']},ctx.query)
        self.assertEqual(('old',),done.intents[0].object_refs)
        self.assertEqual('bottom',done.intents[0].library_order.position)
    def test_empty_hand_does_not_create_a_choice_move_or_shuffle_but_short_hand_is_mandatory(self):
        handler=FixedHandPutbackHandler();empty=handler.prepare(self.effect(1,Placement.SHUFFLE),self.context(()))
        self.assertIsNone(empty.request);self.assertEqual((),empty.preparation_intents)
        self.assertEqual((),empty.auto_continue.prepend_effects)
        short=handler.prepare(self.effect(2),self.context(('only',)))
        self.assertEqual((1,1),(short.request.choice.minimum,short.request.choice.maximum))
    def test_either_edge_is_one_separate_choice_then_exact_private_order_without_repeating_draw(self):
        handler=FixedHandPutbackHandler();ctx=self.context();effect=self.effect(2,Placement.EITHER)
        prep=handler.prepare(effect,ctx);self.assertEqual(('top','bottom'),prep.request.choice.legal_values)
        cont=SimpleNamespace(effect=prep.continuation_effect,semantic_frame=SimpleNamespace(controller='A'))
        for edge in ('top','bottom'):
            choice=handler.complete(cont,{'placement':edge},ctx.query)
            self.assertIsNotNone(choice.repeat_effect)
            cards=handler.prepare(choice.repeat_effect,ctx)
            saved=SimpleNamespace(effect=cards.continuation_effect,semantic_frame=SimpleNamespace(controller='A'))
            done=handler.complete(saved,{'cards':['other','old']},ctx.query)
            self.assertEqual(edge,done.intents[0].library_order.position)
            self.assertEqual(('other','old'),done.intents[0].library_order.members)
            self.assertEqual(done.intents[0].library_order,_restore(_data(done.intents[0].library_order)))
    def test_nonempty_shuffle_completion_queues_existing_shuffle_only_after_movement(self):
        handler=FixedHandPutbackHandler();ctx=self.context();prep=handler.prepare(self.effect(1,Placement.SHUFFLE),ctx)
        cont=SimpleNamespace(effect=prep.continuation_effect,semantic_frame=SimpleNamespace(controller='A'))
        done=handler.complete(cont,{'cards':['old']},ctx.query)
        self.assertEqual(1,len(done.intents));self.assertEqual('library',done.intents[0].destination)
        self.assertEqual('shuffle_controller_library',done.prepend_effects[0]['op'])
        self.assertEqual('A',done.prepend_effects[0]['player'])
    def test_full_sequence_scope_and_runtime_fine_dependency_are_closed(self):
        text='Draw two cards, then shuffle a card from your hand into your library.'
        _,effects,schema,mechanics=fixed_draw_hand_putback_template(text).compiled()
        required=fixed_putback_node_capabilities(effects=effects,target_schema=schema,mechanic_ids=mechanics)
        self.assertIn('library.order.fixed_hand_putback',required);self.assertIn('zone.draw.library_to_hand',required)
        self.assertIn('library.shuffle.fixed_controller',fixed_putback_payload_dependencies(effects[1]))
        self.assertEqual((),fixed_putback_node_capabilities(effects=({**effects[0],'count':4},effects[1]),target_schema=None,mechanic_ids=mechanics))
        self.assertEqual((),fixed_putback_node_capabilities(effects=effects,target_schema={'zones':['player']},mechanic_ids=mechanics))
        raw=Spec(1,Placement.BOTTOM).effect()
        with self.assertRaises(ValueError):fixed_putback_payload_dependencies({**raw,'putback':{**raw['putback'],'schema_version':True}})


from pathlib import Path
import tempfile
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_public_condition_self_replacements as controls

FIXTURE=ROOT/'tests/fixtures/fixed-draw-hand-putback-cards.json'
CANDIDATES=('Sawtooth Loon','Amass the Components','See Beyond','Dream Cache')
RETENTION=('Brainstorm','Riverwise Augur','Winds of Change','Credit Voucher')


class FixedPutbackRegistrationTests(unittest.TestCase):
    def test_four_complete_originals_close_normally_and_old_hand_library_controls_retain(self):
        registry=load_default_capability_registry();semantics=SemanticRegistry()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'originals.sqlite3';build_fixture_database([FIXTURE],path)
            with CardDatabase(path) as db:
                for name in (*CANDIDATES,*RETENTION,'Read the Runes'):
                    with self.subTest(name=name):
                        card=db.lookup(name,fuzzy=False)
                        register_generated_programs(db,semantics,(card,),trust_level='provisional',capability_registry=registry,
                            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
                        program=compile_best_available_card_program(db,card,semantic_registry=semantics,capability_registry=registry,capability_profile='commander_review')
                        bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
                        self.assertEqual(name!='Read the Runes',bool(bound['strict_capability_ready'] and bound['trust_basis']=='capability_closed'),bound['blockers'])


class FixedPutbackGameplayTests(unittest.TestCase):
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
    reject=controls.PublicConditionSelfReplacementTests.reject
    advance=controls.PublicConditionSelfReplacementTests.advance
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'fixed-putback.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',FIXTURE],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Fixed private putback',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()
    def closed(self,s,card):
        bound=self.binding(s,card)
        self.assertTrue(bound['strict_capability_ready'],(card.printed_name,bound['blockers']))
        self.assertEqual('capability_closed',bound['trust_basis'])
    def hand(self,s):return [s.state.cards[oid] for oid in s.state.players['A'].zones['hand']]
    def choose(self,s,command,seat='A'):
        self.assertEqual('semantic.choice',s.state.pending_decision.kind)
        self.reject(s,command,'D' if seat!='D' else 'C')
        result=s.act('pilot:'+seat,command);self.assertTrue(result.ok,result.summary)
    def choose_cards(self,s,refs):self.choose(s,{'action_id':'choose','cards':list(refs)})
    def private(self,s):
        for principal in ('pilot:B','pilot:C','pilot:D','spectator'):
            packet=str(s.packet(principal,full=True))
            for card in self.hand(s):
                if not card.is_commander:self.assertNotIn(card.ref,packet);self.assertNotIn(card.object_id,packet)
    def short_library(self,s,count):
        player=s.state.players['A'];keep=set(player.zones['library'][-count:]) if count else set()
        for oid in tuple(player.zones['library']):
            if oid not in keep:s.engine.move_card(oid,'exile',reason='Initial short library scenario',log=False)
    def order_triggers(self,s):
        if s.state.pending_decision.kind=='trigger.order':
            ctx=s.packet('pilot:A',full=True)['decision']['ctx']
            result=s.act('pilot:A',{'action_id':'order','triggers':[r['id'] for r in ctx['triggers']]});self.assertTrue(result.ok,result.summary)

    def test_actual_amass_draws_three_then_returns_an_old_current_hand_card_privately(self):
        s=self.start(932010);source=self.add(s.engine,'Amass the Components',zone='hand');self.closed(s,source)
        old=self.add(s.engine,'Shock',zone='hand',ref='putback-old-card');before=len(s.state.players['A'].draw_history)
        self.ready(s);self.cast(s,source);self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.assertEqual(before+3,len(s.state.players['A'].draw_history));self.assertEqual(4,len(self.hand(s)));self.private(s)
        s=self.replay(s,load=True);self.choose_cards(s,[old.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(old.object_id,s.state.players['A'].zones['library'][0]);self.assertEqual(3,len(self.hand(s)));self.replay(s,load=True)

    def test_actual_see_beyond_draws_two_and_shuffles_only_after_old_hand_selection(self):
        s=self.start(932020);source=self.add(s.engine,'See Beyond',zone='hand');self.closed(s,source)
        old=self.add(s.engine,'Shock',zone='hand',ref='see-beyond-old');self.ready(s)
        before=s.state.players['A'].stats.get('shuffle_count',0);self.cast(s,source);self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.assertEqual(before,s.state.players['A'].stats.get('shuffle_count',0));self.private(s)
        s=self.replay(s,load=True);self.choose_cards(s,[old.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(before+1,s.state.players['A'].stats.get('shuffle_count',0));self.assertEqual(2,len(self.hand(s)));self.replay(s,load=True)

    def test_actual_dream_cache_one_group_chooses_either_edge_and_exact_private_permutation(self):
        for edge in ('top','bottom'):
            with self.subTest(edge=edge):
                s=self.start(932030+(edge=='bottom'));source=self.add(s.engine,'Dream Cache',zone='hand');self.closed(s,source)
                old=self.add(s.engine,'Shock',zone='hand',ref='dream-old');self.ready(s);self.cast(s,source)
                self.assertEqual('semantic.choice',self.resolve(s).kind);before=len(s.state.players['A'].draw_history)
                for invalid in ('shuffle',['top','bottom']):
                    self.reject(s,{'action_id':'choose','placement':invalid},'A')
                s=self.replay(s,load=True);self.choose(s,{'action_id':'choose','placement':edge})
                self.assertEqual('semantic.choice',self.resolve(s).kind);self.assertEqual(before,len(s.state.players['A'].draw_history))
                cards=self.hand(s);other=next(c for c in cards if c.object_id!=old.object_id)
                refs=[other.ref,old.ref];ids=[other.object_id,old.object_id];self.private(s)
                for invalid in ([old.ref],[old.ref,old.ref],[old.ref,'foreign']):
                    self.reject(s,{'action_id':'choose','cards':invalid},'A')
                s=self.replay(s,load=True);self.choose_cards(s,refs);self.assertIsNone(self.resolve(s))
                library=s.state.players['A'].zones['library']
                self.assertEqual(ids,list(reversed(library[-2:])) if edge=='top' else library[:2])
                self.assertEqual(2,len(self.hand(s)));self.replay(s,load=True)

    def test_actual_sawtooth_both_etb_orders_keep_flying_and_nontargeted_colored_self_return(self):
        for putback_first in (False,True):
            with self.subTest(putback_first=putback_first):
                s=self.start(932040+putback_first);source=self.add(s.engine,'Sawtooth Loon',zone='hand');self.closed(s,source)
                for i in range(2):self.add(s.engine,'Plains',zone='hand',ref='loon-old-'+str(i))
                self.ready(s);self.cast(s,source)
                self.advance(s,lambda game:game.state.pending_decision is not None and game.state.pending_decision.kind=='trigger.order')
                ctx=s.packet('pilot:A',full=True)['decision']['ctx'];triggers=ctx['triggers']
                self.assertIn('flying',s.engine._combat_keywords(s.state.cards[source.object_id]))
                putback=next(r for r in triggers if 'draw two cards' in str(r).casefold())
                other=next(r for r in triggers if r['id']!=putback['id'])
                # Trigger lists are ordered bottom-to-top, so the last trigger resolves first.
                order=[other['id'],putback['id']] if putback_first else [putback['id'],other['id']]
                result=s.act('pilot:A',{'action_id':'order','triggers':order});self.assertTrue(result.ok,result.summary)
                for _ in range(4):
                    decision=self.resolve(s)
                    if decision is None:break
                    packet=s.packet('pilot:A',full=True)['decision']['ctx']
                    if decision.kind=='semantic.choice':
                        self.assertEqual('fixed_hand_putback',packet.get('operation'))
                        refs=[c.ref for c in self.hand(s) if c.object_id!=source.object_id][:2]
                        s=self.replay(s,load=True)
                        self.choose_cards(s,refs)
                    else:
                        self.assertEqual('choice.apnap',decision.kind)
                        command={'action_id':'choose','cards':[s.state.cards[source.object_id].ref]}
                        self.reject(s,command,'D');s=self.replay(s,load=True)
                        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                else:self.fail('Sawtooth two ETBs did not finish')
                self.assertEqual('hand',s.state.cards[source.object_id].zone);self.assertEqual(3,len(self.hand(s)));self.replay(s,load=True)

    def test_actual_amass_short_library_putback_commits_before_failed_draw_state_action(self):
        from quorune.zone_transitions import ZoneTransitionOwner
        for available in (0,1,2):
            with self.subTest(available=available):
                s=self.start(932050+available);source=self.add(s.engine,'Amass the Components',zone='hand');self.closed(s,source)
                old=self.add(s.engine,'Shock',zone='hand',ref='amass-short-old');self.short_library(s,available)
                self.ready(s);self.cast(s,source);decision=self.resolve(s)
                self.assertTrue(s.state.players['A'].in_game);self.assertEqual('semantic.choice',decision.kind)
                self.assertEqual(available+1,len(self.hand(s)));s=self.replay(s,load=True)
                original=ZoneTransitionOwner._commit_move;observed=[]
                def observe(owner,plan,departure,**kwargs):
                    value=original(owner,plan,departure,**kwargs)
                    if plan.card.object_id==old.object_id and plan.card.zone=='library':observed.append(owner.state.players['A'].in_game)
                    return value
                with patch.object(ZoneTransitionOwner,'_commit_move',observe):self.choose_cards(s,[old.ref])
                self.assertEqual([True],observed);self.assertFalse(s.state.players['A'].in_game);self.replay(s,load=True)

    def test_actual_see_beyond_blocked_draw_still_puts_back_and_empty_hand_does_not_shuffle(self):
        for has_old in (False,True):
            with self.subTest(has_old=has_old):
                s=self.start(932060+has_old);source=self.add(s.engine,'See Beyond',zone='hand');self.closed(s,source)
                if has_old:old=self.add(s.engine,'Shock',zone='hand',ref='blocked-see-old')
                limiter=self.add(s.engine,'Spirit of the Labyrinth');self.closed(s,limiter)
                ring=self.add(s.engine,'Sol Ring',zone='library',ref='next-normal-draw')
                for i in range(3):self.add(s.engine,'Island',ref='see-real-mana-'+str(i))
                self.prepare(s,actor='B',active='B',mana={});self.checkpoint(s)
                for next_seat in ('C','D','A'):
                    self.advance(s,lambda game,seat=next_seat:game.state.active_player==seat and game.state.phase=='precombat_main')
                self.assertIn(ring.object_id,s.state.players['A'].zones['hand'])
                self.cast(s,ring);self.assertIsNone(self.resolve(s))
                before=s.state.players['A'].stats.get('shuffle_count',0);draws=len(s.state.players['A'].draw_history)
                self.cast(s,source);decision=self.resolve(s)
                self.assertEqual(draws,len(s.state.players['A'].draw_history))
                if has_old:
                    self.assertEqual('semantic.choice',decision.kind);self.choose_cards(s,[old.ref]);self.assertIsNone(self.resolve(s))
                else:self.assertIsNone(decision)
                self.assertEqual(before+int(has_old),s.state.players['A'].stats.get('shuffle_count',0));self.assertEqual(0,len(self.hand(s)));self.replay(s,load=True)

    def commander_in_hand(self,s):
        commander=next(c for c in s.state.cards.values() if c.owner=='A' and c.is_commander)
        player=s.state.players['A'];player.zones['command'].remove(commander.object_id);player.zones['hand'].append(commander.object_id)
        commander.zone='hand';commander.known_to=['A']
        register_generated_programs(self.db,s.engine.semantics,(self.db.lookup('Isamaru, Hound of Konda'),),trust_level='provisional',
            capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        self.closed(s,commander);return commander

    def commander_replacement(self,s,redirect):
        self.assertEqual('replacement.order',s.state.pending_decision.kind)
        s=self.replay(s,load=True)
        options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        chosen=next(x for x in options if bool(x.get('decline')) is (not redirect))
        command={'action_id':'choose','replacement':chosen['id']};self.reject(s,command,'D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        return s

    def test_actual_see_beyond_selected_commander_still_shuffles_after_replacement_pending_reload(self):
        self.deck=DeckDefinition('Commander fixed putback',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])
        for redirect in (True,False):
            with self.subTest(redirect=redirect):
                s=self.start(932090+redirect);source=self.add(s.engine,'See Beyond',zone='hand');self.closed(s,source)
                commander=self.commander_in_hand(s);self.ready(s);before=s.state.players['A'].stats.get('shuffle_count',0)
                self.cast(s,source);self.assertEqual('semantic.choice',self.resolve(s).kind)
                self.choose_cards(s,[commander.ref]);self.assertEqual('replacement.order',s.state.pending_decision.kind)
                self.assertEqual(before,s.state.players['A'].stats.get('shuffle_count',0))
                s=self.commander_replacement(s,redirect);self.assertIsNone(self.resolve(s))
                self.assertEqual('command' if redirect else 'library',s.state.cards[commander.object_id].zone)
                self.assertEqual(before+1,s.state.players['A'].stats.get('shuffle_count',0));self.assertEqual(2,len(self.hand(s)))
                self.replay(s,load=True)

    def test_actual_dream_cache_replaced_commander_preserves_surviving_private_edge_order(self):
        self.deck=DeckDefinition('Commander ordered putback',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Isamaru, Hound of Konda'])
        for edge in ('top','bottom'):
            with self.subTest(edge=edge):
                s=self.start(932100+(edge=='bottom'));source=self.add(s.engine,'Dream Cache',zone='hand');self.closed(s,source)
                commander=self.commander_in_hand(s);old=self.add(s.engine,'Shock',zone='hand',ref='dream-replaced-survivor')
                self.ready(s);self.cast(s,source);self.assertEqual('semantic.choice',self.resolve(s).kind)
                self.choose(s,{'action_id':'choose','placement':edge});self.assertEqual('semantic.choice',self.resolve(s).kind)
                self.choose_cards(s,[commander.ref,old.ref]);self.assertEqual('replacement.order',s.state.pending_decision.kind)
                s=self.commander_replacement(s,True);self.assertIsNone(self.resolve(s))
                self.assertEqual('command',s.state.cards[commander.object_id].zone)
                library=s.state.players['A'].zones['library'];self.assertEqual(old.object_id,library[-1] if edge=='top' else library[0])
                self.assertEqual(3,len(self.hand(s)));self.replay(s,load=True)

    def test_actual_amass_resumed_dredge_retains_remaining_draws_and_private_putback(self):
        s=self.start(932110);source=self.add(s.engine,'Amass the Components',zone='hand');self.closed(s,source)
        dredger=self.add(s.engine,'Dakmor Salvage',zone='graveyard',ref='putback-dredger');self.closed(s,dredger)
        top=list(reversed(s.state.players['A'].zones['library'][-5:]))
        self.ready(s);before=len(s.state.players['A'].draw_history);self.cast(s,source)
        self.assertEqual('draw.replacement',self.resolve(s).kind);s=self.replay(s,load=True)
        options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        dredge=next(row for row in options if 'Dredge' in row['label'])
        command={'action_id':'choose','choice':dredge['id']};self.reject(s,command,'D')
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('semantic.choice',self.resolve(s).kind)
        self.assertEqual(before+2,len(s.state.players['A'].draw_history))
        self.assertTrue(all(s.state.cards[oid].zone=='graveyard' for oid in top[:2]))
        self.assertTrue(all(s.state.cards[oid].zone=='hand' for oid in top[2:4]))
        for principal in ('pilot:B','pilot:C','pilot:D','spectator'):
            packet=s.packet(principal,full=True);self.assertIsNone(packet['decision'])
            known=packet['state']['players']['A'].get('known_hand',[])
            if principal=='pilot:B':self.assertIn(dredger.ref,{row['id'] for row in known})
            for oid in top[2:4]:
                self.assertNotIn(oid,str(packet));self.assertNotIn(s.state.cards[oid].ref,str(packet))
        s=self.replay(s,load=True);self.choose_cards(s,[s.state.cards[dredger.object_id].ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(dredger.object_id,s.state.players['A'].zones['library'][0]);self.assertEqual(2,len(self.hand(s)))
        self.replay(s,load=True)

    def test_fresh_wrong_library_edge_mutation_is_killed_by_actual_amass(self):
        from quorune.semantic_choices import fixed_hand_putback as owned
        original=owned.FixedHandPutbackOrder
        def mutant(chooser,position,members):return original(chooser,'top' if position=='bottom' else 'bottom',members)
        with patch.object(owned,'FixedHandPutbackOrder',mutant):
            with self.assertRaises(AssertionError):self.test_actual_amass_draws_three_then_returns_an_old_current_hand_card_privately()

    def test_actual_putback_fault_after_library_arrival_rolls_back_choice_and_retries(self):
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s=self.start(932070);source=self.add(s.engine,'Amass the Components',zone='hand');self.closed(s,source)
        old=self.add(s.engine,'Shock',zone='hand',ref='putback-rollback-old')
        self.ready(s);self.cast(s,source);self.assertEqual('semantic.choice',self.resolve(s).kind)
        s=self.replay(s,load=True);before=authoritative_state_hash(s.state);original=ZoneTransitionOwner._commit_move;observed=[]
        def fault(owner,plan,departure,**kwargs):
            result=original(owner,plan,departure,**kwargs)
            if plan.card.object_id==old.object_id and plan.card.zone=='library':
                observed.append(plan.card.zone);raise GameRuleError('Injected fixed putback movement failure')
            return result
        with patch.object(ZoneTransitionOwner,'_commit_move',fault):
            result=s.act('pilot:A',{'action_id':'choose','cards':[old.ref]})
        self.assertFalse(result.ok);self.assertEqual(['library'],observed);self.assertEqual(before,authoritative_state_hash(s.state))
        s=self.replay(s,load=True);self.choose_cards(s,[old.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(old.object_id,s.state.players['A'].zones['library'][0]);self.replay(s,load=True)

    def test_actual_registered_putback_payload_requires_fine_scope_without_coverage(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.fixed_hand_putback_model import FIXED_HAND_PUTBACK_CAPABILITY as cap
        s=self.start(932080);source=self.add(s.engine,'Amass the Components',zone='hand');self.closed(s,source)
        program=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if any(e.get('op')=='fixed_hand_putback' for e in p.effects))
        deps=tuple(value for value in program.capability_dependencies if value!=cap)
        missing=replace(program,coverage=(),capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_actual_see_beyond_fault_after_shuffle_rolls_back_arrival_rng_and_choice_then_retries(self):
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s=self.start(932120);source=self.add(s.engine,'See Beyond',zone='hand');self.closed(s,source)
        old=self.add(s.engine,'Shock',zone='hand',ref='putback-shuffle-rollback')
        self.ready(s);self.cast(s,source);self.assertEqual('semantic.choice',self.resolve(s).kind)
        s=self.replay(s,load=True);before=authoritative_state_hash(s.state);original=ZoneTransitionOwner.shuffle_library;observed=[]
        def fault(owner,seat,**kwargs):
            value=original(owner,seat,**kwargs);observed.append(seat)
            self.assertEqual('library',owner.state.cards[old.object_id].zone)
            raise GameRuleError('Injected fixed putback post-shuffle failure')
        with patch.object(ZoneTransitionOwner,'shuffle_library',fault):
            result=s.act('pilot:A',{'action_id':'choose','cards':[old.ref]})
        self.assertFalse(result.ok);self.assertEqual(['A'],observed);self.assertEqual(before,authoritative_state_hash(s.state))
        s=self.replay(s,load=True);self.choose_cards(s,[old.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual('library',s.state.cards[old.object_id].zone);self.replay(s,load=True)
