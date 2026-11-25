"""A legal target's owner chooses its library position during the original resolution."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_zone_moves import OWNER_LIBRARY_POSITION_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class OwnerLibraryPositionChoiceTests(InheritedNativeFixture, unittest.TestCase):
    spell = "Trial Ordered Return"
    followup = "Trial Ordered Insight"
    body = "Trial Refuge Creature"
    bounce = "Trial Trail Retreat"
    basic = "Generic Bound Plains"
    spell_mana = {"C": 1, "U": 1}

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "owner-library-position.sqlite3"
        fixture = ROOT / "tests/fixtures"
        build_fixture_database([
            fixture / "bound-effect-program-cards.json",
            fixture / "source-counter-reflexive-cards.json",
            fixture / "source-optional-creature-unblockable-cards.json",
            fixture / "owner-library-position-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        name = "Generic Bound Commander"
        cls.deck = DeckDefinition("Owner library placement", [
            DeckEntry(name, 1, "commander"), DeckEntry(cls.basic, 30),
        ], [name])

    def scene(self, seed, *, spell_name=None):
        game = self.session(seed)
        spell = self.add(game.engine, spell_name or self.spell, zone="hand", ref="CASTING-PLAYER-SPELL")
        target = self.add(game.engine, self.body, seat="C", ref="OWNED-BY-C")
        bounce = self.add(game.engine, self.bounce, seat="B", zone="hand", ref="HIDDEN-RESPONSE")
        game.engine.change_control(target.object_id, "B")
        for card in (spell, bounce):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=self.spell_mana)
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast(game, "A", spell, [target.ref])
        return game, spell, target, bounce

    def test_target_owner_alone_places_the_other_players_permanent_and_replays(self):
        for index, position in enumerate(("top", "bottom")):
            with self.subTest(position=position):
                game, spell, target, bounce = self.scene(4003101 + index)
                before_library = tuple(game.state.players["C"].zones["library"])
                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ["C"]), (pending.kind, pending.actors))
                action, = game.packet("pilot:C", full=True)["decision"]["legal_actions"]
                self.assertEqual(["top", "bottom"], action["choice_schema"]["legal_values"])
                self.assertNotIn(bounce.ref, json.dumps(game.packet("pilot:C", full=True)))
                self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
                before = authoritative_state_hash(game.state)
                command = {"action_id": "choose", "choice": position}
                for principal in ("pilot:A", "pilot:B", "pilot:D"):
                    self.assertFalse(game.act(principal, command).ok)
                self.assertFalse(game.act("pilot:C", {**command, "choice": "hand"}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                response = game.act("pilot:C", command)
                self.assertTrue(response.ok, response.summary)
                self.assertIsNone(self.resolve(game))
                after = tuple(game.state.players["C"].zones["library"])
                expected = (*before_library, target.object_id) if position == "top" else (target.object_id, *before_library)
                self.assertEqual(expected, after)
                self.assertEqual("library", game.state.cards[target.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
                self.replay(game, load=True)

    def test_illegal_target_creates_no_owner_choice(self):
        game, spell, target, bounce = self.scene(4003103)
        before_library = tuple(game.state.players["C"].zones["library"])
        self.cast(game, "B", bounce, [target.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual(before_library, tuple(game.state.players["C"].zones["library"]))
        self.replay(game, load=True)

    def test_followup_library_choice_still_belongs_to_the_original_caster(self):
        game, _, target, _ = self.scene(4003104, spell_name=self.followup)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["C"]), (pending.kind, pending.actors))
        placed = game.act("pilot:C", {"action_id": "choose", "choice": "bottom"})
        self.assertTrue(placed.ok, placed.summary)
        self.assertEqual(target.object_id, game.state.players["C"].zones["library"][0])
        self.assertEqual("library", game.state.cards[target.object_id].zone)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertIsNone(game.packet("pilot:C", full=True)["decision"])
        card_id = game.state.players["A"].zones["library"][-1]
        top = game.state.cards[card_id].ref
        command = {"action_id": "choose", "cards": {"top": [], "graveyard": [top]}}
        self.assertFalse(game.act("pilot:C", command).ok)
        game = self.replay(game, load=True)
        continued = game.act("pilot:A", command)
        self.assertTrue(continued.ok, continued.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[card_id].zone)
        self.replay(game, load=True)

    def test_admission_requires_the_target_owner_and_both_fixed_library_positions(self):
        record = self.db.lookup(self.spell)

        def compile_card(card, registry):
            return compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")

        ir = compile_card(record, self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIn(OWNER_LIBRARY_POSITION_CAPABILITY, node.capability_dependencies)
        self.assertEqual({"kind": "target_owner_information", "schema_version": 1, "index": 0}, node.effects[0]["player"])
        for text in (record.oracle_text.replace("owner", "controller"),
                     record.oracle_text.replace("their library", "your library"),
                     record.oracle_text + " Rewrite the game."):
            self.assertNotEqual("exact", compile_card(replace(record, oracle_text=text), self.registry).status)
        data = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        cap = next(row for row in data["capabilities"] if row["id"] == OWNER_LIBRARY_POSITION_CAPABILITY)
        cap.update(status="blocked", blockers=["Withdraw the original owner's position choice."])
        review = CapabilityRegistry(data)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=data, declaration_source=declarations,
                           discovered_test_ids=set(discover_tests(ROOT).values()))
        review.mark_evidence_verified(validate_capability_evidence_index(index, registry=review))
        self.assertNotEqual("exact", compile_card(record, review).status)
        with patch("quorune.compiler.resolution_effect_templates.owner_library_position_template", return_value=None):
            self.assertNotEqual("exact", compile_card(record, self.registry).status)
