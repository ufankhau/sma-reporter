#
# sma-mqtt-reporter
#

# ------------------------
# load necessary libraries
# ------------------------
import _thread
from datetime import datetime
from tzlocal import get_localzone
import threading
import socket
import os
import subprocess
import argparse
import sys
import json
import os.path
from configparser import ConfigParser
from unidecode import unidecode
from time import time, sleep, localtime, strftime
from collections import OrderedDict
from colorama import Fore, Style
import paho.mqtt.client as mqtt
import sdnotify
#from signal import signal, SIGPIPE, SIG_DFL

#signal(SIGPIPE,SIG_DFL)
#
#
script_version = "1.2.4"
script_name = 'sma-mqtt-reporter.py'
script_info = '{} v{}'.format(script_name, script_version)
project_name = 'SMA Inverter-MQTT2HA Reporter'

# will use this throughout
local_tz = get_localzone()

if False:
	# will be caught by python 2.7 to be illegal syntax
	print_line("Sorry, this script requires a pyhton3 runtime environment.", file=sys.stderr)
	os._exit(1)

# construct the argument parse and parse the arguments
ap = argparse.ArgumentParser(description=project_name)
ap.add_argument("-v", "--verbose", help="increase output verbosity", action="store_true")
ap.add_argument("-d", "--debug", help="show debug output", action="store_true")
ap.add_argument("-l", "--logfile", help="store log in logfile", action="store_true")
ap.add_argument("-s", "--stall", help="Test: report only the first time", action="store_true")
ap.add_argument("-c", "--config_dir", help="set directory where config.ini is located", default=sys.path[0])
args = vars(ap.parse_args())
opt_verbose = args["verbose"]
opt_debug = args["debug"]
opt_stall = args["stall"]
opt_logfile = args["logfile"]
config_dir = args["config_dir"]

# Systemd Service Notifications - https://github.com/bb4242/sdnotify
sd_notifier = sdnotify.SystemdNotifier()

# ----------------
# Logging function
# ----------------
def print_line(text, error=False, warning=False, info=False, verbose=False, debug=False, console=True, sd_notify=False):
	timestamp = strftime('%Y-%m-%d %H:%M:%S', localtime())
	if console:
		if error:
			print(Fore.RED + Style.BRIGHT + '[{}] '.format(timestamp) + Style.RESET_ALL + text + Style.RESET_ALL, file=sys.stderr)
			if opt_logfile:
				f.write(Fore.RED + Style.BRIGHT + '[{}] '.format(timestamp) + Style.RESET_ALL + text + '\n' + Style.RESET_ALL, file=sys.stderr)
		elif warning:
			print(Fore.YELLOW + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL)
			if opt_logfile:
				f.write(Fore.YELLOW + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + '\n' + Style.RESET_ALL)
		elif info or verbose:
			if opt_verbose:
				print(Fore.GREEN + '[{}] '.format(timestamp) + Fore.YELLOW + '- ' + '{}'.format(text) + Style.RESET_ALL)
				if opt_logfile:
					f.write(Fore.GREEN + '[{}] '.format(timestamp) + Fore.YELLOW + '- ' + '{}'.format(text) + '\n' + Style.RESET_ALL)
		elif debug:
			if opt_debug:
				print(Fore.CYAN + '[{}] '.format(timestamp) + '- (DBG): ' + '{}'.format(text) + Style.RESET_ALL)
				if opt_logfile:
					f.write(Fore.CYAN + '[{}] '.format(timestamp) + '- (DBG): ' + '{}'.format(text) + '\n' + Style.RESET_ALL)
		else:
			print(Fore.GREEN + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + Style.RESET_ALL)
			if opt_logfile:
				f.write(Fore.GREEN + '[{}] '.format(timestamp) + Style.RESET_ALL + '{}'.format(text) + '\n' + Style.RESET_ALL)

	timestamp_sd = strftime('%b %d %H:%M:%S', localtime())
	if sd_notify:
		sd_notifier.notify('STATUS={} - {}.'.format(timestamp_sd, unidecode(text)))

# -------------
# start logging
# -------------
print_line(script_info, info=True)
if opt_verbose:
	print_line('Verbose enabled', info=True)
if opt_logfile:
	f = open(os.path.join(config_dir, 'sma-mqtt-reporter.log'), "w")
	print_line("Logfile enabled", debug=True)
if opt_debug:
	print_line("Debug enabled", debug=True)
if opt_stall:
	print_line("TEST: Stall (no-re-reporting) enabled", debug=True)


# -------------
# MQTT handlers
# -------------
#
# Eclipse Paho callbacks - http://www.eclipse.org/paho/clients/python/docs/#callbacks
mqtt_client_connected = False
print_line('* INIT mqtt_client_connected=[{}]'.format(mqtt_client_connected), debug=True)
mqtt_client_should_attempt_reconnect = True

def on_connect(client, userdata, flags, rc):
	global mqtt_client_connected
	if rc == 0:
		print_line('* MQTT connection established', console=True, sd_notify=True)
		print_line('')
		mqtt_client_connected = True
		print_line('on_connect() mqtt_clinet_connected=[{}]'.format(mqtt_client_connected), debug=True)
	else:
		print_line('! Connection error with result code {} - {}'.format(str(rc), mqtt.connack_string(rc)), error=True)
		print_line('MQTT Connection error with result code {} - {}'.format(str(rc), mqtt.connack_string(rc)), error=True, sd_notify=True)
		mqtt_client_connected = False
		print_line('on_connect() mqtt_client_connected=[{}]'.format(mqtt_client_connected), debug=True, error=True)
		os._exit(1)

def on_publish(client, userdata, mid):
	print_line('* Data successfully published.')
	pass

# -----------------------
# Load configuration file
# -----------------------
config = ConfigParser(delimiters=('=', ), inline_comment_prefixes=('#'))
config.optionxform = str
try:
	with open(os.path.join(config_dir, 'config.ini')) as config_file:
		config.read_file(config_file)
except IOError:
	print_line('No configuration file "config.ini"', error=True)
	sys.exit(1)

daemon_enabled = config['Daemon'].getboolean('enabled', True)

default_base_topic = 'home/nodes'
base_topic = config['MQTT'].get('base_topic', default_base_topic).lower()

default_sensor_name = 'smainv-reporter'
sensor_name = config['MQTT'].get('sensor_name', default_sensor_name).lower()

default_discovery_prefix = 'homeassistant'
discovery_prefix = config['MQTT'].get('discovery_prefix', default_discovery_prefix).lower()

# frequency of reporting data from the SMA Inverter
min_interval_in_minutes = 1
max_interval_in_minutes = 10
default_interval_in_minutes = 2
interval_in_minutes = config['Daemon'].get('interval_in_minutes', default_interval_in_minutes)

# default domain - check need for this variable

# check configuration
if (interval_in_minutes < min_interval_in_minutes) or (interval_in_minutes > max_interval_in_minutes):
	print_line('ERROR: Invalid "interval_in_minutes" found in configuration fiile: "config.ini"! Must be [{}-{}]. Fix and try again ... aborting'.format(min_interval_in_minutes, max_interval_in_minutes), error=True, sd_notify=True)
	sys.exit(1)
if not config['MQTT']:
	print_line('ERROR: No MQTT settings found in configuration file "config.ini"! Fix and try again ... aborting', error=True, sd_notify=True)
	sys.exit(1)

print_line('Configuration accepted', console=True, sd_notify=True)


# ---------------------------------------------------------
# Timer and timer functions for ALIVE MQTT Notices handling
# ---------------------------------------------------------

ALIVE_TIMEOUT_IN_SECONDS = 60

def publishAliveStatus():
	print_line("- SEND: yes, still alive -", debug=True)
	mqtt_client.publish(lwt_topic, payload=lwt_online_val, retain=False)

def aliveTimeoutHandler():
	print_line("- MQQT TIMER INTERRUPT -", debug=True)
	_thread.start_new_thread(publishAliveStatus, ())
	startAliveTimer()

def startAliveTimer():
	global aliveTimer 
	global aliveTimerRunningStatus
	stopAliveTimer()
	aliveTimer = threading.Timer(ALIVE_TIMEOUT_IN_SECONDS, aliveTimeoutHandler)
	aliveTimer.start()
	aliveTimerRunningStatus = True
	print_line("- started MQTT timer - every {} seconds".format(ALIVE_TIMEOUT_IN_SECONDS), debug=True)

def stopAliveTimer():
	global aliveTimer
	global aliveTimerRunningStatus
	aliveTimer.cancel()
	aliveTimerRunningStatus = False
	print_line("- stopped MQTT timer", debug=True)

def isAliveTimerRunning():
	global aliveTimerRunningStatus
	return aliveTimerRunningStatus

# ALIVE TIMER
aliveTimer = threading.Timer(ALIVE_TIMEOUT_IN_SECONDS, aliveTimeoutHandler)
# BOOL tracking state of ALIVE TIMER
aliveTimerRunningStatus = False



# --------------------------------
# SMA Inverter variables monitored
#---------------------------------
smainv_sunrise = ''
smainv_sunset = ''
smainv_device_type = ''
smainv_software_version = ''
smainv_serial_number = ''
smainv_device_temperature = ''
smainv_gridrelay_status = ''
smainv_etoday = ''
smainv_etotal = ''
smainv_operation_time = ''
smainv_feedin_time = ''
smainv_gridfrequency = ''
smainv_last_update_date = datetime.min
smainv_mqtt_script = script_info
# Tuple (Pdc, Udc, Idc)
smainv_dcstring1 = ''
smainv_dcstring2 = ''
# Tuple (Pac, Uac, Iac)
smainv_acphase1 = ''
smainv_acphase2 = ''
smainv_acphase3 = ''
smainv_totalPac = ''
smainv_totalPdc = ''
smainv_efficiency = ''

# -------------------------------
# monitor variable fetch routines
# -------------------------------

# DC String Parameter extraction
# ------------------------------
def getDCstring_param(text):
	answer_tuple = ''
	power = text[1].strip(' -Udc')
	voltage = text[2].strip(' -Idc')
	ampere = text[3].strip(' ')
	answer_tuple = (power, voltage, ampere)
	return (answer_tuple)

# AC Phase Parameter extraction
# -----------------------------
def getACphase_param(text):
	anwer_tuple = ''
	power = text[1].strip(' -Uac')
	voltage = text[2].strip(' -Iac')
	ampere = text[3].strip(' ')
	answer_tuple = (power, voltage, ampere)
	return (answer_tuple)


# get spot data from SMA inverter using SBFspot
# ---------------------------------------------
# run SBFspot to extract spot data from SMA inverter
def getDatafromSMAInverter():
	global smainv_ip
	global smainv_sunrise
	global smainv_sunset
	global smainv_serial_number
	global smainv_device_type
	global smainv_software_version
	global smainv_device_temperature
	global smainv_gridrelay_status
	global smainv_etoday
	global smainv_etotal
	global smainv_operation_time
	global smainv_feedin_time
	global smainv_gridfrequency
	global smainv_dcstring1
	global smainv_dcstring2
	global smainv_acphase1
	global smainv_acphase2
	global smainv_acphase3
	global smainv_totalPac
	global smainv_totalPdc
	global smainv_efficiency

# SBFspot -v2 -ad0 -am0 -finq -nosql -nocsv
	CMDstring = '/usr/local/bin/sbfspot.3/SBFspot -v2 -ad0 -am0 -finq -nosql -nocsv'
	out = subprocess.Popen(CMDstring,
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT)
	stdout, _ = out.communicate()
	smainv_raw = stdout.decode('utf-8')
	lines = stdout.decode('utf-8').split("\n")
	trimmedLines = []
	for currLine in lines:
		trimmedLine = currLine.lstrip().rstrip()
		trimmedLines.append(trimmedLine)
	print_line('Data obtained from SMA inverter via SPFspot:\n{}'.format(smainv_raw), debug=True)
	print_line('Data extraction:', debug=True)
	for currLine in trimmedLines:
		lineParts = currLine.split(':')
		currValue = ' '  # '{?unk?}'
		if len(lineParts) >= 2:
			currValue = lineParts[1].lstrip().rstrip()
		if 'IP address' in currLine:
			smainv_ip = currValue[:currValue.find(" ")]
			print_line('SMA Inverter IP address: {}'.format(smainv_ip), debug=True)
		if 'sunrise' in currLine:
			smainv_sunrise = str(currValue)+str(":")+str(lineParts[2].lstrip().rstrip())
			print_line("sunrise: {}".format(smainv_sunrise), debug=True)
		if 'sunset' in currLine:
			smainv_sunset = str(currValue)+str(":")+str(lineParts[2].lstrip().rstrip())
			print_line("sunset: {}".format(smainv_sunset), debug=True)
		if 'Serial number' in currLine:
			smainv_serial_number = currValue
			print_line("Inverter Serial Number: {}".format(smainv_serial_number), debug=True)
		if 'Device Type' in currLine:
			smainv_device_type = currValue
			print_line("Device Type: {}".format(smainv_device_type), debug=True)
		if 'Software Version' in currLine:
			smainv_software_version = currValue
			print_line("Sofware Version: {}".format(smainv_software_version), debug=True)
		if 'Device Temperature' in currLine:
			smainv_device_temperature = float(currValue.strip('°C'))
			print_line("Device Temperature: {:.1f}°C".format(smainv_device_temperature), debug=True)
		if 'GridRelay Status' in currLine:
			smainv_gridrelay_status = currValue
			print_line("Status GridRelay: {}".format(smainv_gridrelay_status), debug=True)
		if 'EToday' in currLine:
			smainv_etoday = float(currValue.strip('kWh'))
			print_line("Energy Production Today: {}kWh".format(smainv_etoday), debug=True)
		if 'ETotal' in currLine:
			smainv_etotal = float(currValue.strip('kWh'))
			print_line("Energy Production Total: {:.1f}kWh".format(smainv_etotal), debug=True)
		if 'Operation Time' in currLine:
			smainv_operation_time = currValue
			print_line("Operation Time: {}".format(smainv_operation_time), debug=True)
		if 'Feed-In Time' in currLine:
			smainv_feedin_time = currValue
			print_line("Feed-In Time: {}".format(smainv_feedin_time), debug=True)
		if 'Grid Freq.' in currLine:
			smainv_gridfrequency = float(currValue.strip('Hz'))
			print_line("Grid Frequency: {}Hz".format(smainv_gridfrequency), debug=True)
		if 'Total Pac' in currLine:
			smainv_totalPac = float(currValue.strip(' kW -Calculated Pac').lstrip().rstrip())
			print_line("Total Pac: {}kW".format(smainv_totalPac), debug=True)
		if 'Efficiency' in currLine:
			smainv_efficiency = float(currValue.strip('%'))
			print_line("Efficiency: {}%".format(smainv_efficiency), debug=True)
		if 'String 1' in currLine:
			smainv_dcstring1 = getDCstring_param(lineParts)
			print_line("DC String 1 Tuple: {}".format(smainv_dcstring1), debug=True)
		if 'String 2' in currLine:
			smainv_dcstring2 = getDCstring_param(lineParts)
			print_line("DC String 2 Tuple: {}".format(smainv_dcstring2), debug=True)
		if 'Calculated Total Pdc' in currLine:
			smainv_totalPdc = float(currValue.strip(' kW -Calculated Pdc').lstrip().rstrip())
			print_line("Calculated Total Pdc: {}kW".format(smainv_totalPdc), debug=True)
		if 'Phase 1' in currLine:
			smainv_acphase1 = getACphase_param(lineParts)
			print_line("AC Phase 1 Tuple: {}".format(smainv_acphase1), debug=True)
		if 'Phase 2' in currLine:
			smainv_acphase2 = getACphase_param(lineParts)
			print_line("AC Phase 2 Tuple: {}".format(smainv_acphase2), debug=True)
		if 'Phase 3' in currLine:
			smainv_acphase3 = getACphase_param(lineParts)
			print_line("AC Phase 3 Tuple: {}".format(smainv_acphase3), debug=True)

# get MAC address of speedwire adapter of SMA inverter
def getMACaddressfromSMAInverter():
	global smainv_ip
	global smainv_mac
	out = subprocess.Popen("arp "+ smainv_ip,
		shell=True,
		stdout=subprocess.PIPE,
		stderr=subprocess.STDOUT)
	stdout, _ = out.communicate()
	smainv_mac_raw = stdout.decode('utf-8').replace(" ","")
	print_line('Querry for MAC address: {}'.format(smainv_mac_raw), debug=True)
	pos = smainv_mac_raw.find("ether")
	smainv_mac = smainv_mac_raw[pos+5:pos+22].lower()
	print_line('MAC address SMA Inverter: {}'.format(smainv_mac), debug=True)

# ----------------------
# MQTT setup and startup
# ----------------------

# MQTT connection
lwt_topic = '{}/sensor/{}/status'.format(base_topic, sensor_name.lower())
lwt_online_val = 'online'
lwt_offline_val = 'offline'

print_line('Connecting to MQTT broker ...', verbose=True)
mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_connect
mqtt_client.on_publish = on_publish

mqtt_client.will_set(lwt_topic, payload=lwt_offline_val, retain=True)

if config['MQTT'].getboolean('tls', False):
	# According to the docs, setting PROTOCOL_SSLv23 "Elects the highest protocol version
	# that both the client and server support. Despite the name, this option can select
	# "TLS" protocols as well as "SSL"" - so this seems like a reasonable default
	mqtt_client.tls_set(
		ca_certs=config['MQTT'].get('tls_ca_cert', None),
		keyfile=config['MQTT'].get('tls_keyfile', None),
		certfile=config['MQTT'].get('tls_certfile', None),
		tls_version=ssl.PROTOCOL_SSLv23
	)

mqtt_username = os.environ.get("MQTT_USERNAME", config['MQTT'].get('username'))
mqtt_password = os.environ.get("MQTT_PASSWORD", config['MQTT'].get('password', None))

if mqtt_username:
	mqtt_client.username_pw_set(mqtt_username, mqtt_password)
try:
	mqtt_client.connect(os.environ.get('MQTT_HOSTNAME', config['MQTT'].get('hostname', 'localhost')),
		port=int(os.environ.get('MQTT_PORT', config['MQTT'].get('port', '1883'))),
		keepalive=config['MQTT'].getint('keepalive', 60))
except:
	print_line('MQTT connection error. Please check your settings in the configuation file "config.ini"', error=True, sd_notify=True)
	sys.exit(1)
else:
	mqtt_client.publish(lwt_topic, payload=lwt_online_val, retain=False)
	mqtt_client.loop_start()

	while mqtt_client_connected == False:    #wait in loop
		print_line('* Wait on mqtt_client_connected=[{}]'.format(mqtt_client_connected), debug=True)
		sleep(1.0)    #some slack to establish the connection

	startAliveTimer()

sd_notifier.notify('READY=1')


# ---------------------------------------
# Perform MQTT Discovery Announcement ...
# ---------------------------------------
#
# create uniqID using MAC address of speedwire adapter of SMA inverter
#
getDatafromSMAInverter()
getMACaddressfromSMAInverter()

mac_basic = smainv_mac.replace(":","")
mac_left = mac_basic[:6]
mac_right = mac_basic[6:]
print_line('mac lt= {}, rt= {}, mac= {}'.format(mac_left, mac_right, mac_basic), debug=True)
uniqID = "SMA-{}Inv{}".format(mac_left, mac_right)
print_line('uniqID: {}'.format(uniqID), debug=True)

# SMA Inverter Reporter device
LD_MONITOR = "monitor"
LD_SYS_TEMP = "temperature_inverter"
LD_ENERGY_TODAY = "energy_today"
LD_PWR_INVOUT = "acpower_inverter"
LD_PWR_INVIN = "dcpower_inverter"
LD_ETA_INV = "efficiency"
LD_GRID_FREQ = "grid_frequency"
LDS_PAYLOAD_NAME = "info"

# Publish SMA Inverter to MQTT for auto discovery
# Table of key items to publish:
detectorValues = OrderedDict([
	(LD_MONITOR, dict(title="SMA Inverter Monitor", device_class="timestamp", 
		no_title_prefix="yes", json_value="timestamp", json_attr="yes", 
		icon='mdi:solar-power', device_ident="SMA-INV-{}".format(smainv_serial_number))),
	(LD_SYS_TEMP, dict(title="SMA Inverter Temperature", device_class="temperature", 
		no_title_prefix="yes", unit="C", json_value="temperature_inverter", 
		icon='mdi:thermometer')),
	(LD_ENERGY_TODAY, dict(title="SMA Inverter Energy Today", no_title_prefix="yes", 
		json_value="energy_today", unit="kWh", icon='mdi:counter')),
	(LD_PWR_INVOUT, dict(title="SMA Inverter AC Power out", no_title_prefix="yes",
		json_value="acpower_inverter", unit="kW", icon='mdi:solar-power')),
	(LD_PWR_INVIN, dict(title="SMA Inverter DC Power in", no_title_prefix="yes",
		json_value="dcpower_inverter", unit="kW", icon='mdi:solar-power')),
	(LD_ETA_INV, dict(title="SMA Inverter Efficiency", no_title_prefix="yes",
		json_value="inverter_efficiency", unit="%", icon='mdi:solar-power')),
	(LD_GRID_FREQ, dict(title="Grid Frequency", no_title_prefix="yes",
		json_value="grid_frequency", unit="Hz", icon='mdi:transmission-tower')),
])

print_line('Announcing SMA Inverter Monitoring device to MQTT broker for auto-discovery ...')

base_topic = '{}/sensor/{}'.format(base_topic, sensor_name.lower())
values_topic_rel = '{}/{}'.format('~', LD_MONITOR)
values_topic = '{}/{}'.format(base_topic, LD_MONITOR)
activity_topic_rel = '{}/status'.format('~')
activity_topic = '{}/status'.format(base_topic)

command_topic_rel = '~/set'

print_line('base topic: {}'.format(base_topic), debug=True)
print_line('values topic rel: {}'.format(values_topic_rel), debug=True)
print_line('values topic: {}'.format(values_topic), debug=True)
print_line('activity topic rel: {}'.format(activity_topic_rel), debug=True)
print_line('activity topic: {}'.format(activity_topic), debug=True)

for [sensor, params] in detectorValues.items():
	discovery_topic = '{}/sensor/{}/{}/config'.format(discovery_prefix, sensor_name.lower(), sensor)
	print_line('discovery topic: {}'.format(discovery_topic), debug=True)
	payload = OrderedDict()
	if 'no_title_prefix' in params:
		payload['name'] = "{}".format(params['title'].title())
	else:
		payload['name'] = "{} {}".format(sensor_name.title(), params['title'].title())
	payload['uniq_id'] = "{}_{}".format(uniqID, sensor.lower())
	if 'device_class' in params:
		payload['dev_cla'] = params['device_class']
	if 'unit' in params:
		payload['unit_of_measurement'] = params['unit']
	if 'json_value' in params:
		payload['stat_t'] = values_topic_rel
		payload['val_tpl'] = "{{{{ value_json.{}.{} }}}}".format(LDS_PAYLOAD_NAME, params['json_value'])
	payload['~'] = base_topic
	payload['pl_avail'] = lwt_online_val
	payload['pl_not_avail'] = lwt_offline_val
	if 'icon' in params:
		payload['ic'] = params['icon']
	payload['avty_t'] = activity_topic_rel
	if 'json_attr' in params:
		payload['json_attr_t'] = values_topic_rel
		payload['json_attr_tpl'] = '{{{{ value_json.{} | tojson }}}}'.format(LDS_PAYLOAD_NAME)
	if 'device_ident' in params:
		payload['dev'] = {
			'identifiers' : ["{}".format(uniqID)],
			'manufacturer' : 'SMA Solar Technology AG',
			'name' : params['device_ident'],
			'model' : '{}'.format(smainv_device_type),
			'sw_version': '{}'.format(smainv_software_version)
		}
	else:
		payload['dev'] = {
			'identifiers' : ["{}".format(uniqID)]
		}

	print_line("payload: {}".format(payload), debug=True)
	mqtt_client.publish(discovery_topic, json.dumps(payload), 1, retain=True)

# -----------------------------------------
# timer and timer funcs for period handling
# -----------------------------------------

TIMER_INTERRUPT = (-1)
TEST_INTERRUPT = (-2)

def periodTimeoutHandler():
	print_line("- PERIOD TIMER INTERRUPT -", debug=True)
	handle_interrupt(TIMER_INTERRUPT)
	startPeriodTimer()

def startPeriodTimer():
	global endPeriodTimer
	global periodTimeRunningStatus
	stopPeriodTimer()
	endPeriodTimer = threading.Timer(interval_in_minutes * 60.0, periodTimeoutHandler)
	endPeriodTimer.start()
	periodTimeRunningStatus = True
	print_line("- started PERIOD timer - every {} seconds".format(interval_in_minutes * 60.0), debug=True)

def stopPeriodTimer():
	global endPeriodTimer
	global periodTimeRunningStatus
	endPeriodTimer.cancel()
	periodTimeRunningStatus = False
	print_line("- stopped PERIOD timer", debug=True)

def isPeriodTimerRunning():
	global periodTimeRunningStatus
	return periodTimeRunningStatus

# Timer
endPeriodTimer = threading.Timer(interval_in_minutes * 60.0, periodTimeoutHandler)
# BOOL tracking state of TIMER
periodTimeRunningStatus = False
reported_first_time = False

# -----------------------------
# MQTT Transmit Helper Routines
# -----------------------------
SCRIPT_TIMESTAMP = "timestamp"
SMAINV_MODEL = "smainv_device_type"
SMAINV_SYSTEM_TEMP = "temperature_inverter"
SMAINV_SOFTWARE_VERSION = "firmware"
SMAINV_SCRIPT = "reporter"
SCRIPT_REPORT_INTERVAL = "report_interval"
SMAINV_ENERGY_TODAY = "energy_today"
SMAINV_ENERGY_TOTAL = "energy_total"
SMAINV_SUNRISE = "sunrise"
SMAINV_SUNSET = "sunset"
SMAINV_FEEDIN = "grid_feedin_time"
SMAINV_OPERATING_TIME = "inverter_operating_time"
SMAINV_GRID_FREQ = "grid_frequency"
SMAINV_PWR_INVOUT = "acpower_inverter"
SMAINV_PWR_INVIN = "dcpower_inverter"
SMAINV_EFFICIENCY = "inverter_efficiency"
SMAINV_DCSTRING1 = "dcstring1"
SMAINV_DCSTRING2 = "dcstring2"
SMAINV_ACPHASE1 = "acphase1"
SMAINV_ACPHASE2 = "acphase2"
SMAINV_ACPHASE3 = "acphase3"
SMAINV_IP = "inverter_ip"
SMAINV_MAC = "inverter_mac"

def send_status(timestamp, nothing):
	smainvData = OrderedDict()
	smainvData[SCRIPT_TIMESTAMP] = timestamp.astimezone().replace(microsecond=0).isoformat()
	smainvData[SMAINV_MODEL] = smainv_device_type
	smainvData[SMAINV_SUNRISE] = smainv_sunrise
	smainvData[SMAINV_SUNSET] = smainv_sunset
	smainvData[SMAINV_SYSTEM_TEMP] = smainv_device_temperature
	smainvData[SMAINV_ENERGY_TODAY] = float('{:.1f}'.format(smainv_etoday))
	smainvData[SMAINV_ENERGY_TOTAL] = float('{:.0f}'.format(smainv_etotal))
	smainvData[SMAINV_PWR_INVOUT] = float('{:.2f}'.format(smainv_totalPac))
	smainvData[SMAINV_PWR_INVIN] = float('{:.2f}'.format(smainv_totalPdc))
	smainvData[SMAINV_FEEDIN] = smainv_feedin_time
	smainvData[SMAINV_OPERATING_TIME] = smainv_operation_time
	smainvData[SMAINV_GRID_FREQ] = smainv_gridfrequency
	smainvData[SMAINV_EFFICIENCY] = float('{:.1f}'.format(smainv_efficiency))
	smainvData[SMAINV_DCSTRING1] = smainv_dcstring1
	smainvData[SMAINV_DCSTRING2] = smainv_dcstring2
	smainvData[SMAINV_ACPHASE1] = smainv_acphase1
	smainvData[SMAINV_ACPHASE2] = smainv_acphase2
	smainvData[SMAINV_ACPHASE3] = smainv_acphase3
	smainvData[SMAINV_IP] = smainv_ip
	smainvData[SMAINV_MAC] = smainv_mac
	smainvData[SMAINV_SOFTWARE_VERSION] = smainv_software_version

	smainvData[SMAINV_SCRIPT] = smainv_mqtt_script.replace('.py','')
	smainvData[SCRIPT_REPORT_INTERVAL] = interval_in_minutes

	smainvTopDict = OrderedDict()
	smainvTopDict[LDS_PAYLOAD_NAME] = smainvData
	print_line("smainvTopDict: {}".format(smainvTopDict), debug=True)
	print_line("Topic: {}".format(values_topic), debug=True)

	_thread.start_new_thread(publishMonitorData, (smainvTopDict, values_topic))

def publishMonitorData(latestData, topic):
	print_line('Publishing to MQTT topic "{}, Data:{}"'.format(topic, json.dumps(latestData)))
	mqtt_client.publish('{}'.format(topic), json.dumps(latestData), 1, retain=False)
	sleep(0.5)

def update_values():
	getDatafromSMAInverter()


# -----------------
# Interrupt handler
# -----------------
def handle_interrupt(channel):
	global reported_first_time
	sourceID = "<< INTR(" + str(channel) + ")"
	current_timestamp = datetime.now(local_tz)
	print_line(sourceID + " >> Time to report! (%s)" % current_timestamp.strftime('%H:%M:%S - %Y/%m/%d'), verbose=True)

	# have PERIOD interrupt!
	update_values()

	if (opt_stall == False or reported_first_time == False and opt_stall == True):
		_thread.start_new_thread(send_status, (current_timestamp, ''))
		reported_first_time = True
	else:
		print_line(sourceID + " >> Time to report! (%s) but SKIPPED (TEST: stall)" % current_timestamp.strftime('%H:%M:%S - %Y/%m/%d'), verbose=True)

def afterMQTTConnect():
	print_line('* afterMQTTConnect()', verbose=True)
	# Note: this is run after MQTT connects
	# start interval timer
	startPeriodTimer()
	# do first report
	handle_interrupt(0)

afterMQTTConnect()
try:
	while True:
		sleep(10000)

finally:
	stopPeriodTimer()
	stopAliveTimer()
	f.close()

