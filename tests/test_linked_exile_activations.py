"""Native witnesses for linked public exile and current Food activations."""

from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.adapters import compile_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_ability_exile_model import LINKED_ABILITY_EXILE_ANNOTATION
from quorune.linked_exile_activation_model import LINKED_ACTIVATION_BINDING, LINKED_EXILE_ACTIVATION_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash, replay_record
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import (
    DEFAULT_CAPABILITY_EVIDENCE, _capability_validation_snapshot, capability_evidence_fingerprint,
    validate_capability_evidence_index,
)
from quorune.rules.evidence_storage import read_evidence_document
from quorune.session import CommanderSession
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database


class LinkedExileActivationTests(InheritedNativeFixture, unittest.TestCase):
    source = "Hazel's Brewmaster"
    sprite = "Argothian Sprite"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "linked-exile-activations.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        if frozen_db := os.environ.get("QUORUNE_LINKED_ACTIVATION_WITNESS_DB"):
            path = Path(frozen_db)
        else:
            build_fixture_database(
                [fixtures / name for name in (
                    "bound-effect-program-cards.json",
                    "linked-exile-activation-cards.json",
                    "source-attack-alone-cards.json",
                    "activated-self-transform-cards.json",
                    "activation-result-views-cards.json",
                    "devour-cards.json",
                    "attacking-card-entry-cards.json",
                    "static-mana-spending-cards.json",
                )],
                path,
            )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Linked exile activation witness", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def _board(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, self.source, zone="hand", ref="LINK-ACTIVATION-SOURCE")
        sprite = self.add(game.engine, self.sprite, seat="C", zone="graveyard", ref="LINK-COUNTER-DONOR")
        elf = self.add(game.engine, "Llanowar Elves", seat="D", zone="graveyard", ref="LINK-MANA-DONOR")
        foreign = self.add(game.engine, self.sprite, seat="D", zone="exile", ref="LINK-UNRELATED-EXILE")
        private = self.add(game.engine, "Fervor", seat="D", zone="hand", ref="LINK-HIDDEN-WITNESS")
        a_food = self.add(game.engine, "Gingerbrute", ref="LINK-FOOD-A")
        b_food = self.add(game.engine, "Gingerbrute", seat="B", ref="LINK-FOOD-B")
        nonfood = self.add(game.engine, "Grizzly Bears", ref="LINK-OTHER-A")
        a_food.acquired_control_turn_count = b_food.acquired_control_turn_count = -1
        self.add(game.engine, "Fervor")
        for seat, names in (
            ("A", ("Thran Dynamo", "Thran Dynamo", "Forest", "Swamp")),
            ("B", ("Mountain", "Mountain", "Island", "Swamp")),
            ("C", ("Island",)),
        ):
            for index, name in enumerate(names):
                self.add(game.engine, name, seat=seat, ref=f"LINK-LAND-{seat}-{index}")
        theft = self.add(game.engine, "Act of Treason", seat="B", zone="hand", ref="LINK-CONTROL")
        bounce = self.add(game.engine, "Unsummon", seat="C", zone="hand", ref="LINK-BOUNCE")
        cremate = self.add(game.engine, "Cremate", seat="B", zone="hand", ref="LINK-EXILE-REPLY")
        for required in (source, sprite, elf, a_food, theft, bounce, cremate):
            self.assert_closed(game, required)
        moves = ZoneTransitionOwner(game.engine)
        for player in game.state.players.values():
            for object_id in player.zones["hand"][:-5]:
                moves.move_card(object_id, "library")
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 3, "B": 1})
        self.checkpoint(game)
        return game, source, sprite, elf, foreign, private, a_food, b_food, nonfood, theft, bounce, cremate

    def _begin(self, game, source):
        self.cast(game, "A", source)
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))

    def _food_tokens(self, game, seat):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.controller == seat and card.is_token
                and "food" in game.engine._effective_type_parts(card)[1]]

    def _grants(self, game, recipient):
        live = game.state.cards[recipient.object_id]
        return [ability for ability in game.engine._activated_abilities(live)
                if ability.ability_id.startswith("linked-exile:")]

    def _counter_grant(self, game, food):
        matches = [ability for ability in self._grants(game, food) if ability.cost_text == "{7}"]
        self.assertEqual(1, len(matches), matches)
        return matches[0]

    def _offer(self, game, seat, food, grant):
        self.priority(game, seat)
        action_id = f"activate:{food.ref}:{grant.ability_id}"
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(action for action in actions if action["id"] == action_id)

    def _activate(self, game, seat, food, grant, *, payment="auto"):
        action = self._offer(game, seat, food, grant)
        result = game.act("pilot:" + seat, {"action_id": action["id"], "pay": payment})
        self.assertTrue(result.ok, result.summary)
        return action

    def _record(self, game, label):
        folder = os.environ.get("QUORUNE_FOCUSED_RECORD_DIR")
        if not folder:
            self.replay(game, load=True)
            return
        path = Path(folder) / f"{label}-{game.state.game_id}"
        game.save(path, include_review=False)
        report = replay_record(path, self.db)
        self.assertTrue(report["ok"], report)
        self.assertEqual(len(game.commands), report["commands"])
        expected = authoritative_state_hash(game.state)
        self.assertEqual(expected, report["final_state_hash"])
        restored = CommanderSession.load(self.db, path)
        self.assertEqual(expected, authoritative_state_hash(restored.state))
        (path / "focused-replay.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    @_capability_validation_snapshot()
    def test_original_cast_attack_link_lifecycle_and_replay(self):
        (game, source, sprite, elf, foreign, private, a_food,
         b_food, nonfood, theft, bounce, _) = self._board(486_150_001)
        self._begin(game, source)
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "targets": [foreign.ref]}).ok)
        self.assertFalse(game.act("pilot:C", {"action_id": "choose", "targets": [sprite.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.target(game, "A", [sprite.ref])
        self.assertIsNone(self.resolve(game))
        original = game.state.cards[source.object_id].logical_object_id
        exiled = game.state.cards[sprite.object_id]
        self.assertEqual("exile", exiled.zone)
        link, = exiled.annotations[LINKED_ABILITY_EXILE_ANNOTATION]
        self.assertEqual(original, link["source"]["logical_object_id"])
        self.assertEqual(exiled.logical_object_id, link["exiled"]["logical_object_id"])
        token, = self._food_tokens(game, "A")
        counter_ability = self._counter_grant(game, a_food)
        self.assertEqual(counter_ability.ability_id, self._counter_grant(game, token).ability_id)
        self.assertFalse(self._grants(game, b_food))
        self.assertFalse(self._grants(game, nonfood))
        self.assertIn("menace", game.engine._combat_keywords(game.state.cards[source.object_id]))

        self.attack(game, [(source, "D")])
        self.target(game, "A", [elf.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, len(self._food_tokens(game, "A")))
        mana_ability, = [ability for ability in self._grants(game, token) if ability.mana_ability]
        self.assertTrue(mana_ability.tap_source)
        self._activate(game, "A", token, mana_ability)
        self.assertTrue(game.state.cards[token.object_id].tapped)
        self.assertEqual(1, game.state.players["A"].mana_pool["G"])

        self.until(game, lambda session: session.state.active_player == "B"
                   and session.state.phase == "precombat_main" and session.state.priority_player == "B")
        self.cast(game, "B", theft, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", game.state.cards[source.object_id].controller)
        self.assertFalse(self._grants(game, a_food))
        self._counter_grant(game, b_food)

        # Unstack the temporary control effect through the ordinary turn transition.
        self.until(game, lambda session: session.state.active_player == "C"
                   and session.state.phase == "precombat_main" and session.state.priority_player == "C")
        self.assertEqual("A", game.state.cards[source.object_id].controller)
        self.assertFalse(self._grants(game, b_food))
        action = self._offer(game, "A", a_food, counter_ability)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": action["id"], "pay": {"G": 1}}).ok)
        self.assertFalse(game.act("pilot:B", {"action_id": action["id"], "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self._activate(game, "A", a_food, counter_ability)
        self.cast(game, "C", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertFalse(self._grants(game, a_food))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[a_food.object_id].counters["+1/+1"])
        self.assertEqual("exile", game.state.cards[sprite.object_id].zone)
        self.assertEqual("exile", game.state.cards[elf.object_id].zone)
        self.assertNotIn(LINKED_ABILITY_EXILE_ANNOTATION, game.state.cards[foreign.object_id].annotations)
        self._record(game, "cast-attack-control-and-resolution")

    @_capability_validation_snapshot()
    def test_invalid_target_stops_the_food_and_zero_target_still_creates_it(self):
        for index, mode in enumerate(("zero", "stale", "noncreature")):
            with self.subTest(mode=mode):
                (game, source, sprite, _, _, _, a_food, _, _, _, _, cremate) = self._board(486_150_002 + index)
                donor = sprite
                if mode == "noncreature":
                    donor = self.add(game.engine, "Thran Dynamo", seat="C", zone="graveyard", ref="LINK-NONCREATURE")
                    self.checkpoint(game)
                self._begin(game, source)
                self.target(game, "A", [] if mode == "zero" else [donor.ref])
                if mode == "stale":
                    self.cast(game, "B", cremate, [donor.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual(0 if mode == "stale" else 1, len(self._food_tokens(game, "A")))
                self.assertFalse(self._grants(game, a_food))
                current = game.state.cards[donor.object_id]
                self.assertEqual("graveyard" if mode == "zero" else "exile", current.zone)
                self.assertEqual(mode == "noncreature", LINKED_ABILITY_EXILE_ANNOTATION in current.annotations)
                self._record(game, {"zero": "zero-target", "stale": "selected-target-lost",
                                    "noncreature": "noncreature-link-without-grant"}[mode])


    @_capability_validation_snapshot()
    def test_donor_reexile_and_source_reentry_do_not_reuse_links(self):
        (game, source, sprite, _, _, _, a_food, _, _, _, bounce, cremate) = self._board(486_150_006)
        pull = self.add(game.engine, "Pull from Eternity", seat="C", zone="hand", ref="LINK-PULL")
        self.add(game.engine, "Plains", seat="C", ref="LINK-WHITE")
        self.assert_closed(game, pull)
        self.checkpoint(game)
        self._begin(game, source)
        self.target(game, "A", [sprite.ref])
        self.assertIsNone(self.resolve(game))
        grant = self._counter_grant(game, a_food)
        original_source = game.state.cards[source.object_id].logical_object_id
        original_exile = game.state.cards[sprite.object_id].logical_object_id
        stale_action = self._offer(game, "A", a_food, grant)["id"]

        self.cast(game, "C", pull, [sprite.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[sprite.object_id].zone)
        self.assertFalse(self._grants(game, a_food))
        self.cast(game, "B", cremate, [sprite.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[sprite.object_id].zone)
        self.assertNotEqual(original_exile, game.state.cards[sprite.object_id].logical_object_id)
        self.assertFalse(self._grants(game, a_food))
        self.priority(game, "A")
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": stale_action, "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        self.cast(game, "C", bounce, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("A", "precombat_main"), (game.state.active_player, game.state.phase))
        self._begin(game, source)
        self.target(game, "A", [])
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual(original_source, game.state.cards[source.object_id].logical_object_id)
        self.assertFalse(self._grants(game, a_food))
        self._record(game, "exile-incarnation-and-source-reentry")

    @_capability_validation_snapshot()
    def test_bound_donor_departure_keeps_paid_recipient_source_and_replays(self):
        (game, source, sprite, _, _, _, food, _, _, _, _, _) = self._board(486_150_008)
        pull = self.add(game.engine, "Pull from Eternity", seat="C", zone="hand", ref="ACTIVE-DONOR-PULL")
        self.add(game.engine, "Plains", seat="C", ref="ACTIVE-DONOR-PULL-PRICE")
        self.assert_closed(game, pull)
        self.checkpoint(game)
        self._begin(game, source)
        self.target(game, "A", [sprite.ref])
        self.assertIsNone(self.resolve(game))

        donor = game.state.cards[sprite.object_id]
        original, = [ability for ability in game.engine._activated_abilities(donor) if ability.cost_text == "{7}"]
        link, = donor.annotations[LINKED_ABILITY_EXILE_ANNOTATION]
        identity = ":".join((link["source"]["logical_object_id"], link["ability_pair"],
                             link["exiled"]["logical_object_id"], original.ability_id))
        historical_id = f"linked-exile:{sha256(identity.encode('utf-8')).hexdigest()[:24]}:{original.ability_id}"
        grant = self._counter_grant(game, food)
        self.assertEqual(historical_id, grant.ability_id)
        donor_key = game.engine._semantic_key_for_ability(donor, original)
        action = self._activate(game, "A", food, grant)
        pending = game.state.stack[-1]
        self.assertEqual((food.object_id, donor_key), (pending.source_object_id, pending.semantic_key))
        activation = next(event for event in reversed(game.state.events)
                          if event.code == "stack.activate" and event.details.get("stack") == pending.ref)
        self.assertEqual(food.ref, activation.details["source"])
        self.assertEqual(7, sum(activation.details["payment"].values()))

        self.cast(game, "C", pull, [sprite.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[sprite.object_id].zone)
        self.assertFalse(self._grants(game, food))
        self.assertEqual(pending.ref, game.state.stack[-1].ref)
        self.priority(game, "A")
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": action["id"], "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[food.object_id].counters.get("+1/+1"))
        self.assertEqual(0, game.state.cards[sprite.object_id].counters.get("+1/+1", 0))
        self._record(game, "paid-recipient-after-donor-departure")

    @_capability_validation_snapshot()
    def test_departed_source_still_exiles_but_new_entry_cannot_read_its_link(self):
        (game, source, sprite, _, _, _, a_food, _, _, _, bounce, _) = self._board(486_150_007)
        self._begin(game, source)
        self.target(game, "A", [sprite.ref])
        old_source = game.state.cards[source.object_id].logical_object_id
        self.cast(game, "C", bounce, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.assertEqual(1, len(self._food_tokens(game, "A")))
        self.assertFalse(self._grants(game, a_food))
        exiled = game.state.cards[sprite.object_id]
        self.assertEqual("exile", exiled.zone)
        link, = exiled.annotations[LINKED_ABILITY_EXILE_ANNOTATION]
        self.assertEqual(old_source, link["source"]["logical_object_id"])

        self.assertEqual(("A", "precombat_main"), (game.state.active_player, game.state.phase))
        self._begin(game, source)
        self.target(game, "A", [])
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual(old_source, game.state.cards[source.object_id].logical_object_id)
        self.assertFalse(self._grants(game, a_food))
        self._record(game, "early-source-departure-and-reentry")

    @_capability_validation_snapshot()
    def test_missing_link_authority_and_wrong_pair_leave_original_unresolved(self):
        record = self.db.by_oracle_id("e8180024-1979-4677-9a0d-e08d4b7c825a")
        self.assertEqual(self.source, record.name)
        profile = "commander_review"
        program = compile_card_program(self.db, record, capability_registry=self.registry,
                                       capability_profile=profile, trust_level="trusted")
        bound = bind_card_program_runtime(program, capability_registry=self.registry, profile=profile)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        next(row for row in raw["capabilities"] if row["id"] == "zone.exile.linked_ability_pair").update(
            status="blocked", blockers=["test withdrawal"],
        )
        blocked = CapabilityRegistry(raw)
        index = read_evidence_document(DEFAULT_CAPABILITY_EVIDENCE)
        index["registry_fingerprint"] = blocked.fingerprint
        index["fingerprint"] = capability_evidence_fingerprint(index)
        blocked.mark_evidence_verified(validate_capability_evidence_index(index, registry=blocked))
        unrelated = self.db.lookup("Llanowar Elves", fuzzy=False)
        self.assertEqual("exact", compile_oracle_card(
            unrelated, capability_registry=blocked, capability_profile=profile,
        ).status)
        self.assertFalse(blocked.closure((LINKED_EXILE_ACTIVATION_CAPABILITY,), profile=profile).trusted)
        result = compile_oracle_card(record, capability_registry=blocked, capability_profile=profile)
        self.assertEqual(1, len(result.material_residuals))
        self.assertIn("Foods you control", result.material_residuals[0].text)
        trigger, = [ability for ability in program.abilities if ability.event == "permanent.enter"]
        wrong = replace(trigger, effects=({**trigger.effects[0], LINKED_ACTIVATION_BINDING: "malformed"},
                                          trigger.effects[1]))
        checks = bind_semantic_program_runtime(wrong, capability_registry=self.registry, profile=profile)
        self.assertFalse(checks["strict"], checks)

    @_capability_validation_snapshot()
    def test_actual_exile_link_omission_mutation_is_killed(self):
        for omit in (False, True):
            with self.subTest(omit_receipt=omit):
                game, source, sprite, _, _, _, food, *_ = self._board(486_150_004 + omit)
                self._begin(game, source)
                self.target(game, "A", [sprite.ref])

                def resolve_and_check_grant():
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual("exile", game.state.cards[sprite.object_id].zone)
                    self.assertEqual(1, len(self._food_tokens(game, "A")))
                    self.assertTrue(self._grants(game, food), "linked activation was not offered on the recipient")

                if omit:
                    with patch("quorune.linked_ability_exile_state.record_linked_ability_exile", return_value=()):
                        with self.assertRaisesRegex(AssertionError, "linked activation was not offered"):
                            resolve_and_check_grant()
                else:
                    resolve_and_check_grant()
