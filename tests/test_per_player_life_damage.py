"""CR107.1/120.4/608.2h: each current life total, one simultaneous batch."""
from dataclasses import FrozenInstanceError, replace
import json
from types import SimpleNamespace as Row
import unittest
from unittest.mock import patch

from quorune.announced_damage import AllocatedDamageRecipient
from quorune.fixed_damage_set_model import PlayerDamageRelation as Relation
from quorune.fractional_quantity_model import HalfQuantitySpec
from quorune.per_player_life_damage_model import PerPlayerLifeDamageSpec as Spec, BoundPerPlayerLifeDamage as Bound
from quorune.per_player_query_damage_model import PerPlayerQueryDamageSpec, BoundPerPlayerQueryDamage
from quorune.semantic_runtime.per_player_life_damage import resolve_per_player_life_damage
from quorune.compiler.per_player_life_damage import per_player_life_damage_template
from quorune.rules.per_player_life_damage import per_player_life_damage_program_dependencies


def spec(rounding='down', relation=Relation.ALL):
    return Spec(HalfQuantitySpec(rounding), relation)


def effect(value=None):
    return {'op': 'damage_fixed_set', 'source': '$source', 'amount': (value or spec()).to_dict(),
            'groups': [{'kind': 'players', 'relation': (value or spec()).relation.value}]}


class PerPlayerLifeDamageModelTests(unittest.TestCase):
    def test_fraction_and_sealed_shares_round_trip_without_changing_public_count_kinds(self):
        raw = spec(); self.assertEqual(raw, Spec.from_dict(json.loads(json.dumps(raw.to_dict()))))
        with self.assertRaises(FrozenInstanceError): raw.relation = Relation.OPPONENTS
        with self.assertRaises(ValueError): PerPlayerQueryDamageSpec.from_dict(raw.to_dict())
        bound = Bound('A', Relation.ALL, (AllocatedDamageRecipient('A', None, 6), AllocatedDamageRecipient('B', None, 1)))
        self.assertEqual(bound, Bound.from_dict(json.loads(json.dumps(bound.to_dict())))); self.assertEqual(7, bound.total)
        with self.assertRaises(ValueError): BoundPerPlayerQueryDamage.from_dict(bound.to_dict())
        self.assertEqual(0, Bound('A', Relation.ALL, ()).total)

    def test_rounding_is_independent_for_each_nonnegative_life_total(self):
        self.assertEqual([6, 1, 1, 20, 0, 0, 0], [spec().fraction.amount(v) for v in (13, 2, 3, 40, 1, 0, -3)])
        self.assertEqual([7, 1, 2, 20, 1, 0, 0], [spec('up').fraction.amount(v) for v in (13, 2, 3, 40, 1, 0, -3)])
        with self.assertRaises(ValueError): spec().fraction.amount(True)

    def test_unknown_fields_versions_wrong_domains_and_nonplayer_bound_rows_fail_closed(self):
        for change in ({'schema_version': True}, {'schema_version': 2}, {'relation': 'controller'}, {'fraction': HalfQuantitySpec('down').to_dict() | {'divisor': 3}}, {'target_index': 0}):
            with self.subTest(change=change), self.assertRaises(ValueError): Spec.from_dict(spec().to_dict() | change)
        for rows in ((AllocatedDamageRecipient('B', None, 1),) * 2, (AllocatedDamageRecipient('B', 'creature@1', 1),)):
            with self.assertRaises(ValueError): Bound('A', Relation.ALL, rows)
        with self.assertRaises(ValueError): Bound('A', Relation.OPPONENTS, (AllocatedDamageRecipient('A', None, 1),))
        for change in ({'schema_version': True}, {'schema_version': 2}, {'total': 6}):
            with self.assertRaises(ValueError): Bound.from_dict(Bound('A', Relation.ALL, ()).to_dict() | change)


class PerPlayerLifeDamageRuntimeTests(unittest.TestCase):
    def test_current_lives_are_read_once_and_bound_continuation_never_recomputes(self):
        host = Row(state=Row(players={p: Row(life=v) for p, v in zip('ABCD', (13, 2, 3, 40))}))
        item = Row(controller='A', ref='ability', context={}); snapshot = Row(recipients=tuple(Row(ref=p) for p in 'BCDA'))
        with patch('quorune.semantic_runtime.per_player_life_damage.snapshot_fixed_damage_set', return_value=snapshot):
            bound = Bound.from_dict(resolve_per_player_life_damage(host, spec().to_dict(), item))
        self.assertEqual([('B', 1), ('C', 1), ('D', 20), ('A', 6)], [(r.ref, r.amount) for r in bound.recipients])
        with patch('quorune.semantic_runtime.per_player_life_damage.current_player_life_total', side_effect=AssertionError('Sealed values must not read again')):
            self.assertEqual(bound.to_dict(), resolve_per_player_life_damage(host, bound.to_dict(), item))
            with self.assertRaises(ValueError): resolve_per_player_life_damage(host, bound.to_dict(), Row(controller='B'))

    def test_zero_one_negative_and_opponent_relation_do_not_invent_positive_damage(self):
        host = Row(state=Row(players={p: Row(life=v) for p, v in zip('ABCD', (13, 1, 0, -3))})); item = Row(controller='A')
        snapshot = Row(recipients=tuple(Row(ref=p) for p in 'BCD'))
        with patch('quorune.semantic_runtime.per_player_life_damage.snapshot_fixed_damage_set', return_value=snapshot):
            bound = Bound.from_dict(resolve_per_player_life_damage(host, spec(relation=Relation.OPPONENTS).to_dict(), item))
        self.assertEqual((), bound.recipients)

    def test_missing_and_boolean_current_life_are_rejected_by_existing_reader(self):
        from quorune.rules.live_modifier_quantities import current_player_life_total
        host = Row(state=Row(players={'A': Row(life=True)}))
        for player in ('A', 'unknown'):
            with self.assertRaises(ValueError): current_player_life_total(host, player)

    def test_quantity_uses_only_public_life_and_rejects_private_payload_selectors(self):
        secret = 'unrelated-private-library-identity'
        host = Row(state=Row(players={p: Row(life=v, zones={'library': [secret], 'hand': [secret]})
            for p, v in zip('ABCD', (13, 2, 3, 40))})); item = Row(controller='A')
        snapshot = Row(recipients=tuple(Row(ref=p) for p in 'ABCD'))
        with patch('quorune.semantic_runtime.per_player_life_damage.snapshot_fixed_damage_set', return_value=snapshot):
            first = resolve_per_player_life_damage(host, spec().to_dict(), item)
            for player in host.state.players.values(): player.zones = {'library': ['changed-hidden-card']}
            second = resolve_per_player_life_damage(host, spec().to_dict(), item)
        self.assertEqual(first, second); self.assertNotIn(secret, json.dumps(first))
        with self.assertRaises(ValueError): Spec.from_dict(spec().to_dict() | {'private_zone': 'library'})

    def test_real_value_dispatch_lowers_one_heterogeneous_intent_and_rejects_changed_roles(self):
        from quorune.semantic_runtime.values import resolve_semantic_value
        from quorune.semantic_runtime.fixed_damage_set_handlers import FixedDamageSetHandler
        from quorune.semantic_runtime.context import ReadOnlyHandlerContext, SemanticNodeError
        host = Row(state=Row(players={p: Row(life=v) for p, v in zip('ABCD', (13, 2, 3, 40))}))
        item = Row(controller='A'); snapshot = Row(recipients=tuple(Row(ref=p) for p in 'ABCD'))
        with patch('quorune.semantic_runtime.per_player_life_damage.snapshot_fixed_damage_set', return_value=snapshot):
            amount = resolve_semantic_value(host, spec().to_dict(), item)
        context = ReadOnlyHandlerContext.from_sequences(actor='A', default_reason='Life fraction damage',
            seats='ABCD', active_seats='ABCD', apnap_order='ABCD')
        instruction = {'op': 'damage_fixed_set', 'source': 'source-ref', 'amount': amount, 'groups': [{'kind': 'players', 'relation': 'all'}]}
        plan = FixedDamageSetHandler().lower(instruction, context); self.assertEqual(1, len(plan.intents))
        intent = plan.intents[0]; self.assertEqual((28, None), (intent.amount, intent.spec))
        self.assertEqual([('A', 6), ('B', 1), ('C', 1), ('D', 20)], [(r.ref, r.amount) for r in intent.allocated_recipients])
        for change in ({'groups': [{'kind': 'players', 'relation': 'opponents'}]}, {'amount': spec().to_dict()},
                {'amount': Bound('B', Relation.ALL, (AllocatedDamageRecipient('A', None, 1),)).to_dict()},
                {'amount': Bound('A', Relation.ALL, (AllocatedDamageRecipient('unknown-player', None, 1),)).to_dict()}):
            with self.subTest(change=change), self.assertRaises(SemanticNodeError): FixedDamageSetHandler().lower(instruction | change, context)

    def test_fresh_rounding_up_mutation_is_killed_after_positive_distinct_player_snapshot(self):
        host = Row(state=Row(players={'A': Row(life=13), 'B': Row(life=3)})); item = Row(controller='A')
        snapshot = Row(recipients=tuple(Row(ref=p) for p in 'AB'))
        def witness():
            with patch('quorune.semantic_runtime.per_player_life_damage.snapshot_fixed_damage_set', return_value=snapshot):
                result = Bound.from_dict(resolve_per_player_life_damage(host, spec().to_dict(), item))
            self.assertEqual([6, 1], [r.amount for r in result.recipients])
        witness()
        with patch.object(HalfQuantitySpec, 'amount', lambda _spec, value: (max(0, value) + 1) // 2):
            with self.assertRaises(AssertionError): witness()


class PerPlayerLifeDamageCompilerTests(unittest.TestCase):
    def compile_fixed(self, text):
        self.assertEqual('Example deals 1 damage to each player.', text)
        return 'fixed', ({'op': 'damage_fixed_set', 'source': '$source', 'amount': 1, 'groups': [{'kind': 'players', 'relation': 'all'}]},), None, ('cr-120-damage', 'fixed-simultaneous-damage')

    def test_complete_self_source_clause_keeps_each_player_relation_and_explicit_rounding(self):
        for players, relation in (('player', Relation.ALL), ('opponent', Relation.OPPONENTS)):
            for rounding in ('up', 'down'):
                text = f"Example deals damage to each {players} equal to half that player's life total, rounded {rounding}."
                compiled = per_player_life_damage_template(text, source_name='Example', compile_fixed=self.compile_fixed)
                self.assertIsNotNone(compiled); self.assertEqual(spec(rounding, relation).to_dict(), compiled[1][0]['amount'])
                self.assertEqual([{'kind': 'players', 'relation': relation.value}], compiled[1][0]['groups'])

    def test_other_source_target_controller_total_missing_rounding_and_extra_clause_are_excluded(self):
        text = "Example deals damage to each player equal to half that player's life total, rounded down."
        for bad in (text.replace('Example', 'Another'), text.replace('each player', 'target player'), text.replace("that player's", 'your'), text.replace(', rounded down', ''), text + ' Draw a card.'):
            self.assertIsNone(per_player_life_damage_template(bad, source_name='Example', compile_fixed=self.compile_fixed), bad)

    def test_quantity_and_frame_requirements_do_not_depend_on_coverage(self):
        values = dict(event='activate', active_zone='battlefield', ability_id='ability:ab1', effects=(effect(),), target_schema=None, coverage=(), cost_schema=None)
        required = per_player_life_damage_program_dependencies(Row(**values))
        self.assertIn('damage.batch.per_player_current_life_fraction', required); self.assertIn('damage.batch.fixed_set', required)
        for change in ({'event': 'creature.attacks'}, {'active_zone': 'hand'}, {'target_schema': {}}, {'effects': (effect(), effect())}, {'effects': ({**effect(), 'groups': [{'kind': 'players', 'relation': 'opponents'}]},)}):
            with self.subTest(change=change), self.assertRaises(ValueError): per_player_life_damage_program_dependencies(Row(**(values | change)))

    def test_real_tap_price_keeps_its_owner_and_current_life_cannot_be_used_as_a_price(self):
        from quorune.abilities import parse_activated_abilities
        from quorune.compiler.activated_costs import activated_ability_cost
        ability, = parse_activated_abilities(card_name='Example', oracle_text='{T}: Example deals 1 damage to each player.', keywords=())
        price = activated_ability_cost(ability); before = json.loads(json.dumps(price))
        values = dict(event='activate', active_zone='battlefield', ability_id='ability:ab1', effects=(effect(),),
            target_schema=None, coverage=(), cost_schema=price)
        self.assertIn('damage.batch.per_player_current_life_fraction', per_player_life_damage_program_dependencies(Row(**values)))
        self.assertEqual(before, price); self.assertTrue(price['tap_source'])
        with self.assertRaises(ValueError):
            per_player_life_damage_program_dependencies(Row(**(values | {'cost_schema': price | {'life_payment': spec().to_dict()}})))

    def test_nested_target_price_and_catalog_only_markers_cannot_hide_result_ownership(self):
        values = dict(event='activate', active_zone='battlefield', ability_id='ability:ab1', effects=(effect(),),
            target_schema=None, coverage=(), cost_schema=None, handlers=())
        self.assertIn('damage.batch.per_player_current_life_fraction', per_player_life_damage_program_dependencies(Row(**values)))
        for change in ({'effects': ({'op': 'offer_optional_effect', 'effects': [effect()]},)},
                {'effects': (), 'target_schema': {'unowned_amount': spec().to_dict()}},
                {'effects': (), 'cost_schema': {'unowned_amount': spec().to_dict()}},
                {'effects': (), 'handlers': ({'unowned_amount': spec().to_dict()},)}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                per_player_life_damage_program_dependencies(Row(**(values | change)))


from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_turn_life_loss_recovery as recovery_helpers
from quorune.per_player_life_damage_model import PER_PLAYER_LIFE_DAMAGE_CAPABILITY as CAP


class PerPlayerLifeDamageNativeTests(InheritedNativeFixture, unittest.TestCase):
    start = recovery_helpers.TurnLifeLossRecoveryNativeTests.start
    reject = recovery_helpers.TurnLifeLossRecoveryNativeTests.reject
    finish = recovery_helpers.TurnLifeLossRecoveryNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); folder = Path(cls.temporary.name)
        basic = json.loads((ROOT / 'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        basic['cards'] = [row for row in basic['cards'] if row['name'] == 'Generic Bound Plains']; basic['rulings'] = []
        path = folder / 'basic.json'; path.write_text(json.dumps(basic), encoding='utf-8')
        build_fixture_database([path, ROOT / 'tests/fixtures/per-player-life-damage-cards.json'], folder / 'life-damage.sqlite3')
        cls.db = CardDatabase(folder / 'life-damage.sqlite3'); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Per-player half-life damage', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def lives(self, s):
        return tuple(s.state.players[p].life for p in 'ABCD')

    def world(self, seed, *, lives=(13, 2, 3, 40), haste=True, active='A', extras=(), mana=None, reserve=None):
        s, private = self.start(seed); source = self.add(s.engine, 'Heartless Hidetsugu', zone='hand')
        self.assert_closed(s, source)
        if haste: self.assert_closed(s, self.add(s.engine, 'Fervor'))
        if active != 'A': self.assert_closed(s, self.add(s.engine, 'Vedalken Orrery'))
        for name, seat, zone, ref in extras: self.assert_closed(s, self.add(s.engine, name, seat=seat, zone=zone, ref=ref))
        for player, life in zip('ABCD', lives): s.state.players[player].life = life
        self.prepare(s, active=active, mana=mana or {'C': 3, 'R': 2}); self.checkpoint(s)
        before = sum(s.state.players['A'].mana_pool.values())
        self.cast_for(s, 'A', source, reserve=reserve); self.assertIsNone(self.resolve(s))
        self.assertEqual(before - 5, sum(s.state.players['A'].mana_pool.values()))
        self.assertEqual('battlefield', s.state.cards[source.object_id].zone)
        return s, private, source

    def activate(self, s, source):
        self.priority(s, 'A'); current = s.state.cards[source.object_id]
        action = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('activate:' + current.ref + ':'))
        command = {'action_id': action['id'], 'pay': 'auto'}; self.reject(s, 'D', command)
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        self.assertTrue(s.state.cards[source.object_id].tapped); self.assertEqual(1, len(s.state.stack))
        self.priority(s, 'A')
        self.assertFalse(any(a['id'].startswith('activate:' + current.ref + ':')
            for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']))
        self.reject(s, 'A', command)

    def actual_basic(self, seed, *, finish=True):
        s, private, source = self.world(seed); self.activate(s, source); self.assertIsNone(self.resolve(s))
        self.assertEqual((7, 1, 2, 20), self.lives(s)); self.assertTrue(s.state.cards[source.object_id].tapped)
        if finish: self.finish(s, private)
        return s, private

    def test_complete_original_normally_binds_tap_price_span_and_independent_runtime_minima(self):
        from quorune.oracle_ir import compile_oracle_card
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s, _ = self.start(727000); source = self.add(s.engine, 'Heartless Hidetsugu', zone='hand'); self.assert_closed(s, source)
        card = self.db.lookup('Heartless Hidetsugu'); ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals); node, = ir.faces[0].nodes
        self.assertTrue(node.cost['tap_source']); self.assertEqual(card.oracle_text[node.span.start:node.span.end], node.text)
        program = next(p for p in s.engine.semantics.programs_for_oracle(source.oracle_id) if CAP in p.capability_dependencies)
        for cap in (CAP, 'quantity_expression.current_player_value', 'damage.batch.fixed_set'):
            deps = tuple(d for d in program.capability_dependencies if d != cap)
            changed = replace(program, coverage=(), capability_dependencies=deps,
                capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
            self.assertFalse(bound['strict']); self.assertIn('capability:undeclared_runtime_dependency:' + cap, bound['blockers'])

    def test_actual_printed_three_red_red_cast_and_tap_resolve_one_unequal_player_batch_privately(self):
        import quorune.fixed_damage_set as owner
        original = owner.resolve_damage_batch; observed = []
        def batch(host, proposals, **kwargs):
            proposals = tuple(proposals); observed.append([(p.recipient.ref, p.amount) for p in proposals])
            self.assertEqual((13, 2, 3, 40), tuple(host.state.players[p].life for p in 'ABCD'))
            return original(host, proposals, **kwargs)
        with patch.object(owner, 'resolve_damage_batch', side_effect=batch): s, private = self.actual_basic(727001, finish=False)
        self.assertEqual([[('A', 6), ('B', 1), ('C', 1), ('D', 20)]], observed); self.finish(s, private)

    def test_actual_one_life_is_omitted_and_all_one_life_still_pays_tap_without_damage(self):
        for index, (lives, expected) in enumerate((((13, 1, 3, 40), (7, 1, 2, 20)), ((1, 1, 1, 1), (1, 1, 1, 1)))):
            with self.subTest(lives=lives):
                s, private, source = self.world(727010 + index, lives=lives); self.activate(s, source); self.assertIsNone(self.resolve(s))
                self.assertEqual(expected, self.lives(s)); self.assertTrue(s.state.cards[source.object_id].tapped)
                if index == 1: self.assertEqual((), tuple(s.engine._current_turn_history('damage')))
                self.finish(s, private)

    def test_actual_summoning_sickness_and_later_paid_haste_share_offer_and_command_legality(self):
        s, private, source = self.world(727020, haste=False, mana={'C': 5, 'R': 3}, reserve={'C': 2, 'R': 1},
            extras=(('Fervor', 'A', 'hand', 'later-paid-haste'),))
        self.priority(s, 'A'); current = s.state.cards[source.object_id]
        self.assertFalse(any(a['id'].startswith('activate:' + current.ref + ':')
            for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']))
        ability, = s.engine._activated_abilities(current)
        self.reject(s, 'A', {'action_id': 'activate:' + current.ref + ':' + ability.ability_id, 'pay': 'auto'})
        self.assertFalse(s.state.cards[source.object_id].tapped)
        haste = next(c for c in s.state.cards.values() if c.ref == 'later-paid-haste')
        self.cast(s, 'A', haste); self.assertIsNone(self.resolve(s)); self.activate(s, source); self.assertIsNone(self.resolve(s))
        self.assertEqual((7, 1, 2, 20), self.lives(s)); self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
        self.finish(s, private)

    def test_actual_response_life_gain_changes_only_that_players_resolution_amount(self):
        extras = (('Revitalize', 'B', 'hand', 'life-response'), ('Plains', 'B', 'battlefield', 'life-response-white'),
            ('Island', 'B', 'battlefield', 'life-response-generic'))
        s, private, source = self.world(727030, extras=extras); self.activate(s, source)
        response = next(c for c in s.state.cards.values() if c.ref == 'life-response')
        self.cast(s, 'B', response); self.resolve_top(s); self.assertEqual((13, 5, 3, 40), self.lives(s))
        self.assertIsNone(self.resolve(s)); self.assertEqual((7, 3, 2, 20), self.lives(s)); self.finish(s, private)

    def test_actual_player_concession_before_resolution_removes_that_share_and_preserves_other_players(self):
        import quorune.fixed_damage_set as owner
        s, private, source = self.world(727031); self.activate(s, source); self.priority(s, 'B')
        self.assertTrue(any(a['id'] == 'concede' for a in s.packet('pilot:B', full=True)['decision']['legal_actions']))
        result = s.act('pilot:B', {'action_id': 'concede', 'choices': {'confirm_concede': True}}); self.assertTrue(result.ok, result.summary)
        self.assertNotIn('B', s.engine.active_seats); original = owner.resolve_damage_batch; observed = []
        def batch(host, proposals, **kwargs):
            proposals = tuple(proposals); observed.append([(p.recipient.ref, p.amount) for p in proposals])
            return original(host, proposals, **kwargs)
        with patch.object(owner, 'resolve_damage_batch', side_effect=batch): self.assertIsNone(self.resolve(s))
        self.assertEqual([[('A', 6), ('C', 1), ('D', 20)]], observed)
        self.assertEqual((7, 2, 20), tuple(s.state.players[p].life for p in 'ACD')); self.finish(s, private)

    def test_actual_bounce_preserves_source_information_and_stifle_keeps_paid_tap_without_damage(self):
        for index, name in enumerate(('Unsummon', 'Stifle')):
            with self.subTest(name=name):
                extras = ((name, 'B', 'hand', 'source-response'), ('Island', 'B', 'battlefield', 'source-response-blue'))
                s, private, source = self.world(727040 + index, extras=extras); self.activate(s, source)
                response = next(c for c in s.state.cards.values() if c.ref == 'source-response')
                target = source.ref if name == 'Unsummon' else s.state.stack[-1].ref
                self.cast(s, 'B', response, [target]); self.assertIsNone(self.resolve(s))
                self.assertEqual((7, 1, 2, 20) if index == 0 else (13, 2, 3, 40), self.lives(s))
                self.assertEqual('hand' if index == 0 else 'battlefield', s.state.cards[source.object_id].zone)
                if index == 1: self.assertTrue(s.state.cards[source.object_id].tapped)
                self.finish(s, private)

    def test_actual_lifelink_with_controller_last_keeps_all_four_damage_amounts_frozen(self):
        import quorune.fixed_damage_set as owner
        extras = (('Lifelink', 'A', 'hand', 'source-lifelink'),)
        s, private, source = self.world(727050, active='B', extras=extras,
            mana={'C': 3, 'R': 2, 'W': 1}, reserve={'W': 1})
        aura = next(c for c in s.state.cards.values() if c.ref == 'source-lifelink')
        self.cast(s, 'A', aura, [source.ref]); self.assertIsNone(self.resolve(s))
        self.assertIn('lifelink', s.engine._combat_keywords(s.state.cards[source.object_id])); self.activate(s, source)
        original = owner.resolve_damage_batch; observed = []
        def batch(host, proposals, **kwargs):
            proposals = tuple(proposals); observed.append([(p.recipient.ref, p.amount) for p in proposals])
            return original(host, proposals, **kwargs)
        with patch.object(owner, 'resolve_damage_batch', side_effect=batch): self.assertIsNone(self.resolve(s))
        self.assertEqual([[('B', 1), ('C', 1), ('D', 20), ('A', 6)]], observed)
        self.assertEqual((35, 1, 2, 20), self.lives(s)); self.finish(s, private)

    def test_actual_infect_uses_damage_replacement_instead_of_losing_half_life(self):
        extras = (('Phyresis', 'A', 'hand', 'source-infect'),)
        s, private, source = self.world(727051, lives=(13, 2, 3, 18), extras=extras,
            mana={'C': 4, 'R': 2, 'B': 1}, reserve={'C': 1, 'B': 1})
        aura = next(c for c in s.state.cards.values() if c.ref == 'source-infect')
        self.cast(s, 'A', aura, [source.ref]); self.assertIsNone(self.resolve(s))
        self.assertIn('infect', s.engine._combat_keywords(s.state.cards[source.object_id])); self.activate(s, source)
        self.assertIsNone(self.resolve(s)); self.assertEqual((13, 2, 3, 18), self.lives(s))
        self.assertEqual((6, 1, 1, 9), tuple(s.state.players[p].poison for p in 'ABCD')); self.finish(s, private)

    def test_actual_post_damage_commit_fault_rolls_back_full_batch_and_retries_without_refunding_tap(self):
        import quorune.fixed_damage_set as owner
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s, private, source = self.world(727060); self.activate(s, source)
        original = owner.resolve_damage_batch; observed = []; failed = False
        def fault(host, proposals, **kwargs):
            result = original(host, proposals, **kwargs); observed.append(self.lives(s))
            raise GameRuleError('Injected after committed per-player half-life damage')
        with patch.object(owner, 'resolve_damage_batch', side_effect=fault):
            for _ in range(8):
                before = authoritative_state_hash(s.state); result = s.act(s.pending_principals()[0], {'action_id': 'pass'})
                if not result.ok:
                    self.assertIn('Injected after committed', result.summary); self.assertEqual(before, authoritative_state_hash(s.state)); failed = True; break
        self.assertTrue(failed); self.assertEqual([(7, 1, 2, 20)], observed)
        self.assertEqual((13, 2, 3, 40), self.lives(s)); self.assertTrue(s.state.cards[source.object_id].tapped)
        self.assertEqual(1, len(s.state.stack)); s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual((7, 1, 2, 20), self.lives(s)); self.finish(s, private)

    def test_fresh_native_upward_rounding_mutation_is_killed_after_positive_complete_original_actions(self):
        self.actual_basic(727061); observed = []
        def upward(_spec, value):
            observed.append(value); return (max(0, value) + 1) // 2
        with patch.object(HalfQuantitySpec, 'amount', upward):
            with self.assertRaises(AssertionError): self.actual_basic(727062)
        self.assertEqual([13, 2, 3, 40], observed)

    def test_existing_half_life_and_public_count_controls_admit_but_half_life_price_neighbor_stays_partial(self):
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        s, _ = self.start(727070)
        for name in ('Havoc Festival', 'Fraying Omnipotence', 'Acidic Soil'):
            self.assert_closed(s, self.add(s.engine, name, zone='hand'))
        card = self.db.lookup("Garza's Assassin"); semantics = SemanticRegistry()
        register_generated_programs(self.db, semantics, (card,), capability_registry=self.registry, capability_profile='commander_review',
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True, promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        program = compile_best_available_card_program(self.db, card, semantic_registry=semantics, capability_registry=self.registry, capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')['strict_capability_ready'])
        # Keep the historical evidence test ID while retaining a genuinely unsupported price neighbor.
        from quorune.compiler.recover_nodes import recover_spec_from_line
        self.assertIsNone(recover_spec_from_line('Recover—Pay half your life, rounded down.'))

    def test_genuinely_verified_fraction_player_value_and_damage_owner_withdrawals_block_whole_original(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.oracle_ir import register_generated_programs
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        doc = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json'); known = set(discover_tests(ROOT).values())
        for missing in (CAP, 'quantity_expression.current_player_value', 'damage.batch.fixed_set'):
            changed = deepcopy(raw); next(c for c in changed['capabilities'] if c['id'] == missing).update(status='blocked', blockers=['Life-fraction owner withdrawn'])
            registry = CapabilityRegistry(changed); index = build_index(registry_value=changed, declaration_source=doc, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            controls = [('Heartless Hidetsugu', False), ('Grizzly Bears', True)]
            if missing == CAP: controls += [('Havoc Festival', True), ('Fraying Omnipotence', True), ('Acidic Soil', True)]
            for name, expected in controls:
                card = self.db.lookup(name); semantics = SemanticRegistry()
                register_generated_programs(self.db, semantics, (card,), capability_registry=registry, capability_profile='commander_review',
                    promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True, promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                program = compile_best_available_card_program(self.db, card, semantic_registry=semantics, capability_registry=registry, capability_profile='commander_review')
                bound = bind_card_program_runtime(program, capability_registry=registry, profile='commander_review')
                self.assertEqual(expected, bound['strict_capability_ready'], (missing, name, bound['blockers']))


if __name__ == '__main__':
    unittest.main()
