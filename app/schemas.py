from pydantic import BaseModel, EmailStr, Field, validator
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
    motivation_ids: Optional[List[int]] = None

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
    
    motivation_ids: List[int]
    goal_ids: List[int]
    focus_area_ids: List[int]
    health_issue_ids: List[int]
    equipment_ids: List[int]
    workout_days: List[str]

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
                "motivation_ids": [1, 2],
                "goal_ids": [1, 3],
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
    
    # Simple linked data
    motivations: List[str]
    goals: List[str]
    equipment: List[str]
    health_issues: List[str]
    
    # The new, nested routine data
    routines: List[UserRoutineResponse]
    # New advanced user profile fields
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
    gender: Optional[GenderEnum] = None
    age: Optional[int] = Field(None, gt=0, lt=120)
    height_cm: Optional[float] = None
    current_weight_kg: Optional[float] = None
    target_weight_kg: Optional[float] = None
    fitness_level: Optional[FitnessLevelEnum] = None
    activity_level: Optional[ActivityLevelEnum] = None
    workouts_per_week: Optional[int] = Field(None, ge=1, le=7)
    motivation_ids: Optional[List[int]] = None
    goal_ids: Optional[List[int]] = None
    equipment_ids: Optional[List[int]] = None
    health_issue_ids: Optional[List[int]] = None
    is_matrix: Optional[bool] = None
    randomness: Optional[int] = None
    circute_training: Optional[bool] = None
    rapge_ranges: Optional[bool] = None
    duration: Optional[int] = None
    rest_time: Optional[int] = None
    objective: Optional[str] = None

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