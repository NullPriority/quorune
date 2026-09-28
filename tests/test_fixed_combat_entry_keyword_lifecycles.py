from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.continuous_effect_state import (
    commit_continuous_effect,
    expire_end_of_turn_continuous_effects,
)
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance, CombatState
from quorune.oracle_ir import (
    compile_oracle_card,
    register_generated_programs,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.current_ability_components import (
    program_has_current_ability_fragments,
)
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.trigger_processing import (
    collect_trigger_items,
    enqueue_trigger_batch,
)
from quorune.zone_trigger_events import ZoneTransitionKind
from scripts.build_test_database import build_fixture_database


FIXTURE_PATH = (
    ROOT
    / "tests"
    / "fixtures"
    / "fixed-combat-entry-keyword-lifecycle-cards.json"
)
EXPECTED_TEMPLATES = {
    "Generic Ninjutsu Adept": "fixed-ninjutsu-activation-v1",
    "Generic Commander Ninjutsu Adept": "fixed-commander-ninjutsu-activation-v1",
    "Generic Sneak Adept": "fixed-sneak-cast-lifecycle-v1",
    "Generic Web-Slinging Adept": "fixed-web-slinging-cast-lifecycle-v1",
    "Generic Encore Adept": "fixed-encore-activation-v1",
    "Generic Blitz Adept": "fixed-blitz-cast-lifecycle-v1",
    "Generic Myriad Adept": "fixed-myriad-trigger-v1",
}


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-combat-entry-keyword-lifecycles.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE_PATH,
        ],
        database,
    )
    return CardDatabase(database)


class FixedCombatEntryKeywordLifecycleCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def test_fixed_combat_entry_keyword_contract_matrix(self):
        for name, template_id in EXPECTED_TEMPLATES.items():
            with self.subTest(name=name):
                ir = compile_oracle_card(
                    self.db.lookup(name),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                node = next(
                    node
                    for node in ir.faces[0].nodes
                    if node.template_id == template_id
                )
                self.assertTrue(node.exact)
                self.assertEqual("exact", ir.status)

    def test_open_combat_entry_variants_remain_residual(self):
        base = self.db.lookup("Generic Ninjutsu Adept")
        cases = (
            ("Ninjutsu {X}{U}", ("Ninjutsu",)),
            ("Encore {B/P}", ("Encore",)),
            ("Blitz {X}{R}", ("Blitz",)),
            ("Myriad 2", ("Myriad",)),
            ("Sneak—Pay 2 life.", ("Sneak",)),
            ("Web-slinging {G/W}", ("Web-slinging",)),
        )
        for index, (oracle_text, keywords) in enumerate(cases, start=1):
            with self.subTest(oracle_text=oracle_text):
                record = replace(
                    base,
                    oracle_id=f"26000000-0000-4000-8000-000000001{index:03d}",
                    name=f"Unsupported combat entry {index}",
                    oracle_text=oracle_text,
                    keywords=keywords,
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_combat_entry_compiler_mutation_is_killed(self):
        record = self.db.lookup("Generic Myriad Adept")
        with patch(
            "quorune.compiler.keyword_nodes.fixed_myriad_keyword_node",
            return_value=None,
        ):
            mutated = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertNotEqual("exact", mutated.status)
        self.assertTrue(mutated.material_residuals)


class FixedCombatEntryKeywordLifecycleRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
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
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
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
        engine.state.stack.clear()
        engine.state.pending_trigger_batches.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        engine,
        *,
        name: str,
        ref: str,
        seat: str = "A",
        zone: str = "hand",
        is_commander: bool = False,
    ) -> CardInstance:
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            is_commander=is_commander,
            commander_designation_id=(
                f"commander:{seat}:{ref}" if is_commander else None
            ),
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=[seat] if zone in {"hand", "library"} else list(engine.seats),
            revealed_to=[] if zone in {"hand", "library"} else list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
        )
        return card

    @staticmethod
    def resolve_top(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def declare_attack(self, session, source: CardInstance, target: str = "B"):
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        engine._issue_attackers()
        result = session.act(
            "pilot:A", {"a": "attack", "atk": {source.ref: target}}
        )
        self.assertTrue(result.ok, result.summary)

    @staticmethod
    def prepare_main(session, seat: str = "A") -> None:
        engine = session.engine
        engine.state.active_player = seat
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority(seat)
        engine.pump()

    @staticmethod
    def cast_action(engine, card: CardInstance, seat: str = "A"):
        return next(
            action
            for action in engine._priority_action_hints(seat)["actions"]
            if action.get("action") == "cast" and action.get("card") == card.ref
        )

    @staticmethod
    def resolve_stack_with_passes(session) -> None:
        for _ in range(16):
            if not session.engine.state.stack:
                return
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Combat-entry lifecycle stack did not resolve")

    def cast_blitz(
        self,
        *,
        seed: int,
        players: int = 2,
    ):
        session = self.session(seed, players=players)
        engine = session.engine
        source = self.add_card(
            engine,
            name="Generic Blitz Adept",
            ref=f"blitz-source-{seed}",
        )
        engine.state.players["A"].mana_pool.update({"C": 1, "R": 1})
        self.prepare_main(session)
        action = self.cast_action(engine, source)
        self.assertIn(
            "blitz", {option["id"] for option in action["cost_options"]}
        )
        cast = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "cost_option": "blitz",
                "pay": "auto",
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        self.resolve_stack_with_passes(session)
        self.assertEqual("battlefield", source.zone)
        marker = source.annotations.get("fixed_blitz_designation")
        self.assertIsInstance(marker, dict)
        assert isinstance(marker, dict)
        self.assertIsInstance(marker.get("ability_semantic_key"), str, marker)
        self.assertEqual(1, len(engine.state.delayed_triggers))
        return session, source

    @staticmethod
    def remove_blitz_abilities(engine, source: CardInstance, *, suffix: str):
        return commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id=f"fixture:remove-blitz-abilities:{suffix}",
                source_id=f"fixture:remove-blitz-abilities-source:{suffix}",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        source.object_id,
                        source.logical_object_id,
                    ),
                ),
            ),
        )

    @staticmethod
    def make_blitz_noncreature(engine, source: CardInstance):
        return commit_continuous_effect(
            engine.state,
            ContinuousEffect(
                effect_id="fixture:blitz-noncreature",
                source_id="fixture:blitz-noncreature-source",
                layer=Layer.TYPE,
                sublayer="4",
                timestamp=engine._next_zone_timestamp(),
                operations=(ContinuousOperation("set_types", ["Artifact"]),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        source.object_id,
                        source.logical_object_id,
                    ),
                ),
            ),
        )

    @staticmethod
    def begin_end_step(engine) -> None:
        items = collect_trigger_items(
            engine,
            "step.begin",
            {"phase": "ending", "step": "end_step", "player": "A"},
        )
        enqueue_trigger_batch(engine, items)
        engine._stabilize()

    @staticmethod
    def blitz_draw_items(engine):
        return [
            item for item in engine.state.stack if item.context.get("blitz") is True
        ]

    def test_ninjutsu_offer_and_same_recipient_entry(self):
        session = self.session(260001)
        engine = session.engine
        ninja = self.add_card(
            engine,
            name="Generic Ninjutsu Adept",
            ref="ninjutsu-source",
        )
        attacker = self.add_card(
            engine,
            name="Generic Returning Attacker",
            ref="ninjutsu-returning-attacker",
            zone="battlefield",
        )
        attacker.attacking = "B"
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "combat"
        engine.state.step = "declare_blockers"
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={attacker.object_id: "B"},
            attack_target_context={
                attacker.object_id: {
                    "target": "B",
                    "kind": "player",
                    "defending_player": "B",
                }
            },
            defending_players=["B"],
        )
        engine.state.players["A"].mana_pool["U"] = 1
        engine.state.players["A"].mana_pool["C"] = 1
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        engine._grant_priority("A")
        engine.pump()

        action_id = f"activate:{ninja.ref}:ab1"
        legal = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]
        action = next(row for row in legal["actions"] if row["id"] == action_id)
        self.assertIn(
            attacker.ref,
            action["cost_summary"]["choose_cost"][0]["legal_refs"],
        )

        result = session.act(
            "pilot:A",
            {
                "action_id": action_id,
                "cost_objects": [attacker.ref],
                "pay": "auto",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", attacker.zone)
        self.assertEqual("hand", ninja.zone)
        self.assertEqual(set(engine.seats), set(ninja.revealed_to))
        for _ in range(8):
            if ninja.zone == "battlefield":
                break
            principal = session.pending_principals()[0]
            passed = session.act(principal, {"action_id": "pass"})
            self.assertTrue(passed.ok, passed.summary)
        self.assertEqual("battlefield", ninja.zone)
        self.assertTrue(ninja.tapped)
        self.assertEqual("B", ninja.attacking)

    def test_ninjutsu_reveal_counter_commander_and_unblocked_boundaries(self):
        session = self.session(260007)
        engine = session.engine
        ninja = self.add_card(
            engine,
            name="Generic Ninjutsu Adept",
            ref="countered-ninjutsu",
        )
        attacker = self.add_card(
            engine,
            name="Generic Returning Attacker",
            ref="countered-ninjutsu-return",
            zone="battlefield",
        )
        attacker.attacking = "B"
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "combat"
        engine.state.step = "declare_blockers"
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={attacker.object_id: "B"},
            attack_target_context={
                attacker.object_id: {
                    "target": "B",
                    "kind": "player",
                    "defending_player": "B",
                }
            },
            defending_players=["B"],
        )
        engine.state.players["A"].mana_pool.update({"C": 1, "U": 1})
        engine.permissions.invalidate_current()
        engine._grant_priority("A")
        engine.pump()
        activated = session.act(
            "pilot:A",
            {
                "action_id": f"activate:{ninja.ref}:ab1",
                "cost_objects": [attacker.ref],
                "pay": "auto",
            },
        )
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual(set(engine.seats), set(ninja.revealed_to))
        engine._counter_stack_item(
            engine.state.stack[-1].ref,
            reason="Ninjutsu counter witness",
            countered_by="B",
        )
        self.assertEqual("hand", ninja.zone)
        self.assertEqual([], ninja.revealed_to)

        commander_session = self.session(260008)
        commander_engine = commander_session.engine
        commander = self.add_card(
            commander_engine,
            name="Generic Commander Ninjutsu Adept",
            ref="commander-ninjutsu",
            zone="command",
            is_commander=True,
        )
        commander_attacker = self.add_card(
            commander_engine,
            name="Generic Returning Attacker",
            ref="commander-ninjutsu-return",
            zone="battlefield",
        )
        commander_attacker.attacking = "B"
        commander_engine.state.active_player = "A"
        commander_engine.state.started = True
        commander_engine.state.phase = "combat"
        commander_engine.state.step = "declare_blockers"
        commander_engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={commander_attacker.object_id: "B"},
            attack_target_context={
                commander_attacker.object_id: {
                    "target": "B",
                    "kind": "player",
                    "defending_player": "B",
                }
            },
            defending_players=["B"],
        )
        commander_engine.state.players["A"].mana_pool.update(
            {"U": 1, "B": 1}
        )
        commander_engine.permissions.invalidate_current()
        commander_engine._grant_priority("A")
        commander_engine.pump()
        command_action = f"activate:{commander.ref}:ab1"
        legal = commander_session.packet("pilot:A", full=True)["decision"][
            "ctx"
        ]["legal"]
        self.assertTrue(
            any(row["id"] == command_action for row in legal["actions"])
        )
        result = commander_session.act(
            "pilot:A",
            {
                "action_id": command_action,
                "cost_objects": [commander_attacker.ref],
                "pay": "auto",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_stack_with_passes(commander_session)
        self.assertEqual("battlefield", commander.zone)
        self.assertEqual("B", commander.attacking)

        blocked_session = self.session(260009)
        blocked_engine = blocked_session.engine
        blocked_ninja = self.add_card(
            blocked_engine,
            name="Generic Ninjutsu Adept",
            ref="blocked-ninjutsu",
        )
        blocked_attacker = self.add_card(
            blocked_engine,
            name="Generic Returning Attacker",
            ref="blocked-return",
            zone="battlefield",
        )
        blocker = self.add_card(
            blocked_engine,
            name="Generic Returning Attacker",
            ref="ninjutsu-blocker",
            seat="B",
            zone="battlefield",
        )
        blocked_attacker.attacking = "B"
        blocker.blocking = blocked_attacker.object_id
        blocked_engine.state.active_player = "A"
        blocked_engine.state.started = True
        blocked_engine.state.phase = "combat"
        blocked_engine.state.step = "declare_blockers"
        blocked_engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={blocked_attacker.object_id: "B"},
            attack_target_context={
                blocked_attacker.object_id: {
                    "target": "B",
                    "kind": "player",
                    "defending_player": "B",
                }
            },
            defending_players=["B"],
            blockers={blocked_attacker.object_id: [blocker.object_id]},
        )
        blocked_engine.state.players["A"].mana_pool.update({"C": 1, "U": 1})
        blocked_engine.permissions.invalidate_current()
        blocked_engine._grant_priority("A")
        blocked_engine.pump()
        blocked_legal = blocked_session.packet("pilot:A", full=True)[
            "decision"
        ]["ctx"]["legal"]
        self.assertFalse(
            any(
                row["id"] == f"activate:{blocked_ninja.ref}:ab1"
                for row in blocked_legal["actions"]
            )
        )

    def test_encore_exiles_source_and_creates_one_haste_copy_per_opponent(self):
        session = self.session(260002, players=3)
        engine = session.engine
        source = self.add_card(
            engine,
            name="Generic Encore Adept",
            ref="encore-source",
            zone="graveyard",
        )
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["A"].mana_pool["B"] = 1
        engine.state.players["A"].mana_pool["C"] = 2
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        engine._grant_priority("A")
        engine.pump()

        action_id = f"activate:{source.ref}:ab1"
        legal = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]
        self.assertTrue(any(row["id"] == action_id for row in legal["actions"]))
        result = session.act(
            "pilot:A", {"action_id": action_id, "pay": "auto"}
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("exile", source.zone)

        for _ in range(10):
            copies = [
                card
                for card in engine.state.cards.values()
                if card.zone == "battlefield"
                and card.is_token
                and card.oracle_id == source.oracle_id
            ]
            if len(copies) == 2:
                break
            principal = session.pending_principals()[0]
            passed = session.act(principal, {"action_id": "pass"})
            self.assertTrue(passed.ok, passed.summary)
        self.assertEqual(2, len(copies))
        self.assertTrue(all("Haste" in card.temporary_keywords for card in copies))
        self.assertEqual(
            {"B", "C"},
            {
                card.annotations["encore_attack_if_able"]["opponent"]
                for card in copies
            },
        )
        self.assertEqual(2, len(engine.state.delayed_triggers))

    def test_myriad_uses_per_opponent_optional_destinations_and_delayed_exile(self):
        session = self.session(260003, players=4)
        engine = session.engine
        source = self.add_card(
            engine,
            name="Generic Myriad Adept",
            ref="myriad-source",
            zone="battlefield",
        )
        walker_ref = engine.create_token(
            "C",
            name="Myriad planeswalker recipient",
            characteristics={
                "type_line": "Token Planeswalker — Test",
                "loyalty": "5",
            },
        )[0]
        self.declare_attack(session, source, target="B")
        self.assertTrue(
            engine.state.stack,
            [
                (item.label, item.semantic_key, item.context)
                for item in engine.state.stack
            ],
        )
        item = next(
            item
            for item in engine.state.stack
            if item.context.get("event") == "creature.attacks"
            and item.source_object_id == source.object_id
        )
        self.assertEqual("creature.attacks", item.context["event"])
        engine.move_card(
            source.object_id,
            "graveyard",
            reason="Myriad last-known copy witness",
            semantic_events=True,
        )
        self.resolve_top(engine)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        chosen = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "attacking": {
                    "myriad:C": walker_ref,
                    "myriad:D": "decline",
                },
            },
        )
        self.assertTrue(chosen.ok, chosen.summary)
        copies = [
            card
            for card in engine.state.cards.values()
            if card.zone == "battlefield"
            and card.is_token
            and card.oracle_id == source.oracle_id
        ]
        self.assertEqual(1, len(copies))
        copy = copies[0]
        self.assertTrue(copy.tapped)
        self.assertEqual(walker_ref, copy.attacking)
        self.assertEqual(1, len(engine.state.delayed_triggers))

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.phase = "combat"
        engine.state.step = "end_combat"
        engine.state.phase_index = 8
        engine._enter_step()
        while engine.state.stack:
            self.resolve_top(engine)
        self.assertEqual("outside", copy.zone)

    def test_blitz_cast_grants_haste_and_graveyard_trigger_draws(self):
        session = self.session(260004)
        engine = session.engine
        source = self.add_card(
            engine,
            name="Generic Blitz Adept",
            ref="blitz-source",
        )
        engine.state.players["A"].mana_pool.update({"C": 1, "R": 1})
        self.prepare_main(session)
        action = self.cast_action(engine, source)
        self.assertIn(
            "blitz", {option["id"] for option in action["cost_options"]}
        )
        cast = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "cost_option": "blitz",
                "pay": "auto",
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        self.resolve_stack_with_passes(session)
        self.assertEqual("battlefield", source.zone)
        self.assertIn("haste", engine._effective_card_data(source)["keywords"])
        self.assertIn("fixed_blitz_designation", source.annotations)
        self.assertEqual(1, len(engine.state.delayed_triggers))

        engine.change_control(
            source.object_id,
            "B",
            reason="Blitz last-known controller witness",
        )
        hand_before = len(engine.state.players["B"].zones["hand"])
        engine.move_card(
            source.object_id,
            "graveyard",
            reason="Blitz dies trigger witness",
            semantic_events=True,
            transition_kind=ZoneTransitionKind.SACRIFICE,
        )
        engine._stabilize()
        draw_trigger = next(
            item
            for item in engine.state.stack
            if item.context.get("blitz") is True
        )
        self.assertEqual("B", draw_trigger.controller)
        self.resolve_top(engine)
        self.assertEqual(
            hand_before + 1, len(engine.state.players["B"].zones["hand"])
        )

    def test_blitz_draw_is_suppressed_by_departure_lki_ability_removal(self):
        removed_session, removed = self.cast_blitz(seed=260011)
        removed_engine = removed_session.engine
        self.remove_blitz_abilities(
            removed_engine,
            removed,
            suffix="suppressed",
        )
        self.assertEqual(
            [],
            removed_engine._effective_card_data(removed)["ability_fragments"],
        )
        removed_engine.move_card(
            removed.object_id,
            "graveyard",
            reason="removed Blitz ability departure",
            semantic_events=True,
        )
        removed_engine._stabilize()
        self.assertEqual([], self.blitz_draw_items(removed_engine))

    def test_blitz_draw_uses_permanent_graveyard_event_after_type_loss(self):
        noncreature_session, noncreature = self.cast_blitz(seed=260012)
        noncreature_engine = noncreature_session.engine
        self.make_blitz_noncreature(noncreature_engine, noncreature)
        effective = noncreature_engine._effective_card_data(noncreature)
        self.assertNotIn(
            "creature",
            noncreature_engine._type_parts(effective["type_line"])[0],
        )
        hand_before = len(
            noncreature_engine.state.players["A"].zones["hand"]
        )
        noncreature_engine.move_card(
            noncreature.object_id,
            "graveyard",
            reason="noncreature Blitz permanent departure",
            semantic_events=True,
        )
        noncreature_engine._stabilize()
        self.assertEqual(1, len(self.blitz_draw_items(noncreature_engine)))
        self.resolve_top(noncreature_engine)
        self.assertEqual(
            hand_before + 1,
            len(noncreature_engine.state.players["A"].zones["hand"]),
        )

    def test_blitz_draw_ability_restoration_applies_to_later_departure(self):
        restored_session, restored = self.cast_blitz(seed=260013)
        restored_engine = restored_session.engine
        self.remove_blitz_abilities(
            restored_engine,
            restored,
            suffix="restored",
        )
        self.assertEqual(
            1,
            expire_end_of_turn_continuous_effects(restored_engine.state),
        )
        restored_characteristics = restored_engine._effective_card_data(restored)
        marker = restored.annotations["fixed_blitz_designation"]
        program = restored_engine.semantics.get(marker["ability_semantic_key"])
        self.assertIsNotNone(program, marker)
        assert program is not None
        self.assertTrue(
            program_has_current_ability_fragments(
                program,
                restored_characteristics,
            ),
            (program.key, restored_characteristics["ability_fragments"]),
        )
        restored_engine.move_card(
            restored.object_id,
            "graveyard",
            reason="restored Blitz ability departure",
            semantic_events=True,
        )
        restored_engine._stabilize()
        self.assertEqual(1, len(self.blitz_draw_items(restored_engine)))

    def test_blitz_delayed_sacrifice_requires_its_controller(self):
        session, source = self.cast_blitz(seed=260014, players=3)
        engine = session.engine
        engine.change_control(
            source.object_id,
            "B",
            reason="steal Blitz permanent before cleanup",
        )
        self.begin_end_step(engine)
        self.assertTrue(engine.state.stack)
        self.resolve_top(engine)
        self.assertEqual("battlefield", source.zone)
        self.assertEqual("B", source.controller)

        hand_before = len(engine.state.players["B"].zones["hand"])
        engine.move_card(
            source.object_id,
            "graveyard",
            reason="later B-controlled Blitz departure",
            semantic_events=True,
        )
        engine._stabilize()
        draw = self.blitz_draw_items(engine)
        self.assertEqual(1, len(draw))
        self.assertEqual("B", draw[0].controller)
        self.resolve_top(engine)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["B"].zones["hand"]),
        )

    def test_blitz_normal_delayed_cleanup_sacrifices_and_draws(self):
        session, source = self.cast_blitz(seed=260020)
        engine = session.engine
        hand_before = len(engine.state.players["A"].zones["hand"])
        self.begin_end_step(engine)
        self.resolve_stack_with_passes(session)
        self.assertEqual("graveyard", source.zone)
        self.assertEqual(
            hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )

    def test_blitz_cleanup_is_ability_independent_identity_safe_and_replays(self):
        removed_session, removed = self.cast_blitz(seed=260015)
        removed_engine = removed_session.engine
        self.remove_blitz_abilities(
            removed_engine,
            removed,
            suffix="cleanup-independent",
        )
        self.begin_end_step(removed_engine)
        self.resolve_top(removed_engine)
        self.assertEqual("graveyard", removed.zone)
        self.assertEqual([], self.blitz_draw_items(removed_engine))

        stale_session, stale = self.cast_blitz(seed=260016)
        stale_engine = stale_session.engine
        stale_engine.move_card(
            stale.object_id,
            "exile",
            reason="Blitz stale incarnation departure",
            semantic_events=True,
        )
        stale_engine.move_card(
            stale.object_id,
            "battlefield",
            reason="Blitz stale incarnation return",
            semantic_events=True,
        )
        current_identity = stale.logical_object_id
        self.begin_end_step(stale_engine)
        self.resolve_top(stale_engine)
        self.assertEqual("battlefield", stale.zone)
        self.assertEqual(current_identity, stale.logical_object_id)

        replay_session, replay_source = self.cast_blitz(
            seed=260017,
            players=4,
        )
        replay_engine = replay_session.engine
        self.begin_end_step(replay_engine)
        replay_session.initial_checkpoint = checkpoint_envelope(
            replay_engine.state
        )
        replay_session.commands.clear()
        replay_session.decisions.clear()
        before_rejection = authoritative_state_hash(replay_engine.state)
        rejected = replay_session.act("pilot:B", {"action_id": "pass"})
        self.assertFalse(rejected.ok)
        self.assertEqual(
            before_rejection,
            authoritative_state_hash(replay_engine.state),
        )
        self.resolve_stack_with_passes(replay_session)
        self.assertEqual("graveyard", replay_source.zone)
        self.resolve_stack_with_passes(replay_session)
        expected_hash = authoritative_state_hash(replay_engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-combat-entry-blitz"
            replay_session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_blitz_v1_runtime_payloads_preserve_historical_semantics(self):
        draw_session, draw_source = self.cast_blitz(seed=260018)
        draw_engine = draw_session.engine
        draw_source.annotations["fixed_blitz_designation"] = {
            "logical_object_id": draw_source.logical_object_id,
            "controller": "A",
        }
        self.remove_blitz_abilities(
            draw_engine,
            draw_source,
            suffix="legacy-v1",
        )
        draw_engine.move_card(
            draw_source.object_id,
            "graveyard",
            reason="historical Blitz v1 departure",
            semantic_events=True,
        )
        draw_engine._stabilize()
        self.assertEqual(1, len(self.blitz_draw_items(draw_engine)))

        delayed_session, delayed_source = self.cast_blitz(seed=260019)
        delayed_engine = delayed_session.engine
        delayed = delayed_engine.state.delayed_triggers[0]
        effect = delayed.stack_template["context"]["dynamic_effects"][0]
        effect.pop("required_controller")
        delayed_engine.change_control(
            delayed_source.object_id,
            "B",
            reason="historical Blitz v1 delayed effect",
        )
        self.begin_end_step(delayed_engine)
        self.resolve_top(delayed_engine)
        self.assertEqual("graveyard", delayed_source.zone)

    def test_web_slinging_and_sneak_pay_typed_return_costs(self):
        web_session = self.session(260005)
        web_engine = web_session.engine
        web = self.add_card(
            web_engine,
            name="Generic Web-Slinging Adept",
            ref="web-source",
        )
        tapped = self.add_card(
            web_engine,
            name="Generic Returning Attacker",
            ref="web-return",
            zone="battlefield",
        )
        tapped.tapped = True
        web_engine.state.players["A"].mana_pool.update({"C": 1, "G": 1})
        self.prepare_main(web_session)
        web_action = self.cast_action(web_engine, web)
        web_cast = web_session.act(
            "pilot:A",
            {
                "action_id": web_action["id"],
                "cost_option": "web-slinging",
                "return_cards": [tapped.ref],
                "pay": "auto",
            },
        )
        self.assertTrue(web_cast.ok, web_cast.summary)
        self.assertEqual("hand", tapped.zone)
        self.resolve_stack_with_passes(web_session)
        self.assertEqual("battlefield", web.zone)

        sneak_session = self.session(260006)
        sneak_engine = sneak_session.engine
        sneak = self.add_card(
            sneak_engine,
            name="Generic Sneak Adept",
            ref="sneak-source",
        )
        attacker = self.add_card(
            sneak_engine,
            name="Generic Returning Attacker",
            ref="sneak-return",
            zone="battlefield",
        )
        attacker.attacking = "B"
        sneak_engine.state.active_player = "A"
        sneak_engine.state.started = True
        sneak_engine.state.phase = "combat"
        sneak_engine.state.step = "declare_blockers"
        sneak_engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={attacker.object_id: "B"},
            attack_target_context={
                attacker.object_id: {
                    "target": "B",
                    "kind": "player",
                    "defending_player": "B",
                }
            },
            defending_players=["B"],
        )
        sneak_engine.state.players["A"].mana_pool.update({"C": 1, "B": 1})
        sneak_engine.permissions.invalidate_current()
        sneak_engine.state.priority_player = None
        sneak_engine._grant_priority("A")
        sneak_engine.pump()
        sneak_action = self.cast_action(sneak_engine, sneak)
        sneak_cast = sneak_session.act(
            "pilot:A",
            {
                "action_id": sneak_action["id"],
                "cost_option": "sneak",
                "return_cards": [attacker.ref],
                "pay": "auto",
            },
        )
        self.assertTrue(sneak_cast.ok, sneak_cast.summary)
        self.assertEqual("hand", attacker.zone)
        self.resolve_stack_with_passes(sneak_session)
        self.assertEqual("battlefield", sneak.zone)
        self.assertTrue(sneak.tapped)
        self.assertEqual("B", sneak.attacking)

    def test_ninjutsu_four_player_projection_save_load_and_exact_replay(self):
        session = self.session(260010, players=4)
        engine = session.engine
        ninja = self.add_card(
            engine,
            name="Generic Ninjutsu Adept",
            ref="replay-ninjutsu",
        )
        attacker = self.add_card(
            engine,
            name="Generic Returning Attacker",
            ref="replay-ninjutsu-return",
            zone="battlefield",
        )
        attacker.attacking = "C"
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "combat"
        engine.state.step = "declare_blockers"
        engine.state.combat = CombatState(
            attackers_declared=True,
            blockers_declared=True,
            had_attacking_creature=True,
            attackers={attacker.object_id: "C"},
            attack_target_context={
                attacker.object_id: {
                    "target": "C",
                    "kind": "player",
                    "defending_player": "C",
                }
            },
            defending_players=["B", "C", "D"],
        )
        engine.state.players["A"].mana_pool.update({"C": 1, "U": 1})
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        activated = session.act(
            "pilot:A",
            {
                "action_id": f"activate:{ninja.ref}:ab1",
                "cost_objects": [attacker.ref],
                "pay": "auto",
            },
        )
        self.assertTrue(activated.ok, activated.summary)
        for observer in ("B", "C", "D"):
            self.assertIn(
                "Generic Ninjutsu Adept",
                json.dumps(session.packet(f"pilot:{observer}", full=True)),
            )
        self.resolve_stack_with_passes(session)
        self.assertEqual("battlefield", ninja.zone)
        self.assertEqual("C", ninja.attacking)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-combat-entry-ninjutsu"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
