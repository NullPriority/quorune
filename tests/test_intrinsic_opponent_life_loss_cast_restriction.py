"""The intrinsic spell gate reads committed life loss before offer or payment."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_history_query_model import (
    OPPONENT_LIFE_LOSS_HISTORY_CAPABILITY, PublicHistoryQuerySpec,
    matching_public_history_events, opponent_life_loss_including_departed_query, public_history_query_value,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.self_cast_restrictions import SelfCastRestrictionHandler
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Rakdos, Lord of Riots"
CAPABILITY = OPPONENT_LIFE_LOSS_HISTORY_CAPABILITY
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class IntrinsicOpponentLifeLossCastRestrictionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "opponent-life-loss-cast.sqlite3"
        build_fixture_database([basic, FIXTURES / "intrinsic-opponent-life-loss-cast-restriction-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Opponent life loss cast restriction", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        gate, = [program for program in programs if program.event == "cast.restriction"]
        return card, programs, gate

    def test_complete_original_keeps_flying_trample_and_separate_creature_discount(self):
        original, programs, gate = self.program()
        descriptor, = gate.handlers
        condition = SelfCastRestrictionHandler().validate(descriptor)
        self.assertEqual(opponent_life_loss_including_departed_query(), condition.history_query)
        self.assertTrue(any("combat.block.flying" in item.capability_dependencies for item in programs))
        self.assertTrue(any("combat.damage.assignment.trample" in item.capability_dependencies for item in programs))
        self.assertTrue(any(item.event == "cast.cost.modify" for item in programs))
        for text in (
            original.oracle_text.replace("an opponent lost life", "you lost life"),
            original.oracle_text.replace("this turn", "last turn", 1),
            original.oracle_text.replace("cast Rakdos", "cast another creature"),
        ):
            compiled = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry,
                                           capability_profile=PROFILE)
            self.assertFalse(any(CAPABILITY in node.capability_dependencies
                                 for face in compiled.faces for node in face.nodes), text)

    def test_raw_gate_requires_its_exact_marked_history_scope_and_independent_authority(self):
        _, _, gate = self.program()
        descriptor, = gate.handlers
        query = descriptor["source_condition"]["history_query"]
        for copied in (
            {**query, "schema_version": 1},
            {**query, "relation": "controller"},
            {**query, "event": "player_gained_life"},
        ):
            raw = {**descriptor, "source_condition": {**descriptor["source_condition"], "history_query": copied}}
            with self.assertRaises(SemanticNodeError):
                SelfCastRestrictionHandler().validate(raw)
        caps = [cap for cap in gate.capability_dependencies if cap != CAPABILITY]
        bad = replace(gate, capability_dependencies=caps, capability_closure=self.registry.closure(caps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(bad, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"], bound)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, bound["blockers"])

    def test_legacy_history_keeps_active_opponents_and_new_scope_includes_departed_receipts(self):
        active = SimpleNamespace(in_game=True)
        gone = SimpleNamespace(in_game=False)
        event = lambda **changes: SimpleNamespace(**{
            "kind": "player_lost_life", "target": "B", "amount": 3, "types": [], "actor": "A", **changes,
        })
        state = SimpleNamespace(turn_sequence=11, players={"A": active, "B": gone, "C": active},
                                turn_history=SimpleNamespace(schema_version=1, turn_sequence=11, events=(
                                    event(), event(target="C", amount=2), event(target="A", amount=5),
                                    event(kind="player_gained_life", amount=7),
                                )))
        current = opponent_life_loss_including_departed_query()
        legacy = replace(current, schema_version=1)
        self.assertEqual(current, PublicHistoryQuerySpec.from_dict(current.to_dict()))
        self.assertEqual(2, public_history_query_value(state, "A", legacy))
        self.assertEqual(5, public_history_query_value(state, "A", current))
        state.turn_sequence = 12
        self.assertIsNone(public_history_query_value(state, "A", current))

    def cast_offer(self, game, card):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((row for row in actions if row["id"] == "cast:" + card.ref), None)

    def journey(self, *, recover):
        game = self.session(601020980 if recover else 601020981)
        engine = game.engine
        source = self.add(engine, NAME, zone="hand")
        challenger = self.add(engine, "Lightning Bolt", seat="B", zone="hand", ref="rakdos-take-own-damage")
        initiator = self.add(engine, "Lightning Bolt", zone="hand", ref="rakdos-committed-opponent-damage")
        creature = self.add(engine, "Craw Wurm", zone="hand")
        remedy = self.add(engine, "Revitalize", seat="B", zone="hand") if recover else None
        secret = self.add(engine, "Counterspell", seat="D", zone="hand", ref="rakdos-private")
        self.assert_closed(game, source)
        game.state.players["B"].life = 10 if recover else 3
        self.prepare(game, mana={"R": 3, "B": 2, "C": 1, "G": 2})
        game.state.players["B"].mana_pool.update({"R": 1, "C": 1, "W": 1})
        self.checkpoint(game)

        self.assertIsNone(self.cast_offer(game, source))
        command = {"action_id": "cast:" + source.ref, "pay": "auto", "reserve": {"C": 1, "G": 2}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "B", challenger, ["A"], reserve={"C": 1, "W": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(37, game.state.players["A"].life)
        self.assertIsNone(self.cast_offer(game, source))

        self.cast_for(game, "A", initiator, ["B"], reserve={"R": 2, "B": 2, "C": 1, "G": 2})
        self.assertIsNone(self.resolve(game))
        if recover:
            self.cast_for(game, "B", remedy)
            self.assertIsNone(self.resolve(game))
            self.assertEqual(10, game.state.players["B"].life)
        else:
            self.assertFalse(game.state.players["B"].in_game)
        self.assertIsNotNone(self.cast_offer(game, source), "Committed opponent life loss must enable the native cast")
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        committed = game.act("pilot:A", command)
        self.assertTrue(committed.ok, committed.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)

        discounted = self.cast_offer(game, creature)
        option, = [row for row in discounted["cost_options"] if row["id"] == "normal"]
        self.assertEqual((1, 2), (option["requirements"]["GENERIC"], option["requirements"]["G"]))
        self.cast_for(game, "A", creature)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(secret.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_departed_opponent_still_enables_paid_cast_and_priced_creature(self):
        self.journey(recover=False)

    def test_actual_recovery_keeps_committed_loss_and_separate_colored_creature_price(self):
        self.journey(recover=True)

    def test_actual_reanimation_puts_the_original_onto_battlefield_without_casting(self):
        game = self.session(113060501)
        source = self.add(game.engine, NAME, zone="graveyard")
        reanimation = self.add(game.engine, "Zombify", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, reanimation)
        self.prepare(game, mana={"C": 3, "B": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", reanimation, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual([40, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_departure_witness_kills_only_counting_players_still_in_game(self):
        def active_only(state, controller, query, **values):
            if query.schema_version == 6:
                query = replace(query, schema_version=1)
            return matching_public_history_events(state, controller, query, **values)

        with patch("quorune.public_history_query_model.matching_public_history_events", side_effect=active_only):
            with self.assertRaisesRegex(AssertionError, "must enable the native cast"):
                self.journey(recover=False)
