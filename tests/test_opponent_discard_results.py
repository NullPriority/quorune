"""Exercise source-owned results from authoritative opponent-caused discards."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_discard_effect_origin as origin_witnesses
from quorune.ability_fragments import StaticComponentSpec, ability_fragment_to_dict
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.delayed_object_model import DELAYED_OBJECT_MECHANIC
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.opponent_discard_results import (
    OPPONENT_DISCARD_CAPABILITY, OPPONENT_DISCARD_DAMAGE_CAPABILITY, OPPONENT_DISCARD_DELAYED_RETURN_CAPABILITY,
    OPPONENT_DISCARD_DELAYED_COUNTER_RETURN_CAPABILITY, OPPONENT_DISCARD_PLAINS_TOKEN_CAPABILITY,
    OPPONENT_DISCARD_SELF_EVENT, controlled_plains_discard_condition,
    opponent_discard_capabilities, opponent_discard_group_capabilities,
)
from scripts.build_test_database import build_fixture_database


class OpponentDiscardResultTests(InheritedNativeFixture, unittest.TestCase):
    names = {"return":"Generic Discarded Return", "life":"Generic Discarded Life", "token":"Generic Discarded Token"}
    play_whole_hand_spell = origin_witnesses.DiscardEffectOriginTests.play_whole_hand_spell
    ready = origin_witnesses.DiscardEffectOriginTests.ready

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-discard.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json", fixtures / "whole-hand-discard-cards.json",
            fixtures / "batched-support-assurance-cards.json", fixtures / "opponent-discard-results-cards.json",
            fixtures / "graveyard-exile-replacement-cards.json", fixtures / "random-discard-cards.json",
            fixtures / "colored-hand-reveal-cards.json", fixtures / "nonland-permanent-hand-cards.json",
            fixtures / "ward-discard-replay-cards.json", fixtures / "event-player-modifier-cards.json",
            fixtures / "target-land-controller-choice-cards.json",
        ], path)
        cls.configure_assets(path)

    @classmethod
    def configure_assets(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Source discard", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def discard_trigger(self, seed, result, *, redirected=False):
        game = self.session(seed)
        card = self.add(game.engine, self.names[result], seat="B", zone="hand", ref="DISCARD-RESULT-SOURCE")
        if redirected:
            replacement = self.add(game.engine, "Rest in Peace", seat="A")
            self.assert_closed(game, replacement)
        self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        origin = self.play_whole_hand_spell(game)
        self.resolve_top(game)
        trigger, = [item for item in game.state.stack if item.source_object_id == card.object_id]
        current = game.state.cards[card.object_id]
        self.assertEqual(("B","A",current.logical_object_id),
                         (trigger.controller,trigger.context["causing_player"],trigger.context["discarded_logical_object_id"]))
        self.assertEqual(origin.ref, trigger.context["discard_effect_origin"]["stack_ref"])
        self.assertEqual("exile" if redirected else "graveyard", current.zone)
        return game, card

    def test_original_return_tracks_public_discard_destination_and_replays(self):
        for redirected in (False, True):
            with self.subTest(redirected=redirected):
                game,card = self.discard_trigger(70109061 + redirected,"return",redirected=redirected)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("hand", game.state.cards[card.object_id].zone)
                self.assertIn(card.object_id, game.state.players["B"].zones["hand"])
                self.replay(game, load=True)

    def test_blinked_discarded_incarnation_does_not_return(self):
        game, card = self.discard_trigger(70109071,"return")
        before = card.logical_object_id
        game.engine.move_card(card.object_id,"exile",reason="Move the discarded object before its trigger resolves")
        self.assertNotEqual(before,game.state.cards[card.object_id].logical_object_id)
        self.checkpoint(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile",game.state.cards[card.object_id].zone)
        self.replay(game,load=True)

    def test_own_discard_does_not_trigger(self):
        game = self.session(70109081)
        card = self.add(game.engine,self.names["return"],seat="A",zone="hand")
        spell = self.add(game.engine,"Wheel of Fortune",zone="hand")
        self.assert_closed(game,card)
        game.state.config.semantic_policy = "trusted_only"
        action = self.ready(game,spell,{"R":1,"C":2})
        self.checkpoint(game)
        result = game.act("pilot:A",{"action_id":action["id"],"pay":"auto"})
        self.assertTrue(result.ok,result.summary)
        self.resolve_top(game)
        self.assertFalse(any(item.source_object_id==card.object_id for item in game.state.stack))
        self.assertEqual("graveyard",game.state.cards[card.object_id].zone)
        self.replay(game,load=True)

    def test_causer_loses_life_and_discarded_source_controller_creates_tokens(self):
        game,_ = self.discard_trigger(70109091,"life")
        totals = {who:game.state.players[who].life for who in "ABCD"}
        self.assertIsNone(self.resolve(game))
        self.assertEqual({**totals,"A":totals["A"]-5},{who:game.state.players[who].life for who in "ABCD"})
        self.replay(game,load=True)
        game,_ = self.discard_trigger(70109092,"token")
        self.assertIsNone(self.resolve(game))
        tokens = [card for card in game.state.cards.values() if card.is_token]
        self.assertEqual(4,len(tokens))
        self.assertTrue(all(row.owner=="B" and row.controller=="B" and row.zone=="battlefield" for row in tokens))
        self.replay(game,load=True)

    def test_selected_and_random_spell_discards_bind_the_same_source(self):
        for random in (False, True):
            with self.subTest(random=random):
                game = self.session(70109101 + random)
                engine = game.engine
                for key in tuple(game.state.players["B"].zones["hand"]):
                    engine.move_card(key, "library", log=False)
                source = self.add(engine, self.names["return"], seat="B", zone="hand", ref="CAUSE-WITNESS")
                other = [] if random else [
                    self.add(engine, "Generic Bound Growth", seat="B", zone="hand", ref="SELECTED-OTHER"),
                    self.add(engine, "Generic Bound Plains", seat="B", zone="hand", ref="KEPT-OTHER"),
                ]
                spell = self.add(engine, "Hymn to Tourach" if random else "Mind Rot", zone="hand")
                for card in (source, spell):
                    self.assert_closed(game, card)
                game.state.config.semantic_policy = "trusted_only"
                action = self.ready(game, spell, {"B":2,"C":2})
                self.checkpoint(game)
                result = game.act("pilot:A", {"action_id":action["id"],"targets":["B"],"pay":"auto"})
                self.assertTrue(result.ok,result.summary)
                item, = game.state.stack
                if random:
                    self.resolve_top(game)
                else:
                    decision = self.resolve(game)
                    self.assertEqual(["B"], decision.actors)
                    game = self.replay(game, load=True)
                    result = game.act("pilot:B", {"action_id":"choose","cards":[source.ref,other[0].ref]})
                    self.assertTrue(result.ok,result.summary)
                trigger, = [t for t in game.state.stack if t.source_object_id==source.object_id]
                self.assertEqual(item.ref,trigger.context["discard_effect_origin"]["stack_ref"])
                self.assertEqual("A",trigger.context["causing_player"])
                self.assertIsNone(self.resolve(game))
                self.assertEqual("hand",game.state.cards[source.object_id].zone)
                if not random:
                    self.assertEqual("hand",game.state.cards[other[1].object_id].zone)
                self.replay(game,load=True)

    def test_private_hand_choice_binds_the_opponent_spell_without_leaking_objects(self):
        spells = (("Generic Nonland Hand Look Spell", False), ("Duress", True))
        for offset, (spell_name, publicly_revealed) in enumerate(spells):
            with self.subTest(spell=spell_name):
                game = self.session(70109111 + offset)
                source = self.add(game.engine, self.names["token"], seat="B", zone="hand", ref="INSPECTED-SOURCE")
                spell = self.add(game.engine, spell_name, zone="hand")
                for card in (source, spell):
                    self.assert_closed(game, card)
                game.state.config.semantic_policy = "trusted_only"
                offer = self.ready(game, spell, {"B": 3, "U": 3, "C": 3})
                self.checkpoint(game)
                result = game.act("pilot:A", {"action_id": offer["id"], "targets": ["B"], "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                cause, = game.state.stack
                self.assertEqual(["A"], self.resolve(game).actors)
                packet = game.packet("pilot:C", full=True)
                self.assertIsNone(packet["decision"])
                self.assertEqual(publicly_revealed, source.ref in str(packet))
                self.assertIn(source.ref, str(game.packet("pilot:A", full=True)["decision"]))
                game = self.replay(game, load=True)
                result = game.act("pilot:A", {"action_id": "choose", "card": source.ref})
                self.assertTrue(result.ok, result.summary)
                trigger, = [item for item in game.state.stack if item.source_object_id == source.object_id]
                self.assertEqual(
                    (cause.ref, "A", "B"),
                    (trigger.context["discard_effect_origin"]["stack_ref"], trigger.context["causing_player"],
                     trigger.controller),
                )
                self.assertIsNone(self.resolve(game))
                self.assertEqual(4, sum(1 for row in game.state.cards.values() if row.is_token and row.owner == "B"))
                self.replay(game, load=True)

    def test_optional_ward_discard_uses_the_resolving_opponents_ability(self):
        game = self.session(70109121)
        ward = self.add(game.engine, "Westgate Regent", ref="DISCARD-WARD")
        spell = self.add(game.engine, "Word of Seizing", seat="B", zone="hand", ref="WARD-TARGETING-SPELL")
        source = self.add(game.engine, self.names["return"], seat="B", zone="hand", ref="WARD-PAYMENT-SOURCE")
        for card in (ward, spell, source):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="B", mana={"C": 3, "R": 2})
        self.checkpoint(game)
        self.cast_for(game, "B", spell, [ward.ref])
        cause, = [item for item in game.state.stack if item.source_object_id == ward.object_id]
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        self.assertNotIn(source.ref, str(game.packet("pilot:A", full=True)))
        game = self.replay(game, load=True)
        result = game.act("pilot:B", {"action_id": "choose", "cards": [source.ref]})
        self.assertTrue(result.ok, result.summary)
        trigger, = [item for item in game.state.stack if item.source_object_id == source.object_id]
        origin = trigger.context["discard_effect_origin"]
        self.assertEqual((cause.ref, "triggered_ability", "A", "B"),
                         (origin["stack_ref"], origin["kind"], origin["controller"], trigger.controller))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual("B", game.state.cards[ward.object_id].controller)
        self.replay(game, load=True)

    def test_discarded_source_needs_its_current_ability(self):
        game = self.session(70109131)
        source = self.add(game.engine, self.names["return"], seat="B", zone="hand")
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.play_whole_hand_spell(game)
        program, = game.engine.semantics.programs_for_oracle(
            game.engine.card_record(source).oracle_id, event=OPPONENT_DISCARD_SELF_EVENT,
        )
        fragment = ability_fragment_to_dict(StaticComponentSpec(program.key))
        effective_data = game.engine._effective_card_data
        removed = []

        def remove_current_fragment(value, **kwargs):
            data = effective_data(value, **kwargs)
            card = game.state.cards[value] if isinstance(value, str) else value
            if card.object_id == source.object_id and card.zone == "graveyard":
                fragments = data["ability_fragments"]
                if fragment in fragments:
                    removed.append(fragment)
                    return {**data, "ability_fragments": [row for row in fragments if row != fragment]}
            return data

        with patch.object(game.engine, "_effective_card_data", side_effect=remove_current_fragment):
            self.resolve_top(game)
        self.assertTrue(removed)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertFalse(any(item.source_object_id == source.object_id for item in game.state.stack))

    def test_exact_shape_rejects_false_causes_and_other_result_oracles(self):
        for name in self.names.values():
            record = self.db.lookup(name,fuzzy=False)
            compile_card = lambda candidate: compile_oracle_card(candidate,capability_registry=self.registry,
                capability_profile="commander_review")
            result = compile_card(record)
            self.assertEqual("exact",result.status,result.material_residuals)
            node, = [node for face in result.faces for node in face.nodes
                     if OPPONENT_DISCARD_CAPABILITY in node.capability_dependencies]
            self.assertIn(OPPONENT_DISCARD_CAPABILITY,opponent_discard_capabilities(node.effects[0]))
            self.assertEqual((),opponent_discard_capabilities({**node.effects[0],"unknown":True}))
            for old,new in (("an opponent controls","you control"),("discard this card","discard a card")):
                self.assertNotEqual("exact",compile_card(replace(
                    record,oracle_text=record.oracle_text.replace(old,new))).status)
            with patch("quorune.compiler.opponent_discard_nodes.opponent_discard_effect",return_value=None):
                self.assertNotEqual("exact",compile_card(record).status)

    def test_pinned_damage_chooses_one_legal_target_from_either_public_destination(self):
        for redirected in (False,True):
            with self.subTest(redirected=redirected):
                game=self.session(70109561+redirected)
                source=self.add(game.engine,"Generic Discard Damage",seat="B",zone="hand",ref="DISCARDED-DAMAGE")
                self.assert_closed(game,source)
                if redirected:
                    replacement=self.add(game.engine,"Rest in Peace",seat="A")
                    self.assert_closed(game,replacement)
                game.state.config.semantic_policy="trusted_only"
                cause=self.play_whole_hand_spell(game)
                self.resolve_top(game)
                pending=game.state.pending_decision
                self.assertEqual(("semantic.target",["B"]),(pending.kind,pending.actors))
                self.assertIsNone(game.packet("pilot:A",full=True)["decision"])
                self.assertEqual("exile" if redirected else "graveyard",game.state.cards[source.object_id].zone)
                start={seat:row.life for seat,row in game.state.players.items()}
                game=self.replay(game,load=True)
                self.target(game,"B",["C"])
                trigger,=[item for item in game.state.stack if item.source_object_id==source.object_id]
                self.assertEqual((cause.ref,"B"),(trigger.context["discard_effect_origin"]["stack_ref"],trigger.controller))
                self.assertIsNone(self.resolve(game))
                self.assertEqual({**start,"C":start["C"]-3},{seat:row.life for seat,row in game.state.players.items()})
                self.replay(game,load=True)
    def prepare_response(self, seed, response_name):
        game=self.session(seed)
        source=self.add(game.engine,"Generic Discard Damage",seat="B",zone="hand",ref="PENDING-DISCARDED-SOURCE")
        response=self.add(game.engine,response_name,seat="C",zone="hand",ref="RESPONSE-SPELL")
        target=self.add(game.engine,"Generic Bound Body",seat="D",ref="PENDING-DAMAGE-TARGET")
        for card in (source,response,target):
            self.assert_closed(game,card)
        game.state.config.semantic_policy="trusted_only"
        game.state.players["C"].mana_pool.update({"B":1,"U":1})
        self.play_whole_hand_spell(game)
        self.resolve_top(game)
        return game,source,response,target

    def test_pending_damage_retains_the_departed_original_source(self):
        game,source,response,_=self.prepare_response(70109571,"Generic Graveyard Removal")
        self.target(game,"B",["C"])
        self.cast_for(game,"C",response,[source.ref])
        self.resolve_top(game)
        self.assertEqual("exile",game.state.cards[source.object_id].zone)
        old_life=game.state.players["C"].life
        game=self.replay(game,load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(old_life-3,game.state.players["C"].life)
        self.replay(game,load=True)

    def test_departed_target_receives_no_discard_damage(self):
        game,_,response,target=self.prepare_response(70109572,"Generic Creature Return")
        self.target(game,"B",[target.ref])
        self.cast_for(game,"C",response,[target.ref])
        self.resolve_top(game)
        self.assertEqual("hand",game.state.cards[target.object_id].zone)
        game=self.replay(game,load=True)
        start=game.state.event_sequence
        self.assertIsNone(self.resolve(game))
        self.assertFalse(any(row.event_id>start and row.code=="damage.dealt" for row in game.state.events))
        self.replay(game,load=True)

    def test_discard_damage_needs_the_exact_source_and_damage_owner(self):
        card = self.db.lookup("Generic Discard Damage", fuzzy=False)

        def compile_card(record):
            return compile_oracle_card(
                record, capability_registry=self.registry, capability_profile="commander_review",
            )

        result = compile_card(card)
        self.assertEqual("exact", result.status, result.material_residuals)
        node, = [node for face in result.faces for node in face.nodes
                 if OPPONENT_DISCARD_DAMAGE_CAPABILITY in node.capability_dependencies]
        self.assertEqual(1, node.target_schema["count"])
        self.assertEqual("$source", node.effects[0]["source"])
        self.assertIn("damage.source.stack_current_or_lki", node.capability_dependencies)
        for previous, replacement in (
            ("an opponent controls", "you control"),
            ("it deals 3 damage", "another card deals 3 damage"),
            ("it deals 3 damage to any target.", "it deals 3 damage to a player of your choice."),
        ):
            altered = replace(card, oracle_text=card.oracle_text.replace(previous, replacement))
            self.assertNotEqual("exact", compile_card(altered).status)
        with patch("quorune.compiler.opponent_discard_nodes.fixed_damage_effect_template", return_value=None):
            self.assertNotEqual("exact", compile_card(card).status)

    def test_delayed_discard_life_and_return_follow_original_destination(self):
        for redirected in (False, True):
            with self.subTest(redirected=redirected):
                game = self.session(70109771 + redirected)
                hidden = self.add(game.engine, "Generic Bound Growth", seat="C", zone="hand",
                                  ref="DELAYED-DISCARD-PRIVATE")
                card = self.add(game.engine, "Generic Delayed Discard Return", seat="B", zone="hand")
                self.assert_closed(game, card)
                if redirected:
                    replacement = self.add(game.engine, "Rest in Peace", seat="A")
                    self.assert_closed(game, replacement)
                game.state.config.semantic_policy = "trusted_only"
                life = {seat: player.life for seat, player in game.state.players.items()}
                self.play_whole_hand_spell(game)
                self.resolve_top(game)
                self.assertEqual(life["B"], game.state.players["B"].life)
                self.resolve_top(game)
                self.assertEqual({**life, "B": life["B"] + 2},
                                 {seat: player.life for seat, player in game.state.players.items()})
                self.assertEqual("exile" if redirected else "graveyard", game.state.cards[card.object_id].zone)
                self.assertIn(hidden.ref, str(game.packet("pilot:C", full=True)))
                self.assertNotIn(hidden.ref, str(game.packet("pilot:A", full=True)))
                game = self.replay(game, load=True)
                self.assertNotIn(hidden.ref, str(game.packet("pilot:B", full=True)))
                self.until(game, lambda state: any(item.source_object_id == card.object_id
                    and item.label == "Delayed bounce" for item in state.state.stack))
                self.resolve_top(game)
                self.assertEqual("exile" if redirected else "hand", game.state.cards[card.object_id].zone)
                self.assertEqual(life["B"] + 2, game.state.players["B"].life)
                self.replay(game, load=True)

    def test_delayed_discard_return_requires_the_original_graveyard_incarnation(self):
        game = self.session(70109781)
        card = self.add(game.engine, "Generic Delayed Discard Return", seat="B", zone="hand")
        remove = self.add(game.engine, "Generic Graveyard Removal", seat="C", zone="hand")
        restore = self.add(game.engine, "Generic Return Exiled Card", seat="D", zone="hand")
        for original in (card, remove, restore):
            self.assert_closed(game, original)
        game.state.config.semantic_policy = "trusted_only"
        game.state.players["C"].mana_pool["B"] = 1
        game.state.players["D"].mana_pool["W"] = 1
        self.play_whole_hand_spell(game)
        self.resolve_top(game)
        self.resolve_top(game)
        first = game.state.cards[card.object_id]
        self.assertEqual("graveyard", first.zone)
        identity = first.logical_object_id
        self.cast_for(game, "C", remove, [first.ref])
        self.resolve_top(game)
        exiled = game.state.cards[card.object_id]
        self.assertEqual("exile", exiled.zone)
        self.cast_for(game, "D", restore, [exiled.ref])
        self.resolve_top(game)
        returned = game.state.cards[card.object_id]
        self.assertEqual("graveyard", returned.zone)
        self.assertNotEqual(identity, returned.logical_object_id)
        game = self.replay(game, load=True)
        self.until(game, lambda state: any(item.source_object_id == card.object_id
            and item.label == "Delayed bounce" for item in state.state.stack))
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        self.replay(game, load=True)

    def test_delayed_discard_return_rejects_changed_order_timing_and_source(self):
        record = self.db.lookup("Generic Delayed Discard Return", fuzzy=False)

        def compile_record(card):
            return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

        compiled = compile_record(record)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        node, = [node for face in compiled.faces for node in face.nodes
                 if OPPONENT_DISCARD_DELAYED_RETURN_CAPABILITY in node.capability_dependencies]
        life, future = node.effects
        self.assertIn(OPPONENT_DISCARD_DELAYED_RETURN_CAPABILITY,
                      opponent_discard_group_capabilities(node.effects, mechanic_ids=(DELAYED_OBJECT_MECHANIC,)))
        for effects in ((future, life), (life, {**future, "timing": "end_of_combat"}),
                        (life, {**future, "object": "$context.card"})):
            self.assertEqual((), opponent_discard_group_capabilities(effects, mechanic_ids=(DELAYED_OBJECT_MECHANIC,)))
        for old, new in (("an opponent controls", "you control"), ("from your graveyard", "from exile"),
                         ("the next end step", "your next end step")):
            with self.subTest(old=old):
                altered = replace(record, oracle_text=record.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_record(altered).status)
        with patch("quorune.compiler.opponent_discard_nodes.discard_life_and_delayed_return_effects",
                   return_value=(life, {**future, "timing": "end_of_combat"})):
            self.assertNotEqual("exact", compile_record(record).status)

    def discard_under_plains_condition(self, seed, *, land_seat, response=None):
        game = self.session(seed)
        source = self.add(game.engine, "Generic Plains Discard Token", seat="B", zone="hand")
        land = self.add(game.engine, "Generic Nonbasic Plains", seat=land_seat)
        private_card = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand",
                                ref="PLAINS-DISCARD-PRIVATE")
        self.assert_closed(game, source)
        self.assert_closed(game, land)
        answer = None
        if response is not None:
            answer = self.add(game.engine, response, seat="C", zone="hand")
            self.assert_closed(game, answer)
            game.state.players["C"].mana_pool.update(B=1, C=2, G=1)
        game.state.config.semantic_policy = "trusted_only"
        self.play_whole_hand_spell(game)
        self.resolve_top(game)
        self.assertIn(private_card.ref, str(game.packet("pilot:D", full=True)))
        self.assertNotIn(private_card.ref, str(game.packet("pilot:A", full=True)))
        return game, source, land, answer

    @staticmethod
    def bird_tokens(game):
        return [card for card in game.state.cards.values()
                if card.is_token and card.printed_name == "Bird Token" and card.zone == "battlefield"]

    def test_plains_token_rechecks_its_controller_and_preserves_flying_after_load(self):
        opposing, discarded, _, _ = self.discard_under_plains_condition(70109821, land_seat="A")
        self.assertFalse(any(item.source_object_id == discarded.object_id for item in opposing.state.stack))
        self.assertEqual([], self.bird_tokens(opposing))
        self.replay(opposing, load=True)
        game, discarded, _, exile = self.discard_under_plains_condition(
            70109822, land_seat="B", response="Generic Graveyard Removal",
        )
        trigger, = [item for item in game.state.stack if item.source_object_id == discarded.object_id]
        self.assertEqual("B", trigger.controller)
        self.cast_for(game, "C", exile, [game.state.cards[discarded.object_id].ref])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[discarded.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        token, = self.bird_tokens(game)
        self.assertEqual("B", token.controller)
        characteristics = game.engine._effective_card_data(token)
        self.assertEqual(("2", "2"), (characteristics["power"], characteristics["toughness"]))
        self.assertIn("flying", {str(keyword).lower() for keyword in characteristics["keywords"]})
        self.replay(game, load=True)

    def test_removing_the_only_plains_prevents_the_pending_discard_token(self):
        game, discarded, land, removal = self.discard_under_plains_condition(
            70109823, land_seat="B", response="Generic Land Removal",
        )
        self.assertTrue(any(item.source_object_id == discarded.object_id for item in game.state.stack))
        self.cast_for(game, "C", removal, [land.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[land.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], self.bird_tokens(game))
        self.replay(game, load=True)

    def test_plains_discard_result_rejects_other_conditions_and_non_token_effects(self):
        original = self.db.lookup("Generic Plains Discard Token", fuzzy=False)

        def compile_card(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

        compiled = compile_card(original)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        node, = [node for face in compiled.faces for node in face.nodes
                 if OPPONENT_DISCARD_PLAINS_TOKEN_CAPABILITY in node.capability_dependencies]
        self.assertEqual(controlled_plains_discard_condition(), node.event_condition)
        self.assertIn("token.creation.fixed_definition", node.capability_dependencies)
        for old, new in (("you control a Plains", "an opponent controls a Plains"),
                         ("you control a Plains", "you control a Mountain"),
                         ("create a 2/2 white Bird creature token with flying", "that player loses 2 life")):
            with self.subTest(old=old):
                changed = replace(original, oracle_text=original.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_card(changed).status)
        with patch("quorune.compiler.opponent_discard_nodes.controlled_plains_discard_condition",
                   return_value={**node.event_condition, "value": False}):
            self.assertNotEqual("exact", compile_card(original).status)

    @staticmethod
    def is_discard_counter_return(item, card):
        return item.source_object_id == card.object_id and item.label == "Return discarded card with a counter"

    def test_discard_counter_return_commits_the_counter_with_entry(self):
        for offset, scenario in enumerate(("ordinary", "doubled", "redirected")):
            with self.subTest(scenario=scenario):
                game = self.session(70109931 + offset)
                card = self.add(game.engine, "Generic Discard Counter Return", seat="B", zone="hand")
                hidden = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand",
                                  ref="DELAYED-COUNTER-PRIVATE")
                self.assert_closed(game, card)
                if scenario == "doubled":
                    replacement = self.add(game.engine, "Generic Bound Counter Doubler", seat="B")
                    self.assert_closed(game, replacement)
                if scenario == "redirected":
                    replacement = self.add(game.engine, "Rest in Peace", seat="A")
                    self.assert_closed(game, replacement)
                game.state.config.semantic_policy = "trusted_only"
                self.play_whole_hand_spell(game)
                self.resolve_top(game)
                self.resolve_top(game)
                destination = "exile" if scenario == "redirected" else "graveyard"
                self.assertEqual(destination, game.state.cards[card.object_id].zone)
                game = self.replay(game, load=True)
                self.until(game, lambda current: any(self.is_discard_counter_return(item, card)
                    for item in current.state.stack))
                self.assertIn(hidden.ref, str(game.packet("pilot:D", full=True)))
                self.assertNotIn(hidden.ref, str(game.packet("pilot:A", full=True)))
                self.resolve_top(game)
                moved = game.state.cards[card.object_id]
                if scenario == "redirected":
                    self.assertEqual("exile", moved.zone)
                    self.assertEqual(0, moved.counters.get("+1/+1", 0))
                else:
                    self.assertEqual(("battlefield", "B", 2 if scenario == "doubled" else 1),
                                     (moved.zone, moved.controller, moved.counters.get("+1/+1")))
                self.replay(game, load=True)

    def test_discard_counter_return_keeps_the_original_graveyard_incarnation(self):
        game = self.session(70109941)
        card = self.add(game.engine, "Generic Discard Counter Return", seat="B", zone="hand")
        remove = self.add(game.engine, "Generic Graveyard Removal", seat="C", zone="hand")
        restore = self.add(game.engine, "Generic Return Exiled Card", seat="D", zone="hand")
        for source in (card, remove, restore):
            self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        game.state.players["C"].mana_pool["B"] = 1
        game.state.players["D"].mana_pool["W"] = 1
        self.play_whole_hand_spell(game)
        self.resolve_top(game)
        self.resolve_top(game)
        first = game.state.cards[card.object_id]
        identity = first.logical_object_id
        self.cast_for(game, "C", remove, [first.ref])
        self.resolve_top(game)
        exiled = game.state.cards[card.object_id]
        self.assertEqual("exile", exiled.zone)
        self.cast_for(game, "D", restore, [exiled.ref])
        self.resolve_top(game)
        restored = game.state.cards[card.object_id]
        self.assertEqual("graveyard", restored.zone)
        self.assertNotEqual(identity, restored.logical_object_id)
        game = self.replay(game, load=True)
        self.until(game, lambda value: any(self.is_discard_counter_return(item, card)
            for item in value.state.stack))
        self.resolve_top(game)
        final_card = game.state.cards[card.object_id]
        self.assertEqual("graveyard", final_card.zone)
        self.assertEqual(0, final_card.counters.get("+1/+1", 0))
        self.replay(game, load=True)

    def test_discard_counter_return_rejects_changed_source_timing_and_schema(self):
        original = self.db.lookup("Generic Discard Counter Return", fuzzy=False)

        def compile_card(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

        compiled = compile_card(original)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        node, = [node for face in compiled.faces for node in face.nodes
                 if OPPONENT_DISCARD_DELAYED_COUNTER_RETURN_CAPABILITY in node.capability_dependencies]
        effect, = node.effects
        for variant in ({**effect, "timing": "end_of_combat"}, {**effect, "object": "$context.card"},
                        {**effect, "schema_version": float(effect["schema_version"])}):
            self.assertEqual((), opponent_discard_group_capabilities(
                (variant,), mechanic_ids=(DELAYED_OBJECT_MECHANIC,),
            ))
        for old, new in (("from your graveyard", "from exile"),
                         ("a +1/+1 counter", "two +1/+1 counters"),
                         ("the next end step", "your next end step")):
            changed = replace(original, oracle_text=original.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_card(changed).status)
        with patch("quorune.compiler.opponent_discard_nodes.discard_delayed_counter_return_effect",
                   return_value={**effect, "schema_version": True}):
            self.assertNotEqual("exact", compile_card(original).status)
