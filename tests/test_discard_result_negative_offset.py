"""Pinned CR107.1b/121.2c/701.9: each committed discard count minus one."""
from dataclasses import replace
from types import SimpleNamespace
import json,unittest
from unittest.mock import patch
from quorune.discard_result_model import (
    IntentResultPlayerDraws,ReducedDiscardResultDraws,PlayerDiscardCounts,validate_discard_draw,
)
from quorune.hand_library_redraw_model import HandLibraryResultDraws
from quorune.compiler.discard_result_templates import discard_result_draw_template
from quorune.semantic_choices.discard_results import DiscardResultDrawHandler
from quorune.semantic_choices.model import AutoContinue
from quorune.replacement.immutable import FrozenMap

TEXT='Each player discards all the cards in their hand, then draws that many cards minus one.'


class ReducedDiscardResultModelCompilerTests(unittest.TestCase):
    def test_new_wire_and_binding_clamp_each_own_count_in_apnap_order(self):
        compiled=discard_result_draw_template(TEXT);self.assertEqual('whole-hand-discard-result-draw-v2',compiled[0])
        effect=compiled[1][0];self.assertEqual((2,'own',-1),(effect['schema_version'],effect['reduction'],effect['offset']))
        validate_discard_draw(json.loads(json.dumps(effect)))
        result=PlayerDiscardCounts((('B',3),('C',0),('D',1),('A',5)))
        binding=ReducedDiscardResultDraws(0,'own',-1)
        effects=AutoContinue('Actual committed discards',player_draws_from_result=binding).resolved_effects({0:result})
        self.assertEqual(['B','C','D','A'],[e['player'] for e in effects])
        self.assertEqual([2,0,0,4],[e['count'] for e in effects])
        self.assertTrue(all(e['private'] for e in effects))

    def test_old_wire_bindings_and_hand_library_offsets_do_not_accept_new_semantics(self):
        old=discard_result_draw_template('Discard your hand, then draw that many cards plus one.')[1][0]
        self.assertEqual((1,1),(old['schema_version'],old['offset']));validate_discard_draw(old)
        for change in ({'offset':-1},{'schema_version':2},{'schema_version':True}):
            with self.assertRaises(ValueError):validate_discard_draw({**old,**change})
        for owner in (IntentResultPlayerDraws,HandLibraryResultDraws):
            with self.assertRaises(ValueError):owner(0,'own',-1)
        self.assertEqual([2],[e['count'] for e in IntentResultPlayerDraws(0,'own',1).resolved_effects(PlayerDiscardCounts((('A',1),)))])

    def test_unrepresented_quantities_subjects_filters_and_riders_stay_closed(self):
        for text in (TEXT.replace('minus one','minus two'),TEXT.replace('Each player','Each opponent'),
                TEXT.replace('all the cards','all the nonland cards'),TEXT+' Draw a card.',
                'Each player discards their hand, then draws cards equal to the greatest number of cards a player discarded this way minus one.'):
            self.assertIsNone(discard_result_draw_template(text),text)
        raw=discard_result_draw_template(TEXT)[1][0]
        for change in ({'reduction':'greatest'},{'offset':0},{'offset':True},{'schema_version':3},{'unknown':True}):
            with self.assertRaises(ValueError):validate_discard_draw({**raw,**change})
        with self.assertRaises(ValueError):ReducedDiscardResultDraws(0,'greatest',-1)

    def test_choice_uses_sealed_committed_result_and_never_queries_followup_hand_size(self):
        hand={'B':[{'object_id':'b','logical_object_id':'b@1','ref':'private-b'}],'A':[]}
        query=SimpleNamespace(apnap_order=('B','A'),active_seats=('A','B'),seats=('A','B'),discard_hand_inputs=FrozenMap(hand))
        effect={**discard_result_draw_template(TEXT)[1][0],'actor':'A'}
        prepared=DiscardResultDrawHandler().prepare(effect,SimpleNamespace(actor='A',query=query,stack_label='Reduced discard'))
        self.assertIsNone(prepared.request);self.assertEqual(('B','A'),prepared.preparation_intents[0].players)
        self.assertIsInstance(prepared.auto_continue.player_draws_from_result,ReducedDiscardResultDraws)
        draws=prepared.auto_continue.resolved_effects({0:PlayerDiscardCounts((('B',1),('A',0)))})
        self.assertEqual([0,0],[e['count'] for e in draws])


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.binding import bind_semantic_program_runtime
from scripts.build_test_database import build_fixture_database

FIXTURE=ROOT/'tests/fixtures/discard-result-negative-offset-cards.json'


class ReducedDiscardResultNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        basic=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        basic['cards']=[c for c in basic['cards'] if c['name']=='Generic Bound Plains'];basic['rulings']=[]
        path=folder/'basic.json';path.write_text(json.dumps(basic),encoding='utf-8')
        build_fixture_database([path,FIXTURE],folder/'reduced-discard.sqlite3')
        cls.db=CardDatabase(folder/'reduced-discard.sqlite3');cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Reduced discard',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def hands(self,s,sizes):
        for seat in 'ABCD':
            for oid in tuple(s.state.players[seat].zones['hand']):s.engine.move_card(oid,'library',log=False)
            for index in range(sizes.get(seat,0)):
                self.add(s.engine,'Divination',seat=seat,zone='hand',ref=f'reduced-private-{seat}-{index}')

    def private_replay(self,s):
        for viewer in 'ABCD':
            packet=json.dumps(s.packet('pilot:'+viewer,full=True))
            for owner in 'ABCD':
                if owner==viewer:continue
                for oid in s.state.players[owner].zones['hand']:
                    self.assertNotIn(s.state.cards[oid].ref,packet)
        self.replay(s,load=True)

    def test_complete_original_closes_normally_with_old_result_controls_and_raw_dependency(self):
        s=self.session(715000)
        for name in ('Dark Deal','Windfall','Tolarian Winds','Incendiary Command'):
            with self.subTest(name=name):
                card=self.add(s.engine,name,zone='hand');self.assert_closed(s,card)
                ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
        source=next(c for c in s.state.cards.values() if c.printed_name=='Dark Deal')
        spell=next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.event=='resolve')
        deps=tuple(c for c in spell.capability_dependencies if c!='zone.discard.whole_hand_result')
        changed=replace(spell,coverage=(),capability_dependencies=deps,
            capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        binding=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(binding['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:zone.discard.whole_hand_result',binding['blockers'])

    def test_actual_dark_deal_discards_all_before_clamped_own_draws_in_apnap_order(self):
        s=self.session(715010);self.hands(s,{'A':3,'B':0,'C':1,'D':5})
        source=self.add(s.engine,'Dark Deal',zone='hand');self.assert_closed(s,source)
        original={seat:tuple(oid for oid in s.state.players[seat].zones['hand'] if oid!=source.object_id) for seat in 'ABCD'}
        self.prepare(s,mana={'C':2,'B':1});self.checkpoint(s);self.cast(s,'A',source)
        from quorune.drawing import transaction
        original_draw=transaction._record_draw;observed=[]
        def observe(*args,**kwargs):
            self.assertTrue(all(s.state.cards[oid].zone=='graveyard' for members in original.values() for oid in members))
            observed.append(args[1].resolution.player)
            return original_draw(*args,**kwargs)
        with patch.object(transaction,'_record_draw',side_effect=observe):self.assertIsNone(self.resolve(s))
        self.assertEqual([2,0,0,4],[len(s.state.players[p].zones['hand']) for p in 'ABCD'])
        self.assertEqual(list('AADDDD'),observed)
        self.assertEqual(0,sum(s.state.players['A'].mana_pool.values()))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone);self.private_replay(s)

    def test_actual_response_changes_current_discard_count_without_counting_cast_cards(self):
        s=self.session(715020);self.hands(s,{'A':2,'B':1})
        source=self.add(s.engine,'Dark Deal',zone='hand');bolt=self.add(s.engine,'Lightning Bolt',zone='hand')
        self.prepare(s,mana={'C':2,'B':1,'R':1});self.checkpoint(s)
        self.cast_for(s,'A',source,reserve={'R':1});self.cast(s,'A',bolt,['B'])
        self.assertIsNone(self.resolve(s));self.assertEqual(37,s.state.players['B'].life)
        self.assertEqual([1,0,0,0],[len(s.state.players[p].zones['hand']) for p in 'ABCD'])
        discards=s.engine._current_turn_history('card_discarded')
        self.assertEqual(2,len([e for e in discards if e.actor=='A']))
        self.assertNotIn(bolt.logical_object_id,[e.object_incarnation for e in discards])
        self.assertNotIn(source.logical_object_id,[e.object_incarnation for e in discards]);self.private_replay(s)

    def test_actual_redirected_discard_counts_exile_and_resumes_private_replacement_once(self):
        from quorune.record import authoritative_state_hash
        s=self.session(715030);self.hands(s,{'A':2,'B':3})
        first=self.add(s.engine,'Rest in Peace');second=self.add(s.engine,'Rest in Peace',ref='reduced-second-redirect')
        source=self.add(s.engine,'Dark Deal',zone='hand')
        for card in (source,first,second):self.assert_closed(s,card)
        originals=tuple(s.state.players['B'].zones['hand'])
        self.prepare(s,mana={'C':2,'B':1});self.checkpoint(s);self.cast(s,'A',source)
        self.assertEqual('replacement.order',self.resolve(s).kind)
        for viewer in 'ACD':
            body=json.dumps(s.packet('pilot:'+viewer,full=True))
            for oid in originals:self.assertNotIn(s.state.cards[oid].ref,body)
        s=self.replay(s,load=True)
        while s.state.pending_decision.kind=='replacement.order':
            principal=s.pending_principals()[0]
            options=s.packet(principal,full=True)['decision']['ctx']['options']
            command={'action_id':'choose','replacement':options[0]['id']}
            before=authoritative_state_hash(s.state)
            foreign='pilot:D' if principal!='pilot:D' else 'pilot:C'
            self.assertFalse(s.act(foreign,command).ok);self.assertEqual(before,authoritative_state_hash(s.state))
            accepted=s.act(principal,command);self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve(s))
        self.assertTrue(all(s.state.cards[oid].zone=='exile' for oid in originals))
        self.assertEqual([1,2,0,0],[len(s.state.players[p].zones['hand']) for p in 'ABCD'])
        self.assertEqual(3,len([e for e in s.engine._current_turn_history('card_discarded') if e.actor=='B']))
        self.private_replay(s)

    def test_actual_draw_limit_keeps_discard_counts_and_other_players_draws(self):
        s=self.session(715040);self.hands(s,{'A':3,'B':3,'C':1,'D':3})
        limiter=self.add(s.engine,'Narset, Parter of Veils',seat='B')
        source=self.add(s.engine,'Dark Deal',zone='hand');self.assert_closed(s,source);self.assert_closed(s,limiter)
        self.prepare(s,mana={'C':2,'B':1});self.checkpoint(s);self.cast(s,'A',source)
        self.assertIsNone(self.resolve(s))
        self.assertEqual([1,2,0,1],[len(s.state.players[p].zones['hand']) for p in 'ABCD']);self.private_replay(s)

    def test_actual_dredge_reload_retains_reduced_counts_and_does_not_repeat_discard(self):
        s=self.session(715050);self.hands(s,{'A':3,'B':2})
        dredger=self.add(s.engine,'Dakmor Salvage',zone='graveyard')
        source=self.add(s.engine,'Dark Deal',zone='hand');self.assert_closed(s,source);self.assert_closed(s,dredger)
        self.prepare(s,mana={'C':2,'B':1});self.checkpoint(s);self.cast(s,'A',source)
        self.assertEqual('draw.replacement',self.resolve(s).kind)
        self.assertEqual(5,len(s.engine._current_turn_history('card_discarded')))
        s=self.replay(s,load=True);options=s.packet('pilot:A',full=True)['decision']['ctx']['options']
        dredge=next(o for o in options if 'Dredge' in o['label'])
        command={'action_id':'choose','choice':dredge['id']}
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s))
        self.assertEqual('hand',s.state.cards[dredger.object_id].zone)
        self.assertEqual([2,1,0,0],[len(s.state.players[p].zones['hand']) for p in 'ABCD'])
        self.assertEqual(5,len(s.engine._current_turn_history('card_discarded')))
        # The dredger was public before returning, while the other draw stays private.
        for viewer in 'BCD':
            body=json.dumps(s.packet('pilot:'+viewer,full=True))
            for oid in s.state.players['A'].zones['hand']:
                if oid!=dredger.object_id:self.assertNotIn(s.state.cards[oid].ref,body)
        self.replay(s,load=True)

    def test_actual_post_discard_and_draw_faults_roll_back_then_retry(self):
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        import quorune.semantic_runtime.executor as executor
        from quorune.drawing import transaction
        for index,phase in enumerate(('discard','draw')):
            with self.subTest(phase=phase):
                s=self.session(715060+index);self.hands(s,{'A':3,'B':2})
                source=self.add(s.engine,'Dark Deal',zone='hand');self.assert_closed(s,source)
                originals=tuple(oid for p in 'ABCD' for oid in s.state.players[p].zones['hand'] if oid!=source.object_id)
                self.prepare(s,mana={'C':2,'B':1});self.checkpoint(s);self.cast(s,'A',source)
                module,name=(executor,'execute_discard_result') if phase=='discard' else (transaction,'_record_draw')
                original=getattr(module,name);observed=[];failed=None
                def fault(*args,**kwargs):
                    result=original(*args,**kwargs)
                    observed.append((all(s.state.cards[oid].zone=='graveyard' for oid in originals),
                        sum(len(s.state.players[p].zones['hand']) for p in 'ABCD')))
                    raise GameRuleError('Injected committed discard/draw failure')
                with patch.object(module,name,side_effect=fault):
                    for _ in range(8):
                        principal=s.pending_principals()[0];before=authoritative_state_hash(s.state)
                        result=s.act(principal,{'action_id':'pass'})
                        if not result.ok:
                            self.assertEqual(before,authoritative_state_hash(s.state));failed=principal;break
                self.assertEqual([(True,0 if phase=='discard' else 1)],observed);self.assertIsNotNone(failed)
                self.assertTrue(all(s.state.cards[oid].zone=='hand' for oid in originals))
                s=self.replay(s,load=True);accepted=s.act(failed,{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
                self.assertIsNone(self.resolve(s));self.assertEqual([2,1,0,0],[len(s.state.players[p].zones['hand']) for p in 'ABCD'])
                self.private_replay(s)

    def test_fresh_native_offset_mutant_is_killed_after_actual_complete_cast(self):
        observed=[]
        def unreduced(binding,result):
            observed.append(result.counts)
            return IntentResultPlayerDraws(binding.intent_index,'own',0).resolved_effects(result)
        with patch.object(ReducedDiscardResultDraws,'resolved_effects',unreduced):
            with self.assertRaises(AssertionError):
                self.test_actual_dark_deal_discards_all_before_clamped_own_draws_in_apnap_order()
        self.assertEqual([(('A',3),('B',0),('C',1),('D',5))],observed)

    def test_syphon_mind_counts_only_committed_opponents_with_empty_and_redirected_hands(self):
        from quorune.record import authoritative_state_hash

        for redirect in (False, True):
            with self.subTest(redirect=redirect):
                game = self.session(715110 + int(redirect))
                self.hands(game, {"A": 1, "B": 2, "C": 0, "D": 1})
                source = self.add(game.engine, "Syphon Mind", zone="hand")
                if redirect:
                    for seat in "BD":
                        self.assert_closed(game, self.add(game.engine, "Rest in Peace", seat=seat,
                                                         ref=f"syphon-redirect-{seat}"))
                self.assert_closed(game, source)
                incoming = {seat: [game.state.cards[oid]
                                   for oid in game.state.players[seat].zones["hand"] if oid != source.object_id]
                            for seat in "ABCD"}
                self.prepare(game, mana={"C": 3, "B": 1})
                self.checkpoint(game)
                self.cast(game, "A", source)
                self.assertEqual("choice.apnap", self.resolve(game).kind)
                self.assertEqual(["pilot:B"], game.pending_principals())
                self.assertIsNone(game.packet("pilot:A", full=True)["decision"])
                before = authoritative_state_hash(game.state)
                for actor, card in (("B", incoming["D"][0]), ("C", incoming["B"][1])):
                    rejected = game.act("pilot:" + actor, {"action_id": "choose", "cards": [card.ref]})
                    self.assertFalse(rejected.ok, rejected.summary)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                picked = game.act("pilot:B", {"action_id": "choose", "cards": [incoming["B"][1].ref]})
                self.assertTrue(picked.ok, picked.summary)
                self.assertEqual(["pilot:D"], game.pending_principals())
                self.assertEqual("hand", game.state.cards[incoming["B"][1].object_id].zone)
                self.assertNotIn(incoming["B"][1].ref, json.dumps(game.packet("pilot:D", full=True)))
                game = self.replay(game, load=True)
                picked = game.act("pilot:D", {"action_id": "choose", "cards": [incoming["D"][0].ref]})
                self.assertTrue(picked.ok, picked.summary)
                if redirect:
                    self.assertEqual("replacement.order", game.state.pending_decision.kind)
                    self.assertEqual(1, len(game.state.players["A"].zones["hand"]))
                    game = self.replay(game, load=True)
                    for _ in range(4):
                        if game.state.pending_decision.kind != "replacement.order":
                            break
                        principal = game.pending_principals()[0]
                        option = game.packet(principal, full=True)["decision"]["ctx"]["options"][0]
                        answer = game.act(principal, {"action_id": "choose", "replacement": option["id"]})
                        self.assertTrue(answer.ok, answer.summary)
                self.assertIsNone(self.resolve(game))
                destination = "exile" if redirect else "graveyard"
                self.assertEqual(destination, game.state.cards[incoming["B"][1].object_id].zone)
                self.assertEqual(destination, game.state.cards[incoming["D"][0].object_id].zone)
                self.assertEqual("hand", game.state.cards[incoming["B"][0].object_id].zone)
                self.assertEqual([3, 1, 0, 0], [len(game.state.players[seat].zones["hand"]) for seat in "ABCD"])
                self.private_replay(game)

    def test_syphon_mind_with_all_empty_opponent_hands_draws_zero(self):
        game = self.session(715117)
        self.hands(game, {"A": 1})
        source = self.add(game.engine, "Syphon Mind", zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 3, "B": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([1, 0, 0, 0], [len(game.state.players[seat].zones["hand"]) for seat in "ABCD"])
        self.assertFalse(tuple(game.engine._current_turn_history("card_discarded")))
        self.replay(game, load=True)


class SelectedOpponentDiscardDrawCompilerTests(unittest.TestCase):
    def test_selected_discard_draw_requires_the_complete_printed_pair(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.selected_discard_draw import selected_discard_draw_choice
        from test_qualified_zone_event_queries import query_record

        text = "Each other player discards a card. You draw a card for each card discarded this way."

        def compile_text(text):
            return compile_oracle_card(replace(query_record(text), type_line="Sorcery"),
                                       capability_registry=load_default_capability_registry())

        def witness():
            ir = compile_text(text)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(selected_discard_draw_choice(), node.effects[0])
            self.assertIsNone(node.target_schema)
            self.assertEqual((0, len(text)), (node.span.start, node.span.end))

        witness()
        with patch("quorune.compiler.discard_result_templates.selected_opponent_discard_draw_template",
                   return_value=None):
            with self.assertRaises(AssertionError):
                witness()
        for invalid in (
            text.replace("Each other player", "Each player"),
            text.replace("discards a card", "may discard a card"),
            text.replace("discards a card", "discards two cards"),
            text.replace("each card discarded this way", "each opponent"),
            text + " Gain 1 life.",
        ):
            self.assertNotEqual("exact", compile_text(invalid).status)
