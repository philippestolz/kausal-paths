import argparse
from dotenv import load_dotenv
from nodes.actions.action import ActionNode
import sys
from nodes.instance import InstanceLoader
from common.perf import PerfCounter


if True:
    # Print traceback for warnings
    import traceback
    import warnings

    def warn_with_traceback(message, category, filename, lineno, file=None, line=None):
        log = sys.stderr
        traceback.print_stack()
        log.write(warnings.formatwarning(message, category, filename, lineno, line))

    warnings.showwarning = warn_with_traceback

load_dotenv()

loader = InstanceLoader.from_yaml('configs/tampere.yaml')
context = loader.context
context.print_graph()

page = list(loader.instance.pages.values())[0]


def print_metric(metric):
    print(metric)

    print('Historical:')
    vals = metric.get_historical_values(context)
    for val in vals:
        print(val)

    print('\nBaseline forecast:')
    vals = metric.get_baseline_forecast_values(context)
    for val in vals:
        print(val)

    print('\nRoadmap scenario:')
    vals = metric.get_forecast_values(context)
    for val in vals:
        print(val)


parser = argparse.ArgumentParser(description='Execute the computational graph')
parser.add_argument('--baseline', action='store_true', help='generate baseline scenario values')
parser.add_argument('--scenario', type=str, help='select scenario')
parser.add_argument('--param', action='append', type=str, help='set a parameter')
parser.add_argument('--list-params', action='store_true', help='list parameters')
parser.add_argument('--debug', action='store_true', help='enable debug messages and disable cache')
parser.add_argument('--node', type=str, nargs='+', help='compute node')
parser.add_argument('--pull-datasets', action='store_true', help='refresh all datasets')
args = parser.parse_args()

if args.pull_datasets:
    context.pull_datasets()

if args.scenario:
    context.activate_scenario(context.get_scenario(args.scenario))

if args.list_params:
    context.print_params()

if args.debug:
    for node_id in (args.node or []):
        node = context.get_node(node_id)
        node.debug = True

if args.baseline:
    pc = PerfCounter('Baseline')
    pc.display('generating')
    context.generate_baseline_values()
    pc.display('done')

for param_arg in (args.param or []):
    param_id, val = param_arg.split('=')
    context.set_param_value(param_id, val)

for node_id in (args.node or []):
    node = context.get_node(node_id)
    node.print_output()
    if isinstance(node, ActionNode):
        node.print_impact(context.get_node('net_emissions'))


if False:
    loader.context.dataset_repo.pull_datasets()
    loader.context.print_params()
    loader.context.generate_baseline_values()
    #for sector in page.get_sectors():
    #    print(sector)
