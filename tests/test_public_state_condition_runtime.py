from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from common import ROOT, advance_fixture_turn, keep_all, make_session
from quorune.ability_fragments import StaticComponentSpec, ability_fragment_to_dict
from quorune.card_programs import bind_card_program_runtime, compile_card_program
from quorune.compiler.continuous_templates import fixed_public_state_characteristics_handler
from quorune.compiler.public_state_fact_queries import fixed_public_fact_condition
from quorune.continuous_conditions import FixedPublicStateConditionKind, FixedPublicStateFact, FixedPublicStateConditionSpec
from quorune.continuous_effects import CharacteristicState, Layer, evaluate_continuous_effects
from quorune.deck import DeckLoader
from quorune.engine import CommanderEngine
from quorune.model import CardInstance
from quorune.oracle_ir import register_generated_programs
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticProgram
from quorune.semantic_runtime import LifeChangeIntent, default_continuous_effect_component_registry
from quorune.turn_history import record_creature_attack_history
from public_state_condition_support import CAPABILITY_ID, TEMPLATE_ID, QUERY_HANDLER_ID, FIXTURE, focused_card_database, source_context


class FixedPublicStateCharacteristicRuntimeTests(unittest.TestCase):
    def test_original_channeler_condition_controls_required_attack_and_replays(self):
        from quorune.record import authoritative_state_hash
        from quorune.turn_step_owner import TURN_STEPS
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime

        session = self.session(361001)
        engine = session.engine
        record = self.db.lookup("Dragon's Rage Channeler")
        register_generated_programs(
            self.db, engine.semantics, (record,), trust_level='provisional',
            capability_registry=self.capabilities, capability_profile='commander_review',
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True,
        )
        source = CardInstance('public-state:channeler', 'delirium-channeler', record.oracle_id,
            record.name, 'A', 'A', 'battlefield', acquired_control_turn_count=-1,
            zone_timestamp=engine._next_zone_timestamp(), known_to=list(engine.seats), revealed_to=list(engine.seats))
        engine.state.cards[source.object_id] = source
        engine.state.players['A'].zones['battlefield'].append(source.object_id)
        program = compile_best_available_card_program(self.db, self.db.lookup("Dragon's Rage Channeler"), semantic_registry=engine.semantics, capability_registry=self.capabilities, capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program, capability_registry=self.capabilities, profile='commander_review')['strict_capability_ready'])
        # Four distinct printed types independently establish CR 205.2 and the
        # original Delirium condition; no descriptor or trust override is used.
        witnesses = ('Island', 'Sol Ring', 'Veil of Summer', 'Three Visits')
        cards = []
        for name in witnesses:
            record = self.db.lookup(name)
            card = CardInstance('delirium:' + name, 'delirium-' + name, record.oracle_id, name, 'A', 'A', 'graveyard', known_to=list(engine.seats), revealed_to=list(engine.seats))
            engine.state.cards[card.object_id] = card
            engine.state.players['A'].zones['graveyard'].append(card.object_id)
            cards.append(card)
        effective = engine._effective_card_data(source)
        self.assertEqual(('3', '3'), (str(effective['power']), str(effective['toughness'])))
        self.assertIn('Flying', effective['keywords'])
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.priority_player = None; engine.state.priority_passes = []
        engine.state.started = True; engine.state.active_player = 'A'
        engine.state.phase = 'combat'; engine.state.step = 'declare_attackers'
        engine.state.phase_index = TURN_STEPS.index(('combat', 'declare_attackers'))
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear(); session.decisions.clear()
        before = authoritative_state_hash(session.state)
        for principal, attackers in (('pilot:A', {}), ('pilot:C', {source.ref: 'B'})):
            denied = session.act(principal, {'action_id':'attack', 'attackers':attackers})
            self.assertFalse(denied.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id':'attack', 'attackers':{source.ref:'B'}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('B', session.state.cards[source.object_id].attacking)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        public = json.dumps(session.packet('pilot:D', full=True)['state'])
        self.assertIn(source.ref, public)
        self.assertNotIn(source.object_id, public)
        for seat in 'ABC':
            for object_id in session.state.players[seat].zones['hand']:
                self.assertNotIn(session.state.cards[object_id].ref, public)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay['ok'], replay)
        self.assertEqual(authoritative_state_hash(session.state), replay['final_state_hash'])
        # Losing one distinct type recomputes both layers; the attack declaration
        # has already happened and is not retroactively removed.
        engine.move_card(cards[-1].object_id, 'exile', log=False)
        current = engine.state.cards[source.object_id]
        effective = engine._effective_card_data(current)
        self.assertEqual(('1', '1'), (str(effective['power']), str(effective['toughness'])))
        self.assertNotIn('Flying', effective['keywords'])
        self.assertEqual('B', current.attacking)
        engine.state.combat.attackers.clear(); current.attacking = None; current.tapped = False
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.priority_player = None; engine.state.priority_passes = []
        engine.state.phase = 'combat'; engine.state.step = 'declare_attackers'
        engine.state.phase_index = TURN_STEPS.index(('combat', 'declare_attackers'))
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear(); session.decisions.clear()
        accepted = session.act('pilot:A', {'action_id':'attack', 'attackers':{}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(session.state.cards[source.object_id].attacking)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay['ok'], replay)

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
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        return session

    def add_source(
        self,
        session,
        *,
        name: str,
        ref: str,
        seat: str = "A",
        zone: str = "battlefield",
    ) -> CardInstance:
        engine = session.engine
        record = self.db.lookup(name)
        registration = register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertGreaterEqual(registration["runtime_handlers_promoted"], 1)
        card = CardInstance(
            object_id=f"public-state:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine._next_zone_timestamp(),
            entered_battlefield_turn_sequence=engine.state.turn_sequence,
            known_to=(
                [seat]
                if zone in {"hand", "library"}
                else list(engine.seats)
            ),
            revealed_to=(
                []
                if zone in {"hand", "library"}
                else list(engine.seats)
            ),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def add_constructed_condition_source(
        self,
        session,
        *,
        ref: str,
        texts: tuple[str, ...],
    ) -> CardInstance:
        engine = session.engine
        source = self.creature(
            engine,
            seat="A",
            name=f"Condition Source {ref}",
        )
        characteristics = (
            source.annotations.get("object_characteristics")
            or source.annotations["token_characteristics"]
        )
        fragments = characteristics.setdefault(
            "ability_fragments",
            [],
        )
        for index, text in enumerate(texts):
            compiled = fixed_public_state_characteristics_handler(
                text,
                source_name=source.printed_name,
            )
            self.assertIsNotNone(compiled)
            assert compiled is not None
            key = f"test:public-condition:{ref}:{index}"
            engine.semantics.put(
                SemanticProgram(
                    key=key,
                    label=f"Public condition {ref} {index}",
                    oracle_id=source.oracle_id,
                    ability_id=f"static:{key}",
                    active_zone="battlefield",
                    event="characteristics.evaluate",
                    handlers=[compiled[1]],
                    trust_level="provisional",
                )
            )
            fragments.append(
                ability_fragment_to_dict(StaticComponentSpec(key))
            )
        return source

    @staticmethod
    def resolve_top(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    @staticmethod
    def creature(
        engine,
        *,
        seat: str,
        name: str,
        subtype: str = "Minotaur",
        colors: tuple[str, ...] = (),
    ) -> CardInstance:
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={
                "type_line": f"Token Creature — {subtype}",
                "power": "3",
                "toughness": "3",
                "keywords": [],
                "colors": list(colors),
            },
            reason="fixed public-state characteristic assurance",
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

    @staticmethod
    def attach(source: CardInstance, target: CardInstance) -> None:
        source.attached_to = target.object_id
        if source.object_id not in target.attachments:
            target.attachments.append(source.object_id)

    def test_live_public_object_state_and_layer_five_queries_recompute(self):
        session = self.session(118_220_004)
        engine = session.engine
        self.add_source(
            session,
            name="Public State Attack Standard",
            ref="ATTACK-STANDARD",
        )
        self.add_source(
            session,
            name="Public State Modification Standard",
            ref="MODIFIED-STANDARD",
        )
        metalcraft = self.add_source(
            session,
            name="Public State Metalcraft Standard",
            ref="METALCRAFT-STANDARD",
        )
        target = self.creature(
            engine,
            seat="A",
            name="Public State Target",
        )

        baseline = engine._effective_card_data(target)
        self.assertEqual("3", baseline["power"])
        self.assertNotIn("Lifelink", baseline["keywords"])
        self.assertNotIn("Trample", baseline["keywords"])

        target.attacking = "B"
        attacking = engine._effective_card_data(target)
        self.assertEqual("4", attacking["power"])
        self.assertIn("Lifelink", attacking["keywords"])
        target.attacking = None

        target.counters["stun"] = 1
        self.assertIn(
            "Trample",
            engine._effective_card_data(target)["keywords"],
        )
        target.counters.clear()

        opposing_aura = self.add_source(
            session,
            name="Aboshan's Desire",
            ref="OPPOSING-AURA",
            seat="B",
        )
        self.attach(opposing_aura, target)
        aura_state = engine._public_object_query_result(target)
        self.assertTrue(aura_state.enchanted)
        self.assertFalse(aura_state.modified)
        self.assertNotIn(
            "Trample",
            engine._effective_card_data(target)["keywords"],
        )
        engine.change_control(
            opposing_aura.object_id,
            "A",
            reason="modified-state controller witness",
        )
        controlled_aura_state = engine._public_object_query_result(target)
        self.assertTrue(controlled_aura_state.enchanted)
        self.assertTrue(controlled_aura_state.modified)
        self.assertIn(
            "Trample",
            engine._effective_card_data(target)["keywords"],
        )

        conditioned = self.add_source(
            session,
            name="Public State Equipment Standard",
            ref="EQUIPPED-STANDARD",
        )
        equipment = self.add_source(
            session,
            name="Javelin of Lightning",
            ref="STATE-EQUIPMENT",
            seat="B",
        )
        self.attach(equipment, conditioned)
        equipment_state = engine._public_object_query_result(conditioned)
        self.assertTrue(equipment_state.equipped)
        self.assertTrue(equipment_state.modified)
        equipped = engine._effective_card_data(conditioned)
        self.assertEqual("3", equipped["power"])
        self.assertEqual("3", equipped["toughness"])
        self.assertIn("Flying", equipped["keywords"])
        equipment.attached_to = None
        conditioned.attachments.remove(equipment.object_id)
        unequipped = engine._effective_card_data(conditioned)
        self.assertEqual("2", unequipped["power"])
        self.assertNotIn("Flying", unequipped["keywords"])

        attachment = self.add_source(
            session,
            name="Public State Attachment Standard",
            ref="ATTACHMENT-STANDARD",
        )
        black_target = self.creature(
            engine,
            seat="A",
            name="Black Attachment Target",
            colors=("B",),
        )
        self.attach(attachment, black_target)
        black = engine._effective_card_data(black_target)
        self.assertEqual("4", black["power"])
        self.assertIn("Wither", black["keywords"])
        black_target.annotations["copy_overrides"]["colors"] = ["U"]
        blue = engine._effective_card_data(black_target)
        self.assertEqual("3", blue["power"])
        self.assertNotIn("Wither", blue["keywords"])

        artifact_refs = engine.create_token(
            "A",
            name="Public Artifact",
            quantity=2,
            characteristics={
                "type_line": "Token Artifact",
                "keywords": [],
            },
            reason="public-state count witness",
        )
        artifact = engine._resolve_object(
            "A",
            artifact_refs[0],
            zones={"battlefield"},
        )
        type_changed = self.creature(
            engine,
            seat="A",
            name="Layer Five Type Witness",
        )
        self.assertNotIn(
            "Shroud",
            engine._effective_card_data(artifact)["keywords"],
        )
        type_changed.annotations["continuous_add_types"] = ["Artifact"]
        self.assertIn(
            "Shroud",
            engine._effective_card_data(artifact)["keywords"],
        )
        type_changed.annotations["continuous_add_types"] = []
        self.assertNotIn(
            "Shroud",
            engine._effective_card_data(artifact)["keywords"],
        )

        type_changed.annotations["continuous_add_types"] = ["Artifact"]
        engine.move_card(metalcraft.object_id, "graveyard", log=False)
        self.assertNotIn(
            "Shroud",
            engine._effective_card_data(artifact)["keywords"],
        )

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.started = True
        engine._grant_priority("D")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:D",
            {
                "action_id": "concede",
                "choices": {"confirm_concede": True},
                "plan": "REPLAY_TYPED_PUBLIC_STATE_CHARACTERISTICS",
                "reason": "Verify typed public-state queries from checkpoint.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as directory:
            record_dir = Path(directory) / "public-state-query-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_opponent_graveyard_threshold_revalidates_by_seat_relation(self):
        session = self.session(118_220_010)
        engine = session.engine
        source = self.add_constructed_condition_source(
            session,
            ref="OPPONENT-GRAVEYARD",
            texts=(
                "As long as an opponent has eight or more cards in their "
                "graveyard, creatures you control have flying.",
            ),
        )

        for seat, count in (("A", 8), ("B", 7)):
            player = engine.state.players[seat]
            while len(player.zones["graveyard"]) < count:
                engine.move_card(
                    player.zones["library"][-1],
                    "graveyard",
                    log=False,
                )

        def keywords() -> set[str]:
            with mock.patch.object(
                CommanderEngine,
                "semantic_program_is_current_trusted",
                return_value=True,
            ):
                return set(engine._effective_card_data(source)["keywords"])

        self.assertNotIn("Flying", keywords())
        opponent = engine.state.players["B"]
        engine.move_card(opponent.zones["library"][-1], "graveyard", log=False)
        self.assertIn("Flying", keywords())
        engine.move_card(opponent.zones["graveyard"][-1], "exile", log=False)
        self.assertNotIn("Flying", keywords())

    def test_public_condition_queries_follow_authoritative_history_and_control(self):
        session = self.session(118_220_007)
        engine = session.engine
        source = self.add_constructed_condition_source(
            session,
            ref="HISTORY-QUERY",
            texts=(
                "This creature gets +2/+0 as long as you've drawn two or "
                "more cards this turn.",
                "This creature gets +0/+2 as long as you've cast two or "
                "more noncreature spells this turn.",
                "As long as you control another multicolored permanent, "
                "this creature gets +1/+1 and has vigilance.",
            ),
        )
        status_source = self.add_constructed_condition_source(
            session,
            ref="PLAYER-STATUS",
            texts=(
                "This creature gets +1/+0 as long as an opponent has three "
                "or more poison counters.",
                "This creature gets +0/+1 as long as you're the monarch.",
            ),
        )
        absence_source = self.add_constructed_condition_source(
            session,
            ref="ABSENCE-QUERY",
            texts=(
                "This creature gets +1/+1 as long as you control no untapped "
                "lands.",
            ),
        )

        def stats() -> tuple[int, int, set[str]]:
            with mock.patch.object(
                CommanderEngine,
                "semantic_program_is_current_trusted",
                return_value=True,
            ):
                current = engine._effective_card_data(source)
            return (
                int(current["power"]),
                int(current["toughness"]),
                set(current["keywords"]),
            )

        self.assertEqual((3, 3, set()), stats())
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            absent = engine._effective_card_data(absence_source)
        self.assertEqual((4, 4), (int(absent["power"]), int(absent["toughness"])))
        land_ref = engine.create_token(
            "A",
            name="Condition Land",
            characteristics={"type_line": "Token Land — Forest"},
            reason="public condition tapped-state quantity",
        )[0]
        land = engine._resolve_object("A", land_ref, zones={"battlefield"})
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            present = engine._effective_card_data(absence_source)
        self.assertEqual(
            (3, 3),
            (int(present["power"]), int(present["toughness"])),
        )
        land.tapped = True
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            tapped = engine._effective_card_data(absence_source)
        self.assertEqual((4, 4), (int(tapped["power"]), int(tapped["toughness"])))
        engine.state.players["B"].poison = 3
        engine.become_monarch("A", reason="condition status fixture")
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            status = engine._effective_card_data(status_source)
        self.assertEqual((4, 4), (int(status["power"]), int(status["toughness"])))
        engine.state.players["B"].poison = 0
        engine.become_monarch("B", reason="condition status fixture")
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            inactive_status = engine._effective_card_data(status_source)
        self.assertEqual(
            (3, 3),
            (int(inactive_status["power"]), int(inactive_status["toughness"])),
        )
        multicolor = self.creature(
            engine,
            seat="A",
            name="Multicolor Condition Witness",
            colors=("R", "W"),
        )
        self.assertEqual((4, 4, {"Vigilance"}), stats())

        engine.apply_effect(
            {"op": "draw", "player": "A", "count": 2},
            actor="A",
        )
        self.assertEqual((6, 4, {"Vigilance"}), stats())
        engine._record_turn_history("spell_cast", actor="A", types=("creature",))
        self.assertEqual((6, 4, {"Vigilance"}), stats())
        engine._record_turn_history("spell_cast", actor="A", types=("instant",))
        engine._record_turn_history("spell_cast", actor="A", types=("sorcery",))
        self.assertEqual((6, 6, {"Vigilance"}), stats())

        projected = session.projector._snapshot("pilot:B")
        rendered = json.dumps(projected, sort_keys=True)
        for object_id in engine.state.players["A"].zones["hand"]:
            hidden = engine.state.cards[object_id]
            self.assertNotIn(hidden.object_id, rendered)
            self.assertNotIn(hidden.ref, rendered)

        engine.change_control(source.object_id, "B", reason="condition owner")
        self.assertEqual((3, 3, set()), stats())
        engine.change_control(source.object_id, "A", reason="condition owner")
        source.annotations["token_characteristics"]["colors"] = ["R", "W"]
        engine.move_card(multicolor.object_id, "graveyard", log=False)
        self.assertEqual((5, 5, set()), stats())

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine._grant_priority("D")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:D",
            {
                "action_id": "concede",
                "choices": {"confirm_concede": True},
                "plan": "REPLAY_PUBLIC_CONDITION_QUERIES",
                "reason": "Verify public condition facts from checkpoint.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as directory:
            record_dir = Path(directory) / "public-condition-query-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_public_fact_thresholds_use_canonical_producers_and_consumers(self):
        session = self.session(118_220_021)
        engine = session.engine
        source = self.creature(
            engine,
            seat="A",
            name="Public Threshold Consumer",
        )
        engine.state.turn_history.events.clear()

        entry_condition = fixed_public_fact_condition(
            "two or more nonland permanents entered the battlefield under "
            "your control this turn",
            source_name=source.printed_name,
        )
        death_condition = fixed_public_fact_condition(
            "two or more creatures died under your control this turn",
            source_name=source.printed_name,
        )
        self.assertIsNotNone(entry_condition)
        self.assertIsNotNone(death_condition)
        assert entry_condition is not None and death_condition is not None

        def holds(condition: FixedPublicStateConditionSpec) -> bool:
            return engine._fixed_public_state_condition_holds(
                source,
                condition,
            )

        self.assertFalse(holds(entry_condition))
        engine.create_token(
            "A",
            name="Threshold Land",
            characteristics={"type_line": "Token Land — Gate"},
            reason="public threshold land control",
        )
        self.assertFalse(holds(entry_condition))
        first_entry_ref = engine.create_token(
            "A",
            name="Threshold Artifact",
            characteristics={"type_line": "Token Artifact"},
            reason="public threshold first nonland",
        )[0]
        self.assertFalse(holds(entry_condition))
        first_entry = engine._resolve_object(
            "A",
            first_entry_ref,
            zones={"battlefield"},
        )
        engine.move_card(first_entry.object_id, "graveyard", log=False)
        second_entries = engine.create_token(
            "A",
            name="Threshold Pair",
            quantity=2,
            characteristics={"type_line": "Token Enchantment"},
            reason="public threshold simultaneous nonlands",
        )
        self.assertTrue(holds(entry_condition))
        second_entry = engine._resolve_object(
            "A",
            second_entries[0],
            zones={"battlefield"},
        )
        engine.change_control(
            second_entry.object_id,
            "B",
            reason="public threshold later control change",
        )
        self.assertTrue(holds(entry_condition))

        advance_fixture_turn(engine)
        self.assertFalse(holds(entry_condition))
        victims = [
            self.creature(
                engine,
                seat="A",
                name=f"Death Threshold Victim {index}",
            )
            for index in range(3)
        ]
        opposing_victim = self.creature(
            engine,
            seat="A",
            name="Death Threshold Opposing Controller",
        )
        engine.state.turn_history.events.clear()
        engine.change_control(
            opposing_victim.object_id,
            "B",
            reason="death threshold previous controller",
        )
        engine.move_card(
            opposing_victim.object_id,
            "graveyard",
            semantic_events=True,
        )
        self.assertFalse(holds(death_condition))
        engine.move_card(
            victims[0].object_id,
            "graveyard",
            semantic_events=True,
        )
        self.assertFalse(holds(death_condition))
        engine.move_card(
            victims[1].object_id,
            "graveyard",
            semantic_events=True,
        )
        self.assertTrue(holds(death_condition))
        engine.move_card(
            victims[2].object_id,
            "graveyard",
            semantic_events=True,
        )
        self.assertTrue(holds(death_condition))

    def test_subtype_attack_threshold_counts_distinct_event_time_objects(self):
        session = self.session(118_220_022)
        engine = session.engine
        source = self.creature(
            engine,
            seat="A",
            name="Subtype Attack Consumer",
        )
        first = self.creature(
            engine,
            seat="A",
            name="First Goblin Attacker",
            subtype="Goblin",
        )
        second = self.creature(
            engine,
            seat="A",
            name="Second Goblin Attacker",
            subtype="Goblin",
        )
        nonmatching = self.creature(
            engine,
            seat="A",
            name="Elf Attacker",
            subtype="Elf",
        )
        engine.state.turn_history.events.clear()
        condition = FixedPublicStateConditionSpec(
            FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
            amount=2,
            fact=(
                FixedPublicStateFact
                .CONTROLLER_ATTACKED_WITH_SUBTYPE_THIS_TURN
            ),
            fact_parameter="goblin",
            schema_version=4,
        )
        historical_condition = FixedPublicStateConditionSpec(
            FixedPublicStateConditionKind.PUBLIC_FACT_AT_LEAST,
            amount=2,
            fact=(
                FixedPublicStateFact
                .CONTROLLER_ATTACKED_WITH_SUBTYPE_THIS_TURN
            ),
            fact_parameter="goblin",
            schema_version=3,
        )
        self.assertEqual(
            historical_condition,
            FixedPublicStateConditionSpec.from_dict(
                historical_condition.to_dict()
            ),
        )

        def holds() -> bool:
            return engine._fixed_public_state_condition_holds(
                source,
                condition,
            )

        record_creature_attack_history(
            engine,
            first,
            {"target": "B", "kind": "player"},
        )
        record_creature_attack_history(
            engine,
            first,
            {"target": "C", "kind": "player"},
        )
        self.assertTrue(
            engine._fixed_public_state_condition_holds(
                source,
                historical_condition,
            )
        )
        self.assertFalse(holds())
        record_creature_attack_history(
            engine,
            nonmatching,
            {"target": "B", "kind": "player"},
        )
        self.assertFalse(holds())
        record_creature_attack_history(
            engine,
            second,
            {"target": "D", "kind": "player"},
        )
        self.assertTrue(holds())
        engine.change_control(
            first.object_id,
            "B",
            reason="attack threshold later control change",
        )
        first.annotations["token_characteristics"]["type_line"] = (
            "Token Creature — Elf"
        )
        self.assertTrue(holds())
        advance_fixture_turn(engine)
        self.assertFalse(holds())

    def test_public_fact_conditions_recompute_and_replay(self):
        session = self.session(118_220_011)
        engine = session.engine
        source = self.add_constructed_condition_source(
            session,
            ref="PUBLIC-FACTS",
            texts=(
                "Delirium — This creature gets +2/+2 as long as there are "
                "four or more card types among cards in your graveyard.",
                "This creature gets +1/+0 as long as you gained life this turn.",
                "Celebration — This creature gets +1/+1 and has trample as "
                "long as two or more nonland permanents entered the "
                "battlefield under your control this turn.",
            ),
        )
        engine.state.turn_history.events.clear()
        def characteristics(card: CardInstance):
            with mock.patch.object(
                CommanderEngine,
                "semantic_program_is_current_trusted",
                return_value=True,
            ):
                return engine._effective_card_data(card)

        baseline = characteristics(source)
        self.assertEqual((3, 3), (int(baseline["power"]), int(baseline["toughness"])))
        self.assertNotIn("Trample", baseline["keywords"])

        engine.apply_life_change_intent(
            LifeChangeIntent(
                actor="A",
                player="A",
                amount=1,
                reason="public-fact condition witness",
            )
        )
        life_active = characteristics(source)
        self.assertEqual(
            (4, 3),
            (int(life_active["power"]), int(life_active["toughness"])),
        )

        hand_source = self.add_constructed_condition_source(
            session,
            ref="HAND-ADVANTAGE",
            texts=(
                "This creature gets +0/+1 as long as you have more cards in "
                "hand than each opponent.",
            ),
        )
        self.assertEqual("3", characteristics(hand_source)["toughness"])
        opponent_max = max(
            len(engine.state.players[seat].zones["hand"])
            for seat in ("B", "C", "D")
        )
        while len(engine.state.players["A"].zones["hand"]) <= opponent_max:
            engine.move_card(
                engine.state.players["A"].zones["library"][-1],
                "hand",
                log=False,
            )
        self.assertEqual("4", characteristics(hand_source)["toughness"])

        life_source = self.add_constructed_condition_source(
            session,
            ref="STARTING-LIFE",
            texts=(
                "As long as your life total is less than or equal to half "
                "your starting life total, this creature has indestructible.",
            ),
        )
        self.assertNotIn("Indestructible", characteristics(life_source)["keywords"])
        engine.apply_life_change_intent(
            LifeChangeIntent(
                actor="A",
                player="A",
                amount=(engine.state.config.starting_life // 2)
                - engine.state.players["A"].life,
                reason="starting-life condition witness",
            )
        )
        self.assertIn("Indestructible", characteristics(life_source)["keywords"])

        planeswalker_source = self.add_constructed_condition_source(
            session,
            ref="PLANESWALKER-SUBTYPE",
            texts=(
                "As long as you control a Nissa planeswalker, this creature "
                "gets +2/+0 and has vigilance.",
            ),
        )
        self.assertEqual("3", characteristics(planeswalker_source)["power"])
        engine.create_token(
            "A",
            name="Nissa Condition Witness",
            characteristics={
                "type_line": "Token Legendary Planeswalker — Nissa",
                "loyalty": "4",
                "keywords": [],
            },
            reason="planeswalker subtype condition witness",
        )
        planeswalker_active = characteristics(planeswalker_source)
        self.assertEqual("5", planeswalker_active["power"])
        self.assertIn("Vigilance", planeswalker_active["keywords"])

        attack_source = self.add_constructed_condition_source(
            session,
            ref="ATTACKED-BATTLE",
            texts=(
                "This creature has indestructible as long as it attacked a "
                "battle this turn.",
            ),
        )
        self.creature(engine, seat="A", name="Celebration Witness")
        normal_entry = next(
            engine.state.cards[object_id]
            for object_id in engine.state.players["A"].zones["library"]
            if (
                (record := engine.card_record(engine.state.cards[object_id]))
                is not None
                and record.is_creature
            )
        )
        engine.move_card(
            normal_entry.object_id,
            "battlefield",
            log=False,
            semantic_events=True,
        )
        self.assertIn(
            normal_entry.logical_object_id,
            {
                event.object_incarnation
                for event in engine._current_turn_history("permanent_entered")
            },
        )
        celebration = characteristics(source)
        self.assertEqual(
            (5, 4),
            (int(celebration["power"]), int(celebration["toughness"])),
        )
        self.assertIn("Trample", celebration["keywords"])

        distinct_types: set[str] = set()
        for object_id in tuple(engine.state.players["A"].zones["library"]):
            card = engine.state.cards[object_id]
            row = engine._public_object_query_result(card)
            if set(row.types) <= distinct_types:
                continue
            engine.move_card(card.object_id, "graveyard", log=False)
            distinct_types.update(row.types)
            if len(distinct_types) >= 4:
                break
        self.assertGreaterEqual(len(distinct_types), 4)
        delirium = characteristics(source)
        self.assertEqual(
            (7, 6),
            (int(delirium["power"]), int(delirium["toughness"])),
        )

        self.assertNotIn("Indestructible", characteristics(attack_source)["keywords"])
        engine._record_turn_history(
            "creature_attacked",
            actor="A",
            object_incarnation=attack_source.logical_object_id,
            target="BATTLE-FACT",
            target_kind="battle",
            types=("creature", "fixture"),
        )
        self.assertIn("Indestructible", characteristics(attack_source)["keywords"])

        with mock.patch(
            "quorune.card_programs.runtime._FixedPublicStateSnapshotResolver."
            "_public_fact_quantity",
            return_value=0,
        ):
            mutated = characteristics(source)
        self.assertEqual(
            (3, 3),
            (int(mutated["power"]), int(mutated["toughness"])),
        )
        self.assertNotIn("Trample", mutated["keywords"])

        projected = json.dumps(session.packet("pilot:B", full=True))
        for object_id in engine.state.players["A"].zones["hand"]:
            hidden = engine.state.cards[object_id]
            self.assertNotIn(hidden.object_id, projected)
            self.assertNotIn(hidden.ref, projected)

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine._grant_priority("D")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:D",
            {
                "action_id": "concede",
                "choices": {"confirm_concede": True},
                "plan": "REPLAY_PUBLIC_FACT_CONDITIONS",
                "reason": "Verify sealed public facts from checkpoint.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as directory:
            record_dir = Path(directory) / "public-fact-condition-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_unrelated_fixed_conditions_do_not_scan_turn_history(self):
        session = self.session(118_220_008)
        engine = session.engine
        source = self.add_constructed_condition_source(
            session,
            ref="NO-HISTORY-SCAN",
            texts=("During your turn, this creature gets +2/+0.",),
        )
        engine.state.active_player = "A"

        with (
            mock.patch.object(
                CommanderEngine,
                "semantic_program_is_current_trusted",
                return_value=True,
            ),
            mock.patch(
                "quorune.card_programs.runtime.drawn_this_turn",
                side_effect=AssertionError("unexpected draw-history scan"),
            ),
            mock.patch(
                "quorune.card_programs.runtime.current_turn_history_events",
                side_effect=AssertionError("unexpected cast-history scan"),
            ),
        ):
            current = engine._effective_card_data(source)

        self.assertEqual(
            (5, 3),
            (int(current["power"]), int(current["toughness"])),
        )

    def test_opponent_color_existence_uses_live_public_query(self):
        session = self.session(118_220_009)
        engine = session.engine
        source = self.add_constructed_condition_source(
            session,
            ref="OPPONENT-COLOR",
            texts=(
                "This creature gets +2/+2 as long as an opponent controls "
                "a black permanent.",
            ),
        )

        def stats() -> tuple[int, int]:
            with mock.patch.object(
                CommanderEngine,
                "semantic_program_is_current_trusted",
                return_value=True,
            ):
                current = engine._effective_card_data(source)
            return int(current["power"]), int(current["toughness"])

        self.assertEqual((3, 3), stats())
        black_permanent = self.creature(
            engine,
            seat="B",
            name="Opponent Color Witness",
            colors=("B",),
        )
        self.assertEqual((5, 5), stats())
        engine.change_control(
            black_permanent.object_id,
            "A",
            reason="opponent public-query relation witness",
        )
        self.assertEqual((3, 3), stats())

    def test_public_state_conditions_recompute_layer_six_and_seven_results(self):
        registry = default_continuous_effect_component_registry()
        descriptor = fixed_public_state_characteristics_handler(
            "During your turn, this creature gets +2/+1 and has first strike.",
            source_name="State Source",
        )[1]
        effects = registry.lower(descriptor, source_context(active_player="A"))
        self.assertEqual([Layer.ABILITY, Layer.POWER_TOUGHNESS], [
            effect.layer for effect in effects
        ])
        source = evaluate_continuous_effects(
            CharacteristicState(
                name="State Source",
                controller="A",
                card_types={"Creature"},
                power=2,
                toughness=2,
            ),
            effects,
            context={
                "object_id": "public-state-source",
                "logical_object_id": "public-state-source@1",
                "ref": "PUBLIC-STATE-SOURCE",
                "owner": "A",
                "controller": "A",
                "zone": "battlefield",
            },
        ).characteristics
        self.assertEqual(4, source["power"])
        self.assertEqual(3, source["toughness"])
        self.assertIn("First Strike", source["abilities"])

        unrelated = evaluate_continuous_effects(
            CharacteristicState(
                name="Other Creature",
                controller="A",
                card_types={"Creature"},
                power=2,
                toughness=2,
            ),
            effects,
            context={
                "object_id": "other",
                "logical_object_id": "other@1",
                "ref": "OTHER",
                "owner": "A",
                "controller": "A",
                "zone": "battlefield",
            },
        ).characteristics
        self.assertEqual(2, unrelated["power"])
        self.assertNotIn("First Strike", unrelated["abilities"])
        stale_source = evaluate_continuous_effects(
            CharacteristicState(
                name="State Source",
                controller="A",
                card_types={"Creature"},
                power=2,
                toughness=2,
            ),
            effects,
            context={
                "object_id": "public-state-source",
                "logical_object_id": "public-state-source@2",
                "ref": "PUBLIC-STATE-SOURCE",
                "owner": "A",
                "controller": "A",
                "zone": "battlefield",
            },
        ).characteristics
        self.assertEqual(2, stale_source["power"])
        self.assertNotIn("First Strike", stale_source["abilities"])
        self.assertEqual(
            (),
            registry.lower(descriptor, source_context(active_player="B")),
        )

        threshold = fixed_public_state_characteristics_handler(
            "Threshold — This creature gets +7/+7 as long as there are seven "
            "or more cards in your graveyard.",
            source_name="Threshold Source",
        )[1]
        self.assertEqual(
            (),
            registry.lower(threshold, source_context(graveyard_count=6, condition_quantity=6)),
        )
        self.assertEqual(
            1,
            len(registry.lower(threshold, source_context(graveyard_count=7, condition_quantity=7))),
        )

        counter = fixed_public_state_characteristics_handler(
            "This creature has trample as long as it has a +1/+1 counter on it.",
            source_name="Counter Source",
        )[1]
        self.assertEqual(
            (),
            registry.lower(counter, source_context(counters=())),
        )
        self.assertEqual(
            1,
            len(
                registry.lower(
                    counter,
                    source_context(counters=(("+1/+1", 1),)),
                )
            ),
        )

        attached_pronoun = fixed_public_state_characteristics_handler(
            "Enchanted creature has shroud as long as it's untapped.",
            source_name="Attached Pronoun Source",
        )[1]
        attached_condition = FixedPublicStateConditionSpec.from_dict(
            attached_pronoun["source_condition"]
        )
        self.assertFalse(
            attached_condition.matches(
                source_context(attached_query_matches=False).public_state
            )
        )
        self.assertTrue(
            attached_condition.matches(
                source_context(attached_query_matches=True).public_state
            )
        )

        opponent_anthem = fixed_public_state_characteristics_handler(
            "Threshold — As long as there are seven or more cards in your "
            "graveyard, creatures your opponents control get -1/-0.",
            source_name="Threshold Opponent Anthem",
        )[1]
        opponent_effects = registry.lower(
            opponent_anthem,
            source_context(graveyard_count=7, condition_quantity=7),
        )

        def opponent_power(controller: str) -> int:
            return int(
                evaluate_continuous_effects(
                    CharacteristicState(
                        name=f"{controller} relation witness",
                        controller=controller,
                        card_types={"Creature"},
                        power=3,
                        toughness=3,
                    ),
                    opponent_effects,
                    context={
                        "object_id": f"{controller}-witness",
                        "logical_object_id": f"{controller}-witness@1",
                        "ref": f"{controller}-WITNESS",
                        "owner": controller,
                        "controller": controller,
                        "zone": "battlefield",
                    },
                ).characteristics["power"]
            )

        self.assertEqual(3, opponent_power("A"))
        self.assertEqual(2, opponent_power("B"))
        self.assertEqual(2, opponent_power("C"))

    def test_turn_gated_attachment_and_anthem_compose(self):
        session = self.session(118_220_001)
        engine = session.engine
        riot = self.add_source(session, name="Street Riot", ref="RIOT")
        javelin = self.add_source(
            session,
            name="Javelin of Lightning",
            ref="JAVELIN",
        )
        desire = self.add_source(
            session,
            name="Aboshan's Desire",
            ref="DESIRE",
            zone="hand",
        )
        target = self.creature(engine, seat="A", name="Conditional Target")
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        engine.state.players["A"].mana_pool["C"] = 4
        engine._activate(
            "A",
            {
                "source": javelin.ref,
                "ability": "ab4",
                "targets": [target.ref],
            },
        )
        self.assertEqual("builtin:equip", engine.state.stack[-1].semantic_key)
        self.resolve_top(engine)
        self.assertEqual(target.object_id, javelin.attached_to)

        engine.state.players["A"].mana_pool["U"] = 1
        engine._cast(
            "A",
            {
                "card": desire.ref,
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"U": 1},
            },
        )
        self.resolve_top(engine)
        self.assertEqual(target.object_id, desire.attached_to)

        active = engine._effective_card_data(target)
        self.assertEqual("6", active["power"])
        self.assertIn("First Strike", active["keywords"])
        self.assertIn("Trample", active["keywords"])
        self.assertIn("Flying", active["keywords"])

        threshold_cards = list(
            engine.state.players["A"].zones["library"][-7:]
        )
        for object_id in threshold_cards:
            engine.move_card(object_id, "graveyard", log=False)
        self.assertIn(
            "Shroud",
            engine._effective_card_data(target)["keywords"],
        )
        for object_id in threshold_cards:
            engine.move_card(object_id, "library", log=False)

        engine.state.active_player = "B"
        inactive = engine._effective_card_data(target)
        self.assertEqual("3", inactive["power"])
        self.assertNotIn("First Strike", inactive["keywords"])
        self.assertNotIn("Trample", inactive["keywords"])
        self.assertIn("Flying", inactive["keywords"])
        self.assertNotIn("Shroud", inactive["keywords"])

        engine.change_control(
            riot.object_id,
            "B",
            reason="public-state controller boundary witness",
        )
        engine.change_control(
            target.object_id,
            "B",
            reason="public-state target boundary witness",
        )
        changed = engine._effective_card_data(target)
        self.assertEqual("4", changed["power"])
        self.assertIn("Trample", changed["keywords"])
        self.assertNotIn("First Strike", changed["keywords"])

        engine.move_card(riot.object_id, "graveyard", log=False)
        departed = engine._effective_card_data(target)
        self.assertEqual("3", departed["power"])
        self.assertNotIn("Trample", departed["keywords"])

    def test_typed_aura_and_public_state_characteristics_compose(self):
        session = self.session(118_220_006)
        engine = session.engine
        aura = self.add_source(
            session,
            name="Public State Attachment Standard",
            ref="TYPED-PUBLIC-STATE-AURA",
            zone="hand",
        )
        target = self.creature(
            engine,
            seat="A",
            name="Typed Aura Public-State Target",
            colors=("B",),
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.priority_passes = []
        engine.state.players["A"].mana_pool.update({"B": 1, "C": 1})

        engine._cast(
            "A",
            {
                "card": aura.ref,
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"B": 1, "C": 1},
            },
        )
        self.resolve_top(engine)

        self.assertEqual(target.object_id, aura.attached_to)
        self.assertTrue(engine._attachment_is_legal(aura, subtypes={"aura"}))
        enhanced = engine._effective_card_data(target)
        self.assertEqual("4", enhanced["power"])
        self.assertEqual("4", enhanced["toughness"])
        self.assertIn("Wither", enhanced["keywords"])

        target.annotations["copy_overrides"] = {
            "type_line": "Token Creature — Wall"
        }
        self.assertFalse(
            engine._attachment_is_legal(aura, subtypes={"aura"})
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual("graveyard", aura.zone)
        self.assertNotIn(
            "Wither",
            engine._effective_card_data(target)["keywords"],
        )

    def test_public_state_characteristics_execute_while_replacement_siblings_fail_closed(self):
        session = self.session(118_220_003)
        engine = session.engine
        record = self.db.lookup("Angel of Vitality")
        program = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="provisional",
        )
        blockers = {
            blocker
            for residual in program.residuals
            for blocker in residual["blockers"]
        }
        self.assertGreaterEqual(
            blockers,
            {
                "replacement applicability",
                "self-replacement and prevention ordering",
            },
        )
        binding = bind_card_program_runtime(
            program,
            capability_registry=self.capabilities,
            profile="commander_review",
        )
        self.assertFalse(binding["strict_capability_ready"])
        self.assertFalse(binding["compatible_ready"])

        angel = self.add_source(
            session,
            name="Angel of Vitality",
            ref="ANGEL-OF-VITALITY",
        )
        engine.state.players["A"].life = 25
        active = engine._effective_card_data(angel)
        self.assertEqual("4", active["power"])
        self.assertEqual("4", active["toughness"])
        self.assertIn("Flying", active["keywords"])
        self.assertFalse(
            any(
                "replacement" in runtime_program.event
                and engine.semantic_program_is_current_trusted(runtime_program)
                for runtime_program in engine.semantics.programs_for_oracle(
                    angel.oracle_id
                )
            )
        )

    def test_hand_count_condition_projects_and_replays_without_hidden_identity(self):
        session = self.session(118_220_002)
        engine = session.engine
        self.add_source(session, name="Neheb, the Worthy", ref="NEHEB")
        target = self.creature(engine, seat="A", name="Private Count Target")
        hand = list(engine.state.players["A"].zones["hand"])
        for object_id in hand[1:]:
            engine.move_card(object_id, "library", log=False)
        hidden = engine.state.cards[hand[0]]
        engine.state.active_player = "A"

        self.assertEqual("5", engine._effective_card_data(target)["power"])
        projected = session.projector._snapshot("pilot:B")
        rendered = json.dumps(projected, sort_keys=True)
        self.assertNotIn(hidden.object_id, rendered)
        self.assertNotIn(hidden.ref, rendered)
        self.assertNotIn(hidden.printed_name, rendered)

        engine.move_card(
            engine.state.players["A"].zones["library"][-1],
            "hand",
            log=False,
        )
        self.assertEqual("3", engine._effective_card_data(target)["power"])
        engine.move_card(
            engine.state.players["A"].zones["hand"][-1],
            "library",
            log=False,
        )
        self.assertEqual("5", engine._effective_card_data(target)["power"])

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"a": "pass"})
            self.assertTrue(result.ok, result.summary)
        with tempfile.TemporaryDirectory() as directory:
            record_dir = Path(directory) / "public-state-characteristic-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
