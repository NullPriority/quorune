from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from copy import deepcopy
import json,os,tempfile,unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_token_article_composition as controls
from quorune.rules.evidence_storage import read_evidence_document

CAP='token.creation.post_creation_counter'
NAMES=('Wild Hypothesis','Paradox Zone','Fractal Anomaly','Berta, Wise Extrapolator','Recon Craft Theta',
    'Leyline Invocation','Additive Evolution','Fractal Summoning','Manifestation Sage')


class CreatedTokenCounterTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.TokenArticleCompositionTests.session
    add=controls.TokenArticleCompositionTests.add
    prepare=controls.TokenArticleCompositionTests.prepare
    checkpoint=controls.TokenArticleCompositionTests.checkpoint
    resolve=controls.TokenArticleCompositionTests.resolve
    replay=controls.TokenArticleCompositionTests.replay
    binding=controls.TokenArticleCompositionTests.binding
    actions=controls.TokenArticleCompositionTests.actions
    cast=controls.TokenArticleCompositionTests.cast
    reject=controls.TokenArticleCompositionTests.reject
    private_replay=controls.TokenArticleCompositionTests.private_replay
    priority=controls.TokenArticleCompositionTests.priority
    options=controls.TokenArticleCompositionTests.options
    use=controls.TokenArticleCompositionTests.use
    advance=controls.TokenArticleCompositionTests.advance
    top=controls.TokenArticleCompositionTests.top
    tokens=controls.TokenArticleCompositionTests.tokens
    settle=controls.TokenArticleCompositionTests.settle
    activate=controls.TokenArticleCompositionTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'created-counters.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/created-token-counter-cards.json',ROOT/'tests/fixtures/additional-token-status-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Creation followed by actual counters',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def fractals(self,s,count,size):
        rows=[c for c in self.tokens(s) if c.printed_name=='Fractal Token'];self.assertEqual(count,len(rows))
        for card in rows:
            data=s.engine._copyable_characteristics(card);self.assertEqual(('0','0'),(str(data['power']),str(data['toughness'])))
            self.assertEqual(size,card.counters.get('+1/+1',0))
        return rows

    def normal(self,s,card,payment,**choices):
        rows=self.options(s,card)
        option=next((o for o in rows if o['id']=='normal'),None)
        if option is None:
            option=next((o for o in rows if all(o.get('requirements',{}).get(color,0)==payment.get(color,0) for color in 'WUBRG')),None)
        self.assertIsNotNone(option,rows);self.use(s,card,option,payment,**choices)

    def test_twilight_requires_its_announced_x_and_rejects_authored_exemptions(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        from quorune.compiler.created_token_counters import created_token_counter_effect_template
        from quorune.created_token_counter_model import (
            CREATED_TOKEN_WIPE_CAPABILITY, DESTROY_EXCEPT_CREATED, validate_announced_x_token_wipe,
        )
        from quorune.rules.created_token_counter_shapes import validate_announced_x_token_wipe_program

        s = self.session(808359)
        spell = self.add(s.engine, "White Sun's Twilight", zone='hand')
        row = self.db.lookup("White Sun's Twilight", fuzzy=False)
        rendered = row.oracle_text.split(' (Players dealt', 1)[0]
        kwargs = {'source_name': row.name, 'cast_x_available': True, 'source_is_permanent': False}
        _, effects, target, unused_mechanics = created_token_counter_effect_template(rendered, **kwargs)
        effect, = effects
        self.assertIsNone(target)
        self.assertEqual(5, effect['minimum'])
        self.assertIn(CREATED_TOKEN_WIPE_CAPABILITY, typed_result_amount_runtime_dependencies(effect))
        for mutation in ({'amount': 5}, {'minimum': True}, {'cards': ['forged-token']}):
            with self.assertRaises(ValueError):
                validate_announced_x_token_wipe({**effect, **mutation})
        with self.assertRaises(ValueError):
            typed_result_amount_runtime_dependencies({'op': DESTROY_EXCEPT_CREATED, 'cards': ['forged-token']})
        self.assertIsNone(created_token_counter_effect_template(rendered.replace('all other', 'all'), **kwargs))
        self.assertIsNone(created_token_counter_effect_template(rendered, **{**kwargs, 'cast_x_available': False}))
        self.assertIsNone(created_token_counter_effect_template(rendered, **{**kwargs, 'source_is_permanent': True}))
        program = next(program for program in s.engine.semantics.programs_for_oracle(spell.oracle_id)
            if CREATED_TOKEN_WIPE_CAPABILITY in program.capability_dependencies)
        self.assertTrue(self.binding(s, spell)['strict_capability_ready'])
        for altered in (replace(program, event='permanent.enter.self'), replace(program, effects=(*effects, *effects))):
            with self.assertRaises(ValueError):
                validate_announced_x_token_wipe_program(altered)
        reduced = tuple(value for value in program.capability_dependencies if value != CREATED_TOKEN_WIPE_CAPABILITY)
        reduced_program = replace(program, capability_dependencies=reduced,
            capability_closure=self.registry.closure(reduced, profile='commander_review').to_dict())
        binding = bind_semantic_program_runtime(reduced_program, capability_registry=self.registry, profile='commander_review')
        self.assertFalse(binding['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + CREATED_TOKEN_WIPE_CAPABILITY, binding['blockers'])

    def test_twilight_destruction_retains_only_matching_token_incarnations(self):
        from types import SimpleNamespace
        from quorune.affected_permanents import AffectedPermanentSetSpec
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.destruction_sets import snapshot_destruction_set
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.object_query import ObjectQueryResult
        from quorune.semantic_choices.destruction_intent_identity import (
            destruction_intent_identity, validate_destruction_intent_identity,
        )
        from quorune.semantic_runtime.intents import DestroyPermanentSetIntent

        def row(object_id, logical_id, token=True):
            return ObjectQueryResult(object_id=object_id, logical_object_id=logical_id, ref=object_id,
                printed_name='Shared Token Name', owner='A', controller='A', zone='battlefield',
                types=('creature',), token=token)

        exempt = tuple(ContinuousObjectIdentity(value, value + '@0') for value in ('committed', 'old', 'nontoken'))
        group = AffectedPermanentSetSpec(ObjectQuerySpec(zones=('battlefield',), types_all=('creature',)))
        rows = (row('committed', 'committed@0'), row('old', 'old@1'), row('nontoken', 'nontoken@0', token=False),
            row('prior', 'prior@0'))
        query = SimpleNamespace(affected_permanent_active_seats=lambda: ('A',), affected_permanent_apnap_order=lambda: ('A',))
        chosen = snapshot_destruction_set(query, actor='A', spec=group, object_rows=rows, excluded_created_tokens=exempt)
        self.assertEqual({'old', 'nontoken', 'prior'}, {result.object_id for result in chosen.permanents})
        intent = DestroyPermanentSetIntent('A', group, 'Twilight', excluded_created_tokens=exempt)
        kind, encoded = destruction_intent_identity(intent)
        self.assertEqual(encoded, validate_destruction_intent_identity(kind, encoded))
        with self.assertRaises(ValueError):
            DestroyPermanentSetIntent('A', group, 'Twilight', excluded_created_tokens=(*exempt, exempt[0]))
        _, legacy = destruction_intent_identity(replace(intent, excluded_created_tokens=()))
        self.assertNotIn('excluded_created_tokens', legacy)

        from quorune.created_token_counter_model import DESTROY_EXCEPT_CREATED
        from quorune.semantic_choices.created_token_counters import AnnouncedXCreatedTokenWipeHandler
        context = SimpleNamespace(actor='A', stack_controller='A', stack_ref='STACK', source_ref='SPELL',
            stack_label='Twilight', query=SimpleNamespace(active_seats=('A',)))
        remaining = {'op': DESTROY_EXCEPT_CREATED, 'schema_version': 1, 'controller': 'A', 'source': 'SPELL',
                     'stack': 'STACK', 'cards': []}
        preparation = AnnouncedXCreatedTokenWipeHandler(DESTROY_EXCEPT_CREATED, 'local-witness').prepare(remaining, context)
        wipe, = preparation.preparation_intents
        self.assertEqual((group, ()), (wipe.spec, wipe.excluded_created_tokens))

    def replay_twilight(self, s, case, *, load=False):
        from quorune.record import authoritative_state_hash, replay_record
        from quorune.session import CommanderSession

        saved = os.environ.get('QUORUNE_TWILIGHT_RECORD_PARENT')
        directory = Path(saved or self.temp.name)
        if saved:
            fixture = directory / 'fixture-cards.sqlite3'
            if not fixture.exists():
                fixture.write_bytes(self.db.path.read_bytes())
        path = directory / ('twilight-' + case)
        s.save(path)
        self.assertEqual(3, json.loads((path / 'manifest.json').read_bytes())['schema_version'])
        result = replay_record(path, self.db, verify=True)
        expected = authoritative_state_hash(s.state)
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

    def twilight_mites(self, s, expected):
        from quorune.damage_source import represented_toxic_value

        mites = [card for card in self.tokens(s) if card.printed_name == 'Phyrexian Mite Token']
        self.assertEqual(expected, len(mites))
        for card in mites:
            definition = s.engine._effective_card_data(card)
            types, subtypes, supertypes = s.engine._type_parts(definition['type_line'])
            self.assertTrue(card.is_token)
            self.assertEqual(({'artifact', 'creature'}, {'phyrexian', 'mite'}, set()), (types, subtypes, supertypes))
            self.assertEqual(('1', '1', ()),
                (str(definition['power']), str(definition['toughness']), tuple(definition['colors'])))
            self.assertEqual(1, represented_toxic_value(definition))
            self.assertFalse(card.tapped)
        return mites

    def test_twilight_zero_and_four_gain_announced_life_and_leave_other_creatures(self):
        for x in (0, 4):
            with self.subTest(x=x):
                s = self.session(808341 + x)
                spell = self.add(s.engine, "White Sun's Twilight", zone='hand')
                opposing = self.add(s.engine, 'Generic Bound Body', seat='B', ref='twilight-opponent')
                private = self.add(s.engine, 'Generic Bound Growth', seat='C', zone='hand', ref='twilight-private')
                self.assertTrue(self.binding(s, spell)['strict_capability_ready'])
                original_life = s.state.players['A'].life
                if x:
                    self.add(s.engine, 'Parallel Lives')
                price = {'C': x, 'W': 2}
                self.prepare(s, mana=price)
                self.checkpoint(s)
                self.normal(s, spell, price, x=x)
                self.assertEqual([], s.state.stack[-1].targets)
                self.assertEqual(x, s.state.stack[-1].x_value)
                self.assertEqual(0, sum(s.state.players['A'].mana_pool.values()))
                self.assertIsNone(self.settle(s))
                self.assertEqual(original_life + x, s.state.players['A'].life)
                self.assertEqual('battlefield', s.state.cards[opposing.object_id].zone)
                self.assertFalse(any(event.code == 'effect.permanent.destroy_set' for event in s.state.events))
                for mite in self.twilight_mites(s, 2 * x):
                    self.assertFalse(s.engine._can_block(s.state.cards[opposing.object_id], mite)[0])
                self.assertNotIn(private.ref, str(s.packet('pilot:A', full=True)))
                self.replay_twilight(s, str(x))

    def test_twilight_five_spares_actual_replacement_tokens_and_rolls_back_one_wipe(self):
        from quorune import destruction_sets as owner
        from quorune.compiler.token_templates import fixed_token_creation_effect_template
        from quorune.destruction import DestructionError
        from quorune.record import authoritative_state_hash

        s = self.session(808350)
        prior_definition = fixed_token_creation_effect_template(
            'Create one 1/1 colorless Phyrexian Mite artifact creature token with toxic 1 and "This token can\'t block.".')
        old_ref, = s.engine.create_token('A', name=prior_definition.effect['name'],
            characteristics=prior_definition.effect['characteristics'], quantity=1, reason='Preexisting Mite witness')
        old_mite = next(card for card in s.state.cards.values() if card.ref == old_ref)
        chatterfang = self.add(s.engine, 'Chatterfang, Squirrel General')
        doubler = self.add(s.engine, 'Parallel Lives')
        own = self.add(s.engine, 'Generic Bound Body', ref='twilight-destroy-A')
        opposing = self.add(s.engine, 'Generic Bound Body', seat='B', ref='twilight-destroy-B')
        protected = self.add(s.engine, 'Generic Bound Body', seat='D', ref='twilight-indestructible')
        protected.temporary_keywords.append('indestructible')
        artifact = self.add(s.engine, 'Sol Ring', seat='C', ref='twilight-noncreature')
        spell = self.add(s.engine, "White Sun's Twilight", zone='hand')
        for card in (spell, chatterfang, doubler):
            self.assertTrue(self.binding(s, card)['strict_capability_ready'])
        before_life = s.state.players['A'].life
        price = {'C': 5, 'W': 2}
        self.prepare(s, mana=price)
        self.checkpoint(s)
        self.normal(s, spell, price, x=5)
        decision = self.settle(s)
        self.assertEqual('replacement.order', decision.kind)
        self.assertEqual(before_life + 5, s.state.players['A'].life)
        self.twilight_mites(s, 1)
        self.assertEqual('battlefield', s.state.cards[opposing.object_id].zone)
        s = self.replay_twilight(s, 'replacement-pending', load=True)
        ctx = s.packet('pilot:A', full=True)['decision']['ctx']
        parallel, = [row for row in ctx['options'] if 'Squirrel' not in row['label']]
        command = {'action_id': 'choose', 'replacement': parallel['id']}
        self.reject(s, command, 'pilot:D')
        old_state = authoritative_state_hash(s.state)
        destroyed = {old_mite.object_id, chatterfang.object_id, own.object_id, opposing.object_id}
        real_commit = owner.commit_destruction_plan
        partial = []

        def fail_after_destruction(host, plan, **kwargs):
            result = real_commit(host, plan, **kwargs)
            partial.append(set(result.destroyed_object_ids))
            raise DestructionError('Injected failure after Twilight destruction committed')

        with patch.object(owner, 'commit_destruction_plan', fail_after_destruction):
            rejected = s.act('pilot:A', command)
            self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual([destroyed], partial)
        self.assertEqual(old_state, authoritative_state_hash(s.state))
        accepted = s.act('pilot:A', command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.settle(s))
        self.assertEqual(before_life + 5, s.state.players['A'].life)
        self.assertTrue(all(s.state.cards[card.object_id].zone == 'graveyard' for card in (own, opposing, chatterfang)))
        self.assertTrue(all(s.state.cards[card.object_id].zone == 'battlefield' for card in (protected, artifact, doubler)))
        mites = self.twilight_mites(s, 10)
        self.assertTrue(all(card.object_id != old_mite.object_id for card in mites))
        squirrels = [card for card in self.tokens(s) if card.printed_name == 'Squirrel Token']
        self.assertEqual(10, len(squirrels))
        destruction, = [event for event in s.state.events if event.code == 'effect.permanent.destroy_set']
        self.assertEqual(destroyed, set(destruction.changed_objects))
        replacement_results = {card.object_id for card in (*mites, *squirrels)}
        events = [event for event in s.state.events if event.code == 'token.create'
                  and replacement_results.intersection(event.changed_objects)]
        self.assertEqual(replacement_results, {object_id for event in events for object_id in event.changed_objects})
        self.assertTrue(all(event.event_id < destruction.event_id for event in events))
        self.assertEqual((5, 4, 1), tuple(destruction.details[key] for key in (
            'affected_count', 'destroyed_count', 'indestructible_count',
        )))
        self.replay_twilight(s, 'replacement-settled')

    def test_real_fractal_summoning_pays_announced_x_and_puts_counters_after_entry(self):
        for size in (0,3):
            s=self.session(808000+size);spell=self.add(s.engine,'Fractal Summoning',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.assertTrue(self.binding(s,spell)['strict_capability_ready'])
            self.prepare(s,mana={'C':size,'G':2});self.checkpoint(s);self.normal(s,spell,{'C':size,'G':2},x=size)
            s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
            if size:self.fractals(s,1,size)
            else:self.assertEqual([],self.tokens(s))
            events=[e for e in s.state.events if e.code=='token.create'];self.assertEqual(1,len(events));self.assertEqual(1,events[0].details['base_quantity'])
            self.private_replay(s,private)

    def test_real_leyline_count_and_sage_hand_count_use_their_separate_producers(self):
        for i,name in enumerate(('Leyline Invocation','Manifestation Sage')):
            s=self.session(808010+i);spell=self.add(s.engine,name,zone='hand');self.add(s.engine,'Forest');self.add(s.engine,'Island')
            private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.assertTrue(self.binding(s,spell)['strict_capability_ready'])
            cost={'C':5,'G':1} if i==0 else {'G':4};self.prepare(s,mana=cost);self.checkpoint(s)
            count=2 if i==0 else len(s.state.players['A'].zones['hand'])-1
            self.normal(s,spell,cost);s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.fractals(s,1,count);self.private_replay(s,private)

    def test_created_object_result_binding_is_closed_and_preserves_amount_binding(self):
        from quorune.semantic_choices.model import AutoContinue,IntentResultObjects,IntentResultAmount,SemanticChoiceError
        from quorune.replacement.immutable import FrozenMap
        prepared=AutoContinue('Bind created results',(FrozenMap({'cards':[],'amount':0}),),
            amount_from_result=IntentResultAmount(1,0,'amount'),objects_from_result=IntentResultObjects(0,0))
        self.assertEqual(FrozenMap({'cards':['T1','T2'],'amount':3}),prepared.resolved_effects({0:('T1','T2'),1:3})[0])
        for value in (None,'T1',('T1','T1'),('',),('T1',False)):
            with self.assertRaises(SemanticChoiceError):prepared.resolved_effects({0:value,1:3})

    def test_nine_originals_have_complete_admission_and_independent_runtime_minimum(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(808020)
        for i,name in enumerate(NAMES):
            card=self.add(s.engine,name,ref='created-source-'+str(i));bound=self.binding(s,card)
            self.assertTrue(bound['strict_capability_ready'],(name,bound['blockers']))
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=self.registry,capability_profile='commander_review');self.assertEqual('exact',ir.status,(name,ir.material_residuals))
            program=next((p for p in s.engine.semantics.programs_for_oracle(card.oracle_id) if CAP in p.capability_dependencies),None);self.assertIsNotNone(program,name)
            deps=tuple(c for c in program.capability_dependencies if c!=CAP)
            changed=replace(program,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            binding=bind_semantic_program_runtime(changed,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(binding['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,binding['blockers'])

    def test_entry_observer_sees_zero_base_before_separately_instructed_counters(self):
        s=self.session(808030);observer=self.add(s.engine,'Generic Small Entry Observer');spell=self.add(s.engine,'Fractal Summoning',zone='hand')
        drawn=self.top(s,'Sol Ring','entry-draw');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.assertTrue(self.binding(s,observer)['strict_capability_ready']);self.prepare(s,mana={'C':3,'G':2});self.checkpoint(s)
        self.normal(s,spell,{'C':3,'G':2},x=3);s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        self.fractals(s,1,3);self.assertEqual('hand',s.state.cards[drawn.object_id].zone);self.private_replay(s,private)

    def test_public_count_is_read_after_creation_and_additional_creatures_receive_counters(self):
        s=self.session(808040);extra=self.add(s.engine,'Generic Additional Thopter Observer');self.add(s.engine,'Generic Bound Body')
        spell=self.add(s.engine,'Generic Counted Construct Subject',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        for card in (extra,spell):self.assertTrue(self.binding(s,card)['strict_capability_ready'])
        self.prepare(s,mana={'G':1});self.checkpoint(s);self.normal(s,spell,{'G':1});s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        tokens={c.printed_name:c for c in self.tokens(s)};self.assertEqual({'Construct Token','Thopter Token'},set(tokens))
        for card in tokens.values():self.assertEqual(3,card.counters.get('+1/+1'))
        self.assertEqual(3,s.engine._numeric_stat(tokens['Construct Token'].object_id,'power'))
        self.assertEqual(4,s.engine._numeric_stat(tokens['Thopter Token'].object_id,'power'));self.private_replay(s,private)

    def test_additional_noncreature_token_is_also_a_counter_recipient(self):
        s=self.session(808050);self.add(s.engine,'Generic Token Status Observer');spell=self.add(s.engine,'Generic Fixed Construct Subject',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand');self.assertTrue(self.binding(s,spell)['strict_capability_ready'])
        self.prepare(s,mana={'G':1});self.checkpoint(s);self.normal(s,spell,{'G':1});s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
        tokens={c.printed_name:c for c in self.tokens(s)};self.assertEqual({'Construct Token','Map Token'},set(tokens))
        for card in tokens.values():self.assertEqual(3,card.counters.get('+1/+1'))
        self.assertNotIn('Creature',s.engine._effective_card_data(tokens['Map Token'])['type_line']);self.private_replay(s,private)

    def test_token_and_counter_replacement_choices_resume_without_repeated_creation(self):
        s=self.session(808060)
        for i,name in enumerate(('Parallel Lives','Parallel Lives','Branching Evolution','Hardened Scales')):
            card=self.add(s.engine,name,ref='replacement-'+str(i));self.assertTrue(self.binding(s,card)['strict_capability_ready'],name)
        spell=self.add(s.engine,'Fractal Summoning',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'G':2});self.checkpoint(s);self.normal(s,spell,{'C':3,'G':2},x=3)
        choices=0
        for _ in range(16):
            pending=self.settle(s)
            if pending is None:break
            self.assertEqual('replacement.order',pending.kind)
            s=self.replay(s,load=True);principal=s.pending_principals()[0];options=s.packet(principal,full=True)['decision']['ctx']['options'];self.assertTrue(options)
            selected=next((o for o in options if 'replacement-2' in o['id']),None) if self.tokens(s) else options[0]
            self.assertIsNotNone(selected,options)
            command={'action_id':'choose','replacement':selected['id']};self.reject(s,command,'pilot:D')
            result=s.act(principal,command);self.assertTrue(result.ok,result.summary);choices+=1
        else:self.fail('Creation/counter replacements did not finish')
        self.assertGreaterEqual(choices,2);tokens=self.fractals(s,4,7)
        self.assertEqual(4,len(tokens));self.assertEqual(1,sum(e.code=='token.create' for e in s.state.events));self.private_replay(s,private)

    def test_real_wild_hypothesis_counters_precede_private_surveil(self):
        s=self.session(808070);spell=self.add(s.engine,'Wild Hypothesis',zone='hand')
        first=self.top(s,'Sol Ring','surveil-first');second=self.top(s,'Divination','surveil-second');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'G':1});self.checkpoint(s);self.normal(s,spell,{'C':3,'G':1},x=3)
        pending=self.settle(s);self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind);self.fractals(s,1,3)
        command={'action_id':'choose','cards':{'top':[second.ref],'graveyard':[first.ref]}};self.reject(s,command,'pilot:D')
        s=self.replay(s,load=True);result=s.act('pilot:A',command);self.assertTrue(result.ok,result.summary);self.assertIsNone(self.settle(s))
        self.assertEqual('graveyard',s.state.cards[first.object_id].zone);self.private_replay(s,private)

    def test_real_history_and_source_counter_definitions_use_their_instruction_time(self):
        from quorune.turn_history import reset_turn_history
        s=self.session(808080);spell=self.add(s.engine,'Fractal Anomaly',zone='hand');draw=self.add(s.engine,'Divination',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.turn_history=reset_turn_history(s.state.turn_history,s.state.turn_sequence)
        self.prepare(s,mana={'C':2,'U':2});self.checkpoint(s);self.normal(s,draw,{'C':2,'U':1});self.assertIsNone(self.settle(s))
        self.normal(s,spell,{'U':1});s=self.replay(s,load=True);self.assertIsNone(self.settle(s));self.fractals(s,1,2);self.private_replay(s,private)
        s=self.session(808081);source=self.add(s.engine,'Paradox Zone',zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':4,'G':1});self.checkpoint(s);self.normal(s,source,{'C':4,'G':1});self.assertIsNone(self.settle(s))
        self.assertEqual(1,s.state.cards[source.object_id].counters.get('growth'))
        self.advance(s,lambda q:q.state.phase=='ending' and bool(q.state.stack));self.assertIsNone(self.settle(s))
        self.assertEqual(2,s.state.cards[source.object_id].counters.get('growth'));self.fractals(s,1,2);self.private_replay(s,private)

    def test_real_fixed_entry_sources_keep_counter_base_and_existing_siblings(self):
        for i,(name,cost,size) in enumerate((('Additive Evolution',{'C':3,'G':2},3),('Recon Craft Theta',{'C':4},1))):
            s=self.session(808090+i);source=self.add(s.engine,name,zone='hand');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(s,mana=cost);self.checkpoint(s);self.normal(s,source,cost);s=self.replay(s,load=True);self.assertIsNone(self.settle(s))
            tokens=self.tokens(s);self.assertEqual(1,len(tokens));self.assertEqual(size,tokens[0].counters.get('+1/+1'))
            self.assertEqual('0',str(s.engine._copyable_characteristics(tokens[0])['power']))
            if i:self.assertIn('flying',s.engine._combat_keywords(s.state.cards[source.object_id]))
            self.private_replay(s,private)

    def test_real_berta_activation_and_increment_mana_siblings_remain_exact(self):
        s=self.session(808100);source=self.add(s.engine,'Berta, Wise Extrapolator');spell=self.add(s.engine,'Fractal Summoning',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':8,'G':2});self.checkpoint(s);self.activate(s,source,payment={'C':3},x=3)
        self.assertIsNone(self.settle(s));self.fractals(s,1,3);self.assertEqual(0,s.state.cards[source.object_id].counters.get('+1/+1',0))
        self.normal(s,spell,{'C':5,'G':2},x=5);pending=self.settle(s);self.assertIsNotNone(pending);self.assertEqual('semantic.choice',pending.kind)
        self.assertEqual(1,s.state.cards[source.object_id].counters.get('+1/+1'))
        s=self.replay(s,load=True);result=s.act('pilot:A',{'action_id':'choose','choice':'U'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.settle(s));self.assertEqual([3,5],sorted(c.counters.get('+1/+1',0) for c in self.tokens(s)))
        self.assertEqual(1,s.state.players['A'].mana_pool['U']);self.private_replay(s,private)

    def test_closed_schemas_and_hybrid_x_preserve_unowned_boundaries(self):
        from quorune.compiler.created_token_counters import created_token_counter_effect_template
        from quorune.created_token_counter_model import validate_created_token_counter,COUNTER_CREATED
        from quorune.semantic_choices.model import SemanticChoiceError
        from quorune.semantic_choices.created_token_counters import CREATED_TOKEN_COUNTER_HANDLERS
        text='Create a 0/0 green and blue Fractal creature token. Put three +1/+1 counters on it.'
        compiled=created_token_counter_effect_template(text,source_name='Fixture');self.assertIsNotNone(compiled)
        effect=compiled[1][0];validate_created_token_counter(effect)
        for change in ({'amount':True},{'amount':-1},{'schema_version':False},{'extra':True},
                {'token':{**effect['token'],'quantity':2}}, {'token':{**effect['token'],'controller':'B'}}):
            with self.assertRaises((ValueError,TypeError)):validate_created_token_counter({**effect,**change})
        child={'op':COUNTER_CREATED,'schema_version':1,'controller':'A','source':'source','amount':3,'cards':['T1','T1']}
        with self.assertRaises(ValueError):validate_created_token_counter(child,resolved=True)
        for bad in (text.replace('on it','on target creature'),text+' Attach this Equipment to it.',
                text.replace('three','any number of'),text.replace('+1/+1','-1/-1')):
            self.assertIsNone(created_token_counter_effect_template(bad,source_name='Fixture'),bad)
        for handler in CREATED_TOKEN_COUNTER_HANDLERS:
            with self.assertRaises(SemanticChoiceError):handler.complete(None,{'cards':['foreign']},None)
        from test_declared_effect_amounts import generic_spell
        for cost in ('{X}{G/U}{G/U}','{X}{X}{R/G}'):
            ir=compile_oracle_card(generic_spell('Create an X/X green Ooze creature token.',mana_cost=cost),capability_registry=self.registry,capability_profile='commander_review')
            self.assertEqual('exact',ir.status,ir.material_residuals)
        ir=compile_oracle_card(generic_spell('Create an X/X green Ooze creature token.',mana_cost='{X}{mystery}'),capability_registry=self.registry,capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)

    def test_verified_withdrawal_and_fresh_omitted_counter_step_reject(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        baseline=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));decl=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        for capability in (CAP,'token.creation.fixed_definition','counter.producer.fixed_effect'):
            raw=deepcopy(baseline);next(c for c in raw['capabilities'] if c['id']==capability).update(status='blocked',blockers=['withdrawn creation/counter binding'],dependency_fail_closed_status='not_run')
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,declaration_source=decl,discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            ir=compile_oracle_card(self.db.lookup('Fractal Summoning'),capability_registry=registry,capability_profile='commander_review');blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+capability+':blocked' in b for b in blockers),blockers);self.assertFalse(any('evidence_index:unverified' in b for b in blockers))
        from quorune.semantic_choices.created_token_counters import CreatedTokenCounterHandler
        from quorune.created_token_counter_model import COUNTER_CREATED
        original=CreatedTokenCounterHandler.prepare
        def omit(handler,effect,context):
            prepared=original(handler,effect,context)
            return replace(prepared,preparation_intents=()) if handler.operation==COUNTER_CREATED else prepared
        method='test_real_fractal_summoning_pays_announced_x_and_puts_counters_after_entry'
        with patch.object(CreatedTokenCounterHandler,'prepare',omit):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
