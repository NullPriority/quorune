"""Native evidence for an original's independent cycling and death triggers."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


CASES = (
    ("Bant Sojourners", "W", "apply", "apply"),
    ("Esper Sojourners", "U", "tap", "untap"),
    ("Grixis Sojourners", "B", "apply", "apply"),
    ("Jund Sojourners", "R", "apply", "apply"),
    ("Naya Sojourners", "G", "put", "apply"),
)
CYCLE = "card.cycled"
DEATH = "creature.dies"


class SelfCyclingAndDiesOptionalResultsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "self-cycling-and-dies-optional-results.sqlite3"
        fixture = ROOT / "tests/fixtures/self-cycling-and-dies-optional-results-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Independent cycling and death", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self, name):
        card = self.db.lookup(name, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        return card, semantics.programs_for_oracle(card.oracle_id)

    def test_five_complete_originals_admit_two_separate_typed_triggers_and_cycling(self):
        for name, _, _, _ in CASES:
            with self.subTest(name=name):
                card, programs = self.program(name)
                triggers = {program.event: program for program in programs if program.event.endswith(".self")}
                self.assertEqual({CYCLE + ".self", DEATH + ".self"}, set(triggers))
                self.assertEqual(("hand", "battlefield"),
                                 (triggers[CYCLE + ".self"].active_zone, triggers[DEATH + ".self"].active_zone))
                self.assertEqual(triggers[CYCLE + ".self"].target_schema, triggers[DEATH + ".self"].target_schema)
                self.assertNotEqual(triggers[CYCLE + ".self"].key, triggers[DEATH + ".self"].key)
                cycled = compile_oracle_card(card, capability_registry=self.registry)
                self.assertEqual("exact", cycled.status, cycled.material_residuals)
                self.assertTrue(any("cycling" in node.mechanics for face in cycled.faces for node in face.nodes))

    def test_unrepresented_union_or_result_does_not_keep_one_trigger_as_complete(self):
        card, _ = self.program("Bant Sojourners")
        for altered in (
            card.oracle_text.replace("and when this creature dies", "and when this creature phases out"),
            card.oracle_text.replace("when this creature dies", "when another creature dies"),
            card.oracle_text.replace("creature token.", "creature token, then draw a card."),
        ):
            ir = compile_oracle_card(replace(card, oracle_text=altered), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, altered)
            self.assertFalse(any(node.exact and node.event in {CYCLE + ".self", DEATH + ".self"}
                                 for face in ir.faces for node in face.nodes), altered)

    def cycle(self, game, actor, source):
        self.priority(game, actor)
        rows = game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in rows if row["id"].startswith("activate:" + source.ref + ":")]
        result = game.act("pilot:" + actor, {"action_id": action, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual("activated_ability", game.state.stack[0].kind)

    def target_ref(self, name, source, bear):
        return {
            "Bant Sojourners": None,
            "Esper Sojourners": bear.ref,
            "Grixis Sojourners": source.ref,
            "Jund Sojourners": "D",
            "Naya Sojourners": bear.ref,
        }[name]

    def choose_result(self, game, source, *, actor, event, target, choice):
        if target is not None:
            self.target(game, actor, [target])
        trigger = game.state.stack[-1]
        self.assertEqual(("triggered_ability", actor, event, source.object_id),
                         (trigger.kind, trigger.controller, trigger.context.get("event"), trigger.source_object_id))
        self.assertEqual([target] if target is not None else [], list(trigger.targets))
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", [actor]), (pending.kind, pending.actors))
        command = {"action_id": "choose", "choice": choice}
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:C", command)
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        accepted = game.act("pilot:" + actor, command)
        self.assertTrue(accepted.ok, accepted.summary)
        return game

    def assert_result(self, game, name, source, bear, *, actor, incarnation, life, choice):
        if name == "Bant Sojourners":
            token, = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
            self.assertEqual(actor, token.controller)
            values = game.engine._effective_card_data(token)
            self.assertEqual(["W"], values["colors"])
            self.assertIn("Soldier", values["type_line"])
            self.assertEqual((1, 1), tuple(game.engine._numeric_stat(token.object_id, stat)
                                          for stat in ("power", "toughness")))
        elif name == "Esper Sojourners":
            self.assertEqual(choice == "tap", game.state.cards[bear.object_id].tapped)
        elif name == "Grixis Sojourners":
            self.assertEqual("exile", game.state.cards[source.object_id].zone)
            self.assertEqual("A", game.state.cards[source.object_id].owner)
        elif name == "Jund Sojourners":
            event, = game.engine._current_turn_history("player_damaged")
            self.assertEqual((actor, incarnation, "D", 1),
                             (event.actor, event.object_incarnation, event.target, event.amount))
            self.assertEqual(life - 1, game.state.players["D"].life)
        else:
            self.assertEqual(1, game.state.cards[bear.object_id].counters.get("+1/+1", 0))

    def test_actual_all_five_cycling_results_resolve_before_the_separate_draw(self):
        for index, (name, color, choice, _) in enumerate(CASES):
            with self.subTest(name=name):
                game = self.session(9344700 + index)
                source = self.add(game.engine, name, zone="hand")
                bear = self.add(game.engine, "Grizzly Bears", seat="B")
                self.assert_closed(game, source)
                self.prepare(game, mana={"C": 2, color: 1})
                self.checkpoint(game)
                life = game.state.players["D"].life
                before = list(game.state.players["A"].draw_history)
                self.cycle(game, "A", source)
                incarnation = game.state.cards[source.object_id].logical_object_id
                game = self.choose_result(game, source, actor="A", event=CYCLE,
                                          target=self.target_ref(name, source, bear), choice=choice)
                self.assert_result(game, name, source, bear, actor="A", incarnation=incarnation,
                                   life=life, choice=choice)
                self.assertEqual(before, game.state.players["A"].draw_history)
                self.assertEqual(["activated_ability"], [item.kind for item in game.state.stack])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(len(before) + 1, len(game.state.players["A"].draw_history))
                self.replay(game, load=True)

    def test_actual_all_five_dying_results_use_the_borrowed_creatures_previous_controller(self):
        for index, (name, _, _, choice) in enumerate(CASES):
            with self.subTest(name=name):
                game = self.session(9344720 + index)
                source = self.add(game.engine, name)
                game.engine.change_control(source.object_id, "B", reason="Prepare a borrowed dying source")
                original = source.logical_object_id
                bear = self.add(game.engine, "Grizzly Bears", seat="B")
                bear.tapped = choice == "untap"
                murder = self.add(game.engine, "Murder", seat="C", zone="hand")
                for card in (source, murder):
                    self.assert_closed(game, card)
                self.prepare(game)
                game.state.players["C"].mana_pool.update({"C": 1, "B": 2})
                self.checkpoint(game)
                life = game.state.players["D"].life
                self.cast_for(game, "C", murder, [source.ref])
                self.resolve_top(game)
                self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                game = self.choose_result(game, source, actor="B", event=DEATH,
                                          target=self.target_ref(name, source, bear), choice=choice)
                self.assert_result(game, name, source, bear, actor="B", incarnation=original,
                                   life=life, choice=choice)
                self.assertFalse(game.state.stack)
                self.replay(game, load=True)

    def test_actual_cycling_remains_legal_without_any_target_for_its_other_trigger(self):
        game = self.session(9344740)
        naya = self.add(game.engine, "Naya Sojourners", zone="hand")
        self.assert_closed(game, naya)
        self.prepare(game, mana={"C": 2, "G": 1})
        self.checkpoint(game)
        before = len(game.state.players["A"].draw_history)
        self.cycle(game, "A", naya)
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertEqual(["activated_ability"], [item.kind for item in game.state.stack])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before + 1, len(game.state.players["A"].draw_history))
        self.replay(game, load=True)

    def test_actual_invalidated_graveyard_target_leaves_the_underlying_cycling_draw(self):
        game = self.session(9344750)
        grixis = self.add(game.engine, "Grixis Sojourners", zone="hand")
        cremate = self.add(game.engine, "Cremate", seat="B", zone="hand")
        for card in (grixis, cremate):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "B": 1})
        game.state.players["B"].mana_pool["B"] = 1
        self.checkpoint(game)
        before = len(game.state.players["A"].draw_history)
        self.cycle(game, "A", grixis)
        self.target(game, "A", [grixis.ref])
        self.assertEqual([grixis.ref], list(game.state.stack[-1].targets))
        self.cast_for(game, "B", cremate, [grixis.ref])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[grixis.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before + 1, len(game.state.players["A"].draw_history))
        self.replay(game, load=True)
