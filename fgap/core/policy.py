async def evaluate(tool: str, action: str, resource: str, config: dict) -> bool:
    """Evaluate policy for a request.

    Currently allows all requests. Extension point for future enforcement.
    """
    return True
