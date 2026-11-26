"""Native Birgi retained mana and boast permissions with the Harnfel face."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.mana_provenance import mana_provenance_lots
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.boast_allowance import CAPABILITY as BOAST_CAPABILITY
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.turn_retained_resolution_mana import CAPABILITY as MANA_CAPABILITY
from scripts.build_test_database import build_fixture_database


ORIGINAL = "Birgi, God of Storytelling // Harnfel, Horn of Bounty"
FRONT = "Birgi, God of Storytelling"
BACK = "Harnfel, Horn of Bounty"


class BirgiHarnfelTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "birgi-harnfel.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/birgi-harnfel-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Cast and boast witness", [DeckEntry(commander, 1, "commander"),
                                                            DeckEntry("Plains", 30)], [commander])

    def okay(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def actions(self, game, seat="A"):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def activate(self, game, source):
        action, = (row for row in self.actions(game) if row["id"].startswith("activate:" + source.ref + ":"))
        return {"action_id": action["id"], "pay": "auto"}

    def test_birgi_exact_face_owners_reject_tampered_event_and_activation_permission(self):
        game = self.session(70214200)
        source = self.add(game.engine, ORIGINAL)
        self.assert_closed(game, source)
        ir = compile_oracle_card(self.db.by_oracle_id(source.oracle_id), capability_registry=self.registry,
                                 capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual({FRONT, BACK}, {face.face_id for face in ir.faces})
        for capability, invalid in ((MANA_CAPABILITY, {"event_condition": None}),
                                    (BOAST_CAPABILITY, {"active_zone": "graveyard"})):
            with self.subTest(capability=capability):
                program, = (p for p in game.engine.semantics.programs_for_oracle(source.oracle_id)
                            if capability in p.capability_dependencies)
                result = bind_semantic_program_runtime(replace(program, **invalid),
                    capability_registry=self.registry, profile="commander_review")
                self.assertFalse(result["strict"], result)

    def test_actual_birgi_own_cast_resolves_on_stack_and_red_survives_until_cleanup(self):
        game = self.session(70214201)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        artifact = self.add(game.engine, "Memnite", zone="hand")
        mountain = self.add(game.engine, "Mountain")
        self.prepare(game, mana={"C": 2, "R": 1})
        self.checkpoint(game)
        self.okay(game, "A", {"action_id": f"cast:{source.ref}", "face": FRONT, "pay": "manual",
                              "payment": {"C": 2, "R": 1}})
        self.resolve_top(game)
        self.assertEqual(FRONT, game.state.cards[source.object_id].active_face)
        self.okay(game, "A", self.activate(game, mountain))
        self.cast(game, "A", artifact)
        self.assertEqual(1, game.state.players["A"].mana_pool["R"])
        self.assertEqual(1, self.trigger_count(game, source))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(2, game.state.players["A"].mana_pool["R"])
        lot, = mana_provenance_lots(game.state.players["A"])
        self.assertEqual(("end_of_turn", (("R", 1),)), (lot.retention, lot.bundle))
        self.resolve_top(game)
        self.until(game, lambda s: s.state.step == "beginning_combat")
        self.assertEqual(1, game.state.players["A"].mana_pool["R"])
        self.until(game, lambda s: s.state.step == "end_step")
        self.assertEqual(1, game.state.players["A"].mana_pool["R"])
        self.until(game, lambda s: s.state.active_player != "A")
        self.assertEqual(0, game.state.players["A"].mana_pool["R"])
        self.replay(game, load=True)

    def test_actual_birgi_keeps_attack_requirement_and_limits_boast_to_two_with_replay(self):
        game = self.session(70214202)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        attacker = self.add(game.engine, "Duskwielder")
        attacker.acquired_control_turn_count = -1
        for index in range(3):
            self.add(game.engine, "Swamp", ref=f"boast-price-{index}")
        self.prepare(game, mana={"C": 2, "R": 1})
        self.checkpoint(game)
        self.okay(game, "A", {"action_id": f"cast:{source.ref}", "face": FRONT, "pay": "manual",
                              "payment": {"C": 2, "R": 1}})
        self.resolve_top(game)
        self.assertEqual(FRONT, game.state.cards[source.object_id].active_face)
        ability, = [row for row in game.engine._activated_abilities(attacker) if row.boast]
        command = {"action_id": f"activate:{attacker.ref}:{ability.ability_id}", "targets": ["C"], "pay": "auto"}
        self.reject(game, "A", command)
        self.attack(game, [(attacker, "B")])
        self.priority(game, "A")
        self.okay(game, "A", command)
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.priority(game, "A")
        self.okay(game, "A", command)
        self.resolve_top(game)
        self.priority(game, "A")
        self.reject(game, "A", command)
        self.assertEqual((42, 38), (game.state.players["A"].life, game.state.players["C"].life))
        self.replay(game, load=True)

    def test_actual_harnfel_discard_and_legal_land_choice_remain_private_then_public(self):
        game = self.session(70214203)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        paid = self.add(game.engine, "Plains", zone="hand", ref="harnfel-private-price")
        chosen = self.add(game.engine, "Mountain", zone="library", ref="impulse-first")
        other = self.add(game.engine, "Plains", zone="library", ref="impulse-second")
        self.prepare(game, mana={"C": 4, "R": 1})
        self.checkpoint(game)
        self.okay(game, "A", {"action_id": f"cast:{source.ref}:face:{BACK}", "face": BACK, "pay": "auto"})
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertEqual(("battlefield", BACK), (current.zone, current.active_face))
        command = {**self.activate(game, current), "cost_cards": [paid.ref]}
        self.reject(game, "B", command)
        self.assertNotIn(paid.ref, json.dumps(game.packet("spectator", full=True)))
        self.okay(game, "A", command)
        self.assertEqual("graveyard", game.state.cards[paid.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        for card in (chosen, other):
            self.assertEqual(("exile", False), (game.state.cards[card.object_id].zone,
                                              game.state.cards[card.object_id].face_down))
        offered = {row["id"] for row in self.actions(game)}
        self.assertTrue({f"play-land:{chosen.ref}", f"play-land:{other.ref}"} <= offered)
        self.okay(game, "A", {"action_id": f"play-land:{chosen.ref}"})
        self.reject(game, "A", {"action_id": f"play-land:{other.ref}"})
        self.assertEqual("exile", game.state.cards[other.object_id].zone)
        self.replay(game, load=True)
