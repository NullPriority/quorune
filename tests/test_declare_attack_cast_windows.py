"""Printed attack-step restrictions use declared occurrences, never live attackers."""
import unittest
from pathlib import Path
import tempfile
import json
from unittest.mock import patch
from types import SimpleNamespace
from dataclasses import FrozenInstanceError

from quorune.carddb import CardRecord
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_recipient_control as seat_actions
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from scripts.build_test_database import build_fixture_database
from quorune.declare_attack_cast_model import (
    DeclaredPlayerAttackFact, DeclareAttackCastSpec, declare_attack_cast_version,
)


class DeclareAttackCastWindowModelTests(unittest.TestCase):
    def state(self, fact=DeclaredPlayerAttackFact(('B',))):
        return SimpleNamespace(players={p: object() for p in 'ABCD'}, phase='combat',
            step='declare_attackers', declare_attack_cast_version=1,
            combat=SimpleNamespace(attackers_declared=True, attackers={}, declared_player_attacks=fact))

    def test_declared_occurrence_survives_no_current_attacker_and_is_player_specific(self):
        state = self.state(); restriction = DeclareAttackCastSpec(True)
        self.assertTrue(restriction.permits(state, 'B'))
        self.assertFalse(restriction.permits(state, 'C'))
        self.assertFalse(restriction.permits(state, 'unknown'))
        self.assertFalse(restriction.permits(self.state(DeclaredPlayerAttackFact(())), 'B'))
        self.assertTrue(DeclareAttackCastSpec(False).permits(self.state(None), 'B'))

    def test_before_declaration_other_steps_and_legacy_absence_do_not_authorize_cast(self):
        spec = DeclareAttackCastSpec(True)
        for field, value in (('phase', 'precombat_main'), ('step', 'declare_blockers'),
                ('declare_attack_cast_version', None)):
            state = self.state(); setattr(state, field, value)
            self.assertFalse(spec.permits(state, 'B'))
        state = self.state(); state.combat.attackers_declared = False
        self.assertFalse(spec.permits(state, 'B'))
        state = self.state(); state.combat = SimpleNamespace(attackers_declared=True,
            attackers={}, declared_player_attacks=DeclaredPlayerAttackFact(()))
        self.assertFalse(spec.permits(state, 'B'))

    def test_fact_and_restriction_are_closed_immutable_versioned_values(self):
        fact = DeclaredPlayerAttackFact(('B', 'D'))
        self.assertEqual(fact, DeclaredPlayerAttackFact.from_dict(fact.to_dict()))
        self.assertEqual(DeclareAttackCastSpec(True), DeclareAttackCastSpec.from_dict(
            DeclareAttackCastSpec(True).to_dict()))
        with self.assertRaises(FrozenInstanceError): fact.players = ()
        for value in ({'schema_version': True, 'players': []},
                {'schema_version': 1, 'players': ['B', 'B']},
                {'schema_version': 1, 'players': ['D', 'B']},
                {'schema_version': 1, 'players': [], 'other': 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DeclaredPlayerAttackFact.from_dict(value)
        for value in (True, 0, 2, '1'):
            with self.assertRaises(ValueError): declare_attack_cast_version(value)
        with self.assertRaises(ValueError): DeclareAttackCastSpec(1)


class DeclaredPlayerAttackStateTests(unittest.TestCase):
    def cards(self):
        return {name: SimpleNamespace(object_id=name, controller='A', zone='battlefield',
            phased_out=False, attacking=None) for name in ('player-attacker', 'walker-attacker', 'battle-attacker')}

    def test_canonical_declaration_captures_direct_players_and_survives_removal(self):
        from quorune.model import CombatState
        from quorune.combat_relationship_state import (
            AttackDeclarationAssignment, commit_attack_declaration, remove_combat_relationships,
        )
        combat = CombatState(); cards = self.cards()
        commit_attack_declaration(combat, cards, controller='A', capture_declared_players=True,
            assignments=(AttackDeclarationAssignment('player-attacker', 'B', 'player', 'B'),
                AttackDeclarationAssignment('walker-attacker', 'W', 'planeswalker', 'C', 'walker@1'),
                AttackDeclarationAssignment('battle-attacker', 'S', 'battle', 'D', 'siege@1')))
        self.assertEqual(('B',), combat.declared_player_attacks.players)
        remove_combat_relationships(combat, 'player-attacker')
        self.assertNotIn('player-attacker', combat.attackers)
        self.assertEqual(('B',), combat.declared_player_attacks.players)
        self.assertEqual(combat.declared_player_attacks,
            CombatState.from_dict(combat.to_dict()).declared_player_attacks)
        self.assertIsNone(CombatState().declared_player_attacks)

    def test_legacy_and_entering_attackers_do_not_create_declaration_facts(self):
        from quorune.model import CombatState
        from quorune.combat_relationship_state import AttackDeclarationAssignment, commit_attack_declaration
        assignment = AttackDeclarationAssignment('player-attacker', 'B', 'player', 'B')
        for entering in (False, True):
            combat = CombatState()
            commit_attack_declaration(combat, self.cards(), controller='A',
                assignments=(assignment,), preserve_bands=entering)
            self.assertIsNone(combat.declared_player_attacks)
            self.assertNotIn('declared_player_attacks', combat.to_dict())
        modern = CombatState()
        commit_attack_declaration(modern, self.cards(), controller='A', assignments=(), capture_declared_players=True)
        self.assertEqual((), modern.declared_player_attacks.players)
        with self.assertRaises(ValueError):
            commit_attack_declaration(CombatState(), self.cards(), controller='A',
                assignments=(assignment,), preserve_bands=True, capture_declared_players=True)

    def test_checkpoint_and_manifest_keep_historical_absence_and_reject_mismatch(self):
        from quorune.model import GameState, GameConfig
        from quorune.record_state_provenance import format_state_versions, validate_state_versions
        state = GameState('declaration-state', GameConfig(seed=1), {}, {}, {}, {}, [], None, None)
        payload = state.to_dict()
        self.assertEqual(1, payload['declare_attack_cast_version'])
        self.assertEqual(1, format_state_versions(state)['declare_attack_cast_version'])
        validate_state_versions({'format': format_state_versions(state)}, state)
        old = dict(payload); old.pop('declare_attack_cast_version')
        loaded = GameState.from_dict(old)
        self.assertIsNone(loaded.declare_attack_cast_version)
        self.assertNotIn('declare_attack_cast_version', loaded.to_dict())
        self.assertEqual(old, loaded.to_dict())
        validate_state_versions({'format': format_state_versions(loaded)}, loaded)
        for wrong in (None, True, 0, 2, '1'):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                validate_state_versions({'format': {**format_state_versions(state),
                    'declare_attack_cast_version': wrong}}, state)


class DeclareAttackCastHandlerTests(unittest.TestCase):
    def descriptor(self, require=True):
        from quorune.semantic_runtime.declare_attack_cast import DeclareAttackCastRestrictionHandler
        handler = DeclareAttackCastRestrictionHandler()
        return {'handler_id': handler.handler_id, 'schema_version': 1,
            'event': handler.event, 'restriction': DeclareAttackCastSpec(require).to_dict()}

    def test_handler_decodes_only_its_complete_versioned_restriction(self):
        from quorune.semantic_runtime.declare_attack_cast import DeclareAttackCastRestrictionHandler
        handler = DeclareAttackCastRestrictionHandler()
        self.assertEqual(DeclareAttackCastSpec(True), handler.validate(self.descriptor()))
        for mutation in ({'schema_version': True}, {'event': 'cast.permission'},
                {'restriction': {'schema_version': 1, 'require_attacked': 1}}, {'extra': True}):
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                handler.validate({**self.descriptor(), **mutation})

    def test_shared_cast_proposal_verdict_uses_actual_caster_and_face(self):
        from quorune.compiled_cast_timing import compiled_self_cast_restriction_reason
        from quorune.carddb import CardRecord
        record = CardRecord(oracle_id='fixture:cast-window-shared', name='Window witness',
            mana_cost='{W}', mana_value=1, type_line='Instant', oracle_text='Draw a card.',
            power=None, toughness=None, loyalty=None, defense=None, colors=('W',),
            color_identity=('W',), keywords=(), produced_mana=(), layout='normal',
            released_at=None, legalities={'commander': 'legal'}, faces=(), raw={})
        program = SimpleNamespace(provenance={'face_id': 'front'}, handlers=[self.descriptor()])
        state = DeclareAttackCastWindowModelTests().state()
        host = SimpleNamespace(state=state, card_record=lambda card: record,
            semantic_program_is_current_trusted=lambda candidate: True,
            semantics=SimpleNamespace(runtime_handler_programs_for_oracle=lambda *a, **kw: (program,)))
        self.assertIsNone(compiled_self_cast_restriction_reason(host, 'B', object()))
        self.assertEqual('declare_attackers_cast_window',
            compiled_self_cast_restriction_reason(host, 'C', object()))
        state.step = 'declare_blockers'
        self.assertEqual('declare_attackers_cast_window',
            compiled_self_cast_restriction_reason(host, 'B', object()))


class DeclareAttackCastWindowCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.registry = load_default_capability_registry()

    def record(self, restriction):
        return CardRecord(oracle_id='fixture:attack-step-cast', name='Attack Step Witness',
            mana_cost='{W}', mana_value=1, type_line='Instant',
            oracle_text=restriction + '\nDraw a card.', power=None, toughness=None,
            loyalty=None, defense=None, colors=('W',), color_identity=('W',), keywords=(),
            produced_mana=(), layout='normal', released_at=None,
            legalities={'commander': 'legal'}, faces=(), raw={})

    def compile(self, restriction):
        return compile_oracle_card(self.record(restriction), capability_registry=self.registry,
            capability_profile='commander_review')

    def test_printed_step_and_declared_player_attack_are_complete_intrinsic_restrictions(self):
        for restriction in (
            'Cast this spell only during the declare attackers step.',
            "Cast this spell only during the declare attackers step and only if you've been attacked this step.",
        ):
            with self.subTest(restriction=restriction):
                ir = self.compile(restriction)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node, = (n for n in ir.faces[0].nodes if n.event == 'cast.restriction')
                self.assertEqual('playable', node.active_zone)
                self.assertEqual(restriction, node.text)
                self.assertTrue(node.handlers)

    def test_unknown_phase_and_incompatible_history_clauses_remain_material(self):
        for restriction in (
            "Cast this spell only during a mystery step and only if you've been attacked this step.",
            "Cast this spell only during the declare attackers step and only if you attacked last turn.",
            "Cast this spell only during the declare attackers step and only if an unknown private event happened.",
        ):
            with self.subTest(restriction=restriction):
                self.assertNotEqual('exact', self.compile(restriction).status)


ORIGINALS = ('Defiant Stand', "Warrior's Stand", 'Just Fate', 'Rally the Troops',
    "Champion's Victory", 'Eightfold Maze', 'Teleport', 'Remove', 'Scorching Winds',
    'Treetop Defense', 'Command of Unsummoning')


class DeclareAttackCastWindowNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = seat_actions.RecipientControlNativeTests.reject
    finish = seat_actions.RecipientControlNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'attack-window.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/declare-attack-cast-window-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Declared-attack cast windows',
            [DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls): cls.db.close(); cls.temporary.cleanup()

    def scene(self, name):
        s = self.session(508200 + len(name))
        source = self.add(s.engine, name, seat='B', zone='hand')
        first = self.add(s.engine, 'Grizzly Bears', ref='declared-first')
        second = self.add(s.engine, 'Grizzly Bears', ref='declared-second')
        own = self.add(s.engine, 'Grizzly Bears', seat='B', ref='defending-body')
        walker = self.add(s.engine, 'Jace Beleren', seat='B'); walker.counters['loyalty'] = 3
        bounce = self.add(s.engine, 'Unsummon', zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
        for n, land in enumerate(('Plains', 'Island', 'Island', 'Island', 'Forest', 'Mountain', 'Wastes')):
            self.add(s.engine, land, seat='B', ref='defender-mana-' + str(n))
        self.add(s.engine, 'Island', ref='attack-response-mana')
        first.acquired_control_turn_count = second.acquired_control_turn_count = -1
        if name == 'Rally the Troops': own.tapped = True
        self.assert_closed(s, source)
        s.state.config.semantic_policy = 'trusted_only'
        s.engine.permissions.invalidate_current(); s.state.pending_decision = None
        s.state.active_player = 'A'; s.state.started = True
        s.state.phase = 'precombat_main'; s.state.step = 'main'; s.state.phase_index = 3
        s.engine._grant_priority('A'); s.engine.pump(); self.checkpoint(s)
        return s, source, first, second, own, walker, bounce, private

    def offered(self, s, source):
        self.priority(s, 'B')
        current = s.state.cards[source.object_id]
        rows = s.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
        return any(row['id'] == 'cast:' + current.ref for row in rows)

    def reject_cast(self, s, source, targets=()):
        self.priority(s, 'B')
        self.assertFalse(self.offered(s, source))
        self.reject(s, 'B', {'action_id': 'cast:' + source.ref, 'targets': list(targets), 'pay': 'auto'})

    def original(self, name):
        s, source, first, second, own, walker, bounce, private = self.scene(name)
        self.reject_cast(s, source, [] if name in ("Warrior's Stand", 'Rally the Troops',
            'Scorching Winds', 'Treetop Defense') else [first.ref])
        self.attack(s, [(first, 'B'), (second, 'C')])
        self.assertTrue(self.offered(s, source))
        targets = [] if name in ("Warrior's Stand", 'Rally the Troops', 'Scorching Winds', 'Treetop Defense') else [first.ref]
        if name == 'Command of Unsummoning': targets.append(second.ref)
        self.cast(s, 'B', source, targets)
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        if name in ('Just Fate', 'Eightfold Maze'):
            self.assertEqual('graveyard', s.state.cards[first.object_id].zone)
        elif name in ("Champion's Victory", 'Remove', 'Command of Unsummoning'):
            self.assertEqual('hand', s.state.cards[first.object_id].zone)
            if name == 'Command of Unsummoning': self.assertEqual('hand', s.state.cards[second.object_id].zone)
        elif name == 'Defiant Stand':
            self.assertEqual((3, 5), tuple(s.engine._numeric_stat(first.object_id, k) for k in ('power', 'toughness')))
            self.assertFalse(s.state.cards[first.object_id].tapped)
        elif name == "Warrior's Stand":
            self.assertEqual((4, 4), tuple(s.engine._numeric_stat(own.object_id, k) for k in ('power', 'toughness')))
        elif name == 'Rally the Troops': self.assertFalse(s.state.cards[own.object_id].tapped)
        elif name == 'Scorching Winds':
            self.assertEqual([1, 1, 0], [s.state.cards[c.object_id].marked_damage for c in (first, second, own)])
        elif name == 'Treetop Defense': self.assertIn('reach', s.engine._combat_keywords(s.state.cards[own.object_id]))
        elif name == 'Teleport':
            self.assertFalse(s.engine._can_block(s.state.cards[first.object_id], s.state.cards[own.object_id])[0])
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.finish(s, private)

    def test_originals_cast_only_in_the_printed_window_and_execute_complete_bodies(self):
        for name in ORIGINALS:
            with self.subTest(name=name): self.original(name)

    def test_fresh_original_witness_includes_declared_occurrence_and_shared_guard(self):
        self.original("Warrior's Stand")

    def test_shared_guard_and_declaration_capture_mutations_are_killed(self):
        method = 'test_fresh_original_witness_includes_declared_occurrence_and_shared_guard'
        for symbol, replacement in (
            ('quorune.rules.casting.proposal.compiled_self_cast_restriction_reason', lambda *a, **k: None),
            ('quorune.attack_transition_engine_adapter.declare_attack_cast_version', lambda value: None),
        ):
            with self.subTest(symbol=symbol), patch(symbol, replacement):
                with self.assertRaises(AssertionError): getattr(type(self)(method), method)()

    def test_runtime_dependency_withdrawal_and_compiler_omission_fail_closed(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.declare_attack_cast_model import DECLARE_ATTACK_CAST_CAPABILITY
        s, source, *_ = self.scene('Defiant Stand')
        program, = [p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
            if p.event == 'cast.restriction']
        self.assertTrue(bind_semantic_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')['strict'])
        deps = tuple(c for c in program.capability_dependencies if c != DECLARE_ATTACK_CAST_CAPABILITY)
        # A capability-closed program requires a nonempty direct dependency set.
        # An unrelated trusted owner cannot stand in for the omitted restriction.
        deps = deps or ('continuous.characteristics.fixed_public_state',)
        changed = replace(program, capability_dependencies=deps,
            capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
        bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + DECLARE_ATTACK_CAST_CAPABILITY, bound['blockers'])
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id'] == DECLARE_ATTACK_CAST_CAPABILITY).update(
            status='blocked', blockers=['withdrawn declaration-window authority'])
        self.assertNotEqual('exact', compile_oracle_card(self.db.lookup('Defiant Stand'),
            capability_registry=CapabilityRegistry(raw), capability_profile='commander_review').status)
        with patch('quorune.compiler.self_cast_restriction_nodes.self_cast_restriction_nodes', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(self.db.lookup('Defiant Stand'),
                capability_registry=self.registry, capability_profile='commander_review').status)

    def test_other_player_or_planeswalker_attack_does_not_mean_caster_was_attacked(self):
        for planeswalker in (False, True):
            with self.subTest(planeswalker=planeswalker):
                s, source, first, second, own, walker, bounce, private = self.scene('Defiant Stand')
                self.attack(s, [(first, walker.ref if planeswalker else 'C')])
                self.reject_cast(s, source, [first.ref])
                self.finish(s, private)

    def test_unconditional_teleport_window_does_not_require_an_attack_on_its_caster(self):
        s, source, first, second, own, walker, bounce, private = self.scene('Teleport')
        self.attack(s, [(first, 'C')])
        self.assertTrue(self.offered(s, source)); self.cast(s, 'B', source, [first.ref])
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertFalse(s.engine._can_block(s.state.cards[first.object_id], s.state.cards[own.object_id])[0])
        self.finish(s, private)

    def test_declared_attack_still_qualifies_after_the_attacker_leaves_combat(self):
        s, source, first, second, own, walker, bounce, private = self.scene("Warrior's Stand")
        self.attack(s, [(first, 'B'), (second, 'C')])
        self.cast(s, 'A', bounce, [first.ref]); self.resolve_top(s)
        self.assertNotIn(first.object_id, s.state.combat.attackers)
        self.assertEqual(('B', 'C'), s.state.combat.declared_player_attacks.players)
        self.assertTrue(self.offered(s, source)); self.cast(s, 'B', source)
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual(4, s.engine._numeric_stat(own.object_id, 'power'))
        self.finish(s, private)

    def test_later_step_does_not_keep_the_attackers_step_permission(self):
        s, source, first, second, own, walker, bounce, private = self.scene('Rally the Troops')
        self.attack(s, [(first, 'B')])
        self.until(s, lambda current: current.state.step == 'declare_blockers')
        if s.state.pending_decision.kind == 'combat.blockers':
            command = {'action_id': 'block', 'blocks': {}}
            accepted = s.act(s.pending_principals()[0], command); self.assertTrue(accepted.ok, accepted.summary)
        self.reject_cast(s, source)
        self.finish(s, private)


if __name__ == '__main__': unittest.main()
