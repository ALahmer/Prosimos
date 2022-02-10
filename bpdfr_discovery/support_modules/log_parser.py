import json
import math
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
import pytz
from pm4py.objects.log.importer.xes import importer as xes_importer

from bpdfr_simulation_engine.execution_info import ProcessInfo, Trace
from bpdfr_simulation_engine.probability_distributions import best_fit_distribution

import ntpath

from bpdfr_simulation_engine.resource_calendar import RCalendar, update_calendar_from_log, update_weekly_calendar, \
    CalendarFactory
from bpdfr_simulation_engine.simulation_properties_parser import parse_simulation_model

date_format = "%Y-%m-%dT%H:%M:%S.%f%z"


# def parse_xes_log_1(log_path, bpmn_graph):
#
#     print(1)
#     tree = ET.parse(log_path)
#     root = tree.getroot()
#     ns = {'xes': root.tag.split('}')[0].strip('{')}
#     tags = dict(trace='xes:trace',
#                 string='xes:string',
#                 event='xes:event',
#                 date='xes:date')
#     traces = root.findall(tags['trace'], ns)
#
#     return _extract_log_info(bpmn_graph, traces, tags, ns)


def preprocess_xes_log(log_path, bpmn_path, out_f_path, minutes_x_granule, min_confidence, min_support,
                       min_participation):
    model_name = ntpath.basename(bpmn_path).split('.')[0]
    print('Parsing Event Log %s ...' % model_name)
    bpmn_graph = parse_simulation_model(bpmn_path)

    f_name = ntpath.basename(log_path).split('.')[0]
    print('Parsing Event Log %s ...' % f_name)

    log_traces = xes_importer.apply(log_path)

    calendar_factory = CalendarFactory(minutes_x_granule)
    completed_events = list()
    total_traces = 0

    resource_cases = dict()
    resource_freq = dict()
    max_resource_freq = 0
    task_resource_freq = dict()
    task_resource_events = dict()
    initial_events = dict()
    flow_arcs_frequency = dict()

    for trace in log_traces:
        caseid = trace.attributes['concept:name']
        total_traces += 1
        started_events = dict()
        trace_info = Trace(caseid)
        initial_events[caseid] = datetime(9999, 12, 31, tzinfo=pytz.UTC)
        task_sequence = list()
        for event in trace:
            task_name = event['concept:name']
            resource = event['org:resource']
            state = event['lifecycle:transition'].lower()
            timestamp = event['time:timestamp']

            initial_events[caseid] = min(initial_events[caseid], timestamp)

            if resource not in resource_freq:
                resource_cases[resource] = set()
                resource_freq[resource] = 0
            resource_cases[resource].add(caseid)
            resource_freq[resource] += 1

            max_resource_freq = max(max_resource_freq, resource_freq[resource])

            if task_name not in task_resource_freq:
                task_resource_events[task_name] = dict()
                task_resource_freq[task_name] = [0, dict()]
            if resource not in task_resource_freq[task_name][1]:
                task_resource_freq[task_name][1][resource] = 0
                task_resource_events[task_name][resource] = list()
            task_resource_freq[task_name][1][resource] += 1
            task_resource_freq[task_name][0] = max(task_resource_freq[task_name][0],
                                                   task_resource_freq[task_name][1][resource])

            calendar_factory.check_date_time(resource, task_name, timestamp)
            if state in ["start", "assign"]:
                started_events[task_name] = trace_info.start_event(task_name, task_name, timestamp, resource)
            elif state == "complete":
                if task_name in started_events:
                    task_sequence.append(task_name)
                    c_event = trace_info.complete_event(started_events.pop(task_name), timestamp)
                    task_resource_events[task_name][resource].append(c_event)
                    completed_events.append(c_event)

        is_correct, fired_tasks, pending_tokens, _ = bpmn_graph.reply_trace(task_sequence,
                                                                            flow_arcs_frequency,
                                                                            False,
                                                                            trace_info.event_list)
    #     for i in range(0, len(enabling_times)):
    #         total_enablement += 1
    #         if trace_info.event_list[i].started_at < enabling_times[i]:
    #             wrong_enablement += 1
    #             if fix_enablement_from_incorrect_models(i, enabling_times, trace_info.event_list) \
    #                     and not trace[i].started_at < enabling_times[i]:
    #                 fixed_enablement += 1
    #         trace_info.event_list[i].enabled_at = enabling_times[i]
    #     calendar_factory.register_task_enablement(trace_info.event_list)
    #
    #
    # print("Correct Enablement Ratio: %.2f" % ((total_enablement - wrong_enablement) / total_enablement))
    # print("Fixed   Enablement Ratio: %.2f" % ((total_enablement - wrong_enablement + fixed_enablement) / total_enablement))

    resource_freq_ratio = dict()
    for r_name in resource_freq:
        resource_freq_ratio[r_name] = resource_freq[r_name] / max_resource_freq

    # # (1) Discovering Resource Calendars
    # # resource_calendars = calendar_factory.build_weekly_calendars(min_confidence, min_support)
    # # removed_resources = print_initial_resource_calendar_info(resource_calendars, resource_freq, max_resource_freq)
    res_calendars, task_resources, joint_resource_events, pools_json, coverage_map = \
        discover_resource_calendars(calendar_factory, task_resource_events, min_confidence, min_support,
                                    min_participation)
    print_joint_resource_calendar_info(res_calendars,
                                       calendar_factory.kpi_calendar,
                                       task_resources,
                                       task_resource_events,
                                       joint_resource_events,
                                       coverage_map)

    res_json_calendar = dict()
    for r_id in res_calendars:
        res_json_calendar[r_id] = res_calendars[r_id].to_json()

    # # (2) Discovering Arrival Time Calendar
    arrival_calendar = discover_arrival_calendar(initial_events, minutes_x_granule, min_confidence, min_support)
    json_arrival_calendar = arrival_calendar.to_json()


    # # (3) Discovering Arrival Time Distribution
    arrival_time_dist = discover_arrival_time_distribution(initial_events, arrival_calendar)


    # # (4) Discovering Task Duration Distributions per resource
    task_resource_dist = discover_resource_task_duration_distribution(task_resource_events, res_calendars,
                                                                      task_resources, joint_resource_events)

    # # (5) Discovering Gateways Branching Probabilities
    gateways_branching = bpmn_graph.compute_branching_probability(flow_arcs_frequency)

    to_save = {
        "resource_profiles": pools_json,
        "arrival_time_distribution": arrival_time_dist,
        "arrival_time_calendar": json_arrival_calendar,
        "gateway_branching_probabilities": gateways_branching,
        "task_resource_distribution": task_resource_dist,
        "resource_calendars": res_json_calendar,
    }
    with open(out_f_path, 'w') as file_writter:
        json.dump(to_save, file_writter)


def fix_enablement_from_incorrect_models(from_i: int, task_enablement: list, trace: list):
    started_at = trace[from_i].started_at
    enabled_at = task_enablement[from_i]
    i = from_i
    while i > 0:
        i -= 1
        if enabled_at == trace[i].completed_at:
            task_enablement[from_i] = started_at
            return True
    return False


def discover_resource_calendars(calendar_factory, task_resource_events, min_confidence, min_support, min_participation):
    calendar_candidates = calendar_factory.build_weekly_calendars(min_confidence, min_support, min_participation)

    joint_event_candidates = dict()
    joint_task_resources = dict()
    pools_json = dict()

    task_event_freq = dict()
    task_event_covered_freq = dict()
    joint_resource_freq = dict()
    coverage_map = dict()

    for task_name in task_resource_events:
        unfit_resource_events = list()
        joint_task_resources[task_name] = list()

        task_event_freq[task_name] = 0
        task_event_covered_freq[task_name] = 0

        for resource_name in task_resource_events[task_name]:
            joint_task_resources[task_name].append(resource_name)
            if calendar_candidates[resource_name] is None or calendar_candidates[resource_name].total_weekly_work == 0:
                unfit_resource_events += task_resource_events[task_name][resource_name]
            else:
                task_event_covered_freq[task_name] += (2 * len(task_resource_events[task_name][resource_name]))
            task_event_freq[task_name] += (2 * len(task_resource_events[task_name][resource_name]))

        if len(unfit_resource_events) > 0:
            joint_events = _max_disjoint_intervals(unfit_resource_events)
            for i in range(0, len(joint_events)):
                j_name = f'Joint_{task_name}_{i}'
                joint_resource_freq[j_name] = 2 * len(joint_events[i])
                joint_event_candidates[j_name] = joint_events[i]
                joint_task_resources[task_name].append(j_name)
                for ev_info in joint_events[i]:
                    calendar_factory.check_date_time(j_name, task_name, ev_info.started_at, True)
                    calendar_factory.check_date_time(j_name, task_name, ev_info.completed_at, True)

    calendar_candidates = calendar_factory.build_weekly_calendars(min_confidence, min_support, min_participation)

    resource_calendars = dict()
    task_resources = dict()
    joint_resource_events = dict()

    discarded_joint = dict()
    for task_name in joint_task_resources:
        discarded_joint[task_name] = list()
        pools_json[task_name] = {
            "name": task_name,
            "resource_list": list()
        }
        resource_list = list()
        task_resources[task_name] = list()
        for resource_name in joint_task_resources[task_name]:
            if calendar_candidates[resource_name] is not None and calendar_candidates[
                resource_name].total_weekly_work > 0:
                resource_list.append(_create_resource_profile_entry(resource_name, resource_name))
                resource_calendars[resource_name] = calendar_candidates[resource_name]
                task_resources[task_name].append(resource_name)
                if resource_name in joint_event_candidates:
                    task_event_covered_freq[task_name] += joint_resource_freq[resource_name]
                    joint_resource_events[resource_name] = joint_event_candidates[resource_name]
            elif resource_name in joint_event_candidates:
                discarded_joint[task_name].append([resource_name, joint_resource_freq[resource_name]])

        if calendar_factory.task_coverage(task_name) < min_support:
            discarded_joint[task_name].sort(key=lambda x: x[1], reverse=True)
            for d_info in discarded_joint[task_name]:
                resource_calendars[d_info[0]] = calendar_factory.build_unrestricted_resource_calendar(d_info[0],
                                                                                                      task_name)
                task_event_covered_freq[task_name] += joint_resource_freq[d_info[0]]
                resource_list.append(_create_resource_profile_entry(d_info[0], d_info[0]))
                task_resources[task_name].append(d_info[0])
                joint_resource_events[d_info[0]] = joint_event_candidates[d_info[0]]
                if calendar_factory.task_coverage(task_name) >= min_support:
                    break

        coverage_map[task_name] = task_event_covered_freq[task_name] / task_event_freq[task_name]
        # print("Coverage Task %s: %.2f" % (task_name, calendar_factory.task_coverage(task_name)))
        pools_json[task_name]["resource_list"] = resource_list

    return resource_calendars, task_resources, joint_resource_events, pools_json, coverage_map


def _max_disjoint_intervals(interval_list):
    if len(interval_list) == 1:
        return [interval_list]
    interval_list.sort(key=lambda ev_info: ev_info.completed_at)
    disjoint_intervals = list()
    while True:
        max_set = list()
        discarded_list = list()
        max_set.append(interval_list[0])
        current_last = interval_list[0].completed_at
        for i in range(1, len(interval_list)):
            if interval_list[i].started_at >= current_last:
                max_set.append(interval_list[i])
                current_last = interval_list[i].completed_at
            else:
                discarded_list.append(interval_list[i])
        if len(max_set) > 1:
            disjoint_intervals.append(max_set)
        if len(max_set) == 1 or len(discarded_list) == 0:
            break
        interval_list = discarded_list
    return disjoint_intervals


def _create_resource_profile_entry(r_id, r_name, amount=1, cost_per_hour=1):
    return {
        "id": r_id,
        "name": r_name,
        "cost_per_hour": cost_per_hour,
        "amount": amount
    }


def build_default_calendar(r_name):
    r_calendar = RCalendar("%s_Default" % r_name)
    r_calendar.add_calendar_item('MONDAY', 'SUNDAY', '00:00:00.000''', '23:59:59.999')
    return r_calendar


def discover_arrival_calendar(initial_events, minutes_x_granule, min_confidence, min_support):
    arrival_calendar_factory = CalendarFactory(minutes_x_granule)
    for case_id in initial_events:
        arrival_calendar_factory.check_date_time('arrival', 'arrival', initial_events[case_id])
    arrival_calendar = arrival_calendar_factory.build_weekly_calendars(min_confidence, min_support, 0.9)
    # Printing Calendar Info (Testing) -----------------------------------
    kpi_calendar = arrival_calendar_factory.kpi_calendar
    t_name = 'arrival'
    print("Coverage: %.2f" % (kpi_calendar.task_coverage('arrival')))
    print("In Timetable: %d events, Discarded: %d events"
          % (kpi_calendar.task_events_in_calendar[t_name],
             kpi_calendar.task_events_count[t_name] - kpi_calendar.task_events_in_calendar[t_name]))
    confidence, support = kpi_calendar.compute_confidence_support(t_name)
    participation_ratio = kpi_calendar.resource_participation_ratio(t_name)
    task_participation = kpi_calendar.resource_task_participation_ratio(t_name, t_name)
    print("    %s -> (%d events), Confidence: %.2f, Support: %.2f, "
          "Task Part.: %.2f, Proc. Part.: %.2f"
          % (t_name, kpi_calendar.resource_task_freq[t_name][t_name],
             confidence, support, task_participation, participation_ratio))
    for c_id in arrival_calendar:
        arrival_calendar[c_id].print_calendar_info()
    # End Testing --------------------------------------------------------------------
    return arrival_calendar['arrival']


def discover_arrival_time_distribution(initial_events, arrival_calendar):
    arrival = list()
    for case_id in initial_events:
        is_working, interval_info = arrival_calendar.is_working_datetime(initial_events[case_id])
        if is_working:
            arrival.append(interval_info)
    arrival.sort(key=lambda x: x.date_time)
    durations = list()
    for i in range(1, len(arrival)):
        durations.append(
            arrival[i].to_start_dist - arrival[i - 1].to_start_dist if arrival[i].in_same_interval(arrival[i - 1])
            else arrival[i].to_end_dist + arrival[i - 1].to_start_dist)
    print("In Calendar Event Ratio: %.2f" % (len(arrival) / len(initial_events)))
    print('---------------------------------------------------')
    return best_fit_distribution(durations)


def discover_resource_task_duration_distribution(task_resource_events, res_calendars, task_resources, joint_events):
    task_resource_distribution = dict()

    for t_id in task_resources:
        print("Task ID: %s" % t_id)
        if t_id not in task_resource_distribution:
            task_resource_distribution[t_id] = dict()
        full_task_durations = list()
        pending_resources = list()
        for r_id in task_resources[t_id]:
            event_list = list()
            if res_calendars[r_id].total_weekly_work > 0 and r_id in task_resource_events[t_id]:
                event_list = task_resource_events[t_id][r_id]
            elif r_id in joint_events:
                event_list = joint_events[r_id]
            durations = list()
            for ev_info in event_list:
                durations.append((ev_info.completed_at - ev_info.started_at).total_seconds())
            full_task_durations += durations
            if len(durations) < 10:
                pending_resources.append(r_id)
            else:
                task_resource_distribution[t_id][r_id] = best_fit_distribution(durations)
                print("Resource: %s, Total Events: %d, Distribution: %s"
                      % (r_id, len(durations), str(task_resource_distribution[t_id][r_id])))

        agregated_distribution = best_fit_distribution(full_task_durations)
        for r_id in pending_resources:
            task_resource_distribution[t_id][r_id] = agregated_distribution
            print("Resource: %s, Total Events: %d, Aggregated Distribution: %s"
                  % (r_id, len(full_task_durations), str(task_resource_distribution[t_id][r_id])))
        print('---------------------------------------------------')
    return task_resource_distribution


def print_initial_resource_calendar_info(resource_calendars, resource_freq, max_resource_freq):
    removed_resources = set()
    print("Resources to Remove ...")
    for r_name in resource_calendars:
        if resource_calendars[r_name].total_weekly_work == 0:
            removed_resources.add(r_name)
            print("%s: %.3f (%d)" % (r_name, resource_freq[r_name] / max_resource_freq, resource_freq[r_name]))
    print('-------------------------------------------------------')
    return removed_resources


def print_joint_resource_calendar_info(res_calendars, kpi_calendar, task_resources, task_resource_events,
                                       joint_resource_events, coverage_map):
    for t_name in task_resources:
        print("Task Name: %s, Coverage: %.2f" % (t_name, kpi_calendar.task_coverage(t_name)))
        print("In Timetable: %d events, Discarded: %d events"
              % (kpi_calendar.task_events_in_calendar[t_name],
                 kpi_calendar.task_events_count[t_name] - kpi_calendar.task_events_in_calendar[t_name]))
        removed_resources = list()

        for r_name in task_resources[t_name]:
            if r_name in res_calendars and res_calendars[r_name].total_weekly_work > 0:
                confidence, support = kpi_calendar.compute_confidence_support(r_name)
                participation_ratio = kpi_calendar.resource_participation_ratio(r_name)
                task_participation = kpi_calendar.resource_task_participation_ratio(r_name, t_name)
                print("    %s -> (%d events), Confidence: %.2f, Support: %.2f, "
                      "Task Part.: %.2f, Proc. Part.: %.2f"
                      % (r_name,
                         kpi_calendar.resource_task_freq[r_name][t_name],
                         confidence, support, task_participation, participation_ratio))
            else:
                removed_resources.append(r_name)
        print('----------------------------------------------------------------')

        # for r_name in task_resources[t_name]:
        #     if r_name in task_resource_freq[t_name][1]:
        #         print("%s: %.3f (%d)" % (r_name,
        #                                  task_resource_freq[t_name][1][r_name] / task_resource_freq[t_name][0],
        #                                  task_resource_freq[t_name][1][r_name]))
        #     else:
        #         print("%s: JOINT EXTERNAL RESOURCE" % r_name)
        # for r_name in task_resource_freq[t_name][1]:
        #     if r_name not in task_resources[t_name]:
        #         print("(%s) %s: %.3f (%d)" % ('-', r_name,
        #                                       task_resource_freq[t_name][1][r_name] / task_resource_freq[t_name][0],
        #                                       task_resource_freq[t_name][1][r_name]))


def _cases_to_del(resource_calendars, resource_freq, max_resource_freq, resource_cases, cases_to_remove, total_traces):
    print("Resources to Remove ...")
    for r_name in resource_calendars:
        if resource_calendars[r_name].total_weekly_work == 0:
            print("%s: %.3f (%d)" % (r_name, resource_freq[r_name] / max_resource_freq, resource_freq[r_name]))
            for case_id in resource_cases[r_name]:
                cases_to_remove.add(case_id)
    print("Original Total Cases:      %d" % total_traces)
    print("Postprocessed Total Cases: %d" % (total_traces - len(cases_to_remove)))
    print("Cases to remove: %d" % len(cases_to_remove))
    print('-------------------------------------------------------')


# def parse_xes_log(log_path, bpmn_graph, output_path):
#     f_name = ntpath.basename(log_path).split('.')[0]
#     print('Parsing Event Log %s ...' % f_name)
#     process_info = ProcessInfo()
#     i = 0
#     total_traces = 0
#     resource_list = set()
#
#     task_resource = dict()
#     task_distribution = dict()
#     flow_arcs_frequency = dict()
#     correct_traces = 0
#     correct_activities = 0
#     total_activities = 0
#     task_fired_ratio = dict()
#     task_missed_tokens = 0
#     missed_tokens = dict()
#
#     log_traces = xes_importer.apply(log_path)
#
#     arrival_times = list()
#
#     start_date = end_date = None
#     resource_calendars = dict()
#     arrival_dates = list()
#     month_dates = dict()
#     resource_freq = dict()
#     max_resource_freq = 0
#     task_resource_freq = dict()
#
#     calendar_factory = CalendarFactory(15)
#
#     for trace in log_traces:
#         arrival_dates.append(trace[0]['time:timestamp'])
#         # if previous_arrival_date is not None:
#         #     arrival_times.append((trace[0]['time:timestamp'] - previous_arrival_date).total_seconds())
#         # previous_arrival_date = trace[0]['time:timestamp']
#
#         caseid = trace.attributes['concept:name']
#         total_traces += 1
#         started_events = dict()
#         trace_info = Trace(caseid)
#         task_sequence = list()
#         for event in trace:
#             task_name = event['concept:name']
#             task_id = bpmn_graph.from_name[task_name]
#             resource = event['org:resource']
#             state = event['lifecycle:transition'].lower()
#             timestamp = event['time:timestamp']
#             # if previous_date is not None and previous_date > timestamp:
#             #     print("Unsorted event %s" % task_name)
#             previous_date = timestamp
#
#             calendar_factory.check_date_time(resource, task_name, timestamp)
#
#             start_date, end_date = _update_first_last(start_date, end_date, timestamp)
#             if task_name not in task_resource_freq:
#                 task_resource_freq[task_name] = [0, dict()]
#             if resource not in task_resource_freq[task_name][1]:
#                 task_resource_freq[task_name][1][resource] = 0
#             task_resource_freq[task_name][1][resource] += 1
#             task_resource_freq[task_name][0] = max(task_resource_freq[task_name][0],
#                                                    task_resource_freq[task_name][1][resource])
#             if resource not in resource_list:
#                 resource_list.add(resource)
#                 resource_calendars[resource] = RCalendar("%s_Schedule" % resource)
#                 resource_freq[resource] = 0
#             resource_freq[resource] += 1
#             max_resource_freq = max(max_resource_freq, resource_freq[resource])
#             # update_weekly_calendar(resource_calendars[resource], timestamp, 15)
#             # update_calendar_from_log(resource_calendars[resource], timestamp, state in ["start", "assign"], month_dates)
#             if state in ["start", "assign"]:
#                 started_events[task_id] = trace_info.start_event(task_id, task_name, timestamp, resource)
#                 task_sequence.append(task_id)
#             elif state == "complete":
#                 if task_id in started_events:
#                     event_info = trace_info.complete_event(started_events.pop(task_id), timestamp)
#                     if task_id not in task_resource:
#                         task_resource[task_id] = dict()
#                         task_distribution[task_id] = dict()
#
#                     if resource not in task_resource[task_id]:
#                         task_resource[task_id][resource] = list()
#                     task_resource[task_id][resource].append(event_info)
#         is_correct, fired_tasks, pending_tokens = bpmn_graph.reply_trace(task_sequence, flow_arcs_frequency)
#         if len(pending_tokens) > 0:
#             task_missed_tokens += 1
#             for flow_id in pending_tokens:
#                 if flow_id not in missed_tokens:
#                     missed_tokens[flow_id] = 0
#                 missed_tokens[flow_id] += 1
#         # if not is_correct:
#         #     print(caseid)
#         #     print('------------------------------------------------------')
#         if is_correct:
#             correct_traces += 1
#         for i in range(0, len(task_sequence)):
#             if task_sequence[i] not in task_fired_ratio:
#                 task_fired_ratio[task_sequence[i]] = [0, 0]
#             if fired_tasks[i]:
#                 correct_activities += 1
#                 task_fired_ratio[task_sequence[i]][0] += 1
#             task_fired_ratio[task_sequence[i]][1] += 1
#         total_activities += len(fired_tasks)
#         process_info.traces[caseid] = trace_info
#         i += 1
#
#     t_r = 100 * correct_traces / total_traces
#     print(month_dates)
#     a_r = 100 * correct_activities / total_activities
#     print("Correct Traces Ratio %.2f (Pass: %d, Fail: %d, Total: %d)" % (
#         t_r, correct_traces, total_traces - correct_traces, total_traces))
#     print("Correct Tasks  Ratio %.2f (Fire: %d, Fail: %d, Total: %d)" % (
#         a_r, correct_activities, total_activities - correct_activities, total_activities))
#     print("Missed Tokens Ratio  %.2f" % (100 * task_missed_tokens / total_traces))
#     print('----------------------------------------------')
#     # for task_id in task_fired_ratio:
#     #     print("%s: %.2f (Fail: %d / %d)" % (
#     #         task_id, 100 * task_fired_ratio[task_id][0] / task_fired_ratio[task_id][1],
#     #         task_fired_ratio[task_id][1] - task_fired_ratio[task_id][0],
#     #         task_fired_ratio[task_id][1]))
#     # print('-----------------------------------------------')
#     # for task_name in missed_tokens:
#     #     print("%s: %d" % (task_name, missed_tokens[task_name]))
#     min_dur = sys.float_info.max
#     max_dur = 0
#
#     resource_calendars, kpi_calendars = calendar_factory.build_weekly_calendars(0.5, 0.5)
#     for r_id in resource_calendars:
#         min_dur = min(min_dur, resource_calendars[r_id].total_weekly_work)
#         max_dur = max(max_dur, resource_calendars[r_id].total_weekly_work)
#         # print("Resource frequence:       %d" % resource_freq[r_id])
#         # print("Resource frequency Ratio: %.3f" % (resource_freq[r_id] / max_resource_freq))
#         resource_calendars[r_id].print_calendar_info()
#
#     # for t_name in task_resource_freq:
#     #     print("Task Name: %s" % t_name)
#     #     for r_name in task_resource_freq[t_name][1]:
#     #         print("%d, %.3f, %.2f -> %s" % (task_resource_freq[t_name][1][r_name],
#     #                                         task_resource_freq[t_name][1][r_name] / task_resource_freq[t_name][0],
#     #                                         resource_calendars[r_name].total_weekly_work / 3600, r_name))
#     #         #resource_calendars[r_name].print_calendar_info()
#     #     print("----------------------------------------------------------")
#
#     print('Min Resource Weekly Work: %.2f ' % (min_dur / 3600))
#     print('Max Resource Weekly Work: %.2f ' % (max_dur / 3600))
#     return  # Renmove this
#     print('Saving Resource Calendars ...')
#     json_map = dict()
#     for r_id in resource_calendars:
#         json_map[r_id] = resource_calendars[r_id].to_json()
#     with open('./input_files/resource_calendars/%s_calendars.json' % f_name, 'w') as file_writter:
#         json.dump(json_map, file_writter)
#
#     # create_calendar_from_rule_associations(resource_assoc_times, resource_days_freq, start_date, end_date)
#
#     print('Computing Branching Probability ...')
#     gateways_branching = bpmn_graph.compute_branching_probability(flow_arcs_frequency)
#     with open('./input_files/probability_distributions/%s_gateways_branching.json' % f_name, 'w') as file_writter:
#         json.dump(gateways_branching, file_writter)
#
#     print('Computing Arrival Times Distribution ...')
#     arrival_dates.sort()
#     for i in range(1, len(arrival_dates)):
#         arrival_times.append((arrival_dates[i] - arrival_dates[i - 1]).total_seconds())
#     with open('./input_files/probability_distributions/%s_arrival_times_distribution.json' % f_name,
#               'w') as file_writter:
#         json.dump(best_fit_distribution(arrival_times), file_writter)
#
#     print('Computing Task-Resource Distributions ...')
#     for task_id in task_resource:
#         for resorce_id in task_resource[task_id]:
#             real_durations = list()
#             for e_info in task_resource[task_id][resorce_id]:
#                 real_durations.append(resource_calendars[resorce_id].find_working_time(e_info.started_at,
#                                                                                        e_info.completed_at))
#
#                 if real_durations[len(real_durations) - 1] <= 0 and e_info.started_at != e_info.completed_at:
#                     x = resource_calendars[resorce_id].find_working_time(e_info.started_at, e_info.completed_at)
#                     print(real_durations[len(real_durations) - 1])
#             task_distribution[task_id][resorce_id] = best_fit_distribution(real_durations)
#     with open('./input_files/probability_distributions/%s_task_distribution.json' % f_name, 'w') as file_writter:
#         json.dump(task_distribution, file_writter)
#     print('----------------------------------------------------------------------------------')
#     return process_info


def _update_first_last(start_date, end_date, current_date):
    if start_date is None:
        start_date = current_date
        end_date = current_date
    start_date = min(start_date, current_date)
    end_date = max(end_date, current_date)
    return start_date, end_date
