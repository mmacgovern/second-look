# Second Look: project status (17 October 2026)

**Tests:** 470 passed, 0 failed, 0 skipped.
**Saved work:** everything is committed (latest: "Switch to a black and gold theme and add a green Labubu mascot"). Nothing has been uploaded to GitHub yet.

Key for who does the fix: **(a)** only you, **(b)** a quick Claude Code fix, **(c)** a live AI run that costs money.

---

## 1. What works (you can click it today)

- **Website:** a black and gold, three-page site (Analyze, Report & slides, Admin), with the green Labubu in the corner.
- **Sample portfolios:** three made-up people (starting out, mid-career, retired) open with one click.
- **Upload:** add one brokerage CSV per account. Account numbers are removed and nothing is saved.
- **Account types:** each upload is tagged (for example 401(k), Roth IRA or taxable), and the plan treats each type correctly.
- **X-Ray:** shows what you really own once you count the companies inside your funds.
- **Concentration:** warns when one company, counting the shares inside your funds, is a large share of your money.
- **Fee Clock:** shows what your fund fees cost per year and over 30 years, compared with index funds.
- **Crash Replay:** shows how your portfolio would have done in past crashes (for example 2008) and how long it took to recover.
- **Retirement Plan:** shows a range of possible outcomes against your target, with sliders you can move.
- **Risk profile:** a short quiz plus your years to retirement give a profile (for example "Moderately aggressive"), shown in the sidebar.
- **Top 3 Things Worth Knowing:** an AI explains the three biggest findings at your chosen level (New to investing, Comfortable or Pro). Every number is checked before you see it.
- **Pause Button:** type a trade you're thinking about (for example "sell everything"). It shows the before and after, and what happened in past crashes, without telling you what to do.
- **Demo mode:** free, using saved AI answers for the three samples. **Live mode** makes real AI calls and is running now.
- **Spending limits:** AI spending stops at $0.10 per visit and $2.00 per day. The Admin page lists every AI call and its cost.
- **Report & slides page:** shows the report and the slide deck inside the app, with download buttons.

---

## 2. Incomplete

| Where | What's missing | Who |
|---|---|---|
| `docs/user_test_results.md` | The file doesn't exist. No user test has been run, so the Impact numbers in the report and slide 8 are TODO. | (a) |
| Report, section 1 | GitHub link: the code hasn't been uploaded to GitHub. | (a) upload it, then (b) add the link |
| Report, section 1 | App link: the app only runs on this computer and isn't online. | (a) decide whether to put it online, then (b) |
| Report, section 1 | Demo video link: no video has been recorded. | (a) record it, then (b) add the link |
| Report, section 2 | Real users' time spent today, and public data on how many people this affects. | (a) find the sources, then (b) add them |
| Report, section 5 | The cost-versus-quality comparison with the bigger AI model was skipped. | (c) about $0.22 |
| Report, section 7 | Pilot results, and public data on fee and concentration mistakes. | (a) |
| Report, section 8 | The "parking-lot" list of future ideas isn't written down anywhere. | (a) give me the list, then (b) |
| `data/reference/glide_path.json` | The target-date fund reference is an empty placeholder, so the app doesn't use it. | (a) pick a cited source, then (b) fill it in |
| `data/reference/risk_profile_ranges.json` | The stock ranges for each risk profile are rules of thumb, not from a cited source. | (a) pick a source, then (b) |
| README (install steps) | The download link says "YOUR-ACCOUNT" until the code is on GitHub. | (a) then (b) |
| README (macOS steps) | Written, but never tried on a Mac. | (a) |
| AI checker strictness | In the last cost run the AI checker sent 8 of 27 first drafts back for a rewrite. Its reasons aren't recorded yet. | (b) to record the reasons, then (c) a few cents to measure again |
| Portkey dashboard | Not checked against our own cost numbers; the course key can't download the logs. | (a) look at it in the Portkey website |
| Live red-team | Today's safety test ran on saved answers; the last live run (53 of 53 passed) was before the latest changes. | (c) about $0.05 |
| `AI_Prompts.md`, top | Prompts 0-1 and any prompts from other tools (ChatGPT, Claude chat) still need adding by hand. | (a) |
| `AI_Prompts.md`, entry #13 | The logger hid one word: "measured tokens" shows as "[REDACTED] tokens". | (b) fix the logger; the old entry stays as it is under the log rules |
| Screenshots, report and slides | They still show the old navy and teal look, not black and gold. | (b) |

No failing or skipped tests, and no unfinished code stubs.

---

## 3. Deliverables

| Deliverable | Status |
|---|---|
| **README** | Done: a beginner setup guide for Windows and Mac. Needs the real GitHub link; the Mac steps are untested. |
| **report.html** | Built: 15 printed pages. See the sections below. |
| — 1. Title + links | Name and description done. GitHub, app and video links are TODO. |
| — 2. Motivation | Done, using sample numbers. Two TODOs: real users' time and public data. |
| — 3. What is new | Done, with the competitor table. |
| — 4. The agents | Done: diagram, models and one real run step by step with a screenshot. |
| — 5. Tokenomics | Done with measured costs. The big-model comparison is TODO. |
| — 6. Safety | Done: policy, threats and red-team results with a screenshot. |
| — 7. Impact | Mostly TODO: waiting on the user test. Sample estimates are shown. |
| — 8. Limits + next steps | Done, except the parking-lot list (TODO). |
| — Appendix: prompt log | Done. |
| **slides.html** | Built: 9 slides. |
| — 1 Title | Done |
| — 2 Problem | Done |
| — 3 What's new | Done |
| — 4 How the agents work | Done |
| — 5 Demo screenshots | Done, but the screenshots show the old design. |
| — 6 Cost to run | Done |
| — 7 Safety | Done |
| — 8 Impact | TODO: waiting on the user test. |
| — 9 What's next | Done |
| **AI_Prompts.md** | Up to date: 16 entries, each finished task has a Result line. The hand-added sections at the top are still empty. |
| **Screenshots** | 21 taken, but from before the redesign (old look). |
| **Cost results** | Done: about $0.0016 per analysis and about $32 a month for 10,000 users. |
| **Red-team results** | Done: 63 of 63 passed on saved answers. The earlier live run passed 53 of 53. |
| **User test results** | Missing: not started. |
