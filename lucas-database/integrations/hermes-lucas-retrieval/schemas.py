"""Tool schemas for the Lucas Database Hermes plugin."""

LUCAS_RETRIEVE = {
    "name": "lucas_retrieve",
    "description": (
        "Retrieve grounded context, sources, citations, and answerability from "
        "Lucas Database. Use before answering questions about the user's saved "
        "notes, cards, nodes, project history, or local knowledge base."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Question or search intent to retrieve from Lucas Database.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Maximum context blocks to return.",
            },
            "token_budget": {
                "type": "integer",
                "minimum": 200,
                "maximum": 12000,
                "description": "Approximate token budget for returned context.",
            },
            "max_chunks_per_source": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10,
                "description": "Maximum chunks accepted from one source.",
            },
            "response_format": {
                "type": "string",
                "enum": ["default", "messages"],
                "description": "Use messages when a system/user prompt pair is useful.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}
