from .param import BoolParameter, NumberParameter, StringParameter
from common.i18n import gettext_lazy as _


class CostNode(StringParameter):
    name = _('Cost node')
    id = 'cost_node'


class ImpactNode(StringParameter):
    id = 'impact_node'


class EfficiencyUnit(StringParameter):
    id = 'efficiency_unit'


class MunicipalityName(StringParameter):
    name = _('Municipality name')
    id = 'municipality_name'


class DiscountNodeName(StringParameter):
    name = _('Discount node name')
    id = 'discount_node_name'


class DiscountRate(NumberParameter):
    name = _('Discount rate')
    id = 'discount_rate'


class AvoidedElectricityCapacityPrice(NumberParameter):
    name = _('Avoided electricity capacity price')
    id = 'avoided_electricity_capacity_price'


class HealthImpactsPerKwh(NumberParameter):
    name = _('Health impacts per kWh')
    id = 'health_impacts_per_kwh'


class HeatCo2Ef(NumberParameter):
    name = _('Heat CO2 emission factor')
    id = 'heat_co2_ef'


class ElectricityCo2Ef(NumberParameter):
    name = _('Electricity CO2 emission factor')
    id = 'electricity_co2_ef'


class RenovationRateBaseline(NumberParameter):
    name = _('Renovation rate baseline')
    id = 'renovation_rate_baseline'


class IncludeSocial(BoolParameter):
    name = _('Include energy taxes in calculations?')
    id = 'include_energy_taxes'


class IncludeCO2(BoolParameter):
    name = _('Include CO2 cost variable in calculations?')
    id = 'include_co2'


class IncludeHealth(BoolParameter):
    name = _('Include health impact variable in calculations?')
    id = 'include_health'


class IncludeElAvoided(BoolParameter):
    name = _('Include avoided electricity capacity variable in calculations?')
    id = 'include_el_avoided'


class PriceOfCo2(NumberParameter):
    name = _('Price of CO2')
    id = 'price_of_co2'


class PriceOfCo2AnnualChange(NumberParameter):
    name = _('Price of CO2 annual change')
    id = 'price_of_co2_annual_change'


class PriceOfElectricity(NumberParameter):
    name = _('Price of electricity')
    id = 'price_of_electricity'


class PriceOfElectricityAnnualChange(NumberParameter):
    name = _('Price of electricity annual change')
    id = 'price_of_electricity_annual_change'


class PriceOfHeat(NumberParameter):
    name = _('Price of Heat')
    id = 'price_of_heat'


class PriceOfHeatAnnualChange(NumberParameter):
    name = _('Price of heat annual change')
    id = 'price_of_heat_annual_change'


class AllInInvestment(BoolParameter):
    name = _('Invest all on the first year (in contrast to continuous investing)?')
    id = 'all_in_investment'


class Placeholder(BoolParameter):
    name = _('Placeholder for updated_building_code_residential')
    id = 'placeholder'
