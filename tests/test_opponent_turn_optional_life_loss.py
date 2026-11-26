"""Actual opponent-turn casts, optional losses, copy boundaries and replay."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.spell_cast_predicates import fixed_spell_cast_binding_spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


ORIGINAL = "Faerie Tauntings"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class OpponentTurnOptionalLifeLossTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        witnesses = {
            "conditional-modal-target-color-cards.json": "Twincast",
            "source-event-player-damage-cards.json": "Shock",
        }
        paths = [FIXTURES / "bound-effect-program-cards.json",
                 FIXTURES / "opponent-turn-optional-life-loss-cards.json"]
        for filename, name in witnesses.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] == name]
            (card,) = data["cards"]
            data["rulings"] = [row for row in data.get("rulings", ())
                               if row["oracle_id"] == card["oracle_id"]]
            path = folder / filename
            path.write_text(json.dumps(data), encoding="utf-8")
            paths.append(path)
        db = folder / "opponent-turn-casts.sqlite3"
        build_fixture_database(paths, db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Opponent-turn casts", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_exact_opponent_turn_and_old_first_spell_predicates_stay_distinct(self):
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        lowered = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", lowered.status, lowered.material_residuals)
        (trigger,) = lowered.faces[0].nodes
        self.assertEqual("spell.cast", trigger.event)
        self.assertIsNone(trigger.target_schema)
        self.assertEqual({"op": "offer_optional_effect", "player": "$controller",
                          "effects": [{"op": "lose_life_each_opponent", "amount": 1}]}, trigger.effects[0])
        expected = {"all": [
            {"field": "controller", "op": "eq", "value": "$source.controller"},
            {"field": "active_player", "op": "ne", "value": "$source.controller"},
        ]}
        self.assertEqual(expected, trigger.event_condition)
        first = fixed_spell_cast_binding_spec(
            "Whenever you cast your first spell during each opponent's turn, draw a card.")
        self.assertEqual(1, first.subject.caster_spell_number)
        self.assertIn({"field": "caster_spell_number", "op": "eq", "value": 1},
                      first.subject.event_condition["all"])
        for text in (
            record.oracle_text.replace("lose 1 life", "lose X life"),
            record.oracle_text.replace("during an opponent's turn", "after an opponent's turn"),
            record.oracle_text.replace("each opponent", "an undisclosed number of opponents"),
        ):
            result = compile_oracle_card(replace(record, oracle_text=text),
                                       capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)

    def trigger_total(self, game, source):
        return sum(event.code == "stack.trigger" and event.details.get("source") == source.ref
                   for event in game.state.events)

    def life_total(self, game):
        return [game.state.players[seat].life for seat in "ABCD"]

    def choose_loss(self, game, answer):
        decision = self.resolve(game)
        self.assertEqual("semantic.choice", decision.kind)
        self.assertEqual(["pilot:A"], list(game.pending_principals()))
        command = {"action_id": "choose", "choice": answer}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        chosen = game.act("pilot:A", command)
        self.assertTrue(chosen.ok, chosen.summary)
        return game

    def test_two_actual_casts_on_an_opponent_turn_offer_independent_choices(self):
        game = self.session(60330071)
        source = self.add(game.engine, ORIGINAL)
        hostile = self.add(game.engine, "Shock", seat="B", zone="hand", ref="opponent-original")
        copier = self.add(game.engine, "Twincast", zone="hand", ref="controller-copy-spell")
        later = self.add(game.engine, "Unsummon", zone="hand", ref="controller-later-spell")
        creature = self.add(game.engine, "Grizzly Bears", seat="B", ref="later-legal-target")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="faerie-private")
        for card in (source, hostile, copier, later):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"R": 1})
        game.state.players["A"].mana_pool["U"] = 3
        self.checkpoint(game)
        self.cast(game, "B", hostile, ["C"])
        self.assertEqual(0, self.trigger_total(game, source))
        original = game.state.stack[-1]
        self.cast_for(game, "A", copier, [original.ref], reserve={"U": 1})
        self.assertEqual(1, self.trigger_total(game, source))
        game = self.choose_loss(game, "decline")
        self.assertEqual([40, 40, 40, 40], self.life_total(game))

        choose_copy = self.resolve(game)
        self.assertEqual("semantic.choice", choose_copy.kind)
        keep_target = game.act("pilot:A", {"action_id": "choose"})
        self.assertTrue(keep_target.ok, keep_target.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40, 40, 36, 40], self.life_total(game))
        self.assertEqual(1, self.trigger_total(game, source))
        self.assertEqual("B", game.state.active_player)

        self.cast(game, "A", later, [creature.ref])
        self.assertEqual(2, self.trigger_total(game, source))
        game = self.choose_loss(game, "apply")
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40, 39, 35, 39], self.life_total(game))
        self.assertEqual("hand", game.state.cards[creature.object_id].zone)
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_controller_spell_on_its_own_turn_has_no_optional_trigger(self):
        game = self.session(60330072)
        source = self.add(game.engine, ORIGINAL)
        controlled = self.add(game.engine, "Unsummon", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        self.assert_closed(game, source)
        self.prepare(game, mana={"U": 1})
        self.checkpoint(game)
        self.cast(game, "A", controlled, [target.ref])
        self.assertEqual(0, self.trigger_total(game, source))
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40, 40, 40, 40], self.life_total(game))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.replay(game, load=True)
