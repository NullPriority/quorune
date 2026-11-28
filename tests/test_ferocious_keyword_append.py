"""Native Temur Battle Rage targets, live Ferocious, and combat replay."""

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.ferocious_keyword_append import FEROCIOUS_KEYWORD_CAPABILITY, ferocious_creature_condition
from scripts.build_test_database import build_fixture_database


NAME = "Temur Battle Rage"


class FerociousKeywordAppendTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "ferocious-keyword.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/equipped-color-upkeep-counter-cards.json",
            ROOT / "tests/fixtures/resolution-mana-choice-cards.json",
            ROOT / "tests/fixtures/ferocious-keyword-append-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Native Ferocious", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def setup(self, seed, *, target_seat="A"):
        game = self.session(seed)
        spell = self.add(game.engine, NAME, zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat=target_seat, ref="ferocious-target")
        target.acquired_control_turn_count = -1
        qualifier = self.add(game.engine, "Grizzly Bears", ref="ferocious-power-source")
        qualifier.counters["+1/+1"] = 2
        removal = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        return game, spell, target, qualifier, removal

    def keywords(self, game, card):
        return set(game.engine._combat_keywords(game.state.cards[card.object_id]))

    def test_temur_strict_source_and_same_target_bound_shape(self):
        record = self.db.lookup(NAME)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        spell, = ir.faces[0].nodes
        self.assertIn(FEROCIOUS_KEYWORD_CAPABILITY, spell.capability_dependencies)
        self.assertEqual(1, spell.target_schema["count"])
        prefix, wrapper = spell.effects
        self.assertEqual(("Double Strike", "$target.0"), (prefix["keyword"], prefix["card"]))
        self.assertEqual(ferocious_creature_condition().to_dict(), wrapper["condition"])
        child, = wrapper["effects"]
        self.assertEqual(("Trample", "$target.0"), (child["keyword"], child["card"]))
        for before, after in (("power 4", "power 3"), ("That creature also", "Another target creature also")):
            variant = replace(record, oracle_text=record.oracle_text.replace(before, after))
            residual = compile_oracle_card(variant, capability_registry=self.registry, capability_profile="commander_review")
            self.assertTrue(residual.material_residuals, after)
        from quorune.compiler import ferocious_keyword_append as lowering

        with patch.object(lowering, "ferocious_keyword_append_template", return_value=None):
            omitted = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
            self.assertTrue(omitted.material_residuals)
        game = self.session(7021900)
        self.assert_closed(game, self.add(game.engine, NAME, zone="hand"))

    def test_temur_ordinary_double_strike_when_ferocious_is_lost(self):
        game, spell, target, qualifier, removal = self.setup(7021901, target_seat="B")
        self.prepare(game, mana={"C": 1, "R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, (target.ref,))
        self.cast_for(game, "B", removal, (qualifier.ref,))
        pending = self.replay(game, load=True)
        self.assertIsNone(self.resolve(pending))
        self.assertEqual("hand", pending.state.cards[qualifier.object_id].zone)
        self.assertEqual({"double strike"}, self.keywords(pending, target))
        self.replay(pending, load=True)

    def test_temur_trample_uses_both_damage_steps_and_expires_at_cleanup(self):
        game, spell, target, qualifier, removal = self.setup(7021902)
        blocker = self.add(game.engine, "Grizzly Bears", seat="B", ref="ferocious-blocker")
        self.prepare(game, mana={"C": 1, "R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, (target.ref,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"double strike", "trample"}, self.keywords(game, target))
        self.cast_for(game, "B", removal, (qualifier.ref,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"double strike", "trample"}, self.keywords(game, target))
        self.attack(game, [(target, "B")])
        self.until(game, lambda match: match.state.pending_decision.kind == "combat.blockers")
        life = game.state.players["B"].life
        result = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: target.ref}})
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda match: match.state.pending_decision.kind == "combat.damage")
        game = self.replay(game, load=True)
        first = game.act("pilot:A", {"a": "dmg", "assignments": [
            {"source": target.ref, "target": blocker.ref, "amount": 2},
        ]})
        self.assertTrue(first.ok, first.summary)
        self.assertEqual(life, game.state.players["B"].life)
        self.assertEqual("graveyard", game.state.cards[blocker.object_id].zone)
        self.until(game, lambda match: (
            match.state.players["B"].life < life or match.state.pending_decision.kind == "combat.damage"
        ))
        if game.state.players["B"].life == life:
            regular = game.act("pilot:A", {"a": "dmg", "assignments": [
                {"source": target.ref, "target": "B", "amount": 2},
            ]})
            self.assertTrue(regular.ok, regular.summary)
        self.assertEqual(life - 2, game.state.players["B"].life)
        self.until(game, lambda match: (
            match.state.active_player == "B" or match.state.pending_decision.kind == "cleanup.discard"
        ))
        if game.state.active_player == "A":
            cleanup = game.act("pilot:A", {"a": "discard", "cards": [qualifier.ref]})
            self.assertTrue(cleanup.ok, cleanup.summary)
        self.until(game, lambda match: match.state.active_player == "B")
        self.assertFalse(self.keywords(game, target))
        self.replay(game, load=True)

    def test_temur_illegal_only_target_fizzles_both_grants(self):
        game, spell, target, _, removal = self.setup(7021903)
        self.prepare(game, mana={"C": 1, "R": 1})
        self.checkpoint(game)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in actions if row["id"].startswith(f"cast:{spell.ref}")]
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", {"action_id": action, "targets": [target.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", spell, (target.ref,))
        self.cast_for(game, "B", removal, (target.ref,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertFalse(self.keywords(game, target))
        self.replay(game, load=True)
