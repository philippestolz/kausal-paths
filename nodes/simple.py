from common.perf import PerfCounter
from nodes.calc import extend_last_historical_value_pl, nafill_all_forecast_years
from params.param import Parameter, BoolParameter, NumberParameter, ParameterWithUnit, StringParameter
from typing import List, ClassVar, Tuple
import polars as pl
import pandas as pd
import pint

from common.i18n import TranslatedString
from common import polars as ppl
from .constants import FORECAST_COLUMN, VALUE_COLUMN, YEAR_COLUMN
from .node import Node
from .exceptions import NodeError


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
            right = '%s_right' % metric_col
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

    def add_nodes_pl(self, df: ppl.PathsDataFrame | None, nodes: List[Node], metric: str | None = None) -> ppl.PathsDataFrame:
        if len(nodes) == 0:
            assert df is not None
            return df
        if self.debug:
            print('%s: input dataset:' % self.id)
            if df is not None:
                print(self.print(df))
            else:
                print('\tNo input dataset')

        node_outputs: List[Tuple[Node, ppl.PathsDataFrame]] = []
        for node in nodes:
            node_df = node.get_output_pl(self, metric=metric)
            node_outputs.append((node, node_df))

        if df is None:
            node, df = node_outputs.pop(0)
            if self.debug:
                print('%s: adding output from node %s' % (self.id, node.id))
                self.print(df)

        cols = df.columns
        if VALUE_COLUMN not in cols:
            raise NodeError(self, "Value column missing in data")
        if FORECAST_COLUMN not in cols:
            raise NodeError(self, "Forecast column missing in data")

        unit = self.unit
        assert unit is not None

        df = df.ensure_unit(VALUE_COLUMN, unit)
        meta = df.get_meta()
        for node, node_df in node_outputs:
            if self.debug:
                print('%s: adding output from node %s' % (self.id, node.id))
                self.print(node_df)

            if VALUE_COLUMN not in node_df.columns:
                raise NodeError(self, "Value column missing in output of %s" % node.id)

            ndf_meta = node_df.get_meta()
            if set(ndf_meta.dim_ids) != set(meta.dim_ids):
                raise NodeError(self, "Dimensions do not match with %s" % node.id)
            df = df.paths.add_with_dims(node_df, how='outer')

        df = df.select([YEAR_COLUMN, *meta.dim_ids, VALUE_COLUMN, FORECAST_COLUMN])
        return df

    def add_nodes(self, ndf: pd.DataFrame | None, nodes: List[Node], metric: str | None = None) -> pd.DataFrame:
        if ndf is not None:
            df = ppl.from_pandas(ndf)
        else:
            df = None
        out = self.add_nodes_pl(df, nodes, metric)
        return out.to_pandas()

    def compute(self):
        df = self.get_input_dataset_pl(required=False)
        assert self.unit is not None
        if df is not None:
            if VALUE_COLUMN not in df.columns:
                if len(df.metric_cols) == 1:
                    df = df.rename({df.metric_cols[0]: VALUE_COLUMN})
                else:
                    raise NodeError(self, "Input dataset has multiple metric columns, but no Value column")
            df = df.ensure_unit(VALUE_COLUMN, self.unit)
            df = extend_last_historical_value_pl(df, self.get_end_year())

        metric = self.get_parameter_value('metric', required=False)
        if self.get_parameter_value('fill_gaps_using_input_dataset', required=False):
            df = self.add_nodes_pl(None, self.input_nodes, metric)
            df = self.fill_gaps_using_input_dataset_pl(df)
        else:
            df = self.add_nodes_pl(df, self.input_nodes, metric)

        return df


class SectorEmissions(AdditiveNode):
    quantity = 'emissions'
    """Simple addition of subsector emissions"""
    pass


class MultiplicativeNode(AdditiveNode):
    """Multiply nodes together with potentially adding other input nodes.

    Multiplication and addition is determined based on the input node units.
    """

    operation_label = 'multiplication'

    def perform_operation(self, n1: Node, n2: Node, df1: ppl.PathsDataFrame, df2: ppl.PathsDataFrame) -> ppl.PathsDataFrame:
        assert n1.unit is not None and n2.unit is not None and self.unit is not None
        output_unit = n1.unit * n2.unit
        if not self.is_compatible_unit(output_unit, self.unit):
            raise NodeError(
                self,
                "Multiplying inputs must in a unit compatible with '%s' (%s [%s] * %s [%s])" % (self.unit, n1.id, n1.unit, n2.id, n2.unit))

        df = df1.paths.join_over_index(df2, how='left', index_from='union')
        df = df.multiply_cols([VALUE_COLUMN, VALUE_COLUMN + '_right'], VALUE_COLUMN)
        df = df.drop_nulls(VALUE_COLUMN)
        df = df.ensure_unit(VALUE_COLUMN, self.unit).drop([VALUE_COLUMN + '_right'])
        return df

    def compute(self) -> ppl.PathsDataFrame:
        additive_nodes: list[Node] = []
        operation_nodes: list[Node] = []
        assert self.unit is not None
        for node in self.input_nodes:
            if node.unit is None:
                raise NodeError(self, "Input node %s does not have a unit" % str(node))
            if self.is_compatible_unit(node.unit, self.unit):
                additive_nodes.append(node)
            else:
                operation_nodes.append(node)

        if len(operation_nodes) != 2:
            raise NodeError(self, "Must receive exactly two inputs to operate %s on" % self.operation_label)

        n1, n2 = operation_nodes
        df1 = n1.get_output_pl(target_node=self)
        df2 = n2.get_output_pl(target_node=self)

        if self.debug:
            print('%s: %s input from node 1 (%s):' % (self.operation_label, self.id, n1.id))
            self.print(df1)
            print('%s: %s input from node 2 (%s):' % (self.operation_label, self.id, n2.id))
            self.print(df2)

        df = self.perform_operation(n1, n2, df1, df2)
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

    def perform_operation(self, n1: Node, n2: Node, df1: ppl.PathsDataFrame, df2: ppl.PathsDataFrame) -> ppl.PathsDataFrame:
        assert n1.unit is not None and n2.unit is not None and self.unit is not None
        output_unit = n1.unit / n2.unit
        if not self.is_compatible_unit(output_unit, self.unit):
            raise NodeError(
                self,
                "Division inputs must in a unit compatible with '%s' (%s [%s] * %s [%s])" % (self.unit, n1.id, n1.unit, n2.id, n2.unit))

        df = df1.paths.join_over_index(df2, how='left')
        df = df.divide_cols([VALUE_COLUMN, VALUE_COLUMN + '_right'], VALUE_COLUMN)
        df = df.ensure_unit(VALUE_COLUMN, self.unit).drop([VALUE_COLUMN + '_right'])

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


class FixedMultiplierNode(SimpleNode):
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
