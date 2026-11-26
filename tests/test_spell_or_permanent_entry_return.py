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
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.opponent_spell_or_permanent_return import ENTRY_CAPABILITY, entry_target_schema
from scripts.build_test_database import build_fixture_database


SOURCE = "Venser, Shaper Savant"


class SpellOrPermanentEntryReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "entry-return.sqlite3"
        fixtures = ("bound-effect-program-cards", "spell-or-permanent-entry-return-cards", "target-land-controller-choice-cards")
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Spell or permanent return", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def choose(self, game, target):
        self.assertEqual("semantic.target", game.state.pending_decision.kind)
        command = {"action_id":"choose", "targets":[target]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)

    def test_actual_flash_entry_returns_own_spell_through_both_target_boundaries(self):
        game = self.session(603630)
        source = self.add(game.engine, SOURCE, zone="hand")
        spell = self.add(game.engine, "Grizzly Bears", zone="hand")
        hidden = self.add(game.engine, "Unsummon", seat="C", zone="hand", ref="venser-hidden-response")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C":4, "G":1, "U":4})
        self.checkpoint(game)
        self.cast(game, "A", spell)
        original = game.state.stack[-1]
        self.cast(game, "A", source)
        self.assertFalse(game.state.stack[-1].targets)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        self.choose(game, original.ref)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[spell.object_id].zone)
        self.assertFalse(any(item.ref == original.ref for item in game.state.stack))
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_actual_own_land_return_and_stale_permanent_revalidation_replay(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                game = self.session(603633 + stale)
                source = self.add(game.engine, SOURCE, zone="hand")
                land = self.add(game.engine, "Forest")
                removal = self.add(game.engine, "Generic Land Removal", seat="B", zone="hand")
                game.state.players["B"].mana_pool.update({"G":1,"C":1})
                self.prepare(game, mana={"C":2,"U":2})
                self.checkpoint(game)
                self.cast(game, "A", source)
                self.resolve_top(game)
                self.choose(game, land.ref)
                if stale:
                    self.cast(game, "B", removal, [land.ref])
                    self.resolve_top(game)
                    self.assertEqual("graveyard", game.state.cards[land.object_id].zone)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual("graveyard" if stale else "hand", game.state.cards[land.object_id].zone)
                self.replay(game, load=True)

    def test_original_binds_the_two_public_object_kinds_without_an_opponent_or_nonland_filter(self):
        record = self.db.lookup(SOURCE, fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        (node,) = (node for face in ir.faces for node in face.nodes if ENTRY_CAPABILITY in node.capability_dependencies)
        self.assertEqual("permanent.enter.self", node.event)
        self.assertEqual(entry_target_schema(), node.target_schema)
        changed = replace(record, oracle_text=record.oracle_text.replace("its owner's hand", "your hand"))
        self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                                                        capability_profile="commander_review").status)

    def test_omitting_the_mixed_entry_target_is_killed(self):
        from quorune.compiler import fixed_owner_zone_move_templates as owner

        with patch.object(owner, "entry_target_schema", return_value={"zones":["stack"],"categories":["spell"],"count":1}):
            with self.assertRaises(AssertionError):
                self.test_original_binds_the_two_public_object_kinds_without_an_opponent_or_nonland_filter()
