from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.death_battlefield_return import DEATH_BATTLEFIELD_ARRIVALS
from quorune.rules.granted_death_role_return import (
    DEATH_ROLE_VERSION_FIELD, GRANTED_DEATH_ROLE_RETURN_CAPABILITY,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_target_owner_shuffle_top as target_owner_shuffle

SOURCE = "Not Dead After All"
PROFILE = "commander_review"


class NotDeadAfterAllTests(InheritedNativeFixture, unittest.TestCase):
    compose_target = target_owner_shuffle.TargetOwnerShuffleTopNativeTests.compose_target

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "not-dead-after-all.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/not-dead-after-all-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Granted death return and Wicked Roles", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, seed, *, shape="creature", prevent_death=False, graveyard_reply=False, role_replacement=False):
        game = self.session(seed)
        host = game.engine
        if shape == "composed":
            target = self.compose_target(game)
            objects = tuple(target.permanent_composition.topology.physical_object_ids)
        elif shape == "animated":
            target = self.add(host, "Mishra's Factory", seat="B", ref="not-dead-factory")
            objects = (target.object_id,)
        else:
            target = self.add(host, "Grizzly Bears", seat="B", ref="not-dead-creature")
            objects = (target.object_id,)
        host.change_control(target.object_id, "A", reason="fixture separates original owner and controller")
        other = self.add(host, "Grizzly Bears", seat="B", ref="not-dead-opponent")
        spell = self.add(host, SOURCE, zone="hand", ref="not-dead-cast")
        removal = self.add(host, "Murder", seat="C", zone="hand", ref="not-dead-destroy")
        role_removal = self.add(host, "Disenchant", seat="C", zone="hand", ref="not-dead-destroy-role")
        crypt = self.add(host, "Tormod's Crypt", seat="D", ref="not-dead-crypt") if graveyard_reply else None
        if prevent_death:
            self.add(host, "Rest in Peace", seat="D", ref="not-dead-replacement")
        if role_replacement:
            self.assert_closed(game, self.add(host, "Peregrin Took", ref="not-dead-token-replacement"))
            self.add(host, "Murder", seat="C", zone="hand", ref="not-dead-second-destroy")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"B": 1, "C": 1})
        game.state.players["C"].mana_pool.update({"B": 4, "C": 4, "W": 1})
        self.checkpoint(game)
        if shape == "animated":
            self.activate(game, "A", target, label="becomes")
            self.resolve_top(game)
            self.assertIn("creature", game.engine._type_parts(game.engine._effective_card_data(target)["type_line"])[0])
        action = self.action(game, "A", spell)
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        self.assertNotIn(other.ref, action["target_schema"]["legal_refs"])
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", {"action_id": action["id"], "targets": [other.ref], "pay": "auto"})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", spell, [target.ref])
        self.resolve_top(game)
        self.assertEqual("exile" if prevent_death else "graveyard", game.state.cards[spell.object_id].zone)
        return game, target, objects, removal, role_removal, crypt

    def activate(self, game, seat, card, *, targets=(), label=None):
        self.priority(game, seat)
        candidates = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        actions = [choice for choice in candidates if choice["id"].startswith(f"activate:{card.ref}:")]
        if label:
            ability = next(ability for ability in game.engine._activated_abilities(card) if label in ability.effect_text)
            actions = [row for row in actions if row["id"].endswith(":" + ability.ability_id)]
        action, = actions
        result = game.act(f"pilot:{seat}", {"action_id": action["id"], "targets": list(targets), "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def kill_recipient(self, game, target, removal):
        self.cast_for(game, "C", removal, [target.ref])
        stack_ref = game.state.stack[-1].ref
        for _ in range(16):
            if not any(item.ref == stack_ref for item in game.state.stack):
                return
            decision = game.state.pending_decision
            payload = decision.payload_by_actor[decision.actors[0]]
            if decision.kind == "priority":
                command = {"action_id": "pass"}
            elif decision.kind == "permanent.composition_order":
                self.assertEqual("B", decision.actors[0])
                command = {"action_id": "order", "cards": [row["id"] for row in payload["cards"]]}
            elif decision.kind == "semantic.choice" and payload.get("operation") == "composition_departure_order":
                self.assertEqual("B", decision.actors[0])
                offer, = payload["legal_actions"]
                command = {"action_id": offer["id"], "cards": offer["choice_schema"]["legal_refs"]}
            else:
                self.fail(f"Unexpected decision while resolving Murder: {decision.kind}: {payload}")
            result = game.act(game.pending_principals()[0], command)
            self.assertTrue(result.ok, result.summary)
        self.fail("Murder did not complete through the ordinary resolution path")

    def roles(self, game):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token and card.printed_name.startswith("Wicked")]

    def test_paid_grant_returns_actual_objects_then_creates_roles_for_the_trigger_controller(self):
        for index, shape in enumerate(("creature", "composed", "animated")):
            with self.subTest(shape=shape):
                game, target, objects, removal, role_removal, _ = self.start(7313110 + index, shape=shape)
                old_logical = target.logical_object_id
                self.kill_recipient(game, target, removal)
                trigger, = game.state.stack
                self.assertEqual("A", trigger.controller)
                self.assertCountEqual(objects, [row["object_id"] for row in trigger.context[DEATH_BATTLEFIELD_ARRIVALS]])
                program = game.engine.semantics.get(trigger.semantic_key)
                self.assertEqual(1, program.effects[0][DEATH_ROLE_VERSION_FIELD])
                self.assertIn(GRANTED_DEATH_ROLE_RETURN_CAPABILITY, program.capability_dependencies)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                roles = self.roles(game)
                expected = [] if shape == "animated" else list(objects)
                self.assertCountEqual(expected, [role.attached_to for role in roles])
                self.assertTrue(all(role.controller == "A" for role in roles))
                for object_id in objects:
                    card = game.state.cards[object_id]
                    self.assertEqual(("battlefield", "B", "B", True),
                                     (card.zone, card.owner, card.controller, card.tapped))
                    self.assertIsNone(card.permanent_composition)
                    self.assertFalse(any(
                        fragment.semantic_key == trigger.semantic_key
                        for fragment in game.engine._effective_ability_fragments(card)
                        if hasattr(fragment, "semantic_key")
                    ))
                self.assertNotEqual(old_logical, game.state.cards[target.object_id].logical_object_id)
                if roles and shape == "creature":
                    self.assertEqual(3, game.engine._numeric_stat(target.object_id, "power"))
                    life = {seat: game.state.players[seat].life for seat in "ABCD"}
                    self.cast_for(game, "C", role_removal, [roles[0].ref])
                    self.resolve_top(game)
                    self.resolve_top(game)
                    self.assertEqual({seat: life[seat] - int(seat != "A") for seat in "ABCD"},
                                     {seat: game.state.players[seat].life for seat in "ABCD"})
                self.replay(game, load=True)

    def test_replaced_death_and_stale_graveyard_incarnation_make_no_role(self):
        for index, replaced in enumerate((True, False)):
            with self.subTest(replaced=replaced):
                game, target, _, removal, _, crypt = self.start(
                    7313200 + index, prevent_death=replaced, graveyard_reply=not replaced,
                )
                self.kill_recipient(game, target, removal)
                if replaced:
                    self.assertFalse(game.state.stack)
                else:
                    self.assertEqual(1, len(game.state.stack))
                    self.activate(game, "D", crypt, targets=("B",))
                    self.resolve_top(game)
                    game = self.replay(game, load=True)
                    self.resolve_top(game)
                self.assertEqual("exile", game.state.cards[target.object_id].zone)
                self.assertEqual([], self.roles(game))
                self.replay(game, load=True)

    def test_actual_mixed_role_replacement_batches_both_attachments_and_one_food(self):
        from quorune.token_creation import _commit_resolved_token_specs

        game, target, objects, removal, _, _ = self.start(7313300, shape="composed", role_replacement=True)
        self.kill_recipient(game, target, removal)
        trigger, = game.state.stack
        self.assertCountEqual(objects, [row["object_id"] for row in trigger.context[DEATH_BATTLEFIELD_ARRIVALS]])
        game = self.replay(game, load=True)
        start = game.state.event_sequence
        with patch("quorune.token_creation._commit_resolved_token_specs", wraps=_commit_resolved_token_specs) as commit:
            self.resolve_top(game)
        commit.assert_called_once()
        self.assertEqual(2, commit.call_args.kwargs["base_quantity"])
        replacement_result = commit.call_args.kwargs["resolved"]
        self.assertEqual(1, len(replacement_result.journal))
        self.assertCountEqual(
            [("Wicked", 1, game.state.cards[object_id].ref) for object_id in objects] + [("Food", 1, None)],
            [(spec["name"].removesuffix(" Token"), spec["quantity"], spec.get("aura_target_ref"))
             for spec in replacement_result.specs],
        )
        roles = self.roles(game)
        self.assertCountEqual(objects, [role.attached_to for role in roles])
        self.assertTrue(all(role.controller == "A" for role in roles))
        food, = [card for card in game.state.cards.values() if card.zone == "battlefield"
                 and card.is_token and card.printed_name.startswith("Food")]
        self.assertEqual(("A", None), (food.controller, food.attached_to))
        for object_id in objects:
            current = game.state.cards[object_id]
            self.assertEqual(("battlefield", "B", True), (current.zone, current.controller, current.tapped))
        self.assertEqual(1, sum(event.event_id > start and event.code == "token.create" for event in game.state.events))
        game = self.replay(game, load=True)
        victim, survivor = (game.state.cards[object_id] for object_id in objects)
        second = next(card for card in game.state.cards.values()
                      if card.ref == "not-dead-second-destroy" and card.zone == "hand")
        life = {seat: game.state.players[seat].life for seat in "ABCD"}
        self.kill_recipient(game, victim, second)
        self.resolve_top(game)
        self.assertEqual([survivor.object_id], [role.attached_to for role in self.roles(game)])
        self.assertEqual("battlefield", game.state.cards[food.object_id].zone)
        self.assertEqual({seat: life[seat] - int(seat != "A") for seat in "ABCD"},
                         {seat: game.state.players[seat].life for seat in "ABCD"})
        self.replay(game, load=True)

    def test_role_return_requires_the_granted_source_and_independent_role_authority(self):
        card = self.db.lookup(SOURCE, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        self.assertEqual("exact", compile_oracle_card(card, **options).status)
        report, semantics = register_card(self.db, self.registry, SOURCE)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = (ability for ability in semantics.programs_for_oracle(card.oracle_id)
                    if ability.event == "creature.dies.self")
        self.assertIn(GRANTED_DEATH_ROLE_RETURN_CAPABILITY, program.capability_dependencies)
        body, = deepcopy(thaw_value(program.effects))
        body["role"]["name"] = "Royal"
        malformed = bind_semantic_program_runtime(replace(program, effects=[body]),
            capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(malformed["strict"], malformed)
        from quorune.rules.attached_role_batch import ATTACHED_ROLE_BATCH_CAPABILITY

        for owner in (GRANTED_DEATH_ROLE_RETURN_CAPABILITY, ATTACHED_ROLE_BATCH_CAPABILITY):
            deps = tuple(cap for cap in program.capability_dependencies if cap != owner)
            omitted = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
            report = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
            self.assertIn("capability:undeclared_runtime_dependency:" + owner, report["blockers"])
        with patch("quorune.compiler.granted_death_role_return.granted_death_role_base_text", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, **options).status)


MALAKIR = "Malakir Rebirth // Malakir Mire"


class MalakirRebirthTests(InheritedNativeFixture, unittest.TestCase):
    compose_target = NotDeadAfterAllTests.compose_target
    kill_recipient = NotDeadAfterAllTests.kill_recipient
    activate = NotDeadAfterAllTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "malakir.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/not-dead-after-all-cards.json",
            ROOT / "tests/fixtures/granted-death-return-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Malakir spell and land", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def cast_rebirth(self, game, spell, target):
        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in rows if row["id"].startswith("cast:") and row.get("card") == spell.ref]
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        result = game.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("Malakir Rebirth", game.state.stack[-1].chosen_face)

    def test_front_compiles_ordered_effects_and_binds_exact_quoted_child(self):
        from quorune.card_programs.binding import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.rules.granted_death_role_return import GRANTED_SELF_DEATH_RETURN_CAPABILITY
        from quorune.semantics import SemanticRegistry

        card = self.db.lookup(MALAKIR, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        front, back = ir.faces
        outer, child = front.nodes
        self.assertEqual({"op": "lose_life", "player": "$controller", "amount": 2}, outer.effects[0])
        self.assertEqual("$target.0", outer.effects[1]["card"])
        self.assertEqual("permanent.graveyard.self", child.event)
        self.assertEqual(child.text, card.faces[0]["oracle_text"][child.span.start:child.span.end])
        self.assertEqual(["replacement_effect", "mana_ability"], [node.kind for node in back.nodes])
        program = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(), **options)
        original = bind_card_program_runtime(program, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(original["strict_capability_ready"], original["blockers"])
        self.assertEqual(4, len(program.abilities))
        child_program, = (ability for ability in program.abilities if ability.event == child.event)
        self.assertIn(GRANTED_SELF_DEATH_RETURN_CAPABILITY, child_program.capability_dependencies)
        invalid = type(program).create(
            compiler_version=program.compiler_version, oracle_id=program.oracle_id, card_name=program.card_name,
            faces=program.faces, oracle_source_hash=program.oracle_source_hash,
            rulings_source_hash=program.rulings_source_hash, provenance=program.provenance,
            abilities=(item for item in program.abilities if item != child_program),
        )
        invalid_report = bind_card_program_runtime(invalid, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(any("runtime_temporary_self_death_child" in reason for reason in invalid_report["blockers"]))
        with patch("quorune.compiler.temporary_quoted_ability_nodes._life_loss_self_death_grant_inner", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, **options).status)

    def test_front_loses_life_only_on_resolution_and_returns_noncreature_to_owner(self):
        game = self.session(7313401)
        engine = game.engine
        target = self.add(engine, "Grizzly Bears", seat="B")
        engine.change_control(target.object_id, "C", reason="Distinguish holder controller and owner")
        spell = self.add(engine, MALAKIR, zone="hand")
        change = self.add(engine, "One with the Stars", zone="hand")
        removal = self.add(engine, "Naturalize", seat="D", zone="hand")
        second = self.add(engine, "Murder", seat="D", zone="hand")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"B": 1, "U": 1, "C": 3})
        game.state.players["D"].mana_pool.update(G=1, B=2, C=2)
        self.checkpoint(game)
        start_life = {seat: game.state.players[seat].life for seat in "ABCD"}
        self.cast_rebirth(game, spell, target)
        self.assertEqual(start_life["A"], game.state.players["A"].life)
        self.assertEqual(0, game.state.players["A"].mana_pool["B"])
        self.resolve_top(game)
        self.assertEqual({seat: life - (2 if seat == "A" else 0) for seat, life in start_life.items()},
                         {seat: game.state.players[seat].life for seat in "ABCD"})
        self.cast_for(game, "A", change, [target.ref])
        self.resolve_top(game)
        self.assertNotIn("Creature", engine._effective_card_data(game.state.cards[target.object_id])["type_line"])
        self.cast_for(game, "D", removal, [target.ref])
        self.resolve_top(game)
        trigger, = game.state.stack
        self.assertEqual("C", trigger.controller)
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        returned = game.state.cards[target.object_id]
        self.assertEqual(("battlefield", "B", "B", True, {}),
                         (returned.zone, returned.owner, returned.controller, returned.tapped, returned.counters))
        self.cast_for(game, "D", second, [target.ref])
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.replay(game)

    def test_illegal_sole_target_stops_life_loss_even_if_cast_at_one_life(self):
        game = self.session(7313402)
        engine = game.engine
        target = self.add(engine, "Grizzly Bears", seat="B")
        spell = self.add(engine, MALAKIR, zone="hand")
        removal = self.add(engine, "Murder", seat="C", zone="hand")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"B": 1})
        game.state.players["A"].life = 1
        game.state.players["C"].mana_pool.update(B=2, C=1)
        self.checkpoint(game)
        self.cast_rebirth(game, spell, target)
        self.assertEqual(1, game.state.players["A"].life)
        self.cast_for(game, "C", removal, [target.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.assertEqual(1, game.state.players["A"].life)
        self.assertIn("A", game.engine.active_seats)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.replay(game)

    def test_granted_death_returns_all_merged_arrivals_and_skips_stale_graveyard(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                game = self.session(7313403 + stale)
                target = self.compose_target(game)
                members = tuple(target.permanent_composition.topology.physical_object_ids)
                spell = self.add(game.engine, MALAKIR, zone="hand")
                removal = self.add(game.engine, "Murder", seat="C", zone="hand")
                crypt = self.add(game.engine, "Tormod's Crypt", seat="D") if stale else None
                self.assert_closed(game, spell)
                self.prepare(game, mana={"B": 1})
                game.state.players["C"].mana_pool.update(B=2, C=1)
                self.checkpoint(game)
                self.cast_rebirth(game, spell, target)
                self.resolve_top(game)
                self.kill_recipient(game, target, removal)
                trigger, = game.state.stack
                self.assertCountEqual(members, [row["object_id"] for row in trigger.context[DEATH_BATTLEFIELD_ARRIVALS]])
                if stale:
                    self.activate(game, "D", crypt, targets=("B",))
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                for member in members:
                    card = game.state.cards[member]
                    self.assertEqual("exile" if stale else "battlefield", card.zone)
                    if not stale:
                        self.assertEqual(("B", True, None), (card.controller, card.tapped, card.permanent_composition))
                self.replay(game)

    def test_exile_replacement_is_not_a_death_for_the_granted_trigger(self):
        game = self.session(7313406)
        engine = game.engine
        target = self.add(engine, "Grizzly Bears", seat="B")
        spell = self.add(engine, MALAKIR, zone="hand")
        removal = self.add(engine, "Murder", seat="C", zone="hand")
        self.add(engine, "Rest in Peace", seat="D")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"B": 1})
        game.state.players["C"].mana_pool.update(B=2, C=1)
        self.checkpoint(game)
        self.cast_rebirth(game, spell, target)
        self.resolve_top(game)
        self.cast_for(game, "C", removal, [target.ref])
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        self.replay(game)

    def test_mire_is_played_tapped_and_later_taps_for_exactly_black(self):
        game = self.session(7313405)
        engine = game.engine
        land = self.add(engine, MALAKIR, zone="hand")
        satyr = self.add(engine, "Voyaging Satyr")
        satyr.acquired_control_turn_count = -1
        self.assert_closed(game, land)
        self.prepare(game, mana={})
        self.checkpoint(game)
        life = game.state.players["A"].life
        land_count = game.state.players["A"].land_plays_remaining
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [row for row in rows if row["id"] == f"play-land:{land.ref}"]
        self.assertEqual("Malakir Mire", offered["face"])
        result = game.act("pilot:A", {"action_id": offered["id"]})
        self.assertTrue(result.ok, result.summary)
        current = game.state.cards[land.object_id]
        self.assertEqual(("battlefield", True), (current.zone, current.tapped))
        self.assertEqual("Malakir Mire", engine._effective_card_data(current)["name"])
        self.assertEqual(land_count - 1, game.state.players["A"].land_plays_remaining)
        self.assertEqual(life, game.state.players["A"].life)
        self.assertFalse(game.state.stack)
        action_id = f"activate:{land.ref}:ab2"
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn(action_id, [row["id"] for row in rows])
        self.activate(game, "A", satyr, targets=(land.ref,))
        self.resolve_top(game)
        self.priority(game, "A")
        black = game.state.players["A"].mana_pool["B"]
        result = game.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(black + 1, game.state.players["A"].mana_pool["B"])
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)
