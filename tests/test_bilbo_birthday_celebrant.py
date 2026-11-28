"""Native Bilbo life replacement and unrestricted battlefield search."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from scripts.build_test_database import build_fixture_database
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_search_model import (
    ANY_NUMBER_CREATURE_BATTLEFIELD_CAPABILITY,
    ANY_NUMBER_CREATURE_BATTLEFIELD_VERSION,
    ANY_NUMBER_CREATURE_SUBTYPE_BATTLEFIELD_VERSION,
    AnyNumberCreatureSubtypeSearch,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry


BILBO = "Bilbo, Birthday Celebrant"


class BilboBirthdayCelebrantTests(InheritedNativeFixture, TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "any-number-creatures.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/any-number-creature-library-search-cards.json",
            ROOT / "tests/fixtures/source-counter-quantity-cards.json",
            ROOT / "tests/fixtures/activation-restriction-material-cards.json",
            ROOT / "tests/fixtures/literal-name-library-battlefield-search-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Bilbo search witness",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
            ["Isamaru, Hound of Konda"],
        )

    def ir(self, record):
        return compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")

    def test_bilbo_closed_activation_and_old_subtype_wire_stay_distinct(self):
        original = self.db.lookup(BILBO, fuzzy=False)
        ir = self.ir(original)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertFalse(ir.material_residuals)
        activated, = (node for node in ir.faces[0].nodes if node.event == "activate")
        self.assertIn(ANY_NUMBER_CREATURE_BATTLEFIELD_CAPABILITY, activated.capability_dependencies)
        self.assertIn("activation.condition.fixed_public_state", activated.capability_dependencies)
        self.assertIsNone(activated.target_schema)
        self.assertEqual(ANY_NUMBER_CREATURE_BATTLEFIELD_VERSION, activated.effects[0]["schema_version"])
        unrestricted = AnyNumberCreatureSubtypeSearch().effect()
        subtype = AnyNumberCreatureSubtypeSearch("elf").effect()
        self.assertIsNone(AnyNumberCreatureSubtypeSearch.from_effect(unrestricted).subtype)
        self.assertEqual("elf", AnyNumberCreatureSubtypeSearch.from_effect(subtype).subtype)
        for crossed in ({**subtype, "schema_version": ANY_NUMBER_CREATURE_BATTLEFIELD_VERSION},
                        {**unrestricted, "schema_version": ANY_NUMBER_CREATURE_SUBTYPE_BATTLEFIELD_VERSION}):
            with self.assertRaises(ValueError):
                AnyNumberCreatureSubtypeSearch.from_effect(crossed)
        changed = replace(original, oracle_text=original.oracle_text.replace("any number of creature cards", "any number of cards"))
        self.assertNotEqual("exact", self.ir(changed).status)

    def add_bilbo(self, game):
        source = self.add(game.engine, BILBO)
        source.acquired_control_turn_count = -1
        self.assert_closed(game, source)
        return source

    def test_bilbo_existing_life_addition_applies_once_to_one_ordinary_gain(self):
        game = self.session(13231101)
        self.add_bilbo(game)
        blessing = self.add(game.engine, "Chaplain's Blessing", zone="hand")
        self.prepare(game, mana={"W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", blessing)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(46, game.state.players["A"].life)
        self.assertEqual(40, game.state.players["B"].life)
        self.replay(game, load=True)

    @staticmethod
    def shuffles(game):
        return game.state.players["A"].stats.get("shuffle_count", 0)

    def search_scene(self, seed, *, life=111, count=2, response=False, replacements=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        game.state.players["A"].life = life
        source = self.add_bilbo(game)
        bears = [self.add(game.engine, "Grizzly Bears", zone="library", ref=f"birthday-creature-{index:02}")
                 for index in range(count)]
        decoy = self.add(game.engine, "Lightning Bolt", zone="library", ref="birthday-unselected-instant")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="birthday-response") if response else None
        if replacements:
            for name in ("Grumgully, the Generous", "Hardened Scales", "Doubling Season"):
                self.add(game.engine, name)
        self.prepare(game, mana={"C": 2, "W": 1, "B": 1, "G": 1})
        if response:
            game.state.players["B"].mana_pool.update({"R": 1})
        self.checkpoint(game)
        return game, source, bears, decoy, bolt

    def activation(self, game, source):
        program, = game.engine.semantics.programs_for_oracle(source.oracle_id, event="activate")
        return f"activate:{source.ref}:{program.ability_id.removeprefix('ability:')}"

    def listed(self, game, action):
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return action in {row["id"] for row in rows}

    def activate(self, game, source):
        action = self.activation(game, source)
        self.assertTrue(self.listed(game, action))
        current = game.act("pilot:A", {"action_id": action, "pay": "auto", "targets": []})
        self.assertTrue(current.ok, current.summary)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)

    def begin_search(self, game):
        pending = self.resolve(game)
        self.assertEqual(("semantic.search", ["A"]), (pending.kind, list(pending.actors)))
        return pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]

    def choose(self, game, seat="A", *, accept=True, **payload):
        original = authoritative_state_hash(game.state)
        current = game.act(f"pilot:{seat}", {"action_id": "choose", **payload})
        self.assertEqual(accept, current.ok, current.summary)
        if not accept:
            self.assertEqual(original, authoritative_state_hash(game.state))

    def test_bilbo_life_gate_is_checked_at_activation_and_batch_replays_after_damage(self):
        low, source, _, _, _ = self.search_scene(13231102, life=110)
        low_action = self.activation(low, source)
        self.assertFalse(self.listed(low, low_action))
        before = authoritative_state_hash(low.state)
        self.assertFalse(low.act("pilot:A", {"action_id": low_action, "pay": "auto", "targets": []}).ok)
        self.assertEqual(before, authoritative_state_hash(low.state))

        game, source, bears, decoy, bolt = self.search_scene(13231103, count=12, response=True)
        shuffles = self.shuffles(game)
        self.activate(game, source)
        self.cast_for(game, "B", bolt, ["A"])
        self.resolve_top(game)
        self.assertEqual(108, game.state.players["A"].life)
        schema = self.begin_search(game)
        self.assertEqual((0, 12), (schema["minimum"], schema["maximum"]))
        self.assertEqual({card.ref for card in bears}, set(schema["legal_refs"]))
        for observer in "BCD":
            packet = json.dumps(game.packet(f"pilot:{observer}", full=True))
            self.assertNotIn(decoy.ref, packet)
            self.assertTrue(all(card.ref not in packet for card in bears))
        self.choose(game, "B", accept=False, search_cards=[bears[0].ref])
        self.choose(game, accept=False, search_cards=[decoy.ref])
        game = self.replay(game, load=True)
        self.choose(game, search_cards=[card.ref for card in bears])
        moved = [game.state.cards[card.object_id] for card in bears]
        self.assertTrue(all(card.zone == "battlefield" and card.controller == "A" for card in moved))
        self.assertEqual(1, len({card.zone_timestamp for card in moved}))
        self.assertEqual(shuffles + 1, self.shuffles(game))
        self.assertEqual("library", game.state.cards[decoy.object_id].zone)
        self.replay(game, load=True)

    def test_bilbo_search_can_find_none_and_shuffles_once(self):
        game, source, bears, _, _ = self.search_scene(13231104)
        shuffles = self.shuffles(game)
        self.activate(game, source)
        self.assertEqual(2, self.begin_search(game)["maximum"])
        self.choose(game, search_cards=[])
        self.assertEqual(shuffles + 1, self.shuffles(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in bears))
        self.replay(game, load=True)

    def test_bilbo_simultaneous_creature_search_resumes_entry_replacements(self):
        game, source, bears, decoy, _ = self.search_scene(13231105, replacements=True)
        shuffles = self.shuffles(game)
        self.activate(game, source)
        self.begin_search(game)
        self.choose(game, search_cards=[card.ref for card in bears])
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in bears))
        self.assertEqual(shuffles, self.shuffles(game))
        game = self.replay(game, load=True)
        for _ in range(4):
            if game.state.pending_decision is None or game.state.pending_decision.kind != "replacement.order":
                break
            options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
            scales = next(row for row in options if "hardened" in (row["id"] + row["label"]).casefold())
            self.choose(game, replacement=scales["id"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(shuffles + 1, self.shuffles(game))
        self.assertEqual([4, 4], [game.state.cards[card.object_id].counters.get("+1/+1") for card in bears])
        self.assertNotIn(decoy.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.replay(game, load=True)
