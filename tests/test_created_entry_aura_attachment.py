"""Native rules evidence for a created token and its entering Aura."""

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
from scripts.build_test_database import build_fixture_database


class CreatedEntryAuraAttachmentTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "entry-aura-attachment.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "created-entry-aura-attachment-cards.json",
            fixtures / "controlled-aura-receiver-base-characteristics-cards.json",
            fixtures / "controller-land-set-phase-out-cards.json",
            fixtures / "fixed-basic-land-token-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Created entry Aura attachment", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def birds(self, game):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token and card.printed_name == "Bird Token"]

    def begin(self, seed, *, split_control=False, enchant_land=False, remove_aura=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, "Trial Enchantment Watcher")
        aura = self.add(game.engine, "Wild Growth" if enchant_land else "Pacifism", zone="hand")
        target = self.add(game.engine, "Forest" if enchant_land else "Grizzly Bears", seat="C")
        theft = None
        for card in (source, aura, target):
            self.assert_closed(game, card)
        if split_control:
            theft = self.add(game.engine, "Steal Enchantment", seat="B", zone="hand")
            for card in (theft, self.add(game.engine, "Vedalken Orrery", seat="B"),
                         self.add(game.engine, "Parallel Lives")):
                self.assert_closed(game, card)
            game.state.players["B"].mana_pool.update({"U": 3, "C": 3})
        remover = self.add(game.engine, "Disenchant", seat="B", zone="hand") if remove_aura else None
        if remover is not None:
            self.assert_closed(game, remover)
            game.state.players["B"].mana_pool.update({"W": 1, "C": 1})
        self.prepare(game, mana={"C": 3, "W": 1, "G": 1})
        self.checkpoint(game)
        self.cast(game, "A", aura, [target.ref])
        self.resolve_top(game)
        self.assertEqual(target.object_id, game.state.cards[aura.object_id].attached_to)
        trigger, = game.state.stack
        self.assertEqual(source.object_id, trigger.source_object_id)
        self.assertEqual([], self.birds(game))
        self.assertFalse(trigger.targets)
        if theft is not None:
            self.cast(game, "B", theft, [aura.ref])
            self.resolve_top(game)
            self.assertEqual("B", game.state.cards[aura.object_id].controller)
            self.assertEqual(trigger.ref, game.state.stack[-1].ref)
        if remover is not None:
            self.cast(game, "B", remover, [aura.ref])
            self.resolve_top(game)
            self.assertEqual(trigger.ref, game.state.stack[-1].ref)
        return game, aura, target

    def act(self, game, seat, **choice):
        response = game.act("pilot:" + seat, {"action_id": "choose", **choice})
        self.assertTrue(response.ok, response.summary)

    def reject(self, game, seat, **choice):
        before = authoritative_state_hash(game.state)
        response = game.act("pilot:" + seat, {"action_id": "choose", **choice})
        self.assertFalse(response.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_single_result_is_optional_and_replays(self):
        for attach in (False, True):
            with self.subTest(attach=attach):
                game, aura, target = self.begin(176113901 + int(attach))
                pending = self.resolve(game)
                self.assertEqual(("semantic.choice", ["A"]), (pending.kind, list(pending.actors)))
                bird, = self.birds(game)
                schema = pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
                self.assertEqual([bird.ref], list(schema["legal_refs"]))
                game = self.replay(game, load=True)
                self.reject(game, "A", objects=[target.ref])
                self.reject(game, "B", objects=[bird.ref])
                self.act(game, "A", objects=[bird.ref] if attach else [])
                self.assertIsNone(self.resolve(game))
                expected = bird.object_id if attach else target.object_id
                self.assertEqual(expected, game.state.cards[aura.object_id].attached_to)
                self.assertEqual(1, len(self.birds(game)))
                self.replay(game, load=True)


    def test_two_created_tokens_keep_consent_and_aura_control_distinct(self):
        game, aura, target = self.begin(176113902, split_control=True)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, list(pending.actors)))
        schema = pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
        self.assertEqual(["apply", "decline"], list(schema.get("legal_values", ())))
        cats = self.birds(game)
        self.assertEqual(2, len(cats))
        self.assertTrue(all(cat.controller == "A" for cat in cats))
        self.assertEqual(target.object_id, game.state.cards[aura.object_id].attached_to)
        game = self.replay(game, load=True)
        self.reject(game, "B", choice="apply")
        self.act(game, "A", choice="apply")
        pending = game.state.pending_decision
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, list(pending.actors)))
        schema = pending.payload_by_actor["B"]["legal_actions"][0]["choice_schema"]
        self.assertEqual({cat.ref for cat in cats}, set(schema["legal_refs"]))
        for seat in "ACD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        game = self.replay(game, load=True)
        self.reject(game, "A", objects=[cats[1].ref])
        self.reject(game, "B", objects=[])
        self.reject(game, "B", objects=[target.ref])
        self.act(game, "B", objects=[cats[1].ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(cats[1].object_id, game.state.cards[aura.object_id].attached_to)
        self.assertEqual("B", game.state.cards[aura.object_id].controller)
        self.assertEqual(2, len(self.birds(game)))
        self.replay(game, load=True)

    def test_incompatible_land_aura_preserves_creation_without_a_choice(self):
        game, aura, target = self.begin(176113903, enchant_land=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.birds(game)))
        self.assertEqual(target.object_id, game.state.cards[aura.object_id].attached_to)
        self.replay(game, load=True)


    def test_departed_aura_preserves_creation_without_an_attachment_choice(self):
        game, aura, _ = self.begin(176113904, remove_aura=True)
        self.assertEqual("graveyard", game.state.cards[aura.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.birds(game)))
        self.assertIsNone(game.state.cards[aura.object_id].attached_to)
        self.replay(game, load=True)

    def test_combining_distinct_chooser_roles_fails_the_native_witness(self):
        from quorune.semantic_choices import created_entry_aura_attachment as handler

        with patch.object(handler, "_requires_distinct_chooser", return_value=False), self.assertRaises(AssertionError):
            self.test_two_created_tokens_keep_consent_and_aura_control_distinct()

    def test_entry_attachment_requires_the_exact_untargeted_enchantment_event(self):
        card = self.db.lookup("Trial Enchantment Watcher", fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        original, = (node for face in ir.faces for node in face.nodes if node.event == "permanent.enter")
        self.assertIsNone(original.target_schema)
        for old, new in (
            ("an enchantment you control", "an artifact you control"),
            ("a 1/2 blue Bird creature token with flying", "a Treasure token"),
            ("you may attach", "attach"),
        ):
            with self.subTest(variant=new):
                variant = replace(card, oracle_text=card.oracle_text.replace(old, new), raw={})
                changed = compile_oracle_card(variant, capability_registry=self.registry, capability_profile="commander_review")
                self.assertNotEqual("exact", changed.status)
                self.assertTrue(changed.material_residuals)


if __name__ == "__main__":
    unittest.main()
