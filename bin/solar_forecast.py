#!/usr/local/bin/python3

import growattServer
import configparser
from datetime import datetime,date,timedelta
from statistics import mean
import math

import time

import requests
import urllib.parse
import asyncio
from forecast_solar import ForecastSolar, ForecastSolarRatelimit

import shutil, sys, os

def get_lat_long(address):
  url = 'https://nominatim.openstreetmap.org/search/' + urllib.parse.quote(address) +'?format=json'
  response = requests.get(url).json()
  return response[0]

async def get_fake_generation_forecast(solar_forecast_config, off_peak_start_time, off_peak_end_time, output_string):
  output_string.append("*****USING FAKE DATA*****")

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

async def get_generation_forecast(solar_forecast_config, off_peak_start_time, off_peak_end_time, output_string):
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
      output_string.append("Ratelimit reached")
      output_string.append(f"Rate limit resets at {err.reset_at}")
      reset_period = err.reset_at - datetime.now(timezone.utc)
      # Strip microseconds as they are not informative
      reset_period -= timedelta(microseconds=reset_period.microseconds)
      output_string.append(f"That's in {reset_period}")
      return None

    now = datetime.now()

    off_peak_start_splits = off_peak_start_time.split(":")
    today_off_peak_start = now.replace(hour=int(off_peak_start_splits[0]), minute=int(off_peak_start_splits[1]), second=0, microsecond=0)

    date_for_forecast = date.today()
    output_string.append("Now: %s, Off-peak-start: %s" %(now, today_off_peak_start))
    if now > today_off_peak_start:
      date_for_forecast = date_for_forecast + timedelta(days = 1)

    output_string.append("Date for forecast: %s" % (date_for_forecast))

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

def get_grid_neutral_wh(grid_neutral_time, today_off_peak_end, average_load_w, output_string):
  duration_on_battery = datetime.combine(date.min, grid_neutral_time.time()) - datetime.combine(date.min, today_off_peak_end.time())
  hours = duration_on_battery.seconds/3600
  wh_required = hours * average_load_w

  output_string.append("Grid Neutral Time: %s" % (grid_neutral_time))
  output_string.append("%swH required to get to grid neutral (%s hours @ %sw)" %(wh_required, hours, average_load_w))
  output_string.append("")

  return wh_required

def get_surplus_generation_for_battery(generation_forecast, average_load_w, maximum_charge_rate_w, output_string):
  total_generation = 0
  surplus_generation_for_battery = 0
  for forecast in generation_forecast.values():
    total_generation += forecast
    if forecast > average_load_w:
      hour_to_battery = forecast - average_load_w
      if hour_to_battery > maximum_charge_rate_w:
        hour_to_battery = maximum_charge_rate_w
      surplus_generation_for_battery += hour_to_battery

  output_string.append("Total generation: %.2fwH" % (total_generation))
  output_string.append("Surplus Generation for Battery: %.2fwH" % (surplus_generation_for_battery))
  output_string.append("")
  return surplus_generation_for_battery

def convert_wh_to_battery_pct(amount_to_charge_inc_soc, battery_capacity_wh):
  return int(math.ceil((amount_to_charge_inc_soc / battery_capacity_wh) * 100))

def growatt_get_device_info(growatt_api, login_response):
  plant_list = growatt_api.plant_list(login_response['user']['id'])
  plant_id = plant_list['data'][0]['plantId']
  plant_info = growatt_api.plant_info(plant_id)
  device_sn = plant_info['deviceList'][0]['deviceSn']

  return {'plant_id': plant_id, 'device_sn': device_sn}

def get_current_charge(growatt_api, plant_id, device_sn, output_string):
  mix_status = growatt_api.mix_system_status(device_sn, plant_id)
  output_string.append("SOC for Plant: %s, Device: %s - %s%%" % (plant_id,device_sn,mix_status['SOC']))
  soc_pct = float(mix_status['SOC'])
  return soc_pct

def get_offpeak_duration(off_peak_start_time, off_peak_end_time):
  datetime_start = datetime.strptime(off_peak_start_time, '%H:%M')
  datetime_end = datetime.strptime(off_peak_end_time, '%H:%M')
  if datetime_start > datetime_end:
    raise ValueError("Off-peak start time is after Off-peak end time")
  time_delta=datetime_end-datetime_start
  return time_delta.seconds/3600 #Convert diff into minutes

def set_growatt_datetime(growatt_api, gw_device_sn, output_string):
  now = datetime.now()
  dt_string = now.strftime("%Y-%m-%d %H:%M:%S")
  time_settings={
    'param1': dt_string
  }
  output_string.append("Setting inverter time to: %s" %(dt_string))
  response = growatt_api.update_mix_inverter_setting(gw_device_sn, 'pf_sys_year', time_settings)

  resp_string = "Unsuccessful"
  if response['success'] == True:
    resp_string = "Successful"
  output_string.append("Growatt response to setting time: %s" %(resp_string))

def configure_charge_settings(growatt_api, gw_device_sn, off_peak_start_time, off_peak_end_time, charge_rate_as_pct, target_charge, output_string):
  output_string.append("Configuring Charger - Device: %s, Charge-Start: %s, Charge-End: %s, Charge Rate(%%): %s, Target Charge(%%): %s" % 
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
  output_string.append("Growatt response to setting charge settings: %s" %(resp_string))

def exit_printing(output_string, errored=False):
  now = datetime.now()
  dt_string = now.strftime("%Y-%m-%d-%H:%M:%S")

  with open('/opt/growatt-charger/output/'+dt_string+'.txt', 'w') as f:
    for line in output_string:
      f.write(line)
      f.write("\n")

  filename="latest.txt"
  if errored:
    filename = "error.txt"

  with open('/opt/growatt-charger/output/' + filename, 'w') as f:
    for line in output_string:
      print(line)
      f.write(line)
      f.write("\n")

async def main():
  #MAIN LOGIC STARTS HERE
  config = configparser.ConfigParser()

  output_string = []

  #Use the config file, if it doesn't exist, copy the default one into the conf directory
  config_file="/opt/growatt-charger/conf/growatt-charger.ini"
  if not os.path.exists(config_file):
    output_string += "Config file does not exist in /opt/growatt-charger/conf/, copying the default one to be populated by the user"
    shutil.copyfile("/opt/growatt-charger/defaults/growatt-charger-default.ini", config_file)
    exit_printing(output_string)
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
    output_string += "No growatt username provided either use the 'username' parameter in the config file or expose the GROWATT_USERNAME environment variable"
    exit_printing(output_string)
    sys.exit(1)

  if gw_password == "" or gw_password == None:
    output_string +=  "No growatt password provided either use the 'password' parameter in the config file or expose the GROWATT_PASSWORD environment variable"
    exit_printing(output_string)
    sys.exit(1)

  gw_plant_id = growatt_config.get("plant_id", "")
  gw_device_sn = growatt_config.get("device_sn", "")

  tariff_config = config['tariff']
  off_peak_start_time = tariff_config.get("off_peak_start_time")
  off_peak_end_time = tariff_config.get("off_peak_end_time")

  solar_forecast_config = config['forecast.solar']

  attempts = 0
  max_attempts = 3
  success = False

  while attempts < max_attempts and success == False:
    try:
      growatt_api = growattServer.GrowattApi()
      gw_login_response = growatt_api.login(gw_username, gw_password)
      if gw_login_response['success'] != True:
        output_string +=  "Unable to login to Growatt, aborting"
        exit_printing(output_string)
        sys.exit(1)

      now = datetime.now()
      off_peak_start_splits = off_peak_start_time.split(":")
      off_peak_end_splits = off_peak_end_time.split(":")
      today_off_peak_start = now.replace(hour=int(off_peak_start_splits[0]), minute=int(off_peak_start_splits[1]), second=0, microsecond=0)
      today_off_peak_end = now.replace(hour=int(off_peak_end_splits[0]), minute=int(off_peak_end_splits[1]), second=0, microsecond=0)

      output_string.append("Time is %s, attempting to configure charging..." %(datetime.now()))
      output_string.append("")

      if now < today_off_peak_start or now > today_off_peak_end:
        generation_forecast = await get_generation_forecast(solar_forecast_config, off_peak_start_time, off_peak_end_time, output_string)
        #generation_forecast = await get_fake_generation_forecast(solar_forecast_config, off_peak_start_time, off_peak_end_time, output_string)

        #If grid_neutral_time is None then we're never grid-neutral or if we don't receive a forecast we should assume the worst, defaulting to max percentage
        target_charge = max_chg_pct

        #If we don't get a forecast we can't predict a grid-neutral time
        if generation_forecast != None:
          grid_neutral_time = get_grid_neutral_time(average_load_w, generation_forecast)
          if grid_neutral_time != None:
            wh_required_to_make_grid_neutral = get_grid_neutral_wh(grid_neutral_time, today_off_peak_end, average_load_w, output_string)

            wh_surplus_generation = get_surplus_generation_for_battery(generation_forecast, average_load_w, maximum_charge_rate_w, output_string)

            #If we don't generate enough to make it to full, get the excess during the off-peak window
            extra_wh_needed = 0
            if wh_surplus_generation < battery_capacity_wh:
              extra_wh_needed = battery_capacity_wh - wh_surplus_generation
            output_string.append("Extra to draw from grid to ensure full by end-of-day: %.2fwH" % (extra_wh_needed))

            wh_to_draw_during_off_peak = wh_required_to_make_grid_neutral + extra_wh_needed
            output_string.append("wH to draw during off-peak: %.2fwH" % (wh_to_draw_during_off_peak))

            soc_as_wh = (soc_chg_pct/100) * battery_capacity_wh
            output_string.append("SOC as wH: %s" %(soc_as_wh))

            amount_to_charge_inc_soc = wh_to_draw_during_off_peak + soc_as_wh
            output_string.append("Amount to charge including SoC: %.2f" % (amount_to_charge_inc_soc))
            output_string.append("")

            target_charge = convert_wh_to_battery_pct(amount_to_charge_inc_soc, battery_capacity_wh)
            output_string.append("Pct to charge to: %s" % (target_charge))
          else:
            output_string.append("Never grid neutral - setting target charge to 100%")
        else:
          output_string.append("No forecast received - setting target charge to 100%")

        if target_charge < min_chg_pct:
          target_charge = min_chg_pct
        if target_charge > max_chg_pct:
          target_charge = max_chg_pct
        output_string.append("Refactored pct to charge to based on min/max allowed: %s" % (target_charge))
        output_string.append("")

        #These are optional, so if they're not provided we get them ourselves
        if gw_plant_id == "" and gw_device_sn == "":
          gw_device_info = growatt_get_device_info(growatt_api, gw_login_response)
          gw_plant_id = gw_device_info['plant_id']
          gw_device_sn = gw_device_info['device_sn']

        current_charge = get_current_charge(growatt_api, gw_plant_id, gw_device_sn, output_string)
        pct_growth = target_charge - current_charge
        output_string.append("Growth as %%: %s" % (pct_growth))

        if pct_growth < 0:
          pct_growth = 0

        growth_as_wh = battery_capacity_wh * (pct_growth/100.0)
        output_string.append("Growth as wh: %.2f" %(growth_as_wh))

        off_peak_duration_hours = get_offpeak_duration(off_peak_start_time, off_peak_end_time)

        charge_rate_in_w = growth_as_wh / off_peak_duration_hours
        output_string.append("Charge rate in w: %.2f" % (charge_rate_in_w))

        charge_rate_as_pct = 0
        if charge_rate_in_w > 0:
          charge_rate_as_pct = (charge_rate_in_w / maximum_charge_rate_w)*100.0

        #The percentage charge rate seems to be a bit lower than desired, so we manually tweak it to be a couple of % higher
        #(We have to catch the case where we set it to be too high as well)
        charge_rate_as_pct = charge_rate_as_pct + 2.0
        if charge_rate_as_pct > 100:
          charge_rate_as_pct = 100.00

        output_string.append("Charge rate as %%: %.2f" % (charge_rate_as_pct))
        output_string.append("")

        set_growatt_datetime(growatt_api, gw_device_sn, output_string)
        output_string.append("")

        #Returns false if the charge rate is below 5%
        if charge_rate_as_pct < 5:
          """This is a fail-safe, if we're unable to contact the server between this low rate of charge
             and the off-peak rate then we should set to 100% charge as we're likely to have used battery
             between now and then. If, the battery already has it's required charge then it won't add anything
             therefore, this rate is irrelevant.
          """
          charge_rate_as_pct = 100

        configure_charge_settings(growatt_api, gw_device_sn, off_peak_start_time, off_peak_end_time, charge_rate_as_pct, target_charge, output_string)
        output_string.append("")
      else:
        output_string.append("Within off-peak window - no reconfiguration to happen")
      success = True

    except Exception as e:
      output_string.append("Caught an exception, oh dear....")
      output_string.append("Please check your configuration values also e.g. you've updated the default values")
      output_string.append(str(e))
      if attempts < max_attempts-1:
        output_string.append("Sleeping for 10 seconds before retry")
        output_string.append("")
        time.sleep(10)

    attempts += 1

  if success:
    exit_printing(output_string)
    sys.exit(0)
  else:
    exit_printing(output_string, True)
    sys.exit(1)


asyncio.run(main())

