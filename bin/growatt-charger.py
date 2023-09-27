#!/usr/local/bin/python3

import growattServer
from forecast_solar import ForecastSolar, ForecastSolarRatelimit

import asyncio
import configparser
import logging
import math
import requests
import random
import string
import time
import urllib.parse
import shutil, sys, os

from datetime import datetime,date,timedelta
from statistics import mean

def get_lat_long(address):
  url = 'https://nominatim.openstreetmap.org/search?q=' + urllib.parse.quote(address) +'&format=json'
  response = requests.get(url).json()
  return response[0]

async def get_fake_generation_forecast(solar_forecast_config, off_peak_start_time, off_peak_end_time, logger):
  logger.info("*****USING FAKE DATA*****")

  return {
    datetime.strptime('2022-06-28 04:54:00', '%Y-%m-%d %H:%M:%S'): 0,
    datetime.strptime('2022-06-28 05:00:00', '%Y-%m-%d %H:%M:%S'): 2,
    datetime.strptime('2022-06-28 06:00:00', '%Y-%m-%d %H:%M:%S'): 81,
    datetime.strptime('2022-06-28 07:00:00', '%Y-%m-%d %H:%M:%S'): 245,
    datetime.strptime('2022-06-28 08:00:00', '%Y-%m-%d %H:%M:%S'): 577,
    datetime.strptime('2022-06-28 09:00:00', '%Y-%m-%d %H:%M:%S'): 1016,
    datetime.strptime('2022-06-28 10:00:00', '%Y-%m-%d %H:%M:%S'): 1474,
    datetime.strptime('2022-06-28 11:00:00', '%Y-%m-%d %H:%M:%S'): 2039,
    datetime.strptime('2022-06-28 12:00:00', '%Y-%m-%d %H:%M:%S'): 2774,
    datetime.strptime('2022-06-28 13:00:00', '%Y-%m-%d %H:%M:%S'): 3489,
    datetime.strptime('2022-06-28 14:00:00', '%Y-%m-%d %H:%M:%S'): 3939,
    datetime.strptime('2022-06-28 15:00:00', '%Y-%m-%d %H:%M:%S'): 4066,
    datetime.strptime('2022-06-28 16:00:00', '%Y-%m-%d %H:%M:%S'): 3788,
    datetime.strptime('2022-06-28 17:00:00', '%Y-%m-%d %H:%M:%S'): 3082,
    datetime.strptime('2022-06-28 18:00:00', '%Y-%m-%d %H:%M:%S'): 2147,
    datetime.strptime('2022-06-28 19:00:00', '%Y-%m-%d %H:%M:%S'): 1221,
    datetime.strptime('2022-06-28 20:00:00', '%Y-%m-%d %H:%M:%S'): 551,
    datetime.strptime('2022-06-28 21:00:00', '%Y-%m-%d %H:%M:%S'): 191,
    datetime.strptime('2022-06-28 21:05:00', '%Y-%m-%d %H:%M:%S'): 3,
  }

async def get_generation_forecast(solar_forecast_config, off_peak_start_time, off_peak_end_time, logger):
  lat_long = get_lat_long(solar_forecast_config.get('location'))

  async with ForecastSolar(latitude=lat_long["lat"],
                     longitude=lat_long["lon"],
                     declination=solar_forecast_config.get('declination'),
                     azimuth=solar_forecast_config.get('azimuth'),
                     kwp=solar_forecast_config.get('kw_power'),
                     damping=solar_forecast_config.get('damping')) as forecast:
    try:
      estimate = await forecast.estimate()
    except ForecastSolarRatelimit as err:
      logger.error("Ratelimit reached")
      logger.error(f"Rate limit resets at {err.reset_at}")
      reset_period = err.reset_at - datetime.now(timezone.utc)
      # Strip microseconds as they are not informative
      reset_period -= timedelta(microseconds=reset_period.microseconds)
      logger.error.append(f"That's in {reset_period}")
      return None

    now = datetime.now()

    off_peak_start_splits = off_peak_start_time.split(":")
    today_off_peak_start = now.replace(hour=int(off_peak_start_splits[0]), minute=int(off_peak_start_splits[1]), second=0, microsecond=0)

    date_for_forecast = date.today()
    logger.info("Now: %s, Off-peak-start: %s" %(now, today_off_peak_start))
    if now > today_off_peak_start:
      date_for_forecast = date_for_forecast + timedelta(days = 1)

    logger.info("Date for forecast: %s" % (date_for_forecast))

    confidence_factor = float(solar_forecast_config.get('confidence'))
    wh_hours_forecast = {}
    for hour, forecast in estimate.wh_hours.items():
      if hour.date() == date_for_forecast:
        wh_hours_forecast[hour] = forecast * confidence_factor

    return wh_hours_forecast

def get_grid_neutral_time(average_load_w, generation_forecast):
  for hour, forecast in generation_forecast.items():
    if int(forecast) > average_load_w:
      return hour

  return None # Default to None i.e. we're never grid netural

def get_grid_neutral_wh(grid_neutral_time, today_off_peak_end, average_load_w, logger, output_string):
  duration_on_battery = datetime.combine(date.min, grid_neutral_time.time()) - datetime.combine(date.min, today_off_peak_end.time())
  hours = duration_on_battery.seconds/3600
  wh_required = hours * average_load_w

  log_and_output(logger, output_string, "Grid Neutral Time: %s" % (grid_neutral_time))
  logger.info("%swH required to get to grid neutral (%s hours @ %sw)" %(wh_required, hours, average_load_w))

  return wh_required

def get_surplus_generation_for_battery(generation_forecast, average_load_w, maximum_charge_rate_w, logger, output_string):
  total_generation = 0
  surplus_generation_for_battery = 0
  for forecast in generation_forecast.values():
    total_generation += forecast
    if forecast > average_load_w:
      hour_to_battery = forecast - average_load_w
      if hour_to_battery > maximum_charge_rate_w:
        hour_to_battery = maximum_charge_rate_w
      surplus_generation_for_battery += hour_to_battery

  log_and_output(logger, output_string, "Total generation: %.2fwH" % (total_generation), True)
  logger.info("Surplus Generation for Battery: %.2fwH" % (surplus_generation_for_battery))
  return surplus_generation_for_battery

def convert_wh_to_battery_pct(amount_to_charge_inc_soc, battery_capacity_wh):
  return int(math.ceil((amount_to_charge_inc_soc / battery_capacity_wh) * 100))

def growatt_get_device_info(growatt_api, login_response):
  plant_list = growatt_api.plant_list(login_response['user']['id'])
  plant_id = plant_list['data'][0]['plantId']
  plant_info = growatt_api.plant_info(plant_id)
  device_sn = plant_info['deviceList'][0]['deviceSn']

  return {'plant_id': plant_id, 'device_sn': device_sn}

def get_current_charge(growatt_api, plant_id, device_sn, logger):
  mix_status = growatt_api.mix_system_status(device_sn, plant_id)
  logger.info("SOC for Plant - %s, Device - %s: %s%%" % (plant_id,device_sn,mix_status['SOC']))
  soc_pct = float(mix_status['SOC'])
  return soc_pct

def get_offpeak_duration(off_peak_start_time, off_peak_end_time):
  datetime_start = datetime.strptime(off_peak_start_time, '%H:%M')
  datetime_end = datetime.strptime(off_peak_end_time, '%H:%M')
  if datetime_start > datetime_end:
    raise ValueError("Off-peak start time is after Off-peak end time")
  time_delta=datetime_end-datetime_start
  return time_delta.seconds/3600 #Convert diff into minutes

def set_growatt_datetime(growatt_api, gw_device_sn, logger, output_string):
  now = datetime.now()
  dt_string = now.strftime("%Y-%m-%d %H:%M:%S")
  time_settings={
    'param1': dt_string
  }
  log_and_output(logger, output_string, "Setting inverter time to: %s" %(dt_string))
  response = growatt_api.update_mix_inverter_setting(gw_device_sn, 'pf_sys_year', time_settings)

  resp_string = "Unsuccessful"
  if response['success'] == True:
    resp_string = "Successful"
  log_and_output(logger, output_string, "   - %s" %(resp_string), True)

def configure_charge_settings(growatt_api, gw_device_sn, off_peak_start_time, off_peak_end_time, charge_rate_as_pct, target_charge, logger, output_string):
  log_and_output(logger, output_string, "Configuring Charger - Device: %s, Charge-Start: %s, Charge-End: %s, Charge Rate(%%): %s, Target Charge(%%): %s" % 
          (gw_device_sn, off_peak_start_time, off_peak_end_time, int(charge_rate_as_pct), int(target_charge)))

  off_peak_start_splits = off_peak_start_time.split(":")
  off_peak_end_splits = off_peak_end_time.split(":")

  schedule_settings = [ str(int(charge_rate_as_pct)), #Charging power %
                        str(int(target_charge)), #Target charge (SoC) %
                        "1", #Allow AC charging
                        off_peak_start_splits[0], off_peak_start_splits[1], #Schedule 1 - Start time (HH, MM)
                        off_peak_end_splits[0], off_peak_end_splits[1], #Schedule 1 - End time (HH, MM)
                        "1", #Schedule 1 - Enabled
                        "00","00","00","00","0", #Schedule 2 - Disabled
                        "00","00","00","00","0"] #Schedule 3 - Disabled

  response = growatt_api.update_mix_inverter_setting(gw_device_sn, 'mix_ac_charge_time_period', schedule_settings)

  resp_string = "Unsuccessful"
  if response['success'] == True:
    resp_string = "Successful"
  log_and_output(logger, output_string, "   - %s" %(resp_string), True)

def log_and_output(logger, output_string, print_string, new_line_after = False):
  logger.info(print_string)
  output_string.append(print_string)
  if new_line_after:
    output_string.append("")

def exit_printing(output_string):
  now = datetime.now()
  dt_string = now.strftime("%Y-%m-%d-%H:%M:%S")

  filename="latest.txt"
  with open('/opt/growatt-charger/output/latest.txt', 'w') as f:
    for line in output_string:
      f.write(line)
      f.write("\n")

async def main():
  #MAIN LOGIC STARTS HERE
  logger = logging.getLogger("growatt-charger")
  logger.setLevel(logging.INFO)
  
  formatter = logging.Formatter("%(asctime)s %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S")

  sh = logging.StreamHandler()
  sh.setFormatter(formatter)
  logger.addHandler(sh)

  fh = logging.FileHandler("/opt/growatt-charger/logs/growatt-charger.log", )
  fh.setFormatter(formatter)
  logger.addHandler(fh)

  config = configparser.ConfigParser()

  output_string = []

  #Use the config file, if it doesn't exist, copy the default one into the conf directory
  config_file="/opt/growatt-charger/conf/growatt-charger.ini"
  if not os.path.exists(config_file):
    logger.error("Config file does not exist in /opt/growatt-charger/conf/, copying the default one to be populated by the user")
    shutil.copyfile("/opt/growatt-charger/defaults/growatt-charger-default.ini", config_file)
    sys.exit(0)

  #Parse the configuration   
  config.read(config_file)
  growatt_config = config['growatt']
  soc_chg_pct = int(growatt_config.get("statement_of_charge_pct"))
  min_chg_pct = int(growatt_config.get("minimum_charge_pct"))
  max_chg_pct = int(growatt_config.get("maximum_charge_pct"))
  battery_capacity_wh = int(growatt_config.get("battery_capacity_wh"))
  maximum_charge_rate_w = int(growatt_config.get("maximum_charge_rate_w"))
  average_load_w = int(growatt_config.get("average_load_w"))

  #Parsing username & password for growatt
  gw_username = growatt_config.get("username", "")
  if gw_username == "":
    gw_username = os.getenv('GROWATT_USERNAME')

  gw_password = growatt_config.get("password", "")
  if gw_password == "":
    gw_password = os.getenv('GROWATT_PASSWORD')

  if gw_username == "" or gw_username == None:
    logger.error("No growatt username provided either use the 'username' parameter in the config file or expose the GROWATT_USERNAME environment variable")
    sys.exit(1)

  if gw_password == "" or gw_password == None:
    logger.error("No growatt password provided either use the 'password' parameter in the config file or expose the GROWATT_PASSWORD environment variable")
    sys.exit(1)

  gw_plant_id = growatt_config.get("plant_id", "")
  gw_device_sn = growatt_config.get("device_sn", "")

  tariff_config = config['tariff']
  off_peak_start_time = tariff_config.get("off_peak_start_time")
  off_peak_end_time = tariff_config.get("off_peak_end_time")

  solar_forecast_config = config['forecast.solar']

  generation_forecast = None

  attempts = 0
  max_attempts = 3
  success = False

  while attempts < max_attempts and success == False:
    try:
      rand_num = random.randint(1,50)
      rand_string = ''.join(random.choices(string.ascii_uppercase + string.digits, k=rand_num))
      growatt_api = growattServer.GrowattApi(agent_identifier=rand_string)
      growatt_api.server_url = "https://server.growatt.com/"
      gw_login_response = growatt_api.login(gw_username, gw_password)
      if gw_login_response['success'] != True:
        logger.error("Unable to login to Growatt, aborting")
        sys.exit(1)

      now = datetime.now()
      off_peak_start_splits = off_peak_start_time.split(":")
      off_peak_end_splits = off_peak_end_time.split(":")
      today_off_peak_start = now.replace(hour=int(off_peak_start_splits[0]), minute=int(off_peak_start_splits[1]), second=0, microsecond=0)
      today_off_peak_end = now.replace(hour=int(off_peak_end_splits[0]), minute=int(off_peak_end_splits[1]), second=0, microsecond=0)

      log_and_output(logger, output_string, "Configuration time: %s" %(datetime.now().strftime("%Y-%m-%d %H:%M:%S")), True)

      if now < today_off_peak_start or now > today_off_peak_end:
        #Only get the generation forecast if we don't already have it - saves us hitting our quota of queries per hour
        if generation_forecast == None:
         generation_forecast = await get_generation_forecast(solar_forecast_config, off_peak_start_time, off_peak_end_time, logger)
         #generation_forecast = await get_fake_generation_forecast(solar_forecast_config, off_peak_start_time, off_peak_end_time, logger)

        #If grid_neutral_time is None then we're never grid-neutral or if we don't receive a forecast we should assume the worst, defaulting to max percentage
        target_charge = max_chg_pct

        #If we don't get a forecast we can't predict a grid-neutral time
        if generation_forecast != None:
          grid_neutral_time = get_grid_neutral_time(average_load_w, generation_forecast)
          if grid_neutral_time != None:
            wh_required_to_make_grid_neutral = get_grid_neutral_wh(grid_neutral_time, today_off_peak_end, average_load_w, logger, output_string)

            wh_surplus_generation = get_surplus_generation_for_battery(generation_forecast, average_load_w, maximum_charge_rate_w, logger, output_string)

            #If we don't generate enough to make it to full, get the excess during the off-peak window
            extra_wh_needed = 0
            if wh_surplus_generation < battery_capacity_wh:
              extra_wh_needed = battery_capacity_wh - wh_surplus_generation
            logger.info("Extra to draw from grid to ensure full by end-of-day: %.2fwH" % (extra_wh_needed))

            wh_to_draw_during_off_peak = wh_required_to_make_grid_neutral + extra_wh_needed
            logger.info("wH to draw during off-peak: %.2fwH" % (wh_to_draw_during_off_peak))

            soc_as_wh = (soc_chg_pct/100) * battery_capacity_wh
            logger.info("Baseline SoC as wH: %.2f" %(soc_as_wh))

            amount_to_charge_inc_soc = wh_to_draw_during_off_peak + soc_as_wh
            logger.info("Amount to charge including SoC: %.2f" % (amount_to_charge_inc_soc))

            target_charge = convert_wh_to_battery_pct(amount_to_charge_inc_soc, battery_capacity_wh)
            logger.info("Pct to charge to: %s" % (target_charge))
          else:
            log_and_output(logger, output_string, "Never grid neutral - setting target charge to 100%")
        else:
          log_and_output(logger, output_string, "No forecast received - setting target charge to 100%")

        if target_charge < min_chg_pct:
          target_charge = min_chg_pct
        if target_charge > max_chg_pct:
          target_charge = max_chg_pct
        logger.info("Refactored pct to charge to based on min/max allowed: %s" % (target_charge))

        #These are optional, so if they're not provided we get them ourselves
        if gw_plant_id == "" and gw_device_sn == "":
          gw_device_info = growatt_get_device_info(growatt_api, gw_login_response)
          gw_plant_id = gw_device_info['plant_id']
          gw_device_sn = gw_device_info['device_sn']

        current_charge = get_current_charge(growatt_api, gw_plant_id, gw_device_sn, logger)
        pct_growth = target_charge - current_charge
        log_and_output(logger, output_string, "Current SoC: %s%%, Target SoC: %s%%, Growth: %s" % (current_charge, target_charge, pct_growth), True)

        if pct_growth < 0:
          pct_growth = 0

        growth_as_wh = battery_capacity_wh * (pct_growth/100.0)
        logger.info("Growth as wh: %.2f" %(growth_as_wh))

        off_peak_duration_hours = get_offpeak_duration(off_peak_start_time, off_peak_end_time)

        charge_rate_in_w = growth_as_wh / off_peak_duration_hours
        logger.info("Charge rate in w: %.2f" % (charge_rate_in_w))

        charge_rate_as_pct = 0
        if charge_rate_in_w > 0:
          charge_rate_as_pct = (charge_rate_in_w / maximum_charge_rate_w)*100.0

        #The percentage charge rate seems to be a bit lower than desired, so we manually tweak it to be a couple of % higher
        #(We have to catch the case where we set it to be too high as well)
        charge_rate_as_pct = charge_rate_as_pct + 2.0
        if charge_rate_as_pct > 100:
          charge_rate_as_pct = 100.00

        logger.info("Charge rate as %%: %.2f" % (charge_rate_as_pct))

        set_growatt_datetime(growatt_api, gw_device_sn, logger, output_string)

        #Returns false if the charge rate is below 5%
        if charge_rate_as_pct < 5:
          """This is a fail-safe, if we're unable to contact the server between this low rate of charge
             and the off-peak rate then we should set to 100% charge as we're likely to have used battery
             between now and then. If, the battery already has it's required charge then it won't add anything
             therefore, this rate is irrelevant.
          """
          charge_rate_as_pct = 100

        configure_charge_settings(growatt_api, gw_device_sn, off_peak_start_time, off_peak_end_time, charge_rate_as_pct, target_charge, logger, output_string)
      else:
        log_and_output(logger, output_string, "Within off-peak window - no reconfiguration to happen")
      success = True

    except Exception as e:
      logger.error("Caught an exception, oh dear....")
      logger.error("Please check your configuration values also e.g. you've updated the default values")
      logger.error(str(e))
      output_string.append(str(e))
      if attempts < max_attempts-1:
        logger.info("Sleeping for 10 seconds before retry")
        output_string = []
        time.sleep(10)
      else:
        logger.info("Max attempts reached, giving up")

    attempts += 1
    logger.info("")

  exit_printing(output_string)

  if success:
    sys.exit(0)
  else:
    sys.exit(1)


asyncio.run(main())

