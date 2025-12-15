"""
Sports Schedule API - Main application file
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from dotenv import load_dotenv
import models
import database
import os

# Import routers
from routes import leagues, matches, redis
from utils.redis_client import get_redis_connection

# Load environment variables
load_dotenv()

# Get CORS origins
origins = os.getenv("ALLOWED_ORIGINS", "").split(",")

# Initialize FastAPI app
app = FastAPI(
    title="Sports Schedule API",
    description="API for managing sports schedules and matches",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create database tables
models.Base.metadata.create_all(bind=database.engine)

# Register routers
app.include_router(leagues.router)
app.include_router(matches.router)
app.include_router(redis.router)


@app.get("/")
def read_root():
    """
    Root endpoint - health check
    """
    return {
        "message": "Sports Schedule API is running",
        "version": "1.0.0"
    }

@app.get("/health")
def health_check():
    """
    Complete health check - verifies DB and Redis
    Returns:
    - 200: All systems operational
    - 503: Critical service (database) unavailable
    - 207: Partial success (Redis unavailable but DB operational)
    """
    health_status = {
        "status": "healthy",
        "description": "All systems operational",
        "database": {
            "status": None,
            "description": None
        },
        "redis": {
            "status": None,
            "description": None
        }
    }
    
    status_code = 200
    
    # Check database (CRITICAL)
    db = database.SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        health_status["database"]["status"] = "operational"
        health_status["database"]["description"] = "Database connection successful"
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["description"] = "Critical service unavailable"
        health_status["database"]["status"] = "failed"
        health_status["database"]["description"] = str(e)
        status_code = 503  # Service Unavailable - Critical failure
    finally:
        db.close()
    
    # Check Redis (NON-CRITICAL)
    try:
        r = get_redis_connection()
        if r is None:
            health_status["redis"]["status"] = "connection_failed"
            health_status["redis"]["description"] = "Could not establish Redis connection"
            if status_code != 503:  # Only if DB is OK
                health_status["status"] = "degraded"
                health_status["description"] = "Degraded: Redis unavailable but database operational"
                status_code = 207  # Multi-Status - Partial success
        else:
            r.ping()
            health_status["redis"]["status"] = "operational"
            health_status["redis"]["description"] = "Redis connection successful"
    except Exception as e:
        health_status["redis"]["status"] = "failed"
        health_status["redis"]["description"] = str(e)
        if status_code != 503:  # Only if DB is OK
            health_status["status"] = "degraded"
            health_status["description"] = f"Degraded: Redis error - {str(e)}"
            status_code = 207  # Multi-Status - Partial success
    
    # Return with appropriate status code
    raise HTTPException(status_code=status_code, detail=health_status)