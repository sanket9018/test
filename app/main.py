from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Dict, Any, Optional
import asyncpg
from fastapi.responses import JSONResponse
from . import schemas
from .database import get_db, close_pool, get_pool
from .apis.user import router as user_router
from .apis.public import router as public_router
from contextlib import asynccontextmanager
import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
from .helpers.token import get_access_token_from_header
from app.db.queries import token_verify
from dotenv import load_dotenv
import os

load_dotenv()



# Application lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create the database connection pool
    await get_pool()
    yield
    # Shutdown: close the connection pool
    await close_pool()

app = FastAPI(
    title="Fitness App API",
    description="API for Fitness Application",
    version="1.0.0",
    lifespan=lifespan
)

# Register API routers
# Add more routers here as you create more modules inside app/apis
app.include_router(user_router)
app.include_router(public_router, prefix="", tags=["Public"])

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



# Token Verifier Middleware
class TokenVerifierMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, excluded_paths=None):
        super().__init__(app)
        self.excluded_paths = excluded_paths or ["/docs", "/openapi.json", "/"]

    async def dispatch(self, request: Request, call_next):
        # Skip middleware for excluded paths and OPTIONS requests
        if request.method == "OPTIONS" or any(
            request.url.path.startswith(path) for path in self.excluded_paths
        ):
            return await call_next(request)

        # Get token from header
        token = await get_access_token_from_header(request)
        if not token:
            return JSONResponse(
                {"error": "Authorization header missing or invalid"}, 
                status_code=status.HTTP_401_UNAUTHORIZED
            )

        try:
            # Verify JWT token
            payload = jwt.decode(
                token, 
                os.getenv("SECRET_KEY"), 
                algorithms=[os.getenv("ALGORITHM")]
            )
            
            user_id = payload.get("user_id")
            if not user_id:
                return JSONResponse(
                    {"error": "Invalid token: missing user_id"}, 
                    status_code=status.HTTP_401_UNAUTHORIZED
                )

            # Store user data in request state
            request.state.user = payload
            
            # Get database connection using get_db dependency
            db = await get_db().__anext__()
            try:
                # Verify token in database
                token_data = await token_verify(db, token)
                if token_data and token_data.get("revoked"):
                    return JSONResponse(
                        {"error": "Token has been revoked"},
                        status_code=status.HTTP_401_UNAUTHORIZED,
                    )
            finally:
                # Ensure connection is properly closed
                if db and not db.is_closed():
                    await db.close()

        except jwt.ExpiredSignatureError:
            return JSONResponse(
                {"error": "Token has expired"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        except jwt.InvalidTokenError as e:
            return JSONResponse(
                {"error": f"Invalid token: {str(e)}"},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        except Exception as e:
            return JSONResponse(
                {"error": f"Token verification failed: {str(e)}"},
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return await call_next(request)

app.add_middleware(
    TokenVerifierMiddleware,
    excluded_paths=[
        "/",
        "/docs",
        "/openapi.json",
        "/motivations",
        "/equipment",
        "/health_issues",
    ],
)


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint to check if the API is running."""
    return {"message": "Welcome to Fitness App API"}

# Health check endpoint
@app.get("/health")
async def health_check():
    """Health check endpoint to verify the API is running."""
    return {"status": "healthy"}

# Database connection test
@app.get("/db-check")
async def database_check(conn=Depends(get_db)):
    """Check database connection status."""
    try:
        result = await conn.fetchval("SELECT 1")
        return {"status": "success", "message": "Database connection successful", "data": result}
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Database connection failed: {str(e)}"
        )



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True
    )