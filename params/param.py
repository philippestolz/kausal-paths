from __future__ import annotations
from dataclasses import InitVar, dataclass, field

import hashlib
import orjson
import pandas as pd
from pydantic import BaseModel
from common.i18n import I18nString
from typing import Any, Dict, Optional, TYPE_CHECKING, Type
from nodes.datasets import JSONDataset
from nodes.units import Unit, Quantity

if TYPE_CHECKING:
    from nodes import Node, NodeMetric
    from nodes.dimensions import Dimension
    from nodes.scenario import Scenario


class ValidationError(Exception):
    def __init__(self, param: Parameter, msg: str = ''):
        if msg is not None:
            msg_str = ': %s' % msg
        else:
            msg_str = ''
        super().__init__("[Param %s]: Parameter validation failed%s" % (param.local_id, msg_str))


@dataclass
class Parameter:
    local_id: str  # not globally unique but locally, relative to the parameter's node (if it has one)
    label: Optional[I18nString] = None
    description: Optional[I18nString] = None

    node: Optional[Node] = None
    "Set if this parameter is bound to a specific node"

    subscription_nodes: list[Node] = field(default_factory=list)
    "Nodes that should be notified when the parameter changes value"

    subscription_params: list[Parameter] = field(default_factory=list)
    "Parametres that should be notified when the parameter changes value"

    is_customized: bool = False
    is_customizable: bool = True
    # Maps a scenario ID to the value of this parameter in that scenario
    scenario_settings: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        assert '.' not in self.local_id
        self._hash = None

    def notify_change(self):
        self._hash = None
        if self.node:
            self.node.notify_parameter_change(self)
        for node in self.subscription_nodes:
            node.notify_parameter_change(self)
        for param in self.subscription_params:
            param.notify_change()

    def set(self, value: Any):
        prev_val = getattr(self, 'value', None)
        self.value = self.clean(value)
        if not self.is_value_equal(prev_val):
            self.notify_change()

    def reset_to_scenario_setting(self, scenario: Scenario):
        if scenario.id in self.scenario_settings:
            setting = self.scenario_settings[scenario.id]
            self.set(setting)
            self.is_customized = False

    def get(self) -> Any:
        return self.value

    def serialize_value(self) -> Any:
        if isinstance(self.value, BaseModel):
            return self.value.dict()
        return self.value

    def is_value_equal(self, value: Any) -> bool:
        return self.value == value

    def calculate_hash(self) -> bytes:
        h = getattr(self, '_hash', None)
        if h is not None:
            return h
        s = orjson.dumps({'id': self.global_id, 'value': self.serialize_value()})
        h = hashlib.md5(s).digest()
        self._hash = h
        return h

    def clean(self, value: Any) -> Any:
        raise NotImplementedError('Implement in subclass')

    def add_scenario_setting(self, scenario, value):
        """
        Add the given value as the setting for the given scenario.

        `scenario` can be an instance of `Scenario` or a string that is a scenario ID.
        """
        from nodes.scenario import Scenario
        if isinstance(scenario, Scenario):
            scenario_id = scenario.id
        else:
            scenario_id = scenario

        if scenario_id in self.scenario_settings:
            raise Exception(f"Setting for parameter {self.global_id} in scenario {scenario_id} already exists")
        self.scenario_settings[scenario_id] = value

    def get_scenario_setting(self, scenario):
        return self.scenario_settings.get(scenario.id)

    @property
    def global_id(self):
        if self.node is None:
            return self.local_id
        return f'{self.node.id}.{self.local_id}'

    def set_node(self, node: 'Node'):
        if self.node is not None:
            raise Exception(f"Node for parameter {self.global_id} already set")
        self.node = node

    def has_unit(self) -> bool:
        if isinstance(self, ParameterWithUnit) and self.unit is not None:
            return True
        return False

    def get_unit(self) -> Unit:
        if not self.has_unit():
            raise Exception(f"Parameter {self.global_id} does not have units")
        return self.unit  # type: ignore


@dataclass
class ReferenceParameter(Parameter):
    """Parameter that is a reference to another parameter.

    This parameter cannot be changed.
    """

    target: Parameter | None = None
    _target: Parameter = field(init=False)
    is_customizable: bool = False

    def __post_init__(self):
        super().__post_init__()
        assert self.target is not None
        self._target = self.target
        self.target.subscription_params.append(self)

    @property
    def unit(self) -> Any:
        return getattr(self._target, 'unit')

    @property
    def value(self) -> Any:
        return self._target.value

    def has_unit(self) -> bool:
        return self._target.has_unit()

    def get_unit(self) -> Unit:
        return self._target.get_unit()

    def clean(self, value: Any) -> Any:
        raise NotImplementedError()


@dataclass
class ParameterWithUnit:
    unit: Unit | None = None
    unit_str: InitVar[str | None] = None

    def _init_unit(self, unit_str: str | None = None):
        if hasattr(self, 'unit_str'):
            if unit_str is None:
                unit_str = self.unit_str  # type: ignore

        if unit_str is not None:
            from nodes.context import unit_registry
            self.unit = unit_registry.parse_units(unit_str)

        if self.unit is not None:
            if not isinstance(self.unit, Unit):
                raise Exception("str given for unit for parameter %s" % self.local_id)  # type: ignore


@dataclass
class NumberParameter(ParameterWithUnit, Parameter):
    value: Optional[float] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    step: Optional[float] = None

    def __post_init__(self, unit_str: str | None = None):
        self._init_unit(unit_str)
        super().__post_init__()

    def clean(self, value: float | Quantity) -> float:
        # Store unit first if available
        if isinstance(value, Quantity):
            if self.unit is not None:
                assert isinstance(self.unit, Quantity)
                assert self.unit.is_compatible_with(value.units)
            value = value.m

        # Avoid converting, e.g., bool to float
        if not isinstance(value, (int, float, str)):
            raise ValidationError(self)
        try:
            value = float(value)
        except ValueError:
            raise ValidationError(self)
        if self.min_value is not None and value < self.min_value:
            raise ValidationError(self, 'Below min_value')
        if self.max_value is not None and value > self.max_value:
            raise ValidationError(self, 'Above max_value')
        return value

    def set(self, value: float | Quantity):
        if isinstance(value, Quantity):
            unit = value.units
        else:
            unit = None
        super().set(value)
        if unit is not None:
            self.unit = unit


@dataclass
class DatasetParameter(Parameter):
    """Multi-dimensional time-series."""
    dimensions: list[Dimension] = field(default_factory=list)
    metrics: list[NodeMetric] = field(default_factory=list)
    value: pd.DataFrame | None = None

    def __post_init__(self):
        if not self.metrics:
            raise Exception('Must have at least one metric')
        super().__post_init__()

    def is_value_equal(self, value: pd.DataFrame) -> bool:
        assert self.value is not None
        return self.value.equals(value)

    def serialize_value(self) -> Any:
        assert self.value is not None
        return JSONDataset.serialize_df(self.value)

    def clean(self, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValidationError(self, "Must get a dict as value")

        return super().clean(value)


@dataclass
class PercentageParameter(NumberParameter):
    unit_str = '%'


@dataclass
class BoolParameter(Parameter):
    value: Optional[bool] = None

    def clean(self, value: bool):
        # Avoid converting non-bool to bool
        if not isinstance(value, bool):
            raise ValidationError(self)
        return value


@dataclass
class StringParameter(Parameter):
    value: Optional[str] = None

    def clean(self, value: str):
        if not isinstance(value, str):
            raise ValidationError(self)
        return value


param_type_registry: set[Type[Parameter]] = set()


def register_parameter_type(cls: Type[Parameter]):
    if cls in param_type_registry:
        raise Exception("Parameter class %s already registered", str(cls))
    param_type_registry.add(cls)
