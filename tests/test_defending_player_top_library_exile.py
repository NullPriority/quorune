"""One actual attack supplies its own original defending library."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.defending_player_library_exile import DEFENDING_PLAYER_EXILE_CAPABILITY, validate_defending_player_exile
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class DefendingPlayerTopLibraryExileTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "defending-player-top-library-exile.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/defending-player-top-library-exile-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Defending player exile", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def libraries(self, game, seats="ABCD"):
        return {seat: tuple(game.state.players[seat].zones["library"]) for seat in seats}

    def begin_attack(self, seed, *, short=False, bounced=False, planeswalker=False):
        game = self.session(seed)
        source = self.add(game.engine, "Ulamog, the Ceaseless Hunger")
        other = self.add(game.engine, "Grizzly Bears")
        for creature in (source, other):
            creature.acquired_control_turn_count = -1
        destination = "B"
        if planeswalker:
            walker = self.add(game.engine, "Jace Beleren", seat="B")
            walker.counters["loyalty"] = 3
            self.assert_closed(game, walker)
            destination = walker.ref
        response = self.add(game.engine, "Unsummon", seat="B", zone="hand") if bounced else None
        if response is not None:
            self.add(game.engine, "Island", seat="B")
            self.assert_closed(game, response)
        if short:
            for object_id in tuple(game.state.players["B"].zones["library"])[:-3]:
                game.engine.move_card(object_id, "graveyard", reason="Prepare a short defending library")
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, destination), (other, "C")])
        self.assertEqual(1, self.trigger_count(game, source))
        ability = game.state.stack[-1]
        self.assertEqual(("triggered_ability", "B", []),
                         (ability.kind, ability.context["defending_player"], ability.targets))
        return game, source, response

    def test_actual_attack_exiles_only_its_original_defender_face_up_before_combat_damage(self):
        for mode in ("full", "short", "bounced", "planeswalker"):
            with self.subTest(mode=mode):
                game, source, response = self.begin_attack(76339001 + len(mode), short=mode == "short",
                    bounced=mode == "bounced", planeswalker=mode == "planeswalker")
                before = self.libraries(game)
                hidden = game.state.cards[before["B"][-1]]
                for seat in "ABCD":
                    packet = json.dumps(game.packet("pilot:" + seat, full=True))
                    self.assertFalse(hidden.ref in packet, f"The top identity is visible to {seat} before exile")
                    self.assertFalse(hidden.object_id in packet, f"The physical ID is visible to {seat} before exile")
                if response is not None:
                    self.cast(game, "B", response, [source.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[source.object_id].zone)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                count = min(20, len(before["B"]))
                exiled = tuple(game.state.cards[object_id] for object_id in before["B"][-count:])
                self.assertTrue(all(card.zone == "exile" and not card.face_down
                                    and "temporary_play_permission" not in card.annotations for card in exiled))
                self.assertEqual({**before, "B": before["B"][:-count]}, self.libraries(game))
                self.assertTrue(game.state.players["B"].in_game)
                self.assertEqual([40] * 4, [game.state.players[seat].life for seat in "ABCD"])
                self.replay(game, load=True)

    def test_actual_departed_defender_leaves_every_active_library_alone(self):
        game, source, _ = self.begin_attack(76339019)
        self.priority(game, "B")
        result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.players["B"].in_game)
        self.assertEqual(1, self.trigger_count(game, source))
        before = self.libraries(game, seats="ACD")
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(before, self.libraries(game, seats="ACD"))
        self.replay(game, load=True)

    def test_complete_original_requires_its_original_untargeted_attack_role(self):
        cap = DEFENDING_PLAYER_EXILE_CAPABILITY
        report, semantics = register_card(self.db, self.registry, "Ulamog, the Ceaseless Hunger")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        programs = semantics.programs_for_oracle(report["oracle_id"])
        program, = (row for row in programs if cap in row.capability_dependencies)
        self.assertIsNone(program.target_schema)
        effect, = program.effects
        validate_defending_player_exile(effect)
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        absent = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        card = self.db.lookup("Ulamog, the Ceaseless Hunger", fuzzy=False)
        for old, new in (("defending player", "each opponent"),
                         ("top twenty cards", "top nineteen cards"),
                         ("of their library.", "of their library. Rewrite history.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)
