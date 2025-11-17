import asyncio
import asyncpg
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Database connection URL
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:admin123@localhost:5432/fitness_db"
)

async def run_workout_migration():
    """Run the workout session management migration"""
    try:
        # Connect to the database
        conn = await asyncpg.connect(DATABASE_URL)
        
        # Read and execute the migration SQL
        with open('workout_migration.sql', 'r', encoding='utf-8') as file:
            migration_sql = file.read()
        
        print("Running workout session management migration...")
        
        # Execute the migration in a transaction
        async with conn.transaction():
            await conn.execute(migration_sql)
        
        print("✅ Workout session management tables created successfully!")
        print("The following tables have been added:")
        print("- workout_sessions")
        print("- workout_session_exercises") 
        print("- workout_logs (enhanced)")
        print("- workout_history")
        
        await conn.close()
        
        return True
        
    except asyncpg.PostgresError as e:
        print(f"❌ Migration failed: {e}")
        if conn:
            await conn.close()
        return False
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return False

async def main():
    success = await run_workout_migration()
    if success:
        print("\n🎉 Migration completed! You can now use the workout session APIs.")
    else:
        print("\n💥 Migration failed. Please check the error messages above.")

if __name__ == "__main__":
    asyncio.run(main())
