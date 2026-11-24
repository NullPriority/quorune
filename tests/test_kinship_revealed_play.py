"""Actual shared-type reveal followed by an optional spell or land play."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.shared_type_library_model import SHARED_TYPE_PLAY_CAPABILITY
from quorune.semantic_runtime.action_permissions import LAND_PLAY_ACCOUNTING_STAT, LAND_PLAY_ACCOUNTING_VERSION
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests
from test_shared_creature_type_quantities import SharedTypeLibraryNativeFixture


class KinshipRevealedPlayTests(SharedTypeLibraryNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "kinship-play.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "shared-type-library-cards.json",
            fixtures / "controlled-chosen-subtype-addition-cards.json",
            fixtures / "cast-after-attacking-cards.json",
            fixtures / "optional-result-followup-cards.json",
            fixtures / "external-entry-tapped-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        ruler = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Kinship resolution", [
            DeckEntry(ruler, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [ruler])

    def scene(self, seed, card_name, *, adaptation=False):
        game = self.private_library_session(seed, top_name=card_name)
        source = self.add(game.engine, "Leaf-Crowned Elder", zone="hand", ref="ORIGINAL-KINSHIP")
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        enchantment = None
        if adaptation:
            enchantment = self.add(game.engine, "Arcane Adaptation", zone="hand", ref="SHARED-LIBRARY-TYPE")
            self.assert_closed(game, enchantment)
        self.prepare(game, mana={"C": 4, "G": 2, "U": 1} if enchantment is not None else {"C": 2, "G": 2})
        self.checkpoint(game)
        if enchantment is not None:
            self.cast_for(game, "A", enchantment)
            pending = self.resolve(game)
            self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
            rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
            choice, = [row["id"] for row in rows if row["id"].endswith(":treefolk")]
            accepted = game.act("pilot:A", {"action_id":"choose","replacement":choice})
            self.assertTrue(accepted.ok, accepted.summary)
            self.assertIsNone(self.resolve(game))
        self.cast_for(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.advance_to_look(game, source)
        top = game.state.cards[game.state.players["A"].zones["library"][-1]]
        for seat in "ABCD":
            self.assertNotIn(top.ref, json.dumps(game.packet("pilot:"+seat, full=True)))
        return game, source, top

    def begin_reveal(self, game, top):
        self.choose(game, "look")
        self.assertIn(top.ref, json.dumps(game.packet("pilot:A", full=True)))
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:"+seat, full=True)["decision"])
            self.assertNotIn(top.ref, json.dumps(game.packet("pilot:"+seat, full=True)))

    def play_options(self, game, top):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.choice",["A"]),(pending.kind,pending.actors))
        schema = pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
        self.assertEqual(["play","decline"], schema["legal_values"])
        self.assertTrue(all(option["card"] == top.ref for option in schema["cast_options"]))
        for seat in "BCD":
            packet = game.packet("pilot:"+seat, full=True)
            self.assertIsNone(packet["decision"])
            self.assertIn(top.ref, json.dumps(packet))
        return schema["cast_options"]

    def submit_play(self, game, option):
        old = authoritative_state_hash(game.state)
        command = {"action_id":"choose","choice":"play","cast":{"cost_option":option["id"]}}
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertFalse(game.act("pilot:A", {
            **command, "cast":{**command["cast"],"card":"another-library-card"},
        }).ok)
        self.assertEqual(old, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_original_reveals_then_casts_the_one_library_spell_without_mana(self):
        game, _, top = self.scene(2072801, "Ironroot Treefolk")
        self.begin_reveal(game, top)
        game = self.replay(game, load=True)
        self.choose(game, "reveal")
        option, = self.play_options(game, top)
        self.assertNotEqual("land", option["kind"])
        self.assertEqual(0, sum(option["requirements"].values()))
        game = self.replay(game, load=True)
        self.submit_play(game, option)
        spell, = game.state.stack
        self.assertEqual(("spell", top.object_id, "A"),(spell.kind, spell.card_object_id, spell.controller))
        self.assertEqual(0, spell.context["mana_spent_total"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield",game.state.cards[top.object_id].zone)
        self.replay(game, load=True)

    def test_actual_matching_land_is_played_during_upkeep_and_spends_one_land_play(self):
        game, _, top = self.scene(2072802, "Dryad Arbor", adaptation=True)
        self.begin_reveal(game, top)
        self.choose(game, "reveal")
        option, = self.play_options(game, top)
        self.assertEqual("land", option["kind"])
        before = game.state.players["A"].land_plays_remaining
        game = self.replay(game, load=True)
        self.submit_play(game, option)
        self.assertEqual("battlefield", game.state.cards[top.object_id].zone)
        self.assertFalse(any(item.card_object_id == top.object_id for item in game.state.stack))
        self.assertEqual(before-1, game.state.players["A"].land_plays_remaining)
        self.replay(game, load=True)

    def test_declining_a_play_or_having_no_land_play_keeps_the_revealed_card_in_the_library(self):
        for full in (False, True):
            with self.subTest(land_quota_used=full):
                game, _, top = self.scene(2072810 + int(full), "Dryad Arbor", adaptation=True)
                if full:
                    player = game.state.players["A"]
                    player.land_plays_remaining = 0
                    player.stats[LAND_PLAY_ACCOUNTING_STAT] = {
                        "version":LAND_PLAY_ACCOUNTING_VERSION,"base_allowance":1,"played":1,
                    }
                    self.checkpoint(game)
                self.begin_reveal(game, top)
                self.choose(game, "reveal")
                if not full:
                    self.play_options(game, top)
                    self.choose(game, "decline")
                self.assertIsNone(self.resolve(game))
                self.assertEqual(top.object_id, game.state.players["A"].zones["library"][-1])
                self.replay(game, load=True)

    def test_revealed_land_keeps_its_placement_through_entry_replacement_replay(self):
        game, _, top = self.scene(2072825, "Dryad Arbor", adaptation=True)
        orb = self.add(game.engine, "Orb of Dreams", ref="KINSHIP-ORDER-TAPPED")
        untap = self.add(game.engine, "Generic Controlled Untapped Entry", ref="KINSHIP-ORDER-UNTAPPED")
        self.assert_closed(game, orb)
        self.assert_closed(game, untap)
        self.checkpoint(game)

        self.begin_reveal(game, top)
        self.choose(game, "reveal")
        option, = self.play_options(game, top)
        before = game.state.players["A"].land_plays_remaining
        self.submit_play(game, option)
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual("library", game.state.cards[top.object_id].zone)
        self.assertEqual(before, game.state.players["A"].land_plays_remaining)
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        apply_first = next(option["id"] for option in options if orb.ref in option["id"])
        accepted = game.act("pilot:A", {"action_id":"choose","replacement":apply_first})
        self.assertTrue(accepted.ok, accepted.summary)
        current = game.state.cards[top.object_id]
        self.assertEqual(("battlefield", False), (current.zone, current.tapped))
        self.assertEqual(before - 1, game.state.players["A"].land_plays_remaining)
        self.replay(game, load=True)

    def test_original_requires_matching_result_and_play_authority(self):
        card = self.db.lookup("Leaf-Crowned Elder", fuzzy=False)
        active = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", active.status, active.material_residuals)
        self.assertIn(SHARED_TYPE_PLAY_CAPABILITY, active.faces[0].nodes[0].capability_dependencies)
        for mutated in ("you may play another card","you must play that card","you may put that card onto the battlefield"):
            altered = replace(card, oracle_text=card.oracle_text.replace("you may play that card", mutated))
            self.assertNotEqual("exact",compile_oracle_card(
                altered, capability_registry=self.registry, capability_profile="commander_review",
            ).status)
        raw = json.loads((ROOT/"quorune/rules/capability-registry.json").read_bytes())
        selected = next(row for row in raw["capabilities"] if row["id"]==SHARED_TYPE_PLAY_CAPABILITY)
        selected.update(status="blocked",blockers=["Withdraw the reviewed offer authority."])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT/"platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact",compile_oracle_card(card, capability_registry=registry,
                                                       capability_profile="commander_review").status)
        with patch("quorune.compiler.shared_type_library_templates.shared_type_library_result_template", return_value=None):
            self.assertNotEqual("exact",compile_oracle_card(card, capability_registry=self.registry,
                                                           capability_profile="commander_review").status)
