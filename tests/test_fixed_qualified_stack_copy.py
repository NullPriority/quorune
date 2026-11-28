"""CR707.10: copied stack objects retain their source and paid choices."""
from dataclasses import replace
from copy import deepcopy
from types import SimpleNamespace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients

from quorune.stack_spell_copy_model import FixedStackSpellCopySpec, FixedQualifiedStackCopySpec, qualified_stack_copy_specs
from quorune.compiler.fixed_stack_spell_copy import fixed_stack_spell_copy_template
from quorune.rules.stack_spell_copy_shapes import fixed_stack_spell_copy_node_capabilities
from quorune.ability_source_information import ability_source_filter_dependencies, ABILITY_SOURCE_INFORMATION_CAPABILITY
from quorune.model import StackItem
from quorune.target_predicates import target_predicate_matches
from quorune.targets import target_plan

NAMES = ['Lithoform Engine', 'Strionic Resonator', "Tawnos, Urza's Apprentice", 'Abstruse Archaic']
FIXTURES = [
    ROOT / "tests/fixtures/fixed-qualified-stack-copy-cards.json",
    ROOT / "tests/fixtures/scryfall-exact-lists.json",
]


class QualifiedStackCopyModelTests(unittest.TestCase):
    def test_closed_copy_kinds_preserve_old_spell_wire_and_every_target_policy(self):
        old = FixedStackSpellCopySpec(True)
        self.assertEqual({'schema_version': 1, 'controlled_only': True}, old.to_dict())
        self.assertEqual(old, FixedStackSpellCopySpec.from_dict(old.to_dict()))
        for spec in qualified_stack_copy_specs():
            self.assertEqual(spec, FixedQualifiedStackCopySpec.from_dict(spec.to_dict()))
            self.assertEqual(None if spec.schema_version == 3 else "you", spec.target_schema.get("controller_relation"))
            self.assertTrue(spec.target_schema['source_exclusion'])
            self.assertEqual(spec.target_kind == 'permanent_spell', bool(spec.effects[0].get('retain_targets_only')))
            for changes in ({'schema_version': True}, {'schema_version': 2}, {'target_kind': 'spell'},
                    {'source_filter': 'creature'}, {'kind': 'fixed_instant_sorcery_copy'}, {'extra': True}):
                with self.subTest(spec=spec, changes=changes), self.assertRaises(ValueError):
                    FixedQualifiedStackCopySpec.from_dict({**spec.to_dict(), **changes})

    def test_complete_copy_clauses_lower_and_open_or_incompatible_policies_stay_closed(self):
        lines = [
            'Copy target permanent spell you control.',
            'Copy target triggered ability you control. You may choose new targets for the copy.',
            'Copy target activated or triggered ability you control. You may choose new targets for the copy.',
            'Copy target activated or triggered ability you control from an artifact source. You may choose new targets for the copy.',
            'Copy target activated or triggered ability you control from a colorless source. You may choose new targets for the copy.',
        ]
        for line in lines:
            self.assertIsNotNone(fixed_stack_spell_copy_template(line))
            self.assertIsNone(fixed_stack_spell_copy_template(line+' Draw a card.'))
        for line in ('Copy target activated or triggered ability.', 'Copy target triggered ability you control from an artifact source. You may choose new targets for the copy.',
                'Copy target permanent spell you control. You may choose new targets for the copy.'):
            self.assertIsNone(fixed_stack_spell_copy_template(line))

    def test_mixed_stack_copy_scope_distinguishes_spells_and_stack_abilities(self):
        from quorune.stack_spell_copy_model import mixed_stack_copy_spec

        spec = mixed_stack_copy_spec()
        self.assertEqual(spec, FixedQualifiedStackCopySpec.from_dict(spec.to_dict()))
        self.assertEqual((), fixed_stack_spell_copy_node_capabilities(
            effects=spec.effects, target_schema={**spec.target_schema, "count": True},
            mechanic_ids=spec.mechanics,
        ))
        group, = target_plan(spec.target_schema).groups
        cases = (
            ("spell", "spell", {"instant"}, True),
            ("spell_copy", "spell", {"sorcery"}, True),
            ("spell", "spell", {"creature"}, False),
            ("spell_copy", "spell", {"artifact"}, False),
            ("activated_ability", "ability", {"creature"}, True),
            ("triggered_ability", "ability", set(), True),
            ("spell", "ability", {"instant"}, False),
        )
        for kind, category, types, expected in cases:
            with self.subTest(kind=kind, types=types):
                item = StackItem(stack_id="subject", ref="S1", kind=kind, controller="B", label="Subject")
                row = {"ref": "S1", "zone": "stack", "category": category, "stack_item": item}
                self.assertIs(expected, target_predicate_matches(
                    SimpleNamespace(), group, row, types=types, supertypes=set(), colors=set(), derived={},
                ))

    def test_source_filters_have_closed_controlled_schema_and_fine_information_dependency(self):
        for source in ('artifact', 'colorless'):
            spec = FixedQualifiedStackCopySpec('activated_or_triggered_ability', source)
            self.assertEqual((ABILITY_SOURCE_INFORMATION_CAPABILITY,), ability_source_filter_dependencies(spec.target_schema))
            for changes in ({'controller_relation': 'opponent'}, {'count': 2}, {'categories': ['spell']}, {'extra': True}):
                with self.assertRaises(ValueError): ability_source_filter_dependencies({**spec.target_schema, **changes})

    def test_source_qualification_distinguishes_unavailable_from_colorless_and_exact_kind(self):
        def matches(spec, *, kind='activated_ability', source_types=('artifact',), colors=()):
            item = StackItem(stack_id='ability', ref='S1', kind=kind, controller='A', label='Ability')
            group = target_plan(spec.target_schema).groups[0]
            row = {'ref': 'S1', 'zone': 'stack', 'category': 'ability', 'stack_item': item,
                'ability_source_types': None if source_types is None else frozenset(source_types),
                'ability_source_colors': None if colors is None else frozenset(colors)}
            return target_predicate_matches(SimpleNamespace(), group, row, types=set(), supertypes=set(), colors=set(), derived={})
        artifact = FixedQualifiedStackCopySpec('activated_or_triggered_ability', 'artifact')
        colorless = FixedQualifiedStackCopySpec('activated_or_triggered_ability', 'colorless')
        self.assertTrue(matches(artifact)); self.assertTrue(matches(artifact, kind='triggered_ability'))
        self.assertFalse(matches(artifact, source_types=('creature',))); self.assertFalse(matches(artifact, source_types=None))
        self.assertTrue(matches(colorless)); self.assertFalse(matches(colorless, colors=('G',)))
        self.assertFalse(matches(colorless, colors=None)); self.assertFalse(matches(colorless, source_types=None))
        self.assertFalse(matches(artifact, kind='spell'))

    def test_declaration_gate_keeps_effect_target_policy_and_concrete_source_dependency(self):
        for spec in qualified_stack_copy_specs():
            deps = fixed_stack_spell_copy_node_capabilities(effects=spec.effects,
                target_schema=spec.target_schema, mechanic_ids=spec.mechanics)
            self.assertIn('stack.copy.fixed_qualified_target', deps)
            self.assertEqual(spec.source_filter is not None, ABILITY_SOURCE_INFORMATION_CAPABILITY in deps)
            bad = {**spec.effects[0], 'retain_targets_only': spec.target_kind != 'permanent_spell'}
            self.assertFalse(fixed_stack_spell_copy_node_capabilities(effects=(bad,), target_schema=spec.target_schema, mechanic_ids=spec.mechanics))

    def test_runtime_minimum_is_derived_from_data_without_coverage_or_handlers(self):
        from quorune.rules.stack_spell_copy_shapes import qualified_stack_copy_runtime_dependencies
        for spec in qualified_stack_copy_specs():
            p = SimpleNamespace(effects=spec.effects, target_schema=spec.target_schema, coverage=(), handlers=())
            self.assertIn('stack.copy.fixed_qualified_target', qualified_stack_copy_runtime_dependencies(p))
            for target in ({**spec.target_schema, 'controller_relation': 'opponent'},
                    {**spec.target_schema, 'count': 2}):
                with self.assertRaises(ValueError):
                    qualified_stack_copy_runtime_dependencies(SimpleNamespace(effects=spec.effects, target_schema=target))
        old = FixedStackSpellCopySpec(True)
        self.assertFalse(qualified_stack_copy_runtime_dependencies(SimpleNamespace(effects=old.effects, target_schema=old.target_schema)))


class QualifiedStackCopyCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name)/'qualified-copy.sqlite3'
        build_fixture_database(FIXTURES, path); cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls): cls.db.close(); cls.temporary.cleanup()

    def test_all_four_originals_have_normal_complete_admission_and_source_spans(self):
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
        semantics = SemanticRegistry(); cards = [self.db.lookup(n, fuzzy=False) for n in NAMES]
        register_generated_programs(self.db, semantics, cards, trust_level='provisional', capability_registry=self.registry,
            capability_profile='commander_review', promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(card=card.name):
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                for face in ir.faces:
                    for node in face.nodes: self.assertEqual(node.text, card.oracle_text[node.span.start:node.span.end])
                program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                bound = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'], (card.name, bound['blockers']))
                for ability in semantics.programs_for_oracle(card.oracle_id, event='activate'):
                    if not any(effect.get('op') == 'copy_stack_item' for effect in ability.effects): continue
                    if 'stack.copy.fixed_qualified_target' not in ability.capability_dependencies: continue
                    deps = tuple(x for x in ability.capability_dependencies if x != 'stack.copy.fixed_qualified_target')
                    altered = replace(ability, handlers=(), coverage=(), capability_dependencies=deps,
                        capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                    checked = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile='commander_review')
                    self.assertFalse(checked['strict'])
                    self.assertIn('capability:undeclared_runtime_dependency:stack.copy.fixed_qualified_target', checked['blockers'])

    def test_scientist_uses_strict_source_qualified_copy_with_exact_reviewed_precedence(self):
        from quorune.card_programs.activation_view_equivalence import shadowed_reviewed_activation_view_keys
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
        from quorune.compiler.activated_ability_catalog import with_activated_ability_catalog
        from quorune.semantics import SemanticRegistry

        card = self.db.lookup("Scientist Supreme of A.I.M.", fuzzy=False)
        semantics = SemanticRegistry()
        reviewed = next(p for p in semantics.programs_for_oracle(card.oracle_id) if p.event == "activate")
        profile = "commander_review"
        register_generated_programs(self.db, semantics, [card], capability_registry=self.registry,
            capability_profile=profile, promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True, promote_exact_runtime_handlers=True)
        current, = semantics.programs_for_oracle(card.oracle_id, event="activate")
        self.assertTrue(current.provenance["authored_by"].startswith("oracle-ir-v"))
        self.assertEqual("artifact_ability_source", current.target_schema["predicate"])
        self.assertIn(ABILITY_SOURCE_INFORMATION_CAPABILITY, current.capability_dependencies)
        self.assertEqual(2, current.cost_schema["life_payment"])
        self.assertEqual("once_per_turn", current.cost_schema["activation_limit"])
        conditions = current.handlers[0]["ability"]["activation_conditions"]
        self.assertEqual(["controllers_turn"], [condition["kind"] for condition in conditions])

        program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
            capability_registry=self.registry, capability_profile=profile)
        bound = bind_card_program_runtime(program, capability_registry=self.registry, profile=profile)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        self.assertFalse(bound["compatibility_provenance"])
        dependencies = tuple(cap for cap in current.capability_dependencies if cap != ABILITY_SOURCE_INFORMATION_CAPABILITY)
        omitted = replace(current, capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies, profile=profile).to_dict())
        omitted_binding = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=profile)
        self.assertFalse(omitted_binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + ABILITY_SOURCE_INFORMATION_CAPABILITY,
            omitted_binding["blockers"])

        augmented, = with_activated_ability_catalog(card, [reviewed], reference_programs=[current])
        self.assertEqual("artifact_source", reviewed.target_schema["predicate"])
        self.assertEqual("artifact_source", augmented.handlers[0]["ability"]["target_schema"]["predicate"])

        def shadowed(old):
            return shadowed_reviewed_activation_view_keys(card, [current], [old],
                capability_registry=self.registry, capability_profile=profile)

        self.assertEqual({reviewed.key}, shadowed(augmented))
        for delta in ({"controller": "opponent"}, {"count": True}, {"count": 2}, {"predicate": "any_ability"}):
            with self.subTest(delta=delta):
                schema = {**augmented.target_schema, **delta}
                handlers = deepcopy(augmented.handlers)
                handlers[0]["ability"]["target_schema"] = schema
                self.assertFalse(shadowed(replace(augmented, target_schema=schema, handlers=handlers)))
        self.assertFalse(shadowed(replace(augmented, cost_schema={**current.cost_schema, "life_payment": 1})))
        timing = deepcopy(augmented.handlers)
        timing[0]["ability"]["activation_conditions"] = []
        self.assertFalse(shadowed(replace(augmented, handlers=timing)))

    def test_genuine_copy_owner_withdrawal_keeps_the_old_spell_copy_owner(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8')); raw = deepcopy(raw)
        next(r for r in raw['capabilities'] if r['id'] == 'stack.copy.fixed_qualified_target').update(status='blocked', blockers=['owner withdrawn'])
        registry = CapabilityRegistry(raw); index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        for name in NAMES: self.assertNotEqual('exact', compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review').status)
        self.assertEqual('exact', compile_oracle_card(self.db.lookup('Reverberate'), capability_registry=registry, capability_profile='commander_review').status)

    def test_reviewed_copy_replacement_requires_complete_current_source_and_unchanged_cost(self):
        from quorune.semantics import SemanticRegistry
        from quorune.compiler.activated_ability_catalog import with_activated_ability_catalog
        from quorune.card_programs.activation_view_equivalence import shadowed_reviewed_activation_view_keys
        semantics = SemanticRegistry(); card = self.db.lookup('Lithoform Engine')
        legacy = semantics.programs_for_oracle(card.oracle_id)
        register_generated_programs(self.db, semantics, [card], trust_level='provisional', capability_registry=self.registry,
            capability_profile='commander_review', promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        current = semantics.programs_for_oracle(card.oracle_id)
        augmented = with_activated_ability_catalog(card, legacy, reference_programs=current)
        def keys(new=current, old=augmented):
            return shadowed_reviewed_activation_view_keys(card, new, old,
                capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual({p.key for p in augmented if p.event == 'activate'}, keys())
        for old in augmented:
            if old.event != 'activate': continue
            for changed in (replace(old, provenance={**old.provenance, 'source_oracle_hash': 'stale'}),
                    replace(old, provenance={**old.provenance, 'source_rulings_hash': 'stale'}),
                    replace(old, effects=({'op': 'draw', 'player': '$controller', 'count': 1},)),
                    replace(old, cost_schema={'mana': {'C': 99}})):
                self.assertFalse(keys(old=(changed,)))
        incomplete = tuple(replace(p, provenance={**p.provenance, 'requires_complete_card_program': False}) for p in current)
        self.assertFalse(keys(new=incomplete))


class QualifiedStackCopyNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = recipients.TargetRecipientNativeTests.reject
    finish = recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name)/'qualified-copy-native.sqlite3'
        build_fixture_database(FIXTURES, path); cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Qualified stack copies', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def activate_target(self, s, source, target_ref, *, reserve=None, x=None):
        self.priority(s, 'A')
        actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        candidates = [a for a in actions if a['id'].startswith('activate:'+source.ref+':')
            and target_ref in json.dumps(a.get('target_schema') or {})]
        self.assertTrue(candidates, (source.ref, target_ref, actions))
        command = {'action_id': candidates[0]['id'], 'targets': [target_ref], 'pay': 'auto', 'reserve': reserve or {}}
        if x is not None: command['x'] = x
        self.reject(s, 'C', command); result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        return candidates[0]

    def choose_targets(self, s, refs):
        decision = self.resolve(s); self.assertEqual('semantic.choice', decision.kind)
        command = {'action_id': 'choose', 'targets': list(refs)}; self.reject(s, 'B', command)
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)

    def test_rings_keeps_announced_x_and_allows_a_new_target_with_replay(self):
        for retarget in (False, True):
            with self.subTest(retarget=retarget):
                game = self.session(878300 + retarget)
                rings = self.add(game.engine, "Rings of Brighthearth")
                source = self.add(game.engine, "Arashi, the Sky Asunder")
                first = self.add(game.engine, "Vampire Nighthawk", seat="B", ref="rings-first-flying-target")
                second = self.add(game.engine, "Vampire Nighthawk", seat="C", ref="rings-second-flying-target")
                self.add(game.engine, "Fervor")
                private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="rings-private")
                self.assert_closed(game, rings)
                self.assert_closed(game, source)
                self.prepare(game, mana={"C": 3, "G": 1})
                self.checkpoint(game)
                self.activate_target(game, source, first.ref, x=1, reserve={"C": 2})
                original, trigger = game.state.stack
                self.assertEqual(rings.object_id, trigger.source_object_id)
                self.assertEqual("semantic.choice", self.resolve(game).kind)
                self.assertEqual(["pilot:A"], game.pending_principals())
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B", {"action_id": "choose", "pay": True}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                game = self.replay(game, load=True)
                paid = game.act("pilot:A", {"action_id": "choose", "pay": True})
                self.assertTrue(paid.ok, paid.summary)
                self.assertEqual(["pilot:A"], game.pending_principals())
                for seat in "BCD":
                    self.assertIsNone(game.packet("pilot:"+seat, full=True)["decision"])
                game = self.replay(game, load=True)
                choice = {"action_id": "choose"}
                if retarget:
                    choice["targets"] = [second.ref]
                accepted = game.act("pilot:A", choice)
                self.assertTrue(accepted.ok, accepted.summary)
                still_original, copied = game.state.stack
                self.assertEqual(original.ref, still_original.ref)
                self.assertEqual((1, source.object_id, original.semantic_key),
                                 (copied.x_value, copied.source_object_id, copied.semantic_key))
                self.assertEqual([second.ref if retarget else first.ref], copied.targets)
                self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
                self.assertIsNone(self.resolve(game))
                self.assertEqual(1 + int(not retarget), game.state.cards[first.object_id].marked_damage)
                self.assertEqual(int(retarget), game.state.cards[second.object_id].marked_damage)
                self.finish(game, private)

    def test_rings_copies_a_countered_activation_with_replay(self):
        game = self.session(878310)
        rings = self.add(game.engine, "Rings of Brighthearth")
        dealer = self.add(game.engine, "Prodigal Sorcerer")
        self.add(game.engine, "Fervor")
        stifle = self.add(game.engine, "Stifle", seat="B", zone="hand")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="countered-activation-private")
        self.prepare(game, mana={"C": 2})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.activate_target(game, dealer, "B")
        activated, trigger = game.state.stack
        self.cast_for(game, "B", stifle, [activated.ref])
        self.resolve_top(game)
        self.assertEqual([trigger.ref], [row.ref for row in game.state.stack])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        paid = game.act("pilot:A", {"action_id": "choose", "pay": True})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual("semantic.choice", game.state.pending_decision.kind)
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "targets": ["D"]})
        self.assertTrue(result.ok, result.summary)
        copied, = game.state.stack
        self.assertEqual(dealer.object_id, copied.source_object_id)
        self.assertEqual(["D"], copied.targets)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40, 40, 40, 39], [game.state.players[seat].life for seat in "ABCD"])
        self.finish(game, private)

    def test_rings_observes_only_its_controllers_nonmana_activation_and_decline_replays(self):
        game = self.session(878320)
        self.add(game.engine, "Rings of Brighthearth")
        mana = self.add(game.engine, "Llanowar Elves")
        dealer = self.add(game.engine, "Prodigal Sorcerer")
        opponent = self.add(game.engine, "Prodigal Sorcerer", seat="B", ref="rings-opposing-activation-source")
        self.add(game.engine, "Fervor")
        self.add(game.engine, "Fervor", seat="B", ref="rings-opposing-haste")
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"].startswith("activate:" + mana.ref + ":"))
        accepted = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(game.state.stack)
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"].startswith("activate:" + opponent.ref + ":"))
        accepted = game.act("pilot:B", {"action_id": action["id"], "targets": ["D"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(1, len(game.state.stack))
        self.resolve_top(game)
        self.activate_target(game, dealer, "B")
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        declined = game.act("pilot:A", {"action_id": "choose", "pay": False})
        self.assertTrue(declined.ok, declined.summary)
        self.assertEqual(1, len(game.state.stack))
        self.assertEqual({"C": 2, "G": 1}, {color: count for color, count in game.state.players["A"].mana_pool.items() if count})
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40, 39, 40, 39], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_rings_requires_its_exact_paid_nonmana_occurrence(self):
        from quorune.rules.nonmana_activation_copy_model import ABILITY_ACTIVATION_COPY_CAPABILITY
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.nonmana_activation_copy import nonmana_activation_copy_payment

        record = self.db.lookup("Rings of Brighthearth", fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status)
        semantics = self.session(878330).engine.semantics
        register_generated_programs(self.db, semantics, [record], trust_level="provisional",
            capability_registry=self.registry, capability_profile="commander_review", promote_exact_trigger_programs=True,
            promote_exact_capability_declarations=True)
        program, = semantics.programs_for_oracle(record.oracle_id)
        self.assertEqual((nonmana_activation_copy_payment(),), tuple(program.effects))
        self.assertIn(ABILITY_ACTIVATION_COPY_CAPABILITY, program.capability_dependencies)
        for changes in ({"event": "spell.cast"}, {"event_condition": None}, {"active_zone": "graveyard"},
                        {"effects": ({**program.effects[0], "player": "$context.player"},)}):
            with self.subTest(changes=changes):
                broken = replace(program, **changes)
                self.assertFalse(bind_semantic_program_runtime(broken, capability_registry=self.registry,
                    profile="commander_review")["strict"])
        with patch("quorune.compiler.nonmana_activation_copy.nonmana_activation_copy_binding", return_value=None):
            unsupported = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertNotEqual("exact", unsupported.status)

    def test_actual_lithoform_copies_activated_damage_without_repaying_or_activating_again(self):
        s = self.session(871001); copier = self.add(s.engine, 'Lithoform Engine'); dealer = self.add(s.engine, 'Prodigal Sorcerer')
        self.add(s.engine, 'Fervor'); private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='qualified-litho-private')
        self.assert_closed(s, copier); self.assert_closed(s, dealer); self.prepare(s, mana={'C': 2}); self.checkpoint(s)
        self.activate_target(s, dealer, 'B'); original = s.state.stack[-1]
        self.activate_target(s, copier, original.ref); self.assertTrue(s.state.cards[copier.object_id].tapped)
        self.choose_targets(s, ['D']); self.assertIsNone(self.resolve(s))
        self.assertEqual([40, 39, 40, 39], [s.state.players[x].life for x in 'ABCD'])
        self.assertTrue(s.state.cards[dealer.object_id].tapped); self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        self.finish(s, private)

    def test_actual_strionic_copies_trigger_only_after_source_departure(self):
        s = self.session(871010); copier = self.add(s.engine, 'Strionic Resonator')
        visionary = self.add(s.engine, 'Elvish Visionary', zone='hand'); bounce = self.add(s.engine, 'Unsummon', seat='B', zone='hand')
        self.add(s.engine, 'Island', seat='B'); private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='qualified-trigger-private')
        self.assert_closed(s, copier); self.prepare(s, mana={'C': 3, 'G': 1}); self.checkpoint(s)
        self.cast_for(s, 'A', visionary, reserve={'C': 2}); self.resolve_top(s)
        original = next(i for i in s.state.stack if i.kind == 'triggered_ability' and i.source_object_id == visionary.object_id)
        self.cast(s, 'B', bounce, [visionary.ref]); self.resolve_top(s); count = len(s.state.players['A'].zones['hand'])
        self.activate_target(s, copier, original.ref); self.assertIsNone(self.resolve(s))
        self.assertEqual(count+2, len(s.state.players['A'].zones['hand'])); self.finish(s, private)

    def test_actual_tawnos_requires_artifact_source_and_preserves_source_lki(self):
        s = self.session(871020); copier = self.add(s.engine, "Tawnos, Urza's Apprentice")
        rod = self.add(s.engine, 'Rod of Ruin'); invalid = self.add(s.engine, 'Prodigal Sorcerer'); self.add(s.engine, 'Fervor')
        remove = self.add(s.engine, 'Disenchant', seat='B', zone='hand'); self.add(s.engine, 'Plains', seat='B'); self.add(s.engine, 'Wastes', seat='B')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='qualified-artifact-private')
        self.assert_closed(s, copier); self.prepare(s, mana={'C': 3, 'U': 1, 'R': 1}); self.checkpoint(s)
        self.activate_target(s, invalid, 'D'); bad = s.state.stack[-1]
        self.activate_target(s, rod, 'B', reserve={'U': 1, 'R': 1}); original = s.state.stack[-1]; self.priority(s, 'A')
        actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        action = next(a for a in actions if a['id'].startswith('activate:'+copier.ref+':'))
        self.reject(s, 'A', {'action_id': action['id'], 'targets': [bad.ref], 'pay': 'auto'})
        self.cast(s, 'B', remove, [rod.ref]); self.resolve_top(s)
        self.activate_target(s, copier, original.ref); self.choose_targets(s, ['D']); self.assertIsNone(self.resolve(s))
        self.assertEqual([40, 39, 40, 38], [s.state.players[x].life for x in 'ABCD']); self.finish(s, private)

    def test_actual_scientist_pays_life_once_and_retargets_departed_artifact_ability(self):
        session = self.session(871021)
        scientist = self.add(session.engine, "Scientist Supreme of A.I.M.")
        rod = self.add(session.engine, "Rod of Ruin")
        nonartifact = self.add(session.engine, "Prodigal Sorcerer")
        mana_rock = self.add(session.engine, "Sol Ring")
        self.add(session.engine, "Fervor")
        remove = self.add(session.engine, "Disenchant", seat="B", zone="hand")
        self.add(session.engine, "Plains", seat="B")
        self.add(session.engine, "Wastes", seat="B")
        private = self.add(session.engine, "Counterspell", seat="C", zone="hand", ref="scientist-private")
        self.assert_closed(session, scientist)
        self.prepare(session, mana={"C": 3})
        self.checkpoint(session)
        self.activate_target(session, nonartifact, "D")
        wrong_source = session.state.stack[-1]
        self.activate_target(session, rod, "B")
        original = session.state.stack[-1]
        self.priority(session, "A")
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        mana = next(a for a in actions if a["id"].startswith("activate:" + mana_rock.ref + ":"))
        result = session.act("pilot:A", {"action_id": mana["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([wrong_source.ref, original.ref], [item.ref for item in session.state.stack])
        self.cast(session, "B", remove, [rod.ref])
        self.resolve_top(session)
        self.assertEqual("graveyard", session.state.cards[rod.object_id].zone)
        self.priority(session, "A")
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(a for a in actions if a["id"].startswith("activate:" + scientist.ref + ":"))
        self.assertEqual([original.ref], action["target_schema"]["legal_refs"])
        self.reject(session, "A", {"action_id": action["id"], "targets": [wrong_source.ref], "pay": "auto"})
        self.assertEqual(40, session.state.players["A"].life)
        self.activate_target(session, scientist, original.ref)
        self.assertEqual(38, session.state.players["A"].life)
        self.priority(session, "A")
        self.reject(session, "A", {"action_id": action["id"], "targets": [original.ref], "pay": "auto"})
        self.assertEqual(38, session.state.players["A"].life)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        session = self.replay(session, load=True)
        self.choose_targets(session, ["D"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual([38, 39, 40, 38], [session.state.players[seat].life for seat in "ABCD"])
        self.finish(session, private)

    def test_actual_scientist_copies_artifact_trigger_on_own_turn_only(self):
        for active in ("A", "B"):
            with self.subTest(active=active):
                session = self.session(871022 if active == "A" else 871023)
                scientist = self.add(session.engine, "Scientist Supreme of A.I.M.")
                artifact = self.add(session.engine, "Ichor Wellspring", zone="hand")
                self.add(session.engine, "Vedalken Orrery")
                self.assert_closed(session, scientist)
                self.prepare(session, mana={"C": 2})
                session.state.active_player = active
                self.checkpoint(session)
                self.cast(session, "A", artifact)
                self.resolve_top(session)
                original = session.state.stack[-1]
                self.assertEqual("triggered_ability", original.kind)
                self.assertEqual(artifact.object_id, original.source_object_id)
                self.priority(session, "A")
                actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                offers = [a for a in actions if a["id"].startswith("activate:" + scientist.ref + ":")]
                hand = len(session.state.players["A"].zones["hand"])
                if active == "B":
                    self.assertFalse(offers)
                    self.reject(session, "A", {
                        "action_id": "activate:" + scientist.ref + ":ab1",
                        "targets": [original.ref],
                        "pay": "auto",
                    })
                else:
                    self.assertEqual(1, len(offers))
                    self.activate_target(session, scientist, original.ref)
                self.assertIsNone(self.resolve(session))
                copies = int(active == "A")
                self.assertEqual(hand + 1 + copies, len(session.state.players["A"].zones["hand"]))
                self.assertEqual(40 - 2 * copies, session.state.players["A"].life)
                self.replay(session, load=True)

    def test_actual_archaic_excludes_colored_source_and_copies_colorless_damage(self):
        s = self.session(871030); copier = self.add(s.engine, 'Abstruse Archaic'); rod = self.add(s.engine, 'Rod of Ruin')
        colored = self.add(s.engine, 'Prodigal Sorcerer'); self.add(s.engine, 'Fervor')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='qualified-colorless-private')
        self.assert_closed(s, copier); self.prepare(s, mana={'C': 4}); self.checkpoint(s)
        self.activate_target(s, colored, 'B'); invalid = s.state.stack[-1]
        self.activate_target(s, rod, 'B'); original = s.state.stack[-1]; self.priority(s, 'A')
        actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        action = next(a for a in actions if a['id'].startswith('activate:'+copier.ref+':'))
        self.reject(s, 'A', {'action_id': action['id'], 'targets': [invalid.ref], 'pay': 'auto'})
        self.activate_target(s, copier, original.ref)
        self.choose_targets(s, ['D']); self.assertIsNone(self.resolve(s))
        self.assertEqual([40, 38, 40, 39], [s.state.players[x].life for x in 'ABCD']); self.finish(s, private)

    def test_actual_permanent_spell_copy_becomes_token_and_instant_copy_sibling_retains_targets(self):
        s = self.session(871040); copier = self.add(s.engine, 'Lithoform Engine'); creature = self.add(s.engine, 'Krakilin', zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='qualified-permanent-private')
        self.prepare(s, mana={'C': 6, 'G': 2}); self.checkpoint(s); self.priority(s, 'A')
        offered = self.action(s, 'A', creature)
        result = s.act('pilot:A', {'action_id': offered['id'], 'x': 2, 'pay': 'auto', 'reserve': {'C': 4}})
        self.assertTrue(result.ok, result.summary)
        original = s.state.stack[-1]; self.activate_target(s, copier, original.ref); self.assertIsNone(self.resolve(s))
        bears = [c for c in s.state.cards.values() if c.zone == 'battlefield' and c.controller == 'A'
            and c.printed_name == 'Krakilin']
        self.assertEqual(2, len(bears)); self.assertEqual(1, sum(not c.is_card_object for c in bears))
        self.assertEqual([2, 2], [c.counters.get('+1/+1', 0) for c in bears]); self.finish(s, private)
        s = self.session(871041); copier = self.add(s.engine, 'Lithoform Engine')
        spell = self.add(s.engine, 'Shock', zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='qualified-instant-private')
        self.prepare(s, mana={'C': 3, 'R': 1}); self.checkpoint(s); self.cast_for(s, 'A', spell, ['B'], reserve={'C': 3})
        original = s.state.stack[-1]; self.activate_target(s, copier, original.ref)
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        s = self.replay(s, load=True); self.assertTrue(s.act('pilot:A', {'action_id': 'choose'}).ok)
        self.assertIsNone(self.resolve(s)); self.assertEqual(36, s.state.players['B'].life)
        self.assertEqual(1, len(s.engine._current_turn_history('spell_cast'))); self.finish(s, private)

    def test_actual_ability_copy_preserves_announced_x_and_original_source_without_extra_cost(self):
        s = self.session(871050); copier = self.add(s.engine, 'Lithoform Engine')
        source = self.add(s.engine, 'Arashi, the Sky Asunder'); target = self.add(s.engine, 'Vampire Nighthawk', seat='B')
        self.add(s.engine, 'Fervor'); private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='qualified-x-private')
        self.assert_closed(s, source); self.prepare(s, mana={'C': 3, 'G': 1}); self.checkpoint(s)
        self.activate_target(s, source, target.ref, x=1, reserve={'C': 2}); original = s.state.stack[-1]
        self.activate_target(s, copier, original.ref); self.choose_targets(s, [target.ref])
        copied = s.state.stack[-1]
        self.assertEqual((1, source.object_id, original.semantic_key), (copied.x_value, copied.source_object_id, copied.semantic_key))
        self.assertTrue(s.state.cards[source.object_id].tapped); self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        self.assertIsNone(self.resolve(s)); self.assertEqual(2, s.state.cards[target.object_id].marked_damage)
        self.finish(s, private)

    def test_actual_default_targets_keep_departed_incarnation_and_explicit_targets_rebind(self):
        for change in (False, True):
            with self.subTest(change=change):
                s = self.session(871060+change); copier = self.add(s.engine, 'Abstruse Archaic')
                source = self.add(s.engine, 'Rod of Ruin'); target = self.add(s.engine, 'Ashcoat Bear')
                bounce = self.add(s.engine, 'Unsummon', zone='hand')
                private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='qualified-incarnation-private')
                self.prepare(s, mana={'C': 5, 'U': 1, 'G': 1}); self.checkpoint(s)
                self.activate_target(s, source, target.ref, reserve={'U': 1, 'G': 1, 'C': 2}); original = s.state.stack[-1]
                snapshot = deepcopy(original.context['target_snapshots'][target.ref])
                self.activate_target(s, copier, original.ref, reserve={'U': 1, 'G': 1, 'C': 1})
                self.cast_for(s, 'A', bounce, [target.ref], reserve={'G': 1, 'C': 1}); self.resolve_top(s)
                self.cast_for(s, 'A', target); self.resolve_top(s)
                self.assertEqual('semantic.choice', self.resolve(s).kind); s = self.replay(s, load=True)
                result = s.act('pilot:A', {'action_id': 'choose', **({'targets': [{'ref': target.ref}]} if change else {})})
                self.assertTrue(result.ok, result.summary); copied = s.state.stack[-1]
                self.assertEqual(not change, snapshot == copied.context['target_snapshots'][target.ref])
                self.assertIsNone(self.resolve(s)); self.assertEqual(int(change), s.state.cards[target.object_id].marked_damage)
                self.finish(s, private)

    def test_actual_permanent_aura_copy_has_no_new_target_permission(self):
        s = self.session(871070); copier = self.add(s.engine, 'Lithoform Engine')
        aura = self.add(s.engine, 'Holy Strength', zone='hand'); target = self.add(s.engine, 'Grizzly Bears')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='qualified-aura-private')
        self.prepare(s, mana={'C': 4, 'W': 1}); self.checkpoint(s); self.cast_for(s, 'A', aura, [target.ref], reserve={'C': 4})
        original = s.state.stack[-1]; self.activate_target(s, copier, original.ref)
        self.assertIsNone(self.resolve(s))
        auras = [c for c in s.state.cards.values() if c.zone == 'battlefield' and c.printed_name == 'Holy Strength']
        self.assertEqual(2, len(auras)); self.assertEqual(1, sum(not c.is_card_object for c in auras))
        self.assertEqual((4, 6), tuple(s.engine._numeric_stat(target.object_id, field) for field in ('power', 'toughness')))
        self.finish(s, private)

    def test_actual_aura_copy_retains_departed_target_identity_and_both_spells_fail(self):
        s = self.session(871071); copier = self.add(s.engine, 'Lithoform Engine')
        aura = self.add(s.engine, 'Holy Strength', zone='hand'); target = self.add(s.engine, 'Ashcoat Bear')
        bounce = self.add(s.engine, 'Unsummon', zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='qualified-aura-incarnation-private')
        self.prepare(s, mana={'C': 5, 'W': 1, 'U': 1, 'G': 1}); self.checkpoint(s)
        self.cast_for(s, 'A', aura, [target.ref], reserve={'C': 5, 'U': 1, 'G': 1})
        original = s.state.stack[-1]; old = target.logical_object_id
        self.activate_target(s, copier, original.ref, reserve={'C': 1, 'U': 1, 'G': 1})
        self.cast_for(s, 'A', bounce, [target.ref], reserve={'C': 1, 'G': 1}); self.resolve_top(s)
        self.cast_for(s, 'A', target); self.resolve_top(s)
        self.assertNotEqual(old, s.state.cards[target.object_id].logical_object_id)
        self.assertIsNone(self.resolve(s))
        self.assertFalse([c for c in s.state.cards.values() if c.zone == 'battlefield' and c.printed_name == 'Holy Strength'])
        self.assertEqual(2, s.engine._numeric_stat(target.object_id, 'power')); self.finish(s, private)

    def test_opponent_stack_target_rejects_before_cost_and_countered_original_produces_no_copy(self):
        s = self.session(871075); copier = self.add(s.engine, 'Lithoform Engine')
        own = self.add(s.engine, 'Shock', zone='hand'); enemy = self.add(s.engine, 'Shock', seat='B', zone='hand', ref='qualified-enemy-shock')
        counter = self.add(s.engine, 'Counterspell', seat='B', zone='hand', ref='qualified-enemy-counter')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='qualified-countered-private')
        s.state.players['B'].mana_pool.update({'R': 1, 'U': 2})
        self.prepare(s, mana={'C': 3, 'R': 1}); self.checkpoint(s)
        self.cast_for(s, 'B', enemy, ['D'], reserve={'U': 2}); foreign = s.state.stack[-1]
        self.cast_for(s, 'A', own, ['C'], reserve={'C': 3}); original = s.state.stack[-1]; self.priority(s, 'A')
        action = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('activate:'+copier.ref+':') and original.ref in json.dumps(a.get('target_schema') or {}))
        self.reject(s, 'A', {'action_id': action['id'], 'targets': [foreign.ref], 'pay': 'auto'})
        self.assertFalse(s.state.cards[copier.object_id].tapped); self.assertEqual(3, sum(s.state.players['A'].mana_pool.values()))
        self.activate_target(s, copier, original.ref); self.cast_for(s, 'B', counter, [original.ref]); self.resolve_top(s)
        self.assertFalse(any(i.ref == original.ref for i in s.state.stack)); self.assertIsNone(self.resolve(s))
        self.assertEqual([40, 40, 40, 38], [s.state.players[x].life for x in 'ABCD'])
        self.assertFalse([i for i in s.state.stack if i.kind == 'spell_copy']); self.finish(s, private)

    def test_actual_source_type_revalidation_uses_departure_lki_after_source_returns(self):
        s = self.session(871080); copier = self.add(s.engine, "Tawnos, Urza's Apprentice")
        rod = self.add(s.engine, 'Rod of Ruin'); self.add(s.engine, 'Vedalken Orrery')
        change = self.add(s.engine, 'Song of the Dryads', zone='hand'); bounce = self.add(s.engine, 'Boomerang', zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='qualified-lki-private')
        self.prepare(s, mana={'C': 9, 'G': 1, 'U': 3, 'R': 1}); self.checkpoint(s)
        self.activate_target(s, rod, 'B', reserve={'C': 6, 'G': 1, 'U': 3, 'R': 1}); original = s.state.stack[-1]
        self.activate_target(s, copier, original.ref, reserve={'C': 6, 'G': 1, 'U': 2})
        self.cast_for(s, 'A', change, [rod.ref], reserve={'C': 4, 'U': 2}); self.resolve_top(s)
        from quorune.target_source import stack_targeting_source
        self.assertEqual(frozenset({'land'}), stack_targeting_source(s.engine, original).card_types)
        self.cast_for(s, 'A', bounce, [rod.ref], reserve={'C': 4}); self.resolve_top(s)
        self.cast_for(s, 'A', rod); self.resolve_top(s)
        self.assertIn('artifact', s.engine._type_parts(s.engine._effective_card_data(s.state.cards[rod.object_id])['type_line'])[0])
        self.assertEqual(frozenset({'land'}), stack_targeting_source(s.engine, original).card_types)
        self.assertIsNone(self.resolve(s)); self.assertEqual(39, s.state.players['B'].life)
        self.finish(s, private)

    def test_copy_creation_fault_rolls_back_and_same_command_retries(self):
        from quorune.engine import CommanderEngine
        from quorune.errors import GameRuleError
        s = self.session(871090); copier = self.add(s.engine, 'Strionic Resonator')
        source = self.add(s.engine, 'Elvish Visionary', zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='qualified-rollback-private')
        self.prepare(s, mana={'C': 3, 'G': 1}); self.checkpoint(s); self.cast_for(s, 'A', source, reserve={'C': 2}); self.resolve_top(s)
        original = s.state.stack[-1]; self.activate_target(s, copier, original.ref)
        original_copy = CommanderEngine._copy_stack_item; calls = []
        def fault(host, **kwargs):
            result = original_copy(host, **kwargs); calls.append(result.ref)
            raise GameRuleError('qualified copy rollback witness')
        with patch.object(CommanderEngine, '_copy_stack_item', fault):
            for _ in range(12):
                before = authoritative_state_hash(s.state); actor = s.pending_principals()[0]
                result = s.act(actor, {'action_id': 'pass'})
                if not result.ok:
                    self.assertEqual(before, authoritative_state_hash(s.state)); break
            else: self.fail('The copy mutation fault did not reach the command boundary')
        self.assertTrue(calls); self.assertTrue(s.state.cards[copier.object_id].tapped)
        self.assertTrue(s.act(actor, {'action_id': 'pass'}).ok); self.assertIsNone(self.resolve(s))
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values())); self.finish(s, private)

    def test_fresh_source_qualification_omission_mutant_is_killed(self):
        import quorune.target_predicates as predicates
        original = predicates.target_predicate_matches
        def omitted(host, group, row, **kwargs):
            if group.predicate == 'colorless_ability_source': return True
            return original(host, group, row, **kwargs)
        with patch('quorune.selection.targeting.target_predicate_matches', omitted):
            with self.assertRaises(AssertionError):
                self.test_actual_archaic_excludes_colored_source_and_copies_colorless_damage()



class PeregrineDynamoNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = recipients.TargetRecipientNativeTests.reject
    activate_target = QualifiedStackCopyNativeTests.activate_target
    choose_targets = QualifiedStackCopyNativeTests.choose_targets

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "peregrine-dynamo.sqlite3"
        build_fixture_database([*FIXTURES, ROOT / "tests/fixtures/peregrine-dynamo-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Dynamo", [
            DeckEntry("Sliver Hivelord", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Sliver Hivelord"])

    def offers(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return tuple(row for row in actions if row["id"].startswith("activate:" + source.ref + ":"))

    def test_dynamo_retargets_a_copy_of_a_departed_legendary_source_ability(self):
        game = self.session(11570710)
        dynamo = self.add(game.engine, "The Peregrine Dynamo")
        legend = self.add(game.engine, "Tawnos, Urza's Apprentice")
        first = self.add(game.engine, "Rod of Ruin", ref="dynamo-first-rod")
        second = self.add(game.engine, "Rod of Ruin", ref="dynamo-second-rod")
        removal = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        private = self.add(game.engine, "Counterspell", seat="D", zone="hand", ref="dynamo-private-unrelated")
        self.add(game.engine, "Fervor")
        self.assert_closed(game, dynamo)
        self.assert_closed(game, legend)
        self.prepare(game, mana={"C": 7, "U": 1, "R": 1})
        game.state.config.semantic_policy = "trusted_only"
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.activate_target(game, first, "B", reserve={"U": 1, "R": 1})
        first_ability = game.state.stack[-1]
        self.activate_target(game, second, "C", reserve={"U": 1, "R": 1})
        second_ability = game.state.stack[-1]
        self.activate_target(game, legend, first_ability.ref)
        original = game.state.stack[-1]
        (offer,) = self.offers(game, dynamo)
        self.assertEqual([original.ref], offer["target_schema"]["legal_refs"])
        self.reject(game, "A", {"action_id": offer["id"], "targets": [first_ability.ref], "pay": "auto"})
        self.activate_target(game, dynamo, original.ref)
        self.assertTrue(game.state.cards[dynamo.object_id].tapped)
        self.cast(game, "B", removal, [legend.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[legend.object_id].zone)
        decision = self.resolve(game)
        self.assertIsNotNone(decision)
        self.assertEqual("semantic.choice", decision.kind)
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        game = self.replay(game, load=True)
        self.choose_targets(game, [second_ability.ref])
        original, copied = game.state.stack[-2:]
        self.assertEqual(legend.object_id, copied.source_object_id)
        self.assertEqual([first_ability.ref], original.targets)
        self.assertEqual([second_ability.ref], copied.targets)
        self.choose_targets(game, ["D"])
        self.choose_targets(game, ["D"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40, 39, 39, 38], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_dynamo_excludes_its_own_source_and_designated_commander(self):
        game = self.session(11570711)
        dynamo = self.add(game.engine, "The Peregrine Dynamo")
        legend = self.add(game.engine, "Tawnos, Urza's Apprentice")
        rod = self.add(game.engine, "Rod of Ruin")
        untap = self.add(game.engine, "Twiddle", seat="B", zone="hand")
        self.add(game.engine, "Fervor")
        self.prepare(game, mana={"C": 5, "U": 1, "R": 1})
        game.state.players["B"].mana_pool["U"] = 1
        game.state.config.semantic_policy = "trusted_only"
        self.checkpoint(game)
        self.activate_target(game, rod, "B", reserve={"U": 1, "R": 1})
        self.activate_target(game, legend, game.state.stack[-1].ref)
        legend_ability = game.state.stack[-1].ref
        self.activate_target(game, dynamo, legend_ability)
        own_ability = game.state.stack[-1].ref
        self.cast(game, "B", untap, [dynamo.ref])
        decision = self.resolve(game)
        self.assertEqual("semantic.choice", decision.kind)
        result = game.act("pilot:B", {"action_id": "choose", "choice": "untap"})
        self.assertTrue(result.ok, result.summary)
        (offer,) = self.offers(game, dynamo)
        self.assertEqual([legend_ability], offer["target_schema"]["legal_refs"])
        self.reject(game, "A", {"action_id": offer["id"], "targets": [own_ability], "pay": "auto"})
        self.assertFalse(game.state.cards[dynamo.object_id].tapped)
        self.replay(game, load=True)

        self.deck = DeckDefinition("Commander source", [
            DeckEntry("Tawnos, Urza's Apprentice", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Tawnos, Urza's Apprentice"])
        game = self.session(11570712)
        dynamo = self.add(game.engine, "The Peregrine Dynamo")
        rod = self.add(game.engine, "Rod of Ruin")
        self.add(game.engine, "Fervor")
        (legend,) = (game.state.cards[oid] for oid in game.state.players["A"].zones["command"]
                     if game.state.cards[oid].is_commander)
        game.engine.move_card(legend.object_id, "battlefield", reason="Prepare commander source", log=False)
        self.prepare(game, mana={"C": 4, "U": 1, "R": 1})
        game.state.config.semantic_policy = "trusted_only"
        self.checkpoint(game)
        self.activate_target(game, rod, "B", reserve={"U": 1, "R": 1})
        self.activate_target(game, legend, game.state.stack[-1].ref)
        self.assertFalse(self.offers(game, dynamo))
        self.reject(game, "A", {"action_id": "activate:" + dynamo.ref + ":ab2",
                                "targets": [game.state.stack[-1].ref], "pay": "auto"})
        self.replay(game, load=True)

    def test_dynamo_copies_a_legendary_cycling_ability_but_legacy_source_cannot_supply_commander_facts(self):
        from quorune.target_source import TARGETING_SOURCE_CONTEXT

        for version in (3, 2):
            with self.subTest(version=version):
                game = self.session(11570715 + version)
                dynamo = self.add(game.engine, "The Peregrine Dynamo")
                cycling = self.add(game.engine, "Ozolith, the Shattered Spire", zone="hand")
                private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="dynamo-private-cycle")
                self.assert_closed(game, dynamo)
                self.assert_closed(game, cycling)
                self.prepare(game, mana={"C": 3})
                game.state.ability_source_information_version = version
                game.state.config.semantic_policy = "trusted_only"
                self.checkpoint(game)
                (offer,) = self.offers(game, cycling)
                paid = game.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
                self.assertTrue(paid.ok, paid.summary)
                self.assertEqual("graveyard", game.state.cards[cycling.object_id].zone)
                original = game.state.stack[-1]
                binding = original.context[TARGETING_SOURCE_CONTEXT]
                self.assertEqual("hand", binding["zone"])
                self.assertIs(False if version == 3 else None, binding.get("is_commander"))
                hand = len(game.state.players["A"].zones["hand"])
                if version == 3:
                    self.activate_target(game, dynamo, original.ref)
                else:
                    self.assertFalse(self.offers(game, dynamo))
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(hand + (2 if version == 3 else 1), len(game.state.players["A"].zones["hand"]))
                self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
                self.replay(game, load=True)

    def test_dynamo_exact_predicate_and_binding_require_complete_facts(self):
        from quorune.ability_source_information import DIFFERENT_LEGENDARY_NONCOMMANDER_SOURCE_CAPABILITY as CAP
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.target_source import TargetingSource, TargetingSourceBinding

        game = self.session(11570720)
        source = self.add(game.engine, "The Peregrine Dynamo")
        self.assert_closed(game, source)
        (program,) = game.engine.semantics.programs_for_oracle(source.oracle_id, event="activate")
        deps = tuple(value for value in program.capability_dependencies if value != CAP)
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile="commander_review").to_dict())
        binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + CAP, binding["blockers"])
        group = target_plan(program.target_schema).groups[0]
        original = TargetingSourceBinding("subject", "ref", "subject-incarnation", "hand", "A",
                                          "Legendary Artifact", (), is_commander=False)
        self.assertEqual(original, TargetingSourceBinding.from_dict(original.to_dict()))
        for malformed in (None, 0, "False"):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                TargetingSourceBinding.from_dict({**original.to_dict(), "is_commander": malformed})
        for kind in ("activated_ability", "triggered_ability"):
            item = StackItem(stack_id="subject", ref="S-subject", kind=kind, controller="A", label="Source")
            facts = original.facts()
            for current, expected in (
                (facts, True), (replace(facts, supertypes=frozenset()), False),
                (replace(facts, is_commander=True), False), (replace(facts, is_commander=None), False),
                (replace(facts, logical_object_id="acting"), False), (TargetingSource("ability"), False),
            ):
                row = {"zone": "stack", "category": "ability", "stack_item": item,
                       "ability_source_types": current.card_types, "ability_source_identity": current}
                self.assertEqual(expected, target_predicate_matches(
                    SimpleNamespace(), group, row, types=set(), supertypes=set(), colors=set(),
                    derived={}, source_incarnation="acting",
                ))


class PowerAdjustedStackTargetTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)
        fixture = path / "stack-target-cards.json"
        needed = {"Sliver Hivelord", "Generic Bound Plains", "Counterspell", "Grizzly Bears",
                  "Lightning Bolt", "Wyll's Reversal"}
        cards, rulings = {}, []
        for name in ("fixed-qualified-stack-copy-cards.json", "event-spell-copy-cards.json"):
            data = json.loads((ROOT / "tests/fixtures" / name).read_text(encoding="utf-8"))
            for row in data["cards"]:
                if row["name"] in needed:
                    cards[row["oracle_id"]] = row
            rulings.extend(data["rulings"])
        fixture.write_text(json.dumps({**data, "cards": list(cards.values()), "rulings": [
            row for row in rulings if row["oracle_id"] in cards
        ]}), encoding="utf-8")
        database = path / "stack-targets.sqlite3"
        build_fixture_database([fixture], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Power-adjusted stack targets", [
            DeckEntry("Sliver Hivelord", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Sliver Hivelord"])

    def test_power_adjusted_table_requires_the_complete_target_and_two_result_rows(self):
        from quorune.stack_target_change_model import matches_announced_stack_target

        card = self.db.lookup("Wyll's Reversal", fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        admitted = compile_oracle_card(card, **options)
        self.assertEqual("exact", admitted.status, admitted.material_residuals)
        node, = admitted.faces[0].nodes
        self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
        self.assertIn("random.die.power_adjusted_stack_targets", node.capability_dependencies)
        for old, new in (("one or more targets", "a single target"), ("15+", "16+"), ("d20", "d12")):
            with self.subTest(variant=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                self.assertTrue(compile_oracle_card(changed, **options).material_residuals)

        self.assertFalse(matches_announced_stack_target(SimpleNamespace(kind="spell", targets=[])))
        self.assertTrue(matches_announced_stack_target(SimpleNamespace(kind="activated_ability", targets=["A"])))
        with patch("quorune.compiler.die_table_spell_faces.compile_die_table_spell_face", return_value=None):
            self.assertTrue(compile_oracle_card(card, **options).material_residuals)

    def test_power_adjusted_reselection_and_copy_use_the_ordered_native_choices_and_replay(self):
        from random import Random

        for expected in (14, 15):
            with self.subTest(result=expected):
                seed = next(n for n in range(894200, 894450)
                            if Random(f"{n}|S2|0|die-roll|A").randint(1, 20) == expected - 2)
                game = self.session(seed)
                self.add(game.engine, "Grizzly Bears")
                reversal = self.add(game.engine, "Wyll's Reversal", zone="hand")
                bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
                private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="private-witness")
                self.assert_closed(game, reversal)
                self.prepare(game, mana={"C": 2, "R": 1})
                game.state.players["B"].mana_pool["R"] = 1
                self.checkpoint(game)
                self.cast_for(game, "B", bolt, ["C"])
                original = game.state.stack[-1]
                self.cast_for(game, "A", reversal, [original.ref])
                self.assertEqual("S2", game.state.stack[-1].ref)
                self.assertEqual("semantic.choice", self.resolve(game).kind)
                self.assertEqual(["pilot:A"], game.pending_principals())
                roll, = [event for event in game.state.events if event.code == "die.roll"]
                self.assertEqual((expected - 2, 2, expected), (
                    roll.details["natural_result"], roll.details["modifier"], roll.details["result"],
                ))
                for viewer in ("B", "C", "D"):
                    self.assertIsNone(game.packet(f"pilot:{viewer}", full=True)["decision"])
                before = authoritative_state_hash(game.state)
                count = len(game.commands)
                for principal, targets in (("pilot:B", ["D"]), ("pilot:A", ["unoffered-target"])):
                    self.assertFalse(game.act(principal, {"action_id": "choose", "targets": targets}).ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                    self.assertEqual(count, len(game.commands))

                game = self.replay(game, load=True)
                command = {"action_id": "choose", "targets": ["D"]} if expected == 15 else {"action_id": "choose"}
                outcome = game.act("pilot:A", command)
                self.assertTrue(outcome.ok, outcome.summary)
                if expected == 15:
                    self.assertEqual("semantic.choice", game.state.pending_decision.kind)
                    game = self.replay(game, load=True)
                    outcome = game.act("pilot:A", {"action_id": "choose"})
                    self.assertTrue(outcome.ok, outcome.summary)
                    retained, copied = game.state.stack
                    self.assertEqual((original.ref, "B", ["D"]), (retained.ref, retained.controller, retained.targets))
                    self.assertEqual(("spell_copy", "A", ["D"]), (copied.kind, copied.controller, copied.targets))
                else:
                    retained, = game.state.stack
                    self.assertEqual((original.ref, ["C"]), (retained.ref, retained.targets))
                self.assertIsNone(self.resolve(game))
                self.assertEqual((40, 34) if expected == 15 else (37, 40),
                                 (game.state.players["C"].life, game.state.players["D"].life))
                self.assertEqual(1, sum(event.code == "die.roll" for event in game.state.events))
                recipients.TargetRecipientNativeTests.finish(self, game, private)
