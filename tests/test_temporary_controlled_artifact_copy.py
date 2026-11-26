"""Native mode and temporary-copy evidence for a controlled artifact set."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


SPELL = "Masterful Replication"


class TemporaryControlledArtifactCopyTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "temporary-artifact-copy.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/temporary-controlled-artifact-copy-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Temporary artifact copy", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def cast_action(self, game, card):
        self.priority(game, "A")
        return self.action(game, "A", game.state.cards[card.object_id])

    def cast_mode(self, game, action, mode, targets=(), *, allowed=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", {
            "action_id": action["id"], "modes": [mode], "targets": list(targets), "pay": "auto",
        })
        self.assertEqual(allowed, result.ok, result.summary)
        if not allowed:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def data(self, game, card):
        return game.engine._effective_card_data(game.state.cards[card.object_id])

    def own_tokens(self, game):
        return tuple(card for card in game.state.cards.values()
                     if card.zone == "battlefield" and card.is_token and card.controller == "A")

    def test_actual_token_mode_remains_legal_when_the_copy_mode_has_no_target(self):
        game = self.playing(61127271)
        spell = self.add(game.engine, SPELL, zone="hand")
        foreign = self.add(game.engine, "Sol Ring", seat="B")
        self.assert_closed(game, spell)
        self.prepare(game, actor="A", active="A", mana={"C": 4, "U": 2})
        self.checkpoint(game)
        offered = self.cast_action(game, spell)
        self.assertEqual(["mode_1"], offered["target_schema"]["legal_modes"])
        self.cast_mode(game, offered, "mode_2", [foreign.ref], allowed=False)
        self.cast_mode(game, offered, "mode_1")
        self.assertIsNone(self.resolve(game))
        first, second = self.own_tokens(game)
        for token in (first, second):
            result = self.data(game, token)
            self.assertEqual("Golem Token", result["name"])
            self.assertEqual(("3", "3"), (result["power"], result["toughness"]))
            self.assertEqual([], list(result["colors"]))
            types, subtypes, _ = game.engine._type_parts(result["type_line"])
            self.assertEqual(({"artifact", "creature"}, {"golem"}), (types, subtypes))
        self.replay(game, load=True)

    def test_actual_copy_mode_snapshots_current_artifacts_and_expires(self):
        game = self.playing(61127272)
        token_spell = self.add(game.engine, SPELL, zone="hand", ref="artifact-copy-token-spell")
        copy_spell = self.add(game.engine, SPELL, zone="hand", ref="artifact-copy-copy-spell")
        model = self.add(game.engine, "Ornithopter", zone="hand")
        sword = self.add(game.engine, "Bonesplitter", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears")
        foreign = self.add(game.engine, "Sol Ring", seat="B")
        late = self.add(game.engine, "Arcane Signet", zone="hand")
        for card in (token_spell, copy_spell, model, sword, creature, foreign, late):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"C": 15, "U": 4})
        self.checkpoint(game)

        self.cast_mode(game, self.cast_action(game, token_spell), "mode_1")
        self.assertIsNone(self.resolve(game))
        first, second = self.own_tokens(game)
        for card in (model, sword):
            self.cast_for(game, "A", card)
            self.assertIsNone(self.resolve(game))
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        equip, = (row for row in actions if row["id"].startswith(f"activate:{sword.ref}:"))
        paid = game.act("pilot:A", {"action_id": equip["id"], "targets": [creature.ref], "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(creature.object_id, game.state.cards[sword.object_id].attached_to)

        offered = self.cast_action(game, copy_spell)
        schema = offered["target_schema"]["mode_schemas"]["mode_2"]["groups"][0]
        self.assertIn(model.ref, schema["legal_refs"])
        self.assertIn(first.ref, schema["legal_refs"])
        self.assertNotIn(foreign.ref, schema["legal_refs"])
        self.assertNotIn(creature.ref, schema["legal_refs"])
        self.cast_mode(game, offered, "mode_2", [foreign.ref], allowed=False)
        self.cast_mode(game, offered, "mode_2", [model.ref])
        before = {card.object_id: (card.logical_object_id, card.zone_timestamp, card.is_token)
                  for card in (first, second, game.state.cards[sword.object_id])}
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for card in (first, second, sword):
            current = game.state.cards[card.object_id]
            self.assertEqual(before[card.object_id], (current.logical_object_id, current.zone_timestamp, current.is_token))
            self.assertEqual("Ornithopter", self.data(game, card)["name"])
            self.assertIn("flying", {keyword.casefold() for keyword in self.data(game, card)["keywords"]})
        self.assertEqual("Grizzly Bears", self.data(game, creature)["name"])
        self.assertEqual("Sol Ring", self.data(game, foreign)["name"])
        self.assertIsNone(game.state.cards[sword.object_id].attached_to)

        self.cast_for(game, "A", late)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("Arcane Signet", self.data(game, late)["name"])
        turn = game.state.turn_sequence
        self.until(game, lambda item: item.state.turn_sequence > turn)
        self.assertEqual("Bonesplitter", self.data(game, sword)["name"])
        self.assertIsNone(game.state.cards[sword.object_id].attached_to)
        self.assertEqual("Golem Token", self.data(game, first)["name"])
        self.assertEqual("Golem Token", self.data(game, second)["name"])
        self.replay(game, load=True)

    def test_losing_the_only_actual_model_prevents_every_copy(self):
        game = self.playing(61127273)
        spell = self.add(game.engine, SPELL, zone="hand")
        model = self.add(game.engine, "Ornithopter")
        receiver = self.add(game.engine, "Arcane Signet")
        answer = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        for card in (spell, model, receiver, answer):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"C": 4, "U": 2})
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)

        self.cast_mode(game, self.cast_action(game, spell), "mode_2", [model.ref])
        self.cast_for(game, "B", answer, [model.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[model.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual("Arcane Signet", self.data(game, receiver)["name"])
        self.assertEqual((), self.own_tokens(game))
        self.replay(game, load=True)

    def test_changing_either_material_mode_cannot_admit_the_full_source(self):
        card = self.db.lookup(SPELL, fuzzy=False)
        original = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", original.status)
        for old, new in (("Each other artifact you control", "Each other permanent you control"),
                         ("Create two 3/3 colorless Golem artifact creature tokens.", "Create a copy of a hidden card.")):
            with self.subTest(new=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
