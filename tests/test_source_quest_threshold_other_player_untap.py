"""CR 502.3 adds only controlled creatures after the exact source quest threshold."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.turn_step_owner import TURN_STEPS
from quorune.untap_step import SOURCE_QUEST_UNTAP_CAPABILITY as CAPABILITY, SOURCE_QUEST_UNTAP_SCOPE
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Quest for Renewal"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class SourceQuestThresholdOtherPlayerUntapTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "source-quest-untap.sqlite3"
        build_fixture_database([basic, FIXTURES / "source-quest-threshold-other-player-untap-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Quest threshold untap", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        record = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        return record, {program.event: program for program in semantics.programs_for_oracle(record.oracle_id)}

    def test_complete_original_keeps_tap_trigger_and_exact_other_players_creature_untap(self):
        record, programs = self.programs()
        self.assertEqual({"permanent.tap", "untap.step"}, set(programs))
        self.assertIn("counter.producer.optional_fixed_event_trigger", programs["permanent.tap"].capability_dependencies)
        program = programs["untap.step"]
        self.assertIn(CAPABILITY, program.capability_dependencies)
        handler, = program.handlers
        self.assertEqual(["creature"], handler["subject"]["predicate"]["types_all"])
        self.assertEqual("other_player", handler["condition"]["turn_relation"])
        self.assertEqual(
            {"scope": SOURCE_QUEST_UNTAP_SCOPE, "condition": {
                "schema_version": 1, "kind": "source_counter_at_least", "amount": 4, "counter_name": "quest",
            }},
            handler["public_condition"],
        )
        for text in (
            record.oracle_text.replace("four or more", "three or more"),
            record.oracle_text.replace("quest counters", "charge counters"),
            record.oracle_text.replace("untap all creatures", "untap all permanents"),
            record.oracle_text.replace("each other player's untap step", "your untap step"),
        ):
            ir = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                    capability_profile=PROFILE)
            self.assertFalse(any(CAPABILITY in node.capability_dependencies
                                 for face in ir.faces for node in face.nodes))

    def test_raw_source_threshold_rejects_recipient_or_counter_widening_and_requires_its_owner(self):
        _, programs = self.programs()
        program = programs["untap.step"]
        handler, = program.handlers
        condition = handler["public_condition"]
        for changed in (
            {**handler, "public_condition": {**condition, "condition": {**condition["condition"], "amount": 3}}},
            {**handler, "subject": {**handler["subject"], "controller_relation": "any"}},
            {**handler, "condition": {**handler["condition"], "turn_relation": "subject_controller"}},
        ):
            with self.assertRaises(ValueError):
                replace(program, handlers=(changed,))
        deps = [cap for cap in program.capability_dependencies if cap != CAPABILITY]
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def stage_end_step(self, game):
        self.prepare(game)
        engine = game.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.phase = "ending"
        engine.state.step = "end_step"
        engine.state.phase_index = TURN_STEPS.index(("ending", "end_step"))
        engine._grant_priority("A")
        engine.pump()

    def wait_for_other_upkeep(self, game):
        self.until(game, lambda session: session.state.active_player == "B" and session.state.step == "upkeep")

    def test_actual_creature_tap_choice_controls_next_players_simultaneous_untap(self):
        game = self.session(502360321)
        quest = self.add(game.engine, NAME)
        quest.counters["quest"] = 3
        elf = self.add(game.engine, "Llanowar Elves")
        elf.acquired_control_turn_count = -1
        titan = self.add(game.engine, "Deep-Slumber Titan")
        own_land = self.add(game.engine, "Forest", ref="renewal-own-land")
        active_land = self.add(game.engine, "Forest", seat="B", ref="renewal-active-land")
        other = self.add(game.engine, "Grizzly Bears", seat="C")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="renewal-private")
        for card in (titan, own_land, active_land, other):
            card.tapped = True
        for card in (quest, elf, titan):
            self.assert_closed(game, card)
        self.stage_end_step(game)
        self.checkpoint(game)
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in offers if row["id"].startswith(f"activate:{elf.ref}:")]
        tapped = game.act("pilot:A", {"action_id": action["id"]})
        self.assertTrue(tapped.ok, tapped.summary)
        self.assertTrue(game.state.cards[elf.object_id].tapped)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, list(pending.actors)))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "choice": "put"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        loaded = self.replay(game, load=True)

        for session, choice, expected in ((game, "decline", 3), (loaded, "put", 4)):
            chosen = session.act("pilot:A", {"action_id": "choose", "choice": choice})
            self.assertTrue(chosen.ok, chosen.summary)
            self.assertEqual(expected, session.state.cards[quest.object_id].counters["quest"])
            self.wait_for_other_upkeep(session)
            for card in (elf, titan):
                self.assertEqual(expected == 3, session.state.cards[card.object_id].tapped)
            self.assertFalse(session.state.cards[active_land.object_id].tapped)
            self.assertTrue(session.state.cards[own_land.object_id].tapped)
            self.assertTrue(session.state.cards[other.object_id].tapped)
            for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
                self.assertNotIn(private.ref, json.dumps(session.packet(viewer, full=True)))
            self.replay(session, load=True)

    def test_actual_source_departure_removes_other_player_untap_participation(self):
        game = self.session(502360322)
        quest = self.add(game.engine, NAME)
        quest.counters["quest"] = 4
        titan = self.add(game.engine, "Deep-Slumber Titan")
        titan.tapped = True
        bounce = self.add(game.engine, "Boomerang", seat="B", zone="hand")
        self.assert_closed(game, quest)
        first_hand_card = game.state.players["A"].zones["hand"][0]
        game.engine.move_card(first_hand_card, "library", log=False)
        self.stage_end_step(game)
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast_for(game, "B", bounce, [quest.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[quest.object_id].zone)
        self.wait_for_other_upkeep(game)
        self.assertTrue(game.state.cards[titan.object_id].tapped)
        self.replay(game, load=True)

    def test_native_untap_witness_kills_missing_threshold_participation_mutation(self):
        from quorune.semantic_runtime import conditional_untap

        original = conditional_untap.conditional_untap_applies

        def missing_threshold(host, source, descriptor):
            if descriptor.get("public_condition", {}).get("scope") == SOURCE_QUEST_UNTAP_SCOPE:
                return False
            return original(host, source, descriptor)

        with patch.object(conditional_untap, "conditional_untap_applies", side_effect=missing_threshold):
            with self.assertRaisesRegex(AssertionError, "False != True"):
                self.test_actual_creature_tap_choice_controls_next_players_simultaneous_untap()
