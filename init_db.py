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

        # Enforce only one active routine per user
        try:
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS unique_active_routine_per_user
                ON user_routines(user_id)
                WHERE is_active = TRUE;
            """)
            conn.commit()
            print("Unique active routine index ensured.")
        except Exception as e:
            print(f"Error creating unique active routine index: {e}")
            conn.rollback()
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

def generate_schema_sql():
    """
    Generate only the schema and seed lookup/template data.
    Dynamic exercise inserts are handled in a second phase after the schema is applied.
    """
    schema_sql = """
    -- Drop existing objects in reverse order of dependency to ensure a clean slate.
    DROP TRIGGER IF EXISTS trigger_assign_routines_on_user_insert ON users;
    DROP FUNCTION IF EXISTS assign_default_routines_to_user();

    DROP TABLE IF EXISTS 
        user_routine_day_focus_areas, user_routine_day_exercises, user_routine_days, user_routines,
        routine_day_focus_areas, routine_days,
        weight_history, body_measurement_history, user_workout_days,
        workout_history, workout_logs, workout_session_exercises, workout_sessions,
        workout_plan_exercises, workout_plans, 
        user_goals, user_focus_areas, user_health_issues, user_equipment, 
        exercise_focus_areas, exercise_equipment, exercise_contraindications, exercise_fitness_levels,
        exercises, users, routines, goals, motivations, user_motivations, focus_areas, health_issues, equipment, equipment_types,
        token_blocklist, user_login_history, user_generated_exercises, user_custom_exercises CASCADE;

    DROP TYPE IF EXISTS 
        gender_enum, fitness_level_enum, activity_level_enum, day_of_week_enum, 
        user_status_enum, unit_preference_enum, exercise_type_enum, workout_status_enum;

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
    CREATE TYPE exercise_type_enum AS ENUM ('strength', 'cardio', 'flexibility', 'muscle_growth');

    -- ========= LOOKUP TABLES (Unchanged) =========
    CREATE TABLE goals (id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE, description TEXT);
    CREATE TABLE motivations (id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE, description TEXT);
    CREATE TABLE focus_areas (id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE, description TEXT);
    CREATE TABLE health_issues (id SERIAL PRIMARY KEY, name VARCHAR(100) NOT NULL UNIQUE, description TEXT);
    CREATE TABLE equipment_types (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100) NOT NULL UNIQUE
    );
    CREATE TABLE equipment (
        id SERIAL PRIMARY KEY,
        name VARCHAR(100) NOT NULL UNIQUE,
        description TEXT,
        equipment_type_id INTEGER REFERENCES equipment_types(id)
    );

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
        
        is_matrix BOOLEAN DEFAULT FALSE,
        randomness INTEGER CHECK (randomness IN (10,20,30,40,50,60,70,80,90,100)) DEFAULT 10,
        circute_training BOOLEAN DEFAULT FALSE,
        rapge_ranges BOOLEAN DEFAULT FALSE,
        duration INTEGER DEFAULT 30, -- in minutes
        rest_time INTEGER DEFAULT 30, -- in seconds
        objective VARCHAR(50) DEFAULT 'muscle',
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
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
        image_url VARCHAR(255),
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

    -- Add exercise_mode column to user_routine_days to support dual mode (focus areas OR direct exercises)
    ALTER TABLE user_routine_days 
    ADD COLUMN exercise_mode VARCHAR(20) DEFAULT 'focus_areas' 
    CHECK (exercise_mode IN ('focus_areas', 'direct_exercises'));

    -- Create table to store direct exercises for routine days
    CREATE TABLE user_routine_day_exercises (
        id SERIAL PRIMARY KEY,
        user_routine_day_id INTEGER NOT NULL REFERENCES user_routine_days(id) ON DELETE CASCADE,
        exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
        order_in_day INTEGER DEFAULT 1,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_routine_day_id, exercise_id)
    );

    -- Create indexes for better performance
    CREATE INDEX idx_user_routine_day_exercises_day_id ON user_routine_day_exercises(user_routine_day_id);
    CREATE INDEX idx_user_routine_day_exercises_exercise_id ON user_routine_day_exercises(exercise_id);

    -- Add trigger to update updated_at timestamp
    CREATE TRIGGER trigger_user_routine_day_exercises_updated_at
    BEFORE UPDATE ON user_routine_day_exercises
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

    -- ========= *** CORRECTED FUNCTION AND TRIGGER FOR AUTOMATIC ROUTINE ASSIGNMENT *** =========
    CREATE OR REPLACE FUNCTION assign_default_routines_to_user()
    RETURNS TRIGGER AS $$
    DECLARE
        routine_record RECORD;
        day_record RECORD;
        new_user_routine_id INT;
        new_user_routine_day_id INT;
        target_routine_id INT;
    BEGIN
        -- Step 1: Loop through each master routine and create a personal copy for the new user.
        FOR routine_record IN SELECT * FROM routines LOOP
            INSERT INTO user_routines (user_id, routine_id)
            VALUES (NEW.id, routine_record.id)
            RETURNING id INTO new_user_routine_id;

            FOR day_record IN SELECT * FROM routine_days WHERE routine_id = routine_record.id LOOP
                INSERT INTO user_routine_days (user_routine_id, day_number)
                VALUES (new_user_routine_id, day_record.day_number)
                RETURNING id INTO new_user_routine_day_id;

                INSERT INTO user_routine_day_focus_areas (user_routine_day_id, focus_area_id)
                SELECT new_user_routine_day_id, rdfa.focus_area_id
                FROM routine_day_focus_areas rdfa
                WHERE rdfa.routine_day_id = day_record.id;
            END LOOP;
        END LOOP;

        -- Step 2 (THE FIX): Intelligently activate a routine based on the user's preference.
        -- Find a routine template that has the same number of days as the user's `workouts_per_week`.
        SELECT r.id INTO target_routine_id
        FROM routines r
        JOIN (
            SELECT routine_id, COUNT(*) as day_count
            FROM routine_days
            GROUP BY routine_id
        ) AS routine_day_counts ON r.id = routine_day_counts.routine_id
        WHERE routine_day_counts.day_count = NEW.workouts_per_week
        ORDER BY r.id -- Make the selection deterministic if multiple routines match
        LIMIT 1;

        -- If a matching routine was found, update the user's copy to be active.
        IF FOUND THEN
            UPDATE user_routines
            SET is_active = TRUE
            WHERE user_id = NEW.id AND routine_id = target_routine_id;
        END IF;

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
    -- ========= WORKOUT SESSION MANAGEMENT TABLES =========
    CREATE TYPE workout_status_enum AS ENUM ('active', 'completed', 'cancelled');
    
    -- Workout sessions to track active workouts
    CREATE TABLE workout_sessions (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        status workout_status_enum NOT NULL DEFAULT 'active',
        started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP WITH TIME ZONE,
        total_duration_seconds INTEGER,
        notes TEXT,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    
    -- Workout session exercises (exercises included in this session)
    CREATE TABLE workout_session_exercises (
        id BIGSERIAL PRIMARY KEY,
        workout_session_id BIGINT NOT NULL REFERENCES workout_sessions(id) ON DELETE CASCADE,
        exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
        planned_sets INTEGER DEFAULT 3,
        planned_reps INTEGER DEFAULT 12,
        planned_weight_kg DECIMAL(6, 2) DEFAULT 0.00,
        order_in_workout INTEGER DEFAULT 1,
        is_completed BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    
    -- Enhanced workout logs for individual sets
    CREATE TABLE workout_logs (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        workout_session_id BIGINT REFERENCES workout_sessions(id) ON DELETE CASCADE,
        workout_session_exercise_id BIGINT REFERENCES workout_session_exercises(id) ON DELETE CASCADE,
        exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
        set_number INTEGER NOT NULL,
        weight_kg DECIMAL(6, 2) DEFAULT 0.00,
        reps_completed INTEGER NOT NULL,
        duration_seconds INTEGER,
        rest_time_seconds INTEGER,
        notes TEXT,
        logged_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        workout_plan_exercise_id BIGINT REFERENCES workout_plan_exercises(id) ON DELETE SET NULL
    );
    
    -- Workout history (completed workout summaries)
    CREATE TABLE workout_history (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        workout_session_id BIGINT NOT NULL REFERENCES workout_sessions(id) ON DELETE CASCADE,
        workout_date DATE NOT NULL,
        total_exercises INTEGER DEFAULT 0,
        total_sets INTEGER DEFAULT 0,
        total_duration_seconds INTEGER DEFAULT 0,
        calories_burned INTEGER DEFAULT 0,
        notes TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    
    -- Create indexes for better performance
    CREATE INDEX idx_workout_sessions_user_id ON workout_sessions(user_id);
    CREATE INDEX idx_workout_sessions_status ON workout_sessions(status);
    CREATE INDEX idx_workout_session_exercises_session_id ON workout_session_exercises(workout_session_id);
    CREATE INDEX idx_workout_logs_session_id ON workout_logs(workout_session_id);
    CREATE INDEX idx_workout_logs_user_id ON workout_logs(user_id);
    CREATE INDEX idx_workout_history_user_id ON workout_history(user_id);
    CREATE INDEX idx_workout_history_date ON workout_history(workout_date);
    
    -- Add triggers for updated_at timestamps
    CREATE TRIGGER trigger_workout_sessions_updated_at
    BEFORE UPDATE ON workout_sessions
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
    
    CREATE TRIGGER trigger_workout_session_exercises_updated_at
    BEFORE UPDATE ON workout_session_exercises
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
    
    -- ========= AUTHENTICATION & SECURITY TABLES =========
    CREATE TABLE IF NOT EXISTS token_blocklist (
        id SERIAL PRIMARY KEY, 
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, 
        access_token TEXT NOT NULL UNIQUE, 
        refresh_token TEXT NOT NULL, 
        exp_time TIMESTAMP WITH TIME ZONE NOT NULL, 
        revoked BOOLEAN NOT NULL DEFAULT FALSE, 
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP, 
        updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    
    CREATE TRIGGER trigger_set_updated_at_token_blocklist 
    BEFORE UPDATE ON token_blocklist 
    FOR EACH ROW 
    EXECUTE FUNCTION update_updated_at_column();
    
    CREATE TABLE IF NOT EXISTS user_login_history (
        id SERIAL PRIMARY KEY, 
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, 
        login_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP, 
        logout_time TIMESTAMP WITH TIME ZONE, 
        created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    
    -- ========= USER GENERATED EXERCISES TABLE =========
    CREATE TABLE user_generated_exercises (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
        weight_kg DECIMAL(6, 2) NOT NULL DEFAULT 0.00,
        reps INTEGER NOT NULL DEFAULT 12,
        sets INTEGER NOT NULL DEFAULT 3,
        one_rm_calculated DECIMAL(6, 2) NOT NULL DEFAULT 0.00,
        generated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, exercise_id)
    );
    
    CREATE INDEX idx_user_generated_exercises_user_id ON user_generated_exercises(user_id);
    CREATE INDEX idx_user_generated_exercises_exercise_id ON user_generated_exercises(exercise_id);
    
    CREATE TRIGGER trigger_user_generated_exercises_updated_at
    BEFORE UPDATE ON user_generated_exercises
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
    
    -- ========= USER CUSTOM EXERCISES TABLE (TEMPORARY STORAGE) =========
    CREATE TABLE user_custom_exercises (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
        weight_kg DECIMAL(6, 2) NOT NULL DEFAULT 0.00,
        reps INTEGER NOT NULL DEFAULT 12,
        sets INTEGER NOT NULL DEFAULT 3,
        one_rm_calculated DECIMAL(6, 2) NOT NULL DEFAULT 0.00,
        added_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, exercise_id)
    );
    
    CREATE INDEX idx_user_custom_exercises_user_id ON user_custom_exercises(user_id);
    CREATE INDEX idx_user_custom_exercises_exercise_id ON user_custom_exercises(exercise_id);
    
    CREATE TRIGGER trigger_user_custom_exercises_updated_at
    BEFORE UPDATE ON user_custom_exercises
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
    
    -- ========= EXERCISE EXCLUSION TABLES =========
    -- Table for permanently excluded exercises (exclude forever)
    CREATE TABLE user_excluded_exercises_forever (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
        excluded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        reason TEXT,
        UNIQUE(user_id, exercise_id)
    );
    
    -- Table for temporarily excluded exercises (just for today)
    CREATE TABLE user_excluded_exercises_today (
        id BIGSERIAL PRIMARY KEY,
        user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        exercise_id INTEGER NOT NULL REFERENCES exercises(id) ON DELETE CASCADE,
        excluded_date DATE NOT NULL DEFAULT CURRENT_DATE,
        excluded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        reason TEXT,
        UNIQUE(user_id, exercise_id, excluded_date)
    );
    
    -- Create indexes for better performance
    CREATE INDEX idx_user_excluded_exercises_forever_user_id ON user_excluded_exercises_forever(user_id);
    CREATE INDEX idx_user_excluded_exercises_forever_exercise_id ON user_excluded_exercises_forever(exercise_id);
    CREATE INDEX idx_user_excluded_exercises_today_user_id ON user_excluded_exercises_today(user_id);
    CREATE INDEX idx_user_excluded_exercises_today_exercise_id ON user_excluded_exercises_today(exercise_id);
    CREATE INDEX idx_user_excluded_exercises_today_date ON user_excluded_exercises_today(excluded_date);
    
    -- Function to automatically clean up old "today" exclusions (older than 7 days)
    CREATE OR REPLACE FUNCTION cleanup_old_today_exclusions()
    RETURNS void AS $$
    BEGIN
        DELETE FROM user_excluded_exercises_today 
        WHERE excluded_date < CURRENT_DATE - INTERVAL '7 days';
    END;
    $$ LANGUAGE plpgsql;
    
    -- ========= INSERT INITIAL LOOKUP AND TEMPLATE DATA =========
    -- Basic Lookups
    INSERT INTO goals (name) VALUES ('Improve Fitness'), ('Build Muscle'), ('Burn Fat'), ('Increase Endurance'), ('Boost Mental Strength'), ('Improve Balance');
    INSERT INTO motivations (name) VALUES ('Health and Wellness'), ('Weight Management'), ('Achievement'), ('Become Sexually Attractive'), ('Social Support');
    INSERT INTO focus_areas (name) VALUES ('Chest'), ('Back'), ('Arms'), ('Shoulders'), ('Abs'), ('Legs'), ('Glutes'), ('Full Body'), ('Hips'), ('Thighs'), ('Calves'), ('Forearms'), ('Waist'), ('Biceps'), ('Triceps');
    INSERT INTO health_issues (name, description) VALUES ('None', 'No health issues.'), ('Post-COVID Recovery', 'Avoid high-intensity exercises.'), ('Cannot Jump', 'Avoid high-impact exercises.'), ('Back or Hernia', 'Avoid heavy spinal loading.'), ('Knee Pain', 'Avoid deep squats and lunges.'), ('No medical issues', 'No medical issues.'), ('Back pain', 'Avoid heavy spinal loading and exercises that aggravate pain.'), ('Knee or joint pain', 'Avoid deep squats, lunges, and high-impact moves.'), ('Heart condition', 'Avoid high-intensity workouts; consult a physician.'), ('Diabetes', 'Monitor blood sugar; avoid extreme intensity.'), ('Asthma / breathing issues', 'Avoid high-intensity cardio; monitor breathing.') ON CONFLICT (name) DO NOTHING;
    
    -- Insert equipment types and equipment hierarchy (Your existing code is fine)
    INSERT INTO equipment_types (name) VALUES ('Bodyweight'), ('Household Items'), ('Free Weights'), ('Benches'), ('Racks'), ('Bars'), ('Bands'), ('Cable machines'), ('Cable Attachments'), ('Weight Machines'), ('Other');
    INSERT INTO equipment (name, equipment_type_id) VALUES
        ('Bodyweight', (SELECT id FROM equipment_types WHERE name = 'Bodyweight')),
        ('Bodyweight Only', (SELECT id FROM equipment_types WHERE name = 'Bodyweight')),
        ('Chair', (SELECT id FROM equipment_types WHERE name = 'Household Items')),
        
        -- Benches
        ('Flat Bench', (SELECT id FROM equipment_types WHERE name = 'Benches')),
        ('Box', (SELECT id FROM equipment_types WHERE name = 'Household Items')),
        ('Decline Bench', (SELECT id FROM equipment_types WHERE name = 'Benches')),
        ('Steps', (SELECT id FROM equipment_types WHERE name = 'Household Items')),
        ('Stability Ball', (SELECT id FROM equipment_types WHERE name = 'Household Items')),
        ('Back Extension Bench', (SELECT id FROM equipment_types WHERE name = 'Benches')),
        ('Bench', (SELECT id FROM equipment_types WHERE name = 'Benches')),
        
        -- Cable Attachments
        ('Handles', (SELECT id FROM equipment_types WHERE name = 'Cable Attachments')),
        ('Handle', (SELECT id FROM equipment_types WHERE name = 'Cable Attachments')),
        ('Rope', (SELECT id FROM equipment_types WHERE name = 'Cable Attachments')),
        
        -- Machines
        ('Seated Row Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Leg Curl Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Leg Extension Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Hack Squat Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Sled', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Push Up Bar', (SELECT id FROM equipment_types WHERE name = 'Other')),
        ('Squat Rack', (SELECT id FROM equipment_types WHERE name = 'Racks')),
        ('Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        
        -- Other
        ('Sliders', (SELECT id FROM equipment_types WHERE name = 'Other')),
        ('Large Textbook', (SELECT id FROM equipment_types WHERE name = 'Household Items')),
        ('Stick', (SELECT id FROM equipment_types WHERE name = 'Household Items')),
        ('Towel', (SELECT id FROM equipment_types WHERE name = 'Household Items')),
        ('Barbell', (SELECT id FROM equipment_types WHERE name = 'Free Weights')),
        ('Dumbbells', (SELECT id FROM equipment_types WHERE name = 'Free Weights')),
        ('Kettlebells', (SELECT id FROM equipment_types WHERE name = 'Free Weights')),
        ('EZ Bar', (SELECT id FROM equipment_types WHERE name = 'Free Weights')),
        ('Hammer Curl Bar', (SELECT id FROM equipment_types WHERE name = 'Free Weights')),
        ('Weight Plate', (SELECT id FROM equipment_types WHERE name = 'Free Weights')),
        ('Decline Bench With Rack', (SELECT id FROM equipment_types WHERE name = 'Benches')),
        ('Decline Bench Without Rack', (SELECT id FROM equipment_types WHERE name = 'Benches')),
        ('Flat Bench With Rack', (SELECT id FROM equipment_types WHERE name = 'Benches')),
        ('Flat Bench Without Rack', (SELECT id FROM equipment_types WHERE name = 'Benches')),
        ('Incline (Adjustable) Bench Without Rack', (SELECT id FROM equipment_types WHERE name = 'Benches')),
        ('Incline Bench With Rack', (SELECT id FROM equipment_types WHERE name = 'Benches')),
        ('Incline Bench', (SELECT id FROM equipment_types WHERE name = 'Benches')),
        ('Preacher Curl Bench', (SELECT id FROM equipment_types WHERE name = 'Benches')),
        ('Vertical Bench', (SELECT id FROM equipment_types WHERE name = 'Benches')),
        ('Dumbbell Rack', (SELECT id FROM equipment_types WHERE name = 'Racks')),
        ('Squat Rack Or Power Rack', (SELECT id FROM equipment_types WHERE name = 'Racks')),
        ('Dip Bars', (SELECT id FROM equipment_types WHERE name = 'Bars')),
        ('Padded Parallel Bars', (SELECT id FROM equipment_types WHERE name = 'Bars')),
        ('Pull Up Bar', (SELECT id FROM equipment_types WHERE name = 'Bars')),
        ('Handle Band', (SELECT id FROM equipment_types WHERE name = 'Bands')),
        ('Mini Loop Band', (SELECT id FROM equipment_types WHERE name = 'Bands')),
        ('Close Pulley Towers', (SELECT id FROM equipment_types WHERE name = 'Cable machines')),
        ('Far Pulley Towers', (SELECT id FROM equipment_types WHERE name = 'Cable machines')),
        ('Lat Pulldown', (SELECT id FROM equipment_types WHERE name = 'Cable machines')),
        ('Seated Row', (SELECT id FROM equipment_types WHERE name = 'Cable machines')),
        ('Single Pulley Tower', (SELECT id FROM equipment_types WHERE name = 'Cable machines')),
        ('Ankle Cuff', (SELECT id FROM equipment_types WHERE name = 'Cable Attachments')),
        ('Curl Bar', (SELECT id FROM equipment_types WHERE name = 'Cable Attachments')),
        ('Lat Bar', (SELECT id FROM equipment_types WHERE name = 'Cable Attachments')),
        ('Push Down Bar', (SELECT id FROM equipment_types WHERE name = 'Cable Attachments')),
        ('Rope Attachment', (SELECT id FROM equipment_types WHERE name = 'Cable Attachments')),
        ('Row Handle', (SELECT id FROM equipment_types WHERE name = 'Cable Attachments')),
        ('Single Grip Handles', (SELECT id FROM equipment_types WHERE name = 'Cable Attachments')),
        ('Straight Bar', (SELECT id FROM equipment_types WHERE name = 'Cable Attachments')),
        ('V-Bar', (SELECT id FROM equipment_types WHERE name = 'Cable Attachments')),
        ('Assisted Weight Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Back Extension Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Calf Raise Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Chest Press Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Fly Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Glute Kickback Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('High Row Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Hip Abduction Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Hip Adduction Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Incline Chest Press Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Lat Pulldown Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Lateral Raise Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Lying Crunch Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Preacher Curl Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Row Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Seated Crunch Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Shoulder Press Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Smith Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('T - Bar', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Tricep Extension Machine', (SELECT id FROM equipment_types WHERE name = 'Weight Machines')),
        ('Ab Wheel', (SELECT id FROM equipment_types WHERE name = 'Other')),
        ('Battle Ropes', (SELECT id FROM equipment_types WHERE name = 'Other')),
        ('Landmine Holder', (SELECT id FROM equipment_types WHERE name = 'Other')),
        ('Slider', (SELECT id FROM equipment_types WHERE name = 'Other')),
        ('Stability (swiss) Ball', (SELECT id FROM equipment_types WHERE name = 'Other'));

    -- Seeding Routines and their default day/focus area structures (Your existing code is fine)
    WITH routine AS (INSERT INTO routines (name) VALUES ('3 Day Classic') RETURNING id),
         day1 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 1 FROM routine RETURNING id),
         day2 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 2 FROM routine RETURNING id),
         day3 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 3 FROM routine RETURNING id)
    INSERT INTO routine_day_focus_areas (routine_day_id, focus_area_id) VALUES
        ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Chest')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Triceps')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Abs')),
        ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Back')), ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Biceps')), ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Abs')),
        ((SELECT id FROM day3), (SELECT id FROM focus_areas WHERE name = 'Legs')), ((SELECT id FROM day3), (SELECT id FROM focus_areas WHERE name = 'Shoulders')), ((SELECT id FROM day3), (SELECT id FROM focus_areas WHERE name = 'Abs'));
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
    WITH routine AS (INSERT INTO routines (name) VALUES ('Push, Pull, Legs') RETURNING id),
         day1 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 1 FROM routine RETURNING id),
         day2 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 2 FROM routine RETURNING id),
         day3 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 3 FROM routine RETURNING id)
    INSERT INTO routine_day_focus_areas (routine_day_id, focus_area_id) VALUES
        ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Chest')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Shoulders')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Triceps')),
        ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Back')), ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Biceps')),
        ((SELECT id FROM day3), (SELECT id FROM focus_areas WHERE name = 'Legs')), ((SELECT id FROM day3), (SELECT id FROM focus_areas WHERE name = 'Abs'));
    WITH routine AS (INSERT INTO routines (name) VALUES ('Push, Pull') RETURNING id),
         day1 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 1 FROM routine RETURNING id),
         day2 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 2 FROM routine RETURNING id)
    INSERT INTO routine_day_focus_areas (routine_day_id, focus_area_id) VALUES
        ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Chest')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Shoulders')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Triceps')),
        ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Back')), ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Biceps'));
    WITH routine AS (INSERT INTO routines (name) VALUES ('Upper, Lower') RETURNING id),
         day1 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 1 FROM routine RETURNING id),
         day2 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 2 FROM routine RETURNING id)
    INSERT INTO routine_day_focus_areas (routine_day_id, focus_area_id) VALUES
        ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Chest')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Back')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Shoulders')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Biceps')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Triceps')),
        ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Legs')), ((SELECT id FROM day2), (SELECT id FROM focus_areas WHERE name = 'Abs'));
    WITH routine AS (INSERT INTO routines (name) VALUES ('Full Body') RETURNING id),
         day1 AS (INSERT INTO routine_days (routine_id, day_number) SELECT id, 1 FROM routine RETURNING id)
    INSERT INTO routine_day_focus_areas (routine_day_id, focus_area_id) VALUES
        ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Legs')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Back')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Chest')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Biceps')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Shoulders')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Triceps')), ((SELECT id FROM day1), (SELECT id FROM focus_areas WHERE name = 'Abs'));
    INSERT INTO routines (name) VALUES ('Adaptive'), ('Custom');
    """
    return schema_sql


def generate_dynamic_exercises_sql(valid_equipment_names: set):
    """
    Generate only the dynamic exercise INSERT statements from excercise1.json.
    This runs after schema is applied so FK lookups (focus_areas, equipment, etc.) exist.
    """
    import json
    insert_commands = ["\n-- ========= DYNAMICALLY INSERTED EXERCISE DATA =========\n"]
    def sql_escape(text):
        if text is None:
            return ""
        return text.replace("'", "''")

    json_path = os.path.join(os.path.dirname(__file__), 'excercise1.json')
    with open(json_path, 'r', encoding='utf-8') as f:
        exercise_data = json.load(f)

    processed_exercise_names = set()

    for ex in exercise_data:
        ex_name = sql_escape(ex['name'])
        if ex_name in processed_exercise_names:
            print(f"--- SKIPPING DUPLICATE EXERCISE: {ex['name']} ---")
            continue
        processed_exercise_names.add(ex_name)

        ex_desc = sql_escape(ex.get('desc', ''))

        type_mapping = {"Strength Training": "strength", "Muscle Growth": "muscle_growth", "Calorie Burning": "cardio"}
        json_types = ex.get('type', [])
        ex_type = 'strength'
        for t in json_types:
            if t in type_mapping:
                ex_type = type_mapping[t]
                break

        ex_impact = ex.get('is_high_impact', False)
        primary_focus_area_name = sql_escape(ex['focus_areas'][0]) if ex.get('focus_areas') else 'Full Body'

        # Handle combined equipment names like "Barbell/Dumbbells"
        raw_equipment = ex.get('equipment', [])
        processed_equipment = set()
        for item in raw_equipment:
            parts = [p.strip() for p in item.split('/')]
            for part in parts:
                if part in valid_equipment_names:
                    processed_equipment.add(part)

        ex_video_url = sql_escape(ex.get('video_url', ''))
        ex_image_url = sql_escape(ex.get('image_url', ''))

        with_clauses = [
            f"new_exercise AS (INSERT INTO exercises (name, description, video_url, image_url, exercise_type, is_high_impact, primary_focus_area_id) VALUES ('{ex_name}', '{ex_desc}', NULLIF('{ex_video_url}', ''), NULLIF('{ex_image_url}', ''), '{ex_type}', {ex_impact}, (SELECT id FROM focus_areas WHERE name = '{primary_focus_area_name}')) RETURNING id)"
        ]

        if ex.get('difficulty_levels'):
            difficulty_selects = [f"SELECT id, '{level.lower()}'::fitness_level_enum FROM new_exercise" for level in ex['difficulty_levels']]
            with_clauses.append(f"ins_difficulty AS (INSERT INTO exercise_fitness_levels (exercise_id, fitness_level) {' UNION ALL '.join(difficulty_selects)})")

        if ex.get('focus_areas'):
            focus_area_selects = []
            for i, area in enumerate(ex['focus_areas']):
                is_primary = 'TRUE' if i == 0 else 'FALSE'
                focus_area_selects.append(f"SELECT id, (SELECT id FROM focus_areas WHERE name = '{sql_escape(area)}'), {is_primary} FROM new_exercise")
            with_clauses.append(f"ins_focus_areas AS (INSERT INTO exercise_focus_areas (exercise_id, focus_area_id, is_primary) {' UNION ALL '.join(focus_area_selects)})")

        if processed_equipment:
            equipment_selects = []
            for equip_name in processed_equipment:
                equipment_selects.append(f"SELECT (SELECT id FROM new_exercise), (SELECT id FROM equipment WHERE name = '{equip_name}')")
            if equipment_selects:
                with_clauses.append(f"ins_equipment AS (INSERT INTO exercise_equipment (exercise_id, equipment_id) {' UNION ALL '.join(equipment_selects)})")

        if ex.get('contraindications'):
            contra_selects = [f"SELECT id, (SELECT id FROM health_issues WHERE name = '{sql_escape(issue)}') FROM new_exercise" for issue in ex['contraindications']]
            with_clauses.append(f"ins_contra AS (INSERT INTO exercise_contraindications (exercise_id, health_issue_id) {' UNION ALL '.join(contra_selects)})")

        full_statement = "WITH " + ",\n".join(with_clauses) + "\nSELECT 1;"
        insert_commands.append(full_statement)

    return "\n".join(insert_commands)



def main():
    if not create_database():
        print("Failed to create or verify database existence. Exiting...")
        return

    # Phase 1: Apply schema and seed lookup/template data
    print("Generating schema SQL...")
    schema_sql = generate_schema_sql()

    temp_schema_file = "temp_schema.sql"
    with open(temp_schema_file, "w", encoding='utf-8') as f:
        f.write(schema_sql)

    try:
        if not execute_sql_file(temp_schema_file, DB_NAME):
            print("\nSchema setup failed. Please check the errors above.")
            return
    finally:
        if os.path.exists(temp_schema_file):
            os.remove(temp_schema_file)

    # Phase 2: Generate dynamic exercise data after schema is applied
    print("Generating dynamic exercise data SQL...")
    # Fetch valid equipment names from the DB now that the table exists
    try:
        conn = psycopg2.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT)
        cur = conn.cursor()
        cur.execute("SELECT name FROM equipment;")
        valid_equipment_names = {row[0] for row in cur.fetchall()}
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass

    dynamic_sql = generate_dynamic_exercises_sql(valid_equipment_names)

    temp_data_file = "temp_exercises.sql"
    with open(temp_data_file, "w", encoding='utf-8') as f:
        f.write(dynamic_sql)

    try:
        if execute_sql_file(temp_data_file, DB_NAME):
            print("\nDatabase setup complete! New routine system is in place.")
        else:
            print("\nDynamic exercise data load failed. Please check the errors above.")
    finally:
        if os.path.exists(temp_data_file):
            os.remove(temp_data_file)

if __name__ == "__main__":
    main()