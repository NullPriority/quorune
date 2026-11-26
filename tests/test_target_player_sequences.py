"""CR608.2b-c: fixed instructions sharing one announced player."""
from dataclasses import replace
import unittest
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_qualified_zone_event_queries import query_record

CAPABILITY='resolution.effect.fixed_target_player_sequence'
HALF_LIBRARY_DRAW = (
    "Target player draws cards equal to half the number of cards in their library "
    "and loses half their life. Round up each time."
)


class TargetPlayerSequenceCompilerTests(unittest.TestCase):
    def test_nested_player_sequence_rejects_missing_owner_and_bad_sibling_target(self):
        from copy import deepcopy
        from types import SimpleNamespace
        from test_public_query_effect_amounts import query_amount_record,_NoRulingsDatabase
        from quorune.card_programs import compile_card_program
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.target_player_sequences import target_player_sequence_runtime_dependencies
        text=('Choose one —\n• Target player draws two cards and loses 2 life.\n'
            '• Target player draws three cards and loses 3 life.')
        registry=load_default_capability_registry()
        card=query_amount_record(text,suffix=365211712)
        ability=compile_card_program(_NoRulingsDatabase(),card,capability_registry=registry,
            capability_profile='commander_review',trust_level='trusted').abilities[0]
        self.assertIn(CAPABILITY,ability.capability_dependencies)
        dependencies=tuple(value for value in ability.capability_dependencies if value!=CAPABILITY)
        missing=replace(ability,capability_dependencies=dependencies,
            capability_closure=registry.closure(dependencies,profile='commander_review').to_dict())
        bound=bind_semantic_program_runtime(missing,capability_registry=registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertTrue(any('undeclared_runtime_dependency:'+CAPABILITY in b for b in bound['blockers']))
        schema=deepcopy(ability.target_schema)
        list(schema['modes'].values())[-1]['effects'][1]['player']='$target.9'
        bad=SimpleNamespace(effects=ability.effects,target_schema=schema,coverage=ability.coverage,
            cost_schema=ability.cost_schema,provenance=ability.provenance,capability_dependencies=ability.capability_dependencies)
        with self.assertRaises(ValueError):target_player_sequence_runtime_dependencies(bad)

    def test_reflexive_sacrifice_keeps_the_shared_player_result_owner(self):
        from test_public_query_effect_amounts import query_amount_record,_NoRulingsDatabase
        from quorune.card_programs import compile_card_program,bind_card_program_runtime
        text='When this creature enters, you may sacrifice a creature. When you do, target player draws two cards and loses 2 life.'
        card=query_amount_record(text,suffix=365211710,type_line='Creature — Human')
        registry=load_default_capability_registry()
        ir=compile_oracle_card(card,capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,ir.material_residuals)
        program=compile_card_program(_NoRulingsDatabase(),card,capability_registry=registry,
            capability_profile='commander_review',trust_level='trusted')
        bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
        self.assertTrue(bound['strict_capability_ready'],bound['blockers'])

    def test_symbolic_conditional_and_modal_player_sequences_bind_their_owned_child_scopes(self):
        from test_public_query_effect_amounts import query_amount_record,_NoRulingsDatabase
        from quorune.card_programs import compile_card_program,bind_card_program_runtime
        registry=load_default_capability_registry()
        cases=(
            ('Target player draws X cards and loses X life.','{X}{B}{B}'),
            ('Target player draws X cards and loses X life, where X is the number of artifacts you control.','{3}{B}{B}'),
            ('Target player draws two cards and loses 2 life.\nAdamant — If at least three black mana was spent to cast this spell, create a Food token.','{2}{B}'),
            ('Choose one —\n• Target player discards two cards.\n• Target player draws two cards and loses 2 life.','{2}{B}'),
            ('Choose two —\n• Target creature gets +3/+3 and gains flying until end of turn.\n• Target player draws a card and loses 1 life.\n• Target opponent sacrifices a creature of their choice.','{2}{W}{B}'),
            ('Spree\n+ {2} — Search your library for a card, then shuffle and put that card on top.\n+ {B}{B} — Target player draws three cards and loses 3 life.','{B}'),
        )
        for index,(text,cost) in enumerate(cases):
            with self.subTest(text=text):
                card=replace(query_amount_record(text,suffix=365211700+index),mana_cost=cost)
                program=compile_card_program(_NoRulingsDatabase(),card,capability_registry=registry,
                    capability_profile='commander_review',trust_level='trusted')
                bound=bind_card_program_runtime(program,capability_registry=registry,profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'],bound['blockers'])

    def test_activated_and_triggered_shells_use_the_same_closed_sequence(self):
        body='Target player mills two cards, draws two cards, and loses 2 life.'
        for text in ('{2}, {T}: '+body,'When this creature enters, '+body.lower()):
            ir=compile_oracle_card(replace(query_record(text),type_line='Creature — Wizard'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
            self.assertIn(CAPABILITY,ir.faces[0].nodes[0].capability_dependencies)
    def test_verified_sequence_withdrawal_keeps_standalone_draw(self):
        import json
        from common import ROOT
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding="utf-8"))
        next(c for c in raw['capabilities'] if c['id']==CAPABILITY).update(status='blocked',blockers=['Shared player sequence withdrawn'])
        registry=CapabilityRegistry(raw);declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for text,expected in (('Target player discards a card, then mills a card.',False),('Target player draws a card.',True)):
            ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=registry,capability_profile='commander_review')
            self.assertEqual(expected,ir.status=='exact')

    def test_fixed_player_subject_is_shared_and_order_is_printed(self):
        cases={
            'Target opponent discards two cards, mills a card, and loses 1 life. You gain 1 life.':('choose_cards_apnap','mill','lose_life','life'),
            'Target player discards a card, then mills a card.':('choose_cards_apnap','mill'),
            'Target player mills two cards, draws two cards, and loses 2 life.':('mill','draw','lose_life'),
            'Target player draws three cards, loses 3 life, and gets three poison counters.':('draw','lose_life','place_player_counters'),
        }
        for text,operations in cases.items():
            with self.subTest(text=text):
                ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0];self.assertEqual(1,node.target_schema['count'])
                self.assertEqual(operations,tuple(effect['op'] for effect in node.effects))
                self.assertIn(CAPABILITY,node.capability_dependencies)

    def test_linked_variable_and_independent_targets_remain_residual(self):
        for text in ('Target player discards two cards, then draws that many cards.',
                'Target player draws X cards and loses X life.',
                'Target player draws two cards and another target player loses 2 life.',
                'Target player mills two cards, then chooses the remembered outcome.'):
            ir=compile_oracle_card(replace(query_record(text),type_line='Sorcery'),capability_registry=load_default_capability_registry(),capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,text)


class TargetPlayerSequenceModelTests(unittest.TestCase):
    def test_half_library_draw_rejects_changed_scope_and_missing_authority(self):
        from unittest.mock import patch

        from quorune.card_programs import compile_card_program
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.compiler import target_player_sequences as compiler
        from quorune.fractional_quantity_model import HALF_LIBRARY_DRAW_CAPABILITY
        from test_public_query_effect_amounts import _NoRulingsDatabase, query_amount_record

        card = query_amount_record(HALF_LIBRARY_DRAW, suffix=365211718, type_line="Sorcery")
        registry = load_default_capability_registry()
        profile = "commander_review"
        ir = compile_oracle_card(card, capability_registry=registry, capability_profile=profile)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual(("draw", "lose_life"), tuple(effect["op"] for effect in node.effects))
        self.assertIn(HALF_LIBRARY_DRAW_CAPABILITY, node.capability_dependencies)

        ability, = compile_card_program(
            _NoRulingsDatabase(), card, capability_registry=registry,
            capability_profile=profile, trust_level="trusted",
        ).abilities
        self.assertTrue(bind_semantic_program_runtime(ability, capability_registry=registry, profile=profile)["strict"])
        draw, loss = ability.effects
        for effects in (
            ({**draw, "private": False}, loss),
            ({**draw, "player": "$controller"}, loss),
            ({**draw, "count": {**draw["count"], "rounding": "down"}}, loss),
            ({**draw, "count": {**draw["count"], "schema_version": True}}, loss),
            (draw, {**loss, "player": "$target.1"}),
            (loss, draw),
        ):
            with self.subTest(effects=effects):
                binding = bind_semantic_program_runtime(
                    replace(ability, effects=effects), capability_registry=registry, profile=profile,
                )
                self.assertFalse(binding["strict"], binding)

        dependencies = tuple(value for value in ability.capability_dependencies if value != HALF_LIBRARY_DRAW_CAPABILITY)
        withdrawn = replace(
            ability, capability_dependencies=dependencies,
            capability_closure=registry.closure(dependencies, profile=profile).to_dict(),
        )
        binding = bind_semantic_program_runtime(withdrawn, capability_registry=registry, profile=profile)
        self.assertFalse(binding["strict"], binding)
        self.assertIn("capability:undeclared_runtime_dependency:" + HALF_LIBRARY_DRAW_CAPABILITY, binding["blockers"])

        with patch.object(compiler, "_half_library_draw_life_sequence", return_value=None):
            omitted = compile_oracle_card(card, capability_registry=registry, capability_profile=profile)
        self.assertNotEqual("exact", omitted.status)

    def test_closed_child_shapes_reject_changed_targets_and_unknown_siblings(self):
        from quorune.compiler.target_player_sequences import fixed_target_player_sequence_template
        from quorune.rules.target_player_sequences import target_player_sequence_node_capabilities
        value=fixed_target_player_sequence_template('Target player mills two cards, draws two cards, and loses 2 life.')
        caps=target_player_sequence_node_capabilities(effects=value[1],target_schema=value[2],mechanic_ids=value[3])
        self.assertIn(CAPABILITY,caps)
        from quorune.rules.target_player_sequences import is_closed_target_player_sequence_program
        from types import SimpleNamespace
        self.assertTrue(is_closed_target_player_sequence_program(SimpleNamespace(effects=value[1],target_schema=value[2],coverage=value[3],capability_dependencies=caps)))
        self.assertFalse(is_closed_target_player_sequence_program(SimpleNamespace(effects=value[1],target_schema=value[2],coverage=(),capability_dependencies=())))
        for effects in (({**value[1][0],'player':'$target.1'},*value[1][1:]),
                ({**value[1][0],'unknown':True},*value[1][1:]),
                (*value[1],{'op':'remember_outcome'})):
            self.assertEqual((),target_player_sequence_node_capabilities(effects=effects,target_schema=value[2],mechanic_ids=value[3]))

    def test_omitting_the_shared_target_is_killed(self):
        from unittest.mock import patch
        from quorune.compiler import target_player_sequences as owner
        original=owner._leaf
        def omit(text):
            value=original(text)
            if value is None:return None
            return value[0],tuple({**effect,'player':'$controller'} if effect.get('op')=='mill' else effect for effect in value[1]),value[2],value[3]
        def witness():
            compiled=owner.fixed_target_player_sequence_template('Target player mills two cards, draws two cards, and loses 2 life.')
            self.assertEqual('$target.0',compiled[1][0]['player'])
        witness()
        with patch.object(owner,'_leaf',side_effect=omit),self.assertRaises(AssertionError):witness()


import json,tempfile
from pathlib import Path
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database


class TargetPlayerSequenceNativeTests(InheritedNativeFixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'target-player-sequence.sqlite3'
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/target-player-sequence-cards.json",
            ROOT / "tests/fixtures/fractional-player-result-cards.json",
            ROOT / "tests/fixtures/draw-player-source-damage-cards.json",
        ], path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Shared target sequences',[DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',20)],['Isamaru, Hound of Konda'])

    def test_peer_into_the_abyss_redirects_five_draws_and_loses_four_life(self):
        session = self.session(608200218)
        spell = self.add(session.engine, "Peer into the Abyss", zone="hand")
        thief = self.add(session.engine, "Notion Thief")
        self.assert_closed(session, spell)
        self.assert_closed(session, thief)
        self.prepare(session, mana={"B": 3, "C": 4})
        seat = session.state.players["B"]
        for object_id in tuple(seat.zones["library"])[9:]:
            session.engine.move_card(object_id, "graveyard", log=False)
        self.assertEqual(9, len(seat.zones["library"]))
        seat.life = 7
        self.checkpoint(session)
        self.cast(session, "A", spell, ["B"])
        session = self.replay(session, load=True)

        before = {player: set(session.state.players[player].zones["hand"]) for player in "ABCD"}
        self.assertIsNone(self.resolve(session))
        drawn = set(session.state.players["A"].zones["hand"]) - before["A"]
        self.assertEqual(5, len(drawn))
        self.assertEqual(3, session.state.players["B"].life)
        self.assertEqual(40, session.state.players["A"].life)
        self.assertEqual(9, len(session.state.players["B"].zones["library"]))
        for player in "BCD":
            self.assertEqual(before[player], set(session.state.players[player].zones["hand"]))
            projection = json.dumps(session.packet("pilot:" + player, full=True))
            for object_id in drawn:
                self.assertNotIn(session.state.cards[object_id].ref, projection)
        self.replay(session, load=True)

    def test_peer_into_the_abyss_departed_target_skips_both_results_and_replays(self):
        session = self.session(608200219)
        spell = self.add(session.engine, "Peer into the Abyss", zone="hand")
        self.assert_closed(session, spell)
        self.prepare(session, mana={"B": 3, "C": 4})
        self.checkpoint(session)
        self.cast(session, "A", spell, ["B"])
        self.priority(session, "B")
        self.assertTrue(session.act("pilot:B", {"action_id": "concede", "confirm_concede": True}).ok)
        session = self.replay(session, load=True)

        state = session.state
        before = {player: (state.players[player].life, tuple(state.players[player].zones["hand"])) for player in "ABCD"}
        self.assertIsNone(self.resolve(session))
        state = session.state
        self.assertFalse(state.players["B"].in_game)
        self.assertEqual("graveyard", state.cards[spell.object_id].zone)
        self.assertEqual(before, {
            player: (state.players[player].life, tuple(state.players[player].zones["hand"])) for player in "ABCD"
        })
        self.replay(session, load=True)

    def test_actual_discard_continuation_precedes_mill_and_life_and_replays(self):
        s=self.session(608200201);spell=self.add(s.engine,'Mind Drain',zone='hand')
        private=self.add(s.engine,'Counterspell',seat='B',zone='hand')
        self.assert_closed(s,spell);self.prepare(s,mana={'B':1,'C':2});self.checkpoint(s)
        library=len(s.state.players['B'].zones['library']);self.cast(s,'A',spell,['B'])
        self.assertEqual('choice.apnap',self.resolve(s).kind)
        self.assertEqual((40,40,library),(s.state.players['A'].life,s.state.players['B'].life,len(s.state.players['B'].zones['library'])))
        hand=[s.state.cards[uid].ref for uid in s.state.players['B'].zones['hand']]
        command={'action_id':'choose','cards':hand[:2]}
        before=authoritative_state_hash(s.state);self.assertFalse(s.act('pilot:A',command).ok)
        self.assertEqual(before,authoritative_state_hash(s.state))
        for seat in 'ACD':self.assertNotIn(private.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        s=self.replay(s,load=True);self.assertTrue(s.act('pilot:B',command).ok);self.assertIsNone(self.resolve(s))
        self.assertEqual((41,39,library-1),(s.state.players['A'].life,s.state.players['B'].life,len(s.state.players['B'].zones['library'])))
        self.replay(s,load=True)

    def test_actual_draw_life_poison_sequence_keeps_target_and_private_draws(self):
        s=self.session(608200202);spell=self.add(s.engine,'Caress of Phyrexia',zone='hand')
        self.assert_closed(s,spell);self.prepare(s,mana={'B':2,'C':3});self.checkpoint(s)
        hand=set(s.state.players['C'].zones['hand']);self.cast(s,'A',spell,['C'])
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual((37,3),(s.state.players['C'].life,s.state.players['C'].poison))
        self.assertEqual(len(hand)+3,len(s.state.players['C'].zones['hand']))
        drawn=set(s.state.players['C'].zones['hand'])-hand
        for seat in 'ABD':
            packet=json.dumps(s.packet('pilot:'+seat,full=True))
            for uid in drawn:self.assertNotIn(s.state.cards[uid].ref,packet)
        self.replay(s,load=True)

    def test_departed_sole_target_cancels_all_clauses_including_controller_life_gain(self):
        s=self.session(608200203);spell=self.add(s.engine,'Mind Drain',zone='hand')
        self.assert_closed(s,spell);self.prepare(s,mana={'B':1,'C':2});self.checkpoint(s)
        self.cast(s,'A',spell,['B']);self.priority(s,'B')
        self.assertTrue(s.act('pilot:B',{'action_id':'concede','confirm_concede':True}).ok)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual(40,s.state.players['A'].life);self.assertFalse(s.state.players['B'].in_game)
        self.assertEqual('graveyard',s.state.cards[spell.object_id].zone)
        self.assertFalse(s.state.stack);self.replay(s,load=True)

    def test_short_hand_discards_available_card_and_continues_fixed_siblings(self):
        s=self.session(608200204);spell=self.add(s.engine,'Mind Drain',zone='hand')
        for uid in tuple(s.state.players['B'].zones['hand'])[1:]:s.engine.move_card(uid,'library',log=False)
        self.assert_closed(s,spell);self.prepare(s,mana={'B':1,'C':2});self.checkpoint(s)
        library=len(s.state.players['B'].zones['library']);self.cast(s,'A',spell,['B'])
        pending=self.resolve(s)
        if pending is not None:
            self.assertEqual('choice.apnap',pending.kind)
            card=s.state.cards[s.state.players['B'].zones['hand'][0]]
            self.assertTrue(s.act('pilot:B',{'action_id':'choose','cards':[card.ref]}).ok)
            self.assertIsNone(self.resolve(s))
        self.assertEqual([],s.state.players['B'].zones['hand'])
        self.assertEqual((41,39,library-1),(s.state.players['A'].life,s.state.players['B'].life,len(s.state.players['B'].zones['library'])))
        self.replay(s,load=True)

    def test_generated_activation_keeps_shared_player_after_source_leaves(self):
        s=self.session(608200205)
        source=self.add(s.engine,'Generic Shared Player Archivist')
        removal=self.add(s.engine,'Disenchant',seat='B',zone='hand')
        lands=[self.add(s.engine,'Plains',seat='B',ref='sequence-remove-land-'+str(i)) for i in range(2)]
        self.assert_closed(s,source);self.assert_closed(s,removal)
        self.prepare(s,mana={'C':2});self.checkpoint(s)
        hand=len(s.state.players['C'].zones['hand']);library=len(s.state.players['C'].zones['library'])
        rows=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in rows if row['id'].startswith('activate:'+source.ref+':'))
        self.assertTrue(s.act('pilot:A',{'action_id':action['id'],'targets':['C'],'pay':'auto'}).ok)
        self.priority(s,'B');rows=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
        for land in lands:
            rows=s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
            mana=next(row for row in rows if row.get('mana_ability') and land.ref in row['id'])
            self.assertTrue(s.act('pilot:B',{'action_id':mana['id'],'pay':'auto'}).ok)
        self.cast(s,'B',removal,[source.ref]);self.resolve_top(s)
        s=self.replay(s,load=True);self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        self.assertEqual((hand+2,library-4,38),(len(s.state.players['C'].zones['hand']),len(s.state.players['C'].zones['library']),s.state.players['C'].life))
        self.replay(s,load=True)
