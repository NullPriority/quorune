from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session, pass_current
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.carddb import CardDatabase
from quorune.compiler.program_generation import register_generated_programs
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.engine import TURN_STEPS
from quorune.milling import MillRequest, commit_mill, mill_cards, prepare_mill
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.projection import StateProjector
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime import MillCardsIntent
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "mill.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "mill-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class FixedMillCompilerTests(unittest.TestCase):
    def test_event_bound_mill_domain_dependencies_and_omission_fail_closed(self):
        from quorune.rules.mill_capability_shapes import fixed_mill_node_capabilities
        base=self.db.lookup('Fixed Target Mill')
        text='Whenever this creature deals combat damage to a player, that player mills two cards.'
        card=replace(base,oracle_text=text,type_line='Creature — Human')
        self.assertEqual('exact',compile_oracle_card(card,capability_registry=self.capabilities).status)
        for cap in ('zone.mill.event_recipient','zone.mill.fixed'):
            value=json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
            next(row for row in value['capabilities'] if row['id']==cap).update(status='blocked',blockers=['event mill recipient withdrawn'])
            self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=CapabilityRegistry(value)).status)
        for altered in (
            'Whenever this creature deals damage, that player mills two cards.',
            'Whenever a creature attacks, defending player mills two cards.',
            text+' Return one of those cards to hand.',
        ):
            self.assertNotEqual('exact',compile_oracle_card(replace(card,oracle_text=altered),capability_registry=self.capabilities).status)
        effect={'op':'mill','player':'$context.target','count':2}
        self.assertFalse(fixed_mill_node_capabilities(effects=(effect,),target_schema=None,mechanic_ids=('mill',)))
        with patch('quorune.compiler.mill_templates.event_recipient_mill_result',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=self.capabilities).status)

    def test_event_bound_mill_recipients_retain_occurrence_without_targeting(self):
        base=self.db.lookup('Fixed Target Mill')
        cases=(
            ('Whenever this creature deals combat damage to a player, that player mills two cards.','$context.target',2),
            ('Whenever this creature deals combat damage to a player, that player mills that many cards.','$context.target',None),
            ('Whenever this creature attacks, defending player mills ten cards.','$context.defending_player',10),
            ('Whenever an opponent casts a spell, that player mills two cards.','$context.player',2),
        )
        for text,player,count in cases:
            ir=compile_oracle_card(replace(base,oracle_text=text,type_line='Creature — Human'),
                capability_registry=self.capabilities,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0]
            self.assertIsNone(node.target_schema);self.assertEqual(player,node.effects[0]['player'])
            self.assertIn('zone.mill.event_recipient',node.capability_dependencies)
            if count is not None:self.assertEqual(count,node.effects[0]['count'])
            else:self.assertEqual('committed_event_amount',node.effects[0]['count']['origin'])

    def test_opponent_mill_scope_dependency_and_compiler_omission_fail_closed(self):
        from quorune.compiler.mill_templates import fixed_mill_effect_template
        from quorune.card_programs import bind_card_program_runtime
        text='Each opponent mills two cards.'
        card=replace(self.db.lookup('Fixed Target Mill'),oracle_text=text)
        program=compile_best_available_card_program(self.db,card,semantic_registry=SemanticRegistry(),
            capability_registry=self.capabilities,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.capabilities,profile='commander_review')['strict_capability_ready'])
        value=json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
        next(row for row in value['capabilities'] if row['id']=='zone.mill.fixed').update(status='blocked',blockers=['opponent mill authority withdrawn'])
        self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=CapabilityRegistry(value)).status)
        with patch('quorune.oracle_ir.fixed_mill_effect_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=self.capabilities).status)
        for altered in ('Each opponent mills two card.','Each opponent mills a card, then draw that many cards.'):
            self.assertIsNone(fixed_mill_effect_template(altered))

    def test_opponent_mill_closed_handler_excludes_controller_departed_and_empty_roster(self):
        from quorune.semantic_runtime.generic import MillHandler
        from quorune.semantic_runtime.context import ReadOnlyHandlerContext,SemanticNodeError
        handler=MillHandler();effect={'op':'mill','scope':'controller_opponents','count':2}
        context=ReadOnlyHandlerContext.from_sequences(actor='A',default_reason='opponent scope contrast',
            seats=('A','B','C','D'),active_seats=('A','B','D'),apnap_order=('B','D','A'))
        plan=handler.lower(effect,context)
        self.assertEqual(('B','D'),plan.intents[0].batch_players)
        empty=ReadOnlyHandlerContext.from_sequences(actor='A',default_reason='empty roster',
            seats=('A','B'),active_seats=('A',),apnap_order=('A',))
        self.assertFalse(handler.lower(effect,empty).intents)
        for altered in ({**effect,'player':'A'},{**effect,'scope':'all_opponents'},{**effect,'count':True}):
            with self.assertRaises(SemanticNodeError):handler.lower(altered,context)

    def test_each_opponent_mill_has_closed_simultaneous_scope(self):
        base=self.db.lookup('Fixed Target Mill')
        text='Each opponent mills two cards.'
        ir=compile_oracle_card(replace(base,oracle_text=text),capability_registry=self.capabilities,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0]
        self.assertEqual({'op':'mill','scope':'controller_opponents','count':2},node.effects[0])
        self.assertIsNone(node.target_schema)
        self.assertIn('zone.mill.fixed',node.capability_dependencies)

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_fixed_mill_compiles_across_spell_trigger_and_activation(self):
        expected = {
            "Fixed Target Mill": ["mill-fixed-target-any-v1"],
            "Fixed Opponent Mill": ["mill-fixed-target-opponent-v1"],
            "Fixed Mill Adept": ["mill-fixed-target-any-v1"],
            "Fixed Mill Device": [
                "mill-fixed-controller-v1",
                "mill-fixed-target-any-v1",
            ],
        }
        for name, templates in expected.items():
            with self.subTest(card=name):
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
                    if node.template_id in templates
                ]
                self.assertEqual("exact", ir.status)
                self.assertEqual(templates, [node.template_id for node in nodes])
                for node in nodes:
                    self.assertTrue(node.exact)
                    self.assertIn("zone.mill.fixed", node.capability_dependencies)
                    self.assertEqual("mill", node.effects[0]["op"])
                    self.assertGreater(node.effects[0]["count"], 0)
                    span = record.oracle_text[node.span.start : node.span.end]
                    self.assertIn("mill", span.casefold())
                programs = [
                    program
                    for program in generated_programs(
                        self.db,
                        record,
                        trust_level="trusted",
                        capability_registry=self.capabilities,
                        capability_profile="commander_review",
                    )
                    if program.provenance.get("template_id") in templates
                ]
                self.assertEqual(len(templates), len(programs))
                self.assertTrue(
                    all(
                        program.capability_closure["trusted"]
                        for program in programs
                    )
                )

    def test_fixed_all_player_mill_uses_one_simultaneous_recipient_scope(self):
        # CR 701.17a-b mills the available top cards; CR 608.2f keeps this
        # multi-player instruction simultaneous rather than exposing partial
        # library moves to event observers between players.
        base = self.db.lookup("Fixed Target Mill")
        for text in (
            "Each player mills three cards.",
            "When this creature enters, each player mills three cards.",
            "{T}: Each player mills a card.",
        ):
            with self.subTest(text=text):
                card = replace(base, oracle_text=text,
                    type_line="Creature — Human" if text.startswith(("When", "{T}")) else "Sorcery")
                ir = compile_oracle_card(card, capability_registry=self.capabilities,
                    capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertIsNone(node.target_schema)
                self.assertEqual("mill", node.effects[0]["op"])
                self.assertEqual("all_active_players", node.effects[0]["scope"])
                self.assertNotIn("player", node.effects[0])
                self.assertIn("zone.mill.fixed", node.capability_dependencies)

    def test_dynamic_optional_group_and_linked_mill_remain_residual(self):
        base = self.db.lookup("Fixed Target Mill")
        variants = (
            "Mill X cards.",
            "Each player mills X cards.",
            "Target player mills half their library, rounded down.",
            "Target player mills two card.",
            "Mill a card, then draw cards equal to its mana value.",
            "Target player mills five cards, then returns one of them to hand.",
        )
        for index, text in enumerate(variants, start=1):
            with self.subTest(text=text):
                ir = compile_oracle_card(
                    replace(
                        base,
                        oracle_id=f"50000000-0000-4000-8000-{100 + index:012d}",
                        oracle_text=text,
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_all_player_mill_scope_shape_rejects_conflicting_or_variable_recipients(self):
        from quorune.rules.mill_capability_shapes import fixed_mill_node_capabilities

        valid = {"op":"mill", "scope":"all_active_players", "count":3}
        self.assertEqual(("zone.mill.fixed",), fixed_mill_node_capabilities(
            effects=(valid,), target_schema=None, mechanic_ids=("mill",)))
        for changed in (
            {"count":True}, {"count":0}, {"count":-1}, {"count":1.5},
            {"player":"$controller"}, {"scope":"all_players"}, {"optional":True},
        ):
            with self.subTest(changed=changed):
                self.assertEqual((), fixed_mill_node_capabilities(
                    effects=({**valid, **changed},), target_schema=None, mechanic_ids=("mill",)))
        self.assertEqual((), fixed_mill_node_capabilities(
            effects=(valid,), target_schema={"count":1}, mechanic_ids=("mill",)))

    def test_all_player_mill_missing_destination_and_trigger_authority_stays_residual(self):
        record = replace(self.db.lookup("Fixed Target Mill"),
                         oracle_text="Each player mills three cards.")
        for capability in ("zone.change.destination_replacement", "trigger.placement.apnap"):
            raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            next(row for row in raw["capabilities"] if row["id"] == capability).update(
                status="blocked", blockers=["all-player Mill authority omission"])
            with self.subTest(capability=capability):
                self.assertNotEqual("exact", compile_oracle_card(
                    record, capability_registry=CapabilityRegistry(raw),
                    capability_profile="commander_review").status)
        with patch("quorune.oracle_ir.fixed_mill_effect_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.capabilities,
                capability_profile="commander_review").status)

    def test_mill_dependency_and_compiler_mutations_fail_closed(self):
        record = self.db.lookup("Fixed Target Mill")
        for dependency_id in (
            "trigger.event.normalized_zone_change",
            "trigger.placement.apnap",
            "zone.change.destination_replacement",
        ):
            with self.subTest(dependency=dependency_id):
                value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
                dependency = next(
                    row
                    for row in value["capabilities"]
                    if row["id"] == dependency_id
                )
                dependency["status"] = "blocked"
                dependency["blockers"] = ["test mutation"]
                ir = compile_oracle_card(
                    record,
                    capability_registry=CapabilityRegistry(value),
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(
                    any(
                        dependency_id in blocker
                        for residual in ir.material_residuals
                        for blocker in residual.blockers
                    )
                )
        with patch("quorune.oracle_ir.fixed_mill_effect_template", return_value=None):
            ir = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertNotEqual("exact", ir.status)
        self.assertFalse(
            any(
                node.template_id and node.template_id.startswith("mill-fixed-")
                for face in ir.faces
                for node in face.nodes
            )
        )

    def test_typed_attachment_and_target_predicate_mill_pairs_compile_exactly(self):
        expected = {
            "Fixed Typed Mill Aura": {
                "attachment.aura.typed_restriction",
                "effect.choice.optional_fixed",
                "zone.mill.fixed",
            },
            "Fixed Mill Targeter": {
                "target.permanent.characteristic_predicate",
                "zone.mill.fixed",
            },
        }
        for name, dependencies in expected.items():
            with self.subTest(card=name):
                ir = compile_oracle_card(
                    self.db.lookup(name),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                observed = {
                    dependency
                    for face in ir.faces
                    for node in face.nodes
                    for dependency in node.capability_dependencies
                }
                self.assertTrue(dependencies <= observed, observed)

    def test_residual_mill_pairs_remain_fail_closed_at_cardprogram_boundary(self):
        base = self.db.lookup("Fixed Target Mill")
        variants = (
            (
                "unsupported-condition",
                "Target player mills five cards.\n"
                "If you control a creature of the chosen type, draw a card.",
            ),
            (
                "multiple-targets",
                "Target player mills five cards.\n"
                "Up to two target players each draw a card.",
            ),
            (
                "target-predicate",
                "Target player mills five cards.\n"
                "Return target creature card from a graveyard to its owner's hand.",
            ),
        )
        for index, (label, text) in enumerate(variants, start=1):
            with self.subTest(label=label):
                record = replace(
                    base,
                    oracle_id=(
                        f"50000000-0000-4000-8000-{200 + index:012d}"
                    ),
                    oracle_text=text,
                )
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertTrue(
                    any(
                        "zone.mill.fixed" in node.capability_dependencies
                        for face in ir.faces
                        for node in face.nodes
                    )
                )
                program = compile_best_available_card_program(
                    self.db,
                    record,
                    semantic_registry=SemanticRegistry(),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual(
                    "unresolved",
                    program.trust_closure["trust_basis"],
                )
                self.assertTrue(program.residuals)
                self.assertFalse(program.abilities)


    def test_promoted_mill_conditional_pair_is_capability_closed(self):
        record = replace(self.db.lookup("Fixed Target Mill"),
            oracle_id="fixture:promoted-mill-public-condition",
            oracle_text="Target player mills five cards.\nIf you control an artifact, draw a card.")
        ir = compile_oracle_card(record, capability_registry=self.capabilities,
                                 capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        program = compile_best_available_card_program(self.db, record,
            semantic_registry=SemanticRegistry(), capability_registry=self.capabilities,
            capability_profile="commander_review")
        self.assertEqual("capability_closed", program.trust_closure["trust_basis"])
        self.assertFalse(program.residuals)
        operations = [effect["op"] for ability in program.abilities for effect in ability.effects]
        self.assertEqual(["mill", "apply_if_public_condition"], operations)


class FixedMillRuntimeTests(unittest.TestCase):
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

    def session(self, seed: int, *, players: int = 4):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    def register(self, engine, *names: str) -> None:
        register_generated_programs(
            self.db,
            engine.semantics,
            tuple(self.db.lookup(name) for name in names),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )

    def add_card(self, engine, *, seat: str, name: str, ref: str, zone: str):
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=[seat],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    @staticmethod
    def isolate_library(engine, seat: str, count: int):
        library = engine.state.players[seat].zones["library"]
        keep = set(library[-count:])
        for object_id in list(library):
            if object_id not in keep:
                engine.move_card(object_id, "outside", log=False)
        return tuple(
            engine.state.cards[object_id]
            for object_id in reversed(
                engine.state.players[seat].zones["library"]
            )
        )

    def cast_target_mill(self, engine, *, target: str = "B"):
        self.register(engine, "Fixed Target Mill")
        spell = self.add_card(
            engine,
            seat="A",
            name="Fixed Target Mill",
            ref=f"fixed-mill-{engine.state.config.seed}",
            zone="hand",
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["U"] = 1
        engine._cast("A", {"card": spell.ref, "targets": [target]})
        return spell

    def add_exile_replacement(self, engine):
        name = "Mill Exile Replacement"
        self.register(engine, name)
        return self.add_card(
            engine,
            seat="A",
            name=name,
            ref=f"mill-replacement-{engine.state.config.seed}",
            zone="battlefield",
        )

    @staticmethod
    def resolve_top(engine):
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def test_mill_commits_top_first_and_as_many_as_possible(self):
        session = self.session(7011701)
        engine = session.engine
        top_first = self.isolate_library(engine, "B", 3)

        result = mill_cards(
            engine,
            MillRequest(
                actor="A",
                player="B",
                count=5,
                reason="fixed Mill owner test",
            ),
        )

        self.assertEqual(3, result.actual_count)
        self.assertEqual(
            tuple(card.ref for card in top_first),
            result.refs,
        )
        self.assertFalse(engine.state.players["B"].zones["library"])
        self.assertEqual(
            [card.object_id for card in top_first],
            engine.state.players["B"].zones["graveyard"][-3:],
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "card.mill"
        )
        self.assertEqual(3, event.details["count"])
        self.assertEqual(list(result.refs), event.details["objects"])

    def test_stale_plan_and_malformed_intent_fail_before_mutation(self):
        session = self.session(7011702)
        engine = session.engine
        self.isolate_library(engine, "B", 3)
        plan = prepare_mill(
            engine,
            MillRequest("A", "B", 2, "stale fixed Mill plan"),
        )
        top_id = engine.state.players["B"].zones["library"][-1]
        engine.move_card(top_id, "library", position="bottom", log=False)
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(GameRuleError, "library top changed"):
            commit_mill(engine, plan)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        for count in (True, -1, "2"):
            with self.subTest(count=count), self.assertRaises(ValueError):
                MillCardsIntent("A", "B", count, "malformed fixed Mill")
        self.assertEqual(0,MillCardsIntent("A","B",0,"zero Mill").count)
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_target_mill_revalidates_and_zone_replacement_preserves_actual_result(self):
        stale_session = self.session(7011703)
        stale_engine = stale_session.engine
        stale_top = self.isolate_library(stale_engine, "B", 2)
        self.cast_target_mill(stale_engine)
        stale_engine.state.players["B"].in_game = False
        stale_engine.permissions.invalidate_current()
        stale_engine.state.pending_decision = None
        stale_engine.state.priority_player = None
        stale_engine._prepare_stack_resolution()
        self.assertEqual(
            ["library", "library"],
            [card.zone for card in stale_top],
        )

        session = self.session(7011713)
        engine = session.engine
        top_first = self.isolate_library(engine, "B", 2)
        self.add_exile_replacement(engine)
        self.cast_target_mill(engine)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

        self.assertEqual(["exile", "exile"], [card.zone for card in top_first])
        self.assertTrue(all(card.counters["void"] == 1 for card in top_first))
        self.assertFalse(engine.state.players["B"].zones["graveyard"])
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "card.mill"
        )
        self.assertEqual([card.ref for card in top_first], event.details["objects"])

    def test_aura_attachment_and_triggered_mill_compose(self):
        session = self.session(7011705)
        engine = session.engine
        self.register(engine, "Fixed Mill Aura")
        aura = self.add_card(
            engine,
            seat="A",
            name="Fixed Mill Aura",
            ref="fixed-mill-aura",
            zone="hand",
        )
        target_ref = engine.create_token(
            "A",
            name="Fixed Mill Aura Target",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        target = next(
            card for card in engine.state.cards.values() if card.ref == target_ref
        )
        top_first = self.isolate_library(engine, "A", 2)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["U"] = 2
        engine._cast("A", {"card": aura.ref, "targets": [target.ref]})

        self.resolve_top(engine)
        self.assertEqual(target.object_id, aura.attached_to)
        self.assertIn(aura.object_id, target.attachments)
        self.assertTrue(engine.state.stack)
        self.resolve_top(engine)

        self.assertEqual(["graveyard", "graveyard"], [c.zone for c in top_first])
        self.assertEqual(target.object_id, aura.attached_to)

    def test_typed_aura_optional_triggered_mill_is_atomic_private_and_replays(self):
        session = self.session(7011715)
        engine = session.engine
        self.register(engine, "Fixed Typed Mill Aura")
        aura = self.add_card(
            engine,
            seat="A",
            name="Fixed Typed Mill Aura",
            ref="fixed-typed-mill-aura",
            zone="hand",
        )
        vehicle_ref = engine.create_token(
            "A",
            name="Fixed Mill Vehicle",
            characteristics={"type_line": "Token Artifact — Vehicle"},
        )[0]
        food_ref = engine.create_token(
            "A",
            name="Fixed Mill Food",
            characteristics={"type_line": "Token Artifact — Food"},
        )[0]
        vehicle = next(
            card for card in engine.state.cards.values() if card.ref == vehicle_ref
        )
        food = next(
            card for card in engine.state.cards.values() if card.ref == food_ref
        )
        top_first = self.isolate_library(engine, "A", 2)
        top_refs = tuple(card.ref for card in top_first)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["U"] = 2
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            with engine.transaction():
                engine._cast("A", {"card": aura.ref, "targets": [food.ref]})
        self.assertEqual(before, authoritative_state_hash(engine.state))
        aura = next(
            card
            for card in engine.state.cards.values()
            if card.ref == "fixed-typed-mill-aura"
        )
        vehicle = next(
            card for card in engine.state.cards.values() if card.ref == vehicle_ref
        )

        engine._cast("A", {"card": aura.ref, "targets": [vehicle.ref]})
        self.resolve_top(engine)
        self.assertEqual(vehicle.object_id, aura.attached_to)
        self.assertIn(aura.object_id, vehicle.attachments)
        self.assertTrue(engine.state.stack)
        self.resolve_top(engine)

        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        self.assertEqual(["library", "library"], [card.zone for card in top_first])
        projector = StateProjector(self.db, engine.state)
        self.assertIsNotNone(projector._decision("pilot:A"))
        for seat in ("B", "C", "D"):
            with self.subTest(seat=seat):
                self.assertIsNone(projector._decision(f"pilot:{seat}"))
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choice": "apply",
                "reason": "Apply the represented optional Mill effect.",
            },
        )

        self.assertTrue(result.ok, result.summary)
        resolved_top = [
            next(
                card
                for card in engine.state.cards.values()
                if card.ref == card_ref
            )
            for card_ref in top_refs
        ]
        self.assertEqual(
            ["graveyard", "graveyard"],
            [card.zone for card in resolved_top],
        )
        aura = next(
            card
            for card in engine.state.cards.values()
            if card.ref == "fixed-typed-mill-aura"
        )
        vehicle = next(
            card for card in engine.state.cards.values() if card.ref == vehicle_ref
        )
        self.assertEqual(vehicle.object_id, aura.attached_to)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "typed-aura-optional-mill"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_characteristic_target_and_triggered_mill_compose_on_one_object(self):
        session = self.session(7011716)
        engine = session.engine
        self.register(engine, "Fixed Mill Targeter")
        source = self.add_card(
            engine,
            seat="A",
            name="Fixed Mill Targeter",
            ref="fixed-mill-targeter",
            zone="hand",
        )
        legal_ref = engine.create_token(
            "A",
            name="Fixed Mill Target Artifact",
            characteristics={"type_line": "Token Artifact"},
        )[0]
        illegal_ref = engine.create_token(
            "A",
            name="Fixed Mill Target Enchantment",
            characteristics={"type_line": "Token Enchantment"},
        )[0]
        legal = next(
            card for card in engine.state.cards.values() if card.ref == legal_ref
        )
        illegal = next(
            card for card in engine.state.cards.values() if card.ref == illegal_ref
        )
        milled = self.isolate_library(engine, "A", 1)[0]
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].mana_pool["U"] = 3
        engine._cast("A", {"card": source.ref})
        self.resolve_top(engine)
        self.assertTrue(engine.state.stack)
        self.resolve_top(engine)
        self.assertEqual("graveyard", milled.zone)

        source = next(
            card
            for card in engine.state.cards.values()
            if card.ref == "fixed-mill-targeter"
        )
        engine.state.priority_player = "A"
        engine.state.players["A"].turns_begun = 1
        source.acquired_control_turn_count = 0
        ability = engine._activated_abilities(source)[0]
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            with engine.transaction():
                engine._activate(
                    "A",
                    {
                        "source": source.ref,
                        "ability": ability.ability_id,
                        "targets": [illegal.ref],
                    },
                )
        self.assertEqual(before, authoritative_state_hash(engine.state))
        source = next(
            card
            for card in engine.state.cards.values()
            if card.ref == "fixed-mill-targeter"
        )
        legal = next(
            card for card in engine.state.cards.values() if card.ref == legal_ref
        )
        illegal = next(
            card for card in engine.state.cards.values() if card.ref == illegal_ref
        )
        ability = engine._activated_abilities(source)[0]

        engine._activate(
            "A",
            {
                "source": source.ref,
                "ability": ability.ability_id,
                "targets": [legal.ref],
            },
        )
        self.resolve_top(engine)

        self.assertTrue(source.tapped)
        self.assertTrue(legal.tapped)
        self.assertFalse(illegal.tapped)

    def test_hexproof_target_legality_and_activated_mill_compose(self):
        session = self.session(7011706)
        engine = session.engine
        self.register(engine, "Fixed Mill Hexproofer")
        source = self.add_card(
            engine,
            seat="A",
            name="Fixed Mill Hexproofer",
            ref="fixed-mill-hexproofer",
            zone="battlefield",
        )
        opposing_ref = engine.create_token(
            "B",
            name="Opposing Target Source",
            characteristics={"type_line": "Token Artifact"},
        )[0]
        schema = engine._public_target_schema(
            "B",
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "count": 1,
            },
            source_ref=opposing_ref,
        )
        self.assertIsNotNone(schema)
        self.assertNotIn(source.ref, schema["legal_refs"])
        top_first = self.isolate_library(engine, "B", 2)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].turns_begun = 1
        source.acquired_control_turn_count = 0
        ability = engine._activated_abilities(source)[0]
        engine._activate(
            "A",
            {
                "source": source.ref,
                "ability": ability.ability_id,
                "targets": ["B"],
            },
        )

        self.resolve_top(engine)

        self.assertEqual(["graveyard", "graveyard"], [c.zone for c in top_first])
        self.assertTrue(source.tapped)

    def test_four_player_target_mill_is_public_privacy_safe_and_replays(self):
        session = self.session(7011704)
        engine = session.engine
        top_first = self.isolate_library(engine, "B", 2)
        self.add_exile_replacement(engine)
        self.cast_target_mill(engine)
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        while any(item.kind == "spell" for item in engine.state.stack):
            pass_current(session)

        self.assertEqual(["exile", "exile"], [card.zone for card in top_first])
        for seat in engine.seats:
            snapshot = StateProjector(self.db, engine.state)._snapshot(
                f"pilot:{seat}"
            )
            serialized = json.dumps(snapshot, sort_keys=True)
            self.assertNotIn(top_first[0].object_id, serialized)
            self.assertNotIn(top_first[1].object_id, serialized)
            self.assertIn(top_first[0].ref, serialized)
            self.assertIn(top_first[1].ref, serialized)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-mill-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


class AllPlayerMillRuntimeTests(unittest.TestCase):
    """Original offered actions exercise CR 701.17 and 608.2f together."""

    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry

        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "all-player-mill.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/mill-cards.json",
            ROOT / "tests/fixtures/each-player-mill-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("All-player Mill", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 10),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _bound
    session = _bound.BoundEffectProgramRuntimeTests.session
    add = _bound.BoundEffectProgramRuntimeTests.add
    ready = _bound.BoundEffectProgramRuntimeTests.ready
    checkpoint = _bound.BoundEffectProgramRuntimeTests.checkpoint
    resolve = _bound.BoundEffectProgramRuntimeTests.resolve
    replay = _bound.BoundEffectProgramRuntimeTests.replay

    def assert_admission(self, name):
        from quorune.card_programs import bind_card_program_runtime

        program = compile_best_available_card_program(
            self.db, self.db.lookup(name), semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile="commander_review")
        self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
            profile="commander_review")["strict_capability_ready"])

    def test_malformed_all_player_mill_and_inactive_batch_fail_atomically(self):
        from quorune.milling import mill_player_batch

        session = self.session(seed=7011710)
        engine = session.engine
        before = authoritative_state_hash(session.state)
        for changed in (
            {"count":True}, {"count":-1}, {"count":1.5},
            {"player":"B"}, {"scope":"all_players"}, {"optional":True},
        ):
            with self.subTest(changed=changed), self.assertRaises(GameRuleError):
                engine.apply_effect({"op":"mill", "scope":"all_active_players", "count":1, **changed}, actor="A")
            self.assertEqual(before, authoritative_state_hash(session.state))
        for players in (("A","A"), ("A","missing")):
            with self.subTest(players=players), self.assertRaises(GameRuleError):
                mill_player_batch(engine, tuple(
                    MillRequest("A", player, 1, "batch validation") for player in players))
            self.assertEqual(before, authoritative_state_hash(session.state))
        for players in (("A","A"), ("A",True)):
            with self.assertRaises(ValueError):
                MillCardsIntent("A","A",1,"typed batch",batch_players=players)
        result = engine.apply_effect({"op":"mill", "scope":"all_active_players", "count":0}, actor="A")
        self.assertEqual((), result)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_original_bell_mills_all_active_players_in_one_zone_batch(self):
        self.assert_admission("Ghoulcaller's Bell")
        session = self.session(seed=7011711)
        engine = session.engine
        source = self.add(engine, "Ghoulcaller's Bell")
        expected = {seat:engine.state.cards[engine.state.players[seat].zones["library"][-1]]
                    for seat in engine.active_seats}
        engine.state.phase_index = TURN_STEPS.index(("precombat_main", "main"))
        action = self.ready(session, source, {})
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:B", {"action_id":action["id"]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        source = engine.state.cards[source.object_id]
        expected = {seat:engine.state.cards[card.object_id] for seat,card in expected.items()}
        accepted = session.act("pilot:A", {"action_id":action["id"]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(source.tapped)
        with tempfile.TemporaryDirectory() as directory:
            session.save(Path(directory) / "pending")
            from quorune.session import CommanderSession
            loaded = CommanderSession.load(self.db, Path(directory) / "pending")
            self.assertEqual(authoritative_state_hash(session.state), authoritative_state_hash(loaded.state))
        commits = []
        from quorune.zone_transitions import ZoneTransitionOwner
        original = ZoneTransitionOwner.move_cards_simultaneously
        def observe(owner, changes, **kwargs):
            if changes and all(owner.state.cards[oid].zone == "library" for oid, destination in changes):
                commits.append(tuple(changes))
            return original(owner, changes, **kwargs)
        with patch("quorune.zone_transitions.ZoneTransitionOwner.move_cards_simultaneously", observe):
            self.resolve(session)
        self.assertEqual(1, len(commits), "all-player Mill lost simultaneous commit")
        self.assertEqual({card.object_id for card in expected.values()}, {oid for oid,destination in commits[0]})
        self.assertEqual(["graveyard"] * 4, [card.zone for card in expected.values()])
        for seat in engine.seats:
            projected = json.dumps(session.packet("pilot:"+seat, full=True), sort_keys=True)
            for card in expected.values():
                self.assertNotIn(card.object_id, projected)
                self.assertIn(card.ref, projected)
        self.replay(session)

    def test_sequential_player_mill_mutant_is_killed_by_original_bell(self):
        def sequential(host, requests):
            return tuple(mill_cards(host, request) for request in requests)

        with patch("quorune.milling.mill_player_batch", sequential):
            with self.assertRaisesRegex(AssertionError, "all-player Mill lost simultaneous commit"):
                self.test_original_bell_mills_all_active_players_in_one_zone_batch()

    def test_original_chill_mills_available_cards_and_preserves_replacement_destinations(self):
        self.assert_admission("Chill of Foreboding")
        session = self.session(seed=7011712)
        engine = session.engine
        source = self.add(engine, "Chill of Foreboding", zone="hand")
        self.add(engine, "Mill Exile Replacement")
        expected = {seat:tuple(engine.state.cards[oid] for oid in reversed(engine.state.players[seat].zones["library"]))
                    for seat in engine.active_seats}
        self.assertTrue(all(len(cards) < 5 for cards in expected.values()))
        engine.state.phase_index = TURN_STEPS.index(("precombat_main", "main"))
        action = self.ready(session, source, {"U":1,"C":2})
        self.checkpoint(session)
        accepted = session.act("pilot:A", {"action_id":action["id"]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        for seat,cards in expected.items():
            self.assertEqual([], engine.state.players[seat].zones["library"])
            self.assertTrue(engine.state.players[seat].in_game)
            self.assertEqual(["graveyard" if seat == "A" else "exile"] * len(cards),
                             [card.zone for card in cards])
        self.replay(session)


if __name__ == "__main__":
    unittest.main()
