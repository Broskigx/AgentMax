# Data Files

- `train.jsonl`: 80 training examples.
- `valid.jsonl`: 20 validation examples.

Each line is one JSON object with:

- `category`: short coverage label.
- `messages`: chat-style message array.

The examples are synthetic but grounded in AgentMax issues: UI build failures, package-lock drift, FastAPI router import bugs, mocked security endpoints, Docker path drift, Windows command safety, and git workflow risk.

Do not add raw repository dumps, secrets, real API keys, local credentials, generated dependency folders, or giant logs.
