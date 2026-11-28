"""Actual original target mana value and ordinary destruction outcomes."""

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
from quorune.rules.target_destruction_mana_value_tokens import (
    TARGET_DESTRUCTION_MANA_TOKEN_AMOUNT, TARGET_DESTRUCTION_MANA_TOKEN_CAPABILITY,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class TargetDestructionManaValueTokenTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "target-destruction-mana-value-tokens.sqlite3"
        fixture = ROOT / "tests/fixtures/target-destruction-mana-value-tokens-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Original target mana value", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def ready(self, game, *cards, mana=None):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"U": 2, "G": 1})
        self.prepare(game, mana=mana or {"R": 1, "G": 1, "W": 1})
        self.checkpoint(game)

    def assert_saprolings(self, game, expected):
        tokens = [card for card in game.state.cards.values()
                  if card.zone == "battlefield" and card.is_token and card.printed_name == "Saproling Token"]
        self.assertEqual(expected, len(tokens))
        for token in tokens:
            self.assertEqual(("A", "A"), (token.owner, token.controller))
            self.assertEqual((1, 1), tuple(game.engine._numeric_stat(token.object_id, stat) for stat in ("power", "toughness")))
        if tokens:
            self.assertEqual(1, len({card.zone_timestamp for card in tokens}))

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_paid_artifact_mutation_uses_the_actual_copy_mana_value_through_exile_and_replay(self):
        game = self.session(76350001)
        spell = self.add(game.engine, "Artifact Mutation", zone="hand")
        steel = self.add(game.engine, "Sculpting Steel", zone="hand")
        model = self.add(game.engine, "Sol Ring", seat="B")
        rest = self.add(game.engine, "Rest in Peace", seat="C")
        leyline = self.add(game.engine, "Leyline of the Void", seat="B")
        hidden = self.add(game.engine, "Boomerang", seat="D", zone="hand", ref="mutation-unrelated-hand")
        self.ready(game, spell, steel, rest, leyline, mana={"C": 3, "R": 1, "G": 1})
        self.cast_for(game, "A", steel, reserve={"R": 1, "G": 1})
        self.assertEqual((1, 1), tuple(game.state.players["A"].mana_pool.get(color, 0) for color in ("R", "G")))
        self.assertEqual("replacement.order", self.resolve(game).kind)
        choices = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        copied, = (row for row in choices if model.ref in row["id"] and not row["id"].startswith("decline:"))
        result = game.act("pilot:A", {"action_id": "choose", "replacement": copied["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        target = game.state.cards[steel.object_id]
        self.assertEqual(1, game.engine._effective_card_data(target)["mana_value"])

        self.cast(game, "A", spell, [target.ref])
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        self.assert_saprolings(game, 0)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(hidden.ref, json.dumps(game.packet(principal, full=True)))
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        command = {"action_id": "choose", "replacement": options[0]["id"]}
        self.reject(game, "B", command)
        self.reject(game, "A", {**command, "replacement": "unoffered"})
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        pending = self.resolve(game)
        if pending is not None:
            self.assertEqual("replacement.order", pending.kind)
            principal = game.pending_principals()[0]
            options = game.packet(principal, full=True)["decision"]["ctx"]["options"]
            result = game.act(principal, {"action_id": "choose", "replacement": options[0]["id"]})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(game))
        target = game.state.cards[steel.object_id]
        self.assertEqual("exile", target.zone)
        self.assertEqual(3, game.engine._effective_card_data(target)["mana_value"])
        self.assertEqual("battlefield", game.state.cards[model.object_id].zone)
        self.assert_saprolings(game, 1)
        destruction, = (event for event in game.state.events if event.code == "permanent.destroyed")
        creation, = (event for event in game.state.events if event.code == "token.create")
        self.assertLess(game.state.events.index(destruction), game.state.events.index(creation))
        self.replay(game, load=True)

    def regenerate(self, game, hollow, subject):
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        row, = (action for action in actions if action["id"].startswith(f"activate:{hollow.ref}:")
                and subject.ref in json.dumps(action.get("target_schema")))
        result = game.act("pilot:B", {"action_id": row["id"], "targets": [subject.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[subject.object_id].regeneration_shields)

    def test_paid_forms_keep_their_printed_regeneration_and_tokens_even_when_destruction_is_prevented(self):
        cases = (("Artifact Mutation", "Leaden Myr", True, "graveyard", 2),
                 ("Aura Mutation", "Nyx-Fleece Ram", True, "battlefield", 2),
                 ("Artifact Mutation", "Darksteel Ingot", False, "battlefield", 3))
        for index, (name, subject, can_shield, destination, amount) in enumerate(cases):
            with self.subTest(card=name, target=subject):
                game = self.session(76350020 + index)
                spell = self.add(game.engine, name, zone="hand")
                target = self.add(game.engine, subject, seat="B")
                other = self.add(game.engine, "Plains", seat="C")
                hollow = self.add(game.engine, "Yavimaya Hollow", seat="B") if can_shield else None
                self.ready(game, spell, target, *((hollow,) if hollow is not None else ()))
                self.priority(game, "A")
                action = self.action(game, "A", spell)
                refs = action["target_schema"]["groups"][0]["legal_refs"]
                self.assertIn(target.ref, refs)
                self.assertNotIn(other.ref, refs)
                self.reject(game, "A", {"action_id": action["id"], "targets": [other.ref], "pay": "auto"})
                self.cast(game, "A", spell, [target.ref])
                if hollow is not None:
                    self.regenerate(game, hollow, target)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                current = game.state.cards[target.object_id]
                self.assertEqual(destination, current.zone)
                self.assertEqual(0, current.regeneration_shields)
                self.assert_saprolings(game, amount)
                self.replay(game, load=True)

    def test_paid_zero_mana_value_and_an_illegal_original_target_produce_no_tokens(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.session(76350030 + departed)
                name = "Aura Mutation" if departed else "Artifact Mutation"
                subject = "Nyx-Fleece Ram" if departed else "Great Furnace"
                spell = self.add(game.engine, name, zone="hand")
                target = self.add(game.engine, subject, seat="B")
                response = self.add(game.engine, "Boomerang", seat="B", zone="hand") if departed else None
                self.ready(game, spell, *((response,) if response is not None else ()))
                self.cast(game, "A", spell, [target.ref])
                if departed:
                    self.cast(game, "B", response, [target.ref])
                    self.resolve_top(game)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("hand" if departed else "graveyard", game.state.cards[target.object_id].zone)
                self.assert_saprolings(game, 0)
                self.replay(game, load=True)

    def test_complete_originals_require_independent_target_mana_value_and_keep_their_different_first_actions(self):
        cap = TARGET_DESTRUCTION_MANA_TOKEN_CAPABILITY
        originals = (
            ("Artifact Mutation", "artifact", True, register_card(self.db, self.registry, "Artifact Mutation")),
            ("Aura Mutation", "enchantment", False, register_card(self.db, self.registry, "Aura Mutation")),
        )
        for name, kind, prohibited, (report, programs) in originals:
            with self.subTest(card=name):
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = (row for row in programs.programs_for_oracle(report["oracle_id"])
                            if cap in row.capability_dependencies)
                first, token = program.effects
                self.assertIs(first.get("regeneration_prohibited", False), prohibited)
                self.assertEqual((kind,), tuple(program.target_schema["types_any"]))
                self.assertEqual(TARGET_DESTRUCTION_MANA_TOKEN_AMOUNT.to_dict(), dict(token["quantity"]))
                deps = tuple(key for key in program.capability_dependencies if key != cap)
                withdrawn = replace(program, capability_dependencies=deps,
                                    capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                result = bind_semantic_program_runtime(withdrawn, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"])
                self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", result["blockers"])
        original = self.db.lookup("Aura Mutation", fuzzy=False)
        for before, after in (("enchantment's mana value", "enchantment's power"),
                              ("Destroy target enchantment.", "Destroy target enchantment. It can't be regenerated."),
                              ("Create X", "That enchantment's controller creates X"),
                              ("mana value.", "mana value. Rewrite history.")):
            with self.subTest(unsupported=after):
                candidate = replace(original, oracle_text=original.oracle_text.replace(before, after))
                lowered = compile_oracle_card(candidate, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", lowered.status)
