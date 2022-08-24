# Growatt Inverter Solar Forecast Configuration Assistant
A tool for configuring overnight charging (i.e. during off-peak periods) for Growatt inverters that have storage capacity (batteries) based on the predicted solar generation for the day.

The tool performs the following actions:
* Uses [forecast.solar](https://forecast.solar/) to predict your solar generation for the next day
* Uses your average consumption (you provide this as a configuration parameter) to predict at what point you will be grid-neutral i.e. running purely on solar power
* Calculates excess generation that will be fed into the batteries
* Uses these calculations for two things:
    * The required amount of charge to place into the batteries to make it to the grid-neutral time of day
    * The required amount of charge to place into the batteries to make up the difference between excess generation and having the batteries at 100% by the end of the day
* Configures the inverter to fill the batteries to the desired amount at the slowest possible rate given the window specified.
* Outputs a simple text file that can be used to take a quick glance at what has been configured for the upcoming day

Interfacing with the Growatt Inverter is done using the PyPi library: 
* [PyPi growattServer](https://pypi.org/project/growattServer/)
* [github](https://github.com/indykoning/PyPi_GrowattServer)

## Usage
This software was written to be used as a docker image (paths to config & output locations have been hardcoded), but if you wish you can download the entire repo and just use the scripts as you wish.
The scripts do not contain any sort of loop, therefore you can run them as (in)frequently as you like, NOTE: There is a limit on the free tier of forecast.solar meaning that if you make more than 12 calls per hour you will be blocked temporarily until your rate limit resets

An example of how to run the container (breakdown below):
```
sudo docker run --rm -e TZ=Europe/London -v ${PWD}/conf:/opt/growatt-charger/conf -v ${PWD}/output:/opt/growatt-charger/output -v ${PWD}/logs:/opt/growatt-charger/logs muppet3000/growatt-charger:latest
```

Arguments explained:
```
--rm - Remove the container once the run is complete

-e TZ=Europe/London - Replace this with your timezone, full list here: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones (use the TZ database name column)

-v ${PWD}/conf:/opt/growatt-charger/conf - Maps a local directory (the current directory, with a new sub-dir 'conf') through to the container for configuration

-v ${PWD}/output:/opt/growatt-charger/output - Maps a local directory (the current directory, with a new sub-dir 'output') through to the container for the simple text output

-v ${PWD}/logs:/opt/growatt-charger/logs - Maps a local directory (the current directory, with a new sub-dir 'conf') through to the container for logs to be outputted to
```

Note - it is not mandatory to map through the `logs` and `output` directory if you don't need them.

## Configuration
On the first run of the application a configuration file will be output to the `conf` directory that you can populate, it contains a (hopefully) well documented example of what values are required. More information (including how each value is used) is outlined below.

| Config Parameter | Description |
|------------------|-------------|
| battery_capacity_wh | The capacity of the batteries in Watt-hours e.g. 9.9kwh = 9900wh. Used to determine what percentage to be filled |
| maximum_charge_rate_w | The maximum rate that the batteries can be charged at in Watts e.g. 3kw = 3000w. Used to calculate how much excess solar can be diverted to the batteries. Also used to calculate the off-peak charging rate. |
| statement_of_charge_pct | The statement-of-charge (percentage) that the batteries are configured to have e.g. the minimum amount they will ever go down to (this is for battery preservation) for most systems this is either 10 or 15 |
| minimum_charge_pct | The minimum charge percentage you would ever like the batteries to be filled to during the off-peak window e.g. even if the solar forecast predicts you need X% always fill it to this value as a minimum |
| maximum_charge_pct | The maximum charge % that you would like to ever go up to when charging e.g. never charge the batteries to greater than 100% |
| average_load_w | The average load consumption of your house in Watts e.g. 850w (used to calculate % charge required to get to the point where you are running purely on solar power) |
| username | Growatt username as used in the shinephone app. OPTIONAL - Can also be provided by an environment variable to avoid credentials coded into this file. Environment variable: `GROWATT_USERNAME` e.g. add the following to the docker command line: `-e GROWATT_USERNAME=my_username` |
| password | Growatt password as used in the shinephone app. OPTIONAL - Can also be provided by an environment variable to avoid credentials coded into this file. Environment variable: `GROWATT_PASSWORD` e.g. add the following to the docker command line: `-e GROWATT_PASSWORD=my_password` |
| plant_id | The Growatt Plant ID to be configured - must have a device_sn provided also. OPTIONAL - If not specified the first Plant & SN combination found will be used |
| device_sn | The Growatt Device SN (for the plant ID) to be configured - must be provided as well as plant_id. OPTIONAL - If not specified the first Plant & SN combo will be used |
| off_peak_start_time | Off peak start time in 24hour clock format, used to configure when A/C charging will start |
| off_peak_end_time | Off peak end time in 24 hour clock format, used to configure when A/C charging will end |
| location | Your location e.g. your home address |
| declination | The angle of your solar panels in degrees 0=Horizontal, 90=Vertical |
| azimuth | The orientation of your panels (360 degrees: -180=North, -90=East, 0=South, 90=West) |
| kw_power | The nominal power of your solar panels in kw e.g. 6.1kw |
| damping | Damping factor - Adjusts the results in the morning and evening |
| confidence | Confidence in the returned results 0-1 e.g. 0.8 = 80% confidence in the results returned (sometimes the values from forecast.solar aren't fantastically reliable - this allows you to tweak the results based on your confidence in them) |
