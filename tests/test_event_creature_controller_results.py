"""CR603.3a/608.2h: source controller and dead creature controller are separate roles."""
from copy import deepcopy
import json, unittest
from types import SimpleNamespace as Row
from unittest.mock import patch
from quorune.compiler.fixed_counter_trigger_nodes import fixed_counter_trigger_binding
from quorune.compiler.event_creature_controller_results import dead_creature_controller_optional_draw_result as compile_result
from quorune.rules.event_creature_controller_results import (
    death_controller_draw_payload_dependencies as payload,
    death_controller_draw_program_dependencies as dependencies,
    is_closed_death_controller_draw_program as closed,
)
from quorune.replacement.immutable import FrozenMap

ORIGINAL = "Whenever a creature dies, that creature's controller may draw a card."


def binding(text=ORIGINAL):
    return fixed_counter_trigger_binding(text, card_name='Public Death Observer')


def compiled():
    scope = binding(); return compile_result(scope, scope.body)


def frame():
    scope = binding()
    return dict(event=scope.event.value, active_zone=scope.active_zone, event_condition=scope.event_condition,
        effects=compiled()[0][1], target_schema=None, cost_schema=None, handlers=(), coverage=())


class DeadCreatureControllerCompilerTests(unittest.TestCase):
    def test_complete_death_binding_keeps_prospective_drawer_distinct_from_ability_controller(self):
        result, current = compiled(); self.assertFalse(current); self.assertIsNone(result[2])
        effect, = result[1]; self.assertEqual('$context.previous_controller', effect['player'])
        self.assertEqual(effect['player'], effect['drawer']); self.assertEqual(1, effect['count'])
        self.assertIs(True, effect['private']); self.assertEqual(2, effect['event_optional_draw_version'])
        for draw, count in (('a card.', 1), ('two cards.', 2), ('three cards.', 3)):
            effect = compile_result(binding(), "that creature's controller may draw " + draw)[0][1][0]
            self.assertEqual(count, effect['count'])

    def test_source_owner_damage_caster_draw_amounts_and_material_riders_remain_outside_grammar(self):
        for body in ("that creature's owner may draw a card.", 'you may draw a card.', 'its controller may draw a card.',
                "that creature's controller draws a card.", "that creature's controller may draw X cards.",
                "that creature's controller may draw a card. You gain 1 life.",
                "that creature's controller may draw a card and discard a card."):
            self.assertIsNone(compile_result(binding(), body), body)

    def test_unknown_or_qualified_event_frames_and_legacy_defending_player_grammar_stay_separate(self):
        for text in ('Whenever a nontoken creature dies, that creature\'s controller may draw a card.',
                'Whenever a creature you control dies, that creature\'s controller may draw a card.',
                'Whenever this creature attacks, defending player may draw a card.'):
            other = binding(text); self.assertIsNotNone(other); self.assertIsNone(compile_result(other, other.body))

    def test_fresh_source_controller_substitution_is_killed_after_positive_typed_lowering(self):
        self.assertIsNotNone(compiled())
        with patch('quorune.compiler.event_creature_controller_results.DEATH_CONTROLLER', '$controller'):
            with self.assertRaises(AssertionError):self.assertIsNotNone(compiled())


class DeadCreatureControllerShapeTests(unittest.TestCase):
    def test_closed_fixed_private_payload_survives_json_and_frozen_codec_with_explicit_new_version(self):
        effect = compiled()[0][1][0]
        required = payload(FrozenMap(json.loads(json.dumps(effect))))
        self.assertIn('resolution.event_player.optional_draw', required)
        for actor in ('A', 'B'):
            self.assertEqual(required, payload(effect | {'player': actor, 'drawer': actor}, materialized=True))

    def test_versions_actor_roles_counts_privacy_and_unknown_fields_fail_closed(self):
        effect = compiled()[0][1][0]
        for change in ({'event_optional_draw_version': 1}, {'event_optional_draw_version': True},
                {'event_optional_draw_version': 3}, {'player': '$controller', 'drawer': '$controller'},
                {'player': '$context.controller', 'drawer': '$context.controller'}, {'count': 0}, {'count': True},
                {'private': False}, {'drawer': 'B'}, {'source': '$source'}):
            with self.subTest(change=change), self.assertRaises(ValueError):payload(effect | change)
        with self.assertRaises(ValueError):payload(effect, materialized=True)

    def test_complete_frame_infers_each_dependency_without_coverage_and_rejects_hidden_siblings(self):
        values = frame(); required = dependencies(Row(**values))
        self.assertIn('trigger.event.normalized_zone_change', required)
        self.assertTrue(closed(Row(**values, capability_dependencies=required)))
        for missing in required:
            self.assertFalse(closed(Row(**values, capability_dependencies=tuple(c for c in required if c != missing))))
        effect = values['effects'][0]
        for change in ({'event': 'creature.attacks'}, {'active_zone': 'graveyard'}, {'event_condition': None},
                {'event_condition': {'field': 'token', 'op': 'in', 'value': [0, 1]}},
                {'handlers': ({'hidden': True},)}, {'target_schema': {'count': 1}}, {'cost_schema': {'cost': 1}},
                {'effects': (*values['effects'], {'op': 'draw', 'count': 1})},
                {'effects': ({'op': 'offer_optional_effect', 'effects': (effect,)},)}):
            with self.subTest(change=change), self.assertRaises(ValueError):dependencies(Row(**(values | change)))


class DeadCreatureControllerIntegrationTests(unittest.TestCase):
    def test_existing_compiler_and_runtime_routes_keep_complete_new_scope_without_coverage(self):
        from quorune.compiler.event_optional_draw_results import fixed_event_optional_draw_result
        from quorune.rules.event_optional_draw_shapes import event_optional_draw_runtime_dependencies, is_closed_event_optional_draw_program
        scope = binding(); self.assertEqual(compiled(), fixed_event_optional_draw_result(scope, scope.body, effect_template=None, card_name='Public Death Observer'))
        values = frame(); required = event_optional_draw_runtime_dependencies(Row(**values))
        self.assertTrue(is_closed_event_optional_draw_program(Row(**values, capability_dependencies=required)))
        bad = values | {'event': 'creature.attacks', 'event_condition': {'field': 'card', 'op': 'eq', 'value': '$source.ref'}}
        with self.assertRaises(ValueError):event_optional_draw_runtime_dependencies(Row(**bad))
        self.assertFalse(is_closed_event_optional_draw_program(Row(**bad, capability_dependencies=required)))

    def test_registered_handler_keeps_creature_controller_as_actor_and_preserves_new_continuation_version(self):
        from dataclasses import replace
        from quorune.drawing import DrawPermission
        from quorune.replacement.immutable import thaw_value
        from quorune.semantic_choices.optional_draw import OPTIONAL_DRAW_CHOICE_HANDLERS
        import test_optional_draw_choices as old
        handler, = OPTIONAL_DRAW_CHOICE_HANDLERS
        query = old.query(permission_a=DrawPermission('A', 0), permission_b=DrawPermission('B', 0))
        context = replace(old.context(query, actor='A'), stack_controller='B')
        effect = compiled()[0][1][0] | {'player': 'A', 'drawer': 'A'}
        prepared = handler.prepare(effect, context); self.assertEqual(('draw', 'decline'), prepared.request.choice.legal_values)
        stored = FrozenMap(json.loads(json.dumps(thaw_value(prepared.continuation_effect)))); self.assertEqual(2, stored['event_optional_draw_version'])
        raw = old.continuation(stored); cont = replace(raw, semantic_frame=replace(raw.semantic_frame, controller='B'))
        self.assertEqual('B', cont.semantic_frame.controller)
        draw = handler.complete(cont, {'choice': 'draw'}, query)
        self.assertEqual(({'op': 'draw', 'player': 'A', 'count': 1, 'private': True},), tuple(thaw_value(e) for e in draw.prepend_effects))
        self.assertEqual((), handler.complete(cont, {'choice': 'decline'}, query).prepend_effects)
        with self.assertRaises(ValueError):handler.prepare(effect, replace(context, actor='B'))
        for response in ({'choice': 'draw', 'drawer': 'B'}, {'choice': 'draw', 'count': 99}, {'choice': 'draw', 'event_optional_draw_version': 1}):
            with self.assertRaises(ValueError):handler.complete(cont, response, query)

    def test_shared_draw_permission_removes_illegal_offer_and_rechecks_accepted_command(self):
        from quorune.drawing import DrawPermission
        from quorune.semantic_choices.optional_draw import OptionalDrawHandler
        import test_optional_draw_choices as old
        handler = OptionalDrawHandler(); effect = compiled()[0][1][0] | {'player': 'A', 'drawer': 'A'}
        forbidden = old.query(permission_a=DrawPermission('A', 0, 0, ('fixture:cannot-draw',)), permission_b=DrawPermission('B', 0))
        unavailable = handler.prepare(effect, old.context(forbidden, actor='A'))
        self.assertIsNone(unavailable.request); self.assertIsNotNone(unavailable.auto_continue)
        good = old.query(permission_a=DrawPermission('A', 0), permission_b=DrawPermission('B', 0))
        prepared = handler.prepare(effect, old.context(good, actor='A')); cont = old.continuation(prepared.continuation_effect)
        with self.assertRaises(ValueError):handler.complete(cont, {'choice': 'draw'}, forbidden)
        self.assertEqual((), handler.complete(cont, {'choice': 'decline'}, forbidden).prepend_effects)

    def test_inactive_known_drawer_auto_continues_but_forged_or_unknown_drawers_still_reject(self):
        from quorune.drawing import DrawPermission
        from quorune.semantic_choices.context import SnapshotSemanticChoiceQuery, SemanticChoiceContext
        from quorune.semantic_choices.optional_draw import OptionalDrawHandler
        query = SnapshotSemanticChoiceQuery(seat_order=('A', 'B'), active_order=('B',),
            draw_permissions_by_seat=FrozenMap({'B': DrawPermission('B', 0).to_dict()}))
        context = SemanticChoiceContext(actor='A', stack_ref='stack', stack_controller='B', stack_label='Death observer',
            source_ref='source', card_ref=None, semantic_program_id='program', semantic_program_version=1, query=query)
        effect = compiled()[0][1][0] | {'player': 'A', 'drawer': 'A'}; handler = OptionalDrawHandler()
        prepared = handler.prepare(effect, context); self.assertIsNone(prepared.request); self.assertIsNotNone(prepared.auto_continue)
        for change in ({'count': 0}, {'private': False}, {'drawer': 'B'}, {'extra': True}):
            with self.assertRaises(ValueError):handler.prepare(effect | change, context)
        from dataclasses import replace
        with self.assertRaises(ValueError):handler.prepare(effect, replace(context, actor='B'))
        with self.assertRaises(ValueError):handler.prepare(effect | {'player': 'Z', 'drawer': 'Z'}, replace(context, actor='Z'))


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_life_loss_recovery as recovery_helpers


class DeadCreatureControllerNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = recovery_helpers.TurnLifeLossRecoveryNativeTests.start
    reject = recovery_helpers.TurnLifeLossRecoveryNativeTests.reject
    finish = recovery_helpers.TurnLifeLossRecoveryNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); folder = Path(cls.temporary.name)
        base = json.loads((ROOT / 'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [r for r in base['cards'] if r['name'] == 'Generic Bound Plains']; base['rulings'] = []
        basic = folder / 'basic.json'; basic.write_text(json.dumps(base), encoding='utf-8')
        build_fixture_database([basic, ROOT / 'tests/fixtures/event-creature-controller-result-cards.json'], folder / 'death-controller.sqlite3')
        cls.db = CardDatabase(folder / 'death-controller.sqlite3'); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Death participant private draws', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def world(self, seed, *, extras=(), empty=False):
        s, private = self.start(seed)
        source = self.add(s.engine, 'Fecundity', seat='B', zone='hand', ref='death-draw-source')
        victim = self.add(s.engine, 'Grizzly Bears', seat='A', ref='death-draw-victim')
        kill = self.add(s.engine, 'Doom Blade', seat='B', zone='hand', ref='death-draw-removal')
        for card in (source, victim, kill):self.assert_closed(s, card)
        for name, seat, zone, ref in extras:self.assert_closed(s, self.add(s.engine, name, seat=seat, zone=zone, ref=ref))
        if empty:
            for object_id in list(s.state.players['A'].zones['library']):s.engine.move_card(object_id, 'exile', log=False)
        self.prepare(s, actor='B', active='B', mana={'C': 3, 'G': 1, 'B': 1}); self.checkpoint(s)
        before = sum(s.state.players['B'].mana_pool.values()); self.cast(s, 'B', source)
        self.assertIsNone(self.resolve(s)); self.assertEqual(before - 3, sum(s.state.players['B'].mana_pool.values()))
        return s, private, source, victim, kill

    def death_choice(self, s, victim, kill):
        self.cast(s, 'B', kill, [s.state.cards[victim.object_id].ref])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.assertEqual('graveyard', s.state.cards[victim.object_id].zone)
        self.assertEqual(('A',), tuple(s.state.pending_decision.actors))

    def choose_draw(self, s, draw=True, *, seat='A'):
        command = {'action_id': 'choose', 'choice': 'draw' if draw else 'decline'}
        self.reject(s, 'B' if seat != 'B' else 'A', command)
        result = s.act('pilot:' + seat, command); self.assertTrue(result.ok, result.summary)

    def test_full_original_admits_normally_and_requires_each_private_draw_and_death_authority(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        s, _ = self.start(733000)
        source = self.add(s.engine, 'Fecundity', seat='B', zone='hand'); self.assert_closed(s, source)
        for name in ('Coastal Piracy', 'Curiosity'):self.assert_closed(s, self.add(s.engine, name, zone='hand'))
        program = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if p.effects)
        self.assertEqual('creature.dies', program.event); self.assertEqual(2, program.effects[0]['event_optional_draw_version'])
        for missing in ('resolution.event_player.optional_draw', 'resolution.event_player.fixed_result',
                'zone.draw.library_to_hand', 'trigger.event.normalized_zone_change', 'trigger.placement.apnap'):
            deps = tuple(c for c in program.capability_dependencies if c != missing)
            changed = replace(program, coverage=(), capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict'], missing); self.assertIn('capability:undeclared_runtime_dependency:' + missing, bound['blockers'])
        record = self.db.lookup('Edric, Spymaster of Trest')
        parent = compile_best_available_card_program(self.db, record, semantic_registry=s.engine.semantics,
            capability_registry=self.registry, capability_profile='commander_review')
        self.assertFalse(bind_card_program_runtime(parent, capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])

    def test_actual_printed_cast_foreign_creature_death_private_drawer_choice_reload_and_exact_replay(self):
        s, private, source, victim, kill = self.world(733001)
        before = set(s.state.players['A'].zones['hand']); source_hand = len(s.state.players['B'].zones['hand'])
        self.death_choice(s, victim, kill)
        self.assertEqual(before, set(s.state.players['A'].zones['hand']))
        self.reject(s, 'A', {'action_id': 'choose', 'choice': 'draw', 'drawer': 'B'})
        s = self.replay(s, load=True); self.choose_draw(s); self.assertIsNone(self.resolve(s))
        drawn, = set(s.state.players['A'].zones['hand']) - before
        self.assertEqual(source_hand - 1, len(s.state.players['B'].zones['hand']))
        ref = s.state.cards[drawn].ref
        for viewer in 'ABCD':
            packet = json.dumps(s.packet('pilot:' + viewer, full=True))
            if viewer == 'A':self.assertIn(ref, packet)
            else:self.assertNotIn(ref, packet)
        self.finish(s, private)

    def test_actual_decline_preserves_committed_death_without_drawing_for_any_player(self):
        s, private, source, victim, kill = self.world(733010)
        before = {p: len(s.state.players[p].zones['hand']) for p in 'ABCD'}
        self.death_choice(s, victim, kill); self.choose_draw(s, False); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard', s.state.cards[victim.object_id].zone)
        self.assertEqual(before['A'], len(s.state.players['A'].zones['hand']))
        self.assertEqual(before['B'] - 1, len(s.state.players['B'].zones['hand'])); self.finish(s, private)

    def test_actual_created_token_death_still_offers_its_controller_one_draw_after_token_ceases(self):
        extras = (('Raise the Alarm', 'A', 'hand', 'death-token-spell'), ('Plains', 'A', 'battlefield', 'death-token-white1'),
            ('Plains', 'A', 'battlefield', 'death-token-white2'))
        s, private, source, victim, kill = self.world(733011, extras=extras)
        spell = next(c for c in s.state.cards.values() if c.ref == 'death-token-spell')
        self.cast(s, 'A', spell); self.assertIsNone(self.resolve(s))
        tokens = [c for c in s.state.cards.values() if c.zone == 'battlefield' and not c.is_card_object and c.controller == 'A']
        self.assertEqual(2, len(tokens)); token = tokens[0]
        before = len(s.state.players['A'].zones['hand']); self.cast(s, 'B', kill, [token.ref])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.assertEqual(('A',), tuple(s.state.pending_decision.actors))
        remaining = s.state.cards.get(token.object_id); self.assertTrue(remaining is None or remaining.zone != 'battlefield')
        self.choose_draw(s); self.assertIsNone(self.resolve(s))
        self.assertEqual(before + 1, len(s.state.players['A'].zones['hand']))
        self.assertEqual('battlefield', s.state.cards[victim.object_id].zone); self.finish(s, private)

    def test_actual_direct_exile_is_not_a_creature_death_and_offers_no_draw(self):
        extras = (('Swords to Plowshares', 'C', 'hand', 'death-exile-control'), ('Plains', 'C', 'battlefield', 'death-exile-white'))
        s, private, source, victim, kill = self.world(733012, extras=extras)
        before = len(s.state.players['A'].zones['hand']); spell = next(c for c in s.state.cards.values() if c.ref == 'death-exile-control')
        self.cast(s, 'C', spell, [victim.ref]); self.assertIsNone(self.resolve(s))
        self.assertEqual('exile', s.state.cards[victim.object_id].zone); self.assertEqual(before, len(s.state.players['A'].zones['hand']))
        self.assertFalse(s.state.stack); self.finish(s, private)

    def test_actual_graveyard_exile_replacement_prevents_death_and_its_optional_draw(self):
        extras = (('Samurai of the Pale Curtain', 'D', 'battlefield', 'death-zone-replacement'),)
        s, private, source, victim, kill = self.world(733013, extras=extras)
        before = len(s.state.players['A'].zones['hand']); self.cast(s, 'B', kill, [victim.ref])
        self.assertIsNone(self.resolve(s)); self.assertEqual('exile', s.state.cards[victim.object_id].zone)
        self.assertEqual(before, len(s.state.players['A'].zones['hand'])); self.assertFalse(s.state.stack)
        self.finish(s, private)

    def test_actual_source_destruction_preserves_creature_controllers_draw_but_stifle_suppresses_it(self):
        for index, name in enumerate(('Disenchant', 'Stifle')):
            with self.subTest(response=name):
                extras = ((name, 'C', 'hand', 'death-source-response'), ('Plains', 'C', 'battlefield', 'death-response-white'),
                    ('Island', 'C', 'battlefield', 'death-response-blue'))
                s, private, source, victim, kill = self.world(733020 + index, extras=extras)
                before = len(s.state.players['A'].zones['hand'])
                self.cast(s, 'B', kill, [victim.ref]); self.resolve_top(s); self.priority(s, 'C')
                response = next(c for c in s.state.cards.values() if c.ref == 'death-source-response')
                self.cast(s, 'C', response, [source.ref if index == 0 else s.state.stack[-1].ref])
                if index == 0:
                    self.assertEqual('semantic.choice', self.resolve(s).kind)
                    self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                    self.choose_draw(s); self.assertIsNone(self.resolve(s))
                    self.assertEqual(before + 1, len(s.state.players['A'].zones['hand']))
                else:
                    self.assertIsNone(self.resolve(s)); self.assertFalse(s.state.stack)
                    self.assertEqual(before, len(s.state.players['A'].zones['hand']))
                self.assertEqual('graveyard', s.state.cards[victim.object_id].zone); self.finish(s, private)

    def test_actual_borrowed_creature_death_and_physical_reanimation_keep_original_last_controller(self):
        s, private = self.start(733022)
        source = self.add(s.engine, 'Fecundity', seat='B', zone='hand', ref='borrowed-death-source')
        victim = self.add(s.engine, 'Grizzly Bears', seat='C', ref='borrowed-death-victim')
        borrow = self.add(s.engine, 'Control Magic', seat='A', zone='hand', ref='borrow-before-death')
        kill = self.add(s.engine, 'Doom Blade', seat='B', zone='hand', ref='borrowed-death-kill')
        reanimate = self.add(s.engine, 'Zombify', seat='C', zone='hand', ref='return-dead-physical-card')
        cards = [source, victim, borrow, kill, reanimate, self.add(s.engine, 'Vedalken Orrery', seat='C'),
            self.add(s.engine, 'Swamp', seat='C', ref='return-black-mana')]
        for index in range(3):cards.append(self.add(s.engine, 'Swamp', seat='C', ref='return-generic-' + str(index)))
        for index, name in enumerate(('Forest', 'Forest', 'Forest', 'Swamp', 'Swamp', 'Island', 'Plains')):
            cards.append(self.add(s.engine, name, seat='B', ref='borrowed-death-land-' + str(index)))
        for card in cards:self.assert_closed(s, card)
        self.prepare(s, mana={'C': 2, 'U': 2}); self.checkpoint(s)
        self.cast(s, 'A', borrow, [victim.ref]); self.assertIsNone(self.resolve(s))
        old = s.state.cards[victim.object_id].logical_object_id
        self.assertEqual(('C', 'A'), (s.state.cards[victim.object_id].owner, s.state.cards[victim.object_id].controller))
        def source_offered(current):
            decision = current.state.pending_decision
            if decision is None or decision.kind != 'priority' or tuple(decision.actors) != ('B',):return False
            actions = current.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
            return any(a['id'] == 'cast:' + current.state.cards[source.object_id].ref for a in actions)
        self.until(s, source_offered); self.assertEqual('B', s.state.active_player)
        self.cast(s, 'B', s.state.cards[source.object_id]); self.assertIsNone(self.resolve(s))
        self.cast(s, 'B', s.state.cards[kill.object_id], [s.state.cards[victim.object_id].ref]); self.resolve_top(s)
        self.assertEqual('graveyard', s.state.cards[victim.object_id].zone)
        self.assertIn(victim.object_id, s.state.players['C'].zones['graveyard'])
        self.cast(s, 'C', s.state.cards[reanimate.object_id], [s.state.cards[victim.object_id].ref]); self.resolve_top(s)
        self.assertEqual(('battlefield', 'C'), (s.state.cards[victim.object_id].zone, s.state.cards[victim.object_id].controller))
        self.assertNotEqual(old, s.state.cards[victim.object_id].logical_object_id)
        before = len(s.state.players['A'].zones['hand']); self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.assertEqual(('A',), tuple(s.state.pending_decision.actors)); self.choose_draw(s); self.assertIsNone(self.resolve(s))
        self.assertEqual(before + 1, len(s.state.players['A'].zones['hand'])); self.finish(s, private)

    def test_actual_source_controller_or_prospective_drawer_concession_never_leaves_an_inactive_choice(self):
        for index, leaving in enumerate(('B', 'A')):
            with self.subTest(leaving=leaving):
                s, private, source, victim, kill = self.world(733030 + index)
                self.cast(s, 'B', kill, [victim.ref]); self.resolve_top(s); self.priority(s, leaving)
                before = len(s.state.players['A'].zones['hand'])
                result = s.act('pilot:' + leaving, {'action_id': 'concede', 'choices': {'confirm_concede': True}})
                self.assertTrue(result.ok, result.summary); self.assertNotIn(leaving, s.engine.active_seats)
                self.assertIsNone(self.resolve(s)); self.assertFalse(s.state.stack)
                if leaving == 'B':self.assertEqual(before, len(s.state.players['A'].zones['hand']))
                self.finish(s, private)

    def test_actual_draw_limit_applies_to_creature_controller_and_replacement_doubles_only_its_draw(self):
        for index, limit in enumerate((True, False)):
            with self.subTest(limit=limit):
                extras = (('Spirit of the Labyrinth', 'D', 'battlefield', 'death-draw-limit'),
                    ('Reach Through Mists', 'A', 'hand', 'death-first-draw'), ('Island', 'A', 'battlefield', 'death-first-blue')) if limit else (
                    ('Thought Reflection', 'A', 'battlefield', 'death-double-draw'),)
                s, private, source, victim, kill = self.world(733040 + index, extras=extras)
                if limit:
                    first = next(c for c in s.state.cards.values() if c.ref == 'death-first-draw')
                    self.cast(s, 'A', first); self.assertIsNone(self.resolve(s))
                before = len(s.state.players['A'].zones['hand']); self.cast(s, 'B', kill, [victim.ref])
                decision = self.resolve(s)
                if limit:self.assertIsNone(decision); self.assertEqual(before, len(s.state.players['A'].zones['hand']))
                else:
                    self.assertEqual('semantic.choice', decision.kind); self.choose_draw(s); self.assertIsNone(self.resolve(s))
                    self.assertEqual(before + 2, len(s.state.players['A'].zones['hand']))
                self.finish(s, private)

    def test_actual_empty_library_still_offers_optional_draw_and_chosen_failed_draw_loses_only_drawer(self):
        s, private, source, victim, kill = self.world(733042, empty=True)
        self.death_choice(s, victim, kill); self.assertTrue(s.state.players['A'].in_game)
        self.choose_draw(s); self.assertIsNone(self.resolve(s))
        self.assertFalse(s.state.players['A'].in_game); self.assertTrue(s.state.players['B'].in_game)
        self.finish(s, private)

    def test_actual_post_draw_commit_fault_restores_private_hand_library_pending_choice_and_retries(self):
        import quorune.drawing.transaction as owner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s, private, source, victim, kill = self.world(733050); self.death_choice(s, victim, kill)
        before = authoritative_state_hash(s.state); hand = tuple(s.state.players['A'].zones['hand'])
        library = tuple(s.state.players['A'].zones['library']); observed = []; original = owner._commit_ordinary_draw
        def fault(*args, **kwargs):
            result = original(*args, **kwargs)
            observed.append((result.player, len(s.state.players['A'].zones['hand']), len(s.state.players['A'].zones['library'])))
            raise GameRuleError('Injected after original death-controller private draw commit')
        with patch.object(owner, '_commit_ordinary_draw', side_effect=fault):
            result = s.act('pilot:A', {'action_id': 'choose', 'choice': 'draw'})
        self.assertFalse(result.ok); self.assertIn('Injected after original death-controller', result.summary)
        self.assertEqual([('A', len(hand) + 1, len(library) - 1)], observed)
        self.assertEqual(before, authoritative_state_hash(s.state)); self.assertEqual(hand, tuple(s.state.players['A'].zones['hand']))
        self.assertEqual(library, tuple(s.state.players['A'].zones['library']))
        s = self.replay(s, load=True); self.choose_draw(s); self.assertIsNone(self.resolve(s))
        self.assertEqual(len(hand) + 1, len(s.state.players['A'].zones['hand'])); self.finish(s, private)

    def test_fresh_runtime_source_controller_substitution_is_killed_after_positive_actual_original_journey(self):
        import quorune.engine as engine_module
        import quorune.semantic_runtime.values as owner
        self.test_actual_printed_cast_foreign_creature_death_private_drawer_choice_reload_and_exact_replay()
        original = owner.resolve_semantic_value; observed = []
        def wrong(host, value, item):
            if value == '$context.previous_controller':
                observed.append((item.controller, item.context.get('previous_controller'))); return item.controller
            return original(host, value, item)
        with patch.object(owner, 'resolve_semantic_value', wrong), patch.object(engine_module, 'resolve_semantic_value', wrong):
            with self.assertRaises(AssertionError):
                self.test_actual_printed_cast_foreign_creature_death_private_drawer_choice_reload_and_exact_replay()
        self.assertTrue(observed); self.assertEqual({('B', 'A')}, set(observed))

    def test_genuinely_verified_context_specific_draw_dependencies_preserve_old_attack_and_new_death_forms(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.semantics import SemanticRegistry
        from quorune.oracle_ir import register_generated_programs
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        doc = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'); known = set(discover_tests(ROOT).values())
        for missing in ('resolution.event_player.optional_draw', 'resolution.event_player.fixed_result',
                'zone.draw.library_to_hand', 'trigger.event.normalized_zone_change', 'trigger.event.normalized_self_attack'):
            changed = deepcopy(raw)
            optional = next(c for c in changed['capabilities'] if c['id'] == 'resolution.event_player.optional_draw')
            # Verify the proposed generic-owner dependency graph before its metadata update.
            # Each instruction's runtime owner still requires its own exact event authority.
            optional['dependencies'] = [c for c in optional['dependencies'] if c != 'trigger.event.normalized_self_attack']
            next(c for c in changed['capabilities'] if c['id'] == missing).update(
                status='blocked', blockers=['Death-controller optional draw owner withdrawn'])
            registry = CapabilityRegistry(changed)
            index = build_index(registry_value=changed, declaration_source=doc, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            controls = [('Fecundity', missing == 'trigger.event.normalized_self_attack'), ('Grizzly Bears', True)]
            if missing in ('trigger.event.normalized_self_attack', 'trigger.event.normalized_zone_change'):
                controls.append(('Sibilant Spirit', missing != 'trigger.event.normalized_self_attack'))
            for name, expected in controls:
                record = self.db.lookup(name); semantics = SemanticRegistry()
                register_generated_programs(self.db, semantics, (record,), capability_registry=registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                    promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                parent = compile_best_available_card_program(self.db, record, semantic_registry=semantics,
                    capability_registry=registry, capability_profile='commander_review')
                bound = bind_card_program_runtime(parent, capability_registry=registry, profile='commander_review')
                self.assertEqual(expected, bound['strict_capability_ready'], (missing, name, bound['blockers']))


    def test_actual_two_source_controllers_stack_in_apnap_order_and_same_creature_controller_chooses_each(self):
        extras = (('Fecundity', 'C', 'battlefield', 'second-death-draw-source'),)
        s, private, source, victim, kill = self.world(733060, extras=extras)
        before = len(s.state.players['A'].zones['hand'])
        self.cast(s, 'B', kill, [victim.ref]); self.resolve_top(s)
        self.assertEqual(['B', 'C'], [i.controller for i in s.state.stack])
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.assertEqual(('A',), tuple(s.state.pending_decision.actors)); self.choose_draw(s)
        self.assertEqual(before + 1, len(s.state.players['A'].zones['hand']))
        self.assertEqual('semantic.choice', self.resolve(s).kind)
        self.assertEqual(('A',), tuple(s.state.pending_decision.actors)); self.choose_draw(s, False)
        self.assertIsNone(self.resolve(s)); self.assertEqual(before + 1, len(s.state.players['A'].zones['hand']))
        self.finish(s, private)


if __name__ == '__main__':unittest.main()
