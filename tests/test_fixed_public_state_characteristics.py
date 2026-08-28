from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.card_programs import (
    bind_card_program_runtime,
    compile_card_program,
)
from quorune.carddb import CardDatabase
from quorune.compiler.continuous_templates import (
    fixed_public_state_characteristics_handler,
)
from quorune.continuous_conditions import (
    FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
    FixedPublicStateConditionSnapshot,
)
from quorune.continuous_effects import (
    CharacteristicState,
    Layer,
    evaluate_continuous_effects,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import checkpoint_envelope, replay_record
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime import (
    ContinuousEffectSourceContext,
    default_continuous_effect_component_registry,
)
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database


CAPABILITY_ID = "continuous.characteristics.fixed_public_state"
TEMPLATE_ID = "continuous-fixed-public-state-characteristics-v1"
FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "fixed-public-state-characteristics-cards.json"
)


def focused_card_database(directory: str) -> CardDatabase:
    path = Path(directory) / "fixed-public-state-characteristics.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE,
            ROOT
            / "tests"
            / "fixtures"
            / "fixed-public-state-interaction-cards.json",
        ],
        path,
    )
    return CardDatabase(path)


def source_context(
    *,
    active_player: str | None = "A",
    hand_count: int = 3,
    graveyard_count: int = 0,
    controller_life: int = 40,
    opponent_life: tuple[int, ...] = (40, 40, 40),
    turn_sequence: int = 4,
    entered_turn_sequence: int = 1,
    counters: tuple[tuple[str, int], ...] = (),
) -> ContinuousEffectSourceContext:
    return ContinuousEffectSourceContext(
        source_object_id="public-state-source",
        source_ref="PUBLIC-STATE-SOURCE",
        source_controller="A",
        source_timestamp=7,
        component_id="public-state:0",
        source_logical_object_id="public-state-source@1",
        public_state=FixedPublicStateConditionSnapshot(
            source_controller="A",
            active_player=active_player,
            controller_hand_count=hand_count,
            controller_graveyard_card_count=graveyard_count,
            controller_life=controller_life,
            opponent_life_totals=opponent_life,
            turn_sequence=turn_sequence,
            source_entered_battlefield_turn_sequence=(
                entered_turn_sequence
            ),
            source_counters=counters,
        ),
    )


class FixedPublicStateCharacteristicCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_closed_public_state_grammar_compiles_capability_closed(self):
        cases = {
            "Fresh-Faced Recruit": "controller_turn",
            "Street Riot": "controller_turn",
            "Krosan Beast": "controller_graveyard_card_count_at_least",
            "Chaos Imps": "source_counter_at_least",
            "Keldon Strike Team": "source_entered_this_turn",
            "Neheb, the Worthy": "controller_hand_count_at_most",
        }
        for name, condition_kind in cases.items():
            with self.subTest(name=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                nodes = [
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.template_id == TEMPLATE_ID
                ]
                self.assertEqual(1, len(nodes))
                node = nodes[0]
                self.assertTrue(node.exact)
                self.assertEqual(
                    FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID,
                    node.handlers[0]["handler_id"],
                )
                self.assertEqual(
                    condition_kind,
                    node.handlers[0]["source_condition"]["kind"],
                )
                self.assertIn(CAPABILITY_ID, node.capability_dependencies)
                self.assertGreater(node.span.line, 0)

        street = compile_card_program(
            self.db,
            self.db.lookup("Street Riot"),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        self.assertEqual((), street.residuals)
        ability = next(
            ability
            for ability in street.abilities
            if ability.provenance.get("template_id") == TEMPLATE_ID
        )
        self.assertGreaterEqual(
            set(ability.capability_dependencies),
            {
                CAPABILITY_ID,
                "continuous.power_toughness.fixed_anthem",
                "continuous.ability.fixed_query_keyword_grant",
                "combat.damage.assignment.trample",
            },
        )

    def test_unrepresented_conditions_and_bodies_remain_residual(self):
        base = self.db.lookup("Fresh-Faced Recruit")
        unsupported = (
            "Delirium — This creature gets +2/+2 as long as there are four "
            "or more card types among cards in your graveyard.",
            "Metalcraft — This creature gets +2/+2 as long as you control "
            "three or more artifacts.",
            "This creature gets +1/+1 as long as you control a Forest.",
            "This creature gets +X/+X during your turn.",
            "During your turn, this creature has ward {2}.",
            "During your turn, this creature has \"{T}: Draw a card.\"",
            "This creature gets +2/+0 as long as it's attacking.",
            "As long as this creature is equipped, it has flying.",
        )
        for index, text in enumerate(unsupported):
            with self.subTest(text=text):
                record = replace(
                    base,
                    oracle_id=f"00000000-0000-4000-8000-{118_200_000 + index:012d}",
                    oracle_text=text,
                    keywords=(),
                )
                program = compile_card_program(
                    self.db,
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="provisional",
                )
                self.assertTrue(program.residuals)
                self.assertFalse(
                    any(
                        descriptor.get("handler_id")
                        == FIXED_PUBLIC_STATE_CHARACTERISTICS_HANDLER_ID
                        for ability in program.abilities
                        for descriptor in ability.handlers
                    )
                )

    def test_public_state_descriptors_fail_closed_without_effects(self):
        compiled = fixed_public_state_characteristics_handler(
            "During your turn, this creature gets +2/+0 and has first strike.",
            source_name="Descriptor Fixture",
        )
        self.assertIsNotNone(compiled)
        descriptor = compiled[1]
        registry = default_continuous_effect_component_registry()
        registry.validate(descriptor)
        self.assertEqual(2, len(registry.lower(descriptor, source_context())))
        self.assertEqual(
            (),
            registry.lower(
                descriptor,
                source_context(active_player="B"),
            ),
        )

        malformed = []
        for path, value in (
            (("unknown",), True),
            (("schema_version",), True),
            (("source_condition", "kind"), "metalcraft"),
            (("source_condition", "amount"), 1),
            (("target", "kind"), "opponents"),
            (("target", "exclude_source"), 1),
            (("modifier", "add_abilities"), ["Ward {2}"]),
            (("modifier", "power"), True),
        ):
            candidate = copy.deepcopy(descriptor)
            target = candidate
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            malformed.append(candidate)
        for candidate in malformed:
            with self.subTest(candidate=candidate):
                with self.assertRaises(SemanticNodeError):
                    registry.validate(candidate)

    def test_condition_and_target_mutants_change_card_program_fingerprint(self):
        record = self.db.lookup("Street Riot")
        expected = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="provisional",
        )
        original = fixed_public_state_characteristics_handler

        for mutation in ("condition", "target"):
            def mutated(text: str, *, source_name: str):
                compiled = original(text, source_name=source_name)
                if compiled is None:
                    return None
                template_id, descriptor, capabilities = compiled
                changed = copy.deepcopy(descriptor)
                if mutation == "condition":
                    changed["source_condition"]["kind"] = "other_turn"
                else:
                    changed["target"]["target_controller"] = "any"
                return template_id, changed, capabilities

            with self.subTest(mutation=mutation), mock.patch(
                "quorune.compiler.runtime_templates."
                "fixed_public_state_characteristics_handler",
                side_effect=mutated,
            ):
                changed = compile_card_program(
                    self.db,
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="provisional",
                )
                self.assertNotEqual(
                    expected.fingerprint,
                    changed.fingerprint,
                )


class FixedPublicStateCharacteristicRuntimeTests(unittest.TestCase):
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
    ) -> CardInstance:
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={
                "type_line": f"Token Creature — {subtype}",
                "power": "3",
                "toughness": "3",
                "keywords": [],
            },
            reason="fixed public-state characteristic assurance",
        )[0]
        return engine._resolve_object(seat, ref, zones={"battlefield"})

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
            registry.lower(threshold, source_context(graveyard_count=6)),
        )
        self.assertEqual(
            1,
            len(registry.lower(threshold, source_context(graveyard_count=7))),
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

    def test_aura_and_equip_attachments_compose_with_public_state_characteristics(self):
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


if __name__ == "__main__":
    unittest.main()
