"""CR603.3d/608.2b: a target relation preserves a proved occurrence player."""

import unittest
from types import SimpleNamespace

from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_granted_skulk import record


class OccurrencePlayerTargetCompilerTests(unittest.TestCase):
    def test_player_damage_and_cast_events_bind_only_their_proved_player(self):
        registry=load_default_capability_registry()
        for head,body,reference in (
                ('this creature deals combat damage to a player','you may destroy target artifact that player controls.','$context.target'),
                ('this creature deals damage to a player','tap target land that player controls.','$context.target'),
                ('an opponent casts a spell','tap target creature that player controls.','$context.player'),
                ('this creature attacks','tap target creature defending player controls.','$context.defending_player')):
            with self.subTest(head=head,body=body):
                ir=compile_oracle_card(record('Whenever '+head+', '+body,'Creature — Human'),capability_registry=registry)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                self.assertEqual(reference,ir.faces[0].nodes[0].target_schema['controller_seat'])
                self.assertNotIn('controller_relation',ir.faces[0].nodes[0].target_schema)

    def test_controller_schema_binds_each_field_and_rejects_unavailable_values(self):
        from quorune.rules.event_player_targets import bound_event_player_controller_schema
        for field in ('defending_player','target','player'):
            schema={'controller_seat':'$context.'+field,'count':1}
            self.assertEqual({**schema,'controller_seat':'C'},bound_event_player_controller_schema(schema,{field:'C','controller':'A'}))
            for value in (None,True,'','$context.player'):
                with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                    bound_event_player_controller_schema(schema,{field:value})
        ordinary={'controller_seat':'B','count':1}
        self.assertIs(ordinary,bound_event_player_controller_schema(ordinary,{}))

    def test_runtime_proof_rejects_nonplayer_damage_wrong_events_and_missing_scope(self):
        from quorune.compiler.direct_target import DirectPermanentTargetSpec
        from quorune.rules.event_player_targets import event_player_target_runtime_dependencies
        schema=DirectPermanentTargetSpec(types_any=('artifact',),controller_seat='$context.target').to_target_schema()
        program=SimpleNamespace(event='damage.dealt',event_condition={'field':'target_kind','op':'eq','value':'player'},
            active_zone='battlefield',target_schema=schema,coverage=(),effects=())
        self.assertEqual(('target.revalidate_resolution',),event_player_target_runtime_dependencies(program))
        for change in ({'event_condition':None},{'event_condition':{'field':'target_kind','op':'eq','value':'permanent'}},
                {'event':'creature.attacks'},{'active_zone':'graveyard'},
                {'event_condition':{'any':[program.event_condition,{'field':'target_kind','op':'eq','value':'permanent'}]}}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                event_player_target_runtime_dependencies(SimpleNamespace(**{**vars(program),**change}))

    def test_ambiguous_that_player_and_correlated_or_multiple_targets_remain_residual(self):
        registry=load_default_capability_registry()
        for text in ('Whenever this creature attacks, tap target creature that player controls.',
                'Whenever this creature deals damage to a creature, tap target land that player controls.',
                'Whenever this creature deals damage to a player, tap target creature that player controls and target land that player controls.',
                'Whenever this creature deals damage to a player, tap target creature that player controls. Rewrite history.'):
            with self.subTest(text=text):self.assertNotEqual('exact',compile_oracle_card(record(text,'Creature — Human'),capability_registry=registry).status)
