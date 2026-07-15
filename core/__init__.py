"""
QAX — Core Package
=============================
This package contains all core logic for the AI-powered test case generator:
  - claude_client:  Communicates with the Anthropic Claude API
  - code_reader:    Reads and preprocesses source code files
  - prompt_engine:  Builds prompts with EXAMPLE context injection
  - validator:      Validates AI output against the Xray JSON schema
  - xray_client:    Pushes validated test cases to Jira/Xray
"""
