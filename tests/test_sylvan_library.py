"""CR 121 and 504. The draw-step trigger settles exact private hand objects."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.adapters import compile_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import GameState
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.draw_step_drawing_settlement import (
    DRAW_STEP_SETTLEMENT_CAPABILITY,
    DRAW_STEP_SETTLEMENT_MECHANIC,
    DRAW_STEP_SETTLEMENT_VERSION_FIELD,
    controller_draw_step_condition,
    draw_step_settlement_effect,
)
from quorune.semantics import SemanticRegistry
from quorune.turn_history import reset_turn_history
from quorune.turn_step_owner import TURN_STEPS
from scripts.build_test_database import build_fixture_database


PROFILE = "commander_review"
SOURCE = "Sylvan Library"
ORACLE_ID = "92eed395-62ca-4293-882b-8565c40daab5"


class SylvanLibraryTests(InheritedNativeFixture, TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        db_path = Path(cls.temporary.name) / "sylvan-library.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database(
            [fixtures / name for name in (
                "bound-effect-program-cards.json",
                "draw-step-exempt-multiplier-cards.json",
                "draw-rules-cards.json",
                "sylvan-library-cards.json",
            )],
            db_path,
        )
        cls.db = CardDatabase(db_path)
        cls.addClassCleanup(cls.db.close)
        cls.registry = load_default_capability_registry()
        cls.record = cls.db.by_oracle_id(ORACLE_ID)
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Draw-step settlement",
            [DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            [commander],
        )

    @classmethod
    def tearDownClass(cls):
        pass

    def compile_printed(self, record=None):
        return compile_oracle_card(
            record or self.record, capability_registry=self.registry, capability_profile=PROFILE,
        )

    def compiled_card(self, semantics=None):
        return compile_card_program(
            self.db, self.record, semantic_registry=semantics, capability_registry=self.registry,
            capability_profile=PROFILE, trust_level="trusted",
        )

    def test_printed_trigger_has_one_strict_owner_and_preserves_the_archived_view(self):
        ir = self.compile_printed()
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual("step.begin", node.event)
        self.assertEqual(controller_draw_step_condition(), node.event_condition)
        self.assertEqual((draw_step_settlement_effect(),), node.effects)
        self.assertIn(DRAW_STEP_SETTLEMENT_CAPABILITY, node.capability_dependencies)

        reviewed = SemanticRegistry()
        historical, = (program for program in reviewed.programs_for_oracle(ORACLE_ID)
                       if program.ability_id == "trigger:draw-step")
        compiled = self.compiled_card(reviewed)
        binding = bind_card_program_runtime(compiled, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(binding["strict_capability_ready"], binding)
        trigger, = (program for program in compiled.abilities if program.event == "step.begin")
        self.assertIn(DRAW_STEP_SETTLEMENT_MECHANIC, trigger.coverage)
        self.assertIn(historical.key, compiled.provenance["superseded_reviewed_semantic_keys"])
        self.assertNotIn(DRAW_STEP_SETTLEMENT_VERSION_FIELD, historical.effects[0])
        legacy = bind_semantic_program_runtime(historical, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(legacy["compatibility_path"], legacy)
        self.assertFalse(legacy["blockers"], legacy)

    def test_adjacent_printed_text_and_changed_saved_recipe_fail_closed(self):
        text = self.record.oracle_text
        for changed in (
            text.replace("your draw step", "your upkeep"),
            text.replace("two additional cards", "three additional cards"),
            text.replace("drawn this turn", "drawn this way"),
            text.replace("pay 4 life", "pay 5 life"),
            text.replace("on top", "on the bottom"),
            text + " Rewrite the past.",
            text + "\nRewrite the past.",
        ):
            with self.subTest(text=changed):
                self.assertNotEqual("exact", self.compile_printed(replace(self.record, oracle_text=changed)).status)

        trigger, = (program for program in self.compiled_card().abilities if program.event == "step.begin")
        reversed_effect = deepcopy(trigger.effects[0])
        reversed_effect["then_by_choice"]["draw"].reverse()
        changed_draw = deepcopy(trigger.effects[0])
        changed_draw["then_by_choice"]["draw"][0]["count"] = 2.0
        for mutant in (
            replace(trigger, event_condition={"field": "step", "op": "eq", "value": "draw"}),
            replace(trigger, effects=(reversed_effect,)),
            replace(trigger, effects=(changed_draw,)),
            replace(trigger, effects=(*trigger.effects, {"op": "draw", "player": "$controller", "count": 1})),
        ):
            bound = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(bound["strict"], bound)
            self.assertTrue(any(item.startswith("runtime_draw_step_settlement:invalid:") for item in bound["blockers"]), bound)

        remaining = tuple(cap for cap in trigger.capability_dependencies if cap != DRAW_STEP_SETTLEMENT_CAPABILITY)
        omitted = replace(trigger, capability_dependencies=remaining,
                          capability_closure=self.registry.closure(remaining, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + DRAW_STEP_SETTLEMENT_CAPABILITY, bound["blockers"])

    def test_mutant_removing_the_source_producer_loses_whole_card_support(self):
        self.assertEqual("exact", self.compile_printed().status)
        with patch("quorune.compiler.draw_step_drawing_settlement.draw_step_settlement_trigger_result", return_value=None):
            self.assertNotEqual("exact", self.compile_printed().status)

    def set_turn(self, engine, sequence):
        engine.state.turn_sequence = sequence
        engine.state.turn_history = reset_turn_history(engine.state.turn_history, sequence)

    def scene(
        self, seed, *, owner="A", active="A", life=40, restricted=False,
        dredgers=(), early=False, pilot=None, drawn_commander=False,
    ):
        game = self.session(seed)
        engine = game.engine
        source = self.add(engine, SOURCE, seat=owner)
        self.assert_closed(game, source)
        if owner != active:
            engine.change_control(source.object_id, active, reason="fixture borrowed library")
        game.state.active_player = active
        player = game.state.players[active]
        player.life = life
        if pilot is not None:
            player.stats["turn_controlled_by"] = pilot
        if restricted:
            restriction = self.add(engine, "Spirit of the Labyrinth", seat="D")
            self.assert_closed(game, restriction)
        replacement_cards = [self.add(engine, name, seat=active, zone="graveyard") for name in dredgers]
        if early:
            self.set_turn(engine, 6)
            prior = self.add(engine, "Plains", seat=active, zone="library", ref="prior-turn-private")
            engine.draw(active, reason="fixture draw on the preceding turn", private=True)
            self.set_turn(engine, 7)
            stale = self.add(engine, "Plains", seat=active, zone="library", ref="same-ref-new-hand-object")
            engine.draw(active, reason="fixture draw before leaving hand", private=True)
            stale_identity = stale.logical_object_id
            engine.move_card(stale.object_id, "library", log=False)
            engine.move_card(stale.object_id, "hand", log=False)
            self.assertNotEqual(stale_identity, stale.logical_object_id)
            earlier = self.add(engine, "Plains", seat=active, zone="library", ref="earlier-this-turn-private")
            engine.draw(active, reason="fixture draw earlier this turn", private=True)
        else:
            self.set_turn(engine, 7)
            prior = stale = earlier = None

        if drawn_commander:
            from quorune.turn_history_model import DRAWN_HAND_CARD_EVENT

            commander_id, = player.zones["command"]
            commander = game.state.cards[commander_id]
            engine._remove_from_zone(commander)
            engine._reset_zone_change(commander, "hand")
            commander.zone = "hand"
            player.zones["hand"].append(commander.object_id)
            engine._record_turn_history(
                DRAWN_HAND_CARD_EVENT, actor=active, object_incarnation=commander.logical_object_id,
            )
        upcoming = tuple(self.add(engine, "Plains", seat=active, zone="library", ref=f"sylvan-draw-{index}")
                         for index in reversed(range(3)))
        game.state.phase_index = TURN_STEPS.index(("beginning", "upkeep"))
        engine._enter_step()
        engine.pump()
        self.checkpoint(game)
        return game, source, upcoming, replacement_cards, (prior, stale, earlier)

    @staticmethod
    def pilot_principals(game):
        return tuple(
            principal for seat in game.engine.seats
            if game.engine.permissions.capability_for(principal := "pilot:" + seat) is not None
        )

    def pass_priority(self, game):
        self.assertEqual("priority", game.state.pending_decision.kind)
        principal, = self.pilot_principals(game)
        result = game.act(principal, {"action_id": "pass"})
        self.assertTrue(result.ok, result.summary)

    def ready_draw_step(self, game):
        for _ in range(16):
            pending = game.state.pending_decision
            if game.state.step == "draw" and (
                game.state.stack or pending.kind == "draw.replacement"
            ):
                return
            self.pass_priority(game)
        self.fail("The native turn did not reach the draw step")

    def resolve(self, game):
        for _ in range(20):
            pending = game.state.pending_decision
            if pending is not None and pending.kind != "priority":
                return pending
            if not game.state.stack:
                return None
            self.pass_priority(game)
        self.fail("The native stack did not resolve")

    def ready_offer(self, game, source, principal="A"):
        self.ready_draw_step(game)
        current, = [item for item in game.state.stack if item.source_object_id == source.object_id]
        self.assertEqual("triggered_ability", current.kind)
        owned = game.engine.semantics.get(current.semantic_key)
        self.assertIn(DRAW_STEP_SETTLEMENT_CAPABILITY, owned.capability_dependencies)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual(("pilot:" + principal,), self.pilot_principals(game))

    def choose(self, game, principal, **fields):
        result = game.act("pilot:" + principal, {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, principal, **fields):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + principal, {"action_id": "choose", **fields})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def settlement(self, game, *, actor="A", principal="A"):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.choice", (actor,)), (pending.kind, tuple(pending.actors)))
        self.assertEqual(("pilot:" + principal,), self.pilot_principals(game))
        schema = pending.payload_by_actor[actor]["legal_actions"][0]["choice_schema"]
        return schema

    def assert_card_choice(self, game, card, *, actor="A", principal="A", payable=True):
        schema = self.settlement(game, actor=actor, principal=principal)
        self.assertEqual("choice", schema["field"])
        self.assertEqual(["pay_life", "top"] if payable else ["top"], schema["legal_values"])
        payload = game.state.pending_decision.payload_by_actor[actor]
        self.assertEqual([card.ref], [row["id"] for row in payload["objects"]])

    def test_actual_cast_and_distinct_current_draws_pay_or_return_with_pending_and_finished_replay(self):
        cast_game = self.session(121250001)
        cast_game.state.config.semantic_policy = "trusted_only"
        cast_source = self.add(cast_game.engine, SOURCE, zone="hand")
        self.assert_closed(cast_game, cast_source)
        self.prepare(cast_game, mana={"C": 1, "G": 1})
        self.checkpoint(cast_game)
        self.cast(cast_game, "A", cast_source)
        self.assertIsNone(self.resolve(cast_game))
        self.assertEqual("battlefield", cast_game.state.cards[cast_source.object_id].zone)
        self.replay(cast_game, load=True)

        game, source, upcoming, _, (prior, stale, earlier) = self.scene(121250002, early=True)
        second, first, ordinary = upcoming
        self.ready_offer(game, source)
        self.assertEqual("hand", game.state.cards[ordinary.object_id].zone)
        self.assertEqual("library", game.state.cards[first.object_id].zone)
        self.choose(game, "A", choice="draw")
        schema = self.settlement(game)
        self.assertEqual(("cards", 2, 2, "first_to_last"),
                         (schema["field"], schema["minimum"], schema["maximum"], schema["order"]))
        self.assertEqual({earlier.ref, ordinary.ref, first.ref, second.ref}, set(schema["legal_refs"]))
        self.reject(game, "A", cards=[stale.ref, first.ref])
        self.reject(game, "A", cards=[prior.ref, first.ref])

        pending = self.replay(game, load=True)
        self.assertEqual(schema, self.settlement(pending))
        self.choose(pending, "A", cards=[first.ref, earlier.ref])
        self.assert_card_choice(pending, first)
        self.choose(pending, "A", choice="top")
        self.assertEqual(first.object_id, pending.state.players["A"].zones["library"][-1])
        self.assertEqual(40, pending.state.players["A"].life)
        self.assert_card_choice(pending, earlier)
        pending = self.replay(pending, load=True)
        self.assert_card_choice(pending, earlier)
        self.choose(pending, "A", choice="pay_life")
        self.assertEqual(36, pending.state.players["A"].life)
        self.assertEqual("hand", pending.state.cards[earlier.object_id].zone)
        self.assertFalse(pending.state.stack)
        self.replay(pending, load=True)

        ordered = self.replay(game, load=True)
        self.reject(ordered, "A", cards=[first.ref, first.ref])
        self.choose(ordered, "A", cards=[first.ref, second.ref])
        identities = {card.object_id: ordered.state.cards[card.object_id].logical_object_id for card in (first, second)}
        self.assert_card_choice(ordered, first)
        self.choose(ordered, "A", choice="top")
        self.assertNotEqual(identities[first.object_id], ordered.state.cards[first.object_id].logical_object_id)
        self.assertEqual(identities[second.object_id], ordered.state.cards[second.object_id].logical_object_id)
        self.assertEqual(first.object_id, ordered.state.players["A"].zones["library"][-1])
        self.assertEqual("hand", ordered.state.cards[second.object_id].zone)
        self.assert_card_choice(ordered, second)
        self.choose(ordered, "A", choice="top")
        self.assertNotEqual(identities[second.object_id], ordered.state.cards[second.object_id].logical_object_id)
        self.assertEqual([first.object_id, second.object_id], ordered.state.players["A"].zones["library"][-2:])
        self.assertEqual(40, ordered.state.players["A"].life)
        self.replay(ordered, load=True)

    def test_paying_for_two_draws_commits_two_distinct_four_life_events(self):
        game, source, upcoming, _, _ = self.scene(121250011)
        self.ready_offer(game, source)
        self.choose(game, "A", choice="draw")
        first, second = upcoming[:2]
        self.choose(game, "A", cards=[first.ref, second.ref])
        self.assert_card_choice(game, first)
        self.choose(game, "A", choice="pay_life")
        self.assertEqual(36, game.state.players["A"].life)
        self.assertEqual([4], [event.details["amount"] for event in game.state.events if event.code == "life.pay"])
        self.assert_card_choice(game, second)
        game = self.replay(game, load=True)
        self.assert_card_choice(game, second)
        self.choose(game, "A", choice="pay_life")
        self.assertEqual(32, game.state.players["A"].life)
        self.assertEqual([4, 4], [event.details["amount"] for event in game.state.events if event.code == "life.pay"])
        self.assertTrue(all(game.state.cards[card.object_id].zone == "hand" for card in (first, second)))
        self.replay(game, load=True)

    def test_borrowed_source_and_controlled_turn_keep_the_private_choice_with_the_current_pilot(self):
        game, source, upcoming, _, _ = self.scene(121250003, owner="B", pilot="C", life=3)
        self.assertEqual(("B", "A"), (source.owner, source.controller))
        self.ready_offer(game, source, "C")
        self.reject(game, "B", choice="draw")
        self.reject(game, "A", choice="draw")
        self.choose(game, "C", choice="draw")
        schema = self.settlement(game, principal="C")
        self.assertEqual({card.ref for card in upcoming}, set(schema["legal_refs"]))
        for seat in ("B", "D"):
            packet = json.dumps(game.packet("pilot:" + seat, full=True), sort_keys=True)
            self.assertTrue(all(card.ref not in packet for card in upcoming), packet)
        first, second = upcoming[:2]
        packet = game.packet("pilot:C", full=True)
        self.assertEqual("semantic.choice", packet["decision"]["kind"])
        self.reject(game, "A", cards=[first.ref, second.ref])
        game = self.replay(game, load=True)
        self.assertEqual(schema, self.settlement(game, principal="C"))
        self.choose(game, "C", cards=[first.ref, second.ref])
        self.assert_card_choice(game, first, principal="C", payable=False)
        self.reject(game, "C", choice="pay_life")
        self.choose(game, "C", choice="top")
        self.assert_card_choice(game, second, principal="C", payable=False)
        self.choose(game, "C", choice="top")

        self.assertEqual([first.object_id, second.object_id], game.state.players["A"].zones["library"][-2:])
        self.assertEqual(3, game.state.players["A"].life)
        self.assertEqual(40, game.state.players["C"].life)
        self.replay(game, load=True)

    def test_lawful_draw_choice_with_two_replacements_settles_the_only_actual_draw(self):
        game, source, upcoming, dredgers, _ = self.scene(
            121250004, dredgers=("Darkblast", "Dakmor Salvage"),
        )
        self.ready_draw_step(game)
        self.assertEqual("draw.replacement", game.state.pending_decision.kind)
        self.choose(game, "A", choice="draw")
        self.ready_offer(game, source)
        ordinary = upcoming[-1]
        self.choose(game, "A", choice="draw")
        self.assertEqual("draw.replacement", game.state.pending_decision.kind)
        game = self.replay(game, load=True)
        for card in dredgers:
            self.assertEqual("draw.replacement", game.state.pending_decision.kind)
            self.choose(game, "A", choice=card.ref)
            self.assertEqual("hand", game.state.cards[card.object_id].zone)
        schema = self.settlement(game)
        self.assertEqual(("cards", [ordinary.ref]), (schema["field"], schema["legal_refs"]))
        self.reject(game, "A", cards=[dredgers[0].ref])
        self.choose(game, "A", cards=[ordinary.ref])
        self.assert_card_choice(game, ordinary)
        self.choose(game, "A", choice="top")

        self.assertEqual(ordinary.object_id, game.state.players["A"].zones["library"][-1])
        self.replay(game, load=True)

    def test_draw_limit_disallows_the_offer_and_declining_skips_settlement(self):
        for restricted in (True, False):
            with self.subTest(restricted=restricted):
                game, source, upcoming, _, _ = self.scene(121250006 + restricted, restricted=restricted)
                self.ready_draw_step(game)
                self.assertTrue(any(item.source_object_id == source.object_id for item in game.state.stack))
                pending = self.resolve(game)
                if restricted:
                    self.assertIsNone(pending)
                else:
                    self.assertEqual("semantic.choice", pending.kind)
                    self.choose(game, "A", choice="decline")
                self.assertEqual("priority", game.state.pending_decision.kind)
                self.assertEqual("hand", game.state.cards[upcoming[-1].object_id].zone)
                self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in upcoming[:2]))
                self.assertFalse(any(event.code == "sylvan_library.settle" for event in game.state.events))
                self.replay(game, load=True)

    def test_missing_history_version_preserves_historical_state_and_does_not_invent_draws(self):
        game = self.session(121250009)
        state = game.state.to_dict()
        self.assertEqual(1, state["turn_history"].pop("draw_identity_history_version"))
        restored = GameState.from_dict(state)
        self.assertIsNone(restored.turn_history.draw_identity_history_version)
        self.assertEqual(state, restored.to_dict())
        for bad in (None, True, 2, "1"):
            invalid = deepcopy(state)
            invalid["turn_history"]["draw_identity_history_version"] = bad
            with self.assertRaises(ValueError):
                GameState.from_dict(invalid)

    def test_withdrawn_settlement_authority_is_a_compiler_residual(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        record, = (row for row in raw["capabilities"] if row["id"] == DRAW_STEP_SETTLEMENT_CAPABILITY)
        record.update(status="blocked", blockers=["withdraw the draw-step settlement authority"])
        registry = CapabilityRegistry(raw)
        evidence = build_index(
            registry_value=raw, declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        compiled = compile_oracle_card(self.record, capability_registry=registry, capability_profile=PROFILE)
        self.assertNotEqual("exact", compiled.status)
        self.assertTrue(any(DRAW_STEP_SETTLEMENT_CAPABILITY in blocker for residual in compiled.material_residuals
                            for blocker in residual.blockers))
        ordinary = self.db.lookup("Reach Through Mists", fuzzy=False)
        self.assertEqual("exact", compile_oracle_card(ordinary, capability_registry=registry,
                                                      capability_profile=PROFILE).status)


    def test_ordered_return_resumes_the_owners_commander_redirect(self):
        game, source, upcoming, _, _ = self.scene(121250010, drawn_commander=True)
        commander, = [
            card for card in game.state.cards.values()
            if card.owner == "A" and card.is_commander
        ]
        self.ready_offer(game, source)
        self.choose(game, "A", choice="draw")
        schema = self.settlement(game)
        self.assertIn(commander.ref, schema["legal_refs"])
        kept_top = upcoming[0]
        self.choose(game, "A", cards=[commander.ref, kept_top.ref])
        self.assert_card_choice(game, commander)
        self.choose(game, "A", choice="top")
        self.assertEqual("replacement.order", game.state.pending_decision.kind)
        self.assertEqual(("pilot:A",), self.pilot_principals(game))
        self.assertEqual("hand", game.state.cards[kept_top.object_id].zone)
        game = self.replay(game, load=True)
        self.assertEqual(("pilot:A",), self.pilot_principals(game))
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        redirect, = (row["id"] for row in options if not row.get("decline"))
        self.reject(game, "B", replacement=redirect)
        self.choose(game, "A", replacement=redirect)
        self.assertEqual("command", game.state.cards[commander.object_id].zone)
        self.assertEqual("hand", game.state.cards[kept_top.object_id].zone)
        self.assert_card_choice(game, kept_top)
        game = self.replay(game, load=True)
        self.assert_card_choice(game, kept_top)
        self.choose(game, "A", choice="top")
        self.assertEqual(kept_top.object_id, game.state.players["A"].zones["library"][-1])
        self.assertEqual(40, game.state.players["A"].life)
        self.replay(game, load=True)
