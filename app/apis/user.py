from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional, Dict, Any
import asyncpg
from datetime import datetime, timedelta
from app.schemas import *
from app.database import get_db
from app.utils import hash_password, verify_password, success_response, error_response
from app.db import queries as db_queries
from app.db.queries import clear_user_generated_exercises, get_user_routine_day_exercises
from app.db.custom_exercises import add_custom_exercise, get_user_custom_exercises, clear_user_custom_exercises, update_user_custom_exercise, calculate_exercise_parameters
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
                'password', 'routine_id', 'motivation_ids', 'goal_ids', 'focus_area_ids', 
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
            await db_queries.link_user_to_items(conn, new_user_id, user_data.motivation_ids, 'user_motivations', 'motivation_id')
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

    # The json_agg function in PostgreSQL returns a JSON string.
    # We need to parse these strings into Python lists/dicts before Pydantic validation.
    for key in ['routines', 'motivations', 'goals', 'equipment', 'health_issues', 'focus_areas', 'workout_days']:
        if key in user_dict and isinstance(user_dict.get(key), str):
            user_dict[key] = json.loads(user_dict[key])
        
    # Convert decimal/decimal-like numeric types to float for JSON serialization

    # Ensure new fields are present in the response (default if missing)
    user_dict['is_matrix'] = user_dict.get('is_matrix', False)
    user_dict['randomness'] = user_dict.get('randomness', 10)
    user_dict['circute_training'] = user_dict.get('circute_training', False)
    user_dict['rapge_ranges'] = user_dict.get('rapge_ranges', False)
    user_dict['duration'] = user_dict.get('duration', 30)
    user_dict['rest_time'] = user_dict.get('rest_time', 30)
    user_dict['objective'] = user_dict.get('objective', 'muscle')

    return user_dict


@router.patch("/user/me/profile", response_model=UserDetailResponse)
async def update_user_profile(
    payload: UserProfileUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Update the authenticated user's profile details. Only provided fields will be updated.
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']

    update_data = payload.model_dump(exclude_unset=True)

    # Update main user table fields
    main_fields = [
        "name", "gender", "age", "height_cm", "current_weight_kg", "target_weight_kg",
        "fitness_level", "activity_level", "workouts_per_week", "is_matrix", "randomness",
        "circute_training", "rapge_ranges", "duration", "rest_time", "objective"
    ]
    # Ensure randomness is int if present
    if 'randomness' in update_data and update_data['randomness'] is not None:
        update_data['randomness'] = int(update_data['randomness'])
    main_update = {k: v for k, v in update_data.items() if k in main_fields}
    if main_update:
        set_clause = ", ".join([f"{k} = ${i+2}" for i, k in enumerate(main_update.keys())])
        values = list(main_update.values())
        query = f"UPDATE users SET {set_clause}, updated_at = NOW() WHERE id = $1"
        await conn.execute(query, user_id, *values)

    # Handle many-to-many relationships (clear + insert new links)
    async def update_link_table(table, column, ids):
        if ids is not None:
            await conn.execute(f"DELETE FROM {table} WHERE user_id = $1", user_id)
            if ids:
                await db_queries.link_user_to_items(conn, user_id, ids, table, column)

    await update_link_table("user_motivations", "motivation_id", update_data.get("motivation_ids"))
    await update_link_table("user_goals", "goal_id", update_data.get("goal_ids"))
    await update_link_table("user_equipment", "equipment_id", update_data.get("equipment_ids"))
    await update_link_table("user_health_issues", "health_issue_id", update_data.get("health_issue_ids"))

    # Return updated profile
    row = await db_queries.fetch_user_with_routines(conn, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found after update")
    user_dict: Dict[str, Any] = dict(row)
    for key in ['routines', 'motivations', 'goals', 'equipment', 'health_issues', 'focus_areas', 'workout_days']:
        if key in user_dict and isinstance(user_dict.get(key), str):
            user_dict[key] = json.loads(user_dict[key])
    # Ensure advanced profile fields are properly typed/defaulted
    user_dict['is_matrix'] = user_dict.get('is_matrix', False)
    # Ensure randomness is always an int and never None for response model
    val = user_dict.get('randomness', 10)
    try:
        user_dict['randomness'] = int(val) if val is not None else 10
    except Exception:
        user_dict['randomness'] = 10
    user_dict['circute_training'] = user_dict.get('circute_training', False)
    user_dict['rapge_ranges'] = user_dict.get('rapge_ranges', False)
    user_dict['duration'] = user_dict.get('duration', 30)
    user_dict['rest_time'] = user_dict.get('rest_time', 30)
    user_dict['objective'] = user_dict.get('objective', 'muscle')
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

    # Step 2: Fetch user profile and current day's workout data from active routine
    user_data = await db_queries.get_profile_for_workout_generation(db, user_id)
    print("user_data", user_data)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, 
            detail="User profile not found. Cannot generate workout."
        )

    exercise_mode = user_data.get('exercise_mode', 'focus_areas')
    
    # Handle direct exercises mode
    if exercise_mode == 'direct_exercises':
        direct_exercises_json = user_data.get('direct_exercises', '[]')
        
        # Parse JSON string to Python list
        import json
        try:
            direct_exercises = json.loads(direct_exercises_json) if isinstance(direct_exercises_json, str) else direct_exercises_json
        except json.JSONDecodeError:
            direct_exercises = []
        
        if not direct_exercises:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No direct exercises found for today. Please add exercises to this day or switch to focus areas mode."
            )
        
        # Filter out excluded exercises from direct exercises
        excluded_exercise_ids = await db.fetch("""
            SELECT exercise_id FROM user_excluded_exercises_forever 
            WHERE user_id = $1
            UNION
            SELECT exercise_id FROM user_excluded_exercises_today 
            WHERE user_id = $1 AND excluded_date = CURRENT_DATE
        """, user_id)
        
        excluded_ids = {row['exercise_id'] for row in excluded_exercise_ids}
        
        # Filter out excluded exercises from direct exercises
        if excluded_ids:
            original_count = len(direct_exercises)
            direct_exercises = [ex for ex in direct_exercises if ex.get('id') not in excluded_ids]
            filtered_count = len(direct_exercises)
            print(f"Filtered out {original_count - filtered_count} excluded exercises from direct exercises. Remaining: {filtered_count}")
        
        if not direct_exercises:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="All direct exercises for today have been excluded. Please add more exercises to this day, remove some exclusions, or switch to focus areas mode."
            )
        
        # Return the filtered direct exercises
        return direct_exercises
    
    # Handle focus areas mode (existing logic)
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
    
    # Calculate TOTAL_EXERCISES_WANTED based on duration
    duration = user_data.get('duration', 30)  # Default to 30 minutes
    print(f"Duration: {duration}")
    if duration <= 10:
        TOTAL_EXERCISES_WANTED = 2
    elif duration <= 20:
        TOTAL_EXERCISES_WANTED = 3
    elif duration <= 30:
        TOTAL_EXERCISES_WANTED = 4
    elif duration <= 40:
        TOTAL_EXERCISES_WANTED = 5
    elif duration <= 50:
        TOTAL_EXERCISES_WANTED = 6
    elif duration <= 60:
        TOTAL_EXERCISES_WANTED = 7
    else:
        # For durations > 60, add 1 exercise per additional 10 minutes
        TOTAL_EXERCISES_WANTED = 8 + ((duration - 60) // 10)
    
    # Ensure at least 1-2 exercises per focus area
    min_exercises_per_focus = min(2, TOTAL_EXERCISES_WANTED // len(p_focus_area_ids))
    exercises_per_focus = max(min_exercises_per_focus, 30 // len(p_focus_area_ids))  # Get more candidates

    # Step 4: Get exercises ensuring coverage of all focus areas
    user_objective = user_data.get('objective', 'muscle')  # Default to 'muscle' if not set
    
    # Map user objective to exercise type
    # muscle -> muscle_growth
    # strength -> strength 
    # cardio -> cardio
    exercise_type_filter = 'muscle_growth' if user_objective == 'muscle' else user_objective
    
    all_suitable_exercises = await db_queries.get_recommended_exercises(
        conn=db,
        fitness_level=user_data['fitness_level'],
        focus_area_ids=p_focus_area_ids,
        equipment_ids=p_equipment_ids,
        health_issue_ids=p_health_issue_ids,
        exercises_per_focus=exercises_per_focus,
        total_limit=50,  # Get more candidates for better randomness
        objective=exercise_type_filter
    )
    
    # Sort all exercises by ID for consistent ordering
    all_suitable_exercises = sorted(all_suitable_exercises, key=lambda r: r['id'])

    # Step 4.5: Filter out excluded exercises
    # Get user's excluded exercises (both forever and today)
    excluded_exercise_ids = await db.fetch("""
        SELECT exercise_id FROM user_excluded_exercises_forever 
        WHERE user_id = $1
        UNION
        SELECT exercise_id FROM user_excluded_exercises_today 
        WHERE user_id = $1 AND excluded_date = CURRENT_DATE
    """, user_id)
    
    excluded_ids = {row['exercise_id'] for row in excluded_exercise_ids}
    
    # Filter out excluded exercises
    if excluded_ids:
        original_count = len(all_suitable_exercises)
        all_suitable_exercises = [ex for ex in all_suitable_exercises if ex['id'] not in excluded_ids]
        filtered_count = len(all_suitable_exercises)
        print(f"Filtered out {original_count - filtered_count} excluded exercises. Remaining: {filtered_count}")

    if not all_suitable_exercises:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No suitable exercises found for today's workout. Try adjusting your fitness level or available equipment, or remove some exercise exclusions."
        )

    # Step 5: Apply randomness logic
    # randomness: 0-100, determines what % of exercises are randomized
    # Fixed exercises are high-impact/primary exercises, rest are random
    import math, random
    randomness = user_data.get('randomness', 50)
    try:
        randomness = int(randomness)
        randomness = max(0, min(100, randomness))
    except Exception:
        randomness = 50
    
    total_available = len(all_suitable_exercises)
    if total_available == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No suitable exercises found for today's workout."
        )
    
    # Calculate how many exercises to return
    exercises_to_return = min(TOTAL_EXERCISES_WANTED, total_available)
    
    # Ensure at least 1-2 exercises per focus area are included
    focus_area_exercises = {}
    for exercise in all_suitable_exercises:
        # Group exercises by their primary focus area (assuming it's in the exercise data)
        primary_focus = exercise.get('primary_focus_area', 'Unknown')
        if primary_focus not in focus_area_exercises:
            focus_area_exercises[primary_focus] = []
        focus_area_exercises[primary_focus].append(exercise)
    
    # Guarantee at least 1 exercise per focus area
    guaranteed_exercises = []
    for focus_area, exercises in focus_area_exercises.items():
        if exercises:
            # Take the first exercise (already sorted by ID) for consistency
            guaranteed_exercises.append(exercises[0])
            # Add a second one if we have enough total exercises wanted
            if len(exercises) > 1 and len(guaranteed_exercises) < TOTAL_EXERCISES_WANTED:
                guaranteed_exercises.append(exercises[1])
    
    # Remove guaranteed exercises from the main pool
    guaranteed_ids = {ex['id'] for ex in guaranteed_exercises}
    remaining_pool = [ex for ex in all_suitable_exercises if ex['id'] not in guaranteed_ids]
    
    # Calculate how many more exercises we need
    remaining_needed = TOTAL_EXERCISES_WANTED - len(guaranteed_exercises)
    
    print(f"Duration: {duration}min, Exercises wanted: {TOTAL_EXERCISES_WANTED}, Randomness: {randomness}%, Available: {total_available}, Focus areas: {len(p_focus_area_ids)}")
    print(f"Guaranteed per focus area: {len(guaranteed_exercises)}, Remaining needed: {remaining_needed}")
    
    if remaining_needed > 0 and remaining_pool:
        # Apply randomness to the remaining pool
        fixed_from_remaining = math.ceil(remaining_needed * (100 - randomness) / 100)
        random_from_remaining = remaining_needed - fixed_from_remaining
        
        # Fixed exercises (deterministic, already sorted by ID)
        fixed_exercises = remaining_pool[:fixed_from_remaining]
        
        # Random exercises from remaining pool
        random_pool = remaining_pool[fixed_from_remaining:]
        random.shuffle(random_pool)
        random_exercises = random_pool[:random_from_remaining]
        
        # Combine all exercises
        final_exercises = guaranteed_exercises + fixed_exercises + random_exercises
    else:
        final_exercises = guaranteed_exercises[:TOTAL_EXERCISES_WANTED]
    
    # Final shuffle for better UX while maintaining focus area coverage
    random.shuffle(final_exercises)
    
    # Step 6: Store generated exercises with calculated KG, REPS, SETS
    exercise_ids = [ex['id'] for ex in final_exercises]
    
    # Get user's physical data for 1RM calculation from the database query result
    user_physical_data = {
        'current_weight_kg': float(user_data.get('current_weight_kg', 70.0)) if user_data.get('current_weight_kg') else 70.0,
        'height_cm': user_data.get('height_cm', 170),
        'age': user_data.get('age', 25),
        'fitness_level': user_data.get('fitness_level', 'intermediate')
    }
    
    # Store the generated exercises with calculated values
    storage_success = await db_queries.store_user_generated_exercises(
        db, user_id, exercise_ids, user_physical_data
    )
    
    if not storage_success:
        print(f"Warning: Failed to store generated exercises for user {user_id}")
    
    return [dict(record) for record in final_exercises]


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
    
    # Convert to dict and parse JSON fields
    response_data = dict(status_record)
    
    # Parse direct_exercises_for_today JSON if it's a string
    if isinstance(response_data.get('direct_exercises_for_today'), str):
        import json
        response_data['direct_exercises_for_today'] = json.loads(response_data['direct_exercises_for_today'])
        
    return response_data


@router.get("/equipment", response_model=EquipmentListResponse)
async def get_all_equipment(
    db: asyncpg.Connection = Depends(get_db)
):
    """
    Fetches all equipment grouped by equipment_type as nested structure.
    Returns equipment types with their associated equipment items.
    """
    try:
        # Fetch equipment grouped by type from database
        equipment_data = await db_queries.fetch_all_equipment_grouped_by_type(db)
        
        # Transform the data to match our response model
        equipment_types = []
        for record in equipment_data:
            # Parse the JSON equipment list
            equipment_list = []
            if record['equipment_list'] and record['equipment_list'] != [None]:
                # Handle the case where equipment_list might be a JSON string
                equipment_data_list = record['equipment_list']
                if isinstance(equipment_data_list, str):
                    import json
                    equipment_data_list = json.loads(equipment_data_list)
                
                for equipment in equipment_data_list:
                    if equipment and isinstance(equipment, dict) and equipment.get('id'):  # Skip null equipment
                        equipment_list.append(EquipmentItem(
                            id=equipment['id'],
                            name=equipment['name'],
                            description=equipment.get('description')
                        ))
            
            equipment_types.append(EquipmentTypeResponse(
                equipment_type_id=record['equipment_type_id'],
                equipment_type_name=record['equipment_type_name'],
                equipment_list=equipment_list
            ))
        
        return EquipmentListResponse(equipment_types=equipment_types)
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while fetching equipment: {str(e)}"
        )


@router.get("/exercises", response_model=ExercisesListResponse)
async def get_all_exercises(
    request: Request,
    db: asyncpg.Connection = Depends(get_db)
):
    """
    Fetches all exercises from the database.
    Returns a list of all available exercises with their details.
    """
    try:
        # Try to identify user to apply exclusions (auth optional for this endpoint)
        excluded_ids: set[int] = set()
        try:
            access_token = await get_access_token_from_header(request)
            if access_token:
                token_entry = await db_queries.fetch_access_token(db, access_token)
                if token_entry:
                    user_id = token_entry['user_id']
                    rows = await db.fetch(
                        """
                        SELECT exercise_id FROM user_excluded_exercises_forever 
                        WHERE user_id = $1
                        UNION
                        SELECT exercise_id FROM user_excluded_exercises_today 
                        WHERE user_id = $1 AND excluded_date = CURRENT_DATE
                        """,
                        user_id
                    )
                    excluded_ids = {r['exercise_id'] for r in rows}
        except Exception:
            # Silently ignore auth errors here to keep endpoint usable without token
            excluded_ids = set()

        # Fetch all exercises from database
        exercises_data = await db_queries.fetch_all_exercises(db)
        
        # Transform the data to match our response model
        exercises = []
        for record in exercises_data:
            if excluded_ids and record['id'] in excluded_ids:
                continue
            # Parse focus areas
            focus_areas = []
            if record['focus_areas']:
                # Handle the case where focus_areas might be a JSON string
                focus_areas_data = record['focus_areas']
                if isinstance(focus_areas_data, str):
                    import json
                    focus_areas_data = json.loads(focus_areas_data)
                
                for focus_area in focus_areas_data:
                    if focus_area and isinstance(focus_area, dict) and focus_area.get('id'):
                        focus_areas.append(ExerciseFocusArea(
                            id=focus_area['id'],
                            name=focus_area['name']
                        ))
            
            exercises.append(ExerciseItem(
                id=record['id'],
                name=record['name'],
                description=record.get('description'),
                video_url=record.get('video_url'),
                focus_areas=focus_areas
            ))
        
        return ExercisesListResponse(exercises=exercises)
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while fetching exercises: {str(e)}"
        )


@router.put("/user/me/active-routine", status_code=200)
async def update_user_active_routine(
    routine_update: UserRoutineUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Updates the authenticated user's active routine.

    This operation is fully atomic. If the provided routine_id is invalid
    for the user, an error is returned and no changes are made to the database,
    preserving the previous active routine.
    """
    # Step 1: Get the authenticated user's ID
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")

    user_id = token_entry['user_id']

    # Step 2: Use a transaction to ensure the entire operation is atomic (all or nothing)
    try:
        async with conn.transaction():
            # The new query function performs the safe, two-step update
            success = await db_queries.update_active_routine(conn, user_id, routine_update.routine_id)

            # If the update failed, it means the routine_id was invalid.
            # Raising an exception here will automatically trigger the transaction
            # to be ROLLED BACK, undoing any changes.
            if not success:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Routine with ID {routine_update.routine_id} not found or not assigned to this user."
                )
    except HTTPException as http_exc:
        # Re-raise the specific HTTP exception to be handled by FastAPI
        raise http_exc
    except Exception as e:
        # Catch any other unexpected database errors during the transaction
        return error_response(message=f"An unexpected database error occurred: {str(e)}", status_code=500)

    # Step 3: If the code reaches here, the transaction was committed successfully.
    # Note: A PUT request that modifies an existing resource should return 200 (OK).
    # 201 (Created) is for creating a new resource.
    return success_response(data={}, message="Active routine updated successfully.", status_code=200)



@router.get("/user/me/routines", response_model=List[UserRoutineInfo])
async def list_user_routines(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Fetches a list of all available routines for the authenticated user.

    This endpoint is useful for front-end clients that need to display a list
    of routines from which the user can select a new active plan.
    """
    # 1. Authenticate the user and get their ID
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    
    user_id = token_entry['user_id']

    # 2. Fetch the list of routines from the database
    routines_records = await db_queries.get_user_routines_list(conn, user_id)

    if not routines_records:
        # This case is unlikely due to the trigger, but good practice to handle.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No routines found for this user."
        )

    # 3. Format and return the response
    # The response_model will automatically handle converting the list of records
    # into a list of JSON objects matching the UserRoutineInfo schema.
    return [dict(record) for record in routines_records]


@router.patch("/user/me/active-day", status_code=200)
async def update_user_active_day(
    day_update: UserActiveDayUpdate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Manually sets the active day for the user's current workout routine.

    This allows a user to override the automatic day cycle and choose which
    day of their routine they want to perform, which will then be used for
    generating exercise recommendations.
    """
    # 1. Authenticate the user
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")

    user_id = token_entry['user_id']

    # 2. Clear custom exercises when changing day without saving
    await clear_user_custom_exercises(conn, user_id)
    
    # 3. Perform the update using our new query
    success = await db_queries.set_active_day_for_user(conn, user_id, day_update.day_number)

    # 4. Handle failure
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to set day {day_update.day_number}. It may not be a valid day number for your currently active routine."
        )

    # 5. Return a success response
    return success_response({}, message=f"Successfully set active workout to Day {day_update.day_number}", status_code=201)


@router.patch("/user/me/generated-exercises/{exercise_id}", response_model=UpdateUserGeneratedExerciseResponse)
async def update_user_generated_exercise(
    exercise_id: int,
    update_data: UpdateUserGeneratedExerciseRequest,
    request: Request,
    db: asyncpg.Connection = Depends(get_db)
):
    """
    Updates the weight, reps, or sets for a specific user's generated exercise.
    Only the provided fields will be updated.
    """
    # Authenticate the user
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(db, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")

    user_id = token_entry['user_id']

    # Extract only the fields that were provided in the request
    update_fields = update_data.model_dump(exclude_unset=True)
    
    if not update_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field (weight_kg, reps, or sets) must be provided for update"
        )

    # Update generated exercise first
    updated_record = await db_queries.update_user_generated_exercise(
        db,
        user_id=user_id,
        exercise_id=exercise_id,
        weight_kg=update_fields.get('weight_kg'),
        reps=update_fields.get('reps'),
        sets=update_fields.get('sets')
    )

    # If not found in generated, try updating custom exercise
    if not updated_record:
        custom_updated = await update_user_custom_exercise(
            db,
            user_id=user_id,
            exercise_id=exercise_id,
            weight_kg=update_fields.get('weight_kg'),
            reps=update_fields.get('reps'),
            sets=update_fields.get('sets')
        )

        if custom_updated:
            return UpdateUserGeneratedExerciseResponse(
                id=custom_updated['id'],
                exercise_id=custom_updated['exercise_id'],
                name=custom_updated['name'],
                weight_kg=float(custom_updated['weight_kg']),
                reps=custom_updated['reps'],
                sets=custom_updated['sets'],
                updated_at=custom_updated['updated_at'],
                message="Custom exercise updated successfully"
            )

        # If neither generated nor custom found, return 404
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exercise with ID {exercise_id} not found for this user"
        )

    # Return the updated generated exercise data
    return UpdateUserGeneratedExerciseResponse(
        id=updated_record['id'],
        exercise_id=updated_record['exercise_id'],
        name=updated_record['name'],
        weight_kg=float(updated_record['weight_kg']),
        reps=updated_record['reps'],
        sets=updated_record['sets'],
        updated_at=updated_record['updated_at'],
        message="Exercise updated successfully"
    )


@router.patch("/user/me/routine/reorder-days", response_model=RoutineDayReorderResponse)
async def reorder_routine_days(
    reorder_request: RoutineDayReorderRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Reorders routine days using drag-and-drop logic for the authenticated user's active routine.
    
    When dragging a day to a new position, all days between the source and target shift accordingly.
    For example, dragging day 3 to position 1 in a 5-day routine:
    - Day 3 content goes to day 1
    - Day 1 content shifts to day 2
    - Day 2 content shifts to day 3
    - Days 4 and 5 remain unchanged
    
    The reorder operation is atomic - either all affected days are updated successfully or no changes are made.
    """
    # 1. Authenticate the user
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")

    user_id = token_entry['user_id']

    # 2. Validate the request
    if reorder_request.source_day_number == reorder_request.target_position:
        return RoutineDayReorderResponse(
            message="No reordering needed - source and target are the same",
            source_day_number=reorder_request.source_day_number,
            target_position=reorder_request.target_position,
            affected_days=[]
        )

    # 3. Perform the reorder operation
    try:
        reorder_result = await db_queries.reorder_routine_days_content(
            conn, user_id, reorder_request.source_day_number, reorder_request.target_position
        )
        
        if not reorder_result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to reorder routine days due to an unexpected error."
            )

        # 4. Return success response
        return RoutineDayReorderResponse(
            message=reorder_result["message"],
            source_day_number=reorder_result["source_day_number"],
            target_position=reorder_result["target_position"],
            affected_days=reorder_result["affected_days"]
        )

    except ValueError as ve:
        # Handle specific validation errors from the database function
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve)
        )
    except Exception as e:
        # Handle any other unexpected errors
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while swapping routine days: {str(e)}"
        )



@router.get("/user/me/active-routine/days", response_model=ActiveRoutineDaysResponse)
async def get_active_routine_days_list(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Fetches the details of the user's currently active routine.

    This includes a list of all days within that routine, their respective
    focus areas, and a flag indicating which day is considered the current
    workout day.
    """
    # 1. Authenticate the user and get their ID
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")

    user_id = token_entry['user_id']

    # 2. Fetch the active routine details from the database
    routine_details_record = await db_queries.get_active_routine_days(conn, user_id)

    if not routine_details_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active routine with assigned days found for this user."
        )

    # 3. The record is already structured correctly by the SQL query.
    # We just need to convert it to a dictionary for the response model.
    # The 'days' field is already a JSON string, which Pydantic will parse.
    response_data = dict(routine_details_record)

    # Ensure the 'days' JSON from the DB is parsed into a Python list
    if isinstance(response_data.get('days'), str):
        response_data['days'] = json.loads(response_data['days'])
    
    # Parse nested JSON fields in each day
    for day in response_data.get('days', []):
        if isinstance(day.get('focus_areas'), str):
            day['focus_areas'] = json.loads(day['focus_areas'])
        if isinstance(day.get('direct_exercises'), str):
            day['direct_exercises'] = json.loads(day['direct_exercises'])

    return response_data


@router.get("/focus-areas", response_model=List[FocusAreaInfo])
async def list_all_focus_areas(conn: asyncpg.Connection = Depends(get_db)):
    """
    Provides a complete list of all possible focus areas (e.g., muscle groups)
    available in the system.

    This is a public endpoint designed to provide a list of options for a
    front-end UI, such as in a dropdown or multi-select component when a
    user is customizing their routine.
    """
    focus_areas_records = await db_queries.get_all_focus_areas(conn)
    # The response_model will automatically handle the conversion
    # from a list of database records to a list of JSON objects.
    return [dict(record) for record in focus_areas_records]


@router.post("/user/me/routines/{user_routine_id}/days", response_model=UserRoutineDayResponse, status_code=201)
async def create_day_in_routine(
    user_routine_id: int,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Adds a new day to one of the user's specific routines.
    The day is created with the next available day_number and has no focus areas by default.
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']

    new_day = await db_queries.add_day_to_user_routine(conn, user_id, user_routine_id)

    if not new_day:
        raise HTTPException(status_code=404, detail=f"Routine with ID {user_routine_id} not found for this user.")

    # Create the response object, starting with an empty list of focus areas
    response_data = dict(new_day)
    response_data['focus_areas'] = []
    return response_data


@router.delete("/user/me/routines/{user_routine_id}/days/{day_number}", status_code=204)
async def remove_day_from_routine(
    user_routine_id: int,
    day_number: int,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Deletes a specific day from a user's routine.
    This will also automatically remove all focus areas assigned to that day.
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']

    success = await db_queries.delete_day_from_user_routine(conn, user_id, user_routine_id, day_number)

    if not success:
        raise HTTPException(status_code=404, detail=f"Day {day_number} not found in routine {user_routine_id} for this user.")

    # A 204 response has no body, so we return None
    return None


# --- API for Managing Focus Areas in a Routine Day ---

@router.post("/user/me/routines/{user_routine_id}/days/{day_number}/focus-areas", status_code=201)
async def add_focus_area_to_routine_day(
    user_routine_id: int,
    day_number: int,
    payload: DayFocusAreaRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Assigns a focus area to a specific day within a user's routine.
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']

    try:
        success = await db_queries.add_focus_area_to_day(conn, user_id, user_routine_id, day_number, payload.focus_area_id)
        if not success:
            raise HTTPException(status_code=404, detail=f"Routine {user_routine_id} or Day {day_number} not found for this user.")
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(status_code=400, detail=f"Focus area with ID {payload.focus_area_id} does not exist.")

    return success_response(data={}, message="Focus area added successfully.", status_code=201)


@router.delete("/user/me/routines/{user_routine_id}/days/{day_number}/focus-areas/{focus_area_id}", status_code=204)
async def remove_focus_area_from_routine_day(
    user_routine_id: int,
    day_number: int,
    focus_area_id: int,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Removes a focus area assignment from a specific day in a user's routine.
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']

    success = await db_queries.delete_focus_area_from_day(conn, user_id, user_routine_id, day_number, focus_area_id)

    if not success:
        raise HTTPException(status_code=404, detail="Focus area assignment not found for the specified day and routine.")

    # A 204 response has no body
    return None


# --- PATCH API for Managing Routine Days (Focus Areas OR Direct Exercises) ---

@router.patch("/user/me/routines/{user_routine_id}/days/{day_number}", status_code=200)
async def update_routine_day(
    user_routine_id: int,
    day_number: int,
    payload: Dict[str, Any],
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Updates a specific day in a user's routine. Supports switching between:
    - focus_areas mode: User provides focus_area_ids to set focus areas
    - direct_exercises mode: User provides exercises list with exercise details
    
    The endpoint automatically handles mode switching:
    - If focus_area_ids provided: switches to focus_areas mode, clears exercises
    - If exercises provided: switches to direct_exercises mode, clears focus areas
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']

    focus_area_ids = payload.get('focus_area_ids')
    exercises = payload.get('exercises')
    
    # Validate that only one mode is provided
    if focus_area_ids is not None and exercises is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot provide both focus_area_ids and exercises. Choose one mode."
        )
    
    if focus_area_ids is None and exercises is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide either focus_area_ids or exercises."
        )

    try:
        async with conn.transaction():
            # Handle focus areas mode
            if focus_area_ids is not None:
                success = await db_queries.switch_day_to_focus_areas(
                    conn, user_id, user_routine_id, day_number, focus_area_ids
                )
                if not success:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Routine {user_routine_id} or Day {day_number} not found for this user."
                    )
                return success_response(
                    data={"exercise_mode": "focus_areas", "focus_area_ids": focus_area_ids},
                    message="Day updated to focus areas mode successfully."
                )
            
            # Handle direct exercises mode
            elif exercises is not None:
                # First, get the user_routine_day_id and switch mode
                day_query = """
                SELECT urd.id
                FROM user_routine_days urd
                JOIN user_routines ur ON urd.user_routine_id = ur.id
                WHERE ur.user_id = $1 AND ur.id = $2 AND urd.day_number = $3
                """
                day_result = await conn.fetchrow(day_query, user_id, user_routine_id, day_number)
                
                if not day_result:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Routine {user_routine_id} or Day {day_number} not found for this user."
                    )
                
                user_routine_day_id = day_result['id']
                
                # Clear focus areas and switch mode
                await conn.execute(
                    "DELETE FROM user_routine_day_focus_areas WHERE user_routine_day_id = $1",
                    user_routine_day_id
                )
                await conn.execute(
                    "DELETE FROM user_routine_day_exercises WHERE user_routine_day_id = $1",
                    user_routine_day_id
                )
                await conn.execute(
                    "UPDATE user_routine_days SET exercise_mode = 'direct_exercises' WHERE id = $1",
                    user_routine_day_id
                )
                
                # Add exercises
                exercise_records = []
                for idx, exercise in enumerate(exercises):
                    exercise_id = exercise.get('exercise_id')
                    
                    if not exercise_id:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Each exercise must have an exercise_id."
                        )
                    
                    exercise_records.append((
                        user_routine_day_id, exercise_id, idx + 1
                    ))
                
                # Insert exercises
                if exercise_records:
                    await conn.copy_records_to_table(
                        'user_routine_day_exercises',
                        records=exercise_records,
                        columns=('user_routine_day_id', 'exercise_id', 'order_in_day')
                    )
                
                # Clear custom exercises and generated exercises after saving to routine day
                await clear_user_custom_exercises(conn, user_id)
                await clear_user_generated_exercises(conn, user_id)
                
                return success_response(
                    data={"exercise_mode": "direct_exercises", "exercises_count": len(exercises)},
                    message="Day updated to direct exercises mode successfully."
                )
                
    except asyncpg.ForeignKeyViolationError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ID provided: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred: {str(e)}"
        )


@router.post("/user/me/routines/{user_routine_id}/days/{day_number}/exercises", status_code=201)
async def add_exercise_to_routine_day(
    user_routine_id: int,
    day_number: int,
    payload: Dict[str, Any],
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Adds a direct exercise to a specific day within a user routine.
    Automatically switches the day to 'direct_exercises' mode if not already.
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']

    exercise_id = payload.get('exercise_id')
    
    if not exercise_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="exercise_id is required."
        )

    try:
        success = await db_queries.add_exercise_to_day(
            conn, user_id, user_routine_id, day_number, exercise_id
        )
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Routine {user_routine_id} or Day {day_number} not found for this user."
            )
    except asyncpg.ForeignKeyViolationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Exercise with ID {exercise_id} does not exist."
        )

    return success_response(
        data={"exercise_id": exercise_id},
        message="Exercise added successfully."
    )


@router.delete("/user/me/routines/{user_routine_id}/days/{day_number}/exercises/{exercise_id}", status_code=204)
async def remove_exercise_from_routine_day(
    user_routine_id: int,
    day_number: int,
    exercise_id: int,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Removes a direct exercise from a specific day in a user's routine.
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']

    success = await db_queries.delete_exercise_from_day(
        conn, user_id, user_routine_id, day_number, exercise_id
    )

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exercise assignment not found for the specified day and routine."
        )

    return None


# Custom Exercise Endpoints
@router.patch("/user/me/custom-exercises")
async def add_custom_exercise_endpoint(
    request_data: AddCustomExerciseRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Adds custom exercise(s) to the user's temporary storage.
    Supports both single exercise (exercise_id) and multiple exercises (exercise_ids).
    Calculates weight, reps, sets, and 1RM based on user's matrix settings.
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']

    # Handle single exercise
    if request_data.exercise_id is not None:
        custom_exercise = await add_custom_exercise(conn, user_id, request_data.exercise_id)
        
        if not custom_exercise:
            # Check if exercise is excluded to provide better error message
            exclusion_check = await conn.fetchrow("""
                SELECT 1 FROM user_excluded_exercises_forever 
                WHERE user_id = $1 AND exercise_id = $2
                UNION
                SELECT 1 FROM user_excluded_exercises_today 
                WHERE user_id = $1 AND exercise_id = $2 AND excluded_date = CURRENT_DATE
            """, user_id, request_data.exercise_id)
            
            if exclusion_check:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot add exercise: This exercise is currently excluded. Please remove the exclusion first."
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Exercise not found or failed to add custom exercise"
                )

        # Convert to response format
        exercise_response = CustomExerciseResponse(
            id=custom_exercise['id'],
            exercise_id=custom_exercise['exercise_id'],
            name=custom_exercise['name'],
            description=custom_exercise['description'],
            video_url=custom_exercise['video_url'],
            primary_focus_area=custom_exercise['primary_focus_area'],
            weight_kg=custom_exercise['weight_kg'],
            reps=custom_exercise['reps'],
            sets=custom_exercise['sets'],
            one_rm_calculated=custom_exercise['one_rm_calculated'],
            added_at=custom_exercise['added_at']
        )

        return AddCustomExerciseResponse(
            message="Custom exercise added successfully",
            exercise=exercise_response
        )
    
    # Handle multiple exercises
    elif request_data.exercise_ids is not None:
        successful_exercises = []
        failed_exercise_ids = []
        
        # Process each exercise in a loop
        for exercise_id in request_data.exercise_ids:
            try:
                custom_exercise = await add_custom_exercise(conn, user_id, exercise_id)
                
                if custom_exercise:
                    exercise_response = CustomExerciseResponse(
                        id=custom_exercise['id'],
                        exercise_id=custom_exercise['exercise_id'],
                        name=custom_exercise['name'],
                        description=custom_exercise['description'],
                        video_url=custom_exercise['video_url'],
                        primary_focus_area=custom_exercise['primary_focus_area'],
                        weight_kg=custom_exercise['weight_kg'],
                        reps=custom_exercise['reps'],
                        sets=custom_exercise['sets'],
                        one_rm_calculated=custom_exercise['one_rm_calculated'],
                        added_at=custom_exercise['added_at']
                    )
                    successful_exercises.append(exercise_response)
                else:
                    failed_exercise_ids.append(exercise_id)
            except Exception:
                failed_exercise_ids.append(exercise_id)
        
        # Check if any exercises were added
        total_added = len(successful_exercises)
        if total_added == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No exercises could be added. This may be because the exercises are excluded or don't exist. Please check exercise exclusions and IDs."
            )
        
        # Create response message
        total_requested = len(request_data.exercise_ids)
        failed_count = len(failed_exercise_ids)
        
        if failed_count == 0:
            message = f"All {total_added} custom exercises added successfully"
        else:
            message = f"{total_added} custom exercises added successfully, {failed_count} failed"

        return AddCustomExerciseResponse(
            message=message,
            exercises=successful_exercises,
            total_added=total_added,
            failed_exercises=failed_exercise_ids if failed_exercise_ids else None
        )


@router.get("/user/me/generated-exercises")
async def get_combined_exercises(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Unified endpoint that returns a single 'final' list of exercises for the user.
    Rules:
    - If the current routine day is saved (direct_exercises), return that saved list in order.
    - Otherwise, return generated exercises + any custom exercises (merged, de-duplicated).
    - Exclusions are respected across all sources.
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']

    # Exclusions (forever + today)
    excluded_rows = await conn.fetch(
        """
        SELECT exercise_id FROM user_excluded_exercises_forever WHERE user_id = $1
        UNION
        SELECT exercise_id FROM user_excluded_exercises_today WHERE user_id = $1 AND excluded_date = CURRENT_DATE
        """,
        user_id,
    )
    excluded_ids = {r["exercise_id"] for r in excluded_rows}

    # Source A: generated exercises
    gen_rows = await db_queries.get_user_generated_exercises(conn, user_id)
    gen_rows = [r for r in gen_rows if r["exercise_id"] not in excluded_ids]

    # Source B: custom exercises
    custom_rows = await get_user_custom_exercises(conn, user_id)
    custom_rows = [r for r in custom_rows if r["exercise_id"] not in excluded_ids]

    # Helper mappers (prefer custom values over generated for same exercise_id)
    def map_from_generated(r: Any) -> Dict[str, Any]:
        return {
            "exercise_id": r["exercise_id"],
            "name": r["name"],
            "description": r.get("description"),
            "video_url": r.get("video_url"),
            "primary_focus_area": r.get("primary_focus_area"),
            "weight_kg": float(r["weight_kg"]) if r.get("weight_kg") is not None else 0.0,
            "reps": r.get("reps", 12),
            "sets": r.get("sets", 3),
            "one_rm_calculated": float(r.get("one_rm_calculated", 0.0)),
            "is_custom": False,
            "source": "generated",
        }

    def map_from_custom(r: Any) -> Dict[str, Any]:
        return {
            "exercise_id": r["exercise_id"],
            "name": r["name"],
            "description": r.get("description"),
            "video_url": r.get("video_url"),
            "primary_focus_area": r.get("primary_focus_area"),
            "weight_kg": float(r.get("weight_kg", 0.0)),
            "reps": r.get("reps", 12),
            "sets": r.get("sets", 3),
            "one_rm_calculated": float(r.get("one_rm_calculated", 0.0)),
            "is_custom": True,
            "source": "custom",
        }

    # Build base map by exercise_id (custom takes precedence)
    base_by_id: Dict[int, Dict[str, Any]] = {}
    for r in gen_rows:
        base_by_id[r["exercise_id"]] = map_from_generated(r)
    for r in custom_rows:
        base_by_id[r["exercise_id"]] = map_from_custom(r)

    # Saved routine-day detail (for fallback names/order when saved)
    routine_rows = await get_user_routine_day_exercises(conn, user_id)
    routine_rows = [r for r in routine_rows if r["exercise_id"] not in excluded_ids]
    routine_by_id: Dict[int, Any] = {r["exercise_id"]: r for r in routine_rows}

    final_list: List[Dict[str, Any]] = []

    # If the active day is saved as direct_exercises, use that exact order
    try:
        active_days = await db_queries.get_active_routine_days(conn, user_id)
        if active_days:
            days_val = active_days["days"]
            days = json.loads(days_val) if isinstance(days_val, str) else days_val
            current_day = next((d for d in days if d.get("is_current_day")), None)
            if current_day and current_day.get("exercise_mode") == "direct_exercises":
                direct_list = current_day.get("direct_exercises") or []
                # Filter excluded and order
                ordered_direct = [d for d in direct_list if d.get("id") and d["id"] not in excluded_ids]
                ordered_direct = sorted(
                    ordered_direct,
                    key=lambda x: x.get("order_in_day", 999999)
                )

                for d in ordered_direct:
                    ex_id = int(d["id"])
                    base = base_by_id.get(ex_id)
                    if base:
                        item = {**base, "order_in_day": d.get("order_in_day")}
                    else:
                        r = routine_by_id.get(ex_id)
                        item = {
                            "exercise_id": ex_id,
                            "name": d.get("name") or (r["name"] if r else ""),
                            "description": d.get("description") or (r["description"] if r else None),
                            "video_url": d.get("video_url") or (r["video_url"] if r else None),
                            "primary_focus_area": r["primary_focus_area"] if r else None,
                            "weight_kg": 0.0,
                            "reps": 12,
                            "sets": 3,
                            "one_rm_calculated": 0.0,
                            "is_custom": False,
                            "source": "routine",
                            "order_in_day": d.get("order_in_day"),
                        }
                    final_list.append(item)

    except Exception:
        # Ignore and fall back to unsaved mode below
        pass

    if not final_list:
        # Not saved: return generated first then custom (no duplicates)
        seen: set[int] = set()
        for r in gen_rows:
            ex_id = r["exercise_id"]
            if ex_id in seen:
                continue
            final_list.append(map_from_generated(r))
            seen.add(ex_id)
        for r in custom_rows:
            ex_id = r["exercise_id"]
            if ex_id in seen:
                continue
            final_list.append(map_from_custom(r))
            seen.add(ex_id)

    return {
        "final": final_list,
        "total_count": len(final_list)
    }


@router.post("/user/me/exercises/alternatives", response_model=AlternativeExercisesResponse)
async def get_alternative_exercises(
    request_data: AlternativeExercisesRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Find alternative exercises based on a given exercise ID and user's profile parameters.
    
    This endpoint analyzes the user's onboarding profile (fitness level, equipment, health issues)
    and finds exercises that share similar focus areas with the provided exercise ID.
    The results are ranked by similarity score based on shared focus areas.
    """
    # Authenticate user
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']
    
    # Get original exercise details
    original_exercise = await db_queries.get_exercise_details(conn, request_data.exercise_id)
    if not original_exercise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exercise with ID {request_data.exercise_id} not found"
        )
    
    # Get alternative exercises based on user profile
    alternatives_data = await db_queries.get_alternative_exercises(
        conn, request_data.exercise_id, user_id, limit=15
    )
    
    # Format original exercise data
    original_exercise_dict = {
        'id': original_exercise['id'],
        'name': original_exercise['name'],
        'description': original_exercise['description'],
        'video_url': original_exercise['video_url'],
        'primary_focus_area': original_exercise['primary_focus_area'],
        'focus_areas': original_exercise['focus_areas'] or []
    }
    
    # Format alternative exercises
    alternatives = []
    for alt in alternatives_data:
        alternatives.append(AlternativeExerciseResponse(
            id=alt['id'],
            name=alt['name'],
            description=alt['description'],
            video_url=alt['video_url'],
            primary_focus_area=alt['primary_focus_area'],
            shared_focus_areas=list(alt['shared_focus_areas']) if alt['shared_focus_areas'] else [],
            similarity_score=float(alt['similarity_score'])
        ))
    
    message = f"Found {len(alternatives)} alternative exercises for '{original_exercise['name']}' based on your profile"
    if len(alternatives) == 0:
        message = f"No alternative exercises found for '{original_exercise['name']}' that match your profile criteria"
    
    return AlternativeExercisesResponse(
        original_exercise=original_exercise_dict,
        alternatives=alternatives,
        total_count=len(alternatives),
        message=message
    )


# ========= WORKOUT SESSION MANAGEMENT ENDPOINTS =========

@router.post("/user/workout/start")
async def start_workout(
    request_data: StartWorkoutRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Start a new workout session with the provided exercises.
    Only one active workout session is allowed per user.
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']
    
    async with conn.transaction():
        # Check if user has an active workout session
        existing_session = await conn.fetchrow("""
            SELECT id, started_at 
            FROM workout_sessions 
            WHERE user_id = $1 AND status = 'active'
        """, user_id)
        
        if existing_session:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"You already have an active workout session started at {existing_session['started_at']}"
            )
        
        # Create new workout session
        workout_session = await conn.fetchrow("""
            INSERT INTO workout_sessions (user_id, status)
            VALUES ($1, 'active')
            RETURNING id, user_id, status, started_at, completed_at, total_duration_seconds, notes
        """, user_id)
        
        # Add exercises to the workout session
        exercises = []
        for idx, exercise_data in enumerate(request_data.exercises):
            # Verify exercise exists
            exercise_exists = await conn.fetchrow("""
                SELECT id, name FROM exercises WHERE id = $1
            """, exercise_data['exercise_id'])
            
            if not exercise_exists:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Exercise with ID {exercise_data['exercise_id']} not found"
                )
            
            # Insert workout session exercise
            session_exercise = await conn.fetchrow("""
                INSERT INTO workout_session_exercises 
                (workout_session_id, exercise_id, planned_sets, planned_reps, planned_weight_kg, order_in_workout)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id, workout_session_id, exercise_id, planned_sets, planned_reps, planned_weight_kg, order_in_workout, is_completed, created_at
            """, 
                workout_session['id'],
                exercise_data['exercise_id'],
                exercise_data.get('planned_sets', 3),
                exercise_data.get('planned_reps', 12),
                exercise_data.get('planned_weight_kg', 0.0),
                idx + 1
            )
            
            exercises.append(WorkoutSessionExerciseResponse(
                id=session_exercise['id'],
                workout_session_id=session_exercise['workout_session_id'],
                exercise_id=session_exercise['exercise_id'],
                exercise_name=exercise_exists['name'],
                planned_sets=session_exercise['planned_sets'],
                planned_reps=session_exercise['planned_reps'],
                planned_weight_kg=session_exercise['planned_weight_kg'],
                order_in_workout=session_exercise['order_in_workout'],
                is_completed=session_exercise['is_completed'],
                created_at=session_exercise['created_at']
            ))
        
        session_response = WorkoutSessionResponse(
            id=workout_session['id'],
            user_id=workout_session['user_id'],
            status=WorkoutStatusEnum(workout_session['status']),
            started_at=workout_session['started_at'],
            completed_at=workout_session['completed_at'],
            total_duration_seconds=workout_session['total_duration_seconds'],
            notes=workout_session['notes']
        )
        
        return StartWorkoutResponse(
            message=f"Workout session started successfully with {len(exercises)} exercises",
            workout_session=session_response,
            exercises=exercises
        )


@router.post("/user/workout/add-exercises")
async def add_exercises_to_active_workout(
    request_data: StartWorkoutRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']

    async with conn.transaction():
        # Ensure there is an active session
        active_session = await conn.fetchrow(
            """
            SELECT id, user_id, status, started_at, completed_at, total_duration_seconds, notes
            FROM workout_sessions
            WHERE user_id = $1 AND status = 'active'
            """,
            user_id,
        )

        if not active_session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active workout session found")

        # Get current max order to append after existing items
        current_max_order = await conn.fetchval(
            "SELECT COALESCE(MAX(order_in_workout), 0) FROM workout_session_exercises WHERE workout_session_id = $1",
            active_session['id'],
        )

        # Fetch minimal user data needed for defaults calculation
        user_profile = await conn.fetchrow(
            """
            SELECT is_matrix, randomness, duration, rest_time, objective, fitness_level, current_weight_kg
            FROM users WHERE id = $1
            """,
            user_id,
        )

        added_exercises: List[WorkoutSessionExerciseResponse] = []
        skipped_duplicates: int = 0
        skipped_excluded: int = 0

        for idx, ex_payload in enumerate(request_data.exercises):
            ex_id = ex_payload.get('exercise_id')
            if not ex_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="exercise_id is required for each item")

            # Verify exercise exists and get type/name
            exercise_row = await conn.fetchrow(
                "SELECT id, name, exercise_type FROM exercises WHERE id = $1",
                ex_id,
            )
            if not exercise_row:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Exercise with ID {ex_id} not found")

            # Respect user's exclusions (forever + today)
            exclusion_check = await conn.fetchrow(
                """
                SELECT 1 FROM user_excluded_exercises_forever WHERE user_id = $1 AND exercise_id = $2
                UNION
                SELECT 1 FROM user_excluded_exercises_today WHERE user_id = $1 AND exercise_id = $2 AND excluded_date = CURRENT_DATE
                """,
                user_id,
                ex_id,
            )
            if exclusion_check:
                skipped_excluded += 1
                continue

            # Skip duplicates in the same session
            already_present = await conn.fetchval(
                """
                SELECT 1 FROM workout_session_exercises 
                WHERE workout_session_id = $1 AND exercise_id = $2
                """,
                active_session['id'],
                ex_id,
            )
            if already_present:
                skipped_duplicates += 1
                continue

            # Compute defaults when not explicitly provided
            # Prefer user's custom exercise values if available; otherwise compute from profile
            custom_defaults = await conn.fetchrow(
                "SELECT weight_kg, reps, sets FROM user_custom_exercises WHERE user_id = $1 AND exercise_id = $2",
                user_id,
                ex_id,
            )
            if custom_defaults:
                default_weight = float(custom_defaults['weight_kg'])
                default_reps = int(custom_defaults['reps'])
                default_sets = int(custom_defaults['sets'])
            else:
                default_weight, default_reps, default_sets, _ = calculate_exercise_parameters(user_profile, exercise_row)

            if 'planned_sets' in ex_payload and ex_payload.get('planned_sets') is not None:
                planned_sets = int(ex_payload['planned_sets'])
            else:
                planned_sets = int(default_sets)

            if 'planned_reps' in ex_payload and ex_payload.get('planned_reps') is not None:
                planned_reps = int(ex_payload['planned_reps'])
            else:
                planned_reps = int(default_reps)

            if 'planned_weight_kg' in ex_payload and ex_payload.get('planned_weight_kg') is not None:
                planned_weight = float(ex_payload['planned_weight_kg'])
            else:
                planned_weight = float(default_weight)

            # Insert new session exercise
            session_exercise = await conn.fetchrow(
                """
                INSERT INTO workout_session_exercises
                (workout_session_id, exercise_id, planned_sets, planned_reps, planned_weight_kg, order_in_workout)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id, workout_session_id, exercise_id, planned_sets, planned_reps, planned_weight_kg, order_in_workout, is_completed, created_at
                """,
                active_session['id'],
                ex_id,
                planned_sets,
                planned_reps,
                planned_weight,
                int(current_max_order) + idx + 1,
            )

            added_exercises.append(
                WorkoutSessionExerciseResponse(
                    id=session_exercise['id'],
                    workout_session_id=session_exercise['workout_session_id'],
                    exercise_id=session_exercise['exercise_id'],
                    exercise_name=exercise_row['name'],
                    planned_sets=session_exercise['planned_sets'],
                    planned_reps=session_exercise['planned_reps'],
                    planned_weight_kg=session_exercise['planned_weight_kg'],
                    order_in_workout=session_exercise['order_in_workout'],
                    is_completed=session_exercise['is_completed'],
                    created_at=session_exercise['created_at'],
                )
            )

        session_response = WorkoutSessionResponse(
            id=active_session['id'],
            user_id=active_session['user_id'],
            status=WorkoutStatusEnum(active_session['status']),
            started_at=active_session['started_at'],
            completed_at=active_session['completed_at'],
            total_duration_seconds=active_session['total_duration_seconds'],
            notes=active_session['notes'],
        )

        details = []
        details.append(f"added {len(added_exercises)}")
        if skipped_duplicates:
            details.append(f"skipped {skipped_duplicates} duplicate(s)")
        if skipped_excluded:
            details.append(f"skipped {skipped_excluded} excluded")

        return StartWorkoutResponse(
            message="Added exercises to active workout: " + ", ".join(details),
            workout_session=session_response,
            exercises=added_exercises,
        )


@router.get("/user/workout/status", response_model=WorkoutStatusResponse)
async def get_workout_status(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Check if user has an active workout session and return its details.
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']
    
    # Check for active workout session
    active_session = await conn.fetchrow("""
        SELECT id, user_id, status, started_at, completed_at, total_duration_seconds, notes
        FROM workout_sessions 
        WHERE user_id = $1 AND status = 'active'
    """, user_id)
    
    if not active_session:
        return WorkoutStatusResponse(
            has_active_workout=False,
            active_workout=None,
            exercises=None
        )
    
    # Get exercises for the active session
    session_exercises = await conn.fetch("""
        SELECT wse.id, wse.workout_session_id, wse.exercise_id, e.name as exercise_name,
               wse.planned_sets, wse.planned_reps, wse.planned_weight_kg, 
               wse.order_in_workout, wse.is_completed, wse.created_at
        FROM workout_session_exercises wse
        JOIN exercises e ON wse.exercise_id = e.id
        WHERE wse.workout_session_id = $1
        ORDER BY wse.order_in_workout
    """, active_session['id'])
    
    exercises = [
        WorkoutSessionExerciseResponse(
            id=ex['id'],
            workout_session_id=ex['workout_session_id'],
            exercise_id=ex['exercise_id'],
            exercise_name=ex['exercise_name'],
            planned_sets=ex['planned_sets'],
            planned_reps=ex['planned_reps'],
            planned_weight_kg=ex['planned_weight_kg'],
            order_in_workout=ex['order_in_workout'],
            is_completed=ex['is_completed'],
            created_at=ex['created_at']
        )
        for ex in session_exercises
    ]
    
    # Fetch logs for the current active session (only logs from this user's active session)
    logs_rows = await conn.fetch("""
        SELECT wl.id, wl.user_id, wl.workout_session_id, wl.workout_session_exercise_id, wl.exercise_id,
               wl.set_number, wl.weight_kg, wl.reps_completed, wl.duration_seconds, wl.rest_time_seconds,
               wl.notes, wl.logged_at, e.name AS exercise_name
        FROM workout_logs wl
        JOIN exercises e ON wl.exercise_id = e.id
        WHERE wl.workout_session_id = $1 AND wl.user_id = $2
        ORDER BY wl.logged_at, wl.set_number
    """, active_session['id'], user_id)
    
    workout_logs = [
        WorkoutLogResponse(
            id=wl['id'],
            user_id=wl['user_id'],
            workout_session_id=wl['workout_session_id'],
            workout_session_exercise_id=wl['workout_session_exercise_id'],
            exercise_id=wl['exercise_id'],
            exercise_name=wl['exercise_name'],
            set_number=wl['set_number'],
            weight_kg=wl['weight_kg'],
            reps_completed=wl['reps_completed'],
            duration_seconds=wl['duration_seconds'],
            rest_time_seconds=wl['rest_time_seconds'],
            notes=wl['notes'],
            logged_at=wl['logged_at']
        )
        for wl in logs_rows
    ]
    
    session_response = WorkoutSessionResponse(
        id=active_session['id'],
        user_id=active_session['user_id'],
        status=WorkoutStatusEnum(active_session['status']),
        started_at=active_session['started_at'],
        completed_at=active_session['completed_at'],
        total_duration_seconds=active_session['total_duration_seconds'],
        notes=active_session['notes']
    )
    # Calculate elapsed time so far for the active workout if not completed
    try:
        if active_session['completed_at']:
            elapsed_seconds = active_session['total_duration_seconds']
        else:
            # Use timezone-aware now matching started_at tzinfo
            now_tz = datetime.now(active_session['started_at'].tzinfo) if active_session['started_at'] else datetime.now()
            elapsed_seconds = int((now_tz - active_session['started_at']).total_seconds())
    except Exception:
        elapsed_seconds = None
    
    return WorkoutStatusResponse(
        has_active_workout=True,
        active_workout=session_response,
        exercises=exercises,
        workout_logs=workout_logs,
        total_sets_logged=len(workout_logs),
        total_duration_seconds_so_far=elapsed_seconds
    )


@router.post("/user/me/workout/save", response_model=SaveWorkoutResponse)
async def save_workout_into_active_day(
    request_data: SaveWorkoutRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Save the current list of exercises (generated/custom or active session selection)
    into the user's active routine day. This replaces that day's content and switches
    it to direct_exercises mode.
    """
    # Auth
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']

    # Validate payload
    if not request_data.exercises or not isinstance(request_data.exercises, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="'exercises' must be a non-empty list")

    exercise_ids: list[int] = []
    for item in request_data.exercises:
        eid = item.get('exercise_id') if isinstance(item, dict) else None
        if not eid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Each item must include exercise_id")
        exercise_ids.append(int(eid))

    # Ensure all exercise IDs exist
    rows = await conn.fetch("SELECT id FROM exercises WHERE id = ANY($1::int[])", exercise_ids)
    existing_ids = {r['id'] for r in rows}
    missing = [eid for eid in exercise_ids if eid not in existing_ids]
    if missing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid exercise_ids: {missing}")

    # Replace the active day's exercises
    result = await db_queries.replace_routine_day_exercises(
        conn,
        user_id=user_id,
        day_number=request_data.target_day_number,
        exercise_ids=exercise_ids,
    )
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active routine/day not found")

    saved_list = [SaveWorkoutSavedExercise(exercise_id=e['exercise_id'], order_in_day=e['order_in_day'], name=e.get('name')) for e in result['exercises']]

    return SaveWorkoutResponse(
        message="Workout saved to active day successfully",
        user_routine_id=result['user_routine_id'],
        day_number=result['day_number'],
        total_saved=len(saved_list),
        exercises=saved_list,
    )


@router.post("/user/workout/log-set")
async def log_workout_set(
    request_data: LogSetRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Log a completed set for a specific exercise in the active workout session.
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']
    
    async with conn.transaction():
        # Verify the workout session exercise belongs to the user's active session
        session_exercise = await conn.fetchrow("""
            SELECT wse.id, wse.workout_session_id, wse.exercise_id, e.name as exercise_name,
                   ws.user_id, ws.status
            FROM workout_session_exercises wse
            JOIN workout_sessions ws ON wse.workout_session_id = ws.id
            JOIN exercises e ON wse.exercise_id = e.id
            WHERE wse.id = $1 AND ws.user_id = $2 AND ws.status = 'active'
        """, request_data.workout_session_exercise_id, user_id)
        
        if not session_exercise:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Workout session exercise not found or workout is not active"
            )
        
        # Check if this set number already exists for this exercise
        existing_log = await conn.fetchrow("""
            SELECT id FROM workout_logs 
            WHERE workout_session_exercise_id = $1 AND set_number = $2
        """, request_data.workout_session_exercise_id, request_data.set_number)
        
        if existing_log:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Set {request_data.set_number} has already been logged for this exercise"
            )
        
        # Insert the workout log
        workout_log = await conn.fetchrow("""
            INSERT INTO workout_logs 
            (user_id, workout_session_id, workout_session_exercise_id, exercise_id, 
             set_number, weight_kg, reps_completed, duration_seconds, rest_time_seconds, notes)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING id, user_id, workout_session_id, workout_session_exercise_id, exercise_id,
                      set_number, weight_kg, reps_completed, duration_seconds, rest_time_seconds, notes, logged_at
        """, 
            user_id,
            session_exercise['workout_session_id'],
            request_data.workout_session_exercise_id,
            session_exercise['exercise_id'],
            request_data.set_number,
            request_data.weight_kg,
            request_data.reps_completed,
            request_data.duration_seconds,
            request_data.rest_time_seconds,
            request_data.notes
        )
        
        log_response = WorkoutLogResponse(
            id=workout_log['id'],
            user_id=workout_log['user_id'],
            workout_session_id=workout_log['workout_session_id'],
            workout_session_exercise_id=workout_log['workout_session_exercise_id'],
            exercise_id=workout_log['exercise_id'],
            exercise_name=session_exercise['exercise_name'],
            set_number=workout_log['set_number'],
            weight_kg=workout_log['weight_kg'],
            reps_completed=workout_log['reps_completed'],
            duration_seconds=workout_log['duration_seconds'],
            rest_time_seconds=workout_log['rest_time_seconds'],
            notes=workout_log['notes'],
            logged_at=workout_log['logged_at']
        )
        
        return LogSetResponse(
            message=f"Set {request_data.set_number} logged successfully for {session_exercise['exercise_name']}",
            workout_log=log_response
        )


@router.post("/user/workout/complete")
async def complete_workout(
    request_data: CompleteWorkoutRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Complete the active workout session and create workout history entry.
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']
    
    async with conn.transaction():
        # Get active workout session
        active_session = await conn.fetchrow("""
            SELECT id, started_at
            FROM workout_sessions 
            WHERE user_id = $1 AND status = 'active'
        """, user_id)
        
        if not active_session:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active workout session found"
            )
        
        # Calculate workout statistics
        workout_stats = await conn.fetchrow("""
            SELECT 
                COUNT(DISTINCT wse.exercise_id) as total_exercises,
                COUNT(wl.id) as total_sets,
                EXTRACT(EPOCH FROM (NOW() - $2))::INTEGER as total_duration_seconds
            FROM workout_session_exercises wse
            LEFT JOIN workout_logs wl ON wse.id = wl.workout_session_exercise_id
            WHERE wse.workout_session_id = $1
        """, active_session['id'], active_session['started_at'])
        
        # Update workout session to completed
        completed_session = await conn.fetchrow("""
            UPDATE workout_sessions 
            SET status = 'completed', 
                completed_at = NOW(), 
                total_duration_seconds = $2,
                notes = $3
            WHERE id = $1
            RETURNING id, user_id, status, started_at, completed_at, total_duration_seconds, notes
        """, active_session['id'], workout_stats['total_duration_seconds'], request_data.notes)
        
        # Create workout history entry
        workout_history = await conn.fetchrow("""
            INSERT INTO workout_history 
            (user_id, workout_session_id, workout_date, total_exercises, total_sets, 
             total_duration_seconds, calories_burned, notes)
            VALUES ($1, $2, CURRENT_DATE, $3, $4, $5, $6, $7)
            RETURNING id, user_id, workout_session_id, workout_date, total_exercises, 
                      total_sets, total_duration_seconds, calories_burned, notes, created_at
        """, 
            user_id,
            active_session['id'],
            workout_stats['total_exercises'],
            workout_stats['total_sets'],
            workout_stats['total_duration_seconds'],
            0,  # calories_burned - can be calculated later based on exercises and duration
            request_data.notes
        )
        
        session_response = WorkoutSessionResponse(
            id=completed_session['id'],
            user_id=completed_session['user_id'],
            status=WorkoutStatusEnum(completed_session['status']),
            started_at=completed_session['started_at'],
            completed_at=completed_session['completed_at'],
            total_duration_seconds=completed_session['total_duration_seconds'],
            notes=completed_session['notes']
        )
        
        history_response = WorkoutHistoryResponse(
            id=workout_history['id'],
            user_id=workout_history['user_id'],
            workout_session_id=workout_history['workout_session_id'],
            workout_date=workout_history['workout_date'].strftime('%Y-%m-%d'),
            total_exercises=workout_history['total_exercises'],
            total_sets=workout_history['total_sets'],
            total_duration_seconds=workout_history['total_duration_seconds'],
            calories_burned=workout_history['calories_burned'],
            notes=workout_history['notes'],
            created_at=workout_history['created_at']
        )
        
        return CompleteWorkoutResponse(
            message=f"Workout completed successfully!",
            workout_session=session_response,
            workout_history=history_response
        )


@router.get("/user/workout/history")
async def get_workout_history(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
    limit: int = 20,
    offset: int = 0
):
    """
    Get user's workout history grouped by date with aggregated exercise data.
    """
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    user_id = token_entry['user_id']
    
    # Get workout history grouped by date
    daily_history = await conn.fetch("""
        SELECT 
            wh.workout_date,
            COUNT(DISTINCT wh.workout_session_id) as total_workouts,
            SUM(wh.total_exercises) as total_exercises,
            SUM(wh.total_sets) as total_sets,
            SUM(wh.total_duration_seconds) as total_duration_seconds
        FROM workout_history wh
        WHERE wh.user_id = $1
        GROUP BY wh.workout_date
        ORDER BY wh.workout_date DESC
        LIMIT $2 OFFSET $3
    """, user_id, limit, offset)
    
    # Get total count of unique workout dates
    total_count = await conn.fetchval("""
        SELECT COUNT(DISTINCT workout_date) FROM workout_history WHERE user_id = $1
    """, user_id)
    
    # For each date, get detailed workout sessions
    history = []
    for daily_record in daily_history:
        workout_date = daily_record['workout_date']
        
        # Get all workout sessions for this date
        sessions_data = await conn.fetch("""
            SELECT 
                ws.id as workout_session_id,
                ws.started_at,
                ws.completed_at,
                ws.total_duration_seconds,
                ws.notes
            FROM workout_sessions ws
            JOIN workout_history wh ON ws.id = wh.workout_session_id
            WHERE wh.user_id = $1 AND wh.workout_date = $2
            ORDER BY ws.started_at
        """, user_id, workout_date)
        
        workout_sessions = []
        for session in sessions_data:
            # Get exercise summary for this session
            exercises_data = await conn.fetch("""
                SELECT 
                    e.id as exercise_id,
                    e.name as exercise_name,
                    COUNT(wl.id) as total_sets,
                    ROUND(AVG(wl.weight_kg), 1) as avg_weight,
                    ROUND(AVG(wl.reps_completed), 0) as avg_reps
                FROM workout_session_exercises wse
                JOIN exercises e ON wse.exercise_id = e.id
                LEFT JOIN workout_logs wl ON wse.id = wl.workout_session_exercise_id
                WHERE wse.workout_session_id = $1
                GROUP BY e.id, e.name
                ORDER BY MIN(wse.order_in_workout)
            """, session['workout_session_id'])
            
            exercises = []
            for ex in exercises_data:
                # Create summary string like "4 sets × 10 reps at 9kg"
                if ex['total_sets'] > 0:
                    sets_summary = f"{ex['total_sets']} sets × {int(ex['avg_reps'])} reps at {ex['avg_weight']}kg"
                else:
                    sets_summary = "No sets logged"
                
                exercises.append(ExerciseSummary(
                    exercise_id=ex['exercise_id'],
                    exercise_name=ex['exercise_name'],
                    total_sets=ex['total_sets'],
                    sets_summary=sets_summary
                ))
            
            workout_sessions.append(WorkoutSessionSummary(
                workout_session_id=session['workout_session_id'],
                started_at=session['started_at'],
                completed_at=session['completed_at'],
                total_duration_seconds=session['total_duration_seconds'] or 0,
                exercises=exercises,
                notes=session['notes']
            ))
        
        history.append(DailyWorkoutHistory(
            workout_date=workout_date.strftime('%Y-%m-%d'),
            total_workouts=daily_record['total_workouts'],
            total_exercises=daily_record['total_exercises'],
            total_sets=daily_record['total_sets'],
            total_duration_seconds=daily_record['total_duration_seconds'],
            workout_sessions=workout_sessions
        ))
    
    return WorkoutHistoryListResponse(
        history=history,
        total_count=total_count
    )


# ========= EXERCISE EXCLUSION ENDPOINTS =========

@router.post("/user/exercises/exclude", response_model=ExcludeExerciseResponse)
async def exclude_exercise(
    request: Request,
    exclude_data: ExcludeExerciseRequest,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Exclude an exercise for the user with two options:
    1. 'forever' - Permanently exclude this exercise for the user
    2. 'today' - Exclude this exercise only for today's workout generation
    """
    # Authenticate user
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    
    user_id = token_entry['user_id']
    
    # Build the list of exercise IDs to exclude (supports single or multiple)
    ids: List[int] = []
    if exclude_data.exercise_id is not None:
        ids = [exclude_data.exercise_id]
    elif exclude_data.exercise_ids is not None:
        # Deduplicate and sanitize
        ids = list({int(x) for x in exclude_data.exercise_ids if x is not None})
    
    if not ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No exercise IDs provided")
    
    # Verify all exercises exist
    rows = await conn.fetch(
        """
        SELECT id, name FROM exercises WHERE id = ANY($1::int[])
        """,
        ids
    )
    found_ids = {r['id'] for r in rows}
    missing = [eid for eid in ids if eid not in found_ids]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exercises not found with IDs: {missing}"
        )
    # Map id->name for messaging
    id_to_name = {r['id']: r['name'] for r in rows}
    
    try:
        # Perform upserts within a transaction for atomicity
        async with conn.transaction():
            if exclude_data.exclusion_type == ExclusionTypeEnum.forever:
                for ex_id in ids:
                    await conn.fetchrow(
                        """
                        INSERT INTO user_excluded_exercises_forever 
                        (user_id, exercise_id, reason)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, exercise_id) 
                        DO UPDATE SET 
                            excluded_at = CURRENT_TIMESTAMP,
                            reason = EXCLUDED.reason
                        RETURNING id
                        """,
                        user_id, ex_id, exclude_data.reason
                    )
                if len(ids) == 1:
                    message = f"Exercise '{id_to_name[ids[0]]}' has been permanently excluded from your workouts"
                else:
                    message = f"{len(ids)} exercises have been permanently excluded from your workouts"
            else:  # today
                for ex_id in ids:
                    await conn.fetchrow(
                        """
                        INSERT INTO user_excluded_exercises_today 
                        (user_id, exercise_id, excluded_date, reason)
                        VALUES ($1, $2, CURRENT_DATE, $3)
                        ON CONFLICT (user_id, exercise_id, excluded_date) 
                        DO UPDATE SET 
                            excluded_at = CURRENT_TIMESTAMP,
                            reason = EXCLUDED.reason
                        RETURNING id
                        """,
                        user_id, ex_id, exclude_data.reason
                    )
                if len(ids) == 1:
                    message = f"Exercise '{id_to_name[ids[0]]}' has been excluded from today's workout generation"
                else:
                    message = f"{len(ids)} exercises have been excluded from today's workout generation"

        # Build response (single vs multiple)
        if len(ids) == 1:
            return ExcludeExerciseResponse(
                success=True,
                message=message,
                exclusion_type=exclude_data.exclusion_type.value,
                exercise_id=ids[0]
            )
        else:
            return ExcludeExerciseResponse(
                success=True,
                message=message,
                exclusion_type=exclude_data.exclusion_type.value,
                exercise_ids=ids,
                total_excluded=len(ids)
            )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to exclude exercise: {str(e)}"
        )


@router.get("/user/exercises/excluded", response_model=UserExcludedExercisesResponse)
async def get_excluded_exercises(
    request: Request,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Get all excluded exercises for the user (both forever and today exclusions).
    """
    # Authenticate user
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    
    user_id = token_entry['user_id']
    
    # Get forever excluded exercises
    forever_excluded_data = await conn.fetch("""
        SELECT 
            uef.id,
            uef.exercise_id,
            e.name as exercise_name,
            uef.excluded_at,
            uef.reason
        FROM user_excluded_exercises_forever uef
        JOIN exercises e ON uef.exercise_id = e.id
        WHERE uef.user_id = $1
        ORDER BY uef.excluded_at DESC
    """, user_id)
    
    # Get today excluded exercises
    today_excluded_data = await conn.fetch("""
        SELECT 
            uet.id,
            uet.exercise_id,
            e.name as exercise_name,
            uet.excluded_at,
            uet.excluded_date,
            uet.reason
        FROM user_excluded_exercises_today uet
        JOIN exercises e ON uet.exercise_id = e.id
        WHERE uet.user_id = $1 AND uet.excluded_date = CURRENT_DATE
        ORDER BY uet.excluded_at DESC
    """, user_id)
    
    # Format forever excluded exercises
    forever_excluded = [
        ExcludedExerciseItem(
            id=item['id'],
            exercise_id=item['exercise_id'],
            exercise_name=item['exercise_name'],
            exclusion_type='forever',
            excluded_at=item['excluded_at'],
            reason=item['reason']
        )
        for item in forever_excluded_data
    ]
    
    # Format today excluded exercises
    today_excluded = [
        ExcludedExerciseItem(
            id=item['id'],
            exercise_id=item['exercise_id'],
            exercise_name=item['exercise_name'],
            exclusion_type='today',
            excluded_at=item['excluded_at'],
            excluded_date=item['excluded_date'].strftime('%Y-%m-%d'),
            reason=item['reason']
        )
        for item in today_excluded_data
    ]
    
    return UserExcludedExercisesResponse(
        forever_excluded=forever_excluded,
        today_excluded=today_excluded,
        total_count=len(forever_excluded) + len(today_excluded)
    )


@router.delete("/user/exercises/exclude", response_model=RemoveExclusionResponse)
async def remove_exercise_exclusion(
    request: Request,
    remove_data: RemoveExclusionRequest,
    conn: asyncpg.Connection = Depends(get_db)
):
    """
    Remove an exercise exclusion for the user.
    """
    # Authenticate user
    access_token = await get_access_token_from_header(request)
    token_entry = await db_queries.fetch_access_token(conn, access_token)
    if not token_entry:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token")
    
    user_id = token_entry['user_id']
    
    # Verify exercise exists
    exercise = await conn.fetchrow("""
        SELECT id, name FROM exercises WHERE id = $1
    """, remove_data.exercise_id)
    
    if not exercise:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exercise with ID {remove_data.exercise_id} not found"
        )
    
    try:
        if remove_data.exclusion_type == ExclusionTypeEnum.forever:
            # Remove from permanent exclusions
            result = await conn.execute("""
                DELETE FROM user_excluded_exercises_forever 
                WHERE user_id = $1 AND exercise_id = $2
            """, user_id, remove_data.exercise_id)
            
            if result == "DELETE 0":
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No permanent exclusion found for exercise '{exercise['name']}'"
                )
            
            message = f"Permanent exclusion removed for exercise '{exercise['name']}'"
            
        else:  # today
            # Remove from today's exclusions
            result = await conn.execute("""
                DELETE FROM user_excluded_exercises_today 
                WHERE user_id = $1 AND exercise_id = $2 AND excluded_date = CURRENT_DATE
            """, user_id, remove_data.exercise_id)
            
            if result == "DELETE 0":
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No today exclusion found for exercise '{exercise['name']}'"
                )
            
            message = f"Today's exclusion removed for exercise '{exercise['name']}'"
        
        return RemoveExclusionResponse(
            success=True,
            message=message,
            exercise_id=remove_data.exercise_id,
            exclusion_type=remove_data.exclusion_type.value
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to remove exercise exclusion: {str(e)}"
        )


