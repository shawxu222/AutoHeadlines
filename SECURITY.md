# Security Policy

AutoHeadlines `0.1.x` is intended for local, single-user use and binds its UI to
`127.0.0.1`. Do not expose it directly to a public network.

Never commit `.env`, browser profiles, cookies, imported documents, databases,
logs, or generated reports. Treat article URLs and uploaded files as untrusted.

To report a vulnerability, open a private security advisory in the GitHub
repository. Do not include credentials, cookies, private article text, or other
personal data in a public issue.

OpenAI processing sends selected article text to OpenAI. Ollama processing stays
local, subject to the user's Ollama configuration.
