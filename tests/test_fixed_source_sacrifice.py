"""Source sacrifice is an identity-bound effect through the existing move owner."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from quorune.compiler.source_sacrifice_templates import fixed_source_sacrifice_template
from quorune.rules.source_sacrifice_shapes import (
    fixed_source_sacrifice_node_capabilities, fixed_source_sacrifice_runtime_dependencies,
)
from quorune.semantic_runtime.context import (
    ReadOnlyHandlerContext, ReadOnlyRulesQuery, SemanticNodeError, SemanticSourceContext,
)
from quorune.semantic_runtime.source_sacrifice import (
    apply_fixed_source_sacrifice, lower_fixed_source_sacrifice,
)
from quorune.source_sacrifice_model import (
    FixedSourceSacrificeIntent, SOURCE_SACRIFICE_CAPABILITY, SOURCE_SACRIFICE_FIELD,
    SOURCE_SACRIFICE_MECHANIC, fixed_source_sacrifice_dependencies,
    fixed_source_sacrifice_effect,
)
from quorune.zone_trigger_events import ZoneTransitionKind


class FixedSourceSacrificeModelTests(unittest.TestCase):
    def context(self, *, actor='A', source=None):
        return ReadOnlyHandlerContext(actor, 'Resolve source instruction',
            ReadOnlyRulesQuery(tuple('ABCD'), tuple('ABCD'), tuple('ABCD')),
            source or SemanticSourceContext('stack', 'source-id', 'source@0', 'source-ref'))

    def intent(self):
        plan = lower_fixed_source_sacrifice(fixed_source_sacrifice_effect(), self.context(),
                                           handler_id='generic.self-zone-move.v1')
        self.assertEqual(1, len(plan.intents))
        return plan.intents[0]

    def host(self, **changes):
        card = SimpleNamespace(object_id='source-id', ref='source-ref', logical_object_id='source@0',
            zone='battlefield', phased_out=False, owner='B', controller='A', types=('land',))
        for key, value in changes.items():
            setattr(card, key, value)
        calls = []
        host = SimpleNamespace(state=SimpleNamespace(cards={'source-id': card}),
            move_objects_simultaneously_intent=lambda intent: calls.append(intent) or ('source-ref',))
        return host, card, calls

    def test_only_complete_explicit_source_permanent_clauses_compile(self):
        for subject in ('this creature', 'this enchantment', 'this Aura', 'this permanent',
                        'Generic Source'):
            with self.subTest(subject=subject):
                result = fixed_source_sacrifice_template('Sacrifice ' + subject + '.',
                    source_name='Generic Source', source_is_permanent=True)
                self.assertIsNotNone(result)
                self.assertIn(SOURCE_SACRIFICE_CAPABILITY, fixed_source_sacrifice_node_capabilities(
                    effects=result[1], target_schema=result[2], mechanic_ids=result[3]))
        for text in ('Sacrifice it.', 'Sacrifice target creature.', 'Sacrifice a creature.',
                'Sacrifice another Generic Source.', 'Sacrifice Foreign Source.',
                'Sacrifice this creature. Draw a card.'):
            with self.subTest(text=text):
                self.assertIsNone(fixed_source_sacrifice_template(text,
                    source_name='Generic Source', source_is_permanent=True))
        self.assertIsNone(fixed_source_sacrifice_template('Sacrifice Generic Source.',
            source_name='Generic Source', source_is_permanent=False))
        self.assertIsNotNone(fixed_source_sacrifice_template('Sacrifice Dr. Source.',
            source_name='Dr. Source', source_is_permanent=True))

    def test_versioned_payload_rejects_malformed_or_reinterpreted_movement(self):
        effect = fixed_source_sacrifice_effect()
        for change in ({SOURCE_SACRIFICE_FIELD: True}, {SOURCE_SACRIFICE_FIELD: 1.0},
                {'tapped': 0}, {'destination': 'exile'}, {'origin': 'graveyard'},
                {'source_form': 'card'}, {'player': 'B'}, {'_replacement_selections': 'choice'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                fixed_source_sacrifice_dependencies({**effect, **change})
        self.assertEqual((), fixed_source_sacrifice_dependencies({'op': 'sacrifice_if_present',
                                                                 'card': '$source.zone_object'}))

    def test_runtime_minimum_comes_from_payload_when_coverage_is_missing(self):
        program = SimpleNamespace(effects=(fixed_source_sacrifice_effect(),), handlers=(),
                                  coverage=(), active_zone='battlefield')
        self.assertIn(SOURCE_SACRIFICE_CAPABILITY,
                      fixed_source_sacrifice_runtime_dependencies(program))
        missing = fixed_source_sacrifice_effect(); missing.pop(SOURCE_SACRIFICE_FIELD)
        program.effects = (missing,)
        with self.assertRaises(ValueError):
            fixed_source_sacrifice_runtime_dependencies(program)
        program.effects = (fixed_source_sacrifice_effect(),)
        program.active_zone = 'graveyard'
        with self.assertRaises(ValueError):
            fixed_source_sacrifice_runtime_dependencies(program)

    def test_lowering_seals_source_identity_and_handles_an_already_absent_source(self):
        intent = self.intent()
        self.assertEqual(('A', 'source-id', 'source@0', 'source-ref'),
            (intent.actor, intent.object_id, intent.logical_object_id, intent.card_ref))
        absent = self.context(source=SemanticSourceContext('stack'))
        result = lower_fixed_source_sacrifice(fixed_source_sacrifice_effect(), absent,
                                             handler_id='generic.self-zone-move.v1')
        self.assertEqual((), result.intents)
        partial = self.context(source=SemanticSourceContext('stack', 'source-id', 'source@0'))
        inactive = replace(self.context(), actor='D',
            query=ReadOnlyRulesQuery(tuple('ABCD'), tuple('ABC'), tuple('ABC')))
        for context in (partial, inactive, replace(self.context(), source=None)):
            with self.subTest(context=context), self.assertRaises(SemanticNodeError):
                lower_fixed_source_sacrifice(fixed_source_sacrifice_effect(), context,
                                             handler_id='generic.self-zone-move.v1')

    def test_same_controlled_foreign_owned_source_uses_canonical_sacrifice(self):
        host, card, calls = self.host()
        self.assertEqual(('source-ref',), apply_fixed_source_sacrifice(host, self.intent()))
        move, = calls
        self.assertEqual(('A', ('source-ref',), ('battlefield',), 'graveyard', True, False),
            (move.actor, move.object_refs, move.expected_zones, move.destination,
             move.controlled_only, move.owned_only))
        self.assertIs(ZoneTransitionKind.SACRIFICE, move.transition_kind)
        self.assertEqual('B', card.owner)
        self.assertEqual(('land',), card.types)

    def test_source_control_zone_phase_or_incarnation_changes_do_not_move_anything(self):
        for change in ({'controller': 'B'}, {'zone': 'graveyard'}, {'phased_out': True},
                       {'logical_object_id': 'source@2'}, {'ref': 'different-ref'}):
            with self.subTest(change=change):
                host, _, calls = self.host(**change)
                self.assertIsNone(apply_fixed_source_sacrifice(host, self.intent()))
                self.assertEqual([], calls)
        host, _, calls = self.host(); host.state.cards.clear()
        self.assertIsNone(apply_fixed_source_sacrifice(host, self.intent()))
        self.assertEqual([], calls)

    def test_historical_self_move_payload_still_uses_its_existing_intent(self):
        from quorune.semantic_runtime.self_zone_move import SelfZoneMoveEffectHandler
        from quorune.self_zone_move import SelfZoneMoveIntent
        plan = SelfZoneMoveEffectHandler().lower({'op': 'self_zone_move', 'origin': 'graveyard',
            'destination': 'hand', 'tapped': False, 'source_form': 'card'}, self.context())
        intent, = plan.intents
        self.assertIsInstance(intent, SelfZoneMoveIntent)


import tempfile
from pathlib import Path
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_recipient_control as seat_actions
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database

ORIGINALS = ('Skittering Horror', 'Skittering Skirge', 'Skittering Monstrosity',
             'Straw Golem', 'Illusory Demon', 'Spiritual Asylum', 'Sleep Magic', 'Energy Field')


class _SourceSacrificeFixture:
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); path = Path(cls.temp.name)/'source-sacrifice.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/fixed-source-sacrifice-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Source sacrifice witnesses', [
            DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 20)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temp.cleanup()


class FixedSourceSacrificeCompilerTests(_SourceSacrificeFixture, unittest.TestCase):
    def test_eight_whole_originals_have_normal_complete_source_admission(self):
        semantics = SemanticRegistry(); cards = [self.db.lookup(name) for name in ORIGINALS]
        register_generated_programs(self.db, semantics, cards, trust_level='provisional',
            capability_registry=self.registry, capability_profile='commander_review',
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(name=card.name):
                ir = compile_oracle_card(card, capability_registry=self.registry,
                                         capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry,
                                                     profile='commander_review')
                self.assertEqual('capability_closed', binding['trust_basis'], binding['blockers'])
                self.assertTrue(binding['strict_capability_ready'], binding['blockers'])
        for name in ('Hesitation', 'Lunar Force'):
            with self.subTest(name=name):
                result = compile_oracle_card(self.db.lookup(name), capability_registry=self.registry,
                                             capability_profile='commander_review')
                self.assertEqual('exact', result.status, result.material_residuals)

    def test_missing_source_owner_dependency_cannot_be_hidden_by_removing_coverage(self):
        card = self.db.lookup('Skittering Horror')
        program = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile='commander_review')
        ability, = (a for a in program.abilities if SOURCE_SACRIFICE_CAPABILITY in a.capability_dependencies)
        dependencies = tuple(c for c in ability.capability_dependencies if c != SOURCE_SACRIFICE_CAPABILITY)
        modified = replace(ability, coverage=(), capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies, profile='commander_review').to_dict())
        binding = bind_semantic_program_runtime(modified, capability_registry=self.registry,
                                                profile='commander_review')
        self.assertFalse(binding['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + SOURCE_SACRIFICE_CAPABILITY,
                      binding['blockers'])


class FixedSourceSacrificeNativeTests(_SourceSacrificeFixture, InheritedNativeFixture, unittest.TestCase):
    reject = seat_actions.RecipientControlNativeTests.reject
    finish = seat_actions.RecipientControlNativeTests.finish

    def source_items(self, session, source):
        return [item for item in session.state.stack if item.source_object_id == source.object_id]

    def base(self, name, seed, *, spell='Grizzly Bears', caster='A'):
        s = self.session(seed); source = self.add(s.engine, name)
        cast = self.add(s.engine, spell, seat=caster, zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
        self.assert_closed(s, source); self.assert_closed(s, cast)
        return s, source, cast, private

    def test_original_own_cast_triggers_sacrifice_the_source_as_an_effect_to_its_owner(self):
        for number, name in enumerate(('Skittering Horror', 'Skittering Skirge',
                                      'Skittering Monstrosity', 'Illusory Demon')):
            with self.subTest(name=name):
                s, source, cast, private = self.base(name, 701210 + number); source.owner = 'B'
                observer = self.add(s.engine, 'Mortician Beetle'); self.assert_closed(s, observer)
                self.prepare(s, mana={'C': 8, 'G': 2}); self.checkpoint(s)
                self.cast(s, 'A', cast)
                self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
                self.assertEqual('stack', s.state.cards[cast.object_id].zone)
                self.assertEqual(1, len(self.source_items(s, source)))
                self.resolve_top(s)
                self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                self.assertIn(source.object_id, s.state.players['B'].zones['graveyard'])
                self.assertNotIn(source.object_id, s.state.players['A'].zones['graveyard'])
                decision = self.resolve(s)
                self.assertEqual('semantic.choice', decision.kind)
                command = {'action_id': 'choose', 'choice': 'put'}
                self.reject(s, 'B', command)
                result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(s))
                self.assertEqual(1, s.state.cards[observer.object_id].counters.get('+1/+1', 0))
                self.assertEqual('battlefield', s.state.cards[cast.object_id].zone)
                self.finish(s, private)

    def test_straw_golem_uses_opponent_creature_cast_not_own_or_noncreature_cast(self):
        s, source, own, private = self.base('Straw Golem', 701220)
        creature = self.add(s.engine, 'Grizzly Bears', seat='B', zone='hand', ref='opponent-cast-body')
        instant = self.add(s.engine, 'Lightning Bolt', seat='B', zone='hand')
        flash = self.add(s.engine, 'Vedalken Orrery', seat='B'); self.assert_closed(s, flash)
        self.prepare(s, mana={'C': 6, 'G': 1}); s.state.players['B'].mana_pool.update(C=4, G=1, R=1)
        self.checkpoint(s); self.cast(s, 'A', own); self.assertEqual([], self.source_items(s, source))
        self.assertIsNone(self.resolve(s)); self.cast(s, 'B', instant, ['A'])
        self.assertEqual([], self.source_items(s, source)); self.assertIsNone(self.resolve(s))
        self.cast(s, 'B', creature); self.assertEqual(1, len(self.source_items(s, source)))
        self.assertIsNone(self.resolve(s)); self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.finish(s, private)

    def test_source_control_change_after_trigger_does_not_let_former_controller_sacrifice_it(self):
        s, source, cast, private = self.base('Skittering Horror', 701230)
        donation = self.add(s.engine, 'Donate', zone='hand'); self.assert_closed(s, donation)
        flash = self.add(s.engine, 'Vedalken Orrery'); self.assert_closed(s, flash)
        self.prepare(s, mana={'C': 8, 'G': 1, 'U': 1}); self.checkpoint(s)
        self.cast(s, 'A', cast); self.cast(s, 'A', donation, ['B', source.ref]); self.resolve_top(s)
        self.assertEqual('B', s.state.cards[source.object_id].controller)
        self.assertIsNone(self.resolve(s)); self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
        self.finish(s, private)

    def test_source_blink_and_return_does_not_make_old_trigger_sacrifice_new_incarnation(self):
        s, source, cast, private = self.base('Skittering Horror', 701240)
        blink = self.add(s.engine, 'Momentary Blink', zone='hand'); self.assert_closed(s, blink)
        self.prepare(s, mana={'C': 8, 'G': 1, 'W': 1}); self.checkpoint(s)
        original = source.logical_object_id; self.cast(s, 'A', cast)
        self.cast(s, 'A', blink, [source.ref]); self.resolve_top(s)
        self.assertNotEqual(original, s.state.cards[source.object_id].logical_object_id)
        self.assertIsNone(self.resolve(s)); self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
        self.finish(s, private)

    def test_source_type_change_and_indestructible_do_not_stop_sacrifice(self):
        for number, response_name in enumerate(('Song of the Dryads', 'Heroic Intervention')):
            with self.subTest(response=response_name):
                s, source, cast, private = self.base('Skittering Horror', 701250 + number)
                response = self.add(s.engine, response_name, zone='hand'); self.assert_closed(s, response)
                flash = self.add(s.engine, 'Vedalken Orrery'); self.assert_closed(s, flash)
                self.prepare(s, mana={'C': 8, 'G': 4}); self.checkpoint(s); self.cast(s, 'A', cast)
                self.cast(s, 'A', response, [source.ref] if response_name == 'Song of the Dryads' else [])
                self.resolve_top(s)
                if response_name == 'Song of the Dryads':
                    self.assertEqual({'land'}, s.engine._effective_type_parts(s.state.cards[source.object_id])[0])
                else:
                    self.assertIn('indestructible', s.engine._combat_keywords(s.state.cards[source.object_id]))
                self.assertIsNone(self.resolve(s)); self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                self.finish(s, private)

    def test_asylum_sacrifices_its_observer_when_a_controlled_creature_attacks(self):
        s = self.session(701260); source = self.add(s.engine, 'Spiritual Asylum')
        attacker = self.add(s.engine, 'Grizzly Bears'); attacker.acquired_control_turn_count = -1
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand'); self.assert_closed(s, source)
        self.prepare(s); self.checkpoint(s); self.attack(s, [(attacker, 'B')])
        self.assertEqual(1, len(self.source_items(s, source))); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[attacker.object_id].zone); self.finish(s, private)

    def test_sleep_magic_damage_sacrifices_the_aura_not_its_enchanted_creature(self):
        s = self.session(701270); source = self.add(s.engine, 'Sleep Magic', zone='hand')
        body = self.add(s.engine, 'Centaur Courser', seat='B')
        shock = self.add(s.engine, 'Shock', zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
        for card in (source, shock): self.assert_closed(s, card)
        self.prepare(s, mana={'C': 6, 'U': 2, 'R': 1}); self.checkpoint(s)
        self.cast(s, 'A', source, [body.ref]); self.assertIsNone(self.resolve(s))
        self.cast(s, 'A', shock, [body.ref]); self.resolve_top(s)
        self.assertEqual(1, len(self.source_items(s, source))); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[body.object_id].zone)
        self.assertEqual(2, s.state.cards[body.object_id].marked_damage); self.finish(s, private)

    def test_energy_field_uses_own_graveyard_event_and_stops_preventing_after_source_sacrifice(self):
        s, source, own, private = self.base('Energy Field', 701280, spell='Lightning Bolt')
        body = self.add(s.engine, 'Centaur Courser', seat='B')
        first = self.add(s.engine, 'Lightning Bolt', seat='B', zone='hand', ref='field-opponent-first')
        second = self.add(s.engine, 'Lightning Bolt', seat='B', zone='hand', ref='field-opponent-second')
        self.prepare(s, mana={'R': 1}); s.state.players['B'].mana_pool.update(R=2)
        life = s.state.players['A'].life; self.checkpoint(s); self.cast(s, 'B', first, ['A'])
        self.assertIsNone(self.resolve(s)); self.assertEqual(life, s.state.players['A'].life)
        self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
        self.cast(s, 'A', own, [body.ref]); self.resolve_top(s)
        self.assertEqual(1, len(self.source_items(s, source))); self.resolve_top(s)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual([], self.source_items(s, source)); self.assertIsNone(self.resolve(s))
        self.cast(s, 'B', second, ['A']); self.assertIsNone(self.resolve(s))
        self.assertEqual(life - 3, s.state.players['A'].life); self.finish(s, private)

    def test_missing_sacrifice_commit_is_killed_by_original_complete_actions(self):
        method = 'test_original_own_cast_triggers_sacrifice_the_source_as_an_effect_to_its_owner'
        witness = type(self)(method)
        with patch('quorune.semantic_runtime.source_sacrifice.apply_fixed_source_sacrifice', return_value=None):
            with self.assertRaises(AssertionError):
                getattr(witness, method)()
