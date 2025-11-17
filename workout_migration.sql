-- Workout Session Management Migration
-- This script adds the new workout session tables to the existing database

-- Create workout status enum if it doesn't exist
DO $$ BEGIN
    CREATE TYPE workout_status_enum AS ENUM ('active', 'completed', 'cancelled');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Drop existing workout session tables if they exist (for clean migration)
DROP TABLE IF EXISTS workout_history CASCADE;
DROP TABLE IF EXISTS workout_logs CASCADE;
DROP TABLE IF EXISTS workout_session_exercises CASCADE;
DROP TABLE IF EXISTS workout_sessions CASCADE;

-- Create workout sessions table
CREATE TABLE workout_sessions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_name VARCHAR(255) DEFAULT 'Workout Session',
    status workout_status_enum NOT NULL DEFAULT 'active',
    started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    total_duration_seconds INTEGER,
    notes TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create workout session exercises table
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

-- Create enhanced workout logs table
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

-- Create workout history table
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

-- Success message
SELECT 'Workout session management tables created successfully!' as result;
