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
| `get_financials` | Revenue, profit, EPS, cash flow, balance sheet, exactly as reported to the SEC (XBRL), by fiscal year or quarter, for any US filer |
| `search_sec_filings` | Passages from 10-K/10-Q filings (risks, strategy, IPO details) |
| `calculate` | Growth rates, margins, differences: arithmetic done by code, never in the model's head |

Every tool result carries the exact page it came from. Answers end with a
Sources line, and the panel only makes those tool-returned links clickable.

**Every figure is checked before the answer is final.** A `verify` step in the
graph matches each $, %, and $B figure in the answer against the numbers the
tools returned. A figure no tool returned sends the answer back to be fixed
once (the panel swaps in the corrected answer). The panel then shows "✓ 6
figures matched to sources", or names any figure it still couldn't match.
See [ADR-0005](docs/decisions/0005-traceable-numbers.md).

```
You:   How fast did Apple's revenue grow last fiscal year, and what was its profit margin?
Agent: [SEC financials, the math] For FY2025 (ended September 27, 2025):
       Revenue grew 6.4% ($416.16B vs. $391.04B in FY2024); profit margin was
       26.9% ($112.01B net income ÷ $416.16B revenue). Sources: [Apple 10-K](https://www.sec.gov/...)
       ✓ 6 figures matched to sources
```

## Where each piece lives

Each Python file does one job, and most can be run on their own
(`python tools.py`, `python graph.py`, ...) to see that job in isolation.

| File | What it is |
| --- | --- |
| `app.py` | AgentCore entry point: validates the request, keys memory by session + signed-in user, streams tool/text/sources/done events, logs one line per request (never the question text) |
| `graph.py`, `nodes.py`, `state.py` | The LangGraph loop: call the model, run the tools it asked for, repeat, then check the answer's figures |
| `verify.py` | Pulls each figure out of an answer and matches it against the tool results, allowing honest rounding |
| `prompts.py`, `model.py` | The system prompt; the Bedrock model with timeouts, retries, and `max_tokens` |
| `tools.py` | The 8 tools above |
| `market_data.py` | The only code that talks to yfinance: ticker validation, 60s cache, cleaning third-party text |
| `financials.py` | The only code that talks to the SEC's XBRL API: picks each metric's tag, deduplicates restated periods, names fiscal periods from the filing |
| `calculator.py` | Safe arithmetic: parses the expression and evaluates only numbers, operators, and a few functions (never `eval`) |
| `sec_edgar.py` | Loads recent 10-K/10-Q filings for a watchlist into S3 and runs Knowledge Base ingestion |
| `knowledge.py` | Searches the filings Knowledge Base |
| `main.py` | Asks one question from the terminal and prints every step |
| `eval.py` | Live checks against the real model: right tools, no invented numbers or links, sources cited, follow-ups, prompt-injection resistance, SEC figures and math, fiscal labels, and every figure passing the verify step. Scored by category |
| `evals/` | `financebench.py`: FinanceBench's numeric 10-K questions, graded by code. `results.py`: per-run scores (pass rate by category, latency, tokens, cost, verifier rewrites) saved to `evals/history/`, and the trend report |
| `tests/` | Unit tests with the network and model stubbed; run in CI |
| `extension/` | The Chrome side panel; its `src/` is the shared UI, with host specifics behind `platform.ts` |
| `desktop/` | The same UI as a Tauri menu bar app (macOS/Windows): always on top, docked right, toggled with Alt+Shift+M; signs in through the system browser and a loopback redirect |
| `infra/` | `deploy.sh`, `create_user.sh`, `invoke.sh`, `teardown.sh`, and the deployer IAM policy |

## Running it

```
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
aws configure          # credentials stay in ~/.aws, never in this folder
cp .env.example .env   # set the region, model ID, and SEC_USER_AGENT
python main.py "What's the price of AAPL?"
pytest                 # unit tests, no AWS needed
python eval.py         # live eval; calls Bedrock, Yahoo, SEC, and the Knowledge Base
python eval.py --suite financebench   # FinanceBench numeric questions; --suite all for both
python -m evals.results               # score trend across saved runs
python app.py          # serves the agent on localhost:8080 like AgentCore does
```

Deploy and use it:

```
infra/deploy.sh                           # everything in AWS (needs infra/deployer-policy.json)
infra/create_user.sh                      # your sidebar login
set -a; source .env; source infra/outputs.env; set +a
python sec_edgar.py                       # load SEC filings into the Knowledge Base
cd extension && npm install && npm run build   # then chrome://extensions -> Load unpacked -> extension/dist
cd desktop && npm install && npm run build     # needs Rust; the app lands in desktop/src-tauri/target/release/bundle
infra/teardown.sh                         # delete it all
```

### Desktop auto-update

Installed copies check GitHub Releases (the `desktop-latest` feed) at launch
and every 6 hours, download a newer build in the background, and offer
"Restart to update" in the menu bar.
Each build is signed with a key that only lives on the releasing machine; the
app refuses any build that doesn't match the public key in `tauri.conf.json`.

```
npx tauri signer generate -w ~/.tauri/market-sidebar.key   # once, from desktop/; set a password
gh auth login                                              # once
desktop/release.sh 0.1.1                                   # bump, sign, build (universal macOS), tag, publish
```

Keep `~/.tauri/market-sidebar.key` and its password out of the repo and backed
up: losing the key means installed copies can't update to anything newer.

## Evals

Every change to the prompt, model, tools, or graph is measured, not eyeballed.
Two suites run against the real model and data:

- **behavior** (16 cases): right tool, no invented numbers or links, sources
  cited, follow-ups, prompt injection, refusing advice, SEC figures, math,
  fiscal labels. Scored by category.
- **financebench** (48 questions): the numeric questions from
  [FinanceBench](https://github.com/patronus-ai/financebench), a public
  benchmark of questions about real 10-Ks ("FY2019 fixed asset turnover for
  CVS"). The agent finds the company, pulls the fiscal years it needs, does
  the math, and ends with `Answer: <number>`. Code grades it at the expected
  answer's precision, with no AI judge.

Each run is saved to `evals/history/` with its commit, pass rate by category,
latency, tokens, estimated cost, and verifier rewrites; `python -m
evals.results` prints the trend. The **eval** GitHub Actions workflow runs both
suites weekly and on demand (Actions → eval → Run workflow), posts the scores
on the run page, and commits them to `evals/history/`. It reaches Bedrock
through a role that trusts only this repo's `main` branch, via GitHub's OIDC
token, so no AWS keys live in GitHub ([ADR-0006](docs/decisions/0006-scored-evals-in-ci.md)).

One-time setup (after `infra/deploy.sh`): an admin attaches the policy that
`infra/render_policies.sh` writes to `infra/deployer-ci-policy.json`, then
`infra/ci_role.sh` creates the role and sets the repo's secrets.

## Known limits

- yfinance is unofficial: it can be rate-limited, and some quotes are delayed.
  `market_data.py` is the only file to change to switch to a keyed provider.
- SEC filings cover a 10-company watchlist (`sec_edgar.py`) and stay as loaded
  until `sec_edgar.py` is rerun.
- Information only, not financial advice. The agent never places trades.
