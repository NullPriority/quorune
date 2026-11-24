"""Live compound Hexproof for a controller and the printed permanent sets."""

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
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.continuous_components import FixedQueryKeywordGrantHandler
from quorune.semantic_runtime.player_target_protection import PlayerTargetProtectionHandler
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SHALAI = "Shalai, Voice of Plenty"
SIGARDA = "Sigarda, Heron's Grace"
PROFILE = "commander_review"


class ControllerPlayerAndPermanentHexproofTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controller-and-permanents.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controller-player-and-permanent-hexproof-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current compound Hexproof", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        grant, = [item for item in programs if item.event == "characteristics.evaluate"]
        activation, = [item for item in programs if item.event == "activate"]
        return card, grant, activation

    def test_complete_original_grants_keep_exact_groups_and_both_paid_siblings(self):
        rows = (
            (SHALAI, "place_counters_on_set", (
                (ObjectQuerySpec(zones=("battlefield",), types_all=("planeswalker",)), False),
                (ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)), True),
            )),
            (SIGARDA, "create_token", (
                (ObjectQuerySpec(zones=("battlefield",), subtypes_all=("human",)), False),
            )),
        )
        for name, operation, expected in rows:
            with self.subTest(card=name):
                card, composition, activation = self.programs(name)
                player, *permanents = composition.handlers
                self.assertEqual("hexproof", PlayerTargetProtectionHandler().validate(player))
                grants = [FixedQueryKeywordGrantHandler().validate(item) for item in permanents]
                self.assertEqual(expected, tuple((node.predicate, node.exclude_source) for node in grants))
                self.assertTrue(all(node.target_controller == "source_controller"
                                    and node.abilities == ("Hexproof",) for node in grants))
                self.assertIn(operation, {item["op"] for item in activation.effects})
                ir = compile_oracle_card(card, capability_registry=self.registry)
                self.assertTrue(any(node.template_id == "printed-keyword-list-v1" for face in ir.faces for node in face.nodes))
                changed = replace(card, oracle_text=card.oracle_text.replace("have hexproof", "have shroud"))
                self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry).status)

    def test_raw_pairs_reject_omitted_player_wrong_scope_and_lost_source_exclusion(self):
        _, grant, _ = self.programs(SHALAI)
        player, walkers, creatures = grant.handlers
        for malformed in (
            (walkers, creatures),
            (player, creatures),
            (player, walkers, {**creatures, "condition": {**creatures["condition"], "exclude_source": False}}),
            (player, {**walkers, "condition": {**walkers["condition"], "target_controller": "any"}}, creatures),
            ({**player, "keyword": "shroud"}, walkers, creatures),
        ):
            with self.subTest(handlers=len(malformed), first=malformed[0], last=malformed[-1]):
                result = bind_semantic_program_runtime(
                    replace(grant, handlers=malformed), capability_registry=self.registry, profile=PROFILE,
                )
                self.assertFalse(result["strict"], result)
                self.assertTrue(any("runtime_controller_player_and_permanent_hexproof:invalid:" in reason
                                    for reason in result["blockers"]), result)

    def reject_target(self, game, seat, offer, ref):
        command = {"action_id": offer["id"], "targets": [ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def activation(self, game, source):
        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in rows if row["id"].startswith("activate:" + source.ref + ":")]
        return {"action_id": action["id"], "pay": "auto"}

    def shalai_scene(self):
        game = self.session(70211031)
        engine = game.engine
        source = self.add(engine, SHALAI, zone="hand")
        body = self.add(engine, "Grizzly Bears", ref="shalai-controlled-bear")
        enemy = self.add(engine, "Grizzly Bears", seat="C", ref="shalai-opponent-bear")
        walker = self.add(engine, "Jace Beleren")
        walker.counters["loyalty"] = 3
        shock = self.add(engine, "Shock", seat="B", zone="hand")
        bounce = self.add(engine, "Unsummon", seat="B", zone="hand")
        self.add(engine, "Mountain", seat="B")
        self.add(engine, "Island", seat="B")
        private = self.add(engine, "Counterspell", seat="D", zone="hand", ref="shalai-private-hand")
        for card in (source, shock, bounce, walker):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 7, "W": 1, "G": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"C": 4, "G": 2})
        self.resolve_top(game)
        command = self.activation(game, source)
        self.assertEqual(6, sum(game.state.players["A"].mana_pool.values()))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.resolve_top(game)
        self.priority(game, "B")
        offer = self.action(game, "B", shock)
        refs = set(offer["target_schema"]["legal_refs"])
        self.assertIn(source.ref, refs, "Shalai herself must remain a legal target for a creature-only exclusion")
        self.assertTrue({enemy.ref, "C", "D"} <= refs)
        for ref in ("A", body.ref, walker.ref):
            self.assertNotIn(ref, refs)
            self.reject_target(game, "B", offer, ref)
        return game, source, body, walker, shock, bounce, private

    def test_actual_shalai_shares_target_legality_and_keeps_paid_counters(self):
        game, source, body, walker, shock, bounce, private = self.shalai_scene()
        for creature in (source, body):
            self.assertEqual(1, game.state.cards[creature.object_id].counters.get("+1/+1"))
        self.assertEqual({"loyalty": 3}, game.state.cards[walker.object_id].counters)

        self.cast_for(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.priority(game, "B")
        offer = self.action(game, "B", shock)
        self.assertTrue({"A", body.ref, walker.ref} <= set(offer["target_schema"]["legal_refs"]))
        self.cast_for(game, "B", shock, ["A"])
        self.resolve_top(game)
        self.assertEqual(38, game.state.players["A"].life)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_source_offer_kills_the_lost_exclusion_mutation(self):
        lower = FixedQueryKeywordGrantHandler.lower

        def include_source(handler, descriptor, context):
            condition = descriptor["condition"]
            if condition["exclude_source"]:
                descriptor = {**descriptor, "condition": {**condition, "exclude_source": False}}
            return lower(handler, descriptor, context)

        with patch.object(FixedQueryKeywordGrantHandler, "lower", include_source):
            with self.assertRaisesRegex(AssertionError, "Shalai herself must remain a legal target"):
                self.shalai_scene()

    def test_actual_sigarda_protects_pending_human_target_and_paid_token(self):
        game = self.session(70211032)
        engine = game.engine
        source = self.add(engine, SIGARDA, zone="hand")
        timing = self.add(engine, "Vedalken Orrery")
        human = self.add(engine, "Elite Vanguard", ref="sigarda-controlled-human")
        foreign_human = self.add(engine, "Elite Vanguard", seat="C", ref="sigarda-opposing-human")
        ordinary = self.add(engine, "Grizzly Bears", ref="sigarda-nonhuman")
        paid = self.add(engine, "Plains", zone="graveyard", ref="sigarda-own-grave-cost")
        foreign = self.add(engine, "Plains", seat="C", zone="graveyard", ref="sigarda-foreign-grave-cost")
        first = self.add(engine, "Shock", seat="B", zone="hand", ref="sigarda-stack-shock")
        second = self.add(engine, "Shock", seat="B", zone="hand", ref="sigarda-next-shock")
        for index in range(2):
            self.add(engine, "Mountain", seat="B", ref=f"sigarda-shock-payment-{index}")
        for card in (source, timing, first):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "G": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "B", first, [human.ref])
        self.cast_for(game, "A", source, reserve={"C": 2})
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[human.object_id].zone)
        self.assertEqual(0, game.state.cards[human.object_id].marked_damage)

        command = self.activation(game, source)
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {**command, "cost_objects": [foreign.ref]})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {**command, "cost_objects": [paid.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("exile", game.state.cards[paid.object_id].zone)
        self.resolve_top(game)
        token, = [item for item in game.state.cards.values() if item.is_token and item.controller == "A"]
        self.assertEqual("Human Soldier Token", token.printed_name)
        self.priority(game, "B")
        offer = self.action(game, "B", second)
        refs = set(offer["target_schema"]["legal_refs"])
        self.assertTrue({source.ref, ordinary.ref, foreign_human.ref, "C"} <= refs)
        for ref in ("A", human.ref, token.ref):
            self.assertNotIn(ref, refs)
            self.reject_target(game, "B", offer, ref)
        self.replay(game, load=True)
