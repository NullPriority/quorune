from __future__ import annotations

"""A paid Equipment trigger resolves exile, private search, and joint entry."""

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
from quorune.exiled_target_library_search import EXILED_TARGET_SEARCH_CAPABILITY, exiled_target_search_effects
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune import zone_transitions
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_mutate_cast_native as mutate_fixtures


ORIGINAL = "Sword of Hearth and Home"


class EquippedExiledTargetSearchTests(InheritedNativeFixture, unittest.TestCase):
    mutate = mutate_fixtures.MutateCastNativeTests.mutate
    choose_merge = mutate_fixtures.MutateCastNativeTests.choose_merge

    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipped combat search", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "equipped-exiled-target-search.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "equipped-exiled-target-search-cards.json",
            fixtures / "equipped-combat-wolf-and-mill-cards.json",
            fixtures / "library-search-rules-cards.json",
            fixtures / "external-entry-tapped-cards.json",
            fixtures / "mutate-casting-cards.json",
            fixtures / "manifest-cards.json",
            fixtures / "controlled-aura-receiver-base-characteristics-cards.json",
        ], database)
        cls.use_database(database)

    @staticmethod
    def shuffles(game):
        return int(game.state.players["A"].stats.get("shuffle_count", 0))

    def submit(self, game, seat, command, *, valid=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertEqual(valid, result.ok, result.summary)
        if not valid:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def assert_private(self, game, *cards):
        for seat in "BCD":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            for card in cards:
                self.assertNotIn(card.ref, packet)
                self.assertNotIn(card.object_id, packet)

    def scene(self, seed, *, mode="ordinary"):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        sword = self.add(game.engine, ORIGINAL, zone="battlefield" if mode == "replacement" else "hand")
        carrier = self.add(game.engine, "Runeclaw Bear", ref="hearth-attacker")
        carrier.acquired_control_turn_count = -1
        target = self.add(game.engine, "Grizzly Bears", ref="hearth-owned-creature")
        foreign = self.add(game.engine, "Grizzly Bears", seat="C", ref="hearth-foreign-creature")
        land = self.add(game.engine, "Forest", zone="library", ref="hearth-private-basic-land")
        decoy = self.add(game.engine, "Opt", zone="library", ref="hearth-private-nonland")
        answers = {}
        mana = {"C": 5}
        if mode == "borrowed":
            game.engine.change_control(target.object_id, "C")
        if mode == "stale":
            answers["bounce"] = self.add(game.engine, "Cloudshift", zone="hand")
            self.add(game.engine, "Plains", ref="hearth-blink-payment")
        if mode == "prohibited":
            answers["rule"] = self.add(game.engine, "Mindlock Orb", seat="C")
        if mode == "partial":
            answers["rule"] = self.add(game.engine, "Thalia, Heretic Cathar", seat="C")
        if mode == "token":
            token_ref, = game.engine.create_token("A", name="Bearer Copy", copy_of=target.ref)
            target = next(card for card in game.state.cards.values() if card.ref == token_ref)
        if mode in {"aura", "instant"}:
            answers["manifest"] = self.add(game.engine, "Soul Summons", zone="hand")
            name = "Pacifism" if mode == "aura" else "Opt"
            target = self.add(game.engine, name, zone="library", ref="hearth-manifested-card")
            mana = {"C": 6, "W": 1}
        if mode == "replacement":
            answers["orb"] = self.add(game.engine, "Orb of Dreams", ref="hearth-entry-orb")
            answers["untapped"] = self.add(game.engine, "Archelos, Lagoon Mystic", seat="C", ref="hearth-entry-untapped")
        if mode == "merged":
            part = self.add(game.engine, "Dreamtail Heron", zone="hand", ref="hearth-second-component")
            answers["part"] = part
            mana = {"C": 8, "U": 1}
        for card in (sword, carrier, target, foreign, land, decoy, *answers.values()):
            if not card.is_token:
                self.assert_closed(game, card)

        if mode == "empty":
            for object_id in tuple(game.state.players["A"].zones["library"]):
                game.engine.move_card(object_id, "graveyard", log=False, semantic_events=False)
        self.prepare(game, mana=mana)
        self.checkpoint(game)
        if mode in {"aura", "instant"}:
            self.cast(game, "A", answers["manifest"])
            self.assertIsNone(self.resolve(game))
            self.assertTrue(game.state.cards[target.object_id].face_down)
        if mode == "merged":
            self.mutate(game, part, target)
            game = self.choose_merge(game, "bottom")
            self.assertIsNone(self.resolve(game))
        if mode != "replacement":
            self.cast(game, "A", sword)
            self.resolve_top(game)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        equip = next(row for row in actions if row["id"].startswith(f"activate:{sword.ref}:"))
        self.submit(game, "A", {"action_id": equip["id"], "targets": [carrier.ref], "pay": "auto"})
        self.resolve_top(game)
        self.assertEqual(carrier.object_id, game.state.cards[sword.object_id].attached_to)
        self.assertEqual(4, game.engine._numeric_stat(carrier.object_id, "power"))
        self.attack(game, [(carrier, "B")])
        self.until(game, lambda current: current.state.pending_decision.kind == "semantic.target")
        self.assertEqual(["A"], game.state.pending_decision.actors)
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]["legal_refs"]
        self.assertIn(target.ref, legal)
        self.assertNotIn(foreign.ref, legal)
        self.assertEqual(36, game.state.players["B"].life)
        return game, target, foreign, land, decoy, answers

    def assert_search(self, game, land, decoy):
        pending = self.resolve(game)
        self.assertEqual(("semantic.search", ["A"]), (pending.kind, pending.actors))
        schema = pending.payload_by_actor["A"]["legal_actions"][0]["choice_schema"]
        self.assertEqual((0, 1), (schema["minimum"], schema["maximum"]))
        self.assertIn(land.ref, schema["legal_refs"])
        self.assertNotIn(decoy.ref, schema["legal_refs"])
        self.assert_private(game, land, decoy)

    def test_actual_owned_target_under_opposing_control_enters_with_found_land_and_replays(self):
        game, target, foreign, land, decoy, _ = self.scene(91361801, mode="borrowed")
        self.assertEqual(("A", "C"), (game.state.cards[target.object_id].owner, game.state.cards[target.object_id].controller))
        self.submit(game, "A", {"action_id": "choose", "targets": [foreign.ref]}, valid=False)
        self.target(game, "A", [target.ref])
        before = self.shuffles(game)
        self.assert_search(game, land, decoy)
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        self.assertEqual("library", game.state.cards[land.object_id].zone)
        game = self.replay(game, load=True)
        self.submit(game, "D", {"action_id": "choose", "search_cards": [land.ref]}, valid=False)
        self.submit(game, "A", {"action_id": "choose", "search_cards": [decoy.ref]}, valid=False)
        self.assertEqual(before, self.shuffles(game))
        self.submit(game, "A", {"action_id": "choose", "search_cards": [land.ref]})
        returned, found = (game.state.cards[card.object_id] for card in (target, land))
        self.assertEqual(("battlefield", "A", "battlefield", "A"),
                         (returned.zone, returned.controller, found.zone, found.controller))
        self.assertEqual(returned.zone_timestamp, found.zone_timestamp, "Both permanents must enter simultaneously")
        self.assertEqual(before + 1, self.shuffles(game))
        self.assert_private(game, decoy)
        self.replay(game, load=True)

    def test_zero_targets_and_fail_to_find_are_distinct_legal_choices(self):
        for index, (choose_target, empty_library) in enumerate(((False, False), (True, False), (True, True))):
            with self.subTest(choose_target=choose_target, empty_library=empty_library):
                game, target, _, land, decoy, _ = self.scene(
                    91361802 + index, mode="empty" if empty_library else "ordinary",
                )
                original = target.logical_object_id
                self.target(game, "A", [target.ref] if choose_target else [])
                before = self.shuffles(game)
                if empty_library:
                    pending = self.resolve(game)
                    self.assertEqual("semantic.search", pending.kind)
                    self.assertEqual([], pending.payload_by_actor["A"]["search_cards"])
                else:
                    self.assert_search(game, land, decoy)
                self.assertEqual("exile" if choose_target else "battlefield", game.state.cards[target.object_id].zone)
                self.submit(game, "A", {"action_id": "choose", "search_cards": [] if choose_target else [land.ref]})
                self.assertEqual("graveyard" if empty_library else "library" if choose_target else "battlefield",
                                 game.state.cards[land.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
                self.assertEqual(not choose_target, game.state.cards[target.object_id].logical_object_id == original)
                self.assertEqual(before + 1, self.shuffles(game))
                if not empty_library:
                    self.assert_private(game, decoy)
                    if choose_target:
                        self.assert_private(game, land)
                self.replay(game, load=True)

    def test_sole_target_blinked_before_resolution_stops_search_and_shuffle(self):
        game, target, _, land, decoy, answers = self.scene(91361805, mode="stale")
        self.target(game, "A", [target.ref])
        original = game.state.cards[target.object_id].logical_object_id
        self.cast(game, "A", answers["bounce"], [target.ref])
        self.resolve_top(game)
        incarnation = game.state.cards[target.object_id].logical_object_id
        self.assertNotEqual(original, incarnation)
        before = self.shuffles(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(incarnation, game.state.cards[target.object_id].logical_object_id)
        self.assertEqual(before, self.shuffles(game))
        self.assert_private(game, land, decoy)
        self.replay(game, load=True)

    def test_prohibited_search_has_no_private_choice_and_still_returns_and_shuffles(self):
        game, target, _, land, decoy, _ = self.scene(91361806, mode="prohibited")
        original = target.logical_object_id
        self.target(game, "A", [target.ref])
        before = self.shuffles(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        self.assertNotEqual(original, game.state.cards[target.object_id].logical_object_id)
        self.assertEqual("library", game.state.cards[land.object_id].zone)
        self.assertEqual(before + 1, self.shuffles(game))
        self.assert_private(game, land, decoy)
        self.replay(game, load=True)

    def test_partial_entry_replacement_does_not_separate_the_two_arrivals(self):
        game, target, _, land, decoy, _ = self.scene(91361807, mode="partial")
        self.target(game, "A", [target.ref])
        self.assert_search(game, land, decoy)
        self.submit(game, "A", {"action_id": "choose", "search_cards": [land.ref]})
        returned, found = (game.state.cards[card.object_id] for card in (target, land))
        self.assertTrue(returned.tapped)
        self.assertFalse(found.tapped)
        self.assertEqual(returned.zone_timestamp, found.zone_timestamp)
        self.replay(game, load=True)

    def test_actual_merged_target_returns_both_physical_cards_with_the_land_after_load(self):
        game, target, _, land, decoy, answers = self.scene(91361808, mode="merged")
        part = answers["part"]
        self.assertIsNotNone(game.state.cards[target.object_id].permanent_composition)
        self.target(game, "A", [target.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual("composition_departure_order", context["operation"])
        self.assertEqual({target.ref, part.ref}, {row["id"] for row in context["objects"]})
        game = self.replay(game, load=True)
        self.submit(game, "A", {"action_id": "choose", "cards": [part.ref, target.ref]})
        self.assert_search(game, land, decoy)
        self.assertEqual({"exile"}, {game.state.cards[card.object_id].zone for card in (target, part)})
        game = self.replay(game, load=True)
        self.submit(game, "A", {"action_id": "choose", "search_cards": [land.ref]})
        found = [game.state.cards[card.object_id] for card in (target, part, land)]
        self.assertEqual({"battlefield"}, {card.zone for card in found})
        self.assertEqual(1, len({card.zone_timestamp for card in found}))
        self.assertTrue(all(card.permanent_composition is None for card in found))
        self.replay(game, load=True)

    def test_owned_token_stays_exiled_and_only_the_land_enters(self):
        game, target, _, land, decoy, _ = self.scene(91361809, mode="token")
        self.target(game, "A", [target.ref])
        self.assert_search(game, land, decoy)
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        self.submit(game, "A", {"action_id": "choose", "search_cards": [land.ref]})
        self.assertIsNone(self.resolve(game))
        token = game.state.cards.get(target.object_id)
        self.assertTrue(token is None or token.zone == "outside")
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        self.assert_private(game, decoy)
        self.replay(game, load=True)

    def test_manifested_aura_choice_keeps_the_land_private_and_enters_together(self):
        game, target, foreign, land, decoy, _ = self.scene(91361810, mode="aura")
        self.target(game, "A", [target.ref])
        self.assert_search(game, land, decoy)
        self.submit(game, "A", {"action_id": "choose", "search_cards": [land.ref]})
        pending = game.state.pending_decision
        self.assertEqual(("aura.entry", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(("exile", "library"), (game.state.cards[target.object_id].zone, game.state.cards[land.object_id].zone))
        self.assert_private(game, land, decoy)
        game = self.replay(game, load=True)
        choice = {"action_id": "choose", "aura_target": foreign.ref}
        self.submit(game, "C", choice, valid=False)
        with patch.object(zone_transitions, "dispatch_committed_batch_zone_events",
                          wraps=zone_transitions.dispatch_committed_batch_zone_events) as batch_events:
            self.submit(game, "A", choice)
        returned, found = (game.state.cards[card.object_id] for card in (target, land))
        self.assertEqual(("battlefield", False, foreign.object_id), (returned.zone, returned.face_down, returned.attached_to))
        arrivals = [{card.object_id for card, _ in call.args[1]} for call in batch_events.call_args_list]
        self.assertIn({target.object_id, land.object_id}, arrivals)
        self.assertGreater(returned.zone_timestamp, found.zone_timestamp, "Attachment gets its own later timestamp")
        self.assert_private(game, decoy)
        self.replay(game, load=True)

    def test_manifested_instant_stays_in_the_same_exile_occurrence(self):
        game, target, _, land, decoy, _ = self.scene(91361812, mode="instant")
        self.target(game, "A", [target.ref])
        self.assert_search(game, land, decoy)
        original = game.state.cards[target.object_id].logical_object_id
        self.submit(game, "A", {"action_id": "choose", "search_cards": [land.ref]})
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        self.assertEqual(original, game.state.cards[target.object_id].logical_object_id)
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        self.replay(game, load=True)

    def test_replacement_order_resumes_both_arrivals_and_shuffles_once(self):
        game, target, _, land, decoy, _ = self.scene(91361811, mode="replacement")
        self.target(game, "A", [target.ref])
        before = self.shuffles(game)
        self.assert_search(game, land, decoy)
        self.submit(game, "A", {"action_id": "choose", "search_cards": [land.ref]})
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(("exile", "library"), (game.state.cards[target.object_id].zone, game.state.cards[land.object_id].zone))
        self.assertEqual(before, self.shuffles(game))
        self.assert_private(game, land, decoy)
        game = self.replay(game, load=True)
        for _ in range(2):
            self.assertEqual("replacement.order", game.state.pending_decision.kind)
            self.assertEqual(("exile", "library"), (game.state.cards[target.object_id].zone, game.state.cards[land.object_id].zone))
            options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
            orb = next(row for row in options if "hearth-entry-orb" in row["id"])
            self.submit(game, "A", {"action_id": "choose", "replacement": orb["id"]})
        returned, found = (game.state.cards[card.object_id] for card in (target, land))
        self.assertEqual(("battlefield", "battlefield", False, False), (returned.zone, found.zone, returned.tapped, found.tapped))
        self.assertEqual(returned.zone_timestamp, found.zone_timestamp)
        self.assertEqual(before + 1, self.shuffles(game))
        self.assert_private(game, decoy)
        self.replay(game, load=True)

    def test_admission_requires_the_complete_exact_trigger_and_verified_dependency(self):
        outcome, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(outcome["strict_capability_ready"], outcome["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        trigger, = (program for program in semantics.programs_for_oracle(record.oracle_id) if program.event == "damage.dealt")
        self.assertEqual(exiled_target_search_effects(), tuple(trigger.effects))
        for changed in (
            replace(trigger, effects=list(trigger.effects[1:])),
            replace(trigger, effects=[trigger.effects[0], trigger.effects[1], *reversed(trigger.effects[2:])]),
            replace(trigger, target_schema={**trigger.target_schema, "owner": "any"}),
            replace(trigger, event_condition=None),
        ):
            rejected = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(rejected["strict"], rejected)
        for text in (
            record.oracle_text.replace("creature you own", "creature you control"),
            record.oracle_text.replace("combat damage", "damage"),
            record.oracle_text.replace("a basic land card", "a land card"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(record, oracle_text=text), capability_registry=self.registry, capability_profile="commander_review",
            ).status)

        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == EXILED_TARGET_SEARCH_CAPABILITY).update(
            status="blocked", blockers=["Combined entry authority withdrawn"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=read_evidence_document(
            ROOT / "platform/capability-evidence-declarations.json"), discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(
            record, capability_registry=registry, capability_profile="commander_review",
        ).status)

    def test_sequential_entry_mutation_is_killed_by_the_native_arrival_witness(self):
        original = ZoneTransitionOwner.move_cards_simultaneously
        observed = []

        def split(owner, changes, **kwargs):
            if len(changes) != 2 or any(zone != "battlefield" for _, zone in changes):
                return original(owner, changes, **kwargs)
            observed.append(tuple(changes))
            moved = []
            for object_id, zone in changes:
                arguments = {**kwargs, **{key: {object_id: kwargs[key][object_id]}
                                         for key in ("destination_controllers", "putting_players")}}
                moved.extend(original(owner, [(object_id, zone)], **arguments))
            return moved

        with patch.object(ZoneTransitionOwner, "move_cards_simultaneously", split):
            with self.assertRaisesRegex(AssertionError, "Both permanents must enter simultaneously"):
                self.test_actual_owned_target_under_opposing_control_enters_with_found_land_and_replays()
        self.assertTrue(observed)
