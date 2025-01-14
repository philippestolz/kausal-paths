import functools
from common.perf import PerfCounter
from nodes.calc import extend_last_historical_value_pl, nafill_all_forecast_years
from params.param import Parameter, BoolParameter, NumberParameter, ParameterWithUnit, StringParameter
from typing import List, ClassVar, Tuple
import polars as pl
import pandas as pd
import pint

from common.i18n import TranslatedString
from common import polars as ppl
from .constants import FORECAST_COLUMN, MIX_QUANTITY, NODE_COLUMN, VALUE_COLUMN, YEAR_COLUMN, DEFAULT_METRIC
from .node import Node, NodeMetric
from .exceptions import NodeError
from nodes.actions.energy_saving import UsBuildingAction


EMISSION_UNIT = 'kg'


class SimpleNode(Node):
    allowed_parameters: ClassVar[List[Parameter]] = [
        BoolParameter(
            local_id='fill_gaps_using_input_dataset',
            label=TranslatedString(en="Fill in gaps in computation using input dataset"),
            is_customizable=False
        ),
        BoolParameter(
            local_id='replace_output_using_input_dataset',
            label=TranslatedString(en="Replace output using input dataset"),
            is_customizable=False
        )
    ]

    def replace_output_using_input_dataset_pl(self, df: ppl.PathsDataFrame) -> ppl.PathsDataFrame:
        # If we have also data from an input dataset, we only fill in the gaps from the
        # calculated data.
        df = df.drop_nulls()

        data_df = self.get_input_dataset_pl(required=False)
        if data_df is None:
            return df

        data_latest_year: int = data_df[YEAR_COLUMN].max()  # type: ignore
        df_latest_year: int = df[YEAR_COLUMN].max()  # type: ignore
        df_meta = df.get_meta()
        data_meta = data_df.get_meta()
        if df_latest_year > data_latest_year:
            for col in data_meta.metric_cols:
                data_df = data_df.ensure_unit(col, df_meta.units[col])
            data_df = data_df.paths.join_over_index(df, how='outer')
            fills = [pl.col(col).fill_null(pl.col(col + '_right')) for col in data_meta.metric_cols]
            data_df = data_df.select([YEAR_COLUMN, *data_meta.dim_ids, FORECAST_COLUMN, *fills], units=df_meta.units)

        return data_df

    def replace_output_using_input_dataset(self, df: pd.DataFrame) -> pd.DataFrame:
        return self.replace_output_using_input_dataset_pl(ppl.from_pandas(df)).to_pandas()

    def fill_gaps_using_input_dataset(self, df: pd.DataFrame) -> pd.DataFrame:
        ndf = ppl.from_pandas(df)
        out = self.fill_gaps_using_input_dataset_pl(ndf)
        return out.to_pandas()

    def fill_gaps_using_input_dataset_pl(self, df: ppl.PathsDataFrame) -> ppl.PathsDataFrame:
        data_df = self.get_input_dataset_pl(required=False)
        if data_df is None:
            return df

        meta = df.get_meta()
        df = df.paths.join_over_index(data_df, how='outer')
        for metric_col in meta.metric_cols:
            right = '%s_right' % metric_col  # FIXME Not clear that the right column has same metric name as left
            df = df.ensure_unit(right, meta.units[metric_col])
            df = df.with_columns([
                pl.col(metric_col).fill_null(pl.col(right))
            ])
        return df


class AdditiveNode(SimpleNode):
    """Simple addition of inputs"""
    allowed_parameters: ClassVar[list[Parameter]] = [
        StringParameter(local_id='metric', is_customizable=False),
    ] + SimpleNode.allowed_parameters

    def add_nodes(self, ndf: pd.DataFrame | None, nodes: List[Node], metric: str | None = None) -> pd.DataFrame:
        if ndf is not None:
            df = ppl.from_pandas(ndf)
        else:
            df = None
        out = self.add_nodes_pl(df, nodes, metric)
        return out.to_pandas()

    def compute(self):
        df = self.get_input_dataset_pl(required=False)
        metric = self.get_parameter_value('metric', required=False)
        assert self.unit is not None
        if df is not None:
            if VALUE_COLUMN not in df.columns:
                if len(df.metric_cols) == 1:
                    df = df.rename({df.metric_cols[0]: VALUE_COLUMN})
                elif metric is not None:
                    if metric in df.columns:
                        df = df.rename({metric: VALUE_COLUMN})
                        cols = [YEAR_COLUMN, *df.dim_ids, VALUE_COLUMN]
                        if FORECAST_COLUMN in df.columns:
                            cols.append(FORECAST_COLUMN)
                        df = df.select(cols)
                    else:
                        raise NodeError(self, "Metric is not found in metric columns")
                else:
                    compatible_cols = [
                        col for col, unit in df.get_meta().units.items()
                        if self.is_compatible_unit(unit, self.unit)
                    ]
                    if len(compatible_cols) == 1:
                        df = df.rename({compatible_cols[0]: VALUE_COLUMN})
                        cols = [YEAR_COLUMN, *df.dim_ids, VALUE_COLUMN]
                        if FORECAST_COLUMN in df.columns:
                            cols.append(FORECAST_COLUMN)
                        df = df.select(cols)
                    else:
                        raise NodeError(self, "Input dataset has multiple metric columns, but no Value column")
            df = df.ensure_unit(VALUE_COLUMN, self.unit)
            df = extend_last_historical_value_pl(df, self.get_end_year())

        if self.get_parameter_value('fill_gaps_using_input_dataset', required=False):
            df = self.add_nodes_pl(None, self.input_nodes, metric)
            df = self.fill_gaps_using_input_dataset_pl(df)
        else:
            df = self.add_nodes_pl(df, self.input_nodes, metric)

        return df


class SubtractiveNode(Node):
    allowed_parameters = [
        BoolParameter(local_id='only_historical', description='Perform subtraction on only historical data', is_customizable=False)
    ]

    def compute(self):
        nodes = list(self.input_nodes)
        mults = [1.0 if i == 0 else -1.0 for i, _ in enumerate(nodes)]
        df = self.add_nodes_pl(None, nodes, node_multipliers=mults)
        only_historical = self.get_parameter_value('only_historical', required=False)
        if only_historical:
            df = df.filter(~pl.col(FORECAST_COLUMN))
        df = extend_last_historical_value_pl(df, self.get_end_year())
        return df


class SectorEmissions(AdditiveNode):
    quantity = 'emissions'
    """Simple addition of subsector emissions"""

    allowed_parameters = AdditiveNode.allowed_parameters + [
        StringParameter(local_id='category', description='Category id for the emission sector dimension', is_customizable=False)
    ]

    def compute(self):
        val = self.get_parameter_value('category', required=False)
        if val is not None:
            df = self.get_input_dataset_pl()
            df_dims = df.dim_ids
            for dim_id in self.input_dimensions.keys():
                if dim_id not in df_dims:
                    raise NodeError(self, "Dataset doesn't have dimension %s" % dim_id)
                df_dims.remove(dim_id)
            if len(df_dims) != 1:
                raise NodeError(self, "Emission sector dimension missing")
            sector_dim = df_dims[0]
            df = df.filter(pl.col(sector_dim).eq(val))
            if not len(df):
                raise NodeError(self, "Emission sector %s not found in input data" % val)
            df = df.drop(sector_dim)
            m = self.get_default_output_metric()
            if len(df.metric_cols) != 1:
                raise NodeError(self, "Input dataset has more than 1 metric")
            df = df.rename({df.metric_cols[0]: m.column_id})
            df = extend_last_historical_value_pl(df, self.get_end_year())
            df = df.drop_nulls()
            return super().add_nodes_pl(df, self.input_nodes)

        return super().compute()


class MultiplicativeNode(SimpleNode):
    """Multiply nodes together with potentially adding other input nodes.

    Multiplication and addition is determined based on the input node units.
    """

    allowed_parameters = SimpleNode.allowed_parameters + [
        BoolParameter(
            local_id='only_historical',
            description='Process only historical rows',
            is_customizable=False,
        ),
        BoolParameter(
            local_id='extend_rows',
            description='Extend last row to future years',
            is_customizable=False,
        )
    ]
    operation_label = 'multiplication'

    def perform_operation(self, nodes: list[Node], outputs: list[ppl.PathsDataFrame]) -> ppl.PathsDataFrame:
        for n in nodes:
            assert n.unit is not None
        assert self.unit is not None

        output_unit = functools.reduce(lambda x, y: x * y, [n.unit for n in nodes])  # type: ignore
        assert output_unit is not None
        if not self.is_compatible_unit(output_unit, self.unit):
            raise NodeError(
                self,
                "Multiplying inputs must in a unit compatible with '%s' (got '%s')" % (self.unit, output_unit)
            )

        node = nodes.pop(0)
        df = outputs.pop(0)
        m = node.get_default_output_metric()
        df = df.rename({m.column_id: '_Left'})
        for n, ndf in zip(nodes, outputs):
            m = n.get_default_output_metric()
            ndf = ndf.rename({m.column_id: '_Right'})
            df = df.paths.join_over_index(ndf, how='left', index_from='union')
            df = df.multiply_cols(['_Left', '_Right'], '_Left').drop('_Right')

        df = df.rename({'_Left': VALUE_COLUMN})
        df = df.drop_nulls(VALUE_COLUMN)
        df = df.ensure_unit(VALUE_COLUMN, self.unit)
        return df

    def compute(self) -> ppl.PathsDataFrame:
        additive_nodes: list[Node] = []
        operation_nodes: list[Node] = []
        assert self.unit is not None
        non_additive_nodes = self.get_input_nodes(tag='non_additive')
        for node in self.input_nodes:
            if node.unit is None:
                raise NodeError(self, "Input node %s does not have a unit" % str(node))
            if node in non_additive_nodes:
                operation_nodes.append(node)
            elif self.is_compatible_unit(node.unit, self.unit):
                additive_nodes.append(node)
            else:
                operation_nodes.append(node)

        if len(operation_nodes) < 2:
            raise NodeError(self, "Must receive at least two inputs to operate %s on" % self.operation_label)

        outputs = [n.get_output_pl(target_node=self) for n in operation_nodes]

        if self.debug:
            for idx, (n, df) in enumerate(zip(operation_nodes, outputs)):
                print('%s: %s input from node %d (%s):' % (self.operation_label, self.id, idx, n.id))

        if self.get_parameter_value('only_historical', required=False):
            outputs = [df.filter(~pl.col(FORECAST_COLUMN)) for df in outputs]

        df = self.perform_operation(operation_nodes, outputs)

        if self.get_parameter_value('extend_rows', required=False):
            df = extend_last_historical_value_pl(df, self.get_end_year())

        df = self.add_nodes_pl(df, additive_nodes)
        fill_gaps = self.get_parameter_value('fill_gaps_using_input_dataset', required=False)
        if fill_gaps:
            df = self.fill_gaps_using_input_dataset_pl(df)
        replace_output = self.get_parameter_value('replace_output_using_input_dataset', required=False)
        if replace_output:
            df = self.replace_output_using_input_dataset_pl(df)
        if self.debug:
            print('%s: Output:' % self.id)
            self.print(df)

        return df


class DivisiveNode(MultiplicativeNode):
    """Divide two nodes together with potentially adding other input nodes.

    Division and addition is determined based on the input node units.
    """

    operation_label = 'division'

    # FIXME The roles of nominator and denumerator are determined based on the node appearance, not explicitly.
    def perform_operation(self, nodes: list[Node], outputs: list[ppl.PathsDataFrame]) -> ppl.PathsDataFrame:
        for n in nodes:
            assert n.unit is not None
        assert self.unit is not None

        output_unit = functools.reduce(lambda x, y: x / y, [n.unit for n in nodes])  # type: ignore
        assert output_unit is not None
        if not self.is_compatible_unit(output_unit, self.unit):
            raise NodeError(
                self,
                "Divising inputs must in a unit compatible with '%s' (got '%s')" % (self.unit, output_unit)
            )

        node = nodes.pop(0)
        df = outputs.pop(0)
        m = node.get_default_output_metric()
        df = df.rename({m.column_id: '_Left'})
        for n, ndf in zip(nodes, outputs):
            m = n.get_default_output_metric()
            ndf = ndf.rename({m.column_id: '_Right'})
            df = df.paths.join_over_index(ndf, how='left', index_from='union')
            df = df.divide_cols(['_Left', '_Right'], '_Left').drop('_Right')

        df = df.rename({'_Left': VALUE_COLUMN})
        df = df.drop_nulls(VALUE_COLUMN)
        df = df.ensure_unit(VALUE_COLUMN, self.unit)
        return df


class EmissionFactorActivity(MultiplicativeNode):
    """Multiply an activity by an emission factor."""
    quantity = 'emissions'
    default_unit = '%s/a' % EMISSION_UNIT
    allowed_parameters = MultiplicativeNode.allowed_parameters + [
        BoolParameter(local_id='convert_missing_values_to_zero')
    ]

    def compute(self) -> ppl.PathsDataFrame:
        convert = self.get_parameter_value('convert_missing_values_to_zero', required=False)
        df = super().compute()
        if convert:
            df = df.with_columns(pl.col(VALUE_COLUMN).fill_nan(pl.lit(0)))
            df = df.with_columns(pl.col(VALUE_COLUMN).fill_null(pl.lit(0)))
        return df


class PerCapitaActivity(MultiplicativeNode):
    pass


class Activity(AdditiveNode):
    """Add activity amounts together."""
    pass


class FixedMultiplierNode(SimpleNode):  # FIXME Merge functionalities with MultiplicativeNode
    allowed_parameters = [
        NumberParameter(local_id='multiplier'),
        StringParameter(local_id='global_multiplier'),
    ] + SimpleNode.allowed_parameters

    def compute(self) -> ppl.PathsDataFrame:
        if len(self.input_nodes) != 1:
            raise NodeError(self, 'FixedMultiplier needs exactly one input node')

        node = self.input_nodes[0]

        df = node.get_output_pl()
        multiplier_param = self.get_parameter('multiplier')
        multiplier = multiplier_param.get()
        if multiplier_param.has_unit():
            m_unit = multiplier_param.get_unit()
        else:
            m_unit = self.context.unit_registry.parse_units('dimensionless')

        meta = df.get_meta()
        exprs = [pl.col(col) * multiplier for col in meta.metric_cols]
        units = {col: meta.units[col] * m_unit for col in meta.metric_cols}
        df = df.with_columns(exprs, units=units)
        for metric in self.output_metrics.values():
            df = df.ensure_unit(metric.column_id, metric.unit)

        replace_output = self.get_parameter_value('replace_output_using_input_dataset', required=False)
        if replace_output:
            df = self.replace_output_using_input_dataset_pl(df)
        return df


class YearlyPercentageChangeNode(SimpleNode):
    allowed_parameters = [
        NumberParameter(local_id='yearly_change', unit_str='%'),
    ] + SimpleNode.allowed_parameters

    def compute(self):
        df = self.get_input_dataset()
        if len(self.input_nodes) != 0:
            raise NodeError(self, "YearlyPercentageChange can't have input nodes")
        df = nafill_all_forecast_years(df, self.get_end_year())
        mult = self.get_parameter_value('yearly_change') / 100 + 1
        df['Multiplier'] = 1
        df.loc[df[FORECAST_COLUMN], 'Multiplier'] = mult
        df['Multiplier'] = df['Multiplier'].cumprod()
        for col in df.columns:
            if col in (FORECAST_COLUMN, 'Multiplier'):
                continue
            dt = df.dtypes[col]
            df[col] = df[col].pint.m.fillna(method='pad').astype(dt)
            df.loc[df[FORECAST_COLUMN], col] *= df['Multiplier']

        replace_output = self.get_parameter_value('replace_output_using_input_dataset', required=False)
        if replace_output:
            df = self.replace_output_using_input_dataset(df)

        df = df.drop(columns=['Multiplier'])

        return df


class CurrentTrendNode(MultiplicativeNode):  # FIXME Exploratory, not necessarily needed
    """Continue the situation in node1 based on the trend in node2.
    """

    operation_label = 'current_trend'

    def perform_operation(self, n1: Node, n2: Node, df1: ppl.PathsDataFrame, df2: ppl.PathsDataFrame) -> ppl.PathsDataFrame:
        assert n1.unit is not None and self.unit is not None
        output_unit = n1.unit
        if not self.is_compatible_unit(output_unit, self.unit):
            raise NodeError(
                self,
                "The input must in a unit compatible with '%s' (%s [%s])" % (self.unit, n1.id, n1.unit))

        df = df1.paths.join_over_index(df2, how='left')
        df = df.divide_cols([VALUE_COLUMN, VALUE_COLUMN + '_right'], VALUE_COLUMN)
        df = df.ensure_unit(VALUE_COLUMN, self.unit).drop([VALUE_COLUMN + '_right'])

        return df


class MixNode(AdditiveNode):
    output_metrics = {
        MIX_QUANTITY: NodeMetric(unit='%', quantity=MIX_QUANTITY)
    }
    default_unit = '%'

    def add_mix_normalized(self, df: ppl.PathsDataFrame, nodes: list[Node], over_dims: list[str] | None = None):
        df = self.add_nodes_pl(df=df, nodes=nodes)
        if len(df.metric_cols) != 1:
            raise NodeError(self, "Must have exactly one metric column")

        if over_dims is None:
            over_dims = df.dim_ids
        col = df.metric_cols[0]
        df = (df
            .ensure_unit(col, 'dimensionless')
            .with_columns(pl.col(col).clip(0, 1))
        )
        sdf = df.paths.sum_over_dims(over_dims).rename({col: '_YearSum'})
        df = df.paths.join_over_index(sdf)
        df = df.divide_cols([col, '_YearSum'], col).drop('_YearSum')

        df = extend_last_historical_value_pl(df, self.get_end_year())
        m = self.get_default_output_metric()
        df = df.ensure_unit(m.column_id, m.unit)
        return df

    def compute(self):
        anode = self.get_input_node(tag='activity')
        adf = anode.get_output_pl(target_node=self)
        am = anode.get_default_output_metric()
        adf = adf.paths.calculate_shares(am.column_id, '_Share')
        m = self.get_default_output_metric()
        df = adf.select_metrics(['_Share']).ensure_unit('_Share', m.unit).rename({'_Share': m.column_id})
        df = extend_last_historical_value_pl(df, self.get_end_year())
        nodes = list(self.input_nodes)
        nodes.remove(anode)
        return self.add_mix_normalized(df, nodes)


class AdditiveRelativeNode(SimpleNode):
    """Simple addition of inputs with a possiblity to have a relative change for the output"""
    allowed_parameters: ClassVar[list[Parameter]] = [
        StringParameter(local_id='metric', is_customizable=False),
    ] + SimpleNode.allowed_parameters

    def add_nodes(self, ndf: pd.DataFrame | None, nodes: List[Node], metric: str | None = None) -> pd.DataFrame:
        if ndf is not None:
            df = ppl.from_pandas(ndf)
        else:
            df = None
        out = self.add_nodes_pl(df, nodes, metric)
        return out.to_pandas()

    def compute(self):
        additive_nodes: list[Node] = []
        relative_nodes: list[Node] = []
        assert self.unit is not None

        for node in self.input_nodes:
            if node.unit is None:
                raise NodeError(self, "Input node %s does not have a unit" % str(node))
            if node.quantity == 'fraction':
                relative_nodes.append(node)
            else:
                additive_nodes.append(node)

        df = self.get_input_dataset_pl(required=False)
        metric = self.get_parameter_value('metric', required=False)
        assert self.unit is not None
        if df is not None:
            if VALUE_COLUMN not in df.columns:
                if len(df.metric_cols) == 1:
                    df = df.rename({df.metric_cols[0]: VALUE_COLUMN})
                elif metric is not None:
                    if metric in df.columns:
                        df = df.rename({metric: VALUE_COLUMN})
                        cols = [YEAR_COLUMN, *df.dim_ids, VALUE_COLUMN]
                        if FORECAST_COLUMN in df.columns:
                            cols.append(FORECAST_COLUMN)
                        df = df.select(cols)
                    else:
                        raise NodeError(self, "Metric is not found in metric columns")
                else:
                    compatible_cols = [
                        col for col, unit in df.get_meta().units.items()
                        if self.is_compatible_unit(unit, self.unit)
                    ]
                    if len(compatible_cols) == 1:
                        df = df.rename({compatible_cols[0]: VALUE_COLUMN})
                        cols = [YEAR_COLUMN, *df.dim_ids, VALUE_COLUMN]
                        if FORECAST_COLUMN in df.columns:
                            cols.append(FORECAST_COLUMN)
                        df = df.select(cols)
                    else:
                        raise NodeError(self, "Input dataset has multiple metric columns, but no Value column")
            df = df.ensure_unit(VALUE_COLUMN, self.unit)
            df = extend_last_historical_value_pl(df, self.get_end_year())

        if self.get_parameter_value('fill_gaps_using_input_dataset', required=False):
            df = self.add_nodes_pl(None, additive_nodes, metric)
            df = self.fill_gaps_using_input_dataset_pl(df)
        else:
            df = self.add_nodes_pl(df, additive_nodes, metric)

        factors = [n.get_output_pl(target_node=self) for n in relative_nodes]
        # to be continued

        return df


class MultiplicativeRelativeNode(MultiplicativeNode):
    """Multiply nodes together with potentially adding other input nodes.

    Multiplication and addition is determined based on the input node units.
    Finally, the result is scaled relative to dimsionless input nodes.
    """

    allowed_parameters = SimpleNode.allowed_parameters + [
        BoolParameter(
            local_id='only_historical',
            description='Process only historical rows',
            is_customizable=False,
        ),
        BoolParameter(
            local_id='extend_rows',
            description='Extend last row to future years',
            is_customizable=False,
        )
    ]
    operation_label = 'multiplication'

    def compute_old(self):
        all_nodes = self.get_input_nodes()
        input_nodes: list[Node] = []
        relative_nodes: list[Node] = []

        for node in all_nodes:
            if node.quantity == 'fraction':
                relative_nodes.append(node)
            else:
                input_nodes.append(node)
        print(all_nodes, input_nodes, relative_nodes)
        stripped_node = self
#        stripped_node.input_nodes = input_nodes

        df = super(MultiplicativeRelativeNode, stripped_node).compute()

        return df

    # Fork the whole compute() function because I don't know how to super() it with relative_nodes stripped.
    def compute(self) -> ppl.PathsDataFrame:
        additive_nodes: list[Node] = []
        operation_nodes: list[Node] = []
        relative_nodes: list[Node] = []
        assert self.unit is not None
        non_additive_nodes = self.get_input_nodes(tag='non_additive')
        for node in self.input_nodes:
            if node.unit is None:
                raise NodeError(self, "Input node %s does not have a unit" % str(node))
            if node in non_additive_nodes:
                operation_nodes.append(node)
            elif self.is_compatible_unit(node.unit, self.unit):
                additive_nodes.append(node)
            elif node.quantity == 'fraction':
                relative_nodes.append(node)
            else:
                operation_nodes.append(node)

        if len(operation_nodes) < 2:
            raise NodeError(self, "Must receive at least two inputs to operate %s on" % self.operation_label)

        outputs = [n.get_output_pl(target_node=self) for n in operation_nodes]

        if self.debug:
            for idx, (n, df) in enumerate(zip(operation_nodes, outputs)):
                print('%s: %s input from node %d (%s):' % (self.operation_label, self.id, idx, n.id))

        if self.get_parameter_value('only_historical', required=False):
            outputs = [df.filter(~pl.col(FORECAST_COLUMN)) for df in outputs]

        df = self.perform_operation(operation_nodes, outputs)

        if self.get_parameter_value('extend_rows', required=False):
            df = extend_last_historical_value_pl(df, self.get_end_year())

        df = self.add_nodes_pl(df, additive_nodes)
        fill_gaps = self.get_parameter_value('fill_gaps_using_input_dataset', required=False)
        if fill_gaps:
            df = self.fill_gaps_using_input_dataset_pl(df)
        replace_output = self.get_parameter_value('replace_output_using_input_dataset', required=False)
        if replace_output:
            df = self.replace_output_using_input_dataset_pl(df)
        if self.debug:
            print('%s: Output:' % self.id)
            self.print(df)

        factors = [n.get_output_pl(target_node=self) for n in relative_nodes]
        # to be continued

        return df


class UsFloorAreaNode(MultiplicativeNode):
    '''
    Floor area splits into 1+2+4 categories based on building energy class:
    # all: all floor area
    ## floor_old: existing floor area built by the last historical year
    ### renovated: floor area that is triggered to renovation (increases yearly)
    ### regular: the remaining existing floor area (stays constant in future years)
    ## floor_new: floor area that is built after the last historical year (cumulative)
    ### compliant: share of new floor area that follows the stricter energy efficiency
    ### non_compliant: share that does not follow the stricter energy efficiency
    '''
    output_dimension_ids = ['building_energy_class', 'action', 'emission_sectors']  # FIXME Generalise and remove emission_sectors

    def include_customer_dimension(self, df: ppl.PathsDataFrame):  # Dimension must be explained in column name in the right syntax
        df = df.paths.to_wide()  # Make column names consistent
        for s in df.columns:
            arr = s.split('@')
            if len(arr) > 2:
                s2 = '@'.join(arr[:2]) + '/' + ''.join(arr[2:])
                df = df.rename({s: s2})
        df = df.paths.to_narrow()

        return df
    
    def compute(self):
        nodes: list(Node) = []
        actions: list(UsBuildingAction) = []
        for node in self.get_input_nodes():
            if isinstance(node, UsBuildingAction):
                actions += [node]
            else:
                nodes += [node]
        if len(nodes) > 0:
            df: ppl.PathsDataFrame = nodes.pop(0).get_output_pl(target_node=self)
            for node in nodes:
                df = df.paths.join_over_index(node.get_output_pl(target_node=self))
                df = df.multiply_cols([VALUE_COLUMN, VALUE_COLUMN + '_right'], VALUE_COLUMN)
                df = df.drop(VALUE_COLUMN + '_right')
        else:
            df = self.get_input_dataset_pl(required=True)
            df = extend_last_historical_value_pl(df, self.get_end_year())
            df = df.with_columns(pl.lit(False).alias(FORECAST_COLUMN))
            df = df.rename({'floor_area': VALUE_COLUMN})

        # Existing (old) and new floor area in baseline
        flhv = df.get_last_historical_values()
        flhv = flhv.rename({flhv.metric_cols[0]: 'floor_old'})
        df_bau = df.paths.join_over_index(flhv.drop([YEAR_COLUMN, FORECAST_COLUMN]))
        df_bau = df_bau.with_columns(
            pl.when(pl.col(FORECAST_COLUMN)).then(pl.col('floor_old'))
            .otherwise(pl.col(VALUE_COLUMN)).alias('floor_old')
            )
        df_bau = df_bau.with_columns((pl.col(VALUE_COLUMN) - pl.col('floor_old')).alias('floor_new'))
        df_bau = df_bau.set_unit('floor_new', df_bau.get_unit('floor_old'))

        col = 'floor_area@building_energy_class:'
        df_out = df_bau.rename({'floor_old': col + 'regular/action:none'})
        df_out = df_out.rename({VALUE_COLUMN: col + 'all/action:none'})
        df_out = df_out.drop('floor_new')
        df_out = self.include_customer_dimension(df_out)

        for action in actions:
            df_a = action.get_output_pl(target_node=self)
            df_a = df_a.rename({'compliant': 'f_compliant'})
            df_a = df_a.ensure_unit('triggered', 'dimensionless')
            df_a = df_a.ensure_unit('f_compliant', 'dimensionless')

            df = df_bau.paths.join_over_index(df_a)
            df = df.with_columns(pl.col('triggered').fill_null(pl.lit(0)))
            df = df.with_columns(pl.col('f_compliant').fill_null(pl.lit(0)))

            # Old floor area: regular floor area stays, renovation adds floor area
            df = df.multiply_cols(['floor_old', 'triggered'], 'renovated')
            df = df.cumulate('renovated')
            df = df.rename({'renovated': col + 'renovated/action:' + action.id})

            # New floor area
            df = df.multiply_cols(['floor_new', 'f_compliant'], 'compliant')
            df = df.with_columns((pl.col('floor_new') - pl.col('compliant')).alias('non_compliant'))
            df = df.set_unit('non_compliant', df.get_unit('floor_new'))
            df = df.rename({'compliant': col + 'compliant/action:' + action.id})
            df = df.rename({'non_compliant': col + 'non_compliant/action:' + action.id})
            df = df.drop(['triggered', 'f_compliant', 'floor_old', 'floor_new', VALUE_COLUMN])

            df = self.include_customer_dimension(df)
            meta = df.get_meta()
            df_out = pl.concat([df_out, df], rechunk=True)
            df_out = ppl.to_ppdf(df_out, meta)

        df_out = df_out.ensure_unit('floor_area', self.unit)
        df_out = df_out.with_columns(pl.col('floor_area').alias(VALUE_COLUMN))
        df_out = df_out.with_columns(
            pl.when(pl.col('building_energy_class').eq('all'))
            .then(pl.col(VALUE_COLUMN)).otherwise(pl.lit(0.0)).alias(VALUE_COLUMN))
        return df_out


class UsEuiNode(UsFloorAreaNode):
    '''
    Consumption fraction has 2 + 3 * i combined categories for action * building_energy class:
    # none * all: the BAU CF
    # none * regular: the difference between BAU CF and CF for regular old buildings (0 by definition)
    # action_i * renovated: the difference between BAU CF and CF of action_i
    # action_i * compliant: same as action_i * renovated
    # action_i * non_compliant: same as none * regular
    '''
    output_dimension_ids = ['building_energy_class', 'action', 'emission_sectors']

    def compute(self):
        nodes: list(Node) = []
        actions: list(UsBuildingAction) = []
        for node in self.get_input_nodes():
            if isinstance(node, UsBuildingAction):
                actions += [node]
            else:
                nodes += [node]

        df = self.get_input_dataset_pl(required=True)
        df = extend_last_historical_value_pl(df, self.get_end_year())
        df = df.rename({VALUE_COLUMN: 'bau'})
        df = df.with_columns(pl.lit(0.0).alias('regular'))
        df = df.set_unit('regular', df.get_unit('bau'))

        for action in actions:
            df_a = action.get_output_pl(target_node=self)
            df_a = df_a.rename({VALUE_COLUMN: 'improvement'})
            df_a = df_a.ensure_unit('improvement', 'dimensionless')
            df_a = df_a.with_columns((-pl.col('improvement')).alias('improvement'))

            df = df.paths.join_over_index(df_a)
            df = df.with_columns(pl.col('improvement').fill_null(pl.lit(0)))
            df = df.multiply_cols(['improvement', 'bau'], 'improvement')

            col = 'consumption_factor@action:' + action.id + '/building_energy_class:'
            df = df.with_columns(pl.col('improvement').alias(col + 'compliant'))
            df = df.with_columns(pl.col('regular').alias(col + 'non_compliant'))
            df = df.rename({'improvement': col + 'renovated'})

        col = 'consumption_factor@action:none/building_energy_class:'
        df = df.rename({'regular': col + 'regular'})
        df = df.rename({'bau': col + 'all'})

        df = self.include_customer_dimension(df)

        df = df.rename({'consumption_factor': VALUE_COLUMN})
        df = df.ensure_unit(VALUE_COLUMN, self.unit)
        return df


class UsEnergyNode(MultiplicativeNode):
    '''
    Takes the floor area and consumption factor categorized by building energy class and action.
    The consumption factors are differences to the baseline, except the combination 
    building energy class: all * none has the baseline values. This gives the total without any actions,
    and each action has some energy-reducing impact on this.

    Then the change in floor area is calculated, and this is multiplied by the difference in consumption
    factor compared with the regular building energy class.
    Finally, this energy change is accumulated over time to reflect the situation that
    the energy use of a building stays constant after renovation.
    '''
    output_dimension_ids = ['building_energy_class', 'emission_sectors']

    def compute(self):
        floor = self.get_input_node(tag='floor_area')
        floor = floor.get_output_pl(target_node=self)
        floor = floor.with_columns(pl.col(VALUE_COLUMN).alias('diff'))
        floor = floor.diff('diff')
        floor = floor.with_columns(pl.col('diff').fill_null(0))

        cf = self.get_input_node(tag='consumption_factor')
        cf = cf.get_output_pl(target_node=self)

        df = floor.paths.join_over_index(cf, how='left')
        df = df.multiply_cols(['diff', VALUE_COLUMN + '_right'], 'diff')
        df = df.cumulate('diff')
        df = df.multiply_cols([VALUE_COLUMN, VALUE_COLUMN + '_right'], VALUE_COLUMN)
        df = df.with_columns([
            pl.when(pl.col('building_energy_class').eq('all'))
            .then(pl.col(VALUE_COLUMN)).otherwise(pl.col('diff')).alias(VALUE_COLUMN)
            ])

        df = df.drop([VALUE_COLUMN + '_right', 'diff'])
        cols = list(set(df.columns) - {VALUE_COLUMN, 'floor_area', 'action'})
        meta = df.get_meta()
        df = df.groupby(cols).sum().drop('action')
        df = ppl.to_ppdf(df, meta=meta)

        df = df.ensure_unit(VALUE_COLUMN, self.unit)
        return df


class MultiplyLastBuildingNode(MultiplicativeNode):  # FIXME Tailored class for one purpose only. Generalize!
    """First add other input nodes, then multiply the output.

    Multiplication and addition is determined based on the input node units.
    """

    operation_label = 'multiplication'

    def compute(self) -> ppl.PathsDataFrame:
        additive_nodes: list[Node] = []
        operation_nodes: list[Node] = []
        assert self.unit is not None
        non_additive_nodes = self.get_input_nodes(tag='non_additive')
        for node in self.input_nodes:
            if node in non_additive_nodes:
                operation_nodes.append(node)
            elif self.is_compatible_unit(node.unit, self.unit):
                additive_nodes.append(node)
            else:
                operation_nodes.append(node)

        df = self.get_input_dataset_pl(required=False)
        if df is not None:
            df = extend_last_historical_value_pl(df, self.get_end_year())
        outputs = [n.get_output_pl(metric='improvement', target_node=self) for n in operation_nodes]

        df = self.add_nodes_pl(df, additive_nodes)

        col = VALUE_COLUMN + '_right'
        df = df.paths.join_over_index(outputs.pop(0))
        df = df.with_columns(pl.col(col).fill_null(pl.lit(0)))
        df = df.ensure_unit(col, 'dimensionless')
        df = df.with_columns((pl.col(VALUE_COLUMN) * (1 - pl.col(col))).alias(VALUE_COLUMN))
        df = df.drop(col)
        df = df.ensure_unit(VALUE_COLUMN, self.unit)

        return df


class MultiplyLastNode(MultiplicativeNode):  # FIXME Tailored class for a bit wider use. Generalize!
    """First add other input nodes, then multiply the output.

    Multiplication and addition is determined based on the input node units.
    """

    operation_label = 'multiplication'

    def compute(self) -> ppl.PathsDataFrame:
        additive_nodes: list[Node] = []
        operation_nodes: list[Node] = []
        assert self.unit is not None
        non_additive_nodes = self.get_input_nodes(tag='non_additive')
        for node in self.input_nodes:
            if node in non_additive_nodes:
                operation_nodes.append(node)
            elif self.is_compatible_unit(node.unit, self.unit):
                additive_nodes.append(node)
            else:
                operation_nodes.append(node)

        df = self.get_input_dataset_pl(required=False)
        if df is not None:
            df = extend_last_historical_value_pl(df, self.get_end_year())
        df = self.add_nodes_pl(df, additive_nodes)

        outputs = [n.get_output_pl() for n in operation_nodes]
        assert len(operation_nodes) == 1  # FIXME Multiplication should be generalised to several operation nodes.

        col = VALUE_COLUMN + '_right'
        df = df.paths.join_over_index(outputs.pop(0))
        df = df.ensure_unit(col, 'dimensionless')
        df = df.with_columns([
            pl.col(col).fill_null(pl.lit(0)),
            (1 - pl.col(col)).alias('ratio')
            ])
        df = df.multiply_cols([VALUE_COLUMN, 'ratio'], VALUE_COLUMN).drop([col, 'ratio'])
        df = df.ensure_unit(VALUE_COLUMN, self.unit)

        return df


class MultiplyLastNode2(MultiplicativeNode):  # FIXME Tailored class for a bit wider use. Generalize!
    """First add other input nodes, then multiply the output.

    Multiplication and addition is determined based on the input node units.
    """

    operation_label = 'multiplication'

    def perform_operation(self, nodes: list[Node], outputs: list[ppl.PathsDataFrame]) -> ppl.PathsDataFrame:
        for n in nodes:
            assert n.unit is not None
        assert self.unit is not None

        output_unit = functools.reduce(lambda x, y: x * y, [n.unit for n in nodes])  # type: ignore
        assert output_unit is not None
#        if not self.is_compatible_unit(output_unit, self.unit):
#            raise NodeError(
#                self,
#                "Multiplying inputs must in a unit compatible with '%s' (got '%s')" % (self.unit, output_unit)
#            )

        node = nodes.pop(0)
        df = outputs.pop(0)
        m = node.get_default_output_metric()
        df = df.rename({m.column_id: '_Left'})
        for n, ndf in zip(nodes, outputs):
            m = n.get_default_output_metric()
            ndf = ndf.rename({m.column_id: '_Right'})
            df = df.paths.join_over_index(ndf, how='left', index_from='union')
            df = df.multiply_cols(['_Left', '_Right'], '_Left').drop('_Right')

        df = df.rename({'_Left': VALUE_COLUMN})
        df = df.drop_nulls(VALUE_COLUMN)
#        df = df.ensure_unit(VALUE_COLUMN, self.unit)
        return df

    def compute(self) -> ppl.PathsDataFrame:
        additive_nodes: list[Node] = []
        operation_nodes: list[Node] = []
        assert self.unit is not None
        non_additive_nodes = self.get_input_nodes(tag='non_additive')
        for node in self.input_nodes:
            if node in non_additive_nodes:
                operation_nodes.append(node)
            elif self.is_compatible_unit(node.unit, self.unit):
                additive_nodes.append(node)
            else:
                operation_nodes.append(node)

        df_add = self.get_input_dataset_pl(required=False)
        if df_add is not None:
            df_add = extend_last_historical_value_pl(df_add, self.get_end_year())
        df_add = self.add_nodes_pl(df_add, additive_nodes)

        df_mult = [n.get_output_pl() for n in operation_nodes]
        df_mult = self.perform_operation(operation_nodes, df_mult)
        df = df_add.paths.join_over_index(df_mult, how='outer', index_from='union')
        df = df.multiply_cols([VALUE_COLUMN, VALUE_COLUMN + '_right'], VALUE_COLUMN)
        df = df.drop(VALUE_COLUMN + '_right')
 
        return df


class ImprovementNode(MultiplicativeNode):
    '''First does what MultiplicativeNode does, then calculates 1 - result.
    Can only be used for dimensionless content (i.e., fractions and percentages)
    '''

    def compute(self):
        if len(self.input_nodes) == 1:
            node = self.input_nodes[0]
            df = node.get_output_pl(target_node=self)
        else:
            df = super().compute()
        if not isinstance(df, ppl.PathsDataFrame):
            df = ppl.from_pandas(df)
        df = df.ensure_unit(VALUE_COLUMN, 'dimensionless')
        df = df.with_columns((pl.lit(1) - pl.col(VALUE_COLUMN)).alias(VALUE_COLUMN))

        return df


class ImprovementNode2(MultiplicativeNode):
    '''First does what MultiplicativeNode does, then calculates 1 + result.
    Can only be used for dimensionless content (i.e., fractions and percentages)
    '''

    def compute(self):
        if len(self.input_nodes) == 1:
            node = self.input_nodes[0]
            df = node.get_output_pl(target_node=self)
        else:
            df = super().compute()
        if not isinstance(df, ppl.PathsDataFrame):
            df = ppl.from_pandas(df)
        df = df.ensure_unit(VALUE_COLUMN, 'dimensionless')
        df = df.with_columns((pl.lit(1) + pl.col(VALUE_COLUMN)).alias(VALUE_COLUMN))

        return df
