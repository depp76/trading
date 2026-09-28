"""strategy — one sub-package per trading strategy (CLAUDE.md "Strategy rule").

Each strategy lives in ``strategy/<name>/`` next to its spec ``<name>.md``;
callers import from ``strategy.<name>`` directly, never through the
``data_fetcher`` facade, and nothing under ``data/`` imports this package.
"""
