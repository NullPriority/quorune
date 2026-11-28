"""Explicit look permission governs face-down exile in every view."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.impulse_access import grant_temporary_cast_permission
from quorune.impulse_access_model import ImpulseAccessDuration, TemporaryCastPermissionGrant
from quorune.private_opponent_library_cast_model import private_opponent_library_cast_permission
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


SEATS = ("A", "B", "C", "D")
PRINCIPALS = (*("pilot:" + seat for seat in SEATS), "spectator")


class FaceDownExileVisibilityTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "face-down-exile-visibility.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/linked-library-imprint-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Face-down exile visibility fixture",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)],
            [commander],
        )

    def staged_card(self, seed, *, viewers=()):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        card = self.add(game.engine, "Sol Ring", seat="B", zone="exile", ref="sealed-exile")
        card.face_down = True
        card.known_to = list(viewers)
        card.revealed_to = []
        self.assert_closed(game, card)
        return game, card

    def grant_cast(self, game, card, seat):
        grant_temporary_cast_permission(
            game.engine, card=card, reason="ordinary cast visibility fixture",
            grant=TemporaryCastPermissionGrant(
                player=seat, duration=ImpulseAccessDuration.END_OF_TURN,
                not_before_turn_sequence=None, without_mana_cost=False,
                source="ordinary cast visibility fixture",
            ),
        )

    def assert_visible_only_to(self, game, card, viewer=None, *, seat="B", zone="ex"):
        for principal in PRINCIPALS:
            with self.subTest(principal=principal):
                packet = game.packet(principal, full=True)
                row, = (entry for entry in packet["state"]["players"][seat][zone] if entry["id"] == card.ref)
                if viewer is not None and principal == "pilot:" + viewer:
                    self.assertEqual(card.printed_name, row["n"])
                    self.assertEqual(card.oracle_id[:8], row["cid"])
                else:
                    self.assertEqual("?", row["n"])
                    self.assertNotIn("cid", row)
                    self.assertNotIn(card.printed_name, json.dumps(packet, ensure_ascii=False))
                self.assertEqual(1, row["fd"])

    def actions(self, game, seat):
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def test_legacy_pending_offer_is_private_and_reconnect_keeps_exact_state(self):
        game, card = self.staged_card(406300001)
        self.grant_cast(game, card, "B")
        self.prepare(game, actor="B", active="B", mana={"C": 1})
        action = "cast:" + card.ref
        retained = deepcopy(game.state.pending_decision.payload_by_actor["B"])
        self.assertIn(card.ref, retained["legal"]["cast"])
        offer, = (row for row in retained["legal"]["actions"] if row["id"] == action)
        self.assertIn(card.printed_name, offer["label"])
        self.checkpoint(game)
        state_hash = authoritative_state_hash(game.state)

        reopened = self.replay(game, load=True)
        self.assertEqual(state_hash, authoritative_state_hash(reopened.state))
        self.assertEqual(retained, reopened.state.pending_decision.payload_by_actor["B"])
        card = reopened.state.cards[card.object_id]
        self.assert_visible_only_to(reopened, card)
        projected = reopened.packet("pilot:B", full=True)["decision"]
        self.assertNotIn(card.ref, projected["ctx"]["legal"]["cast"])
        self.assertNotIn(action, [row["id"] for row in projected["legal_actions"]])
        self.assertNotIn(action, [row["id"] for row in projected["ctx"]["legal"]["actions"]])

        for command in (
            {"action_id": action, "pay": "auto"},
            {"a": "c", "crd": card.ref.upper(), "from": "exile", "pay": "auto"},
            {"action": "cast", "id": card.object_id, "from": ["exile"], "pay": "auto"},
            {"action": "cast", "card": card.printed_name, "from": "exile", "pay": "auto"},
            {"a": "l", "crd": card.ref, "from": "exile"},
            {"a": "x", "src": card.ref},
        ):
            with self.subTest(command=command):
                rejected = reopened.act("pilot:B", command)
                self.assertFalse(rejected.ok, rejected.summary)
                message = "Unknown or stale action_id" if "action_id" in command else "Could not find "
                self.assertTrue(rejected.summary.startswith(message), rejected.summary)
                self.assertEqual(state_hash, authoritative_state_hash(reopened.state))
        self.assertEqual(retained, reopened.state.pending_decision.payload_by_actor["B"])

    def test_legacy_accepted_engine_command_still_replays(self):
        game, card = self.staged_card(406300005)
        self.grant_cast(game, card, "B")
        self.prepare(game, actor="B", active="B", mana={"C": 1})
        self.checkpoint(game)
        command = {"a": "c", "crd": card.ref, "from": "exile", "pay": "auto"}
        with patch.object(type(game), "_reject_concealed_exile_source", return_value=None):
            accepted = game.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("stack", game.state.cards[card.object_id].zone)
        reopened = self.replay(game, load=True)
        self.assertEqual(authoritative_state_hash(game.state), authoritative_state_hash(reopened.state))

    def test_retained_diagnostics_and_choice_card_stay_private(self):
        game, card = self.staged_card(406300006)
        self.grant_cast(game, card, "B")
        self.prepare(game, actor="B", active="B", mana={})
        payload = game.state.pending_decision.payload_by_actor["B"]
        self.assertIn(card.ref, {row.get("card") for row in payload["legal"]["diagnostic"]["unpayable"]})
        payload["objects"] = [{"id": card.ref, "name": card.printed_name, "cid": card.oracle_id[:8]}]
        self.checkpoint(game)
        retained = deepcopy(payload)

        reopened = self.replay(game, load=True)
        projected = reopened.packet("pilot:B", full=True)["decision"]["ctx"]
        diagnostic = projected["legal"]["diagnostic"]["unpayable"]
        self.assertNotIn(card.ref, {row.get("card") for row in diagnostic})
        self.assertEqual([{"id": card.ref, "name": "?"}], projected["objects"])
        self.assertEqual(retained, reopened.state.pending_decision.payload_by_actor["B"])

    def test_explicit_a_only_look_and_authenticated_cast_keep_owner_and_replay(self):
        game, card = self.staged_card(406300002, viewers=("A",))
        self.grant_cast(game, card, "A")
        self.prepare(game, actor="A", active="A", mana={"C": 1})
        self.checkpoint(game)

        reopened = self.replay(game, load=True)
        card = reopened.state.cards[card.object_id]
        self.assert_visible_only_to(reopened, card, "A")
        action = "cast:" + card.ref
        self.assertIn(action, [row["id"] for row in self.actions(reopened, "A")])
        command = {"action_id": action, "pay": "auto"}
        state_hash = authoritative_state_hash(reopened.state)
        rejected = reopened.act("pilot:B", command)
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(state_hash, authoritative_state_hash(reopened.state))
        accepted = reopened.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        card = reopened.state.cards[card.object_id]
        self.assertEqual(("stack", "B", "A", False), (card.zone, card.owner, card.controller, card.face_down))
        self.replay(reopened, load=True)

    def test_explicit_revealed_viewer_can_look_without_granting_the_owner_access(self):
        game, card = self.staged_card(406300003)
        card.revealed_to = ["C"]
        self.prepare(game)
        self.checkpoint(game)
        reopened = self.replay(game, load=True)
        self.assert_visible_only_to(reopened, reopened.state.cards[card.object_id], "C")

    def test_typed_viewer_remains_exclusive_and_departure_revokes_the_look(self):
        game, card = self.staged_card(406300003, viewers=("B", "D"))
        card.annotations["temporary_play_permission"] = private_opponent_library_cast_permission(
            card, actor="A", source_stack_ref="privacy-entry",
        )
        self.prepare(game, actor="A", active="A", mana={"C": 1})
        self.checkpoint(game)
        self.assert_visible_only_to(game, card, "A")
        result = game.act("pilot:A", {"action_id": "concede", "confirm_concede": True})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.players["A"].in_game)
        card = game.state.cards[card.object_id]
        self.assertTrue(card.face_down)
        self.assert_visible_only_to(game, card)
        self.replay(game, load=True)

    def test_face_down_battlefield_controller_keeps_legacy_look_without_known_metadata(self):
        game = self.session(406300004)
        card = self.add(game.engine, "Grizzly Bears", seat="B", zone="graveyard", ref="masked-battlefield")
        game.engine.move_card(card.object_id, "battlefield", controller="A", log=False, semantic_events=False)
        card.face_down = True
        card.known_to = []
        card.revealed_to = []
        self.prepare(game)
        self.checkpoint(game)
        self.assert_visible_only_to(game, card, "A", seat="A", zone="bf")
        self.replay(game, load=True)
