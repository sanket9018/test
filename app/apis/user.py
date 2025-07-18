from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional, Dict, Any
import asyncpg
from datetime import datetime, timedelta
from app.schemas import ExerciseResponse, UserOnboardingCreate, UserDetailResponse, UserLogin, Token
from app.database import get_db
from app.utils import hash_password, verify_password, success_response, error_response
from app.db import queries as db_queries
from app.security import create_access_token, create_refresh_token, SECRET_KEY, ALGORITHM, verify_token, ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS
import jwt
from app.helpers.token import get_access_token_from_header
from fastapi import  Request
import json


router = APIRouter()


@router.post("/users", status_code=201)
async def create_user(
    user_data: UserOnboardingCreate, 
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Create a new user with full onboarding data.
    This endpoint accepts IDs for related entities (goals, equipment, etc.)
    and performs efficient bulk inserts.
    """
    async with conn.transaction():
        try:
            # 1. Hash the password
            hashed_password = hash_password(user_data.password)

            # 2. Prepare user data, excluding all linked IDs for the main insert
            user_dict = user_data.model_dump(exclude={
                'password', 'routine_id', 'goal_ids', 'focus_area_ids', 
                'health_issue_ids', 'equipment_ids', 'workout_days'
            })
            user_dict['password_hash'] = hashed_password

            # 3. Insert the main user record.
            # The DB trigger 'trigger_assign_routines_on_user_insert' will fire here
            # and automatically create all 7 user_routines records.
            new_user_row = await db_queries.insert_user(conn, user_dict)
            if not new_user_row:
                raise HTTPException(status_code=500, detail="Failed to create user record.")
            new_user_id = new_user_row['id']

            # 4. Set the initial active routine for the user.
            # This updates one of the records just created by the trigger.
            await db_queries.set_initial_active_routine(conn, new_user_id, user_data.routine_id)

            # 5. Perform efficient bulk inserts for other profile details
            await db_queries.link_user_to_items(conn, new_user_id, user_data.goal_ids, 'user_goals', 'goal_id')
            await db_queries.link_user_to_items(conn, new_user_id, user_data.focus_area_ids, 'user_focus_areas', 'focus_area_id')
            await db_queries.link_user_to_items(conn, new_user_id, user_data.health_issue_ids, 'user_health_issues', 'health_issue_id')
            await db_queries.link_user_to_items(conn, new_user_id, user_data.equipment_ids, 'user_equipment', 'equipment_id')
            await db_queries.link_user_to_days(conn, new_user_id, [day.lower() for day in user_data.workout_days])

            # 6. Prepare and return the successful response
            response_data = user_data.model_dump(exclude={'password'})
            response_data["id"] = new_user_id
            response_data["created_at"] = new_user_row['created_at'].isoformat()
            response_data["updated_at"] = new_user_row['updated_at'].isoformat()
            
            return success_response(response_data, message="User created and routines assigned successfully", status_code=201)

        except asyncpg.UniqueViolationError:
            return error_response(message="A user with this email already exists.", status_code=409)
        except asyncpg.ForeignKeyViolationError as e:
            # This error will now catch an invalid motivation_id, goal_id, etc.
            return error_response(message=f"Invalid ID provided for a linked item. {e.detail}", status_code=400)
        except Exception as e:
            return error_response(message=f"An unexpected error occurred: {str(e)}", status_code=500)
            

# ---------------------------------------------------------------------------
# Read User – full details
# ---------------------------------------------------------------------------
@router.get("/user/me", response_model=UserDetailResponse)
async def read_user(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Fetch the currently authenticated user's details with all related information.
    The user is identified from the JWT token in the Authorization header.
    """
    # Get user_id from the request state (set by the middleware)
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']
    print("user_id", user_id)
    # Fetch user details using the new, powerful query
    row = await db_queries.fetch_user_with_routines(conn, user_id)
    print("row", row)
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )


    user_dict: Dict[str, Any] = dict(row)

    if isinstance(user_dict.get('routines'), str):
        user_dict['routines'] = json.loads(user_dict['routines'])
        
    # Convert decimal/decimal-like numeric types to float for JSON serialization
    return user_dict


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------
@router.post("/login", response_model=Token)
async def login(user_credentials: UserLogin, db = Depends(get_db)):
    # Find user by email
    query = "SELECT id, email, password_hash FROM users WHERE email = $1"
    user_record = await db.fetchrow(query, user_credentials.email)
    print("user_record", user_record)
    if not user_record:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid credentials")

    # Verify password
    if not verify_password(user_credentials.password, user_record["password_hash"]):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid credentials")

    revoke_query = """
        UPDATE token_blocklist
        SET revoked = TRUE
        WHERE user_id = $1 AND revoked = FALSE
    """
    await db.execute(revoke_query, user_record["id"])
    
    # Create tokens
    access_token = create_access_token(data={"sub": user_record["email"]})
    refresh_token = create_refresh_token(data={"sub": user_record["email"]})

    # Store tokens in token_blocklist
    access_token_expires = datetime.now() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_token_expires = datetime.now() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    # Insert tokens into token_blocklist
    query = """
        INSERT INTO token_blocklist (access_token, refresh_token, exp_time, user_id, revoked)
        VALUES ($1, $2, $3, $4, FALSE)
    """
    await db.execute(query, access_token, refresh_token, access_token_expires, user_record["id"])

    # Insert login history
    query = "INSERT INTO user_login_history (user_id, login_time) VALUES ($1, CURRENT_TIMESTAMP)"
    await db.execute(query, user_record["id"])

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }

# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

# It's highly recommended to use a different secret for refresh tokens
SECRET_KEY = "your-access-token-secret-key"
REFRESH_SECRET_KEY = "your-different-and-stronger-refresh-secret-key"
ALGORITHM = "HS256"

# --- End of Mock objects ---
from pydantic import BaseModel

class RefreshTokenPayload(BaseModel):
    refresh_token: str


@router.post("/logout")
async def logout(payload: RefreshTokenPayload, db = Depends(get_db)):
    """
    Logs out the user by revoking their refresh token.
    """

    refresh_token = payload.refresh_token

    try:
        # 1. Decode the refresh token to get the user's email (sub)
        #    Use the REFRESH_SECRET_KEY for this.
        token_payload = jwt.decode(refresh_token, REFRESH_SECRET_KEY, algorithms=[ALGORITHM])
        email: str = token_payload.get("sub")
        if email is None:
            print("Invalid credentials")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid credentials")
    except Exception as e:
        print("e", e)
        # This catches expired tokens, invalid signatures, etc.

    # 2. Find the token in the database to get its user_id and check its status
    query = """
        SELECT user_id, revoked FROM token_blocklist WHERE refresh_token = $1
    """
    record = await db.fetchrow(query, refresh_token)

    # If the token isn't in our DB or is already revoked, it's an invalid request
    if not record or record["revoked"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token is invalid or has already been revoked"
        )
    
    user_id = record["user_id"]

    try:
        # 3. Use a transaction to perform both updates atomically
        async with db.transaction():
            # Action A: Revoke the token record by setting revoked = TRUE
            await db.execute(
                "UPDATE token_blocklist SET revoked = TRUE WHERE refresh_token = $1",
                refresh_token
            )

            # Action B: Update the user's latest login history with a logout time
            await db.execute(
                """
                UPDATE user_login_history 
                SET logout_time = CURRENT_TIMESTAMP 
                WHERE id = (
                    SELECT id FROM user_login_history 
                    WHERE user_id = $1 AND logout_time IS NULL 
                    ORDER BY login_time DESC LIMIT 1
                )
                """,
                user_id
            )
    except Exception as e:
        # If anything goes wrong in the transaction, raise a server error
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred during logout: {e}"
        )

    return {"message": "Successfully logged out"}


@router.post("/generate", response_model=List[ExerciseResponse])
async def generate_workout_plan(
    request: Request,
    db: asyncpg.Connection = Depends(get_db)
):
    """
    Generates a personalized list of exercises based on the user's active
    routine and the current day in their cyclical workout plan.
    """
    # Step 1: Authenticate the user
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(db, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")

    user_id = token_entry['user_id']

    # Step 2: Fetch all necessary profile data and today's focus areas in one go
    user_data = await db_queries.get_profile_for_workout_generation(db, user_id)
    
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="User profile not found. Cannot generate workout."
        )

    focus_area_ids = user_data['focus_area_ids']
    if not focus_area_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not determine focus areas for today. Please ensure you have an active routine with assigned workout days."
        )

    # Step 3: Prepare parameters for the recommendation query
    bodyweight_id = await db_queries.get_equipment_id_by_name(db, 'Bodyweight')
    
    # Safely get equipment IDs and add 'Bodyweight' by default
    equipment_ids = set(user_data['equipment_ids'] or [])
    if bodyweight_id:
        equipment_ids.add(bodyweight_id)

    # Sanitize inputs to prevent errors with empty arrays in SQL
    p_focus_area_ids = focus_area_ids or [0]
    p_equipment_ids = list(equipment_ids) or [0]
    p_health_issue_ids = user_data['health_issue_ids'] or [0]
    
    TOTAL_EXERCISES_WANTED = 7
    # Ensure there's at least 1 exercise per focus area
    exercises_per_focus = max(1, TOTAL_EXERCISES_WANTED // len(p_focus_area_ids))

    # Step 4: Get recommendations with the corrected and efficient query
    recommended_exercises = await db_queries.get_recommended_exercises(
        conn=db,
        fitness_level=user_data['fitness_level'],
        focus_area_ids=p_focus_area_ids,
        equipment_ids=p_equipment_ids,
        health_issue_ids=p_health_issue_ids,
        exercises_per_focus=exercises_per_focus,
        total_limit=TOTAL_EXERCISES_WANTED
    )

    if not recommended_exercises:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No suitable exercises found for today's workout. Try adjusting your fitness level or available equipment."
        )

    # Step 5: Format and return the response
    return [dict(record) for record in recommended_exercises]


from pydantic import BaseModel # Make sure this import is at the top

# ... (keep your other imports and endpoints) ...

# Define a Pydantic model for the response of our new status endpoint
class WorkoutDayStatusResponse(BaseModel):
    routine_name: str
    today_day_number: int
    total_routine_days: int
    focus_areas_for_today: List[str]

@router.get("/me/workout/status", response_model=WorkoutDayStatusResponse)
async def get_current_workout_day_status(
    request: Request,
    db: asyncpg.Connection = Depends(get_db)
):
    """
    Provides the status of the user's workout plan for the current day.
    
    This is a utility endpoint to see which routine is active, which day of the
    cycle it is, and what muscle groups are targeted for today's workout.
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(db, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")

    user_id = token_entry['user_id']
    
    status_record = await db_queries.get_workout_day_status(db, user_id)
    
    if not status_record:
        raise HTTPException(
            status_code=404,
            detail="Could not determine workout status. User may not have an active routine with workout days."
        )
        
    return dict(status_record)
