from __future__ import annotations

import json

from tests import test_public_library_piles as native


class PublicLibraryPileDepartureTests(native.PublicLibraryPilesNativeTests):
    def decision(self, game, actor, stage):
        packet = game.packet("pilot:" + actor, full=True)
        projected = packet["decision"]
        self.assertIsNotNone(projected, stage)
        self.assertEqual(stage, projected["ctx"]["stage"])
        self.assertEqual((actor,), tuple(game.state.pending_decision.actors))
        self.assertEqual(["pilot:" + actor], game.pending_principals())
        return projected

    def test_selected_opponent_concedes_and_controller_appoints_another(self):
        for mirror in (False, True):
            with self.subTest(mirror=mirror):
                self.play_departure(mirror=mirror)

    def play_departure(self, *, mirror):
        spell_name = "Steam Augury" if mirror else "Fact or Fiction"
        mana = {"C": 2, "U": 1, "R": 1} if mirror else {"C": 3, "U": 1}
        game, spell, cards, hidden, private = self.scene(
            8004002 if mirror else 8004001, neighbor=True, spell_name=spell_name,
        )
        departing_private = game.state.cards[game.state.players["C"].zones["hand"][0]]
        self.announce(game, spell, cards, active="B", mana=mana)
        self.assertIsNotNone(self.resolve(game))
        piles = {"first": [cards[0].ref, cards[3].ref], "second": [cards[1].ref, cards[2].ref, cards[4].ref]}
        if mirror:
            self.decision(game, "A", "divide")
            self.submit(game, "A", piles=piles)
        self.decision(game, "A", "choose_opponent")
        self.submit(game, "A", opponent="C")
        acting_stage = "choose_pile" if mirror else "divide"
        offered = self.decision(game, "C", acting_stage)
        concessions = [action for action in offered["legal_actions"] if action["id"] == "concede"]
        self.assertEqual(1, len(concessions), "Expected a separately confirmed concede action for the chosen opponent")
        confirmation, = concessions[0]["form"]["fields"]
        self.assertEqual("confirm_concede", confirmation["name"])
        self.reject(game, "B", {"action_id": "concede", "confirm_concede": True})
        self.reject(game, "C", {"action_id": "concede", "confirm_concede": False})
        old_library = tuple(game.state.players["A"].zones["library"])
        old_stack_ref = game.state.stack[-1].ref
        result = game.act("pilot:C", {"action_id": "concede", "confirm_concede": True})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.players["C"].in_game)
        self.assertFalse(game.state.game_over)
        self.assertEqual(old_library, tuple(game.state.players["A"].zones["library"]))
        self.assertEqual(old_stack_ref, game.state.stack[-1].ref)
        new = self.decision(game, "A", "choose_opponent")
        choice, = new["ctx"]["legal_actions"]
        self.assertEqual({"B", "D"}, set(choice["choice_schema"]["legal_values"]))
        self.reject(game, "A", {"action_id": "choose", "opponent": "C"})
        self.reject(game, "C", {"action_id": "choose", "pile": "first"})
        for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            projected = game.packet(viewer, full=True)
            self.assertNotIn(departing_private.ref, json.dumps(projected))
        self.assert_visible(game, cards, actor="A", seats="ABD", hidden=hidden, private_hand=private)
        game = self.replay(game, load=True)
        self.decision(game, "A", "choose_opponent")
        self.submit(game, "A", opponent="D")
        replacement = self.decision(game, "D", acting_stage)
        if mirror:
            self.assertEqual(piles, replacement["ctx"]["piles"])
        else:
            self.submit(game, "D", piles=piles)
            self.decision(game, "A", "choose_pile")
        self.submit(game, "D" if mirror else "A", pile="first")
        self.decision(game, "A", "order_graveyard")
        ordered = [cards[4].ref, cards[2].ref, cards[1].ref]
        self.submit(game, "A", graveyard_order=ordered)
        self.assert_destinations_and_replay(
            game, spell, cards,
            [cards[0], cards[3]],
            [cards[4], cards[2], cards[1]],
        )
