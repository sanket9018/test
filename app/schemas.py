from pydantic import BaseModel, EmailStr, Field, validator, model_validator
from typing import List, Optional, Dict, Any, Literal
from enum import Enum
from datetime import datetime

# Enums for validation
class GenderEnum(str, Enum):
    male = "male"
    female = "female"
    other = "other"
    prefer_not_to_say = "prefer_not_to_say"

class FitnessLevelEnum(str, Enum):
    beginner = "beginner"
    intermediate = "intermediate"
    advanced = "advanced"

class ActivityLevelEnum(str, Enum):
    sedentary = "sedentary"
    lightly_active = "lightly_active"
    moderately_active = "moderately_active"
    very_active = "very_active"
    extra_active = "extra_active"

class DayOfWeekEnum(str, Enum):
    monday = "monday"
    tuesday = "tuesday"
    wednesday = "wednesday"
    thursday = "thursday"
    friday = "friday"
    saturday = "saturday"
    sunday = "sunday"

# Base schemas
class BaseSchema(BaseModel):
    class Config:
        from_attributes = True
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }

# Request schemas
class UserBase(BaseSchema):
    name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=100)

class UserCreate(UserBase):
    gender: Optional[GenderEnum] = None
    age: Optional[int] = Field(None, gt=0, lt=120)
    height_cm: Optional[int] = Field(None, gt=0, le=300)
    current_weight_kg: Optional[float] = Field(None, gt=0, le=1000)
    target_weight_kg: Optional[float] = Field(None, gt=0, le=1000)
    fitness_level: FitnessLevelEnum = FitnessLevelEnum.beginner
    activity_level: ActivityLevelEnum = ActivityLevelEnum.moderately_active
    workouts_per_week: int = Field(3, ge=1, le=7)
    motivation_id: Optional[List[int]] = None

class UserOnboardingCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=8)
    age: int = Field(..., gt=0, lt=120)
    gender: str
    height_cm: int
    current_weight_kg: float
    target_weight_kg: float
    
    fitness_level: str
    activity_level: str
    workouts_per_week: int = Field(..., ge=1, le=7)
    
    # This ID specifies which of the 7 default routines should be marked as active.
    routine_id: int
    
    # Objective can be one of: muscle (maps to muscle_growth), strength, cardio
    objective: str = Field(...) 
    
    motivations: List[str]
    goals: List[str]
    focus_area_ids: List[int]
    health_issue_ids: List[int]
    equipment_ids: List[int]
    workout_days: List[str]

    @validator('activity_level', pre=True)
    def normalize_activity_level(cls, v):
        if isinstance(v, str):
            mapping = {
                "light active": "lightly_active",
                "moderately active": "moderately_active",
                "moderate active": "moderately_active",
                "very active": "very_active",
                "sedentary": "sedentary",
                "extra active": "extra_active"
            }
            # Normalize: strip and lowercase, then check map
            norm = v.strip().lower()
            if norm in mapping:
                return mapping[norm]
            # Also try replacing space with underscore directly
            return norm.replace(" ", "_")
        return v

    @validator('fitness_level', pre=True)
    def normalize_fitness_level(cls, v):
        if isinstance(v, str):
            norm = v.strip().lower()
            # Ensure it matches one of the allowed values or simple mapping if needed
            # "intermediate" is fine, but just in case
            return norm.replace(" ", "_")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Jane Doe",
                "email": "jane.doe@example.com",
                "password": "strongpassword456",
                "age": 28,
                "gender": "female",
                "height_cm": 165,
                "current_weight_kg": 65.0,
                "target_weight_kg": 60.0,
                "fitness_level": "beginner",
                "activity_level": "lightly_active",
                "workouts_per_week": 3,
                "routine_id": 1,  # User wants to start with "3 Day Classic"
                "objective": "muscle",
                "objective": "muscle",
                "motivations": ["Health and Wellness"],
                "goals": ["Build Muscle"],
                "focus_area_ids": [1, 2, 5],
                "health_issue_ids": [1],
                "equipment_ids": [5],
                "workout_days": ["monday", "wednesday", "friday"]
            }
        }
        
class FocusAreaResponse(BaseModel):
    """Represents a single focus area."""
    id: int
    name: str

class DirectExerciseInfo(BaseModel):
    """Represents a direct exercise with details."""
    id: int
    name: str
    description: Optional[str] = None
    video_url: Optional[str] = None
    order_in_day: int

class UserRoutineDayResponse(BaseModel):
    """Represents a single, customizable day within a user's routine."""
    id: int
    day_number: int
    exercise_mode: str = "focus_areas"  # "focus_areas" or "direct_exercises"
    focus_areas: List[FocusAreaResponse] = []
    direct_exercises: List[DirectExerciseInfo] = []

class UserRoutineResponse(BaseModel):
    """Represents a user's personal, customizable copy of a routine."""
    id: int
    name: str  # e.g., "3 Day Classic"
    is_active: bool
    days: List[UserRoutineDayResponse] = []

# --- The Main, Updated Response Model ---

class UserDetailResponse(BaseModel):
    """The new, comprehensive response model for the /user/me endpoint."""
    id: int
    name: str
    email: EmailStr
    gender: str
    age: int
    height_cm: float
    current_weight_kg: float
    target_weight_kg: float
    fitness_level: str
    activity_level: str
    workouts_per_week: int
    workout_days: List[DayOfWeekEnum]
    days: List[str]
    
    # Simple linked data
    motivations: Optional[List[str]] = None
    goals: Optional[List[str]] = None
    equipment: List[str]
    health_issues: List[str]
    
    # The new, nested routine data
    routines: List[UserRoutineResponse]
    # New advanced user profile fields
    profile_image_url: Optional[str] = None
    reminder: bool = True
    vibration_alert: bool = True
    is_matrix: bool
    randomness: int
    circute_training: bool
    rapge_ranges: bool
    duration: int
    rest_time: int
    objective: str
    
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        

class UserUpdate(BaseSchema):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    email: Optional[EmailStr] = None
    gender: Optional[GenderEnum] = None
    age: Optional[int] = Field(None, gt=0, lt=120)
    height_cm: Optional[int] = Field(None, gt=0, le=300)
    current_weight_kg: Optional[float] = Field(None, gt=0, le=1000)
    target_weight_kg: Optional[float] = Field(None, gt=0, le=1000)
    fitness_level: Optional[FitnessLevelEnum] = None
    activity_level: Optional[ActivityLevelEnum] = None
    workouts_per_week: Optional[int] = Field(None, ge=1, le=7)
    motivation_ids: Optional[List[int]] = None

class UserProfileUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    gender: Optional[GenderEnum] = None
    age: Optional[int] = Field(None, gt=0, lt=120)
    height_cm: Optional[float] = None
    current_weight_kg: Optional[float] = None
    target_weight_kg: Optional[float] = None
    fitness_level: Optional[FitnessLevelEnum] = None
    activity_level: Optional[ActivityLevelEnum] = None
    workouts_per_week: Optional[int] = Field(None, ge=1, le=7)
    motivations: Optional[List[str]] = None
    goals: Optional[List[str]] = None
    days: Optional[List[str]] = None
    reminder: Optional[bool] = None
    vibration_alert: Optional[bool] = None
    equipment_ids: Optional[List[int]] = None
    health_issue_ids: Optional[List[int]] = None
    is_matrix: Optional[bool] = None
    randomness: Optional[int] = None
    circute_training: Optional[bool] = None
    rapge_ranges: Optional[bool] = None
    duration: Optional[int] = None
    rest_time: Optional[int] = None
    objective: Optional[str] = Field(None)
    profile_image_base64: Optional[str] = Field(
        None,
        description="Base64-encoded image data for the user's profile picture",
    )

class WorkoutGenerationRequest(BaseSchema):
    workout_days: List[DayOfWeekEnum] = Field(..., min_items=1, max_items=7)
    focus_areas: Optional[List[int]] = None
    equipment_ids: Optional[List[int]] = None
    exclude_high_impact: bool = False

# Response schemas
class User(UserBase):
    id: int
    gender: Optional[GenderEnum] = None
    age: Optional[int] = None
    height_cm: Optional[int] = None
    current_weight_kg: Optional[float] = None
    target_weight_kg: Optional[float] = None
    fitness_level: FitnessLevelEnum = FitnessLevelEnum.beginner
    activity_level: ActivityLevelEnum = ActivityLevelEnum.moderately_active
    workouts_per_week: int = 3
    motivation_ids: Optional[List[int]] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class Goal(BaseSchema):
    id: int
    name: str
    description: Optional[str] = None

class Motivation(BaseSchema):
    id: int
    name: str
    description: Optional[str] = None

class FocusArea(BaseSchema):
    id: int
    name: str
    description: Optional[str] = None

class HealthIssue(BaseSchema):
    id: int
    name: str
    description: Optional[str] = None

class Equipment(BaseSchema):
    id: int
    name: str
    description: Optional[str] = None

class Exercise(BaseSchema):
    id: int
    name: str
    description: Optional[str] = None
    video_url: Optional[str] = None
    primary_focus_area_id: Optional[int] = None
    difficulty: FitnessLevelEnum = FitnessLevelEnum.beginner
    is_high_impact: bool = False

class WorkoutExercise(BaseSchema):
    exercise_id: int
    sets_recommended: int = 3
    reps_recommended: str = "8-12"
    rest_period_seconds: int = 60
    order_in_workout: int = 0

class WorkoutDayPlan(BaseSchema):
    day: DayOfWeekEnum
    exercises: List[WorkoutExercise]

class WorkoutPlanResponse(BaseSchema):
    id: int
    plan_name: str
    user_id: int
    full_plan: List[WorkoutDayPlan]
    created_at: datetime

# Token schemas for authentication (if needed)
class Token(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class TokenData(BaseModel):
    email: Optional[str] = None

class UserLogin(BaseModel):
    email: str
    password: str

class UserLoginHistory(BaseModel):
    user_id: int

class ListItem(BaseModel):
    id: int
    name: str

    class Config:
        from_attributes = True

class ExerciseResponse(BaseModel):
    """Defines the structure of a single exercise in the response."""
    id: int
    name: str
    description: Optional[str] = None
    video_url: Optional[str] = Field(None, alias="videoUrl")
    image_url: Optional[str] = Field(None, alias="imageUrl")
    primary_focus_area: str

from pydantic import BaseModel

class UserRoutineUpdate(BaseModel):
    routine_id: int

class UserRoutineInfo(BaseModel):
    """
    Provides concise information about a single routine available to a user.
    """
    routine_id: int
    name: str
    is_active: bool

# You might also create a specific response model for clarity
class UserRoutinesListResponse(BaseModel):
    routines: List[UserRoutineInfo]
    

class UserActiveDayUpdate(BaseModel):
    """
    Schema for updating the user-selected active day for their current routine.
    """
    day_number: int


class FocusAreaInfo(BaseModel):
    id: int
    name: str


# Define a Pydantic model for the response of our new status endpoint
class WorkoutDayStatusResponse(BaseModel):
    routine_name: str
    today_day_number: int
    total_routine_days: int
    exercise_mode: str = "focus_areas"
    focus_areas_for_today: List[str] = []
    direct_exercises_for_today: List[DirectExerciseInfo] = []

# Define the structure for a single day in the active routine
class RoutineDayDetail(BaseModel):
    day_number: int
    is_current_day: bool  # True if this is the currently calculated workout day
    exercise_mode: str = "focus_areas"  # "focus_areas" or "direct_exercises"
    focus_areas: List[FocusAreaInfo] = []
    direct_exercises: List[DirectExerciseInfo] = []

# Define the final response model for the new endpoint
class ActiveRoutineDaysResponse(BaseModel):
    routine_id: int
    routine_name: str
    days: List[RoutineDayDetail]


class FocusAreaInfo(BaseModel):
    id: int
    name: str

class DayFocusAreaRequest(BaseModel):
    """
    Schema for the request body when adding a focus area to a routine day.
    """
    focus_area_id: int = Field(..., gt=0, description="The ID of the focus area to add.")

# We can also add a response model for clarity when a new day is created
class UserRoutineDayResponse(BaseModel):
    id: int
    user_routine_id: int
    day_number: int
    focus_areas: List[FocusAreaInfo] # Re-using the schema from the previous step

# Equipment response models
class EquipmentItem(BaseModel):
    """Represents a single equipment item."""
    id: int
    name: str
    description: Optional[str] = None

class EquipmentTypeResponse(BaseModel):
    """Represents an equipment type with its associated equipment."""
    equipment_type_id: int
    equipment_type_name: str
    equipment_list: List[EquipmentItem]

class EquipmentListResponse(BaseModel):
    """Response model for the equipment list endpoint."""
    equipment_types: List[EquipmentTypeResponse]

# Exercise response models
class ExerciseFocusArea(BaseModel):
    """Represents a focus area for an exercise."""
    id: int
    name: str

class ExerciseItem(BaseModel):
    """Represents a single exercise item."""
    id: int
    name: str
    description: Optional[str] = None
    video_url: Optional[str] = None
    image_url: Optional[str] = None
    focus_areas: List[ExerciseFocusArea] = []

class ExercisesListResponse(BaseModel):
    """Response model for the exercises list endpoint."""
    exercises: List[ExerciseItem]

class RoutineDayReorderRequest(BaseModel):
    """Schema for reordering routine days via drag and drop."""
    source_day_number: int = Field(..., ge=1, le=7, description="Day number being dragged")
    target_position: int = Field(..., ge=1, le=7, description="Position where the day is being dropped")

class RoutineDayReorderResponse(BaseModel):
    """Response model for successful day reorder operation."""
    message: str
    source_day_number: int
    target_position: int
    affected_days: List[int]  # List of day numbers that were affected by the reorder

class UserGeneratedExerciseResponse(BaseModel):
    """Response model for user's generated exercises with calculated values."""
    id: int
    exercise_id: int
    name: str
    description: Optional[str] = None
    video_url: Optional[str] = None
    primary_focus_area: Optional[str] = None
    weight_kg: float
    reps: int
    sets: int
    one_rm_calculated: float
    generated_at: datetime
    updated_at: datetime

class UserGeneratedExercisesListResponse(BaseModel):
    """Response model for the list of user's generated exercises."""
    exercises: List[UserGeneratedExerciseResponse]

class UpdateUserGeneratedExerciseRequest(BaseModel):
    """Request model for updating user generated exercise values."""
    weight_kg: Optional[float] = Field(None, ge=0, le=1000, description="Weight in kg")
    reps: Optional[int] = Field(None, ge=1, le=100, description="Number of repetitions")
    sets: Optional[int] = Field(None, ge=1, le=20, description="Number of sets")

class UpdateUserGeneratedExerciseResponse(BaseModel):
    """Response model for updated user generated exercise."""
    id: int
    exercise_id: int
    name: str
    weight_kg: float
    reps: int
    sets: int
    updated_at: datetime
    message: str

# Custom Exercise Schemas
class AddCustomExerciseRequest(BaseModel):
    """Request model for adding custom exercise(s) to temporary storage."""
    exercise_id: Optional[int] = Field(None, gt=0, description="ID of the exercise to add as custom (for single exercise)")
    exercise_ids: Optional[List[int]] = Field(None, min_items=1, description="List of exercise IDs to add as custom (for multiple exercises)")
    
    @validator('exercise_ids')
    def validate_exercise_ids(cls, v):
        if v is not None:
            for exercise_id in v:
                if exercise_id <= 0:
                    raise ValueError(f"All exercise IDs must be positive integers, got: {exercise_id}")
        return v
    
    @model_validator(mode='before')
    def validate_either_single_or_multiple(cls, values):
        exercise_id = values.get('exercise_id')
        exercise_ids = values.get('exercise_ids')
        
        if exercise_id is None and exercise_ids is None:
            raise ValueError('Either exercise_id or exercise_ids must be provided')
        
        if exercise_id is not None and exercise_ids is not None:
            raise ValueError('Provide either exercise_id or exercise_ids, not both')
        
        return values

class CustomExerciseResponse(BaseModel):
    """Response model for custom exercise with calculated values."""
    id: int
    exercise_id: int
    name: str
    description: Optional[str] = None
    video_url: Optional[str] = None
    primary_focus_area: Optional[str] = None
    weight_kg: float
    reps: int
    sets: int
    one_rm_calculated: float
    added_at: datetime
    is_custom: bool = True  # Flag to identify custom exercises in combined responses

class AddCustomExerciseResponse(BaseModel):
    """Response model for successfully added custom exercise(s)."""
    message: str
    exercise: Optional[CustomExerciseResponse] = None  # For single exercise
    exercises: Optional[List[CustomExerciseResponse]] = None  # For multiple exercises
    total_added: Optional[int] = None  # For multiple exercises
    failed_exercises: Optional[List[int]] = None  # For multiple exercises - IDs that failed

class RoutineDayExerciseResponse(BaseModel):
    """Response model for exercises from routine days."""
    exercise_id: int
    name: str
    description: Optional[str] = None
    video_url: Optional[str] = None
    primary_focus_area: Optional[str] = None
    order_in_day: int
    day_number: int
    routine_name: str

class CombinedExercisesResponse(BaseModel):
    """Response model combining generated exercises, custom exercises, and routine day exercises."""
    generated_exercises: List[UserGeneratedExerciseResponse]
    custom_exercises: List[CustomExerciseResponse]
    routine_day_exercises: List[RoutineDayExerciseResponse]
    total_count: int

# Alternative Exercises Schemas
class AlternativeExercisesRequest(BaseModel):
    """Request model for finding alternative exercises based on an exercise ID."""
    exercise_id: int = Field(..., gt=0, description="ID of the exercise to find alternatives for")

class AlternativeExerciseResponse(BaseModel):
    """Response model for a single alternative exercise."""
    id: int
    name: str
    description: Optional[str] = None
    video_url: Optional[str] = None
    primary_focus_area: Optional[str] = None
    shared_focus_areas: List[str] = []  # Focus areas shared with the original exercise
    similarity_score: float = Field(..., ge=0, le=1, description="Similarity score (0-1) with original exercise")

class AlternativeExercisesResponse(BaseModel):
    """Response model for alternative exercises list."""
    original_exercise: Dict[str, Any]
    alternatives: List[AlternativeExerciseResponse]
    total_count: int

# Workout Session Management Schemas
class WorkoutStatusEnum(str, Enum):
    active = "active"
    completed = "completed"
    cancelled = "cancelled"

class StartWorkoutRequest(BaseModel):
    """Request model for starting a workout session."""
    exercises: List[Dict[str, Any]] = Field(..., description="List of exercises with their planned sets, reps, and weights")
    
    class Config:
        json_schema_extra = {
            "example": {
                "exercises": [
                    {
                        "exercise_id": 1,
                        "planned_sets": 3,
                        "planned_reps": 12,
                        "planned_weight_kg": 20.0
                    },
                    {
                        "exercise_id": 2,
                        "planned_sets": 4,
                        "planned_reps": 10,
                        "planned_weight_kg": 15.0
                    }
                ]
            }
        }

class RepeatWorkoutRequest(BaseModel):
    workout_session_id: int = Field(..., gt=0, description="ID of the workout session to repeat")

class WorkoutSessionResponse(BaseModel):
    """Response model for workout session."""
    id: int
    user_id: int
    status: WorkoutStatusEnum
    started_at: datetime
    completed_at: Optional[datetime] = None
    total_duration_seconds: Optional[int] = None
    notes: Optional[str] = None

class WorkoutSessionExerciseResponse(BaseModel):
    """Response model for workout session exercise."""
    id: int
    workout_session_id: int
    exercise_id: int
    exercise_name: str
    planned_sets: int
    planned_reps: int
    planned_weight_kg: float
    order_in_workout: int
    is_completed: bool
    created_at: datetime

class StartWorkoutResponse(BaseModel):
    """Response model for starting a workout."""
    message: str
    workout_session: WorkoutSessionResponse
    exercises: List[WorkoutSessionExerciseResponse]


class SaveWorkoutRequest(BaseModel):
    """Request model for saving current workout list into the active routine day."""
    exercises: List[Dict[str, Any]] = Field(..., description="List of exercises; each item must contain exercise_id")
    target_day_number: Optional[int] = Field(None, ge=1, le=7, description="Optional override of day number; defaults to computed active day")


class SaveWorkoutSavedExercise(BaseModel):
    """Saved exercise with order information."""
    exercise_id: int
    order_in_day: int
    name: Optional[str] = None


class SaveWorkoutResponse(BaseModel):
    """Response model for saved workout list."""
    message: str
    user_routine_id: int
    day_number: int
    total_saved: int
    exercises: List[SaveWorkoutSavedExercise]

class LogSetRequest(BaseModel):
    """Request model for logging a set."""
    workout_session_exercise_id: int = Field(..., gt=0, description="ID of the workout session exercise")
    set_number: int = Field(..., ge=1, le=20, description="Set number (1-based)")
    weight_kg: float = Field(..., ge=0, le=1000, description="Weight used in kg")
    reps_completed: int = Field(..., ge=0, le=100, description="Number of reps completed")
    duration_seconds: Optional[int] = Field(None, ge=0, description="Duration of the set in seconds")
    rest_time_seconds: Optional[int] = Field(None, ge=0, description="Rest time after the set in seconds")
    notes: Optional[str] = Field(None, max_length=500, description="Optional notes for the set")

class WorkoutLogResponse(BaseModel):
    """Response model for workout log entry."""
    id: int
    user_id: int
    workout_session_id: int
    workout_session_exercise_id: int
    exercise_id: int
    exercise_name: str
    set_number: int
    weight_kg: float
    reps_completed: int
    duration_seconds: Optional[int] = None
    rest_time_seconds: Optional[int] = None
    notes: Optional[str] = None
    logged_at: datetime

class LogSetResponse(BaseModel):
    """Response model for logged set."""
    message: str
    workout_log: WorkoutLogResponse

class CompleteWorkoutRequest(BaseModel):
    """Request model for completing a workout."""
    notes: Optional[str] = Field(None, max_length=1000, description="Optional notes for the workout")

class WorkoutHistoryResponse(BaseModel):
    """Response model for workout history entry."""
    id: int
    user_id: int
    workout_session_id: int
    workout_date: str  # Date as string (YYYY-MM-DD)
    total_exercises: int
    total_sets: int
    total_duration_seconds: int
    calories_burned: int
    notes: Optional[str] = None
    created_at: datetime

class CompleteWorkoutResponse(BaseModel):
    """Response model for completed workout."""
    message: str
    workout_session: WorkoutSessionResponse
    workout_history: WorkoutHistoryResponse

class WorkoutStatusResponse(BaseModel):
    """Response model for workout status check."""
    has_active_workout: bool
    active_workout: Optional[WorkoutSessionResponse] = None
    exercises: Optional[List[WorkoutSessionExerciseResponse]] = None
    workout_logs: Optional[List[WorkoutLogResponse]] = None
    total_sets_logged: Optional[int] = None
    total_duration_seconds_so_far: Optional[int] = None

class ExerciseSetDetail(BaseModel):
    """Detailed information about a single set for an exercise in workout history."""
    set_number: int
    weight_kg: float
    reps_completed: int
    duration_seconds: Optional[int] = None
    rest_time_seconds: Optional[int] = None


class ExerciseSummary(BaseModel):
    """Summary of an exercise in workout history."""
    exercise_id: int
    exercise_name: str
    total_sets: int
    sets_summary: str  # e.g., "4 sets × 10 reps at 9kg"
    video_url: Optional[str] = None
    image_url: Optional[str] = None
    sets: List[ExerciseSetDetail] = []
    
class WorkoutSessionSummary(BaseModel):
    """Summary of a workout session."""
    workout_session_id: int
    started_at: datetime
    completed_at: Optional[datetime] = None
    total_duration_seconds: int
    exercises: List[ExerciseSummary]
    notes: Optional[str] = None

class DailyWorkoutHistory(BaseModel):
    """Workout history grouped by date."""
    workout_date: str  # Date as string (YYYY-MM-DD)
    total_workouts: int
    total_exercises: int
    total_sets: int
    total_duration_seconds: int
    workout_sessions: List[WorkoutSessionSummary]

class WorkoutHistoryListResponse(BaseModel):
    """Response model for workout history list grouped by date."""
    history: List[DailyWorkoutHistory]
    total_count: int

# Exercise Exclusion Schemas
class ExclusionTypeEnum(str, Enum):
    forever = "forever"
    today = "today"

class ExcludeExerciseRequest(BaseModel):
    """Request model for excluding exercises."""
    exercise_id: Optional[int] = Field(None, gt=0, description="ID of a single exercise to exclude")
    exercise_ids: Optional[List[int]] = Field(None, min_items=1, description="List of exercise IDs to exclude")
    exclusion_type: ExclusionTypeEnum = Field(..., description="Type of exclusion: 'forever' or 'today'")
    reason: Optional[str] = Field(None, max_length=500, description="Optional reason for exclusion")

    @validator('exercise_ids')
    def validate_exercise_ids(cls, v):
        if v is not None:
            for exercise_id in v:
                if exercise_id <= 0:
                    raise ValueError(f"All exercise IDs must be positive integers, got: {exercise_id}")
        return v

    @model_validator(mode='before')
    def validate_either_single_or_multiple(cls, values):
        single = values.get('exercise_id')
        multiple = values.get('exercise_ids')
        if single is None and (multiple is None or len(multiple) == 0):
            raise ValueError('Either exercise_id or exercise_ids must be provided')
        if single is not None and multiple is not None:
            raise ValueError('Provide either exercise_id or exercise_ids, not both')
        return values

class ExcludeExerciseResponse(BaseModel):
    """Response model for exercise exclusion."""
    success: bool
    message: str
    exclusion_type: str
    exercise_id: Optional[int] = None
    exercise_ids: Optional[List[int]] = None
    total_excluded: Optional[int] = None
    excluded_at: Optional[datetime] = None

class ExcludedExerciseItem(BaseModel):
    """Model for excluded exercise item."""
    id: int
    exercise_id: int
    exercise_name: str
    exclusion_type: str  # 'forever' or 'today'
    excluded_at: datetime
    excluded_date: Optional[str] = None  # Only for 'today' exclusions (YYYY-MM-DD format)
    reason: Optional[str] = None

class UserExcludedExercisesResponse(BaseModel):
    """Response model for user's excluded exercises."""
    forever_excluded: List[ExcludedExerciseItem]
    today_excluded: List[ExcludedExerciseItem]
    total_count: int

class RemoveExclusionRequest(BaseModel):
    """Request model for removing exercise exclusions."""
    exercise_id: int = Field(..., gt=0, description="ID of the exercise to remove exclusion for")
    exclusion_type: ExclusionTypeEnum = Field(..., description="Type of exclusion to remove: 'forever' or 'today'")

class RemoveExclusionResponse(BaseModel):
    """Response model for removing exercise exclusion."""
    success: bool
    message: str
    exercise_id: int
    exclusion_type: str