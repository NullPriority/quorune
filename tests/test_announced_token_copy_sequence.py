"""Announced token copies and the ordered protection and life follow-ups."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.next_turn_effect import NEXT_TURN_EFFECT_CAPABILITY, NEXT_TURN_EFFECT_FIELD
from quorune.oracle_ir import compile_oracle_card
from quorune.query_effect_amount_model import CastXAmountSpec, PublicQueryAmountSpec
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules import closed_effect_program_shapes as ordered
from scripts.build_test_database import build_fixture_database


SOURCE = "For the Common Good"
SUPPORT = {
    "next-personal-turn-protection-cards.json": {"Isamaru, Hound of Konda", "Plains", "Grizzly Bears", "Bonesplitter"},
    "counter-replacement-cards.json": {"Doubling Season"},
    "external-entry-tapped-cards.json": {"Blind Obedience"},
    "activated-self-transform-cards.json": {"Word of Seizing"},
    "bargain-cards.json": {"Raise the Alarm", "Vedalken Orrery"},
    "counter-doubling-cards.json": {"Mossborn Hydra"},
}


class AnnouncedTokenCopySequenceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        sources = [fixtures / "announced-token-copy-sequence-cards.json"]
        for name, selected in SUPPORT.items():
            data = json.loads((fixtures / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in selected]
            assert {card["name"] for card in data["cards"]} == selected, name
            oracle_ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", []) if row["oracle_id"] in oracle_ids]
            support = folder / name
            support.write_text(json.dumps(data), encoding="utf-8")
            sources.append(support)
        database = folder / "announced-token-copy.sqlite3"
        build_fixture_database(sources, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Announced token copy with follow-ups", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def scene(self, seed):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        spell = self.add(game.engine, SOURCE, zone="hand")
        self.assert_closed(game, spell)
        return game, spell

    def copy_token(self, game, model, seat="A"):
        ref, = game.engine.create_token(seat, name="", copy_of=model.ref)
        return next(card for card in game.state.cards.values() if card.ref == ref)

    def tokens(self, game, seat):
        return {card.object_id: card for card in game.state.cards.values()
                if card.is_token and card.zone == "battlefield" and card.controller == seat}

    def protected(self, game, card):
        current = game.state.cards[card.object_id]
        keywords = game.engine._effective_card_data(current).get("keywords", ())
        return "indestructible" in {keyword.casefold() for keyword in keywords}

    def start(self, game, mana, *support, active="A", opponent_mana=None):
        for card in support:
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active=active, mana=mana)
        if active != "A":
            turn = game.state.current_turn
            game.state.current_turn = replace(turn, player=active)
            game.state.last_normal_turn_player = active
        if opponent_mana:
            game.state.players["B"].mana_pool.update(opponent_mana)
        self.checkpoint(game)

    def reject(self, game, command, seat="A"):
        before = authoritative_state_hash(game.state), len(game.commands)
        response = game.act("pilot:" + seat, command)
        self.assertFalse(response.ok, response.summary)
        self.assertEqual(before, (authoritative_state_hash(game.state), len(game.commands)))

    def command(self, game, spell, x, refs):
        self.priority(game, "A")
        offer = self.action(game, "A", spell)
        return {"action_id": offer["id"], "x": x, "targets": list(refs), "pay": "auto"}

    def cast_common(self, game, spell, x, target):
        command = self.command(game, spell, x, [target.ref])
        response = game.act("pilot:A", command)
        self.assertTrue(response.ok, response.summary)
        item = next(item for item in game.state.stack if item.card_object_id == spell.object_id)
        self.assertEqual((x, [target.ref], "A"), (item.x_value, item.targets, item.controller))

    def test_three_existing_effects_close_the_implicit_targeted_copy_reference(self):
        card = self.db.lookup(SOURCE, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        result = compile_oracle_card(card, **options)
        self.assertEqual("exact", result.status, result.material_residuals)
        node, = result.faces[0].nodes
        self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
        self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"], "count": 1,
                          "controller_relation": "you", "token": True}, node.target_schema)
        copy, grant, life = node.effects
        self.assertEqual(("create_token", "$controller", "target", CastXAmountSpec().to_dict()),
                         (copy["op"], copy["controller"], copy["copy_spec"]["origin"], copy["quantity"]))
        self.assertEqual(("modify_all_matching_permanents_until_end_of_turn", "$controller", True, ["Indestructible"]),
                         (grant["op"], grant["predicate"]["controller"], grant[NEXT_TURN_EFFECT_FIELD], grant["keywords"]))
        self.assertIs(grant["predicate"]["token"], True)
        amount = PublicQueryAmountSpec.from_dict(life["delta"])
        self.assertEqual(("life", "$controller", 1, True),
                         (life["op"], life["player"], amount.coefficient, amount.quantity.query.token))
        self.assertNotEqual("exact", compile_oracle_card(replace(card, mana_cost="{1}{G}"), **options).status)

        original = ordered._contains_target_reference

        def without_implicit_copy(value):
            if isinstance(value, dict) and value.get("op") == "create_token":
                return False
            return original(value)

        with patch.object(ordered, "_contains_target_reference", side_effect=without_implicit_copy):
            self.assertNotEqual("exact", compile_oracle_card(card, **options).status)

        game, _ = self.scene(61120011)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        deps = tuple(dep for dep in program.capability_dependencies if dep != NEXT_TURN_EFFECT_CAPABILITY)
        altered = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        report = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + NEXT_TURN_EFFECT_CAPABILITY, report["blockers"])

    def test_actual_repeated_x_copies_artifact_and_reads_the_replacement_adjusted_token_set(self):
        game, spell = self.scene(10730011)
        equipment = self.add(game.engine, "Bonesplitter")
        land = self.add(game.engine, "Plains", ref="land-model")
        bear = self.add(game.engine, "Grizzly Bears")
        target = self.copy_token(game, equipment)
        land_token = self.copy_token(game, land)
        own = (target, land_token, self.copy_token(game, bear))
        land.tapped = land_token.tapped = True
        other = self.copy_token(game, bear, "B")
        target.counters["+1/+1"] = 3
        target.tapped = False
        season = self.add(game.engine, "Doubling Season")
        tapper = self.add(game.engine, "Blind Obedience", seat="B")
        self.start(game, {"C": 4, "G": 1}, season, tapper)
        old = self.tokens(game, "A")
        self.assertEqual(3, len(old))
        self.reject(game, self.command(game, spell, 3, [target.ref]))
        self.reject(game, self.command(game, spell, 2, [equipment.ref]))
        self.reject(game, self.command(game, spell, 2, [other.ref]))
        self.reject(game, self.command(game, spell, 2, [target.ref, own[1].ref]))
        self.cast_common(game, spell, 2, target)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        now = self.tokens(game, "A")
        copied = [now[oid] for oid in now.keys() - old.keys()]
        self.assertEqual(4, len(copied))
        self.assertTrue(all(card.printed_name == "Bonesplitter" and card.tapped and not card.counters for card in copied))
        self.assertEqual({"+1/+1": 3}, game.state.cards[target.object_id].counters)
        self.assertFalse(game.state.cards[target.object_id].tapped)
        self.assertTrue(all(self.protected(game, card) for card in (*own, *copied)))
        self.assertFalse(self.protected(game, equipment))
        self.assertFalse(self.protected(game, other))
        self.assertEqual((47, 40, 40, 40), tuple(game.state.players[seat].life for seat in "ABCD"))
        self.replay(game, load=True)

    def test_legal_x_zero_freezes_the_grant_through_control_change_until_the_casters_turn(self):
        game, spell = self.scene(61120031)
        land = self.add(game.engine, "Plains", ref="land-model")
        target = self.copy_token(game, land)
        orrery = self.add(game.engine, "Vedalken Orrery")
        new_tokens = self.add(game.engine, "Raise the Alarm", zone="hand")
        theft = self.add(game.engine, "Word of Seizing", zone="hand", seat="B")
        self.start(game, {"C": 1, "W": 1, "G": 1}, orrery, new_tokens, theft,
                   active="D", opponent_mana={"C": 3, "R": 2})
        turn = game.state.turn_sequence
        before = set(self.tokens(game, "A"))
        self.reject(game, self.command(game, spell, 0, []))
        self.cast_common(game, spell, 0, target)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, set(self.tokens(game, "A")))
        self.assertEqual(41, game.state.players["A"].life)
        self.assertTrue(self.protected(game, target))
        self.cast_for(game, "A", new_tokens)
        self.assertIsNone(self.resolve(game))
        soldiers = {oid: card for oid, card in self.tokens(game, "A").items() if oid not in before}
        self.assertEqual(2, len(soldiers))
        self.assertTrue(all(not self.protected(game, card) for card in soldiers.values()))
        self.cast_for(game, "B", theft, [target.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", game.state.cards[target.object_id].controller)
        self.assertTrue(self.protected(game, target))
        self.assertEqual(41, game.state.players["A"].life)
        game = self.replay(game, load=True)
        self.until(game, lambda s: s.state.turn_sequence > turn and s.state.active_player == "A")
        self.assertFalse(self.protected(game, target))
        self.replay(game, load=True)

    def test_losing_control_of_the_same_target_before_resolution_cancels_all_clauses(self):
        game, spell = self.scene(60820091)
        model = self.add(game.engine, "Grizzly Bears")
        target = self.copy_token(game, model)
        survivor = self.copy_token(game, model)
        theft = self.add(game.engine, "Word of Seizing", zone="hand", seat="B")
        self.start(game, {"C": 2, "G": 1}, theft, opponent_mana={"C": 3, "R": 2})
        identity = target.logical_object_id
        self.cast_common(game, spell, 1, target)
        self.cast_for(game, "B", theft, [target.ref])
        self.resolve_top(game)
        current = game.state.cards[target.object_id]
        self.assertEqual(("B", identity), (current.controller, current.logical_object_id))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual({survivor.object_id}, set(self.tokens(game, "A")))
        self.assertEqual(40, game.state.players["A"].life)
        self.assertFalse(self.protected(game, survivor))
        self.assertFalse(self.protected(game, target))
        self.replay(game, load=True)

    def test_copy_applies_the_copiable_entry_counter_instead_of_copying_original_counters(self):
        game, spell = self.scene(70750011)
        hydra = self.add(game.engine, "Mossborn Hydra")
        token = self.copy_token(game, hydra)
        token.counters["+1/+1"] = 5
        token.tapped = True
        self.start(game, {"C": 2, "G": 1}, hydra)
        self.cast_common(game, spell, 1, token)
        self.assertIsNone(self.resolve(game))
        copy, = [card for oid, card in self.tokens(game, "A").items() if oid != token.object_id]
        self.assertEqual(("Mossborn Hydra", False, {"+1/+1": 1}),
                         (copy.printed_name, copy.tapped, copy.counters))
        self.assertEqual({"+1/+1": 5}, game.state.cards[token.object_id].counters)
        self.assertEqual(42, game.state.players["A"].life)
        self.assertTrue(self.protected(game, copy))
        self.replay(game, load=True)
