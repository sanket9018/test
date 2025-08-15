import asyncpg
from typing import Optional, Dict, List
import json

async def execute_query(conn: asyncpg.Connection, query: str, params: dict):
    # result = await db.execute(query, params)
    result = await conn.fetchrow(query, *params)  # Unpack params dictionary

    # await db.commit()
    return result
    
# Motivation
async def get_motivation_id(conn: asyncpg.Connection, motivation_name: str) -> Optional[int]:
    row = await conn.fetchrow("SELECT id FROM motivations WHERE name = $1", motivation_name)
    return row['id'] if row else None

# Health Issue
async def get_health_issue_id(conn: asyncpg.Connection, health_issue_name: str) -> Optional[int]:
    row = await conn.fetchrow("SELECT id FROM health_issues WHERE name = $1", health_issue_name)
    return row['id'] if row else None


async def fetch_access_token(conn: asyncpg.Connection, access_token: str):
    """Fetch a valid access token"""

    query = """
        SELECT id, user_id from token_blocklist
        WHERE access_token = $1 AND revoked = false
    """
    result = await execute_query(conn, query, [access_token])

    return result


async def token_verify(conn: asyncpg.Connection, token: str):
    query = "SELECT revoked FROM token_blocklist WHERE access_token = $1"
    result = await execute_query(conn, query, [token])

    return result 

# MODIFIED: Simplified to match the new 'users' table schema (no routine_id).
async def insert_user(conn: asyncpg.Connection, user_data: Dict) -> asyncpg.Record:
    """
    Inserts a single user record into the 'users' table.
    The database trigger will handle creating the default user routines.
    """
    query = """
        INSERT INTO users (
            name, email, password_hash, gender, age, height_cm, 
            current_weight_kg, target_weight_kg, fitness_level, 
            activity_level, workouts_per_week
        ) VALUES (
            $1, $2, $3, $4::gender_enum, $5, $6, $7, $8, 
            $9::fitness_level_enum, $10::activity_level_enum, $11
        ) RETURNING id, created_at, updated_at;
    """
    return await conn.fetchrow(
        query,
        user_data['name'], 
        user_data['email'], 
        user_data['password_hash'],
        user_data['gender'], 
        user_data['age'], 
        user_data['height_cm'],
        user_data['current_weight_kg'], 
        user_data['target_weight_kg'],
        user_data['fitness_level'], 
        user_data['activity_level'],
        user_data['workouts_per_week']
    )

# This function is correct and essential for the new flow.
async def set_initial_active_routine(conn: asyncpg.Connection, user_id: int, routine_id: int):
    """
    Sets a specific routine for a user as active. This is called after the user
    and their default routines have been created by the trigger.
    """
    if not routine_id:
        return
        
    # Step 1: Deactivate all routines for the user
    await conn.execute("UPDATE user_routines SET is_active = FALSE WHERE user_id = $1", user_id)
    # Step 2: Activate only the specified routine
    query = """
        UPDATE user_routines
        SET is_active = TRUE
        WHERE user_id = $1 AND routine_id = $2;
    """
    await conn.execute(query, user_id, routine_id)

# These functions remain correct and unchanged.
async def link_user_to_items(conn: asyncpg.Connection, user_id: int, item_ids: List[int], table_name: str, column_name: str):
    """Performs a bulk insert to link a user to multiple items in a junction table."""
    if not item_ids:
        return

    # Use copy_records_to_table for efficient bulk inserts.
    records_to_insert = [(user_id, item_id) for item_id in item_ids]
    await conn.copy_records_to_table(
        table_name,
        records=records_to_insert,
        columns=('user_id', column_name),
    )

async def link_user_to_days(conn: asyncpg.Connection, user_id: int, days: List[str]):
    """Performs a bulk insert for user workout days."""
    if not days:
        return

    # Use copy_records_to_table for efficient bulk inserts.
    records_to_insert = [(user_id, day) for day in days]
    await conn.copy_records_to_table(
        'user_workout_days',
        records=records_to_insert,
        columns=('user_id', 'day'),
    )

# You can add more queries as needed for other user operations.

# Fetch user with all related details

async def fetch_user_with_routines(conn: asyncpg.Connection, user_id: int) -> Optional[asyncpg.Record]:
    """
    Fetches a user's complete profile by pre-aggregating related data in CTEs
    and then joining them, avoiding complex final grouping.
    """
    query = """
    WITH user_routine_details AS (
        -- This CTE for routines is perfect, no changes needed.
        WITH day_focus_areas AS (
            SELECT
                urda.user_routine_day_id,
                json_agg(json_build_object('id', fa.id, 'name', fa.name)) AS focus_areas
            FROM user_routine_day_focus_areas urda
            JOIN focus_areas fa ON urda.focus_area_id = fa.id
            GROUP BY urda.user_routine_day_id
        ),
        routine_days_agg AS (
            SELECT
                urd.user_routine_id,
                json_agg(
                    json_build_object(
                        'id', urd.id,
                        'day_number', urd.day_number,
                        'focus_areas', COALESCE(dfa.focus_areas, '[]'::json)
                    ) ORDER BY urd.day_number
                ) AS days
            FROM user_routine_days urd
            LEFT JOIN day_focus_areas dfa ON urd.id = dfa.user_routine_day_id
            GROUP BY urd.user_routine_id
        )
        SELECT
            ur.user_id,
            json_agg(
                json_build_object(
                    'id', ur.id,
                    'name', r.name,
                    'is_active', ur.is_active,
                    'days', COALESCE(rda.days, '[]'::json)
                ) ORDER BY r.id
            ) AS routines
        FROM user_routines ur
        JOIN routines r ON ur.routine_id = r.id
        LEFT JOIN routine_days_agg rda ON ur.id = rda.user_routine_id
        WHERE ur.user_id = $1
        GROUP BY ur.user_id
    ),
    -- NEW: Pre-aggregate user goals into an array
    user_goals_agg AS (
        SELECT
            ug.user_id,
            json_agg(g.name ORDER BY g.name) AS goals
        FROM user_goals ug
        JOIN goals g ON g.id = ug.goal_id
        WHERE ug.user_id = $1
        GROUP BY ug.user_id
    ),
    -- NEW: Pre-aggregate user equipment into an array
    user_equipment_agg AS (
        SELECT
            ue.user_id,
            json_agg(e.name ORDER BY e.name) AS equipment
        FROM user_equipment ue
        JOIN equipment e ON e.id = ue.equipment_id
        WHERE ue.user_id = $1
        GROUP BY ue.user_id
    ),
    user_health_issues_agg AS (
        SELECT
            uhi.user_id,
            json_agg(hi.name) as health_issues
        FROM user_health_issues uhi
        JOIN health_issues hi ON uhi.health_issue_id = hi.id
        WHERE uhi.user_id = $1
        GROUP BY uhi.user_id
    ),
    -- NEW: Pre-aggregate user motivations into an array
    user_motivations_agg AS (
        SELECT
            um.user_id,
            json_agg(m.name) as motivations
        FROM user_motivations um
        JOIN motivations m ON um.motivation_id = m.id
        WHERE um.user_id = $1
        GROUP BY um.user_id
    )
    -- FINAL SELECT: Join the main table with the pre-aggregated CTEs.
    -- No GROUP BY is needed here because each join is on a 1-to-1 basis.
    SELECT
        u.id,
        u.name,
        u.email,
        u.gender,
        u.age,
        u.height_cm,
        u.randomness,
        u.circute_training,
        u.rapge_ranges,
        u.duration,
        u.rest_time,
        u.objective,
        u.current_weight_kg,
        u.target_weight_kg,
        u.fitness_level,
        u.activity_level,
        u.workouts_per_week,
        -- Use COALESCE to gracefully handle users with no goals/equipment etc.
        COALESCE(uga.goals, '[]'::json) AS goals,
        COALESCE(uea.equipment, '[]'::json) AS equipment,
        COALESCE(uh.health_issues, '[]'::json) AS health_issues,
        COALESCE(urd.routines, '[]'::json) AS routines,
        COALESCE(uma.motivations, '[]'::json) as motivations,
        u.created_at,
        u.updated_at
    FROM users u
    -- Join our pre-aggregated CTEs
    LEFT JOIN user_routine_details urd ON u.id = urd.user_id
    LEFT JOIN user_goals_agg uga ON u.id = uga.user_id
    LEFT JOIN user_equipment_agg uea ON u.id = uea.user_id
    LEFT JOIN user_health_issues_agg uh ON u.id = uh.user_id
    LEFT JOIN user_motivations_agg uma ON u.id = uma.user_id
    WHERE u.id = $1; -- Filter for the specific user
    """
    return await conn.fetchrow(query, user_id)

async def fetch_all_motivations(conn: asyncpg.Connection):
    """Fetches all motivations from the database."""
    return await conn.fetch("SELECT id, name FROM motivations ORDER BY id")

async def fetch_all_goal(conn: asyncpg.Connection):
    """Fetches all equipment from the database."""
    return await conn.fetch("SELECT id, name FROM goals ORDER BY id")

async def fetch_all_health_issues(conn: asyncpg.Connection):
    """Fetches all health issues from the database."""
    return await conn.fetch("SELECT id, name FROM health_issues ORDER BY id")

async def fetch_all_equipment_grouped_by_type(conn: asyncpg.Connection):
    """Fetches all equipment grouped by equipment_type as nested structure."""
    query = """
    SELECT 
        et.id as equipment_type_id,
        et.name as equipment_type_name,
        json_agg(
            json_build_object(
                'id', e.id,
                'name', e.name,
                'description', e.description
            ) ORDER BY e.id
        ) as equipment_list
    FROM equipment_types et
    LEFT JOIN equipment e ON et.id = e.equipment_type_id
    GROUP BY et.id, et.name
    ORDER BY et.id;
    """
    return await conn.fetch(query)

async def fetch_all_exercises(conn: asyncpg.Connection):
    """Fetches all exercises from the database with their focus areas."""
    query = """
    SELECT 
        e.id,
        e.name,
        e.description,
        e.video_url,
        json_agg(
            json_build_object(
                'id', fa.id,
                'name', fa.name
            ) ORDER BY fa.id
        ) FILTER (WHERE fa.id IS NOT NULL) as focus_areas
    FROM exercises e
    LEFT JOIN exercise_focus_areas efa ON e.id = efa.exercise_id
    LEFT JOIN focus_areas fa ON efa.focus_area_id = fa.id
    GROUP BY e.id, e.name, e.description, e.video_url
    ORDER BY e.id;
    """
    return await conn.fetch(query)


# Add these new functions to your existing db_queries.py
async def get_user_profile_for_workout(conn: asyncpg.Connection, user_id: int):

    """
    Fetches a user's complete profile needed for workout generation using IDs.
    This single query gathers all necessary information from junction tables.
    """
    query = """
    SELECT 
        u.fitness_level,
        COALESCE(ARRAY_AGG(DISTINCT ufa.focus_area_id) FILTER (WHERE ufa.focus_area_id IS NOT NULL), '{}') AS focus_area_ids,
        COALESCE(ARRAY_AGG(DISTINCT ue.equipment_id) FILTER (WHERE ue.equipment_id IS NOT NULL), '{}') AS equipment_ids,
        COALESCE(ARRAY_AGG(DISTINCT uhi.health_issue_id) FILTER (WHERE uhi.health_issue_id IS NOT NULL), '{}') AS health_issue_ids
    FROM users u
    LEFT JOIN user_focus_areas ufa ON u.id = ufa.user_id
    LEFT JOIN user_equipment ue ON u.id = ue.user_id
    LEFT JOIN user_health_issues uhi ON u.id = uhi.user_id
    WHERE u.id = $1
    GROUP BY u.id, u.fitness_level;
    """
    return await conn.fetchrow(query, user_id)

# MODIFIED: This is the core of the update.

async def get_recommended_exercises(
    conn: asyncpg.Connection,
    fitness_level: str,
    focus_area_ids: List[int],
    equipment_ids: List[int],
    health_issue_ids: List[int],
    exercises_per_focus: int,
    total_limit: int
) -> List[asyncpg.Record]:
    """
    Selects a balanced, personalized set of exercises, ensuring no duplicates.
    
    FIXED: Uses a subquery with DISTINCT ON to ensure each exercise is selected
    only once, even if it matches multiple focus areas. The final result is
    then shuffled to provide variety.
    """
#            ROW_NUMBER() OVER(PARTITION BY edfa.focus_area_id ORDER BY RANDOM()) as rn

    query = """
    WITH RankedExercises AS (
        -- This CTE finds all possible exercise matches for each focus area.
        SELECT
            e.id,
            e.name,
            e.description,
            e.video_url,
            fa.name as primary_focus_area,
            ROW_NUMBER() OVER(PARTITION BY edfa.focus_area_id) as rn
        FROM exercises e
        JOIN exercise_focus_areas edfa ON e.id = edfa.exercise_id
        JOIN focus_areas fa ON edfa.focus_area_id = fa.id
        WHERE
            edfa.focus_area_id = ANY($1::int[])
            AND EXISTS (
                SELECT 1 FROM exercise_fitness_levels efl
                WHERE efl.exercise_id = e.id AND efl.fitness_level = $2::fitness_level_enum
            )
            AND EXISTS (
                SELECT 1 FROM exercise_equipment ee
                WHERE ee.exercise_id = e.id AND ee.equipment_id = ANY($3::int[])
            )
            AND NOT EXISTS (
                SELECT 1 FROM exercise_contraindications ec
                WHERE ec.exercise_id = e.id AND ec.health_issue_id = ANY($4::int[])
            )
    )
    -- FINAL SELECTION: This part is rewritten to handle duplicates.
    SELECT id, name, description, video_url, primary_focus_area
    FROM (
        -- Inner subquery selects each exercise only ONCE, picking its highest-ranked version.
        SELECT DISTINCT ON (id) *
        FROM RankedExercises
        WHERE rn <= $5 -- Get the top N candidates for each muscle group
        ORDER BY id, rn -- Important for DISTINCT ON to work predictably
    ) AS UniqueTopExercises
    
    LIMIT $6;
    """
    #ORDER BY RANDOM() -- Shuffle the final, unique list
    return await conn.fetch(
        query,
        focus_area_ids,         # $1
        fitness_level,          # $2
        equipment_ids,          # $3
        health_issue_ids,       # $4
        exercises_per_focus,    # $5
        total_limit             # $6
    )
    

# NO CHANGES NEEDED to this helper function.
async def get_equipment_id_by_name(conn: asyncpg.Connection, name: str) -> Optional[int]:
    """Fetches the ID of an equipment item by its name."""
    return await conn.fetchval("SELECT id FROM equipment WHERE name = $1", name)


async def get_profile_and_active_day_focus(conn: asyncpg.Connection, user_id: int):
    """
    Fetches a user's base profile AND the focus area IDs for their currently
    active routine, based on a cyclical day calculation since user registration.

    This version calculates the number of days that have passed since the user
    was created, and uses a modulo operator with the total number of days in their
    active routine to determine which day of the cycle it is. This ensures that
    workouts are proposed cyclically and avoids issues with fixed weekly schedules.
    """
    query = """
    WITH UserBaseProfile AS (
        -- Fetch essential user profile information, including creation date.
        SELECT
            u.id AS user_id,
            u.fitness_level,
            u.created_at, -- Needed to calculate the workout day
            COALESCE(json_agg(DISTINCT ue.equipment_id) FILTER (WHERE ue.equipment_id IS NOT NULL), '[]'::json) AS equipment_ids,
            COALESCE(json_agg(DISTINCT uhi.health_issue_id) FILTER (WHERE uhi.health_issue_id IS NOT NULL), '[]'::json) AS health_issue_ids
        FROM users u
        LEFT JOIN user_equipment ue ON u.id = ue.user_id
        LEFT JOIN user_health_issues uhi ON u.id = uhi.user_id
        WHERE u.id = $1
        GROUP BY u.id
    ),
    ActiveRoutineInfo AS (
        -- Find the user's active routine and count the number of days in it.
        SELECT
            ur.routine_id,
            COUNT(rd.id) AS total_routine_days
        FROM user_routines ur
        JOIN routine_days rd ON ur.routine_id = rd.routine_id
        WHERE ur.user_id = $1 AND ur.is_active = TRUE
        GROUP BY ur.routine_id
        -- Ensure we only proceed if there is an active routine with days.
        HAVING COUNT(rd.id) > 0
    ),
    CurrentWorkoutDay AS (
        -- Calculate which day of the routine cycle it is today.
        SELECT
            ari.routine_id,
            -- Formula: (days_since_creation % total_days_in_routine) + 1
            (EXTRACT(DAY FROM (NOW() - ubp.created_at))::int % ari.total_routine_days) + 1 AS today_day_number
        FROM UserBaseProfile ubp, ActiveRoutineInfo ari
    ),
    ActiveDayFocusAreas AS (
        -- Get the focus areas for today's calculated workout day.
        SELECT
            cwd.routine_id,
            json_agg(rdfa.focus_area_id) as focus_area_ids
        FROM CurrentWorkoutDay cwd
        JOIN routine_days rd ON cwd.routine_id = rd.routine_id AND cwd.today_day_number = rd.day_number
        JOIN routine_day_focus_areas rdfa ON rd.id = rdfa.routine_day_id
        GROUP BY cwd.routine_id
    )
    -- Combine the user's profile with the focus areas for today's workout.
    SELECT
        ubp.user_id,
        ubp.fitness_level,
        ubp.equipment_ids,
        ubp.health_issue_ids,
        adfa.focus_area_ids
    FROM UserBaseProfile ubp
    -- LEFT JOIN to handle cases where there might be no active routine or workout today.
    LEFT JOIN ActiveDayFocusAreas adfa ON 1=1 -- Cross join is acceptable as both CTEs are filtered by user_id
    WHERE ubp.user_id = $1;
    """
    return await conn.fetchrow(query, user_id)

async def get_profile_for_workout_generation(conn: asyncpg.Connection, user_id: int) -> Optional[asyncpg.Record]:
    """
    Fetches all data needed for workout generation in a single, efficient query.

    MODIFIED: This now prioritizes the user-selected 'current_day_number'
    if it exists, falling back to the cyclical calculation otherwise.
    """
    query = """
    WITH UserBaseProfile AS (
        -- Step 1: Get the user's static profile information. (No change here)
        SELECT
            u.id AS user_id,
            u.fitness_level,
            u.created_at,
            COALESCE(ARRAY_AGG(DISTINCT ue.equipment_id) FILTER (WHERE ue.equipment_id IS NOT NULL), '{}'::int[]) AS equipment_ids,
            COALESCE(ARRAY_AGG(DISTINCT uhi.health_issue_id) FILTER (WHERE uhi.health_issue_id IS NOT NULL), '{}'::int[]) AS health_issue_ids
        FROM users u
        LEFT JOIN user_equipment ue ON u.id = ue.user_id
        LEFT JOIN user_health_issues uhi ON u.id = uhi.user_id
        WHERE u.id = $1
        GROUP BY u.id
    ),
    ActiveRoutineInfo AS (
        -- Step 2: Find the active routine, its day count, AND the manually set day. (MODIFIED)
        SELECT
            ur.id as user_routine_id,
            ur.current_day_number, -- <<< GET THE MANUALLY SET DAY
            COUNT(urd.id) AS total_routine_days
        FROM user_routines ur
        JOIN user_routine_days urd ON ur.id = urd.user_routine_id
        WHERE ur.user_id = $1 AND ur.is_active = TRUE
        GROUP BY ur.id
        HAVING COUNT(urd.id) > 0
    ),
    CurrentWorkoutDay AS (
        -- Step 3: Calculate which day of the routine cycle it is today. (MODIFIED)
        SELECT
            ari.user_routine_id,
            -- Use the stored day if not NULL, otherwise calculate it.
            COALESCE(
                ari.current_day_number,
                (EXTRACT(DAY FROM (NOW() - ubp.created_at))::int % ari.total_routine_days) + 1
            ) AS today_day_number
        FROM UserBaseProfile ubp, ActiveRoutineInfo ari
    ),
    TodayFocusAreas AS (
        -- Step 4: Get the focus areas for today's workout day. (No change here)
        SELECT
            cwd.user_routine_id,
            ARRAY_AGG(urdfa.focus_area_id) as focus_area_ids
        FROM CurrentWorkoutDay cwd
        JOIN user_routine_days urd ON cwd.user_routine_id = urd.user_routine_id AND cwd.today_day_number = urd.day_number
        JOIN user_routine_day_focus_areas urdfa ON urd.id = urdfa.user_routine_day_id
        GROUP BY cwd.user_routine_id
    )
    -- Final Step: Combine the profile with the focus areas for today. (No change here)
    SELECT
        ubp.fitness_level,
        ubp.equipment_ids,
        ubp.health_issue_ids,
        tfa.focus_area_ids,
        u.randomness,
        u.duration
    FROM UserBaseProfile ubp
    LEFT JOIN TodayFocusAreas tfa ON 1=1
    JOIN users u ON u.id = ubp.user_id
    WHERE ubp.user_id = $1;
    """
    return await conn.fetchrow(query, user_id)


async def get_workout_day_status(conn: asyncpg.Connection, user_id: int) -> Optional[asyncpg.Record]:
    """
    MODIFIED: Fetches status, prioritizing the user-set 'current_day_number'.
    """
    query = """
    WITH ActiveRoutineInfo AS (
        -- Find the active routine and get the manually set day. (MODIFIED)
        SELECT
            ur.id as user_routine_id,
            r.name as routine_name,
            u.created_at,
            ur.current_day_number, -- <<< GET THE MANUALLY SET DAY
            COUNT(urd.id) AS total_routine_days
        FROM users u
        JOIN user_routines ur ON u.id = ur.user_id
        JOIN routines r ON ur.routine_id = r.id
        JOIN user_routine_days urd ON ur.id = urd.user_routine_id
        WHERE u.id = $1 AND ur.is_active = TRUE
        GROUP BY u.id, ur.id, r.name
        HAVING COUNT(urd.id) > 0
    ),
    CurrentWorkoutDay AS (
        -- Calculate the active day. (MODIFIED)
        SELECT
            ari.user_routine_id,
            ari.routine_name,
            ari.total_routine_days,
            -- Use the stored day if not NULL, otherwise calculate it.
            COALESCE(
                ari.current_day_number,
                (EXTRACT(DAY FROM (NOW() - ari.created_at))::int % ari.total_routine_days) + 1
            ) AS today_day_number
        FROM ActiveRoutineInfo ari
    )
    -- Get focus areas for the determined day. (No change here)
    SELECT
        cwd.routine_name,
        cwd.today_day_number,
        cwd.total_routine_days,
        ARRAY_AGG(fa.name) as focus_areas_for_today
    FROM CurrentWorkoutDay cwd
    JOIN user_routine_days urd ON cwd.user_routine_id = urd.user_routine_id AND cwd.today_day_number = urd.day_number
    JOIN user_routine_day_focus_areas urdfa ON urd.id = urdfa.user_routine_day_id
    JOIN focus_areas fa ON urdfa.focus_area_id = fa.id
    GROUP BY cwd.routine_name, cwd.today_day_number, cwd.total_routine_days;
    """
    return await conn.fetchrow(query, user_id)


async def update_active_routine(conn: asyncpg.Connection, user_id: int, routine_id: int) -> bool:
    """
    Safely updates the user's active routine within a transaction.

    This function is designed to be called from an existing database transaction.
    It performs a two-step update to ensure atomicity:
    1. Deactivates all routines for the user.
    2. Activates only the specific routine requested.

    If the requested routine_id does not exist for the user, the second step
    will fail to update any rows, the function will return False, and the calling
    transaction should be rolled back.

    Args:
        conn: The database connection (which must be in a transaction).
        user_id: The ID of the user whose routine is being updated.
        routine_id: The ID of the routine to set as active.

    Returns:
        True if the specific routine was found and activated, False otherwise.
    """
    # Step 1: Deactivate all of the user's routines first.
    # This is safe because it's inside a transaction that will be rolled back on failure.
    await conn.execute(
        "UPDATE user_routines SET is_active = FALSE WHERE user_id = $1",
        user_id
    )

    # Step 2: Attempt to activate ONLY the specified routine.
    # The `RETURNING id` clause is crucial. If no row is updated (because the
    # user_id/routine_id combination doesn't exist), this will return None.
    result = await conn.fetchval(
        """
        UPDATE user_routines
        SET is_active = TRUE
        WHERE user_id = $1 AND routine_id = $2
        RETURNING id;
        """,
        user_id,
        routine_id
    )

    # Return True if a row was successfully updated, False otherwise.
    return result is not None
    

async def get_user_routines_list(conn: asyncpg.Connection, user_id: int) -> List[asyncpg.Record]:
    """
    Fetches a list of all routines assigned to a user, including their names
    and active status.
    """
    query = """
    SELECT 
        r.id AS routine_id,
        r.name,
        ur.is_active
    FROM user_routines ur
    JOIN routines r ON ur.routine_id = r.id
    WHERE ur.user_id = $1
    ORDER BY r.id; -- Order for a consistent response
    """
    return await conn.fetch(query, user_id)


async def set_active_day_for_user(conn: asyncpg.Connection, user_id: int, day_number: int) -> bool:
    """
    Sets a user-selected 'current_day_number' for their active routine.

    This overrides the automatic cyclical day calculation. It checks to ensure
    the day number is valid for the user's currently active routine before updating.

    Args:
        conn: The database connection.
        user_id: The ID of the user.
        day_number: The day number to set as active.

    Returns:
        True if the update was successful, False otherwise (e.g., if the day
        number was invalid for the routine).
    """
    query = """
    UPDATE user_routines ur
    SET current_day_number = $2
    WHERE ur.user_id = $1 AND ur.is_active = TRUE
    -- This EXISTS clause is a safeguard to prevent setting an invalid day number.
    AND EXISTS (
        SELECT 1
        FROM user_routine_days urd
        WHERE urd.user_routine_id = ur.id AND urd.day_number = $2
    )
    RETURNING ur.id;
    """
    # fetchval will return the ID on success, or None on failure.
    result = await conn.fetchval(query, user_id, day_number)
    return result is not None
async def get_active_routine_days(conn: asyncpg.Connection, user_id: int) -> Optional[asyncpg.Record]:
    """
    Fetches the details for the user's currently active routine, including a list
    of ALL its days, their focus areas, and which day is currently active.

    CORRECTED: This version uses LEFT JOINs to ensure that all days of a routine
    are returned, even if they have not yet been assigned any focus areas.
    """
    query = """
    WITH ActiveRoutine AS (
        -- Step 1: Find the active routine and calculate the current day. This part is correct.
        SELECT
            ur.id as user_routine_id,
            ur.routine_id,
            r.name as routine_name,
            COALESCE(
                ur.current_day_number,
                (EXTRACT(DAY FROM (NOW() - u.created_at))::int % COUNT(urd.id)) + 1
            ) AS current_day_number
        FROM user_routines ur
        JOIN users u ON ur.user_id = u.id
        JOIN routines r ON ur.routine_id = r.id
        JOIN user_routine_days urd ON ur.id = urd.user_routine_id
        WHERE ur.user_id = $1 AND ur.is_active = TRUE
        GROUP BY ur.id, r.id, u.id
        HAVING COUNT(urd.id) > 0
    ),
    DayFocusAreas AS (
        -- Step 2: Pre-aggregate focus areas for each day. THIS PART IS CORRECTED.
        SELECT
            urd.user_routine_id,
            urd.day_number,
            -- Use COALESCE to turn a NULL result (no focus areas) into an empty JSON array '[]'.
            COALESCE(
                json_agg(
                    json_build_object('id', fa.id, 'name', fa.name) ORDER BY fa.name
                -- The FILTER clause is essential to prevent aggregating a [null] object.
                ) FILTER (WHERE fa.id IS NOT NULL),
                '[]'::json
            ) AS focus_areas
        FROM user_routine_days urd
        -- Use LEFT JOIN to include days even if they have no matching focus areas.
        LEFT JOIN user_routine_day_focus_areas urdfa ON urd.id = urdfa.user_routine_day_id
        LEFT JOIN focus_areas fa ON urdfa.focus_area_id = fa.id
        WHERE urd.user_routine_id IN (SELECT user_routine_id FROM ActiveRoutine)
        GROUP BY urd.user_routine_id, urd.day_number
    )
    -- Final Step: Combine the data. This part is also made more robust.
    SELECT
        ar.routine_id,
        ar.routine_name,
        -- Final COALESCE to handle the edge case where a routine might have no days.
        COALESCE(json_agg(
            json_build_object(
                'day_number', dfa.day_number,
                'is_current_day', (dfa.day_number = ar.current_day_number),
                'focus_areas', dfa.focus_areas
            ) ORDER BY dfa.day_number
        ), '[]'::json) AS days
    FROM ActiveRoutine ar
    -- Use LEFT JOIN here as a final safeguard.
    LEFT JOIN DayFocusAreas dfa ON ar.user_routine_id = dfa.user_routine_id
    GROUP BY ar.routine_id, ar.routine_name;
    """
    return await conn.fetchrow(query, user_id)
    

async def get_all_focus_areas(conn: asyncpg.Connection) -> List[asyncpg.Record]:
    """
    Fetches a list of all available focus areas from the lookup table,
    ordered alphabetically.
    """
    query = "SELECT id, name FROM focus_areas ORDER BY id;"
    return await conn.fetch(query)


async def add_day_to_user_routine(conn: asyncpg.Connection, user_id: int, user_routine_id: int) -> Optional[asyncpg.Record]:
    """
    Adds a new, empty day to a specific user routine.

    It calculates the next available day number and creates the day. It also
    verifies that the routine belongs to the user.

    Returns:
        The newly created user_routine_days record, or None if the routine
        does not belong to the user.
    """
    query = """
    WITH routine_info AS (
        -- Step 1: Verify ownership and find the next day number
        SELECT
            ur.id as target_user_routine_id,
            COALESCE(MAX(urd.day_number), 0) + 1 as next_day_number
        FROM user_routines ur
        LEFT JOIN user_routine_days urd ON ur.id = urd.user_routine_id
        WHERE ur.user_id = $1 AND ur.id = $2
        GROUP BY ur.id
    ),
    new_day AS (
        -- Step 2: Insert the new day if the routine was found
        INSERT INTO user_routine_days (user_routine_id, day_number)
        SELECT target_user_routine_id, next_day_number FROM routine_info
        RETURNING id, user_routine_id, day_number
    )
    -- Step 3: Return the result
    SELECT * FROM new_day;
    """
    new_day_record = await conn.fetchrow(query, user_id, user_routine_id)
    return new_day_record


async def delete_day_from_user_routine(conn: asyncpg.Connection, user_id: int, user_routine_id: int, day_number: int) -> bool:
    """
    Deletes a day (and its associated focus areas via CASCADE) from a user routine.

    Verifies ownership before deleting. The CASCADE constraint on the
    user_routine_day_focus_areas table handles cleanup.

    Returns:
        True if a day was deleted, False otherwise.
    """
    query = """
    DELETE FROM user_routine_days urd
    WHERE urd.day_number = $3
    AND urd.user_routine_id = $2
    -- This EXISTS clause is a robust way to verify ownership
    AND EXISTS (
        SELECT 1 FROM user_routines ur
        WHERE ur.id = urd.user_routine_id
        AND ur.user_id = $1
    )
    RETURNING urd.id;
    """
    deleted_id = await conn.fetchval(query, user_id, user_routine_id, day_number)
    return deleted_id is not None


# --- Day Focus Area Management ---

async def add_focus_area_to_day(conn: asyncpg.Connection, user_id: int, user_routine_id: int, day_number: int, focus_area_id: int) -> bool:
    """
    Adds a focus area to a specific day within a user routine.

    Verifies ownership of the routine and that the day exists before adding.
    Handles potential unique constraint violations gracefully.

    Returns:
        True on successful insertion, False otherwise.
    """
    query = """
    WITH target_day AS (
        -- Step 1: Find the specific user_routine_day_id while verifying ownership
        SELECT urd.id
        FROM user_routine_days urd
        JOIN user_routines ur ON urd.user_routine_id = ur.id
        WHERE ur.user_id = $1
          AND ur.id = $2
          AND urd.day_number = $3
    )
    -- Step 2: Insert the link if the day was found
    INSERT INTO user_routine_day_focus_areas (user_routine_day_id, focus_area_id)
    SELECT id, $4 FROM target_day
    -- If the focus area is already linked, do nothing and don't raise an error
    ON CONFLICT (user_routine_day_id, focus_area_id) DO NOTHING
    RETURNING user_routine_day_id;
    """
    result = await conn.fetchval(query, user_id, user_routine_id, day_number, focus_area_id)
    return result is not None


async def delete_focus_area_from_day(conn: asyncpg.Connection, user_id: int, user_routine_id: int, day_number: int, focus_area_id: int) -> bool:
    """
    Deletes a focus area from a specific day within a user routine.
    Verifies ownership before deleting.

    Returns:
        True if a link was deleted, False otherwise.
    """
    query = """
    DELETE FROM user_routine_day_focus_areas urdfa
    WHERE urdfa.user_routine_day_id = (
        -- Subquery to find the target day ID while verifying ownership
        SELECT urd.id
        FROM user_routine_days urd
        JOIN user_routines ur ON urd.user_routine_id = ur.id
        WHERE ur.user_id = $1
          AND ur.id = $2
          AND urd.day_number = $3
    )
    AND urdfa.focus_area_id = $4
    RETURNING urdfa.user_routine_day_id;
    """
    deleted_id = await conn.fetchval(query, user_id, user_routine_id, day_number, focus_area_id)
    return deleted_id is not None
