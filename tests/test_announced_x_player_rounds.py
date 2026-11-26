"""One sealed spell X controls four ordered all-player rounds."""

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
from quorune.query_effect_amount_model import CastXAmountSpec
from quorune.record import authoritative_state_hash
from quorune.rules.announced_x_player_rounds import ANNOUNCED_X_PLAYER_ROUNDS_CAPABILITY
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class AnnouncedXPlayerRoundsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "announced-rounds.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/announced-x-player-rounds-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Announced player rounds", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def scene(self, seed):
        game = self.session(seed)
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"]):
                game.engine.move_card(object_id, "library", log=False)
        cloud = self.add(game.engine, "Death Cloud", zone="hand")
        return game, cloud

    def ready(self, game, cloud, *, x):
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, cloud)
        self.prepare(game, mana={"C": x, "B": 3})
        self.checkpoint(game)

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def cast_announced(self, game, cloud, *, x):
        target = "cast:" + cloud.ref
        offered = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(target, {action["id"] for action in offered})
        self.act(game, "A", {"action_id": target, "x": x, "pay": "auto"})
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        original = game.state.stack[-1]
        self.assertEqual(x, original.x_value)
        return original

    def stage(self, game, seat, cards):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("choice.apnap", [seat]), (pending.kind, pending.actors))
        context = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertEqual(len(cards), context["count"])
        self.assertEqual({card.ref for card in cards}, set(context["options"]))
        return context

    def choose(self, game, seat, cards):
        self.act(game, seat, {"action_id": "choose", "cards": [card.ref for card in cards]})

    def zones(self, game, cards):
        return [game.state.cards[card.object_id].zone for card in cards]

    def test_paid_x_precedes_three_distinct_rounds_and_defers_zero_life_departure(self):
        game, cloud = self.scene(707040401)
        a_hand = [self.add(game.engine, "Plains", zone="hand", ref=f"cloud-a-hidden-{i}") for i in range(2)]
        b_hand = [self.add(game.engine, "Plains", seat="B", zone="hand", ref="cloud-b-hidden")]
        c_hand = [self.add(game.engine, "Plains", seat="C", zone="hand", ref=f"cloud-c-hidden-{i}") for i in range(2)]
        myr = self.add(game.engine, "Myr Sire", ref="cloud-a-myr")
        a_creatures = [myr, self.add(game.engine, "Grizzly Bears", ref="cloud-a-bear")]
        b_creatures = [self.add(game.engine, "Grizzly Bears", seat="B", ref="cloud-b-bear")]
        arbor = self.add(game.engine, "Dryad Arbor", seat="C", ref="cloud-c-creature-land")
        c_creatures = [arbor, self.add(game.engine, "Grizzly Bears", seat="C", ref="cloud-c-bear")]
        lands = {seat: [self.add(game.engine, "Plains", seat=seat, ref=f"cloud-land-{seat}")]
                 for seat in "ABCD"}
        lands["D"].append(self.add(game.engine, "Plains", seat="D", ref="cloud-land-D-two"))
        game.state.players["B"].life = 2
        self.ready(game, cloud, x=2)
        self.cast_announced(game, cloud, x=2)

        self.stage(game, "A", a_hand)
        self.assertEqual([38, 0, 38, 38], [game.state.players[seat].life for seat in "ABCD"])
        self.assertTrue(game.state.players["B"].in_game)
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            for card in a_hand:
                self.assertNotIn(card.ref, packet)
                self.assertNotIn(card.object_id, packet)
        self.reject(game, "B", {"action_id": "choose", "cards": [card.ref for card in a_hand]})
        self.reject(game, "A", {"action_id": "choose", "cards": [a_hand[0].ref]})
        self.choose(game, "A", a_hand)
        game = self.replay(game, load=True)
        context = self.stage(game, "B", b_hand)
        self.assertEqual({}, context["prior_public_choices"])
        self.assertEqual(["hand"] * 2, self.zones(game, a_hand))
        self.choose(game, "B", b_hand)
        self.stage(game, "C", c_hand)
        self.choose(game, "C", c_hand)

        self.stage(game, "A", a_creatures)
        self.assertEqual(["graveyard"] * 5, self.zones(game, [*a_hand, *b_hand, *c_hand]))
        self.choose(game, "A", a_creatures)
        context = self.stage(game, "B", b_creatures)
        self.assertEqual([card.ref for card in a_creatures], context["prior_public_choices"]["A"])
        self.assertEqual(["battlefield"] * 2, self.zones(game, a_creatures))
        self.choose(game, "B", b_creatures)
        self.stage(game, "C", c_creatures)
        self.choose(game, "C", c_creatures)

        self.stage(game, "A", lands["A"])
        self.assertEqual(["graveyard"] * 5, self.zones(game, [*a_creatures, *b_creatures, *c_creatures]))
        self.assertFalse(any(item.source_object_id == myr.object_id for item in game.state.stack))
        for seat in "ABCD":
            if seat != "A":
                self.stage(game, seat, lands[seat])
            self.assertTrue(game.state.players["B"].in_game)
            if seat == "C":
                self.reject(game, "C", {"action_id": "choose", "cards": [arbor.ref]})
            self.choose(game, seat, lands[seat])
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.players["B"].in_game)
        self.assertEqual(["graveyard"] * 4, self.zones(game, [*lands["A"], *lands["C"], *lands["D"]]))
        self.assertTrue(any(card.is_token and card.controller == "A" and card.zone == "battlefield"
                            for card in game.state.cards.values()))
        self.replay(game, load=True)

    def test_zero_paid_x_leaves_existing_options_untouched_and_never_opens_a_choice(self):
        game, cloud = self.scene(707040402)
        hand = self.add(game.engine, "Plains", zone="hand", ref="cloud-zero-hand")
        creature = self.add(game.engine, "Dryad Arbor", seat="B", ref="cloud-zero-creature-land")
        self.ready(game, cloud, x=0)
        self.cast_announced(game, cloud, x=0)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40] * 4, [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(["hand", "battlefield", "graveyard"], self.zones(game, [hand, creature, cloud]))
        self.replay(game, load=True)

    def test_an_actual_spell_copy_retains_announced_x_without_a_new_payment(self):
        game, cloud = self.scene(707040403)
        copy = self.add(game.engine, "Reverberate", seat="B", zone="hand")
        self.ready(game, cloud, x=1)
        game.state.players["B"].mana_pool["R"] = 2
        self.checkpoint(game)
        original = self.cast_announced(game, cloud, x=1)
        self.cast(game, "B", copy, [original.ref])
        self.resolve_top(game)
        self.assertEqual([1, 1], [item.x_value for item in game.state.stack])
        self.assertEqual(0, sum(game.state.players["B"].mana_pool.values()))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([38] * 4, [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_exact_source_and_independent_runtime_require_every_unmodified_round(self):
        source = self.db.lookup("Death Cloud", fuzzy=False)
        ir = compile_oracle_card(source, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(source.oracle_text, source.oracle_text[node.span.start:node.span.end])
        self.assertIsNone(node.target_schema)
        self.assertEqual(("lose_life", "discard", "sacrifice", "sacrifice"),
                         tuple(effect.get("then", effect["op"]) for effect in node.effects))
        for effect in node.effects:
            self.assertEqual(CastXAmountSpec().to_dict(), effect.get("amount", effect.get("count")))
        report, semantics = register_card(self.db, self.registry, "Death Cloud")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (p for p in semantics.programs_for_oracle(source.oracle_id) if p.event == "resolve")
        cap = ANNOUNCED_X_PLAYER_ROUNDS_CAPABILITY
        deps = tuple(value for value in program.capability_dependencies if value != cap)
        missing = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        for effects in (program.effects[:-1], tuple(reversed(program.effects)),
                        (program.effects[0], {**program.effects[1], "count": CastXAmountSpec(coefficient=2, schema_version=2).to_dict()},
                         *program.effects[2:])):
            changed = replace(program, effects=effects)
            self.assertFalse(bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)["strict"])
        for before, after in (("discards X cards", "may discard X cards"), ("sacrifices X lands", "sacrifices X artifacts"),
                              ("their choice.", "their choice. Rewrite history.")):
            changed = replace(source, oracle_text=source.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status)
