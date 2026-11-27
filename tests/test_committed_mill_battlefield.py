"""The original Fetch Quest selects from the public results of its own Mill."""

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import (
    CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry,
)
from quorune.rules.committed_mill_cards import PUBLIC_MILLED_CARDS_CAPABILITY
from quorune.rules.milled_permanent_battlefield import MILL_PERMANENT_ENTRY_CAPABILITY
from scripts.build_test_database import build_fixture_database


CARD = "Bramble Familiar // Fetch Quest"


class CommittedMillBattlefieldTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixture_dir = ROOT / "tests/fixtures"
        support = {
            "ability-label-composition-cards.json": {"Forest"},
            "ability-source-counter-cards.json": {"Counterspell", "Grizzly Bears", "Sol Ring"},
            "activation-restriction-material-cards.json": {"Lightning Bolt"},
            "activation-result-views-cards.json": {"Llanowar Elves"},
            "any-number-target-cards.json": {"Pacifism"},
            "bound-effect-program-cards.json": {"Generic Bound Commander", "Generic Bound Plains"},
        }
        fixtures = [fixture_dir / "mill-committed-battlefield-cards.json"]
        for filename, names in support.items():
            document = json.loads((fixture_dir / filename).read_bytes())
            cards = [card for card in document["cards"] if card["name"] in names]
            assert {card["name"] for card in cards} == names, filename
            ids = {card["oracle_id"] for card in cards}
            document["cards"] = cards
            document["rulings"] = [row for row in document.get("rulings", ()) if row["oracle_id"] in ids]
            selected = folder / filename
            selected.write_text(json.dumps(document), encoding="utf-8", newline="\n")
            fixtures.append(selected)
        database = folder / "committed-mill-battlefield.sqlite3"
        build_fixture_database(fixtures, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Choose one committed Mill card", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def arrange_mill(self, game, names):
        cards = tuple(self.add(game.engine, name, zone="library", ref=f"mill-result-{i}")
                      for i, name in enumerate(names))
        library = game.state.players["A"].zones["library"]
        placed = {card.object_id for card in cards}
        library[:] = [object_id for object_id in library if object_id not in placed]
        library.extend(card.object_id for card in reversed(cards))
        return cards

    def cast_fetch_quest(self, game, source):
        self.priority(game, "A")
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = f"cast:{source.ref}:face:Fetch Quest"
        offer, = (row for row in offers if row["id"] == action)
        result = game.act("pilot:A", {"action_id": offer["id"], "face": "Fetch Quest", "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))

    def test_compiler_shape_and_dependency_fail_closed(self):
        card = self.db.lookup(CARD)
        profile = "commander_review"

        def compile_card(registry):
            return compile_oracle_card(card, capability_registry=registry, capability_profile=profile)

        normal = compile_card(self.registry)
        self.assertEqual("exact", normal.status, normal.material_residuals)
        self.assertIn(MILL_PERMANENT_ENTRY_CAPABILITY, normal.faces[-1].nodes[0].capability_dependencies)
        with patch("quorune.compiler.mill_templates.committed_mill_card_template", return_value=None):
            self.assertNotEqual("exact", compile_card(self.registry).status)
        for dependency in (PUBLIC_MILLED_CARDS_CAPABILITY, MILL_PERMANENT_ENTRY_CAPABILITY):
            raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
            row = next(item for item in raw["capabilities"] if item["id"] == dependency)
            row.update(status="blocked", blockers=["Withdraw the exact committed Mill dependency"])
            untrusted = compile_card(CapabilityRegistry(raw))
            self.assertNotEqual("exact", untrusted.status)
            self.assertTrue(any(dependency in item for residual in untrusted.material_residuals
                                for item in residual.blockers))

    def test_original_creature_retains_mana_and_paid_discard_return(self):
        game = self.session(7011303)
        game.state.config.semantic_policy = "trusted_only"
        producer = self.add(game.engine, CARD, ref="bramble-mana")
        source = self.add(game.engine, CARD, ref="bramble-return")
        cost = self.add(game.engine, "Lightning Bolt", zone="hand")
        wrong = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 1})
        self.checkpoint(game)

        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        mana, = (row for row in actions if row["id"].startswith(f"activate:{producer.ref}:")
                 and row.get("mana_ability"))
        paid = game.act("pilot:A", {"action_id": mana["id"]})
        self.assertTrue(paid.ok, paid.summary)
        self.assertTrue(game.state.cards[producer.object_id].tapped)
        self.assertEqual(1, game.state.players["A"].mana_pool.get("G"))

        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        bounce, = (row for row in actions if row["id"].startswith(f"activate:{source.ref}:")
                   and not row.get("mana_ability"))
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:A", {"action_id": bounce["id"], "cost_cards": [wrong.ref], "pay": "auto"})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        paid = game.act("pilot:A", {"action_id": bounce["id"], "cost_cards": [cost.ref], "pay": "auto"})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual("graveyard", game.state.cards[cost.object_id].zone)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_original_selects_one_public_card_then_aura_attachment_and_replays(self):
        game = self.session(7011301)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, CARD, zone="hand")
        old_land = self.add(game.engine, "Forest", zone="graveyard", ref="not-in-mill")
        first = self.add(game.engine, "Grizzly Bears", ref="attach-own")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="attach-other")
        names = ["Llanowar Elves", "Lightning Bolt", "Pacifism", "Counterspell",
                 "Forest", "Sol Ring", "Lightning Bolt", "Forest"]
        cards = self.arrange_mill(game, names)
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 5, "G": 2})
        self.checkpoint(game)
        self.cast_fetch_quest(game, source)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in cards[:7]))
        self.assertEqual("library", game.state.cards[cards[-1].object_id].zone)
        public = json.dumps(game.packet("pilot:A", full=True)["decision"]["ctx"])
        for card in (cards[0], cards[2], cards[4]):
            self.assertIn(card.ref, public)
        for card in (old_land, cards[1], cards[3], cards[5], cards[6], cards[7]):
            self.assertNotIn(card.ref, public)
        waiting = game.packet("pilot:B", full=True).get("decision") or {}
        self.assertFalse(waiting.get("ctx", {}).get("legal", {}).get("actions", ()))
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        for actor, command in (
            ("B", {"action_id": "choose", "card": cards[0].ref}),
            ("A", {"action_id": "choose", "card": old_land.ref}),
            ("A", {"action_id": "choose"}),
        ):
            self.assertFalse(game.act("pilot:" + actor, command).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        response = game.act("pilot:A", {"action_id": "choose", "card": cards[2].ref})
        self.assertTrue(response.ok, response.summary)
        pending = game.state.pending_decision
        self.assertEqual(("aura.entry", ["A"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        chosen = game.act("pilot:A", {"action_id": "choose", "aura_target": other.ref})
        self.assertTrue(chosen.ok, chosen.summary)
        aura = game.state.cards[cards[2].object_id]
        self.assertEqual(("battlefield", "A", other.object_id), (aura.zone, aura.controller, aura.attached_to))
        self.assertEqual("battlefield", game.state.cards[first.object_id].zone)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard"
                            for i, card in enumerate(cards[:7]) if i != 2))
        self.replay(game, load=True)

    def test_original_without_eligible_cards_continues_without_a_choice(self):
        game = self.session(7011302)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, CARD, zone="hand")
        preexisting = self.add(game.engine, "Llanowar Elves", zone="graveyard", ref="already-in-grave")
        cards = self.arrange_mill(game, ["Sol Ring", "Lightning Bolt", "Counterspell",
                                        "Sol Ring", "Counterspell", "Lightning Bolt", "Sol Ring"])
        self.prepare(game, mana={"C": 5, "G": 2})
        self.checkpoint(game)
        self.cast_fetch_quest(game, source)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in cards))
        self.assertEqual("graveyard", game.state.cards[preexisting.object_id].zone)
        self.assertEqual("exile", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)
