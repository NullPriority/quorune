"""CR 115.10 and 702.96: each names a complete untargeted public set."""
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, packet_without_decision_capability
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase, CardRecord
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.semantics import SemanticRegistry
from quorune.compiler.destruction_templates import mass_destruction_effect_template
from quorune.compiler.public_tap_state_set_templates import fixed_public_tap_state_set_effect_template
from quorune.compiler.public_zone_move_templates import public_zone_move_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class SingularPublicSetCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def test_each_singular_and_all_plural_retain_the_same_typed_set(self):
        for compile, singular, plural in (
            (mass_destruction_effect_template, "Destroy each artifact you don't control.",
                'Destroy all artifacts your opponents control.'),
            (mass_destruction_effect_template, 'Destroy each creature. A creature destroyed this way can\'t be regenerated.',
                'Destroy all creatures. They can\'t be regenerated.'),
            (fixed_public_tap_state_set_effect_template, "Tap each creature you don't control.",
                'Tap all creatures your opponents control.'),
            (fixed_public_tap_state_set_effect_template, 'Untap each nonland permanent you control.',
                'Untap all nonland permanents you control.'),
            (fixed_public_tap_state_set_effect_template, 'Untap each other creature you control.',
                'Untap all other creatures you control.'),
            (public_zone_move_effect_template, "Return each nonland permanent you don't control to its owner's hand.",
                "Return all nonland permanents your opponents control to their owners' hands."),
            (public_zone_move_effect_template, 'Exile each tapped creature target player controls.',
                'Exile all tapped creatures target player controls.'),
        ):
            with self.subTest(source=singular):
                expected, actual = compile(plural), compile(singular)
                self.assertIsNotNone(expected)
                self.assertIsNotNone(actual)
                self.assertEqual(expected.compiled(), actual.compiled())

    def record(self, body, cost='Overload {4}{R}'):
        return CardRecord(oracle_id='fixture:singular-public-set', name='Public Set Witness', mana_cost='{R}',
            mana_value=1, type_line='Sorcery', oracle_text=body+'\n'+cost, power=None, toughness=None,
            loyalty=None, defense=None, colors=('R',), color_identity=('R',), keywords=('Overload',),
            produced_mana=(), layout='normal', released_at=None, legalities={'commander':'legal'}, faces=(), raw={})

    def test_complete_overload_pair_keeps_original_source_and_distinct_target_authority(self):
        for body in ("Destroy target artifact you don't control.",
                "Return target nonland permanent you don't control to its owner's hand.",
                "Tap target creature you don't control.",
                "Destroy target creature. A creature destroyed this way can't be regenerated."):
            with self.subTest(body=body):
                record = self.record(body)
                ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                ordinary, paid = ir.faces[0].nodes
                self.assertIsNotNone(ordinary.target_schema)
                self.assertIsNone(paid.target_schema)
                self.assertEqual(record.oracle_text, paid.text)
                self.assertEqual((0,len(record.oracle_text)), (paid.span.start,paid.span.end))
                self.assertEqual(ordinary.span,paid.span)
                self.assertEqual(ordinary.cost,paid.cost)
                self.assertNotEqual(ordinary.effects,paid.effects)

    def test_unknown_qualities_linked_results_and_unclosed_overload_neighbors_stay_residual(self):
        for compiler, texts in (
            (mass_destruction_effect_template, ('Destroy each chosen creature.',
                'Destroy each creature with an unrepresented quality.', 'Destroy each creature. Draw a card if it died.')),
            (fixed_public_tap_state_set_effect_template, ('Tap each creature you own but do not control.',
                'Untap each creature that attacked this combat.')),
            (public_zone_move_effect_template, ("Return each permanent to its controller's hand.",
                "Return each chosen permanent to its owner's hand.")),
        ):
            for text in texts:
                with self.subTest(source=text): self.assertIsNone(compiler(text))
        for record in (self.record("Destroy target artifact you don't control.",cost='Overload {X}{R}'),
                self.record('Target creature you control gains protection from each color until end of turn.'),
                replace(self.record("Destroy target artifact you don't control."),
                    oracle_text="Destroy target artifact you don't control. Invent an unrepresented sibling.\nOverload {4}{R}")):
            self.assertNotEqual('exact',compile_oracle_card(record,capability_registry=self.registry,
                capability_profile='commander_review').status)

    def test_missing_singular_contribution_cannot_satisfy_the_positive_contract(self):
        with mock.patch('quorune.compiler.destruction_templates._singular_set_subject',return_value=None):
            with self.assertRaises(AssertionError):
                self.assertIsNotNone(mass_destruction_effect_template('Destroy each creature.'))


class SingularPublicSetNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(); path=Path(cls.temporary.name)/'singular-sets.sqlite3'
        base=json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards']=[row for row in base['cards'] if row['name']=='Generic Bound Plains']; base['rulings']=[]
        basic=Path(cls.temporary.name)/'basic.json'; basic.write_text(json.dumps(base),encoding='utf-8')
        build_fixture_database([basic,ROOT/'tests/fixtures/singular-public-permanent-set-cards.json'],path)
        cls.db=CardDatabase(path); cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Singular public sets',[DeckEntry('Sliver Hivelord',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Sliver Hivelord'])

    def reject(self,session,seat,command):
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:'+seat,command).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))

    def cast_variant(self,session,source,option,targets=()):
        self.priority(session,'A')
        offered=next(row for row in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if row['id']=='cast:'+source.ref)
        self.assertIn(option,[row['id'] for row in offered['cost_options']])
        command={'action_id':offered['id'],'cost_option':option,'targets':list(targets),'pay':'auto'}
        self.reject(session,'D',command)
        result=session.act('pilot:A',command); self.assertTrue(result.ok,result.summary)
        self.assertEqual(list(targets),session.state.stack[-1].targets)

    def finish(self,session,private):
        for viewer in 'ABCD':
            packet=packet_without_decision_capability(session.packet('pilot:'+viewer,full=True))
            if viewer=='C':self.assertIn(private.ref,json.dumps(packet))
            else:self.assertNotIn(private.ref,json.dumps(packet))
        self.replay(session,load=True)

    def test_vandalblast_retains_its_reviewed_admission_boundary(self):
        record=self.db.lookup('Vandalblast',fuzzy=False)
        ir=compile_oracle_card(record,capability_registry=self.registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        program=compile_best_available_card_program(self.db,record,semantic_registry=SemanticRegistry(),
            capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['compatible_ready'])
        self.assertFalse(binding['strict_capability_ready'])
        self.assertEqual('mixed',binding['trust_basis'])

    def test_actual_blustersquall_affects_three_opponents_and_a_later_creature(self):
        session=self.session(997010); source=self.add(session.engine,'Blustersquall',zone='hand')
        own=self.add(session.engine,'Grizzly Bears',ref='singular-own-tap-control')
        first=self.add(session.engine,'Gladecover Scout',seat='B')
        second=self.add(session.engine,'Grizzly Bears',seat='C',ref='singular-third-seat-creature')
        later=self.add(session.engine,'Ashcoat Bear',seat='D',zone='hand')
        self.add(session.engine,'Forest',seat='D',ref='singular-fourth-seat-payer'); self.add(session.engine,'Wastes',seat='D')
        land=self.add(session.engine,'Forest',seat='B',ref='singular-second-seat-land-control')
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='singular-tap-private-card')
        self.assert_closed(session,source); self.prepare(session,mana={'C':3,'U':1}); self.checkpoint(session)
        self.cast_variant(session,source,'overload'); self.cast(session,'D',later); self.resolve_top(session)
        session=self.replay(session,load=True); self.assertIsNone(self.resolve(session))
        for card in (first,second,later): self.assertTrue(session.state.cards[card.object_id].tapped)
        for card in (own,land): self.assertFalse(session.state.cards[card.object_id].tapped)
        self.finish(session,private)

    def test_actual_rift_uses_current_control_for_selection_and_owner_for_destination(self):
        session=self.session(997020); source=self.add(session.engine,'Cyclonic Rift',zone='hand')
        own=self.add(session.engine,'Grizzly Bears',ref='singular-borrowed-kept'); own.owner='B'
        borrowed=self.add(session.engine,'Grizzly Bears',seat='B',ref='singular-borrowed-returned'); borrowed.owner='A'
        protected=self.add(session.engine,'Gladecover Scout',seat='C')
        later=self.add(session.engine,'Ashcoat Bear',seat='B',zone='hand')
        self.add(session.engine,'Forest',seat='B',ref='singular-second-seat-payer'); self.add(session.engine,'Wastes',seat='B')
        land=self.add(session.engine,'Forest',seat='D',ref='singular-fourth-seat-land-control')
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='singular-return-private-card')
        self.assert_closed(session,source); self.prepare(session,mana={'C':6,'U':1}); self.checkpoint(session)
        self.cast_variant(session,source,'overload'); self.cast(session,'B',later); self.resolve_top(session)
        session=self.replay(session,load=True); self.assertIsNone(self.resolve(session))
        self.assertEqual('battlefield',session.state.cards[own.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[land.object_id].zone)
        for card,owner in ((borrowed,'A'),(protected,'C'),(later,'B')):
            self.assertEqual('hand',session.state.cards[card.object_id].zone)
            self.assertIn(card.object_id,session.state.players[owner].zones['hand'])
        self.finish(session,private)

    def test_actual_damn_prevents_regeneration_and_preserves_indestructible(self):
        session=self.session(997030); source=self.add(session.engine,'Damn',zone='hand')
        own=self.add(session.engine,'Grizzly Bears'); shield=self.add(session.engine,'Regenerate',zone='hand')
        protected=self.add(session.engine,'Gladecover Scout',seat='B')
        indestructible=self.add(session.engine,'Darksteel Myr',seat='C')
        ordinary=self.add(session.engine,'Runeclaw Bear',seat='D')
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='singular-destruction-private-card')
        self.assert_closed(session,source); self.prepare(session,mana={'C':3,'G':1,'W':2}); self.checkpoint(session)
        self.cast(session,'A',shield,targets=[own.ref]); self.assertIsNone(self.resolve(session))
        self.assertEqual(1,session.state.cards[own.object_id].regeneration_shields)
        self.cast_variant(session,source,'overload')
        session=self.replay(session,load=True); self.assertIsNone(self.resolve(session))
        for card in (own,protected,ordinary): self.assertEqual('graveyard',session.state.cards[card.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[indestructible.object_id].zone)
        self.finish(session,private)

    def test_actual_attack_and_end_step_untap_retain_source_exclusion_and_player_scope(self):
        for attack in (False,True):
            with self.subTest(attack=attack):
                session=self.session(997040+attack)
                if attack: source=self.add(session.engine,'Copperhorn Scout')
                else: source=self.add(session.engine,'Veteran Beastrider'); source.tapped=True
                own=self.add(session.engine,'Grizzly Bears',ref='singular-own-untap-partner'); own.tapped=not attack
                waiting=self.add(session.engine,'Runeclaw Bear'); waiting.tapped=True
                opposing=self.add(session.engine,'Grizzly Bears',seat='B',ref='singular-opposing-untap-control'); opposing.tapped=True
                land=self.add(session.engine,'Forest'); land.tapped=True
                private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='singular-trigger-untap-private-card')
                if attack: self.add(session.engine,'Fervor')
                self.assert_closed(session,source); self.prepare(session); self.checkpoint(session)
                if attack: self.attack(session,[(source,'D'),(own,'D')])
                else: self.until(session,lambda s:self.trigger_count(s,source)>0)
                self.assertEqual(1,self.trigger_count(session,source))
                session=self.replay(session,load=True); self.assertIsNone(self.resolve(session))
                for card in (own,waiting): self.assertFalse(session.state.cards[card.object_id].tapped)
                for card in (opposing,land): self.assertTrue(session.state.cards[card.object_id].tapped)
                self.assertEqual(attack,session.state.cards[source.object_id].tapped)
                if attack: self.assertEqual(2,len(session.state.combat.attackers))
                self.finish(session,private)

    def test_actual_denizen_keeps_itself_and_returns_other_controlled_creatures_to_their_owners(self):
        session=self.session(997050); source=self.add(session.engine,'Denizen of the Deep',zone='hand')
        own=self.add(session.engine,'Grizzly Bears')
        borrowed=self.add(session.engine,'Runeclaw Bear'); borrowed.owner='B'
        opposing=self.add(session.engine,'Gladecover Scout',seat='D')
        private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='singular-entry-return-private-card')
        self.assert_closed(session,source); self.prepare(session,mana={'C':6,'U':2}); self.checkpoint(session)
        self.cast(session,'A',source); self.resolve_top(session)
        for card in (source,own,borrowed): self.assertEqual('battlefield',session.state.cards[card.object_id].zone)
        self.assertEqual(1,self.trigger_count(session,source))
        session=self.replay(session,load=True); self.assertIsNone(self.resolve(session))
        for card,owner in ((own,'A'),(borrowed,'B')):
            self.assertEqual('hand',session.state.cards[card.object_id].zone)
            self.assertIn(card.object_id,session.state.players[owner].zones['hand'])
        for card in (source,opposing): self.assertEqual('battlefield',session.state.cards[card.object_id].zone)
        self.finish(session,private)

    def test_actual_ready_half_and_fused_willing_untap_and_lock_only_current_creatures(self):
        for fused in (False,True):
            with self.subTest(fused=fused):
                session=self.session(997060+fused); source=self.add(session.engine,'Ready // Willing',zone='hand')
                own=self.add(session.engine,'Grizzly Bears'); own.tapped=True
                opposing=self.add(session.engine,'Runeclaw Bear',seat='B'); opposing.tapped=True
                later=self.add(session.engine,'Ashcoat Bear',zone='hand')
                self.add(session.engine,'Forest'); self.add(session.engine,'Wastes')
                private=self.add(session.engine,'Counterspell',seat='C',zone='hand',ref='singular-fuse-untap-private-card')
                self.assert_closed(session,source)
                self.prepare(session,mana={'C':2,'G':1,'W':2,'B':1} if fused else {'C':1,'G':1,'W':1})
                self.checkpoint(session); self.priority(session,'A')
                rows=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                offered=next(row for row in rows if row.get('card')==source.ref and (
                    row.get('cast_method')=='fuse' if fused else row.get('cast_method') is None and row.get('face')=='Ready'))
                command={'action_id':offered['id'],'targets':[],'pay':'auto'}
                self.reject(session,'D',command)
                result=session.act('pilot:A',command); self.assertTrue(result.ok,result.summary)
                self.assertEqual(0,sum(session.state.players['A'].mana_pool.values()))
                session=self.replay(session,load=True); self.assertIsNone(self.resolve(session))
                self.assertFalse(session.state.cards[own.object_id].tapped)
                self.assertTrue(session.state.cards[opposing.object_id].tapped)
                self.assertNotIn('indestructible',session.engine._combat_keywords(session.state.cards[opposing.object_id]))
                keywords=session.engine._combat_keywords(session.state.cards[own.object_id])
                self.assertIn('indestructible',keywords)
                for keyword in ('deathtouch','lifelink'): self.assertEqual(fused,keyword in keywords)
                self.cast(session,'A',later); self.assertIsNone(self.resolve(session))
                for keyword in ('indestructible','deathtouch','lifelink'):
                    self.assertNotIn(keyword,session.engine._combat_keywords(session.state.cards[later.object_id]))
                self.finish(session,private)
