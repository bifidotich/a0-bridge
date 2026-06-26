"""Entry point. `a0-mcp` (console script) и `python -m a0_mcp` запускают streamable HTTP сервер."""

from __future__ import annotations

from .server import run_streamable_http


def main() -> None:
    run_streamable_http()


if __name__ == "__main__":
    main()
