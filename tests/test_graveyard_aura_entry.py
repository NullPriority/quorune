"""Native witnesses for event-bound, untargeted graveyard Aura entry."""

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
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.graveyard_aura_entry import GRAVEYARD_AURA_ENTRY_CAPABILITY
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


AURAS = ("Dragon Fangs", "Dragon Scales", "Dragon Breath", "Dragon Wings", "Dragon Shadow")


class GraveyardAuraEntryTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Entrant attachment witnesses", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "graveyard-aura-entry.sqlite3"
        build_fixture_database(ROOT / "tests/fixtures/graveyard-aura-entry-cards.json", path)
        cls.use_database(path)

    def ready(self, game, mana, cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def test_all_five_auras_attach_to_the_original_shrouded_entrant(self):
        game = self.session(3034001)
        entrant = self.add(game.engine, "Simic Sky Swallower", zone="hand")
        auras = [self.add(game.engine, name, seat="B", zone="graveyard") for name in AURAS]
        private = self.add(game.engine, "Grizzly Bears", seat="D", zone="hand", ref="private-aura-witness")
        self.ready(game, {"C": 5, "G": 1, "U": 1}, [entrant, *auras])
        self.cast_for(game, "A", entrant)
        self.resolve_top(game)
        self.assertEqual("trigger.order", game.state.pending_decision.kind)
        triggers = [item.ref for batch in game.state.pending_trigger_batches for item in batch.items]
        self.assertEqual(5, len(triggers))
        ordered = game.act("pilot:B", {"action_id": "order", "triggers": triggers})
        self.assertTrue(ordered.ok, ordered.summary)
        for item in game.state.stack:
            self.assertFalse(item.targets)
            self.assertIsNone(game.engine.semantics.get(item.semantic_key).target_schema)
        self.assertTrue(self.resolve(game))
        self.assertEqual(["pilot:B"], game.pending_principals())
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:B", full=True)))
        command = {"action_id": "choose", "choice": "apply"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        for index in range(len(auras)):
            accepted = game.act("pilot:B", command)
            self.assertTrue(accepted.ok, accepted.summary)
            if index + 1 < len(auras):
                self.assertTrue(self.resolve(game))
        self.assertIsNone(self.resolve(game))
        for source in auras:
            card = game.state.cards[source.object_id]
            self.assertEqual(("battlefield", "B", entrant.object_id), (card.zone, card.controller, card.attached_to))
        self.replay(game, load=True)

    def test_old_trigger_cannot_attach_to_a_new_incarnation_of_the_creature(self):
        game = self.session(3034002)
        entrant = self.add(game.engine, "Ancient Brontodon", zone="hand")
        flash = self.add(game.engine, "Cloudshift", zone="hand")
        aura = self.add(game.engine, "Dragon Fangs", seat="B", zone="graveyard")
        alternative = self.add(game.engine, "Grizzly Bears", seat="D")
        self.ready(game, {"C": 6, "G": 2, "W": 1}, [entrant, flash, aura])
        cast = self.action(game, "A", entrant)
        accepted = game.act("pilot:A", {"action_id": cast["id"], "targets": [], "pay": "auto", "reserve": {"W": 1}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_top(game)
        existing, = game.state.stack
        first_incarnation = game.state.cards[entrant.object_id].logical_object_id
        self.assertEqual(1, game.state.players["A"].mana_pool["W"])
        self.cast_for(game, "A", flash, [entrant.ref])
        self.resolve_top(game)
        self.assertNotEqual(first_incarnation, game.state.cards[entrant.object_id].logical_object_id)
        self.assertEqual(2, self.trigger_count(game, aura))
        self.assertTrue(self.resolve(game))
        self.assertEqual(["pilot:B"], game.pending_principals())
        declined = game.act("pilot:B", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(declined.ok, declined.summary)
        self.assertEqual([existing.ref], [item.ref for item in game.state.stack])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[aura.object_id].zone)
        self.assertIsNone(game.state.cards[aura.object_id].attached_to)
        self.assertEqual("battlefield", game.state.cards[alternative.object_id].zone)
        self.replay(game, load=True)

    def test_below_threshold_creature_never_places_the_return_trigger(self):
        game = self.session(3034003)
        entrant = self.add(game.engine, "Grizzly Bears", zone="hand")
        aura = self.add(game.engine, "Dragon Scales", seat="D", zone="graveyard")
        self.ready(game, {"C": 1, "G": 1}, [entrant, aura])
        self.cast_for(game, "A", entrant)
        self.resolve_top(game)
        self.assertEqual(0, self.trigger_count(game, aura))
        self.assertEqual("graveyard", game.state.cards[aura.object_id].zone)
        self.replay(game, load=True)

    def test_withdrawal_and_changed_source_scope_leave_the_aura_residual(self):
        card = self.db.lookup(AURAS[0], fuzzy=False)
        value = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in value["capabilities"] if row["id"] == GRAVEYARD_AURA_ENTRY_CAPABILITY).update(
            status="blocked", blockers=["Withhold the graveyard attachment owner"],
        )
        registry = CapabilityRegistry(value)
        entries = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=value, declaration_source=entries,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=registry,
                                                        capability_profile="commander_review").status)
        with patch("quorune.compiler.graveyard_aura_entry_nodes.graveyard_aura_entry_node", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry,
                                                            capability_profile="commander_review").status)
        changed = replace(card, oracle_text=card.oracle_text.replace("that creature", "a creature you control"))
        self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                                                        capability_profile="commander_review").status)
