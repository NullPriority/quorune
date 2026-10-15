from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.direct_target import DirectPermanentTargetSpec
from quorune.compiler.return_to_hand_templates import (
    ReturnToHandTarget,
    TargetedReturnToHandEffectTemplate,
    targeted_return_to_hand_effect_template,
    targeted_own_graveyard_return_to_hand_effect_template,
)
from quorune.compiler.self_return_templates import (
    FIXED_SELF_RETURN_MECHANIC,
    fixed_self_return_effect_template,
)
from quorune.oracle_ir import (
    compile_oracle_card,
    register_generated_programs,
)
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.semantics import SemanticRegistry
from quorune.targets import TargetGroup
from scripts.build_test_database import build_fixture_database


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "targeted-return-to-hand.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT
            / "tests"
            / "fixtures"
            / "targeted-return-to-hand-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class NumericOwnedGraveyardReturnRuntimeTests(unittest.TestCase):
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _Witnesses
    session = _Witnesses.session
    add = _Witnesses.add
    ready = _Witnesses.ready
    checkpoint = _Witnesses.checkpoint
    resolve = _Witnesses.resolve
    replay = _Witnesses.replay
    register_fixture_cards_on_add = True

    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'numeric-return.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/targeted-return-to-hand-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.capabilities = cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Numeric return', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_original_numeric_graveyard_returns_share_target_legality_and_replay(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.record import authoritative_state_hash
        for name, mana, cast_source in (
            ('Leonin Squire', {'W': 1, 'C': 1}, True),
            ('Auriok Salvagers', {'W': 1, 'C': 1}, False),
        ):
            with self.subTest(name=name):
                session = self.session(311020 + int(cast_source))
                engine = session.engine
                source = self.add(engine, name, zone='hand' if cast_source else 'battlefield')
                legal = self.add(engine, 'Sol Ring', zone='graveyard', ref='numeric-return-legal')
                too_large = self.add(engine, 'Mind Stone', zone='graveyard', ref='numeric-return-large')
                foreign = self.add(engine, 'Sol Ring', seat='B', zone='graveyard', ref='numeric-return-foreign')
                nonartifact = self.add(engine, 'Generic Bound Body', zone='graveyard', ref='numeric-return-wrong-kind')
                program = compile_best_available_card_program(self.db, self.db.lookup(name),
                    semantic_registry=engine.semantics, capability_registry=self.registry, capability_profile='commander_review')
                self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
                    profile='commander_review')['strict_capability_ready'])
                offered = self.ready(session, source, mana)
                self.checkpoint(session)
                if cast_source:
                    accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
                    self.assertTrue(accepted.ok, accepted.summary)
                    decision = self.resolve(session)
                    self.assertEqual('semantic.target', decision.kind)
                    action_id = 'choose'
                else:
                    action_id = offered['id']
                for principal, ref in (('pilot:A', too_large.ref), ('pilot:A', foreign.ref),
                    ('pilot:A', nonartifact.ref), ('pilot:D', legal.ref)):
                    before = authoritative_state_hash(session.state)
                    denied = session.act(principal, {'action_id': action_id, 'targets': [ref], 'pay': 'auto'})
                    self.assertFalse(denied.ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))
                accepted = session.act('pilot:A', {'action_id': action_id, 'targets': [legal.ref], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary)
                session = self.replay(session, load=True)
                self.resolve(session)
                engine = session.engine
                self.assertEqual('hand', engine.state.cards[legal.object_id].zone)
                for card in (too_large, foreign, nonartifact):
                    self.assertEqual('graveyard', engine.state.cards[card.object_id].zone)
                self.assertEqual(0, sum(engine.state.players['A'].mana_pool.values()))
                projected = session.projector._snapshot('pilot:D')
                import json
                self.assertIn(legal.ref, [row['id'] for row in projected['players']['A']['known_hand']])
                rendered = json.dumps(projected, sort_keys=True)
                for seat in 'ABC':
                    for object_id in engine.state.players[seat].zones['hand']:
                        if object_id != legal.object_id:
                            self.assertNotIn(engine.state.cards[object_id].ref, rendered)
                self.replay(session)


    def test_original_salvagers_target_exiled_in_response_is_not_returned(self):
        session = self.session(311022)
        engine = session.engine
        source = self.add(engine, 'Auriok Salvagers')
        legal = self.add(engine, 'Sol Ring', zone='graveyard', ref='numeric-return-stale')
        crypt = self.add(engine, "Purify the Grave", seat='B', zone='hand', ref='numeric-return-response')
        offered = self.ready(session, source, {'W': 1, 'C': 1})
        engine.state.players['B'].mana_pool['W'] = 1
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'targets': [legal.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(8):
            if session.pending_principals()[0] == 'pilot:B':
                break
            accepted = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(accepted.ok, accepted.summary)
        actions = session.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        offered = next(a for a in actions if a['id'].startswith('cast:') and a.get('card') == crypt.ref)
        accepted = session.act('pilot:B', {'action_id': offered['id'], 'targets': [legal.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        self.assertIsNone(session.state.pending_decision if session.state.pending_decision.kind != 'priority' else None,
            repr(session.state.pending_decision))
        self.assertEqual('exile', session.state.cards[legal.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[crypt.object_id].zone)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.replay(session)


class TargetedReturnToHandTemplateTests(unittest.TestCase):
    def test_numeric_owned_graveyard_return_preserves_kind_and_bound(self):
        from quorune.rules.graveyard_card_targets import OwnGraveyardCardTargetSpec
        for quality, expected in (
            ('artifact', ['artifact']),
            ('creature', ['creature']),
            ('permanent', ['artifact', 'battle', 'creature', 'enchantment', 'land', 'planeswalker']),
            ('nonland permanent', ['artifact', 'battle', 'creature', 'enchantment', 'land', 'planeswalker']),
        ):
            with self.subTest(quality=quality):
                template = targeted_own_graveyard_return_to_hand_effect_template(
                    'Return target ' + quality + ' card with mana value 3 or less from your graveyard to your hand.')
                self.assertIsNotNone(template)
                schema = template.target_schema
                self.assertEqual(expected, schema['types_any'])
                self.assertEqual(3, schema['mana_value_max'])
                self.assertEqual('you', schema['owner_relation'])
                if quality == 'nonland permanent':
                    self.assertEqual(['land'], schema['types_none'])
                self.assertEqual(schema, OwnGraveyardCardTargetSpec.from_target_schema(schema).to_target_schema())
        from test_targeted_graveyard_return_effect_clauses import card_record
        from quorune.rules.capabilities import CapabilityRegistry
        import json
        text = 'Return target artifact card with mana value 1 or less from your graveyard to your hand.'
        registry = load_default_capability_registry()
        self.assertEqual('exact', compile_oracle_card(card_record(text), capability_registry=registry).status)
        with patch.object(OwnGraveyardCardTargetSpec, 'from_target_schema', side_effect=ValueError('numeric target owner withdrawn')):
            self.assertNotEqual('exact', compile_oracle_card(card_record(text), capability_registry=registry).status)
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id'] == 'card.return.own_graveyard_to_owner_hand').update(
            status='blocked', blockers=['graveyard return authority withdrawn'])
        self.assertNotEqual('exact', compile_oracle_card(card_record(text),
            capability_registry=CapabilityRegistry(raw)).status)
        for text in (
            'Return target creature card with mana value X or less from your graveyard to your hand.',
            'Return target creature card with mana value 3 or greater from your graveyard to your hand.',
            'Return target creature card with power 3 or less from your graveyard to your hand.',
            'Return target chosen card with mana value 3 or less from your graveyard to your hand.',
            'Return target artifact card with mana value 21 or less from your graveyard to your hand.',
        ):
            self.assertIsNone(targeted_own_graveyard_return_to_hand_effect_template(text))

    def test_targeted_return_template_is_immutable_and_copy_isolated(self):
        template = TargetedReturnToHandEffectTemplate(
            ReturnToHandTarget.CREATURE
        )

        self.assertEqual("return-target-creature-v2", template.template_id)
        self.assertEqual(
            ({"op": "bounce", "card": "$target.0"},),
            template.effects,
        )
        schema = template.target_schema
        schema["types_any"].append("artifact")
        effects = template.effects
        effects[0]["op"] = "exile"
        self.assertEqual(["creature"], template.target_schema["types_any"])
        self.assertEqual("bounce", template.effects[0]["op"])
        self.assertEqual(
            ["land"],
            TargetedReturnToHandEffectTemplate(
                ReturnToHandTarget.NONLAND_PERMANENT
            ).target_schema["types_none"],
        )
        with self.assertRaisesRegex(ValueError, "target"):
            TargetedReturnToHandEffectTemplate(  # type: ignore[arg-type]
                "creature"
            )

    def test_return_whole_clause_parser_accepts_only_closed_direct_targets(self):
        for target in ReturnToHandTarget:
            with self.subTest(target=target):
                template = targeted_return_to_hand_effect_template(
                    f"Return target {target.value} to its owner's hand."
                )
                self.assertIsNotNone(template)
                assert template is not None
                self.assertEqual(target, template.target)
        for text in (
            "Return up to one target creature to its owner's hand.",
            "You may return target creature to its owner's hand.",
            "Return target creature to its controller's hand.",
            "Return target creature to its owners hand.",
            "Return target creature card from a graveyard to its owner's hand.",
            "Return all creatures to their owners' hands.",
            "Return target creature to its owner's hand. Draw a card.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(targeted_return_to_hand_effect_template(text))

    def test_closed_direct_target_predicates_share_the_return_owner(self):
        cases = {
            "Return another target creature you control to their owner's hand.": (
                "creature-you-another",
                "you",
                True,
            ),
            "Return target tapped creature an opponent controls to its owner's hand.": (
                "creature-opponent-tapped",
                "opponent",
                False,
            ),
            "Return target artifact, enchantment, or land to its owner's hand.": (
                "artifact-or-enchantment-or-land",
                "any",
                False,
            ),
        }
        for text, (slug, controller, exclude_source) in cases.items():
            with self.subTest(text=text):
                template = targeted_return_to_hand_effect_template(text)
                self.assertIsNotNone(template)
                assert template is not None
                self.assertEqual(f"return-target-{slug}-v2", template.template_id)
                self.assertIsNotNone(template.target_spec)
                assert template.target_spec is not None
                self.assertEqual(controller, template.target_spec.controller_relation)
                self.assertEqual(
                    exclude_source,
                    template.target_spec.source_exclusion,
                )

    def test_combat_state_return_reuses_shared_direct_target_schema(self):
        template = targeted_return_to_hand_effect_template(
            "Return target blocking creature to its owner's hand."
        )

        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(
            DirectPermanentTargetSpec(
                types_any=("creature",), combat_state="blocking"
            ),
            template.target_spec,
        )
        self.assertEqual(
            "return-target-creature-blocking-v2", template.template_id
        )
        self.assertEqual("blocking", template.target_schema["combat_state"])

    def test_nonland_target_uses_the_shared_exact_type_predicate(self):
        group = TargetGroup.from_mapping(
            TargetedReturnToHandEffectTemplate(
                ReturnToHandTarget.NONLAND_PERMANENT
            ).target_schema
        )

        self.assertTrue(
            group.matches_type_characteristics(
                types=("artifact",),
                subtypes=(),
                supertypes=(),
            )
        )
        self.assertFalse(
            group.matches_type_characteristics(
                types=("artifact", "land"),
                subtypes=(),
                supertypes=(),
            )
        )


class TargetedReturnToHandCompilerTests(unittest.TestCase):
    def test_shared_return_fixture_reuses_canonical_existing_card_payloads(self):
        import json
        canonical = json.loads((ROOT / 'tests/fixtures/scryfall-exact-lists.json').read_text(encoding='utf-8'))
        focused = json.loads((ROOT / 'tests/fixtures/targeted-return-to-hand-cards.json').read_text(encoding='utf-8'))
        by_id = {card['oracle_id']: card for card in canonical['cards']}
        shared = [card for card in focused['cards'] if card['oracle_id'] in by_id]
        self.assertTrue(shared)
        for card in shared:
            with self.subTest(card=card['name']):
                self.assertEqual(by_id[card['oracle_id']], card)

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.base = cls.db.lookup("Lightning Greaves")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, oracle_text: str, *, type_line: str = "Instant"):
        return compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=oracle_text,
                type_line=type_line,
                keywords=(),
                faces=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_spell_trigger_and_activated_contexts_share_targeted_return_lowering(self):
        contexts = (
            (
                "Return target creature to its owner's hand.",
                "Instant",
                "spell_ability",
                "return-target-creature-v2",
            ),
            (
                "When this creature enters, return target artifact to its owner's hand.",
                "Creature — Test",
                "triggered_ability",
                "return-target-artifact-v2",
            ),
            (
                "{2}{U}, {T}: Return target nonland permanent to its owner's hand.",
                "Creature — Test",
                "activated_ability",
                "return-target-nonland-permanent-v2",
            ),
            (
                "Return target tapped creature an opponent controls to its owner's hand.",
                "Instant",
                "spell_ability",
                "return-target-creature-opponent-tapped-v2",
            ),
            (
                "{W}: Return another target creature you control to their owner's hand.",
                "Creature — Test",
                "activated_ability",
                "return-target-creature-you-another-v2",
            ),
        )
        for text, type_line, kind, template_id in contexts:
            with self.subTest(kind=kind, text=text):
                ir = self.compile(text, type_line=type_line)
                node = ir.faces[0].nodes[0]
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertEqual(template_id, node.template_id)
                expected_capabilities = {
                    "permanent.return.owner_hand",
                    "target.revalidate_resolution",
                }
                if template_id == "return-target-nonland-permanent-v2":
                    expected_capabilities.add(
                        "target.permanent.characteristic_predicate"
                    )
                if template_id == "return-target-creature-opponent-tapped-v2":
                    expected_capabilities.add(
                        "state_query.permanent.public_state_predicate"
                    )
                self.assertEqual(
                    expected_capabilities,
                    set(node.capability_dependencies)
                    - {
                        "trigger.event.normalized_zone_change",
                        "trigger.placement.apnap",
                    },
                )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_fixed_self_return_activation_is_capability_closed(self):
        text = "{2}{U}: Return this creature to its owner's hand."
        ir = self.compile(text, type_line="Creature — Test")
        node = ir.faces[0].nodes[0]

        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertTrue(node.exact)
        self.assertEqual("activated_ability", node.kind)
        self.assertEqual("bounce-self-creature-v1", node.template_id)
        self.assertEqual((FIXED_SELF_RETURN_MECHANIC,), node.mechanics)
        self.assertEqual(
            ({"op": "bounce", "card": "$source"},),
            node.effects,
        )
        self.assertIn(
            "permanent.return.owner_hand",
            node.capability_dependencies,
        )

        template = fixed_self_return_effect_template(
            "Return this enchantment to its owner's hand."
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual("bounce-self-enchantment-v1", template.template_id)
        for effects, schema, mechanics in (
            (template.compiled()[1], {"count": 1}, template.compiled()[3]),
            (({"op": "bounce", "card": "$target.0"},), None, template.compiled()[3]),
            (template.compiled()[1], None, ("cr-400-general",)),
        ):
            with self.subTest(effects=effects, schema=schema):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=schema,
                        mechanic_ids=mechanics,
                    )
                )

    def test_unsupported_return_variants_remain_material_residuals(self):
        for text in (
            "Return target creature card from a graveyard to its owner's hand.",
            "Return target creature to its controller's hand.",
            "Return target creature or spell to its owner's hand.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_targeted_return_shape_mutants_fail_closed(self):
        template = TargetedReturnToHandEffectTemplate(
            ReturnToHandTarget.NONLAND_PERMANENT
        )
        expected = {
            "permanent.return.owner_hand",
            "target.permanent.characteristic_predicate",
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
        malformed_effects = (
            ({"op": "bounce", "card": "$target.1"},),
            ({"op": "bounce", "card": "$source"},),
            (
                {
                    "op": "bounce",
                    "card": "$target.0",
                    "reason": "open grammar",
                },
            ),
            ({"op": "move", "card": "$target.0"},),
        )
        for effects in malformed_effects:
            with self.subTest(effects=effects):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=template.target_schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        malformed_schemas = (
            {**template.target_schema, "zones": ["graveyard"]},
            {**template.target_schema, "count": 2},
            {**template.target_schema, "types_none": []},
            {**template.target_schema, "types_any": ["spell"]},
            {**template.target_schema, "controller": "opponent"},
        )
        for schema in malformed_schemas:
            with self.subTest(schema=schema):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=template.effects,
                        target_schema=schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        self.assertFalse(
            capability_dependencies_for_node(
                effects=template.effects,
                target_schema=template.target_schema,
                mechanic_ids=("cr-115-targets",),
            )
        )

    def test_expanded_target_compiler_mutant_is_killed(self):
        text = (
            "Return target tapped creature an opponent controls to its "
            "owner's hand."
        )
        self.assertEqual("exact", self.compile(text).status)
        from common import without_direct_resolution_compiler
        with without_direct_resolution_compiler("targeted_return_to_hand_effect_template"):
            mutated = self.compile(text)
        self.assertNotEqual("exact", mutated.status)
        self.assertTrue(mutated.material_residuals)

    def test_generated_return_program_is_capability_closed(self):
        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            (self.db.lookup("Unsummon"),),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_effect_programs=True,
        )
        programs = [program for program in registry.programs() if program.effects]
        self.assertEqual(1, result["exact_effect_programs_promoted"])
        self.assertEqual(1, len(programs))
        self.assertEqual("trusted", programs[0].trust_level)
        self.assertTrue(
            {
                "permanent.return.owner_hand",
                "target.revalidate_resolution",
            }.issubset(programs[0].capability_dependencies)
        )


if __name__ == "__main__":
    unittest.main()
