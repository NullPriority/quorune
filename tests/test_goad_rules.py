from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from common import keep_all, load_assets, make_session
from quorune.card_programs import compile_card_program
from copy import deepcopy
from quorune.carddb import CardDatabase
from scripts.build_test_database import build_fixture_database
from quorune.compiler.combat_metadata_templates import (
    static_goad_prohibition_handler,
)
from quorune.engine import GameRuleError
from quorune.model import CardInstance, CombatState, TurnEntry
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.combat_metadata import (
    GOAD_PROHIBITION_EVENT,
    GOAD_PROHIBITION_HANDLER_ID,
    default_goad_prohibition_registry,
)
from quorune.semantic_runtime.context import SemanticNodeError


class FixedGoadInstructionRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition,DeckEntry
        cls.temporary=tempfile.TemporaryDirectory();root=Path(__file__).parents[1]
        path=Path(cls.temporary.name)/'fixed-goad.sqlite3'
        build_fixture_database([root/'tests/fixtures/bound-effect-program-cards.json',
            root/'tests/fixtures/fixed-goad-instruction-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Generic Goad Deck',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _Actions
    add=_Actions.add
    ready=_Actions.ready
    checkpoint=_Actions.checkpoint
    resolve=_Actions.resolve
    replay=_Actions.replay

    def session(self,seed):
        from quorune.semantics import SemanticRegistry
        session=self._Actions.session(self,seed);engine=session.engine
        engine.semantics=SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(self.db,engine.semantics,tuple(self.db.iter_cards()),trust_level='provisional',
            capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        return session

    def test_original_disrupt_decorum_goads_only_resolving_opponent_set_and_replays(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        session=self.session(388001);engine=session.engine
        spell=self.add(engine,'Disrupt Decorum',zone='hand')
        own=self.add(engine,'Generic Bound Body',ref='own-ungoaded')
        targets=[self.add(engine,'Generic Bound Body',seat=seat,ref='goad-'+seat) for seat in 'BCD']
        phased=self.add(engine,'Generic Bound Body',seat='B',ref='phased-ungoaded');phased.phased_out=True
        action=self.ready(session,spell,{'R':2,'C':2})
        program=compile_best_available_card_program(self.db,self.db.lookup('Disrupt Decorum'),semantic_registry=engine.semantics,
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])
        self.checkpoint(session);before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':action['id'],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual([],engine.state.cards[own.object_id].goaded_by)
        self.assertEqual([],engine.state.cards[phased.object_id].goaded_by)
        for card in targets:self.assertEqual(['A'],[d.player for d in engine.state.cards[card.object_id].goaded_by])
        self.replay(session)
        late=self.add(engine,'Generic Bound Body',seat='B',ref='late-ungoaded')
        self.assertEqual([],late.goaded_by)
        # Requirements use the current B controller but retain A as goader.
        attacker=engine.state.cards[targets[0].object_id];attacker.acquired_control_turn_count=-1
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.active_player='B';engine.state.started=True
        engine.state.phase='combat';engine.state.step='declare_attackers';engine.state.phase_index=5
        engine.state.combat=CombatState();engine._issue_attackers()
        self.checkpoint(session);before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':'attack','attackers':{attacker.ref:'A'}})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:B',{'action_id':'attack','attackers':{attacker.ref:'C'}})
        self.assertTrue(accepted.ok,accepted.summary);self.replay(session)

    def test_original_geode_rager_landfall_goads_chosen_player_set_with_source_independent_duration(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        session=self.session(388002);engine=session.engine
        source=self.add(engine,'Geode Rager')
        land=self.add(engine,'Generic Bound Plains',zone='hand',ref='rager-land')
        b=self.add(engine,'Generic Bound Body',seat='B',ref='rager-b-target')
        c=self.add(engine,'Generic Bound Body',seat='C',ref='rager-c-untargeted')
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.active_player='A';engine.state.started=True
        engine.state.phase='precombat_main';engine.state.step='main'
        engine._grant_priority('A');engine.pump()
        program=compile_best_available_card_program(self.db,self.db.lookup('Geode Rager'),semantic_registry=engine.semantics,
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        play=next(row for row in actions if row.get('card')==land.ref and row.get('action')=='play_land')
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':play['id']})
        self.assertTrue(accepted.ok,accepted.summary)
        decision=self.resolve(session)
        self.assertEqual('semantic.target',decision.kind)
        accepted=session.act('pilot:A',{'action_id':'choose','targets':['B']})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(['A'],[v.player for v in engine.state.cards[b.object_id].goaded_by])
        self.assertEqual([],engine.state.cards[c.object_id].goaded_by)
        self.replay(session)
        engine.move_card(source.object_id,'graveyard',reason='fixture Goad source departure')
        self.assertEqual(['A'],[v.player for v in engine._active_goad_designations(engine.state.cards[b.object_id])])
        engine.move_card(b.object_id,'exile',reason='fixture Goad incarnation departure')
        engine.move_card(b.object_id,'battlefield',controller='B',reason='fixture new Goad incarnation')
        self.assertEqual([],engine.state.cards[b.object_id].goaded_by)

    def test_goad_query_rejects_unrepresented_runtime_fields(self):
        from quorune.goad_instruction import validate_fixed_goad
        from quorune.compiler.goad_templates import fixed_goad_effect_template
        malformed=fixed_goad_effect_template("Goad all creatures you don't control.")[1][0]
        for bad in ({**malformed,'reason':'client supplied'},
                    {**malformed,'predicate':{**malformed['predicate'],'include_phased_out':True}}):
            with self.assertRaises(ValueError):validate_fixed_goad(bad)

    def test_original_optional_goad_decline_and_target_reentry_preserve_no_designation(self):
        for seed,leave in ((388003,False),(388004,True)):
            session=self.session(seed);engine=session.engine
            source=self.add(engine,'Jeering Homunculus',zone='hand')
            target=self.add(engine,'Generic Bound Body',seat='B')
            action=self.ready(session,source,{'U':1,'C':1});self.checkpoint(session)
            accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary);decision=self.resolve(session)
            self.assertEqual('semantic.target',decision.kind)
            accepted=session.act('pilot:A',{'action_id':'choose','targets':[target.ref]})
            self.assertTrue(accepted.ok,accepted.summary)
            if leave:
                engine.move_card(target.object_id,'exile',reason='fixture selected target departure')
                engine.move_card(target.object_id,'battlefield',controller='B',reason='fixture distinct return')
                self.checkpoint(session);self.resolve(session)
                self.assertEqual([],engine.state.cards[target.object_id].goaded_by)
            else:
                decision=self.resolve(session)
                self.assertEqual('semantic.choice',decision.kind)
                resumed=self.replay(session,load=True)
                accepted=resumed.act('pilot:A',{'action_id':'choose','choice':'decline'})
                self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual([],resumed.state.cards[target.object_id].goaded_by)
                self.replay(resumed)


class GoadRuleTests(unittest.TestCase):
    def test_fixed_goad_shapes_and_precise_dependency_fail_closed(self):
        from quorune.compiler.goad_templates import fixed_goad_effect_template,GOAD_CAPABILITY
        from quorune.rules.goad_capability_shapes import fixed_goad_node_capabilities
        from quorune.rules.capabilities import CapabilityRegistry
        compiled=fixed_goad_effect_template("Goad all creatures you don't control.")
        self.assertEqual((GOAD_CAPABILITY,),fixed_goad_node_capabilities(effects=compiled[1],
            target_schema=None,mechanic_ids=compiled[3]))
        effect=compiled[1][0]
        for bad in ({**effect,'extra':True},{**effect,'schema_version':True},
                    {**effect,'predicate':{**effect['predicate'],'zones':['hand']}}):
            self.assertEqual((),fixed_goad_node_capabilities(effects=(bad,),target_schema=None,mechanic_ids=compiled[3]))
        card=replace(self.db.lookup('Island'),oracle_id='fixture:goad-mutation',name='Generic Goad',
            type_line='Sorcery',oracle_text="Goad all creatures you don't control.")
        raw=json.loads((Path(__file__).parents[1]/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for row in raw['capabilities']:
            if row['id']==GOAD_CAPABILITY:row['status']='blocked';row['blockers']=['fixture dependency withdrawal']
        self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=CapabilityRegistry(raw),capability_profile='commander_review').status)
        with mock.patch('quorune.compiler.resolution_effect_templates.fixed_goad_effect_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=self.capabilities,capability_profile='commander_review').status)
    def test_fixed_goad_compiler_preserves_closed_target_and_public_set_queries(self):
        from quorune.compiler.goad_templates import fixed_goad_effect_template
        for text in ('Goad target creature you control.','Goad target tapped creature.',
                     "Goad all creatures you don't control.",'Goad all creatures.','Goad each creature target player controls.'):
            compiled=fixed_goad_effect_template(text)
            self.assertIsNotNone(compiled,text)
            self.assertEqual(2,compiled[1][0]['schema_version'])
        for text in ('Goad target permanent.','Goad each creature that had counters put on it this way.',
                     'Goad target creature until end of turn.','Goad target creature, then draw a card.'):
            self.assertIsNone(fixed_goad_effect_template(text),text)
        card=replace(self.db.lookup('Island'),oracle_id='fixture:goad-compiler',name='Generic Goad',
                     type_line='Sorcery',oracle_text="Goad all creatures you don't control.")
        ir=compile_oracle_card(card,capability_registry=self.capabilities,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.to_dict())
        for text in ('Put a +1/+1 counter on target creature and goad it.',
                     'Tap target creature and goad it.'):
            combined=compile_oracle_card(replace(card,oracle_text=text),capability_registry=self.capabilities,
                capability_profile='commander_review')
            self.assertEqual('exact',combined.status,combined.to_dict())
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_combat_session(self, seed: int, *, players: int = 4):
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
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def attacker(
        engine,
        name: str = "Goaded Attacker",
        *,
        controller: str = "A",
    ):
        ref = engine.create_token(
            controller,
            name=name,
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
            temporary_keywords=["Haste"],
        )[0]
        return engine._resolve_object(
            controller,
            ref,
            zones={"battlefield"},
        )

    def add_typed_goad_prohibition(self, session, *, seat: str = "A"):
        engine = session.engine
        record = self.db.lookup("The Kami Knight")
        source = CardInstance(
            object_id=f"fixture:typed-goad-prohibition:{seat}",
            ref=f"{seat}-typed-goad-prohibition",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone="battlefield",
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[source.object_id] = source
        engine.state.players[seat].zones["battlefield"].append(source.object_id)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_capability_declarations=True,
        )
        return source

    def test_contract_traces_every_goad_rule(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (root / "mechanics" / "contracts" / "goad.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            {"701.15", "701.15a", "701.15b", "701.15c", "701.15d"},
            set(contract["rule_references"]),
        )

    def test_single_goad_requires_another_player_when_available_and_replays(self):
        session = self.make_combat_session(7011501)
        engine = session.engine
        attacker = self.attacker(engine)
        engine.apply_effect(
            {"op": "goad", "card": attacker.ref},
            actor="B",
        )
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(session.state)

        constraints = engine.state.pending_decision.payload_by_actor["A"][
            "declaration_constraints"
        ]
        self.assertEqual(2, constraints["maximum_requirements"])
        other = next(
            requirement
            for requirement in constraints["requirements"]
            if requirement["kind"] == "choose_option_in"
        )
        self.assertEqual(["C", "D"], other["options"])
        self.assertEqual(
            ["B"],
            StateProjector(self.db, engine.state)._obj(
                attacker,
                "pilot:A",
            )["goad"],
        )

        before = authoritative_state_hash(session.state)
        rejected = session.act(
            "pilot:A",
            {"a": "attack", "atk": {attacker.ref: "B"}},
        )
        self.assertFalse(rejected.ok)
        self.assertIn("possible 2 requirements", rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))

        accepted = session.act(
            "pilot:A",
            {"a": "attack", "atk": {attacker.ref: "C"}},
        )
        self.assertTrue(accepted.ok, accepted.summary)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "goaded-attack"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
            self.assertTrue(replay["ok"], replay)
            self.assertEqual(1, replay["commands"])

    def test_goad_in_duel_still_requires_attacking_the_goader(self):
        session = self.make_combat_session(7011502, players=2)
        engine = session.engine
        attacker = self.attacker(engine)
        engine.apply_effect(
            {"op": "goad", "card": attacker.ref},
            actor="B",
        )
        problem = engine._attack_declaration_problem("A")

        self.assertEqual(1, problem.maximum_satisfied_requirements())
        engine._issue_attackers()
        result = session.act(
            "pilot:A",
            {"a": "attack", "atk": {attacker.ref: "B"}},
        )
        self.assertTrue(result.ok, result.summary)

    def test_multiple_goaders_create_independent_maximized_requirements(self):
        session = self.make_combat_session(7011503)
        engine = session.engine
        attacker = self.attacker(engine)
        for player in ("B", "C"):
            engine.apply_effect(
                {"op": "goad", "card": attacker.ref},
                actor=player,
            )
        engine._issue_attackers()

        constraints = engine.state.pending_decision.payload_by_actor["A"][
            "declaration_constraints"
        ]
        self.assertEqual(4, constraints["maximum_requirements"])
        rejected = session.act(
            "pilot:A",
            {"a": "attack", "atk": {attacker.ref: "B"}},
        )
        self.assertFalse(rejected.ok)
        accepted = session.act(
            "pilot:A",
            {"a": "attack", "atk": {attacker.ref: "D"}},
        )
        self.assertTrue(accepted.ok, accepted.summary)

    def test_goaded_by_every_opponent_accepts_any_maximal_attack(self):
        session = self.make_combat_session(7011504)
        engine = session.engine
        attacker = self.attacker(engine)
        for player in ("B", "C", "D"):
            engine.apply_effect(
                {"op": "goad", "card": attacker.ref},
                actor=player,
            )
        problem = engine._attack_declaration_problem("A")

        self.assertEqual(5, problem.maximum_satisfied_requirements())
        for defender in ("B", "C", "D"):
            self.assertTrue(
                problem.evaluate({attacker.ref: defender}).legal,
                defender,
            )

    def test_same_player_goad_is_redundant_and_expires_on_their_next_turn(self):
        session = self.make_combat_session(7011505)
        engine = session.engine
        attacker = self.attacker(engine)
        public_epoch = engine._yield_change_epoch("public")
        for _ in range(2):
            engine.apply_effect(
                {"op": "goad", "card": attacker.ref},
                actor="B",
            )

        self.assertEqual(1, len(attacker.goaded_by))
        self.assertEqual(
            public_epoch + 1,
            engine._yield_change_epoch("public"),
        )
        self.assertEqual(2, engine._attack_declaration_problem("A").maximum_satisfied_requirements())
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._begin_turn(
            TurnEntry(
                turn_id="goad-expiration-turn",
                player="B",
                extra=True,
                created_sequence=engine.state.turn_sequence,
            )
        )

        self.assertEqual([], attacker.goaded_by)
        self.assertEqual(
            public_epoch + 2,
            engine._yield_change_epoch("public"),
        )
        self.assertTrue(
            any(
                event.code == "permanent.goad.expire"
                for event in engine.state.events
            )
        )

    def test_zone_change_removes_the_noncopiable_designation(self):
        session = self.make_combat_session(7011506)
        engine = session.engine
        attacker = self.attacker(engine)
        engine.apply_effect(
            {"op": "goad", "card": attacker.ref},
            actor="B",
        )

        engine.move_card(attacker.object_id, "exile", reason="goad witness")

        self.assertEqual([], attacker.goaded_by)

    def test_goad_rejects_a_noncreature(self):
        session = self.make_combat_session(7011507)
        engine = session.engine
        ref = engine.create_token(
            "A",
            name="Test Rock",
            characteristics={"type_line": "Token Artifact"},
        )[0]

        with self.assertRaisesRegex(GameRuleError, "Only a creature"):
            engine.apply_effect({"op": "goad", "card": ref}, actor="B")

    def test_goad_accepts_a_permanent_that_is_both_creature_and_battle(self):
        session = self.make_combat_session(7011509)
        engine = session.engine
        ref = engine.create_token(
            "A",
            name="Animated Siege",
            battle_protector="B",
            characteristics={
                "type_line": "Token Creature Battle — Siege",
                "power": "3",
                "toughness": "3",
                "defense": "4",
            },
        )[0]

        result = engine.apply_effect(
            {"op": "goad", "card": ref},
            actor="B",
        )

        card = engine._resolve_object("A", ref, zones={"battlefield"})
        self.assertEqual(ref, result)
        self.assertEqual(["B"], [value.player for value in card.goaded_by])

    def test_closed_goad_prohibition_compiles_source_spanned_program(self):
        record = self.db.lookup("The Kami Knight")
        card_program = compile_card_program(
            self.db,
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )

        ability = next(
            ability
            for ability in card_program.abilities
            if any(
                descriptor.get("handler_id") == GOAD_PROHIBITION_HANDLER_ID
                for descriptor in ability.handlers
            )
        )
        self.assertEqual(GOAD_PROHIBITION_EVENT, ability.event)
        self.assertEqual("battlefield", ability.active_zone)
        self.assertEqual("front", ability.provenance["face_id"])
        self.assertEqual(2, ability.provenance["source_span"]["line"])
        self.assertIn(
            "combat.goad.prohibition.controller_creatures",
            ability.capability_dependencies,
        )
        descriptor = static_goad_prohibition_handler(
            "Creatures you control can't be goaded."
        )[1]
        self.assertEqual(
            "source_controller",
            descriptor["affected_controller"],
        )
        self.assertEqual("creature", descriptor["affected_card_type"])

    def test_goad_prohibition_near_miss_and_malformed_descriptor_fail_closed(
        self,
    ):
        record = self.db.lookup("The Kami Knight")
        unsupported = (
            "Creatures you control can't be goaded this turn.",
            "Other creatures you control can't be goaded.",
            "Creatures your opponents control can't be goaded.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                program = compile_card_program(
                    self.db,
                    replace(record, oracle_text=text),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="provisional",
                )
                self.assertTrue(program.residuals)
                self.assertFalse(
                    any(
                        ability.event == GOAD_PROHIBITION_EVENT
                        for ability in program.abilities
                    )
                )

        descriptor = static_goad_prohibition_handler(
            "Creatures you control can't be goaded."
        )[1]
        malformed = (
            {**descriptor, "affected_controller": "opponent"},
            {**descriptor, "affected_card_type": "permanent"},
            {**descriptor, "unknown": True},
        )
        registry = default_goad_prohibition_registry()
        for value in malformed:
            with self.subTest(descriptor=value):
                with self.assertRaises(SemanticNodeError):
                    registry.validate(value)

    def test_goad_prohibition_compiler_mutant_is_killed(self):
        record = self.db.lookup("The Kami Knight")

        def assert_compiled() -> None:
            program = compile_card_program(
                self.db,
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
                trust_level="provisional",
            )
            self.assertTrue(
                any(
                    ability.event == GOAD_PROHIBITION_EVENT
                    for ability in program.abilities
                )
            )

        assert_compiled()
        with mock.patch(
            "quorune.compiler.runtime_templates."
            "static_goad_prohibition_handler",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_compiled()

    def test_raw_oracle_goad_prohibition_without_typed_metadata_fails_closed(
        self,
    ):
        session = self.make_combat_session(7011508)
        engine = session.engine
        attacker = self.attacker(engine)
        engine.create_token(
            "A",
            name="Goad Ward",
            characteristics={
                "type_line": "Token Enchantment",
                "oracle_text": "Creatures you control can't be goaded.",
            },
        )

        result = engine.apply_effect(
            {"op": "goad", "card": attacker.ref},
            actor="B",
        )

        self.assertEqual(attacker.ref, result)
        self.assertEqual(["B"], [value.player for value in attacker.goaded_by])
        self.assertNotEqual("permanent.goad.prevented", engine.state.events[-1].code)

    def test_typed_goad_prohibition_is_controller_scoped_and_replays(self):
        session = self.make_combat_session(7011510)
        engine = session.engine
        self.add_typed_goad_prohibition(session)
        protected = self.attacker(engine, "Protected Attacker")

        result = engine.apply_effect(
            {"op": "goad", "card": protected.ref},
            actor="B",
        )

        self.assertEqual(protected.ref, result)
        self.assertEqual([], protected.goaded_by)
        self.assertEqual("permanent.goad.prevented", engine.state.events[-1].code)
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act(
            "pilot:A",
            {"a": "attack", "atk": {protected.ref: "B"}},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-goad-prohibition"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

        scope_session = self.make_combat_session(7011511)
        scope_engine = scope_session.engine
        source = self.add_typed_goad_prohibition(scope_session)
        other_controller = self.attacker(
            scope_engine,
            "Other Controller",
            controller="C",
        )
        scope_engine.apply_effect(
            {"op": "goad", "card": other_controller.ref},
            actor="B",
        )
        self.assertEqual(
            ["B"],
            [value.player for value in other_controller.goaded_by],
        )

        source.phased_out = True
        unprotected = self.attacker(scope_engine, "Phased Source")
        scope_engine.apply_effect(
            {"op": "goad", "card": unprotected.ref},
            actor="B",
        )
        self.assertEqual(
            ["B"],
            [value.player for value in unprotected.goaded_by],
        )

    def test_oracle_compiler_lowers_only_anchored_target_goad_templates(self):
        # Use a card in the compact CI fixture as the immutable record shell;
        # this test replaces its Oracle text and does not depend on its rules.
        base = self.db.lookup("Arcum Dagsson")
        ordinary = replace(base, oracle_text="{2}: Goad target creature.")
        opponent = replace(
            base,
            oracle_text=(
                "{2}: Goad target creature an opponent controls."
            ),
        )

        ordinary_program = generated_programs(self.db, ordinary)[0]
        opponent_program = generated_programs(self.db, opponent)[0]
        self.assertEqual(
            [{"op": "goad", "card": "$target.0"}],
            ordinary_program.effects,
        )
        self.assertNotIn(
            "controller_relation",
            ordinary_program.target_schema,
        )
        self.assertEqual(
            "opponent",
            opponent_program.target_schema["controller_relation"],
        )
        self.assertIn("goad", opponent_program.coverage)

        mutated = replace(
            base,
            oracle_text=(
                "{2}: Goad target creature, then draw a card."
            ),
        )
        ir = compile_oracle_card(mutated)
        self.assertTrue(ir.material_residuals)
        self.assertNotEqual("exact", ir.status)


if __name__ == "__main__":
    unittest.main()
