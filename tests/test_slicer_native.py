"""Native casting, upkeep handoff, combat conversion, and replay for Slicer."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.characteristic_evaluation import type_parts
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_quantity_reductions import public_mana_value
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.combat_metadata import active_goading_players
from scripts.build_test_database import build_fixture_database
import test_bounded_hand_casts as hand_cast
from test_controller_creature_keyword_absence import register_card


FRONT = "Slicer, Hired Muscle"
BACK = "Slicer, High-Speed Antagonist"
CARD = FRONT + " // " + BACK
PROFILE = "commander_review"


class SlicerNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = hand_cast.BoundedHandCastRuntimeTests.start

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "slicer.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "slicer-hired-muscle-cards.json",
            fixtures / "bound-effect-program-cards.json",
            fixtures / "bounded-hand-cast-cards.json",
            fixtures / "damage-triggered-extra-combat-cards.json",
            fixtures / "delayed-object-action-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Slicer two-face combat",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def ready(self, game, *cards, mana=None):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def accept(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def effective(self, game, card):
        return game.engine._effective_card_data(self.current(game, card))

    def cast_face(self, game, source, *, converted, reserve=None):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        option = "more_than_meets_the_eye" if converted else "normal"
        choices = [
            (action, price) for action in actions
            if action.get("card") == source.ref or action["id"] == "cast:" + source.ref
            for price in action.get("cost_options", ()) if price["id"] == option
        ]
        self.assertEqual(1, len(choices), (option, choices))
        (action, price), = choices
        self.assertEqual(3 if converted else 5, sum(price["requirements"].values()))
        command = {"action_id": action["id"], "cost_option": price["id"], "pay": "auto"}
        if reserve is not None:
            command["reserve"] = reserve
        if converted:
            self.assertEqual((BACK, "alternate"), (action["face"], price["kind"]))
            command["face"] = action["face"]
        self.accept(game, "A", command)
        item, = game.state.stack
        self.assertEqual(("spell", source.object_id, "A"), (item.kind, item.card_object_id, item.controller))
        if converted:
            self.assertEqual(BACK, item.chosen_face)
        else:
            self.assertIn(item.chosen_face, (None, FRONT))
        stack_data = self.effective(game, source)
        self.assertEqual(5, public_mana_value(stack_data))
        self.assertEqual(not converted, "creature" in type_parts(stack_data["type_line"])[0])
        self.assertIsNone(self.resolve(game))
        current = self.current(game, source)
        self.assertEqual(("battlefield", BACK if converted else FRONT), (current.zone, current.active_face))
        self.assertEqual(5, public_mana_value(self.effective(game, source)))

    def start_upkeep(self, game, seat, source):
        self.until(game, lambda current: current.state.active_player == seat and current.state.step == "upkeep")
        trigger, = game.state.stack
        self.assertEqual(("triggered_ability", "A", source.object_id),
                         (trigger.kind, trigger.controller, trigger.source_object_id))
        self.assertFalse(trigger.targets)

    def upkeep_choice(self, game, seat):
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        schema = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal_actions"][0]["choice_schema"]
        self.assertEqual({"apply", "decline"}, set(schema["legal_values"]))
        self.reject(game, seat, {"action_id": "choose", "choice": "apply"})

    def sacrifice_offer(self, game, seat, spell):
        self.priority(game, seat)
        offer = self.action(game, seat, spell)
        price, = offer["cost_options"]
        return offer["id"], price["id"], set(price["choice_schema"]["sacrifice_cards"]["legal_refs"])

    def attackers(self, game, seat):
        self.until(game, lambda current: current.state.active_player == seat
                   and current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.attackers")

    def delayed_count(self, game, source):
        return sum(trigger.active and trigger.source_object_id == source.object_id
                   for trigger in game.state.delayed_triggers)

    def end_combat(self, game, count):
        self.until(game, lambda current: current.state.step == "end_combat" and len(current.state.stack) == count
                   and current.state.pending_decision is not None and current.state.pending_decision.kind == "priority")
        self.assertTrue(all(item.kind == "triggered_ability" and item.controller == "A" and not item.targets
                            for item in game.state.stack))

    def test_complete_original_binds_both_faces_and_casting_siblings(self):
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        original = self.db.lookup(CARD, fuzzy=False)
        compiled = compile_oracle_card(original, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        self.assertEqual(2, len(compiled.faces))
        programs = semantics.programs_for_oracle(original.oracle_id)
        front, = (program for program in programs if program.event == "step.begin")
        back, = (program for program in programs if program.event == "damage.dealt.self")
        self.assertIsNone(front.target_schema)
        self.assertIsNone(back.target_schema)

        faces = tuple({**face, "oracle_text": face["oracle_text"].replace("can't be sacrificed", "can't be destroyed")}
                      for face in original.faces)
        modified = replace(original, faces=faces,
                           oracle_text=original.oracle_text.replace("can't be sacrificed", "can't be destroyed"))
        self.assertNotEqual("exact", compile_oracle_card(
            modified, capability_registry=self.registry, capability_profile=PROFILE,
        ).status)

    def test_actual_upkeep_handoff_uses_the_trigger_controller_and_expires_before_its_goad(self):
        game = self.start(70128001)
        source = self.add(game.engine, CARD, zone="hand", ref="slicer-handoff")
        self.add(game.engine, "Swamp", seat="B", ref="slicer-borrower-mana")
        self.add(game.engine, "Swamp", ref="slicer-original-controller-mana")
        other = self.add(game.engine, "Grizzly Bears", seat="B")
        borrowed_cost = self.add(game.engine, "Village Rites", seat="B", zone="hand", ref="slicer-borrowed-sacrifice")
        original_cost = self.add(game.engine, "Village Rites", zone="hand", ref="slicer-original-sacrifice")
        self.ready(game, source, borrowed_cost, original_cost, mana={"C": 4, "R": 1})
        self.cast_face(game, source, converted=False)
        self.assertEqual((3, 4), tuple(game.engine._numeric_stat(source.object_id, stat) for stat in ("power", "toughness")))
        self.assertLessEqual({"double strike", "haste"}, set(game.engine._combat_keywords(self.current(game, source))))
        self.attackers(game, "A")
        self.accept(game, "A", {"action_id": "attack", "attackers": {source.ref: "D"}})
        self.start_upkeep(game, "B", source)
        original = self.current(game, source)
        identity = original.logical_object_id
        self.assertTrue(original.tapped)
        self.upkeep_choice(game, "B")

        game = self.replay(game, load=True)
        self.accept(game, "A", {"action_id": "choose", "choice": "apply"})
        self.assertIsNone(self.resolve(game))
        current = self.current(game, source)
        self.assertEqual((identity, "A", "B", False), (current.logical_object_id, current.owner, current.controller, current.tapped))
        self.assertEqual(("A",), active_goading_players(game.engine, current))
        action_id, price, refs = self.sacrifice_offer(game, "B", borrowed_cost)
        self.assertEqual({other.ref}, refs)
        use_cost = {"action_id": action_id, "cost_option": price, "pay": "auto"}
        self.reject(game, "B", {**use_cost, "sacrifice_cards": [source.ref]})
        self.accept(game, "B", {**use_cost, "sacrifice_cards": [other.ref]})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.current(game, other).zone)
        self.assertEqual("battlefield", self.current(game, source).zone)

        self.attackers(game, "B")
        self.reject(game, "B", {"action_id": "attack", "attackers": {}})
        self.reject(game, "B", {"action_id": "attack", "attackers": {source.ref: "A"}})
        self.accept(game, "B", {"action_id": "attack", "attackers": {source.ref: "C"}})
        self.start_upkeep(game, "C", source)
        self.assertEqual(34, game.state.players["C"].life)
        self.assertEqual("A", self.current(game, source).controller)
        self.assertEqual(("A",), active_goading_players(game.engine, self.current(game, source)))
        self.assertIn(source.ref, self.sacrifice_offer(game, "A", original_cost)[2])
        self.upkeep_choice(game, "C")
        self.accept(game, "A", {"action_id": "choose", "choice": "decline"})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(BACK, self.current(game, source).active_face)

        loan_turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.active_player == "A" and current.state.turn_sequence > loan_turn)
        self.assertEqual((), active_goading_players(game.engine, self.current(game, source)))
        self.assertIn("creature", type_parts(self.effective(game, source)["type_line"])[0])
        self.replay(game)

    def test_actual_upkeep_decline_converts_the_original_without_handoff(self):
        game = self.session(70128002)
        source = self.add(game.engine, CARD, zone="hand", ref="slicer-declined")
        self.ready(game, source, mana={"C": 4, "R": 1})
        self.cast_face(game, source, converted=False)
        self.attackers(game, "A")
        self.accept(game, "A", {"action_id": "attack", "attackers": {source.ref: "C"}})
        self.start_upkeep(game, "B", source)
        current = self.current(game, source)
        identity = current.logical_object_id
        before = current.transform_count
        self.assertTrue(current.tapped)
        self.upkeep_choice(game, "B")
        command = {"action_id": "choose", "choice": "decline"}
        self.reject(game, "B", command)
        self.accept(game, "A", command)
        self.assertIsNone(self.resolve(game))
        current = self.current(game, source)
        self.assertEqual((identity, before + 1, "A", BACK, True),
                         (current.logical_object_id, current.transform_count, current.controller, current.active_face, current.tapped))
        self.assertNotIn("creature", type_parts(self.effective(game, source)["type_line"])[0])
        self.assertEqual((), active_goading_players(game.engine, current))
        self.replay(game)

    def test_actual_converted_cast_deals_player_damage_and_converts_at_end_of_combat(self):
        game = self.session(70128003)
        source = self.add(game.engine, CARD, zone="hand", ref="slicer-delayed-convert")
        self.ready(game, source, mana={"C": 2, "R": 1})
        self.cast_face(game, source, converted=True)
        current = self.current(game, source)
        self.assertEqual((3, 2), tuple(game.engine._numeric_stat(current.object_id, stat) for stat in ("power", "toughness")))
        self.assertLessEqual({"artifact", "creature"}, type_parts(self.effective(game, source)["type_line"])[0])
        keywords = set(game.engine._combat_keywords(current))
        self.assertLessEqual({"first strike", "haste"}, keywords)
        self.assertNotIn("double strike", keywords)
        identity = current.logical_object_id
        epoch = current.transform_count

        self.attackers(game, "A")
        self.accept(game, "A", {"action_id": "attack", "attackers": {source.ref: "B"}})
        self.until(game, lambda current: self.delayed_count(current, source) == 1)
        self.assertEqual(37, game.state.players["B"].life)
        self.assertEqual(BACK, self.current(game, source).active_face)
        self.assertNotEqual("end_combat", game.state.step)

        game = self.replay(game, load=True)
        self.end_combat(game, 1)
        self.assertEqual(BACK, self.current(game, source).active_face)
        self.assertIsNone(self.resolve(game))
        current = self.current(game, source)
        self.assertEqual((identity, epoch + 1, FRONT), (current.logical_object_id, current.transform_count, current.active_face))
        self.replay(game)

    def test_actual_delayed_conversion_rejects_reentry_and_intervening_conversion(self):
        for index, intervention in enumerate(("reentry", "earlier conversion")):
            with self.subTest(intervention=intervention):
                game = self.session(70128010 + index)
                source = self.add(game.engine, CARD, zone="hand")
                if intervention == "reentry":
                    blink = self.add(game.engine, "Cloudshift", zone="hand")
                    self.add(game.engine, "Plains")
                    self.ready(game, source, blink, mana={"C": 2, "R": 1})
                else:
                    double_strike = self.add(game.engine, "Assault Strobe", zone="hand")
                    self.ready(game, source, double_strike, mana={"C": 2, "R": 2})
                reserve = {"R": 1} if intervention == "earlier conversion" else None
                self.cast_face(game, source, converted=True, reserve=reserve)
                identity = self.current(game, source).logical_object_id
                epoch = self.current(game, source).transform_count
                if intervention == "earlier conversion":
                    self.cast(game, "A", double_strike, [source.ref])
                    self.assertIsNone(self.resolve(game))
                    self.assertIn("double strike", game.engine._combat_keywords(self.current(game, source)))

                self.attackers(game, "A")
                self.accept(game, "A", {"action_id": "attack", "attackers": {source.ref: "B"}})
                delay_count = 1 if intervention == "reentry" else 2
                self.until(game, lambda current: self.delayed_count(current, source) == delay_count)
                self.assertEqual(40 - 3 * delay_count, game.state.players["B"].life)
                if intervention == "reentry":
                    self.cast(game, "A", blink, [source.ref])
                    self.resolve_top(game)
                    current = self.current(game, source)
                    self.assertEqual(FRONT, current.active_face)
                    self.assertNotEqual(identity, current.logical_object_id)
                    identity, epoch = current.logical_object_id, current.transform_count

                self.end_combat(game, delay_count)
                if intervention == "earlier conversion":
                    self.resolve_top(game)
                    self.assertEqual((FRONT, epoch + 1), (self.current(game, source).active_face,
                                                         self.current(game, source).transform_count))
                    epoch += 1
                self.assertIsNone(self.resolve(game))
                current = self.current(game, source)
                self.assertEqual((identity, epoch, FRONT), (current.logical_object_id, current.transform_count, current.active_face))
                self.replay(game)
