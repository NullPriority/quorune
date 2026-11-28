"""Arcbond through ordinary cast, delayed trigger, damage, and replay owners."""

from dataclasses import replace
from hashlib import file_digest
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.event_damage_source_model import EVENT_DAMAGE_SOURCE_CONTEXT, EventDamageSourceInformation
from quorune.oracle_ir import compile_oracle_card
from quorune.received_damage_model import RECEIVED_DAMAGE_WATCH_FIELD, ReceivedDamageWatch
from quorune.record import authoritative_state_hash, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.received_damage_watch import WATCHED_DAMAGE_CAPABILITY
from scripts.build_test_database import build_fixture_database


ORIGINAL = "Arcbond"
PROFILE = "commander_review"


class ReceivedDamageWatchTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "watched-damage.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database(
            [fixtures / "bound-effect-program-cards.json", fixtures / "received-damage-watch-cards.json"], path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Repeated received damage",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30)], [commander])

    def _record(self, game, name):
        directory = os.environ.get("QUORUNE_ARCBOND_RECORD_PARENT")
        if directory is None:
            self.replay(game, load=True)
            return
        path = Path(directory) / name
        game.save(path)
        result = replay_record(path, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(authoritative_state_hash(game.state), result["final_state_hash"])
        with (path / "manifest.json").open("rb") as handle:
            digest = file_digest(handle, "sha256").hexdigest()
        receipt = {"manifest_sha256": digest, "commands": len(game.commands),
                   "final_state_sha256": result["final_state_hash"]}
        path.with_suffix(".receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    def _cast(self, game, actor, spell, *targets, reserve=None):
        refs = [value if isinstance(value, str) else value.ref for value in targets]
        self.cast_for(game, actor, spell, refs, reserve=reserve)
        self.resolve_top(game)

    def _watch(self, game, controller, spell, creature, *, reserve=None):
        self._cast(game, controller, spell, creature, reserve=reserve)
        watch = game.state.delayed_triggers[-1]
        info = ReceivedDamageWatch.from_trigger(watch)
        self.assertEqual((controller, creature.object_id, creature.logical_object_id),
                         (watch.controller, info.object_id, info.logical_object_id))
        self.assertEqual(spell.object_id, watch.source_object_id)
        self.assertFalse(watch.once)
        self.assertEqual(game.state.turn_sequence, watch.expires_turn_sequence)
        return watch

    @staticmethod
    def _triggers(game):
        return [item for item in game.state.stack if EVENT_DAMAGE_SOURCE_CONTEXT in item.context
                and "received_damage_watch_source_stack" in item.context]

    def _expected_trigger(self, game, watch, creature_id, amount):
        (item,) = self._triggers(game)
        self.assertEqual((watch.ref, watch.controller, watch.source_object_id, []),
                         (item.context["delayed_trigger_ref"], item.controller, item.source_object_id, item.targets))
        info = EventDamageSourceInformation.from_dict(item.context[EVENT_DAMAGE_SOURCE_CONTEXT])
        self.assertEqual((creature_id, amount), (info.source_snapshot.logical_object_id, info.received_amount))
        return info

    @staticmethod
    def _life(game):
        return [game.state.players[seat].life for seat in "ABCD"]

    def _advance_turn(self, game):
        turn = game.state.turn_sequence
        while game.state.turn_sequence == turn:
            self.until(game, lambda current: current.state.turn_sequence != turn or (
                current.state.pending_decision is not None and current.state.pending_decision.kind == "cleanup.discard"
            ))
            if game.state.turn_sequence == turn:
                principal = game.pending_principals()[0]
                decision = game.packet(principal, full=True)["decision"]["ctx"]
                result = game.act(principal, {"action_id": "discard",
                                  "cards": [row["id"] for row in decision["hand"][:decision["count"]]]})
                self.assertTrue(result.ok, result.summary)

    def test_watch_keeps_spell_controller_across_control_change_and_two_receipts(self):
        game = self.session(61127031)
        arcbond = self.add(game.engine, ORIGINAL, seat="B", zone="hand")
        watched = self.add(game.engine, "Ancient Brontodon", seat="C")
        other = self.add(game.engine, "Grizzly Bears", seat="D")
        theft = self.add(game.engine, "Act of Treason", zone="hand")
        first = self.add(game.engine, "Shock", seat="B", zone="hand", ref="watch-first")
        second = self.add(game.engine, "Shock", seat="C", zone="hand", ref="watch-second")
        for card in (arcbond, theft, first, second):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "R": 1})
        game.state.players["B"].mana_pool.update({"C": 2, "R": 2})
        game.state.players["C"].mana_pool["R"] = 1
        self.checkpoint(game)
        incarnation = watched.logical_object_id
        watch = self._watch(game, "B", arcbond, watched, reserve={"R": 1})
        self.assertEqual("C", game.state.cards[watched.object_id].controller)
        self.assertNotIn(second.ref, json.dumps(game.packet("pilot:A", full=True)))
        self._cast(game, "A", theft, watched)
        self.assertEqual(("A", incarnation), (game.state.cards[watched.object_id].controller,
                                             game.state.cards[watched.object_id].logical_object_id))
        self._cast(game, "B", first, watched)
        info = self._expected_trigger(game, watch, incarnation, 2)
        self.assertEqual("A", info.source_snapshot.controller)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([38] * 4, self._life(game))
        self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
        (batch,) = (event for event in game.state.events if event.code == "effect.damage.fixed_set")
        self.assertEqual((2, 5), (batch.details["assigned_amount"], batch.details["recipient_count"]))
        self.assertEqual({other.ref, "A", "B", "C", "D"},
                         {event["target"] for event in batch.details["damage_events"]})
        self._cast(game, "C", second, watched)
        self._expected_trigger(game, watch, incarnation, 2)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([36] * 4, self._life(game))
        self.assertEqual(4, game.state.cards[watched.object_id].marked_damage)
        outgoing = [event for event in game.state.turn_history.events
                    if event.kind == "player_damaged" and event.object_incarnation == incarnation]
        self.assertEqual(8, len(outgoing))
        self.assertTrue(all(event.actor == "A" and event.amount == 2 and "history:combat" not in event.types
                            for event in outgoing))
        self._record(game, "arcbond-control-two-receipts")

    def test_ability_loss_preserves_watch_and_lethal_source_uses_real_lki(self):
        game = self.session(61127032)
        arcbond = self.add(game.engine, ORIGINAL, seat="B", zone="hand")
        watched = self.add(game.engine, "Gifted Aetherborn")
        humble = self.add(game.engine, "Humble", zone="hand")
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        for card in (arcbond, humble, bolt):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 1})
        game.state.players["B"].mana_pool.update({"C": 2, "R": 2})
        self.checkpoint(game)
        incarnation = watched.logical_object_id
        watch = self._watch(game, "B", arcbond, watched, reserve={"R": 1})
        self._cast(game, "A", humble, watched)
        self.assertNotIn("Lifelink", game.engine._effective_card_data(watched)["keywords"])
        self._cast(game, "B", bolt, watched)
        self._expected_trigger(game, watch, incarnation, 3)
        self.assertEqual("graveyard", game.state.cards[watched.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([37] * 4, self._life(game))
        outgoing = [event for event in game.state.turn_history.events
                    if event.kind == "player_damaged" and event.object_incarnation == incarnation]
        self.assertEqual({"A", "B", "C", "D"}, {event.target for event in outgoing})
        self.assertTrue(all(event.amount == 3 and event.actor == "A" for event in outgoing))
        self.assertFalse(any(event.kind == "player_gained_life" for event in game.state.turn_history.events))
        self._record(game, "arcbond-loss-and-lethal")

    def test_blink_excludes_rewatch_and_old_pending_blast_hits_new_incarnation(self):
        game = self.session(61127033)
        arcbond = self.add(game.engine, ORIGINAL, zone="hand")
        watched = self.add(game.engine, "Ancient Brontodon")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        first = self.add(game.engine, "Shock", zone="hand", ref="blink-first-shock")
        second = self.add(game.engine, "Shock", zone="hand", ref="blink-second-shock")
        for card in (arcbond, blink, first, second):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "R": 3, "W": 1})
        self.checkpoint(game)
        original = watched.logical_object_id
        watch = self._watch(game, "A", arcbond, watched, reserve={"R": 2, "W": 1})
        self._cast(game, "A", first, watched)
        self._expected_trigger(game, watch, original, 2)
        self._cast(game, "A", blink, watched)
        returned = game.state.cards[watched.object_id]
        self.assertNotEqual(original, returned.logical_object_id)
        self._cast(game, "A", second, returned)
        self._expected_trigger(game, watch, original, 2)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, game.state.cards[watched.object_id].marked_damage)
        self.assertEqual([38] * 4, self._life(game))
        self.assertFalse(self._triggers(game))
        self._record(game, "arcbond-blink-incarnation")

    def test_fully_prevented_damage_never_queues_and_cleanup_expires_a_live_watch(self):
        game = self.session(61127034)
        arcbond = self.add(game.engine, ORIGINAL, zone="hand")
        watched = self.add(game.engine, "Ancient Brontodon")
        passage = self.add(game.engine, "Safe Passage", zone="hand")
        first = self.add(game.engine, "Lightning Bolt", zone="hand", ref="prevented-watch-bolt")
        after_cleanup = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="expired-watch-bolt")
        self.add(game.engine, "Mountain", seat="B")
        for card in (arcbond, passage, first, after_cleanup):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "R": 2, "W": 1})
        self.checkpoint(game)
        watch = self._watch(game, "A", arcbond, watched, reserve={"C": 2, "R": 1, "W": 1})
        self._cast(game, "A", passage, reserve={"R": 1})
        self._cast(game, "A", first, watched)
        self.assertFalse(self._triggers(game))
        self.assertEqual(0, game.state.cards[watched.object_id].marked_damage)
        self._advance_turn(game)
        self._cast(game, "B", after_cleanup, watched)
        self.assertFalse(any(item.active and item.ref == watch.ref for item in game.state.delayed_triggers))
        self.assertFalse(self._triggers(game))
        self.assertEqual(3, game.state.cards[watched.object_id].marked_damage)
        self.assertEqual([40] * 4, self._life(game))
        self._record(game, "arcbond-prevention-expiry")

    def test_grouped_combat_damage_fires_once_and_deals_noncombat_damage(self):
        game = self.session(61127037)
        arcbond = self.add(game.engine, ORIGINAL, zone="hand")
        watched = self.add(game.engine, "Steel Wall", seat="B")
        valor = self.add(game.engine, "Valor Made Real", seat="B", zone="hand")
        attackers = [self.add(game.engine, "Grizzly Bears", ref=f"arcbond-combat-{index}")
                     for index in (1, 2)]
        for attacker in attackers:
            attacker.acquired_control_turn_count = -1
        self.prepare(game, mana={"C": 2, "R": 1})
        game.state.players["B"].mana_pool["W"] = 1
        self.checkpoint(game)
        incarnation = watched.logical_object_id
        watch = self._watch(game, "A", arcbond, watched)
        self._cast(game, "B", valor, watched)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        action = game.act("pilot:A", {"action_id": "attack", "attackers": {card.ref: "B" for card in attackers}})
        self.assertTrue(action.ok, action.summary)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        block = game.act("pilot:B", {"action_id": "block", "blocks": {watched.ref: [card.ref for card in attackers]}})
        self.assertTrue(block.ok, block.summary)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.damage")
        assignment = game.act("pilot:B", {"action_id": "assign_damage", "assignments": []})
        self.assertTrue(assignment.ok, assignment.summary)
        self._expected_trigger(game, watch, incarnation, 4)
        self.assertEqual("graveyard", game.state.cards[watched.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([36] * 4, self._life(game))
        damage = [event for event in game.state.turn_history.events if event.kind == "player_damaged"]
        self.assertEqual(4, len(damage))
        self.assertTrue(all(event.object_incarnation == incarnation and event.amount == 4
                            and "history:combat" not in event.types for event in damage))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in attackers))
        self._record(game, "arcbond-grouped-combat")

    def test_separate_watches_form_the_finite_reciprocal_sequence(self):
        game = self.session(61127035)
        first = self.add(game.engine, ORIGINAL, zone="hand", ref="reciprocal-arcbond-a")
        second = self.add(game.engine, ORIGINAL, zone="hand", ref="reciprocal-arcbond-b")
        bear_a = self.add(game.engine, "Grizzly Bears", seat="B", ref="reciprocal-bear-b")
        bear_b = self.add(game.engine, "Grizzly Bears", seat="C", ref="reciprocal-bear-c")
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand")
        self.prepare(game, mana={"C": 4, "R": 3})
        self.checkpoint(game)
        first_incarnation, second_incarnation = bear_a.logical_object_id, bear_b.logical_object_id
        first_watch = self._watch(game, "A", first, bear_a, reserve={"C": 2, "R": 2})
        second_watch = self._watch(game, "A", second, bear_b, reserve={"R": 1})
        self._cast(game, "A", bolt, bear_a)
        self._expected_trigger(game, first_watch, first_incarnation, 3)
        self.resolve_top(game)
        self._expected_trigger(game, second_watch, second_incarnation, 3)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([34] * 4, self._life(game))
        self.assertEqual(["graveyard", "graveyard"], [game.state.cards[card.object_id].zone for card in (bear_a, bear_b)])
        self._record(game, "arcbond-finite-reciprocal")

    def test_dropping_logical_identity_mutant_rewatches_the_blinked_object(self):
        def physical_only(watch):
            return {"target_kind": "permanent", "target_object_id": watch.object_id}

        with patch.object(ReceivedDamageWatch, "condition", property(physical_only)):
            with self.assertRaisesRegex(ValueError, "too many values to unpack"):
                self.test_blink_excludes_rewatch_and_old_pending_blast_hits_new_incarnation()

    def test_exact_spell_requires_one_creature_target_and_its_watch_authority(self):
        game = self.session(61127036)
        arcbond = self.add(game.engine, ORIGINAL, zone="hand")
        self.assert_closed(game, arcbond)
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        (program,) = game.engine.semantics.programs_for_oracle(record.oracle_id)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status)
        (node,) = ir.faces[0].nodes
        self.assertEqual((0, len(record.oracle_text)), (node.span.start, node.span.end))
        dependencies = tuple(dep for dep in program.capability_dependencies if dep != WATCHED_DAMAGE_CAPABILITY)
        (effect,) = program.effects
        invalid = {**effect, RECEIVED_DAMAGE_WATCH_FIELD: {"schema_version": True, "creature": "$target.0"}}
        for changed in (
            replace(program, capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict()),
            replace(program, effects=(invalid,)),
            replace(program, target_schema=None),
        ):
            result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"], result)
        different = replace(record, oracle_text=record.oracle_text.replace("this turn", "this game"))
        other = compile_oracle_card(different, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertTrue(other.material_residuals)
