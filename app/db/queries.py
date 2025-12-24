import asyncpg
from typing import Optional, Dict, List, Any
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
            activity_level, workouts_per_week, motivation, goal, days
        ) VALUES (
            $1, $2, $3, $4::gender_enum, $5, $6, $7, $8, 
            $9::fitness_level_enum, $10::activity_level_enum, $11, $12, $13, $14
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
        user_data.get('motivation'),
        user_data.get('goal'),
        user_data.get('days')
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
        -- Updated CTE to include both focus areas and direct exercises
        WITH day_focus_areas AS (
            SELECT
                urda.user_routine_day_id,
                json_agg(json_build_object('id', fa.id, 'name', fa.name)) AS focus_areas
            FROM user_routine_day_focus_areas urda
            JOIN focus_areas fa ON urda.focus_area_id = fa.id
            GROUP BY urda.user_routine_day_id
        ),
        day_direct_exercises AS (
            SELECT
                urde.user_routine_day_id,
                json_agg(
                    json_build_object(
                        'id', e.id,
                        'name', e.name,
                        'description', e.description,
                        'video_url', e.video_url,
                        'order_in_day', urde.order_in_day
                    ) ORDER BY urde.order_in_day
                ) AS direct_exercises
            FROM user_routine_day_exercises urde
            JOIN exercises e ON urde.exercise_id = e.id
            GROUP BY urde.user_routine_day_id
        ),
        routine_days_agg AS (
            SELECT
                urd.user_routine_id,
                json_agg(
                    json_build_object(
                        'id', urd.id,
                        'day_number', urd.day_number,
                        'exercise_mode', urd.exercise_mode,
                        'focus_areas', COALESCE(dfa.focus_areas, '[]'::json),
                        'direct_exercises', COALESCE(dde.direct_exercises, '[]'::json)
                    ) ORDER BY urd.day_number
                ) AS days
            FROM user_routine_days urd
            LEFT JOIN day_focus_areas dfa ON urd.id = dfa.user_routine_day_id
            LEFT JOIN day_direct_exercises dde ON urd.id = dde.user_routine_day_id
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
    user_workout_days_agg AS (
        SELECT
            uwd.user_id,
            json_agg(uwd.day ORDER BY 
                CASE uwd.day
                    WHEN 'monday' THEN 1
                    WHEN 'tuesday' THEN 2
                    WHEN 'wednesday' THEN 3
                    WHEN 'thursday' THEN 4
                    WHEN 'friday' THEN 5
                    WHEN 'saturday' THEN 6
                    WHEN 'sunday' THEN 7
                END
            ) AS workout_days
        FROM user_workout_days uwd
        WHERE uwd.user_id = $1
        GROUP BY uwd.user_id
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
        u.profile_image_key,
        u.motivation,
        u.goal,
        u.days,
        u.reminder,
        u.vibration_alert,
        COALESCE(uwda.workout_days, '[]'::json) AS workout_days,
        -- Use COALESCE to gracefully handle users with no equipment etc.
        COALESCE(uea.equipment, '[]'::json) AS equipment,
        COALESCE(uh.health_issues, '[]'::json) AS health_issues,
        COALESCE(urd.routines, '[]'::json) AS routines,
        u.created_at,
        u.updated_at
    FROM users u
    -- Join our pre-aggregated CTEs
    LEFT JOIN user_routine_details urd ON u.id = urd.user_id
    LEFT JOIN user_equipment_agg uea ON u.id = uea.user_id
    LEFT JOIN user_health_issues_agg uh ON u.id = uh.user_id
    LEFT JOIN user_workout_days_agg uwda ON u.id = uwda.user_id
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
        e.pro_tip,
        e.muscle_groups,
        e.video_url,
        e.image_url,
        json_agg(
            json_build_object(
                'id', fa.id,
                'name', fa.name
            ) ORDER BY fa.id
        ) FILTER (WHERE fa.id IS NOT NULL) as focus_areas
    FROM exercises e
    LEFT JOIN exercise_focus_areas efa ON e.id = efa.exercise_id
    LEFT JOIN focus_areas fa ON efa.focus_area_id = fa.id
    GROUP BY e.id, e.name, e.description, e.pro_tip, e.muscle_groups, e.video_url, e.image_url
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
    total_limit: int,
    objective: Optional[str] = None
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
            e.image_url,
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
            AND ($7::text IS NULL OR e.exercise_type::text = $7::text)
    )
    -- FINAL SELECTION: This part is rewritten to handle duplicates.
    SELECT id, name, description, video_url, image_url, primary_focus_area
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
        total_limit,            # $6
        objective               # $7
    )
    

# NO CHANGES NEEDED to this helper function.
async def get_equipment_id_by_name(conn: asyncpg.Connection, name: str) -> Optional[int]:
    """Fetches the ID of an equipment item by its name."""
    return await conn.fetchval("SELECT id FROM equipment WHERE name = $1", name)


async def get_alternative_exercises(
    conn: asyncpg.Connection,
    original_exercise_id: int,
    user_id: int,
    limit: int = 10
) -> List[asyncpg.Record]:
    """
    Finds alternative exercises based on an original exercise ID and user's profile.
    
    The algorithm considers:
    1. Shared focus areas with the original exercise
    2. User's fitness level compatibility
    3. User's available equipment
    4. User's health issues (contraindications)
    5. Excludes the original exercise from results
    
    Returns exercises sorted by similarity score (number of shared focus areas).
    """
    query = """
    WITH OriginalExerciseFocusAreas AS (
        -- Get all focus areas for the original exercise
        SELECT efa.focus_area_id, fa.name as focus_area_name
        FROM exercise_focus_areas efa
        JOIN focus_areas fa ON efa.focus_area_id = fa.id
        WHERE efa.exercise_id = $1
    ),
    UserProfile AS (
        -- Get user's profile data
        SELECT 
            u.fitness_level,
            COALESCE(array_agg(DISTINCT ue.equipment_id) FILTER (WHERE ue.equipment_id IS NOT NULL), ARRAY[]::int[]) as equipment_ids,
            COALESCE(array_agg(DISTINCT uhi.health_issue_id) FILTER (WHERE uhi.health_issue_id IS NOT NULL), ARRAY[]::int[]) as health_issue_ids
        FROM users u
        LEFT JOIN user_equipment ue ON u.id = ue.user_id
        LEFT JOIN user_health_issues uhi ON u.id = uhi.user_id
        WHERE u.id = $2
        GROUP BY u.id, u.fitness_level
    ),
    AlternativeExercises AS (
        -- Find exercises that share focus areas with the original
        SELECT DISTINCT
            e.id,
            e.name,
            e.description,
            e.video_url,
            pfa.name as primary_focus_area,
            -- Calculate similarity score based on shared focus areas
            (
                SELECT COUNT(*)
                FROM exercise_focus_areas efa2
                WHERE efa2.exercise_id = e.id
                AND efa2.focus_area_id IN (
                    SELECT focus_area_id FROM OriginalExerciseFocusAreas
                )
            )::float / GREATEST(
                (
                    SELECT COUNT(*) FROM OriginalExerciseFocusAreas
                ),
                (
                    SELECT COUNT(*) FROM exercise_focus_areas efa3 WHERE efa3.exercise_id = e.id
                )
            ) as similarity_score,
            -- Get shared focus area names
            (
                SELECT array_agg(fa.name)
                FROM exercise_focus_areas efa4
                JOIN focus_areas fa ON efa4.focus_area_id = fa.id
                WHERE efa4.exercise_id = e.id
                AND efa4.focus_area_id IN (
                    SELECT focus_area_id FROM OriginalExerciseFocusAreas
                )
            ) as shared_focus_areas
        FROM exercises e
        JOIN exercise_focus_areas efa ON e.id = efa.exercise_id
        LEFT JOIN focus_areas pfa ON e.primary_focus_area_id = pfa.id
        CROSS JOIN UserProfile up
        WHERE
            -- Exclude the original exercise
            e.id != $1
            -- Must share at least one focus area
            AND EXISTS (
                SELECT 1 FROM exercise_focus_areas efa_shared
                WHERE efa_shared.exercise_id = e.id
                AND efa_shared.focus_area_id IN (
                    SELECT focus_area_id FROM OriginalExerciseFocusAreas
                )
            )
            -- Must be compatible with user's fitness level
            AND EXISTS (
                SELECT 1 FROM exercise_fitness_levels efl
                WHERE efl.exercise_id = e.id 
                AND efl.fitness_level = up.fitness_level::fitness_level_enum
            )
            -- Must be compatible with user's equipment (if user has equipment preferences)
            AND (
                array_length(up.equipment_ids, 1) IS NULL
                OR EXISTS (
                    SELECT 1 FROM exercise_equipment ee
                    WHERE ee.exercise_id = e.id 
                    AND ee.equipment_id = ANY(up.equipment_ids)
                )
            )
            -- Must not be contraindicated by user's health issues
            AND NOT EXISTS (
                SELECT 1 FROM exercise_contraindications ec
                WHERE ec.exercise_id = e.id 
                AND ec.health_issue_id = ANY(up.health_issue_ids)
            )
    )
    SELECT 
        id,
        name,
        description,
        video_url,
        primary_focus_area,
        similarity_score,
        COALESCE(shared_focus_areas, ARRAY[]::text[]) as shared_focus_areas
    FROM AlternativeExercises
    ORDER BY similarity_score DESC, name ASC
    LIMIT $3;
    """
    
    return await conn.fetch(query, original_exercise_id, user_id, limit)


async def get_exercise_details(conn: asyncpg.Connection, exercise_id: int) -> Optional[asyncpg.Record]:
    """
    Gets detailed information about a specific exercise including its focus areas.
    """
    query = """
    SELECT 
        e.id,
        e.name,
        e.description,
        e.video_url,
        pfa.name as primary_focus_area,
        (
            SELECT array_agg(fa.name)
            FROM exercise_focus_areas efa
            JOIN focus_areas fa ON efa.focus_area_id = fa.id
            WHERE efa.exercise_id = e.id
        ) as focus_areas
    FROM exercises e
    LEFT JOIN focus_areas pfa ON e.primary_focus_area_id = pfa.id
    WHERE e.id = $1;
    """
    
    return await conn.fetchrow(query, exercise_id)


async def get_active_routine_day_info(
    conn: asyncpg.Connection,
    user_id: int,
    override_day_number: Optional[int] = None,
) -> Optional[asyncpg.Record]:
    """
    Returns the active user_routine_id, computed (or overridden) day_number, and the
    corresponding user_routine_day_id for the given user.
    """
    query = """
    WITH ActiveRoutineInfo AS (
        SELECT
            ur.id AS user_routine_id,
            ur.current_day_number,
            u.created_at,
            COUNT(urd.id) AS total_routine_days,
            r.name AS routine_name
        FROM users u
        JOIN user_routines ur ON u.id = ur.user_id
        JOIN routines r ON ur.routine_id = r.id
        JOIN user_routine_days urd ON ur.id = urd.user_routine_id
        WHERE u.id = $1 AND ur.is_active = TRUE
        GROUP BY ur.id, u.created_at, r.name
        HAVING COUNT(urd.id) > 0
    ),
    CurrentDay AS (
        SELECT
            ari.user_routine_id,
            ari.routine_name,
            COALESCE(
                $2, -- override_day_number if provided
                COALESCE(
                    ari.current_day_number,
                    (EXTRACT(DAY FROM (NOW() - ari.created_at))::int % ari.total_routine_days) + 1
                )
            ) AS day_number
        FROM ActiveRoutineInfo ari
    )
    SELECT
        cd.user_routine_id,
        cd.routine_name,
        cd.day_number,
        urd.id AS user_routine_day_id
    FROM CurrentDay cd
    JOIN user_routine_days urd
      ON urd.user_routine_id = cd.user_routine_id
     AND urd.day_number = cd.day_number;
    """
    return await conn.fetchrow(query, user_id, override_day_number)


async def replace_routine_day_exercises(
    conn: asyncpg.Connection,
    user_id: int,
    day_number: Optional[int],
    exercise_ids: List[int],
) -> Optional[Dict[str, Any]]:
    """
    Replaces the specified active routine day's content with the provided exercises.

    - Switches the day to 'direct_exercises' mode
    - Clears focus areas and previous direct exercises
    - Inserts the given exercises with increasing order_in_day
    Returns dict with user_routine_id, day_number and list of saved exercises including names and order.
    """
    day_info = await get_active_routine_day_info(conn, user_id, day_number)
    if not day_info:
        return None

    user_routine_day_id = day_info['user_routine_day_id']

    # De-duplicate while preserving order
    seen = set()
    ordered_unique_ids: List[int] = []
    for eid in exercise_ids:
        if eid and eid not in seen:
            seen.add(eid)
            ordered_unique_ids.append(int(eid))

    async with conn.transaction():
        # Ensure ownership: user_routine_day_id belongs to this user
        owner_check = await conn.fetchval(
            """
            SELECT 1
            FROM user_routine_days urd
            JOIN user_routines ur ON urd.user_routine_id = ur.id
            WHERE urd.id = $1 AND ur.user_id = $2
            """,
            user_routine_day_id,
            user_id,
        )
        if not owner_check:
            return None

        # Persist the resolved day_number as the active day to keep /generate consistent
        await conn.execute(
            "UPDATE user_routines SET current_day_number = $1 WHERE id = $2",
            day_info["day_number"],
            day_info["user_routine_id"],
        )

        # Switch mode to direct_exercises and clear previous content
        await conn.execute(
            "UPDATE user_routine_days SET exercise_mode = 'direct_exercises' WHERE id = $1",
            user_routine_day_id,
        )
        await conn.execute(
            "DELETE FROM user_routine_day_focus_areas WHERE user_routine_day_id = $1",
            user_routine_day_id,
        )
        await conn.execute(
            "DELETE FROM user_routine_day_exercises WHERE user_routine_day_id = $1",
            user_routine_day_id,
        )

        # Insert new exercises with order
        for idx, eid in enumerate(ordered_unique_ids, start=1):
            await conn.execute(
                """
                INSERT INTO user_routine_day_exercises (user_routine_day_id, exercise_id, order_in_day)
                VALUES ($1, $2, $3)
                ON CONFLICT (user_routine_day_id, exercise_id) DO UPDATE SET order_in_day = EXCLUDED.order_in_day
                """,
                user_routine_day_id,
                eid,
                idx,
            )

    rows = await conn.fetch(
        """
        SELECT urde.exercise_id, e.name, urde.order_in_day
        FROM user_routine_day_exercises urde
        JOIN exercises e ON e.id = urde.exercise_id
        WHERE urde.user_routine_day_id = $1
        ORDER BY urde.order_in_day
        """,
        user_routine_day_id,
    )

    return {
        "user_routine_id": day_info["user_routine_id"],
        "day_number": day_info["day_number"],
        "exercises": [
            {
                "exercise_id": r["exercise_id"],
                "name": r["name"],
                "order_in_day": r["order_in_day"],
            }
            for r in rows
        ],
    }


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

    UPDATED: Now supports both focus_areas and direct_exercises modes.
    Returns focus_area_ids for focus_areas mode or direct_exercises for direct_exercises mode.
    """
    query = """
    WITH UserBaseProfile AS (
        -- Step 1: Get the user's static profile information.
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
        -- Step 2: Find the active routine, its day count, AND the manually set day.
        SELECT
            ur.id as user_routine_id,
            ur.current_day_number,
            COUNT(urd.id) AS total_routine_days
        FROM user_routines ur
        JOIN user_routine_days urd ON ur.id = urd.user_routine_id
        WHERE ur.user_id = $1 AND ur.is_active = TRUE
        GROUP BY ur.id
        HAVING COUNT(urd.id) > 0
    ),
    CurrentWorkoutDay AS (
        -- Step 3: Calculate which day of the routine cycle it is today.
        SELECT
            ari.user_routine_id,
            COALESCE(
                ari.current_day_number,
                (EXTRACT(DAY FROM (NOW() - ubp.created_at))::int % ari.total_routine_days) + 1
            ) AS today_day_number
        FROM UserBaseProfile ubp, ActiveRoutineInfo ari
    ),
    TodayWorkoutData AS (
        -- Step 4: Get the workout data for today (focus areas OR direct exercises).
        SELECT
            cwd.user_routine_id,
            urd.exercise_mode,
            -- Focus areas (only if in focus_areas mode)
            CASE 
                WHEN urd.exercise_mode = 'focus_areas' THEN
                    COALESCE(ARRAY_AGG(DISTINCT urdfa.focus_area_id) FILTER (WHERE urdfa.focus_area_id IS NOT NULL), '{}'::int[])
                ELSE '{}'::int[]
            END as focus_area_ids,
            -- Direct exercises (only if in direct_exercises mode)
            CASE 
                WHEN urd.exercise_mode = 'direct_exercises' THEN
                    COALESCE(
                        json_agg(
                            json_build_object(
                                'id', e.id,
                                'name', e.name,
                                'description', e.description,
                                'video_url', e.video_url,
                                'primary_focus_area', COALESCE(fa.name, 'General')
                            ) ORDER BY urde.order_in_day
                        ) FILTER (WHERE e.id IS NOT NULL),
                        '[]'::json
                    )
                ELSE '[]'::json
            END as direct_exercises
        FROM CurrentWorkoutDay cwd
        JOIN user_routine_days urd ON cwd.user_routine_id = urd.user_routine_id AND cwd.today_day_number = urd.day_number
        LEFT JOIN user_routine_day_focus_areas urdfa ON urd.id = urdfa.user_routine_day_id
        LEFT JOIN user_routine_day_exercises urde ON urd.id = urde.user_routine_day_id
        LEFT JOIN exercises e ON urde.exercise_id = e.id
        LEFT JOIN focus_areas fa ON e.primary_focus_area_id = fa.id
        GROUP BY cwd.user_routine_id, urd.exercise_mode
    )
    -- Final Step: Combine the profile with the workout data for today.
    SELECT
        ubp.fitness_level,
        ubp.equipment_ids,
        ubp.health_issue_ids,
        twd.exercise_mode,
        twd.focus_area_ids,
        twd.direct_exercises,
        u.randomness,
        u.duration,
        u.current_weight_kg,
        u.height_cm,
        u.age,
        u.objective
    FROM UserBaseProfile ubp
    LEFT JOIN TodayWorkoutData twd ON 1=1
    JOIN users u ON u.id = ubp.user_id
    WHERE ubp.user_id = $1;
    """
    return await conn.fetchrow(query, user_id)


async def get_workout_day_status(conn: asyncpg.Connection, user_id: int) -> Optional[asyncpg.Record]:
    """
    UPDATED: Fetches status with both focus areas and direct exercises support.
    """
    query = """
    WITH ActiveRoutineInfo AS (
        -- Find the active routine and get the manually set day.
        SELECT
            ur.id as user_routine_id,
            r.name as routine_name,
            u.created_at,
            ur.current_day_number,
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
        -- Calculate the active day.
        SELECT
            ari.user_routine_id,
            ari.routine_name,
            ari.total_routine_days,
            COALESCE(
                ari.current_day_number,
                (EXTRACT(DAY FROM (NOW() - ari.created_at))::int % ari.total_routine_days) + 1
            ) AS today_day_number
        FROM ActiveRoutineInfo ari
    )
    -- Get both focus areas and direct exercises for today's workout day.
    SELECT
        cwd.routine_name,
        cwd.today_day_number,
        cwd.total_routine_days,
        urd.exercise_mode,
        -- Focus areas (only if in focus_areas mode)
        CASE 
            WHEN urd.exercise_mode = 'focus_areas' THEN
                COALESCE(ARRAY_AGG(DISTINCT fa.name) FILTER (WHERE fa.name IS NOT NULL), '{}')
            ELSE '{}'::text[]
        END as focus_areas_for_today,
        -- Direct exercises (only if in direct_exercises mode)
        CASE 
            WHEN urd.exercise_mode = 'direct_exercises' THEN
                COALESCE(
                    json_agg(
                        json_build_object(
                            'id', e.id,
                            'name', e.name,
                            'description', e.description,
                            'video_url', e.video_url,
                            'order_in_day', urde.order_in_day
                        ) ORDER BY urde.order_in_day
                    ) FILTER (WHERE e.id IS NOT NULL),
                    '[]'::json
                )
            ELSE '[]'::json
        END as direct_exercises_for_today
    FROM CurrentWorkoutDay cwd
    JOIN user_routine_days urd ON cwd.user_routine_id = urd.user_routine_id AND cwd.today_day_number = urd.day_number
    LEFT JOIN user_routine_day_focus_areas urdfa ON urd.id = urdfa.user_routine_day_id
    LEFT JOIN focus_areas fa ON urdfa.focus_area_id = fa.id
    LEFT JOIN user_routine_day_exercises urde ON urd.id = urde.user_routine_day_id
    LEFT JOIN exercises e ON urde.exercise_id = e.id
    GROUP BY cwd.routine_name, cwd.today_day_number, cwd.total_routine_days, urd.exercise_mode;
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


def calculate_one_rm(body_weight_kg: float, height_cm: float, age: int) -> float:
    """
    Calculate 1RM using the formula: 0.6*BodyWeight + 0.08*Height - 0.3*Age + 10
    Then divide by 2 to get the working weight as specified by user requirements.
    """
    one_rm = (0.6 * body_weight_kg) + (0.08 * height_cm) - (0.3 * age) + 10
    working_weight = one_rm / 2
    return round(working_weight, 2)

def get_default_reps_sets_by_fitness_level(fitness_level: str) -> tuple:
    """
    Returns default reps and sets based on fitness level.
    Returns (reps, sets)
    """
    fitness_defaults = {
        'beginner': (15, 3),
        'intermediate': (12, 3), 
        'advanced': (10, 3)
    }
    return fitness_defaults.get(fitness_level.lower(), (12, 3))

async def store_user_generated_exercises(
    conn: asyncpg.Connection, 
    user_id: int, 
    exercise_ids: List[int],
    user_data: dict
) -> bool:
    """
    Stores generated exercises for a user with calculated KG, REPS, and SETS.
    First deletes existing generated exercises, then inserts new ones.
    """
    try:
        # Step 1: Delete existing generated exercises for this user
        await conn.execute(
            "DELETE FROM user_generated_exercises WHERE user_id = $1",
            user_id
        )
        
        # Step 2: Calculate 1RM and get default reps/sets
        body_weight = user_data.get('current_weight_kg', 70.0)
        height = user_data.get('height_cm', 170)
        age = user_data.get('age', 25)
        fitness_level = user_data.get('fitness_level', 'intermediate')
        
        calculated_weight = calculate_one_rm(body_weight, height, age)
        default_reps, default_sets = get_default_reps_sets_by_fitness_level(fitness_level)
        
        # Step 3: Insert new generated exercises
        if exercise_ids:
            records_to_insert = [
                (user_id, exercise_id, calculated_weight, default_reps, default_sets, calculated_weight * 2)
                for exercise_id in exercise_ids
            ]
            
            await conn.copy_records_to_table(
                'user_generated_exercises',
                records=records_to_insert,
                columns=('user_id', 'exercise_id', 'weight_kg', 'reps', 'sets', 'one_rm_calculated')
            )
        
        return True
    except Exception as e:
        print(f"Error storing user generated exercises: {e}")
        return False

async def get_user_generated_exercises(conn: asyncpg.Connection, user_id: int) -> List[asyncpg.Record]:
    """
    Fetches user's generated exercises with their calculated weights, reps, and sets.
    """
    query = """
    SELECT 
        uge.id,
        uge.exercise_id,
        e.name,
        e.description,
        e.pro_tip,
        e.muscle_groups,
        e.video_url,
        fa.name as primary_focus_area,
        uge.weight_kg,
        uge.reps,
        uge.sets,
        uge.one_rm_calculated,
        uge.generated_at,
        uge.updated_at
    FROM user_generated_exercises uge
    JOIN exercises e ON uge.exercise_id = e.id
    LEFT JOIN focus_areas fa ON e.primary_focus_area_id = fa.id
    WHERE uge.user_id = $1
    ORDER BY uge.generated_at DESC;
    """
    return await conn.fetch(query, user_id)

async def update_user_generated_exercise(
    conn: asyncpg.Connection, 
    user_id: int, 
    exercise_id: int, 
    weight_kg: Optional[float] = None,
    reps: Optional[int] = None,
    sets: Optional[int] = None
) -> Optional[asyncpg.Record]:
    """
    Updates user's generated exercise with new weight, reps, or sets values.
    Only updates the fields that are provided (not None).
    """
    # Build dynamic SET clause based on provided values
    set_clauses = []
    params = [user_id, exercise_id]
    param_index = 3
    
    if weight_kg is not None:
        set_clauses.append(f"weight_kg = ${param_index}")
        params.append(weight_kg)
        param_index += 1
    
    if reps is not None:
        set_clauses.append(f"reps = ${param_index}")
        params.append(reps)
        param_index += 1
    
    if sets is not None:
        set_clauses.append(f"sets = ${param_index}")
        params.append(sets)
        param_index += 1
    
    if not set_clauses:
        return None  # Nothing to update
    
    # Always update the updated_at timestamp
    set_clauses.append("updated_at = NOW()")
    
    query = f"""
    UPDATE user_generated_exercises 
    SET {', '.join(set_clauses)}
    WHERE user_id = $1 AND exercise_id = $2
    RETURNING id, exercise_id, weight_kg, reps, sets, updated_at;
    """
    
    # Execute update and get the updated record
    updated_record = await conn.fetchrow(query, *params)
    
    if not updated_record:
        return None
    
    # Fetch the complete record with exercise details
    complete_query = """
    SELECT 
        uge.id,
        uge.exercise_id,
        e.name,
        uge.weight_kg,
        uge.reps,
        uge.sets,
        uge.updated_at
    FROM user_generated_exercises uge
    JOIN exercises e ON uge.exercise_id = e.id
    WHERE uge.user_id = $1 AND uge.exercise_id = $2;
    """
    
    return await conn.fetchrow(complete_query, user_id, exercise_id)

async def clear_user_generated_exercises(conn: asyncpg.Connection, user_id: int) -> bool:
    """
    Clears all generated exercises for a user.
    
    Returns:
        True if successful, False otherwise.
    """
    try:
        await conn.execute("DELETE FROM user_generated_exercises WHERE user_id = $1", user_id)
        return True
    except Exception:
        return False

async def get_user_routine_day_exercises(conn: asyncpg.Connection, user_id: int) -> List[asyncpg.Record]:
    """
    Fetches exercises from active routine days for a user.
    This includes exercises that are set directly on routine days (direct_exercises mode).
    """
    query = """
    SELECT DISTINCT
        urde.exercise_id,
        e.name,
        e.description,
        e.pro_tip,
        e.muscle_groups,
        e.video_url,
        fa.name as primary_focus_area,
        urde.order_in_day,
        urd.day_number,
        r.name as routine_name
    FROM user_routine_day_exercises urde
    JOIN user_routine_days urd ON urde.user_routine_day_id = urd.id
    JOIN user_routines ur ON urd.user_routine_id = ur.id
    JOIN routines r ON ur.routine_id = r.id
    JOIN exercises e ON urde.exercise_id = e.id
    LEFT JOIN focus_areas fa ON e.primary_focus_area_id = fa.id
    WHERE ur.user_id = $1 
      AND ur.is_active = true
      AND urd.exercise_mode = 'direct_exercises'
    ORDER BY urd.day_number, urde.order_in_day;
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
    of ALL its days, their focus areas/exercises, and which day is currently active.

    UPDATED: Now supports both focus_areas and direct_exercises modes.
    """
    query = """
    WITH ActiveRoutine AS (
        -- Step 1: Find the active routine and calculate the current day.
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
        -- Step 2: Pre-aggregate focus areas for each day.
        SELECT
            urd.user_routine_id,
            urd.day_number,
            urd.exercise_mode,
            COALESCE(
                json_agg(
                    json_build_object('id', fa.id, 'name', fa.name) ORDER BY fa.name
                ) FILTER (WHERE fa.id IS NOT NULL),
                '[]'::json
            ) AS focus_areas
        FROM user_routine_days urd
        LEFT JOIN user_routine_day_focus_areas urdfa ON urd.id = urdfa.user_routine_day_id
        LEFT JOIN focus_areas fa ON urdfa.focus_area_id = fa.id
        WHERE urd.user_routine_id IN (SELECT user_routine_id FROM ActiveRoutine)
        GROUP BY urd.user_routine_id, urd.day_number, urd.exercise_mode
    ),
    DayDirectExercises AS (
        -- Step 3: Pre-aggregate direct exercises for each day.
        SELECT
            urd.user_routine_id,
            urd.day_number,
            COALESCE(
                json_agg(
                    json_build_object(
                        'id', e.id,
                        'name', e.name,
                        'description', e.description,
                        'video_url', e.video_url,
                        'order_in_day', urde.order_in_day
                    ) ORDER BY urde.order_in_day, e.name
                ) FILTER (WHERE e.id IS NOT NULL),
                '[]'::json
            ) AS direct_exercises
        FROM user_routine_days urd
        LEFT JOIN user_routine_day_exercises urde ON urd.id = urde.user_routine_day_id
        LEFT JOIN exercises e ON urde.exercise_id = e.id
        WHERE urd.user_routine_id IN (SELECT user_routine_id FROM ActiveRoutine)
        GROUP BY urd.user_routine_id, urd.day_number
    )
    -- Final Step: Combine all the data.
    SELECT
        ar.routine_id,
        ar.routine_name,
        COALESCE(json_agg(
            json_build_object(
                'day_number', dfa.day_number,
                'is_current_day', (dfa.day_number = ar.current_day_number),
                'exercise_mode', dfa.exercise_mode,
                'focus_areas', dfa.focus_areas,
                'direct_exercises', dde.direct_exercises
            ) ORDER BY dfa.day_number
        ), '[]'::json) AS days
    FROM ActiveRoutine ar
    LEFT JOIN DayFocusAreas dfa ON ar.user_routine_id = dfa.user_routine_id
    LEFT JOIN DayDirectExercises dde ON ar.user_routine_id = dde.user_routine_id AND dfa.day_number = dde.day_number
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


async def swap_routine_days_content(conn: asyncpg.Connection, user_id: int, from_day: int, to_day: int) -> Dict[str, Any]:
    """
    Swaps the content (focus areas and/or direct exercises) between two routine days
    for the user's active routine. Handles all combinations:
    - Focus areas to focus areas
    - Direct exercises to direct exercises  
    - Focus areas to direct exercises
    - Direct exercises to focus areas
    
    Returns information about what was swapped.
    """
    async with conn.transaction():
        # Step 1: Get the user's active routine and validate the days exist
        active_routine_query = """
        SELECT ur.id as user_routine_id
        FROM user_routines ur
        WHERE ur.user_id = $1 AND ur.is_active = TRUE
        """
        active_routine = await conn.fetchrow(active_routine_query, user_id)
        
        if not active_routine:
            raise ValueError("No active routine found for user")
        
        user_routine_id = active_routine['user_routine_id']
        
        # Step 2: Get both days' data
        days_query = """
        SELECT 
            urd.id,
            urd.day_number,
            urd.exercise_mode,
            -- Focus areas
            COALESCE(
                array_agg(DISTINCT urdfa.focus_area_id) FILTER (WHERE urdfa.focus_area_id IS NOT NULL), 
                '{}'::int[]
            ) as focus_area_ids,
            -- Direct exercises
            COALESCE(
                json_agg(
                    json_build_object(
                        'exercise_id', urde.exercise_id,
                        'order_in_day', urde.order_in_day
                    ) ORDER BY urde.order_in_day
                ) FILTER (WHERE urde.exercise_id IS NOT NULL),
                '[]'::json
            ) as direct_exercises
        FROM user_routine_days urd
        LEFT JOIN user_routine_day_focus_areas urdfa ON urd.id = urdfa.user_routine_day_id
        LEFT JOIN user_routine_day_exercises urde ON urd.id = urde.user_routine_day_id
        WHERE urd.user_routine_id = $1 AND urd.day_number = ANY($2::int[])
        GROUP BY urd.id, urd.day_number, urd.exercise_mode
        ORDER BY urd.day_number
        """
        
        days_data = await conn.fetch(days_query, user_routine_id, [from_day, to_day])
        
        if len(days_data) != 2:
            raise ValueError(f"Could not find both days {from_day} and {to_day} in active routine")
        
        # Organize data by day number
        day_data = {row['day_number']: row for row in days_data}
        from_day_data = day_data[from_day]
        to_day_data = day_data[to_day]
        
        # Step 3: Clear existing data for both days
        await conn.execute(
            "DELETE FROM user_routine_day_focus_areas WHERE user_routine_day_id = ANY($1::int[])",
            [from_day_data['id'], to_day_data['id']]
        )
        await conn.execute(
            "DELETE FROM user_routine_day_exercises WHERE user_routine_day_id = ANY($1::int[])",
            [from_day_data['id'], to_day_data['id']]
        )
        
        # Step 4: Update exercise modes
        await conn.execute(
            "UPDATE user_routine_days SET exercise_mode = $1 WHERE id = $2",
            to_day_data['exercise_mode'], from_day_data['id']
        )
        await conn.execute(
            "UPDATE user_routine_days SET exercise_mode = $1 WHERE id = $2", 
            from_day_data['exercise_mode'], to_day_data['id']
        )
        
        # Step 5: Swap focus areas
        if from_day_data['focus_area_ids']:
            focus_area_records = [(to_day_data['id'], fa_id) for fa_id in from_day_data['focus_area_ids']]
            await conn.copy_records_to_table(
                'user_routine_day_focus_areas',
                records=focus_area_records,
                columns=('user_routine_day_id', 'focus_area_id')
            )
        
        if to_day_data['focus_area_ids']:
            focus_area_records = [(from_day_data['id'], fa_id) for fa_id in to_day_data['focus_area_ids']]
            await conn.copy_records_to_table(
                'user_routine_day_focus_areas', 
                records=focus_area_records,
                columns=('user_routine_day_id', 'focus_area_id')
            )
        
        # Step 6: Swap direct exercises
        from_exercises = json.loads(from_day_data['direct_exercises']) if isinstance(from_day_data['direct_exercises'], str) else from_day_data['direct_exercises']
        to_exercises = json.loads(to_day_data['direct_exercises']) if isinstance(to_day_data['direct_exercises'], str) else to_day_data['direct_exercises']
        
        if from_exercises:
            exercise_records = [(to_day_data['id'], ex['exercise_id'], ex['order_in_day']) for ex in from_exercises]
            await conn.copy_records_to_table(
                'user_routine_day_exercises',
                records=exercise_records,
                columns=('user_routine_day_id', 'exercise_id', 'order_in_day')
            )
        
        if to_exercises:
            exercise_records = [(from_day_data['id'], ex['exercise_id'], ex['order_in_day']) for ex in to_exercises]
            await conn.copy_records_to_table(
                'user_routine_day_exercises',
                records=exercise_records, 
                columns=('user_routine_day_id', 'exercise_id', 'order_in_day')
            )
        
        # Step 7: Determine what was swapped
        from_has_focus = bool(from_day_data['focus_area_ids'])
        from_has_exercises = bool(from_exercises)
        to_has_focus = bool(to_day_data['focus_area_ids'])
        to_has_exercises = bool(to_exercises)
        
        if (from_has_focus and to_has_focus) and not (from_has_exercises or to_has_exercises):
            swapped_type = "focus_areas"
        elif (from_has_exercises and to_has_exercises) and not (from_has_focus or to_has_focus):
            swapped_type = "direct_exercises"
        else:
            swapped_type = "mixed"
        
        return {
            "success": True,
            "swapped_content_type": swapped_type,
            "from_day_number": from_day,
            "to_day_number": to_day
        }


async def reorder_routine_days_content(conn: asyncpg.Connection, user_id: int, source_day: int, target_position: int) -> Dict[str, Any]:
    """
    Reorders routine days using drag-and-drop logic. When dragging a day to a new position,
    all days between the source and target shift accordingly.
    
    Example: If dragging day 3 to position 1 in a 5-day routine:
    - Day 3 content goes to day 1
    - Day 1 content shifts to day 2
    - Day 2 content shifts to day 3
    - Days 4 and 5 remain unchanged
    
    Returns information about the reorder operation.
    """
    if source_day == target_position:
        return {
            "success": True,
            "message": "No reordering needed - source and target are the same",
            "affected_days": []
        }
    
    async with conn.transaction():
        # Step 1: Get the user's active routine
        active_routine_query = """
        SELECT ur.id as user_routine_id
        FROM user_routines ur
        WHERE ur.user_id = $1 AND ur.is_active = TRUE
        """
        active_routine = await conn.fetchrow(active_routine_query, user_id)
        
        if not active_routine:
            raise ValueError("No active routine found for user")
        
        user_routine_id = active_routine['user_routine_id']
        
        # Step 2: Get all days data that will be affected
        if source_day < target_position:
            # Moving forward: get days from source to target
            affected_days = list(range(source_day, target_position + 1))
        else:
            # Moving backward: get days from target to source
            affected_days = list(range(target_position, source_day + 1))
        
        days_query = """
        SELECT 
            urd.id,
            urd.day_number,
            urd.exercise_mode,
            -- Focus areas
            COALESCE(
                array_agg(DISTINCT urdfa.focus_area_id) FILTER (WHERE urdfa.focus_area_id IS NOT NULL), 
                '{}'::int[]
            ) as focus_area_ids,
            -- Direct exercises
            COALESCE(
                json_agg(
                    json_build_object(
                        'exercise_id', urde.exercise_id,
                        'order_in_day', urde.order_in_day
                    ) ORDER BY urde.order_in_day
                ) FILTER (WHERE urde.exercise_id IS NOT NULL),
                '[]'::json
            ) as direct_exercises
        FROM user_routine_days urd
        LEFT JOIN user_routine_day_focus_areas urdfa ON urd.id = urdfa.user_routine_day_id
        LEFT JOIN user_routine_day_exercises urde ON urd.id = urde.user_routine_day_id
        WHERE urd.user_routine_id = $1 AND urd.day_number = ANY($2::int[])
        GROUP BY urd.id, urd.day_number, urd.exercise_mode
        ORDER BY urd.day_number
        """
        
        days_data = await conn.fetch(days_query, user_routine_id, affected_days)
        
        if len(days_data) != len(affected_days):
            raise ValueError(f"Could not find all required days in active routine")
        
        # Step 3: Create mapping of day_number to data
        day_data_map = {row['day_number']: row for row in days_data}
        
        # Step 4: Calculate the new arrangement
        source_data = day_data_map[source_day]
        
        if source_day < target_position:
            # Moving forward: shift days backward
            new_arrangement = {}
            for day in affected_days:
                if day == source_day:
                    continue  # Skip source, it goes to target
                elif day <= target_position:
                    new_arrangement[day - 1] = day_data_map[day]
            new_arrangement[target_position] = source_data
        else:
            # Moving backward: shift days forward
            new_arrangement = {}
            for day in affected_days:
                if day == source_day:
                    continue  # Skip source, it goes to target
                elif day >= target_position:
                    new_arrangement[day + 1] = day_data_map[day]
            new_arrangement[target_position] = source_data
        
        # Step 5: Clear existing data for affected days
        affected_day_ids = [day_data_map[day]['id'] for day in affected_days]
        await conn.execute(
            "DELETE FROM user_routine_day_focus_areas WHERE user_routine_day_id = ANY($1::int[])",
            affected_day_ids
        )
        await conn.execute(
            "DELETE FROM user_routine_day_exercises WHERE user_routine_day_id = ANY($1::int[])",
            affected_day_ids
        )
        
        # Step 6: Apply the new arrangement
        for new_day_number, data in new_arrangement.items():
            target_day_id = day_data_map[new_day_number]['id']
            
            # Update exercise mode
            await conn.execute(
                "UPDATE user_routine_days SET exercise_mode = $1 WHERE id = $2",
                data['exercise_mode'], target_day_id
            )
            
            # Insert focus areas
            if data['focus_area_ids']:
                focus_area_records = [(target_day_id, fa_id) for fa_id in data['focus_area_ids']]
                await conn.copy_records_to_table(
                    'user_routine_day_focus_areas',
                    records=focus_area_records,
                    columns=('user_routine_day_id', 'focus_area_id')
                )
            
            # Insert direct exercises
            direct_exercises = json.loads(data['direct_exercises']) if isinstance(data['direct_exercises'], str) else data['direct_exercises']
            if direct_exercises:
                exercise_records = [(target_day_id, ex['exercise_id'], ex['order_in_day']) for ex in direct_exercises]
                await conn.copy_records_to_table(
                    'user_routine_day_exercises',
                    records=exercise_records,
                    columns=('user_routine_day_id', 'exercise_id', 'order_in_day')
                )
        
        return {
            "success": True,
            "message": f"Successfully moved day {source_day} to position {target_position}",
            "source_day_number": source_day,
            "target_position": target_position,
            "affected_days": affected_days
        }


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


# --- Direct Exercise Management for Routine Days ---

async def add_exercise_to_day(conn: asyncpg.Connection, user_id: int, user_routine_id: int, day_number: int, exercise_id: int) -> bool:
    """
    Adds a direct exercise to a specific day within a user routine.
    Automatically switches the day to 'direct_exercises' mode and clears focus areas.
    
    Returns:
        True on successful insertion, False otherwise.
    """
    async with conn.transaction():
        # First, get the user_routine_day_id and verify ownership
        day_query = """
        SELECT urd.id, urd.exercise_mode
        FROM user_routine_days urd
        JOIN user_routines ur ON urd.user_routine_id = ur.id
        WHERE ur.user_id = $1 AND ur.id = $2 AND urd.day_number = $3
        """
        day_result = await conn.fetchrow(day_query, user_id, user_routine_id, day_number)
        
        if not day_result:
            return False
        
        user_routine_day_id = day_result['id']
        current_mode = day_result['exercise_mode']
        
        # If switching from focus_areas to direct_exercises, clear focus areas
        if current_mode == 'focus_areas':
            await conn.execute(
                "DELETE FROM user_routine_day_focus_areas WHERE user_routine_day_id = $1",
                user_routine_day_id
            )
            # Update mode to direct_exercises
            await conn.execute(
                "UPDATE user_routine_days SET exercise_mode = 'direct_exercises' WHERE id = $1",
                user_routine_day_id
            )
        
        # Add the exercise
        insert_query = """
        INSERT INTO user_routine_day_exercises (user_routine_day_id, exercise_id)
        VALUES ($1, $2)
        ON CONFLICT (user_routine_day_id, exercise_id) DO NOTHING
        RETURNING id
        """
        result = await conn.fetchval(insert_query, user_routine_day_id, exercise_id)
        return result is not None


async def delete_exercise_from_day(conn: asyncpg.Connection, user_id: int, user_routine_id: int, day_number: int, exercise_id: int) -> bool:
    """
    Deletes a direct exercise from a specific day within a user routine.
    Verifies ownership before deleting.
    
    Returns:
        True if an exercise was deleted, False otherwise.
    """
    query = """
    DELETE FROM user_routine_day_exercises urde
    WHERE urde.user_routine_day_id = (
        SELECT urd.id
        FROM user_routine_days urd
        JOIN user_routines ur ON urd.user_routine_id = ur.id
        WHERE ur.user_id = $1 AND ur.id = $2 AND urd.day_number = $3
    )
    AND urde.exercise_id = $4
    RETURNING urde.id
    """
    deleted_id = await conn.fetchval(query, user_id, user_routine_id, day_number, exercise_id)
    return deleted_id is not None


async def switch_day_to_focus_areas(conn: asyncpg.Connection, user_id: int, user_routine_id: int, day_number: int, focus_area_ids: List[int]) -> bool:
    """
    Switches a day from direct_exercises mode to focus_areas mode.
    Clears all direct exercises and adds the specified focus areas.
    
    Returns:
        True if successful, False otherwise.
    """
    async with conn.transaction():
        # Get the user_routine_day_id and verify ownership
        day_query = """
        SELECT urd.id
        FROM user_routine_days urd
        JOIN user_routines ur ON urd.user_routine_id = ur.id
        WHERE ur.user_id = $1 AND ur.id = $2 AND urd.day_number = $3
        """
        day_result = await conn.fetchrow(day_query, user_id, user_routine_id, day_number)
        
        if not day_result:
            return False
        
        user_routine_day_id = day_result['id']
        
        # Clear direct exercises
        await conn.execute(
            "DELETE FROM user_routine_day_exercises WHERE user_routine_day_id = $1",
            user_routine_day_id
        )
        
        # Clear existing focus areas
        await conn.execute(
            "DELETE FROM user_routine_day_focus_areas WHERE user_routine_day_id = $1",
            user_routine_day_id
        )
        
        # Update mode to focus_areas
        await conn.execute(
            "UPDATE user_routine_days SET exercise_mode = 'focus_areas' WHERE id = $1",
            user_routine_day_id
        )
        
        # Add new focus areas
        if focus_area_ids:
            focus_area_records = [(user_routine_day_id, fa_id) for fa_id in focus_area_ids]
            await conn.copy_records_to_table(
                'user_routine_day_focus_areas',
                records=focus_area_records,
                columns=('user_routine_day_id', 'focus_area_id')
            )
        
        return True
