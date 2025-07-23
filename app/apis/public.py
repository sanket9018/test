from typing import List
from fastapi import APIRouter, Depends
import asyncpg

from app.db import queries as db_queries
from app.schemas import ListItem
from app.database import get_db

router = APIRouter()

@router.get("/motivations", response_model=List[ListItem], summary="Get all motivations")
async def list_motivations(conn: asyncpg.Connection = Depends(get_db)):
    """
    Lists all available motivations that users can choose from.
    This endpoint is public and does not require authentication.
    """
    motivations = await db_queries.fetch_all_motivations(conn)
    return [dict(r) for r in motivations]

@router.get("/goals", response_model=List[ListItem], summary="Get all goals")
async def list_goals(conn: asyncpg.Connection = Depends(get_db)):
    """
    Lists all available goals that users can select.
    This endpoint is public and does not require authentication.
    """
    goals = await db_queries.fetch_all_goal(conn)
    return [dict(r) for r in goals]

@router.get("/health_issues", response_model=List[ListItem], summary="Get all health issues")
async def list_health_issues(conn: asyncpg.Connection = Depends(get_db)):
    """
    Lists all available health issues that users can specify.
    This endpoint is public and does not require authentication.
    """
    health_issues = await db_queries.fetch_all_health_issues(conn)
    return [dict(r) for r in health_issues]
