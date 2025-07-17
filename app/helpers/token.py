
from fastapi import  Request


async def get_access_token_from_header(request: Request):
    """Extract the access token from the request header."""
    
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    
    return auth_header.split(" ")[1]