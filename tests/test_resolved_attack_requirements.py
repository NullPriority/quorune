"""Resolved attack-if-able requirements apply to each combat of this turn."""
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from inherited_composition_fixture import InheritedNativeFixture
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from unittest.mock import patch
import test_recipient_control as seat_actions

ORIGINALS = ('Into the Fray', 'Basandra, Battle Seraph', 'Heckling Fiends',
    "Imps' Taunt", 'Rage Nimbus', 'Boiling Blood')


class ResolvedAttackRequirementCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'resolved-attacks.sqlite3'
        build_fixture_database(ROOT / 'tests/fixtures/resolved-attack-requirement-cards.json', path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls): cls.db.close(); cls.temporary.cleanup()

    def compile(self, record):
        return compile_oracle_card(record, capability_registry=self.registry,
            capability_profile='commander_review')

    def test_six_originals_compile_the_complete_this_turn_requirement(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                ir = self.compile(self.db.lookup(name))
                self.assertEqual('exact', ir.status, ir.material_residuals)
                self.assertTrue(any('Target creature attacks this turn if able.' in node.text
                    and (node.effects or node.handlers) for face in ir.faces for node in face.nodes))

    def test_unrepresented_duration_recipient_and_extra_demand_remain_material(self):
        record = self.db.lookup('Boiling Blood')
        for text in ('Target creature attacks next turn if able.',
                'Target creature attacks this turn if able and attacks an opponent other than you if able.',
                'All creatures attack this turn if able.',
                'Target creature attacks this turn if able unless its controller pays {1}.'):
            with self.subTest(text=text):
                self.assertNotEqual('exact', self.compile(replace(record, oracle_text=text)).status)


class ResolvedAttackRequirementNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = seat_actions.RecipientControlNativeTests.reject
    finish = seat_actions.RecipientControlNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name) / 'attack-native.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/resolved-attack-requirement-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Resolved attack requirements',
            [DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls): cls.db.close(); cls.temporary.cleanup()

    def scene(self, name='Boiling Blood', *, tax=False, pacifism=False, haste=False, splice=False):
        s = self.session(508611 + len(name) + tax + pacifism)
        activated = name in ('Basandra, Battle Seraph', 'Heckling Fiends', 'Rage Nimbus')
        source = self.add(s.engine, name, seat='B', zone='battlefield' if activated else 'hand')
        target = self.add(s.engine, 'Grizzly Bears', ref='required-attacker')
        target.acquired_control_turn_count = -1
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
        bounce = self.add(s.engine, 'Unsummon', zone='hand')
        blink = self.add(s.engine, 'Cloudshift', zone='hand')
        assault = self.add(s.engine, 'Relentless Assault', zone='hand')
        pacify = self.add(s.engine, 'Pacifism', zone='hand')
        if splice: self.add(s.engine, 'Reach Through Mists', seat='B', zone='hand', ref='arcane-host')
        for seat in 'AB':
            for index, land in enumerate(('Mountain',)*6 + ('Island', 'Plains', 'Swamp', 'Forest', 'Forest')):
                self.add(s.engine, land, seat=seat, ref=seat+'-requirement-mana-'+str(index))
        if tax:
            for seat in 'BCD': self.add(s.engine, 'Ghostly Prison', seat=seat, ref='prison-'+seat)
        if haste: self.add(s.engine, 'Fervor')
        self.assert_closed(s, source); s.state.config.semantic_policy = 'trusted_only'
        s.engine.permissions.invalidate_current(); s.state.pending_decision = None
        s.state.active_player = 'A'; s.state.started = True
        s.state.phase = 'precombat_main'; s.state.step = 'main'; s.state.phase_index = 3
        s.engine._grant_priority('A'); s.engine.pump(); self.checkpoint(s)
        return s, source, target, private, bounce, blink, assault, pacify

    def impose(self, s, source, target):
        if source.zone == 'hand': self.cast(s, 'B', source, [target.ref])
        else:
            self.priority(s, 'B')
            action = next(a for a in s.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
                if a['id'].startswith('activate:'+source.ref+':'))
            command = {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'}
            self.reject(s, 'D', command)
            result = s.act('pilot:B', command); self.assertTrue(result.ok, result.summary)

    def declaration(self, s):
        self.until(s, lambda current: current.state.pending_decision is not None
            and (current.state.pending_decision.kind == 'combat.attackers'
                or current.state.phase == 'combat' and current.state.step == 'declare_attackers'
                and current.state.combat.attackers_declared))

    def omit(self, s, *, allowed):
        self.declaration(s); command = {'action_id': 'attack', 'attackers': {}}
        if s.state.combat.attackers_declared:
            self.assertTrue(allowed, 'A required legal attacker must not be automatically omitted')
            self.assertFalse(s.state.combat.attackers)
            return
        before = authoritative_state_hash(s.state)
        self.reject(s, 'D', command)
        result = s.act('pilot:A', command); self.assertEqual(allowed, result.ok, result.summary)
        if not allowed: self.assertEqual(before, authoritative_state_hash(s.state))

    def original(self, name):
        s, source, target, private, *_ = self.scene(name)
        hand = len(s.state.players['B'].zones['hand']); self.impose(s, source, target)
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        if name == 'Boiling Blood': self.assertEqual(hand, len(s.state.players['B'].zones['hand']))
        self.omit(s, allowed=False)
        # The effect's controller is a legal defender: this instruction is not goad.
        self.attack(s, [(target, 'B')])
        self.assertEqual('B', s.state.combat.attackers[target.object_id]); self.finish(s, private)

    def test_all_six_original_spells_and_activations_require_an_attack_without_forcing_a_defender(self):
        for name in ORIGINALS:
            with self.subTest(name=name): self.original(name)

    def test_fresh_original_witness_requires_the_runtime_requirement_owner(self):
        self.original('Boiling Blood')

    def test_actual_arcane_splice_preserves_the_requirement_and_selected_source_in_hand(self):
        s, source, target, private, *_ = self.scene('Into the Fray', splice=True)
        host = next(c for c in s.state.cards.values() if c.ref == 'arcane-host')
        self.assert_closed(s, host); self.priority(s, 'B')
        offer = next(a for a in s.packet('pilot:B', full=True)['decision']['ctx']['legal']['actions']
            if a['id'] == 'cast:'+host.ref)
        self.assertIn('prepare_splice', offer['choice_schema'])
        result = s.act('pilot:B', {'action_id': offer['id'], 'prepare_splice': True})
        self.assertTrue(result.ok, result.summary); self.assertEqual('selection.splice_cast', s.state.pending_decision.kind)
        self.reject(s, 'D', {'action_id': 'add:'+source.ref})
        result = s.act('pilot:B', {'action_id': 'add:'+source.ref}); self.assertTrue(result.ok, result.summary)
        result = s.act('pilot:B', {'action_id': 'cast', 'targets': [target.ref], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual('hand', s.state.cards[source.object_id].zone)
        self.assertEqual(['draw', 'grant_declaration_restriction_until_end_of_turn'],
            [effect['op'] for effect in s.state.stack[-1].context['cast_option_effects']])
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.omit(s, allowed=False); self.attack(s, [(target, 'B')]); self.finish(s, private)

    def test_resolution_owner_omission_mutant_is_killed(self):
        method = 'test_fresh_original_witness_requires_the_runtime_requirement_owner'
        with patch('quorune.continuous_effect_state.active_resolution_declaration_rule_effects', return_value=()):
            with self.assertRaises(AssertionError): getattr(type(self)(method), method)()

    def test_runtime_minimum_owners_and_compiler_producer_cannot_be_withheld(self):
        from quorune.temporary_attack_requirement_model import TEMPORARY_ATTACK_REQUIREMENT_CAPABILITY
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s = self.session(508614); source = self.add(s.engine, 'Boiling Blood', seat='B', zone='hand')
        self.assert_closed(s, source)
        program, = [p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
            if p.event == 'resolve']
        broadened = replace(program, target_schema={**program.target_schema, 'types_any': ['creature', 'artifact']})
        self.assertFalse(bind_semantic_program_runtime(broadened, capability_registry=self.registry,
            profile='commander_review')['strict'])
        for cap in (TEMPORARY_ATTACK_REQUIREMENT_CAPABILITY, 'combat.declaration.typed_components',
                'continuous.resolution.declaration_rules_until_end_of_turn', 'target.revalidate_resolution'):
            with self.subTest(cap=cap):
                deps = tuple(c for c in program.capability_dependencies if c != cap)
                changed = replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry,
                    profile='commander_review')
                self.assertFalse(bound['strict']); self.assertIn('capability:undeclared_runtime_dependency:'+cap, bound['blockers'])
                raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
                next(row for row in raw['capabilities'] if row['id'] == cap).update(status='blocked', blockers=['withdrawn attack requirement owner'])
                self.assertNotEqual('exact', compile_oracle_card(self.db.lookup('Boiling Blood'),
                    capability_registry=CapabilityRegistry(raw), capability_profile='commander_review').status)
        with patch('quorune.compiler.temporary_declaration_templates.temporary_attack_requirement_template', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(self.db.lookup('Boiling Blood'),
                capability_registry=self.registry, capability_profile='commander_review').status)

    def test_optional_attack_costs_are_not_forced_and_attack_prohibitions_win(self):
        for tax in (True, False):
            with self.subTest(tax=tax):
                s, source, target, private, bounce, blink, assault, pacify = self.scene(tax=tax)
                self.impose(s, source, target); self.assertIsNone(self.resolve(s))
                if not tax: self.cast(s, 'A', pacify, [target.ref]); self.assertIsNone(self.resolve(s))
                self.omit(s, allowed=True); self.finish(s, private)

    def test_a_blinked_creature_is_a_new_incarnation_even_when_it_can_attack(self):
        s, source, target, private, bounce, blink, assault, pacify = self.scene(haste=True)
        self.impose(s, source, target); self.assertIsNone(self.resolve(s)); old = target.logical_object_id
        self.cast(s, 'A', blink, [target.ref]); self.assertIsNone(self.resolve(s))
        self.assertNotEqual(old, s.state.cards[target.object_id].logical_object_id)
        self.assertIn('haste', s.engine._combat_keywords(s.state.cards[target.object_id]))
        self.omit(s, allowed=True); self.finish(s, private)

    def test_illegal_sole_target_creates_no_requirement_on_a_recast_creature(self):
        s, source, target, private, bounce, blink, assault, pacify = self.scene(haste=True)
        self.impose(s, source, target); self.cast(s, 'A', bounce, [target.ref]); self.resolve_top(s)
        self.assertIsNone(self.resolve(s)); self.assertEqual('hand', s.state.cards[target.object_id].zone)
        self.cast(s, 'A', s.state.cards[target.object_id]); self.assertIsNone(self.resolve(s))
        self.omit(s, allowed=True); self.finish(s, private)

    def test_same_turn_additional_combat_requires_another_attack_then_requirement_expires(self):
        s, source, target, private, bounce, blink, assault, pacify = self.scene()
        self.impose(s, source, target); self.assertIsNone(self.resolve(s)); self.attack(s, [(target, 'B')])
        self.until(s, lambda current: current.state.phase == 'postcombat_main'
            and current.state.pending_decision is not None and current.state.pending_decision.kind == 'priority')
        s = self.replay(s, load=True)
        # Prepared public owner boundary: extra-combat card production is outside
        # this family. Keep the same physical turn and the actual resolved rule,
        # then exercise real offers/commands from a fresh combat checkpoint.
        from quorune.model import CombatState
        from quorune.tap_state import untap_permanent
        untap_permanent(s.engine, s.state.cards[target.object_id], actor='A', reason='Prepared second-combat fixture')
        s.engine.permissions.invalidate_current(); s.state.pending_decision = None
        s.state.priority_player = None; s.state.priority_passes = []
        s.state.phase_index = 5; s.state.phase = 'combat'; s.state.step = 'declare_attackers'
        s.state.combat = CombatState(); s.engine.pump(); self.checkpoint(s)
        self.assertFalse(s.state.cards[target.object_id].tapped); self.omit(s, allowed=False)
        self.attack(s, [(s.state.cards[target.object_id], 'C')])
        self.assertEqual(('C',), s.state.combat.declared_player_attacks.players)
        self.finish_current_turn(s)
        from quorune.declaration_rule_effects import ResolutionDeclarationRuleEffect
        self.assertFalse(any(isinstance(effect, ResolutionDeclarationRuleEffect)
            and getattr(effect.restriction, 'kind', None) == 'attack_each_combat'
            for effect in s.state.continuous_effects or ()))
        self.finish(s, private)

    def finish_current_turn(self, s):
        turn = s.state.turn_sequence
        for _ in range(100):
            s.engine.pump()
            if s.state.turn_sequence > turn: return
            decision = s.state.pending_decision; principal = s.pending_principals()[0]
            if decision.kind == 'cleanup.discard':
                ctx = s.packet(principal, full=True)['decision']['ctx']
                command = {'action_id': 'discard', 'cards': [row['id'] for row in ctx['hand'][:ctx['count']]]}
            elif decision.kind == 'priority': command = {'action_id': 'pass'}
            elif decision.kind == 'combat.blockers': command = {'action_id': 'block', 'blocks': {}}
            else: self.fail('Unexpected prepared-combat ending decision '+decision.kind)
            result = s.act(principal, command); self.assertTrue(result.ok, result.summary)
        self.fail('Prepared combat turn did not complete')


class ResolvedAttackRequirementModelTests(unittest.TestCase):
    def test_closed_versioned_spec_and_existing_journal_round_trip(self):
        from quorune.temporary_attack_requirement_model import TemporaryAttackRequirementSpec
        from quorune.declaration_rule_effects import ResolutionDeclarationRuleEffect
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        spec = TemporaryAttackRequirementSpec(); self.assertEqual(spec, type(spec).from_dict(spec.to_dict()))
        effect = ResolutionDeclarationRuleEffect('E1', 'S1', 1, spec.to_requirement(), (ContinuousObjectIdentity('C1', 'C1@1'),))
        self.assertEqual(effect.to_dict(), type(effect).from_dict(effect.to_dict()).to_dict())
        self.assertEqual('attack_each_combat', effect.restriction.kind)
        for changed in ({**spec.to_dict(), 'schema_version': True}, {**spec.to_dict(), 'kind': 'goad'},
                {**spec.to_dict(), 'extra': 1}):
            with self.assertRaises(ValueError): type(spec).from_dict(changed)


class ChosenAttackTurnTests(InheritedNativeFixture, unittest.TestCase):
    source_name = "Chosen Attack Test Advisor"

    @classmethod
    def source_fixture(cls):
        return {"cards": [{
            "name": cls.source_name, "oracle_id": "c30099a9-8efa-4dbf-aee7-f4e18ba01edf",
            "type_line": "Creature — Advisor", "mana_cost": "{1}{W}", "cmc": 2,
            "color_identity": ["W"], "power": "1", "toughness": "1",
            "oracle_text": "{T}: Target opponent chooses any number of creatures they control. During that player's "
                           "next turn, the chosen creatures attack if able, and other creatures can't attack. "
                           "At the beginning of that turn's end step, destroy each of the chosen creatures "
                           "that didn't attack this turn. Activate only during your turn.",
        }]}

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "chosen-attack-turn.sqlite3"
        fixture = path.with_suffix(".json")
        payload = {**cls.source_fixture(), "schema_version": 1}
        payload["cards"].append({
            "name": "Chosen Attack Test Defender", "oracle_id": "66321018-5ef9-4961-a785-0d1255da3379",
            "type_line": "Creature — Wall", "power": "0", "toughness": "6",
            "oracle_text": "Defender", "keywords": ["Defender"],
        })
        fixture.write_text(json.dumps(payload), encoding="utf-8")
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "resolved-attack-requirement-cards.json",
            fixtures / "own-combat-entwine-cards.json",
            fixture,
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Chosen attack turn", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def assert_rejected(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, command)
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_chosen_attack_turn_whole_activation_is_closed_and_guarded(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.chosen_attack_turn_model import CHOOSE, COMMIT, DESTROY
        from quorune.errors import GameRuleError
        from quorune.stack_resolution import arbiter_resolution_effects

        game = self.session(603710)
        source = self.add(game.engine, self.source_name)
        self.assert_closed(game, source)
        record = self.db.lookup(self.source_name)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        program, = game.engine.semantics.programs_for_oracle(source.oracle_id)
        effect, = program.effects
        for invalid in (
            replace(program, target_schema={**program.target_schema, "player_relation": "any"}),
            replace(program, effects=({**effect, "player": "$controller"},)),
            replace(program, cost_schema={**program.cost_schema, "tap_source": False}),
        ):
            result = bind_semantic_program_runtime(invalid, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(result["strict"], result)
        for op in (CHOOSE, COMMIT, DESTROY):
            with self.assertRaises(GameRuleError):
                arbiter_resolution_effects([{"op": op}])

    def test_chosen_attack_turn_replays_choice_and_exact_delayed_destruction(self):
        from quorune.chosen_attack_turn_model import DELAYED_FIELD, ResolvedChosenAttackTurn

        game = self.session(603711)
        for seat in "ABCD":
            for object_id in game.state.players[seat].zones["hand"][:3]:
                game.engine.move_card(object_id, "library", log=False, reason="Initial hand fixture")
        source = self.add(game.engine, self.source_name, ref="attack-selection-source")
        spare = self.add(game.engine, self.source_name, ref="attack-selection-spare")
        ready = self.add(game.engine, "Generic Bound Body", seat="C", ref="chosen-ready")
        wall = self.add(game.engine, "Chosen Attack Test Defender", seat="C", ref="chosen-defender")
        blinking = self.add(game.engine, "Grizzly Bears", seat="C", ref="chosen-to-blink")
        omitted = self.add(game.engine, "Grizzly Bears", seat="C", ref="unchosen")
        blink = self.add(game.engine, "Cloudshift", seat="C", zone="hand")
        beating = self.add(game.engine, "Savage Beating", seat="C", zone="hand")
        secret = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="selection-private-card")
        for index, name in enumerate(("Plains", "Mountain", "Mountain", "Mountain", "Mountain")):
            self.add(game.engine, name, seat="C", ref=f"choice-mana-{index}")
        for card in (source, spare, ready, wall, blinking, omitted):
            card.acquired_control_turn_count = -1
        self.prepare(game)
        self.checkpoint(game)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
        command = {"action_id": action["id"], "targets": ["C"], "pay": "auto"}
        self.assert_rejected(game, "B", command)
        self.assert_rejected(game, "A", {**command, "targets": ["A"]})
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        choice = self.resolve(game)
        self.assertEqual(("semantic.choice", ["C"]), (choice.kind, choice.actors))
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertIsNone(game.packet("pilot:A", full=True)["decision"])
        self.assertNotIn(secret.ref, json.dumps(game.packet("pilot:C", full=True)))
        self.assertIn(secret.ref, json.dumps(game.packet("pilot:D", full=True)))
        self.assert_rejected(game, "A", {"action_id": "choose", "cards": [ready.ref]})
        self.assert_rejected(game, "C", {"action_id": "choose", "cards": [ready.ref, ready.ref]})
        self.assert_rejected(game, "C", {"action_id": "choose", "cards": [source.ref]})
        game = self.replay(game, load=True)
        result = game.act("pilot:C", {"action_id": "choose", "cards": [ready.ref, wall.ref, blinking.ref]})
        self.assertTrue(result.ok, result.summary)
        restriction, = [effect for effect in game.state.continuous_effects if isinstance(effect, ResolvedChosenAttackTurn)]
        self.assertEqual(("C", 3), (restriction.turn.player, len(restriction.turn.selected)))
        before_blink = game.state.cards[blinking.object_id].logical_object_id
        self.cast_for(game, "C", blink, [blinking.ref])
        self.resolve_top(game)
        self.assertNotEqual(before_blink, game.state.cards[blinking.object_id].logical_object_id)

        self.until(game, lambda current: current.state.active_player == "B" and current.state.step == "upkeep")
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(row["id"].startswith(f"activate:{spare.ref}:") for row in actions))
        self.until(game, lambda current: current.state.active_player == "B" and current.state.step == "end_step")
        self.assertEqual("battlefield", game.state.cards[wall.object_id].zone)
        self.assertFalse(any(DELAYED_FIELD in item.context for item in game.state.stack))

        for combat in range(2):
            self.until(game, lambda current: current.state.active_player == "C"
                       and current.state.pending_decision.kind == "combat.attackers")
            packet = game.packet("pilot:C", full=True)["decision"]
            domains = packet["ctx"]["declaration_constraints"]["domains"]
            self.assertEqual({ready.ref}, set(domains))
            self.assert_rejected(game, "C", {"action_id": "attack", "attackers": {}})
            self.assert_rejected(game, "C", {"action_id": "attack", "attackers": {omitted.ref: "B"}})
            attack = game.act("pilot:C", {"action_id": "attack", "attackers": {ready.ref: "B"}})
            self.assertTrue(attack.ok, attack.summary)
            if combat == 0:
                self.priority(game, "C")
                result = game.act("pilot:C", {"action_id": "cast:" + beating.ref,
                                            "modes": ["mode_2"], "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                self.resolve_top(game)
        self.until(game, lambda current: any(DELAYED_FIELD in item.context for item in current.state.stack))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[wall.object_id].zone)
        self.assertEqual(["battlefield"] * 3,
                         [game.state.cards[card.object_id].zone for card in (ready, blinking, omitted)])
        self.replay(game)

    def test_chosen_attack_turn_destruction_mutation_is_killed(self):
        with patch("quorune.rules.chosen_attack_turn.resolve_chosen_nonattackers", return_value=None):
            with self.assertRaisesRegex(AssertionError, "graveyard.*battlefield"):
                self.test_chosen_attack_turn_replays_choice_and_exact_delayed_destruction()

    def test_chosen_attack_turn_zero_choices_forbid_all_without_destruction(self):
        from quorune.chosen_attack_turn_model import DELAYED_FIELD, ResolvedChosenAttackTurn

        game = self.session(603712)
        for seat in "ABCD":
            for object_id in game.state.players[seat].zones["hand"][:3]:
                game.engine.move_card(object_id, "library", log=False, reason="Initial hand fixture")
        source = self.add(game.engine, self.source_name)
        creature = self.add(game.engine, "Generic Bound Body", seat="B", ref="empty-selection-creature")
        source.acquired_control_turn_count = creature.acquired_control_turn_count = -1
        self.prepare(game)
        self.checkpoint(game)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
        result = game.act("pilot:A", {"action_id": action["id"], "targets": ["B"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        result = game.act("pilot:B", {"action_id": "choose", "cards": []})
        self.assertTrue(result.ok, result.summary)
        restriction, = [effect for effect in game.state.continuous_effects if isinstance(effect, ResolvedChosenAttackTurn)]
        self.assertEqual((), restriction.turn.selected)
        self.until(game, lambda current: current.state.active_player == "B" and
                   current.state.step == "declare_attackers" and current.state.combat.attackers_declared)
        self.assertEqual({}, game.state.combat.attackers)
        self.until(game, lambda current: any(DELAYED_FIELD in item.context for item in current.state.stack))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
        self.replay(game)


if __name__ == '__main__': unittest.main()
