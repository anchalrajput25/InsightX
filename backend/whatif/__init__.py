# backend/whatif
#
# What-if scenarios, sensitivity sweeps and forecasts.
#
#   spec.py      a scenario as data, verified against the real schema
#   builder.py   deterministic baseline / scenario SQL
#   forecast.py  Holt-Winters / Holt / exponential smoothing in plain Python
#   planner.py   plain English -> scenario spec (the only LLM step)
#   service.py   run_whatif, run_sweep, run_forecast
#   api.py       POST /whatif, /whatif/sweep, /forecast
