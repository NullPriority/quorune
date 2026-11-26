from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_mutate_cast_native as mutate_actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.event_card_return import EVENT_HAND_RETURN_CAPABILITY, event_hand_return_effect
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class GrantedDepartureHandReturnTests(InheritedNativeFixture, unittest.TestCase):
    mutate = mutate_actions.MutateCastNativeTests.mutate
    choose_merge = mutate_actions.MutateCastNativeTests.choose_merge

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "departure-hand-return.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "granted-departure-hand-return-cards",
            "activation-public-state-cards", "fixed-public-zone-move-cards", "mutate-casting-cards",
        )
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Departure hand return", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def returned_grant(self, game, card):
        children = [item for item in game.state.stack
                    if item.source_object_id == card.object_id and item.kind == "triggered_ability"]
        item, = children
        return item

    def test_actual_death_or_exile_returns_to_owners_hand_under_prior_controller_and_replays(self):
        for spell_name, zone in (("Murder", "graveyard"), ("Final Reward", "exile")):
            with self.subTest(zone=zone):
                game = self.session(9039 + len(zone))
                spell = self.add(game.engine, "Ashnod's Intervention", zone="hand")
                removal = self.add(game.engine, spell_name, zone="hand")
                holder = self.add(game.engine, "Grizzly Bears", seat="B")
                game.engine.change_control(holder.object_id, "C", reason="Borrowed recipient")
                previous = holder.logical_object_id
                for card in (spell, removal, holder):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"C": 4, "B": 3})
                self.checkpoint(game)
                self.cast(game, "A", spell, [holder.ref])
                self.resolve_top(game)
                self.assertEqual(4, game.engine._numeric_stat(holder.object_id, "power"))
                self.cast(game, "A", removal, [holder.ref])
                self.resolve_top(game)
                arrived = game.state.cards[holder.object_id]
                self.assertEqual(zone, arrived.zone)
                self.assertNotEqual(previous, arrived.logical_object_id)
                item = self.returned_grant(game, holder)
                self.assertEqual("C", item.controller)
                self.assertEqual("B", item.context["owner"])
                self.assertEqual(arrived.zone_change_counter, item.context["card_zone_change_counter"])
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertIn(holder.object_id, game.state.players["B"].zones["hand"])
                self.assertNotIn(holder.object_id, game.state.players["C"].zones["hand"])
                self.assertEqual("hand", game.state.cards[holder.object_id].zone)
                self.assertNotIn(holder.object_id, json.dumps(game.packet("pilot:D", full=True)["state"]))
                self.replay(game, load=True)

    def test_stale_pending_return_and_nonqualifying_bounce_replay(self):
        for bounce in (False, True):
            with self.subTest(bounce=bounce):
                game = self.session(6093 + bounce)
                spell = self.add(game.engine, "Ashnod's Intervention", zone="hand")
                removal = self.add(game.engine, "Unsummon" if bounce else "Murder", zone="hand")
                remote = self.add(game.engine, "Public Grave Exile", seat="B", zone="hand")
                holder = self.add(game.engine, "Grizzly Bears")
                game.state.players["B"].mana_pool["B"] = 1
                self.prepare(game, mana={"C": 1, "B": 3, "U": 1})
                self.checkpoint(game)
                self.cast(game, "A", spell, [holder.ref])
                self.resolve_top(game)
                self.cast(game, "A", removal, [holder.ref])
                self.resolve_top(game)
                if bounce:
                    self.assertEqual("hand", game.state.cards[holder.object_id].zone)
                    self.assertFalse(game.state.stack)
                else:
                    arrived = game.state.cards[holder.object_id]
                    self.returned_grant(game, holder)
                    self.cast(game, "B", remote, [arrived.ref])
                    self.resolve_top(game)
                    game = self.replay(game, load=True)
                    self.resolve_top(game)
                    self.assertEqual("exile", game.state.cards[holder.object_id].zone)
                self.replay(game, load=True)

    def test_composed_departure_returns_only_remaining_physical_arrivals_and_replays(self):
        game = self.session(7303)
        spell = self.add(game.engine, "Ashnod's Intervention", zone="hand")
        root = self.add(game.engine, "Grizzly Bears")
        part = self.add(game.engine, "Dreamtail Heron", zone="hand")
        removal = self.add(game.engine, "Murder", seat="B", zone="hand")
        exile = self.add(game.engine, "Public Grave Exile", seat="C", zone="hand")
        self.prepare(game, mana={"C": 3, "U": 1, "B": 1})
        game.state.players["B"].mana_pool.update({"C": 1, "B": 2})
        game.state.players["C"].mana_pool["B"] = 1
        self.checkpoint(game)
        self.mutate(game, part, root, payment={"C": 3, "U": 1})
        game = self.choose_merge(game, "bottom")
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", spell, [root.ref])
        self.resolve_top(game)
        self.cast(game, "B", removal, [root.ref])
        mutate_actions.MutateCastNativeTests._complete_public_departure_order(self, game, "A", (root, part))
        self.returned_grant(game, root)
        arrived = game.state.cards[root.object_id]
        self.cast(game, "C", exile, [arrived.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[root.object_id].zone)
        self.assertEqual("hand", game.state.cards[part.object_id].zone)
        self.assertIn(part.object_id, game.state.players["A"].zones["hand"])
        self.replay(game, load=True)

    def test_original_requires_bounded_granted_occurrence_and_rejects_altered_destinations(self):
        card = self.db.lookup("Ashnod's Intervention", fuzzy=False)
        result = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", result.status, result.material_residuals)
        node, = (node for face in result.faces for node in face.nodes if tuple(node.effects) == (event_hand_return_effect(),))
        self.assertEqual("granted_triggered_ability", node.kind)
        self.assertIn(EVENT_HAND_RETURN_CAPABILITY, node.capability_dependencies)
        for before, after in (("from the battlefield", "from a graveyard"), ("its owner's hand", "an opponent's hand")):
            changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                                                            capability_profile="commander_review").status)

    def test_omitting_the_departure_binding_is_killed(self):
        with patch("quorune.compiler.event_card_return_templates.source_departure_hand_return_binding", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_original_requires_bounded_granted_occurrence_and_rejects_altered_destinations()
