"""Independent CR205.4c/700.9/702.5 expectations for state-qualified Enchant."""
from dataclasses import FrozenInstanceError
import json
import unittest

from quorune.enchant_public_state_model import EnchantPublicStateKind as Kind, EnchantPublicStateSpec as Spec
from quorune.compiler.enchant_public_state_templates import parse_enchant_public_state_requirement as parse
from quorune.object_predicate import PermanentStatePredicateSpec, permanent_state_predicate_matches
from quorune.targets import TargetGroup


class EnchantPublicStateContractTests(unittest.TestCase):
    def test_exact_immutable_qualifier_codec_rejects_other_kinds_versions_and_unowned_combinations(self):
        for kind in Kind:
            spec = Spec(kind); self.assertEqual(spec, Spec.from_dict(json.loads(json.dumps(spec.to_dict()))))
            with self.assertRaises(FrozenInstanceError):spec.kind = Kind.NONBASIC_LAND
            for value in (True, False, 0, 2, '1'):
                with self.assertRaises(ValueError):Spec.from_dict(spec.to_dict() | {'schema_version': value})
            for extra in ({'kind': 'enchanted_creature'}, {'kind': 'another_aura'}, {'controller': 'you'},
                    {'optional': True}, {'count': 2}, {'types_all': ['creature', 'land']}):
                with self.assertRaises(ValueError):Spec.from_dict(spec.to_dict() | extra)
        with self.assertRaises(ValueError):Spec('modified_creature')

    def test_nonbasic_is_supertype_exclusion_and_basic_land_types_never_substitute_for_basic(self):
        fields = Spec(Kind.NONBASIC_LAND).target_fields()
        self.assertEqual({'types_all': ['land'], 'supertypes_none': ['basic']}, fields)
        group = TargetGroup.from_mapping({'zones': ['battlefield'], 'categories': ['permanent'], 'count': 1, **fields})
        for types, subtypes, supertypes, expected in (
                (('land',), ('forest',), (), True),
                (('land',), ('mountain',), ('legendary',), True),
                (('land',), (), ('basic',), False),
                (('land',), ('forest',), ('basic', 'snow'), False),
                (('artifact',), (), (), False)):
            self.assertEqual(expected, group.matches_type_characteristics(types=types, subtypes=subtypes, supertypes=supertypes))

    def test_modified_delegates_to_the_existing_current_public_state_owner(self):
        fields = Spec(Kind.MODIFIED_CREATURE).target_fields()
        self.assertEqual(['creature'], fields['types_all'])
        state = PermanentStatePredicateSpec.from_dict(fields['state_predicate'])
        self.assertEqual(PermanentStatePredicateSpec(modified=True), state)
        group = TargetGroup.from_mapping({'zones': ['battlefield'], 'categories': ['permanent'], 'count': 1, **fields})
        self.assertEqual(state, group.state_predicate)
        for modified in (False, True):
            self.assertEqual(modified, permanent_state_predicate_matches(state, counters={}, entered_this_turn=False,
                tapped=False, modified=modified))
        with self.assertRaises(ValueError):permanent_state_predicate_matches(state, counters={},
            entered_this_turn=False, tapped=False, modified=1)

    def test_exact_two_grammars_preserve_the_modified_reminder_and_exclude_other_aura_or_material_riders(self):
        reminder = '(Equipment, Auras its controller controls, and counters are modifications.)'
        for text, kind in (('Enchant nonbasic land', Kind.NONBASIC_LAND),
                ('Enchant modified creature', Kind.MODIFIED_CREATURE),
                ('Enchant modified creature ' + reminder, Kind.MODIFIED_CREATURE)):
            self.assertEqual(Spec(kind), parse(text))
        for text in ('Enchant creature with two other Auras attached to it', 'Enchant nonbasic land you control',
                'Enchant modified permanent', 'Enchant unmodified creature', 'Enchant modified creature and land',
                'Enchant modified creature (Only counters are modifications.)',
                'Enchant modified creature. Draw a card.', 'Enchant nonbasic land. Destroy it.'):
            self.assertIsNone(parse(text))


class EnchantPublicStateIntegrationTests(unittest.TestCase):
    def test_fresh_qualification_compiler_omission_is_killed_after_positive_version_four_codec(self):
        from unittest.mock import patch
        import quorune.compiler.enchant_public_state_templates as owner
        self.test_additive_version_four_codecs_close_state_domain_without_reinterpreting_two_or_three()
        with patch.object(owner, 'parse_enchant_public_state_requirement', return_value=None):
            with self.assertRaises(AssertionError):
                self.test_additive_version_four_codecs_close_state_domain_without_reinterpreting_two_or_three()

    def test_additive_version_four_codecs_close_state_domain_without_reinterpreting_two_or_three(self):
        from dataclasses import replace
        from quorune.aura import parse_enchant_line
        from quorune.enchant_spec import (TypedEnchantSpec, AuraEnchantSubject, AuraControllerRelation,
            enchant_spec_to_dict, enchant_spec_from_dict)
        from quorune.ability_fragments import ability_fragment_to_dict, ability_fragment_from_dict
        from quorune.target_forms import TargetCharacteristicForm
        for text, kind in (('Enchant modified creature', Kind.MODIFIED_CREATURE), ('Enchant nonbasic land', Kind.NONBASIC_LAND)):
            spec = parse_enchant_line(text); self.assertIsNotNone(spec); self.assertEqual(4, spec.schema_version)
            self.assertEqual(Spec(kind), spec.public_state)
            self.assertEqual(spec, TypedEnchantSpec.from_dict(json.loads(json.dumps(spec.to_dict()))))
            self.assertEqual(spec, enchant_spec_from_dict(enchant_spec_to_dict(spec)))
            self.assertEqual(spec, ability_fragment_from_dict(ability_fragment_to_dict(spec)))
            from quorune.compiler.ability_keyword_fragments import _lower_enchant_fragment
            from quorune.semantic_runtime.components import validate_runtime_handler_descriptors
            lowered = _lower_enchant_fragment(text)
            self.assertEqual('ability.static.enchant.typed.v2', lowered.handlers[0]['handler_id'])
            validate_runtime_handler_descriptors(lowered.handlers)
            for key, value in Spec(kind).target_fields().items():self.assertEqual(value, spec.target_schema()[key])
            for change in ({'schema_version': 2}, {'schema_version': 3}, {'schema_version': True},
                    {'public_state': {'kind': 'another_aura', 'schema_version': 1}}, {'public_state': None},
                    {'public_state': Spec(kind).to_dict() | {'count': 2}}, {'numeric_characteristic': None}):
                with self.assertRaises(ValueError):TypedEnchantSpec.from_dict(spec.to_dict() | change)
            for change in ({'controller_relation': AuraControllerRelation.YOU}, {'types_any': ('artifact',)},
                    {'types_all': ('creature', 'land')}, {'mana_value_max': 2}, {'keywords_none': ('flying',)},
                    {'subject': AuraEnchantSubject.PLAYER}):
                with self.assertRaises(ValueError):replace(spec, **change)
        legacy = parse_enchant_line('Enchant blue creature')
        self.assertEqual(2, legacy.schema_version); self.assertNotIn('public_state', legacy.to_dict())
        self.assertEqual('47c6810b12af136853e5b62e6afdc2494573b0cf2a46146ae98025f5e4432aea', legacy.fingerprint)
        fixed = parse_enchant_line('Enchant creature without flying')
        self.assertEqual(3, fixed.schema_version); self.assertNotIn('public_state', fixed.to_dict())
        old_form = TypedEnchantSpec(subject=AuraEnchantSubject.PERMANENT,
            characteristic_forms_any=(TargetCharacteristicForm(types_all=('land',), supertypes_none=('basic',)),))
        self.assertEqual(2, old_form.schema_version)
        self.assertEqual(old_form, TypedEnchantSpec.from_dict(old_form.to_dict()))
        for old in (legacy, fixed, old_form):
            with self.assertRaises(ValueError):replace(old, public_state=Spec(Kind.MODIFIED_CREATURE))

    def test_raw_fragment_minimum_uses_its_actual_qualified_version_without_coverage_and_preserves_legacy_forms(self):
        from quorune.aura import parse_enchant_line
        from quorune.enchant_spec import TypedEnchantSpec, AuraEnchantSubject
        from quorune.target_forms import TargetCharacteristicForm
        from quorune.ability_fragments import ability_fragment_to_dict
        from quorune.rules.enchant_public_state import public_state_enchant_spec_dependencies, public_state_enchant_descriptor_dependencies
        from quorune.rules.keyword_runtime_requirements import _fragment_descriptor_requirements
        from quorune.enchant_public_state_model import ENCHANT_PUBLIC_STATE_CAPABILITY as cap
        for text, modified in (('Enchant modified creature', True), ('Enchant nonbasic land', False)):
            spec = parse_enchant_line(text); raw = ability_fragment_to_dict(spec)
            required = set(public_state_enchant_descriptor_dependencies(raw))
            self.assertTrue({cap, 'attachment.aura.typed_restriction', 'target.revalidate_resolution'} <= required)
            self.assertEqual(modified, 'state_query.permanent.public_state_predicate' in required)
            self.assertTrue(required <= _fragment_descriptor_requirements({'handler': {'fragment': raw}}))
            with self.assertRaises(ValueError):public_state_enchant_descriptor_dependencies(raw | {'unowned_handler': True})
        old_form = TypedEnchantSpec(subject=AuraEnchantSubject.PERMANENT,
            characteristic_forms_any=(TargetCharacteristicForm(types_all=('land',), supertypes_none=('basic',)),))
        self.assertEqual((), public_state_enchant_spec_dependencies(old_form))
        self.assertNotIn(cap, _fragment_descriptor_requirements(ability_fragment_to_dict(old_form)))

    def test_target_bridge_reads_the_canonical_current_attachment_graph_with_controller_relative_modified(self):
        from types import SimpleNamespace
        from quorune.model import CardInstance
        from quorune.characteristic_evaluation_host import CharacteristicEvaluationHostMixin
        from quorune.characteristic_evaluation import type_parts
        from quorune.selection.targeting import _target_group_attachment_state, _target_group_public_state_matches
        target = CardInstance('target', 'target', 'fixture-target', 'Creature', 'A', 'B', 'battlefield')
        aura = CardInstance('aura', 'aura', 'fixture-aura', 'Aura', 'B', 'A', 'battlefield', attached_to=target.object_id)
        equipment = CardInstance('equipment', 'equipment', 'fixture-equipment', 'Equipment', 'A', 'C', 'battlefield', attached_to=target.object_id)
        data = {'aura': {'type_line': 'Enchantment — Aura'}, 'equipment': {'type_line': 'Artifact — Equipment'}}
        host = SimpleNamespace(state=SimpleNamespace(cards={c.object_id: c for c in (target, aura, equipment)}),
            _effective_card_data=lambda card, **kw:data[card.object_id], _type_parts=type_parts)
        host._attachment_public_state = lambda card:CharacteristicEvaluationHostMixin._attachment_public_state(host, card)
        group = TargetGroup.from_mapping({'zones': ['battlefield'], 'categories': ['permanent'], 'count': 1,
            **Spec(Kind.MODIFIED_CREATURE).target_fields()})
        def permits():
            return _target_group_public_state_matches(group, card=target, turn_sequence=1,
                attachment_state=_target_group_attachment_state(host, group, target))
        target.attachments = [aura.object_id]; self.assertFalse(permits())
        aura.controller = 'B'; self.assertTrue(permits())
        aura.controller = 'A'; target.counters['stun'] = 1; self.assertTrue(permits())
        target.counters.clear(); target.attachments.append(equipment.object_id); self.assertTrue(permits())
        equipment.phased_out = True; self.assertFalse(permits())
        equipment.phased_out = False; equipment.attached_to = None; self.assertFalse(permits())
        aura.controller = 'B'; self.assertTrue(permits())
        aura.zone = 'graveyard'; self.assertFalse(permits())
        legacy = TargetGroup.from_mapping({'zones': ['battlefield'], 'categories': ['permanent'], 'count': 1,
            'state_predicate': PermanentStatePredicateSpec(tapped=True).to_dict()})
        self.assertEqual({}, _target_group_attachment_state(SimpleNamespace(), legacy, target))


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_life_loss_recovery as recovery_helpers


class EnchantPublicStateNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = recovery_helpers.TurnLifeLossRecoveryNativeTests.start
    reject = recovery_helpers.TurnLifeLossRecoveryNativeTests.reject
    finish = recovery_helpers.TurnLifeLossRecoveryNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); folder = Path(cls.temporary.name)
        base = json.loads((ROOT / 'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [c for c in base['cards'] if c['name'] == 'Generic Bound Plains']; base['rulings'] = []
        basic = folder / 'basic.json'; basic.write_text(json.dumps(base), encoding='utf-8')
        build_fixture_database([basic, ROOT / 'tests/fixtures/enchant-public-state-cards.json'], folder / 'state-enchant.sqlite3')
        cls.db = CardDatabase(folder / 'state-enchant.sqlite3'); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Current public-state Enchant', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def test_complete_two_originals_and_all_material_siblings_admit_with_independent_state_owner_minima(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.oracle_ir import compile_oracle_card
        from quorune.enchant_public_state_model import ENCHANT_PUBLIC_STATE_CAPABILITY as cap
        s, _ = self.start(736000)
        for name in ('Lion Umbra', 'Uncontrolled Infestation'):
            card = self.add(s.engine, name, zone='hand'); self.assert_closed(s, card)
            self.assertEqual('exact', compile_oracle_card(self.db.lookup(name), capability_registry=self.registry,
                capability_profile='commander_review').status)
            program = next(p for p in s.engine.semantics.programs_for_oracle(card.oracle_id) if cap in p.capability_dependencies)
            required = (cap, 'attachment.aura.typed_restriction', 'target.revalidate_resolution',
                *(('state_query.permanent.public_state_predicate',) if name == 'Lion Umbra' else ()))
            for missing in required:
                deps = tuple(c for c in program.capability_dependencies if c != missing)
                changed = replace(program, coverage=(), capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(bound['strict'], (name, missing))
                self.assertIn('capability:undeclared_runtime_dependency:' + missing, bound['blockers'])
        for name in ("Runner's Bane", 'Roots', 'Threads of Disloyalty'):
            self.assert_closed(s, self.add(s.engine, name, zone='hand'))
        original = self.db.lookup('Daybreak Coronet')
        two_auras = replace(original, oracle_text=original.oracle_text.replace(
            'another Aura attached', 'two other Auras attached'))
        self.assertNotEqual('exact', compile_oracle_card(two_auras, capability_registry=self.registry,
            capability_profile='commander_review').status)

    def test_actual_lion_printed_gg_cast_requires_prior_modification_then_attaches_with_stats_keywords_and_private_replay(self):
        s, private = self.start(736001); aura = self.add(s.engine, 'Lion Umbra', zone='hand')
        own = self.add(s.engine, 'Grizzly Bears', ref='lion-own-creature')
        foreign = self.add(s.engine, 'Grizzly Bears', seat='B', ref='lion-unmodified-control')
        growth = self.add(s.engine, 'Battlegrowth', zone='hand')
        for card in (aura, own, foreign, growth):self.assert_closed(s, card)
        self.prepare(s, mana={'G': 3}); self.checkpoint(s); self.priority(s, 'A')
        actions = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(a['id'].startswith('cast:' + aura.ref) for a in actions))
        self.reject(s, 'A', {'action_id': 'cast:' + aura.ref, 'targets': [own.ref], 'pay': 'auto'})
        self.cast(s, 'A', growth, [own.ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual(1, s.state.cards[own.object_id].counters['+1/+1']); before = s.state.players['A'].mana_pool['G']
        self.priority(s, 'A'); offered = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('cast:' + aura.ref))
        self.reject(s, 'A', {'action_id': offered['id'], 'targets': [foreign.ref], 'pay': 'auto'})
        self.reject(s, 'D', {'action_id': offered['id'], 'targets': [own.ref], 'pay': 'auto'})
        self.cast(s, 'A', aura, [own.ref]); self.assertEqual(before - 2, s.state.players['A'].mana_pool['G'])
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual(own.object_id, s.state.cards[aura.object_id].attached_to)
        self.assertEqual((6, 6), tuple(s.engine._numeric_stat(own.object_id, field) for field in ('power', 'toughness')))
        self.assertTrue({'vigilance', 'reach'} <= s.engine._combat_keywords(s.state.cards[own.object_id]))
        self.finish(s, private)

    def test_actual_infestation_printed_one_red_cast_rejects_basic_but_destroys_nonbasic_basic_land_type_after_actual_mana_tap(self):
        s, private = self.start(736002); aura = self.add(s.engine, 'Uncontrolled Infestation', zone='hand')
        land = self.add(s.engine, 'Taiga', seat='B', ref='infestation-nonbasic-forest-mountain')
        basic = self.add(s.engine, 'Forest', ref='infestation-basic-control')
        body = self.add(s.engine, 'Grizzly Bears', seat='B'); growth = self.add(s.engine, 'Giant Growth', seat='B', zone='hand')
        for card in (aura, land, basic, body, growth):self.assert_closed(s, card)
        self.prepare(s, mana={'C': 1, 'R': 1}); self.checkpoint(s); self.priority(s, 'A')
        offered = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('cast:' + aura.ref))
        self.reject(s, 'A', {'action_id': offered['id'], 'targets': [basic.ref], 'pay': 'auto'})
        before = sum(s.state.players['A'].mana_pool.values()); self.cast(s, 'A', aura, [land.ref])
        self.assertEqual(before - 2, sum(s.state.players['A'].mana_pool.values())); self.assertIsNone(self.resolve(s))
        self.assertEqual(land.object_id, s.state.cards[aura.object_id].attached_to); self.assertEqual(0, self.trigger_count(s, aura))
        self.assertFalse(s.state.cards[land.object_id].tapped); self.cast(s, 'B', growth, [body.ref])
        self.assertTrue(s.state.cards[land.object_id].tapped); self.assertEqual(1, self.trigger_count(s, aura))
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[land.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[aura.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[basic.object_id].zone)
        self.assertEqual((5, 5), tuple(s.engine._numeric_stat(body.object_id, field) for field in ('power', 'toughness')))
        self.finish(s, private)


    def activate_material(self, s, source, targets, *, seat='B'):
        self.priority(s, seat); current = s.state.cards[source.object_id]
        action = next(a for a in s.packet('pilot:' + seat, full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('activate:' + current.ref + ':'))
        command = {'action_id': action['id'], 'targets': list(targets), 'pay': 'auto'}
        self.reject(s, 'D' if seat != 'D' else 'C', command)
        result = s.act('pilot:' + seat, command); self.assertTrue(result.ok, result.summary)

    def test_actual_last_counter_removal_keeps_own_lion_modified_but_foreign_controlled_aura_falls_off(self):
        for index, seat in enumerate(('A', 'B')):
            with self.subTest(creature_controller=seat):
                s, private = self.start(736010 + index); aura = self.add(s.engine, 'Lion Umbra', zone='hand')
                body = self.add(s.engine, 'Grizzly Bears', seat=seat); body.counters['+1/+1'] = 1
                hexmage = self.add(s.engine, 'Vampire Hexmage', seat='B')
                for card in (aura, body, hexmage):self.assert_closed(s, card)
                self.prepare(s, mana={'G': 2}); self.checkpoint(s); self.cast(s, 'A', aura, [body.ref]); self.assertIsNone(self.resolve(s))
                self.activate_material(s, hexmage, (body.ref,)); self.assertEqual('graveyard', s.state.cards[hexmage.object_id].zone)
                s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
                self.assertFalse(s.state.cards[body.object_id].counters)
                self.assertEqual('battlefield' if seat == 'A' else 'graveyard', s.state.cards[aura.object_id].zone)
                self.assertEqual(body.object_id if seat == 'A' else None, s.state.cards[aura.object_id].attached_to)
                self.assertEqual((5, 5) if seat == 'A' else (2, 2),
                    tuple(s.engine._numeric_stat(body.object_id, field) for field in ('power', 'toughness')))
                self.finish(s, private)

    def test_actual_last_modification_removed_in_response_cannot_be_bootstrapped_by_unresolved_lion(self):
        s, private = self.start(736012); aura = self.add(s.engine, 'Lion Umbra', zone='hand')
        body = self.add(s.engine, 'Grizzly Bears'); body.counters['stun'] = 1
        hexmage = self.add(s.engine, 'Vampire Hexmage', seat='B')
        for card in (aura, body, hexmage):self.assert_closed(s, card)
        self.prepare(s, mana={'G': 2}); self.checkpoint(s); self.cast(s, 'A', aura, [body.ref])
        self.activate_material(s, hexmage, (body.ref,)); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[aura.object_id].zone); self.assertIsNone(s.state.cards[aura.object_id].attached_to)
        self.assertFalse(s.state.cards[body.object_id].counters)
        self.assertEqual((2, 2), tuple(s.engine._numeric_stat(body.object_id, field) for field in ('power', 'toughness')))
        self.assertEqual(0, s.state.players['A'].mana_pool['G']); self.finish(s, private)

    def test_actual_foreign_equipment_modifies_opponent_creature_until_equipment_is_destroyed_then_wrong_controller_lion_falls(self):
        from quorune.attachments import attach_objects
        s, private = self.start(736013); aura = self.add(s.engine, 'Lion Umbra', zone='hand')
        body = self.add(s.engine, 'Grizzly Bears', seat='B'); equipment = self.add(s.engine, 'Bonesplitter')
        removal = self.add(s.engine, 'Disenchant', seat='B', zone='hand')
        for card in (aura, body, equipment, removal):self.assert_closed(s, card)
        attach_objects(s.state.cards, equipment, body, source_timestamp=s.engine._next_zone_timestamp(), players=s.state.players)
        s.state.players['B'].mana_pool.update({'C': 1, 'W': 1})
        self.prepare(s, mana={'G': 2}); self.checkpoint(s); self.cast(s, 'A', aura, [body.ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual(body.object_id, s.state.cards[aura.object_id].attached_to)
        self.assertEqual((7, 5), tuple(s.engine._numeric_stat(body.object_id, field) for field in ('power', 'toughness')))
        self.cast(s, 'B', removal, [equipment.ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[equipment.object_id].zone)
        self.assertEqual('graveyard', s.state.cards[aura.object_id].zone)
        self.assertEqual((2, 2), tuple(s.engine._numeric_stat(body.object_id, field) for field in ('power', 'toughness')))
        self.finish(s, private)


    def test_actual_controller_change_rechecks_current_aura_controller_after_own_lion_had_been_its_only_modification(self):
        s, private = self.start(736020); aura = self.add(s.engine, 'Lion Umbra', zone='hand')
        body = self.add(s.engine, 'Grizzly Bears'); body.counters['stun'] = 1
        hexmage = self.add(s.engine, 'Vampire Hexmage', seat='B')
        control = self.add(s.engine, 'Act of Treason', seat='B', zone='hand')
        lands = [self.add(s.engine, 'Mountain', seat='B', ref='state-control-payment-' + str(i)) for i in range(3)]
        for card in (aura, body, hexmage, control, *lands):self.assert_closed(s, card)
        self.prepare(s, mana={'G': 2}); self.checkpoint(s); self.cast(s, 'A', aura, [body.ref]); self.assertIsNone(self.resolve(s))
        self.activate_material(s, hexmage, (body.ref,)); self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield', s.state.cards[aura.object_id].zone); self.assertFalse(s.state.cards[body.object_id].counters)
        self.until(s, lambda world: world.state.active_player == 'B' and world.state.pending_decision is not None
            and world.state.pending_decision.kind == 'priority' and world.state.priority_player == 'B'
            and any(a['id'].startswith('cast:' + world.state.cards[control.object_id].ref)
                for a in world.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']))
        self.cast(s, 'B', s.state.cards[control.object_id], [s.state.cards[body.object_id].ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual('B', s.state.cards[body.object_id].controller); self.assertEqual('A', s.state.cards[aura.object_id].controller)
        self.assertEqual('graveyard', s.state.cards[aura.object_id].zone)
        self.assertEqual((2, 2), tuple(s.engine._numeric_stat(body.object_id, field) for field in ('power', 'toughness')))
        self.finish(s, private)

    def test_actual_replenish_untargeted_entry_can_attach_to_modified_hexproof_opponent_but_no_legal_subject_stays_in_graveyard(self):
        for index, eligible in enumerate((True, False)):
            with self.subTest(eligible=eligible):
                s, private = self.start(736030 + index); hand = self.add(s.engine, 'Lion Umbra', zone='hand')
                aura = self.add(s.engine, 'Lion Umbra', zone='graveyard', ref='nonspell-state-enchant')
                body = self.add(s.engine, 'Slippery Bogle', seat='B'); source = self.add(s.engine, 'Replenish', zone='hand')
                if eligible:body.counters['stun'] = 1
                for card in (hand, aura, body, source):self.assert_closed(s, card)
                self.prepare(s, mana={'C': 3, 'W': 1, 'G': 2}); self.checkpoint(s); self.priority(s, 'A')
                self.reject(s, 'A', {'action_id': 'cast:' + hand.ref, 'targets': [body.ref], 'pay': 'auto'})
                self.cast(s, 'A', source); pending = self.resolve(s)
                if eligible:
                    self.assertEqual('aura.entry', pending.kind); self.assertEqual(('A',), tuple(pending.actors))
                    self.assertEqual('graveyard', s.state.cards[aura.object_id].zone); s = self.replay(s, load=True)
                    command = {'action_id': 'choose', 'aura_target': s.state.cards[body.object_id].ref}
                    self.reject(s, 'B', command); result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve(s)); self.assertEqual(body.object_id, s.state.cards[aura.object_id].attached_to)
                    self.assertEqual('battlefield', s.state.cards[aura.object_id].zone)
                    self.assertEqual((4, 4), tuple(s.engine._numeric_stat(body.object_id, field) for field in ('power', 'toughness')))
                else:
                    self.assertIsNone(pending); self.assertEqual('graveyard', s.state.cards[aura.object_id].zone)
                self.assertEqual('hand', s.state.cards[hand.object_id].zone); self.finish(s, private)

    def test_actual_lion_umbra_armor_replaces_destruction_removes_damage_and_preserves_counter_without_tapping(self):
        s, private = self.start(736040); aura = self.add(s.engine, 'Lion Umbra', zone='hand')
        body = self.add(s.engine, 'Grizzly Bears'); body.counters['stun'] = 1; body.marked_damage = 1
        kill = self.add(s.engine, 'Doom Blade', zone='hand')
        for card in (aura, body, kill):self.assert_closed(s, card)
        self.prepare(s, mana={'G': 2, 'C': 1, 'B': 1}); self.checkpoint(s); self.cast(s, 'A', aura, [body.ref]); self.assertIsNone(self.resolve(s))
        self.cast(s, 'A', kill, [body.ref]); pending = self.resolve(s)
        if pending is not None:
            self.assertEqual('replacement.order', pending.kind); self.assertEqual(('A',), tuple(pending.actors))
            s = self.replay(s, load=True)
            option = next(o['id'] for o in s.packet('pilot:A', full=True)['decision']['ctx']['options'] if o.get('source') == aura.object_id)
            command = {'action_id': 'choose', 'replacement': option}; self.reject(s, 'B', command)
            result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[aura.object_id].zone); self.assertEqual('battlefield', s.state.cards[body.object_id].zone)
        self.assertEqual(0, s.state.cards[body.object_id].marked_damage); self.assertFalse(s.state.cards[body.object_id].tapped)
        self.assertEqual(1, s.state.cards[body.object_id].counters['stun'])
        self.assertEqual((2, 2), tuple(s.engine._numeric_stat(body.object_id, field) for field in ('power', 'toughness')))
        self.finish(s, private)

    def test_actual_already_tapped_attachment_does_not_trigger_and_island_type_changes_preserve_nonbasic_vs_basic(self):
        s, private = self.start(736050); aura = self.add(s.engine, 'Uncontrolled Infestation', zone='hand')
        second = self.add(s.engine, 'Uncontrolled Infestation', zone='hand', ref='second-state-land-aura')
        land = self.add(s.engine, 'Taiga', seat='B'); land.tapped = True
        basic = self.add(s.engine, 'Forest'); first_claim = self.add(s.engine, "Sea's Claim", zone='hand')
        second_claim = self.add(s.engine, "Sea's Claim", zone='hand', ref='state-basic-island-claim')
        for card in (aura, second, land, basic, first_claim, second_claim):self.assert_closed(s, card)
        self.prepare(s, mana={'C': 2, 'R': 2, 'U': 4}); self.checkpoint(s); self.cast(s, 'A', aura, [land.ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield', s.state.cards[land.object_id].zone); self.assertTrue(s.state.cards[land.object_id].tapped)
        self.assertEqual(0, self.trigger_count(s, aura)); self.cast(s, 'A', first_claim, [land.ref]); self.assertIsNone(self.resolve(s))
        data = s.engine._effective_card_data(s.state.cards[land.object_id]); types, subtypes, supers = s.engine._type_parts(data['type_line'])
        self.assertIn('island', subtypes); self.assertNotIn('basic', supers); self.assertEqual('battlefield', s.state.cards[aura.object_id].zone)
        self.cast(s, 'A', second_claim, [basic.ref]); self.assertIsNone(self.resolve(s))
        data = s.engine._effective_card_data(s.state.cards[basic.object_id]); self.assertIn('basic', s.engine._type_parts(data['type_line'])[2])
        self.priority(s, 'A'); offered = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('cast:' + second.ref))
        self.reject(s, 'A', {'action_id': offered['id'], 'targets': [basic.ref], 'pay': 'auto'})
        self.assertEqual(0, self.trigger_count(s, aura)); self.finish(s, private)

    def test_actual_infestation_trigger_respects_indestructible_nonbasic_land_after_an_offered_mana_ability(self):
        s, private = self.start(736051); aura = self.add(s.engine, 'Uncontrolled Infestation', zone='hand')
        land = self.add(s.engine, 'Darksteel Citadel', seat='B')
        for card in (aura, land):self.assert_closed(s, card)
        self.prepare(s, mana={'C': 1, 'R': 1}); self.checkpoint(s); self.cast(s, 'A', aura, [land.ref]); self.assertIsNone(self.resolve(s))
        before = s.state.players['B'].mana_pool.get('C', 0); self.activate_material(s, land, (), seat='B')
        self.assertEqual(before + 1, s.state.players['B'].mana_pool.get('C', 0)); self.assertTrue(s.state.cards[land.object_id].tapped)
        self.assertEqual(1, self.trigger_count(s, aura)); self.assertIsNone(self.resolve(s))
        self.assertEqual('battlefield', s.state.cards[land.object_id].zone); self.assertEqual('battlefield', s.state.cards[aura.object_id].zone)
        self.assertEqual(land.object_id, s.state.cards[aura.object_id].attached_to); self.finish(s, private)


    def test_actual_nonspell_post_attachment_fault_restores_full_graveyard_choice_and_private_state_then_loads_and_retries(self):
        import quorune.aura.runtime as owner
        from unittest.mock import patch
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s, private = self.start(736060); aura = self.add(s.engine, 'Lion Umbra', zone='graveyard')
        body = self.add(s.engine, 'Slippery Bogle', seat='B'); body.counters['stun'] = 1
        source = self.add(s.engine, 'Replenish', zone='hand')
        for card in (aura, body, source):self.assert_closed(s, card)
        self.prepare(s, mana={'C': 3, 'W': 1}); self.checkpoint(s); self.cast(s, 'A', source)
        self.assertEqual('aura.entry', self.resolve(s).kind)
        before = authoritative_state_hash(s.state); observed = []; original = owner.attach_objects
        def fault(*args, **kwargs):
            result = original(*args, **kwargs)
            observed.append((s.state.cards[aura.object_id].zone, s.state.cards[aura.object_id].attached_to))
            raise GameRuleError('Injected after public-state Aura canonical attachment')
        command = {'action_id': 'choose', 'aura_target': body.ref}
        with patch.object(owner, 'attach_objects', side_effect=fault):result = s.act('pilot:A', command)
        self.assertFalse(result.ok); self.assertIn('Injected after public-state Aura', result.summary)
        self.assertEqual([('battlefield', body.object_id)], observed)
        self.assertEqual(before, authoritative_state_hash(s.state)); self.assertEqual('graveyard', s.state.cards[aura.object_id].zone)
        self.assertIsNone(s.state.cards[aura.object_id].attached_to)
        s = self.replay(s, load=True); self.reject(s, 'B', command)
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary); self.assertIsNone(self.resolve(s))
        self.assertEqual(body.object_id, s.state.cards[aura.object_id].attached_to); self.finish(s, private)

    def test_fresh_native_any_aura_controller_substitution_is_killed_after_positive_foreign_equipment_journey(self):
        from unittest.mock import patch
        from quorune.characteristic_evaluation_host import CharacteristicEvaluationHostMixin as Owner
        self.test_actual_foreign_equipment_modifies_opponent_creature_until_equipment_is_destroyed_then_wrong_controller_lion_falls()
        original = Owner._attachment_public_state; observed = []
        def wrong(host, card, **kwargs):
            enchanted, equipped, modified = original(host, card, **kwargs)
            if enchanted and not modified:observed.append(card.object_id)
            return enchanted, equipped, modified or enchanted
        with patch.object(Owner, '_attachment_public_state', wrong):
            with self.assertRaises(AssertionError):
                self.test_actual_foreign_equipment_modifies_opponent_creature_until_equipment_is_destroyed_then_wrong_controller_lion_falls()
        self.assertTrue(observed)

    def test_genuinely_verified_qualified_and_shared_owner_withdrawals_block_only_dependent_complete_originals(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.semantics import SemanticRegistry
        from quorune.oracle_ir import register_generated_programs
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.enchant_public_state_model import ENCHANT_PUBLIC_STATE_CAPABILITY as cap
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        doc = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'); known = set(discover_tests(ROOT).values())
        def ready(name, registry):
            card = self.db.lookup(name); semantics = SemanticRegistry()
            register_generated_programs(self.db, semantics, (card,), capability_registry=registry, capability_profile='commander_review',
                promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
            parent = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                capability_registry=registry, capability_profile='commander_review')
            return bind_card_program_runtime(parent, capability_registry=registry, profile='commander_review')['strict_capability_ready']
        baseline = CapabilityRegistry(deepcopy(raw)); index = build_index(registry_value=raw, declaration_source=doc, discovered_test_ids=known)
        baseline.mark_evidence_verified(validate_capability_evidence_index(index, registry=baseline))
        for name in ('Lion Umbra', 'Uncontrolled Infestation'):self.assertTrue(ready(name, baseline), name)
        for missing in (cap, 'attachment.aura.typed_restriction', 'attachment.aura.simple_object',
                'target.revalidate_resolution', 'state_query.permanent.public_state_predicate'):
            changed = deepcopy(raw); next(row for row in changed['capabilities'] if row['id'] == missing).update(
                status='blocked', blockers=['Qualified Enchant or canonical shared owner withdrawn'])
            registry = CapabilityRegistry(changed); current = build_index(registry_value=changed, declaration_source=doc, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(current, registry=registry))
            withdrawn = registry.closure((missing,), profile='commander_review'); self.assertFalse(withdrawn.trusted)
            self.assertIn('status:' + missing + ':blocked', withdrawn.blockers)
            self.assertFalse(any('unverified' in b for b in withdrawn.blockers))
            controls = [('Lion Umbra', False), ('Uncontrolled Infestation', missing == 'state_query.permanent.public_state_predicate'),
                ('Grizzly Bears', True), ('Forest', True)]
            if missing == cap:controls.extend((name, True) for name in ("Runner's Bane", 'Roots', 'Threads of Disloyalty'))
            for name, expected in controls:self.assertEqual(expected, ready(name, registry), (missing, name))



    def test_actual_infestation_trigger_keeps_its_land_after_aura_is_destroyed_in_response(self):
        s, private = self.start(736070); aura = self.add(s.engine, 'Uncontrolled Infestation', zone='hand')
        land = self.add(s.engine, 'Taiga', seat='B'); body = self.add(s.engine, 'Grizzly Bears', seat='B')
        growth = self.add(s.engine, 'Giant Growth', seat='B', zone='hand')
        removal = self.add(s.engine, 'Disenchant', zone='hand')
        for card in (aura, land, body, growth, removal):self.assert_closed(s, card)
        self.prepare(s, mana={'C': 2, 'R': 1, 'W': 3}); self.checkpoint(s)
        self.cast(s, 'A', aura, [land.ref]); self.assertIsNone(self.resolve(s))
        self.cast(s, 'B', growth, [body.ref]); self.assertTrue(s.state.cards[land.object_id].tapped)
        self.assertEqual(1, self.trigger_count(s, aura)); self.cast(s, 'A', removal, [aura.ref])
        self.resolve_top(s); self.assertEqual('graveyard', s.state.cards[aura.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[land.object_id].zone); self.assertEqual(1, self.trigger_count(s, aura))
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[land.object_id].zone)
        self.assertEqual((5, 5), tuple(s.engine._numeric_stat(body.object_id, field) for field in ('power', 'toughness')))
        self.finish(s, private)


if __name__ == '__main__':unittest.main()
