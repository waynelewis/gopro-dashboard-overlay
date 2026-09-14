import bisect
from pathlib import Path

import numpy as np
import collections
import math

import fitdecode

from gopro_overlay.entry import Entry
from gopro_overlay.gpmf import GPSFix
from gopro_overlay.point import Point
from gopro_overlay.timeseries import Timeseries


def garmin_to_gps(v):
    return v / ((2 ** 32) / 360)


interpret = {
    "position_lat": lambda v, u: {"lat": garmin_to_gps(v)},
    "position_long": lambda v, u: {"lon": garmin_to_gps(v)},
    "distance": lambda v, u: {"odo": u.Quantity(v, u.m)},
    "altitude": lambda v, u: {"alt": u.Quantity(v, u.m)},
    "enhanced_altitude": lambda v, u: {"alt": u.Quantity(v, u.m)},
    "speed": lambda v, u: {"speed": u.Quantity(v, u.mps)},
    "enhanced_speed": lambda v, u: {"speed": u.Quantity(v, u.mps)},
    "heart_rate": lambda v, u: {"hr": u.Quantity(v, u.bpm)},
    "cadence": lambda v, u: {"cad": u.Quantity(v, u.rpm)},
    "temperature": lambda v, u: {"atemp": u.Quantity(v, u.degC)},
    "gps_accuracy": lambda v, u: {"dop": u.Quantity(v)},
    "power": lambda v, u: {"power": u.Quantity(v, u.watt)},
    "grade": lambda v, u: {"grad": u.Quantity(v)},
    "Sdps": lambda v, u: {"sdps": u.Quantity(v, u.cm)},
    "rear_gear_num": lambda v, u: {"gear_rear": u.Quantity(v)},
    "front_gear_num": lambda v, u: {"gear_front": u.Quantity(v)},
    "unknown_108": lambda v, u: {"respiration": u.Quantity(v / 100, u.brpm)},
    "total_distance": lambda v, u: {"total_distance": u.Quantity(v, u.m)},
    "norm_power": lambda v, u: {"norm_power": u.Quantity(v, u.watt)},
}


def load_timeseries(filepath: Path, units):
    ts = Timeseries()

    persistent_events = {}
    persistent_event_times = []

    power_buffer = collections.deque(maxlen=30)
    for i in range(0,30):
        power_buffer.append(0)

    power_val_count = 0
    power_val_sum = 0
    norm_power_sum = 0

    last_ts_event = None

    with fitdecode.FitReader(filepath) as ff:
        for frame in (f for f in ff if f.frame_type == fitdecode.FIT_FRAME_DATA):
            if frame.name == 'session':
                for field in frame.fields:
                    if field.name == "total_distance":
                        #print("total_distance = {0}".format(field.value))
                        total_distance = field.value


    with fitdecode.FitReader(filepath) as ff:
        for frame in (f for f in ff if f.frame_type == fitdecode.FIT_FRAME_DATA):

            if frame.name == 'record':
                entry = None
                items = {}

                items.update(**interpret["total_distance"](total_distance, units))

                for field in frame.fields:
                    if field.name == "timestamp":
                        # we should set the gps fix or Journey.accept() will skip the point:
                        entry = Entry(
                            dt=field.value,
                            gpsfix=GPSFix.LOCK_3D.value
                        )

                        # Now we need to see if there are relevant persistent events for us to copy in
                        if persistent_event_times:
                            relevant_persistent_event_index = bisect.bisect_right(persistent_event_times,
                                                                                  field.value) - 1
                            if relevant_persistent_event_index >= 0:
                                relevant_persistent_event_timestamp = persistent_event_times[
                                    relevant_persistent_event_index]
                                relevant_persistent_events = persistent_events[relevant_persistent_event_timestamp]
                                entry.update(**relevant_persistent_events)

                        last_ts_event = entry
                    else:
                        if field.name in interpret and field.value is not None:
                            items.update(**interpret[field.name](field.value, units))

                    if field.name == "power":
                        if isinstance(field.value, (int)):

                            print("power = {0}".format(field.value))
                            power_val_sum += field.value - power_buffer.popleft()
                            power_buffer.append(field.value)
                            power_val_count += 1

                            if power_val_count >= 30:
                                power_avg = power_val_sum / 30
                                norm_power_sum += math.pow(power_avg, 4)
                                norm_power_avg = norm_power_sum / (power_val_count - 30 + 1)
                                norm_power = math.pow(norm_power_avg, 0.25)
                            else:
                                norm_power = 0
                                power_avg = power_val_sum / power_val_count


                            print("power value sum = {0:4d}".format(power_val_sum))
                            print("power value count = {0:4d}".format(power_val_count))
                            print("30s avg. power = {0:4d}".format(round(power_avg)))
                            print("normalized power = {0:4d}".format(round(norm_power)))
                            print()

                            items.update(**interpret["norm_power"](norm_power, units))

                if "lat" in items and "lon" in items:
                    items["point"] = Point(lat=items["lat"], lon=items["lon"])
                    del (items["lat"])
                    del (items["lon"])

                # only use fit data items that have lat/lon
                if "point" in items:
                    entry.update(**items)
                    ts.add(entry)
            elif frame.name == 'event':

                event_frame = {fi.name: fi.value for fi in frame.fields}

                # this is pretty hacky - it will only work when the only events we care about have all the data fields
                if event_frame['event'] in {'front_gear_change', 'rear_gear_change'}:
                    # we want to consolidate this event with any other event we had at the same time
                    timestamp = event_frame['timestamp']
                    if timestamp in persistent_events:
                        item = persistent_events[timestamp]
                    else:
                        item = {}
                        persistent_events[timestamp] = item
                        persistent_event_times.append(timestamp)

                    for k in ['front_gear_num', 'rear_gear_num']:
                        if k in event_frame:
                            d = interpret[k](event_frame[k], units)
                            item.update(d)

                    if last_ts_event is not None:
                        if timestamp == last_ts_event.dt:
                            last_ts_event.update(**item)
            else:
                pass

    return ts
