from enum import Enum
from functools import reduce
from random import choices
from typing import Dict

from pix_framework.statistics.distribution import DurationDistribution

from prosimos.exceptions import InvalidEventAttributeException


class EVENT_ATTR_TYPE(Enum):
    DISCRETE = "discrete"
    CONTINUOUS = "continuous"


def parse_discrete_value(value_info_arr):
    prob_arr = []
    options_arr = []
    for item in value_info_arr:
        options_arr.append(item["key"])
        prob_arr.append(float(item["value"]))

    return {
        "options": options_arr,
        "probabilities": prob_arr
    }


def parse_continuous_value(value_info) -> "DurationDistribution":
    return DurationDistribution.from_dict(value_info)


class EventAttribute:
    def __init__(self, event_id, name, event_attr_type, value):
        self.event_id: str = event_id
        self.name: str = name
        self.event_attr_type: EVENT_ATTR_TYPE = EVENT_ATTR_TYPE(event_attr_type)

        if self.event_attr_type == EVENT_ATTR_TYPE.DISCRETE:
            self.value = parse_discrete_value(value)
        elif self.event_attr_type == EVENT_ATTR_TYPE.CONTINUOUS:
            self.value = parse_continuous_value(value)
        else:
            raise Exception(f"Not supported event attribute {type}")

        self.validate()

    def get_next_value(self):
        if self.event_attr_type == EVENT_ATTR_TYPE.DISCRETE:
            one_choice_arr = choices(self.value["options"], self.value["probabilities"])
            return one_choice_arr[0]
        else:
            return self.value.generate_one_value_with_boundaries()

    def validate(self):
        if self.event_attr_type == EVENT_ATTR_TYPE.DISCRETE:
            actual_sum_probabilities = reduce(lambda acc, item: acc + item, self.value["probabilities"], 0)

            if actual_sum_probabilities != 1:
                raise InvalidEventAttributeException(
                    f"Event attribute ${self.name}: probabilities' sum should be equal to 1")

        return True


class AllEventAttributes:
    def __init__(self, event_attr_arr: Dict[str, Dict[str, EventAttribute]]):
        self.attributes = event_attr_arr

    def get_columns_generated(self):
        return list({attr.name for event_id in self.attributes for attr in self.attributes[event_id].values()})

    def get_values_calculated(self):
        return {attr.name: attr.get_next_value() for event_id in self.attributes for attr in self.attributes[event_id].values()}

    def validate(self, case_attributes):
        case_attribute_names = [attr.name for attr in case_attributes.attributes]
        event_attribute_names = [attr.name for event_id in self.attributes for attr in self.attributes[event_id].values()]

        for attr_name in event_attribute_names:
            if attr_name in case_attribute_names:
                raise ValueError(f"Event attribute: {attr_name} already defined in case attributes")
        
        return True
