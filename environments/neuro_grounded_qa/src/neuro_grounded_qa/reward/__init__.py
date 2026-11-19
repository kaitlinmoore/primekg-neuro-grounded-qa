"""Grounding reward: strict output parser, reward components and metrics.

Will score a trace in code: parse the model's structured claims or abstention,
check each cited evidence ID against what the model actually retrieved through
logged tool calls, and record rewards (``@vf.reward``) and metrics
(``@vf.metric``). This is the instrument the reward-hacking audit targets.

Owner: reward lane. Empty for now.
"""
