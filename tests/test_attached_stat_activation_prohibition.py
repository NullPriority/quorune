"""An original Aura composes fixed stats with attached activation rules."""

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
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Viper's Kiss"
ACTIVATION = "activation.restriction.attached_static_rule"
STATS = "continuous.attached.fixed_characteristics"
TEMPLATE = "static-attached-stat-activation-prohibition-v1"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class AttachedStatActivationProhibitionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "attached-activation-prohibition.sqlite3"
        build_fixture_database([basic, FIXTURES / "attached-stat-activation-prohibition-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Attached creature activation prohibition", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        source = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        programs = semantics.programs_for_oracle(source.oracle_id)
        rule, = [part for part in programs if part.event == "characteristics.evaluate"]
        self.assertTrue(any("attachment.aura.simple_object" in part.capability_dependencies
                            for part in programs), programs)
        return source, rule

    def test_complete_original_admits_enchant_and_both_independent_static_owners(self):
        card, program = self.program()
        self.assertTrue({STATS, ACTIVATION} <= set(program.capability_dependencies))
        characteristic, prohibition = program.handlers
        self.assertEqual(
            ("characteristics.evaluate", "characteristics.evaluate"),
            (characteristic["event"], prohibition["event"]),
        )
        self.assertEqual((-1, -1), (characteristic["modifier"]["power"], characteristic["modifier"]["toughness"]))
        self.assertEqual("ability.static.attached-activation-rule.v1", prohibition["handler_id"])
        self.assertEqual(("attached", "all"), (prohibition["fragment"]["value"]["relation"],
                                               prohibition["fragment"]["value"]["scope"]))
        for text in (
            card.oracle_text.replace("Enchanted creature gets", "Enchanted artifact gets"),
            card.oracle_text.replace("gets -1/-1", "gets +1/+1"),
            card.oracle_text.replace("be activated.", "be activated unless they're mana abilities."),
        ):
            changed = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry,
                                          capability_profile=PROFILE)
            self.assertFalse(any(node.template_id == TEMPLATE for face in changed.faces for node in face.nodes))

    def test_raw_compound_requires_each_runtime_owner(self):
        _, program = self.program()
        for capability in (STATS, ACTIVATION):
            with self.subTest(capability=capability):
                caps = [value for value in program.capability_dependencies if value != capability]
                changed = replace(program, capability_dependencies=caps,
                                  capability_closure=self.registry.closure(caps, profile=PROFILE).to_dict())
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound)
                self.assertIn("capability:undeclared_runtime_dependency:" + capability, bound["blockers"])

    def activation_offer(self, game, actor, source):
        self.priority(game, actor)
        actions = game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((row for row in actions if row["id"].startswith("activate:" + source.ref + ":")), None)

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness"))

    def test_actual_aura_lowers_stats_and_hides_mana_action_until_source_departure(self):
        game = self.session(602050601)
        engine = game.engine
        aura = self.add(engine, NAME, zone="hand")
        opponent = self.add(engine, "Vine Trellis", seat="B", ref="restriction-attached-recipient")
        independent = self.add(engine, "Vine Trellis", seat="B", ref="restriction-other-recipient")
        for creature in (opponent, independent):
            creature.acquired_control_turn_count = -1
        for index in (1, 2):
            self.add(engine, "Plains", ref=f"viper-removal-plains-{index}")
        remove = self.add(engine, "Disenchant", zone="hand")
        secret = self.add(engine, "Counterspell", seat="C", zone="hand", ref="attached-prohibition-private")
        for card in (aura, opponent, remove):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="A", mana={})
        game.state.players["A"].mana_pool["B"] = 1
        self.checkpoint(game)

        before_aura = self.activation_offer(game, "B", opponent)
        self.assertIsNotNone(before_aura)
        self.cast_for(game, "A", aura, [opponent.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(opponent.object_id, game.state.cards[aura.object_id].attached_to)
        self.assertEqual((-1, 3), self.stats(game, opponent))
        self.assertEqual((0, 4), self.stats(game, independent))
        self.assertIsNone(self.activation_offer(game, "B", opponent), "The attached mana activation must be suppressed")
        self.assertIsNotNone(self.activation_offer(game, "B", independent))
        command = {"action_id": before_aura["id"], "pay": "auto", "mana_output": {"G": 1}}
        state_hash = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(state_hash, authoritative_state_hash(game.state))
        self.assertFalse(game.state.cards[opponent.object_id].tapped)
        game = self.replay(game, load=True)

        self.cast_for(game, "A", remove, [aura.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((0, 4), self.stats(game, opponent))
        restored = self.activation_offer(game, "B", opponent)
        self.assertEqual(before_aura["id"], restored["id"])
        paid = game.act("pilot:B", command)
        self.assertTrue(paid.ok, paid.summary)
        self.assertTrue(game.state.cards[opponent.object_id].tapped)
        self.assertEqual(1, game.state.players["B"].mana_pool["G"])
        for principal in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(secret.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_flash_attachment_keeps_a_paid_activation_and_permits_the_attack(self):
        game = self.session(602050602)
        engine = game.engine
        aura = self.add(engine, NAME, zone="hand")
        self.add(engine, "Vedalken Orrery")
        dragon = self.add(engine, "Shivan Dragon", seat="B")
        dragon.acquired_control_turn_count = -1
        self.prepare(game, actor="B", active="B", mana={"R": 2})
        game.state.players["A"].mana_pool["B"] = 1
        self.checkpoint(game)

        offer = self.activation_offer(game, "B", dragon)
        command = {"action_id": offer["id"], "pay": "auto"}
        accepted = game.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        original_activation, = game.state.stack
        self.cast_for(game, "A", aura, [dragon.ref])
        self.resolve_top(game)
        self.assertEqual(original_activation.ref, game.state.stack[-1].ref)
        self.assertEqual((4, 4), self.stats(game, dragon))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((5, 4), self.stats(game, dragon))
        self.assertIsNone(self.activation_offer(game, "B", dragon))
        self.assertEqual(1, game.state.players["B"].mana_pool["R"])
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        sent = game.act("pilot:B", {"action_id": "attack", "attackers": {dragon.ref: "A"}})
        self.assertTrue(sent.ok, sent.summary)
        self.assertEqual("A", game.state.combat.attackers[dragon.object_id])
        self.replay(game, load=True)

    def test_actual_mana_witness_kills_omitting_attached_prohibition(self):
        with patch("quorune.semantic_runtime.activation_restrictions.activation_prohibition_specs", return_value=()):
            with self.assertRaisesRegex(AssertionError, "attached mana activation must be suppressed"):
                self.test_actual_aura_lowers_stats_and_hides_mana_action_until_source_departure()
