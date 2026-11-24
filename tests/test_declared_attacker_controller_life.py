"""Native evidence for life changes triggered by a creature attacking a defender."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.target_controller_information import is_occurrence_controller_reference
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = ("Hissing Miasma", "Blood Reckoning", "Marchesa's Decree", "Revenge of Ravens")


class DeclaredAttackerControllerLifeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "declared-attacker-controller-life.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/declared-attacker-controller-life-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Declared attacker life effects", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self, name):
        original = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(original.oracle_id)
        attack, = (program for program in programs if program.event == "creature.attacks")
        return original, programs, attack

    def test_all_originals_bind_a_declared_attacker_controller_and_the_complete_siblings(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                _card, programs, attack = self.programs(name)
                self.assertEqual("battlefield", attack.active_zone)
                self.assertIsNone(attack.target_schema)
                self.assertTrue(is_occurrence_controller_reference(attack.effects[0]["player"]))
                self.assertEqual(2 if name == "Revenge of Ravens" else 1, len(attack.effects))
                if name == "Revenge of Ravens":
                    self.assertEqual(("$controller", 1), (attack.effects[1]["player"], attack.effects[1]["delta"]))
                if name == "Marchesa's Decree":
                    self.assertEqual({"creature.attacks", "permanent.enter.self"}, {program.event for program in programs})

    def test_narrow_recipient_grammar_and_raw_reference_fail_closed(self):
        card, _programs, attack = self.programs("Hissing Miasma")
        for text in (
            card.oracle_text.replace("attacks you", "attacks a battle you protect"),
            card.oracle_text.replace("attacks you", "becomes attacking you"),
            card.oracle_text.replace("1 life.", "1 life. Rewrite history."),
        ):
            ir = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, text)
        independently_targeted = compile_oracle_card(
            replace(card, oracle_text=card.oracle_text.replace("its controller", "target player")),
            capability_registry=self.registry,
        )
        targets = [node for face in independently_targeted.faces for node in face.nodes if node.target_schema]
        self.assertEqual(1, len(targets))
        self.assertFalse(any(is_occurrence_controller_reference(effect.get("player"))
                             for node in targets for effect in node.effects))
        for altered in (
            replace(attack, event="creature.unblocked"),
            replace(attack, event_condition={"field": "defending_player", "op": "eq", "value": "$source.controller"}),
            replace(attack, active_zone="graveyard"),
        ):
            binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def test_actual_attacks_distinguish_player_planeswalker_battle_and_other_seat(self):
        game = self.session(50836031)
        hissing = self.add(game.engine, "Hissing Miasma", seat="B")
        reckoning = self.add(game.engine, "Blood Reckoning", seat="B")
        walker = self.add(game.engine, "Jace Beleren", seat="B")
        walker.counters["loyalty"] = 3
        battle = self.add(game.engine, "Invasion of Zendikar", seat="C")
        battle.counters["defense"], battle.battle_protector = 3, "B"
        attackers = [self.add(game.engine, "Grizzly Bears", ref=f"declared-attacker-{n}") for n in range(4)]
        for card in (hissing, reckoning, walker, *attackers):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, list(zip(attackers, ("B", walker.ref, battle.ref, "C"), strict=True)))
        self.assertEqual((1, 2), (self.trigger_count(game, hissing), self.trigger_count(game, reckoning)))
        game = self.replay(game, load=True)
        for expected in (39, 38, 37):
            self.resolve_top(game)
            self.assertEqual(expected, game.state.players["A"].life)
        self.assertEqual([40, 40, 40], [game.state.players[seat].life for seat in "BCD"])
        self.replay(game, load=True)

    def test_actual_marchesa_keeps_monarch_and_ignores_a_token_put_onto_battlefield_attacking(self):
        game = self.session(50836041)
        decree = self.add(game.engine, "Marchesa's Decree", zone="hand")
        adeline = self.add(game.engine, "Adeline, Resplendent Cathar", seat="B")
        attacker = self.add(game.engine, "Grizzly Bears", seat="B")
        private = self.add(game.engine, "Plains", seat="D", zone="hand", ref="defender-family-private")
        for card in (decree, adeline, attacker):
            self.assert_closed(game, card)
        self.prepare(game, mana={"B": 1, "C": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", decree)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("A", game.state.monarch)
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "cleanup.discard")
        cleanup = game.packet("pilot:A", full=True)["decision"]["ctx"]
        discarded = game.act("pilot:A", {
            "action_id": "discard",
            "cards": [card["id"] for card in cleanup["hand"][:cleanup["count"]]],
        })
        self.assertTrue(discarded.ok, discarded.summary)
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "combat.attackers"
                   and current.state.pending_decision.actors == ["B"])
        order = {"action_id": "attack", "attackers": {attacker.ref: "C"}}
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:D", order)
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:B", order)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual((0, 1), (self.trigger_count(game, decree), self.trigger_count(game, adeline)))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(0, self.trigger_count(game, decree))
        self.assertEqual("A", game.state.monarch)
        self.assertEqual(40, game.state.players["B"].life)
        self.assertTrue(any(card.is_token and card.controller == "B"
                            and game.state.combat.attackers.get(card.object_id) == "A"
                            for card in game.state.cards.values()))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_actual_ravens_resolves_before_the_attackers_trigger_and_gains_life_even_at_lethal_loss(self):
        game = self.session(50836051)
        ravens = self.add(game.engine, "Revenge of Ravens", seat="B")
        adeline = self.add(game.engine, "Adeline, Resplendent Cathar")
        attacker = self.add(game.engine, "Grizzly Bears")
        for card in (ravens, adeline, attacker):
            self.assert_closed(game, card)
        game.state.players["A"].life = 1
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(attacker, "B")])
        self.assertEqual([adeline.object_id, ravens.object_id], [item.source_object_id for item in game.state.stack])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertFalse(game.state.players["A"].in_game)
        self.assertEqual(41, game.state.players["B"].life)
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_actual_life_loss_reads_the_changed_attacker_controller_and_its_departure(self):
        for bounce_before_resolution in (False, True):
            with self.subTest(bounce_before_resolution=bounce_before_resolution):
                game = self.session(60825011 + bounce_before_resolution)
                hissing = self.add(game.engine, "Hissing Miasma", seat="B")
                attacker = self.add(game.engine, "Grizzly Bears")
                theft = self.add(game.engine, "Turn Against", seat="C", zone="hand")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand") if bounce_before_resolution else None
                for card in (hissing, attacker, theft, bounce):
                    if card is not None:
                        self.assert_closed(game, card)
                for index in range(4):
                    self.add(game.engine, "Plains", seat="C", ref=f"attacker-control-mana-{index}")
                self.add(game.engine, "Mountain", seat="C")
                if bounce is not None:
                    self.add(game.engine, "Island", seat="B")
                self.prepare(game)
                self.checkpoint(game)
                self.attack(game, [(attacker, "B")])
                self.assertEqual(1, self.trigger_count(game, hissing))
                self.cast_for(game, "C", theft, [attacker.ref])
                self.resolve_top(game)
                self.assertEqual("C", game.state.cards[attacker.object_id].controller)
                self.assertNotIn(attacker.object_id, game.state.combat.attackers)
                if bounce is not None:
                    self.cast_for(game, "B", bounce, [attacker.ref])
                    self.resolve_top(game)
                    moved = game.state.cards[attacker.object_id]
                    self.assertEqual(("A", "hand"), (moved.owner, moved.zone))
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual([40, 40, 39, 40], [game.state.players[seat].life for seat in "ABCD"])
                self.replay(game, load=True)
