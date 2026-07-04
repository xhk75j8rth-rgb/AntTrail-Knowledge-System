"""Lucas Database plugin for Hermes."""

from . import schemas, tools


def register(ctx):
    """Register Lucas Database retrieval tool."""
    ctx.register_tool(
        name="lucas_retrieve",
        toolset="lucas_database",
        schema=schemas.LUCAS_RETRIEVE,
        handler=tools.lucas_retrieve,
    )
