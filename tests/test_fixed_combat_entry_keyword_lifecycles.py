from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.deck import DeckLoader
from quorune.model import CardInstance, CombatState
from quorune.oracle_ir import (
    compile_oracle_card,
    register_generated_programs,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
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

    def test_blitz_cast_grants_haste_and_its_dies_trigger_draws(self):
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
