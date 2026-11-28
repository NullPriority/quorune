"""Mana-value set predicates preserve zero, source LKI and announced X."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

from quorune.mana_value_filter import ManaValueFilter,ManaValueComparison,MANA_VALUE_FILTER_CAPABILITY
from quorune.affected_permanents import AffectedPermanentSetSpec,select_affected_permanents
from quorune.object_predicate import ObjectQuerySpec
from quorune.scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin,SCALAR_AMOUNT_CAPABILITY
from quorune.query_effect_amount_model import CastXAmountSpec,DECLARED_AMOUNT_CAPABILITY
from quorune.compiler.mana_value_destruction import mana_value_destruction_template
from quorune.compiler.scalar_effect_amounts import scalar_amount_shape_context
from quorune.compiler.declared_effect_amounts import declared_effect_amount_template
from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies


class ManaValueDestructionModelTests(unittest.TestCase):
    def test_fixed_zero_equality_and_inequality_are_closed_and_legacy_sets_keep_their_encoding(self):
        legacy=AffectedPermanentSetSpec(ObjectQuerySpec(zones=('battlefield',))).to_dict()
        self.assertNotIn('mana_value_filter',legacy)
        self.assertEqual(legacy,AffectedPermanentSetSpec.from_dict(legacy).to_dict())
        for comparison,bound,expected in ((ManaValueComparison.EQUAL,0,[True,False,False,False]),
                (ManaValueComparison.EQUAL,2,[False,False,True,False]),
                (ManaValueComparison.AT_MOST,2,[True,True,True,False]),
                (ManaValueComparison.AT_LEAST,2,[False,False,True,True])):
            predicate=ManaValueFilter(comparison,bound)
            self.assertEqual(expected,[predicate.matches(value) for value in range(4)])
            self.assertEqual(predicate,ManaValueFilter.from_dict(predicate.to_dict()))
            spec=AffectedPermanentSetSpec(ObjectQuerySpec(zones=('battlefield',)),mana_value_filter=predicate)
            self.assertEqual(spec,AffectedPermanentSetSpec.from_dict(spec.to_dict()))
        for raw in (None,True,0,{}, {'schema_version':1,'comparison':'equal','value':None}):
            with self.assertRaises(ValueError):AffectedPermanentSetSpec.from_dict({**legacy,'mana_value_filter':raw})

    def test_filter_fields_bounds_and_symbolic_operands_reject_unknown_or_ambiguous_values(self):
        valid=ManaValueFilter(ManaValueComparison.EQUAL,2).to_dict()
        for change in ({'schema_version':True},{'schema_version':2},{'comparison':'less'},
                       {'value':True},{'value':-1},{'value':'2'},{'value':2.0},{'extra':1}):
            with self.subTest(change=change),self.assertRaises(ValueError):ManaValueFilter.from_dict({**valid,**change})
        named=ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE,counter_name='charge',schema_version=2)
        for amount in (named,CastXAmountSpec()):
            predicate=ManaValueFilter(ManaValueComparison.AT_MOST,amount)
            self.assertEqual(predicate,ManaValueFilter.from_dict(predicate.to_dict()))
            self.assertIn(MANA_VALUE_FILTER_CAPABILITY,predicate.capability_dependencies)
            with self.assertRaises(ValueError):predicate.matches(0)
            spec=AffectedPermanentSetSpec(ObjectQuerySpec(zones=('battlefield',)),mana_value_filter=predicate)
            with self.assertRaises(ValueError):select_affected_permanents((),spec,actor='A',active_seats=('A','B'),apnap_order=('A','B'))
        for amount in (CastXAmountSpec(coefficient=-1),replace(named,coefficient=-1),
                       ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE,characteristic='power')):
            with self.assertRaises(ValueError):ManaValueFilter(ManaValueComparison.EQUAL,amount)

    def test_counter_operand_is_bound_to_its_source_and_the_payload_minimum_does_not_need_annotations(self):
        result=mana_value_destruction_template('Destroy each nonland permanent with mana value equal to the number of charge counters on this artifact.',source_name='Generic Artifact')
        self.assertIsNotNone(result)
        effect=result[1][0]
        predicate=ManaValueFilter.from_dict(effect['set']['mana_value_filter'])
        self.assertEqual('charge',predicate.value.counter_name)
        minimum=typed_result_amount_runtime_dependencies(effect)
        self.assertIn(MANA_VALUE_FILTER_CAPABILITY,minimum)
        self.assertIn(SCALAR_AMOUNT_CAPABILITY,minimum)
        projected=scalar_amount_shape_context(result[1],set(result[3]))
        self.assertIsNotNone(projected)
        self.assertEqual(2,projected[0][0]['set']['mana_value_filter']['value'])
        forged=deepcopy(effect)
        forged['set']['mana_value_filter']['value']=ScalarEffectAmountSpec(ScalarAmountOrigin.TARGET,characteristic='power').to_dict()
        with self.assertRaises(ValueError):typed_result_amount_runtime_dependencies(forged)

    def test_literal_and_announced_x_lowering_preserve_all_base_predicates_and_regeneration_policy(self):
        for body,comparison in (('Destroy all nonland permanents with mana value 3 or less.',ManaValueComparison.AT_MOST),
                ('Destroy each creature with mana value 4 or greater.',ManaValueComparison.AT_LEAST),
                ("Destroy each creature with mana value 3 or less. They can't be regenerated.",ManaValueComparison.AT_MOST)):
            result=mana_value_destruction_template(body,source_name='Generic Source')
            self.assertIsNotNone(result,body)
            predicate=ManaValueFilter.from_dict(result[1][0]['set']['mana_value_filter'])
            self.assertEqual(comparison,predicate.comparison)
            self.assertEqual("can't be regenerated" in body,result[1][0].get('regeneration_prohibited',False))
        text='Destroy each artifact, creature, and enchantment with mana value X or less.'
        self.assertIsNone(mana_value_destruction_template(text,source_name='Generic Source'))
        result=declared_effect_amount_template(text,source_name='Generic Source',cast_x_available=True,
            compile_fixed=lambda body:mana_value_destruction_template(body,source_name='Generic Source') or (None,(),None,()))
        self.assertIsNotNone(result)
        predicate=ManaValueFilter.from_dict(result[1][0]['set']['mana_value_filter'])
        self.assertEqual(CastXAmountSpec(),predicate.value)
        self.assertIn(DECLARED_AMOUNT_CAPABILITY,typed_result_amount_runtime_dependencies(result[1]))
        for text in ('Destroy each creature with mana value of the chosen quality.',
                'Destroy each artifact with mana value less than or equal to the number of rust counters on it.',
                'Destroy each creature with mana value X plus one.'):
            self.assertIsNone(mana_value_destruction_template(text,source_name='Generic Source',cast_x_available=True),text)

    def test_celestial_kirin_cast_value_filter_rejects_non_exact_shapes(self):
        from types import SimpleNamespace
        from quorune.cast_event_amounts import CAST_MANA_VALUE_CAPABILITY, cast_mana_value_runtime_dependencies
        from quorune.mana_value_filter import CAST_SPELL_MANA_VALUE_FILTER_CAPABILITY
        from quorune.oracle_ir import compile_oracle_card
        from test_bound_effect_programs import record

        registry = load_default_capability_registry()
        text = ("Flying\nWhenever you cast a Spirit or Arcane spell, "
                "destroy all permanents with that spell's mana value.")
        card = record(text, "Legendary Creature — Kirin Spirit")
        ir = compile_oracle_card(card, capability_registry=registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = (node for face in ir.faces for node in face.nodes if node.kind == "triggered_ability")
        self.assertEqual(text[node.span.start:node.span.end], node.text)
        self.assertIsNone(node.target_schema)
        self.assertIn(CAST_SPELL_MANA_VALUE_FILTER_CAPABILITY, node.capability_dependencies)
        effect, = node.effects
        self.assertIn(CAST_SPELL_MANA_VALUE_FILTER_CAPABILITY, typed_result_amount_runtime_dependencies(effect))
        from quorune.rules.capabilities import CapabilityRegistry

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        capability = next(row for row in raw["capabilities"]
                          if row["id"] == CAST_SPELL_MANA_VALUE_FILTER_CAPABILITY)
        capability.update(status="blocked", blockers=["The committed cast value filter was withheld"])
        withheld = CapabilityRegistry(raw)
        withheld.mark_evidence_verified(registry.evidence_fingerprint)
        refused = compile_oracle_card(card, capability_registry=withheld, capability_profile="commander_review")
        self.assertNotEqual("exact", refused.status)
        self.assertIn(CAST_SPELL_MANA_VALUE_FILTER_CAPABILITY, str(refused.material_residuals))
        scope = AffectedPermanentSetSpec.from_dict(effect["set"])
        self.assertEqual(ObjectQuerySpec(zones=("battlefield",)), scope.query)
        self.assertEqual(ManaValueComparison.EQUAL, scope.mana_value_filter.comparison)
        self.assertEqual(ScalarAmountOrigin.CAST_SPELL_MANA_VALUE, scope.mana_value_filter.value.origin)
        for choice in ({"comparison": ManaValueComparison.AT_MOST},
                       {"value": replace(scope.mana_value_filter.value, coefficient=2)},
                       {"value": replace(scope.mana_value_filter.value, binding_id="another-event")}):
            with self.subTest(choice=choice), self.assertRaises(ValueError):
                replace(scope.mana_value_filter, **choice)
        for scope_change in ({"exclude_source": True}, {"query": ObjectQuerySpec(
            zones=("battlefield",), types_all=("creature",))}):
            with self.subTest(scope_change=scope_change), self.assertRaises(ValueError):
                replace(scope, **scope_change)
        witness = SimpleNamespace(event="spell.cast", effects=(effect,), coverage=node.mechanics)
        self.assertIn(CAST_MANA_VALUE_CAPABILITY, cast_mana_value_runtime_dependencies(witness))
        witness.event = "spell.cast_or_copy"
        with self.assertRaisesRegex(ValueError, "original|normalized"):
            cast_mana_value_runtime_dependencies(witness)
        for changed in (text.replace("all permanents", "all creatures"),
                        text.replace("you cast a Spirit or Arcane spell", "you draw a card"),
                        text.replace("mana value", "power")):
            result = compile_oracle_card(record(changed, "Legendary Creature — Kirin Spirit"),
                capability_registry=registry, capability_profile="commander_review")
            self.assertNotEqual("exact", result.status)

    def test_withdrawing_filter_authority_keeps_unfiltered_destruction_available(self):
        import json
        from common import ROOT
        from test_bound_effect_programs import record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id']==MANA_VALUE_FILTER_CAPABILITY).update(status='blocked',blockers=['filter authority withdrawn'])
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        self.assertNotEqual('exact',compile_oracle_card(record('Destroy all creatures with mana value 3 or less.'),capability_registry=registry).status)
        self.assertEqual('exact',compile_oracle_card(record('Destroy all creatures.'),capability_registry=registry).status)


import json
from pathlib import Path
import tempfile
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_target_recipient_results as recipients
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class ManaValueDestructionNativeTests(InheritedNativeFixture,unittest.TestCase):
    reject=recipients.TargetRecipientNativeTests.reject
    finish=recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();folder=Path(cls.temporary.name)
        basic=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        basic['cards']=[r for r in basic['cards'] if r['name']=='Generic Bound Plains'];basic['rulings']=[]
        (folder/'basic.json').write_text(json.dumps(basic),encoding='utf-8')
        path=folder/'mana-value.sqlite3'
        build_fixture_database([folder/'basic.json',ROOT/'tests/fixtures/mana-value-destruction-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Mana-value destruction',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def scene(self,name,*,zone='hand',counter=None,amount=0,before=None,initial_step=None):
        s=self.session(2021011+len(name)+amount)
        source=self.add(s.engine,name,zone=zone);source.acquired_control_turn_count=-1
        if counter is not None:source.counters.update({counter:amount,'unrelated':7})
        records=(('zero','Ornithopter','A',0,{'artifact','creature'},False),
            ('one','Llanowar Elves','B',1,{'creature'},False),
            ('two','Grizzly Bears','C',2,{'creature'},False),
            ('three','Centaur Courser','D',3,{'creature'},False),
            ('four','Hill Giant','A',4,{'creature'},False),
            ('tree','Tree of Tales','B',0,{'artifact','land'},False),
            ('forest','Forest','C',0,{'land'},False),
            ('indestructible','Darksteel Myr','D',3,{'artifact','creature'},True))
        board={}
        for key,card_name,seat,mv,types,indestructible in records:
            card=self.add(s.engine,card_name,seat=seat,ref='mv-'+key)
            board[key]=(card,mv,types,indestructible,False)
        board['two'][0].owner='D'
        from quorune.fixed_token_production import clue_token_effect
        before_tokens=set(s.state.cards)
        s.engine.apply_effect({**clue_token_effect(1),'controller':'A'},actor='A')
        clue,=(c for oid,c in s.state.cards.items() if oid not in before_tokens)
        board['ordinary_token']=(clue,0,{'artifact'},False,True)
        ref,=s.engine.create_token('B',name='Grizzly Bears',copy_of=board['two'][0].ref)
        card=next(c for c in s.state.cards.values() if c.ref==ref)
        board['copy']=(card,2,{'creature'},False,True)
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='mana-filter-private-C')
        if before is not None:before(s,source,board)
        s.state.config.semantic_policy='trusted_only';self.assert_closed(s,source)
        self.prepare(s,mana={'C':30,**{color:10 for color in 'WUBRG'}});self.checkpoint(s)
        if initial_step is not None:
            from quorune.engine import TURN_STEPS
            e=s.engine;e.permissions.invalidate_current();s.state.pending_decision=None
            s.state.priority_player=None;s.state.priority_passes=[]
            s.state.phase,s.state.step=initial_step;s.state.phase_index=TURN_STEPS.index(initial_step)
            e._grant_priority('A');e.pump();self.checkpoint(s)
        return s,source,board,private

    def cast_checked(self,s,source,*,x=None,face=None,cost_option=None,modes=None,payment=None):
        self.priority(s,'A')
        rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        row=next(r for r in rows if r.get('card')==source.ref and
                 (r.get('face')==face if face else r['id']=='cast:'+source.ref))
        command={'action_id':row['id'],'pay':'auto'}
        if payment is not None:command.update(pay='manual',payment=payment)
        if x is not None:command['x']=x
        if face:command['face']=face
        if cost_option:command['cost_option']=cost_option
        if modes is not None:command['modes']=modes
        self.reject(s,'D',command)
        if x is not None:self.reject(s,'A',{**command,'x':True})
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        if x is not None:self.assertEqual(x,s.state.stack[-1].x_value)
        return s.state.stack[-1]

    def assert_board(self,s,board,*,bound,comparison='at_most',domain='nonland'):
        for key,(card,mv,types,indestructible,token) in board.items():
            selected=(('land' not in types) if domain=='nonland' else bool(types&set(domain.split(','))))
            numeric=mv==bound if comparison=='equal' else mv<=bound if comparison=='at_most' else mv>=bound
            destroyed=selected and numeric and not indestructible
            expected='outside' if destroyed and token else 'graveyard' if destroyed else 'battlefield'
            self.assertEqual(expected,s.state.cards[card.object_id].zone,key)
            if destroyed and not token:self.assertIn(card.object_id,s.state.players[card.owner].zones['graveyard'])

    def kirin_x_scene(self, value, *, verify_replay=True):
        sources = {}
        def setup(game, observer, board):
            sources["nexus"] = self.add(game.engine, "Maskwood Nexus")
            sources["discount"] = self.add(game.engine, "Foundry Inspector")
            sources["spell"] = self.add(game.engine, "Stonecoil Serpent", zone="hand")
            sources["counter"] = self.add(game.engine, "Counterspell", seat="B", zone="hand")
            citadel = self.add(game.engine, "Darksteel Citadel", seat="D")
            board["indestructible_land"] = (citadel, 0, {"artifact", "land"}, True, False)
            game.state.players["B"].mana_pool["U"] = 2
            for card in (*sources.values(), citadel):
                self.assert_closed(game, card)

        game, observer, board, private = self.scene("Celestial Kirin", zone="battlefield", amount=value, before=setup)
        self.priority(game, "A")
        action = self.action(game, "A", sources["spell"])
        cast = {"action_id": action["id"], "pay": "auto", "x": value}
        self.reject(game, "D", cast)
        self.reject(game, "A", {**cast, "x": True})
        result = game.act("pilot:A", cast)
        self.assertTrue(result.ok, result.summary)
        original, = (item for item in game.state.stack if item.card_object_id == sources["spell"].object_id)
        trigger, = (item for item in game.state.stack if item.source_object_id == observer.object_id)
        self.assertEqual(value, original.x_value)
        self.assertEqual(max(0, value - 1), original.context["mana_spent_total"])
        self.assertEqual(float(value), trigger.context["mana_value"])
        self.assertIn("spirit", trigger.context["subtypes"])
        self.assertEqual([], list(trigger.targets))
        self.cast(game, "B", sources["counter"], [original.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[sources["spell"].object_id].zone)
        self.assertTrue(any(item.ref == trigger.ref for item in game.state.stack))
        if verify_replay:
            game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assert_board(game, board, bound=value, comparison="equal", domain="artifact,creature,land")
        for card in (observer, sources["nexus"]):
            self.assertEqual("graveyard" if value == 4 else "battlefield", game.state.cards[card.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[sources["discount"].object_id].zone)
        if verify_replay:
            self.finish(game, private)

    def test_celestial_kirin_uses_announced_x_after_the_spell_is_countered(self):
        for value in (0, 4):
            with self.subTest(value=value):
                self.kirin_x_scene(value)

    def test_celestial_kirin_does_not_use_current_graveyard_mana_value(self):
        with patch("quorune.semantic_runtime.values.resolve_scalar_effect_amount", return_value=0):
            with self.assertRaisesRegex(AssertionError, "'(?:battlefield|graveyard)' != '(?:battlefield|graveyard)'"):
                self.kirin_x_scene(4, verify_replay=False)

    def test_celestial_kirin_recognizes_own_arcane_cast_and_ignores_copy_and_other_casts(self):
        sources = {}
        def setup(game, observer, board):
            sources["own"] = self.add(game.engine, "Reach Through Mists", zone="hand", ref="kirin-own-arcane")
            sources["foreign"] = self.add(game.engine, "Reach Through Mists", seat="B", zone="hand", ref="kirin-foreign-arcane")
            sources["ordinary"] = self.add(game.engine, "Grizzly Bears", zone="hand", ref="kirin-own-ordinary")
            sources["copy"] = self.add(game.engine, "Reverberate", zone="hand")
            game.state.players["B"].mana_pool["U"] = 1
            for card in sources.values():
                self.assert_closed(game, card)

        game, observer, board, private = self.scene("Celestial Kirin", zone="battlefield", before=setup)
        self.cast(game, "B", sources["foreign"])
        self.assertEqual(0, self.trigger_count(game, observer))
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", sources["ordinary"])
        self.assertEqual(0, self.trigger_count(game, observer))
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", sources["own"])
        original, = (item for item in game.state.stack if item.card_object_id == sources["own"].object_id)
        self.assertEqual(1, self.trigger_count(game, observer))
        self.cast(game, "A", sources["copy"], [original.ref])
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, observer))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[sources["ordinary"].object_id].zone)
        self.assert_board(game, board, bound=1, comparison="equal", domain="artifact,creature,land")
        self.finish(game, private)

    def fixed_scene(self,*,finish=True):
        s,source,board,private=self.scene('Pest Control')
        self.cast_checked(s,source)
        self.assertIsNone(self.resolve(s))
        self.assert_board(s,board,bound=1)
        if finish:self.finish(s,private)

    def test_actual_fixed_filter_keeps_lands_and_copied_token_mana_value_but_destroys_zero_and_one(self):
        self.fixed_scene()

    def counter_scene(self,name='Ratchet Bomb',*,counter='charge',amount=2,domain='nonland',finish=True):
        s,source,board,private=self.scene(name,zone='battlefield',counter=counter,amount=amount)
        self.priority(s,'A')
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                 if r['id'].startswith('activate:'+source.ref+':') and 'destroy ' in r['label'].casefold())
        command={'action_id':row['id'],'pay':'auto'}
        self.reject(s,'D',command)
        result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertNotIn(counter,s.state.cards[source.object_id].counters)
        s=self.replay(s,load=True)
        self.assertIsNone(self.resolve(s))
        self.assert_board(s,board,bound=amount,comparison='equal',domain=domain)
        if finish:self.finish(s,private)

    def test_actual_sacrificed_source_uses_named_counter_lki_including_zero_and_does_not_count_other_counters(self):
        for amount in (0,2,3):
            with self.subTest(amount=amount):self.counter_scene(amount=amount)

    def test_actual_announced_x_is_paid_and_compares_once_at_zero_and_two(self):
        for x in (0,2):
            with self.subTest(x=x):
                s,source,board,private=self.scene('Gaze of Granite')
                item=self.cast_checked(s,source,x=x)
                self.assertEqual(3+x,item.context['mana_spent_total'])
                s=self.replay(s,load=True)
                self.assertIsNone(self.resolve(s))
                self.assert_board(s,board,bound=x)
                self.finish(s,private)

    def test_actual_omitting_filter_mutant_is_killed_by_an_independent_surviving_boundary(self):
        with patch.object(ManaValueFilter,'matches',return_value=True),self.assertRaisesRegex(AssertionError,"'battlefield' != 'graveyard'"):
            self.fixed_scene(finish=False)

    def test_actual_source_count_reset_mutant_is_killed_after_sacrifice(self):
        with patch('quorune.semantic_runtime.values.resolve_scalar_effect_amount',return_value=0),self.assertRaisesRegex(AssertionError,"'battlefield' != 'graveyard'"):
            self.counter_scene(finish=False)

    def test_actual_fixed_creature_and_nonland_artifact_threshold_originals_preserve_their_domains(self):
        for name,bound,domain in (('Ritual of Soot',3,'creature'),('Culling Sun',3,'creature'),
                ('Consume the Meek',3,'creature'),('Granulate',4,'artifact')):
            with self.subTest(name=name):
                s,source,board,private=self.scene(name)
                self.cast_checked(s,source)
                self.assertIsNone(self.resolve(s))
                if name=='Granulate':
                    tree=board.pop('tree');self.assertEqual('battlefield',s.state.cards[tree[0].object_id].zone)
                self.assert_board(s,board,bound=bound,domain=domain)
                self.finish(s,private)

    def test_actual_other_x_originals_keep_artifact_creature_and_union_scopes(self):
        for name,domain in (('Forced March','creature'),('Meltdown','artifact'),('Vicious Rivalry','artifact,creature')):
            with self.subTest(name=name):
                s,source,board,private=self.scene(name)
                self.cast_checked(s,source,x=2)
                self.assertIsNone(self.resolve(s))
                self.assert_board(s,board,bound=2,domain=domain)
                self.finish(s,private)

    def test_actual_other_counter_sources_keep_charge_and_fuse_names_and_artifact_land_policy(self):
        for name,counter,amount,domain in (('Blast Zone','charge',2,'nonland'),
                ('Engineered Explosives','charge',2,'nonland'),('Powder Keg','fuse',2,'artifact,creature'),
                ('Powder Keg','fuse',0,'artifact,creature')):
            with self.subTest(name=name,amount=amount):self.counter_scene(name,counter=counter,amount=amount,domain=domain)

    def test_actual_austere_command_keeps_both_complementary_creature_modes_and_canonical_order(self):
        s,source,board,private=self.scene('Austere Command')
        item=self.cast_checked(s,source,modes=['mode_4','mode_3'])
        self.assertEqual(['mode_3','mode_4'],item.modes)
        self.assertIsNone(self.resolve(s))
        self.assert_board(s,board,bound=100,domain='creature')
        self.finish(s,private)

    def test_actual_brotherhood_artifact_mode_keeps_the_threshold_and_damage_mode_retains_its_own_rules(self):
        for mode in ('mode_1','mode_2'):
            with self.subTest(mode=mode):
                s,source,board,private=self.scene("Brotherhood's End")
                self.cast_checked(s,source,modes=[mode])
                self.assertIsNone(self.resolve(s))
                if mode=='mode_2':self.assert_board(s,board,bound=3,domain='artifact')
                else:
                    self.assert_board(s,board,bound=100,domain='creature')
                    self.assertEqual(3,s.state.cards[board['indestructible'][0].object_id].marked_damage)
                self.finish(s,private)

    def test_actual_normal_and_cleave_keep_distinct_prices_and_only_normal_has_the_mana_bound(self):
        for paid in (False,True):
            with self.subTest(paid=paid):
                s,source,board,private=self.scene('Path of Peril')
                item=self.cast_checked(s,source,cost_option='cleave' if paid else None)
                self.assertEqual(6 if paid else 3,item.context['mana_spent_total'])
                self.assertIsNone(self.resolve(s))
                self.assert_board(s,board,bound=100 if paid else 2,domain='creature')
                self.finish(s,private)

    def test_actual_sunburst_colors_determine_the_later_counter_bound_independently_of_chosen_x(self):
        s,source,board,private=self.scene('Engineered Explosives')
        self.cast_checked(s,source,x=2,payment={'C':1,'U':1})
        self.assertIsNone(self.resolve(s))
        self.assertEqual(1,s.state.cards[source.object_id].counters['charge'])
        self.priority(s,'A')
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                 if r['id'].startswith('activate:'+source.ref+':') and 'destroy ' in r['label'].casefold())
        result=s.act('pilot:A',{'action_id':row['id'],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assert_board(s,board,bound=1,comparison='equal')
        self.finish(s,private)

    def test_actual_source_reentry_does_not_replace_the_sacrificed_incarnation_counter_count(self):
        extra={}
        def before(s,source,board):
            extra['return']=self.add(s.engine,'Refurbish',zone='hand')
            self.add(s.engine,'Vedalken Orrery')
        s,source,board,private=self.scene('Ratchet Bomb',zone='battlefield',counter='charge',amount=2,before=before)
        self.priority(s,'A')
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                 if r['id'].startswith('activate:'+source.ref+':') and 'destroy ' in r['label'].casefold())
        result=s.act('pilot:A',{'action_id':row['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.cast(s,'A',extra['return'],[source.ref]);self.resolve_top(s)
        self.assertEqual('battlefield',s.state.cards[source.object_id].zone)
        self.assertFalse(s.state.cards[source.object_id].counters)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assert_board(s,board,bound=2,comparison='equal')
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.finish(s,private)

    def test_actual_necroplasm_freezes_the_value_before_simultaneously_destroying_itself_and_other_matches(self):
        s,source,board,private=self.scene('Necroplasm',zone='battlefield',counter='+1/+1',amount=3,
            initial_step=('postcombat_main','main'))
        self.until(s,lambda game:any(item.source_object_id==source.object_id for item in game.state.stack))
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assert_board(s,board,bound=3,comparison='equal',domain='creature')
        self.finish(s,private)

    def test_actual_wave_reads_current_counters_then_preserves_latest_lki_when_its_source_is_destroyed(self):
        for departed in (False,True):
            with self.subTest(departed=departed):
                extra={}
                def before(s,source,board):
                    extra['proliferate']=self.add(s.engine,'Contentious Plan',zone='hand')
                    extra['destroy']=self.add(s.engine,'Naturalize',seat='B',zone='hand')
                    self.add(s.engine,'Vedalken Orrery')
                    for i in range(2):
                        self.add(s.engine,'Island',ref='mv-live-blue-'+str(i))
                        self.add(s.engine,'Forest',seat='B',ref='mv-live-green-'+str(i))
                s,source,board,private=self.scene('Wave of Terror',zone='battlefield',counter='age',amount=1,
                    before=before,initial_step=('beginning','upkeep'))
                self.until(s,lambda game:any(item.source_object_id==source.object_id for item in game.state.stack))
                self.cast(s,'A',extra['proliferate'])
                self.assertIsNotNone(self.resolve(s))
                command={'action_id':'choose','objects':[source.ref]}
                self.reject(s,'D',command)
                result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
                if any(item.card_object_id==extra['proliferate'].object_id for item in s.state.stack):self.resolve_top(s)
                self.assertTrue(any(item.source_object_id==source.object_id for item in s.state.stack))
                self.assertEqual(2,s.state.cards[source.object_id].counters['age'])
                if departed:
                    self.cast(s,'B',extra['destroy'],[source.ref]);self.resolve_top(s)
                    self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assert_board(s,board,bound=2,comparison='equal',domain='creature')
                self.finish(s,private)

    def test_actual_copied_x_spell_keeps_the_announced_value_without_paying_again(self):
        extra={}
        def before(s,source,board):
            extra['copy']=self.add(s.engine,'Reverberate',seat='B',zone='hand')
            s.state.players['B'].mana_pool['R']=2
        s,source,board,private=self.scene('Gaze of Granite',before=before)
        self.cast_checked(s,source,x=2)
        self.cast(s,'B',extra['copy'],[s.state.stack[-1].ref])
        self.resolve_top(s)
        copied=s.state.stack[-1]
        self.assertEqual('B',copied.controller);self.assertEqual(2,copied.x_value)
        self.resolve_top(s)
        self.assert_board(s,board,bound=2)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assert_board(s,board,bound=2)
        self.finish(s,private)

    def test_actual_regeneration_remains_available_unless_the_printed_instruction_prohibits_it(self):
        for name,expected in (('Ritual of Soot','battlefield'),('Consume the Meek','graveyard')):
            with self.subTest(name=name):
                extra={}
                def before(s,source,board):
                    extra['skeleton']=self.add(s.engine,'Drudge Skeletons',seat='B')
                    s.state.players['B'].mana_pool['B']=1
                s,source,board,private=self.scene(name,before=before)
                self.cast_checked(s,source)
                self.priority(s,'B')
                row=next(r for r in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
                         if r['id'].startswith('activate:'+extra['skeleton'].ref+':'))
                result=s.act('pilot:B',{'action_id':row['id'],'pay':'auto'})
                self.assertTrue(result.ok,result.summary)
                self.resolve_top(s)
                s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
                self.assertEqual(expected,s.state.cards[extra['skeleton'].object_id].zone)
                self.assert_board(s,board,bound=3,domain='creature')
                self.finish(s,private)

    def test_actual_face_down_and_card_copy_mana_values_override_their_printed_front_values(self):
        import test_fixed_copy_type_exceptions as copies
        extra={}
        def before(s,source,board):
            extra['morph']=self.add(s.engine,'Daru Lancer',zone='hand')
            extra['clone']=self.add(s.engine,'Clone',zone='hand')
        s,source,board,private=self.scene('Ritual of Soot',before=before)
        self.priority(s,'A')
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                 if r['id']=='cast-morph:'+extra['morph'].ref)
        result=s.act('pilot:A',{'action_id':row['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertTrue(s.state.cards[extra['morph'].object_id].face_down)
        for viewer in 'BCD':self.assertNotIn('Daru Lancer',json.dumps(s.packet('pilot:'+viewer,full=True)))
        self.cast(s,'A',extra['clone'])
        s=copies.FixedCopyTypeExceptionRuntimeTests.choose_copy(self,s,board['copy'][0])
        self.assertFalse(s.state.cards[extra['clone'].object_id].is_token)
        self.cast_checked(s,source);self.assertIsNone(self.resolve(s))
        for card in extra.values():self.assertEqual('graveyard',s.state.cards[card.object_id].zone)
        self.assert_board(s,board,bound=3,domain='creature')
        self.finish(s,private)

    def test_malformed_runtime_filter_and_unbound_operand_preserve_authoritative_state(self):
        from quorune.errors import GameRuleError
        from quorune.record import authoritative_state_hash
        s,source,board,private=self.scene('Pest Control')
        template=mana_value_destruction_template('Destroy all creatures with mana value 3 or less.',source_name='Generic Source')
        base={**template[1][0],'source':source.ref}
        unbound=ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE,counter_name='charge',schema_version=2).to_dict()
        for change in ({'schema_version':True},{'value':True},{'comparison':'greater'}, {'value':None},{'value':unbound}):
            effect=deepcopy(base);effect['set']['mana_value_filter'].update(change)
            before=authoritative_state_hash(s.state)
            with self.assertRaises(GameRuleError):s.engine.apply_effect(effect,actor='A')
            self.assertEqual(before,authoritative_state_hash(s.state))
        self.finish(s,private)
