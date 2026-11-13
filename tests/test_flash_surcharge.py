"""A timing permission never substitutes for its additional mana payment."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from quorune.cast_timing import (
    ManaSurchargeFlashSpec,CastTimingPermissionError,SURCHARGE_FLASH_HANDLER,
    SURCHARGE_FLASH_OPTION_FIELD,
)
from quorune.semantic_runtime.surcharge_flash import ManaSurchargeFlashPermissionHandler
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.rules.casting.surcharge_flash_costs import mana_surcharge_flash_variants


class FlashSurchargeModelTests(unittest.TestCase):
    def test_cost_spec_and_permission_component_are_closed_and_do_not_grant_a_keyword(self):
        spec=ManaSurchargeFlashSpec(2);self.assertEqual(spec,ManaSurchargeFlashSpec.from_dict(spec.to_dict()))
        for value in (0,-1,True,'2',None):
            with self.subTest(value=value),self.assertRaises(CastTimingPermissionError):ManaSurchargeFlashSpec(value)
        for changed in ({'schema_version':True,'generic':2},{'schema_version':1,'generic':2,'extra':0}):
            with self.assertRaises(CastTimingPermissionError):ManaSurchargeFlashSpec.from_dict(changed)
        handler=ManaSurchargeFlashPermissionHandler()
        descriptor={'handler_id':SURCHARGE_FLASH_HANDLER,'schema_version':1,'event':'cast.permission','surcharge':spec.to_dict()}
        self.assertEqual(spec,handler.validate(descriptor));permission,=handler.lower(descriptor,None)
        self.assertEqual({'schema_version':1,'timing':'instant','scope':'this_face'},permission.to_dict())
        for changed in ({**descriptor,'schema_version':True},{**descriptor,'event':'characteristics.evaluate'},
                        {**descriptor,'permission':permission.to_dict()}):
            with self.assertRaises(SemanticNodeError):handler.validate(changed)

    def test_only_the_paid_branch_survives_without_independent_timing_and_server_override_is_explicit(self):
        class Host:
            state=SimpleNamespace(active_player='B',phase='precombat_main',stack=[],config=SimpleNamespace(strict_timing=True))
            def card_record(self,card):return SimpleNamespace(type_line='Creature')
            def _select_cast_face(self,record,face):return None
            def _mana_vector(self,value):return {'GENERIC':0,**dict(value)}
        host=Host();card=SimpleNamespace(active_face='front');base=[{'id':'printed','kind':'normal','requirements':{'GENERIC':3,'G':1}}]
        with patch('quorune.rules.casting.surcharge_flash_costs.compiled_mana_surcharge_flash_spec',return_value=ManaSurchargeFlashSpec(2)),patch(
                'quorune.rules.casting.proposal._combined_cast_timing_permissions',return_value=()):
            paid=mana_surcharge_flash_variants(host,'A',card,base,suppress_source_costs=False)
            self.assertEqual(1,len(paid));self.assertEqual(5,paid[0]['requirements']['GENERIC'])
            self.assertEqual({'schema_version':1,'generic':2},paid[0][SURCHARGE_FLASH_OPTION_FIELD])
            self.assertEqual(3,base[0]['requirements']['GENERIC'])
            override=mana_surcharge_flash_variants(host,'A',card,base,suppress_source_costs=False,ignore_timing=True)
            self.assertEqual(2,len(override));self.assertEqual('printed',override[0]['id'])
            host.state.active_player='A'
            ordinary=mana_surcharge_flash_variants(host,'A',card,base,suppress_source_costs=False)
            self.assertEqual(2,len(ordinary));self.assertNotIn(SURCHARGE_FLASH_OPTION_FIELD,ordinary[0])
        self.assertIs(base,mana_surcharge_flash_variants(host,'A',card,base,suppress_source_costs=True))
