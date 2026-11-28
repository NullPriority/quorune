from __future__ import annotations

import unittest
import tempfile
from dataclasses import replace

from common import advance_fixture_turn, current_fixture_program_for_templates, keep_all, load_assets, make_session, pass_current
from quorune.model import StackItem, TurnEntry
from quorune.preflight import card_semantic_status
from quorune.projection import ProjectionCursor, StateProjector
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.saga_progression import advance_active_player_sagas
from quorune.trigger_processing import collect_ward_occurrences


class ExactMishraClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=seed,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        return session

    @staticmethod
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    @staticmethod
    def resolve_top(engine):
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def artifact_entry_program(self, engine, source):
        programs = [program for program in engine.semantics.programs_for_oracle(source.oracle_id)
                    if program.event == "permanent.enter"
                    and "trigger.event.qualified_zone_change" in program.capability_dependencies]
        self.assertEqual(1, len(programs))
        self.assertTrue(engine.semantic_program_is_current_trusted(programs[0]))
        return programs[0]

    @staticmethod
    def prepare_main(engine, seat: str = "A"):
        engine.state.active_player = seat
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_player = seat

    def test_emry_mills_and_grants_exact_graveyard_cast_permission(self):
        session = self.make_session(1100)
        engine = session.engine
        emry = self.card(engine, "A", "Emry, Lurker of the Loch")
        target = self.card(engine, "A", "Sol Ring")
        engine.move_card(
            emry.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        before_library = len(engine.state.players["A"].zones["library"])
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        self.assertEqual(
            before_library - 4,
            len(engine.state.players["A"].zones["library"]),
        )

        engine.move_card(target.object_id, "graveyard")
        emry.acquired_control_turn_count = -1
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": emry.ref,
                "ability": "ab3",
                "targets": [target.ref],
            },
        )
        self.resolve_top(engine)
        permission = target.annotations["temporary_play_permission"]
        self.assertEqual("A", permission["player"])
        self.assertEqual("graveyard", permission["zone"])
        self.assertFalse(permission["without_mana_cost"])

        self.prepare_main(engine)
        engine.state.players["A"].mana_pool["C"] = 1
        self.assertIn(
            target.ref,
            engine._priority_action_hints("A")["cast"],
        )

    def test_master_transmuter_pays_return_cost_and_puts_artifact(self):
        session = self.make_session(1101)
        engine = session.engine
        transmuter = self.card(engine, "A", "Master Transmuter")
        returned = self.card(engine, "A", "Sol Ring")
        deployed = self.card(engine, "A", "Portal to Phyrexia")
        for card in (transmuter, returned):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="A",
            )
        transmuter.acquired_control_turn_count = -1
        engine.move_card(deployed.object_id, "hand")
        engine.state.players["A"].mana_pool["U"] = 1
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": transmuter.ref,
                "ability": "ab1",
                "cost_cards": [returned.ref],
                "pay": "manual",
                "payment": {"U": 1},
            },
        )
        self.assertEqual("hand", returned.zone)
        self.resolve_top(engine)
        self.assertEqual(
            "semantic.choice", engine.state.pending_decision.kind
        )
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "card": deployed.ref,
                "plan": "DEVELOP_BOARD",
                "reason": "Put the selected artifact onto the battlefield.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", deployed.zone)

    def test_loki_scepter_temporary_control_untap_type_and_haste(self):
        session = self.make_session(1102)
        engine = session.engine
        scepter = self.card(engine, "A", "Loki's Scepter")
        victim = self.card(engine, "B", "Zimone and Dina")
        engine.move_card(
            victim.object_id,
            "battlefield",
            controller="B",
        )
        victim.tapped = True
        engine.move_card(
            scepter.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.assertTrue(engine._stabilize())
        self.assertEqual(
            "semantic.target", engine.state.pending_decision.kind
        )
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "targets": {"target_0": [victim.ref]},
                "plan": "TEMPORARY_THEFT",
                "reason": "Take the opposing creature for the turn.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(engine)
        self.assertEqual("A", victim.controller)
        self.assertFalse(victim.tapped)
        data = engine._effective_card_data(victim)
        self.assertIn("villain", engine._type_parts(data["type_line"])[1])
        self.assertIn("Haste", data["keywords"])

        engine._finish_cleanup()
        self.assertEqual("B", victim.controller)
        self.assertNotIn(
            "villain",
            engine._type_parts(
                engine._effective_card_data(victim)["type_line"]
            )[1],
        )

    def test_shuri_reduces_artifact_spells_and_copies_nonlegendary(self):
        session = self.make_session(1103)
        engine = session.engine
        shuri = self.card(engine, "A", "Shuri, Wakandan Inventor")
        signet = self.card(engine, "A", "Arcane Signet")
        source = self.card(engine, "A", "Strionic Resonator")
        copied = self.card(engine, "A", "The Mightstone and Weakstone")
        engine.move_card(shuri.object_id, "battlefield", controller="A")
        shuri.acquired_control_turn_count = -1
        engine.move_card(signet.object_id, "hand")
        self.prepare_main(engine)
        engine.state.players["A"].mana_pool["C"] = 1
        self.assertIn(
            signet.ref,
            engine._priority_action_hints("A")["cast"],
        )

        for card in (source, copied):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="A",
            )
        engine.state.players["A"].mana_pool["C"] = 1
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": shuri.ref,
                "ability": "ab2",
                "targets": [
                    {"group": "copying", "ref": source.ref},
                    {"group": "copied", "ref": copied.ref},
                ],
                "pay": "manual",
                "payment": {"C": 1},
            },
        )
        self.resolve_top(engine)
        data = engine._effective_card_data(source)
        self.assertEqual(
            "The Mightstone and Weakstone", data["name"]
        )
        self.assertNotIn("legendary", data["type_line"].casefold())
        engine._finish_cleanup()
        self.assertEqual(
            "Strionic Resonator",
            engine._effective_card_data(source)["name"],
        )

    def test_simulacrum_synthesizer_scry_and_construct_trigger(self):
        session = self.make_session(1104)
        engine = session.engine
        synthesizer = self.card(engine, "A", "Simulacrum Synthesizer")
        program = self.artifact_entry_program(engine, synthesizer)
        portal = self.card(engine, "A", "The Stasis Coffin")
        engine.move_card(
            synthesizer.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        self.assertEqual(
            "semantic.choice", engine.state.pending_decision.kind
        )
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": [],
                "plan": "CARD_SELECTION",
                "reason": "Keep both cards on top.",
            },
        )
        self.assertTrue(result.ok, result.summary)

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.move_card(
            portal.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual(1, len(engine.state.stack))
        self.assertEqual(program.key, engine.state.stack[0].semantic_key)
        self.resolve_top(engine)
        constructs = [
            card
            for card in engine.state.cards.values()
            if card.is_token
            and card.printed_name == "Construct Token"
            and card.controller == "A"
            and card.zone == "battlefield"
        ]
        self.assertEqual(1, len(constructs))
        artifact_count = sum(
            1
            for object_id in engine.state.players["A"].zones[
                "battlefield"
            ]
            if "artifact"
            in engine._type_parts(
                engine._effective_card_data(object_id)["type_line"]
            )[0]
        )
        self.assertEqual(
            artifact_count,
            engine._numeric_stat(constructs[0].object_id, "power"),
        )

    def test_stridehangar_adds_thopter_and_applies_anthem(self):
        session = self.make_session(1105)
        engine = session.engine
        automaton = self.card(engine, "A", "Stridehangar Automaton")
        engine.move_card(
            automaton.object_id,
            "battlefield",
            controller="A",
        )
        created = engine.create_token(
            "A",
            name="Treasure",
            characteristics={
                "type_line": "Token Artifact — Treasure",
            },
            reason="replacement characterization",
        )
        self.assertEqual(2, len(created))
        thopter = next(
            engine._resolve_object("A", ref)
            for ref in created
            if engine._resolve_object("A", ref).printed_name == "Thopter Token"
        )
        self.assertEqual(
            2, engine._numeric_stat(thopter.object_id, "power")
        )
        self.assertEqual(
            2, engine._numeric_stat(thopter.object_id, "toughness")
        )

    def test_worldwalker_adds_map_copies_token_and_map_explores(self):
        session = self.make_session(1106)
        engine = session.engine
        helm = self.card(engine, "A", "Worldwalker Helm")
        creature = self.card(engine, "A", "Goblin Engineer")
        land = self.card(engine, "A", "Island")
        engine.move_card(helm.object_id, "battlefield", controller="A")
        engine.move_card(creature.object_id, "battlefield", controller="A")
        created = engine.create_token(
            "A",
            name="Treasure",
            characteristics={
                "type_line": "Token Artifact — Treasure",
            },
            reason="replacement characterization",
        )
        self.assertEqual(2, len(created))
        treasure = next(
            engine._resolve_object("A", ref)
            for ref in created
            if engine._resolve_object("A", ref).printed_name == "Treasure"
        )
        first_map = next(
            engine._resolve_object("A", ref)
            for ref in created
            if engine._resolve_object("A", ref).printed_name == "Map Token"
        )

        helm.acquired_control_turn_count = -1
        self.prepare_main(engine)
        engine.state.players["A"].mana_pool.update({"C": 1, "U": 1})
        engine._activate(
            "A",
            {
                "source": helm.ref,
                "ability": "ab2",
                "targets": [treasure.ref],
                "pay": "manual",
                "payment": {"C": 1, "U": 1},
            },
        )
        self.resolve_top(engine)
        maps = [
            card
            for card in engine.state.cards.values()
            if card.is_token
            and card.printed_name == "Map Token"
            and card.zone == "battlefield"
        ]
        self.assertEqual(2, len(maps))

        engine.move_card(land.object_id, "library")
        library = engine.state.players["A"].zones["library"]
        library.remove(land.object_id)
        library.append(land.object_id)
        engine.state.players["A"].mana_pool["C"] = 1
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": first_map.ref,
                "ability": "ab1",
                "targets": [creature.ref],
                "pay": "manual",
                "payment": {"C": 1},
            },
        )
        self.assertEqual("outside", first_map.zone)
        self.resolve_top(engine)
        self.assertEqual("hand", land.zone)

    @staticmethod
    def stack_item(
        engine,
        *,
        ref: str,
        kind: str,
        controller: str,
        label: str,
        semantic_key: str,
        source_object_id: str | None = None,
        card_object_id: str | None = None,
        targets: list[str] | None = None,
    ) -> StackItem:
        item = StackItem(
            stack_id=engine._stable_runtime_id("stack", ref),
            ref=ref,
            kind=kind,
            controller=controller,
            label=label,
            semantic_key=semantic_key,
            source_object_id=source_object_id,
            card_object_id=card_object_id,
            targets=list(targets or []),
            visibility=list(engine.seats),
            context={
                "target_groups": (
                    {"target_0": list(targets or [])}
                    if targets
                    else {}
                ),
                "target_snapshots": {
                    target: engine._target_snapshot(target)
                    for target in targets or []
                },
                "targets_revalidated": False,
                "targets_chosen_at_creation": True,
            },
        )
        engine.state.stack.append(item)
        return item

    def test_lithoform_engine_copies_abilities_spells_and_permanent_spells(
        self,
    ):
        with self.subTest(kind="ability"):
            session = self.make_session(1108)
            engine = session.engine
            lithoform = self.card(engine, "A", "Lithoform Engine")
            synthesizer = self.card(
                engine, "A", "Simulacrum Synthesizer"
            )
            for card in (lithoform, synthesizer):
                engine.move_card(
                    card.object_id,
                    "battlefield",
                    controller="A",
                )
            target = self.stack_item(
                engine,
                ref="S-copy-ability",
                kind="triggered_ability",
                controller="A",
                label="Synthetic artifact trigger",
                semantic_key=self.artifact_entry_program(engine, synthesizer).key,
                source_object_id=synthesizer.object_id,
            )
            engine.state.players["A"].mana_pool["C"] = 2
            engine.state.priority_player = "A"
            engine._activate(
                "A",
                {
                    "source": lithoform.ref,
                    "ability": "ab1",
                    "targets": [target.ref],
                    "pay": "manual",
                    "payment": {"C": 2},
                },
            )
            self.resolve_top(engine)
            self.assertTrue(
                any(
                    item.context.get("copied_from_stack") == target.ref
                    for item in engine.state.stack
                )
            )

        with self.subTest(kind="instant"):
            session = self.make_session(1109)
            engine = session.engine
            lithoform = self.card(engine, "A", "Lithoform Engine")
            chaos_warp = self.card(engine, "A", "Chaos Warp")
            first_target = self.card(engine, "B", "Zimone and Dina")
            second_target = self.card(engine, "B", "Deathrite Shaman")
            engine.move_card(
                lithoform.object_id,
                "battlefield",
                controller="A",
            )
            for card in (first_target, second_target):
                engine.move_card(
                    card.object_id,
                    "battlefield",
                    controller="B",
                )
            engine.move_card(chaos_warp.object_id, "hand")
            engine.state.players["A"].mana_pool["R"] = 1
            engine.state.players["A"].mana_pool["C"] = 2
            engine.state.priority_player = "A"
            engine._cast(
                "A",
                {
                    "card": chaos_warp.ref,
                    "targets": [first_target.ref],
                    "pay": "manual",
                    "payment": {"R": 1, "C": 2},
                },
            )
            original = engine.state.stack[-1]
            lithoform.tapped = False
            engine.state.players["A"].mana_pool["C"] = 3
            engine.state.priority_player = "A"
            engine._activate(
                "A",
                {
                    "source": lithoform.ref,
                    "ability": "ab2",
                    "targets": [original.ref],
                    "pay": "manual",
                    "payment": {"C": 3},
                },
            )
            self.resolve_top(engine)
            self.assertEqual(
                "semantic.choice", engine.state.pending_decision.kind
            )
            result = session.act(
                "pilot:A",
                {
                    "action_id": "choose",
                    "targets": [second_target.ref],
                    "plan": "COPY_INTERACTION",
                    "reason": "Retarget the copied spell.",
                },
            )
            self.assertTrue(result.ok, result.summary)
            copied = next(
                item
                for item in engine.state.stack
                if item.context.get("copied_from_stack")
                == original.ref
            )
            self.assertEqual([second_target.ref], copied.targets)

        with self.subTest(kind="permanent"):
            session = self.make_session(1110)
            engine = session.engine
            lithoform = self.card(engine, "A", "Lithoform Engine")
            sol_ring = self.card(engine, "A", "Sol Ring")
            engine.move_card(
                lithoform.object_id,
                "battlefield",
                controller="A",
            )
            engine.move_card(sol_ring.object_id, "hand")
            self.prepare_main(engine)
            engine.state.players["A"].mana_pool["C"] = 1
            engine._cast(
                "A",
                {
                    "card": sol_ring.ref,
                    "pay": "manual",
                    "payment": {"C": 1},
                },
            )
            original = engine.state.stack[-1]
            lithoform.tapped = False
            engine.state.players["A"].mana_pool["C"] = 4
            engine.state.priority_player = "A"
            engine._activate(
                "A",
                {
                    "source": lithoform.ref,
                    "ability": "ab3",
                    "targets": [original.ref],
                    "pay": "manual",
                    "payment": {"C": 4},
                },
            )
            self.resolve_top(engine)
            copy_item = engine.state.stack[-1]
            self.assertTrue(copy_item.context["copy_permanent_spell"])
            self.resolve_top(engine)
            rings = [
                card
                for card in engine.state.cards.values()
                if card.printed_name == "Sol Ring"
                and card.zone == "battlefield"
                and card.controller == "A"
            ]
            self.assertEqual(1, len(rings))
            self.assertTrue(rings[0].is_token)

    def test_scientist_supreme_copies_artifact_ability_once_on_own_turn(
        self,
    ):
        session = self.make_session(1111)
        engine = session.engine
        scientist = self.card(
            engine, "A", "Scientist Supreme of A.I.M."
        )
        synthesizer = self.card(
            engine, "A", "Simulacrum Synthesizer"
        )
        for card in (scientist, synthesizer):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="A",
            )
        self.prepare_main(engine)
        target = self.stack_item(
            engine,
            ref="S-scientist-target",
            kind="triggered_ability",
            controller="A",
            label="Artifact-source trigger",
            semantic_key=self.artifact_entry_program(engine, synthesizer).key,
            source_object_id=synthesizer.object_id,
        )
        before_life = engine.state.players["A"].life
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": scientist.ref,
                "ability": "ab1",
                "targets": [target.ref],
            },
        )
        self.resolve_top(engine)
        self.assertEqual(before_life - 2, engine.state.players["A"].life)
        ability = next(
            ability
            for ability in engine._activated_abilities(scientist)
            if ability.ability_id == "ab1"
        )
        self.assertEqual(
            "unavailable",
            engine._ability_availability("A", scientist, ability)[0],
        )
        self.assertTrue(
            any(
                item.context.get("copied_from_stack") == target.ref
                for item in engine.state.stack
            )
        )

    def test_strionic_resonator_only_copies_controlled_trigger(self):
        session = self.make_session(1112)
        engine = session.engine
        resonator = self.card(engine, "A", "Strionic Resonator")
        synthesizer = self.card(
            engine, "A", "Simulacrum Synthesizer"
        )
        for card in (resonator, synthesizer):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="A",
            )
        trigger = self.stack_item(
            engine,
            ref="S-strionic-trigger",
            kind="triggered_ability",
            controller="A",
            label="Controlled trigger",
            semantic_key=self.artifact_entry_program(engine, synthesizer).key,
            source_object_id=synthesizer.object_id,
        )
        activated = self.stack_item(
            engine,
            ref="S-strionic-activated",
            kind="activated_ability",
            controller="A",
            label="Controlled activated ability",
            semantic_key=self.artifact_entry_program(engine, synthesizer).key,
            source_object_id=synthesizer.object_id,
        )
        engine.state.players["A"].mana_pool["C"] = 2
        engine.state.priority_player = "A"
        hints = engine._priority_action_hints("A")
        action = next(
            row
            for row in hints["actions"]
            if row["id"] == f"activate:{resonator.ref}:ab1"
        )
        self.assertIn(trigger.ref, action["target_schema"]["legal_refs"])
        self.assertNotIn(
            activated.ref, action["target_schema"]["legal_refs"]
        )
        engine._activate(
            "A",
            {
                "source": resonator.ref,
                "ability": "ab1",
                "targets": [trigger.ref],
                "pay": "manual",
                "payment": {"C": 2},
            },
        )
        self.resolve_top(engine)
        self.assertTrue(
            any(
                item.context.get("copied_from_stack") == trigger.ref
                for item in engine.state.stack
            )
        )

    def test_deflecting_swat_free_cost_and_exact_retarget(self):
        session = self.make_session(1113)
        engine = session.engine
        swat = self.card(engine, "A", "Deflecting Swat")
        commander = self.card(engine, "A", "Mishra, Eminent One")
        first = self.card(engine, "A", "Sol Ring")
        second = self.card(engine, "A", "Lightning Greaves")
        for card in (commander, first, second):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="A",
            )
        engine.move_card(swat.object_id, "hand")
        targeted = self.stack_item(
            engine,
            ref="S-retargeted",
            kind="spell",
            controller="B",
            label="Synthetic Assassin's Trophy",
            semantic_key=(
                "ac10d218-f9a6-4058-9cda-a15ca1b0b7b5:"
                "spell:front"
            ),
            targets=[first.ref],
        )
        engine.state.priority_player = "A"
        engine._cast(
            "A",
            {
                "card": swat.ref,
                "targets": [targeted.ref],
                "cost_option": "commander_free",
            },
        )
        self.assertEqual(0, sum(engine.state.players["A"].mana_pool.values()))
        self.resolve_top(engine)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "targets": [second.ref],
                "plan": "REDIRECT_INTERACTION",
                "reason": "Move the effect to the other legal permanent.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([second.ref], targeted.targets)
        self.assertEqual("graveyard", swat.zone)

    def test_fomori_vault_counts_artifacts_and_bottoms_rest_randomly(self):
        session = self.make_session(1114)
        engine = session.engine
        vault = self.card(engine, "A", "Fomori Vault")
        program = next(
            row for row in engine.semantics.programs_for_oracle(vault.oracle_id)
            if row.ability_id == "ability:ab2"
        )
        self.assertEqual("variable_library_selection", program.effects[0]["op"])
        self.assertTrue(engine.semantic_program_is_current_trusted(program))
        top = [
            self.card(engine, "A", name)
            for name in (
                "Daretti, Scrap Savant",
                "Demonic Junker",
                "Transmute Artifact",
            )
        ]
        discard = self.card(engine, "A", "Lightning Greaves")
        artifacts = [
            self.card(engine, "A", "Sol Ring"),
            self.card(engine, "A", "Sensei's Divining Top"),
            self.card(engine, "A", "Panharmonicon"),
        ]
        for card in (vault, *artifacts):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="A",
            )
        for card in top:
            engine.move_card(card.object_id, "library")
        library = engine.state.players["A"].zones["library"]
        for card in top:
            library.remove(card.object_id)
        library.extend(card.object_id for card in top)
        engine.move_card(discard.object_id, "hand")
        engine.state.players["A"].mana_pool["C"] = 3
        engine.state.priority_player = "A"
        engine._grant_priority("A")
        engine.pump()
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action_id = f"activate:{vault.ref}:ab2"
        self.assertTrue(any(row["id"] == action_id for row in actions))
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        activated = session.act(
            "pilot:A",
            {
                "action_id": action_id,
                "cost_cards": [discard.ref],
                "pay": "manual",
                "payment": {"C": 3},
            },
        )
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual("graveyard", discard.zone)
        self.assertTrue(vault.tapped)
        self.assertFalse(any(engine.state.players["A"].mana_pool.values()))
        for _ in range(8):
            if engine.state.pending_decision.kind == "semantic.choice":
                break
            pass_current(session)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        chosen = top[1]
        choice_packet = session.packet("pilot:A", full=True)["decision"]
        legal_refs = choice_packet["ctx"]["legal_actions"][0]["choice_schema"]["legal_refs"]
        self.assertEqual(set(card.ref for card in top), set(legal_refs))
        opponent_packet = str(session.packet("pilot:B", full=True))
        for card in top:
            self.assertNotIn(card.ref, opponent_packet)
            self.assertNotIn(card.printed_name, opponent_packet)
        chosen_id = chosen.object_id
        before_decline = authoritative_state_hash(engine.state)
        declined = session.act("pilot:A", {"action_id": "choose", "cards": []})
        self.assertFalse(declined.ok)
        self.assertEqual(before_decline, authoritative_state_hash(engine.state))
        top = [engine.state.cards[card.object_id] for card in top]
        chosen = engine.state.cards[chosen_id]
        library = engine.state.players["A"].zones["library"]
        result = session.act("pilot:A", {"action_id": "choose", "cards": [chosen.ref]})
        self.assertTrue(result.ok, result.summary)
        for _ in range(8):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        chosen = engine.state.cards[chosen_id]
        library = engine.state.players["A"].zones["library"]
        self.assertEqual("hand", chosen.zone)
        self.assertEqual(
            {top[0].object_id, top[2].object_id},
            set(library[:2]),
        )
        expected = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_fomori_vault_zero_artifacts_resolves_without_a_choice(self):
        session = self.make_session(11141)
        engine = session.engine
        vault = self.card(engine, "A", "Fomori Vault")
        discard = self.card(engine, "A", "Lightning Greaves")
        engine.move_card(vault.object_id, "battlefield", controller="A")
        engine.move_card(discard.object_id, "hand")
        engine.state.players["A"].mana_pool["C"] = 3
        engine.state.priority_player = "A"
        engine._grant_priority("A")
        engine.pump()
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action_id = f"activate:{vault.ref}:ab2"
        self.assertTrue(any(row["id"] == action_id for row in actions))
        before_library = tuple(engine.state.players["A"].zones["library"])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        activated = session.act(
            "pilot:A",
            {
                "action_id": action_id,
                "cost_cards": [discard.ref],
                "pay": "manual",
                "payment": {"C": 3},
            },
        )
        self.assertTrue(activated.ok, activated.summary)
        for _ in range(8):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertNotEqual("semantic.choice", engine.state.pending_decision.kind)
        self.assertEqual(before_library, tuple(engine.state.players["A"].zones["library"]))
        self.assertEqual("graveyard", discard.zone)

    def test_fomori_vault_legacy_choice_record_replays(self):
        session = self.make_session(11142)
        engine = session.engine
        vault = self.card(engine, "A", "Fomori Vault")
        current = next(
            row for row in engine.semantics.programs_for_oracle(vault.oracle_id)
            if row.ability_id == "ability:ab2"
        )
        legacy = replace(
            current,
            label="Fomori Vault selection activation",
            effects=[{"op": "fomori_vault"}],
            trust_level="trusted",
            requires_arbiter=False,
            provenance={
                "authored_by": "v0.8-exact-deck-closure",
                "review_status": "reviewed",
                "source_oracle_hash": current.provenance["source_oracle_hash"],
                "source_rulings_hash": current.provenance["source_rulings_hash"],
            },
            tests=("test_fomori_vault_counts_artifacts_and_bottoms_rest_randomly",),
            cost_schema=None,
            capability_dependencies=(),
            capability_closure=None,
            coverage=("activated_ability", "discard_cost", "artifact_count",
                      "private_library_look", "random_bottom_order"),
        )
        engine.semantics.put(legacy)
        self.assertEqual("fomori_vault", engine.semantics.get(legacy.key).effects[0]["op"])

        top = [
            self.card(engine, "A", name)
            for name in ("Daretti, Scrap Savant", "Demonic Junker", "Transmute Artifact")
        ]
        discard = self.card(engine, "A", "Lightning Greaves")
        artifacts = [
            self.card(engine, "A", name)
            for name in ("Sol Ring", "Sensei's Divining Top", "Panharmonicon")
        ]
        for card in (vault, *artifacts):
            engine.move_card(card.object_id, "battlefield", controller="A")
        for card in top:
            engine.move_card(card.object_id, "library")
        library = engine.state.players["A"].zones["library"]
        for card in top:
            library.remove(card.object_id)
        library.extend(card.object_id for card in top)
        engine.move_card(discard.object_id, "hand")
        engine.state.players["A"].mana_pool["C"] = 3
        engine.state.priority_player = "A"
        engine._grant_priority("A")
        engine.pump()
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action_id = f"activate:{vault.ref}:ab2"
        self.assertTrue(any(row["id"] == action_id for row in actions))
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        activated = session.act("pilot:A", {
            "action_id": action_id, "cost_cards": [discard.ref],
            "pay": "manual", "payment": {"C": 3},
        })
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual("graveyard", discard.zone)
        for _ in range(8):
            if engine.state.pending_decision.kind == "semantic.choice":
                break
            pass_current(session)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        result = session.act("pilot:A", {
            "action_id": "choose", "card": top[1].ref,
            "plan": "SELECT_CARD", "reason": "Keep the selected card.",
        })
        self.assertTrue(result.ok, result.summary)
        for _ in range(8):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertFalse(engine.state.stack)
        self.assertEqual("hand", top[1].zone)
        expected = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_mindslaver_routes_controlled_turn_to_controller_pilot(self):
        session = self.make_session(1115)
        engine = session.engine
        mindslaver = self.card(engine, "A", "Mindslaver")
        engine.move_card(
            mindslaver.object_id,
            "battlefield",
            controller="A",
        )
        engine.state.players["A"].mana_pool["C"] = 4
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": mindslaver.ref,
                "ability": "ab1",
                "targets": ["B"],
                "pay": "manual",
                "payment": {"C": 4},
            },
        )
        self.assertEqual("graveyard", mindslaver.zone)
        self.resolve_top(engine)
        self.assertEqual(
            "A",
            engine.state.players["B"].stats["next_turn_controlled_by"],
        )
        engine.state.players["B"].stats["turn_controlled_by"] = "A"
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.permissions.issue(
            kind="priority",
            role="pilot",
            actors=["B"],
            allowed_actions=["pass"],
            payload_by_actor={"B": {"legal_actions": []}},
        )
        capability = engine.permissions.capability_for("pilot:A")
        self.assertIsNotNone(capability)
        self.assertEqual("B", capability.actor)
        self.assertIsNone(engine.permissions.capability_for("pilot:B"))
        packet = StateProjector(self.db, engine.state).packet(
            "pilot:A",
            ProjectionCursor(),
            force_full=True,
        )
        view = packet["state"]
        self.assertIn("hand", view["players"]["B"])
        self.assertIn("hand", view["players"]["A"])

    def assert_current_mindslaver_activation(self, engine, source):
        return current_fixture_program_for_templates(self, engine, source, {"fixed-next-turn-control-v1"})

    def test_mindslaver_concession_stays_with_own_principal_and_replays(self):
        session = self.make_session(11151)
        engine = session.engine
        mindslaver = self.card(engine, "A", "Mindslaver")
        land = next(
            card for card in engine.state.cards.values()
            if card.owner == "B" and engine.card_record(card).is_land
        )
        self.assert_current_mindslaver_activation(engine, mindslaver)
        engine.move_card(mindslaver.object_id, "battlefield", controller="A")
        engine.move_card(land.object_id, "hand")
        self.prepare_main(engine)
        engine.state.players["A"].mana_pool["C"] = 4
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        initial_capability = engine.permissions.capability_for("pilot:A")
        self.assertIsNotNone(initial_capability)
        original_hash = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {
            "action_id": f"activate:{mindslaver.ref}:ab1", "targets": ["missing-player"],
            "pay": "manual", "payment": {"C": 4},
        })
        self.assertFalse(rejected.ok)
        self.assertEqual(original_hash, authoritative_state_hash(session.state))
        self.assertFalse(session.commands)
        activated = session.act("pilot:A", {
            "action_id": f"activate:{mindslaver.ref}:ab1",
            "targets": ["B"],
            "pay": "manual",
            "payment": {"C": 4},
        })
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual("graveyard", engine.state.cards[mindslaver.object_id].zone)
        for _ in range(60):
            delegated = engine.permissions.capability_for("pilot:A")
            if delegated is not None and delegated.actor == "B":
                break
            pass_current(session)
        else:
            self.fail("The normal priority loop never entered the controlled turn")

        self.assertEqual("B", engine.state.active_player)
        self.assertEqual("A", engine.state.players["B"].stats["turn_controlled_by"])
        self.assertEqual(["pilot:A", "pilot:B"], session.pending_principals())
        self.assertNotIn("concede", delegated.allowed_actions)
        delegated_view = session.packet("pilot:A", full=True)["decision"]
        self.assertEqual("B", delegated_view["actor"])
        self.assertNotIn("concede", {row["action"] for row in delegated_view["legal_actions"]})
        self.assertNotIn("concede", {row["action"] for row in delegated_view["ctx"]["legal"]["actions"]})
        own_view = session.packet("pilot:B", full=True)["decision"]
        self.assertEqual(["concede"], own_view["allow"])
        self.assertEqual({"legal"}, set(own_view["ctx"]))
        self.assertEqual(["concede"], [row["action"] for row in own_view["legal_actions"]])

        before = authoritative_state_hash(engine.state)
        accepted = len(session.commands)
        for token, principal in (
            (delegated.token, "pilot:A"),
            (own_view["cap"], "pilot:A"),
            (initial_capability.token, "pilot:A"),
        ):
            rejected = engine.try_submit(
                token=token, principal=principal, action="concede",
                payload={"confirm_concede": True},
            )
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(engine.state))
        mismatched = engine.try_submit(
            token=delegated.token, principal="pilot:A", action="pass",
            payload={"action": "concede", "confirm_concede": True},
        )
        self.assertFalse(mismatched.ok)
        self.assertIn("Payload action", mismatched.summary)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertFalse(engine.permissions.capability_for("pilot:A").consumed)
        rejected = session.act("pilot:A", {
            "action_id": "concede", "confirm_concede": True,
        })
        self.assertFalse(rejected.ok)
        self.assertEqual(accepted, len(session.commands))
        self.assertEqual(before, authoritative_state_hash(engine.state))

        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            loaded = type(session).load(self.db, directory)
            restored = loaded.packet("pilot:B", full=True)["decision"]
            self.assertNotEqual(own_view["cap"], restored["cap"])
            self.assertEqual(["concede"], restored["allow"])
            self.assertEqual(before, authoritative_state_hash(loaded.state))
            conceded = loaded.act("pilot:B", {
                "action_id": "concede", "confirm_concede": True,
            })
            self.assertTrue(conceded.ok, conceded.summary)
            self.assertFalse(loaded.state.players["B"].in_game)
            self.assertTrue(loaded.state.players["A"].in_game)
            self.assertEqual("pilot:B", loaded.commands[-1]["principal"])
            self.assertEqual("B", loaded.commands[-1]["actor"])
            expected = authoritative_state_hash(loaded.state)
            loaded.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_mindslaver_departure_restores_target_authority_and_replays(self):
        import json

        session = make_session(self.db, self.mishra, self.zimone, players=4, seed=629410)
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        source = self.card(engine, "A", "Mindslaver")
        self.assert_current_mindslaver_activation(engine, source)
        engine.move_card(source.object_id, "battlefield", controller="A")
        secret = next(card for card in engine.state.cards.values()
                      if card.owner == "B" and card.zone == "library" and engine.card_record(card).is_land)
        engine.move_card(secret.object_id, "hand")
        for owner in ("A", "B"):
            hand = engine.state.players[owner].zones["hand"]
            for object_id in tuple(hand):
                if object_id != secret.object_id and len(hand) > 5:
                    engine.move_card(object_id, "library")
        self.prepare_main(engine)
        engine.state.players["A"].mana_pool["C"] = 4
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        def command(game, principal, payload):
            result = game.act(principal, payload)
            self.assertTrue(result.ok, result.summary)

        def priority_for(seat):
            for _ in range(120):
                pending = session.state.pending_decision
                self.assertEqual("priority", pending.kind)
                cap, = (cap for cap in engine.permissions.pending_capabilities() if "pass" in cap.allowed_actions)
                if session.state.active_player == "B" and cap.actor == seat:
                    return cap
                command(session, cap.principal, {"action_id": "pass"})
            self.fail("Did not reach the requested rules actor in B's turn")

        def visible(game, pilot):
            return secret.ref in json.dumps(game.packet(pilot, full=True))

        self.assertFalse(visible(session, "pilot:A"))
        command(session, "pilot:A", {
            "action_id": f"activate:{source.ref}:ab1", "targets": ["B"], "pay": "manual", "payment": {"C": 4},
        })
        delegated = priority_for("B")
        self.assertEqual("pilot:A", delegated.principal)
        self.assertTrue(visible(session, "pilot:A"))
        self.assertFalse(visible(session, "pilot:C"))

        with tempfile.TemporaryDirectory() as directory:
            fixture = f"{directory}/delegated"
            session.save(fixture, include_review=False)
            stale_game = type(session).load(self.db, fixture)
            stale = stale_game.engine.permissions.capability_for("pilot:A")
            self.assertEqual("B", stale.actor)
            # Retain an already issued credential across a simulated departure
            # at the authorization boundary. The independent native path below
            # also performs an actual concession and obtains fresh priority.
            stale_game.state.players["A"].in_game = False
            before = authoritative_state_hash(stale_game.state)
            self.assertFalse(visible(stale_game, "pilot:A"))
            self.assertIsNone(stale_game.packet("pilot:A", full=True)["decision"])
            rejected = stale_game.engine.try_submit(token=stale.token, principal="pilot:A", action="pass", payload={})
            self.assertFalse(rejected.ok)
            self.assertIn("live authority", rejected.summary)
            self.assertEqual(before, authoritative_state_hash(stale_game.state))
            self.assertFalse(stale_game.state.capabilities[stale.token].consumed)
            stale_game.engine.permissions.reissue_pending()
            self.assertEqual(["pilot:B"], stale_game.pending_principals())
            self.assertIn("pass", stale_game.packet("pilot:B", full=True)["decision"]["allow"])

            own = priority_for("A")
            self.assertEqual("pilot:A", own.principal)
            self.assertTrue(visible(session, "pilot:A"))
            command(session, "pilot:A", {"action_id": "concede", "confirm_concede": True})
            self.assertFalse(session.state.players["A"].in_game)
            self.assertTrue(session.state.players["B"].in_game)
            self.assertEqual("A", session.state.players["B"].stats["turn_controlled_by"])
            self.assertEqual("pilot:B", priority_for("B").principal)
            self.assertEqual(["pilot:B"], session.pending_principals())
            self.assertTrue(visible(session, "pilot:B"))
            for principal in ("pilot:A", "pilot:C", "spectator"):
                self.assertFalse(visible(session, principal))
                self.assertIsNone(session.packet(principal, full=True)["decision"])

            base = authoritative_state_hash(session.state)
            accepted = len(session.commands)
            for principal in ("pilot:A", "pilot:C"):
                self.assertFalse(session.act(principal, {"action_id": "pass"}).ok)
                self.assertEqual(base, authoritative_state_hash(session.state))
                self.assertEqual(accepted, len(session.commands))
            native = f"{directory}/departure"
            session.save(native, include_review=False)
            restored = type(session).load(self.db, native)
            self.assertEqual(["pilot:B"], restored.pending_principals())
            self.assertFalse(visible(restored, "pilot:A"))
            command(restored, "pilot:B", {"action_id": "pass"})
            self.assertEqual(("pilot:B", "B"), (restored.commands[-1]["principal"], restored.commands[-1]["actor"]))
            restored.save(native, include_review=False)
            replay = replay_record(native, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(authoritative_state_hash(restored.state), replay["final_state_hash"])

    def test_mindslaver_marked_control_survives_repeated_cleanup_and_replays(self):
        import json

        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.turn_priority_model import (
            NEXT_TURN_CONTROL_LIFETIME_VERSION_FIELD, TURN_CONTROL_LIFETIME_VERSION_FIELD,
            read_turn_control_lifetime_version,
        )
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests

        self.assertIsNone(read_turn_control_lifetime_version({}))
        for value in (None, True, 2, "1"):
            with self.subTest(invalid_lifetime=value), self.assertRaisesRegex(ValueError, "lifetime version"):
                read_turn_control_lifetime_version({TURN_CONTROL_LIFETIME_VERSION_FIELD: value})

        session = self.make_session(11152)
        engine = session.engine
        self.registry = load_default_capability_registry()
        self.register_fixture_cards_on_add = True
        add = BoundEffectProgramRuntimeTests.add
        source = self.card(engine, "A", "Mindslaver")
        self.assert_current_mindslaver_activation(engine, source)
        engine.move_card(source.object_id, "battlefield", controller="A")
        madness = add(self, engine, "Basking Rootwalla", seat="B", zone="hand", ref="CLEANUP-MADNESS")
        flashback = add(self, engine, "Think Twice", seat="B", zone="graveyard", ref="CLEANUP-FLASHBACK")
        for index in range(3):
            add(self, engine, "Island", seat="B", ref=f"CLEANUP-ISLAND-{index}")
        player = engine.state.players["B"]
        secret = engine.state.cards[player.zones["library"][-1]]
        engine.move_card(secret.object_id, "hand")
        while len(player.zones["hand"]) < 8:
            engine.move_card(player.zones["library"][-1], "hand")
        self.prepare_main(engine)
        engine.state.players["A"].mana_pool["C"] = 4
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        def command(game, principal, payload):
            outcome = game.act(principal, payload)
            self.assertTrue(outcome.ok, outcome.summary)

        def until(game, predicate):
            for _ in range(100):
                if predicate(game):
                    return
                self.assertEqual("priority", game.state.pending_decision.kind)
                cap, = (cap for cap in game.engine.permissions.pending_capabilities() if "pass" in cap.allowed_actions)
                command(game, cap.principal, {"action_id": "pass"})
            self.fail("The expected real cleanup boundary was never reached")

        def discard(game, *required):
            decision = game.packet("pilot:A", full=True)["decision"]
            self.assertEqual(("cleanup.discard", "B"), (decision["kind"], decision["actor"]))
            legal = game.state.pending_decision.payload_by_actor["B"]
            refs = [*required, *(row["id"] for row in legal["hand"] if row["id"] not in (*required, secret.ref))]
            command(game, "pilot:A", {"a": "discard", "cards": refs[:legal["count"]]})
            self.assertEqual(len(game.state.players["B"].zones["hand"]), game.state.players["B"].max_hand_size)

        command(session, "pilot:A", {
            "action_id": f"activate:{source.ref}:ab1", "targets": ["B"], "pay": "manual", "payment": {"C": 4},
        })
        until(session, lambda game: game.state.players["B"].stats.get(NEXT_TURN_CONTROL_LIFETIME_VERSION_FIELD) == 1)
        self.assertNotIn(TURN_CONTROL_LIFETIME_VERSION_FIELD, session.state.players["B"].stats)
        until(session, lambda game: game.state.pending_decision.kind == "cleanup.discard")
        turn = session.state.turn_sequence
        first_discard = session.state.pending_decision.decision_id
        self.assertEqual("B", session.state.active_player)
        discard(session, madness.ref)
        first = session.packet("pilot:A", full=True)["decision"]
        self.assertEqual(("priority", "B"), (first["kind"], first["actor"]))
        self.assertEqual(1, session.state.players["B"].stats[TURN_CONTROL_LIFETIME_VERSION_FIELD])
        self.assertIn(secret.ref, json.dumps(session.packet("pilot:A", full=True)))
        self.assertEqual("exile", session.state.cards[madness.object_id].zone)
        offer = next(row for row in first["ctx"]["legal"]["actions"] if row["id"] == f"cast:{flashback.ref}")
        command(session, "pilot:A", {"action_id": offer["id"], "cost_option": "flashback", "pay": "auto"})
        self.assertEqual("B", session.state.stack[-1].controller)
        until(session, lambda game: game.state.pending_decision.kind == "semantic.choice")
        self.assertEqual("B", session.packet("pilot:A", full=True)["decision"]["actor"])
        command(session, "pilot:A", {"action_id": "choose", "choice": "decline"})
        self.assertEqual("graveyard", session.state.cards[madness.object_id].zone)
        self.assertEqual("exile", session.state.cards[flashback.object_id].zone)
        until(session, lambda game: game.state.pending_decision.kind == "cleanup.discard")
        self.assertEqual(turn, session.state.turn_sequence)
        self.assertNotEqual(first_discard, session.state.pending_decision.decision_id)
        self.assertEqual(("ending", "cleanup"), (session.state.phase, session.state.step))

        with tempfile.TemporaryDirectory() as record:
            session.save(record, include_review=False)
            restored = type(session).load(self.db, record)
            self.assertEqual(1, restored.state.players["B"].stats[TURN_CONTROL_LIFETIME_VERSION_FIELD])
            discard(restored)
            until(restored, lambda game: game.state.turn_sequence > turn)
            self.assertEqual(turn + 1, restored.state.turn_sequence)
            self.assertNotIn("turn_controlled_by", restored.state.players["B"].stats)
            self.assertNotIn(TURN_CONTROL_LIFETIME_VERSION_FIELD, restored.state.players["B"].stats)
            self.assertNotIn(secret.ref, json.dumps(restored.packet("pilot:A", full=True)))
            restored.save(record, include_review=False)
            actual = replay_record(record, self.db, verify=True)
            self.assertTrue(actual["ok"], actual)
            self.assertEqual(authoritative_state_hash(restored.state), actual["final_state_hash"])

    def test_roaming_throne_type_trigger_multiplier_and_ward(self):
        session = self.make_session(1116)
        engine = session.engine
        throne = self.card(engine, "A", "Roaming Throne")
        engineer = self.card(engine, "A", "Goblin Engineer")
        engine.move_card(throne.object_id, "hand")
        self.prepare_main(engine)
        engine.state.players["A"].mana_pool["C"] = 4
        engine._grant_priority("A")
        engine.pump()
        offered = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action_id = f"cast:{throne.ref}"
        self.assertTrue(any(row["id"] == action_id for row in offered))
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        cast = session.act(
            "pilot:A",
            {
                "action_id": action_id,
                "pay": "manual",
                "payment": {"C": 4},
            },
        )
        self.assertTrue(cast.ok, cast.summary)
        choices = {"semantic.choice", "replacement.order"}
        for _ in range(16):
            if engine.state.pending_decision.kind in choices:
                break
            pass_current(session)
        self.assertIn(engine.state.pending_decision.kind, choices)
        self.assertEqual("stack", throne.zone)
        if engine.state.pending_decision.kind == "replacement.order":
            from quorune.entry_designations import SELF_CHOSEN_SUBTYPE_ENTRY_HANDLER_ID

            options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
            option, = (row["id"] for row in options if SELF_CHOSEN_SUBTYPE_ENTRY_HANDLER_ID in row["id"]
                       and row["id"].endswith(":goblin"))
            command = {"action_id": "choose", "replacement": option}
        else:
            command = {
                "action_id": "choose",
                "creature_type": "Goblin",
                "plan": "SELECT_TYPE",
                "reason": "Double the Goblin Engineer trigger.",
            }
        result = session.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", throne.zone)
        self.assertIn(
            "goblin",
            engine._type_parts(
                str(engine._effective_card_data(throne)["type_line"])
            )[1],
        )
        # One AS-enters choice completes entry; a second generic choice would
        # leave the object on the stack and violate its CR 607.2d link.
        self.assertNotIn(engine.state.pending_decision.kind, {"semantic.choice", "replacement.order"})
        expected = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])
        engine.move_card(
            engineer.object_id,
            "battlefield",
            controller="A",
        )
        refs = engine._dispatch_semantic_event(
            "permanent.enter",
            {
                "card": engineer.ref,
                "controller": "A",
                "types": ["creature"],
            },
            sources=[engineer],
        )
        self.assertEqual(2, len(refs))

        engine.state.pending_trigger_batches.clear()
        targeted = self.stack_item(
            engine,
            ref="S-ward-target",
            kind="activated_ability",
            controller="B",
            label="Opponent targets Roaming Throne",
            semantic_key=(
                "c1d6cce8-085f-42cb-8b0c-b6fbbf88b16a:"
                "ability:ab2"
            ),
            targets=[throne.ref],
        )
        ward_refs = collect_ward_occurrences(engine, targeted)
        self.assertEqual(1, len(ward_refs))
        ward_occurrence = engine.state.pending_trigger_batches[0].items[0]
        self.assertEqual("object.became_target", ward_occurrence.normalized_event_id)
        self.assertEqual(throne.object_id, ward_occurrence.source_object_id)
        self.assertEqual(
            throne.logical_object_id,
            ward_occurrence.source_logical_object_id,
        )
        self.assertEqual(
            {"schema_version": 1, "generic_cost": 2},
            dict(ward_occurrence.event_facts["ward_spec"]),
        )
        self.resolve_top(engine)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "pay": False,
                "plan": "DECLINE_WARD",
                "reason": "Cannot pay ward.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertNotIn(targeted, engine.state.stack)

    def test_sundial_ends_turn_and_exiles_stack(self):
        session = self.make_session(1117)
        engine = session.engine
        sundial = self.card(engine, "A", "Sundial of the Infinite")
        stranded = self.card(engine, "A", "Deflecting Swat")
        buffed = self.card(engine, "A", "Mishra, Eminent One")
        for card in (sundial, buffed):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="A",
            )
        buffed.annotations["until_end_of_turn"] = {"power": 3}
        engine._remove_from_zone(stranded)
        stranded.zone = "stack"
        stranded_item = self.stack_item(
            engine,
            ref="S-stranded",
            kind="spell",
            controller="A",
            label="Stranded spell",
            semantic_key=(
                "ae120613-97d6-4393-b39d-c3e6c076f5d6:"
                "spell:front"
            ),
            card_object_id=stranded.object_id,
        )
        self.prepare_main(engine)
        engine.state.stack.append(stranded_item)
        engine.state.players["A"].mana_pool["C"] = 1
        engine._activate(
            "A",
            {
                "source": sundial.ref,
                "ability": "ab1",
                "pay": "manual",
                "payment": {"C": 1},
            },
        )
        self.resolve_top(engine)
        self.assertEqual("exile", stranded.zone)
        self.assertFalse(engine.state.stack)
        self.assertNotIn("until_end_of_turn", buffed.annotations)
        self.assertEqual("B", engine.state.active_player)

    def test_mightstone_modal_enter_and_restricted_powerstone_mana(self):
        with self.subTest(mode="draw"):
            session = self.make_session(1118)
            engine = session.engine
            mightstone = self.card(
                engine, "A", "The Mightstone and Weakstone"
            )
            before = len(engine.state.players["A"].zones["hand"])
            engine.move_card(
                mightstone.object_id,
                "battlefield",
                controller="A",
                semantic_events=True,
            )
            self.assertTrue(engine._stabilize())
            result = session.act(
                "pilot:A",
                {
                    "action_id": "choose",
                    "modes": ["mode_1"],
                    "targets": [],
                    "plan": "DRAW_CARDS",
                    "reason": "Select the draw mode.",
                },
            )
            self.assertTrue(result.ok, result.summary)
            self.resolve_top(engine)
            self.assertEqual(
                before + 2,
                len(engine.state.players["A"].zones["hand"]),
            )

        with self.subTest(mode="weaken"):
            session = self.make_session(1119)
            engine = session.engine
            mightstone = self.card(
                engine, "A", "The Mightstone and Weakstone"
            )
            target_ref = engine.create_token(
                "B",
                name="Large Test Creature",
                characteristics={
                    "type_line": "Token Creature — Giant",
                    "power": "10",
                    "toughness": "10",
                },
                reason="test setup",
            )[0]
            target = engine._resolve_object("A", target_ref)
            engine.move_card(
                mightstone.object_id,
                "battlefield",
                controller="A",
                semantic_events=True,
            )
            self.assertTrue(engine._stabilize())
            result = session.act(
                "pilot:A",
                {
                    "action_id": "choose",
                    "modes": ["mode_2"],
                    "targets": [target.ref],
                    "plan": "WEAKEN_CREATURE",
                    "reason": "Apply the temporary reduction.",
                },
            )
            self.assertTrue(result.ok, result.summary)
            self.resolve_top(engine)
            self.assertEqual(
                5, engine._numeric_stat(target.object_id, "power")
            )
            self.assertEqual(
                5, engine._numeric_stat(target.object_id, "toughness")
            )

        with self.subTest(mode="restricted_mana"):
            session = self.make_session(1120)
            engine = session.engine
            mightstone = self.card(
                engine, "A", "The Mightstone and Weakstone"
            )
            greaves = self.card(engine, "A", "Lightning Greaves")
            engine.move_card(
                mightstone.object_id,
                "battlefield",
                controller="A",
            )
            engine.move_card(greaves.object_id, "hand")
            engine.state.priority_player = "A"
            engine._activate(
                "A",
                {
                    "source": mightstone.ref,
                    "ability": "ab4",
                },
            )
            self.assertEqual(2, engine.state.players["A"].mana_pool["C"])
            self.assertFalse(
                engine._cost_is_affordable(
                    "A",
                    {"GENERIC": 2},
                    spend_context="nonartifact_spell",
                )
            )
            self.assertTrue(
                engine._cost_is_affordable(
                    "A",
                    {"GENERIC": 2},
                    spend_context="artifact_spell",
                )
            )
            self.prepare_main(engine)
            self.assertIn(
                greaves.ref,
                engine._priority_action_hints("A")["cast"],
            )
            engine._cast(
                "A",
                {
                    "card": greaves.ref,
                    "pay": "manual",
                    "payment": {"C": 2},
                },
            )
            self.assertEqual(0, engine.state.players["A"].mana_pool["C"])
            self.assertNotIn(
                "restricted_mana",
                engine.state.players["A"].stats,
            )

    def test_stasis_coffin_protection_blocks_targeting_and_damage(self):
        session = self.make_session(1121)
        engine = session.engine
        coffin = self.card(engine, "A", "The Stasis Coffin")
        engine.move_card(
            coffin.object_id,
            "battlefield",
            controller="A",
        )
        engine.state.players["A"].mana_pool["C"] = 2
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": coffin.ref,
                "ability": "ab1",
                "pay": "manual",
                "payment": {"C": 2},
            },
        )
        self.assertEqual("exile", coffin.zone)
        self.resolve_top(engine)
        self.assertTrue(
            engine.state.players["A"].stats[
                "protection_from_everything_until_next_turn"
            ]
        )
        schema = engine._public_target_schema(
            "B",
            {
                "zones": ["player"],
                "categories": ["player"],
                "player_relation": "any",
                "count": 1,
            },
            source_ref=None,
        )
        self.assertNotIn("A", schema["legal_refs"])
        before = engine.state.players["A"].life
        engine.apply_effect(
            {"op": "damage", "target": "A", "amount": 5},
            actor="B",
        )
        self.assertEqual(before, engine.state.players["A"].life)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._begin_turn(
            TurnEntry(
                turn_id="N-stasis-expiry",
                player="A",
                created_sequence=engine.state.turn_sequence,
            )
        )
        self.assertNotIn(
            "protection_from_everything_until_next_turn",
            engine.state.players["A"].stats,
        )

    def test_daretti_loyalty_exchange_and_emblem_return(self):
        from quorune.semantics import SemanticRegistry

        session = self.make_session(1122)
        engine = session.engine
        daretti = self.card(engine, "A", "Daretti, Scrap Savant")
        for program in SemanticRegistry().programs_for_oracle(daretti.oracle_id):
            if program.event == "activate":
                catalog = engine.semantics.get(program.key)
                engine.semantics.put(replace(program, handlers=catalog.handlers, cost_schema=catalog.cost_schema))
        sacrifice = self.card(engine, "A", "Arcane Signet")
        returned = self.card(engine, "A", "Sol Ring")
        engine.move_card(
            daretti.object_id, "battlefield", controller="A"
        )
        self.assertEqual(3, daretti.counters["loyalty"])
        self.prepare_main(engine)

        hand_refs = [
            engine.state.cards[object_id].ref
            for object_id in engine.state.players["A"].zones["hand"]
        ][:2]
        before_hand = len(engine.state.players["A"].zones["hand"])
        engine._activate(
            "A", {"source": daretti.ref, "ability": "ab1"}
        )
        self.resolve_top(engine)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": hand_refs,
                "plan": "FILTER_HAND",
                "reason": "Exchange two cards for two fresh draws.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(5, daretti.counters["loyalty"])
        self.assertEqual(
            before_hand, len(engine.state.players["A"].zones["hand"])
        )

        advance_fixture_turn(engine)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.move_card(
            sacrifice.object_id, "battlefield", controller="A"
        )
        engine.move_card(returned.object_id, "graveyard")
        self.prepare_main(engine)
        engine._activate(
            "A",
            {
                "source": daretti.ref,
                "ability": "ab2",
                "targets": [returned.ref],
            },
        )
        self.resolve_top(engine)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "card": sacrifice.ref,
                "plan": "RECUR_ARTIFACT",
                "reason": "Exchange the small artifact for Sol Ring.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", sacrifice.zone)
        self.assertEqual("battlefield", returned.zone)

        advance_fixture_turn(engine)
        daretti.counters["loyalty"] = 10
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        self.prepare_main(engine)
        engine._activate(
            "A", {"source": daretti.ref, "ability": "ab3"}
        )
        self.resolve_top(engine)
        self.assertEqual(
            1, engine.state.players["A"].stats["daretti_emblems"]
        )
        daretti_emblems = [
            engine.state.cards[object_id]
            for object_id in engine.state.players["A"].zones["command"]
            if engine.state.cards[object_id].object_kind == "emblem"
        ]
        self.assertEqual(1, len(daretti_emblems))
        self.assertEqual("A", daretti_emblems[0].controller)
        engine.move_card(
            returned.object_id,
            "graveyard",
            reason="emblem test",
            semantic_events=True,
        )
        self.assertFalse(engine._stabilize())
        self.assertTrue(
            any(
                item.semantic_key == "builtin:daretti-emblem"
                and item.source_object_id
                == daretti_emblems[0].object_id
                for item in engine.state.stack
            )
        )
        self.resolve_top(engine)
        self.assertTrue(engine.state.delayed_triggers)
        delayed = engine._matching_delayed_triggers(
            "step.begin",
            {
                "phase": "ending",
                "step": "end_step",
                "player": "A",
            },
        )
        self.assertTrue(delayed)
        engine._start_trigger_batch(delayed, after="grant_priority")
        self.resolve_top(engine)
        self.assertEqual("battlefield", returned.zone)

    def test_demonic_junker_targets_per_player_and_crews(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=1123,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        junker = self.card(engine, "A", "Demonic Junker")
        creatures = {}
        for seat in engine.seats:
            ref = engine.create_token(
                seat,
                name=f"{seat} Crew Test",
                characteristics={
                    "type_line": "Token Creature — Citizen",
                    "power": "2",
                    "toughness": "2",
                },
                reason="test setup",
            )[0]
            creatures[seat] = engine._resolve_object(seat, ref)
        engine.move_card(
            junker.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.assertTrue(engine._stabilize())
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "targets": [
                    {"group": f"seat_{seat.casefold()}", "ref": card.ref}
                    for seat, card in creatures.items()
                ],
                "plan": "CLEAR_CREATURES",
                "reason": "Destroy one creature controlled by each player.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(engine)
        self.assertTrue(
            all(card.zone != "battlefield" for card in creatures.values())
        )
        self.assertEqual(2, junker.counters["+1/+1"])

        crew_ref = engine.create_token(
            "A",
            name="Crew Pilot",
            characteristics={
                "type_line": "Token Creature — Pilot",
                "power": "2",
                "toughness": "2",
            },
            reason="crew test",
        )[0]
        crew = engine._resolve_object("A", crew_ref)
        self.prepare_main(engine)
        crew_hint = next(
            hint
            for hint in engine._priority_action_hints("A")["abilities"]
            if hint["s"] == junker.ref and hint["a"] == "ab3"
        )
        self.assertEqual(
            2, crew_hint["choose_cost"][0]["minimum_total_power"]
        )
        self.assertIn(
            crew.ref, crew_hint["choose_cost"][0]["legal_refs"]
        )
        engine._activate(
            "A",
            {
                "source": junker.ref,
                "ability": "ab3",
                "cost_cards": [crew.ref],
            },
        )
        self.assertTrue(crew.tapped)
        self.resolve_top(engine)
        self.assertIn(
            "creature",
            engine._type_parts(
                engine._effective_card_data(junker)["type_line"]
            )[0],
        )

    def test_tithing_blade_apnap_craft_and_back_upkeep(self):
        session = self.make_session(1124)
        engine = session.engine
        blade = self.card(
            engine, "A", "Tithing Blade // Consuming Sepulcher"
        )
        victim_ref = engine.create_token(
            "B",
            name="Tithing Victim",
            characteristics={
                "type_line": "Token Creature — Citizen",
                "power": "1",
                "toughness": "1",
            },
            reason="test setup",
        )[0]
        victim = engine._resolve_object("B", victim_ref)
        engine.move_card(
            blade.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        self.assertEqual("choice.apnap", engine.state.pending_decision.kind)
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "cards": [victim.ref],
                "plan": "PAY_MANDATORY_COST",
                "reason": "Sacrifice the required creature.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertNotEqual("battlefield", victim.zone)

        craft_ref = engine.create_token(
            "A",
            name="Craft Creature",
            characteristics={
                "type_line": "Token Creature — Citizen",
                "power": "1",
                "toughness": "1",
            },
            reason="craft test",
        )[0]
        craft_card = engine._resolve_object("A", craft_ref)
        self.prepare_main(engine)
        engine.state.players["A"].mana_pool.update(
            {"C": 4, "B": 1}
        )
        engine._activate(
            "A",
            {
                "source": blade.ref,
                "ability": "craft_battlefield",
                "cost_cards": [craft_card.ref],
                "pay": "manual",
                "payment": {"C": 4, "B": 1},
            },
        )
        self.assertEqual("exile", blade.zone)
        self.assertNotEqual("battlefield", craft_card.zone)
        self.resolve_top(engine)
        self.assertEqual("battlefield", blade.zone)
        self.assertEqual("Consuming Sepulcher", blade.active_face)

        before_a = engine.state.players["A"].life
        before_b = engine.state.players["B"].life
        engine._dispatch_semantic_event(
            "step.begin",
            {"phase": "beginning", "step": "upkeep", "player": "A"},
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        self.assertEqual(before_a + 1, engine.state.players["A"].life)
        self.assertEqual(before_b - 1, engine.state.players["B"].life)

    def test_transmute_artifact_resolution_payment_and_decline(self):
        session = self.make_session(1125)
        engine = session.engine
        transmute = self.card(engine, "A", "Transmute Artifact")
        sacrifice = self.card(engine, "A", "Arcane Signet")
        found = self.card(engine, "A", "Panharmonicon")
        engine.move_card(
            sacrifice.object_id, "battlefield", controller="A"
        )
        engine.move_card(transmute.object_id, "hand")
        engine.move_card(found.object_id, "library")
        self.prepare_main(engine)
        engine.state.players["A"].mana_pool.update(
            {"U": 2, "C": 2}
        )
        engine._cast(
            "A",
            {
                "card": transmute.ref,
                "pay": "manual",
                "payment": {"U": 2},
            },
        )
        self.resolve_top(engine)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "card": sacrifice.ref,
                "plan": "TUTOR_ARTIFACT",
                "reason": "Sacrifice the two-mana artifact.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "card": found.ref,
                "plan": "TUTOR_ARTIFACT",
                "reason": "Find the four-mana artifact.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "pay": True,
                "plan": "TUTOR_ARTIFACT",
                "reason": "Pay the two-mana difference.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", found.zone)
        self.assertEqual("graveyard", sacrifice.zone)

    def test_transmute_artifact_orders_candidates_and_replays_decline_after_reload(self):
        session = self.make_session(11253)
        engine = session.engine
        spell = self.card(engine, "A", "Transmute Artifact")
        sacrifice = self.card(engine, "A", "Sol Ring")
        alternative = self.card(engine, "A", "Arcane Signet")
        copy_model = self.card(engine, "A", "Panharmonicon")
        found = self.card(engine, "A", "Mindslaver")
        engine.move_card(spell.object_id, "hand")
        engine.move_card(sacrifice.object_id, "battlefield", controller="A")
        engine.move_card(alternative.object_id, "battlefield", controller="A")
        engine.move_card(copy_model.object_id, "library")
        engine.move_card(found.object_id, "library")
        sacrifice.annotations["copy_overrides"] = dict(engine._copyable_characteristics(copy_model))
        self.assertEqual(4, engine._effective_card_data(sacrifice)["mana_value"])
        engine.state.cards = dict(sorted(
            engine.state.cards.items(), key=lambda pair: pair[1].ref, reverse=True,
        ))
        program = engine.semantics.get(f"{spell.oracle_id}:spell:front")
        self.assertEqual("artifact_search_exchange", program.effects[0]["op"])
        self.prepare_main(engine)
        engine.state.players["A"].mana_pool.update({"U": 2, "C": 2})
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        shuffle_count = engine.state.players["A"].stats.get("shuffle_count", 0)

        cast = session.act("pilot:A", {
            "action_id": f"cast:{spell.ref}", "pay": "manual", "payment": {"U": 2},
        })
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual("battlefield", sacrifice.zone)
        for _ in range(12):
            if engine.state.pending_decision.kind == "semantic.choice":
                break
            pass_current(session)
        else:
            self.fail("Transmute Artifact did not reach its sacrifice choice")
        sacrifice_refs = session.packet("pilot:A", full=True)["decision"]["ctx"]["objects"]
        self.assertEqual(sorted((sacrifice.ref, alternative.ref)), sacrifice_refs)

        paid_cost = session.act("pilot:A", {"action_id": "choose", "card": sacrifice.ref})
        self.assertTrue(paid_cost.ok, paid_cost.summary)
        self.assertEqual(1, engine._effective_card_data(sacrifice)["mana_value"])
        search = session.packet("pilot:A", full=True)["decision"]["ctx"]
        candidate_refs = [row["id"] for row in search["search_cards"]]
        self.assertEqual(sorted(candidate_refs), candidate_refs)
        self.assertIn(found.ref, candidate_refs)
        self.assertIn(copy_model.ref, candidate_refs)
        self.assertIsNone(session.packet("pilot:B", full=True)["decision"])

        selected = session.act("pilot:A", {"action_id": "choose", "card": found.ref})
        self.assertTrue(selected.ok, selected.summary)
        pending_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            loaded = type(session).load(self.db, directory)
            self.assertEqual(pending_hash, authoritative_state_hash(loaded.state))
            pending_payment = loaded.packet("pilot:A", full=True)["decision"]["ctx"]
            self.assertEqual({"GENERIC": 2}, pending_payment["cost"])
            self.assertEqual("library", loaded.state.cards[found.object_id].zone)
            decline = loaded.act("pilot:A", {"action_id": "choose", "pay": False})
            self.assertTrue(decline.ok, decline.summary)
            self.assertEqual("graveyard", loaded.state.cards[found.object_id].zone)
            self.assertEqual("graveyard", loaded.state.cards[spell.object_id].zone)
            self.assertEqual(2, loaded.state.players["A"].mana_pool["C"])
            self.assertEqual(shuffle_count + 1, loaded.state.players["A"].stats["shuffle_count"])
            final_hash = authoritative_state_hash(loaded.state)
            loaded.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(final_hash, replay["final_state_hash"])

    def test_transmute_artifact_restricted_mandatory_search_shuffles_and_replays(self):
        from quorune.model import CardInstance
        from quorune.oracle_ir import register_generated_programs
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.rules.library_search_rules import library_search_constraint

        for index, restriction_name in enumerate((None, "Aven Mindcensor", "Mindlock Orb")):
            with self.subTest(restriction=restriction_name):
                session = make_session(self.db, self.mishra, self.zimone, players=3, seed=11256 + index)
                keep_all(session)
                engine = session.engine
                engine.permissions.invalidate_current()
                engine.state.pending_decision = None
                spell = self.card(engine, "A", "Transmute Artifact")
                sacrifice = self.card(engine, "A", "Arcane Signet")
                near = self.card(engine, "A", "Mindslaver")
                deep = self.card(engine, "A", "Ichor Wellspring")
                if restriction_name is not None:
                    oracle = self.db.lookup(restriction_name)
                    register_generated_programs(
                        self.db, engine.semantics, (oracle,),
                        capability_registry=load_default_capability_registry(),
                        capability_profile=engine.state.config.review_profile,
                        promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                        promote_exact_effect_programs=True, promote_exact_capability_declarations=True,
                    )
                    restriction = CardInstance(
                        object_id="fixture:transmute-search-restriction", ref="fixture-transmute-restriction",
                        oracle_id=oracle.oracle_id, printed_name=oracle.name, owner="B", controller="B",
                        zone="battlefield", zone_timestamp=engine._next_zone_timestamp(),
                        known_to=list(engine.seats), revealed_to=list(engine.seats),
                    )
                    engine.state.cards[restriction.object_id] = restriction
                    engine.state.players["B"].zones["battlefield"].append(restriction.object_id)
                scope = library_search_constraint(engine, "A")
                self.assertEqual(4 if restriction_name == "Aven Mindcensor" else None, scope.top_count)
                self.assertEqual(restriction_name == "Mindlock Orb", scope.prohibited)

                engine.move_card(spell.object_id, "hand")
                engine.move_card(sacrifice.object_id, "battlefield", controller="A")
                engine.move_card(near.object_id, "library")
                engine.move_card(deep.object_id, "library")
                library = engine.state.players["A"].zones["library"]
                library.remove(near.object_id)
                library.append(near.object_id)
                library.remove(deep.object_id)
                library.insert(0, deep.object_id)
                self.prepare_main(engine)
                engine.state.players["A"].mana_pool["U"] = 2
                engine._grant_priority("A")
                engine.pump()
                session.initial_checkpoint = checkpoint_envelope(engine.state)
                session.commands.clear()
                session.decisions.clear()
                shuffle_count = engine.state.players["A"].stats.get("shuffle_count", 0)
                cast = session.act("pilot:A", {
                    "action_id": f"cast:{spell.ref}", "pay": "manual", "payment": {"U": 2},
                })
                self.assertTrue(cast.ok, cast.summary)
                self.assertEqual("battlefield", sacrifice.zone)
                for _ in range(12):
                    if engine.state.pending_decision.kind == "semantic.choice":
                        break
                    pass_current(session)
                else:
                    self.fail("Transmute Artifact did not reach its sacrifice choice")
                paid = session.act("pilot:A", {"action_id": "choose", "card": sacrifice.ref})
                self.assertTrue(paid.ok, paid.summary)

                if scope.prohibited:
                    self.assertNotEqual("semantic.choice", engine.state.pending_decision.kind)
                    for principal in ("pilot:A", "pilot:B", "spectator"):
                        decision = session.packet(principal, full=True)["decision"]
                        self.assertNotIn("search_cards", (decision or {}).get("ctx", {}))
                else:
                    view = session.packet("pilot:A", full=True)["decision"]
                    self.assertEqual("semantic.choice", view["kind"])
                    offered = {row["id"] for row in view["ctx"]["search_cards"]}
                    self.assertIn(near.ref, offered)
                    self.assertEqual(restriction_name is None, deep.ref in offered)
                    self.assertIsNone(session.packet("pilot:B", full=True)["decision"])
                    self.assertIsNone(session.packet("pilot:C", full=True)["decision"])
                    self.assertIsNone(session.packet("spectator", full=True)["decision"])
                    before = authoritative_state_hash(engine.state)
                    accepted = len(session.commands)
                    disallowed_ref = deep.ref if restriction_name else sacrifice.ref
                    rejected = session.act("pilot:A", {"action_id": "choose", "card": disallowed_ref})
                    self.assertFalse(rejected.ok)
                    self.assertEqual(before, authoritative_state_hash(engine.state))
                    self.assertEqual(accepted, len(session.commands))
                    failed_to_find = session.act("pilot:A", {"action_id": "choose"})
                    self.assertTrue(failed_to_find.ok, failed_to_find.summary)

                self.assertEqual("graveyard", engine.state.cards[sacrifice.object_id].zone)
                self.assertEqual("graveyard", engine.state.cards[spell.object_id].zone)
                self.assertEqual("library", engine.state.cards[near.object_id].zone)
                self.assertEqual("library", engine.state.cards[deep.object_id].zone)
                self.assertEqual(shuffle_count + 1, engine.state.players["A"].stats["shuffle_count"])
                expected = authoritative_state_hash(engine.state)
                with tempfile.TemporaryDirectory() as directory:
                    session.save(directory)
                    replay = replay_record(directory, self.db, verify=True)
                self.assertTrue(replay["ok"], replay)
                self.assertEqual(expected, replay["final_state_hash"])

    def test_transmute_artifact_requires_exchange_owner_and_exact_grammar(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.artifact_search_exchange import ARTIFACT_SEARCH_EXCHANGE_CAPABILITY
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.semantics import SemanticRegistry

        registry = load_default_capability_registry()
        record = self.db.lookup("Transmute Artifact")
        program = compile_best_available_card_program(
            self.db, record, semantic_registry=SemanticRegistry(),
            capability_registry=registry, capability_profile="traditional",
        )
        bound = bind_card_program_runtime(program, capability_registry=registry, profile="traditional")
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        ability = next(ability for ability in program.abilities if ability.ability_id == "spell:front")
        self.assertIn(ARTIFACT_SEARCH_EXCHANGE_CAPABILITY, ability.capability_dependencies)
        incomplete = replace(ability, capability_dependencies=(), capability_closure=None)
        invalid = bind_semantic_program_runtime(incomplete, capability_registry=registry, profile="traditional")
        self.assertFalse(invalid["strict"])
        self.assertIn(
            f"capability:undeclared_runtime_dependency:{ARTIFACT_SEARCH_EXCHANGE_CAPABILITY}",
            invalid["blockers"],
        )

        from unittest.mock import patch

        with patch(
            "quorune.rules.artifact_search_exchange._EXACT",
            record.oracle_text.removesuffix(" Then shuffle."),
        ):
            mutant = compile_oracle_card(record, capability_registry=registry, capability_profile="traditional")
        self.assertTrue(mutant.material_residuals)

        disputed = replace(record, oracle_text=record.oracle_text.removesuffix(" Then shuffle."))
        for changed in (
            disputed,
            replace(record, oracle_text=record.oracle_text.replace("Sacrifice an artifact.", "Sacrifice a creature.", 1)),
            replace(record, oracle_text=record.oracle_text.replace("owner's graveyard", "owner's hand")),
        ):
            ir = compile_oracle_card(changed, capability_registry=registry, capability_profile="traditional")
            self.assertTrue(ir.material_residuals)

    def test_transmute_artifact_native_shuffles_without_a_legal_artifact(self):
        session = self.make_session(11260)
        engine = session.engine
        source = self.card(engine, "A", "Transmute Artifact")
        engine.move_card(source.object_id, "hand")
        self.prepare_main(engine)
        engine.state.players["A"].mana_pool["U"] = 2
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        shuffles = engine.state.players["A"].stats.get("shuffle_count", 0)
        cast = session.act("pilot:A", {
            "action_id": f"cast:{source.ref}", "pay": "manual", "payment": {"U": 2},
        })
        self.assertTrue(cast.ok, cast.summary)
        for _ in range(12):
            if source.zone != "stack":
                break
            self.assertEqual("priority", engine.state.pending_decision.kind)
            pass_current(session)
        self.assertEqual("graveyard", source.zone)
        self.assertEqual(shuffles + 1, engine.state.players["A"].stats["shuffle_count"])
        expected = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_transmute_artifact_routes_equal_and_unaffordable_results(self):
        for index, (card_name, destination) in enumerate((
            ("Ichor Wellspring", "battlefield"),
            ("Panharmonicon", "graveyard"),
        )):
            with self.subTest(card=card_name):
                session = self.make_session(11261 + index)
                engine = session.engine
                spell = self.card(engine, "A", "Transmute Artifact")
                paid = self.card(engine, "A", "Arcane Signet")
                found = self.card(engine, "A", card_name)
                engine.move_card(spell.object_id, "hand")
                engine.move_card(paid.object_id, "battlefield", controller="A")
                engine.move_card(found.object_id, "library")
                self.prepare_main(engine)
                engine.state.players["A"].mana_pool["U"] = 2
                engine._grant_priority("A")
                engine.pump()
                session.initial_checkpoint = checkpoint_envelope(engine.state)
                session.commands.clear()
                session.decisions.clear()
                shuffles = engine.state.players["A"].stats.get("shuffle_count", 0)
                cast = session.act("pilot:A", {
                    "action_id": f"cast:{spell.ref}", "pay": "manual", "payment": {"U": 2},
                })
                self.assertTrue(cast.ok, cast.summary)
                for _ in range(12):
                    if engine.state.pending_decision.kind == "semantic.choice":
                        break
                    pass_current(session)
                else:
                    self.fail("Artifact exchange did not reach its sacrifice choice")
                sacrifice = session.act("pilot:A", {"action_id": "choose", "card": paid.ref})
                self.assertTrue(sacrifice.ok, sacrifice.summary)
                selection = session.act("pilot:A", {"action_id": "choose", "card": found.ref})
                self.assertTrue(selection.ok, selection.summary)
                self.assertEqual(destination, found.zone)
                self.assertEqual("graveyard", spell.zone)
                self.assertEqual(shuffles + 1, engine.state.players["A"].stats["shuffle_count"])
                self.assertFalse(any(event.code == "artifact.exchange.pay" for event in engine.state.events))
                expected = authoritative_state_hash(engine.state)
                with tempfile.TemporaryDirectory() as directory:
                    session.save(directory)
                    replay = replay_record(directory, self.db, verify=True)
                self.assertTrue(replay["ok"], replay)
                self.assertEqual(expected, replay["final_state_hash"])

    def test_transmute_artifact_paid_replacement_resumes_once_without_revealing_selection(self):
        from unittest.mock import patch

        from quorune.replacement_effects import ReplacementClass, ReplacementEffect, SetField
        from quorune.semantic_runtime import zone_replacements

        session = self.make_session(11263)
        engine = session.engine
        spell = self.card(engine, "A", "Transmute Artifact")
        sacrifice = self.card(engine, "A", "Arcane Signet")
        found = self.card(engine, "A", "Panharmonicon")
        engine.move_card(spell.object_id, "hand")
        engine.move_card(sacrifice.object_id, "battlefield", controller="A")
        engine.move_card(found.object_id, "library")
        self.prepare_main(engine)
        engine.state.players["A"].mana_pool.update({"U": 2, "C": 2})
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        shuffles = engine.state.players["A"].stats.get("shuffle_count", 0)
        redirects = tuple(
            ReplacementEffect(
                effect_id=f"fixture:exchange-{zone}", source_id=f"fixture:{zone}",
                event_kind="zone.change", replacement_class=ReplacementClass.OTHER,
                conditions={
                    "object_ref": {"eq": found.ref}, "origin": {"eq": "library"},
                    "destination": {"eq": "battlefield"},
                },
                operations=(SetField("destination", zone),), label=f"Redirect to {zone}",
            )
            for zone in ("hand", "exile")
        )
        collect = zone_replacements.collect_zone_change_replacement_effects

        def with_redirects(host, **kwargs):
            return (*collect(host, **kwargs), *redirects)

        with patch.object(zone_replacements, "collect_zone_change_replacement_effects", with_redirects):
            cast = session.act("pilot:A", {
                "action_id": f"cast:{spell.ref}", "pay": "manual", "payment": {"U": 2},
            })
            self.assertTrue(cast.ok, cast.summary)
            for _ in range(12):
                if engine.state.pending_decision.kind == "semantic.choice":
                    break
                pass_current(session)
            else:
                self.fail("Artifact exchange did not reach its sacrifice choice")
            for choice in ({"card": sacrifice.ref}, {"card": found.ref}, {"pay": True}):
                result = session.act("pilot:A", {"action_id": "choose", **choice})
                self.assertTrue(result.ok, result.summary)
            self.assertEqual("replacement.order", engine.state.pending_decision.kind)
            self.assertEqual("library", found.zone)
            self.assertEqual(0, engine.state.players["A"].mana_pool["C"])
            self.assertEqual(shuffles, engine.state.players["A"].stats.get("shuffle_count", 0))
            self.assertNotIn(found.ref, repr(session.packet("pilot:B", full=True)))
            before_restore = authoritative_state_hash(session.state)
            with tempfile.TemporaryDirectory() as directory:
                session.save(directory)
                restored = type(session).load(self.db, directory)
                self.assertEqual(before_restore, authoritative_state_hash(restored.state))
                options = restored.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                self.assertEqual(2, len(options))
                replacement = restored.act("pilot:A", {
                    "action_id": "choose", "replacement": options[0]["id"],
                })
                self.assertTrue(replacement.ok, replacement.summary)
                self.assertIn(restored.state.cards[found.object_id].zone, {"hand", "exile"})
                self.assertEqual(0, restored.state.players["A"].mana_pool["C"])
                self.assertEqual(shuffles + 1, restored.state.players["A"].stats["shuffle_count"])
                self.assertEqual(1, sum(event.code == "artifact.exchange.pay" for event in restored.state.events))
                expected = authoritative_state_hash(restored.state)
                restored.save(directory)
                replay = replay_record(directory, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(expected, replay["final_state_hash"])

    def test_urzas_saga_chapters_construct_search_and_sacrifice(self):
        session = self.make_session(1126)
        engine = session.engine
        saga = self.card(engine, "A", "Urza's Saga")
        found = self.card(engine, "A", "Sol Ring")
        engine.move_card(found.object_id, "library")
        engine.move_card(
            saga.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        self.assertEqual(
            ["saga_mana"],
            [
                fragment["value"]["ability_id"]
                for fragment in saga.annotations[
                    "granted_ability_fragments"
                ]
            ],
        )
        self.assertIn(
            "saga_mana",
            {
                ability.ability_id
                for ability in engine._activated_abilities(saga)
            },
        )

        advance_active_player_sagas(engine, "A")
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        self.assertEqual(
            ["saga_mana", "saga_construct"],
            [
                fragment["value"]["ability_id"]
                for fragment in saga.annotations[
                    "granted_ability_fragments"
                ]
            ],
        )
        saga.tapped = False
        self.prepare_main(engine)
        engine.state.players["A"].mana_pool["C"] = 2
        engine._activate(
            "A",
            {
                "source": saga.ref,
                "ability": "saga_construct",
                "pay": "manual",
                "payment": {"C": 2},
            },
        )
        self.resolve_top(engine)
        self.assertTrue(
            any(
                card.is_token
                and card.printed_name == "Construct"
                and card.zone == "battlefield"
                for card in engine.state.cards.values()
            )
        )

        advance_active_player_sagas(engine, "A")
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        self.assertEqual("semantic.search", engine.state.pending_decision.kind)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "search_cards": [found.ref],
                "plan": "TUTOR_ARTIFACT",
                "reason": "Put Sol Ring onto the battlefield.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", found.zone)
        self.assertEqual("graveyard", saga.zone)

    def test_promoted_mishra_cards_preflight_fully(self):
        engine = self.make_session(1107).engine
        for name in (
            "Emry, Lurker of the Loch",
            "Loki's Scepter",
            "Master Transmuter",
            "Shuri, Wakandan Inventor",
            "Simulacrum Synthesizer",
            "Stridehangar Automaton",
            "Strionic Resonator",
            "Lithoform Engine",
            "Scientist Supreme of A.I.M.",
            "Worldwalker Helm",
            "Deflecting Swat",
            "Fomori Vault",
            "Mindslaver",
            "Roaming Throne",
            "Sundial of the Infinite",
            "The Mightstone and Weakstone",
            "The Stasis Coffin",
            "Daretti, Scrap Savant",
            "Demonic Junker",
            "Tithing Blade",
            "Transmute Artifact",
            "Urza's Saga",
        ):
            with self.subTest(card=name):
                row = card_semantic_status(
                    self.db.lookup(name),
                    engine.semantics,
                    db=self.db,
                )
                self.assertEqual("fully_playable", row["status"], row)


if __name__ == "__main__":
    unittest.main()
