import asyncpg
from typing import Optional, Dict, List, Any
import random
from app.s3 import build_exercise_video_url

async def add_custom_exercise(conn: asyncpg.Connection, user_id: int, exercise_id: int) -> Optional[Dict]:
    """
    Adds a custom exercise to the user's temporary custom exercises table.
    Calculates weight, reps, sets, and 1RM based on user's matrix settings.
    
    Returns:
        Dict with exercise details if successful, None if failed.
        Returns None if exercise is excluded (forever or today).
    """
    async with conn.transaction():
        # Check if exercise is permanently excluded (forever)
        exclusion_check = await conn.fetchrow("""
            SELECT 1 FROM user_excluded_exercises_forever 
            WHERE user_id = $1 AND exercise_id = $2
        """, user_id, exercise_id)
        
        if exclusion_check:
            # Exercise is permanently excluded, don't add it
            return None

        # If it was previously excluded "today", clear that soft exclusion
        await conn.execute(
            """
            DELETE FROM user_excluded_exercises_today
            WHERE user_id = $1 AND exercise_id = $2 AND excluded_date = CURRENT_DATE
            """,
            user_id,
            exercise_id,
        )

        # Get user's matrix settings
        user_query = """
        SELECT is_matrix, randomness, duration, rest_time, objective, 
               fitness_level, current_weight_kg
        FROM users 
        WHERE id = $1
        """
        user_data = await conn.fetchrow(user_query, user_id)
        
        if not user_data:
            return None
        
        # Get exercise details
        exercise_query = """
        SELECT e.id, e.name, e.description, e.video_url, e.exercise_type, fa.name as primary_focus_area
        FROM exercises e
        LEFT JOIN focus_areas fa ON e.primary_focus_area_id = fa.id
        WHERE e.id = $1
        """
        exercise_data = await conn.fetchrow(exercise_query, exercise_id)
        
        if not exercise_data:
            return None
        
        # Calculate exercise parameters based on user matrix
        weight_kg, reps, sets, one_rm = calculate_exercise_parameters(user_data, exercise_data)
        
        # Insert or update custom exercise
        insert_query = """
        INSERT INTO user_custom_exercises (user_id, exercise_id, weight_kg, reps, sets, one_rm_calculated)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (user_id, exercise_id) 
        DO UPDATE SET 
            weight_kg = EXCLUDED.weight_kg,
            reps = EXCLUDED.reps,
            sets = EXCLUDED.sets,
            one_rm_calculated = EXCLUDED.one_rm_calculated,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id, added_at, updated_at
        """
        
        result = await conn.fetchrow(insert_query, user_id, exercise_id, weight_kg, reps, sets, one_rm)
        
        if result:
            return {
                'id': result['id'],
                'exercise_id': exercise_id,
                'name': exercise_data['name'],
                'description': exercise_data['description'],
                'video_url': build_exercise_video_url(exercise_data['video_url']),
                'primary_focus_area': exercise_data['primary_focus_area'],
                'weight_kg': weight_kg,
                'reps': reps,
                'sets': sets,
                'one_rm_calculated': one_rm,
                'added_at': result['added_at'],
                'updated_at': result['updated_at']
            }
        
        return None

async def get_user_custom_exercises(conn: asyncpg.Connection, user_id: int) -> List[Dict]:
    """
    Retrieves all custom exercises for a user from temporary storage.
    
    Returns:
        List of custom exercise dictionaries.
    """
    query = """
    SELECT uce.id, uce.exercise_id, e.name, e.description, e.video_url,
           fa.name as primary_focus_area, uce.weight_kg, uce.reps, uce.sets,
           uce.one_rm_calculated, uce.added_at, uce.updated_at
    FROM user_custom_exercises uce
    JOIN exercises e ON uce.exercise_id = e.id
    LEFT JOIN focus_areas fa ON e.primary_focus_area_id = fa.id
    WHERE uce.user_id = $1
    ORDER BY uce.added_at DESC
    """
    
    rows = await conn.fetch(query, user_id)
    
    return [
        {
            'id': row['id'],
            'exercise_id': row['exercise_id'],
            'name': row['name'],
            'description': row['description'],
            'video_url': build_exercise_video_url(row['video_url']),
            'primary_focus_area': row['primary_focus_area'],
            'weight_kg': float(row['weight_kg']),
            'reps': row['reps'],
            'sets': row['sets'],
            'one_rm_calculated': float(row['one_rm_calculated']),
            'added_at': row['added_at'],
            'updated_at': row['updated_at']
        }
        for row in rows
    ]

async def update_user_custom_exercise(
    conn: asyncpg.Connection,
    user_id: int,
    exercise_id: int,
    weight_kg: Optional[float] = None,
    reps: Optional[int] = None,
    sets: Optional[int] = None
) -> Optional[asyncpg.Record]:
    """
    Updates user's custom exercise with new weight, reps, or sets values and recalculates 1RM.
    Only updates the fields that are provided (not None).
    Returns the updated record joined with exercise name or None if not found.
    """
    # Ensure there is something to update
    if weight_kg is None and reps is None and sets is None:
        return None

    # Fetch current values to compute 1RM
    current = await conn.fetchrow(
        """
        SELECT id, weight_kg, reps
        FROM user_custom_exercises
        WHERE user_id = $1 AND exercise_id = $2
        """,
        user_id,
        exercise_id,
    )

    if not current:
        return None

    final_weight = float(weight_kg if weight_kg is not None else current['weight_kg'])
    final_reps = int(reps if reps is not None else current['reps'])
    new_one_rm = round(final_weight * (1 + final_reps / 30.0), 2)

    # Build dynamic SET clause
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

    # Always update 1RM and timestamp
    set_clauses.append(f"one_rm_calculated = ${param_index}")
    params.append(new_one_rm)
    param_index += 1
    set_clauses.append("updated_at = NOW()")

    update_query = f"""
    UPDATE user_custom_exercises
    SET {', '.join(set_clauses)}
    WHERE user_id = $1 AND exercise_id = $2
    RETURNING id, exercise_id, weight_kg, reps, sets, updated_at;
    """

    updated = await conn.fetchrow(update_query, *params)
    if not updated:
        return None

    complete_query = """
    SELECT 
        uce.id,
        uce.exercise_id,
        e.name,
        uce.weight_kg,
        uce.reps,
        uce.sets,
        uce.updated_at
    FROM user_custom_exercises uce
    JOIN exercises e ON uce.exercise_id = e.id
    WHERE uce.user_id = $1 AND uce.exercise_id = $2;
    """

    return await conn.fetchrow(complete_query, user_id, exercise_id)

async def clear_user_custom_exercises(conn: asyncpg.Connection, user_id: int) -> bool:
    """
    Clears all custom exercises for a user from temporary storage.
    
    Returns:
        True if successful, False otherwise.
    """
    try:
        await conn.execute("DELETE FROM user_custom_exercises WHERE user_id = $1", user_id)
        return True
    except Exception:
        return False

def calculate_exercise_parameters(user_data: asyncpg.Record, exercise_data: asyncpg.Record) -> tuple:
    """
    Calculates exercise parameters (weight, reps, sets, 1RM) based on user's matrix settings and exercise type.
    Different exercise types get different parameter ranges for optimal training.
    
    Returns:
        Tuple of (weight_kg, reps, sets, one_rm_calculated)
    """
    # Base values adjusted by exercise type
    base_weight = float(user_data['current_weight_kg'] or 70.0) * 0.5  # Start with 50% of body weight
    exercise_type = exercise_data.get('exercise_type', 'strength')
    
    # Adjust base parameters based on exercise type
    if exercise_type == 'muscle_growth':
        base_reps = 10  # Hypertrophy range: 8-12 reps
        base_sets = 4   # More volume for muscle growth
        weight_multiplier = 0.7  # Moderate weight
    elif exercise_type == 'strength':
        base_reps = 6   # Strength range: 3-6 reps
        base_sets = 3   # Standard strength sets
        weight_multiplier = 0.8  # Higher weight
    elif exercise_type == 'cardio':
        base_reps = 20  # Higher reps for cardio
        base_sets = 3   # Standard sets
        weight_multiplier = 0.4  # Lower weight, focus on endurance
    else:  # flexibility or other
        base_reps = 12
        base_sets = 3
        weight_multiplier = 0.5
    
    base_weight *= weight_multiplier
    
    # Adjust based on fitness level
    fitness_multipliers = {
        'beginner': 0.6,
        'intermediate': 0.8,
        'advanced': 1.0
    }
    
    multiplier = fitness_multipliers.get(user_data['fitness_level'], 0.8)
    
    # Apply randomness if enabled
    if user_data['randomness']:
        randomness_factor = user_data['randomness'] / 100.0
        weight_variation = random.uniform(1 - randomness_factor * 0.2, 1 + randomness_factor * 0.2)
        reps_variation = random.uniform(1 - randomness_factor * 0.3, 1 + randomness_factor * 0.3)
    else:
        weight_variation = 1.0
        reps_variation = 1.0
    
    # Calculate final values
    weight_kg = round(base_weight * multiplier * weight_variation, 2)
    reps = max(1, int(base_reps * reps_variation))
    sets = base_sets
    
    # Calculate 1RM using Epley formula: 1RM = weight * (1 + reps/30)
    one_rm_calculated = round(weight_kg * (1 + reps / 30.0), 2)
    
    return weight_kg, reps, sets, one_rm_calculated
