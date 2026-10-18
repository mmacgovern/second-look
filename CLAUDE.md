# Second Look — Standing Rules

Second Look is a Streamlit app that gives an AI second opinion on an investment portfolio.

## Secrets
- Never read, print, echo, log or commit `.env` or any variable containing `KEY`, `SECRET` or `TOKEN`.
- `.env.example` (placeholders only) is the one env file that may be read and edited.

## Architecture boundaries
- Only `secondlook/config.py` reads environment variables.
- Only `secondlook/llm.py` calls Portkey.
- Every LLM call goes through `budget.py`. No exceptions.

## Numbers and advice
- All financial numbers are computed in Python (`secondlook/quant`). LLMs only explain numbers they are given.
- The app never tells users to buy, sell or hold a specific security.
- Only the Possible Shifts tab makes suggestions: shifts between industries, asset types and account types, computed by
  rules in `secondlook/quant/shifts.py`. It never names a specific stock, fund or ticker. Top 3 and the Pause Button stay neutral.

## Testing
- Write a pytest test for every module.
- Run tests before saying a task is done.

## Prompt log
- At the end of each completed task, append one line under the latest AI_Prompts.md entry: '**Result:**' followed by a one-sentence summary of what was built and whether tests passed. Never edit or delete earlier entries.
