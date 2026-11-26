"""Native ordered current-thirds life loss and three scoped APNAP rounds."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.third_player_rounds import THIRD_PLAYER_ROUNDS_CAPABILITY
from quorune.third_quantity_model import ThirdQuantitySpec
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class RoundedThirdPlayerRoundsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "rounded-thirds.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/rounded-third-player-rounds-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Rounded thirds", [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)],
                                  [commander])

    def scene(self, seed):
        game = self.session(seed)
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"]):
                game.engine.move_card(object_id, "library", log=False)
        return game, self.add(game.engine, "Pox", zone="hand")

    def ready(self, game, pox):
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, pox)
        self.prepare(game, mana={"B": 3})
        self.checkpoint(game)
        self.cast(game, "A", pox)
        self.assertEqual(0, game.state.players["A"].mana_pool["B"])

    def stage(self, game, seat, count, options):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("choice.apnap", [seat]), (pending.kind, pending.actors))
        context = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertEqual(count, context["count"])
        self.assertEqual({card.ref for card in options}, set(context["options"]))
        return context

    def choose(self, game, seat, selected):
        result = game.act("pilot:" + seat, {"action_id": "choose", "cards": [card.ref for card in selected]})
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, selected):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": "choose", "cards": [card.ref for card in selected]})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def zones(self, game, cards):
        return [game.state.cards[card.object_id].zone for card in cards]

    def test_paid_pox_recalculates_lands_after_creature_sacrifice_replaced_by_exile(self):
        game, pox = self.scene(708091601)
        rest = self.add(game.engine, "Rest in Peace", seat="B")
        for seat, life in zip("ABCD", (5, 8, 9, 1), strict=True):
            game.state.players[seat].life = life
        hands = {seat: [self.add(game.engine, "Plains", seat=seat, zone="hand", ref=f"round-hidden-{seat}-{i}")
                         for i in range(count)] for seat, count in zip("ABC", (4, 3, 2), strict=True)}
        creatures = {seat: [self.add(game.engine, "Grizzly Bears", seat=seat, ref=f"round-creature-{seat}-{i}")
                             for i in range(count)] for seat, count in zip("ACD", (3, 3, 1), strict=True)}
        arbor = self.add(game.engine, "Dryad Arbor", seat="C", ref="round-creature-land")
        creatures["C"].append(arbor)
        lands = {seat: [self.add(game.engine, "Plains", seat=seat, ref=f"round-land-{seat}-{i}")
                        for i in range(count)] for seat, count in zip("ABC", (1, 2, 3), strict=True)}
        self.ready(game, pox)

        self.stage(game, "A", 2, hands["A"])
        self.assertEqual([3, 5, 6, 0], [game.state.players[seat].life for seat in "ABCD"])
        self.assertTrue(game.state.players["D"].in_game)
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = json.dumps(game.packet(principal, full=True))
            for private in hands["A"]:
                self.assertNotIn(private.ref, packet)
                self.assertNotIn(private.object_id, packet)
        self.reject(game, "B", hands["A"][:2])
        self.reject(game, "A", [hands["A"][0]] * 2)
        self.choose(game, "A", hands["A"][:2])
        game = self.replay(game, load=True)
        view = self.stage(game, "B", 1, hands["B"])
        self.assertEqual({}, view["prior_public_choices"])
        self.assertEqual(["hand"] * 4, self.zones(game, hands["A"]))
        self.choose(game, "B", hands["B"][:1])
        self.stage(game, "C", 1, hands["C"])
        self.choose(game, "C", hands["C"][:1])

        self.stage(game, "A", 1, creatures["A"])
        self.assertEqual(["exile", "exile", "hand", "hand"], self.zones(game, hands["A"]))
        self.choose(game, "A", creatures["A"][:1])
        view = self.stage(game, "C", 2, creatures["C"])
        self.assertEqual([creatures["A"][0].ref], view["prior_public_choices"]["A"])
        self.assertEqual(["battlefield"] * 3, self.zones(game, creatures["A"]))
        self.choose(game, "C", [arbor, creatures["C"][0]])
        self.stage(game, "D", 1, creatures["D"])
        self.assertTrue(game.state.players["D"].in_game)
        self.choose(game, "D", creatures["D"])

        self.stage(game, "A", 1, lands["A"])
        self.assertEqual(["exile", "exile"], self.zones(game, [arbor, creatures["C"][0]]))
        self.choose(game, "A", lands["A"])
        self.stage(game, "B", 1, lands["B"])
        self.choose(game, "B", lands["B"][:1])
        self.stage(game, "C", 1, lands["C"])
        self.reject(game, "C", [arbor])
        self.assertTrue(game.state.players["D"].in_game)
        self.choose(game, "C", lands["C"][:1])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.players["D"].in_game)
        self.assertEqual(["exile", "battlefield", "battlefield"], self.zones(game, lands["C"]))
        self.assertEqual(["exile", "battlefield"], self.zones(game, [pox, rest]))
        self.replay(game, load=True)

    def test_empty_domains_skip_choices_and_life_round_still_rounds_up(self):
        game, pox = self.scene(708091602)
        self.ready(game, pox)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([26] * 4, [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(["graveyard"], self.zones(game, [pox]))
        self.replay(game, load=True)

    def test_complete_original_requires_each_independent_rounded_third_in_printed_order(self):
        source = self.db.lookup("Pox", fuzzy=False)
        ir = compile_oracle_card(source, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(source.oracle_text, source.oracle_text[node.span.start:node.span.end])
        self.assertEqual(("lose_life", "discard", "sacrifice", "sacrifice"),
                         tuple(effect.get("then", effect["op"]) for effect in node.effects))
        self.assertTrue(all(effect.get("count", effect.get("amount")) == ThirdQuantitySpec("up").to_dict()
                            for effect in node.effects))
        report, semantics = register_card(self.db, self.registry, "Pox")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (p for p in semantics.programs_for_oracle(source.oracle_id) if p.event == "resolve")
        cap = THIRD_PLAYER_ROUNDS_CAPABILITY
        deps = tuple(value for value in program.capability_dependencies if value != cap)
        missing = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        life, discard, creatures, lands = program.effects
        for effects in ((life, discard, lands, creatures), (life, discard, creatures),
                        (life, {**discard, "hidden": False}, creatures, lands),
                        (life, discard, creatures, {**lands, "count": ThirdQuantitySpec("down").to_dict()})):
            modified = replace(program, effects=effects)
            self.assertFalse(bind_semantic_program_runtime(modified, capability_registry=self.registry, profile=PROFILE)["strict"])
        for before, after in (("Each player", "Each opponent"), ("Round up", "Round down"),
                              ("sacrifices a third of the lands", "may sacrifice a third of the lands"),
                              ("each time.", "each time. Rewrite history.")):
            modified = replace(source, oracle_text=source.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_oracle_card(modified, capability_registry=self.registry).status)
