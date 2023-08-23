# Trading bot

Fully automated trading bot that plugs into robinhood API and is somewhat easy to extend with additional trading strategies.

**Caveat:** I haven't been actively working on this for several months (as of august 2023), and I didn't initially intend to open source this but some people expressed interest. Apologies on how disorganized this is.

If you do end up tinkering with this and need help with something feel free to reach out on discord where the 1DTE SPY condor strategy runs to this day!

https://discord.gg/P8KVXzcSrs 

## Requirements

1. Python 3.9+, pipenv
2. Redis7
3. Polygon API key
4. Robinhood account with options level 3 enabled
5. Discord for logging (optional but you'll have to remove ```@log``` decorators)

## Moving pieces

* Aggregator: does some basic modeling for option value
* Trading strategies: condorer, condorer_spy, strangler - strategies. Only condorer_spy (1DTE SPY condors) has so far not been a complete disaster
* Scheduler: schedule when to run certain functions
* IV scraper: basic IV% scraper
* Notifications: logs stuff to discord
* Oracle: the "controller" that ties everything together and serves as an entry point

## Installation

1. Clone repo
2. run ```pipenv install```
3. Configure config/settings.yml and config/vendors.yml

### cron setup

```
CRON_TZ=America/New_York

* * * * 1-5 ec2-user cd ~/trading_bot; pipenv run python oracle.py
```
