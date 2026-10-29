"""Default continuous-component registry assembly; handlers keep their own owners."""
from functools import lru_cache
from ..rules.capabilities import load_default_capability_registry
from ..counter_snapshot import COUNTER_TOTAL_CAPABILITY
from .continuous_components import (ContinuousEffectComponentRegistry, FixedPowerToughnessAnthemHandler,
    FixedQueryPowerToughnessAnthemHandler, AddBasicLandTypeHandler, FixedQueryAbilityGrantHandler,
    FixedQueryKeywordGrantHandler, FixedQueryCharacteristicGrantHandler, FixedQueryProtectionGrantHandler,
    FixedQueryCharacteristicProtectionGrantHandler, LevelerBandsHandler)

@lru_cache(maxsize=1)
def default_continuous_effect_component_registry(
) -> ContinuousEffectComponentRegistry:
    from .station_symbols import StationSymbolsHandler
    from .cipher import CipherEncodingGrantHandler
    from .conditional_flash import ConditionalFlashKeywordHandler
    from .negative_keyword_grants import DisjointNegativeKeywordGrantHandler
    from .enchanted_player_characteristics import EnchantedPlayerCharacteristicsHandler
    from .defender_declarations import DefenderDeclarationPermissionHandler
    from .conditional_color_grants import ConditionalColorGrantHandler
    from .keyword_prohibitions import OpponentKeywordProhibitionHandler
    from .basic_type_addition import ScopedBasicTypeAdditionHandler
    from .basic_type_setting import FixedBasicTypeSettingHandler
    from .chosen_characteristics import ChosenCharacteristicsHandler
    from .conditional_chosen_characteristics import ConditionalChosenCharacteristicsHandler
    from .attached_continuous import AttachedFixedCharacteristicsHandler
    from .chosen_aura_protection import SourceChosenAuraProtectionHandler
    from .soulbond import SoulbondPairedGrantHandler
    from .fixed_characteristic_settings import FixedCharacteristicSettingsHandler, ConditionalCharacteristicSettingsHandler, ConditionalCreatureTypeRemovalHandler
    from .attached_control import AttachedControlHandler
    from .quantified_anthems import QuantifiedAnthemHandler
    from .recipient_anthems import RecipientAnthemHandler
    from .shared_type_anthems import SharedTypeAnthemHandler
    from .graveyard_grants import GraveyardQueryAbilityGrantHandler, GraveyardConditionalAbilityGrantHandler
    from .composite_grants import CompositeAbilityGrantHandler, FixedTypeReplacementGrantHandler
    from .subtype_composite_grants import FixedSubtypeCompositeGrantHandler
    from .conditional_continuous import (
        FixedPublicStateCharacteristicsHandler,
        FixedPublicStateGrantedAbilityHandler,
        ConditionalDefenderPermissionHandler,
        ConditionalAsUnblockedAssignmentHandler,
        ConditionalBlockCapacityHandler,
        ConditionalDeclarationGrantHandler,
        GraveyardStaticCharacteristicsHandler,
        ConditionalTypedKeywordHandler,
    )

    registry = ContinuousEffectComponentRegistry(
        (
            OpponentKeywordProhibitionHandler(),
            ChosenCharacteristicsHandler(),
            ConditionalChosenCharacteristicsHandler(),
            FixedPowerToughnessAnthemHandler(),
            FixedQueryPowerToughnessAnthemHandler(),
            FixedQueryPowerToughnessAnthemHandler(
                handler_id="continuous.anthem.source-counter-query.v3", schema_version=3,
            ),
            AddBasicLandTypeHandler(),
            ScopedBasicTypeAdditionHandler(),
            FixedBasicTypeSettingHandler(),
            FixedQueryAbilityGrantHandler(),
            FixedQueryKeywordGrantHandler(),
            FixedQueryCharacteristicGrantHandler(),
            FixedQueryProtectionGrantHandler(),
            FixedQueryCharacteristicProtectionGrantHandler(),
            LevelerBandsHandler(),
            StationSymbolsHandler(),
            CipherEncodingGrantHandler(),
            FixedPublicStateCharacteristicsHandler(),
            FixedPublicStateGrantedAbilityHandler(),
            ConditionalColorGrantHandler(),
            ConditionalFlashKeywordHandler(),
            FixedQueryPowerToughnessAnthemHandler(
                handler_id='continuous.anthem.source-counter-total-query.v4', schema_version=4,
                capability_dependencies=('continuous.power_toughness.fixed_anthem', COUNTER_TOTAL_CAPABILITY)),
            ConditionalDefenderPermissionHandler(),
            DefenderDeclarationPermissionHandler(),
            ConditionalAsUnblockedAssignmentHandler(),
            ConditionalBlockCapacityHandler(),
            ConditionalDeclarationGrantHandler(),
            GraveyardStaticCharacteristicsHandler(),
            GraveyardQueryAbilityGrantHandler(),
            CompositeAbilityGrantHandler(),
            FixedTypeReplacementGrantHandler(),
            FixedSubtypeCompositeGrantHandler(),
            GraveyardConditionalAbilityGrantHandler(),
            ConditionalTypedKeywordHandler(),
            AttachedFixedCharacteristicsHandler(),
            SourceChosenAuraProtectionHandler(),
            SoulbondPairedGrantHandler(),
            FixedCharacteristicSettingsHandler(),
            EnchantedPlayerCharacteristicsHandler(),
            DisjointNegativeKeywordGrantHandler(),
            ConditionalCharacteristicSettingsHandler(),
            ConditionalCreatureTypeRemovalHandler(),
            AttachedControlHandler(),
            QuantifiedAnthemHandler(),
            RecipientAnthemHandler(),
            SharedTypeAnthemHandler(),
        )
    )
    registry.require_registered_capabilities(
        load_default_capability_registry()
    )
    return registry.freeze()
