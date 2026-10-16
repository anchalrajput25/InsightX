# backend/investigation: "why did this metric change?" root-cause analysis.
#
#   planner.py    LLM proposes metric/time/dimensions; verified by code
#   engine.py     deterministic SQL analysis (trend, drivers, signals)
#   narrative.py  LLM explains computed facts; numbers checked
#   service.py    run_investigation(): the whole flow
