"""CR 702.6/107.4f/601.2f: printed Equip prices compose existing owners."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import test_recipient_control as recipients
import test_fixed_creature_power_damage as records
from inherited_composition_fixture import InheritedNativeFixture
from quorune.abilities import ActivatedAbility
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
from quorune.compiler.activated_ability_catalog import compile_activated_ability_catalog
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.attachment_actions import fixed_equip_ability_spec
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from scripts.build_test_database import compact_ci_fixture_paths, build_fixture_database

NAMES = ('Lashwrithe', "Transmogrant's Crown", 'Robe of the Archmagi', 'Plate Armor')


class PricedEquipCompilerTests(unittest.TestCase):
    def test_subtype_union_and_local_cost_grammar_preserve_the_whole_clause(self):
        from quorune.compiler.nonmana_equip_nodes import is_closed_nonmana_equip_program
        self.assertFalse(is_closed_nonmana_equip_program(SimpleNamespace(
            capability_dependencies=('attachment.equip.fixed_mana',), effects=())))
        spec = fixed_equip_ability_spec('Equip Shaman, Warlock, or Wizard {1}')
        self.assertEqual(['shaman', 'warlock', 'wizard'], spec.target_schema['subtypes_any'])
        for text in ('Equip Wizard or UnknownSubtype {1}', 'Equip Wizard and Shaman {1}',
                'Equip Wizard, Shaman {1}', 'Equip Wizard, Wizard, or Shaman {1}', 'Equip {2} or discard a card',
                'Equip {X}', 'Equip {2}. Invent an unknown result.'):
            self.assertIsNone(fixed_equip_ability_spec(text), text)
        registry = load_default_capability_registry()
        hand_price = replace(records.card_record('Equip {3}. This ability costs {1} less to activate if you have one or fewer cards in hand.',
            type_line='Artifact — Equipment'), keywords=('Equip',))
        self.assertEqual('exact', compile_oracle_card(hand_price, capability_registry=registry).status)
        for suffix in ('if an opponent has one or fewer cards in hand.',
                'for each other Equipment you control. Invent an unknown result.'):
            card = replace(records.card_record('Equip {3}. This ability costs {1} less to activate ' + suffix,
                type_line='Artifact — Equipment'), keywords=('Equip',))
            self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=registry).status)


class PricedEquipNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        names = {*NAMES, 'Sliver Hivelord', 'Generic Bound Plains', 'Grizzly Bears', 'Counterspell',
            'Swamp', 'Mountain', 'Island', 'Lightning Bolt', 'Unsummon',
            'Elvish Visionary', 'Killian, Ink Duelist', 'Prodigal Sorcerer'}
        paths = (*compact_ci_fixture_paths(root=ROOT), ROOT/'tests/fixtures/bound-effect-program-cards.json')
        cards = {r['name']:r for path in paths
            for r in json.loads(path.read_text(encoding='utf-8'))['cards'] if r['name'] in names}
        assert set(cards) == names, names-set(cards)
        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixture = folder/'prices.json'
        document = json.loads((ROOT/'tests/fixtures/recipient-control-cards.json').read_text(encoding='utf-8'))
        document.update(cards=list(cards.values()), rulings=[])
        fixture.write_text(json.dumps(document), encoding='utf-8')
        path = folder/'prices.sqlite3'
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Printed Equip prices', [DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)], ['Sliver Hivelord'])
    reject = recipients.RecipientControlNativeTests.reject
    finish = recipients.RecipientControlNativeTests.finish

    def scene(self, name, *, mana=None, zone='battlefield', before=None):
        s = self.session(2021032 + len(name))
        source = self.add(s.engine, name, zone=zone)
        body = self.add(s.engine, 'Grizzly Bears')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand')
        if before:before(s, source, body)
        s.state.config.semantic_policy = 'trusted_only'
        self.assert_closed(s, source)
        self.prepare(s, mana=mana or {'C':8, 'B':2})
        self.checkpoint(s)
        return s, source, body, private

    def equip_offer(self, s, source, *, cost=None):
        self.priority(s, 'A')
        ability = next(a for a in s.engine._activated_abilities(s.state.cards[source.object_id])
            if a.builtin_semantic_key == 'builtin:equip' and (cost is None or a.cost_text == cost))
        rows = s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        row = next(r for r in rows if r['id'] == 'activate:' + source.ref + ':' + ability.ability_id)
        return ability, row

    def equip(self, s, source, body, *, cost=None, option=None):
        _ability, row = self.equip_offer(s, source, cost=cost)
        command = {'action_id':row['id'], 'targets':[body.ref], 'pay':'auto'}
        if option is not None:command['cost_option'] = option.option_id
        self.reject(s, 'B', command)
        result = s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)

    def test_originals_bind_normally_and_roundtrip_their_complete_price_catalogs(self):
        for name in NAMES:
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                program = _compile_best_available_card_program_from_ir(self.db, card, ir,
                    semantic_registry=SemanticRegistry(), capability_registry=self.registry, capability_profile='commander_review')
                bound = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'], bound)
                self.assertEqual('capability_closed', bound['trust_basis'])
                for abilities in compile_activated_ability_catalog(card).values():
                    for ability in abilities:
                        self.assertEqual(ability, ActivatedAbility.from_dict(ability.to_dict()))
                        self.assertFalse(ability.tap_source)

    def test_actual_lashwrithe_offers_all_phyrexian_payments_then_moves_off_its_germ(self):
        for life in (0, 2, 4):
            with self.subTest(life=life):
                def before(s, source, body):self.add(s.engine, 'Swamp')
                s, source, body, private = self.scene('Lashwrithe', zone='hand', before=before)
                self.cast(s, 'A', source)
                self.assertIsNone(self.resolve(s))
                germ = s.state.cards[s.state.cards[source.object_id].attached_to]
                self.assertTrue(germ.is_token)
                self.assertEqual(1, s.engine._numeric_stat(germ.object_id, 'toughness'))
                ability, row = self.equip_offer(s, source)
                self.assertEqual({0, 2, 4}, {o.life_payment for o in ability.mana_cost_options})
                self.assertEqual(3, len(row['cost_options']))
                self.reject(s, 'A', {'action_id':row['id'], 'targets':[body.ref], 'cost_option':'unknown', 'pay':'auto'})
                option = next(o for o in ability.mana_cost_options if o.life_payment == life)
                mana_before = s.state.players['A'].mana_pool['B']
                self.equip(s, source, body, option=option)
                self.assertEqual(40-life, s.state.players['A'].life)
                self.assertEqual(mana_before-(2-life//2), s.state.players['A'].mana_pool['B'])
                self.assertEqual(germ.object_id, s.state.cards[source.object_id].attached_to)
                s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                self.assertEqual(body.object_id, s.state.cards[source.object_id].attached_to)
                self.assertEqual(3, s.engine._numeric_stat(body.object_id, 'power'))
                self.assertNotEqual('battlefield', s.state.cards[germ.object_id].zone)
                self.assertFalse(any(germ.object_id in objects for p in s.state.players.values() for objects in p.zones.values()))
                self.finish(s, private)

    def test_actual_crown_accepts_both_prices_and_retains_its_death_draw(self):
        for color in ('GENERIC', 'B'):
            with self.subTest(color=color):
                extra = {}
                def before(s, source, body):
                    extra['bolt'] = self.add(s.engine, 'Lightning Bolt', seat='B', zone='hand')
                    self.add(s.engine, 'Mountain', seat='B')
                s, source, body, private = self.scene("Transmogrant's Crown", mana={'C':2, 'B':1}, before=before)
                ability, row = self.equip_offer(s, source)
                self.assertEqual(2, len(row['cost_options']))
                option = next(o for o in ability.mana_cost_options if o.requirements[color])
                self.equip(s, source, body, option=option)
                self.assertIsNone(s.state.cards[source.object_id].attached_to)
                self.assertFalse(s.state.cards[source.object_id].tapped)
                self.assertIsNone(self.resolve(s))
                self.assertEqual((0,1) if color == 'GENERIC' else (2,0),
                    (s.state.players['A'].mana_pool['C'], s.state.players['A'].mana_pool['B']))
                self.assertEqual((4,2), tuple(s.engine._numeric_stat(body.object_id, stat) for stat in ('power','toughness')))
                hand = len(s.state.players['A'].zones['hand'])
                self.cast(s, 'B', extra['bolt'], [body.ref])
                s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                self.assertEqual('graveyard', s.state.cards[body.object_id].zone)
                self.assertEqual(hand+1, len(s.state.players['A'].zones['hand']))
                self.assertIsNone(s.state.cards[source.object_id].attached_to)
                self.finish(s, private)

    def test_actual_robe_restricted_price_accepts_each_subtype_and_rejects_an_unqualified_body(self):
        extra = {}
        def before(s, source, body):
            for name in ('Elvish Visionary', 'Killian, Ink Duelist', 'Prodigal Sorcerer'):
                extra[name] = self.add(s.engine, name)
        s, source, body, private = self.scene('Robe of the Archmagi', before=before)
        _ability, row = self.equip_offer(s, source, cost='{1}')
        self.reject(s, 'A', {'action_id':row['id'], 'targets':[body.ref], 'pay':'auto'})
        for target in extra.values():
            self.equip(s, source, target, cost='{1}')
            self.assertIsNone(self.resolve(s))
            self.assertEqual(target.object_id, s.state.cards[source.object_id].attached_to)
        self.equip(s, source, body, cost='{4}')
        s = self.replay(s, load=True)
        self.assertIsNone(self.resolve(s))
        self.assertEqual(body.object_id, s.state.cards[source.object_id].attached_to)
        self.assertEqual(1, s.state.players['A'].mana_pool['C'])
        self.finish(s, private)

    def test_actual_plate_counts_only_other_controlled_equipment_and_revalidates_after_payment(self):
        for count in (0, 2, 4):
            with self.subTest(count=count):
                extra = {}
                def before(s, source, body):
                    for i in range(count):self.add(s.engine, "Transmogrant's Crown", ref='other-equip-'+str(i))
                    self.add(s.engine, "Transmogrant's Crown", seat='B', ref='foreign-equip')
                    extra['bounce'] = self.add(s.engine, 'Unsummon', seat='B', zone='hand')
                    self.add(s.engine, 'Island', seat='B')
                price = max(0, 3-count)
                s, source, body, private = self.scene('Plate Armor', mana={'C':3}, before=before)
                _ability, row = self.equip_offer(s, source)
                self.assertEqual({'GENERIC':price} if price else {}, row['cost_summary']['m'])
                if price:
                    self.reject(s, 'A', {'action_id':row['id'], 'targets':[body.ref], 'pay':'manual', 'payment':{'C':price-1}})
                self.equip(s, source, body)
                self.assertEqual(3-price, s.state.players['A'].mana_pool['C'])
                self.assertIsNone(s.state.cards[source.object_id].attached_to)
                if count == 2:
                    self.cast(s, 'B', extra['bounce'], [body.ref])
                    self.resolve_top(s)
                s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                if count == 2:
                    self.assertEqual('hand', s.state.cards[body.object_id].zone)
                    self.assertIsNone(s.state.cards[source.object_id].attached_to)
                else:
                    self.assertEqual(body.object_id, s.state.cards[source.object_id].attached_to)
                    self.assertEqual(5, s.engine._numeric_stat(body.object_id, 'power'))
                    self.assertIn('ward {1}', s.engine._combat_keywords(s.state.cards[body.object_id]))
                self.finish(s, private)

    def test_catalog_equip_retains_independent_cost_and_attachment_authority(self):
        from quorune.compiler.nonmana_equip_nodes import is_closed_nonmana_equip_program
        for name, cost_capability in (('Lashwrithe','activation.mana_cost.fixed_complex'),
                ("Transmogrant's Crown",'activation.mana_cost.fixed_complex'),
                ('Plate Armor','activation.mana_cost.ability_local_public_count')):
            with self.subTest(name=name):
                s = self.session(202103210)
                source = self.add(s.engine, name, zone='hand')
                programs = [p for p in s.engine.semantics.programs_for_oracle(source.oracle_id)
                    if p.event == 'activate' and p.effects]
                self.assertEqual(1, len(programs))
                program, = programs
                self.assertTrue(is_closed_nonmana_equip_program(program))
                self.assertFalse(is_closed_nonmana_equip_program(replace(program, effects=[])))
                self.assertFalse(is_closed_nonmana_equip_program(replace(program,
                    cost_schema={**program.cost_schema, 'mana':{}})))
                unlabelled = replace(program, coverage=[])
                self.assertTrue(bind_semantic_program_runtime(unlabelled, capability_registry=self.registry,
                    profile='commander_review')['strict'])
                for capability in (cost_capability, 'attachment.equip.fixed_mana',
                        'attachment.action.fixed_source', 'target.revalidate_resolution'):
                    deps = tuple(c for c in program.capability_dependencies if c != capability)
                    changed = replace(unlabelled, capability_dependencies=deps,
                        capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                    result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
                    self.assertFalse(result['strict'], capability)
                    self.assertIn('capability:undeclared_runtime_dependency:'+capability, result['blockers'])

    def test_fresh_local_price_and_complex_payment_omissions_are_killed(self):
        for owner, replacement, method in (
            ('quorune.rules.activation.cost_modifiers._ability_local_mana_modifiers', lambda *a: [],
             'test_actual_plate_counts_only_other_controlled_equipment_and_revalidates_after_payment'),
            ('quorune.compiler.activation_mana_costs.fixed_complex_activation_mana_options', lambda *a: (),
             'test_originals_bind_normally_and_roundtrip_their_complete_price_catalogs')):
            with patch(owner, replacement), self.assertRaises(AssertionError):
                getattr(type(self)(method), method)()


if __name__ == '__main__':unittest.main()
