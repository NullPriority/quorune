from __future__ import annotations

"""Pinned CR400/608/303: exact private candidates and selected incarnation."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses


class HandEntryExpansionCompilerTests(unittest.TestCase):
    def test_hand_entry_artifact_historic_and_draw_prefix_preserve_queries(self):
        for text in ('You may put an artifact card from your hand onto the battlefield.',
            '{4}, {T}: You may put a historic permanent card from your hand onto the battlefield.',
            'Draw a card, then you may put a land card from your hand onto the battlefield tapped.'):
            card=replace(query_record(text),type_line='Artifact' if text.startswith('{') else 'Sorcery')
            ir=compile_oracle_card(card,capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,text)
            effect=ir.faces[0].nodes[0].effects[-1]
            self.assertEqual('put_card_from_hand',effect['op'])
            if 'historic' in text:self.assertEqual('hand_entry_query_union',effect['query']['kind'])
        from quorune.hand_entry_queries import decode_hand_entry_queries,historic_hand_entry_query_descriptor,hand_entry_matches
        from quorune.object_query import ObjectQueryResult
        queries=decode_hand_entry_queries(historic_hand_entry_query_descriptor())
        for index,types,subtypes,supertypes,expected in [
            (1,('artifact','land'),(),(),True),(2,('enchantment',),('saga',),(),True),
            (3,('enchantment',),('aura',),('legendary',),True),(4,('sorcery',),(),('legendary',),False),
            (5,('creature',),('human',),(),False)]:
            row=ObjectQueryResult(object_id=str(index),ref=str(index),printed_name='Historic witness',owner='A',controller='A',
                zone='hand',types=types,subtypes=subtypes,supertypes=supertypes)
            self.assertEqual(expected,hand_entry_matches(row,queries))

    def test_committed_cave_life_compiles_with_independent_life_and_subtype_authority(self):
        from quorune.rules.hand_entry_followup import (
            HAND_ENTRY_SUBTYPE_LIFE_VERSION, HandEntrySubtypeLife, decode_hand_entry_followup,
        )

        body = (
            "Draw a card, then you may put a land card from your hand onto the battlefield. "
            "If you put a Cave onto the battlefield this way, you gain four life."
        )
        registry = load_default_capability_registry()
        record = replace(query_record(body), type_line="Sorcery")
        ir = compile_oracle_card(record, capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        draw, entry = node.effects
        self.assertEqual(("draw", "put_card_from_hand"), (draw["op"], entry["op"]))
        self.assertEqual(HAND_ENTRY_SUBTYPE_LIFE_VERSION, entry["schema_version"])
        self.assertEqual(HandEntrySubtypeLife("cave", 4), decode_hand_entry_followup(entry))
        self.assertTrue({"life.change.effect", "state_query.permanent.public_state_predicate",
                         "zone.move.fixed_private_hand_choice"}.issubset(node.capability_dependencies))
        with self.assertRaises(ValueError):
            decode_hand_entry_followup({**entry, "followup": {"land_subtype": "cave", "amount": True}})
        with self.assertRaises(ValueError):
            decode_hand_entry_followup({**entry, "tapped": True})
        for unsupported in (body.replace("Cave", "Gate"), body.replace("four", "X"),
                            body.replace("battlefield. If", "battlefield tapped. If")):
            bad = compile_oracle_card(replace(record, oracle_text=unsupported), capability_registry=registry)
            self.assertNotEqual("exact", bad.status, unsupported)

    def test_hand_entry_disjunction_schema_and_unsupported_tails_stay_closed(self):
        from quorune.hand_entry_queries import historic_hand_entry_query_descriptor,decode_hand_entry_queries
        descriptor=historic_hand_entry_query_descriptor()
        for change in ({'schema_version':True},{'extra':0},{'alternatives':descriptor['alternatives'][:2]}):
            with self.assertRaises(ValueError):decode_hand_entry_queries({**descriptor,**change})
        for text in ('Each player may put a creature card from their hand onto the battlefield.',
            'You may put a historic card from your hand onto the battlefield.',
            'You may put a creature card from your hand onto the battlefield with a +1/+1 counter on it.',
            'Draw a card, then you may put a land card from your hand onto the battlefield. You win the game.'):
            ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=load_default_capability_registry())
            self.assertNotEqual('exact',ir.status,text)


class HandEntryExpansionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'hand.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/hand-entry-expansion-cards.json',ROOT/'tests/fixtures/riot-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Hand entry witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def test_actual_gateway_offers_only_historic_permanents_and_enters_selected_artifact(self):
        session=self.session(287001);engine=session.engine
        source=self.add(engine,'Thran Temporal Gateway')
        eligible=self.add(engine,'Generic Hand Artifact',zone='hand')
        noneligible=self.add(engine,'Generic Hand Legendary Sorcery',zone='hand')
        private=self.add(engine,'Generic Bound Growth',seat='C',zone='hand',ref='private-entry-c')
        action=self.ready(session,source,{'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);decision=self.resolve(session);self.assertEqual('semantic.choice',decision.kind)
        packet=session.packet('pilot:A',full=True)['decision'];self.assertIn(eligible.ref,str(packet));self.assertNotIn(noneligible.ref,str(packet))
        self.assertNotIn(eligible.ref,str(session.packet('pilot:B',full=True)))
        self.assertNotIn(private.ref,str(packet))
        before=authoritative_state_hash(session.state)
        wrong=session.act('pilot:B',{'action_id':'choose','card':eligible.ref});self.assertFalse(wrong.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        wrong=session.act('pilot:A',{'action_id':'choose','card':noneligible.ref});self.assertFalse(wrong.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','card':eligible.ref});self.assertTrue(result.ok,result.summary)
        self.resolve(session);self.assertEqual('battlefield',session.state.cards[eligible.object_id].zone)
        self.assertFalse(session.state.cards[eligible.object_id].tapped)
        self.replay(session,load=True)

    def test_actual_copper_source_sacrifice_keeps_private_entry_ability(self):
        session=self.session(287002);engine=session.engine
        source=self.add(engine,'Copper Gnomes');candidate=self.add(engine,'Generic Hand Artifact',zone='hand')
        action=self.ready(session,source,{'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.resolve(session);result=session.act('pilot:A',{'action_id':'choose','card':candidate.ref})
        self.assertTrue(result.ok,result.summary);self.resolve(session)
        self.assertEqual('battlefield',session.state.cards[candidate.object_id].zone);self.replay(session,load=True)

    def test_hand_entry_aura_attachment_pending_save_load_and_replay(self):
        session=self.session(287004);engine=session.engine
        source=self.add(engine,'Thran Temporal Gateway')
        body=self.add(engine,'Generic Bound Body',seat='B')
        aura=self.add(engine,'Generic Hand Legendary Aura',zone='hand')
        action=self.ready(session,source,{'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        result=session.act('pilot:A',{'action_id':'choose','card':aura.ref});self.assertTrue(result.ok,result.summary)
        self.assertEqual('aura.entry',session.state.pending_decision.kind)
        self.assertEqual('hand',session.state.cards[aura.object_id].zone)
        resumed=self.replay(session,load=True);before=authoritative_state_hash(resumed.state)
        wrong=resumed.act('pilot:B',{'action_id':'choose','target':body.ref})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(resumed.state))
        result=resumed.act('pilot:A',{'action_id':'choose','target':body.ref});self.assertTrue(result.ok,result.summary)
        self.resolve(resumed)
        self.assertEqual('battlefield',resumed.state.cards[aura.object_id].zone)
        self.assertEqual(body.object_id,resumed.state.cards[aura.object_id].attached_to)
        self.assertEqual(3,resumed.engine._numeric_stat(body.object_id,'power'))
        self.replay(resumed,load=True)

    def test_hand_entry_stale_new_incarnation_is_rejected_before_mutation(self):
        session=self.session(287003);engine=session.engine
        source=self.add(engine,'Thran Temporal Gateway');candidate=self.add(engine,'Generic Hand Artifact',zone='hand')
        action=self.ready(session,source,{'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        engine.move_card(candidate.object_id,'graveyard',log=False);engine.move_card(candidate.object_id,'hand',log=False)
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'action_id':'choose','card':candidate.ref})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))

    def test_hand_entry_predicate_mutant_is_killed(self):
        from quorune.semantic_choices import object_selection as owner
        with patch.object(owner,'hand_entry_matches',return_value=True):
            with self.assertRaises(AssertionError):self.test_actual_gateway_offers_only_historic_permanents_and_enters_selected_artifact()

    def test_hand_entry_malformed_identity_map_is_rejected_and_decline_is_legal(self):
        session=self.session(287010);engine=session.engine
        source=self.add(engine,'Thran Temporal Gateway')
        candidate=self.add(engine,'Generic Hand Artifact',zone='hand')
        action=self.ready(session,source,{'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        from quorune.semantic_choices.defaults import default_semantic_choice_registry
        from quorune.semantic_choices.model import SemanticChoiceError
        from quorune.replacement.immutable import FrozenMap
        handler,continuation=default_semantic_choice_registry().decode_continuation(session.state.pending_decision.continuation)
        for identities in ([],{}, {candidate.ref:True}, {candidate.ref:candidate.logical_object_id,'extra':'identity'}):
            malformed=replace(continuation,effect=FrozenMap({**continuation.effect,'_legal_logical_ids':identities}))
            with self.assertRaises(SemanticChoiceError):handler.complete(malformed,{'card':candidate.ref},None)
        before_identity=candidate.logical_object_id
        result=session.act('pilot:A',{'action_id':'choose','card':''});self.assertTrue(result.ok,result.summary)
        self.resolve(session)
        self.assertEqual('hand',session.state.cards[candidate.object_id].zone)
        self.assertEqual(before_identity,session.state.cards[candidate.object_id].logical_object_id)
        self.replay(session,load=True)

    def test_hand_entry_aura_ignores_shroud_and_enforces_protection(self):
        session=self.session(287006);engine=session.engine
        source=self.add(engine,'Thran Temporal Gateway')
        body=self.add(engine,'Generic Hand Shroud Creature',seat='B')
        protected=self.add(engine,'Generic Hand Protected Creature',seat='C')
        aura=self.add(engine,'Generic Hand Legendary Aura',zone='hand')
        action=self.ready(session,source,{'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        result=session.act('pilot:A',{'action_id':'choose','card':aura.ref});self.assertTrue(result.ok,result.summary)
        self.assertEqual('aura.entry',session.state.pending_decision.kind)
        packet=session.packet('pilot:A',full=True)['decision']
        self.assertIn(body.ref,str(packet))
        self.assertNotIn(protected.ref,str(packet))
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:A',{'action_id':'choose','target':protected.ref})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',{'action_id':'choose','target':body.ref});self.assertTrue(result.ok,result.summary)
        self.resolve(session)
        self.assertEqual(body.object_id,session.state.cards[aura.object_id].attached_to)
        self.replay(session,load=True)

    def test_hand_entry_aura_with_no_recipient_stays_in_hand(self):
        session=self.session(287007);engine=session.engine
        source=self.add(engine,'Thran Temporal Gateway')
        aura=self.add(engine,'Generic Hand Legendary Aura',zone='hand')
        action=self.ready(session,source,{'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        result=session.act('pilot:A',{'action_id':'choose','card':aura.ref});self.assertTrue(result.ok,result.summary)
        self.resolve(session)
        self.assertEqual('hand',session.state.cards[aura.object_id].zone)
        self.assertIsNone(session.state.cards[aura.object_id].attached_to)
        self.replay(session,load=True)

    def test_hand_entry_selected_incarnation_is_sealed_through_aura_choice(self):
        session=self.session(287008);engine=session.engine
        source=self.add(engine,'Thran Temporal Gateway')
        body=self.add(engine,'Generic Bound Body',seat='B')
        aura=self.add(engine,'Generic Hand Legendary Aura',zone='hand')
        action=self.ready(session,source,{'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        result=session.act('pilot:A',{'action_id':'choose','card':aura.ref});self.assertTrue(result.ok,result.summary)
        self.assertEqual('aura.entry',session.state.pending_decision.kind)
        engine.move_card(aura.object_id,'graveyard',log=False);engine.move_card(aura.object_id,'hand',log=False)
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'action_id':'choose','target':body.ref})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))

    def test_hand_entry_actual_riot_choice_resumes_selected_move_and_replays(self):
        session=self.session(287009);engine=session.engine
        source=self.add(engine,'Quicksilver Amulet')
        candidate=self.add(engine,'Zhur-Taa Goblin',zone='hand')
        action=self.ready(session,source,{'C':4});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        result=session.act('pilot:A',{'action_id':'choose','card':candidate.ref});self.assertTrue(result.ok,result.summary)
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertEqual('hand',session.state.cards[candidate.object_id].zone)
        resumed=self.replay(session,load=True)
        packet=resumed.packet('pilot:A',full=True)['decision']
        option=next(row['id'] for row in packet['ctx']['options'] if not row['id'].startswith('decline:'))
        before=authoritative_state_hash(resumed.state)
        rejected=resumed.act('pilot:B',{'action_id':'choose','choices':{'replacement':option}})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(resumed.state))
        result=resumed.act('pilot:A',{'action_id':'choose','choices':{'replacement':option}});self.assertTrue(result.ok,result.summary)
        self.resolve(resumed)
        self.assertEqual('battlefield',resumed.state.cards[candidate.object_id].zone)
        self.assertEqual(1,resumed.state.cards[candidate.object_id].counters.get('+1/+1',0))
        self.replay(resumed,load=True)

    def test_actual_chulane_draws_once_before_optional_land_entry_and_replays(self):
        session=self.session(287005);engine=session.engine
        source=self.add(engine,'Chulane, Teller of Tales')
        spell=self.add(engine,'Generic Bound Body',zone='hand')
        for object_id in tuple(engine.state.players['A'].zones['hand']):
            if object_id!=spell.object_id:engine.move_card(object_id,'graveyard',log=False)
        library=engine.state.players['A'].zones['library'];drawn=engine.state.cards[library[-1]]
        before_library=len(library);before_plays=engine.state.players['A'].land_plays_remaining
        action=self.ready(session,spell,{'W':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);pending=self.resolve(session)
        self.assertEqual('semantic.choice',pending.kind)
        self.assertEqual(before_library-1,len(session.state.players['A'].zones['library']))
        self.assertEqual([drawn.object_id],session.state.players['A'].zones['hand'])
        resumed=self.replay(session,load=True)
        packet=resumed.packet('pilot:A',full=True)['decision'];self.assertIn(drawn.ref,str(packet))
        result=resumed.act('pilot:A',{'action_id':'choose','card':drawn.ref});self.assertTrue(result.ok,result.summary)
        self.resolve(resumed)
        self.assertEqual('battlefield',resumed.state.cards[drawn.object_id].zone)
        self.assertEqual(before_library-1,len(resumed.state.players['A'].zones['library']))
        self.assertEqual(before_plays,resumed.state.players['A'].land_plays_remaining)
        self.replay(resumed,load=True)


class SpelunkingRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = witnesses.BoundEffectProgramRuntimeTests.session
    add = witnesses.BoundEffectProgramRuntimeTests.add
    ready = witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint = witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve = witnesses.BoundEffectProgramRuntimeTests.resolve
    replay = witnesses.BoundEffectProgramRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "spelunking.sqlite3"
        fixtures = ("bound-effect-program-cards.json", "external-entry-tapped-cards.json",
                    "discover-cards.json", "opponent-life-gain-to-loss-cards.json", "fixed-basic-type-setting-cards.json")
        build_fixture_database([ROOT / "tests" / "fixtures" / name for name in fixtures], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Spelunking witness", [DeckEntry("Generic Bound Commander", 1, "commander"),
                                  DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def act(self, session, action, **fields):
        result = session.act("pilot:A", {"action_id": action, **fields})
        self.assertTrue(result.ok, result.summary)

    def replacement_options(self, session):
        decision = session.state.pending_decision
        self.assertEqual("replacement.order", decision.kind)
        self.assertEqual(["A"], decision.actors)
        self.assertIsNone(session.packet("pilot:B", full=True)["decision"])
        return session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]

    def cast_spelunking(self, session, source):
        action = self.ready(session, source, {"C": 2, "G": 1})
        self.checkpoint(session)
        self.act(session, action["id"], pay="auto")
        pending = self.resolve(session)
        self.assertEqual("semantic.choice", pending.kind)
        packet = session.packet("pilot:A", full=True)["decision"]
        self.assertGreater(len(packet["ctx"]["objects"]), 1)
        self.assertIsNone(session.packet("pilot:B", full=True)["decision"])

    def test_cave_life_requires_an_arrival_with_the_effective_cave_subtype(self):
        for decline in (True, False):
            with self.subTest(decline=decline):
                session = self.session(287041 + decline)
                engine = session.engine
                spell = self.add(engine, "Spelunking", zone="hand")
                cave = self.add(engine, "Hidden Nursery", zone="hand")
                self.add(engine, "Blood Moon", seat="B")
                before = session.state.players["A"].life
                self.cast_spelunking(session, spell)
                if decline:
                    self.act(session, "choose")
                else:
                    self.act(session, "choose", card=cave.ref)
                self.resolve(session)
                actual = session.state.cards[cave.object_id]
                self.assertEqual("hand" if decline else "battlefield", actual.zone)
                if not decline:
                    types = engine._effective_card_data(actual)["type_line"].casefold()
                    self.assertIn("mountain", types)
                    self.assertNotIn("cave", types)
                self.assertEqual(before, session.state.players["A"].life)

    def test_copy_cave_then_life_replacement_replays_each_pending_choice(self):
        session = self.session(287040)
        engine = session.engine
        spell = self.add(engine, "Spelunking", zone="hand")
        vesuva = self.add(engine, "Vesuva", zone="hand")
        cave = self.add(engine, "Hidden Nursery", seat="B")
        doubling = self.add(engine, "Boon Reflection")
        self.add(engine, "Tainted Remedy", seat="B")
        before_life = session.state.players["A"].life
        before_draws = len(session.state.players["A"].draw_history)
        before_land_plays = session.state.players["A"].land_plays_remaining
        before_move = vesuva.zone_change_counter

        self.cast_spelunking(session, spell)
        session = self.replay(session, load=True)
        self.act(session, "choose", card=vesuva.ref)
        self.replacement_options(session)
        session = self.replay(session, load=True)
        copied = False
        for _ in range(4):
            options = self.replacement_options(session)
            copy_cave = next((option for option in options if cave.ref in option["id"]), None)
            intrinsic = next((option for option in options if option.get("source") == vesuva.ref
                              and not option.get("decline")), None)
            if copy_cave is not None:
                self.act(session, "choose", replacement=copy_cave["id"])
                copied = True
            elif not copied and intrinsic is not None:
                self.act(session, "choose", replacement=intrinsic["id"])
            else:
                break
        self.assertTrue(copied)
        moved = session.state.cards[vesuva.object_id]
        self.assertEqual(("battlefield", before_move + 1), (moved.zone, moved.zone_change_counter))
        self.assertIn("cave", session.engine._effective_card_data(moved)["type_line"].casefold())
        self.assertEqual(before_life, session.state.players["A"].life)
        self.assertTrue(any(row.get("source") == doubling.ref for row in self.replacement_options(session)))
        session = self.replay(session, load=True)
        double = next(row for row in self.replacement_options(session) if row.get("source") == doubling.ref)
        self.act(session, "choose", replacement=double["id"])
        self.resolve(session)
        self.assertEqual(before_life - 8, session.state.players["A"].life)
        self.assertEqual(before_move + 1, session.state.cards[vesuva.object_id].zone_change_counter)
        self.assertEqual(before_draws + 1, len(session.state.players["A"].draw_history))
        self.assertEqual(before_land_plays, session.state.players["A"].land_plays_remaining)
        self.replay(session)


if __name__=='__main__':unittest.main()
