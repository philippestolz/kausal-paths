from typing import Optional
import logging
import dataclasses

import graphene
from graphql import GraphQLResolveInfo
import graphql
from graphql.error import GraphQLError
from numpy import require
from wagtail.rich_text import expand_db_html

import polars as pl

from common import polars as ppl
from nodes.context import Context
from nodes.goals import NodeGoalsEntry
from nodes.normalization import Normalization
from paths.graphql_helpers import (
    GQLInfo, GQLInstanceInfo, ensure_instance, pass_context
)

from . import Node
from .actions import ActionEfficiencyPair, ActionGroup, ActionNode
from .constants import (
    FORECAST_COLUMN, IMPACT_COLUMN, IMPACT_GROUP, YEAR_COLUMN, DecisionLevel
)
from .instance import Instance, InstanceFeatures
from .metric import DimensionalFlow, DimensionalMetric, Metric
from .models import InstanceConfig, NodeConfig
from .scenario import Scenario


logger = logging.getLogger(__name__)


class InstanceHostname(graphene.ObjectType):
    hostname = graphene.String()
    base_path = graphene.String()


class ActionGroupType(graphene.ObjectType):
    id = graphene.ID(required=True)
    name = graphene.String(required=True)
    color = graphene.String(required=False)
    actions = graphene.List(graphene.NonNull('nodes.schema.NodeType'), required=True)

    @staticmethod
    def resolve_actions(root: ActionGroup, info: GQLInstanceInfo):
        context = info.context.instance.context
        return [act for act in context.get_actions() if act.group == root]


def create_from_dataclass(kls):
    fields = dataclasses.fields(kls)
    gfields = {}
    for field in fields:
        if field.type == bool:
            gf = graphene.Boolean
        elif field.type == int:
            gf = graphene.Int
        else:
            raise Exception("Unsupported type: %s" % field.type)
        gfields[field.name] = gf(required=True)
    out = type(kls.__name__ + 'Type', (graphene.ObjectType,), gfields)
    return out


InstanceFeaturesType = create_from_dataclass(InstanceFeatures)


class InstanceYearlyGoalType(graphene.ObjectType):
    year = graphene.Int(required=True)
    goal = graphene.Float(required=False)
    actual = graphene.Float(required=False)
    is_interpolated = graphene.Boolean(required=False)
    is_forecast = graphene.Boolean(required=True)


class InstanceGoalDimension(graphene.ObjectType):
    dimension = graphene.String(required=True)
    categories = graphene.List(graphene.NonNull(graphene.String), required=True)
    category = graphene.String(required=True, deprecation_reason='replaced with categories')

    @staticmethod
    def resolve_category(root, info):
        return root.categories[0]  # type: ignore


class InstanceGoalEntry(graphene.ObjectType):
    id = graphene.ID(required=True)
    label = graphene.String(required=False)
    outcome_node: 'Node' = graphene.Field('nodes.schema.NodeType', required=True)  # type: ignore
    dimensions = graphene.List(graphene.NonNull(InstanceGoalDimension), required=True)
    default = graphene.Boolean(required=True)
    values = graphene.List(graphene.NonNull(InstanceYearlyGoalType), required=True)
    unit = graphene.Field('paths.schema.UnitType', required=True)

    _goal: NodeGoalsEntry

    def resolve_values(self, info):
        return self._goal.get_actual()

    def resolve_unit(self, info):
        df = self._goal._get_values_df()
        return df.get_unit(self.outcome_node.get_default_output_metric().column_id)


class InstanceType(graphene.ObjectType):
    id = graphene.ID(required=True)
    name = graphene.String(required=True)
    owner = graphene.String()
    default_language = graphene.String(required=True)
    supported_languages = graphene.List(graphene.NonNull(graphene.String), required=True)
    base_path = graphene.String(required=True)
    target_year = graphene.Int()
    model_end_year = graphene.Int(required=True)
    reference_year = graphene.Int()
    minimum_historical_year = graphene.Int(required=True)
    maximum_historical_year = graphene.Int()

    hostname = graphene.Field(InstanceHostname, hostname=graphene.String())
    lead_title = graphene.String()
    lead_paragraph = graphene.String()
    theme_identifier = graphene.String()
    action_groups = graphene.List(graphene.NonNull(ActionGroupType), required=True)
    features = graphene.Field(InstanceFeaturesType, required=True)
    goals = graphene.List(graphene.NonNull(InstanceGoalEntry), id=graphene.ID(required=False), required=True)

    @staticmethod
    def resolve_lead_title(root, info):
        obj = InstanceConfig.objects.filter(identifier=root.id).first()
        if obj is None:
            return None
        return obj.lead_title_i18n  # type: ignore

    @staticmethod
    def resolve_lead_paragraph(root, info):
        obj = InstanceConfig.objects.filter(identifier=root.id).first()
        if obj is None:
            return None
        return obj.lead_paragraph_i18n  # type: ignore

    @staticmethod
    def resolve_hostname(root, info, hostname):
        return InstanceConfig.objects.get(identifier=root.id)\
            .hostnames.filter(hostname__iexact=hostname).first()

    @staticmethod
    def resolve_goals(root: Instance, info: GQLInstanceInfo, id: str | None = None):
        ret = []
        for goal in root.get_goals():
            node = goal.get_node()
            goal_id = goal.get_id()
            if id is not None:
                if goal_id != id:
                    continue

            dims = []
            for dim_id, path in goal.dimensions.items():
                dims.append(InstanceGoalDimension(dimension=dim_id, categories=path.groups or path.categories))

            out = InstanceGoalEntry(
                id=goal_id,
                label=str(goal.label) if goal.label else str(node.name),
                outcome_node=node,
                dimensions=dims,
                default=goal.default,
            )
            out._goal = goal
            ret.append(out)
        return ret


class YearlyValue(graphene.ObjectType):
    year = graphene.Int(required=True)
    value = graphene.Float(required=True)


class ForecastMetricType(graphene.ObjectType):
    id = graphene.ID()
    name = graphene.String()
    output_node = graphene.Field(lambda: NodeType, description="Will be set if the node outputs multiple time-series")
    unit = graphene.Field('paths.schema.UnitType')
    yearly_cumulative_unit = graphene.Field('paths.schema.UnitType')
    historical_values = graphene.List(graphene.NonNull(YearlyValue), required=True, latest=graphene.Int(required=False))
    forecast_values = graphene.List(graphene.NonNull(YearlyValue), required=True)
    cumulative_forecast_value = graphene.Float()
    baseline_forecast_values = graphene.List(graphene.NonNull(YearlyValue))

    @staticmethod
    def resolve_historical_values(root: Metric, info, latest: Optional[int] = None):
        ret = root.get_historical_values()
        if latest:
            if latest >= len(ret):
                return ret
            return ret[-latest:]
        return ret

    @staticmethod
    def resolve_forecast_values(root: Metric, info):
        return root.get_forecast_values()

    @staticmethod
    def resolve_baseline_forecast_values(root: Metric, info):
        return root.get_baseline_forecast_values()

    @staticmethod
    def resolve_cumulative_forecast_value(root: Metric, info):
        return root.get_cumulative_forecast_value()


class MetricDimensionCategoryType(graphene.ObjectType):
    id = graphene.ID(required=True)
    original_id = graphene.ID(required=False)
    label = graphene.String(required=True)
    color = graphene.String(required=False)
    order = graphene.Int(required=False)


class MetricDimensionType(graphene.ObjectType):
    id = graphene.ID(required=True)
    original_id = graphene.ID(required=False)
    label = graphene.String(required=True)
    categories = graphene.List(graphene.NonNull(MetricDimensionCategoryType), required=True)


class MetricYearlyGoalType(graphene.ObjectType):
    year = graphene.Int(required=True)
    value = graphene.Float(required=True)
    is_interpolated = graphene.Boolean(required=True)


class DimensionalMetricGoalEntry(graphene.ObjectType):
    categories = graphene.List(graphene.NonNull(graphene.String), required=True)
    values = graphene.List(graphene.NonNull(MetricYearlyGoalType), required=True)


class DimensionalMetricType(graphene.ObjectType):
    id = graphene.ID(required=True)
    name = graphene.String(required=True)
    dimensions = graphene.List(graphene.NonNull(MetricDimensionType), required=True)
    values = graphene.List(graphene.Float, required=True)
    years = graphene.List(graphene.NonNull(graphene.Int), required=True)
    unit = graphene.Field('paths.schema.UnitType', required=True)
    stackable = graphene.Boolean(required=True)
    forecast_from = graphene.Int(required=False)
    goals = graphene.List(graphene.NonNull(DimensionalMetricGoalEntry), required=True)
    normalized_by = graphene.Field('nodes.schema.NodeType', required=False)


ActionDecisionLevel = graphene.Enum.from_enum(DecisionLevel)


class FlowNodeType(graphene.ObjectType):
    id = graphene.String(required=True)
    label = graphene.String(required=True)
    color = graphene.String(required=False)


class FlowLinksType(graphene.ObjectType):
    year = graphene.Int(required=True)
    is_forecast = graphene.Boolean(required=True)
    sources = graphene.List(graphene.NonNull(graphene.String), required=True)
    targets = graphene.List(graphene.NonNull(graphene.String), required=True)
    values = graphene.List(graphene.Float, required=True)
    absolute_source_values = graphene.List(graphene.NonNull(graphene.Float), required=True)


class DimensionalFlowType(graphene.ObjectType):
    id = graphene.String(required=True)
    nodes = graphene.List(graphene.NonNull(FlowNodeType), required=True)
    unit = graphene.Field('paths.schema.UnitType', required=True)
    sources = graphene.List(graphene.NonNull(graphene.String), required=True)
    links = graphene.List(graphene.NonNull(FlowLinksType), required=True)


class NodeGoal(graphene.ObjectType):
    year = graphene.Int(required=True)
    value = graphene.Float(required=True)


class NodeType(graphene.ObjectType):
    id = graphene.ID(required=True)
    name = graphene.String(required=True)
    short_name = graphene.String(required=False)
    color = graphene.String()
    order = graphene.Int(required=False)
    unit = graphene.Field('paths.schema.UnitType')
    quantity = graphene.String()
    target_year_goal = graphene.Float(deprecation_reason='Replaced by "goals".')
    goals = graphene.List(graphene.NonNull(NodeGoal), active_goal=graphene.ID(required=False), required=True)
    is_action = graphene.Boolean(required=True)
    decision_level = graphene.Field(ActionDecisionLevel)
    input_nodes = graphene.List(graphene.NonNull(lambda: NodeType), required=True)
    output_nodes = graphene.List(graphene.NonNull(lambda: NodeType), required=True)
    downstream_nodes = graphene.List(graphene.NonNull(lambda: NodeType), required=True)
    upstream_nodes = graphene.List(
        graphene.NonNull(lambda: NodeType),
        same_unit=graphene.Boolean(),
        same_quantity=graphene.Boolean(),
        include_actions=graphene.Boolean(),
        required=True
    )
    upstream_actions = graphene.List(graphene.NonNull(lambda: NodeType, required=True))
    group = graphene.Field(ActionGroupType, required=False)

    # TODO: Many nodes will output multiple time series. Remove metric
    # and handle a single-metric node as a special case in the UI??
    metric = graphene.Field(ForecastMetricType, goal_id=graphene.ID(required=False))
    outcome = graphene.Field(DimensionalMetricType)

    # If resolving through `descendant_nodes`, `impact_metric` will be
    # by default be calculated from the ancestor node.
    impact_metric = graphene.Field(ForecastMetricType, target_node_id=graphene.ID(required=False), goal_id=graphene.ID(required=False))

    metrics = graphene.List(graphene.NonNull(ForecastMetricType))
    dimensional_flow = graphene.Field(DimensionalFlowType, required=False)
    #metric_dim = graphene.Field(
    #    DimensionalMetricType, include_impact=graphene.Boolean(default=False), impact_action_node=graphene.ID(required=False),
    #    required=False
    #)
    metric_dim = graphene.Field(DimensionalMetricType, required=False)

    # TODO: input_datasets, baseline_values, context
    parameters = graphene.List(graphene.NonNull('params.schema.ParameterInterface'), required=True)

    # These are potentially plucked from nodes.models.NodeConfig
    short_description = graphene.String()
    description = graphene.String()

    @staticmethod
    def resolve_color(root: Node, info):
        if root.color:
            return root.color
        if root.quantity == 'emissions':
            for parent in root.output_nodes:
                if parent.color:
                    root.color = parent.color
                    return root.color

    @staticmethod
    def resolve_is_action(root: Node, info):
        return isinstance(root, ActionNode)

    @staticmethod
    def resolve_downstream_nodes(root: Node, info: GQLInstanceInfo):
        info.context._upstream_node = root  # type: ignore
        return root.get_downstream_nodes()

    @staticmethod
    def resolve_upstream_nodes(
        root: Node, info: GQLInstanceInfo,
        same_unit: bool = False, same_quantity: bool = False,
        include_actions: bool = True
    ):
        def filter_nodes(node):
            if same_unit:
                if root.unit != node.unit:
                    return False
            if same_quantity:
                if root.quantity != node.quantity:
                    return False
            if not include_actions:
                if isinstance(node, ActionNode):
                    return False
            return True
        return root.get_upstream_nodes(filter=filter_nodes)

    @staticmethod
    def resolve_upstream_actions(root: Node, info: GQLInstanceInfo):
        return root.get_upstream_nodes(filter=lambda x: isinstance(x, ActionNode))

    @staticmethod
    def resolve_metric(root: Node, info: GQLInstanceInfo, goal_id: str | None = None):
        return Metric.from_node(root, goal_id=goal_id)

    @staticmethod
    def resolve_dimensional_flow(root: Node, info: GraphQLResolveInfo):
        if not isinstance(root, ActionNode):
            return None
        return DimensionalFlow.from_action_node(root)

    @staticmethod
    def resolve_metric_dim(root: Node, info: GraphQLResolveInfo):
        try: 
            ret = DimensionalMetric.from_node(root)
        except Exception as e:
            logging.exception("Exception while resolving metric_dim for node %s" % root.id)
            return None
        return ret

    @staticmethod
    def resolve_impact_metric(root: Node, info: GQLInstanceInfo, target_node_id: str | None = None, goal_id: str | None = None):
        instance = info.context.instance
        context = instance.context
        upstream_node = getattr(info.context, '_upstream_node', None)

        if goal_id is not None:
            try:
                goal = instance.get_goals(goal_id=goal_id)
            except:
                raise GraphQLError("Goal not found", info.field_nodes)
        else:
            goal = None

        target_node: Node
        if target_node_id is not None:
            if target_node_id not in context.nodes:
                raise GraphQLError("Node %s not found" % target_node_id, info.field_nodes)
            source_node = root
            target_node = context.get_node(target_node_id)
        elif upstream_node is not None:
            source_node = upstream_node
            target_node = root
        elif goal is not None:
            source_node = root
            target_node = goal.get_node()
        else:
            # FIXME: Determine a "default" target node from instance
            outcome_nodes = context.get_outcome_nodes()
            if not len(outcome_nodes):
                raise GraphQLError("No default target node available", info.field_nodes)
            source_node = root
            target_node = outcome_nodes[0]

        if not isinstance(source_node, ActionNode):
            return None

        df: ppl.PathsDataFrame = source_node.compute_impact(target_node)
        if goal is not None:
            df = goal.filter_df(df)

        df = df.filter(pl.col(IMPACT_COLUMN).eq(IMPACT_GROUP)).drop(IMPACT_COLUMN)
        if df.dim_ids:
            # FIXME: Check if can be summed?
            df = df.paths.sum_over_dims()

        try:
            m = target_node.get_default_output_metric()
        except Exception:
            return None

        df = df.select([*df.primary_keys, FORECAST_COLUMN, m.column_id])
        if target_node.context.active_normalization:
            _, df = target_node.context.active_normalization.normalize_output(m, df)

        metric = Metric(
            id='%s-%s-impact' % (source_node.id, target_node.id), name='Impact', df=df,
            unit=df.get_unit(m.column_id)
        )
        return metric

    @staticmethod
    def resolve_group(root: Node, info: GQLInstanceInfo):
        if not isinstance(root, ActionNode):
            return None
        return root.group

    @staticmethod
    def resolve_parameters(root: Node, info):
        return [param for param in root.parameters.values() if param.is_customizable]

    @staticmethod
    def resolve_short_description(root: Node, info: GQLInstanceInfo) -> Optional[str]:
        obj: NodeConfig | None = (
            NodeConfig.objects
            .filter(instance__identifier=info.context.instance.id, identifier=root.id)
            .first()
        )
        if obj is not None and obj.short_description_i18n:  # type: ignore
            return expand_db_html(obj.short_description_i18n)  # type: ignore
        if root.description:
            return '<p>%s</p>' % root.description
        return None

    @staticmethod
    def resolve_description(root: Node, info: GQLInstanceInfo) -> Optional[str]:
        obj = (NodeConfig.objects
               .filter(instance__identifier=info.context.instance.id, identifier=root.id)
               .first())
        if obj is None or not obj.description_i18n:  # type: ignore
            return None
        return expand_db_html(obj.description_i18n)  # type: ignore

    @staticmethod
    def resolve_goals(root: Node, info: GQLInstanceInfo, active_goal: str | None = None):
        if root.goals is None:
            return []
        goal = None
        if active_goal:
            agoal = info.context.instance.get_goals(active_goal)
            if agoal.dimensions:
                # FIXME
                dim_id, cats = list(agoal.dimensions.items())[0]
                goal = root.goals.get_exact_match(
                    dim_id,
                    groups=cats.groups,
                    categories=cats.categories
                )
        if not goal:
            goal = root.goals.get_dimensionless()
        if not goal:
            return []
        return goal.get_values()

    @staticmethod
    def resolve_target_year_goal(root: Node, info: GQLInstanceInfo):
        if root.goals is None:
            return None
        goal = root.goals.get_dimensionless()
        if not goal:
            return None

        target_year = root.context.target_year
        vals = goal.get_values()
        for val in vals:
            if val.year == target_year:
                break
        else:
            return None
        return val.value


class ScenarioType(graphene.ObjectType):
    id = graphene.ID()
    name = graphene.String()
    is_active = graphene.Boolean()
    is_default = graphene.Boolean()

    @staticmethod
    def resolve_is_active(root: Scenario, info: GQLInstanceInfo):
        context = info.context.instance.context
        return context.active_scenario == root

    @staticmethod
    def resolve_is_default(root: Scenario, info: GQLInfo):
        return root.default


class ActionEfficiency(graphene.ObjectType):
    action = graphene.Field(NodeType, required=True)
    cost_values = graphene.List(YearlyValue, required=True)
    impact_values = graphene.List(YearlyValue, required=True)
    efficiency_divisor = graphene.Float()


class ActionEfficiencyPairType(graphene.ObjectType):
    id = graphene.ID(required=True)
    cost_node = graphene.Field(NodeType, required=True)
    impact_node = graphene.Field(NodeType, required=True)
    efficiency_unit = graphene.Field('paths.schema.UnitType', required=True)
    cost_unit = graphene.Field('paths.schema.UnitType', required=True)
    impact_unit = graphene.Field('paths.schema.UnitType', required=True)
    plot_limit_efficiency = graphene.Float()
    invert_cost = graphene.Boolean(required=True)
    invert_impact = graphene.Boolean(required=True)
    label = graphene.String(required=True)
    actions = graphene.List(graphene.NonNull(ActionEfficiency), required=True)

    @staticmethod
    def resolve_id(root: ActionEfficiencyPair, info: GQLInstanceInfo):
        return '%s:%s' % (root.cost_node.id, root.impact_node.id)

    @staticmethod
    def resolve_actions(root: ActionEfficiencyPair, info: GQLInstanceInfo):
        all_aes = root.calculate(info.context.instance.context)
        out = []
        for ae in all_aes:
            years = ae.df[YEAR_COLUMN]
            d = dict(
                action=ae.action,
                cost_values=[YearlyValue(year, float(val)) for year, val in zip(years, list(ae.df['Cost']))],
                impact_values=[YearlyValue(year, float(val)) for year, val in zip(years, list(ae.df['Impact']))],
                efficiency_divisor=ae.efficiency_divisor,
            )
            out.append(d)
        return out

    @staticmethod
    def resolve_efficiency_unit(root: ActionEfficiencyPair, info: GQLInstanceInfo):
        return root.efficiency_unit

    @staticmethod
    def resolve_cost_unit(root: ActionEfficiencyPair, info: GQLInstanceInfo):
        return root.cost_unit

    @staticmethod
    def resolve_impact_unit(root: ActionEfficiencyPair, info: GQLInstanceInfo):
        return root.impact_unit


class InstanceBasicConfiguration(graphene.ObjectType):
    identifier = graphene.String(required=True)
    is_protected = graphene.Boolean(required=True)
    default_language = graphene.String(required=True)
    theme_identifier = graphene.String(required=True)
    supported_languages = graphene.List(graphene.NonNull(graphene.String), required=True)
    hostname = graphene.Field(InstanceHostname, required=True)

    @staticmethod
    def resolve_identifier(root: Instance, info: GQLInfo):
        return root.id

    @staticmethod
    def resolve_is_protected(root: Instance, info: GQLInfo):
        return root._config.is_protected  # type: ignore

    @staticmethod
    def resolve_hostname(root: Instance, info: GQLInfo):
        hostname = root._config.hostnames.filter(hostname=root._hostname.lower()).first()  # type: ignore
        if not hostname:
            return dict(hostname=root._hostname, base_path='')  # type: ignore


class NormalizationType(graphene.ObjectType):
    id = graphene.ID(required=True)
    label = graphene.String(required=True)
    normalizer = graphene.Field(NodeType, required=True)
    is_active = graphene.Boolean(required=True)

    @staticmethod
    def resolve_is_active(root: Normalization, info: GQLInstanceInfo):
        return info.context.instance.context.active_normalization == root

    @staticmethod
    def resolve_label(root: Normalization, info: GQLInstanceInfo):
        return root.normalizer_node.name

    @staticmethod
    def resolve_normalizer(root: Normalization, info: GQLInstanceInfo):
        return root.normalizer_node

    @staticmethod
    def resolve_id(root: Normalization, info: GQLInstanceInfo):
        return root.normalizer_node.id


class Query(graphene.ObjectType):
    available_instances = graphene.List(
        graphene.NonNull(InstanceBasicConfiguration), hostname=graphene.String(), required=True
    )
    instance = graphene.Field(InstanceType, required=True)
    nodes = graphene.List(graphene.NonNull(NodeType), required=True)
    node = graphene.Field(
        NodeType, id=graphene.ID(required=True)
    )
    actions = graphene.List(graphene.NonNull(NodeType), required=True)
    action_efficiency_pairs = graphene.List(graphene.NonNull(ActionEfficiencyPairType), required=True)
    scenarios = graphene.List(graphene.NonNull(ScenarioType), required=True)
    scenario = graphene.Field(ScenarioType, id=graphene.ID(required=True))
    active_scenario = graphene.Field(ScenarioType)
    available_normalizations = graphene.List(graphene.NonNull(NormalizationType), required=True)
    active_normalization = graphene.Field(NormalizationType, required=False)

    @ensure_instance
    def resolve_instance(root, info: GQLInstanceInfo):
        return info.context.instance

    @ensure_instance
    def resolve_scenario(root, info: GQLInstanceInfo, id):
        context = info.context.instance.context
        return context.get_scenario(id)

    @ensure_instance
    def resolve_active_scenario(root, info: GQLInstanceInfo):
        context = info.context.instance.context
        return context.active_scenario

    @ensure_instance
    def resolve_scenarios(root, info: GQLInstanceInfo):
        context = info.context.instance.context
        return list(context.scenarios.values())

    @ensure_instance
    def resolve_node(root, info: GQLInstanceInfo, id: str):
        instance = info.context.instance
        nodes = instance.context.nodes
        if id.isnumeric():
            for node in nodes.values():
                if node.database_id is not None and node.database_id == int(id):
                    return node
            return None

        return instance.context.nodes.get(id)

    @ensure_instance
    def resolve_nodes(root, info: GQLInstanceInfo):
        instance = info.context.instance
        return instance.context.nodes.values()

    @pass_context
    def resolve_actions(root, info: GQLInstanceInfo, context: Context):
        instance = info.context.instance
        return instance.context.get_actions()

    @pass_context
    def resolve_action_efficiency_pairs(root, info: GQLInstanceInfo, context: Context):
        return context.action_efficiency_pairs

    @pass_context
    def resolve_available_normalizations(root, info: GQLInstanceInfo, context: Context):
        return context.normalizations.values()

    @pass_context
    def resolve_active_normalization(root, info: GQLInstanceInfo, context: Context):
        return context.active_normalization

    @staticmethod
    def resolve_available_instances(root, info: GQLInfo, hostname: str):
        qs = InstanceConfig.objects.for_hostname(hostname)
        instances = []
        for config in qs:
            instance = config.get_instance()
            instance._config = config  # type: ignore
            instance._hostname = hostname  # type: ignore
            instances.append(instance)
        return instances

class SetNormalizerMutation(graphene.Mutation):
    class Arguments:
        id = graphene.ID(required=False)

    ok = graphene.Boolean(required=True)
    active_normalization = graphene.Field(NormalizationType, required=False)

    @pass_context
    def mutate(root, info: GQLInstanceInfo, context: Context, id: str | None = None):
        if id:
            normalizer = context.normalizations.get(id)
            if normalizer is None:
                raise GraphQLError("Normalization '%s' not found" % id)

        assert context.setting_storage is not None
        context.setting_storage.set_option('normalizer', id)
        context.set_option('normalizer', id)

        return dict(ok=True, active_normalizer=context.active_normalization)


class Mutations(graphene.ObjectType):
    set_normalizer = SetNormalizerMutation.Field()
