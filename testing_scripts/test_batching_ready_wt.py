import pytest
from datetime import datetime
import pandas as pd

from bpdfr_simulation_engine.batching_processing import AndFiringRule, FiringSubRule, OrFiringRule
from bpdfr_simulation_engine.resource_calendar import parse_datetime
from testing_scripts.test_batching import (
    _verify_logs_ordered_asc,
)
from testing_scripts.test_batching_daily_hour import _arrange_and_act_base
from testing_scripts.test_batching import (
    _verify_logs_ordered_asc,
    assets_path,
)

data_one_week_day = [
    # Rule:             ready_wt < 3600 (3600 seconds = 1 hour)
    #                   (it's being parsed to ready_wt > 3598, cause it should be fired last_item_en_time + 3599)
    # Current state:    4 tasks waiting for the batch execution.
    #                   difference between each of them is not > 3598
    # Expected result:  firing rule is enabled at the time we check for enabled time 
    #                   enabled time of the batch equals to the enabled time of the last item in the batch + 3598 
    #                   (value dictated by rule)
    (
        "19/09/22 14:35:26",
        [
            "19/09/22 12:00:26",
            "19/09/22 12:30:26",
            "19/09/22 13:00:26",
            "19/09/22 13:30:26",
        ],
        ">",    # "<"   - in the json file
        3598,   # 3600  - in the json file
        True,
        [4],
        "19/09/2022 14:30:25",
    ),
    # Rule:             ready_wt > 3600 (3600 seconds = 1 hour)
    # Current state:    5 tasks waiting for the batch execution.
    # Expected result:  Firing rule is enabled for the all items and this equals to two batches to be executed.
    #                   Enabled time of the batch equals to the enabled time of the last item in each batch
    #                   (meaning, to the maximum datetime from all enabled batches).
    #                   There is difference between two activities (3d and 4th) which exceeds one hour limit,
    #                   so that's what triggers the first batch to be enabled. 
    #                   Verify that enabled_time of the batch is one second after the datetime forced by the rule.
    (
        "19/09/22 14:05:26",
        [
            "19/09/22 10:00:26",
            "19/09/22 10:00:26",
            "19/09/22 10:30:26",
            "19/09/22 12:00:26",
            "19/09/22 12:30:26",
        ],
        ">",
        3600, # one hour
        True,
        [3, 2],
        "19/09/2022 11:30:27",
    ),
    # Rule:             ready_wt >= 3600 (3600 seconds = 1 hour)
    # Current state:    4 tasks waiting for the batch execution.
    # Expected result:  Firing rule is enabled for the all items and this equals to one batch enabled.
    #                   All activities have difference of less than one hour,
    #                   that's why the rule were not satisfied at that point somewhere.
    #                   Verify that enabled_time of the batch equals exactly to the datetime forced by the rule.
    (
        "19/09/22 14:05:26",
        [
            "19/09/22 11:00:26",
            "19/09/22 11:30:26",
            "19/09/22 12:00:26",
            "19/09/22 12:30:26",
        ],
        ">=",
        3600, # one hour
        True,
        [4],
        "19/09/2022 13:30:26",
    ),
    # Rule:             ready_wt > 3600 (3600 seconds = 1 hour)
    # Current state:    3 tasks waiting for the batch execution.
    # Expected result:  Firing rule is not enabled since no items
    #                   waiting for batch execution satisfies the rule.
    (
        "19/09/22 13:00:26",
        [
            "19/09/22 12:00:26",
            "19/09/22 12:15:26",
            "19/09/22 12:30:26",
        ],
        ">",
        3600, # one hour
        False,
        None,
        None,
    ),
]


@pytest.mark.parametrize(
    "curr_enabled_at_str, enabled_datetimes, sign_ready_wt, ready_wt_value_sec, expected_is_true, expected_batch_size, expected_start_time_from_rule",
    data_one_week_day,
)
def test_ready_wt_rule_correct_is_true(
    curr_enabled_at_str,
    enabled_datetimes,
    sign_ready_wt,
    ready_wt_value_sec,
    expected_is_true,
    expected_batch_size,
    expected_start_time_from_rule,
):

    # ====== ARRANGE ======
    firing_sub_rule_1 = FiringSubRule(
        "ready_wt", sign_ready_wt, ready_wt_value_sec
    )

    firing_rule_1 = AndFiringRule([firing_sub_rule_1])
    rule = OrFiringRule([firing_rule_1])

    curr_enabled_at = datetime.strptime(curr_enabled_at_str, "%d/%m/%y %H:%M:%S")
    enabled_datetimes = [
        datetime.strptime(item, "%d/%m/%y %H:%M:%S") for item in enabled_datetimes
    ]
    waiting_time_arr = [curr_enabled_at - item for item in enabled_datetimes]

    current_exec_status = {
        "size": len(waiting_time_arr),
        "waiting_times": waiting_time_arr,
        "enabled_datetimes": enabled_datetimes,
        "curr_enabled_at": curr_enabled_at,
        "is_triggered_by_batch": False,
    }

    # ====== ACT & ASSERT ======
    (is_true, batch_spec, start_time_from_rule) = rule.is_true(current_exec_status)
    assert expected_is_true == is_true
    assert expected_batch_size == batch_spec

    if expected_start_time_from_rule == None:
        assert expected_start_time_from_rule == start_time_from_rule
    else:
        start_dt = start_time_from_rule.strftime("%d/%m/%Y %H:%M:%S")
        assert expected_start_time_from_rule == start_dt

data_whole_sim_one_batch = [
    # Input:    Firing rule: ready_wt > 18000 sec (5 hours)
    # Expected: Waiting for collecting all activities where ready_wt is < 5 hours.
    #           Enablind the batch the moment when we surpass the limitation of 5 hours.
    #           This equals to the last enabled time + 5 hours 1 sec.
    (
        ">",
        18000,   # 5 hours  - in the json file
        [
            ("2022-09-30 19:49:31.035185+03:00", 6)
        ],
        "assets_path"
    ),
    # Input:    Firing rule: ready_wt < 18000 sec (5 hours)
    # Expected: Waiting for collecting all activities where ready_wt is < 5 hours.
    #           Enablind the batch the moment when we are about to surpass the limitation of 5 hours.
    #           This equals to the last enabled time + 04:59:59 
    (
        "<",
        18000,   # 5 hours  - in the json file
        [
            ("2022-09-30 19:49:29.035185+03:00", 6)
        ],
        "assets_path"
    ),
]

@pytest.mark.parametrize('execution_number', range(5))
def test_2(execution_number, assets_path):
    """
    Input:      6 process cases are being generated. A new case arrive every 3 hours.
                Batched task are executed in parallel.
    Expected:   Batched task are executed only when the difference between newly arrived 
                and the previous one exceeds the range of 5 hours.
                Since we generate 6 new cases with the arrival case of 3 hours,
                the batch will not get executed during the generation of those cases.
                Batch of 6 activities will be enabled after 5 hours of the last arrived activity
                (the one which supposed to be in the batch).
    Verified:   The start_time of the appropriate grouped D task.
                The number of tasks in every executed batch.
                The resource which executed the batch is the same for all tasks in the batch.
                The start_time of all logs files is being sorted by ASC.
    """

    # ====== ARRANGE & ACT ======
    firing_rules = [
        [
            {"attribute": "ready_wt", "comparison": "<", "value": 7200}, # 2 hours
        ]
    ]

    sim_logs = assets_path / "batch_logs.csv"

    start_string = "2022-09-29 23:45:30.035185+03:00"
    start_date = parse_datetime(start_string, True)

    total_num_cases = 10
    _arrange_and_act_exp(assets_path, firing_rules, start_date, total_num_cases)

    # ====== ASSERT ======
    df = pd.read_csv(sim_logs)
    df["enable_time"] = pd.to_datetime(df["start_time"], errors="coerce")
    logs_d_task = df[df["activity"] == "D"]
    grouped_by_start_and_resource = logs_d_task.groupby(by=["start_time", "resource"])

    prev_row_value = None
    total_count_activities = 0

    # verify time distance between tasks inside the batch is less
    # than the one specified in the rule (2 hours)
    for _, group in grouped_by_start_and_resource:
        for index, row in group.iterrows():
            total_count_activities += 1
            if prev_row_value == None:
                prev_row_value = row["enable_time"]
                continue
        
            diff = (row["enable_time"] - prev_row_value).seconds
            assert (
                diff < 7200,
            ), f"The diff between two rows {index} and {index-1} does not follow the rule. \
                    Expected 7200 sec, but was {diff}"

            prev_row_value = row["enable_time"]

    assert total_count_activities == total_num_cases, \
        f"Total number of batched activites should be equal to total num of generated use cases. \
            Expected {total_num_cases}, but was {total_count_activities}"

    # verify that distance between pair of batch
    # is greater that the one specified in the rule (2 hours)
    first_last_enable_times = pd \
        .concat([
            grouped_by_start_and_resource.head(1),
            grouped_by_start_and_resource.tail(1)
        ]) \
        .reset_index(drop=True)

    prev_row_enable_time = None
    for index, item in first_last_enable_times.iterrows():
        # verify that enabled and start time are not equal
        # since we should wait at least two hours
        assert ( 
            item["enable_time"] != item["start_time"]
        ), f"The enable_time and start_time should not be equal (row {index+2})."

        if index in [0, 1]:
            prev_row_enable_time = item["enable_time"]
            continue

        diff = (item["enable_time"] - prev_row_enable_time).seconds
        
        assert (
            diff > 7200,
        ), f"The diff between two rows {index+2} and {index+1} does not follow the rule. \
                Expected greater than 7200 sec, but was {diff}"
        
        prev_row_enable_time = item["enable_time"]

    # verify that column 'start_time' is ordered ascendingly
    _verify_logs_ordered_asc(df, start_date.tzinfo)


def _arrange_and_act_exp(assets_path, firing_rules, start_date, num_cases):
    arrival_distr = {
        "distribution_name": "expon",
        "distribution_params": [
            { "value": 0 },
            { "value": 7200.0 },
            { "value": 0.0 },
            { "value": 100000.0 },
        ]
    }

    _arrange_and_act_base(assets_path, firing_rules, start_date, num_cases, arrival_distr)
