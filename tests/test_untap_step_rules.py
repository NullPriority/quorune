from __future__ import annotations

import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from common import keep_all, load_assets, make_session
from quorune.compiler.untap_step_templates import (
    static_untap_step_handler,
    static_untap_step_limit_handler,
)
from quorune.engine import CommanderEngine, TURN_STEPS
from quorune.record import checkpoint_envelope, replay_record
from quorune.semantics import SemanticProgram
from quorune.semantic_runtime import default_untap_step_component_registry


class UntapStepRuleTests(unittest.TestCase):
    def test_next_untap_marker_follows_current_controller_and_repeated_markers_expire_together(self):
        from quorune.tap_state import prohibit_next_untap, TapStateError
        session = self.make_session(357007, players=4)
        engine = session.engine
        card = self.token(engine, 'A', 'Controller prohibition', tapped=True)
        for _ in range(2):
            prohibit_next_untap(engine, card.ref, actor='A', reason='repeat marker', logical_object_id=card.logical_object_id)
        engine.change_control(card.object_id, 'B', reason='affected controller witness')
        engine.state.active_player = 'A'
        self.enter_untap(session)
        self.assertTrue(card.annotations['does_not_untap_next'])
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.active_player = 'B'; engine.state.priority_player = None; engine.state.priority_passes = []
        self.enter_untap(session)
        self.assertTrue(card.tapped)
        self.assertNotIn('does_not_untap_next', card.annotations)
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.priority_player = None; engine.state.priority_passes = []
        self.enter_untap(session)
        self.assertFalse(card.tapped)
        card.annotations['does_not_untap_next'] = 'malformed'
        before = dict(card.annotations)
        with self.assertRaises(TapStateError):
            from quorune.tap_state import consume_next_untap_prohibition
            consume_next_untap_prohibition(card)
        self.assertEqual(before, card.annotations)

    def test_next_untap_producer_omission_mutant_is_killed(self):
        from quorune import tap_state
        def witness():
            session = self.make_session(357005)
            card = self.token(session.engine, 'A', 'Omission witness', tapped=True)
            tap_state.prohibit_next_untap(session.engine, card.ref, actor='A',
                reason='omission witness', logical_object_id=card.logical_object_id)
            self.enter_untap(session)
            self.assertTrue(card.tapped)
        witness()
        with mock.patch.object(tap_state, 'prohibit_next_untap', return_value='no-prohibition'):
            with self.assertRaises(AssertionError):
                witness()

    def test_typed_next_untap_producer_pins_incarnation_and_does_not_consume_stun(self):
        from quorune.tap_state import prohibit_next_untap
        session = self.make_session(357003)
        engine = session.engine
        card = self.token(engine, 'A', 'Next untap producer', tapped=True)
        card.counters['stun'] = 1
        identity = card.logical_object_id
        prohibit_next_untap(engine, card.ref, actor='A', reason='typed producer', logical_object_id=identity)
        self.enter_untap(session)
        self.assertTrue(card.tapped)
        self.assertEqual(1, card.counters['stun'])
        self.assertNotIn('does_not_untap_next', card.annotations)
        engine.permissions.invalidate_current(); engine.state.pending_decision = None
        engine.state.priority_player = None; engine.state.priority_passes = []
        self.enter_untap(session)
        self.assertNotIn('stun', card.counters)
        self.assertTrue(card.tapped)
        permanent = self.card(session, 'A', 'Sol Ring')
        engine.move_card(permanent.object_id, 'battlefield', controller='A', reason='original producer control')
        old_identity = permanent.logical_object_id
        prohibit_next_untap(engine, permanent.ref, actor='A', reason='departure marker', logical_object_id=old_identity)
        engine.move_card(permanent.object_id, 'hand', reason='stale producer control')
        engine.move_card(permanent.object_id, 'battlefield', controller='A', reason='stale producer control')
        prohibit_next_untap(engine, permanent.ref, actor='A', reason='stale producer', logical_object_id=old_identity)
        self.assertNotIn('does_not_untap_next', permanent.annotations)

    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int, *, players: int = 2):
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
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def card(session, owner: str, name: str):
        return next(
            card
            for card in session.state.cards.values()
            if card.owner == owner
            and card.is_card_object
            and card.printed_name == name
        )

    @staticmethod
    def token(
        engine,
        controller: str,
        name: str,
        *,
        tapped: bool = False,
        oracle_text: str = "",
        keywords: list[str] | None = None,
        type_line: str = "Token Creature — Construct",
    ):
        ref = engine.create_token(
            controller,
            name=name,
            tapped=tapped,
            characteristics={
                "type_line": type_line,
                "oracle_text": oracle_text,
                "power": "2",
                "toughness": "2",
                "keywords": list(keywords or []),
            },
        )[0]
        return next(
            card
            for card in engine.state.cards.values()
            if card.ref == ref
        )

    @staticmethod
    def enter_untap(session) -> None:
        engine = session.engine
        engine.state.phase_index = TURN_STEPS.index(
            ("beginning", "untap")
        )
        engine._enter_step()

    def test_contract_traces_every_cr_502_rule(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (
                root
                / "mechanics"
                / "contracts"
                / "untap-step.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            {
                "502",
                "502.1",
                "502.2",
                "502.2a",
                "502.3",
                "502.4",
            },
            set(contract["rule_references"]),
        )

    def test_ordinary_untap_is_stackless_and_advances_directly_to_upkeep(
        self,
    ):
        session = self.make_session(50201)
        engine = session.engine
        first = self.token(engine, "A", "First Untap Witness", tapped=True)
        second = self.token(
            engine,
            "A",
            "Second Untap Witness",
            tapped=True,
        )
        before_event = engine.state.event_sequence
        before_priority_epoch = engine.state.priority_epoch

        self.enter_untap(session)

        self.assertFalse(first.tapped)
        self.assertFalse(second.tapped)
        self.assertEqual(
            ("beginning", "upkeep"),
            (engine.state.phase, engine.state.step),
        )
        self.assertEqual("A", engine.state.priority_player)
        self.assertEqual(
            before_priority_epoch + 1,
            engine.state.priority_epoch,
        )
        self.assertFalse(engine.state.stack)
        events = [
            event
            for event in engine.state.events
            if event.event_id > before_event
        ]
        self.assertEqual(
            ["step.begin", "permanent.untap", "step.begin"],
            [event.code for event in events],
        )
        self.assertEqual(
            {first.ref, second.ref},
            set(events[1].details["objects"]),
        )

    def test_stun_and_next_untap_restrictions_precede_the_untap_batch(
        self,
    ):
        session = self.make_session(50202)
        engine = session.engine
        stunned = self.token(engine, "A", "Stunned Witness", tapped=True)
        delayed = self.token(
            engine,
            "A",
            "Next-Untap Witness",
            tapped=True,
        )
        ordinary = self.token(
            engine,
            "A",
            "Ordinary Witness",
            tapped=True,
        )
        stunned.counters["stun"] = 1
        delayed.annotations["does_not_untap_next"] = True

        self.enter_untap(session)

        self.assertTrue(stunned.tapped)
        self.assertNotIn("stun", stunned.counters)
        self.assertTrue(delayed.tapped)
        self.assertNotIn("does_not_untap_next", delayed.annotations)
        self.assertFalse(ordinary.tapped)

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        self.enter_untap(session)

        self.assertFalse(stunned.tapped)
        self.assertFalse(delayed.tapped)

    def test_phasing_turn_action_fails_closed_before_untap_mutation(
        self,
    ):
        session = self.make_session(50203)
        engine = session.engine
        phasing = self.token(
            engine,
            "A",
            "Phasing Witness",
            tapped=True,
            keywords=["Phasing"],
        )
        ordinary = self.token(
            engine,
            "A",
            "Unaffected Witness",
            tapped=True,
        )

        self.enter_untap(session)

        pause = engine._semantic_pause_annotation()
        self.assertIsNotNone(pause)
        self.assertEqual("untap.phasing", pause["event"])
        self.assertEqual("Phasing Witness", pause["label"])
        self.assertEqual(
            ("beginning", "untap"),
            (engine.state.phase, engine.state.step),
        )
        self.assertTrue(phasing.tapped)
        self.assertTrue(ordinary.tapped)
        self.assertIsNone(engine.state.priority_player)
        self.assertFalse(engine.state.stack)

    def test_global_untap_limit_fails_closed_instead_of_untapping_all(
        self,
    ):
        session = self.make_session(50204, players=4)
        engine = session.engine
        limiter = self.token(
            engine,
            "C",
            "Untap Limit Witness",
            oracle_text=(
                "As long as this artifact is untapped, players can't untap "
                "more than two permanents during their untap steps."
            ),
        )
        limiter.annotations["token_characteristics"]["type_line"] = (
            "Token Artifact"
        )
        compiled = static_untap_step_limit_handler(
            limiter.annotations["token_characteristics"]["oracle_text"],
            source_name=limiter.printed_name,
        )
        self.assertIsNotNone(compiled)
        _, descriptor, _ = compiled
        engine.semantics.put(
            SemanticProgram(
                key="test:typed-untap-limit",
                label="Typed untap limit witness",
                oracle_id=limiter.oracle_id,
                ability_id="test:typed-untap-limit",
                active_zone="battlefield",
                event="untap.step",
                handlers=[descriptor],
                trust_level="provisional",
            )
        )
        tapped = [
            self.token(
                engine,
                "A",
                f"Limited Permanent {index}",
                tapped=True,
            )
            for index in range(3)
        ]

        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            self.enter_untap(session)

        pause = engine._semantic_pause_annotation()
        self.assertIsNotNone(pause)
        self.assertEqual(
            "untap.selection_restriction",
            pause["event"],
        )
        self.assertEqual("Untap Limit Witness", pause["label"])
        self.assertTrue(all(card.tapped for card in tapped))
        self.assertIsNone(engine.state.priority_player)

        inactive_session = self.make_session(50206, players=4)
        inactive_engine = inactive_session.engine
        inactive_limiter = self.token(
            inactive_engine,
            "C",
            "Inactive Untap Limit Witness",
            tapped=True,
            oracle_text=(
                "As long as this artifact is untapped, players can't untap "
                "more than two permanents during their untap steps."
            ),
        )
        inactive_limiter.annotations["token_characteristics"][
            "type_line"
        ] = "Token Artifact"
        inactive_compiled = static_untap_step_limit_handler(
            inactive_limiter.annotations["token_characteristics"][
                "oracle_text"
            ],
            source_name=inactive_limiter.printed_name,
        )
        self.assertIsNotNone(inactive_compiled)
        _, inactive_descriptor, _ = inactive_compiled
        inactive_engine.semantics.put(
            SemanticProgram(
                key="test:typed-inactive-untap-limit",
                label="Typed inactive untap limit witness",
                oracle_id=inactive_limiter.oracle_id,
                ability_id="test:typed-inactive-untap-limit",
                active_zone="battlefield",
                event="untap.step",
                handlers=[inactive_descriptor],
                trust_level="provisional",
            )
        )
        unrestricted = [
            self.token(
                inactive_engine,
                "A",
                f"Unrestricted Permanent {index}",
                tapped=True,
            )
            for index in range(3)
        ]

        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            self.enter_untap(inactive_session)

        self.assertIsNone(inactive_engine._semantic_pause_annotation())
        self.assertTrue(inactive_limiter.tapped)
        self.assertTrue(all(not card.tapped for card in unrestricted))

    def test_shared_public_queries_govern_untap_participation(self):
        cases = (
            (
                "Islands don't untap during their controllers' untap steps.",
                "prohibition",
                "any",
                {"types_all": ["land"], "subtypes_all": ["island"]},
            ),
            (
                "Snow permanents don't untap during their controllers' untap steps.",
                "prohibition",
                "any",
                {"supertypes_all": ["snow"]},
            ),
            (
                "Nonland permanents don't untap during their controllers' untap steps.",
                "prohibition",
                "any",
                {"excluded_types": ["land"]},
            ),
            (
                "Untap all artifacts you control during each other player's untap step.",
                "additional",
                "source_controller",
                {"types_all": ["artifact"]},
            ),
        )
        registry = default_untap_step_component_registry()
        for index, (text, kind, relation, predicate_fields) in enumerate(cases):
            with self.subTest(text=text):
                compiled = static_untap_step_handler(
                    text,
                    source_name=f"Untap Query Source {index}",
                )
                self.assertIsNotNone(compiled)
                assert compiled is not None
                _template_id, descriptor, _capability = compiled
                registry.validate(descriptor)
                self.assertEqual(kind, descriptor["instruction"]["kind"])
                self.assertEqual(
                    relation,
                    descriptor["subject"]["controller_relation"],
                )
                for field, value in predicate_fields.items():
                    self.assertEqual(
                        value,
                        descriptor["subject"]["predicate"][field],
                    )

        session = self.make_session(50207)
        engine = session.engine
        source = self.card(session, "A", "Sai, Master Thopterist")
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        island = self.token(
            engine,
            "A",
            "Island Witness",
            tapped=True,
            type_line="Token Land — Island",
        )
        forest = self.token(
            engine,
            "A",
            "Forest Witness",
            tapped=True,
            type_line="Token Land — Forest",
        )
        compiled = static_untap_step_handler(
            "Islands don't untap during their controllers' untap steps.",
            source_name=source.printed_name,
        )
        assert compiled is not None
        engine.semantics.put(
            SemanticProgram(
                key="test:shared-island-untap-query",
                label="Shared Island untap query",
                oracle_id=source.oracle_id,
                ability_id="static:test:shared-island-untap-query",
                active_zone="battlefield",
                event="untap.step",
                handlers=[compiled[1]],
                trust_level="provisional",
            )
        )
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            self.enter_untap(session)
        self.assertTrue(island.tapped)
        self.assertFalse(forest.tapped)

        engine.move_card(source.object_id, "graveyard", log=False)
        island.tapped = True
        forest.tapped = True
        with mock.patch.object(
            CommanderEngine,
            "semantic_program_is_current_trusted",
            return_value=True,
        ):
            self.enter_untap(session)
        self.assertFalse(island.tapped)
        self.assertFalse(forest.tapped)

    def test_untap_trigger_waits_for_upkeep_priority_and_replays(
        self,
    ):
        session = self.make_session(50205)
        engine = session.engine
        source = self.card(session, "A", "Sai, Master Thopterist")
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        source.tapped = True
        engine.semantics.put(
            SemanticProgram(
                key=f"{source.oracle_id}:test:cr502-untapped",
                label="CR 502 held untap trigger",
                oracle_id=source.oracle_id,
                ability_id="test:cr502-untapped",
                active_zone="battlefield",
                event="permanent.untap.self",
                effects=[],
            )
        )
        before_priority_epoch = engine.state.priority_epoch

        self.enter_untap(session)

        self.assertEqual(
            ("beginning", "upkeep"),
            (engine.state.phase, engine.state.step),
        )
        self.assertEqual(
            ["CR 502 held untap trigger"],
            [item.label for item in engine.state.stack],
        )
        self.assertEqual("A", engine.state.priority_player)
        self.assertEqual(
            before_priority_epoch + 1,
            engine.state.priority_epoch,
        )

        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        for seat in ("A", "B"):
            result = session.act(
                f"pilot:{seat}",
                {
                    "a": "pass",
                    "reason": "Pass priority over the held untap trigger.",
                },
            )
            self.assertTrue(result.ok, result.summary)
        self.assertFalse(engine.state.stack)
        self.assertEqual("A", engine.state.priority_player)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "untap-held-trigger"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(2, replay["commands"])


class NextUntapOriginalAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'next-untap-originals.sqlite3'
        build_fixture_database(ROOT / 'tests/fixtures/bound-effect-program-cards.json', path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def program(self, name):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        from quorune.oracle_ir import compile_oracle_card, register_generated_programs
        from quorune.semantics import SemanticRegistry
        semantics = SemanticRegistry()
        card = self.db.lookup(name, fuzzy=False)
        register_generated_programs(self.db, semantics, (card,), trust_level='provisional',
            capability_registry=self.registry, capability_profile='commander_review',
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
            capability_registry=self.registry, capability_profile='commander_review')
        bound = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(bound['strict_capability_ready'], bound['blockers'])
        self.assertEqual('capability_closed', bound['trust_basis'])
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        return card, ir, program

    def test_verified_next_untap_owner_closes_only_its_instruction_coverage(self):
        from quorune.rules.capabilities import capability_covered_mechanics
        self.assertIn('tap-and-untap', capability_covered_mechanics(('untap.step.prohibit_next_controller',)))
        self.assertNotIn('tap-and-untap', capability_covered_mechanics(()))
        self.assertNotIn('tap-and-untap', capability_covered_mechanics(('life.change.effect',)))

    def test_all_original_bodies_keep_entry_keyword_cost_and_mana_siblings(self):
        expected_kinds = {
            'Skyline Cascade': {'replacement_effect', 'triggered_ability', 'mana_ability'},
            "Kefnet's Monument": {'static_ability', 'triggered_ability'},
            'Fogwalker': {'keyword_ability', 'triggered_ability'},
        }
        for name, kinds in expected_kinds.items():
            with self.subTest(name=name):
                card, ir, _ = self.program(name)
                self.assertEqual(kinds, {node.kind for node in ir.faces[0].nodes})
                node, = [node for node in ir.faces[0].nodes
                    if any(effect.get('op') == 'prohibit_next_untap' for effect in node.effects)]
                self.assertEqual(card.oracle_text[node.span.start:node.span.end], node.text)
                self.assertIn('untap.step.prohibit_next_controller', node.capability_dependencies)
                self.assertIn('target.revalidate_resolution', node.capability_dependencies)
                self.assertEqual('opponent', node.target_schema['controller_relation'])

    def test_single_target_descriptor_rejects_bool_counts_foreign_bindings_and_future_steps(self):
        from quorune.rules.tap_state_capability_shapes import targeted_tap_state_node_capabilities
        schema = {'zones': ['battlefield'], 'categories': ['permanent'], 'count': 1,
            'types_any': ['creature'], 'controller_relation': 'opponent'}
        effect = {'op': 'prohibit_next_untap', 'card': '$target.0'}
        def owned(effect, schema):
            return targeted_tap_state_node_capabilities(effects=(effect,), target_schema=schema,
                mechanic_ids=('tap-and-untap', 'cr-115-targets'))
        self.assertIn('untap.step.prohibit_next_controller', owned(effect, schema))
        for changed in ({**schema, 'count': True}, {**schema, 'count': 2},
                        {**schema, 'controller_relation': 'owner_opponent'}):
            with self.subTest(schema=changed):
                self.assertEqual((), owned(effect, changed))
        for changed in ({**effect, 'card': '$source'}, {**effect, 'steps': 2},
                        {**effect, 'player': '$controller'}):
            with self.subTest(effect=changed):
                self.assertEqual((), owned(changed, schema))

    def test_registered_minimum_cannot_be_removed_with_coverage_hints(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        _, _, program = self.program('Fogwalker')
        ability, = [ability for ability in program.abilities
            if any(effect.get('op') == 'prohibit_next_untap' for effect in ability.effects)]
        dependencies = tuple(value for value in ability.capability_dependencies
            if value != 'untap.step.prohibit_next_controller')
        changed = replace(ability, coverage=(), capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(bound['strict'], bound)
        self.assertIn('capability:undeclared_runtime_dependency:untap.step.prohibit_next_controller', bound['blockers'])


from inherited_composition_fixture import InheritedNativeFixture


class NextUntapOriginalNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'next-untap-native.sqlite3'
        build_fixture_database(ROOT / 'tests/fixtures/bound-effect-program-cards.json', path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Next untap originals', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30),
        ], ['Generic Bound Commander'])

    def reject(self, session, seat, command):
        from quorune.record import authoritative_state_hash
        before = authoritative_state_hash(session.state)
        result = session.act('pilot:' + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def mark(self, session, card):
        from quorune.tap_state import NEXT_UNTAP_PROHIBITION_ANNOTATION
        return session.state.cards[card.object_id].annotations.get(NEXT_UNTAP_PROHIBITION_ANNOTATION)

    def finish(self, session, private):
        for seat in 'ABC':
            self.assertNotIn(private.ref, str(session.packet('pilot:' + seat, full=True)))
        self.replay(session, load=True)

    def advance(self, session, seat, phase=None):
        def reached(current):
            return current.state.active_player == seat and (phase is None or current.state.phase == phase)
        for _ in range(8):
            self.until(session, lambda current: reached(current)
                or (current.state.pending_decision is not None
                    and current.state.pending_decision.kind == 'cleanup.discard'))
            if reached(session):
                return
            principal = session.pending_principals()[0]
            context = session.packet(principal, full=True)['decision']['ctx']
            hand = [row for row in context['hand'] if row.get('name') == 'Generic Bound Plains']
            self.assertGreaterEqual(len(hand), context['count'])
            result = session.act(principal, {'action_id': 'discard',
                'cards': [row['id'] for row in hand[:context['count']]]})
            self.assertTrue(result.ok, result.summary)
        self.fail('Native next-untap turn advance did not settle')

    def test_all_originals_use_real_actions_and_follow_current_controller_once(self):
        session = self.session(50283001)
        skyline = self.add(session.engine, 'Skyline Cascade', zone='hand', ref='next-skyline')
        monument = self.add(session.engine, "Kefnet's Monument", zone='hand', ref='next-monument')
        fogwalker = self.add(session.engine, 'Fogwalker', zone='hand', ref='next-fogwalker')
        green = self.add(session.engine, 'Grizzly Bears', zone='hand', ref='next-green-cast')
        own = self.add(session.engine, 'Grizzly Bears', ref='next-own'); own.owner = 'B'
        untapped = self.add(session.engine, 'Grizzly Bears', seat='B', ref='next-untapped')
        moving = self.add(session.engine, 'Grizzly Bears', seat='C', ref='next-controller'); moving.tapped = True
        moving.counters['stun'] = 1
        departed = self.add(session.engine, 'Grizzly Bears', seat='D', ref='next-incarnation'); departed.tapped = True
        other = self.add(session.engine, 'Grizzly Bears', seat='B', ref='next-other-color'); other.tapped = True
        forest = self.add(session.engine, 'Forest', ref='next-forest')
        plains = self.add(session.engine, 'Generic Bound Plains', ref='next-plains')
        self.add(session.engine, 'Forest', seat='D', ref='next-return-forest')
        self.add(session.engine, 'Generic Bound Plains', seat='D', ref='next-return-plains')
        control = self.add(session.engine, 'Control Magic', zone='hand', ref='next-control')
        bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand', ref='next-bounce')
        destroy = self.add(session.engine, 'Disenchant', seat='B', zone='hand', ref='next-destroy')
        private = self.add(session.engine, 'Unsummon', seat='D', zone='hand', ref='next-private')
        for card in (skyline, monument, fogwalker, control, bounce, destroy):
            self.assert_closed(session, card)
        self.prepare(session, mana={'C': 5, 'U': 3})
        session.state.players['B'].mana_pool.update({'U': 1, 'W': 1, 'C': 1})
        self.checkpoint(session)

        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'] == 'play-land:' + skyline.ref)
        self.reject(session, 'C', {'action_id': offered['id']})
        accepted = session.act('pilot:A', {'action_id': offered['id']})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('battlefield', session.state.cards[skyline.object_id].zone)
        self.assertTrue(session.state.cards[skyline.object_id].tapped)
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        self.reject(session, 'A', {'action_id': 'choose', 'targets': [own.ref]})
        self.target(session, 'A', [untapped.ref])
        self.assertIsNone(self.resolve(session))
        self.assertFalse(session.state.cards[untapped.object_id].tapped)
        self.assertIs(self.mark(session, untapped), True)

        def resources():
            return (sum(session.state.players['A'].mana_pool.values())
                + int(not session.state.cards[forest.object_id].tapped)
                + int(not session.state.cards[plains.object_id].tapped))
        before_resources = resources()
        self.cast_for(session, 'A', monument, reserve={'G': 1, 'U': 3})
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before_resources - 3, resources())
        before_resources = resources()
        self.cast_for(session, 'A', fogwalker, reserve={'G': 1, 'U': 2})
        self.assertEqual('stack', session.state.cards[fogwalker.object_id].zone)
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        self.assertEqual(before_resources - 1, resources())
        self.target(session, 'A', [moving.ref])
        self.resolve_top(session)
        self.assertEqual('stack', session.state.cards[fogwalker.object_id].zone)
        self.assertIs(self.mark(session, moving), True)
        self.assertIsNone(self.mark(session, departed))
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.target(session, 'A', [departed.ref])
        self.assertIsNone(self.resolve(session))
        self.assertIs(self.mark(session, departed), True)
        self.assertIn('skulk', session.engine._combat_keywords(session.state.cards[fogwalker.object_id]))
        session = self.replay(session, load=True)

        before_resources = resources()
        self.cast_for(session, 'A', green, reserve={'C': 2, 'U': 2})
        self.assertEqual('semantic.target', session.state.pending_decision.kind)
        self.assertEqual(before_resources - 2, resources())
        self.assertIsNone(self.mark(session, other))
        self.target(session, 'A', [other.ref])
        self.resolve_top(session)
        self.assertEqual('stack', session.state.cards[green.object_id].zone)
        self.assertIs(self.mark(session, other), True)
        self.assertIsNone(self.resolve(session))
        self.cast_for(session, 'A', control, [moving.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('A', session.state.cards[moving.object_id].controller)
        self.assertIs(self.mark(session, moving), True)
        old_identity = session.state.cards[departed.object_id].logical_object_id
        self.cast_for(session, 'B', bounce, [departed.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[departed.object_id].zone)
        self.assertIsNone(self.mark(session, departed))
        self.cast_for(session, 'B', destroy, [monument.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[monument.object_id].zone)
        self.assertIs(self.mark(session, moving), True)
        session = self.replay(session, load=True)

        self.advance(session, 'B')
        self.assertFalse(session.state.cards[untapped.object_id].tapped)
        self.assertIsNone(self.mark(session, untapped))
        self.assertTrue(session.state.cards[other.object_id].tapped)
        self.assertIsNone(self.mark(session, other))
        self.advance(session, 'C')
        self.assertTrue(session.state.cards[moving.object_id].tapped)
        self.assertIs(self.mark(session, moving), True)
        self.advance(session, 'D', 'precombat_main')
        self.cast_for(session, 'D', session.state.cards[departed.object_id])
        self.assertEqual(1, len(session.state.stack))
        self.assertIsNone(self.resolve(session))
        returned = session.state.cards[departed.object_id]
        self.assertNotEqual(old_identity, returned.logical_object_id)
        self.assertFalse(returned.tapped)
        self.assertIsNone(self.mark(session, departed))
        self.advance(session, 'A')
        self.assertTrue(session.state.cards[moving.object_id].tapped)
        self.assertEqual(1, session.state.cards[moving.object_id].counters['stun'])
        self.assertIsNone(self.mark(session, moving))
        self.assertFalse(session.state.cards[skyline.object_id].tapped)
        before_blue = session.state.players['A'].mana_pool.get('U', 0)
        offered = next(row for row in session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if row['id'].startswith('activate:' + skyline.ref + ':'))
        result = session.act('pilot:A', {'action_id': offered['id']})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(before_blue + 1, session.state.players['A'].mana_pool.get('U', 0))
        self.assertTrue(session.state.cards[skyline.object_id].tapped)
        self.finish(session, private)

    def test_departed_single_target_fizzles_after_real_fogwalker_entry(self):
        session = self.session(50283002)
        source = self.add(session.engine, 'Fogwalker', zone='hand', ref='next-fizzle-source')
        target = self.add(session.engine, 'Grizzly Bears', seat='B', ref='next-fizzle-target'); target.tapped = True
        bounce = self.add(session.engine, 'Unsummon', seat='B', zone='hand', ref='next-fizzle-bounce')
        private = self.add(session.engine, 'Unsummon', seat='D', zone='hand', ref='next-fizzle-private')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 1, 'U': 1})
        session.state.players['B'].mana_pool.update({'U': 1})
        self.checkpoint(session)
        self.cast_for(session, 'A', source)
        self.assertEqual('semantic.target', self.resolve(session).kind)
        self.target(session, 'A', [target.ref])
        self.cast_for(session, 'B', bounce, [target.ref])
        self.resolve_top(session)
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[target.object_id].zone)
        self.assertIsNone(self.mark(session, target))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertIn('skulk', session.engine._combat_keywords(session.state.cards[source.object_id]))
        self.finish(session, private)


if __name__ == "__main__":
    unittest.main()
