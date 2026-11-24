"""Phase the current land set through real successive Taniwha upkeeps."""

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
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


TANIWHA = "Taniwha"


class ControllerLandSetPhaseOutTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controlled-land-phase-out.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controller-land-set-phase-out-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Controller land phasing", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_phasing_trample_and_one_untargeted_land_set(self):
        card = self.db.lookup(TANIWHA, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, TANIWHA)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "step.begin"]
        self.assertEqual("battlefield", program.active_zone)
        self.assertIsNone(program.target_schema)
        self.assertEqual(({"op": "phase_out_permanent", "schema_version": 4, "subject": "controlled_land_set"},),
                         tuple(program.effects))
        ir = compile_oracle_card(card, capability_registry=self.registry)
        lines = [node.text for face in ir.faces for node in face.nodes]
        self.assertIn("Trample", lines)
        self.assertTrue(any(text.startswith("Phasing") for text in lines))
        for text in (
            card.oracle_text.replace("your upkeep", "each upkeep"),
            card.oracle_text.replace("lands you control", "creatures you control"),
            card.oracle_text.replace("all lands", "target land"),
            card.oracle_text + " Draw a card.",
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                capability_registry=self.registry).status, text)

    def test_raw_payload_rejects_other_steps_result_fields_and_missing_authority(self):
        card = self.db.lookup(TANIWHA, fuzzy=False)
        _, semantics = register_card(self.db, self.registry, TANIWHA)
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "step.begin"]
        effect, = program.effects
        retained = tuple(key for key in program.capability_dependencies if key != "permanent.phasing.fixed_phase_out")
        for changed in (
            replace(program, event_condition=None),
            replace(program, event="creature.attacks"),
            replace(program, effects=({**effect, "cards": ["A"]},)),
            replace(program, effects=({**effect, "subject": "source"},)),
            replace(program, coverage=(), capability_dependencies=retained,
                    capability_closure=self.registry.closure(retained, profile="commander_review").to_dict()),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                  profile="commander_review")
            self.assertFalse(bound["strict"], bound)

    def enter_upkeep(self, game, turn):
        self.until(game, lambda current: current.state.current_turn is not None
                   and current.state.current_turn.turn_id == turn.turn_id
                   and current.state.step == "upkeep"
                   and current.state.pending_decision.kind == "priority")

    def test_actual_phasing_opens_only_the_present_upkeep_and_moves_the_land_set_simultaneously(self):
        game = self.session(61341701)
        engine = game.engine
        opening_hand = tuple(game.state.players["A"].zones["hand"])
        for object_id in opening_hand[:-3]:
            engine.move_card(object_id, "library", position="bottom", log=False, semantic_events=False)
        source = self.add(engine, TANIWHA, zone="hand")
        stretch = self.add(engine, "Time Stretch", zone="hand")
        warp = self.add(engine, "Time Warp", zone="hand")
        growth = self.add(engine, "Wild Growth", zone="hand")
        first = self.add(engine, "Forest", ref="taniwha-attached-land")
        second = self.add(engine, "Island", ref="taniwha-other-land")
        foreign = self.add(engine, "Plains", seat="B", ref="taniwha-foreign-land")
        creature = self.add(engine, "Grizzly Bears", ref="taniwha-nonland")
        secret = self.add(engine, "Counterspell", seat="D", zone="hand", ref="taniwha-private-card")
        for card in (source, stretch, warp, growth):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 18, "U": 12, "G": 12})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        self.assertIn("trample", game.engine._combat_keywords(game.state.cards[source.object_id]))
        self.cast_for(game, "A", growth, [first.ref])
        self.resolve_top(game)
        self.assertEqual(first.object_id, game.state.cards[growth.object_id].attached_to)
        self.cast_for(game, "A", stretch, ["A"])
        self.resolve_top(game)
        self.cast_for(game, "A", warp, ["A"])
        self.resolve_top(game)
        first_extra, trigger_extra, return_extra = game.state.extra_turns
        incarnations = {card.object_id: game.state.cards[card.object_id].logical_object_id
                        for card in (first, second, growth, creature, foreign)}

        self.enter_upkeep(game, first_extra)
        self.assertTrue(game.state.cards[source.object_id].phased_out)
        self.assertFalse(game.state.stack)
        self.assertTrue(all(not game.state.cards[card.object_id].phased_out for card in (first, second, growth)))
        self.enter_upkeep(game, trigger_extra)
        self.assertFalse(game.state.cards[source.object_id].phased_out)
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertTrue(all(not game.state.cards[card.object_id].phased_out for card in (first, second, growth)))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual([True, True, True], [game.state.cards[card.object_id].phased_out
                         for card in (first, second, growth)], "All current controlled lands must phase out")
        self.assertFalse(game.state.cards[foreign.object_id].phased_out)
        self.assertFalse(game.state.cards[creature.object_id].phased_out)
        self.assertTrue(game.state.cards[growth.object_id].phasing_record.indirect)
        self.assertEqual(first.object_id, game.state.cards[growth.object_id].attached_to)
        self.assertEqual(incarnations, {obj: game.state.cards[obj].logical_object_id for obj in incarnations})
        self.assertTrue(all(game.state.cards[obj].zone == "battlefield" for obj in incarnations))
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(secret.ref, json.dumps(game.packet(viewer, full=True)))

        self.enter_upkeep(game, return_extra)
        self.assertTrue(game.state.cards[source.object_id].phased_out)
        self.assertFalse(game.state.stack)
        self.assertTrue(all(not game.state.cards[card.object_id].phased_out for card in (first, second, growth)))
        self.assertEqual(incarnations, {obj: game.state.cards[obj].logical_object_id for obj in incarnations})
        self.replay(game, load=True)

    def test_missing_land_set_is_killed_by_a_real_present_upkeep(self):
        from quorune import phasing

        original = phasing.phase_out_permanents

        def omit_upkeep_batch(host, identities, *, actor, reason):
            if host.state.step == "upkeep":
                return ()
            return original(host, identities, actor=actor, reason=reason)

        with patch.object(phasing, "phase_out_permanents", side_effect=omit_upkeep_batch):
            with self.assertRaisesRegex(AssertionError, "All current controlled lands must phase out"):
                self.test_actual_phasing_opens_only_the_present_upkeep_and_moves_the_land_set_simultaneously()
