# backend/alerting
#
# "🚨 Alerts": InsightX watches numbers in your database and warns you
# when something unusual happens.
#
#   detect.py     the maths: is the latest value unusual? (no AI)
#   metrics.py    what to watch: suggested numbers and turning query results into a series
#   store.py      alerts, the alerts they raised, and past readings
#   notify.py     web hook (Slack / Discord / Teams) and e-mail messages
#   service.py    one check of one alert, and the background checker
#   api.py        the /alerts endpoints
#
# Plugged into the API by a few lines at the end of backend/main.py.
