from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import GrantedTriggeredAbilitySpec, ability_fragment_from_dict
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class EquippedMyriadTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "equipped-myriad.sqlite3"
        fixtures = [
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/equipped-myriad-cards.json",
        ]
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipped Myriad", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def myriad_grants(self, game, card):
        fragments = game.engine._effective_card_data(game.state.cards[card.object_id])["ability_fragments"]
        return [spec for fragment in fragments
                if isinstance(spec := ability_fragment_from_dict(fragment), GrantedTriggeredAbilitySpec)
                and spec.label.casefold() == "myriad"]

    def assert_rejected(self, game, seat, choice):
        before = authoritative_state_hash(game.state)
        result = game.act(f"pilot:{seat}", choice)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def equip(self, game, blade, bearer):
        self.priority(game, "A")
        view = game.packet("pilot:A", full=True)
        action, = (row for row in view["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith(f"activate:{blade.ref}:"))
        self.assertIn(bearer.ref, action["target_schema"]["legal_refs"])
        result = game.act("pilot:A", {"action_id": action["id"], "targets": [bearer.ref],
                                    "pay": "manual", "payment": {"C": 4}})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)

    def test_actual_choices_use_defending_seat_and_copies_exile_after_grant_departure(self):
        game = self.session(70211622)
        blade = self.add(game.engine, "Blade of Selves", zone="hand")
        bearer = self.add(game.engine, "Grizzly Bears")
        bearer.counters["+1/+1"] = 1
        bearer.acquired_control_turn_count = -1
        destroy = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        private = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="concealed-myriad-response")
        for index in range(2):
            self.add(game.engine, "Generic Bound Plains", seat="B", ref=f"blade-answer-plains-{index}")
        walkers = {}
        for seat in ("B", "C"):
            walkers[seat], = game.engine.create_token(
                seat, name=f"{seat} Myriad walker",
                characteristics={"type_line": "Token Planeswalker — Test", "loyalty": "5"},
            )
        for card in (blade, bearer, destroy):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6})
        self.checkpoint(game)
        self.cast(game, "A", blade)
        self.resolve_top(game)
        blade = game.state.cards[blade.object_id]
        self.equip(game, blade, bearer)
        self.assertEqual(1, len(self.myriad_grants(game, bearer)))
        self.attack(game, [(bearer, walkers["B"])])
        pending, = (item for item in game.state.stack if item.source_object_id == bearer.object_id)
        self.assertEqual("B", pending.context["defending_player"])
        self.cast(game, "B", destroy, [blade.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[blade.object_id].zone)
        self.assertEqual([], self.myriad_grants(game, bearer))

        self.assertEqual("semantic.choice", self.resolve(game).kind)
        choose = {"action_id": "choose", "attacking": {"myriad:C": "create", "myriad:D": "create"}}
        self.assert_rejected(game, "D", choose)
        self.assert_rejected(game, "A", {"action_id": "choose", "attacking":
                             {"myriad:B": "create", "myriad:C": "create", "myriad:D": "create"}})
        result = game.act("pilot:A", choose)
        self.assertTrue(result.ok, result.summary)
        game = self.replay(game, load=True)
        destinations = {"action_id": "choose", "attacking": {"myriad:C:0": walkers["C"], "myriad:D:0": "D"}}
        self.assert_rejected(game, "A", {"action_id": "choose", "attacking":
                             {"myriad:C:0": walkers["B"], "myriad:D:0": "D"}})
        result = game.act("pilot:A", destinations)
        self.assertTrue(result.ok, result.summary)
        copies = [card for card in game.state.cards.values()
                  if card.is_token and card.oracle_id == bearer.oracle_id and card.zone == "battlefield"]
        self.assertEqual(2, len({card.logical_object_id for card in copies}))
        self.assertEqual({walkers["C"], "D"}, {card.attacking for card in copies})
        for card in copies:
            self.assertTrue(card.tapped)
            self.assertEqual("A", card.controller)
            self.assertEqual("Grizzly Bears", game.engine.display_name(card.object_id))
            self.assertEqual(2, game.engine._numeric_stat(card.object_id, "power"))
            self.assertEqual({}, card.counters)
            self.assertEqual([], self.myriad_grants(game, card))
        self.assertEqual(2, len(game.state.delayed_triggers))
        for seat in ("A", "B", "C"):
            self.assertNotIn(private.ref, json.dumps(game.packet(f"pilot:{seat}", full=True)))
        self.until(game, lambda s: s.state.phase == "postcombat_main")
        self.assertEqual("battlefield", game.state.cards[bearer.object_id].zone)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "outside" for card in copies))
        self.assertFalse(any(trigger.active for trigger in game.state.delayed_triggers))
        self.replay(game, load=True)

    def test_exact_equipped_keyword_binds_the_independent_ordinary_myriad_child(self):
        record = self.db.lookup("Blade of Selves", fuzzy=False)
        original = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", original.status, original.material_residuals)
        nodes = [node for face in original.faces for node in face.nodes]
        child, = (node for node in nodes if any(effect.get("op") == "choose_myriad_token_destinations" for effect in node.effects))
        self.assertEqual("granted_triggered_ability", child.kind)
        self.assertIn("combat.entry.lifecycle.fixed_public", child.capability_dependencies)
        self.assertTrue(any("continuous.ability.fixed_keyword_event_grant" in node.capability_dependencies
                            for node in nodes if node is not child))
        for wrong in ("Equipped creature has myriad 2.", "Equipped creature has myriad and gains 1 life."):
            with self.subTest(text=wrong):
                changed = replace(record, oracle_text=wrong + "\n" + record.oracle_text.splitlines()[-1])
                compiled = compile_oracle_card(changed, capability_registry=self.registry, capability_profile="commander_review")
                self.assertNotEqual("exact", compiled.status, compiled.material_residuals)

    def test_mutant_omitting_the_ordinary_myriad_child_is_killed(self):
        with patch("quorune.compiler.myriad_nodes.fixed_myriad_keyword_node", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_exact_equipped_keyword_binds_the_independent_ordinary_myriad_child()
