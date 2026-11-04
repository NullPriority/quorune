from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.counter_removal_templates import (
    AllCounterRemovalTemplate,
    all_counter_removal_effect_template,
    FixedCounterRemovalTemplate,
    fixed_counter_removal_effect_template,
    fixed_source_counter_removal_effect_template,
)
from quorune.compiler.unlock_frontier import _clause_families
from quorune.counter_removal import (
    AllCounterRemovalResult,
    CounterRemovalError,
    CounterRemovalResult,
)
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantic_runtime import (
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
    RemoveAllCountersIntent,
    RemoveCountersIntent,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.counter_removal_handlers import (
    AllCounterRemovalHandler,
    FixedCounterRemovalHandler,
)
from quorune.semantic_runtime.executor import execute_intent_plan
from quorune.semantics import SemanticProgram
from scripts.build_test_database import build_fixture_database


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-counter-removal.sqlite3"
    build_fixture_database(
        [ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json"],
        database,
    )
    return CardDatabase(database)


def source_counter_removal_record(text: str) -> CardRecord:
    return CardRecord(
        oracle_id='fixture:source-counter-removal', name='Generic Removal Observer',
        mana_cost='{2}{U}', mana_value=3, type_line='Creature — Elemental',
        oracle_text=text, power='6', toughness='6', loyalty=None, defense=None,
        colors=('U',), color_identity=('U',), keywords=(), produced_mana=(),
        layout='normal', released_at='2026-01-01', legalities={'commander': 'legal'},
        faces=(), raw={},
    )


class FixedCounterRemovalCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.base = cls.db.lookup("Sol Ring")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, text: str, *, type_line: str = "Sorcery"):
        return compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=text,
                type_line=type_line,
                keywords=(),
                faces=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_mandatory_source_counter_removal_keeps_incarnation_without_targeting(self):
        for text in (
            'Whenever you cast a blue spell, remove a -1/-1 counter from this creature.',
            'Whenever an artifact enters, remove a +1/+1 counter from this creature.',
            'At the beginning of your upkeep, remove an intervention counter from this enchantment.',
            'Whenever this creature attacks, remove two charge counters from it.',
        ):
            with self.subTest(text=text):
                record = source_counter_removal_record(text)
                ir = compile_oracle_card(record, capability_registry=self.capabilities,
                                         capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertEqual('triggered_ability', node.kind)
                self.assertIsNone(node.target_schema)
                effect, = node.effects
                self.assertEqual('remove_counters', effect['op'])
                self.assertEqual('$source.zone_object', effect['card'])
                self.assertEqual('$source', effect['source'])
                self.assertNotIn('target.revalidate_resolution', node.capability_dependencies)

    def test_all_named_source_counter_removal_is_distinct_from_fixed_and_all_kinds(self):
        texts = (
            'Whenever this creature deals combat damage to a player, remove all -1/-1 counters from it.',
            'At the beginning of your end step, remove all +1/+1 counters from this creature.',
        )
        for text in texts:
            with self.subTest(text=text):
                ir = compile_oracle_card(source_counter_removal_record(text),
                    capability_registry=self.capabilities, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                self.assertIsNone(node.target_schema)
                effect, = node.effects
                self.assertEqual('remove_all_counters', effect['op'])
                self.assertEqual(2, effect['schema_version'])
                self.assertEqual('$source.zone_object', effect['card'])
                self.assertIn(effect['counter'], {'-1/-1', '+1/+1'})
                self.assertNotIn('amount', effect)
                self.assertEqual('$source', effect['source'])

    def test_all_named_source_shape_and_dependency_withdrawal_fail_closed(self):
        from quorune.rules.counter_removal_capabilities import all_counter_removal_node_capabilities
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        effect = {'op': 'remove_all_counters', 'schema_version': 2, 'counter': '-1/-1',
                  'card': '$source.zone_object', 'source': '$source'}
        expected = ('counter.removal.all_effect', 'counter.removal.all_named_source_effect')
        self.assertEqual(expected, all_counter_removal_node_capabilities(
            effects=(effect,), target_schema=None, mechanic_ids=('cr-122-counters',)))
        for change in ({'schema_version': True}, {'schema_version': 1}, {'counter': ''},
                       {'counter': 'Charge'}, {'counter': False}, {'card': '$source'},
                       {'card': '$target.0'}, {'amount': 1000}, {'unknown': True}):
            with self.subTest(change=change):
                self.assertEqual((), all_counter_removal_node_capabilities(
                    effects=({**effect, **change},), target_schema=None, mechanic_ids=('cr-122-counters',)))
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id'] == 'counter.removal.all_named_source_effect').update(
            status='blocked', blockers=['Named-kind selector authority withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        ir = compile_oracle_card(source_counter_removal_record(
            'At the beginning of your end step, remove all +1/+1 counters from this creature.'),
            capability_registry=registry, capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)
        self.assertTrue(any('counter.removal.all_named_source_effect' in str(row.blockers) for row in ir.material_residuals))

    def test_source_counter_removal_leaf_and_shape_reject_ambiguous_references(self):
        from quorune.rules.counter_removal_capabilities import fixed_counter_removal_node_capabilities
        compiled = fixed_source_counter_removal_effect_template(
            'Remove two charge counters from this artifact.', card_name='Generic Observer')
        self.assertIsNotNone(compiled)
        _, effects, schema, mechanics = compiled
        self.assertEqual(('counter.removal.fixed_effect', 'counter.removal.fixed_source_effect'),
                         fixed_counter_removal_node_capabilities(
                             effects=effects, target_schema=schema, mechanic_ids=mechanics))
        for text in ('Remove a counter from this artifact.',
                     'Remove all charge counters from this artifact.',
                     'Remove X charge counters from this artifact.',
                     'Remove a charge counter from that artifact.',
                     'Remove a charge counter from another artifact.',
                     'Remove a charge counter from this artifact. Draw a card.',
                     'Remove a charge counter from it.'):
            with self.subTest(text=text):
                self.assertIsNone(fixed_source_counter_removal_effect_template(
                    text, card_name='Generic Observer'))
        self.assertIsNotNone(fixed_source_counter_removal_effect_template(
            'Remove a charge counter from it.', card_name='Generic Observer',
            allow_source_pronoun=True))
        for change in ({'card': '$source'}, {'card': '$event.counter_object'},
                       {'amount': True}, {'amount': 0}, {'counter': ''},
                       {'source': '$controller'}, {'unknown': True}):
            with self.subTest(change=change):
                self.assertEqual((), fixed_counter_removal_node_capabilities(
                    effects=({**effects[0], **change},), target_schema=None,
                    mechanic_ids=mechanics))
        self.assertEqual((), fixed_counter_removal_node_capabilities(
            effects=effects, target_schema={'count': 1}, mechanic_ids=mechanics))
        for text in (
            'Whenever another creature enters, remove a charge counter from it.',
            'Whenever you cast a blue spell, you may remove a charge counter from this creature.',
            'Whenever you cast a blue spell, you may pay {1}. If you do, remove a charge counter from this creature.',
            'Whenever this creature attacks, remove all charge counters from it.',
            'Whenever this creature attacks, remove X charge counters from it.',
        ):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_oracle_card(source_counter_removal_record(text),
                    capability_registry=self.capabilities).status)

    def test_source_counter_removal_parser_and_dependency_mutations_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        text = 'Whenever you cast a blue spell, remove a -1/-1 counter from this creature.'
        with patch('quorune.compiler.counter_removal_templates.fixed_source_counter_removal_effect_template', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(source_counter_removal_record(text),
                capability_registry=self.capabilities).status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id'] == 'counter.removal.fixed_source_effect').update(
            status='blocked', blockers=['Source-incarnation authority withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        ir = compile_oracle_card(source_counter_removal_record(text),
            capability_registry=registry, capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status)
        self.assertTrue(any('counter.removal.fixed_source_effect' in str(row.blockers) for row in ir.material_residuals))

    def test_fixed_counter_removal_compiles_across_spell_trigger_and_activation(
        self,
    ):
        contexts = (
            (
                "Remove a -1/-1 counter from target creature.",
                "Sorcery",
                "spell_ability",
                1,
                "-1/-1",
            ),
            (
                "When this creature enters, remove a charge counter from target artifact.",
                "Creature — Human",
                "triggered_ability",
                1,
                "charge",
            ),
            (
                "{T}: Remove two charge counters from target artifact you control.",
                "Artifact",
                "activated_ability",
                2,
                "charge",
            ),
        )
        for text, type_line, kind, amount, counter_name in contexts:
            with self.subTest(kind=kind):
                ir = self.compile(text, type_line=type_line)
                node = next(
                    value
                    for value in ir.faces[0].nodes
                    if value.template_id
                    and value.template_id.startswith("remove-fixed-counter-")
                )
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertEqual(
                    {
                        "op": "remove_counters",
                        "card": "$target.0",
                        "counter": counter_name,
                        "amount": amount,
                        "source": "$source",
                    },
                    node.effects[0],
                )
                self.assertTrue(
                    {
                        "counter.removal.fixed_effect",
                        "target.revalidate_resolution",
                    }.issubset(node.capability_dependencies)
                )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_closed_counter_removal_target_grammar_is_shared(self):
        expected = (
            (
                "Remove one charge counter from target artifact you control.",
                {"types_any": ["artifact"], "controller_relation": "you"},
            ),
            (
                "Remove two -1/-1 counters from another target creature an opponent controls.",
                {
                    "types_any": ["creature"],
                    "controller_relation": "opponent",
                    "source_exclusion": True,
                },
            ),
            (
                "Remove a time counter from target Fungus.",
                {"subtypes_any": ["fungus"]},
            ),
        )
        for text, fields in expected:
            with self.subTest(text=text):
                template = fixed_counter_removal_effect_template(text)
                self.assertIsNotNone(template)
                assert template is not None
                for field, value in fields.items():
                    self.assertEqual(value, template.target_schema[field])

    def test_unsupported_fixed_counter_removal_variants_remain_residual(self):
        texts = (
            "Remove a counter from target creature.",
            "Remove up to one +1/+1 counter from target creature.",
            "Remove all charge counters from target artifact.",
            "Remove X charge counters from target artifact.",
            "Remove a poison counter from target player.",
            "Remove a +1/+1 counter from this creature.",
            "Move a +1/+1 counter from target creature.",
        )
        for text in texts:
            with self.subTest(text=text):
                self.assertIsNone(fixed_counter_removal_effect_template(text))
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_counter_removal_shape_fails_closed(self):
        template = fixed_counter_removal_effect_template(
            "Remove two charge counters from target artifact you control."
        )
        self.assertIsInstance(template, FixedCounterRemovalTemplate)
        assert template is not None
        expected = {
            "counter.removal.fixed_effect",
            "target.revalidate_resolution",
        }
        self.assertEqual(
            expected,
            set(
                capability_dependencies_for_node(
                    effects=template.effects,
                    target_schema=template.target_schema,
                    mechanic_ids=template.mechanics,
                )
            ),
        )
        for effects in (
            ({**template.effects[0], "amount": True},),
            ({**template.effects[0], "amount": 0},),
            ({**template.effects[0], "card": "$target.1"},),
            ({**template.effects[0], "counter": ""},),
            ({**template.effects[0], "source": "$controller"},),
            ({**template.effects[0], "extra": True},),
        ):
            with self.subTest(effects=effects):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=template.target_schema,
                        mechanic_ids=template.mechanics,
                    )
                )

    def test_fixed_counter_removal_compiler_mutant_is_killed(self):
        def exact() -> None:
            self.assertEqual(
                "exact",
                self.compile(
                    "Remove a -1/-1 counter from target creature."
                ).status,
            )

        exact()
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "fixed_counter_removal_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact()

    def test_all_counter_removal_compiles_and_promotes_complete_fixture(self):
        text = (
            "First strike\n"
            "Sacrifice this creature: Remove all counters from target permanent."
        )
        ir = self.compile(text, type_line="Creature — Vampire Shaman")
        node = next(
            value
            for value in ir.faces[0].nodes
            if value.template_id == "remove-all-counters-permanent-v1"
        )
        self.assertEqual("exact", ir.status)
        self.assertTrue(node.exact)
        self.assertEqual("activated_ability", node.kind)
        self.assertEqual(
            {
                "op": "remove_all_counters",
                "card": "$target.0",
                "source": "$source",
            },
            node.effects[0],
        )
        self.assertTrue(
            {
                "counter.removal.all_effect",
                "target.revalidate_resolution",
            }.issubset(node.capability_dependencies)
        )

    def test_all_counter_removal_shape_and_compiler_mutant_fail_closed(self):
        template = all_counter_removal_effect_template(
            "Remove all counters from target permanent."
        )
        self.assertIsInstance(template, AllCounterRemovalTemplate)
        assert template is not None
        expected = {
            "counter.removal.all_effect",
            "target.revalidate_resolution",
        }
        self.assertEqual(
            expected,
            set(
                capability_dependencies_for_node(
                    effects=template.effects,
                    target_schema=template.target_schema,
                    mechanic_ids=template.mechanics,
                )
            ),
        )
        for effects in (
            ({**template.effects[0], "card": "$target.1"},),
            ({**template.effects[0], "source": "$controller"},),
            ({**template.effects[0], "extra": True},),
        ):
            with self.subTest(effects=effects):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=template.target_schema,
                        mechanic_ids=template.mechanics,
                    )
                )

        def exact() -> None:
            self.assertEqual(
                "exact",
                self.compile(
                    "Sacrifice this creature: Remove all counters from target permanent.",
                    type_line="Creature — Vampire Shaman",
                ).status,
            )

        exact()
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "all_counter_removal_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact()

    def test_unsupported_all_counter_removal_variants_remain_residual(self):
        composed = (
            "Remove all counters from target permanent, then draw a card."
        )
        self.assertIsNone(all_counter_removal_effect_template(composed))
        texts = (
            "Remove all charge counters from target artifact.",
            "Remove all counters from target player.",
            "Remove all counters from this creature.",
            "Remove all counters from each creature.",
            "Remove all counters from up to one target permanent.",
        )
        for text in texts:
            with self.subTest(text=text):
                self.assertIsNone(all_counter_removal_effect_template(text))
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_unlock_frontier_groups_counter_removal_by_reusable_family(self):
        counter = _clause_families(
            "Remove a charge counter from target artifact.",
            kind="effect_clause",
            reason="unparsed",
        )
        unrelated = _clause_families(
            "Remove target creature from combat.",
            kind="effect_clause",
            reason="unparsed",
        )
        self.assertIn("effect_clause:remove-counter", counter)
        self.assertNotIn("effect_clause:remove-counter", unrelated)


class FixedCounterRemovalRuntimeTests(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
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
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_permanent(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
    ) -> CardInstance:
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["battlefield"].append(card.object_id)
        return card

    @staticmethod
    def context(*, players: int = 4) -> ReadOnlyHandlerContext:
        seats = tuple(chr(ord("A") + index) for index in range(players))
        return ReadOnlyHandlerContext(
            actor="A",
            default_reason="Fixed counter removal fixture",
            query=ReadOnlyRulesQuery(
                seats=seats,
                active_seats=seats,
                apnap_order=seats,
            ),
        )

    @staticmethod
    def effect(target: str, *, amount: int = 2) -> dict[str, object]:
        return {
            "op": "remove_counters",
            "card": target,
            "counter": "charge",
            "amount": amount,
            "source": "fixture-source",
        }

    def test_fixed_counter_removal_does_as_much_as_possible(self):
        session = self.session(12270201)
        target = self.add_permanent(
            session.engine,
            seat="B",
            name="Island",
            ref="partial-removal-target",
        )
        target.counters["charge"] = 1
        plan = FixedCounterRemovalHandler().lower(
            self.effect(target.ref, amount=3),
            self.context(players=2),
        )
        self.assertEqual(
            (
                RemoveCountersIntent(
                    actor="A",
                    object_ref=target.ref,
                    counter_name="charge",
                    amount=3,
                    reason="Fixed counter removal fixture",
                    source_ref="fixture-source",
                ),
            ),
            plan.intents,
        )

        result = execute_intent_plan(session.engine, plan)

        self.assertEqual((3, 1, 1, 0), (
            result.requested,
            result.removed,
            result.before,
            result.after,
        ))
        self.assertNotIn("charge", target.counters)
        zero = execute_intent_plan(session.engine, plan)
        self.assertEqual((3, 0, 0, 0), (
            zero.requested,
            zero.removed,
            zero.before,
            zero.after,
        ))

    def test_fixed_counter_removal_handler_rejects_malformed_effects(self):
        valid = self.effect("target")
        for effect in (
            {**valid, "amount": True},
            {**valid, "amount": 0},
            {**valid, "counter": ""},
            {**valid, "source": None},
            {**valid, "unknown": 1},
            {**valid, "_replacement_selections": ["decline:fixture"]},
        ):
            with self.subTest(effect=effect):
                with self.assertRaises(SemanticNodeError):
                    FixedCounterRemovalHandler().lower(
                        effect,
                        self.context(players=2),
                    )

    def test_unavailable_source_counter_removal_is_validated_before_noop(self):
        valid = self.effect(None)
        plan = FixedCounterRemovalHandler().lower(valid, self.context(players=2))
        self.assertEqual((), plan.intents)
        self.assertEqual('by_player', plan.result_shape)
        for change in ({'amount': True}, {'amount': 0}, {'counter': ''},
                       {'source': None}, {'unknown': 1}, {'op': 'remove_all_counters'}):
            with self.subTest(change=change):
                with self.assertRaises(SemanticNodeError):
                    FixedCounterRemovalHandler().lower({**valid, **change}, self.context(players=2))

    def test_source_counter_removal_resolution_keeps_control_and_blink_boundaries(self):
        from quorune.semantic_runtime.values import resolve_semantic_value
        session = self.session(12270207)
        engine = session.engine
        source = self.add_permanent(engine, seat='A', name='Sol Ring', ref='removal-source')
        peer = self.add_permanent(engine, seat='A', name='Sol Ring', ref='removal-source-peer')
        source.counters.update({'charge': 1, 'study': 2})
        peer.counters['charge'] = 3
        original_identity = source.logical_object_id
        item = StackItem(stack_id='source-removal-unit', ref='S-source-removal-unit',
                         kind='triggered_ability', controller='A', label='Source removal unit',
                         source_object_id=source.object_id,
                         context={'source_logical_object_id': original_identity})
        descriptor = {'op': 'remove_counters', 'card': '$source.zone_object',
                      'counter': 'charge', 'amount': 2, 'source': '$source'}
        engine.change_control(source.object_id, 'B', reason='source removal control boundary')
        resolved = resolve_semantic_value(engine, descriptor, item)
        plan = FixedCounterRemovalHandler().lower(resolved, self.context(players=2))
        result = execute_intent_plan(engine, plan)
        self.assertEqual((2, 1), (result.requested, result.removed))
        self.assertEqual({'study': 2}, source.counters)
        self.assertEqual({'charge': 3}, peer.counters)
        self.assertEqual('B', source.controller)
        self.assertEqual('A', plan.intents[0].actor)
        engine.move_card(source.object_id, 'exile', reason='source removal departure boundary')
        engine.move_card(source.object_id, 'battlefield', controller='A',
                         reason='source removal new incarnation boundary')
        self.assertNotEqual(original_identity, source.logical_object_id)
        source.counters['charge'] = 4
        before = authoritative_state_hash(engine.state)
        resolved = resolve_semantic_value(engine, descriptor, item)
        self.assertIsNone(resolved['card'])
        self.assertEqual({}, execute_intent_plan(engine,
            FixedCounterRemovalHandler().lower(resolved, self.context(players=2))))
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual({'charge': 4}, source.counters)
        self.assertEqual({'charge': 3}, peer.counters)

    def test_all_counter_removal_commits_every_kind_and_handles_empty_target(
        self,
    ):
        session = self.session(12270205)
        target = self.add_permanent(
            session.engine,
            seat="B",
            name="Island",
            ref="all-counter-removal-target",
        )
        target.counters.update({"charge": 2, "+1/+1": 1})
        plan = AllCounterRemovalHandler().lower(
            {
                "op": "remove_all_counters",
                "card": target.ref,
                "source": "fixture-source",
            },
            self.context(players=2),
        )
        self.assertEqual(
            (
                RemoveAllCountersIntent(
                    actor="A",
                    object_ref=target.ref,
                    reason="Fixed counter removal fixture",
                    source_ref="fixture-source",
                ),
            ),
            plan.intents,
        )
        result = execute_intent_plan(session.engine, plan)
        self.assertEqual((("+1/+1", 1), ("charge", 2)), result.removed)
        self.assertEqual(3, result.total_removed)
        self.assertEqual({}, target.counters)
        empty = execute_intent_plan(session.engine, plan)
        self.assertEqual((), empty.removed)
        self.assertEqual(0, empty.total_removed)

    def test_all_counter_removal_handler_rejects_malformed_effects(self):
        valid = {
            "op": "remove_all_counters",
            "card": "target",
            "source": "fixture-source",
        }
        for effect in (
            {**valid, "card": ""},
            {**valid, "source": None},
            {**valid, "counter": "charge"},
            {**valid, "unknown": True},
            {**valid, "_replacement_selections": ["decline:fixture"]},
        ):
            with self.subTest(effect=effect):
                with self.assertRaises(SemanticNodeError):
                    AllCounterRemovalHandler().lower(
                        effect,
                        self.context(players=2),
                    )

    def test_named_kind_removal_preserves_other_counters_zero_and_stale_plan_boundaries(self):
        from quorune.counter_removal import plan_all_counter_removal_effect, commit_all_counter_removal_effect
        session = self.session(12270401)
        engine = session.engine
        source = self.add_permanent(engine, seat='A', name='Sol Ring', ref='named-removal-source')
        source.counters.update({'charge': 3, 'study': 2})
        descriptor = {'op': 'remove_all_counters', 'schema_version': 2,
                      'card': source.ref, 'counter': 'charge', 'source': source.ref}
        lowered = AllCounterRemovalHandler().lower(descriptor, self.context(players=2))
        self.assertEqual('charge', lowered.intents[0].counter_name)
        result = execute_intent_plan(engine, lowered)
        self.assertEqual((('charge', 3),), result.removed)
        self.assertEqual({'study': 2}, source.counters)
        self.assertEqual((), execute_intent_plan(engine, lowered).removed)
        self.assertEqual({'study': 2}, source.counters)
        source.counters['charge'] = 2
        plan = plan_all_counter_removal_effect(engine, object_id=source.object_id,
            expected_logical_object_id=source.logical_object_id, counter_name='charge')
        source.counters['charge'] = 1
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(CounterRemovalError):
            commit_all_counter_removal_effect(engine, plan)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual({'charge': 1, 'study': 2}, source.counters)

    def test_named_kind_removal_rejects_malformed_data_even_when_source_is_unavailable(self):
        valid = {'op': 'remove_all_counters', 'schema_version': 2,
                 'card': None, 'counter': 'charge', 'source': 'source-ref'}
        self.assertEqual((), AllCounterRemovalHandler().lower(valid, self.context(players=2)).intents)
        for change in ({'schema_version': True}, {'schema_version': 1}, {'counter': ''},
                       {'counter': 'Charge'}, {'counter': True}, {'source': None},
                       {'amount': 0}, {'unknown': 1}):
            with self.subTest(change=change), self.assertRaises((SemanticNodeError, ValueError)):
                AllCounterRemovalHandler().lower({**valid, **change}, self.context(players=2))

    def test_named_kind_removal_commit_omission_is_killed(self):
        session = self.session(12270402)
        engine = session.engine
        source = self.add_permanent(engine, seat='A', name='Sol Ring', ref='named-removal-mutant')
        source.counters.update({'charge': 3, 'study': 2})
        plan = AllCounterRemovalHandler().lower({'op': 'remove_all_counters', 'schema_version': 2,
            'card': source.ref, 'counter': 'charge', 'source': source.ref}, self.context(players=2))
        with patch('quorune.semantic_choices.intent_host.commit_all_counter_removal_effect',
                   return_value=AllCounterRemovalResult(object_id=source.object_id, removed=(('charge', 3),))):
            with self.assertRaises(AssertionError):
                execute_intent_plan(engine, plan)
                self.assertEqual({'study': 2}, source.counters)

    def test_all_counter_removal_result_requires_canonical_immutable_shape(
        self,
    ):
        malformed = (
            {"object_id": "target", "removed": [["charge", 1]]},
            {"object_id": "target", "removed": (("charge",),)},
            {"object_id": "target", "removed": (("Charge", 1),)},
            {"object_id": "target", "removed": ((" charge ", 1),)},
            {"object_id": "target", "removed": (("charge", True),)},
            {"object_id": "target", "removed": (("charge", 0),)},
            {
                "object_id": "target",
                "removed": (("charge", 1), ("+1/+1", 1)),
            },
            {
                "object_id": "target",
                "removed": (("charge", 1), ("charge", 2)),
            },
        )
        for fields in malformed:
            with self.subTest(fields=fields):
                with self.assertRaises(CounterRemovalError):
                    AllCounterRemovalResult(**fields)  # type: ignore[arg-type]

    def test_counter_removal_rejects_malformed_state_before_mutation(self):
        for index, malformed in enumerate((True, "1", -1)):
            with self.subTest(malformed=malformed):
                session = self.session(12270220 + index)
                target = self.add_permanent(
                    session.engine,
                    seat="B",
                    name="Island",
                    ref=f"malformed-counter-target-{index}",
                )
                target.counters["charge"] = malformed  # type: ignore[assignment]
                plan = FixedCounterRemovalHandler().lower(
                    self.effect(target.ref),
                    self.context(players=2),
                )

                with self.assertRaises(GameRuleError):
                    execute_intent_plan(session.engine, plan)

                self.assertEqual(malformed, target.counters["charge"])

    def test_fixed_counter_removal_preserves_target_revalidation_and_siege_trigger(
        self,
    ):
        session = self.session(12270202)
        engine = session.engine
        stale = self.add_permanent(
            engine,
            seat="B",
            name="Scute Swarm",
            ref="stale-removal-target",
        )
        stale.counters["charge"] = 2
        template = fixed_counter_removal_effect_template(
            "Remove two charge counters from target creature."
        )
        self.assertIsNotNone(template)
        assert template is not None
        program = SemanticProgram(
            key="fixture:fixed-removal-target-revalidation",
            label="Fixed removal target revalidation",
            effects=[dict(template.effects[0])],
            target_schema=dict(template.target_schema),
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="fixed-removal-target-revalidation",
            ref="S-fixed-removal-target-revalidation",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            targets=[stale.ref],
            visibility=list(engine.seats),
            context={
                "target_groups": {"target_0": [stale.ref]},
                "target_snapshots": {
                    stale.ref: engine._target_snapshot(stale.ref)
                },
                "targets_revalidated": False,
                "targets_chosen_at_creation": True,
            },
        )
        engine.state.stack.append(item)
        stale_plan = FixedCounterRemovalHandler().lower(
            self.effect(stale.ref),
            self.context(players=2),
        )
        engine.state.players["B"].zones["battlefield"].remove(stale.object_id)
        engine.state.players["B"].zones["graveyard"].append(stale.object_id)
        stale.zone = "graveyard"
        engine._begin_resolve_item(
            item,
            [dict(value) for value in program.effects],
            None,
        )
        self.assertEqual(2, stale.counters["charge"])
        self.assertTrue(
            any(event.code == "target.illegal" for event in engine.state.events)
        )
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            execute_intent_plan(engine, stale_plan)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual(2, stale.counters["charge"])

        battle_ref = engine.create_token(
            "B",
            name="Removal Battle",
            battle_protector="A",
            characteristics={
                "type_line": "Token Battle — Siege",
                "defense": "1",
            },
        )[0]
        battle = engine._resolve_object("A", battle_ref, zones={"battlefield"})
        battle_plan = FixedCounterRemovalHandler().lower(
            {
                "op": "remove_counters",
                "card": battle.ref,
                "counter": "defense",
                "amount": 3,
                "source": "fixture-source",
            },
            self.context(players=2),
        )
        execute_intent_plan(engine, battle_plan)
        self.assertNotIn("defense", battle.counters)
        self.assertFalse(engine._stabilize())
        trigger = next(
            item
            for item in engine.state.stack
            if item.semantic_key == "builtin:siege-defeated"
        )
        self.assertEqual(battle.object_id, trigger.source_object_id)

    def test_fixed_counter_removal_replay_is_exact_in_four_player_game(self):
        session = self.session(12270203, players=4)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="C",
            name="Island",
            ref="four-player-removal-target",
        )
        target.counters["charge"] = 3
        program = SemanticProgram(
            key="fixture:fixed-counter-removal-replay",
            label="Fixed counter removal replay",
            effects=[self.effect(target.ref)],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="fixed-counter-removal-replay",
                ref="S-fixed-counter-removal-replay",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                visibility=list(engine.seats),
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for seat in engine.seats:
            result = session.act(f"pilot:{seat}", {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, target.counters["charge"])
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "fixed-counter-removal-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(4, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_fixed_counter_removal_owner_mutant_is_killed(self):
        session = self.session(12270204)
        target = self.add_permanent(
            session.engine,
            seat="A",
            name="Island",
            ref="owner-mutation-target",
        )
        target.counters["charge"] = 2
        plan = FixedCounterRemovalHandler().lower(
            self.effect(target.ref),
            self.context(players=2),
        )
        with patch(
            "quorune.semantic_choices.intent_host.commit_counter_removal_effect",
            return_value=CounterRemovalResult(
                object_id=target.object_id,
                counter_name="charge",
                requested=2,
                removed=2,
                before=2,
                after=0,
            ),
        ):
            with self.assertRaises(AssertionError):
                execute_intent_plan(session.engine, plan)
                self.assertNotIn("charge", target.counters)

    def test_all_counter_removal_revalidates_target_and_replays_in_four_player_game(
        self,
    ):
        stale_session = self.session(12270206)
        stale_engine = stale_session.engine
        stale = self.add_permanent(
            stale_engine,
            seat="B",
            name="Scute Swarm",
            ref="all-counter-stale-target",
        )
        stale.counters["charge"] = 2
        template = all_counter_removal_effect_template(
            "Remove all counters from target creature."
        )
        self.assertIsNotNone(template)
        assert template is not None
        stale_program = SemanticProgram(
            key="fixture:all-counter-stale-target",
            label="All-counter stale target",
            effects=[
                {
                    "op": "remove_all_counters",
                    "card": stale.ref,
                    "source": "fixture-source",
                }
            ],
            target_schema=dict(template.target_schema),
            trust_level="provisional",
        )
        stale_engine.semantics.put(stale_program)
        stale_item = StackItem(
            stack_id="all-counter-stale-target",
            ref="S-all-counter-stale-target",
            kind="triggered_ability",
            controller="A",
            label=stale_program.label,
            semantic_key=stale_program.key,
            targets=[stale.ref],
            visibility=list(stale_engine.seats),
            context={
                "target_groups": {"target_0": [stale.ref]},
                "target_snapshots": {
                    stale.ref: stale_engine._target_snapshot(stale.ref)
                },
                "targets_revalidated": False,
                "targets_chosen_at_creation": True,
            },
        )
        stale_engine.state.stack.append(stale_item)
        stale_engine.move_card(
            stale.object_id,
            "graveyard",
            reason="response",
        )
        # The zone change correctly removed the battlefield incarnation's
        # counters. A counter on the new graveyard incarnation now witnesses
        # that target revalidation prevents the effect from touching it.
        stale.counters["charge"] = 2
        stale_engine._begin_resolve_item(
            stale_item,
            [dict(value) for value in stale_program.effects],
            None,
        )
        self.assertEqual(2, stale.counters["charge"])
        self.assertTrue(
            any(
                event.code == "target.illegal"
                for event in stale_engine.state.events
            )
        )

        session = self.session(12270207, players=4)
        engine = session.engine
        target = self.add_permanent(
            engine,
            seat="C",
            name="Scute Swarm",
            ref="all-counter-replay-target",
        )
        target.counters.update({"charge": 3, "+1/+1": 2})
        program = SemanticProgram(
            key="fixture:all-counter-removal-replay",
            label="All-counter removal replay",
            effects=[
                {
                    "op": "remove_all_counters",
                    "card": target.ref,
                    "source": "fixture-source",
                }
            ],
            target_schema=dict(template.target_schema),
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="all-counter-removal-replay",
                ref="S-all-counter-removal-replay",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                targets=[target.ref],
                visibility=list(engine.seats),
                context={
                    "target_groups": {"target_0": [target.ref]},
                    "target_snapshots": {
                        target.ref: engine._target_snapshot(target.ref)
                    },
                    "targets_revalidated": False,
                    "targets_chosen_at_creation": True,
                },
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for seat in engine.seats:
            result = session.act(f"pilot:{seat}", {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual({}, target.counters)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "all-counter-removal-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(4, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_all_counter_removal_owner_mutant_is_killed(self):
        session = self.session(12270208)
        target = self.add_permanent(
            session.engine,
            seat="A",
            name="Island",
            ref="all-counter-owner-mutation-target",
        )
        target.counters.update({"charge": 2, "+1/+1": 1})
        plan = AllCounterRemovalHandler().lower(
            {
                "op": "remove_all_counters",
                "card": target.ref,
                "source": "fixture-source",
            },
            self.context(players=2),
        )
        with patch(
            "quorune.semantic_choices.intent_host."
            "commit_all_counter_removal_effect",
            return_value=AllCounterRemovalResult(
                object_id=target.object_id,
                removed=(("+1/+1", 1), ("charge", 2)),
            ),
        ):
            with self.assertRaises(AssertionError):
                execute_intent_plan(session.engine, plan)
                self.assertEqual({}, target.counters)


class FixedSourceCounterRemovalActionTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    import test_bound_effect_programs as _actions
    import test_self_public_zone_casting as _zone_actions
    import test_counter_placement_events as _counter_actions
    import test_live_battlefield_quantities as _combat_actions
    session = _actions.BoundEffectProgramRuntimeTests.session
    add = _actions.BoundEffectProgramRuntimeTests.add
    checkpoint = _actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = _counter_actions.CounterPlacementEventRuntimeTests.resolve
    order_triggers = _counter_actions.CounterPlacementEventRuntimeTests.order_triggers
    replay = _actions.BoundEffectProgramRuntimeTests.replay
    prepare = _zone_actions.SelfPublicZoneRuntimeTests.prepare
    cast = _zone_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = _zone_actions.SelfPublicZoneRuntimeTests.assert_closed
    attack = _combat_actions.LiveBattlefieldQuantityActionTests.attack

    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'source-removal.sqlite3'
        build_fixture_database([
            ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/source-counter-removal-cards.json',
            ROOT / 'tests/fixtures/source-damage-death-cards.json',
            ROOT / 'tests/fixtures/event-player-subject-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Source removal', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30),
        ], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def pass_until_stack_size(self, session, count):
        for _ in range(40):
            if len(session.state.stack) == count:
                return
            self.assertGreater(len(session.state.stack), count)
            self.assertEqual('priority', session.state.pending_decision.kind)
            result = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('Source-removal stack did not reach the expected boundary')

    def test_original_removal_carriers_bind_and_last_counter_sibling_stays_residual(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        semantics = SemanticRegistry()
        for name in ('Shrewd Hatchling', 'Sturdy Hatchling', 'Noxious Hatchling',
                     'Belligerent Hatchling', 'Voracious Hatchling', 'Karstoderm', 'Magmaroth',
                     'Divine Intervention'):
            with self.subTest(name=name):
                record = self.db.lookup(name)
                register_generated_programs(self.db, semantics, (record,), trust_level='provisional',
                    capability_registry=self.registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                program = compile_best_available_card_program(self.db, record, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                ir = compile_oracle_card(record, capability_registry=self.registry,
                                         capability_profile='commander_review')
                if name == 'Divine Intervention':
                    self.assertFalse(binding['strict_capability_ready'])
                    self.assertNotEqual('exact', ir.status)
                    self.assertTrue(any('game is a draw' in row.text for row in ir.material_residuals))
                    self.assertTrue(any(node.exact and node.effects
                        and node.effects[0].get('op') == 'remove_counters'
                        for face in ir.faces for node in face.nodes))
                else:
                    self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
                    self.assertEqual('exact', ir.status, ir.material_residuals)
                    self.assertEqual('capability_closed', binding['trust_basis'])

    def test_actual_shrewd_multicolor_cast_keeps_two_triggers_and_partial_removal_replay(self):
        session = self.session(12270301)
        engine = session.engine
        source = self.add(engine, 'Shrewd Hatchling', zone='hand')
        peer = self.add(engine, 'Shrewd Hatchling', seat='B', ref='other-hatchling')
        peer.counters['-1/-1'] = 4
        first = self.add(engine, 'Stream Hopper', zone='hand', ref='first-hopper')
        second = self.add(engine, 'Stream Hopper', zone='hand', ref='second-hopper')
        blue = self.add(engine, 'Generic Bound Next Untap', zone='hand')
        self.assert_closed(session, source)
        self.prepare(session, mana={'U': 12})
        other_view = json.dumps(session.packet('pilot:B', full=True))
        self.assertNotIn(first.ref, other_view)
        self.assertNotIn(second.ref, other_view)
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:B', {'action_id': 'cast:' + source.ref, 'pay': 'auto'})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, 'A', source)
        self.resolve(session)
        self.assertEqual(4, session.state.cards[source.object_id].counters['-1/-1'])
        self.cast(session, 'A', first)
        self.order_triggers(session)
        triggers = [item for item in session.state.stack if item.kind == 'triggered_ability']
        self.assertEqual(2, len(triggers))
        self.assertEqual({'A'}, {item.controller for item in triggers})
        self.assertEqual({source.object_id}, {item.source_object_id for item in triggers})
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual(2, session.state.cards[source.object_id].counters['-1/-1'])
        self.cast(session, 'A', blue, (peer.ref,))
        self.resolve(session)
        self.assertEqual(1, session.state.cards[source.object_id].counters['-1/-1'])
        self.cast(session, 'A', second)
        self.order_triggers(session)
        self.assertEqual(2, sum(item.kind == 'triggered_ability' for item in session.state.stack))
        self.resolve(session)
        self.assertNotIn('-1/-1', session.state.cards[source.object_id].counters)
        self.assertEqual(4, session.state.cards[peer.object_id].counters['-1/-1'])
        changes = [event.details['delta'] for event in session.state.events
                   if event.code == 'permanent.counter' and event.details.get('object') == source.ref]
        self.assertEqual([-1, 0], changes[-2:])
        self.replay(session)

    def test_actual_source_removal_survives_control_change_and_excludes_returned_incarnation(self):
        session = self.session(12270302)
        engine = session.engine
        source = self.add(engine, 'Shrewd Hatchling', zone='hand')
        hopper = self.add(engine, 'Stream Hopper', zone='hand')
        control = self.add(engine, 'Generic Event Subject Control', seat='D', zone='hand')
        blink = self.add(engine, 'Cloudshift', seat='D', zone='hand')
        self.prepare(session, mana={'U': 12})
        session.state.players['D'].mana_pool.update({'W': 2})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.resolve(session)
        original_identity = session.state.cards[source.object_id].logical_object_id
        self.cast(session, 'A', hopper)
        self.order_triggers(session)
        self.assertEqual(3, len(session.state.stack))
        self.cast(session, 'D', control, (source.ref,))
        self.pass_until_stack_size(session, 3)
        current = session.state.cards[source.object_id]
        self.assertEqual('D', current.controller)
        self.assertEqual(4, current.counters['-1/-1'])
        self.pass_until_stack_size(session, 2)
        self.assertEqual(3, session.state.cards[source.object_id].counters['-1/-1'])
        self.assertEqual('A', session.state.stack[-1].controller)
        self.cast(session, 'D', blink, (source.ref,))
        self.pass_until_stack_size(session, 2)
        returned = session.state.cards[source.object_id]
        self.assertNotEqual(original_identity, returned.logical_object_id)
        self.assertEqual(('A', 'D', 4), (returned.owner, returned.controller, returned.counters['-1/-1']))
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertEqual(4, session.state.cards[source.object_id].counters['-1/-1'])
        self.assertEqual('D', session.state.cards[source.object_id].controller)
        self.replay(session)

    def test_actual_ammit_removes_only_minus_counters_after_combat_and_excludes_blinked_source(self):
        for blink_source in (False, True):
            with self.subTest(blink_source=blink_source):
                session = self.session(12270410 + blink_source)
                engine = session.engine
                source = self.add(engine, 'Ammit Eternal', zone='hand', ref='actual-named-removal-ammit')
                peer = self.add(engine, 'Ammit Eternal', seat='C', ref='other-named-removal-ammit')
                peer.counters.update({'-1/-1': 1, 'study': 2})
                enemy_spell = self.add(engine, 'Generic Bound Next Untap', seat='B', zone='hand')
                blink = self.add(engine, 'Cloudshift', zone='hand')
                self.add(engine, 'Generic Bound Plains', ref='named-removal-response-plains')
                private = self.add(engine, 'Generic Bound Growth', seat='D', zone='hand', ref='named-removal-private')
                self.assert_closed(session, source)
                self.prepare(session, mana={'B': 6, 'W': 1})
                session.state.players['B'].mana_pool['U'] = 1
                self.cast(session, 'A', source)
                self.resolve(session)
                self.cast(session, 'B', enemy_spell, (peer.ref,))
                self.order_triggers(session)
                self.resolve(session)
                source = session.state.cards[source.object_id]
                self.assertEqual(1, source.counters['-1/-1'])
                self.assertEqual(3, session.state.cards[peer.object_id].counters['-1/-1'])
                source.counters['study'] = 3
                original_identity = source.logical_object_id
                self.attack(session, [source])
                for _ in range(64):
                    if session.state.stack:
                        break
                    decision = session.state.pending_decision
                    if decision.kind == 'combat.blockers':
                        response = {'action_id': 'block', 'blocks': {}}
                    elif decision.kind == 'combat.damage':
                        response = {'action_id': 'assign_damage', 'assignments': [
                            {'source': source.ref, 'target': 'B', 'amount': 4}]}
                    else:
                        response = {'action_id': 'pass'}
                    accepted = session.act(session.pending_principals()[0], response)
                    self.assertTrue(accepted.ok, accepted.summary)
                else:
                    self.fail('Actual Ammit combat produced no removal trigger')
                self.assertEqual(36, session.state.players['B'].life)
                self.assertEqual(1, len(session.state.stack))
                self.assertEqual(source.object_id, session.state.stack[-1].source_object_id)
                self.assertEqual(1, session.state.cards[source.object_id].counters['-1/-1'])
                before = authoritative_state_hash(session.state)
                self.assertFalse(session.act('pilot:D', {'action_id': 'pass'}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                if blink_source:
                    self.cast(session, 'A', blink, (source.ref,))
                    self.pass_until_stack_size(session, 1)
                    current = session.state.cards[source.object_id]
                    self.assertNotEqual(original_identity, current.logical_object_id)
                    current.counters.update({'-1/-1': 2, 'study': 4})
                    # Start the replay recipe at the pending returned-object boundary.
                    self.checkpoint(session)
                self.resolve(session)
                expected = {'-1/-1': 2, 'study': 4} if blink_source else {'study': 3}
                self.assertEqual(expected, session.state.cards[source.object_id].counters)
                self.assertEqual({'-1/-1': 4 if blink_source else 3, 'study': 2},
                                 session.state.cards[peer.object_id].counters)
                for viewer in 'ABC':
                    self.assertNotIn(private.ref, json.dumps(session.packet('pilot:' + viewer, full=True)))
                self.replay(session, load=True)

    def test_original_named_removal_carriers_preserve_unsupported_sibling_boundaries(self):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        semantics = SemanticRegistry()
        for name in ('Witherscale Wurm', 'Force Bubble', 'Blood Hound',
                     'Energy Vortex', 'Ammit Eternal', 'Discordant Spirit'):
            with self.subTest(name=name):
                source = self.db.lookup(name)
                register_generated_programs(self.db, semantics, (source,), trust_level='provisional',
                    capability_registry=self.registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                program = compile_best_available_card_program(self.db, source, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertEqual(name == 'Ammit Eternal', binding['strict_capability_ready'], binding['blockers'])
                if name != 'Ammit Eternal':
                    ir = compile_oracle_card(source, capability_registry=self.registry,
                                             capability_profile='commander_review')
                    self.assertTrue(ir.material_residuals)


if __name__ == "__main__":
    unittest.main()
