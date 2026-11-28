"""Native paid declarations under the original temporary defender tax."""

from dataclasses import replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.protected_attack_payment_model import CAPABILITY, ResolvedProtectedAttackPayment
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class TemporaryProtectedAttackPaymentTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "protected-attack-payment.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "shared-type-library-cards.json",
            fixtures / "attached-stat-activation-prohibition-cards.json",
            fixtures / "temporary-protected-attack-payment-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        ruler = "Generic Bound Commander"
        cls.deck = DeckDefinition("Temporary attack payment", [
            DeckEntry(ruler, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [ruler])

    def advance(self, game, *, seat, kind):
        for _ in range(500):
            game.engine.pump()
            decision = game.state.pending_decision
            if (game.state.active_player == seat and decision is not None
                    and decision.kind == kind and decision.actors == [seat]):
                return
            self.assertIsNotNone(decision)
            if decision.kind == "priority":
                command = {"action_id": "pass"}
            elif decision.kind == "combat.attackers":
                command = {"action_id": "attack", "attackers": {}}
            elif decision.kind == "combat.blockers":
                command = {"action_id": "block", "blocks": {}}
            elif decision.kind == "cleanup.discard":
                principal = game.pending_principals()[0]
                context = game.packet(principal, full=True)["decision"]["ctx"]
                command = {"action_id": "discard", "cards": [row["id"] for row in context["hand"][:context["count"]]]}
            else:
                self.fail(f"Unexpected real turn decision {decision.kind}")
            completed = game.act(game.pending_principals()[0], command)
            self.assertTrue(completed.ok, completed.summary)
        self.fail("The requested real turn did not arrive")

    def test_annex_static_declares_only_its_exact_phyrexian_scope(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.declaration_costs import parse_declaration_cost_line
        from quorune.declaration_fragments import DeclarationCostTemplate, PHYREXIAN_DEFENDER_TAX_CAPABILITY

        card = self.db.lookup("Norn's Annex", fuzzy=False)
        line = card.oracle_text.splitlines()[-1]
        fragment = parse_declaration_cost_line(line).template
        self.assertIsNotNone(fragment)
        self.assertEqual((('attack',), 'source_controller', True, (('W', 1),), 2),
            (fragment.declarations, fragment.scope, fragment.includes_planeswalkers, fragment.mana, fragment.phyrexian_life))
        self.assertEqual(fragment, DeclarationCostTemplate.from_dict(fragment.to_dict()))
        for changed in ({'includes_planeswalkers': False}, {'phyrexian_life': 1},
                {'printed_cost': '{w/u/p}'}, {'mana': {'U': 1}}, {'source_condition': 'source_untapped'}):
            with self.assertRaises(ValueError):
                DeclarationCostTemplate.from_dict({**fragment.to_dict(), **changed})
        for text in (line.replace('{W/P}', '{W/P}{W/P}'),
                line.replace('{W/P}', '{W/U/P}'), 'As long as this creature is untapped, ' + line,
                line.replace(' or planeswalkers you control', ' or battles you protect')):
            self.assertFalse(parse_declaration_cost_line(text).exact, text)
        ordinary = parse_declaration_cost_line(line.replace('{W/P}', '{2}')).template
        self.assertNotIn('phyrexian_life', ordinary.to_dict())
        game = self.session(508019333)
        source = self.add(game.engine, "Norn's Annex", zone='hand')
        self.assert_closed(game, source)
        program, = game.engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertEqual('combat.declaration', program.event)
        self.assertIn(PHYREXIAN_DEFENDER_TAX_CAPABILITY, program.capability_dependencies)
        retained = tuple(cap for cap in program.capability_dependencies if cap != PHYREXIAN_DEFENDER_TAX_CAPABILITY)
        modified = replace(program, capability_dependencies=retained,
            capability_closure=self.registry.closure(retained, profile='commander_review').to_dict())
        bound = bind_semantic_program_runtime(modified, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(bound['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + PHYREXIAN_DEFENDER_TAX_CAPABILITY, bound['blockers'])

    def test_annex_cost_choices_lock_each_attacker_source_and_defender(self):
        from quorune.declaration_costs import DeclarationCost, lock_declaration_payment
        from quorune.errors import GameRuleError

        first = DeclarationCost('first', 'a', 'P', 'A', (('W', 1),), phyrexian_life=2)
        second = DeclarationCost('second', 'a', 'P', 'A', (('W', 1),), phyrexian_life=2)
        third = DeclarationCost('third', 'b', 'WALKER', 'A', (('W', 1),), phyrexian_life=2)
        unselected = DeclarationCost('unselected', 'a', 'T', 'A', (('W', 1),), phyrexian_life=2)
        fixed = DeclarationCost('fixed', 'a', 'P', 'A', (('GENERIC', 1),))
        costs = (first, second, third, unselected, fixed)
        attacks = {'a': 'P', 'b': 'WALKER'}
        methods = {'first': 'mana', 'second': 'life', 'third': 'life'}
        mana, consumed, life, receipt = lock_declaration_payment(costs, attacks, payer='A', phyrexian_payments=methods)
        self.assertEqual((1, 1, 4), (mana['W'], mana['GENERIC'], life))
        self.assertEqual((first, second, third, fixed), consumed)
        self.assertEqual(methods, receipt)
        for forged in (None, {}, ['first'], {**methods, 'unselected': 'life'},
                {**methods, 'fixed': 'life'}, {**methods, 'first': True}, {**methods, 'second': 'free'}):
            with self.assertRaises(GameRuleError):
                lock_declaration_payment(costs, attacks, payer='A', phyrexian_payments=forged)

    def replay_annex(self, game, case, *, load=False):
        from quorune.record import replay_record
        from quorune.session import CommanderSession

        saved = os.environ.get('QUORUNE_ANNEX_RECORD_PARENT')
        directory = Path(saved or self.temporary.name)
        if saved:
            fixture = directory / 'fixture-cards.sqlite3'
            if not fixture.exists():
                fixture.write_bytes(self.db.path.read_bytes())
        path = directory / ('annex-' + case)
        game.save(path)
        self.assertEqual(3, json.loads((path / 'manifest.json').read_bytes())['schema_version'])
        result = replay_record(path, self.db, verify=True)
        expected = authoritative_state_hash(game.state)
        self.assertTrue(result['ok'], result)
        self.assertEqual('command_replay', result['mode'])
        self.assertEqual(expected, result['final_state_hash'])
        if saved:
            proof = directory / (path.name + '.verification.json')
            proof.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        if load:
            resumed = CommanderSession.load(self.db, path)
            self.assertEqual(expected, authoritative_state_hash(resumed.state))
            return resumed

    def annex_reject(self, game, command, *, principal='pilot:B'):
        state = authoritative_state_hash(game.state)
        result = game.act(principal, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(state, authoritative_state_hash(game.state))

    def test_annex_actual_cast_and_two_sources_tax_player_and_walker_per_attacker(self):
        from quorune.choice_forms import _phyrexian_declaration_payment_field

        game = self.session(508019334)
        engine = game.engine
        spell = self.add(engine, "Norn's Annex", zone='hand', ref='ANNEX-CAST')
        tapped = self.add(engine, "Norn's Annex", ref='ANNEX-TAPPED')
        tapped.tapped = True
        bodies = tuple(self.add(engine, 'Grizzly Bears', seat='B', ref=f'ANNEX-ATTACKER-{i}') for i in range(3))
        lands = tuple(self.add(engine, 'Plains', seat='B', ref=f'ANNEX-WHITE-{i}') for i in range(2))
        walkers = [engine.create_token(seat, name=f'{seat} Witness Walker', characteristics={
            'type_line': 'Token Planeswalker — Test', 'loyalty': '4',
        })[0] for seat in 'AC']
        battle, = engine.create_token('C', name='Annex Excluded Battle', battle_protector='A',
            characteristics={'type_line': 'Token Battle — Siege', 'defense': '3'})
        game.state.players['B'].life = 6
        self.assert_closed(game, spell)
        self.prepare(game, mana={'C': 3})
        self.checkpoint(game)
        before_life = game.state.players['A'].life
        rows = game.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        quoted = next(row for row in rows if row['id'] == 'cast:' + spell.ref)
        choice, = [row for row in quoted['cost_options'] if row.get('additional_life') == 4]
        result = game.act('pilot:A', {'action_id': quoted['id'], 'cost_option': choice['id'], 'pay': 'auto'})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual('battlefield', game.state.cards[spell.object_id].zone)
        self.assertEqual(before_life - 4, game.state.players['A'].life)
        self.advance(game, seat='B', kind='combat.attackers')
        ctx = game.packet('pilot:B', full=True)['decision']['ctx']
        costs = ctx['declaration_costs']
        self.assertIn(battle, ctx['defenders'])
        self.assertIn(walkers[1], ctx['defenders'])
        self.assertEqual({(body.ref, target, source.ref) for body in bodies
            for target in ('A', walkers[0]) for source in (spell, tapped)},
            {(row['variable'], row['option'], row['source']) for row in costs})
        self.assertEqual(12, len(costs))
        self.assertTrue(all(row['kind'] == 'phyrexian' and row['mana'] == {'W': 1} and row['life'] == 2
            and row['methods'] == ['mana', 'life'] for row in costs))
        field = _phyrexian_declaration_payment_field(ctx)
        self.assertEqual('phyrexian_payments', field['name'])
        self.assertEqual({row['id'] for row in costs}, {row['value'] for row in field['rows']})
        self.assertIsNone(game.packet('pilot:C', full=True)['decision'])
        game = self.replay_annex(game, 'mixed-pending', load=True)
        attacks = {bodies[0].ref: 'A', bodies[1].ref: walkers[0], bodies[2].ref: battle}
        selected = [row for row in costs if attacks.get(row['variable']) == row['option']]
        methods = {row['id']: 'life' if row['source'] == tapped.ref else 'mana' for row in selected}
        command = {'action_id': 'attack', 'attackers': attacks, 'pay': 'auto', 'phyrexian_payments': methods}
        self.annex_reject(game, command, principal='pilot:C')
        self.annex_reject(game, {**command, 'phyrexian_payments': {}})
        self.annex_reject(game, {**command, 'phyrexian_payments': {row['id']: 'life' for row in selected}})
        self.annex_reject(game, {**command, 'phyrexian_payments': {row['id']: 'mana' for row in selected}})
        unselected = next(row['id'] for row in costs if row not in selected)
        self.annex_reject(game, {**command, 'phyrexian_payments': {**methods, unselected: 'life'}})
        self.assertTrue(all(not game.state.cards[land.object_id].tapped for land in lands))
        result = game.act('pilot:B', command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, game.state.players['B'].life)
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in lands))
        event = next(row for row in reversed(game.state.events) if row.code == 'combat.attack' and row.actor == 'B')
        self.assertEqual(attacks, event.details['attackers'])
        self.assertEqual(methods, event.details['phyrexian_payments'])
        self.assertEqual(4, event.details['life_payment'])
        self.assertEqual({'W': 2}, event.details['payment'])
        self.replay_annex(game, 'mixed-committed')

    def test_annex_low_life_offer_and_submission_agree(self):
        game = self.session(508019335)
        annex = self.add(game.engine, "Norn's Annex", ref='ANNEX-AT-ONE-LIFE')
        body = self.add(game.engine, 'Grizzly Bears', seat='B', ref='ANNEX-LOW-LIFE-ATTACKER')
        land = self.add(game.engine, 'Plains', seat='B', ref='ANNEX-LOW-LIFE-WHITE')
        game.state.players['B'].life = 1
        self.assert_closed(game, annex)
        self.prepare(game, mana={})
        self.checkpoint(game)
        self.advance(game, seat='B', kind='combat.attackers')
        cost, = game.packet('pilot:B', full=True)['decision']['ctx']['declaration_costs']
        self.assertEqual(['mana'], cost['methods'])
        game = self.replay_annex(game, 'life-one-pending', load=True)
        command = {'action_id': 'attack', 'attackers': {body.ref: 'A'}, 'pay': 'auto'}
        self.annex_reject(game, {**command, 'phyrexian_payments': {cost['id']: 'life'}})
        accepted = game.act('pilot:B', {**command, 'phyrexian_payments': {cost['id']: 'mana'}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(1, game.state.players['B'].life)
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.replay_annex(game, 'life-one-committed')

    def test_paid_split_attack_survives_the_creatures_departure_and_expires_on_next_turn(self):
        game = self.session(508010913)
        engine = game.engine
        source = self.add(engine, "Forbidding Spirit", zone="hand", ref="RESOLVING-PROTECTION")
        bounce = self.add(engine, "Unsummon", zone="hand", ref="REMOVE-SOURCE")
        attackers = [self.add(engine, "Grizzly Bears", seat="B", ref=f"ATTACKER-{index}") for index in range(2)]
        lands = [self.add(engine, "Plains", seat="B", ref=f"ATTACK-PAYMENT-{index}") for index in range(2)]
        walkers = [engine.create_token(seat, name=f"{seat} Public Walker", characteristics={
            "type_line": "Token Planeswalker — Test", "loyalty": "4",
        })[0] for seat in "AC"]
        for card in (source, bounce):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 2, "W": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"U": 1})
        self.assertIsNone(self.resolve(game))
        rule, = [effect for effect in game.state.continuous_effects
                 if isinstance(effect, ResolvedProtectedAttackPayment)]
        self.assertEqual("A", rule.expires_on_next_turn_of)
        self.cast_for(game, "A", bounce, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[source.object_id].zone)

        self.advance(game, seat="B", kind="combat.attackers")
        costs = game.state.pending_decision.payload_by_actor["B"]["declaration_costs"]
        self.assertEqual({(card.ref, destination) for card in attackers for destination in ("A", walkers[0])},
                         {(row["variable"], row["option"]) for row in costs})
        self.assertTrue(all(row["mana"] == {"GENERIC": 2} and row["source"] == rule.effect_id for row in costs))
        self.assertIsNone(game.packet("pilot:C", full=True)["decision"])
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        attempt = {"a": "attack", "atk": {attackers[0].ref: "A", attackers[1].ref: walkers[0]}, "pay": "auto"}
        self.assertFalse(game.act("pilot:C", attempt).ok)
        self.assertFalse(game.act("pilot:B", attempt).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertTrue(all(not game.state.cards[land.object_id].tapped for land in lands))
        split = {**attempt, "atk": {attackers[0].ref: walkers[0], attackers[1].ref: walkers[1]}}
        result = game.act("pilot:B", split)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in lands))
        self.assertIsNotNone(game.state.cards[attackers[0].object_id].attacking)
        self.assertIsNotNone(game.state.cards[attackers[1].object_id].attacking)
        self.advance(game, seat="A", kind="priority")
        self.assertEqual("A", game.state.active_player)
        self.assertFalse(any(isinstance(effect, ResolvedProtectedAttackPayment) for effect in game.state.continuous_effects))
        self.replay(game, load=True)

    def test_original_attack_payment_requires_the_exact_owner_scope_and_duration(self):
        card = self.db.lookup("Forbidding Spirit", fuzzy=False)
        def compile_card(record, registry=self.registry):
            return compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
        whole = compile_card(card)
        self.assertEqual("exact", whole.status, whole.material_residuals)
        self.assertTrue(any(CAPABILITY in node.capability_dependencies for face in whole.faces for node in face.nodes))
        for old, new in (("until your next turn", "until next end step"),
                         ("or planeswalkers you control", "or battles you protect"),
                         ("for each of those creatures", "to attack once"),
                         ("When this creature enters", "Whenever this creature attacks")):
            altered = replace(card, oracle_text=card.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_card(altered).status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        selected = next(row for row in raw["capabilities"] if row["id"] == CAPABILITY)
        selected.update(status="blocked", blockers=["Withdraw the protected-defender payment authority."])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_card(card, registry).status)
        with patch("quorune.compiler.protected_attack_payment_templates.next_turn_attack_payment_template", return_value=None):
            self.assertNotEqual("exact", compile_card(card).status)
