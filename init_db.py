import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database connection parameters
DB_NAME = "fitness_db"
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "admin123")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")

def create_database():
    """Create the database if it doesn't exist"""
    try:
        # Connect to the default 'postgres' database
        conn = psycopg2.connect(
            dbname="postgres",
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cursor = conn.cursor()
        
        # Check if database exists
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
        exists = cursor.fetchone()
        
        if not exists:
            # Create the database
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DB_NAME)))
            print(f"Database '{DB_NAME}' created successfully")
        else:
            print(f"Database '{DB_NAME}' already exists")
            
        cursor.close()
        conn.close()
        
        return True
    except psycopg2.Error as e:
        print(f"Error creating database: {e}")
        return False

def execute_sql_file(file_path, db_name):
    """
    Execute a large SQL script as a single transaction.
    This is much more robust than splitting by semicolons.
    """
    conn = None  # Initialize conn to None
    try:
        # Connect to the specified database
        conn = psycopg2.connect(
            dbname=db_name,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT
        )
        cursor = conn.cursor()
        
        # Read the entire SQL file
        with open(file_path, 'r', encoding='utf-8') as file:
            sql_script = file.read()
            
        # Execute the entire script as a single command
        print("Executing SQL script...")
        cursor.execute(sql_script)
        
        # Commit the transaction
        conn.commit()
        
        print("SQL script executed successfully and changes committed.")
        return True
    except psycopg2.Error as e:
        print(f"--- SQL Execution Error ---")
        print(f"PostgreSQL Error: {e.pgcode} - {e.pgerror}")
        print("Transaction will be rolled back.")
        if conn:
            conn.rollback()
        return False
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            cursor.close()
            conn.close()

def generate_full_sql_script():
    """
    Generates the complete SQL script including schema and detailed exercise data.
    This approach keeps data management in Python, which is easier to read and maintain.
    """
    
    # Base schema definition
    schema_sql = """
    -- Drop existing objects in reverse order of dependency to ensure a clean slate.
    DROP TRIGGER IF EXISTS trigger_assign_routines_on_user_insert ON users;
    DROP FUNCTION IF EXISTS assign_default_routines_to_user();

    DROP TABLE IF EXISTS 
        user_routine_day_focus_areas, user_routine_days, user_routines,
        routine_day_focus_areas, routine_days,
        weight_history, body_measurement_history, user_workout_days,
        workout_logs, workout_plan_exercises, workout_plans, 
        user_goals, user_focus_areas, user_health_issues, user_equipment, 
        exercise_focus_areas, exercise_equipment, exercise_contraindications, exercise_fitness_levels,
        exercises, users, routines, goals, motivations, user_motivations, focus_areas, health_issues, equipment,
        token_blocklist, user_login_history CASCADE;

    DROP TYPE IF EXISTS 
        gender_enum, fitness_level_enum, activity_level_enum, day_of_week_enum, 
        user_status_enum, unit_preference_enum, exercise_type_enum;

    -- Trigger function to automatically update the 'updated_at' timestamp on any table.
    CREATE OR REPLACE FUNCTION update_updated_at_column()
    RETURNS TRIGGER AS $$
    BEGIN
        NEW.updated_at = NOW();
        RETURN NEW;
    END;
    $$ language 'plpgsql';

    -- ========= ENUMERATED TYPES (Unchanged) =========
    CREATE TYPE gender_enum AS ENUM ('male', 'female', 'other', 'prefer_not_to_say');
    CREATE TYPE fitness_level_enum AS ENUM ('beginner', 'intermediate', 'advanced');
    CREATE TYPE activity_level_enum AS ENUM ('sedentary', 'lightly_active', 'moderately_active', 'very_active');
    CREATE TYPE day_of_week_enum AS ENUM ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday');
    CREATE TYPE user_status_enum AS ENUM ('active', 'pending_verification', 'suspended', 'deleted');
    CREATE TYPE unit_preference_enum AS ENUM ('metric', 'imperial');
    CREATE TYPE exercise_type_enum AS ENUM ('strength', 'cardio', 'flexibility');

    -- ========= LOOKUP TABLES (Unchanged) =========
    CREATE TABLE goals (id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE, description TEXT);
    CREATE TABLE motivations (id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE, description TEXT);
    CREATE TABLE focus_areas (id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE, description TEXT);
    CREATE TABLE health_issues (id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE, description TEXT);
    CREATE TABLE equipment (id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE, description TEXT);

    -- ========= NEW: ROUTINE TEMPLATE TABLES =========
    CREATE TABLE routines (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100) NOT NULL UNIQUE,
        description TEXT
    );

    CREATE TABLE routine_days (
        id SERIAL PRIMARY KEY,
        routine_id INTEGER NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
        day_number INTEGER NOT NULL,
        UNIQUE (routine_id, day_number)
    );

    CREATE TABLE routine_day_focus_areas (
        routine_day_id INTEGER NOT NULL REFERENCES routine_days(id) ON DELETE CASCADE,
        focus_area_id INTEGER NOT NULL REFERENCES focus_areas(id) ON DELETE CASCADE,
        PRIMARY KEY (routine_day_id, focus_area_id)
    );
    
    -- ========= CORE ENTITY TABLES =========
    -- MODIFIED: users.routine_id is REMOVED as per the new flexible routine system.
    CREATE TABLE users (
        id BIGSERIAL PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        email VARCHAR(255) NOT NULL UNIQUE,
        password_hash VARCHAR(255) NOT NULL,
        gender gender_enum,
        age INTEGER CHECK (age > 0 AND age < 120),
        height_cm INTEGER,
        current_weight_kg DECIMAL(5, 2), 
        target_weight_kg DECIMAL(5, 2),
        fitness_level fitness_level_enum NOT NULL,
        activity_level activity_level_enum NOT NULL,
        workouts_per_week INTEGER CHECK (workouts_per_week >= 1 AND workouts_per_week <= 7),

        account_status user_status_enum NOT NULL DEFAULT 'pending_verification',
        unit_preference unit_preference_enum NOT NULL DEFAULT 'metric',
        timezone VARCHAR(100) DEFAULT 'UTC',
        last_login_at TIMESTAMP WITH TIME ZONE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        -- These string fields can be considered for deprecation in favor of junction tables
        goals VARCHAR(255),
        focus_areas VARCHAR(255),
        health_issues VARCHAR(255),
        equipment VARCHAR(255),
        workout_days VARCHAR(255)
    );
    CREATE TABLE user_motivations (
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        motivation_id INTEGER NOT NULL REFERENCES motivations(id) ON DELETE CASCADE,
        PRIMARY KEY (user_id, motivation_id)
    );

    CREATE INDEX idx_users_email ON users(email);
    CREATE TRIGGER trigger_users_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

    CREATE TABLE exercises (
        id SERIAL PRIMARY KEY,
        name VARCHAR(255) NOT NULL UNIQUE,
        description TEXT,
        video_url VARCHAR(255),
        exercise_type exercise_type_enum,
        primary_focus_area_id INTEGER REFERENCES focus_areas(id), 
        is_high_impact BOOLEAN DEFAULT FALSE
    );
    CREATE INDEX idx_exercises_name ON exercises(name);

    -- ========= NEW: USER-SPECIFIC CUSTOMIZABLE ROUTINE TABLES =========
    CREATE TABLE user_routines (
        id SERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        routine_id INTEGER NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
        is_active BOOLEAN DEFAULT FALSE,
        current_day_number INTEGER DEFAULT NULL,
        UNIQUE (user_id, routine_id)
    );

    CREATE TABLE user_routine_days (
        id SERIAL PRIMARY KEY,
        user_routine_id INTEGER NOT NULL REFERENCES user_routines(id) ON DELETE CASCADE,
        day_number INTEGER NOT NULL,
        UNIQUE (user_routine_id, day_number)
    );

    CREATE TABLE user_routine_day_focus_areas (
        user_routine_day_id INTEGER NOT NULL REFERENCES user_routine_days(id) ON DELETE CASCADE,
        focus_area_id INTEGER NOT NULL REFERENCES focus_areas(id) ON DELETE CASCADE,
        PRIMARY KEY (user_routine_day_id, focus_area_id)
    );

    -- ========= NEW: FUNCTION AND TRIGGER FOR AUTOMATIC ROUTINE ASSIGNMENT =========
    CREATE OR REPLACE FUNCTION assign_default_routines_to_user()
    RETURNS TRIGGER AS $$
    DECLARE
        routine_record RECORD;
        day_record RECORD;
        new_user_routine_id INT;
        new_user_routine_day_id INT;
    BEGIN
        -- Loop through each master routine in the 'routines' table
        FOR routine_record IN SELECT * FROM routines LOOP
            -- 1. Create a personal copy of the routine for the new user
            INSERT INTO user_routines (user_id, routine_id)
            VALUES (NEW.id, routine_record.id)
            RETURNING id INTO new_user_routine_id;

            -- 2. Loop through the default days for that master routine
            FOR day_record IN SELECT * FROM routine_days WHERE routine_id = routine_record.id LOOP
                -- 2a. Create a personal copy of the day for the user's new routine
                INSERT INTO user_routine_days (user_routine_id, day_number)
                VALUES (new_user_routine_id, day_record.day_number)
                RETURNING id INTO new_user_routine_day_id;

                -- 2b. Copy all default focus areas for that day to the user's new day
                INSERT INTO user_routine_day_focus_areas (user_routine_day_id, focus_area_id)
                SELECT new_user_routine_day_id, rdfa.focus_area_id
                FROM routine_day_focus_areas rdfa
                WHERE rdfa.routine_day_id = day_record.id;
            END LOOP;
        END LOOP;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;

    CREATE TRIGGER trigger_assign_routines_on_user_insert
    AFTER INSERT ON users
    FOR EACH ROW
    EXECUTE FUNCTION assign_default_routines_to_user();
    
    -- ========= JUNCTION & LOGGING TABLES (Largely Unchanged) =========
    CREATE TABLE user_workout_days (user_id BIGINT REFERENCES users(id) ON DELETE CASCADE, day day_of_week_enum NOT NULL, PRIMARY KEY (user_id, day));
    CREATE TABLE user_goals (user_id BIGINT REFERENCES users(id) ON DELETE CASCADE, goal_id INTEGER REFERENCES goals(id) ON DELETE CASCADE, PRIMARY KEY (user_id, goal_id));
    CREATE TABLE user_focus_areas (user_id BIGINT REFERENCES users(id) ON DELETE CASCADE, focus_area_id INTEGER REFERENCES focus_areas(id) ON DELETE CASCADE, PRIMARY KEY (user_id, focus_area_id));
    CREATE TABLE user_health_issues (user_id BIGINT REFERENCES users(id) ON DELETE CASCADE, health_issue_id INTEGER REFERENCES health_issues(id) ON DELETE CASCADE, PRIMARY KEY (user_id, health_issue_id));
    CREATE TABLE user_equipment (user_id BIGINT REFERENCES users(id) ON DELETE CASCADE, equipment_id INTEGER REFERENCES equipment(id) ON DELETE CASCADE, PRIMARY KEY (user_id, equipment_id));
    CREATE TABLE exercise_fitness_levels (exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE, fitness_level fitness_level_enum NOT NULL, PRIMARY KEY (exercise_id, fitness_level));
    CREATE TABLE exercise_focus_areas (exercise_id INTEGER REFERENCES exercises(id) ON DELETE CASCADE, focus_area_id INTEGER REFERENCES focus_areas(id) ON DELETE CASCADE, is_primary BOOLEAN DEFAULT FALSE, PRIMARY KEY (exercise_id, focus_area_id));
    CREATE TABLE exercise_equipment (exercise_id INTEGER REFERENCES exercises(id) ON DELETE CASCADE, equipment_id INTEGER REFERENCES equipment(id) ON DELETE CASCADE, PRIMARY KEY (exercise_id, equipment_id));
    CREATE TABLE exercise_contraindications (exercise_id INTEGER REFERENCES exercises(id) ON DELETE CASCADE, health_issue_id INTEGER REFERENCES health_issues(id) ON DELETE CASCADE, PRIMARY KEY (exercise_id, health_issue_id));
    CREATE TABLE weight_history (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, weight_kg DECIMAL(5, 2) NOT NULL, logged_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE body_measurement_history (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, measurement_type VARCHAR(50) NOT NULL, value DECIMAL(6, 2) NOT NULL, logged_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE workout_plans (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, plan_name VARCHAR(255) NOT NULL, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE workout_plan_exercises (id BIGSERIAL PRIMARY KEY, workout_plan_id BIGINT NOT NULL REFERENCES workout_plans(id) ON DELETE CASCADE, exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE, day_of_week day_of_week_enum NOT NULL, order_in_workout INTEGER DEFAULT 0, sets_recommended INTEGER, reps_recommended VARCHAR(50), rest_period_seconds INTEGER);
    CREATE TABLE workout_logs (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE, workout_plan_exercise_id BIGINT REFERENCES workout_plan_exercises(id) ON DELETE SET NULL, completed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP, set_number INTEGER, weight_kg DECIMAL(6, 2), reps_completed INTEGER, duration_seconds INTEGER, notes TEXT);
    CREATE TABLE IF NOT EXISTS token_blocklist (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, access_token TEXT NOT NULL UNIQUE, refresh_token TEXT NOT NULL, exp_time TIMESTAMP WITH TIME ZONE NOT NULL, revoked BOOLEAN NOT NULL DEFAULT FALSE, created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP);
    CREATE TRIGGER trigger_set_updated_at_token_blocklist BEFORE UPDATE ON token_blocklist FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    CREATE TABLE IF NOT EXISTS user_login_history (id SERIAL PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, login_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP, logout_time TIMESTAMP WITH TIME ZONE, created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP);
    
    -- ========= INSERT INITIAL LOOKUP AND TEMPLATE DATA =========
    -- Basic Lookups
    INSERT INTO goals (name) VALUES ('Improve Fitness'), ('Build Muscle'), ('Burn Fat'), ('Increase Endurance'), ('Boost Mental Strength'), ('Improve Balance');
    INSERT INTO motivations (name) VALUES ('Health and Wellness'), ('Weight Management'), ('Achievement'), ('Become Sexually Attractive'), ('Social Support');
    INSERT INTO focus_areas (name) VALUES ('Chest'), ('Back'), ('Arms'), ('Shoulders'), ('Abs'), ('Legs'), ('Glutes'), ('Full Body'), ('Hips'), ('Thighs'), ('Calves'), ('Forearms'), ('Waist'), ('Biceps'), ('Triceps');
    INSERT INTO health_issues (name, description) VALUES ('None', 'No health issues.'), ('Post-COVID Recovery', 'Avoid high-intensity exercises.'), ('Cannot Jump', 'Avoid high-impact exercises.'), ('Back or Hernia', 'Avoid heavy spinal loading.'), ('Knee Pain', 'Avoid deep squats and lunges.');
    INSERT INTO equipment (name) VALUES ('Full Gym'), ('Barbell'), ('Dumbbell'), ('Kettlebell'), ('Bodyweight'), ('Band'), ('Leverage machine'), ('Smith machine'), ('Cable'), ('Suspension'), ('Stability ball');

    -- Seeding Routines and their default day/focus area structures
    -- 1. 3 Day Classic
    WITH routine AS (INSERT INTO routines (name) VALUES ('3 Day Classic') RETURNING id),
         day1 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 1 FROM routine RETURNING id),
         day2 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 2 FROM routine RETURNING id),
         day3 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 3 FROM routine RETURNING id)
    INSERT INTO routine_day_focus_areas (routine_day_id, focus_area_id) VALUES
        ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Chest')),
        ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Triceps')),
        ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Abs')),
        ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Back')),
        ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Biceps')),
        ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Abs')),
        ((SELECT id FROM day3), (SELECT id FROM focus_areas WHERE name = 'Legs')),
        ((SELECT id FROM day3), (SELECT id FROM focus_areas WHERE name = 'Shoulders')),
        ((SELECT id FROM day3), (SELECT id FROM focus_areas WHERE name = 'Abs'));

    -- 2. 4 Day Classic
    WITH routine AS (INSERT INTO routines (name) VALUES ('4 Day Classic') RETURNING id),
         day1 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 1 FROM routine RETURNING id),
         day2 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 2 FROM routine RETURNING id),
         day3 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 3 FROM routine RETURNING id),
         day4 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 4 FROM routine RETURNING id)
    INSERT INTO routine_day_focus_areas (routine_day_id, focus_area_id) VALUES
        ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Chest')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Triceps')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Abs')),
        ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Back')), ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Biceps')), ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Abs')),
        ((SELECT id FROM day3), (SELECT id FROM focus_areas WHERE name = 'Shoulders')), ((SELECT id FROM day3), (SELECT id FROM focus_areas WHERE name = 'Abs')),
        ((SELECT id FROM day4), (SELECT id FROM focus_areas WHERE name = 'Legs')), ((SELECT id FROM day4), (SELECT id FROM focus_areas WHERE name = 'Abs'));

    -- 3. Push, Pull, Legs
    WITH routine AS (INSERT INTO routines (name) VALUES ('Push, Pull, Legs') RETURNING id),
         day1 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 1 FROM routine RETURNING id),
         day2 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 2 FROM routine RETURNING id),
         day3 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 3 FROM routine RETURNING id)
    INSERT INTO routine_day_focus_areas (routine_day_id, focus_area_id) VALUES
        ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Chest')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Shoulders')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Triceps')),
        ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Back')), ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Biceps')),
        ((SELECT id FROM day3), (SELECT id FROM focus_areas WHERE name = 'Legs')), ((SELECT id FROM day3), (SELECT id FROM focus_areas WHERE name = 'Abs'));

    -- 4. Push, Pull
    WITH routine AS (INSERT INTO routines (name) VALUES ('Push, Pull') RETURNING id),
         day1 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 1 FROM routine RETURNING id),
         day2 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 2 FROM routine RETURNING id)
    INSERT INTO routine_day_focus_areas (routine_day_id, focus_area_id) VALUES
        ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Chest')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Shoulders')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Triceps')),
        ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Back')), ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Biceps'));

    -- 5. Upper, Lower
    WITH routine AS (INSERT INTO routines (name) VALUES ('Upper, Lower') RETURNING id),
         day1 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 1 FROM routine RETURNING id),
         day2 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 2 FROM routine RETURNING id)
    INSERT INTO routine_day_focus_areas (routine_day_id, focus_area_id) VALUES
        ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Chest')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Back')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Shoulders')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Biceps')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Triceps')),
        ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Legs')), ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Abs'));

    -- 6. Full Body
    WITH routine AS (INSERT INTO routines (name) VALUES ('Full Body') RETURNING id),
         day1 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 1 FROM routine RETURNING id)
    INSERT INTO routine_day_focus_areas (routine_day_id, focus_area_id) VALUES
        ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Legs')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Back')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Chest')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Biceps')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Shoulders')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Triceps')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Abs'));

    -- 7. Adaptive and Custom (no default days/focus areas)
    INSERT INTO routines (name) VALUES ('Adaptive'), ('Custom');
    
    """
    
    # --- Structured Exercise Data ---
    exercise_data = [
        # Note the more specific focus areas for push/pull movements
        {'name': 'Barbell Bench Press', 'type': 'strength', 'impact': False, 'desc': 'A compound exercise for the upper body...', 'difficulty_levels': ['intermediate', 'advanced'], 'focus_areas': ['Chest', 'Shoulders', 'Triceps'], 'equipment': ['Barbell', 'Full Gym'], 'contraindications': []},
        {'name': 'Dumbbell Bench Press', 'type': 'strength', 'impact': False, 'desc': 'Similar to the barbell bench press...', 'difficulty_levels': ['beginner', 'intermediate', 'advanced'], 'focus_areas': ['Chest', 'Shoulders', 'Triceps'], 'equipment': ['Dumbbell', 'Full Gym'], 'contraindications': []},
        {'name': 'Push-up', 'type': 'strength', 'impact': False, 'desc': 'A classic bodyweight exercise...', 'difficulty_levels': ['beginner', 'intermediate', 'advanced'], 'focus_areas': ['Chest', 'Shoulders', 'Triceps', 'Abs'], 'equipment': ['Bodyweight'], 'contraindications': []},
        {'name': 'Cable Crossover', 'type': 'strength', 'impact': False, 'desc': 'An isolation exercise for the chest...', 'difficulty_levels': ['intermediate', 'advanced'], 'focus_areas': ['Chest'], 'equipment': ['Cable', 'Full Gym'], 'contraindications': []},
        {'name': 'Deadlift', 'type': 'strength', 'impact': False, 'desc': 'A full-body compound exercise...', 'difficulty_levels': ['intermediate', 'advanced'], 'focus_areas': ['Back', 'Glutes', 'Legs', 'Full Body'], 'equipment': ['Barbell', 'Dumbbell', 'Kettlebell'], 'contraindications': ['Back or Hernia']},
        {'name': 'Pull-up', 'type': 'strength', 'impact': False, 'desc': 'An advanced bodyweight exercise...', 'difficulty_levels': ['advanced'], 'focus_areas': ['Back', 'Biceps'], 'equipment': ['Bodyweight', 'Full Gym'], 'contraindications': []},
        {'name': 'Bent-Over Row', 'type': 'strength', 'impact': False, 'desc': 'A compound exercise for building a strong back.', 'difficulty_levels': ['intermediate', 'advanced'], 'focus_areas': ['Back', 'Biceps'], 'equipment': ['Barbell', 'Dumbbell'], 'contraindications': ['Back or Hernia']},
        {'name': 'Lat Pulldown', 'type': 'strength', 'impact': False, 'desc': 'A machine-based exercise that mimics the pull-up.', 'difficulty_levels': ['beginner', 'intermediate'], 'focus_areas': ['Back', 'Biceps'], 'equipment': ['Cable', 'Leverage machine', 'Full Gym'], 'contraindications': []},
        {'name': 'Barbell Squat', 'type': 'strength', 'impact': False, 'desc': 'The king of leg exercises...', 'difficulty_levels': ['intermediate', 'advanced'], 'focus_areas': ['Legs', 'Glutes', 'Full Body'], 'equipment': ['Barbell', 'Smith machine', 'Full Gym'], 'contraindications': ['Knee Pain', 'Back or Hernia']},
        {'name': 'Dumbbell Lunge', 'type': 'strength', 'impact': False, 'desc': 'A unilateral exercise for balance and strength.', 'difficulty_levels': ['beginner', 'intermediate'], 'focus_areas': ['Legs', 'Glutes'], 'equipment': ['Dumbbell', 'Kettlebell', 'Bodyweight'], 'contraindications': ['Knee Pain']},
        {'name': 'Leg Press', 'type': 'strength', 'impact': False, 'desc': 'A machine exercise for heavy leg training...', 'difficulty_levels': ['beginner', 'intermediate', 'advanced'], 'focus_areas': ['Legs', 'Glutes'], 'equipment': ['Leverage machine', 'Full Gym'], 'contraindications': []},
        {'name': 'Calf Raise', 'type': 'strength', 'impact': False, 'desc': 'An isolation exercise for the calf muscles.', 'difficulty_levels': ['beginner', 'intermediate', 'advanced'], 'focus_areas': ['Calves', 'Legs'], 'equipment': ['Bodyweight', 'Dumbbell', 'Barbell', 'Leverage machine'], 'contraindications': []},
        {'name': 'Plank', 'type': 'strength', 'impact': False, 'desc': 'An isometric core exercise for stability.', 'difficulty_levels': ['beginner', 'intermediate', 'advanced'], 'focus_areas': ['Abs', 'Full Body'], 'equipment': ['Bodyweight'], 'contraindications': []},
        {'name': 'Hanging Leg Raise', 'type': 'strength', 'impact': False, 'desc': 'An advanced core exercise for lower abs.', 'difficulty_levels': ['advanced'], 'focus_areas': ['Abs'], 'equipment': ['Bodyweight', 'Full Gym'], 'contraindications': ['Back or Hernia']},
        {'name': 'Burpee', 'type': 'cardio', 'impact': True, 'desc': 'A full-body aerobic exercise.', 'difficulty_levels': ['intermediate', 'advanced'], 'focus_areas': ['Full Body', 'Legs', 'Chest'], 'equipment': ['Bodyweight'], 'contraindications': ['Cannot Jump', 'Knee Pain', 'Post-COVID Recovery']},
        {'name': 'Jumping Jacks', 'type': 'cardio', 'impact': True, 'desc': 'A classic full-body cardio exercise.', 'difficulty_levels': ['beginner'], 'focus_areas': ['Full Body'], 'equipment': ['Bodyweight'], 'contraindications': ['Cannot Jump', 'Knee Pain']},
        {'name': 'Kettlebell Swing', 'type': 'strength', 'impact': False, 'desc': 'A ballistic exercise for the posterior chain.', 'difficulty_levels': ['intermediate', 'advanced'], 'focus_areas': ['Glutes', 'Legs', 'Back', 'Full Body'], 'equipment': ['Kettlebell'], 'contraindications': ['Back or Hernia']},
    ]

    insert_commands = ["\n-- ========= DYNAMICALLY INSERTED EXERCISE DATA =========\n"]
    def sql_escape(text):
        return text.replace("'", "''")

    for ex in exercise_data:
        ex_name = sql_escape(ex['name'])
        ex_desc = sql_escape(ex.get('desc', ''))
        ex_type = ex.get('type', 'strength')
        ex_impact = ex.get('impact', False)
        primary_focus_area_name = sql_escape(ex['focus_areas'][0])
        with_clauses = [f"new_exercise AS (INSERT INTO exercises (name, description, exercise_type, is_high_impact, primary_focus_area_id) VALUES ('{ex_name}', '{ex_desc}', '{ex_type}', {ex_impact}, (SELECT id FROM focus_areas WHERE name = '{primary_focus_area_name}')) RETURNING id)"]
        if ex.get('difficulty_levels'):
            difficulty_selects = [f"SELECT id, '{level}'::fitness_level_enum FROM new_exercise" for level in ex['difficulty_levels']]
            with_clauses.append(f"ins_difficulty AS (INSERT INTO exercise_fitness_levels (exercise_id, fitness_level) {' UNION ALL '.join(difficulty_selects)})")
        if ex.get('focus_areas'):
            focus_area_selects = []
            for i, area in enumerate(ex['focus_areas']):
                is_primary = 'TRUE' if i == 0 else 'FALSE'
                focus_area_selects.append(f"SELECT id, (SELECT id FROM focus_areas WHERE name = '{sql_escape(area)}'), {is_primary} FROM new_exercise")
            with_clauses.append(f"ins_focus_areas AS (INSERT INTO exercise_focus_areas (exercise_id, focus_area_id, is_primary) {' UNION ALL '.join(focus_area_selects)})")
        if ex.get('equipment'):
            equipment_selects = [f"SELECT id, (SELECT id FROM equipment WHERE name = '{sql_escape(equip)}') FROM new_exercise" for equip in ex['equipment']]
            with_clauses.append(f"ins_equipment AS (INSERT INTO exercise_equipment (exercise_id, equipment_id) {' UNION ALL '.join(equipment_selects)})")
        if ex.get('contraindications'):
            contra_selects = [f"SELECT id, (SELECT id FROM health_issues WHERE name = '{sql_escape(issue)}') FROM new_exercise" for issue in ex['contraindications']]
            with_clauses.append(f"ins_contra AS (INSERT INTO exercise_contraindications (exercise_id, health_issue_id) {' UNION ALL '.join(contra_selects)})")
        full_statement = "WITH " + ",\n".join(with_clauses) + "\nSELECT 1;"
        insert_commands.append(full_statement)
        
    return schema_sql + "\n".join(insert_commands)


def main():
    if not create_database():
        print("Failed to create or verify database existence. Exiting...")
        return
    
    print("Generating full SQL script...")
    full_sql_script = generate_full_sql_script()
    
    temp_sql_file = "temp_schema_and_data.sql"
    with open(temp_sql_file, "w", encoding='utf-8') as f:
        f.write(full_sql_script)
    
    try:
        if execute_sql_file(temp_sql_file, DB_NAME):
            print("\nDatabase setup complete! New routine system is in place.")
        else:
            print("\nDatabase setup failed. Please check the errors above.")
    finally:
        if os.path.exists(temp_sql_file):
            os.remove(temp_sql_file)

if __name__ == "__main__":
    main()