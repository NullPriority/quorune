import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_vanishing_rules as upkeep_witness
import test_discard_effect_origin as discard_witness
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

from quorune.compiler.draw_templates import static_draw_result_handler

from quorune.drawing import DrawEventRequest, QueuedDraw, prepare_draw_event, validate_prepared_draw

from quorune.replacement import (
    DiscardInsteadOfDraw,
    ReplacementClass,
    ReplacementEffect,
    operation_from_dict,
    operation_to_dict,
)
from quorune.replacement.operations import ReplacementOperationError


class DrawDiscardReplacementModelTests(unittest.TestCase):
    def test_library_choice_draw_requires_exact_optional_controller_definition(self):
        from quorune.replacement import application
        from quorune.replacement.operations import ReplaceDrawWithLibraryChoice
        from quorune.semantic_runtime.context import SemanticNodeError
        from quorune.semantic_runtime.draw_replacements import (
            DrawReplacementSourceContext, LibraryChoiceDrawReplacementHandler,
        )

        text = (
            "If you would draw a card, you may instead choose land or nonland and reveal cards "
            "from the top of your library until you reveal a card of the chosen kind. "
            "Put that card into your hand and put all other cards revealed this way on the bottom "
            "of your library in any order."
        )
        _, descriptor, _ = static_draw_result_handler(text)
        handler = LibraryChoiceDrawReplacementHandler()
        source = DrawReplacementSourceContext("A-abundance", "source-1", 1, "A", "A")
        effect = handler.replacement_effect(descriptor, source)
        self.assertTrue(effect.optional)
        self.assertEqual((ReplaceDrawWithLibraryChoice(),), effect.operations)
        self.assertEqual(effect, ReplacementEffect.from_dict(effect.to_dict()))
        self.assertIsNone(static_draw_result_handler(text.replace("your library", "an opponent's library")))
        with self.assertRaises(SemanticNodeError):
            handler.validate({**descriptor, "condition": {"affected_player_relation": "any"}})
        with self.assertRaises(ReplacementOperationError):
            operation_from_dict({"op": "replace_draw_with_library_choice", "category": "land"})

        def witness():
            request = DrawEventRequest("draw:replacement-fixture", "A", 0, reason="fixture", private=True)
            prepared = prepare_draw_event(
                request, apnap_order=("A", "B"), effects=(effect,), selections=(effect.effect_id,),
            )
            self.assertEqual("library_choice", prepared.resolution.kind)
            self.assertFalse(prepared.event.payload["is_draw"])
            self.assertEqual(effect.effect_id, prepared.resolution.library_choice_effect_id)
            validate_prepared_draw(prepared, apnap_order=("A", "B"))

        witness()
        original = application._apply_draw_operation

        def drop_library_replacement(event, payload, children, operation, *, effect_id):
            if isinstance(operation, ReplaceDrawWithLibraryChoice):
                return
            return original(event, payload, children, operation, effect_id=effect_id)

        with patch.object(application, "_apply_draw_operation", side_effect=drop_library_replacement):
            with self.assertRaises(AssertionError):
                witness()

    def test_typed_discard_replacement_round_trips_as_one_closed_operation(self):
        operation = DiscardInsteadOfDraw()
        self.assertEqual({"op": "discard_instead_of_draw"}, operation_to_dict(operation))
        self.assertEqual(operation, operation_from_dict(operation_to_dict(operation)))
        source = ReplacementEffect(
            effect_id="discard:printed@3",
            source_id="printed@3",
            event_kind="draw",
            replacement_class=ReplacementClass.OTHER,
            conditions={"is_draw": {"eq": True}},
            operations=(operation,),
        )
        self.assertEqual(source, ReplacementEffect.from_dict(source.to_dict()))

    def test_replacement_operation_rejects_extra_choices_or_outcomes(self):
        for field, value in (("count", 2), ("chooser", "source_controller"), ("discarded", True)):
            with self.subTest(field=field), self.assertRaises(ReplacementOperationError):
                operation_from_dict({"op": "discard_instead_of_draw", field: value})

    def test_replacement_seals_the_result_draw_until_discard_is_committed(self):
        effect = ReplacementEffect(
            effect_id="discard:printed@3",
            source_id="printed@3",
            event_kind="draw",
            replacement_class=ReplacementClass.OTHER,
            conditions={"is_draw": {"eq": True}},
            operations=(DiscardInsteadOfDraw(),),
        )
        request = DrawEventRequest(
            "draw:conditional", "A", 3, reason="fixture", private=True,
            excluded_effect_ids=("ancestor",),
        )
        prepared = prepare_draw_event(request, apnap_order=("A", "B"), effects=(effect,))
        self.assertEqual("discard_or_mill", prepared.resolution.kind)
        self.assertFalse(prepared.event.payload["is_draw"])
        self.assertEqual((), prepared.resolution.result_draws)
        self.assertEqual(
            QueuedDraw("A", 1, "fixture", True, ("ancestor", effect.effect_id)),
            prepared.resolution.discard_followup,
        )
        validate_prepared_draw(prepared, apnap_order=("A", "B"))

    def test_compiler_accepts_only_the_sealed_discard_draw_or_mill_shape(self):
        text = (
            "If a player would draw a card except the first one they draw in each of their draw steps, "
            "that player discards a card instead. If the player discards a card this way, they draw a card. "
            "If the player doesn't discard a card this way, they mill a card."
        )
        _, descriptor, requirements = static_draw_result_handler(text)
        self.assertEqual("replacement.draw.discard_or_mill_after_first.v1", descriptor["handler_id"])
        self.assertEqual({"affected_player_relation": "any"}, descriptor["condition"])
        self.assertEqual({"operation": "discard_instead_of_draw"}, descriptor["modification"])
        self.assertEqual(
            ("zone.draw.discard_replacement_after_first", "zone.draw.result_generated_ordering",
             "zone.discard.typed_cause", "zone.mill.fixed"),
            requirements,
        )
        self.assertIsNone(static_draw_result_handler(text.replace("they mill a card", "they mill two cards")))
        self.assertIsNone(static_draw_result_handler(text.replace("If a player would", "If an opponent would")))


class DrawDiscardReplacementNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "discard-draw.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/draw-discard-replacement-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Draw discard test", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def _library_prefix(self, game, names, *, seat="A"):
        for object_id in tuple(game.state.players[seat].zones["library"]):
            game.engine.move_card(object_id, "exile", semantic_events=False, reason="Fixture library")
        return tuple(
            self.add(game.engine, name, seat=seat, zone="library", ref=f"{seat}-public-prefix-{index}")
            for index, name in enumerate(reversed(names))
        )[::-1]

    def _abundance_offer(self, game, seat="A"):
        self.assertEqual("draw.replacement", game.state.pending_decision.kind)
        options = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["options"]
        choice, = [option["id"] for option in options if option["id"] != "draw"]
        response = game.act("pilot:" + seat, {"action_id": "choose", "choice": choice})
        self.assertTrue(response.ok, response.summary)
        self.assertEqual("draw.library_kind", game.state.pending_decision.kind)
        self.assertEqual(["pilot:" + seat], game.pending_principals())

    def test_abundance_reveals_one_prefix_and_orders_before_the_next_draw(self):
        game = self.session(121072016)
        source = self.add(game.engine, "Abundance")
        spell = self.add(game.engine, "Divination", zone="hand")
        first, second, match, tail = self._library_prefix(
            game, ("Reach Through Mists", "Divination", "Island", "Plains"),
        )
        self.assert_closed(game, source)
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.assertIsNotNone(self.resolve(game))
        self._abundance_offer(game)

        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "category": "land"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertNotIn(tail.ref, json.dumps(game.packet("pilot:A", full=True)["decision"]))
        game = self.replay(game, load=True)
        response = game.act("pilot:A", {"action_id": "choose", "category": "land"})
        self.assertTrue(response.ok, response.summary)
        self.assertEqual("draw.library_bottom", game.state.pending_decision.kind)
        self.assertEqual(0, self._draws(game))
        self.assertEqual("hand", game.state.cards[match.object_id].zone)
        reveal = next(event for event in reversed(game.state.events) if event.code == "library.reveal")
        self.assertEqual([first.ref, second.ref, match.ref], [row["ref"] for row in reveal.details["cards"]])

        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": "order", "order": [tail.ref, first.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        response = game.act("pilot:A", {"action_id": "order", "order": [second.ref, first.ref]})
        self.assertTrue(response.ok, response.summary)
        self.assertEqual([second.object_id, first.object_id], game.state.players["A"].zones["library"][:2])
        self.assertEqual("draw.replacement", game.state.pending_decision.kind)
        response = game.act("pilot:A", {"action_id": "choose", "choice": "draw"})
        self.assertTrue(response.ok, response.summary)
        self.assertEqual("hand", game.state.cards[tail.object_id].zone)
        self.assertEqual(1, self._draws(game))
        self.replay(game, load=True)

    def test_abundance_can_order_every_card_and_replace_an_empty_turn_draw(self):
        game = self.session(121072017)
        source = self.add(game.engine, "Abundance")
        spell = self.add(game.engine, "Reach Through Mists", zone="hand")
        first, second = self._library_prefix(game, ("Island", "Plains"))
        self.assert_closed(game, source)
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        self.assertIsNotNone(self.resolve(game))
        self._abundance_offer(game)
        response = game.act("pilot:A", {"action_id": "choose", "category": "nonland"})
        self.assertTrue(response.ok, response.summary)
        self.assertEqual("draw.library_bottom", game.state.pending_decision.kind)
        game = self.replay(game, load=True)
        response = game.act("pilot:A", {"action_id": "order", "order": [first.ref, second.ref]})
        self.assertTrue(response.ok, response.summary)
        self.assertEqual([first.object_id, second.object_id], game.state.players["A"].zones["library"])
        self.assertEqual(0, self._draws(game))
        self.replay(game, load=True)

        game = self.session(121072018)
        source = self.add(game.engine, "Abundance")
        self.assert_closed(game, source)
        self._library_prefix(game, ())
        self.prepare(game)
        upkeep_witness.VanishingRuntimeTests.upkeep(self, game, seat="A")
        self.checkpoint(game)
        self.until(game, lambda s: s.state.pending_decision and s.state.pending_decision.kind == "draw.replacement")
        self.assertFalse(game.state.stack)
        self._abundance_offer(game)
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "category": "land"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.players["A"].in_game)
        self.assertEqual([], game.state.players["A"].zones["library"])
        self.assertEqual(0, self._draws(game))
        self.assertFalse(any(event.code == "library.reveal" for event in game.state.events))
        self.replay(game, load=True)

    def test_abundance_replays_one_redirected_hand_move_before_bottom_order(self):
        from quorune.replacement.operations import SetField
        from quorune.semantic_runtime import zone_replacements

        game = self.session(121072019)
        source = self.add(game.engine, "Abundance")
        spell = self.add(game.engine, "Reach Through Mists", zone="hand")
        first, second, match = self._library_prefix(game, ("Divination", "Reach Through Mists", "Island"))
        self.assert_closed(game, source)
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)

        collect = zone_replacements.collect_zone_change_replacement_effects
        alternatives = tuple(
            ReplacementEffect(
                effect_id=f"fixture:library-choice-move-{zone}",
                source_id="fixture:library-choice-move",
                event_kind="zone.change", replacement_class=ReplacementClass.OTHER,
                conditions={"origin": {"eq": "library"}, "destination": {"eq": "hand"}},
                operations=(SetField("destination", zone),),
            )
            for zone in ("exile", "graveyard")
        )

        def with_redirects(host, **kwargs):
            return (*collect(host, **kwargs), *alternatives)

        with patch.object(zone_replacements, "collect_zone_change_replacement_effects", side_effect=with_redirects):
            self.cast_for(game, "A", spell)
            self.assertIsNotNone(self.resolve(game))
            self._abundance_offer(game)
            result = game.act("pilot:A", {"action_id": "choose", "category": "land"})
            self.assertTrue(result.ok, result.summary)
            self.assertEqual("replacement.order", game.state.pending_decision.kind)
            self.assertEqual("library", game.state.cards[match.object_id].zone)
            self.assertEqual(0, self._draws(game))
            game = self.replay(game, load=True)
            options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
            exile, = [row["id"] for row in options if "library-choice-move-exile" in row["id"]]
            command = {"action_id": "choose", "replacement": exile}
            before = authoritative_state_hash(game.state)
            self.assertFalse(game.act("pilot:B", command).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
            self.assertTrue(game.act("pilot:A", command).ok)
            self.assertEqual("exile", game.state.cards[match.object_id].zone)
            self.assertEqual("draw.library_bottom", game.state.pending_decision.kind)
            self.assertEqual(1, sum(event.code == "library.reveal" for event in game.state.events))
            self.assertTrue(game.act("pilot:A", {"action_id": "order", "order": [second.ref, first.ref]}).ok)
            self.assertEqual([second.object_id, first.object_id], game.state.players["A"].zones["library"])
            self.assertEqual(0, self._draws(game))
            self.replay(game, load=True)

    def _chain(self, session, seat="B", ref=None):
        card = self.add(session.engine, "Chains of Mephistopheles", seat=seat, ref=ref)
        self.assert_closed(session, card)
        return card

    def _draws(self, session, seat="A", field="cards_drawn_by_turn"):
        return session.state.players[seat].stats.get(field, {}).get(str(session.state.turn_sequence), 0)

    def _hand_only(self, session, seat, *cards):
        keep={card.object_id for card in cards}
        for oid in tuple(session.state.players[seat].zones["hand"]):
            if oid not in keep:
                session.engine.move_card(oid, "graveyard", semantic_events=False, reason="Fixture hand")

    def _choose(self, session, seat, card_ref):
        decision=session.packet("pilot:"+seat, full=True)["decision"]
        self.assertEqual("draw.discard", decision["kind"])
        command={"action_id":"choose","card":card_ref}
        result=session.act("pilot:"+seat, command)
        self.assertTrue(result.ok,result.summary)

    def _mists(self, session, seat="A", ref="mists"):
        self.add(session.engine, "Island", seat=seat, ref=f"{ref}-mana")
        return self.add(session.engine, "Reach Through Mists", seat=seat, zone="hand", ref=ref)

    def test_actual_rolls_back_unoffered_discards_and_resumes_the_original_sequence(self):
        game=self.session(121072001)
        self._chain(game)
        spell=self.add(game.engine,"Divination",zone="hand",ref="draw-two")
        original=self.add(game.engine,"Plains",zone="hand",ref="secret-discard")
        foreign=self.add(game.engine,"Island",seat="C",zone="hand",ref="foreign-card")
        for n in range(3):
            self.add(game.engine,"Island",ref=f"divination-mana-{n}")
        self._hand_only(game,"A",spell,original)
        self.prepare(game)
        self.checkpoint(game)
        first,second=reversed(game.state.players["A"].zones["library"][-2:])
        self.cast_for(game,"A",spell)
        self.assertIsNotNone(self.resolve(game))
        actor=game.packet("pilot:A",full=True)["decision"]
        self.assertEqual("draw.discard",actor["kind"])
        self.assertIn(original.ref,json.dumps(actor))
        self.assertIsNone(game.packet("pilot:C",full=True)["decision"])
        self.assertNotIn(original.ref,json.dumps(game.packet("pilot:C",full=True)))
        snapshot=authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A",{"action_id":"choose","card":foreign.ref}).ok)
        self.assertFalse(game.act("pilot:C",{"action_id":"choose","card":original.ref}).ok)
        self.assertEqual(snapshot,authoritative_state_hash(game.state))
        game=self.replay(game,load=True)
        self._choose(game,"A",original.ref)
        self.assertEqual(1,self._draws(game))
        self._choose(game,"A",game.state.cards[first].ref)
        self.assertEqual(2,self._draws(game))
        self.assertEqual("graveyard",game.state.cards[original.object_id].zone)
        self.assertEqual("graveyard",game.state.cards[first].zone)
        self.assertEqual("hand",game.state.cards[second].zone)
        self.assertFalse(game.state.stack)
        self.replay(game,load=True)

    def test_actual_empty_hand_mills_one_and_an_empty_library_does_not_attempt_a_draw(self):
        for seed, empty_library in ((121072002,False),(121072003,True)):
            with self.subTest(empty_library=empty_library):
                game=self.session(seed)
                self._chain(game)
                spell=self._mists(game)
                self._hand_only(game,"A",spell)
                if empty_library:
                    for oid in tuple(game.state.players["A"].zones["library"]):
                        game.engine.move_card(oid,"graveyard",semantic_events=False,reason="Fixture library")
                self.prepare(game)
                self.checkpoint(game)
                library=tuple(game.state.players["A"].zones["library"])
                self.cast_for(game,"A",spell)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(0,self._draws(game))
                self.assertTrue(game.state.players["A"].in_game)
                self.assertEqual(library[:-1],tuple(game.state.players["A"].zones["library"]))
                if library:
                    self.assertEqual("graveyard",game.state.cards[library[-1]].zone)
                self.replay(game,load=True)

    def test_actual_two_sources_apply_once_each_to_one_draw(self):
        game=self.session(121072004)
        self._chain(game,seat="A",ref="own-chain")
        other=self._chain(game,seat="C",ref="other-chain")
        spell=self._mists(game)
        offered=self.add(game.engine,"Plains",zone="hand",ref="lone-discard")
        self._hand_only(game,"A",spell,offered)
        self.prepare(game)
        self.checkpoint(game)
        top=game.state.players["A"].zones["library"][-1]
        self.cast_for(game,"A",spell)
        self.assertIsNotNone(self.resolve(game))
        decision=game.packet("pilot:A",full=True)["decision"]
        self.assertEqual("draw.replacement",decision["kind"])
        self.assertEqual(2,len(decision["ctx"]["options"]))
        option,=(row["id"] for row in decision["ctx"]["options"] if row["label"].startswith(other.ref+":"))
        result=game.act("pilot:A",{"action_id":"choose","choice":option})
        self.assertTrue(result.ok,result.summary)
        self._choose(game,"A",offered.ref)
        self.assertEqual(0,self._draws(game))
        self.assertEqual("graveyard",game.state.cards[offered.object_id].zone)
        self.assertEqual("graveyard",game.state.cards[top].zone)
        self.assertFalse(game.state.stack)
        self.replay(game,load=True)

    def _reach_first_draw(self, game):
        self.until(game, lambda current: current.state.phase == "beginning" and current.state.step == "draw"
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "priority")
        self.assertEqual(1,self._draws(game,field="cards_drawn_in_draw_step_by_turn"))

    def test_actual_first_draw_in_step_is_exempt_and_a_later_draw_is_replaced(self):
        game=self.session(121072005)
        self._chain(game)
        spell=self._mists(game)
        self.prepare(game)
        upkeep_witness.VanishingRuntimeTests.upkeep(self,game,seat="A")
        self.checkpoint(game)
        self._reach_first_draw(game)
        self.cast_for(game,"A",spell)
        self.assertIsNotNone(self.resolve(game))
        self.assertEqual("draw.discard",game.state.pending_decision.kind)
        card=game.state.cards[game.state.players["A"].zones["hand"][0]]
        self._choose(game,"A",card.ref)
        self.assertEqual(2,self._draws(game,field="cards_drawn_in_draw_step_by_turn"))
        self.replay(game,load=True)

    def test_actual_nested_zone_order_keeps_one_discard_and_replays(self):
        game=self.session(121072006)
        self._chain(game)
        spell=self._mists(game)
        card=self.add(game.engine,"Plains",zone="hand",ref="private-discard")
        self._hand_only(game,"A",spell,card)
        self.assert_closed(game,self.add(game.engine,"Rest in Peace",seat="D",ref="rest-first"))
        self.assert_closed(game,self.add(game.engine,"Rest in Peace",seat="B",ref="rest-second"))
        self.prepare(game)
        self.checkpoint(game)
        prior=card.logical_object_id
        top=game.state.players["A"].zones["library"][-1]
        self.cast_for(game,"A",spell)
        self.assertIsNotNone(self.resolve(game))
        self._choose(game,"A",card.ref)
        self.assertEqual("replacement.order",game.state.pending_decision.kind)
        self.assertEqual(0,self._draws(game))
        self.assertEqual("hand",game.state.cards[card.object_id].zone)
        game=self.replay(game,load=True)
        decision=game.packet("pilot:A",full=True)["decision"]
        options=decision["ctx"]["options"]
        self.assertEqual(2,len(options))
        replace_id=options[0]["id"]
        result=game.act("pilot:A",{"action_id":"choose","replacement":replace_id})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual("exile",game.state.cards[card.object_id].zone)
        self.assertEqual("hand",game.state.cards[top].zone)
        self.assertEqual(1,self._draws(game))
        entries=game.engine._current_turn_history("card_discarded")
        self.assertEqual(1,sum(entry.object_incarnation==prior for entry in entries))
        self.replay(game,load=True)

    def test_native_exemption_mutant_exposes_a_replacement_on_the_first_draw(self):
        game=self.session(121072007)
        self._chain(game)
        self.prepare(game)
        upkeep_witness.VanishingRuntimeTests.upkeep(self,game,seat="A")
        self.checkpoint(game)
        with patch("quorune.semantic_runtime.draw_replacements.is_first_draw_in_own_draw_step",return_value=False):
            self.until(game,lambda current: current.state.pending_decision is not None
                       and current.state.pending_decision.kind=="draw.discard")
        self.assertEqual(0,self._draws(game,field="cards_drawn_in_draw_step_by_turn"))

    def test_actual_opponent_draw_retains_the_original_spell_as_the_discard_cause(self):
        game=self.session(121072008)
        self._chain(game,seat="C")
        spell=self.add(game.engine,"Inspiration",seat="B",zone="hand",ref="opponent-draw")
        offered=self.add(game.engine,"Plains",zone="hand",ref="provenance-discard")
        self._hand_only(game,"A",offered)
        for n in range(4):
            self.add(game.engine,"Island",seat="B",ref=f"inspiration-mana-{n}")
        self.assert_closed(game,spell)
        self.prepare(game)
        self.checkpoint(game)
        self.priority(game,"B")
        action=self.action(game,"B",spell)
        result=game.act("pilot:B",{"action_id":action["id"],"targets":["A"],"pay":"auto"})
        self.assertTrue(result.ok,result.summary)
        source=game.state.stack[-1].ref
        self.assertIsNotNone(self.resolve(game))
        events=[]
        with discard_witness.DiscardEffectOriginTests.collect_discards(
            self,game.engine,events,discard_witness.OPPONENT_SOURCE_DISCARD_EVENT,
        ):
            self._choose(game,"A",offered.ref)
            card=game.state.cards[game.state.players["A"].zones["hand"][0]]
            self._choose(game,"A",card.ref)
        self.assertEqual(2,len(events))
        for event in events:
            self.assertEqual(("A","B"),(event["previous_controller"],event["causing_player"]))
            self.assertEqual(source,event["discard_effect_origin"]["stack_ref"])
        self.assertEqual(2,self._draws(game))
        self.replay(game,load=True)

    def test_actual_madness_discard_to_exile_still_produces_one_draw(self):
        game=self.session(121072009)
        self._chain(game)
        spell=self._mists(game)
        offered=self.add(game.engine,"Fiery Temper",zone="hand",ref="madness-exile")
        self.assert_closed(game,offered)
        self._hand_only(game,"A",spell,offered)
        self.prepare(game)
        self.checkpoint(game)
        top=game.state.players["A"].zones["library"][-1]
        self.cast_for(game,"A",spell)
        self.assertIsNotNone(self.resolve(game))
        self._choose(game,"A",offered.ref)
        self.assertEqual("exile",game.state.cards[offered.object_id].zone)
        self.assertEqual("hand",game.state.cards[top].zone)
        self.assertEqual(1,self._draws(game))
        self.replay(game,load=True)
