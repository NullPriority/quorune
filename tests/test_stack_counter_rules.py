from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
import test_life_gain_prohibitions as native
from quorune import stack_counter as stack_counter_module
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry, DeckLoader
from quorune.errors import GameRuleError
from quorune.model import StackItem
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.record_trust import implicit_semantic_execution_provenance
from quorune.rules.event_spell_copy import capture_event_spell_copy_source
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.semantic_runtime import (
    CounterStackIntent,
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
    execute_intent_plan,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.stack_counter_handlers import (
    CounterStackTargetHandler,
)
from quorune.semantics import SemanticProgram
from quorune.stack_counter import (
    INTRINSIC_COUNTER_PROHIBITION_CAPABILITY,
    oracle_has_intrinsic_counter_prohibition,
)
from quorune.stack_resolution import trusted_generic_empty_resolution
from scripts.build_test_database import build_fixture_database


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "stack-counter-rules.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "targeted-counter-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class StackCounterRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 4):
        mishra = copy.deepcopy(self.mishra)
        zimone = copy.deepcopy(self.zimone)
        next(
            entry for entry in mishra.entries if entry.board == "mainboard"
        ).name = "Counterspell"
        next(
            entry for entry in zimone.entries if entry.board == "mainboard"
        ).name = "Unanswerable Test Spell"
        session = make_session(
            self.db,
            mishra,
            zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    def put_spell_on_stack(
        self,
        engine,
        seat: str,
        name: str,
        *,
        ref: str,
    ) -> StackItem:
        card = self.card(engine, seat, name)
        engine._remove_from_zone(card)
        card.zone = "stack"
        card.controller = seat
        card.known_to = list(engine.seats)
        card.revealed_to = list(engine.seats)
        item = StackItem(
            stack_id=f"test-{ref}",
            ref=ref,
            kind="spell",
            controller=seat,
            label=name,
            card_object_id=card.object_id,
            default_destination=(
                "battlefield"
                if self.db.lookup(name).is_permanent_spell
                else "graveyard"
            ),
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        return item

    @staticmethod
    def pass_stack(session):
        while session.state.stack:
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Stack resolution stopped without priority")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)

    def assert_replays(self, session, label: str):
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / label
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_handler_lowers_one_strict_typed_counter_intent(self):
        context = ReadOnlyHandlerContext(
            actor="A",
            default_reason="counter fixture",
            query=ReadOnlyRulesQuery(
                seats=("A", "B", "C", "D"),
                active_seats=("A", "B", "C", "D"),
                apnap_order=("B", "C", "D", "A"),
            ),
        )
        plan = CounterStackTargetHandler().lower(
            {"op": "counter_stack_target", "stack": "S12"},
            context,
        )
        self.assertEqual("generic.counter-stack-target.v1", plan.handler_id)
        self.assertEqual(
            (
                CounterStackIntent(
                    actor="A",
                    stack_ref="S12",
                    reason="counter fixture",
                    countered_by="A",
                ),
            ),
            plan.intents,
        )
        for malformed in (
            {"op": "counter_stack_target", "stack": ""},
            {"op": "counter_stack_target", "stack": 12},
            {
                "op": "counter_stack_target",
                "stack": "S12",
                "reason": 4,
            },
            {
                "op": "counter_stack_target",
                "stack": "S12",
                "destination": "exile",
            },
        ):
            with self.subTest(effect=malformed):
                with self.assertRaises(SemanticNodeError):
                    CounterStackTargetHandler().lower(malformed, context)

    def test_compiled_counter_targets_exact_stack_domains_and_replays(self):
        session = self.session(7012701)
        engine = session.engine
        source = self.card(engine, "A", "Counterspell")
        target = self.put_spell_on_stack(
            engine,
            "B",
            "Birds of Paradise",
            ref="S-counter-target",
        )
        engine.move_card(source.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool["U"] = 2
        engine.state.priority_player = "A"
        hints = engine._priority_action_hints("A")
        action = next(
            row for row in hints["actions"] if row.get("card") == source.ref
        )

        self.assertEqual("cast", action["action"])
        self.assertEqual([target.ref], action["target_schema"]["legal_refs"])
        engine._issue_priority("A", hints)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"U": 2},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        with patch.object(
            engine,
            "_dispatch_semantic_event",
            wraps=engine._dispatch_semantic_event,
        ) as dispatch:
            self.pass_stack(session)

        self.assertEqual("graveyard", engine.state.cards[target.card_object_id].zone)
        self.assertEqual("graveyard", source.zone)
        normalized_calls = [
            call.args[0]
            for call in dispatch.call_args_list
            if call.args[0] in {"spell.countered", "card.graveyard"}
        ]
        self.assertEqual(
            ["spell.countered", "card.graveyard"],
            normalized_calls,
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "stack.counter"
        )
        self.assertEqual(target.ref, event.details["stack"])
        self.assertEqual("effect", event.details["counter_kind"])
        for seat in engine.seats:
            projected = StateProjector(self.db, engine.state)._snapshot(
                f"pilot:{seat}"
            )
            self.assertIn(
                engine.state.cards[target.card_object_id].ref,
                {
                    row["id"]
                    for row in projected["players"]["B"]["gy"]
                },
            )
        projected_d = json.dumps(
            StateProjector(self.db, engine.state)._snapshot("pilot:D"),
            sort_keys=True,
        )
        private_refs = {
            engine.state.cards[object_id].ref
            for object_id in engine.state.players["B"].zones["hand"]
        }
        self.assertTrue(all(ref not in projected_d for ref in private_refs))
        self.assert_replays(session, "typed-stack-counter-record")

    def test_countered_ability_leaves_card_zones_unchanged(self):
        session = self.session(7012702)
        engine = session.engine
        ability = StackItem(
            stack_id="test-activated-ability",
            ref="S-activated",
            kind="activated_ability",
            controller="B",
            label="Test activated ability",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(ability)
        before_zones = {
            seat: copy.deepcopy(player.zones)
            for seat, player in engine.state.players.items()
        }
        plan = CounterStackTargetHandler().lower(
            {"op": "counter_stack_target", "stack": ability.ref},
            ReadOnlyHandlerContext(
                actor="A",
                default_reason="counter activated ability",
                query=ReadOnlyRulesQuery(
                    seats=tuple(engine.seats),
                    active_seats=tuple(engine.active_seats),
                    apnap_order=tuple(engine.apnap_order()),
                ),
            ),
        )

        with patch.object(
            engine,
            "_dispatch_semantic_event",
            wraps=engine._dispatch_semantic_event,
        ) as dispatch:
            execute_intent_plan(engine, plan)

        self.assertNotIn(ability, engine.state.stack)
        self.assertEqual(
            before_zones,
            {seat: player.zones for seat, player in engine.state.players.items()},
        )
        self.assertFalse(
            any(
                call.args[0] in {"spell.countered", "card.graveyard"}
                for call in dispatch.call_args_list
            )
        )

    def test_counter_destination_replacement_emits_only_pre_counter_event(self):
        session = self.session(7012711, players=2)
        engine = session.engine
        target = self.put_spell_on_stack(
            engine,
            "A",
            "Counterspell",
            ref="S-counter-to-exile",
        )
        source = self.card(engine, "B", "Birds of Paradise")
        engine.move_card(source.object_id, "battlefield", controller="B")
        engine.semantics.put(
            SemanticProgram(
                key="test:counter-destination-replacement",
                label="Replace counter graveyard destination",
                oracle_id=source.oracle_id,
                ability_id="static:front:counter-destination",
                active_zone="battlefield",
                event="zone.change",
                trust_level="provisional",
                handlers=[
                    {
                        "handler_id": "replacement.zone.destination.v1",
                        "schema_version": 1,
                        "event": "zone.change",
                        "condition": {
                            "destination": "graveyard",
                            "object_kind": "card",
                            "owner_relation": "opponent",
                        },
                        "destination": "exile",
                        "counters": {},
                    }
                ],
            )
        )

        with patch.object(
            type(engine),
            "semantic_program_is_current_trusted",
            return_value=True,
        ), patch.object(
            engine,
            "_dispatch_semantic_event",
            wraps=engine._dispatch_semantic_event,
        ) as dispatch:
            engine._counter_stack_item(
                target.ref,
                as_rule=True,
                countered_by="B",
                reason="counter destination replacement witness",
            )

        self.assertEqual("exile", engine.state.cards[target.card_object_id].zone)
        normalized_calls = [
            call.args[0]
            for call in dispatch.call_args_list
            if call.args[0] in {"spell.countered", "card.graveyard"}
        ]
        self.assertEqual(["spell.countered"], normalized_calls)

    def test_counter_source_cannot_target_its_own_stack_object(self):
        session = self.session(7012703, players=2)
        engine = session.engine
        source = self.card(engine, "A", "Counterspell")
        engine._remove_from_zone(source)
        source.zone = "stack"
        source.controller = "A"
        program = engine.semantics.get(f"{source.oracle_id}:spell:front")
        self.assertIsNotNone(program)
        assert program is not None
        source_item = StackItem(
            stack_id="test-counter-source",
            ref="S-counter-source",
            kind="spell",
            controller="A",
            label=source.printed_name,
            card_object_id=source.object_id,
            semantic_key=program.key,
            visibility=list(engine.seats),
        )
        engine.state.stack.append(source_item)

        with self.assertRaises(GameRuleError):
            engine._validate_semantic_targets(
                "A",
                program,
                [source_item.ref],
                source_ref=source_item.ref,
            )

    def test_intrinsic_uncounterable_spell_survives_typed_counter(self):
        session = self.session(7012704, players=2)
        engine = session.engine
        spell = self.card(engine, "B", "Unanswerable Test Spell")
        counter = self.card(engine, "A", "Counterspell")
        engine.move_card(spell.object_id, "hand", log=False)
        engine.state.active_player = "B"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "B"
        engine.state.players["B"].mana_pool["U"] = 1

        engine._cast(
            "B",
            {
                "card": spell.ref,
                "pay": "manual",
                "payment": {"U": 1},
            },
        )
        item = engine.state.stack[-1]
        self.assertTrue(item.context["cant_be_countered"])
        empty = trusted_generic_empty_resolution(engine, item, engine.semantics.get(item.semantic_key))
        self.assertEqual("trusted_intrinsic_empty_spell", empty.provenance)
        engine.move_card(counter.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool["U"] = 2
        engine.state.priority_player = "A"
        hints = engine._priority_action_hints("A")
        action = next(
            row for row in hints["actions"] if row.get("card") == counter.ref
        )
        self.assertEqual([item.ref], action["target_schema"]["legal_refs"])
        engine._issue_priority("A", hints)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [item.ref],
                "pay": "manual",
                "payment": {"U": 2},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_stack(session)

        self.assertEqual("graveyard", spell.zone)
        self.assertTrue(
            any(
                event.code == "stack.counter.failed"
                and event.details.get("stack") == item.ref
                for event in engine.state.events
            )
        )
        self.assert_replays(session, "intrinsic-counter-prohibition-record")

    def test_temporary_spell_prohibition_does_not_protect_abilities(self):
        session = self.session(7012705, players=2)
        engine = session.engine
        engine.state.players["B"].stats[
            "spells_cant_be_countered_until_end"
        ] = True
        ability = StackItem(
            stack_id="temporary-prohibition-ability",
            ref="S-triggered",
            kind="triggered_ability",
            controller="B",
            label="Test triggered ability",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(ability)

        engine._counter_stack_item(
            ability.ref,
            reason="ability counter witness",
            countered_by="A",
        )

        self.assertNotIn(ability, engine.state.stack)

    def test_counter_target_that_leaves_stack_invalidates_resolution(self):
        session = self.session(7012706, players=2)
        engine = session.engine
        source = self.card(engine, "A", "Counterspell")
        target = self.put_spell_on_stack(
            engine,
            "B",
            "Birds of Paradise",
            ref="S-stale-target",
        )
        engine._remove_from_zone(source)
        source.zone = "stack"
        source.controller = "A"
        program = engine.semantics.get(f"{source.oracle_id}:spell:front")
        assert program is not None
        selected, grouped = engine._validate_semantic_targets(
            "A", program, [target.ref], source_ref="S-counter-stale"
        )
        counter_item = StackItem(
            stack_id="counter-stale-target",
            ref="S-counter-stale",
            kind="spell",
            controller="A",
            label=source.printed_name,
            card_object_id=source.object_id,
            semantic_key=program.key,
            targets=selected,
            default_destination="graveyard",
            visibility=list(engine.seats),
            context={
                "target_groups": grouped,
                "target_snapshots": {
                    target.ref: engine._target_snapshot(target.ref)
                },
                "targets_revalidated": False,
            },
        )
        engine.state.stack.append(counter_item)
        with patch.object(
            engine,
            "_dispatch_semantic_event",
            wraps=engine._dispatch_semantic_event,
        ) as dispatch:
            engine._counter_stack_item(
                target.ref,
                as_rule=True,
                countered_by="B",
                reason="target left before resolution",
            )
            engine.state.priority_player = None

            engine._prepare_stack_resolution()

        self.assertEqual("graveyard", source.zone)
        self.assertNotIn(counter_item, engine.state.stack)
        normalized_calls = [
            call.args[0]
            for call in dispatch.call_args_list
            if call.args[0] in {"spell.countered", "card.graveyard"}
        ]
        self.assertEqual(
            [
                "spell.countered",
                "card.graveyard",
                "spell.countered",
                "card.graveyard",
            ],
            normalized_calls,
        )
        self.assertTrue(
            any(
                event.code == "target.illegal"
                and event.details.get("target") == target.ref
                for event in engine.state.events
            )
        )

    def test_untrusted_counter_prohibition_declaration_is_ignored(self):
        session = self.session(7012707, players=2)
        engine = session.engine
        spell = self.card(engine, "B", "Unanswerable Test Spell")
        for program in engine.semantics.programs_for_oracle(spell.oracle_id):
            engine.semantics.remove(program.key)
        engine.semantics.put(
            SemanticProgram(
                key="test:untrusted-counter-prohibition",
                label="Untrusted counter prohibition",
                oracle_id=spell.oracle_id,
                ability_id="static:front:test",
                active_zone="stack",
                event="continuous",
                trust_level="provisional",
                capability_dependencies=[
                    INTRINSIC_COUNTER_PROHIBITION_CAPABILITY
                ],
                capability_closure=load_default_capability_registry()
                .closure(
                    [INTRINSIC_COUNTER_PROHIBITION_CAPABILITY],
                    profile=engine.state.config.review_profile,
                )
                .to_dict(),
            )
        )

        self.assertFalse(
            oracle_has_intrinsic_counter_prohibition(
                engine.semantics,
                spell.oracle_id,
                current_trusted=engine.semantic_program_is_current_trusted,
            )
        )

    def test_missing_stack_target_rolls_back_without_mutation(self):
        session = self.session(7012708, players=3)
        engine = session.engine
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(GameRuleError, "No stack object"):
            engine._counter_stack_item(
                "S-missing",
                reason="rollback witness",
                countered_by="A",
            )

        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_stack_counter_owner_mutants_are_killed(self):
        session = self.session(7012709, players=2)
        engine = session.engine
        target = self.put_spell_on_stack(
            engine,
            "B",
            "Birds of Paradise",
            ref="S-mutation-target",
        )

        with patch.object(
            stack_counter_module,
            "stack_item_can_be_countered",
            return_value=False,
        ):
            engine._counter_stack_item(
                target.ref,
                reason="mutation witness",
                countered_by="A",
            )
        self.assertIn(target, engine.state.stack)

        engine._counter_stack_item(
            target.ref,
            reason="owner witness",
            countered_by="A",
        )
        self.assertNotIn(target, engine.state.stack)
        self.assertEqual(
            "graveyard", engine.state.cards[target.card_object_id].zone
        )

    def test_counter_event_derivation_mutant_is_killed(self):
        mutated = self.session(7012712, players=2)
        mutated_target = self.put_spell_on_stack(
            mutated.engine,
            "B",
            "Birds of Paradise",
            ref="S-event-mutation-target",
        )
        with patch(
            "quorune.zone_trigger_processing."
            "normalized_zone_trigger_events",
            return_value=(),
        ), patch.object(
            mutated.engine,
            "_dispatch_semantic_event",
            wraps=mutated.engine._dispatch_semantic_event,
        ) as mutated_dispatch:
            mutated.engine._counter_stack_item(
                mutated_target.ref,
                reason="event mutation witness",
                countered_by="A",
            )
        self.assertFalse(
            any(
                call.args[0] in {"spell.countered", "card.graveyard"}
                for call in mutated_dispatch.call_args_list
            )
        )

        canonical = self.session(7012713, players=2)
        canonical_target = self.put_spell_on_stack(
            canonical.engine,
            "B",
            "Birds of Paradise",
            ref="S-event-owner-target",
        )
        with patch.object(
            canonical.engine,
            "_dispatch_semantic_event",
            wraps=canonical.engine._dispatch_semantic_event,
        ) as canonical_dispatch:
            canonical.engine._counter_stack_item(
                canonical_target.ref,
                reason="event owner witness",
                countered_by="A",
            )
        self.assertEqual(
            ["spell.countered", "card.graveyard"],
            [
                call.args[0]
                for call in canonical_dispatch.call_args_list
                if call.args[0] in {"spell.countered", "card.graveyard"}
            ],
        )

    def test_intrinsic_counter_prohibition_mutant_is_killed(self):
        session = self.session(7012710, players=2)
        engine = session.engine
        spell = self.card(engine, "B", "Unanswerable Test Spell")
        engine.move_card(spell.object_id, "hand", log=False)
        engine.state.active_player = "B"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "B"
        engine.state.players["B"].mana_pool["U"] = 1

        with patch(
            "quorune.rules.casting.commit."
            "oracle_has_intrinsic_counter_prohibition",
            return_value=False,
        ):
            engine._cast(
                "B",
                {
                    "card": spell.ref,
                    "pay": "manual",
                    "payment": {"U": 1},
                },
            )

        self.assertFalse(engine.state.stack[-1].context["cant_be_countered"])


class IntrinsicEmptySpellResolutionTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.LifeGainProhibitionRuntimeTests.session
    add = native.LifeGainProhibitionRuntimeTests.add
    prepare = native.LifeGainProhibitionRuntimeTests.prepare
    actions = native.LifeGainProhibitionRuntimeTests.actions
    binding = native.LifeGainProhibitionRuntimeTests.binding
    checkpoint = native.LifeGainProhibitionRuntimeTests.checkpoint
    resolve = native.LifeGainProhibitionRuntimeTests.resolve
    replay = native.LifeGainProhibitionRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        fixtures = ROOT / "tests" / "fixtures"
        path = Path(cls.temporary.name) / "intrinsic-empty-spell.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "intrinsic-empty-spell-resolution-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Intrinsic empty spell resolution",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"],
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_partial_intrinsic_spell_pauses_and_replays_authorized_arbitration(self):
        session = self.session(128608220)
        session.state.config = replace(session.state.config, semantic_policy="arbitrate_or_pause")
        spell = self.add(session.engine, "Caught Red-Handed", zone="hand")
        target = self.add(session.engine, "Generic Bound Body", seat="B")
        hidden = self.add(session.engine, "Void Rend", seat="D", zone="hand")
        target.tapped = True
        self.assertFalse(self.binding(session, spell)["strict_capability_ready"])
        self.prepare(session, mana={"C": 4, "R": 1})
        self.checkpoint(session)
        action, = [row for row in self.actions(session) if row["id"] == "cast:" + spell.ref]
        self.assertFalse(action.get("target_schema"))
        command = {"action_id": action["id"], "pay": "auto"}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {**command, "targets": [target.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        cast = session.act("pilot:A", command)
        self.assertTrue(cast.ok, cast.summary)
        item = session.state.stack[-1]
        self.assertTrue(item.context["cant_be_countered"])
        self.assertIsNone(session.engine.semantics.get(item.semantic_key))
        self.assertIsNone(implicit_semantic_execution_provenance(session.engine, item.semantic_key, stack_item=item))
        with self.assertRaisesRegex(ValueError, "no current trusted resolution program"):
            capture_event_spell_copy_source(session.engine, item)

        decision = self.resolve(session)
        self.assertEqual("arbiter.resolve", decision.kind)
        self.assertEqual(["arbiter"], session.pending_principals())
        self.assertEqual("stack", session.state.cards[spell.object_id].zone)
        current = session.state.cards[target.object_id]
        self.assertEqual(("B", True), (current.controller, current.tapped))
        pilot = session.packet("pilot:A", full=True)
        self.assertIsNone(pilot["decision"])
        self.assertNotIn(hidden.ref, json.dumps(pilot))
        self.assertEqual("arbiter.resolve", session.packet("arbiter", full=True)["decision"]["kind"])

        resumed = self.replay(session, load=True)
        before = authoritative_state_hash(resumed.state)
        self.assertFalse(resumed.act("pilot:A", {"action_id": "fizzle"}).ok)
        self.assertEqual(before, authoritative_state_hash(resumed.state))
        fizzle = resumed.act("arbiter", {"action_id": "fizzle"})
        self.assertTrue(fizzle.ok, fizzle.summary)
        self.assertEqual("graveyard", resumed.state.cards[spell.object_id].zone)
        self.assertFalse(resumed.state.stack)
        current = resumed.state.cards[target.object_id]
        self.assertEqual(("B", True), (current.controller, current.tapped))
        self.replay(resumed, load=True)

    def test_trusted_only_admits_the_complete_intrinsic_spell_and_keeps_its_target(self):
        session = self.session(128608221)
        session.state.config = replace(session.state.config, semantic_policy="trusted_only")
        partial = self.add(session.engine, "Caught Red-Handed", zone="hand")
        spell = self.add(session.engine, "Void Rend", zone="hand")
        target = self.add(session.engine, "Generic Bound Body", seat="B")
        self.assertTrue(self.binding(session, spell)["strict_capability_ready"])
        self.prepare(session, mana={"C": 4, "R": 1, "W": 1, "U": 1, "B": 1})
        self.checkpoint(session)
        actions = {row["id"]: row for row in self.actions(session)}
        partial_id = "cast:" + partial.ref
        self.assertNotIn(partial_id, actions)
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": partial_id, "targets": [target.ref], "pay": "auto"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        action = actions["cast:" + spell.ref]
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        cast = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        item = session.state.stack[-1]
        self.assertTrue(item.context["cant_be_countered"])
        self.assertEqual([target.ref], item.targets)
        self.assertEqual(item.ref, capture_event_spell_copy_source(session.engine, item).item().ref)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[spell.object_id].zone)
        self.replay(session, load=True)

    def test_empty_spell_resolution_mode_is_bound_to_the_checkpoint(self):
        from quorune.model import GameConfig, GameState
        from quorune.record_state_provenance import format_state_versions, validate_state_versions

        key = "empty_spell_resolution_version"
        state = GameState(game_id="empty-spell-mode", config=GameConfig(), players={}, cards={},
                          deck_names={}, commander_oracle_ids={}, turn_order=[], current_turn=None,
                          last_normal_turn_player=None)
        wire = state.to_dict()
        self.assertEqual(1, wire[key])
        self.assertEqual(wire, GameState.from_dict(wire).to_dict())
        manifest = {"format": format_state_versions(state)}
        validate_state_versions(manifest, state)

        historical_wire = {field: value for field, value in wire.items() if field != key}
        historical = GameState.from_dict(historical_wire)
        self.assertIsNone(historical.empty_spell_resolution_version)
        self.assertEqual(historical_wire, historical.to_dict())
        historical_manifest = {"format": format_state_versions(historical)}
        self.assertNotIn(key, historical_manifest["format"])
        validate_state_versions(historical_manifest, historical)
        for versioned, checkpoint in ((manifest, historical), (historical_manifest, state)):
            with self.assertRaisesRegex(ValueError, "Empty spell resolution provenance"):
                validate_state_versions(versioned, checkpoint)
        for bad in (True, False, 0, 2, "1"):
            with self.subTest(value=bad):
                with self.assertRaisesRegex(ValueError, "Unsupported empty spell resolution version"):
                    GameState.from_dict({**wire, key: bad})
                with self.assertRaisesRegex(ValueError, "Empty spell resolution provenance"):
                    validate_state_versions({"format": {**manifest["format"], key: bad}}, state)


if __name__ == "__main__":
    unittest.main()
