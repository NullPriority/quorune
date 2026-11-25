"""Phase out the same declared attacker at the real end of combat."""

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
from quorune.phasing_model import PHASE_OUT_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


VEIL = "Teferi's Veil"


class ControlledAttackerDelayedPhaseOutTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controlled-attacker-delayed-phase-out.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controlled-attacker-delayed-phase-out-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Controlled attacker delayed phasing", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_uses_one_untargeted_attacker_and_existing_delayed_phasing(self):
        card = self.db.lookup(VEIL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, VEIL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "creature.attacks"]
        self.assertEqual("battlefield", program.active_zone)
        self.assertIsNone(program.target_schema)
        self.assertEqual(({"op": "schedule_fixed_object_action", "schema_version": 7,
                           "object": "$event.permanent_object", "action": "phase_out",
                           "timing": "end_of_combat"},), tuple(program.effects))
        self.assertLessEqual({"trigger.delayed.fixed_object_action", PHASE_OUT_CAPABILITY,
                              "resolution.event_object.fixed_result"}, set(program.capability_dependencies))
        for text in (
            card.oracle_text.replace("a creature you control", "a creature an opponent controls"),
            card.oracle_text.replace("phases out at end of combat", "phases out at the next end step"),
            card.oracle_text.replace("it phases out", "target creature phases out"),
            card.oracle_text + " Draw a card.",
        ):
            self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=text),
                                capability_registry=self.registry).status, text)

    def test_raw_phase_schedule_requires_the_controlled_attack_and_unchanged_recipient_kind(self):
        card = self.db.lookup(VEIL, fuzzy=False)
        _, semantics = register_card(self.db, self.registry, VEIL)
        program, = [item for item in semantics.programs_for_oracle(card.oracle_id) if item.event == "creature.attacks"]
        effect, = program.effects
        retained = tuple(key for key in program.capability_dependencies if key != PHASE_OUT_CAPABILITY)
        for changed in (
            replace(program, event_condition=None),
            replace(program, event="creature.becomes_blocked"),
            replace(program, effects=({**effect, "object": "$source.zone_object"},)),
            replace(program, effects=({**effect, "timing": "next_end_step"},)),
            replace(program, coverage=(), capability_dependencies=retained,
                    capability_closure=self.registry.closure(retained, profile="commander_review").to_dict()),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                                                  profile="commander_review")
            self.assertFalse(bound["strict"], bound)

    def test_actual_attacks_phase_only_the_unchanged_attacker_after_combat_and_return_next_untap(self):
        game = self.session(61341601)
        engine = game.engine
        veil = self.add(engine, VEIL, zone="hand")
        warp = self.add(engine, "Time Warp", zone="hand")
        recon = self.add(engine, "Reconnaissance")
        haste = self.add(engine, "Fervor")
        cloudshift = self.add(engine, "Cloudshift", zone="hand")
        disenchant = self.add(engine, "Disenchant", seat="C", zone="hand")
        bearer = self.add(engine, "Grizzly Bears", ref="veil-unchanged-attacker")
        changed = self.add(engine, "Savannah Lions", ref="veil-returned-attacker")
        lethal = self.add(engine, "Goblin Piker", ref="veil-lethal-attacker")
        blocker = self.add(engine, "Grizzly Bears", seat="B", ref="veil-blocker")
        secret = self.add(engine, "Counterspell", seat="D", zone="hand", ref="veil-private-hand")
        for index in range(5):
            self.add(engine, "Plains", ref=f"veil-cloudshift-plains-{index}")
        for index in range(2):
            self.add(engine, "Plains", seat="C", ref=f"veil-removal-plains-{index}")
        for card in (veil, warp, recon, haste, cloudshift, disenchant):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 8, "U": 6, "W": 6})
        self.checkpoint(game)
        self.cast_for(game, "A", veil)
        self.resolve_top(game)
        self.cast_for(game, "A", warp, ["A"])
        self.resolve_top(game)
        next_turn, = game.state.extra_turns
        bearer_incarnation = game.state.cards[bearer.object_id].logical_object_id
        changed_incarnation = game.state.cards[changed.object_id].logical_object_id

        self.attack(game, [(bearer, "B"), (changed, "C"), (lethal, "B")])
        self.assertEqual(3, self.trigger_count(game, veil))
        for _ in range(3):
            self.resolve_top(game)
        self.assertFalse(game.state.cards[bearer.object_id].phased_out)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in actions if row["id"].startswith("activate:" + recon.ref + ":")]
        result = game.act("pilot:A", {"action_id": action["id"], "targets": [bearer.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertNotIn(bearer.object_id, game.state.combat.attackers)
        self.cast_for(game, "A", cloudshift, [changed.ref])
        self.resolve_top(game)
        self.assertNotEqual(changed_incarnation, game.state.cards[changed.object_id].logical_object_id)
        self.cast_for(game, "C", disenchant, [veil.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[veil.object_id].zone)

        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        result = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: lethal.ref}})
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda current: current.state.step == "end_combat"
                   and current.state.pending_decision.kind == "trigger.order")
        self.assertEqual("graveyard", game.state.cards[lethal.object_id].zone)
        self.assertFalse(game.state.cards[bearer.object_id].phased_out)
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
        self.assertEqual(3, len(rows))
        command = {"action_id": "order", "triggers": [row["id"] for row in rows]}
        state = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(state, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[bearer.object_id].phased_out, "Only the unchanged attacker phases out")
        self.assertEqual(("battlefield", bearer_incarnation),
                         (game.state.cards[bearer.object_id].zone, game.state.cards[bearer.object_id].logical_object_id))
        self.assertFalse(game.state.cards[changed.object_id].phased_out)
        self.assertFalse(game.state.cards[lethal.object_id].phased_out)
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(secret.ref, json.dumps(game.packet(viewer, full=True)))
        self.until(game, lambda current: current.state.current_turn is not None
                   and current.state.current_turn.turn_id == next_turn.turn_id)
        self.assertFalse(game.state.cards[bearer.object_id].phased_out)
        self.assertEqual(bearer_incarnation, game.state.cards[bearer.object_id].logical_object_id)
        self.replay(game, load=True)

    def test_removing_the_actual_delayed_phase_result_breaks_the_unchanged_attacker_witness(self):
        with patch("quorune.phasing.phase_out_permanent", return_value=None):
            with self.assertRaisesRegex(AssertionError, "Only the unchanged attacker"):
                self.test_actual_attacks_phase_only_the_unchanged_attacker_after_combat_and_return_next_untap()
