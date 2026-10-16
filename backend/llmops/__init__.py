# backend/llmops/
#
# LLMOps for InsightX: live monitoring, agent evaluations, hallucination
# checks, a feedback loop for answer quality, and smart model routing.
#
# Everything here is new and plugs in from the end of backend/main.py
# (include the router, install the capture middleware and the router
# for smart routing). It reads the existing stores (AI usage ledger,
# traces, evaluation reports) and never changes how a question is
# answered unless smart routing is switched on from the Routing page.
