from __future__ import annotations

import unittest
import tempfile

from common import advance_fixture_turn, keep_all, load_assets, make_session, pass_current
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
            and card.printed_name == "Construct"
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
            if engine._resolve_object("A", ref).printed_name == "Thopter"
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
            if engine._resolve_object("A", ref).printed_name == "Map"
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
            and card.printed_name == "Map"
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
        engine._activate(
            "A",
            {
                "source": vault.ref,
                "ability": "ab2",
                "cost_cards": [discard.ref],
                "pay": "manual",
                "payment": {"C": 3},
            },
        )
        self.assertEqual("graveyard", discard.zone)
        self.resolve_top(engine)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        chosen = top[1]
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "card": chosen.ref,
                "plan": "SELECT_CARD",
                "reason": "Keep the selected card.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", chosen.zone)
        self.assertEqual(
            {top[0].object_id, top[2].object_id},
            set(library[:2]),
        )

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
        for _ in range(16):
            if engine.state.pending_decision.kind == "semantic.choice":
                break
            pass_current(session)
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        self.assertEqual("stack", throne.zone)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "creature_type": "Goblin",
                "plan": "SELECT_TYPE",
                "reason": "Double the Goblin Engineer trigger.",
            },
        )
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
                    "modes": ["draw"],
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
                    "modes": ["weaken"],
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
        session = self.make_session(1122)
        engine = session.engine
        daretti = self.card(engine, "A", "Daretti, Scrap Savant")
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
