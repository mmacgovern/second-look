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

## Testing
- Write a pytest test for every module.
- Run tests before saying a task is done.
