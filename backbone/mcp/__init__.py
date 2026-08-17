"""Career Copilot MCP server package.

Exposes a curated, read-only subset of Career Copilot data and tools to
Hermes Agent over the Model Context Protocol (MCP).

The MCP server runs as a separate stdio process launched by Hermes, so it
never shares a process or secrets with the Hermes runtime.
"""
