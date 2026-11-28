import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.activation.battlefield_grants import BATTLEFIELD_CREATURE_ACTIVATION_GRANT_CAPABILITY
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from quorune.semantic_runtime.context import SemanticNodeError
from scripts.build_test_database import build_fixture_database


SOURCE = 'Marvin, Murderous Mimic'
PROFILE = 'commander_review'
GRANT_PREFIX = 'battlefield-creature:'


class BattlefieldActivationGrantTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / 'battlefield-creature-grants.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures' / name for name in (
            'battlefield-activation-grant-cards.json', 'bound-effect-program-cards.json',
            'activation-view-equivalence-cards.json', 'public-value-cost-cards.json',
            'activation-public-state-cards.json', 'bound-static-declaration-cards.json',
            'granted-death-return-cards.json', 'composite-quoted-grant-cards.json', 'activated-self-transform-cards.json',
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = 'Generic Bound Commander'
        cls.deck = DeckDefinition('Live battlefield ability grants', [
            DeckEntry(commander, 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], [commander])

    def grants(self, game, recipient):
        live = game.state.cards[recipient.object_id]
        return [ability for ability in game.engine._activated_abilities(live)
                if ability.ability_id.startswith(GRANT_PREFIX)]

    def options(self, game, seat, recipient):
        self.priority(game, seat)
        rows = game.packet(f'pilot:{seat}', full=True)['decision']['ctx']['legal']['actions']
        prefix = f'activate:{recipient.ref}:{GRANT_PREFIX}'
        return [row for row in rows if row['id'].startswith(prefix)]

    def accept(self, game, seat, action, **kwargs):
        result = game.act(f'pilot:{seat}', {'action_id':action,'pay':'auto',**kwargs})
        self.assertTrue(result.ok, result.summary)

    def test_exact_source_contract_and_malformed_handler_fail_closed(self):
        source = self.db.lookup(SOURCE, fuzzy=False)
        options = {'capability_registry':self.registry, 'capability_profile':PROFILE}
        exact = compile_oracle_card(source, **options)
        self.assertEqual('exact',exact.status,exact.material_residuals)
        node, = exact.faces[0].nodes
        self.assertEqual('characteristics.evaluate', node.event)
        self.assertEqual(source.oracle_text[node.span.start:node.span.end],node.text)
        whole = compile_best_available_card_program(self.db,source,semantic_registry=SemanticRegistry(),**options)
        result = bind_card_program_runtime(whole,capability_registry=self.registry,profile=PROFILE)
        self.assertTrue(result['strict_capability_ready'], result['blockers'])
        program, = whole.abilities
        with self.assertRaisesRegex(SemanticNodeError, 'closed static descriptor'):
            replace(program, handlers=[{**program.handlers[0], 'extra': True}])
        omitted = replace(program,capability_dependencies=(),capability_closure=None)
        report = bind_semantic_program_runtime(omitted,capability_registry=self.registry,profile=PROFILE)
        self.assertIn('capability:undeclared_runtime_dependency:'+BATTLEFIELD_CREATURE_ACTIVATION_GRANT_CAPABILITY,report['blockers'])
        for text in (source.oracle_text.replace('you control', 'opponents control'),
                     source.oracle_text.replace("don't have the same name", 'have the same name')):
            opened=replace(source,oracle_text=text)
            self.assertNotEqual('exact',compile_oracle_card(opened,**options).status)
        with patch('quorune.compiler.battlefield_activation_grant_nodes.battlefield_activation_grant_node', return_value=None):
            self.assertFalse(compile_oracle_card(source,**options).faces[0].nodes[0].exact)

    def test_distinct_life_and_mana_costs_use_recipient_and_current_donor_names(self):
        game = self.session(6612601)
        engine = game.engine
        marvin = self.add(engine, SOURCE)
        first = self.add(engine, 'Llanowar Elves', ref='eligible-elf-one')
        second = self.add(engine, 'Llanowar Elves', ref='eligible-elf-two')
        same_name = self.add(engine, 'Llanowar Elves', ref='same-name-donor')
        opponent = self.add(engine, 'Llanowar Elves', seat='B', ref='opponent-elf')
        frog = self.add(engine, 'Spore Frog')
        self.assert_closed(game,marvin)
        first.tapped = second.tapped = True
        same_name.annotations['copy_overrides'] = {**engine._copyable_characteristics(same_name), 'name':SOURCE}
        marvin.acquired_control_turn_count = game.state.players['A'].turns_begun
        self.prepare(game)
        self.checkpoint(game)
        grants = self.grants(game,marvin)
        self.assertEqual(2,len([ability for ability in grants if ability.mana_ability]))
        self.assertEqual(3,len({ability.ability_id for ability in grants}))
        offered = self.options(game,'A',marvin)
        self.assertEqual([f'activate:{marvin.ref}:{next(a.ability_id for a in grants if a.sacrifice_source)}'],
                         [row['id'] for row in offered])
        before = authoritative_state_hash(game.state)
        illegal = f'activate:{marvin.ref}:{next(a.ability_id for a in grants if a.mana_ability)}'
        self.assertFalse(game.act('pilot:A',{'action_id':illegal}).ok)
        self.assertEqual(before,authoritative_state_hash(game.state))
        self.accept(game,'A',offered[0]['id'])
        self.assertEqual('graveyard',game.state.cards[marvin.object_id].zone)
        self.assertEqual('battlefield',game.state.cards[frog.object_id].zone)
        game = self.replay(game,load=True)
        self.resolve_top(game)
        self.replay(game)

    def test_inherited_mana_taps_the_recipient_and_preserves_actor_permission(self):
        game = self.session(6612602)
        engine=game.engine
        marvin = self.add(engine, SOURCE)
        marvin.acquired_control_turn_count = -1
        elf = self.add(engine, 'Llanowar Elves')
        elf.tapped = True
        self.assert_closed(game,marvin)
        self.prepare(game)
        self.checkpoint(game)
        ability, = self.grants(game,marvin)
        self.assertTrue(ability.tap_source)
        opponent_offer = self.options(game,'B',marvin)
        self.assertEqual([],opponent_offer)
        offer, = self.options(game,'A',marvin)
        self.accept(game,'A',offer['id'])
        self.assertEqual(1,game.state.players['A'].mana_pool['G'])
        self.assertEqual(0,game.state.players['B'].mana_pool['G'])
        self.assertTrue(game.state.cards[marvin.object_id].tapped)
        self.assertTrue(game.state.cards[elf.object_id].tapped)
        event = next(item for item in reversed(game.state.events) if item.code == 'mana.ability')
        self.assertEqual(marvin.ref,event.details['source'])
        self.replay(game,load=True)

    def test_paid_grant_resolves_after_donor_leaves_without_a_stale_new_offer(self):
        game = self.session(6612603)
        engine = game.engine
        marvin = self.add(engine,SOURCE)
        hydra = self.add(engine,'Feral Hydra')
        hydra.counters['+1/+1'] = 1
        bounce = self.add(engine,'Unsummon',seat='C',zone='hand')
        self.assert_closed(game,marvin)
        self.prepare(game,actor='B',active='B',mana={'C':3})
        game.state.players['C'].mana_pool['U'] = 1
        self.checkpoint(game)
        donor_ability, = engine._activated_abilities(hydra)
        offer, = self.options(game,'B',marvin)
        self.accept(game,'B',offer['id'])
        pending, = game.state.stack
        self.assertEqual('B',pending.controller)
        self.assertEqual('A',game.state.cards[marvin.object_id].controller)
        self.assertEqual((marvin.object_id,engine._semantic_key_for_ability(hydra,donor_ability)),
                         (pending.source_object_id,pending.semantic_key))
        self.cast_for(game,'C',bounce,[hydra.ref])
        self.resolve_top(game)
        self.assertEqual('hand',game.state.cards[hydra.object_id].zone)
        self.assertEqual([],self.grants(game,marvin))
        self.priority(game,'B')
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act('pilot:B',{'action_id':offer['id'],'pay':'auto'}).ok)
        self.assertEqual(before,authoritative_state_hash(game.state))
        game = self.replay(game,load=True)
        self.resolve_top(game)
        self.assertEqual({'+1/+1':1},game.state.cards[marvin.object_id].counters)
        self.assertEqual({},game.state.cards[hydra.object_id].counters)
        self.replay(game)

    def test_control_and_ability_loss_remove_live_donors_and_recipient_grants(self):
        for effect in ('Act of Treason',"Kasmina's Transmutation"):
            with self.subTest(effect=effect):
                game = self.session(6612604 + int(effect.startswith('Kasmina')))
                engine = game.engine
                marvin = self.add(engine,SOURCE)
                hydra = self.add(engine,'Feral Hydra')
                hydra.counters['+1/+1']=1
                response = self.add(engine,effect,seat='B',zone='hand')
                self.assert_closed(game,marvin)
                self.assert_closed(game,response)
                self.prepare(game,actor='B',active='B',mana={'C':5,'R':1,'U':1})
                self.checkpoint(game)
                self.assertEqual(1,len(self.grants(game,marvin)))
                self.cast_for(game,'B',response,[hydra.ref])
                self.resolve_top(game)
                self.assertEqual([],self.grants(game,marvin))
                self.replay(game,load=True)

    def test_live_dependency_order_uses_donor_compatibility_and_terminates_cycles(self):
        for earlier_is_creature in (True,False):
            with self.subTest(earlier_is_creature=earlier_is_creature):
                game = self.session(6612606 + earlier_is_creature)
                engine = game.engine
                first = self.add(engine,SOURCE,ref='first-activation-grantee')
                second = self.add(engine,SOURCE,ref='second-activation-grantee')
                elf = self.add(engine,'Llanowar Elves')
                elf.tapped = True
                first.acquired_control_turn_count = second.acquired_control_turn_count = -1
                self.assert_closed(game,first)
                # Seed a named copy in the replay checkpoint. Its copiable static
                # component is identical; its current name and timestamp differ.
                copy = engine._copyable_characteristics(first)
                second.annotations['copy_overrides'] = {**copy,'name':'Other name fixture', 'type_line':'Artifact Creature — Toy'}
                if not earlier_is_creature:
                    aura = self.add(engine,'One with the Stars',zone='hand')
                self.prepare(game,mana={'C':3,'U':1})
                self.checkpoint(game)
                if not earlier_is_creature:
                    self.cast_for(game,'A',aura,[first.ref])
                    self.resolve_top(game)
                many, few = ((second,first) if earlier_is_creature else (first,second))
                self.assertEqual(1,len(self.grants(game,few)))
                grants = self.grants(game,many)
                self.assertEqual(2,len(grants))
                self.assertEqual(2,len({ability.ability_id for ability in grants}))
                self.assertTrue(all(ability.mana_ability for ability in grants))
                refreshed = self.grants(game,many)
                self.assertEqual([ability.ability_id for ability in grants],
                                 [ability.ability_id for ability in refreshed])
                offer = self.options(game,'A',many)[0]
                self.accept(game,'A',offer['id'])
                self.assertEqual(1,game.state.players['A'].mana_pool['G'])
                self.assertTrue(game.state.cards[many.object_id].tapped)
                self.assertFalse(game.state.cards[few.object_id].tapped)
                self.replay(game,load=True)

    def test_live_donor_reads_existing_layer_six_activated_grants(self):
        game = self.session(6612609)
        engine = game.engine
        marvin = self.add(engine,SOURCE)
        marvin.acquired_control_turn_count = -1
        donor = self.add(engine,'Memnite')
        equipment = self.add(engine,"Wolfhunter's Quiver")
        self.assert_closed(game,marvin)
        self.assert_closed(game,equipment)
        self.prepare(game,mana={'C':5})
        self.checkpoint(game)
        self.assertEqual([],self.grants(game,marvin))
        equips = [row for row in game.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                  if row['id'].startswith(f'activate:{equipment.ref}:')]
        equip, = [row for row in equips if donor.ref in row.get('target_schema',{}).get('legal_refs',())]
        self.accept(game,'A',equip['id'],targets=[donor.ref])
        self.resolve_top(game)
        grants = self.grants(game,marvin)
        self.assertEqual(2,len(grants))
        offer, = [row for row in self.options(game,'A',marvin)
                  if 'B' in row.get('target_schema',{}).get('legal_refs',())]
        self.accept(game,'A',offer['id'],targets=['B'])
        self.resolve_top(game)
        self.assertEqual(39,game.state.players['B'].life)
        self.assertTrue(game.state.cards[marvin.object_id].tapped)
        self.assertFalse(game.state.cards[donor.object_id].tapped)
        self.replay(game,load=True)
