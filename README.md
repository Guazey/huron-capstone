# Capstone Agent

A small agent that answers stock-price questions by deciding, on its own,
when to look up real data instead of guessing.

## What it does

Takes a question, and if it needs a stock price to answer it, calls a tool
to look one up, then uses that result to give a real answer — rather than
just generating a plausible-sounding number.

```
$ python main.py "Is TSLA above 200 dollars?"
human: Is TSLA above 200 dollars?
ai:    [asks to run get_stock_price for TSLA]
tool:  $248.50
ai:    Yes, TSLA is currently trading at $248.50, which is above $200.
```

Four steps: the question, the model asking for a lookup, the lookup's
result, and the model's answer built on that result. If the question
doesn't need a price, the model answers directly and the lookup never runs.

## How the pieces fit together

- **Bedrock** hosts the Claude model on AWS's infrastructure. The code never
  talks to Anthropic directly, which matters for clients who need everything
  inside their existing AWS security and compliance boundary.
- **LangChain** provides the model wrapper, the tool definition, and the prompt.
- **LangGraph** provides the control flow — it's the loop that lets the
  model ask for a tool, get a real result, and use it in its final answer.

## Where each piece lives

Each file does one job and can be run on its own to see that job in isolation.

| File | What it is |
| --- | --- |
| `tools.py` | The one tool the model can ask for: a stock-price lookup (hardcoded prices for now). |
| `prompts.py` | The instructions the model sees on every turn, plus a slot for the conversation so far. |
| `model.py` | The connection to Claude on Bedrock, with the tool attached so the model knows it exists. |
| `state.py` | The shared memory that flows between steps: the list of messages, set up so each step appends rather than overwrites. |
| `nodes.py` | The two steps that do the work: one calls the model, one runs whatever tool the model asked for. |
| `graph.py` | Wires the two steps together, including the decision that either loops back or stops. |
| `main.py` | Runs the whole thing on a question and prints every message. |
| `eval.py` | Three repeatable checks, including one failure path, to rerun after any change to the prompt, model, or graph. |
| `tests/` | Unit tests for the parts that don't need a model: the tool and the tool-result step. Run free in CI. |

## Running it

```
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
aws configure          # credentials stay in ~/.aws, never in this folder
cp .env.example .env   # then set the region and model ID
python main.py "What's the price of AAPL?"
pytest                 # unit tests for the deterministic code, no AWS needed
python eval.py         # should print 3/3 passed; calls Bedrock
```

The model is a single string in `.env`. Swapping Haiku for Sonnet, or any
other Claude model available in Bedrock, changes nothing else in the code.

## What this isn't yet

This is a working prototype, not a production system. Before I'd call it
production-ready, it would need: error handling for a failed tool call or
a Bedrock timeout, monitoring so I'd know if it started failing silently,
a real data source instead of a hardcoded dictionary, and automated tests
that check its answers stay correct as the prompt or model changes.

The path I'd actually take to get there: deploy this through Amazon
Bedrock AgentCore rather than build all of that hardening by hand.
AgentCore is AWS's managed runtime for exactly this — session isolation,
identity/auth, memory, observability, and scaling — and it's framework-
agnostic, so it runs the LangGraph agent as-is rather than requiring a
rewrite.
