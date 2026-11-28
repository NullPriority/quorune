"""Compiler and native replay evidence for a stun target bound to a direct defender."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class DefenderBoundStunTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "defender-bound-stun.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/defender-bound-stun-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Defender-bound stun", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, card):
        return compile_oracle_card(
            card, capability_registry=self.registry, capability_profile="commander_review",
        )

    def test_actual_printed_sentences_rebuild_the_targeted_result_and_fail_closed(self):
        card = self.db.lookup("Lulu, Stern Guardian", fuzzy=False)
        ir = self.compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        trigger, activation = ir.faces[0].nodes
        self.assertEqual(card.oracle_text.splitlines()[0], trigger.text)
        self.assertEqual("combat.player_attacked", trigger.event)
        self.assertEqual({"field": "defender", "op": "eq", "value": "$source.controller"}, trigger.event_condition)
        self.assertEqual("attacking_actor", trigger.target_schema["combat_state"])
        self.assertEqual(["creature"], trigger.target_schema["types_any"])
        self.assertEqual(1, trigger.target_schema["count"])
        self.assertEqual(
            ({"op": "place_counters", "card": "$target.0", "counter": "stun", "amount": 1, "source": "$source"},),
            trigger.effects,
        )
        self.assertEqual(({"op": "proliferate", "proliferate_order_version": 1},), activation.effects)

        for replacement in (
            ("attacks you,", "attacks you with two or more creatures,"),
            ("creature attacking you.", "creature attacking another player."),
            ("on that creature.", "on another creature."),
            ("on that creature.", "on that creature. Draw a card."),
        ):
            with self.subTest(replacement=replacement):
                changed = replace(card, oracle_text=card.oracle_text.replace(*replacement))
                self.assertNotEqual("exact", self.compile(changed).status)

    def start_combat(self, seed, *, direct, blue_lands=0, bounce=False):
        game = self.session(seed)
        lulu = self.add(game.engine, "Lulu, Stern Guardian", seat="B")
        walker = self.add(game.engine, "Jace Beleren", seat="B")
        walker.counters["loyalty"] = 3
        battle = self.add(game.engine, "Invasion of Zendikar", seat="C")
        battle.counters["defense"], battle.battle_protector = 3, "B"
        recipients = (("B",) * direct) + ("C", walker.ref, battle.ref)
        attackers = [
            self.add(game.engine, "Grizzly Bears", ref=f"lulu-attacker-{i}")
            for i in range(len(recipients))
        ]
        for attacker in attackers:
            attacker.acquired_control_turn_count = -1
        for i in range(blue_lands):
            self.add(game.engine, "Island", seat="B", ref=f"lulu-island-{i}")
        unsummon = self.add(game.engine, "Unsummon", seat="B", zone="hand") if bounce else None
        self.assert_closed(game, lulu)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, list(zip(attackers, recipients, strict=True)))
        return game, lulu, attackers, unsummon

    def test_actual_multiplayer_declaration_targets_only_direct_attackers_and_replays_proliferate(self):
        game, lulu, attackers, _ = self.start_combat(731034001, direct=2, blue_lands=4)
        self.assertEqual("semantic.target", game.state.pending_decision.kind)
        self.assertEqual(("pilot:B",), tuple(game.pending_principals()))
        game = self.replay(game, load=True)
        offer = game.packet("pilot:B", full=True)["decision"]["legal_actions"][0]
        self.assertEqual({card.ref for card in attackers[:2]}, set(offer["target_schema"]["legal_refs"]))
        before = authoritative_state_hash(game.state)
        for outsider in attackers[2:]:
            result = game.act("pilot:B", {"action_id": "choose", "targets": [outsider.ref]})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.target(game, "B", [attackers[1].ref])
        self.assertEqual(1, self.trigger_count(game, lulu))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual([0, 1, 0, 0, 0], [
            game.state.cards[card.object_id].counters.get("stun", 0) for card in attackers
        ])

        self.priority(game, "B")
        offer = next(
            row for row in game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
            if row["id"].startswith(f"activate:{lulu.ref}:")
        )
        result = game.act("pilot:B", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        choice = game.packet("pilot:B", full=True)["decision"]
        self.assertEqual("proliferate", choice["ctx"]["operation"])
        self.assertIn(attackers[1].ref, choice["legal_actions"][0]["choice_schema"]["legal_refs"])
        result = game.act("pilot:B", {"action_id": "choose", "objects": [attackers[1].ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, game.state.cards[attackers[1].object_id].counters["stun"])
        self.replay(game, load=True)

    def test_actual_attacks_on_other_seats_walkers_and_battles_do_not_trigger(self):
        game, lulu, _, _ = self.start_combat(731034002, direct=0)
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertEqual(0, self.trigger_count(game, lulu))
        self.replay(game, load=True)

    def test_actual_target_departure_before_resolution_prevents_the_stun_counter(self):
        game, lulu, attackers, unsummon = self.start_combat(731034003, direct=1, blue_lands=1, bounce=True)
        attacker = attackers[0]
        self.target(game, "B", [attacker.ref])
        self.assertEqual(1, self.trigger_count(game, lulu))
        self.cast_for(game, "B", unsummon, [attacker.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[attacker.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertNotIn("stun", game.state.cards[attacker.object_id].counters)
        self.assertEqual(0, self.trigger_count(game, lulu))
        self.replay(game, load=True)
