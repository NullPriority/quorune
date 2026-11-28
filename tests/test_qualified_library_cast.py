"""Native qualification, source cost and direct library casting for a paid activation."""

import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.carddb import CardDatabase
from quorune.compiler.activated_costs import parse_compiled_activated_abilities
from quorune.compiler.library_search_templates import qualified_instant_library_cast_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.record import authoritative_state_hash, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.qualified_library_cast import QualifiedLibraryCastSpec


class QualifiedLibraryCastTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = CardDatabase(os.environ.get("MTG_CARD_DB", ROOT / "data" / "scryfall-current.sqlite3"))
        cls.db.connection.execute("PRAGMA query_only = ON")
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Qualified private library cast",
                                  [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30)],
                                  ["Isamaru, Hound of Konda"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_exact_sunforger_cost_is_fully_admitted_by_deck_preflight(self):
        from quorune.preflight import semantic_preflight

        deck = DeckDefinition("Exact Sunforger cost", [DeckEntry("Sunforger", 1)], [])
        result, = semantic_preflight(self.db, deck, capability_profile="commander_review",
                                     collect_source_diagnostics=True)["cards"]
        self.assertEqual("fully_playable", result["status"], result["unresolved"])
        self.assertTrue(result["card_program_strict_capability_ready"], result["unresolved"])
        self.assertEqual(0, result["oracle_ir"]["material_residual_count"])

    def test_exact_payment_and_search_scope_reject_forged_variants(self):
        record = self.db.lookup("Sunforger", fuzzy=False)
        original = record.oracle_text.splitlines()[1]
        cost, body = original.split(": ", 1)
        template = qualified_instant_library_cast_template(body)
        self.assertIsNotNone(template)
        for wrong in (body.replace("instant", "sorcery"), body.replace("your library", "target player's library"),
                      body.replace("cast that card", "you may cast that card"), body.replace("Then shuffle.", "")):
            self.assertIsNone(qualified_instant_library_cast_template(wrong))

        def required_cost(text):
            ability, = parse_compiled_activated_abilities(
                card_name=record.name, oracle_text=text + ": Draw a card.", keywords=(),
            )
            self.assertTrue(ability.compiled_cost)
            self.assertEqual({"kind": "unattach_source"}, ability.granting_attachment_payment.to_dict())

        required_cost(cost)
        with patch("quorune.compiler.activated_costs._compile_source_equipment_unattach_cost", side_effect=lambda ability: ability):
            with self.assertRaises(AssertionError):
                required_cost(cost)
        with self.assertRaises(AssertionError):
            required_cost(cost.replace("this Equipment", "an Equipment you control"))
        spec = QualifiedLibraryCastSpec.from_effect(template[1][0])

        def witness():
            self.assertTrue(spec.matches(("instant",), ("W", "U"), 4))
            self.assertFalse(spec.matches(("instant",), ("U",), 4))
            self.assertFalse(spec.matches(("sorcery",), ("W",), 4))
            self.assertFalse(spec.matches(("instant",), ("R",), 5))

        witness()
        with patch.object(QualifiedLibraryCastSpec, "matches", return_value=True):
            with self.assertRaises(AssertionError):
                witness()

    def reject(self, game, principal, command):
        before = authoritative_state_hash(game.state)
        result = game.act(principal, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def accept(self, game, command):
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def save_replay(self, game, label):
        root = os.environ.get("QUORUNE_SUNFORGER_RECORDS")
        if root:
            path = Path(root) / label
            game.save(path, include_review=False)
            replay = replay_record(path, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(authoritative_state_hash(game.state), replay["final_state_hash"])

    def fixture(self, seed, *, attached=True):
        game = self.session(seed)
        source = self.add(game.engine, "Sunforger")
        receiver = self.add(game.engine, "Grizzly Bears", seat="C")
        bolt = self.add(game.engine, "Lightning Bolt", zone="library", ref="sunforger-qualified")
        wrong_color = self.add(game.engine, "Counterspell", zone="library", ref="sunforger-blue")
        wrong_type = self.add(game.engine, "Act of Treason", zone="library", ref="sunforger-sorcery")
        too_large = self.add(game.engine, "Volcanic Offering", zone="library", ref="sunforger-too-large")
        for card in (bolt, wrong_color, wrong_type, too_large):
            card.known_to.clear()
            card.revealed_to.clear()
        if attached:
            attach_objects(game.state.cards, source, receiver, source_timestamp=source.zone_timestamp,
                           players=game.state.players)
        self.assert_closed(game, source)
        self.prepare(game, mana={"R": 1, "W": 1})
        self.checkpoint(game)
        return game, source, receiver, bolt, (wrong_color, wrong_type, too_large)

    def activation(self, game, source):
        choices = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((row for row in choices if row["id"].startswith("activate:" + source.ref + ":")
                     and row.get("cost_summary", {}).get("granting_attachment")), None)

    def activate(self, game, source):
        row = self.activation(game, source)
        self.assertIsNotNone(row, game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"])
        self.accept(game, {"action_id": row["id"], "pay": "auto"})
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.assertIsNone(game.state.cards[source.object_id].attached_to)

    def test_actual_detach_private_search_direct_cast_shuffle_and_rollback(self):
        from quorune.rules.activation import granting_attachment

        game, source, receiver, bolt, hidden = self.fixture(742190201)
        self.assertEqual(6, game.engine._numeric_stat(receiver.object_id, "power"))
        activate = {"action_id": self.activation(game, source)["id"], "pay": "auto"}
        self.reject(game, "pilot:C", activate)
        real_payment = granting_attachment.pay_granting_attachment

        def fail_after_payment(*args, **kwargs):
            real_payment(*args, **kwargs)
            raise GameRuleError("Injected post-unattachment payment fault")

        with patch.object(granting_attachment, "pay_granting_attachment", side_effect=fail_after_payment):
            self.reject(game, "pilot:A", activate)
        self.assertEqual(receiver.object_id, game.state.cards[source.object_id].attached_to)
        self.activate(game, source)
        self.assertEqual(2, game.engine._numeric_stat(receiver.object_id, "power"))
        self.assertEqual("C", receiver.controller)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual({bolt.ref}, {row["id"] for row in ctx["search_cards"]})
        self.assertTrue({card.ref for card in hidden} <= {row["id"] for row in ctx["search_window"]})
        self.assertEqual(
            [game.state.cards[oid].ref for oid in game.state.players["A"].zones["library"]],
            [row["id"] for row in ctx["search_window"]],
        )
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(bolt.ref, json.dumps(game.packet(principal, full=True)))
        self.reject(game, "pilot:C", {"action_id": "choose", "card": bolt.ref})
        self.reject(game, "pilot:A", {"action_id": "choose", "card": hidden[0].ref})
        self.save_replay(game, "private-search")
        game = self.replay(game, load=True)
        self.accept(game, {"action_id": "choose", "card": bolt.ref})
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        option, = ctx["cast_options"]
        self.assertEqual(bolt.ref, option["card"])
        self.reject(game, "pilot:A", {"action_id": "choose", "choice": "decline"})
        self.save_replay(game, "prepared-cast")
        game = self.replay(game, load=True)
        command = {"action_id": "choose", "choice": "cast",
                   "cast": {"card": bolt.ref, "cost_option": option["id"], "targets": ["B"]}}
        shuffle = game.engine.shuffle_library

        def fail_late(seat, **kwargs):
            self.assertEqual("stack", game.state.cards[bolt.object_id].zone)
            shuffle(seat, **kwargs)
            raise GameRuleError("Injected late shuffle fault")

        with patch.object(game.engine, "shuffle_library", side_effect=fail_late):
            self.reject(game, "pilot:A", command)
        self.assertEqual("library", game.state.cards[bolt.object_id].zone)
        self.assertIsNone(game.state.cards[source.object_id].attached_to)
        life = game.state.players["B"].life
        with patch.object(game.engine, "shuffle_library", wraps=shuffle) as shuffled:
            self.accept(game, command)
            self.assertEqual(["A"], [call.args[0] for call in shuffled.call_args_list])
        self.assertEqual("stack", game.state.cards[bolt.object_id].zone)
        self.assertEqual(life, game.state.players["B"].life)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life - 3, game.state.players["B"].life)
        self.save_replay(game, "cast-and-resolved")
        self.replay(game, load=True)

    def test_actual_unattached_source_unavailable_and_qualified_search_can_fail(self):
        no_offer, source, *_ = self.fixture(742190202, attached=False)
        self.assertIsNone(self.activation(no_offer, source))
        game, source, _, bolt, _ = self.fixture(742190203)
        self.activate(game, source)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        with patch.object(game.engine, "shuffle_library", wraps=game.engine.shuffle_library) as shuffled:
            self.accept(game, {"action_id": "choose"})
            self.assertIsNone(self.resolve(game))
            self.assertEqual(["A"], [call.args[0] for call in shuffled.call_args_list])
        self.assertEqual("library", game.state.cards[bolt.object_id].zone)
        self.save_replay(game, "fail-to-find")
        self.replay(game, load=True)
