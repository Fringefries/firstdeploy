from django.db import models
from django.utils import timezone
from django.utils.dateparse import parse_time 
from django.db.models.functions import TruncMonth
from django.db.models import Count

from views.redis import RS

from datetime import datetime
import statistics

def data_default():
    return {
        'humidity':{},
        'temperature':{},
        'hourly':{}
    }

class Report(models.Model):
    date        = models.DateField(auto_now_add=True, unique=True)
    data        = models.JSONField(default=data_default)
    
    class Meta:
        ordering = ['-date']
        indexes = [
            models.Index(fields=["date"]),
        ]

    def __str__(self):
        return f'{self.date.strftime("%B %d, %Y")}'
    
    def get_today():
        now = datetime.now().date()
        obj, created = Report.objects.get_or_create(date=now)
        print(f'{obj} is created? {created}')
        return obj
    
    def update_statistic():
        try:
            n = datetime.now().time().hour
            r = Report.get_today()
            data = r.data.copy()
            
            humid = RS.get('Humidity').replace('%', '')
            temp = RS.get('Temperature')
            
            if not str(n) in data['hourly'].keys():
                # lh = data['humidity'].keys()[-1]
                # lt = data['temperature'].keys()[-1]
                
                data['hourly'][n] = {
                    'humidity': humid,
                    'temperature': temp,
                }
                data['humidity'][n] = [humid]
                data['temperature'][n] = [temp]
            else:
                data['humidity'][str(n)].append(humid)
                data['temperature'][str(n)].append(temp)
            
            r.data = data
            r.save()
        except Exception as e:
            print(f"Error updating statistic: {e}")
        
    def get_th_report(id):
        obj = Report.objects.get(id=id)

        hourly = obj.data.get('hourly')
        
        data = {
            'temp':[],
            'humid':[],
            'labels':[]
        }
        
        for t, d in hourly.items():
            time = parse_time(f'{t}:00').strftime('%I:%M %p')
            data['labels'].append(time)
            data['temp'].append(d.get('temperature'))
            data['humid'].append(d.get('humidity'))
        
        # data['temp'] = obj.data.get('temperature')
        # data['humid'] = obj.data.get('humidity')
        
        return data
    
    def get_report_average_hourly():
        mdata = {}
        ldata = {
                    'h':[],
                    't':[],
                    'l':[
                            "12:00 AM", "01:00 AM", "02:00 AM", "03:00 AM", "04:00 AM", "05:00 AM",
                            "06:00 AM", "07:00 AM", "08:00 AM", "09:00 AM", "10:00 AM", "11:00 AM",
                            "12:00 PM", "01:00 PM", "02:00 PM", "03:00 PM", "04:00 PM", "05:00 PM",
                            "06:00 PM", "07:00 PM", "08:00 PM", "09:00 PM", "10:00 PM", "11:00 PM"
                        ]
                }
        
        # This get_average is the one that was refactored before and seemed more correct for the data structure
        # where it processes a single day's report data for one sensor type (e.g., humidity_data_for_day)
        # and returns a dictionary of hourly averages for that day.
        # The version in the current file view seems to be an older one.
        # I will implement based on the previous understanding of the data structure for hourly reports.

        reports = Report.objects.all().order_by('-id')[:7]
        
        # Initialize mdata structure to ensure all hours are present for 'h' and 't'
        # Each hour will store a list of daily averages for that hour from the reports
        mdata['h'] = {str(i): [] for i in range(24)}
        mdata['t'] = {str(i): [] for i in range(24)}

        for r_idx, r_obj in enumerate(reports): # For each of the last 7 daily reports
            report_data_dict = r_obj.data # This is the JSON field, typically a dict
            if not isinstance(report_data_dict, dict):
                continue # Skip if data is not a dictionary
            
            for sensor_type_key, hourly_avg_lists_in_mdata in [('humidity', mdata['h']), ('temperature', mdata['t'])]:
                sensor_data_for_day = report_data_dict.get(sensor_type_key) # e.g., {'0':[20,21], '1':[22,23],...}
                
                if not isinstance(sensor_data_for_day, dict):
                    continue # Skip if this sensor's data for the day isn't a dict

                for hour_str, readings_for_hour_list in sensor_data_for_day.items():
                    if hour_str not in hourly_avg_lists_in_mdata: # Ensure hour_str is '0'-'23'
                        continue
                    
                    if readings_for_hour_list and isinstance(readings_for_hour_list, list):
                        valid_float_readings = []
                        for reading in readings_for_hour_list:
                            try:
                                valid_float_readings.append(float(reading))
                            except (ValueError, TypeError):
                                pass # Ignore non-convertible values
                        
                        if valid_float_readings: # If there were valid float readings for this hour on this day
                            try:
                                # Average of readings for THIS hour, from THIS day's report
                                daily_avg_for_this_hour = statistics.fmean(valid_float_readings)
                                hourly_avg_lists_in_mdata[hour_str].append(daily_avg_for_this_hour)
                            except statistics.StatisticsError:
                                # This case (valid_float_readings not empty but fmean fails) is unlikely
                                pass 
        
        # Now mdata['h']['0'] is a list of average humidities for 00:00 from up to 7 days.
        # We need to average these daily averages for each hour.
        for ldata_sensor_key in ['h', 't']: # For humidity ('h') and temperature ('t') in ldata
            # Ensure ldata lists are built in hour order 0-23
            hourly_averages_source = mdata[ldata_sensor_key] # e.g. mdata['h']
            for hour_int in range(24):
                hour_str_key = str(hour_int)
                list_of_daily_averages_for_this_hour = hourly_averages_source.get(hour_str_key, [])
                
                if list_of_daily_averages_for_this_hour: # If there's any data for this hour across the days
                    try:
                        final_avg_for_hour = statistics.fmean(list_of_daily_averages_for_this_hour)
                        ldata[ldata_sensor_key].append(round(final_avg_for_hour, 1))
                    except statistics.StatisticsError:
                        # This case (list not empty but fmean fails) is unlikely
                        ldata[ldata_sensor_key].append(None) 
                else: # No data for this hour from any of the last 7 days
                    ldata[ldata_sensor_key].append(None)
        return ldata
        
        
    def get_report_average():
        def get_day_average(data_for_sensor_type):
            # data_for_sensor_type is expected to be like: 
            # {'0':[val1,val2], '1':[val3,val4]} where keys are hours, values are lists of readings
            # OR it could be directly a list of daily values if not hourly: [val1, val2, val3]
            # The original traceback suggests it's called with d.get('humidity'), which is 
            # the structure for a whole day: {"0":[...], "1":[...], ...}

            if not data_for_sensor_type or not isinstance(data_for_sensor_type, dict):
                return None

            daily_averages_for_each_hour = [] # Store the average of readings for each hour
            for hour_key in data_for_sensor_type.keys():
                readings_for_this_hour = data_for_sensor_type.get(hour_key)
                if readings_for_this_hour and isinstance(readings_for_this_hour, list):
                    valid_float_readings_for_hour = []
                    for reading in readings_for_this_hour:
                        try:
                            valid_float_readings_for_hour.append(float(reading))
                        except (ValueError, TypeError):
                            pass # Ignore non-convertible values
                    
                    if valid_float_readings_for_hour: # If list 'l' has valid float values for the hour
                        try:
                            avg_for_hour = statistics.fmean(valid_float_readings_for_hour)
                            daily_averages_for_each_hour.append(avg_for_hour)
                        except statistics.StatisticsError:
                            pass # Should not happen if valid_float_readings_for_hour is not empty
            
            if not daily_averages_for_each_hour: # If no hourly averages were calculated for this day
                return None
            
            try:
                # The final value is the average of the hourly averages for that day
                overall_day_average = statistics.fmean(daily_averages_for_each_hour)
                return round(overall_day_average, 1)
            except statistics.StatisticsError:
                return None # Should not happen if daily_averages_for_each_hour is not empty
                
        reports = Report.objects.all().order_by('-id')
        if reports.count() > 14:
            reports = reports[:14]
            
        rdata = {
            't':[],
            'h':[],
            'l':[],
        }
        
        for r in reports:
            d = r.data.copy()
            rdata['h'].append(get_day_average(d.get('humidity')))
            rdata['t'].append(get_day_average(d.get('temperature')))
            rdata['l'].append(r.date.strftime("%b %d"))
        
        return rdata
        
    def range_report(yr, mt):
        qset = Report.objects.filter(date__year=yr, date__month=mt)
        
        for obj in qset:
            hourly = obj.data.get('hourly')
            
