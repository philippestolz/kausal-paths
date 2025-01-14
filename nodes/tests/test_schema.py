import pytest
from django.utils.translation import get_language
from nodes.tests.factories import ActionNodeFactory, NodeConfigFactory, NodeFactory
from nodes.metric import Metric

pytestmark = pytest.mark.django_db


def test_instance_type(graphql_client_query_data, instance, instance_config):
    data = graphql_client_query_data(
        '''
        query {
          instance {
            __typename
            id
            name
            targetYear
            modelEndYear
            referenceYear
            minimumHistoricalYear
            maximumHistoricalYear
            leadTitle
            leadParagraph
          }
        }
        '''
    )
    expected = {
        'instance': {
            '__typename': 'InstanceType',
            'id': instance.id,
            'name': instance.name,
            'targetYear': instance.context.target_year,
            'modelEndYear': instance.context.model_end_year,
            'referenceYear': instance.reference_year,
            'minimumHistoricalYear': instance.minimum_historical_year,
            'maximumHistoricalYear': instance.maximum_historical_year,
            'leadTitle': instance_config.lead_title,
            'leadParagraph': instance_config.lead_paragraph,
        }
    }
    assert data == expected


def test_forecast_metric_type(graphql_client_query_data, additive_action, context, baseline_scenario):
    context.generate_baseline_values()
    metric = Metric.from_node(additive_action)
    data = graphql_client_query_data(
        '''
        query($id: ID!) {
          node(id: $id) {
            metric {
              __typename
              id
              name
              outputNode {
                __typename
                id
              }
              unit {
                __typename
                short
              }
              historicalValues {
                __typename
                year
                value
              }
              forecastValues {
                __typename
                year
                value
              }
              baselineForecastValues {
                __typename
                year
                value
              }
            }
          }
        }
        ''',
        variables={'id': additive_action.id}
    )
    expected_historical_values = [{
        '__typename': 'YearlyValue',
        'year': yearly_value.year,
        'value': yearly_value.value,
    } for yearly_value in metric.get_historical_values()]
    expected_forecast_values = [{
        '__typename': 'YearlyValue',
        'year': yearly_value.year,
        'value': yearly_value.value,
    } for yearly_value in metric.get_forecast_values()]
    expected_baseline_forecast_values = [{
        '__typename': 'YearlyValue',
        'year': yearly_value.year,
        'value': yearly_value.value,
    } for yearly_value in metric.get_baseline_forecast_values()]
    expected = {
        'node': {
            'metric': {
                '__typename': 'ForecastMetricType',
                'id': metric.id,
                'name': metric.name,
                'outputNode': None,  # TODO
                'unit': {
                    '__typename': 'UnitType',
                    'short': metric.unit.format_babel('~P', locale=get_language()),
                },
                'historicalValues': expected_historical_values,
                'forecastValues': expected_forecast_values,
                'baselineForecastValues': expected_baseline_forecast_values,
            }
        }
    }
    assert data == expected


def test_node_type(graphql_client_query_data, additive_action, instance_config):
    node_config = NodeConfigFactory(instance=instance_config, identifier=additive_action.id)
    input_node = NodeFactory()
    additive_action.add_input_node(input_node)
    output_node = NodeFactory()
    additive_action.add_output_node(output_node)
    upstream_action = ActionNodeFactory()
    input_node.add_input_node(upstream_action)
    data = graphql_client_query_data(
        '''
        query($id: ID!) {
          node(id: $id) {
            __typename
            id
            name
            color
            unit {
              __typename
            }
            quantity
            goals {
              __typename
              year
              value
            }
            isAction
            decisionLevel
            inputNodes {
              __typename
              id
            }
            outputNodes {
              __typename
              id
            }
            downstreamNodes {
              __typename
              id
            }
            upstreamNodes {
              __typename
              id
            }
            upstreamActions {
              __typename
              id
            }
            metric {
              __typename
              id
            }
            impactMetric {
              __typename
              id
            }
            parameters {
              __typename
              id
            }
            shortDescription
            description
          }
        }
        ''',
        variables={'id': additive_action.id}
    )
    expected = {
        'node': {
            '__typename': 'NodeType',
            'id': additive_action.id,
            'name': str(additive_action.name),
            'color': additive_action.color,
            'unit': {
                '__typename': 'UnitType'
            },
            'quantity': additive_action.quantity,
            'goals': [{
                '__typename': 'NodeGoal',
                'year': g.year,
                'value': g.value,
            } for g in additive_action.goals.get_dimensionless().values],
            'isAction': True,
            'decisionLevel': additive_action.decision_level.name,
            'inputNodes': [{
                '__typename': 'NodeType',
                'id': input_node.id,
            }],
            'outputNodes': [{
                '__typename': 'NodeType',
                'id': output_node.id,
            }],
            'downstreamNodes': [{
                '__typename': 'NodeType',
                'id': output_node.id,
            }],
            'upstreamNodes': [{
                '__typename': 'NodeType',
                'id': input_node.id,
            }, {
                '__typename': 'NodeType',
                'id': upstream_action.id,
            }],
            'upstreamActions': [{
                '__typename': 'NodeType',
                'id': upstream_action.id,
            }],
            'metric': {
                '__typename': 'ForecastMetricType',
                'id': additive_action.id,
            },
            'impactMetric': {
                '__typename': 'ForecastMetricType',
                'id': f'{additive_action.id}-{additive_action.id}-impact',
            },
            'parameters': [{
                '__typename': 'BoolParameterType',
                'id': additive_action.enabled_param.global_id,
            }],
            'shortDescription': node_config.short_description,
            'description': str(additive_action.description),
        }
    }
    assert data == expected


def test_scenario_type(graphql_client_query_data, context, scenario):
    is_active = scenario == context.active_scenario
    data = graphql_client_query_data(
        '''
        query($id: ID!) {
          scenario(id: $id) {
            __typename
            id
            name
            isActive
            isDefault
          }
        }
        ''',
        variables={'id': scenario.id}
    )
    expected = {
        'scenario': {
            '__typename': 'ScenarioType',
            'id': scenario.id,
            'name': str(scenario.name),
            'isActive': is_active,
            'isDefault': scenario.default,
        }
    }
    assert data == expected
