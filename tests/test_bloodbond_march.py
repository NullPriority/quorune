"""Native Bloodbond March casts, public identities and exact replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from scripts.build_test_database import build_fixture_database
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.public_zone_moves import PublicZoneMoveError, PublicZoneMoveSetSpec
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.cast_name_graveyard_returns import (
    CAST_NAMES_FIELD, CAST_NAME_RETURN_CONTEXT, CastNameReturnSnapshot, cast_name_graveyard_return_effect,
)


MARCH = "Bloodbond March"


class BloodbondMarchTests(InheritedNativeFixture, TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "cast-name-graveyard-return.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/cast-name-graveyard-return-cards.json",
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/ashiok-nightmare-weaver-cards.json",
            ROOT / "tests/fixtures/paradigm-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Rograkh, Son of Rohgahh"
        cls.deck = DeckDefinition(
            "Cast-name graveyard return",
            [DeckEntry("Rograkh, Son of Rohgahh", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            [commander],
        )

    def main(self, game, *, mana, counter_mana=False):
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, active="A", actor="A", mana=mana)
        if counter_mana:
            game.state.players["D"].mana_pool.update({"U": 2})
        self.checkpoint(game)

    def march(self, game, *, seat="A", zone="battlefield"):
        source = self.add(game.engine, "Bloodbond March", seat=seat, zone=zone, ref="bloodbond-march-source")
        self.assert_closed(game, source)
        return source

    def bear(self, game, *, seat, name, zone="graveyard"):
        return self.add(game.engine, "Grizzly Bears", seat=seat, zone=zone, ref=f"bloodbond-bear-{name}")

    def original_trigger(self, game):
        trigger = game.state.stack[-1]
        self.assertEqual("triggered_ability", trigger.kind)
        self.assertFalse(trigger.targets)
        self.assertIn(CAST_NAME_RETURN_CONTEXT, trigger.context)
        return trigger

    def assert_arrival(self, game, members):
        observed = next(event for event in reversed(game.state.events) if event.code == "effect.public_zone_move_set")
        self.assertEqual([value[0] for value in members], observed.changed_objects)
        self.assertEqual(len(members), observed.details["affected_count"])
        arrivals = []
        for object_id, origin_identity, owner in members:
            card = game.state.cards[object_id]
            self.assertEqual(("battlefield", owner, owner), (card.zone, card.owner, card.controller))
            self.assertNotEqual(origin_identity, card.logical_object_id)
            arrivals.append(card.zone_timestamp)
        if arrivals:
            self.assertEqual(1, len(set(arrivals)))

    @staticmethod
    def identities(cards):
        return [(card.object_id, card.logical_object_id, card.owner) for card in cards]

    def test_bloodbond_closed_shape_rejects_other_event_and_unsealed_names(self):
        original = self.db.lookup(MARCH)
        ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertFalse(ir.material_residuals)
        for phrase in ("an opponent casts a creature spell", "a player casts an artifact spell"):
            variant = replace(original, oracle_text=original.oracle_text.replace("a player casts a creature spell", phrase))
            self.assertNotEqual("exact", compile_oracle_card(
                variant, capability_registry=self.registry, capability_profile="commander_review",
            ).status)
        spec = cast_name_graveyard_return_effect()["set"]
        crossed = {**spec, "schema_version": 9}
        with self.assertRaises(PublicZoneMoveError):
            PublicZoneMoveSetSpec.from_dict(crossed)
        with self.assertRaises(PublicZoneMoveError):
            PublicZoneMoveSetSpec.from_dict({**spec, CAST_NAMES_FIELD: ["Grizzly Bears", "Grizzly Bears"]})
        with self.assertRaises(ValueError):
            CastNameReturnSnapshot.from_dict({"schema_version": True})

    def test_bloodbond_returns_exact_matches_under_each_owner_in_one_batch(self):
        game = self.session(10201801)
        source = self.march(game, zone="hand")
        live = self.bear(game, seat="A", name="live-spell", zone="hand")
        grouped = [self.bear(game, seat=seat, name=f"grave-{seat.lower()}-{index}")
                   for seat, index in (("C", 1), ("A", 2), ("B", 1), ("A", 1), ("D", 1))]
        expected = self.identities(sorted(grouped, key=lambda card: (card.owner, card.logical_object_id)))
        buried_decoy = self.add(game.engine, "Unsummon", seat="B", zone="graveyard", ref="wrong-name-card")
        exiled = self.bear(game, seat="C", name="outside-graveyard", zone="exile")
        hidden = self.bear(game, seat="D", name="private-hand", zone="hand")
        self.main(game, mana={"C": 3, "G": 2, "B": 1})
        self.cast_for(game, "A", source, reserve={"C": 1, "G": 1})
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "A", live)
        trigger = self.original_trigger(game)
        snapshot = CastNameReturnSnapshot.from_dict(trigger.context[CAST_NAME_RETURN_CONTEXT])
        self.assertEqual(("Grizzly Bears",), snapshot.last_known_names)
        self.assertEqual(live.object_id, snapshot.object_identity.object_id)
        game = self.replay(game, load=True)
        committed = []
        dispatch = game.engine._dispatch_zone_change_events

        def observe(card, **facts):
            if facts.get("origin") == "graveyard" and facts.get("destination") == "battlefield":
                committed.append((card.object_id, facts.get("putting_player")))
                self.assertTrue(all(game.state.cards[oid].zone == "battlefield" for oid, _, _ in expected))
            return dispatch(card, **facts)

        with patch.object(game.engine, "_dispatch_zone_change_events", side_effect=observe):
            self.resolve_top(game)
        self.assertEqual([(oid, owner) for oid, _, owner in expected], committed)
        self.assert_arrival(game, expected)
        self.assertEqual("stack", game.state.cards[live.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[buried_decoy.object_id].zone)
        self.assertEqual("exile", game.state.cards[exiled.object_id].zone)
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.assertIsNone(self.resolve(game))
        self.replay(game, load=True)

    def test_bloodbond_keeps_cast_name_after_counter_and_returns_that_physical_card(self):
        game = self.session(10201802)
        self.march(game, seat="C")
        live = self.bear(game, seat="A", name="countered-spell", zone="hand")
        existing = [self.bear(game, seat=seat, name=f"prior-{seat.lower()}") for seat in "BD"]
        counter = self.add(game.engine, "Counterspell", seat="D", zone="hand")
        self.main(game, mana={"C": 1, "G": 1}, counter_mana=True)
        self.cast_for(game, "A", live)
        trigger = self.original_trigger(game)
        snapshot = CastNameReturnSnapshot.from_dict(trigger.context[CAST_NAME_RETURN_CONTEXT])
        self.assertEqual("C", trigger.controller)
        self.cast_for(game, "D", counter, [snapshot.stack_ref])
        self.resolve_top(game)
        departed = game.state.cards[live.object_id]
        self.assertEqual("graveyard", departed.zone)
        self.assertNotEqual(snapshot.object_identity.logical_object_id, departed.logical_object_id)
        self.assertNotIn(snapshot.stack_ref, [item.ref for item in game.state.stack])
        expected = self.identities([departed, *existing])
        game = self.replay(game, load=True)
        self.assertEqual(trigger.ref, game.state.stack[-1].ref)
        self.assertIsNone(self.resolve(game))
        self.assert_arrival(game, expected)
        self.replay(game, load=True)

    def test_bloodbond_nameless_cast_triggers_without_using_hidden_identity(self):
        game = self.session(10201803)
        self.march(game)
        morph = self.add(game.engine, "Daru Lancer", zone="hand")
        match = self.add(game.engine, "Daru Lancer", seat="B", zone="graveyard", ref="bloodbond-morph-decoy")
        counter = self.add(game.engine, "Counterspell", seat="D", zone="hand")
        self.main(game, mana={"C": 3}, counter_mana=True)
        action = game.act("pilot:A", {"action_id": f"cast-morph:{morph.ref}", "pay": "auto"})
        self.assertTrue(action.ok, action.summary)
        trigger = self.original_trigger(game)
        snapshot = CastNameReturnSnapshot.from_dict(trigger.context[CAST_NAME_RETURN_CONTEXT])
        self.assertEqual((), snapshot.last_known_names)
        self.cast_for(game, "D", counter, [snapshot.stack_ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[morph.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assert_arrival(game, [])
        self.assertEqual("graveyard", game.state.cards[match.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[morph.object_id].zone)
        self.replay(game, load=True)
