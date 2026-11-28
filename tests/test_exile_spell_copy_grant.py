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
from quorune.stack_spell_copy_model import EXILE_CAST_COPY_CAPABILITY, PERMANENT_SPELL_COPY_FIELD, PermanentSpellCopySpec
from scripts.build_test_database import build_fixture_database


class ExileSpellCopyGrantTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "exile-spell-copy-grant.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "exile-spell-copy-grant-cards",
            "attached-cast-restriction-cards", "activation-restriction-material-cards",
            "global-alternative-cost-cards", "activation-view-equivalence-cards",
        )
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Exile spell copy grant", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def scene(self, seed, first, second):
        game = self.session(seed)
        source = self.add(game.engine, "Nalfeshnee")
        impulse = self.add(game.engine, "Reckless Impulse", zone="hand")
        exiled = []
        for index, name in enumerate((first, second)):
            card = self.add(game.engine, name, zone="library", ref=f"copy-exile-{index}")
            card.known_to = []
            card.revealed_to = []
            exiled.append(card)
        hidden = self.add(game.engine, "Forest", seat="D", zone="hand", ref="copy-private-d")
        for card in (source, impulse, *exiled):
            self.assert_closed(game, card)
        return game, source, impulse, exiled, hidden

    def exile_by_cast(self, game, source, impulse, cards):
        self.prepare(game, mana={"C": 7, "R": 3, "G": 2})
        self.checkpoint(game)
        self.cast(game, "A", impulse)
        self.assertEqual(0, self.trigger_count(game, source))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["exile"] * len(cards), [game.state.cards[card.object_id].zone for card in cards])

    def test_native_exile_instant_retains_new_targets_no_permanent_grant_and_replays(self):
        game, source, impulse, cards, hidden = self.scene(70710041, "Shock", "Grizzly Bears")
        self.exile_by_cast(game, source, impulse, cards)
        shock = cards[0]
        self.cast(game, "A", shock, ["B"])
        self.assertEqual(1, self.trigger_count(game, source))
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        before = authoritative_state_hash(game.state)
        bad = game.act("pilot:D", {"action_id": "choose", "targets": ["C"]})
        self.assertFalse(bad.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        choice = game.act("pilot:A", {"action_id": "choose", "targets": ["C"]})
        self.assertTrue(choice.ok, choice.summary)
        copied, = [item for item in game.state.stack if item.kind == "spell_copy"]
        self.assertEqual(["C"], copied.targets)
        self.assertFalse(any(obj.object_id == copied.card_object_id for effect in game.state.continuous_effects
                             for obj in getattr(effect, "locked_objects", ())))
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40, 38, 38, 40], [game.state.players[seat].life for seat in "ABCD"])
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(hidden.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_native_permanent_copy_carries_exact_grant_and_recurs_after_first_sacrifice_is_countered(self):
        game, source, impulse, cards, _ = self.scene(70710042, "Grizzly Bears", "Shock")
        stifle = self.add(game.engine, "Stifle", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.assert_closed(game, stifle)
        self.exile_by_cast(game, source, impulse, cards)
        original = cards[0]
        self.cast(game, "A", original)
        self.assertEqual(1, self.trigger_count(game, source))
        self.resolve_top(game)
        copied, = [item for item in game.state.stack if item.kind == "spell_copy"]
        spell = game.state.cards[copied.card_object_id]
        effect, = [effect for effect in game.state.continuous_effects if any(
            obj.logical_object_id == spell.logical_object_id for obj in getattr(effect, "locked_objects", ())) ]
        self.assertIn("haste", {value.casefold() for value in game.engine._effective_card_data(spell)["keywords"]})
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        token = game.state.cards[spell.object_id]
        self.assertEqual(("battlefield", True), (token.zone, token.is_token))
        carried, = [entry for entry in game.state.continuous_effects if entry.effect_id == effect.effect_id]
        self.assertEqual(effect.timestamp, carried.timestamp)
        self.assertEqual(effect, carried)
        self.assertEqual(("battlefield", "stack"), carried.applies.zones)
        self.assertEqual(token.logical_object_id, carried.locked_objects[0].logical_object_id)
        self.assertIn("haste", game.engine._combat_keywords(token))
        copiable = game.engine._copyable_characteristics(token)
        self.assertNotIn("haste", {value.casefold() for value in copiable["keywords"]})
        self.assertNotIn("haste", game.engine._combat_keywords(game.state.cards[original.object_id]))

        def cleanup(session):
            return [item for item in session.state.stack if item.source_object_id == token.object_id]
        self.until(game, lambda session: bool(cleanup(session)))
        trigger, = cleanup(game)
        self.cast(game, "B", stifle, [trigger.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[token.object_id].zone)
        self.replay(game, load=True)
        self.until(game, lambda session: bool(cleanup(session)))
        self.assertIsNone(self.resolve(game))
        self.assertFalse(token.object_id in game.state.cards and game.state.cards[token.object_id].zone == "battlefield")
        self.assertEqual("battlefield", game.state.cards[original.object_id].zone)
        self.replay(game, load=True)

    def test_exact_origin_and_recurring_quoted_instruction_are_required(self):
        original = self.db.lookup("Nalfeshnee", fuzzy=False)
        compiled = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        parent, = (node for face in compiled.faces for node in face.nodes if node.event == "spell.cast")
        child, = (node for face in compiled.faces for node in face.nodes if node.event == "step.begin")
        self.assertIn(EXILE_CAST_COPY_CAPABILITY, parent.capability_dependencies)
        self.assertEqual("granted_triggered_ability", child.kind)
        spec = PermanentSpellCopySpec.from_dict(parent.effects[0][PERMANENT_SPELL_COPY_FIELD])
        self.assertEqual("step.begin", spec.grant.event)
        self.assertNotIn("retain_targets_only", parent.effects[0])
        for old, new in (("from exile", "from your graveyard"), ("permanent spell", "creature spell"),
                         ("the end step", "the next end step"), ("haste and", "flying and")):
            changed = replace(original, oracle_text=original.oracle_text.replace(old, new, 1))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                                                            capability_profile="commander_review").status)

    def test_missing_origin_bound_grant_is_killed(self):
        with patch("quorune.compiler.exile_spell_copy_grant_nodes.exile_spell_copy_grant_nodes", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_exact_origin_and_recurring_quoted_instruction_are_required()
