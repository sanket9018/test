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
            activity_level, workouts_per_week, motivation_id
        ) VALUES (
            $1, $2, $3, $4::gender_enum, $5, $6, $7, $8, 
            $9::fitness_level_enum, $10::activity_level_enum, $11, $12
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
        user_data['workouts_per_week'], 
        user_data['motivation_id']
    )

# This function is correct and essential for the new flow.
async def set_initial_active_routine(conn: asyncpg.Connection, user_id: int, routine_id: int):
    """
    Sets a specific routine for a user as active. This is called after the user
    and their default routines have been created by the trigger.
    """
    if not routine_id:
        return
        
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
    query = f"INSERT INTO {table_name} (user_id, {column_name}) SELECT $1, unnest($2::integer[]);"
    await conn.execute(query, user_id, item_ids)

async def link_user_to_days(conn: asyncpg.Connection, user_id: int, days: List[str]):
    """Performs a bulk insert for user workout days."""
    if not days:
        return
    query = "INSERT INTO user_workout_days (user_id, day) SELECT $1, unnest($2::text[])::day_of_week_enum;"
    await conn.execute(query, user_id, days)

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
            ARRAY_AGG(g.name ORDER BY g.name) AS goals
        FROM user_goals ug
        JOIN goals g ON g.id = ug.goal_id
        WHERE ug.user_id = $1
        GROUP BY ug.user_id
    ),
    -- NEW: Pre-aggregate user equipment into an array
    user_equipment_agg AS (
        SELECT
            ue.user_id,
            ARRAY_AGG(e.name ORDER BY e.name) AS equipment
        FROM user_equipment ue
        JOIN equipment e ON e.id = ue.equipment_id
        WHERE ue.user_id = $1
        GROUP BY ue.user_id
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
        u.current_weight_kg,
        u.target_weight_kg,
        u.fitness_level,
        u.activity_level,
        u.workouts_per_week,
        m.name AS motivation,
        -- Use COALESCE to gracefully handle users with no goals/equipment etc.
        COALESCE(uga.goals, '{}') AS goals,
        COALESCE(uea.equipment, '{}') AS equipment,
        COALESCE(hi.name, 'None') AS health_issues,
        COALESCE(urd.routines, '[]'::json) AS routines,
        u.created_at,
        u.updated_at
    FROM users u
    LEFT JOIN motivations m ON m.id = u.motivation_id
    LEFT JOIN user_health_issues uhi ON uhi.user_id = u.id
    LEFT JOIN health_issues hi ON hi.id = uhi.health_issue_id
    -- Join our pre-aggregated CTEs
    LEFT JOIN user_routine_details urd ON u.id = urd.user_id
    LEFT JOIN user_goals_agg uga ON u.id = uga.user_id
    LEFT JOIN user_equipment_agg uea ON u.id = uea.user_id
    WHERE u.id = $1; -- Filter for the specific user
    """
    return await conn.fetchrow(query, user_id)


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
    query = """
    WITH RankedExercises AS (
        -- This CTE finds all possible exercise matches for each focus area.
        SELECT
            e.id,
            e.name,
            e.description,
            e.video_url,
            fa.name as primary_focus_area,
            ROW_NUMBER() OVER(PARTITION BY edfa.focus_area_id ORDER BY RANDOM()) as rn
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
    ORDER BY RANDOM() -- Shuffle the final, unique list
    LIMIT $6;
    """
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

    This determines the user's current workout day based on a cyclical calculation
    since their registration date, making it independent of the day of the week.
    """
    query = """
    WITH UserBaseProfile AS (
        -- Step 1: Get the user's static profile information.
        SELECT
            u.id AS user_id,
            u.fitness_level,
            u.created_at, -- Needed to calculate the workout day cycle
            COALESCE(ARRAY_AGG(DISTINCT ue.equipment_id) FILTER (WHERE ue.equipment_id IS NOT NULL), '{}'::int[]) AS equipment_ids,
            COALESCE(ARRAY_AGG(DISTINCT uhi.health_issue_id) FILTER (WHERE uhi.health_issue_id IS NOT NULL), '{}'::int[]) AS health_issue_ids
        FROM users u
        LEFT JOIN user_equipment ue ON u.id = ue.user_id
        LEFT JOIN user_health_issues uhi ON u.id = uhi.user_id
        WHERE u.id = $1
        GROUP BY u.id
    ),
    ActiveRoutineInfo AS (
        -- Step 2: Find the user's active routine and count the number of days in it.
        SELECT
            ur.id as user_routine_id,
            COUNT(urd.id) AS total_routine_days
        FROM user_routines ur
        JOIN user_routine_days urd ON ur.id = urd.user_routine_id
        WHERE ur.user_id = $1 AND ur.is_active = TRUE
        GROUP BY ur.id
        HAVING COUNT(urd.id) > 0 -- Ensure routine has at least one day.
    ),
    CurrentWorkoutDay AS (
        -- Step 3: Calculate which day of the routine cycle it is today.
        SELECT
            ari.user_routine_id,
            -- Formula: (days_since_creation % total_days_in_routine) + 1
            -- This creates a cycle like Day 1, Day 2, Day 3, Day 1, Day 2, ...
            (EXTRACT(DAY FROM (NOW() - ubp.created_at))::int % ari.total_routine_days) + 1 AS today_day_number
        FROM UserBaseProfile ubp, ActiveRoutineInfo ari
    ),
    TodayFocusAreas AS (
        -- Step 4: Get the focus areas for today's calculated workout day.
        SELECT
            cwd.user_routine_id,
            ARRAY_AGG(urdfa.focus_area_id) as focus_area_ids
        FROM CurrentWorkoutDay cwd
        JOIN user_routine_days urd ON cwd.user_routine_id = urd.user_routine_id AND cwd.today_day_number = urd.day_number
        JOIN user_routine_day_focus_areas urdfa ON urd.id = urdfa.user_routine_day_id
        GROUP BY cwd.user_routine_id
    )
    -- Final Step: Combine the base profile with the focus areas for today.
    SELECT
        ubp.fitness_level,
        ubp.equipment_ids,
        ubp.health_issue_ids,
        tfa.focus_area_ids
    FROM UserBaseProfile ubp
    -- LEFT JOIN to gracefully handle cases where no active routine is found.
    LEFT JOIN TodayFocusAreas tfa ON 1=1
    WHERE ubp.user_id = $1;
    """
    return await conn.fetchrow(query, user_id)


async def get_workout_day_status(conn: asyncpg.Connection, user_id: int) -> Optional[asyncpg.Record]:
    """
    Fetches a human-readable status of the user's workout plan for today,
    including active routine, today's calculated day number, and its focus areas.
    """
    query = """
    WITH ActiveRoutineInfo AS (
        -- Find the user's active routine and count its days.
        SELECT
            ur.id as user_routine_id,
            r.name as routine_name,
            u.created_at,
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
        -- Calculate which day of the routine cycle it is today.
        SELECT
            ari.user_routine_id,
            ari.routine_name,
            ari.total_routine_days,
            -- Formula: (days_since_creation % total_days_in_routine) + 1
            (EXTRACT(DAY FROM (NOW() - ari.created_at))::int % ari.total_routine_days) + 1 AS today_day_number
        FROM ActiveRoutineInfo ari
    )
    -- Get the focus areas for today's calculated workout day and return all info.
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


