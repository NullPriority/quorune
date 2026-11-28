"""Gonti's native private selection, scoped casting authority, and replay."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.replacement.operations import SetField
from quorune.replacement_effects import ReplacementClass, ReplacementEffect
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime import zone_replacements
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


GONTI = "Gonti, Lord of Luxury"
OTHER_PRINCIPALS = ("pilot:B", "pilot:C", "pilot:D", "spectator")
MANA_TYPES = ("W", "U", "B", "R", "G", "C")


class GontiNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "gonti.sqlite3"
        build_fixture_database(ROOT / "tests" / "fixtures" / "gonti-native-cards.json", database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Gonti private library witness",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)],
            [commander],
        )

    def ready(self, game, *cards, mana=None, opposing_mana=None):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        if opposing_mana:
            game.state.players["B"].mana_pool.update(opposing_mana)
        self.prepare(game, mana=mana or {"B": 2, "C": 2})
        self.checkpoint(game)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def library(self, game, seat="B"):
        return list(game.state.players[seat].zones["library"])

    def add_top(self, game, *names):
        cards = []
        for index, name in enumerate(reversed(names)):
            card = self.add(game.engine, name, seat="B", zone="library", ref=f"opponent-looked-{index}")
            card.known_to.clear()
            card.revealed_to.clear()
            cards.append(card)
        return list(reversed(cards))

    def empty_library(self, game, seat="B"):
        for object_id in self.library(game, seat):
            game.engine.move_card(object_id, "graveyard", log=False, semantic_events=False)

    def enter(self, game, gonti, *, reserve=None):
        self.cast_for(game, "A", gonti, reserve=reserve)
        self.resolve_top(game)
        self.assertEqual("battlefield", self.current(game, gonti).zone)
        self.until(game, lambda current: (
            current.state.pending_decision is not None
            and current.state.pending_decision.kind == "semantic.target"
        ))

    def target_opponent(self, game, seat="B"):
        result = game.act("pilot:A", {"action_id": "choose", "targets": [seat]})
        self.assertTrue(result.ok, result.summary)
        item, = [item for item in game.state.stack if item.kind == "triggered_ability"]
        self.assertEqual(("A", [seat]), (item.controller, item.targets))
        return item

    def look(self, game, expected):
        observer_baselines = {
            principal: game.packet(principal, full=True) for principal in OTHER_PRINCIPALS
        }
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["objects"]
        self.assertEqual(
            [(card.ref, card.printed_name) for card in expected],
            [(row["id"], row["name"]) for row in rows],
        )
        self.assert_masked_look(game, expected, observer_baselines)

    def assert_masked_look(self, game, cards, observer_baselines):
        for principal in OTHER_PRINCIPALS:
            baseline = observer_baselines[principal]
            packet = game.packet(principal, full=True)
            self.assertIsNone(packet["decision"])
            existing_event_ids = {event["id"] for event in baseline.get("events", ())}
            self.assertNotIn("library.look", [
                event["c"] for event in packet.get("events", ()) if event["id"] not in existing_event_ids
            ])
            before = json.dumps(baseline, ensure_ascii=False)
            rendered = json.dumps(packet, ensure_ascii=False)
            for card in cards:
                self.assertNotIn(card.ref, rendered, principal)
                self.assertNotIn(card.object_id, rendered, principal)
                # A different visible card may have the same printed name.
                if card.printed_name not in before:
                    self.assertNotIn(card.printed_name, rendered, principal)

    def reject(self, game, principal, command):
        before = authoritative_state_hash(game.state)
        result = game.act(principal, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def pick(self, game, card):
        result = game.act("pilot:A", {"action_id": "choose", "cards": [card.ref]})
        self.assertTrue(result.ok, result.summary)

    def choose_for_cast(self, game, gonti, card, *, reserve=None):
        self.enter(game, gonti, reserve=reserve)
        self.target_opponent(game)
        self.look(game, [game.state.cards[oid] for oid in reversed(self.library(game)[-4:])])
        self.pick(game, card)
        self.assertEqual("exile", self.current(game, card).zone)

    def cast_actions(self, game, seat):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def cast_is_offered(self, game, seat, card):
        return any(row["id"] == "cast:" + self.current(game, card).ref
                   for row in self.cast_actions(game, seat))

    def assert_selected_visibility(self, game, selected):
        current = self.current(game, selected)
        self.assertEqual(("exile", "B", True, ["A"], []),
                         (current.zone, current.owner, current.face_down, current.known_to, current.revealed_to))
        for principal in ("pilot:A", *OTHER_PRINCIPALS):
            packet = game.packet(principal, full=True)
            rows = packet["state"]["players"]["B"]["ex"]
            row, = (row for row in rows if row["id"] == current.ref)
            if principal == "pilot:A":
                self.assertEqual(current.printed_name, row["n"])
            else:
                self.assertEqual({"id": current.ref, "n": "?", "fd": 1}, row)
                rendered = json.dumps(packet)
                self.assertNotIn(current.printed_name, rendered)
                self.assertNotIn(current.object_id, rendered)

    def manual_cast(self, game, card, payment, targets=()):
        self.assertTrue(self.cast_is_offered(game, "A", card))
        command = {
            "action_id": "cast:" + self.current(game, card).ref,
            "pay": "manual", "payment": payment, "targets": list(targets),
        }
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        item, = [item for item in game.state.stack if item.card_object_id == card.object_id]
        self.assertEqual("A", item.controller)
        return item

    def test_actual_target_opponent_and_revalidates_hexproof_before_private_look(self):
        game = self.session(701200101)
        gonti = self.add(game.engine, GONTI, zone="hand")
        veil = self.add(game.engine, "Veil of Summer", seat="B", zone="hand")
        self.add_top(game, "Lightning Bolt", "Opt", "Grizzly Bears", "Sol Ring")
        self.ready(game, gonti, veil, opposing_mana={"G": 1})
        self.enter(game, gonti)
        self.reject(game, "pilot:A", {"action_id": "choose", "targets": ["A"]})
        self.reject(game, "pilot:C", {"action_id": "choose", "targets": ["B"]})
        self.target_opponent(game)

        self.cast_for(game, "B", veil)
        self.resolve_top(game)
        after_veil = self.library(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(after_veil, self.library(game))
        self.assertEqual([], game.state.players["B"].zones["exile"])
        self.assertEqual([], game.state.stack)
        self.replay(game, load=True)

    def test_private_top_four_mandatory_pick_random_bottom_and_principal_replay(self):
        game = self.session(701200102)
        gonti = self.add(game.engine, GONTI, zone="hand")
        looked = self.add_top(game, "Lightning Bolt", "Opt", "Grizzly Bears", "Sol Ring")
        selected, *rest = looked
        self.ready(game, gonti)
        before_library = self.library(game)
        unseen = before_library[:-4]
        self.enter(game, gonti)
        trigger_ref = self.target_opponent(game).ref
        self.look(game, looked)
        for principal in OTHER_PRINCIPALS:
            self.reject(game, principal, {"action_id": "choose", "cards": [selected.ref]})
        for reply in (
            [],
            [selected.ref, rest[0].ref],
            [selected.ref, selected.ref],
            ["stale-object-ref"],
            {"exile": [selected.ref], "bottom": [card.ref for card in rest]},
        ):
            self.reject(game, "pilot:A", {"action_id": "choose", "cards": reply})
        game = self.replay(game, load=True)

        old_token = game.engine.permissions.capability_for("pilot:A").token
        from quorune.rules import private_opponent_library_cast

        shuffle = private_opponent_library_cast._shuffle_bottom_selection
        with patch.object(private_opponent_library_cast, "_shuffle_bottom_selection", wraps=shuffle) as randomize:
            self.pick(game, selected)
        randomize.assert_called_once()
        _, owner, source_ref, randomized = randomize.call_args.args
        self.assertEqual(("B", trigger_ref), (owner, source_ref))
        self.assertCountEqual([card.object_id for card in rest], randomized)
        self.assert_selected_visibility(game, selected)
        result = self.library(game)
        self.assertCountEqual([card.object_id for card in rest], result[:3])
        self.assertEqual(unseen, result[3:])
        for card in rest:
            self.assertEqual([], self.current(game, card).known_to)

        before = authoritative_state_hash(game.state)
        rejected = game.engine.try_submit(
            token=old_token, principal="pilot:A", action="choose", payload={"cards": [rest[0].ref]},
        )
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.replay(game, load=True)

    def test_short_and_empty_libraries_respect_mandatory_one_and_prohibit_land_play(self):
        for size in (0, 1):
            with self.subTest(library_size=size):
                game = self.session(701200103 + size)
                gonti = self.add(game.engine, GONTI, zone="hand")
                self.empty_library(game)
                land = self.add_top(game, "Forest")[0] if size else None
                self.ready(game, gonti)
                self.enter(game, gonti)
                self.target_opponent(game)
                pending = self.resolve(game)
                if land is None:
                    self.assertIsNone(pending)
                    self.assertEqual([], game.state.players["B"].zones["exile"])
                else:
                    if pending is not None:
                        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["objects"]
                        self.assertEqual([land.ref], [row["id"] for row in rows])
                        self.reject(game, "pilot:A", {"action_id": "choose", "cards": []})
                        self.pick(game, land)
                    self.assert_selected_visibility(game, land)
                    self.assertFalse(any(row.get("card") == land.ref for row in self.cast_actions(game, "A")))
                    self.reject(game, "pilot:A", {"action_id": "cast:" + land.ref, "pay": "auto"})
                    self.reject(game, "pilot:A", {"action_id": "play-land:" + land.ref})
                    self.assertEqual("exile", self.current(game, land).zone)
                self.assertEqual([], self.library(game))
                self.replay(game, load=True)

    def test_source_departure_preserves_permission_and_reexile_ends_old_permission(self):
        game = self.session(701200105)
        gonti = self.add(game.engine, GONTI, zone="hand")
        bolt = self.add_top(game, "Lightning Bolt")[0]
        donate = self.add(game.engine, "Donate", zone="hand")
        kill = self.add(game.engine, "Murder", seat="B", zone="hand")
        counter = self.add(game.engine, "Dissipate", seat="B", zone="hand")
        self.ready(
            game, gonti, bolt, donate, kill, counter,
            mana={"B": 2, "C": 5, "U": 1}, opposing_mana={"B": 2, "C": 2, "U": 2},
        )
        self.choose_for_cast(game, gonti, bolt, reserve={"C": 3, "U": 1})
        original = self.current(game, bolt).logical_object_id

        self.cast_for(game, "A", donate, ["C", gonti.ref], reserve={"C": 1})
        self.resolve_top(game)
        self.assertEqual("C", self.current(game, gonti).controller)
        self.assert_selected_visibility(game, bolt)
        self.cast_for(game, "B", kill, [gonti.ref], reserve={"C": 1, "U": 2})
        self.resolve_top(game)
        self.assertEqual("graveyard", self.current(game, gonti).zone)
        self.assert_selected_visibility(game, bolt)

        game = self.replay(game, load=True)
        borrowed = self.manual_cast(game, bolt, {"C": 1}, targets=["D"])
        self.cast_for(game, "B", counter, [borrowed.ref])
        self.resolve_top(game)
        fresh = self.current(game, bolt)
        self.assertEqual(("exile", "B", False), (fresh.zone, fresh.owner, fresh.face_down))
        self.assertNotEqual(original, fresh.logical_object_id)
        self.assertFalse(self.cast_is_offered(game, "A", bolt))
        self.reject(game, "pilot:A", {"action_id": "cast:" + fresh.ref, "pay": "auto", "targets": ["D"]})
        self.replay(game, load=True)

    def test_selected_destination_redirect_keeps_random_remainder_and_does_not_grant_cast(self):
        game = self.session(701200106)
        gonti = self.add(game.engine, GONTI, zone="hand")
        looked = self.add_top(game, "Lightning Bolt", "Opt", "Grizzly Bears", "Sol Ring")
        selected, *rest = looked
        self.ready(game, gonti, mana={"B": 2, "C": 3})
        self.enter(game, gonti, reserve={"C": 1})
        self.target_opponent(game)
        self.look(game, looked)
        earlier = self.library(game)
        redirect = ReplacementEffect(
            effect_id="test:gonti-library-selection-redirect",
            source_id="test:redirect-library-exile",
            event_kind="zone.change",
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "origin": {"eq": "library"},
                "destination": {"eq": "exile"},
                "object_ref": {"eq": selected.ref},
            },
            operations=(SetField("destination", "graveyard"),),
        )
        collect = zone_replacements.collect_zone_change_replacement_effects

        def collect_with_redirect(host, **kwargs):
            return (*collect(host, **kwargs), redirect)

        with patch.object(zone_replacements, "collect_zone_change_replacement_effects", collect_with_redirect):
            self.pick(game, selected)
            self.assertEqual(("graveyard", "B", False), (
                self.current(game, selected).zone, self.current(game, selected).owner,
                self.current(game, selected).face_down,
            ))
            self.assertCountEqual([card.object_id for card in rest], self.library(game)[:3])
            self.assertEqual(earlier[:-4], self.library(game)[3:])
            self.assertFalse(self.cast_is_offered(game, "A", selected))
            self.assertTrue(any(
                event.code == "replacement.apply" and event.details.get("effect_id") == redirect.effect_id
                and event.details.get("destination") == "graveyard"
                for event in game.state.events
            ))
            self.replay(game, load=True)

    def test_sorcery_permission_keeps_timing_and_all_six_mana_types_pay_colored_cost(self):
        game = self.session(701200107)
        gonti = self.add(game.engine, GONTI, zone="hand")
        spike = self.add_top(game, "Lava Spike")[0]
        ordinary = self.add(game.engine, "Murder", zone="hand", ref="unrelated-ordinary-murder")
        kill = self.add(game.engine, "Murder", seat="B", zone="hand", ref="opponent-timing-reply")
        one_each = dict.fromkeys(MANA_TYPES, 1)
        self.ready(
            game, gonti, spike, ordinary, kill,
            mana={**one_each, "B": 3, "C": 3}, opposing_mana={"B": 2, "C": 1},
        )
        self.choose_for_cast(game, gonti, spike, reserve=one_each)
        self.assertTrue(self.cast_is_offered(game, "A", spike))
        pool = game.state.players["A"].mana_pool
        self.assertEqual(one_each, {m: pool.get(m, 0) for m in MANA_TYPES})
        self.assertFalse(self.cast_is_offered(game, "A", ordinary))
        self.reject(game, "pilot:A", {
            "action_id": "cast:" + ordinary.ref, "pay": "manual",
            "payment": {"B": 1, "G": 1, "C": 1}, "targets": [gonti.ref],
        })
        with TemporaryDirectory() as folder:
            snapshot = Path(folder) / "one-root-exile-permission"
            game.save(snapshot)
            out_of_timing = CommanderSession.load(self.db, snapshot)
            self.cast_for(out_of_timing, "B", kill, [gonti.ref])
            self.assertTrue(out_of_timing.state.stack)
            self.assertFalse(self.cast_is_offered(out_of_timing, "A", spike))
            self.reject(out_of_timing, "pilot:A", {
                "action_id": "cast:" + spike.ref, "pay": "manual",
                "payment": {"R": 1}, "targets": ["D"],
            })
            for mana_type in MANA_TYPES:
                with self.subTest(mana_type=mana_type):
                    branch = CommanderSession.load(self.db, snapshot)
                    before = branch.state.players["D"].life
                    self.manual_cast(branch, spike, {mana_type: 1}, targets=["D"])
                    pool = branch.state.players["A"].mana_pool
                    self.assertEqual({m: int(m != mana_type) for m in MANA_TYPES},
                                     {m: pool.get(m, 0) for m in MANA_TYPES})
                    self.resolve_top(branch)
                    self.assertEqual(before - 3, branch.state.players["D"].life)
                    self.assertEqual(("graveyard", "B"), (
                        self.current(branch, spike).zone, self.current(branch, spike).owner,
                    ))
            self.replay(branch, load=True)

    def test_any_type_permission_preserves_snow_mana_provenance(self):
        game = self.session(701200108)
        gonti = self.add(game.engine, GONTI, zone="hand")
        golem = self.add_top(game, "Icehide Golem")[0]
        snow_land = self.add(game.engine, "Snow-Covered Island", zone="hand")
        self.ready(game, gonti, golem, snow_land, mana={"B": 2, "C": 3})
        self.choose_for_cast(game, gonti, golem, reserve={"C": 1})
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertFalse(self.cast_is_offered(game, "A", golem))
        self.reject(game, "pilot:A", {"action_id": "cast:" + golem.ref, "pay": "manual", "payment": {"C": 1}})

        row, = (row for row in self.cast_actions(game, "A") if row.get("card") == snow_land.ref)
        moved = game.act("pilot:A", {"action_id": row["id"]})
        self.assertTrue(moved.ok, moved.summary)
        self.assertEqual("battlefield", self.current(game, snow_land).zone)
        self.assertTrue(self.cast_is_offered(game, "A", golem))
        cast = game.act("pilot:A", {"action_id": "cast:" + golem.ref, "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertTrue(self.current(game, snow_land).tapped)
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.resolve_top(game)
        self.assertEqual(("battlefield", "B", "A"), (
            self.current(game, golem).zone, self.current(game, golem).owner,
            self.current(game, golem).controller,
        ))
        self.replay(game, load=True)
