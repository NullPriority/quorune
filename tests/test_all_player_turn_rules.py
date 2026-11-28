"""Witness all-player turn rules through a trusted cast and saved game."""

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_match_outcome_rules as outcomes
from quorune.all_player_turn_rule_model import AllPlayerTurnRule, ResolvedAllPlayerTurnRule
from quorune.record import authoritative_state_hash
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.all_player_turn_rules import ALL_PLAYER_TURN_RULE_CAPABILITIES
from quorune.rules.capabilities import CapabilityRegistry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.match_outcome_restrictions import current_match_outcome_restrictions
from quorune.semantic_runtime.life_prohibitions import life_payment_prohibited
from quorune.semantic_runtime.player_target_protection import current_player_target_keywords
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class AllPlayerTurnRuleTests(InheritedNativeFixture, unittest.TestCase):
    begin_upkeep = outcomes.MatchOutcomeRulesTests.begin_upkeep
    witnessed_match = outcomes.MatchOutcomeRulesTests.witnessed_match

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db_path = Path(cls.temporary.name) / "all-player-turn-rules.sqlite3"
        build_fixture_database(ROOT / "tests/fixtures/all-player-turn-rule-cards.json", db_path)
        outcomes.MatchOutcomeRulesTests.use_database.__func__(cls, db_path)

    def test_paid_original_protects_all_players_and_existing_creatures_through_replay(self):
        game = self.witnessed_match(6112501)
        protection = self.add(game.engine, "Everybody Lives!", zone="hand")
        first_creature = self.add(game.engine, "Grizzly Bears", seat="B", ref="turn-rules-bear-b")
        second_creature = self.add(game.engine, "Grizzly Bears", seat="C", ref="turn-rules-bear-c")
        newcomer = self.add(game.engine, "Grizzly Bears", zone="hand", ref="turn-rules-late-bear")
        bolt = self.add(game.engine, "Lightning Bolt", seat="C", zone="hand")
        gain = self.add(game.engine, "Revitalize", seat="D", zone="hand")
        confluence = self.add(game.engine, "Mana Confluence", seat="C")
        for object_id in tuple(game.state.players["D"].zones["library"]):
            game.engine.move_card(object_id, "graveyard", log=False)
        for card in (protection, bolt, gain, confluence):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 1, "G": 1})
        game.state.players["C"].mana_pool["R"] = 1
        game.state.players["D"].mana_pool.update({"W": 1, "C": 1})
        self.checkpoint(game)

        self.cast_for(game, "A", protection)
        self.assertIsNone(self.resolve(game))
        for seat in "ABCD":
            self.assertIn("hexproof", current_player_target_keywords(game.engine, seat))
            self.assertTrue(life_payment_prohibited(game.engine, seat))
        for creature in (first_creature, second_creature):
            self.assertTrue({"hexproof", "indestructible"} <= set(game.engine._combat_keywords(creature)))
        self.assertNotIn(newcomer.ref, json.dumps(game.packet("pilot:C", full=True)))
        game = self.replay(game, load=True)
        rules = [effect for effect in game.state.continuous_effects if isinstance(effect, ResolvedAllPlayerTurnRule)]
        self.assertEqual(tuple(AllPlayerTurnRule), tuple(effect.rule for effect in rules))

        self.priority(game, "C")
        actions = game.packet("pilot:C", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(row["id"].startswith("activate:" + confluence.ref) for row in actions))
        offer = next(row for row in actions if row["id"].startswith("cast:" + bolt.ref))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", {"action_id": offer["id"], "targets": ["B"], "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        life = game.state.players["C"].life
        self.cast_for(game, "C", bolt, ["C"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life, game.state.players["C"].life)

        life = game.state.players["D"].life
        self.cast_for(game, "D", gain)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life + 3, game.state.players["D"].life)
        self.assertTrue(game.state.players["D"].in_game)
        self.assertFalse(game.state.players["D"].attempted_empty_draw)

        self.cast_for(game, "A", newcomer)
        self.assertIsNone(self.resolve(game))
        late = game.state.cards[newcomer.object_id]
        self.assertEqual("battlefield", late.zone)
        self.assertNotIn("hexproof", game.engine._combat_keywords(late))
        self.assertNotIn("indestructible", game.engine._combat_keywords(late))

        turn = game.state.turn_sequence
        self.until(game, lambda current: current.state.turn_sequence > turn)
        self.assertFalse(any(isinstance(effect, ResolvedAllPlayerTurnRule) for effect in game.state.continuous_effects))
        self.assertNotIn("hexproof", current_player_target_keywords(game.engine, "C"))
        self.assertFalse(life_payment_prohibited(game.engine, "C"))
        self.assertNotIn("indestructible", game.engine._combat_keywords(game.state.cards[second_creature.object_id]))
        self.replay(game, load=True)

    def test_resolving_rule_blocks_an_explicit_win_and_preserves_concession(self):
        game = self.witnessed_match(6112502)
        source = self.add(game.engine, "Test of Endurance", seat="B")
        spell = self.add(game.engine, "Everybody Lives!", zone="hand")
        for ref in ("turn-rule-plains-one", "turn-rule-plains-two"):
            self.add(game.engine, "Plains", ref=ref)
        game.state.players["B"].life = 50
        self.assert_closed(game, spell)
        self.begin_upkeep(game, "B")
        self.assertEqual(1, self.trigger_count(game, source))
        self.cast_for(game, "A", spell)
        self.resolve_top(game)
        active = frozenset("ABCD")
        rules = current_match_outcome_restrictions(game.engine)
        self.assertEqual(active, rules.win_prohibited)
        self.assertEqual(active, rules.loss_prohibited)
        self.priority(game, "A")
        concede = {"action_id": "concede", "choices": {"confirm_concede": True}}
        result = game.act("pilot:A", concede)
        self.assertTrue(result.ok, result.summary)
        game = self.replay(game, load=True)
        self.assertFalse(game.state.players["A"].in_game)
        self.resolve_top(game)
        self.assertIsNone(game.state.winner)
        for seat in "CD":
            self.priority(game, seat)
            result = game.act("pilot:" + seat, concede)
            self.assertTrue(result.ok, result.summary)
        self.assertEqual("B", game.state.winner)
        self.replay(game, load=True)

    def test_every_rule_is_required_for_complete_admission(self):
        card = self.db.lookup("Everybody Lives!", fuzzy=False)
        options = {"capability_profile": "commander_review"}
        self.assertEqual("exact", compile_oracle_card(card, capability_registry=self.registry, **options).status)
        changed = replace(card, oracle_text=card.oracle_text.replace("Players gain hexproof", "Opponents gain hexproof", 1))
        self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry, **options).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        found = set(discover_tests(ROOT).values())
        for capability in ALL_PLAYER_TURN_RULE_CAPABILITIES.values():
            with self.subTest(capability=capability):
                value = deepcopy(raw)
                next(row for row in value["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Withhold this exact all-player rule"],
                )
                registry = CapabilityRegistry(value)
                index = build_index(registry_value=value, declaration_source=declarations, discovered_test_ids=found)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry, **options).status)
        with patch("quorune.compiler.all_player_turn_rules.all_player_turn_rules_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry, **options).status)


class GiftCardCompilerTests(unittest.TestCase):
    def test_gift_card_links_closed_protection_and_rejects_other_gifts_and_targets(self):
        from test_fixed_public_alternative_costs import card_record
        from quorune.gift_card_model import GIFT_CARD_CAPABILITY, GIFT_CARD_COST_FIELD
        from quorune.rules.all_player_turn_rules import CONTROLLER_TURN_HEXPROOF_CAPABILITY
        from quorune.rules.capabilities import load_default_capability_registry

        oracle = (
            "Gift a card\nYou and permanents you control gain hexproof until end of turn. "
            "If the gift was promised, permanents you control also gain indestructible until end of turn."
        )
        record = card_record(oracle, keywords=("Gift",))
        registry = load_default_capability_registry()

        def compile_source(source=record):
            return compile_oracle_card(source, capability_registry=registry, capability_profile="commander_review")

        def witness():
            ir = compile_source()
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(oracle, node.text)
            self.assertEqual({"schema_version": 1}, node.cost[GIFT_CARD_COST_FIELD])
            self.assertLessEqual({GIFT_CARD_CAPABILITY, CONTROLLER_TURN_HEXPROOF_CAPABILITY},
                                 set(node.capability_dependencies))
            self.assertIsNone(node.target_schema)

        witness()
        for alternate in (
            replace(record, oracle_text=oracle.replace("Gift a card", "Gift a Food")),
            replace(record, oracle_text=oracle.replace("permanents you control also", "target permanent also")),
            replace(record, oracle_text=oracle + "\nKicker {2}"),
            replace(record, type_line="Creature — Human"),
        ):
            self.assertNotEqual("exact", compile_source(alternate).status)
        registry._capabilities[GIFT_CARD_CAPABILITY]["status"] = "blocked"
        self.assertNotEqual("exact", compile_source().status)

    def test_dawn_truce_runtime_binding_requires_the_cost_and_both_authorities(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.gift_card_model import GIFT_CARD_CAPABILITY
        from quorune.gift_card_model import GIFT_CARD_COST_FIELD
        from quorune.rules.all_player_turn_rules import CONTROLLER_TURN_HEXPROOF_CAPABILITY
        from quorune.semantics import SemanticRegistry

        fixture = ROOT / "tests/fixtures/all-player-turn-rule-cards.json"
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "gift.sqlite3"
        build_fixture_database(fixture, path)
        from quorune.carddb import CardDatabase
        from quorune.rules.capabilities import load_default_capability_registry

        with CardDatabase(path) as db:
            registry = load_default_capability_registry()
            card = db.lookup("Dawn's Truce", fuzzy=False)
            semantics = SemanticRegistry()
            program = compile_best_available_card_program(
                db, card, semantic_registry=semantics, capability_registry=registry,
                capability_profile="commander_review",
            )
            self.assertTrue(bind_card_program_runtime(
                program, capability_registry=registry, profile="commander_review",
            )["strict_capability_ready"])
            from quorune.oracle_ir import register_generated_programs

            register_generated_programs(
                db, semantics, (card,), capability_registry=registry,
                capability_profile="commander_review", promote_exact_effect_programs=True,
            )
            resolve, = [r for r in semantics.programs_for_oracle(card.oracle_id) if r.event == "resolve"]
            def assert_rejected(changed, blocker):
                result = bind_semantic_program_runtime(changed, capability_registry=registry,
                                                       profile="commander_review")
                self.assertFalse(result["strict"])
                self.assertTrue(any(blocker in row for row in result["blockers"]), result)
            for omitted in (GIFT_CARD_CAPABILITY, CONTROLLER_TURN_HEXPROOF_CAPABILITY):
                required = tuple(dep for dep in resolve.capability_dependencies if dep != omitted)
                assert_rejected(
                    replace(resolve, capability_dependencies=required,
                            capability_closure=registry.closure(required, profile="commander_review").to_dict()),
                    "undeclared_runtime_dependency:" + omitted,
                )
            for invalid in (None, {GIFT_CARD_COST_FIELD: {"schema_version": True}}):
                assert_rejected(replace(resolve, cost_schema=invalid), "runtime_gift_card:invalid")


class GiftCardNativeTests(InheritedNativeFixture, unittest.TestCase):
    witnessed_match = outcomes.MatchOutcomeRulesTests.witnessed_match

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "gift.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/all-player-turn-rule-cards.json",
            ROOT / "tests/fixtures/bargain-cards.json",
            ROOT / "tests/fixtures/cast-participant-optional-opponent-draw-cards.json",
        ], path)
        outcomes.MatchOutcomeRulesTests.use_database.__func__(cls, path)

    def gift_offer(self, game, spell):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row["id"].startswith("cast:" + spell.ref))

    def gift_cast(self, game, spell, recipient=None):
        offer = self.gift_offer(game, spell)
        option = "normal" if recipient is None else "normal+gift:" + recipient
        result = game.act("pilot:A", {"action_id": offer["id"], "cost_option": option, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        return game.state.stack[-1].ref

    def test_dawn_truce_paid_and_unpromised_draw_order_privacy_and_replay(self):
        for recipient in (None, "C"):
            with self.subTest(recipient=recipient):
                game = self.witnessed_match(174005 + int(recipient is not None))
                spell = self.add(game.engine, "Dawn's Truce", zone="hand")
                guard = self.add(game.engine, "Grizzly Bears", ref="gift-guard")
                other = self.add(game.engine, "Grizzly Bears", seat="B", ref="gift-other")
                late = self.add(game.engine, "Grizzly Bears", zone="hand", ref="gift-late")
                self.assert_closed(game, spell)
                self.prepare(game, mana={"C": 2, "W": 1, "G": 1})
                self.checkpoint(game)

                offer = self.gift_offer(game, spell)
                prices = {row["id"]: row["requirements"] for row in offer["cost_options"]}
                self.assertEqual({"normal", "normal+gift:B", "normal+gift:C", "normal+gift:D"}, set(prices))
                self.assertTrue(all(price == prices["normal"] for price in prices.values()))
                before = authoritative_state_hash(game.state)
                for actor, option in (("B", "normal+gift:C"), ("A", "normal+gift:A"),
                                      ("A", "normal+gift:B+gift:C")):
                    result = game.act("pilot:" + actor, {
                        "action_id": offer["id"], "cost_option": option, "pay": "auto",
                    })
                    self.assertFalse(result.ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                hands = {seat: set(game.state.players[seat].zones["hand"]) for seat in "BCD"}
                self.gift_cast(game, spell, recipient)
                self.assertEqual(hands, {seat: set(game.state.players[seat].zones["hand"]) for seat in "BCD"})
                self.assertNotIn("hexproof", current_player_target_keywords(game.engine, "A"))
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))

                self.assertIn("hexproof", current_player_target_keywords(game.engine, "A"))
                for seat in "BCD":
                    self.assertNotIn("hexproof", current_player_target_keywords(game.engine, seat))
                    drawn = set(game.state.players[seat].zones["hand"]) - hands[seat]
                    self.assertEqual(int(seat == recipient), len(drawn))
                    for object_id in drawn:
                        ref = game.state.cards[object_id].ref
                        self.assertIn(ref, json.dumps(game.packet("pilot:" + seat, full=True)))
                        for observer in set("ABCD") - {seat}:
                            self.assertNotIn(ref, json.dumps(game.packet("pilot:" + observer, full=True)))
                if recipient is not None:
                    codes = [event.code for event in game.state.events]
                    cast_index = max(index for index, code in enumerate(codes) if code == "stack.cast")
                    self.assertLess(codes.index("card.draw", cast_index), codes.index("rule.player.turn.created"))
                guard_keywords = game.engine._combat_keywords(game.state.cards[guard.object_id])
                self.assertIn("hexproof", guard_keywords)
                self.assertEqual(recipient is not None, "indestructible" in guard_keywords)
                self.assertNotIn("hexproof", game.engine._combat_keywords(game.state.cards[other.object_id]))

                self.cast_for(game, "A", late)
                self.assertIsNone(self.resolve(game))
                self.assertFalse({"hexproof", "indestructible"} & set(
                    game.engine._combat_keywords(game.state.cards[late.object_id])))
                turn = game.state.turn_sequence
                self.until(game, lambda current: current.state.turn_sequence > turn)
                self.assertNotIn("hexproof", current_player_target_keywords(game.engine, "A"))
                self.assertNotIn("indestructible", game.engine._combat_keywords(game.state.cards[guard.object_id]))
                self.replay(game, load=True)

    def test_dawn_truce_copy_keeps_recipient_and_countering_original_gives_nothing(self):
        game = self.witnessed_match(174008)
        truce = self.add(game.engine, "Dawn's Truce", zone="hand")
        copy = self.add(game.engine, "Twincast", seat="B", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="C", zone="hand")
        other = self.add(game.engine, "Grizzly Bears", seat="B")
        self.prepare(game, mana={"C": 1, "W": 1})
        for seat in "BC":
            game.state.players[seat].mana_pool["U"] = 2
        self.checkpoint(game)
        stack_ref = self.gift_cast(game, truce, "B")
        self.cast_for(game, "B", copy, [stack_ref])
        self.resolve_top(game)
        hand = len(game.state.players["B"].zones["hand"])
        self.resolve_top(game)
        self.assertEqual(hand + 1, len(game.state.players["B"].zones["hand"]))
        self.assertIn("hexproof", current_player_target_keywords(game.engine, "B"))
        self.assertIn("indestructible", game.engine._combat_keywords(other))
        self.assertNotIn("hexproof", current_player_target_keywords(game.engine, "A"))
        self.cast_for(game, "C", counter, [stack_ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(hand + 1, len(game.state.players["B"].zones["hand"]))
        self.assertNotIn("hexproof", current_player_target_keywords(game.engine, "A"))
        self.replay(game, load=True)

    def test_omitting_gift_resolution_mutant_loses_the_draw_and_bonus(self):
        from quorune.rules import gift_card_spells

        def witness():
            game = self.witnessed_match(174009)
            spell = self.add(game.engine, "Dawn's Truce", zone="hand")
            self.prepare(game, mana={"C": 1, "W": 1})
            before = len(game.state.players["B"].zones["hand"])
            self.gift_cast(game, spell, "B")
            self.assertIsNone(self.resolve(game))
            self.assertEqual(before + 1, len(game.state.players["B"].zones["hand"]))

        witness()
        with patch.object(gift_card_spells, "prepare_gift_card_resolution",
                          side_effect=lambda host, item, effects: effects):
            with self.assertRaises(AssertionError):
                witness()

    def test_omitting_scoped_player_rule_mutant_loses_controller_hexproof(self):
        from quorune.semantic_runtime import player_target_protection

        game = self.witnessed_match(174010)
        spell = self.add(game.engine, "Dawn's Truce", zone="hand")
        self.prepare(game, mana={"C": 1, "W": 1})
        self.gift_cast(game, spell)
        self.assertIsNone(self.resolve(game))

        def witness():
            self.assertIn("hexproof", current_player_target_keywords(game.engine, "A"))
            self.assertNotIn("hexproof", current_player_target_keywords(game.engine, "B"))

        witness()
        with patch.object(player_target_protection, "has_player_turn_rule", return_value=False):
            with self.assertRaises(AssertionError):
                witness()

    def test_dawn_truce_keeps_promised_bonus_after_the_recipient_leaves(self):
        game = self.witnessed_match(174011)
        spell = self.add(game.engine, "Dawn's Truce", zone="hand")
        guard = self.add(game.engine, "Grizzly Bears")
        self.prepare(game, mana={"C": 1, "W": 1})
        self.checkpoint(game)
        self.gift_cast(game, spell, "C")
        self.priority(game, "C")
        conceded = game.act("pilot:C", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(conceded.ok, conceded.summary)
        self.assertFalse(game.state.players["C"].in_game)
        before = sum(event.code == "card.draw" for event in game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, sum(event.code == "card.draw" for event in game.state.events))
        self.assertIn("indestructible", game.engine._combat_keywords(guard))
        self.replay(game, load=True)


    def test_dawn_truce_can_choose_hexproof_and_keeps_bonus_when_the_draw_is_prohibited(self):
        game = self.witnessed_match(174012)
        spell = self.add(game.engine, "Dawn's Truce", zone="hand", ref="gift-protection-a")
        prior = self.add(game.engine, "Dawn's Truce", seat="C", zone="hand", ref="gift-protection-c")
        guard = self.add(game.engine, "Grizzly Bears")
        draw = self.add(game.engine, "Revitalize", seat="C", zone="hand")
        spirit = self.add(game.engine, "Spirit of the Labyrinth", seat="B")
        self.assert_closed(game, spirit)
        self.prepare(game, mana={"C": 1, "W": 1})
        game.state.players["C"].mana_pool.update({"C": 2, "W": 2})
        self.checkpoint(game)
        self.cast_for(game, "C", prior)
        self.assertIsNone(self.resolve(game))
        self.assertIn("hexproof", current_player_target_keywords(game.engine, "C"))
        self.cast_for(game, "C", draw)
        self.assertIsNone(self.resolve(game))
        before = len(game.state.players["C"].zones["hand"])
        self.gift_cast(game, spell, "C")
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, len(game.state.players["C"].zones["hand"]))
        self.assertIn("indestructible", game.engine._combat_keywords(guard))
        self.assertTrue(any(event.code == "card.draw.prohibited" for event in game.state.events))
        self.replay(game, load=True)


class GiftFishNativeTests(InheritedNativeFixture, unittest.TestCase):
    witnessed_match = outcomes.MatchOutcomeRulesTests.witnessed_match

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "gift-fish.sqlite3"
        source = ROOT / "tests/fixtures/announced-x-player-rounds-cards.json"
        document=json.loads(source.read_bytes())
        document["cards"]=[row for row in document["cards"] if row["name"]=="Dryad Arbor"]
        ids={row["oracle_id"] for row in document["cards"]}
        document["rulings"]=[row for row in document.get("rulings", ()) if row["oracle_id"] in ids]
        filtered=path.with_name("dryad-arbor.json")
        filtered.write_text(json.dumps(document), encoding="utf-8")
        build_fixture_database([
            ROOT / "tests/fixtures/all-player-turn-rule-cards.json",
            ROOT / "tests/fixtures/bargain-cards.json",
            filtered,
        ], path)
        outcomes.MatchOutcomeRulesTests.use_database.__func__(cls, path)

    def options(self, game, spell):
        self.priority(game, "A")
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in offers if row["id"] == "cast:" + spell.ref)

    def cast_fish_gift(self, game, spell, target, recipient=None):
        offer=self.options(game, spell)
        option="normal" if recipient is None else f"normal+gift:{recipient}"
        action={"action_id":offer["id"],"cost_option":option,"targets":[target.ref],"pay":"auto"}
        accepted=game.act("pilot:A", action)
        self.assertTrue(accepted.ok, accepted.summary)
        return game.state.stack[-1]

    def test_flood_maw_promise_changes_the_target_domain_and_fish_goes_first(self):
        game=self.witnessed_match(70217421)
        spell=self.add(game.engine, "Into the Flood Maw", zone="hand")
        artifact=self.add(game.engine, "Sol Ring", seat="B")
        arbor=self.add(game.engine, "Dryad Arbor", seat="B")
        own=self.add(game.engine, "Grizzly Bears", ref="fish-own")
        guard=self.add(game.engine, "Dawn's Truce", seat="C", zone="hand")
        hidden=self.add(game.engine, "Counterspell", seat="D", zone="hand", ref="fish-private")
        for card in (spell, guard):
            self.assert_closed(game, card)
        self.prepare(game, mana={"U":1})
        game.state.players["C"].mana_pool.update({"C":1,"W":1})
        self.checkpoint(game)
        self.cast_for(game, "C", guard)
        self.assertIsNone(self.resolve(game))
        self.assertIn("hexproof", current_player_target_keywords(game.engine, "C"))
        offer=self.options(game, spell)
        by_id={row["id"]:row for row in offer["cost_options"]}
        self.assertEqual({"normal","normal+gift:B","normal+gift:C","normal+gift:D"},set(by_id))
        self.assertTrue(all(row["requirements"]==by_id["normal"]["requirements"] for row in by_id.values()))
        plain=by_id["normal"]["target_schema"]["legal_refs"]
        promised=by_id["normal+gift:C"]["target_schema"]["legal_refs"]
        self.assertIn(arbor.ref, plain)
        self.assertNotIn(artifact.ref, plain)
        self.assertIn(artifact.ref, promised)
        self.assertNotIn(arbor.ref, promised)
        self.assertNotIn(own.ref, promised)
        before=authoritative_state_hash(game.state)
        for actor, option, target in (("B","normal+gift:C",artifact),("A","normal",artifact),
                                      ("A","normal+gift:C",arbor),("A","normal+gift:A",artifact)):
            result=game.act("pilot:"+actor,{"action_id":offer["id"],"cost_option":option,
                                          "targets":[target.ref],"pay":"auto"})
            self.assertFalse(result.ok)
            self.assertEqual(before,authoritative_state_hash(game.state))
        stack=self.cast_fish_gift(game,spell,artifact,"C")
        self.assertEqual([artifact.ref], stack.targets)
        game = self.replay(game, load=True)
        start_sequence = game.state.event_sequence
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[artifact.object_id].zone)
        fish,=tuple(card for card in game.state.cards.values() if card.is_token)
        self.assertEqual(("C","C","Fish Token",True), (fish.owner,fish.controller,fish.printed_name,fish.tapped))
        current=game.engine._effective_card_data(fish)
        self.assertEqual(["U"],current["colors"])
        self.assertEqual((1,1),tuple(game.engine._numeric_stat(fish.object_id,k) for k in ("power","toughness")))
        new_events = [event for event in game.state.events if event.event_id > start_sequence]
        created = next(event.event_id for event in new_events if event.code == "token.create"
                       and fish.object_id in event.changed_objects)
        bounced = next(event.event_id for event in new_events
                       if event.code == "permanent.return_to_owner_hand"
                       and artifact.object_id in event.changed_objects)
        self.assertLess(created, bounced)
        self.assertNotIn(hidden.ref,json.dumps(game.packet("pilot:A",full=True)))
        self.replay(game,load=True)

    def test_flood_maw_no_gift_can_return_a_land_creature_and_countered_gift_gives_nothing(self):
        for promised in (False,True):
            with self.subTest(promised=promised):
                game=self.witnessed_match(70217422+int(promised))
                spell=self.add(game.engine,"Into the Flood Maw",zone="hand")
                target=self.add(game.engine,"Dryad Arbor" if not promised else "Grizzly Bears",seat="B")
                counter=self.add(game.engine,"Counterspell",seat="B",zone="hand")
                self.prepare(game,mana={"U":1})
                game.state.players["B"].mana_pool["U"]=2
                self.checkpoint(game)
                original=self.cast_fish_gift(game,spell,target,"C" if promised else None)
                if promised:
                    self.cast_for(game,"B",counter,[original.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield" if promised else "hand",game.state.cards[target.object_id].zone)
                self.assertFalse(any(card.is_token for card in game.state.cards.values()))
                self.replay(game,load=True)

    def test_flood_maw_unsupported_gift_grammars_and_forged_target_shape_fail_closed(self):
        from test_fixed_public_alternative_costs import card_record
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.gift_fish_spells import GIFT_FISH_CAPABILITY, GIFT_FISH_COST_FIELD
        from test_controller_creature_keyword_absence import register_card

        record=self.db.lookup("Into the Flood Maw",fuzzy=False)
        synthetic=card_record(record.oracle_text,type_line="Instant",keywords=("Gift",))
        config={"capability_registry":self.registry,"capability_profile":"commander_review"}
        exact=compile_oracle_card(synthetic,**config)
        self.assertEqual("exact",exact.status,exact.material_residuals)
        for text in (record.oracle_text.replace("a tapped Fish","a Treasure",1),
                     record.oracle_text.replace("nonland permanent","permanent",1),
                     record.oracle_text.replace("an opponent controls","you control",1)):
            self.assertNotEqual("exact",compile_oracle_card(replace(synthetic,oracle_text=text),**config).status)
        with patch("quorune.compiler.gift_card_spells._gift_spell_form",return_value=None):
            self.assertNotEqual("exact",compile_oracle_card(synthetic,**config).status)
        report,semantics=register_card(self.db,self.registry,record.name)
        self.assertTrue(report["strict_capability_ready"],report)
        original,=semantics.programs_for_oracle(record.oracle_id)
        changed=deepcopy(original.cost_schema)
        changed[GIFT_FISH_COST_FIELD]["promised"]["types_none"]=[]
        binding=bind_semantic_program_runtime(replace(original,cost_schema=changed),capability_registry=self.registry,
                                              profile="commander_review")
        self.assertFalse(binding["strict"],binding)
        required=tuple(cap for cap in original.capability_dependencies if cap!=GIFT_FISH_CAPABILITY)
        invalid=replace(original,capability_dependencies=required,
                        capability_closure=self.registry.closure(required,profile="commander_review").to_dict())
        binding=bind_semantic_program_runtime(invalid,capability_registry=self.registry,profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:"+GIFT_FISH_CAPABILITY,binding["blockers"])


    def test_flood_maw_copy_inherits_promised_recipient_and_target_branch_then_original_fails_legality(self):
        game=self.witnessed_match(70217424)
        spell=self.add(game.engine,"Into the Flood Maw",zone="hand")
        artifact=self.add(game.engine,"Sol Ring",seat="B")
        arbor=self.add(game.engine,"Dryad Arbor",seat="D")
        copy=self.add(game.engine,"Twincast",seat="C",zone="hand")
        self.prepare(game,mana={"U":1})
        game.state.players["C"].mana_pool["U"]=2
        self.checkpoint(game)
        original=self.cast_fish_gift(game,spell,artifact,"C")
        self.cast_for(game,"C",copy,[original.ref])
        pending=self.resolve(game)
        self.assertEqual(("semantic.choice",["C"]),(pending.kind,pending.actors))
        before=authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C",{"action_id":"choose","targets":[arbor.ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(game.state))
        game=self.replay(game,load=True)
        accepted=game.act("pilot:C",{"action_id":"choose","targets":[artifact.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        game=self.replay(game,load=True)
        self.assertIsNone(self.resolve(game))
        fish,=tuple(card for card in game.state.cards.values() if card.is_token)
        self.assertEqual(("C",True),(fish.controller,fish.tapped))
        self.assertEqual("hand",game.state.cards[artifact.object_id].zone)
        self.assertEqual("graveyard",game.state.cards[spell.object_id].zone)
        self.replay(game,load=True)


    def test_flood_maw_departed_recipient_keeps_the_promised_nonland_target(self):
        game = self.witnessed_match(70217425)
        spell = self.add(game.engine, "Into the Flood Maw", zone="hand")
        artifact = self.add(game.engine, "Sol Ring", seat="B")
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)
        self.cast_fish_gift(game, spell, artifact, "D")
        self.priority(game, "D")
        concede = game.act("pilot:D", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(concede.ok, concede.summary)
        self.assertFalse(game.state.players["D"].in_game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[artifact.object_id].zone)
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        self.replay(game, load=True)


class GiftFishCounterReturnTests(InheritedNativeFixture, unittest.TestCase):
    options = GiftFishNativeTests.options
    cast_fish_gift = GiftFishNativeTests.cast_fish_gift

    @classmethod
    def setUpClass(cls):
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry

        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "gift-fish-counter-return.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / "bound-effect-program-cards.json",
                                fixtures / "all-player-turn-rule-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Gift counter return", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def save_replay(self, game, name):
        from hashlib import file_digest
        from quorune.record import replay_record

        parent = os.environ.get("QUORUNE_GIFT_COUNTER_RETURN_RECORD_PARENT")
        if parent is None:
            self.replay(game, load=True)
            return
        destination = Path(parent) / name
        game.save(destination)
        result = replay_record(destination, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        state_hash = authoritative_state_hash(game.state)
        self.assertEqual(state_hash, result["final_state_hash"])
        with (destination / "manifest.json").open("rb") as handle:
            manifest_hash = file_digest(handle, "sha256").hexdigest()
        destination.with_suffix(".receipt.json").write_text(
            json.dumps({"manifest_sha256": manifest_hash, "final_state_sha256": state_hash,
                        "commands": len(game.commands)}, indent=2) + "\n", encoding="utf-8",
        )

    def await_return_trigger(self, game):
        self.until(game, lambda current: current.state.step == "end_step" and bool(current.state.stack))
        trigger, = game.state.stack
        self.assertEqual(("A", [], "Return linked exiled objects"), (trigger.controller, trigger.targets, trigger.label))
        return trigger

    def test_gust_unpromised_returns_its_exiled_card_with_one_entry_counter(self):
        game = self.session(7021741620)
        spell = self.add(game.engine, "Parting Gust", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        own = self.add(game.engine, "Grizzly Bears", ref="gust-own-bear")
        self.prepare(game, mana={"W": 2})
        self.assert_closed(game, spell)
        self.checkpoint(game)
        offer = self.options(game, spell)
        options = {row["id"]: row for row in offer["cost_options"]}
        self.assertEqual({"normal", "normal+gift:B", "normal+gift:C", "normal+gift:D"}, set(options))
        self.assertTrue(all(row["requirements"] == options["normal"]["requirements"] for row in options.values()))
        self.assertTrue(all(set(row["target_schema"]["legal_refs"]) == {target.ref, own.ref} for row in options.values()))
        prior_id = target.logical_object_id
        self.cast_fish_gift(game, spell, target)
        self.assertIsNone(self.resolve(game))
        exiled = game.state.cards[target.object_id]
        self.assertEqual("exile", exiled.zone)
        self.assertNotEqual(prior_id, exiled.logical_object_id)
        exile_id = exiled.logical_object_id
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        self.save_replay(game, "parting-gust-original-exile-game-record")
        trigger = self.await_return_trigger(game)
        effect, = trigger.context["dynamic_effects"]
        identities = effect["objects"]
        self.assertEqual([exile_id], [row["logical_object_id"] for row in identities])
        self.save_replay(game, "parting-gust-end-step-pending-game-record")
        self.assertIsNone(self.resolve(game))
        entered = game.state.cards[target.object_id]
        self.assertEqual(("battlefield", "B", 1), (entered.zone, entered.controller, entered.counters.get("+1/+1")))
        self.assertNotEqual(exile_id, entered.logical_object_id)
        self.assertEqual((3, 3), tuple(game.engine._numeric_stat(target.object_id, value) for value in ("power", "toughness")))
        self.save_replay(game, "parting-gust-return-game-record")

    def test_gust_cannot_return_a_second_exile_incarnation(self):
        game = self.session(7021741621)
        spell = self.add(game.engine, "Parting Gust", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        pull = self.add(game.engine, "Pull from Eternity", zone="hand")
        cremate = self.add(game.engine, "Cremate", zone="hand")
        self.prepare(game, mana={"W": 3, "B": 1})
        for card in (spell, pull, cremate):
            self.assert_closed(game, card)
        self.checkpoint(game)
        self.cast_fish_gift(game, spell, target)
        self.assertIsNone(self.resolve(game))
        original = game.state.cards[target.object_id].logical_object_id
        self.cast_for(game, "A", pull, [game.state.cards[target.object_id].ref], reserve={"B": 1})
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.cast_for(game, "A", cremate, [game.state.cards[target.object_id].ref])
        self.resolve_top(game)
        later = game.state.cards[target.object_id]
        self.assertEqual("exile", later.zone)
        later_id = later.logical_object_id
        self.assertNotEqual(original, later_id)
        self.await_return_trigger(game)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[target.object_id]
        self.assertEqual(("exile", later_id, None), (current.zone, current.logical_object_id, current.counters.get("+1/+1")))
        self.save_replay(game, "parting-gust-incarnation-moved-game-record")

    def test_gust_promised_copy_gives_the_fish_before_exile_and_original_fizzles(self):
        from quorune.zone_transitions import ZoneTransitionOwner

        game = self.session(7021741622)
        spell = self.add(game.engine, "Parting Gust", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        copy_spell = self.add(game.engine, "Twincast", seat="C", zone="hand")
        self.prepare(game, mana={"W": 2})
        game.state.players["C"].mana_pool["U"] = 2
        for card in (spell, copy_spell):
            self.assert_closed(game, card)
        self.checkpoint(game)
        original = self.cast_fish_gift(game, spell, target, "D")
        self.cast_for(game, "C", copy_spell, [original.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["C"]), (pending.kind, pending.actors))
        self.assertIsNone(game.packet("pilot:D", full=True)["decision"])
        self.save_replay(game, "parting-gust-copy-pending-game-record")
        result = game.act("pilot:C", {"action_id": "choose", "targets": [target.ref]})
        self.assertTrue(result.ok, result.summary)
        observe = ZoneTransitionOwner._publish_simultaneous_zone_batch
        groups = []

        def record_exile(owner, snapshots, *args, **kwargs):
            if any(card.object_id == target.object_id and card.zone == "exile" for card, _ in snapshots):
                groups.append([(card.owner, card.controller, card.tapped) for card in owner.state.cards.values()
                               if card.is_token and card.zone == "battlefield"])
            return observe(owner, snapshots, *args, **kwargs)

        with patch.object(ZoneTransitionOwner, "_publish_simultaneous_zone_batch", record_exile):
            self.resolve_top(game)
        self.assertEqual([[("D", "D", True)]], groups)
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        self.save_replay(game, "parting-gust-original-illegal-game-record")
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        fish, = (card for card in game.state.cards.values() if card.is_token)
        self.assertEqual(("D", "D", "Fish Token", True), (fish.owner, fish.controller, fish.printed_name, fish.tapped))
        self.assertEqual(["U"], game.engine._effective_card_data(fish)["colors"])
        self.assertEqual((1, 1), tuple(game.engine._numeric_stat(fish.object_id, key) for key in ("power", "toughness")))
        self.until(game, lambda current: current.state.step == "end_step")
        self.assertFalse(game.state.stack)
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        self.save_replay(game, "parting-gust-copy-resolved-game-record")

    def test_gust_requires_its_printed_counter_return_and_untouched_target_domain(self):
        from copy import deepcopy
        from test_controller_creature_keyword_absence import register_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.gift_fish_spells import GIFT_FISH_COUNTER_RETURN_CAPABILITY

        card = self.db.lookup("Parting Gust", fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        profile = "commander_review"
        deps = tuple(cap for cap in program.capability_dependencies if cap != GIFT_FISH_COUNTER_RETURN_CAPABILITY)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=profile).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=profile)
        self.assertFalse(result["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{GIFT_FISH_COUNTER_RETURN_CAPABILITY}", result["blockers"])
        for field, bad in (("token", True), ("controller_relation", "opponent")):
            schema = {**deepcopy(program.target_schema), field: bad}
            result = bind_semantic_program_runtime(
                replace(program, target_schema=schema), capability_registry=self.registry, profile=profile,
            )
            self.assertFalse(result["strict"])
        for text in (card.oracle_text.replace("nontoken creature", "creature"),
                     card.oracle_text.replace("wasn't promised", "was promised"),
                     card.oracle_text.replace("one", "two").replace("a +1/+1 counter", "two +1/+1 counters"),
                     card.oracle_text.replace("the next end step", "your next end step")):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(card, oracle_text=text), capability_registry=self.registry, capability_profile=profile,
            ).status)
