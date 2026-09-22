# Market Sidebar

A Chrome side panel that answers investing questions while you trade. It looks
up live market data and SEC filings instead of guessing, links every answer to
its sources, and remembers the conversation so follow-ups work.

```
You:   How has TSLA moved over the last 3 months?
Agent: [looks up price history] Essentially flat, down 0.71% ($381.61 -> $378.90)...
You:   what did they do in order for it to not grow?
Agent: [looks up earnings, news, fundamentals] Q2 EPS of $0.33 missed the $0.54
       estimate by 39.1%... Sources: [TSLA earnings](https://...), ...
```

## How the pieces fit together

- **Chrome side panel** (React + TypeScript + Vite) with **Cognito** login
  (hosted UI, OAuth code + PKCE).
- **Amazon Bedrock AgentCore Runtime** hosts the agent, rejects calls without a
  valid login, and streams answers back. **AgentCore Memory** keeps each chat
  (per user, 7 days).
- **The agent**: Python, **LangChain**, and **LangGraph**, a loop that lets
  Claude call tools, read the results, and answer from them.
- **Claude Haiku 4.5 on Amazon Bedrock**: the model. The code never calls
  Anthropic directly, so everything stays inside the AWS account.
- **Data**: live market data from Yahoo Finance (yfinance), and SEC filings
  in a **Bedrock Managed Knowledge Base** (the RAG part).

Diagram: [`docs/architecture.excalidraw`](docs/architecture.excalidraw) (open at excalidraw.com).
Design and decisions: [`docs/architecture.md`](docs/architecture.md), [`docs/decisions/`](docs/decisions/).

## The agent's tools

| Tool | Answers |
| --- | --- |
| `search_ticker` | "What's SpaceX's ticker?" (live Yahoo search; the model's memory of listings is out of date) |
| `get_stock_price` | Current price and change |
| `get_price_history` | Moves over 5 days to 5 years |
| `get_company_profile` | Valuation, growth, margins, analyst consensus |
| `get_earnings` | Next report date; recent EPS vs. estimates |
| `get_news` | Headlines for one company or the whole market |
| `get_market_overview` | Indexes and today's top gainers, losers, most active |
| `search_sec_filings` | Passages from 10-K/10-Q filings (risks, strategy, IPO details) |

Every tool result carries the exact page it came from. Answers end with a
Sources line, and the panel only makes those tool-returned links clickable.

## Where each piece lives

Each Python file does one job, and most can be run on their own
(`python tools.py`, `python graph.py`, ...) to see that job in isolation.

| File | What it is |
| --- | --- |
| `app.py` | AgentCore entry point: validates the request, keys memory by session + signed-in user, streams tool/text/sources/done events, logs one line per request (never the question text) |
| `graph.py`, `nodes.py`, `state.py` | The LangGraph loop: call the model, run the tools it asked for, repeat |
| `prompts.py`, `model.py` | The system prompt; the Bedrock model with timeouts, retries, and `max_tokens` |
| `tools.py` | The 8 tools above |
| `market_data.py` | The only code that talks to yfinance: ticker validation, 60s cache, cleaning third-party text |
| `sec_edgar.py` | Loads recent 10-K/10-Q filings for a watchlist into S3 and runs Knowledge Base ingestion |
| `knowledge.py` | Searches the filings Knowledge Base |
| `main.py` | Asks one question from the terminal and prints every step |
| `eval.py` | Live checks against the real model: right tools, no invented numbers or links, sources cited, follow-ups, prompt-injection resistance |
| `tests/` | Unit tests with the network and model stubbed; run in CI |
| `extension/` | The Chrome side panel |
| `infra/` | `deploy.sh`, `create_user.sh`, `invoke.sh`, `teardown.sh`, and the deployer IAM policy |

## Running it

```
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
aws configure          # credentials stay in ~/.aws, never in this folder
cp .env.example .env   # set the region, model ID, and SEC_USER_AGENT
python main.py "What's the price of AAPL?"
pytest                 # unit tests, no AWS needed
python eval.py         # live eval; calls Bedrock, Yahoo, and the Knowledge Base
python app.py          # serves the agent on localhost:8080 like AgentCore does
```

Deploy and use it:

```
infra/deploy.sh                           # everything in AWS (needs infra/deployer-policy.json)
infra/create_user.sh                      # your sidebar login
set -a; source .env; source infra/outputs.env; set +a
python sec_edgar.py                       # load SEC filings into the Knowledge Base
cd extension && npm install && npm run build   # then chrome://extensions -> Load unpacked -> extension/dist
infra/teardown.sh                         # delete it all
```

## Known limits

- yfinance is unofficial: it can be rate-limited, and some quotes are delayed.
  `market_data.py` is the only file to change to switch to a keyed provider.
- SEC filings cover a 10-company watchlist (`sec_edgar.py`) and stay as loaded
  until `sec_edgar.py` is rerun.
- Information only, not financial advice. The agent never places trades.
