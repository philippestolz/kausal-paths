import numpy as np
import pandas as pd
from params import StringParameter, BoolParameter
from nodes import Node
from nodes.constants import (
    VALUE_COLUMN, YEAR_COLUMN, EMISSION_FACTOR_QUANTITY, EMISSION_QUANTITY, ENERGY_QUANTITY
)
from nodes.exceptions import NodeError
from nodes.node import NodeMetric


class AlasNode(Node):
    input_datasets = [
        'syke/alas_emissions',
    ]
    global_parameters = ['municipality_name']
    output_metrics = {
        EMISSION_QUANTITY: NodeMetric(unit='kt/a', quantity=EMISSION_QUANTITY),
        ENERGY_QUANTITY: NodeMetric(unit='GWh/a', quantity=ENERGY_QUANTITY),
        EMISSION_FACTOR_QUANTITY: NodeMetric(unit='g/kWh', quantity=EMISSION_FACTOR_QUANTITY)
    }

    def compute(self) -> pd.DataFrame:
        muni_name = self.get_global_parameter_value('municipality_name')

        df = self.get_input_dataset()
        df = df[df['kunta'] == muni_name].drop(columns=['kunta'])
        df = df.rename(columns={
            'vuosi': YEAR_COLUMN,
            'ktCO2e': EMISSION_QUANTITY,
            'energiankulutus': ENERGY_QUANTITY,
        })
        df[EMISSION_FACTOR_QUANTITY] = df[EMISSION_QUANTITY] / df[ENERGY_QUANTITY].replace(0, np.nan)
        df['Sector'] = ''
        for i in range(1, 6):
            if i > 1:
                df['Sector'] += '|'
            df['Sector'] += df['taso_%d' % i].astype(str)
        df.loc[df['päästökauppa'], 'Sector'] += ':ETS'

        df = df[[YEAR_COLUMN, EMISSION_QUANTITY, ENERGY_QUANTITY, EMISSION_FACTOR_QUANTITY, 'Sector']]
        df = df.set_index(['Year', 'Sector'])
        if len(df) == 0:
            raise NodeError(self, "Municipality %s not found in data" % muni_name)
        for dim_id, dim in self.output_metrics.items():
            df[dim_id] = self.convert_to_unit(df[dim_id], dim.unit)
        return df


class AlasEmissions(Node):
    unit = 'kt/a'
    quantity = EMISSION_QUANTITY
    allowed_input_classes = [
        AlasNode
    ]
    allowed_parameters = [
        StringParameter(
            local_id='sector',
            label='Sector path in ALaS',
            is_customizable=False
        ),
        BoolParameter(
            local_id='required',
            label='Has to exist in data',
            is_customizable=False,
        ),
    ]

    def compute(self) -> pd.DataFrame:
        df = self.input_nodes[0].get_output()
        sector = self.get_parameter_value('sector')
        required = self.get_parameter_value('required', required=False)
        try:
            df = df.xs(sector, level='Sector')
        except KeyError:
            if not required:
                years = df.index.get_level_values('Year').unique()
                dt = df.dtypes[EMISSION_QUANTITY]
                df = pd.DataFrame([0.0] * len(years), index=years, columns=[EMISSION_QUANTITY])
                df[EMISSION_QUANTITY] = df[EMISSION_QUANTITY].astype(dt)
            else:
                raise
        df = df[[EMISSION_QUANTITY]]
        df = df.rename(columns={EMISSION_QUANTITY: VALUE_COLUMN})
        df['Forecast'] = False
        return df
