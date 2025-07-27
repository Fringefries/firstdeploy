from django.shortcuts import render
from django.views import View
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.contrib.auth import get_user_model, authenticate, login, logout
from django.http import HttpResponseRedirect, Http404, JsonResponse, HttpResponse, StreamingHttpResponse
from django.views.decorators import gzip

from datetime import datetime, timedelta

from django.template.loader import render_to_string
from django.contrib.auth import views as auth_views
from report.models import Report

from .forms import AdminAuthForm
from .redis import RS
import io, logging, socketserver, subprocess, serial, time, json, schedule, threading, requests

import threading


class LoginView(View):
    form_class = AdminAuthForm
    template_name = "login.html"

    def get(self, request, *args, **kwargs):
        form = self.form_class()
        return render(request, self.template_name, {"form": form})

    def post(self, request, *args, **kwargs):
        form = self.form_class(request.POST)
        if form.is_valid():
            username =form.cleaned_data['username']
            password =form.cleaned_data['password']
            user = authenticate(username=username, password=password)
            if user:
                
                if form.cleaned_data['keep_me_logged']:
                    request.session.set_expiry(604800)
                
                login(request, user)
                    
            return HttpResponseRedirect("/")

        return render(request, self.template_name, {"form": form})

class Login(auth_views.LoginView):
    template_name = "login.html"
    
def firebase_token_update(request):
    token = request.POST.get('token')
    
    if request.user.is_authenticated and token:
        user = request.user
        existing_token = user.data.get('firebase_token', '')
        
        if token != existing_token:
            user.data['firebase_token'] = token
            user.save()
            admin_subscribe_topic(token)
            return JsonResponse({'status': 200})
    
    return JsonResponse({'status':404})

last_watered = {}


def check_soil_and_control_relays():
    current_time = datetime.now()
    
    temperature = safe_float(RS.get('Temperature'))
    temperature_threshold = 30
    is_hot_day = temperature and temperature > temperature_threshold
    
    sensors_data = []
    sensors_needing_water = []
    
    for i in range(1, 10): 
        sensor_value = safe_float(RS.get(f'soilSensor{i}'))
        relay_pin = globals().get(f'RELAY{i}')
        
        if sensor_value is not None and relay_pin:
            moisture_threshold = 350 if is_hot_day else 400
            
            sensor_key = f'last_watered_sensor_{i}'
            last_time_str = RS.get(sensor_key)
            last_time = None
            days_since = None
            
            if last_time_str:
                last_time = datetime.fromtimestamp(float(last_time_str))
                hours_since = (current_time - last_time).total_seconds() / 3600
                days_since = hours_since / 24
            
            sensor_data = {
                'id': i,
                'value': sensor_value,
                'threshold': moisture_threshold,
                'last_watered': last_time.strftime("%Y-%m-%d %H:%M") if last_time else "Never",
            }
            
            if days_since is not None:
                sensor_data['days_since_watered'] = days_since
            
            min_days_between_watering = 1 if is_hot_day else 2
            
            watering_action = None
            
            if sensor_value < moisture_threshold:
                if last_time_str and days_since < min_days_between_watering:
                    pin, state = GP.turn_off(relay_pin)
                    watering_action = f"Sensor {i} was watered {days_since:.1f} days ago, skipping (Hot day: {is_hot_day})"
                    print(watering_action)
                else:
                    duration = 90
                    if is_hot_day:
                        duration = 120
                    
                    pin, state = GP.turn_on(relay_pin)
                    watering_action = f"Activated relay {i} for soil sensor {i} with value {sensor_value}, duration: {duration}s (Hot day: {is_hot_day})"
                    print(watering_action)
                    
                    threading.Timer(duration, turn_off_relay, args=[relay_pin, i, current_time]).start()
                    
                    sensors_needing_water.append(i)
            else:
                pin, state = GP.turn_off(relay_pin)
            
            if watering_action:
                sensor_data['watering_action'] = watering_action
            
            sensors_data.append(sensor_data)
    
    if sensors_needing_water:
        send_soil_moisture_alert(sensors_data, temperature)
    
    return sensors_data
                
def turn_off_relay(relay_pin, sensor_number, watering_time):
    pin, state = GP.turn_off(relay_pin)
    
    sensor_key = f'last_watered_sensor_{sensor_number}'
    RS.set(sensor_key, watering_time.timestamp())
    
    print(f'Deactivated relay {sensor_number} after timed watering, timestamp recorded: {watering_time}')

class KillableThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self._kill = threading.Event()
        self.daemon = True
    
    def run(self):
        
        schedule.every(1).minutes.do(Report.update_statistic)

        #Auto check of soil sensors and control relays
        schedule.every(5).minutes.do(check_soil_and_control_relays)

        data = RS.get('light_timer')
        
        if data and data.get('sleep_at'):
            schedule.every().day.at(data.get('sleep_at')).do(GP.light_off)
            schedule.every().day.at(data.get('wake_at')).do(GP.light_on)
        
        data = RS.get('fan_timer')
        
        if data and data.get('sleep_at'):
            schedule.every().day.at(data.get('sleep_at')).do(GP.fan_off)
            schedule.every().day.at(data.get('wake_at')).do(GP.fan_on)
            
        while True:
            schedule.run_pending()
            time.sleep(1)
            is_killed = self._kill.wait(1)
            if is_killed:
                break

        print("Killing Thread")

    def kill(self):
        self._kill.set()

t = KillableThread()
t.name = "relay_timer"
t.start()

def kill_thread(name):
    for thread in threading.enumerate(): 
        print(thread.name)
        if thread.name == name:
            thread.kill()
            
def relay_timer(request):
    if request.method == "POST" and request.POST.get('relay').lower() == "valve":
        RS.set('valve_off_at_waterlevel', int(request.POST.get('offAt')))
        RS.set('valve_on_at_waterlevel', int(request.POST.get('onAt')))
        return JsonResponse({'status':1})

def toggle(request, pin):
    cs = GP.get_state(pin)
    print(f'CURRENT STATE {cs}')
    pin, state = GP.toggle(pin)
    print(f'RESULT STATE {state}')
    
    if cs != state:
        return JsonResponse({'status':1})

    return JsonResponse({'status':0})

def get_verbose_name(v):
    verb = ''
    if 0 <= v <= 200:  # Low sensor value means wet soil
        verb = 'Wet'
    elif 300 < v <= 400:  # Mid-range sensor value means moist soil
        verb = 'Moist'
    else:  # High sensor value (v > 600) means dry soil
        verb = 'Dry'
    
    return verb

def safe_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

@gzip.gzip_page
def cam_feed(request):
    subprocess.call("sudo systemctl restart papacam.service", shell=True)
    return HttpResponseRedirect('https://localhost:8000/index.html')

@method_decorator(login_required, name="dispatch")
class LandingPage(View):
    test=''

    def post(self, request):
        get_th_report = request.POST.get('THReport')
        update_stats = request.POST.get('UpdateStats')
        get_average_report = request.POST.get('averageReport')
        
        if get_average_report:
            d = Report.get_report_average()
            h = Report.get_report_average_hourly()
            data = {
                            'd':d, 
                            'h':h
                }
            
            return JsonResponse(data, safe=False)
        
            
        elif get_th_report:
            id = request.POST.get('ID')
            
            data = Report.get_th_report(id)
            
            return JsonResponse(data, safe=False)
        
        elif update_stats:
            data = {}
            humidity = RS.get('Humidity')
            humidity_out = RS.get('2Humidity')
            ss1 = RS.get('soilSensor1')
            ss2 = RS.get('soilSensor2')
            ss3 = RS.get('soilSensor3')
            ss4 = RS.get('soilSensor4')
            ss5 = RS.get('soilSensor5')
            ss6 = RS.get('soilSensor6')
            ss7 = RS.get('soilSensor7')
            ss8 = RS.get('soilSensor8')
            ss9 = RS.get('soilSensor9')
            
            raw_temp = RS.get('Temperature')
            if isinstance(raw_temp, str):
                raw_temp = raw_temp.replace('°C', '')
            data['temp'] = f"{raw_temp}°C" if raw_temp is not None else "None°C"

            raw_temp_out = RS.get('2Temperature')
            if isinstance(raw_temp_out, str):
                raw_temp_out = raw_temp_out.replace('°C', '')
            data['temp_out'] = f"{raw_temp_out}°C" if raw_temp_out is not None else "None°C"
            data['humid'] = humidity
            data['humid_out'] = humidity_out

            ss1 = safe_float(ss1)
            ss2 = safe_float(ss2)
            ss3 = safe_float(ss3)
            ss4 = safe_float(ss4)
            ss5 = safe_float(ss5)
            ss6 = safe_float(ss6)
            ss7 = safe_float(ss7)            
            ss8 = safe_float(ss8)
            ss9 = safe_float(ss9)
            
            if ss1 is not None:
                data['ss1'] = ss1
                data['ss1p'] = f'{round((ss1 / 950) * 100)}%'
                data['ss1_verbose'] = get_verbose_name(ss1)
            else:
                data['ss1'] = 'N/A'
                data['ss1p'] = 'N/A'
                data['ss1_verbose'] = 'Unknown'
                
            if ss2 is not None:
                data['ss2'] = ss2
                data['ss2p'] = f'{round((ss2 / 950) * 100)}%'
                data['ss2_verbose'] = get_verbose_name(ss2)
            else:
                data['ss2'] = 'N/A'
                data['ss2p'] = 'N/A'
                data['ss2_verbose'] = 'Unknown'
            if ss3 is not None:
                data['ss3'] = ss3
                data['ss3p'] = f'{round((ss3 / 950) * 100)}%'
                data['ss3_verbose'] = get_verbose_name(ss3)
            else:
                data['ss3'] = 'N/A'
                data['ss3p'] = 'N/A'
                data['ss3_verbose'] = 'Unknown'
            if ss4 is not None:
                data['ss4'] = ss4
                data['ss4p'] = f'{round((ss4 / 950) * 100)}%'
                data['ss4_verbose'] = get_verbose_name(ss4)
            else:
                data['ss4'] = 'N/A'
                data['ss4p'] = 'N/A'
                data['ss4_verbose'] = 'Unknown'
            if ss5 is not None:
                data['ss5'] = ss5
                data['ss5p'] = f'{round((ss5 / 950) * 100)}%'
                data['ss5_verbose'] = get_verbose_name(ss5)
            else:
                data['ss5'] = 'N/A'
                data['ss5p'] = 'N/A'
                data['ss5_verbose'] = 'Unknown'
            if ss6 is not None:
                data['ss6'] = ss6
                data['ss6p'] = f'{round((ss6 / 950) * 100)}%'
                data['ss6_verbose'] = get_verbose_name(ss6)
            else:
                data['ss6'] = 'N/A'
                data['ss6p'] = 'N/A'
                data['ss6_verbose'] = 'Unknown'
            if ss7 is not None:
                data['ss7'] = ss7
                data['ss7p'] = f'{round((ss7 / 950) * 100)}%'
                data['ss7_verbose'] = get_verbose_name(ss7)
            else:
                data['ss7'] = 'N/A'
                data['ss7p'] = 'N/A'
                data['ss7_verbose'] = 'Unknown'
            if ss8 is not None:
                data['ss8'] = ss8
                data['ss8p'] = f'{round((ss8 / 950) * 100)}%'
                data['ss8_verbose'] = get_verbose_name(ss8)
            else:
                data['ss8'] = 'N/A'
                data['ss8p'] = 'N/A'
                data['ss8_verbose'] = 'Unknown'
            if ss9 is not None:
                data['ss9'] = ss9
                data['ss9p'] = f'{round((ss9 / 950) * 100)}%'
                data['ss9_verbose'] = get_verbose_name(ss9)
            else:
                data['ss9'] = 'N/A'
                data['ss9p'] = 'N/A'
                data['ss9_verbose'] = 'Unknown'
            
            return JsonResponse(data, safe=False)
        
    def get(self, request):
        reports = Report.objects.all()
        context = {
            'reports':reports
        }
        
        humidity = RS.get('Humidity')
        humidity_out = RS.get('2Humidity')
        ss1 = RS.get('soilSensor1')
        ss2 = RS.get('soilSensor2')
        ss3 = RS.get('soilSensor3')
        ss4 = RS.get('soilSensor4')
        ss5 = RS.get('soilSensor5')
        ss6 = RS.get('soilSensor6')
        ss7 = RS.get('soilSensor7')
        ss8 = RS.get('soilSensor8')
        ss9 = RS.get('soilSensor9')

        # Safely handle ss1 and ss2
        ss1 = safe_float(ss1)
        ss2 = safe_float(ss2)

        ss3 = safe_float(ss3)
        ss4 = safe_float(ss4)
        ss5 = safe_float(ss5)
        ss6 = safe_float(ss6)
        ss7 = safe_float(ss7)
        ss8 = safe_float(ss8)
        ss9 = safe_float(ss9)

        ss1_verbose = get_verbose_name(ss1) if ss1 is not None else 'Unknown'
        ss2_verbose = get_verbose_name(ss2) if ss2 is not None else 'Unknown'
        ss3_verbose = get_verbose_name(ss3) if ss3 is not None else 'Unknown'
        ss4_verbose = get_verbose_name(ss4) if ss4 is not None else 'Unknown'
        ss5_verbose = get_verbose_name(ss5) if ss5 is not None else 'Unknown'
        ss6_verbose = get_verbose_name(ss6) if ss6 is not None else 'Unknown'
        ss7_verbose = get_verbose_name(ss7) if ss7 is not None else 'Unknown'
        ss8_verbose = get_verbose_name(ss8) if ss8 is not None else 'Unknown'
        ss9_verbose = get_verbose_name(ss9) if ss9 is not None else 'Unknown'
        
        context['temp'] = RS.get('Temperature')
        context['temp_out'] = RS.get('2Temperature')
        context['humid'] = humidity
        context['humid_out'] = humidity_out
        
        # Sensor 1
        if ss1 is not None:
            context['ss1'] = ss1
            context['ss1_verbose'] = ss1_verbose
        else:
            context['ss1'] = 'N/A'
            context['ss1_verbose'] = 'Unknown'
        
        # Sensor 2
        if ss2 is not None:
            context['ss2'] = ss2
            context['ss2_verbose'] = ss2_verbose
        else:
            context['ss2'] = 'N/A'
            context['ss2_verbose'] = 'Unknown'
       
        # Sensor 3
        if ss3 is not None:
            context['ss3'] = ss3
            context['ss3_verbose'] = ss3_verbose
        else:
            context['ss3'] = 'N/A'
            context['ss3_verbose'] = 'Unknown'
        
        # Sensor 4
        if ss4 is not None:
            context['ss4'] = ss4
            context['ss4_verbose'] = ss4_verbose
        else:
            context['ss4'] = 'N/A'
            context['ss4_verbose'] = 'Unknown'
        
        # Sensor 5
        if ss5 is not None:
            context['ss5'] = ss5
            context['ss5_verbose'] = ss5_verbose
        else:
            context['ss5'] = 'N/A'
            context['ss5_verbose'] = 'Unknown'
        
        # Sensor 6
        if ss6 is not None:
            context['ss6'] = ss6
            context['ss6_verbose'] = ss6_verbose
        else:
            context['ss6'] = 'N/A'
            context['ss6_verbose'] = 'Unknown'
        
        # Sensor 7
        if ss7 is not None:
            context['ss7'] = ss7
            context['ss7_verbose'] = ss7_verbose
        else:
            context['ss7'] = 'N/A'
            context['ss7_verbose'] = 'Unknown'
        
        # Sensor 8
        if ss8 is not None:
            context['ss8'] = ss8
            context['ss8_verbose'] = ss8_verbose
        else:
            context['ss8'] = 'N/A'
            context['ss8_verbose'] = 'Unknown'
        
        # Sensor 9
        if ss9 is not None:
            context['ss9'] = ss9
            context['ss9_verbose'] = ss9_verbose
        else:
            context['ss9'] = 'N/A'
            context['ss9_verbose'] = 'Unknown'
        
        context['relay_states'] = RS.get('relay_states')
        
        context['valve_off_at_waterlevel'] = RS.get('valve_off_at_waterlevel')
        context['valve_on_at_waterlevel'] = RS.get('valve_on_at_waterlevel')
        
        return render(request, "pages/home.html", context)

def report_view(request):
    reports = Report.objects.all()
    context = {
        'reports':reports
    }
    
    humidity = RS.get('Humidity')
    humidity_out = RS.get('2Humidity')
    ss1 = RS.get('soilSensor1')
    ss2 = RS.get('soilSensor2')
    ss3 = RS.get('soilSensor3')
    ss4 = RS.get('soilSensor4')
    ss5 = RS.get('soilSensor5')
    ss6 = RS.get('soilSensor6')
    ss7 = RS.get('soilSensor7')
    ss8 = RS.get('soilSensor8')
    ss9 = RS.get('soilSensor9')
    ss1_verbose = get_verbose_name(int(ss1))
    ss2_verbose = get_verbose_name(int(ss2))
    ss3_verbose = get_verbose_name(int(ss3))
    ss4_verbose = get_verbose_name(int(ss4))
    ss5_verbose = get_verbose_name(int(ss5))
    ss6_verbose = get_verbose_name(int(ss6))
    ss7_verbose = get_verbose_name(int(ss7))
    ss8_verbose = get_verbose_name(int(ss8))
    ss9_verbose = get_verbose_name(int(ss9))
    
    context['temp'] = RS.get('Temperature')
    context['temp_out'] = RS.get('2Temperature')
    context['humid'] = humidity
    context['humid_int'] = humidity.replace('%', '')
    context['humid_out'] = humidity_out
    context['humid_out_int'] = humidity_out.replace('%', '')
    context['ss1'] = ss1
    context['ss2'] = ss2
    context['ss3'] = ss3
    context['ss4'] = ss4
    context['ss5'] = ss5
    context['ss6'] = ss6
    context['ss7'] = ss7
    context['ss8'] = ss8
    context['ss9'] = ss9
    context['ss1_verbose'] = ss1_verbose
    context['ss2_verbose'] = ss2_verbose
    context['ss3_verbose'] = ss3_verbose
    context['ss4_verbose'] = ss4_verbose
    context['ss5_verbose'] = ss5_verbose
    context['ss6_verbose'] = ss6_verbose
    context['ss7_verbose'] = ss7_verbose
    context['ss8_verbose'] = ss8_verbose
    context['ss9_verbose'] = ss9_verbose
    context['valve'] = GP.get_state(VALVE)
    context['valve_pin'] = VALVE
    
    return render(request, "pages/report_view.html", context)

